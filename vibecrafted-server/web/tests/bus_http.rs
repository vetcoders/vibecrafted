//! HTTP and MCP contract for the message-store front.
//!
//! Run with:
//! ```text
//! cargo test -p vibecrafted-server-web --features ssr --test bus_http
//! ```
//!
//! The `vibecrafted` binary is a PATH fixture. It records argv and the file
//! the server passed. Message text must be in that file and absent from argv.

#![cfg(feature = "ssr")]

use std::fs;
use std::io::ErrorKind;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::OnceLock;
use std::time::Duration;
use tokio::sync::Mutex;

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode, header};
use leptos::config::{Env, LeptosOptions};
use serde_json::{Value, json};
use tower::ServiceExt;
use vibecrafted_server_web::bus::api::bus_routes_with;
use vibecrafted_server_web::mcp::api::mcp_routes_with;

const TOKEN: &str = "test-bearer-token";
const NEEDLE: &str = "needle-secret-do-not-argv";
const ENVELOPE_ID: &str = "id11111111111111111111111111111111";
const REPLY_ID: &str = "id22222222222222222222222222222222";
const TARGET_RUN: &str = "run-target-1";
const SENDER_RUN: &str = "sender-run-9";

const FAKE_CLI: &str = r#"#!/usr/bin/env python3
import json, os, stat, sys
log = os.environ.get("VC_BUS_FAKE_LOG", "")
argv = sys.argv[1:]
file_body = ""
file_mode = None
if "--file" in argv:
    path = argv[argv.index("--file") + 1]
    with open(path, encoding="utf-8") as handle:
        file_body = handle.read()
    file_mode = stat.S_IMODE(os.stat(path).st_mode)
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": argv, "file": file_body, "mode": file_mode}) + "\n")

def emit(payload, code):
    sys.stdout.write(json.dumps(payload))
    sys.stdout.write("\n")
    raise SystemExit(code)

if "--inspect" in argv:
    mid = argv[argv.index("--inspect") + 1]
    if mid == "missing-id":
        sys.stderr.write(f"message not found: {mid}\n")
        raise SystemExit(1)
    emit({
        "schema": "vibecrafted.provider-message.v1",
        "message_id": mid,
        "run_id": os.environ.get("VC_BUS_FAKE_SENDER_RUN", "sender-run-9"),
        "delivery_state": "inbox_pending",
        "text": os.environ.get("VC_BUS_FAKE_TEXT", "plain-not-an-envelope"),
    }, 0)

if "--run-id" in argv or "--session" in argv:
    run_id = argv[argv.index("--run-id") + 1] if "--run-id" in argv else ""
    emit({
        "schema": "vibecrafted.provider-message.v1",
        "message_id": os.environ.get("VC_BUS_FAKE_MESSAGE_ID", "msg-from-store"),
        "run_id": run_id,
        "delivery_state": os.environ.get("VC_BUS_FAKE_STATE", "inbox_pending"),
    }, int(os.environ.get("VC_BUS_FAKE_EXIT", "0")))

sys.stderr.write("error: fake_usage\n")
raise SystemExit(2)
"#;

struct FakeCli {
    log: PathBuf,
    _dir: PathBuf,
}

