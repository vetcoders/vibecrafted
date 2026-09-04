//! HTTP contract for `GET /api/control/observability` — the split-observability
//! index standing next to the caretaker route.
//!
//! The property under guard: logs, metrics and the run board are projections
//! of ONE control plane, not three independent stores. If a future change
//! re-sources a projection from somewhere else (a second database, a sidecar
//! file, an in-process counter), this contract fails — because that is exactly
//! how observability splits into competing truths.
//!
//! Also guarded, mirroring the caretaker route's doctrine: the index answers
//! `200` even when nothing is published (answering is the liveness proof), and
//! absent projection sources are named with reasons rather than rendered as
//! healthy empty surfaces.

#![cfg(feature = "ssr")]

use std::fs;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode};
use leptos::config::{Env, LeptosOptions};
use serde_json::Value;
use tower::ServiceExt;
use vibecrafted_server_web::control::api::control_routes;

struct TestHome(PathBuf);

impl TestHome {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "vc-observability-http-{}-{}",
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

    fn control_plane(&self) -> PathBuf {
        self.0.join("control_plane")
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

async fn get_observability() -> (StatusCode, Option<String>, Value) {
    let response = test_app()
        .oneshot(
            Request::builder()
                .uri("/api/control/observability")
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

#[tokio::test]
async fn observability_index_names_projections_of_one_control_plane() {
    let home = TestHome::new();

    // An empty plane still gets a 200: answering is the liveness proof, and
    // "nothing published yet" must stay distinguishable from "server down".
    let (status, cache_control, body) = get_observability().await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(cache_control.as_deref(), Some("no-store"));
    assert_eq!(body["schema"], "vibecrafted.observability-view.v1");
    assert!(
        !body["server_version"]
            .as_str()
            .expect("server version")
            .is_empty(),
        "the view identifies the serving build"
    );

    let control_plane = body["control_plane"].as_str().expect("control_plane");
    assert_eq!(control_plane, home.control_plane().display().to_string());

    let projections = body["projections"].as_array().expect("projections");
    let by_name: std::collections::BTreeMap<&str, &Value> = projections
        .iter()
        .map(|row| (row["name"].as_str().expect("name"), row))
        .collect();

    // The split the product promises: logs, metrics and the run board all
    // exist, all named as projections of the same control plane.
    for name in ["logs", "metrics", "run-board"] {
        let row = by_name
            .get(name)
            .unwrap_or_else(|| panic!("missing projection {name}"));
        assert_eq!(
            row["source"], "control_plane",
            "{name} must read the one store"
        );
        let source_path = row["source_path"].as_str().expect("source_path");
        assert!(
            source_path.starts_with(&format!("{control_plane}/")),
            "{name} escapes the control plane: {source_path}"
        );
        assert!(
            row["route"]
                .as_str()
                .expect("route")
                .starts_with("/api/control/"),
            "{name} must be served by the control surface"
        );
    }

    // On an empty plane every projection is honestly unavailable with a reason.
    for row in projections {
        assert_eq!(row["available"], false, "empty plane: {}", row["name"]);
        assert!(
            !row["reason"].as_str().expect("reason").is_empty(),
            "absence must be named: {row}"
        );
    }

    // Publish the sources and the same index turns available — observed from
    // the filesystem, never asserted from config.
    fs::create_dir_all(home.control_plane().join("runs")).expect("runs dir");
    fs::create_dir_all(home.control_plane().join("lifecycle_runs")).expect("lifecycle dir");
    fs::write(home.control_plane().join("events.jsonl"), "").expect("event stream");
    fs::write(
        home.control_plane().join("caretaker.json"),
        r#"{"schema": "vibecrafted.caretaker.v1"}"#,
    )
    .expect("caretaker snapshot");

    let (status, _, body) = get_observability().await;
    assert_eq!(status, StatusCode::OK);
    let projections = body["projections"].as_array().expect("projections");
    for row in projections {
        assert_eq!(
            row["available"], true,
            "published source must read available: {}",
            row["name"]
        );
        assert_eq!(row["reason"], "", "available projection carries no reason");
    }

    // The caretaker projections point at the same envelope the caretaker route
    // serves — metrics are not a second metrics store.
    let metrics = by_name["metrics"].clone();
    assert_eq!(metrics["route"], "/api/control/caretaker");
    assert!(
        metrics["source_path"]
            .as_str()
            .expect("metrics source")
            .ends_with("/caretaker.json"),
        "metrics are the caretaker envelope's observability section"
    );
}
