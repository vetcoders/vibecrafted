//! HTTP-level contract tests for server-owned run observation and await.

#![cfg(feature = "ssr")]

use std::fs;
use std::net::SocketAddr;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode};
use leptos::config::{Env, LeptosOptions};
use serde_json::{Value, json};
use tower::ServiceExt;
use vibecrafted_server_web::control::api::control_routes;

struct TestHome(PathBuf);

impl TestHome {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "vc-run-observation-http-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        fs::create_dir_all(path.join("control_plane/runtime_runs/run-http"))
            .expect("fixture run directory");
        let writer = path.join("writer.sh");
        let writer_mode = path.join("writer-mode");
        let writer_pid = path.join("writer.pid");
        fs::write(&writer_mode, "pass").expect("writer mode");
        fs::write(
            &writer,
            format!(
                "#!/bin/sh\nif [ \"$(cat '{}')\" = block ]; then\n  printf '%s' \"$$\" > '{}'\n  exec /bin/sleep 30\nfi\nexit 0\n",
                writer_mode.display(),
                writer_pid.display()
            ),
        )
        .expect("writer script");
        let mut permissions = fs::metadata(&writer)
            .expect("writer metadata")
            .permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&writer, permissions).expect("writer executable");
        // Safety: this integration binary contains one test, so its process-wide
        // observation configuration has a single owner for the test lifetime.
        unsafe {
            std::env::set_var("VIBECRAFTED_HOME", &path);
            std::env::set_var("VC_RUN_OBSERVATION_WRITER", &writer);
            std::env::set_var("VC_RUN_OBSERVATION_WRITER_TIMEOUT_SECONDS", "5");
            std::env::set_var("VC_RUN_AWAIT_POLL_SECONDS", "0.02");
            std::env::set_var("VC_RUN_AWAIT_EMPTY_GRACE_SECONDS", "0.03");
        }
        Self(path)
    }

    fn block_writer(&self) {
        fs::write(self.0.join("writer-mode"), "block").expect("block writer");
        let _ = fs::remove_file(self.0.join("writer.pid"));
    }

    fn allow_writer(&self) {
        fs::write(self.0.join("writer-mode"), "pass").expect("allow writer");
    }

    async fn wait_for_writer_pid(&self) -> u32 {
        tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                if let Ok(raw) = fs::read_to_string(self.0.join("writer.pid"))
                    && let Ok(pid) = raw.parse::<u32>()
                {
                    return pid;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("blocking writer pid")
    }

    fn write_meta(&self, state: &str, exit_code: Option<i32>) {
        fs::write(
            self.0.join("control_plane/runtime_runs/run-http/meta.json"),
            serde_json::to_vec(&json!({
                "run_id": "run-http",
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
            .expect("fixture JSON"),
        )
        .expect("write fixture meta");
    }
}

impl Drop for TestHome {
    fn drop(&mut self) {
        unsafe {
            std::env::remove_var("VIBECRAFTED_HOME");
            std::env::remove_var("VC_RUN_OBSERVATION_WRITER");
            std::env::remove_var("VC_RUN_OBSERVATION_WRITER_TIMEOUT_SECONDS");
            std::env::remove_var("VC_RUN_AWAIT_POLL_SECONDS");
            std::env::remove_var("VC_RUN_AWAIT_EMPTY_GRACE_SECONDS");
        }
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn process_exists(pid: u32) -> bool {
    Command::new("/bin/kill")
        .args(["-0", &pid.to_string()])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

fn test_app() -> axum::Router {
    let opts = LeptosOptions::builder()
        .output_name("vibecrafted-server-web-test")
        .site_root("target/site-test")
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr("127.0.0.1:0".parse::<SocketAddr>().expect("addr"))
        .reload_port(0)
        .build();
    control_routes().with_state(opts)
}

async fn get_json(uri: &str) -> (StatusCode, Value) {
    let response = test_app()
        .oneshot(
            Request::builder()
                .uri(uri)
                .body(Body::empty())
                .expect("request"),
        )
        .await
        .expect("response");
    let status = response.status();
    let body = to_bytes(response.into_body(), 1024 * 1024)
        .await
        .expect("response body");
    (
        status,
        serde_json::from_slice(&body).expect("response JSON"),
    )
}

#[tokio::test]
async fn http_contract_names_timeouts_unknown_and_terminal_fast_path() {
    let home = TestHome::new();
    home.write_meta("running", None);

    let (status, idle) =
        get_json("/api/control/runs/run-http/await?idle_timeout=0.08&hard_cap=2").await;
    assert_eq!(status, StatusCode::REQUEST_TIMEOUT);
    assert_eq!(idle["outcome"], "idle_stall");
    assert_eq!(idle["idle_timeout_seconds"], 0.08);
    assert_eq!(idle["hard_cap_seconds"], 2.0);

    tokio::time::sleep(Duration::from_millis(100)).await;
    home.block_writer();
    let (status, hard) =
        get_json("/api/control/runs/run-http/await?idle_timeout=2&hard_cap=0.08").await;
    assert_eq!(status, StatusCode::REQUEST_TIMEOUT);
    assert_eq!(hard["outcome"], "hard_cap");
    assert_eq!(hard["idle_timeout_seconds"], 2.0);
    assert_eq!(hard["hard_cap_seconds"], 0.08);
    let writer_pid = home.wait_for_writer_pid().await;
    tokio::time::timeout(Duration::from_secs(2), async {
        while process_exists(writer_pid) {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("HTTP hard-cap terminates and reaps writer child");
    home.allow_writer();

    let (status, missing) = get_json("/api/control/runs/run-does-not-exist/observe").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(missing["found"], false);
    assert_eq!(missing["schema"], "vibecrafted.run-observation.v1");

    tokio::time::sleep(Duration::from_millis(100)).await;
    home.write_meta("report_validated", Some(0));
    let (status, terminal) =
        get_json("/api/control/runs/run-http/await?idle_timeout=2&hard_cap=2").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(terminal["outcome"], "terminal");
    assert_eq!(terminal["completed"], true);
    assert_eq!(terminal["process_truth"], "terminal");
    assert_eq!(
        terminal["subscription"]["ownership"],
        "server_await_subscription"
    );
}
