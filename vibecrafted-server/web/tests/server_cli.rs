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

#[test]
fn twenty_real_cli_await_clients_share_one_server_observation() {
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::process::CommandExt;

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

    let repo = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("repository root");
    let project_python = fs::canonicalize(repo.join(".venv/bin/python3"))
        .expect("resolve checkout Python without the macOS symlink launcher");
    assert!(
        project_python.is_file(),
        "checkout Python is required for CLI e2e"
    );
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
        .env("PYTHONPATH", repo.join("vibecrafted-core"))
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0);
    let mut server = ChildGuard(server.spawn().expect("start isolated vc-server"));
    wait_for_server(port);

    let mut clients = Vec::new();
    for _ in 0..20 {
        let mut command = Command::new(&project_python);
        command
            .args([
                "-m",
                "vibecrafted_core.cli",
                "await",
                "codex",
                "--run-id",
                "run-http-fanin",
                "--timeout",
                "5",
                "--hard-cap",
                "10",
                "--json",
            ])
            .current_dir(repo)
            .env_clear()
            .env("HOME", &root)
            .env("PATH", "/opt/homebrew/bin:/usr/bin:/bin")
            .env("PYTHONPATH", repo.join("vibecrafted-core"))
            .env("VIBECRAFTED_HOME", &home)
            .env("XDG_CONFIG_HOME", &config_home)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0);
        clients.push(ChildGuard(
            command.spawn().expect("start real CLI await client"),
        ));
    }
    // Keep the fixture live long enough for all real interpreter processes to
    // cross HTTP accept and join the same monitor before terminal settlement.
    thread::sleep(Duration::from_secs(2));
    write_runtime_meta(&home, "report_validated", Some(0));

    let deadline = Instant::now() + Duration::from_secs(15);
    while Instant::now() < deadline {
        if clients
            .iter_mut()
            .all(|child| child.try_wait().expect("poll CLI client").is_some())
        {
            break;
        }
        thread::sleep(Duration::from_millis(25));
    }
    let mut observations = Vec::new();
    for child in &mut clients {
        if child.try_wait().expect("final CLI poll").is_none() {
            stop_child(child);
            stop_child(&mut server);
            panic!("CLI await client exceeded deterministic test deadline");
        }
        let mut stdout = String::new();
        let mut stderr = String::new();
        child
            .stdout
            .take()
            .expect("captured stdout")
            .read_to_string(&mut stdout)
            .expect("read CLI stdout");
        child
            .stderr
            .take()
            .expect("captured stderr")
            .read_to_string(&mut stderr)
            .expect("read CLI stderr");
        let status = child.wait().expect("collect CLI status");
        if !status.success() {
            stop_child(&mut server);
            let _ = fs::remove_dir_all(&root);
            panic!("CLI failed with {status}: stdout={stdout:?} stderr={stderr:?}");
        }
        let payload: Value = serde_json::from_str(&stdout).expect("CLI verdict JSON");
        assert_eq!(payload["outcome"], "terminal");
        assert_eq!(
            payload["subscription"]["ownership"],
            "server_await_subscription"
        );
        observations.push(payload["generated_at"].clone());
    }
    assert!(observations.iter().all(|stamp| stamp == &observations[0]));

    stop_child(&mut server);
    let _ = fs::remove_dir_all(root);
}