struct EnvRestore {
    saved: Vec<(&'static str, Option<std::ffi::OsString>)>,
}

impl EnvRestore {
    fn capture(keys: &[&'static str]) -> Self {
        Self {
            saved: keys
                .iter()
                .map(|key| (*key, std::env::var_os(key)))
                .collect(),
        }
    }
}

impl Drop for EnvRestore {
    fn drop(&mut self) {
        for (key, value) in &self.saved {
            // SAFETY: bus_http holds ENV_LOCK across the mutation, so this
            // process environment is written from one test thread at a time.
            unsafe {
                match value {
                    Some(value) => std::env::set_var(key, value),
                    None => std::env::remove_var(key),
                }
            }
        }
    }
}

fn env_lock() -> &'static Mutex<()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
}

fn set_env(key: &str, value: &str) {
    // SAFETY: caller holds ENV_LOCK for the whole mutation.
    unsafe { std::env::set_var(key, value) }
}

fn install_fake(label: &str) -> FakeCli {
    static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
    let nonce = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let dir = (0..100)
        .find_map(|attempt| {
            let candidate = std::env::temp_dir().join(format!(
                "vc-bus-fake-{label}-{}-{nonce}-{attempt}",
                std::process::id()
            ));
            match fs::create_dir(&candidate) {
                Ok(()) => Some(candidate),
                Err(error) if error.kind() == ErrorKind::AlreadyExists => None,
                Err(error) => panic!("create fake bin dir: {error}"),
            }
        })
        .expect("fake bin dir");
    let script = dir.join("vibecrafted");
    fs::write(&script, FAKE_CLI).expect("fake script");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = fs::metadata(&script).expect("meta").permissions();
        perms.set_mode(0o755);
        fs::set_permissions(&script, perms).expect("chmod");
    }
    let log = dir.join("argv.jsonl");
    fs::write(&log, "").expect("log");
    let mut path = std::ffi::OsString::from(dir.as_os_str());
    path.push(":");
    if let Some(old) = std::env::var_os("PATH") {
        path.push(old);
    }
    // SAFETY: caller holds ENV_LOCK. Prepends the fixture so `vibecrafted`
    // resolves to it, and leaves the rest of PATH so `env python3` still works.
    unsafe { std::env::set_var("PATH", path) }
    set_env("VC_BUS_FAKE_LOG", &log.display().to_string());
    set_env("VC_BUS_FAKE_SENDER_RUN", SENDER_RUN);
    set_env("VC_BUS_FAKE_STATE", "inbox_pending");
    set_env("VC_BUS_FAKE_EXIT", "0");
    set_env("VC_BUS_FAKE_MESSAGE_ID", "msg-from-store");
    set_env("VC_BUS_FAKE_TEXT", "plain-not-an-envelope");
    FakeCli { log, _dir: dir }
}

impl Drop for FakeCli {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self._dir);
    }
}

fn log_lines(fake: &FakeCli) -> Vec<Value> {
    fs::read_to_string(&fake.log)
        .expect("log")
        .lines()
        .filter(|line| !line.is_empty())
        .map(|line| serde_json::from_str(line).expect("log line"))
        .collect()
}

fn argv_of(entry: &Value) -> Vec<String> {
    entry["argv"]
        .as_array()
        .expect("argv")
        .iter()
        .map(|item| item.as_str().unwrap_or("").to_string())
        .collect()
}

fn assert_text_not_in_argv(argv: &[String]) {
    assert!(
        argv.iter().all(|arg| !arg.contains(NEEDLE)),
        "message text leaked into argv: {argv:?}"
    );
}

fn envelope(run_id: &str, text: &str, id: &str) -> Value {
    json!({
        "spec": "fleet.envelope/0.1",
        "id": id,
        "source": "fleet:fable",
        "type": "fleet.message",
        "time": "2026-09-26T19:40:00Z",
        "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
        "messageId": id,
        "contextId": "ctx-same",
        "taskId": null,
        "referenceTaskIds": [],
        "role": "agent",
        "parts": [{"kind": "text", "text": text}],
        "recipient": "kodeksik",
        "hopCount": 0,
        "maxHops": 8,
        "status": null,
        "statusDetail": null,
        "metadata": {"vibecrafted": {"run_id": run_id}}
    })
}

fn router(token: &str) -> axum::Router {
    let opts = LeptosOptions::builder()
        .output_name("vibecrafted-server-web-test")
        .site_root("target/site-test")
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr("127.0.0.1:0".parse::<SocketAddr>().expect("addr"))
        .reload_port(0)
        .build();
    mcp_routes_with(std::env::temp_dir(), token, Duration::from_millis(200))
        .merge(bus_routes_with(token))
        .with_state(opts)
}

