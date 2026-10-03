//! The HTTP door invokes the real Python writer in an isolated generation/home.
#![cfg(feature = "ssr")]

use std::fs;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use axum::body::{Body, to_bytes};
use axum::extract::ConnectInfo;
use axum::http::{Request, StatusCode};
use leptos::config::LeptosOptions;
use serde_json::{Value, json};
use tower::ServiceExt;
use vibecrafted_server_web::dispatch::dispatch_routes;

struct Fixture {
    root: PathBuf,
    saved: Vec<(&'static str, Option<std::ffi::OsString>)>,
}

impl Drop for Fixture {
    fn drop(&mut self) {
        for (key, previous) in self.saved.drain(..).rev() {
            unsafe {
                if let Some(value) = previous {
                    std::env::set_var(key, value);
                } else {
                    std::env::remove_var(key);
                }
            }
        }
        fs::remove_dir_all(&self.root).ok();
    }
}

fn command(program: &Path, args: &[&str], cwd: &Path) -> String {
    let output = Command::new(program)
        .args(args)
        .current_dir(cwd)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).unwrap().trim().into()
}

async fn post(payload: &Value, peer: Option<&str>, origin: Option<&str>) -> (StatusCode, Value) {
    let app =
        dispatch_routes().with_state(LeptosOptions::builder().output_name("claim-test").build());
    let mut request = Request::builder()
        .method("POST")
        .uri("/api/dispatch/claim")
        .header("content-type", "application/json");
    if let Some(origin) = origin {
        request = request.header("origin", origin);
    }
    let mut request = request.body(Body::from(payload.to_string())).unwrap();
    if let Some(peer) = peer {
        request
            .extensions_mut()
            .insert(ConnectInfo(peer.parse::<SocketAddr>().unwrap()));
    }
    let response = app.oneshot(request).await.unwrap();
    let status = response.status();
    assert_eq!(response.headers()["cache-control"], "no-store");
    let bytes = to_bytes(response.into_body(), 64 * 1024).await.unwrap();
    (status, serde_json::from_slice(&bytes).unwrap())
}

