use std::fs;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};

const VALID_NONCE: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

struct ChildGuard(Child);

impl std::ops::Deref for ChildGuard {
    type Target = Child;

    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

impl std::ops::DerefMut for ChildGuard {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.0
    }
}

impl Drop for ChildGuard {
    fn drop(&mut self) {
        stop_child(&mut self.0);
    }
}

fn server_command() -> Command {
    Command::new(env!("CARGO_BIN_EXE_vibecrafted-server-web"))
}

fn fixture_root() -> PathBuf {
    let root = std::env::temp_dir().join(format!(
        "vc-server-await-http-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos()
    ));
    fs::create_dir_all(&root).expect("fixture root");
    root
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .expect("ephemeral listener")
        .local_addr()
        .expect("listener address")
        .port()
}

fn write_runtime_meta(home: &Path, state: &str, exit_code: Option<i32>) {
    let run_dir = home.join("control_plane/runtime_runs/run-http-fanin");
    fs::create_dir_all(&run_dir).expect("runtime run directory");
    fs::write(
        run_dir.join("meta.json"),
        serde_json::to_vec(&json!({
            "run_id": "run-http-fanin",
            "status": state,
            "state": state,
            "agent": "codex",
            "skill": "implement",
            "mode": "implement",
            "root": "/repo",
            "updated_at": "2026-08-26T05:00:00+00:00",
            "completed_at": if exit_code.is_some() { "2026-08-26T05:00:01+00:00" } else { "" },
            "health": if exit_code.is_some() { "final" } else { "active" },
            "liveness": if exit_code.is_some() { "terminal" } else { "heartbeat" },
            "exit_code": exit_code,
        }))
        .expect("meta JSON"),
    )
    .expect("write runtime meta");
}

fn wait_for_server(port: u16) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        if let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) {
            stream
                .write_all(
                    b"GET /api/health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
                )
                .expect("health request");
            let mut response = String::new();
            stream
                .read_to_string(&mut response)
                .expect("health response");
            if response.starts_with("HTTP/1.1 200") {
                return;
            }
        }
        thread::sleep(Duration::from_millis(25));
    }
    panic!("vc-server did not become healthy on port {port}");
}

fn stop_child(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

#[test]
fn lifecycle_nonce_requires_a_value() {
    let output = server_command()
        .arg("--lifecycle-nonce")
        .output()
        .expect("run vc-server");

    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("requires a value"));
}

#[test]
fn lifecycle_nonce_rejects_non_canonical_values() {
    for value in [
        "short",
        "A123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    ] {
        let output = server_command()
            .args(["--lifecycle-nonce", value])
            .output()
            .expect("run vc-server");

        assert_eq!(output.status.code(), Some(2));
        assert!(String::from_utf8_lossy(&output.stderr).contains("invalid lifecycle nonce"));
    }
}

#[test]
fn lifecycle_nonce_accepts_canonical_value_forms() {
    for args in [
        vec!["--lifecycle-nonce", VALID_NONCE, "--version"],
        vec![
            "--lifecycle-nonce=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "--version",
        ],
    ] {
        let output = server_command().args(args).output().expect("run vc-server");

        assert!(output.status.success());
        assert!(String::from_utf8_lossy(&output.stdout).starts_with("vc-server "));
    }
}

fn http_await_json(port: u16, run_id: &str) -> Value {
    let path = format!(
        "/api/control/runs/{run_id}/await?idle_timeout=5&hard_cap=10"
    );
    let mut stream = TcpStream::connect(("127.0.0.1", port)).expect("await connect");
    stream
        .set_read_timeout(Some(Duration::from_secs(15)))
        .expect("await read timeout");
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .expect("await write timeout");
    stream
        .write_all(
            format!(
                "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAccept: application/json\r\nConnection: close\r\n\r\n"
            )
            .as_bytes(),
        )
        .expect("await request");
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).expect("await response");
    let text = String::from_utf8_lossy(&raw);
    let body = text
        .split("\r\n\r\n")
        .nth(1)
        .expect("HTTP body after headers");
    let json_body = if text.to_ascii_lowercase().contains("transfer-encoding: chunked") {
        decode_chunked_body(body)
    } else {
        body.trim().to_string()
    };
    serde_json::from_str(&json_body).unwrap_or_else(|err| {
        panic!("server await JSON: {err}; body={json_body:?} raw={text:?}");
    })
}