async fn post(
    app: &axum::Router,
    token: Option<&str>,
    origin: Option<&str>,
    uri: &str,
    body: &str,
) -> (StatusCode, Vec<u8>) {
    let mut builder = Request::builder()
        .method("POST")
        .uri(uri)
        .header(header::HOST, "127.0.0.1:3024")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::ACCEPT, "application/json");
    if let Some(origin) = origin {
        builder = builder.header(header::ORIGIN, origin);
    }
    if let Some(token) = token {
        builder = builder.header(header::AUTHORIZATION, format!("Bearer {token}"));
    }
    let request = builder.body(Body::from(body.to_string())).expect("request");
    let response = app.clone().oneshot(request).await.expect("response");
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 1024 * 1024)
        .await
        .expect("body")
        .to_vec();
    (status, bytes)
}

fn json_body(bytes: &[u8]) -> Value {
    serde_json::from_slice(bytes).expect("json")
}

async fn hold_env() -> (tokio::sync::MutexGuard<'static, ()>, EnvRestore) {
    let lock = env_lock().lock().await;
    let restore = EnvRestore::capture(&[
        "PATH",
        "VC_BUS_FAKE_LOG",
        "VC_BUS_FAKE_STATE",
        "VC_BUS_FAKE_EXIT",
        "VC_BUS_FAKE_MESSAGE_ID",
        "VC_BUS_FAKE_SENDER_RUN",
        "VC_BUS_FAKE_TEXT",
    ]);
    (lock, restore)
}

