//! Integration tests for `POST /api/scaffold/status` and control event SSE stream bridge.

#![cfg(feature = "ssr")]

use std::fs;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode, header};
use futures_util::StreamExt;
use leptos::config::{Env, LeptosOptions};
use tower::ServiceExt;
use vibecrafted_server_web::control::api::{SsePace, control_routes_with};
use vibecrafted_server_web::scaffold::api::scaffold_routes;

static ENV_LOCK: Mutex<()> = Mutex::new(());

struct TempHome {
    path: PathBuf,
    _guard: std::sync::MutexGuard<'static, ()>,
}

impl TempHome {
    fn new(label: &str) -> Self {
        let guard = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let path = std::env::temp_dir().join(format!(
            "vc-scaffold-status-{}-{}-{}",
            label,
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        fs::create_dir_all(&path).expect("create temp home");
        // Scaffold handlers still call `vibecrafted_home()` per request.
        // This binary has one test. Drop restores the variable. SSE pace
        // is passed into the control router and is not process env.
        unsafe {
            std::env::set_var("VIBECRAFTED_HOME", &path);
        }
        Self {
            path,
            _guard: guard,
        }
    }

    fn setup_scaffold_plan(&self, org: &str, repo: &str, day: &str, plan_id: &str) -> PathBuf {
        let plan_dir = self
            .path
            .join("artifacts")
            .join(org)
            .join(repo)
            .join(day)
            .join("plans")
            .join(plan_id);
        fs::create_dir_all(&plan_dir).expect("create plan dir");

        let manifest = serde_json::json!({
            "schema_version": "1",
            "org": org,
            "repo": repo,
            "day": day,
            "plan_id": plan_id,
            "created_at": "2026-07-28T08:00:00Z",
            "artifacts": [
                {
                    "id": "tracker",
                    "path": "tracker.md",
                    "role": "tracker",
                    "editable": true,
                    "required": true
                }
            ]
        });
        fs::write(
            plan_dir.join("manifest.json"),
            serde_json::to_string_pretty(&manifest).unwrap(),
        )
        .expect("write manifest");

        let tracker_md = format!(
            "---\nplan_id: {plan_id}\nsession_id: test-session\nrole: tracker\nagent: grok\ndate: 2026-07-28\nproject: {org}/{repo}\n---\n\n# Tracker\n\n- [ ] T1 First item\n- [ ] T2 Second item\n"
        );
        fs::write(plan_dir.join("tracker.md"), tracker_md).expect("write tracker.md");

        plan_dir
    }

    fn events_path(&self) -> PathBuf {
        self.path.join("control_plane").join("events.jsonl")
    }
}

impl Drop for TempHome {
    fn drop(&mut self) {
        unsafe {
            std::env::remove_var("VIBECRAFTED_HOME");
        }
        let _ = fs::remove_dir_all(&self.path);
    }
}

fn test_app(home: &std::path::Path) -> axum::Router {
    let opts = LeptosOptions::builder()
        .output_name("vibecrafted-server-web-test")
        .site_root("target/site-test")
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr("127.0.0.1:0".parse::<SocketAddr>().expect("addr"))
        .reload_port(0)
        .build();
    axum::Router::new()
        .merge(scaffold_routes())
        .merge(control_routes_with(
            home,
            SsePace {
                poll: Duration::from_millis(50),
                keepalive: Duration::from_millis(200),
            },
        ))
        .with_state(opts)
}

async fn collect_sse_until(
    body: Body,
    mut pred: impl FnMut(&str) -> bool,
    timeout: Duration,
) -> String {
    let mut collected = String::new();
    let mut stream = body.into_data_stream();
    let deadline = tokio::time::Instant::now() + timeout;
    loop {
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            break;
        }
        match tokio::time::timeout(remaining, stream.next()).await {
            Ok(Some(Ok(chunk))) => {
                let text = String::from_utf8_lossy(&chunk);
                collected.push_str(&text);
                if pred(&collected) {
                    break;
                }
            }
            Ok(Some(Err(err))) => panic!("body stream error: {err}"),
            Ok(None) => break,
            Err(_) => break,
        }
    }
    collected
}

#[tokio::test]
async fn post_scaffold_status_updates_disk_and_emits_control_event_sse() {
    let home = TempHome::new("scaffold-status");
    let plan_dir = home.setup_scaffold_plan("vetcoders", "vibecrafted", "2026_0728", "plan-alpha");

    let app = test_app(&home.path);

    // 1. Post typed status update via JSON to /api/scaffold/status
    let payload = serde_json::json!({
        "org": "vetcoders",
        "repo": "vibecrafted",
        "day": "2026_0728",
        "plan_id": "plan-alpha",
        "artifact_id": "tracker",
        "item_index": 0,
        "status": "x"
    });

    let req = Request::builder()
        .method("POST")
        .uri("/api/scaffold/status")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(payload.to_string()))
        .unwrap();

    let response = app.clone().oneshot(req).await.expect("post status");
    let status_code = response.status();
    let body_bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("body bytes");
    let body_str = String::from_utf8_lossy(&body_bytes);
    assert_eq!(
        status_code,
        StatusCode::OK,
        "Expected 200 OK from /api/scaffold/status, got {status_code}: {body_str}"
    );

    let res_json: serde_json::Value =
        serde_json::from_slice(&body_bytes).expect("parse json response");
    assert_eq!(res_json["status"], "ok");

    // 2. Verify tracker.md on disk was updated to [x]
    let updated_tracker = fs::read_to_string(plan_dir.join("tracker.md")).expect("read tracker.md");
    assert!(
        updated_tracker.contains("- [x] T1 First item"),
        "tracker.md should have [x] for T1, got:\n{updated_tracker}"
    );

    // 3. Verify .scaffold-changes.jsonl
    let changes_path = plan_dir.join(".scaffold-changes.jsonl");
    assert!(changes_path.is_file(), ".scaffold-changes.jsonl must exist");
    let changes_content = fs::read_to_string(&changes_path).expect("read changes");
    assert!(changes_content.contains("\"action\":\"status\""));

    // 4. Verify control_plane/events.jsonl contains scaffold.status.updated
    let events_path = home.events_path();
    assert!(events_path.is_file(), "events.jsonl must exist");
    let events_content = fs::read_to_string(&events_path).expect("read events");
    assert!(
        events_content.contains("scaffold.status.updated"),
        "events.jsonl must contain scaffold.status.updated, got:\n{events_content}"
    );
    assert!(events_content.contains("plan-alpha"));

    // 5. Connect to GET /api/control/events SSE stream and verify event delivery
    let sse_req = Request::builder()
        .uri("/api/control/events")
        .body(Body::empty())
        .unwrap();

    let sse_res = app.oneshot(sse_req).await.expect("sse request");
    assert_eq!(sse_res.status(), StatusCode::OK);

    let body = collect_sse_until(
        sse_res.into_body(),
        |s| s.contains("scaffold.status.updated"),
        Duration::from_secs(3),
    )
    .await;

    assert!(
        body.contains("scaffold.status.updated"),
        "SSE stream should deliver scaffold.status.updated event, got:\n{body}"
    );
}