fn decode_chunked_body(body: &str) -> String {
    let mut rest = body;
    let mut out = String::new();
    loop {
        let (size_line, after) = rest.split_once("\r\n").expect("chunk size");
        let size = usize::from_str_radix(size_line.trim(), 16).expect("chunk hex");
        if size == 0 {
            break;
        }
        out.push_str(&after[..size]);
        rest = after[size..].strip_prefix("\r\n").unwrap_or(&after[size..]);
    }
    out
}

#[test]
fn twenty_real_http_await_clients_share_one_server_observation() {
    // CLI await is dispatcher UDS (see vibecrafted-core/tests/test_run_signal.py).
    // This fixture is the server HTTP projection only: twenty real TCP clients
    // share one in-process monitor. It must not spawn vibecrafted_core.cli.
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::process::CommandExt;
    use std::sync::{Arc, Mutex};

    let root = fixture_root();
    let home = root.join("home");
    let config_home = root.join("config");
    let site_root = root.join("site");
    fs::create_dir_all(config_home.join("vibecrafted")).expect("config directory");
    fs::create_dir_all(&site_root).expect("site root");
    let port = free_port();
    fs::write(
        config_home.join("vibecrafted/config.toml"),
        format!(
            "[server]\nbind_host = \"127.0.0.1\"\nport = {port}\npublic_url = \"http://127.0.0.1:{port}\"\n"
        ),
    )
    .expect("server config");
    write_runtime_meta(&home, "running", None);

    let writer = root.join("control-plane-writer.sh");
    fs::write(&writer, "#!/bin/sh\nexit 0\n").expect("writer shim");
    let mut permissions = fs::metadata(&writer)
        .expect("writer metadata")
        .permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(&writer, permissions).expect("writer executable");

    let mut server = server_command();
    server
        .args(["--addr", &format!("127.0.0.1:{port}")])
        .env("VIBECRAFTED_HOME", &home)
        .env("VC_RUN_OBSERVATION_WRITER", &writer)
        .env("VC_RUN_OBSERVATION_WRITER_TIMEOUT_SECONDS", "5")
        .env("VC_RUN_AWAIT_POLL_SECONDS", "0.03")
        .env("VC_RUN_AWAIT_EMPTY_GRACE_SECONDS", "0.05")
        .env("VC_SERVER_SITE_ROOT", &site_root)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0);
    let mut server = ChildGuard(server.spawn().expect("start isolated vc-server"));
    wait_for_server(port);

    let errors = Arc::new(Mutex::new(Vec::new()));
    let mut joins = Vec::new();
    for _ in 0..20 {
        let errors = Arc::clone(&errors);
        joins.push(thread::spawn(move || {
            match std::panic::catch_unwind(|| http_await_json(port, "run-http-fanin")) {
                Ok(payload) => Some(payload),
                Err(panic) => {
                    errors.lock().expect("error lock").push(format!("{panic:?}"));
                    None
                }
            }
        }));
    }
    // Give every TCP client time to join the shared server monitor before
    // terminal settlement. This is join-window, not await-timeout inflation.
    thread::sleep(Duration::from_secs(2));
    write_runtime_meta(&home, "report_validated", Some(0));

    let mut observations = Vec::new();
    for join in joins {
        let payload = join
            .join()
            .expect("http client thread")
            .unwrap_or_else(|| {
                stop_child(&mut server);
                let errs = errors.lock().expect("error lock");
                panic!("HTTP await client failed: {errs:?}");
            });
        if payload["outcome"] != "terminal"
            || payload["subscription"]["ownership"] != "server_await_subscription"
        {
            stop_child(&mut server);
            let _ = fs::remove_dir_all(&root);
            panic!("unexpected HTTP await payload: {payload}");
        }
        observations.push(payload["generated_at"].clone());
    }
    assert!(observations.iter().all(|stamp| stamp == &observations[0]));

    stop_child(&mut server);
    let _ = fs::remove_dir_all(root);
}