#[tokio::test]
async fn post_envelope_returns_store_receipt_and_keeps_text_out_of_argv() {
    let (_lock, _env) = hold_env().await;
    let fake = install_fake("post");
    let app = router(TOKEN);
    let (status, body) = post(
        &app,
        Some(TOKEN),
        Some("http://127.0.0.1:3024"),
        "/api/bus/messages",
        &envelope(TARGET_RUN, NEEDLE, ENVELOPE_ID).to_string(),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    let payload = json_body(&body);
    assert_eq!(payload["message_id"], "msg-from-store");
    assert_eq!(payload["delivery_state"], "inbox_pending");
    assert_eq!(payload["run_id"], TARGET_RUN);
    assert!(payload.get("text").is_none());

    let lines = log_lines(&fake);
    assert_eq!(lines.len(), 1);
    let argv = argv_of(&lines[0]);
    assert_text_not_in_argv(&argv);
    assert!(argv.contains(&"--run-id".to_string()));
    assert!(argv.contains(&TARGET_RUN.to_string()));
    assert!(argv.contains(&"--idempotency-key".to_string()));
    assert!(argv.contains(&ENVELOPE_ID.to_string()));
    assert!(argv.contains(&"--json".to_string()));
    assert!(argv.contains(&"--file".to_string()));
    assert!(!argv.contains(&"--retry".to_string()));
    assert_eq!(lines[0]["mode"], 0o600);
    let stored = lines[0]["file"].as_str().expect("file");
    assert!(stored.contains(NEEDLE));
    assert!(stored.contains("fleet.envelope/0.1"));
}

#[tokio::test]
async fn exit_one_receipt_is_returned_with_store_state() {
    let (_lock, _env) = hold_env().await;
    let _fake = install_fake("exit1");
    set_env("VC_BUS_FAKE_STATE", "retryable_failure");
    set_env("VC_BUS_FAKE_EXIT", "1");
    let app = router(TOKEN);
    let (status, body) = post(
        &app,
        Some(TOKEN),
        None,
        "/api/bus/messages",
        &envelope(TARGET_RUN, NEEDLE, ENVELOPE_ID).to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let payload = json_body(&body);
    assert_eq!(payload["delivery_state"], "retryable_failure");
    assert_eq!(payload["message_id"], "msg-from-store");
}

#[tokio::test]
async fn mcp_vc_message_send_matches_the_http_receipt() {
    let (_lock, _env) = hold_env().await;
    let fake = install_fake("mcp-send");
    let app = router(TOKEN);
    let (status, body) = post(
        &app,
        Some(TOKEN),
        Some("http://localhost:3024"),
        "/mcp",
        &json!({
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/list",
            "params": {}
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let listed = json_body(&body);
    let send_tool = listed["result"]["tools"]
        .as_array()
        .expect("tools")
        .iter()
        .find(|tool| tool["name"] == "vc_message_send")
        .expect("vc_message_send");
    assert_eq!(send_tool["inputSchema"]["required"], json!(["envelope"]));

    let (status, body) = post(
        &app,
        Some(TOKEN),
        None,
        "/mcp",
        &json!({
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "vc_message_send",
                "arguments": {"envelope": envelope(TARGET_RUN, NEEDLE, ENVELOPE_ID)}
            }
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let payload = json_body(&body);
    assert_eq!(payload["id"], 8);
    assert_eq!(payload["result"]["isError"], false);
    assert_eq!(
        payload["result"]["structuredContent"]["message_id"],
        "msg-from-store"
    );
    assert_eq!(
        payload["result"]["structuredContent"]["delivery_state"],
        "inbox_pending"
    );
    let lines = log_lines(&fake);
    assert_eq!(lines.len(), 1);
    let argv = argv_of(&lines[0]);
    assert_text_not_in_argv(&argv);
    assert!(argv.contains(&TARGET_RUN.to_string()));
    assert!(argv.contains(&ENVELOPE_ID.to_string()));
    assert!(lines[0]["file"].as_str().expect("file").contains(NEEDLE));
}

#[tokio::test]
async fn vc_message_status_returns_receipt_and_unknown_id_is_typed() {
    let (_lock, _env) = hold_env().await;
    let fake = install_fake("status");
    let app = router(TOKEN);
    let (status, body) = post(
        &app,
        Some(TOKEN),
        None,
        "/mcp",
        &json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "vc_message_status",
                "arguments": {"message_id": "msg-known-1"}
            }
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let payload = json_body(&body);
    assert_eq!(payload["result"]["isError"], false);
    assert_eq!(
        payload["result"]["structuredContent"]["message_id"],
        "msg-known-1"
    );
    assert_eq!(
        payload["result"]["structuredContent"]["delivery_state"],
        "inbox_pending"
    );
    assert_eq!(payload["result"]["structuredContent"]["run_id"], SENDER_RUN);
    let known = argv_of(&log_lines(&fake)[0]);
    assert!(known.contains(&"--inspect".to_string()));
    assert!(known.contains(&"msg-known-1".to_string()));
    assert!(!known.iter().any(|arg| arg == "--file"));

    let (status, body) = post(
        &app,
        Some(TOKEN),
        None,
        "/mcp",
        &json!({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "vc_message_status",
                "arguments": {"message_id": "missing-id"}
            }
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let payload = json_body(&body);
    assert_eq!(payload["result"]["isError"], true);
    assert_eq!(
        payload["result"]["structuredContent"]["error"],
        "message_not_found"
    );
    assert_eq!(
        payload["result"]["structuredContent"]["message_id"],
        "missing-id"
    );
}

#[tokio::test]
async fn vc_message_reply_addresses_the_receipt_run() {
    let (_lock, _env) = hold_env().await;
    let fake = install_fake("reply");
    let app = router(TOKEN);
    let mut reply = envelope("decoy-run", NEEDLE, REPLY_ID);
    reply["metadata"]["vibecrafted"]["run_id"] = json!("decoy-run");
    let (status, body) = post(
        &app,
        Some(TOKEN),
        None,
        "/mcp",
        &json!({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "vc_message_reply",
                "arguments": {
                    "message_id": "msg-known-1",
                    "envelope": reply
                }
            }
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let payload = json_body(&body);
    assert_eq!(payload["result"]["isError"], false);
    assert_eq!(payload["result"]["structuredContent"]["run_id"], SENDER_RUN);
    assert_eq!(
        payload["result"]["structuredContent"]["delivery_state"],
        "inbox_pending"
    );

    let lines = log_lines(&fake);
    assert_eq!(lines.len(), 2);
    let inspect = argv_of(&lines[0]);
    assert!(inspect.contains(&"--inspect".to_string()));
    assert!(inspect.contains(&"msg-known-1".to_string()));
    assert_text_not_in_argv(&inspect);
    let send = argv_of(&lines[1]);
    assert_text_not_in_argv(&send);
    assert!(send.contains(&"--run-id".to_string()));
    assert!(send.contains(&SENDER_RUN.to_string()));
    assert!(
        !send.iter().any(|arg| arg == "decoy-run"),
        "reply followed the envelope target instead of the receipt: {send:?}"
    );
    assert!(lines[1]["file"].as_str().expect("file").contains(NEEDLE));
    assert_eq!(lines[1]["mode"], 0o600);
}

#[tokio::test]
async fn reply_context_mismatch_does_not_send() {
    let (_lock, _env) = hold_env().await;
    let fake = install_fake("mismatch");
    set_env(
        "VC_BUS_FAKE_TEXT",
        r#"{"spec":"fleet.envelope/0.1","contextId":"ctx-same"}"#,
    );
    let app = router(TOKEN);
    let mut reply = envelope("decoy-run", NEEDLE, REPLY_ID);
    reply["contextId"] = json!("ctx-other");
    let (status, body) = post(
        &app,
        Some(TOKEN),
        None,
        "/mcp",
        &json!({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "vc_message_reply",
                "arguments": {"message_id": "msg-known-1", "envelope": reply}
            }
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let payload = json_body(&body);
    assert_eq!(payload["result"]["isError"], true);
    assert_eq!(
        payload["result"]["structuredContent"]["error"],
        "context_mismatch"
    );
    let lines = log_lines(&fake);
    assert_eq!(lines.len(), 1);
    assert!(argv_of(&lines[0]).contains(&"--inspect".to_string()));
}

#[tokio::test]
async fn bearer_missing_or_wrong_is_401_and_bad_origin_is_403() {
    let (_lock, _env) = hold_env().await;
    let fake = install_fake("auth");
    let app = router(TOKEN);
    let body = envelope(TARGET_RUN, NEEDLE, ENVELOPE_ID).to_string();

    let (status, bytes) = post(&app, None, None, "/api/bus/messages", &body).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(json_body(&bytes)["error"], "unauthorized");

    let (status, _) = post(
        &app,
        Some("other-token-value"),
        None,
        "/api/bus/messages",
        &body,
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);

    let (status, bytes) = post(
        &app,
        Some(TOKEN),
        Some("https://evil.example"),
        "/api/bus/messages",
        &body,
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(json_body(&bytes)["error"], "origin rejected");

    let (status, _) = post(
        &app,
        None,
        None,
        "/mcp",
        &json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "vc_message_send",
                "arguments": {"envelope": envelope(TARGET_RUN, NEEDLE, ENVELOPE_ID)}
            }
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert!(log_lines(&fake).is_empty());
}

#[tokio::test]
async fn invalid_envelope_does_not_spawn_the_store() {
    let (_lock, _env) = hold_env().await;
    let fake = install_fake("invalid");
    let app = router(TOKEN);
    let mut bad = envelope(TARGET_RUN, NEEDLE, ENVELOPE_ID);
    bad["spec"] = json!("fleet.envelope/0.2");
    let (status, body) = post(
        &app,
        Some(TOKEN),
        None,
        "/api/bus/messages",
        &bad.to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let payload = json_body(&body);
    assert_eq!(payload["error"], "invalid_envelope");
    assert_eq!(payload["detail"], "spec");
    assert!(log_lines(&fake).is_empty());

    let mut unaddressed = envelope(TARGET_RUN, NEEDLE, ENVELOPE_ID);
    unaddressed["metadata"] = json!({});
    let (status, body) = post(
        &app,
        Some(TOKEN),
        None,
        "/api/bus/messages",
        &unaddressed.to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(json_body(&body)["error"], "run_id_or_session_required");
    assert!(log_lines(&fake).is_empty());
}
