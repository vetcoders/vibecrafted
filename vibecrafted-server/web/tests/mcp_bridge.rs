//! Stdio bridge: lazy handshake, proxy, restart backoff, process group.
//!
//! ```text
//! cargo test -p vibecrafted-server-web --features ssr --test mcp_bridge
//! ```

#![cfg(feature = "ssr")]

use std::fs;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode, header};
use leptos::config::{Env, LeptosOptions};
use serde_json::{Value, json};
use tower::ServiceExt;
use vibecrafted_server_web::mcp::api::mcp_routes_with_bridge;
use vibecrafted_server_web::mcp_bridge::{
    BridgeCommand, BridgeConfig, BridgeEnv, BridgeError, StdioBridge, merge_vc_tools,
    resolve_bridge_command,
};

const TOKEN: &str = "bridge-bearer";

const FAKE_PY: &str = r#"
import json
import os
import subprocess
import sys
import time

def emit(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()

def log(method):
    path = os.environ.get("FAKE_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(method + "\n")

def fail_once():
    path = os.environ.get("FAKE_COUNT")
    if not path:
        return False
    count = 0
    if os.path.exists(path):
        raw = open(path, encoding="utf-8").read().strip()
        count = int(raw or "0")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(str(count + 1))
    return count == 0

def spawn_grandchild():
    path = os.environ.get("FAKE_PIDFILE")
    if not path:
        return
    proc = subprocess.Popen(
        ["/bin/sleep", "30"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(str(proc.pid))

def main():
    mode = os.environ.get("FAKE_MODE", "ok")
    if mode == "exit" or (mode == "fail-once" and fail_once()):
        os._exit(7)
    spawn_grandchild()
    tools = [
        {
            "name": "vc_doctor",
            "description": "bridged doctor",
            "inputSchema": {
                "type": "object",
                "properties": {"project": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        {
            "name": "vc_run_status",
            "description": "stdio run status",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "home": {"type": "string"},
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "vc_extra",
            "description": "extra bridged tool",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "not_a_vc_tool",
            "description": "should not cross the bridge",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        if method:
            log(str(method))
        if method == "initialize":
            emit({
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake", "version": "0"},
                },
            })
            continue
        if isinstance(method, str) and method.startswith("notifications/"):
            continue
        if method == "tools/list":
            if mode == "hang":
                time.sleep(3600)
            emit({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"tools": tools}})
            continue
        if method == "tools/call":
            if mode == "hang":
                time.sleep(3600)
            params = msg.get("params") or {}
            emit({
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": {
                    "content": [{"type": "text", "text": "echo:" + str(params.get("name"))}],
                    "structuredContent": {
                        "name": params.get("name"),
                        "arguments": params.get("arguments"),
                    },
                    "isError": False,
                },
            })
            continue
        if "id" in msg and method:
            emit({
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32601, "message": "method not found"},
            })

if __name__ == "__main__":
    main()
"#;

struct TempDir {
    path: PathBuf,
}

impl TempDir {
    fn new(label: &str) -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "vc-mcp-bridge-{label}-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&path).expect("temp dir");
        Self { path }
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

fn config(timeout: Duration) -> BridgeConfig {
    BridgeConfig {
        call_timeout: timeout,
        initial_backoff: Duration::from_millis(250),
        max_backoff: Duration::from_millis(500),
        inherit_stderr: false,
    }
}

fn python() -> String {
    std::env::var("MCP_BRIDGE_TEST_PYTHON").unwrap_or_else(|_| "python3".to_string())
}

fn write_fake(dir: &Path) -> PathBuf {
    let path = dir.join("fake_mcp.py");
    fs::write(&path, FAKE_PY).expect("fake script");
    path
}

fn bridge(dir: &Path, mode: &str, timeout: Duration) -> Arc<StdioBridge> {
    let script = write_fake(dir);
    let mut command = BridgeCommand::new(python(), [script.as_os_str()]);
    command.env = vec![
        ("FAKE_MODE".into(), mode.into()),
        ("FAKE_LOG".into(), dir.join("methods.log").into()),
        ("FAKE_COUNT".into(), dir.join("count").into()),
        ("FAKE_PIDFILE".into(), dir.join("grandchild.pid").into()),
    ];
    StdioBridge::start(command, config(timeout))
}

fn process_alive(pid: i32) -> bool {
    Command::new("/bin/kill")
        .args(["-0", &pid.to_string()])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

fn wait_until_dead(pid: i32) -> bool {
    let deadline = Instant::now() + Duration::from_secs(2);
    while Instant::now() < deadline {
        if !process_alive(pid) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(40));
    }
    !process_alive(pid)
}

fn methods(dir: &Path) -> Vec<String> {
    fs::read_to_string(dir.join("methods.log"))
        .unwrap_or_default()
        .lines()
        .map(str::to_string)
        .collect()
}

fn router(bridge: Arc<StdioBridge>) -> axum::Router {
    let opts = LeptosOptions::builder()
        .output_name("vibecrafted-server-web-test")
        .site_root("target/site-test")
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr("127.0.0.1:0".parse::<SocketAddr>().expect("addr"))
        .reload_port(0)
        .build();
    mcp_routes_with_bridge(
        std::env::temp_dir(),
        TOKEN,
        Duration::from_millis(50),
        Some(bridge),
    )
    .with_state(opts)
}

async fn post(app: &axum::Router, body: &str) -> (StatusCode, Value) {
    let request = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::ACCEPT, "application/json")
        .header(header::AUTHORIZATION, format!("Bearer {TOKEN}"))
        .body(Body::from(body.to_string()))
        .expect("request");
    let response = app.clone().oneshot(request).await.expect("response");
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 1024 * 1024)
        .await
        .expect("body");
    let payload = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    (status, payload)
}

fn touch_exe(path: &Path) {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).expect("exe dir");
    }
    fs::write(path, b"#!/bin/sh\nexit 0\n").expect("exe");
    let mut permissions = fs::metadata(path).expect("meta").permissions();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        permissions.set_mode(0o755);
    }
    fs::set_permissions(path, permissions).expect("mode");
}

#[test]
fn merge_keeps_pilots_and_drops_non_vc_duplicates() {
    let pilots = vec![json!({"name": "vc_ping", "description": "pilot"})];
    let remote = vec![
        json!({"name": "vc_ping", "description": "remote"}),
        json!({"name": "vc_doctor", "inputSchema": {"type": "object"}}),
        json!({"name": "not_a_vc_tool"}),
        json!({"name": ""}),
    ];
    let merged = merge_vc_tools(pilots, remote);
    assert_eq!(merged.len(), 2);
    assert_eq!(merged[0]["description"], "pilot");
    assert_eq!(merged[1]["name"], "vc_doctor");
}

#[test]
fn resolve_honors_command_override_and_checkout_uv() {
    let overridden = resolve_bridge_command(&BridgeEnv {
        command: Some("  /opt/bin/vibecrafted-mcp  ".to_string()),
        args_json: Some(r#"["--stdio"]"#.to_string()),
        path: String::new(),
        pythonpath: None,
        current_dir: PathBuf::from("."),
        executable: None,
    })
    .expect("override");
    assert_eq!(overridden.program, "/opt/bin/vibecrafted-mcp");
    assert_eq!(overridden.args, vec![std::ffi::OsString::from("--stdio")]);

    let bad = resolve_bridge_command(&BridgeEnv {
        command: Some("tool".to_string()),
        args_json: Some("nope".to_string()),
        path: String::new(),
        pythonpath: None,
        current_dir: PathBuf::from("."),
        executable: None,
    });
    assert!(bad.is_err(), "{bad:?}");

    let root = TempDir::new("resolve");
    let bin = root.path.join("bin");
    touch_exe(&bin.join("uv"));
    touch_exe(&bin.join("vibecrafted-mcp"));
    fs::create_dir_all(root.path.join("checkout/vibecrafted-mcp")).expect("mcp dir");
    fs::write(
        root.path.join("checkout/vibecrafted-mcp/pyproject.toml"),
        b"[project]\nname='x'\n",
    )
    .expect("pyproject");
    fs::create_dir_all(root.path.join("checkout/vibecrafted-core")).expect("core");

    let on_path = resolve_bridge_command(&BridgeEnv {
        command: None,
        args_json: None,
        path: bin.display().to_string(),
        pythonpath: None,
        current_dir: root.path.join("checkout"),
        executable: None,
    })
    .expect("path entry");
    assert!(
        on_path
            .program
            .to_string_lossy()
            .ends_with("vibecrafted-mcp"),
        "{}",
        on_path.program.to_string_lossy()
    );

    fs::remove_file(bin.join("vibecrafted-mcp")).expect("remove path entry");
    let via_uv = resolve_bridge_command(&BridgeEnv {
        command: None,
        args_json: None,
        path: bin.display().to_string(),
        pythonpath: Some("/already".to_string()),
        current_dir: root.path.join("checkout"),
        executable: None,
    })
    .expect("uv");
    assert!(via_uv.program.to_string_lossy().ends_with("uv"));
    assert_eq!(via_uv.args[0], "run");
    assert_eq!(via_uv.args[1], "--project");
    assert!(
        via_uv.args[2]
            .to_string_lossy()
            .ends_with("vibecrafted-mcp"),
        "{}",
        via_uv.args[2].to_string_lossy()
    );
    assert_eq!(via_uv.args[3], "vibecrafted-mcp");

    fs::remove_file(bin.join("uv")).expect("remove uv");
    touch_exe(&bin.join("python3"));
    let via_python = resolve_bridge_command(&BridgeEnv {
        command: None,
        args_json: None,
        path: bin.display().to_string(),
        pythonpath: None,
        current_dir: root.path.join("checkout"),
        executable: None,
    })
    .expect("python");
    assert!(via_python.program.to_string_lossy().ends_with("python3"));
    let pythonpath = via_python
        .env
        .iter()
        .find(|(key, _)| key == "PYTHONPATH")
        .map(|(_, value)| value.to_string_lossy().into_owned())
        .expect("PYTHONPATH");
    assert!(pythonpath.contains("vibecrafted-mcp"), "{pythonpath}");
    assert!(pythonpath.contains("vibecrafted-core"), "{pythonpath}");
}

#[tokio::test]
async fn lazy_handshake_proxies_tools_call_and_caches_list() {
    let dir = TempDir::new("lazy");
    let bridge = bridge(&dir.path, "ok", Duration::from_millis(800));
    assert!(bridge.child_pid().is_none(), "bridge must stay cold");

    let tools = bridge
        .list_tools()
        .await
        .unwrap_or_else(|error| panic!("{error}\n{:?}", bridge.recent_events()));
    assert!(bridge.child_pid().is_some(), "first list starts the child");
    assert!(tools.iter().any(|tool| tool["name"] == "vc_doctor"));
    let again = bridge.list_tools().await.expect("cached list");
    assert_eq!(again, tools);

    let result = bridge
        .call_tool("vc_doctor", json!({"project": "."}))
        .await
        .unwrap_or_else(|error| panic!("{error}\n{:?}", bridge.recent_events()));
    assert_eq!(result["content"][0]["text"], "echo:vc_doctor");
    assert_eq!(result["structuredContent"]["arguments"]["project"], ".");
    assert_eq!(result["isError"], false);

    let seen = methods(&dir.path);
    assert_eq!(seen.first().map(String::as_str), Some("initialize"));
    assert!(seen.iter().any(|method| method == "tools/list"));
    assert!(seen.iter().any(|method| method == "tools/call"));
    assert!(seen.contains(&"notifications/initialized".to_string()));
    assert_eq!(
        seen.iter()
            .filter(|method| method.as_str() == "tools/list")
            .count(),
        1,
        "second list must hit the cache: {seen:?}"
    );
    let init_at = seen
        .iter()
        .position(|method| method == "initialize")
        .unwrap();
    let call_at = seen
        .iter()
        .position(|method| method == "tools/call")
        .unwrap();
    assert!(init_at < call_at, "{seen:?}");
}

#[tokio::test]
async fn dead_child_backs_off_and_a_call_during_restart_is_typed() {
    let dir = TempDir::new("restart");
    let bridge = bridge(&dir.path, "fail-once", Duration::from_millis(800));
    let started = Instant::now();
    let first = bridge.list_tools().await.expect_err("first child exits");
    assert!(
        started.elapsed() < Duration::from_secs(2),
        "first failure hung: {first} after {:?}",
        started.elapsed()
    );
    assert!(
        !matches!(first, BridgeError::Restarting { .. }),
        "the death itself is not the backoff rejection: {first}"
    );

    let started = Instant::now();
    let second = bridge.list_tools().await.expect_err("backoff window");
    assert!(
        started.elapsed() < Duration::from_millis(150),
        "restart rejection hung: {second} after {:?}",
        started.elapsed()
    );
    assert!(
        matches!(
            second,
            BridgeError::Restarting {
                retry_after_ms: 1..
            }
        ),
        "{second:?}\n{:?}",
        bridge.recent_events()
    );

    tokio::time::sleep(Duration::from_millis(600)).await;
    let tools = bridge.list_tools().await.unwrap_or_else(|error| {
        panic!(
            "restart did not recover: {error}\n{:?}",
            bridge.recent_events()
        )
    });
    assert!(tools.iter().any(|tool| tool["name"] == "vc_extra"));
    let log = bridge.recent_events().join("\n");
    assert!(log.contains("backoff"), "{log}");
}

#[tokio::test]
async fn hung_call_times_out_instead_of_blocking() {
    let dir = TempDir::new("hang");
    let bridge = bridge(&dir.path, "hang", Duration::from_millis(250));
    let started = Instant::now();
    let error = bridge
        .call_tool("vc_doctor", json!({}))
        .await
        .expect_err("hang");
    assert!(
        started.elapsed() < Duration::from_secs(2),
        "hung call blocked for {:?}",
        started.elapsed()
    );
    assert!(
        matches!(error, BridgeError::Timeout),
        "{error:?}\n{:?}",
        bridge.recent_events()
    );
    let started = Instant::now();
    let during = bridge
        .call_tool("vc_doctor", json!({}))
        .await
        .expect_err("backoff");
    assert!(started.elapsed() < Duration::from_millis(150), "{during:?}");
    assert!(
        matches!(during, BridgeError::Restarting { .. }),
        "{during:?}"
    );
}

#[tokio::test]
async fn dropping_the_bridge_kills_the_process_group() {
    let dir = TempDir::new("group");
    let bridge = bridge(&dir.path, "ok", Duration::from_millis(800));
    bridge
        .list_tools()
        .await
        .unwrap_or_else(|error| panic!("{error}\n{:?}", bridge.recent_events()));
    let leader = i32::try_from(bridge.child_pid().expect("leader")).expect("pid");
    let grandchild = fs::read_to_string(dir.path.join("grandchild.pid"))
        .expect("grandchild pid")
        .trim()
        .parse::<i32>()
        .expect("pid int");
    assert!(process_alive(leader), "leader {leader}");
    assert!(process_alive(grandchild), "grandchild {grandchild}");
    drop(bridge);
    assert!(wait_until_dead(leader), "leader {leader} survived drop");
    assert!(
        wait_until_dead(grandchild),
        "grandchild {grandchild} survived the process-group kill"
    );
}

#[test]
fn parent_exit_without_drop_kills_the_group() {
    if let Ok(path) = std::env::var("MCP_BRIDGE_ORPHAN") {
        orphan_parent(&PathBuf::from(path));
        return;
    }
    let dir = TempDir::new("orphan");
    let mut child = Command::new(std::env::current_exe().expect("test exe"));
    child
        .arg("parent_exit_without_drop_kills_the_group")
        .arg("--exact")
        .env("MCP_BRIDGE_ORPHAN", &dir.path)
        .env("MCP_BRIDGE_TEST_PYTHON", python());
    let status = child.status().expect("re-exec");
    assert!(status.success(), "orphan parent failed: {status}");
    let leader = fs::read_to_string(dir.path.join("leader.pid"))
        .expect("leader pid")
        .trim()
        .parse::<i32>()
        .expect("leader int");
    let grandchild = fs::read_to_string(dir.path.join("grandchild.pid"))
        .expect("grandchild pid")
        .trim()
        .parse::<i32>()
        .expect("grandchild int");
    assert!(
        wait_until_dead(leader),
        "leader {leader} survived parent exit without Drop"
    );
    assert!(
        wait_until_dead(grandchild),
        "grandchild {grandchild} survived parent exit without Drop"
    );
}

fn orphan_parent(dir: &Path) {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("runtime");
    runtime.block_on(async {
        let bridge = bridge(dir, "ok", Duration::from_secs(2));
        bridge
            .list_tools()
            .await
            .unwrap_or_else(|error| panic!("{error}\n{:?}", bridge.recent_events()));
        let leader = bridge.child_pid().expect("leader");
        fs::write(dir.join("leader.pid"), leader.to_string()).expect("write leader");
        std::mem::forget(bridge);
    });
    std::process::exit(0);
}

#[tokio::test]
async fn http_list_prefers_pilots_and_proxies_other_vc_tools() {
    let dir = TempDir::new("http");
    let bridge = bridge(&dir.path, "ok", Duration::from_millis(800));
    let app = router(Arc::clone(&bridge));

    let (status, ping) = post(
        &app,
        &json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "vc_ping", "arguments": {}}
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(ping["result"]["isError"], false);
    assert!(
        bridge.child_pid().is_none(),
        "pilot vc_ping must not start the bridge\n{:?}",
        bridge.recent_events()
    );

    let (status, listed) = post(
        &app,
        &json!({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{listed}");
    let tools = listed["result"]["tools"].as_array().expect("tools");
    let names: Vec<_> = tools
        .iter()
        .map(|tool| tool["name"].as_str().unwrap_or(""))
        .collect();
    assert_eq!(
        names
            .iter()
            .filter(|name| **name == "vc_run_status")
            .count(),
        1
    );
    assert!(names.contains(&"vc_ping"));
    assert!(names.contains(&"vc_doctor"));
    assert!(names.contains(&"vc_extra"));
    assert!(!names.contains(&"not_a_vc_tool"), "{names:?}");
    let status_tool = tools
        .iter()
        .find(|tool| tool["name"] == "vc_run_status")
        .expect("pilot");
    assert_eq!(
        status_tool["description"],
        "Read one control-plane run by id."
    );
    assert!(
        status_tool["inputSchema"]["properties"]
            .get("home")
            .is_none()
    );
    let doctor = tools
        .iter()
        .find(|tool| tool["name"] == "vc_doctor")
        .expect("doctor");
    assert_eq!(doctor["description"], "bridged doctor");

    let (status, called) = post(
        &app,
        &json!({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "vc_doctor", "arguments": {"project": "repo"}}
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{called}");
    assert_eq!(
        called["result"]["structuredContent"]["arguments"]["project"],
        "repo"
    );
    assert_eq!(called["result"]["content"][0]["text"], "echo:vc_doctor");

    let seen = methods(&dir.path);
    assert_eq!(
        seen.iter()
            .filter(|method| method.as_str() == "tools/call")
            .count(),
        1,
        "vc_ping must not be proxied: {seen:?}"
    );
}

#[test]
fn missing_python_error_is_readable() {
    let missing = resolve_bridge_command(&BridgeEnv {
        command: None,
        args_json: None,
        path: "/nonexistent-vc-bridge-path".to_string(),
        pythonpath: None,
        current_dir: PathBuf::from("/nonexistent-vc-bridge-dir"),
        executable: Some(PathBuf::from("/nonexistent-vc-bridge-dir/vc-server")),
    });
    match missing {
        Err(error) => assert!(error.contains("vibecrafted-mcp"), "{error}"),
        Ok(command) => panic!("resolved without a checkout: {command:?}"),
    }
}
