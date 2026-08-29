//! HTTP contract for `GET /api/control/caretaker`.
//!
//! The route is the tray's single truth. Three properties are load-bearing and
//! each is asserted here, because losing any one of them silently restores the
//! ad-hoc multi-source fusion this surface replaced:
//!
//! * an unpublished envelope answers `200` with `published: false` and a
//!   reason, never a `404` a caller could confuse with "server down";
//! * a published envelope is served through unmodified, with freshness added;
//! * a corrupt envelope is reported as corrupt, not silently downgraded to the
//!   same shape as "never published".

#![cfg(feature = "ssr")]

use std::fs;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

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
            "vc-caretaker-http-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        fs::create_dir_all(path.join("control_plane")).expect("fixture control plane");
        // Safety: this integration binary contains one test, so the process-wide
        // home has a single owner for the whole test lifetime.
        unsafe {
            std::env::set_var("VIBECRAFTED_HOME", &path);
        }
        Self(path)
    }

    fn snapshot_path(&self) -> PathBuf {
        self.0.join("control_plane/caretaker.json")
    }

    fn publish(&self, payload: &Value) {
        fs::write(
            self.snapshot_path(),
            serde_json::to_vec_pretty(payload).expect("fixture JSON"),
        )
        .expect("publish fixture");
    }

    fn publish_raw(&self, raw: &str) {
        fs::write(self.snapshot_path(), raw).expect("publish raw fixture");
    }

    fn unpublish(&self) {
        let _ = fs::remove_file(self.snapshot_path());
    }
}

impl Drop for TestHome {
    fn drop(&mut self) {
        unsafe {
            std::env::remove_var("VIBECRAFTED_HOME");
        }
        let _ = fs::remove_dir_all(&self.0);
    }
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

async fn get_caretaker() -> (StatusCode, Option<String>, Value) {
    let response = test_app()
        .oneshot(
            Request::builder()
                .uri("/api/control/caretaker")
                .body(Body::empty())
                .expect("request"),
        )
        .await
        .expect("response");
    let status = response.status();
    let cache_control = response
        .headers()
        .get("cache-control")
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    let body = to_bytes(response.into_body(), 1024 * 1024)
        .await
        .expect("response body");
    (
        status,
        cache_control,
        serde_json::from_slice(&body).expect("response JSON"),
    )
}

fn caretaker_fixture() -> Value {
    json!({
        "schema": "vibecrafted.caretaker.v1",
        "generated_at": "2026-08-29T08:00:00+00:00",
        "control_plane": "/fixture/control_plane",
        "server": {
            "available": true,
            "endpoint": { "host": "127.0.0.1", "port": 3024, "url": "http://127.0.0.1:3024" },
            "state": "healthy",
            "supervisor_pid": 4242,
            "receipt": { "present": true, "stale": false, "age_seconds": 1.0 },
            "liveness": { "probed": true, "reachable": true, "reason": "", "version": "4.3.0" }
        },
        "observability": { "available": true, "run_snapshots": 7 },
        "resumeability": { "available": true, "matched": 3, "counts": { "operator_resume": 3 } },
        "maintenance": { "available": true, "findings": [] },
        "verdict": {
            "health": "healthy",
            "server_health": "healthy",
            "header": "VC Server: HEALTHY · 127.0.0.1:3024",
            "detail": "Supervisor PID 4242",
            "findings": []
        }
    })
}

#[tokio::test]
async fn caretaker_route_reports_publication_freshness_and_corruption() {
    let home = TestHome::new();

    // Never published: the route still answers, because answering at all is the
    // liveness proof. A 404 here would be indistinguishable from a dead server.
    home.unpublish();
    let (status, cache_control, absent) = get_caretaker().await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(cache_control.as_deref(), Some("no-store"));
    assert_eq!(absent["schema"], "vibecrafted.caretaker-view.v1");
    assert_eq!(absent["published"], false);
    assert!(absent["snapshot"].is_null());
    assert_eq!(absent["stale"], true);
    assert!(
        absent["reason"]
            .as_str()
            .expect("reason")
            .contains("not published"),
        "unpublished reason must name the condition: {absent}"
    );
    assert!(
        !absent["server_version"]
            .as_str()
            .expect("server version")
            .is_empty(),
        "the view identifies the serving build"
    );

    // Published: the snapshot is served through unmodified, and the derived
    // verdict crosses the wire intact so no consumer re-derives health.
    let fixture = caretaker_fixture();
    home.publish(&fixture);
    let (status, _, published) = get_caretaker().await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(published["published"], true);
    assert_eq!(published["snapshot"], fixture);
    assert_eq!(published["snapshot"]["verdict"]["health"], "healthy");
    assert_eq!(published["stale"], false);
    assert!(
        published["age_seconds"].as_f64().expect("age") < 60.0,
        "a just-written snapshot must read as fresh: {published}"
    );
    assert_eq!(published["reason"], "");

    // Corrupt: distinguishable from "never published", because the two need
    // different operator responses.
    home.publish_raw("{not json");
    let (status, _, corrupt) = get_caretaker().await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(corrupt["published"], false);
    assert_eq!(corrupt["stale"], true);
    assert!(
        corrupt["reason"]
            .as_str()
            .expect("reason")
            .contains("corrupt"),
        "corruption must be named, not collapsed into absence: {corrupt}"
    );

    // A non-object payload is equally unusable and equally must not masquerade
    // as a snapshot.
    home.publish_raw("[1, 2, 3]");
    let (_, _, non_object) = get_caretaker().await;
    assert_eq!(non_object["published"], false);
    assert!(
        non_object["reason"]
            .as_str()
            .expect("reason")
            .contains("not a JSON object"),
        "non-object payload must be named: {non_object}"
    );
}