#[tokio::test]
async fn claim_post_is_a_python_owned_unverified_receipt() {
    // A single test owns process environment in this integration-test binary.
    let root = std::env::temp_dir().join(format!(
        "vc-claim-http-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    fs::create_dir_all(&root).unwrap();
    let mut fixture = Fixture {
        root: root.clone(),
        saved: Vec::new(),
    };
    let home = root.join("home");
    let repo = root.join("repo");
    fs::create_dir_all(&repo).unwrap();
    let report = repo.join("report.md");
    fs::write(&report, "Measured owned scope.\n").unwrap();
    let tracker = repo.join("tracker.md");
    let tracker_before = "- [ ] cut-1\n- [ ] cut-embargo\n";
    fs::write(&tracker, tracker_before).unwrap();
    command(Path::new("git"), &["init", "-q"], &repo);
    command(Path::new("git"), &["add", "report.md", "tracker.md"], &repo);
    command(
        Path::new("git"),
        &[
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        &repo,
    );
    let sha = command(Path::new("git"), &["rev-parse", "HEAD"], &repo);

    let generation = root.join("generation");
    command(
        Path::new("python3"),
        &[
            "-m",
            "venv",
            "--without-pip",
            "--system-site-packages",
            generation.to_str().unwrap(),
        ],
        &root,
    );
    let python = generation.join("bin/python3");
    let core = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../vibecrafted-core")
        .canonicalize()
        .unwrap();
    // Mirror a runtime pack: modules are beside Python, not installed into
    // site-packages and not inherited from the worker's PYTHONPATH.
    std::os::unix::fs::symlink(core, generation.join("vibecrafted-core")).unwrap();
    fs::create_dir(generation.join("python-site")).unwrap();
    for (key, value) in [
        ("VIBECRAFTED_HOME", home.as_os_str()),
        ("VIBECRAFTED_PYTHON", python.as_os_str()),
        ("VIBECRAFTED_RUNTIME_ROOT", generation.as_os_str()),
    ] {
        fixture.saved.push((key, std::env::var_os(key)));
        unsafe {
            std::env::set_var(key, value);
        }
    }
    // Deliberately poison PYTHONPATH: isolated Python must load the selected
    // generation, never arbitrary request/worker module roots.
    fixture
        .saved
        .push(("PYTHONPATH", std::env::var_os("PYTHONPATH")));
    unsafe {
        std::env::set_var("PYTHONPATH", root.join("untrusted"));
    }

    let receipts = home.join("control_plane/dispatches/disp-test/receipts.json");
    fs::create_dir_all(receipts.parent().unwrap()).unwrap();
    let cut = |id: &str| json!({"cut_id":id, "state":"active", "worktree_path":repo, "report_path":report, "artifact_path":repo, "provider_run_id":"worker-1"});
    let mut seed = json!({"schema":"vibecrafted.dispatch-receipts.v1", "run_id":"disp-test", "repo_root":repo, "cuts":{"cut-1":cut("cut-1"), "cut-embargo":cut("cut-embargo")}});
    seed["cuts"]["cut-embargo"]["compile_embargo"] = json!(true);
    fs::write(&receipts, seed.to_string()).unwrap();
    let payload = json!({"run_id":"disp-test", "cut_id":"cut-1", "commit_sha":sha, "report_path":report, "measurements":["owned scope self-check passed"]});

    assert_eq!(
        post(&json!("not an object"), Some("127.0.0.1:4000"), None)
            .await
            .0,
        StatusCode::BAD_REQUEST
    );
    let mut oversized = payload.clone();
    oversized["measurements"] = json!(["m".repeat(64 * 1024)]);
    assert_eq!(
        post(&oversized, Some("127.0.0.1:4000"), None).await.0,
        StatusCode::PAYLOAD_TOO_LARGE
    );
    unsafe {
        std::env::set_var("VIBECRAFTED_PYTHON", root.join("missing-python"));
    }
    assert_eq!(
        post(&payload, Some("127.0.0.1:4000"), None).await.0,
        StatusCode::SERVICE_UNAVAILABLE
    );
    unsafe {
        std::env::set_var("VIBECRAFTED_PYTHON", &python);
    }

    for (peer, origin) in [
        (None, None),
        (Some("192.0.2.1:4000"), None),
        (Some("127.0.0.1:4000"), Some("https://foreign.invalid")),
        (Some("127.0.0.1:4000"), Some("null")),
    ] {
        assert_eq!(post(&payload, peer, origin).await.0, StatusCode::FORBIDDEN);
        assert_eq!(fs::read_to_string(&receipts).unwrap(), seed.to_string());
    }
    let (status, body) = post(&payload, Some("127.0.0.1:4000"), None).await;
    assert_eq!(status, StatusCode::ACCEPTED, "{body}");
    assert_eq!(body["marker"], "[~]");
    assert_eq!(body["verification"], "unverified");
    assert_eq!(body["writer"], "vibecrafted_core.dispatch.claims");
    let stored: Value = serde_json::from_str(&fs::read_to_string(&receipts).unwrap()).unwrap();
    assert_eq!(stored["cuts"]["cut-1"]["claim_marker"], "[~]");
    assert_eq!(stored["cuts"]["cut-1"]["acceptance"], "unverified");
    assert_eq!(stored["cuts"]["cut-1"]["state"], "active");
    assert_eq!(
        stored["cuts"]["cut-1"]["claim_writer"],
        "vibecrafted_core.dispatch.claims"
    );
    let writer_pid = stored["cuts"]["cut-1"]["claim_writer_pid"]
        .as_u64()
        .unwrap();
    assert_ne!(
        writer_pid,
        u64::from(std::process::id()),
        "Python subprocess wrote the receipt, not this HTTP process"
    );
    assert_eq!(fs::read_to_string(&tracker).unwrap(), tracker_before);

    let mut checkpoint = payload.clone();
    checkpoint["cut_id"] = json!("cut-embargo");
    assert_eq!(
        post(&checkpoint, Some("[::1]:4000"), None).await.0,
        StatusCode::BAD_REQUEST,
        "an embargo cut must enumerate its skipped controls"
    );
    checkpoint["checkpoint"] =
        json!({"owned_scope":["src/editor.rs"], "skipped_controls":["cargo test", "semgrep"]});
    let (status, body) = post(&checkpoint, Some("[::1]:4000"), None).await;
    assert_eq!(status, StatusCode::ACCEPTED, "{body}");
    let stored: Value = serde_json::from_str(&fs::read_to_string(&receipts).unwrap()).unwrap();
    assert_eq!(stored["cuts"]["cut-embargo"]["acceptance"], "unverified");
    assert_eq!(
        stored["cuts"]["cut-embargo"]["claim"]["checkpoint"]["skipped_controls"],
        json!(["cargo test", "semgrep"])
    );
    assert_eq!(fs::read_to_string(&tracker).unwrap(), tracker_before);

    let mut forged_settlement = checkpoint.clone();
    forged_settlement["checkpoint"]["closed"] = json!(true);
    forged_settlement["tracker_status"] = json!("[x]");
    assert_eq!(
        post(&forged_settlement, Some("127.0.0.1:4000"), None)
            .await
            .0,
        StatusCode::BAD_REQUEST,
        "the writer must refuse worker-supplied settlement or embargo closure"
    );
    assert_eq!(fs::read_to_string(&tracker).unwrap(), tracker_before);

    let mut foreign = payload.clone();
    foreign["run_id"] = json!("disp-unknown");
    assert_eq!(
        post(&foreign, Some("127.0.0.1:4000"), None).await.0,
        StatusCode::BAD_REQUEST
    );
    assert!(!home.join("control_plane/dispatches/disp-unknown").exists());
    let handler = include_str!("../src/dispatch.rs");
    assert!(
        !handler.contains("fs::write"),
        "durable receipt belongs to Python"
    );
    assert!(
        !handler.contains("OpenOptions"),
        "HTTP must not open a durable write path"
    );
}
