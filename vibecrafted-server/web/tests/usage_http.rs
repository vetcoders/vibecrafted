#![cfg(feature = "ssr")]

use std::fs;
use std::net::SocketAddr;
use std::path::PathBuf;

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode, header};
use leptos::config::{Env, LeptosOptions};
use serde_json::{Value, json};
use tower::ServiceExt;
use vibecrafted_server_web::control::api::control_routes;

struct TestHome(PathBuf);

impl TestHome {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "vc-usage-http-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        fs::create_dir_all(path.join("control_plane/runtime_runs/run-usd")).expect("run dir");
        fs::write(
            path.join("control_plane/runtime_runs/run-usd/meta.json"),
            serde_json::to_vec(&json!({
                "run_id": "run-usd",
                "provider": "openai",
                "agent": "codex",
                "agent_model": "gpt-5.6-terra",
                "status": "completed",
                "exit_code": 0,
                "completed_at": chrono::Utc::now().to_rfc3339(),
                "usage": {
                    "schema": "vibecrafted.usage.v1",
                    "unit": "tokens",
                    "source": "provider_stream",
                    "events": 1,
                    "tokens_input": 20,
                    "tokens_cached_input": 0,
                    "tokens_cache_write": 0,
                    "tokens_output": 5,
                    "tokens_total": 25
                },
                "cost": {"amount": 0.1, "currency": "USD", "source": "provider_reported"}
            }))
            .expect("json"),
        )
        .expect("meta");
        // Safety: this dedicated integration binary owns the process-wide home.
        unsafe { std::env::set_var("VIBECRAFTED_HOME", &path) };
        Self(path)
    }
}

impl Drop for TestHome {
    fn drop(&mut self) {
        unsafe { std::env::remove_var("VIBECRAFTED_HOME") };
        fs::remove_dir_all(&self.0).ok();
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

async fn get(uri: &str) -> (StatusCode, String, Value) {
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
    let cache = response
        .headers()
        .get(header::CACHE_CONTROL)
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default()
        .to_string();
    let bytes = to_bytes(response.into_body(), 1024 * 1024)
        .await
        .expect("body");
    (
        status,
        cache,
        serde_json::from_slice(&bytes).expect("JSON response"),
    )
}

#[tokio::test]
async fn usage_api_projects_filters_totals_and_validation() {
    let _home = TestHome::new();

    let (status, cache, report) = get("/api/usage?window=24h&agent=codex").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(cache, "no-store");
    assert_eq!(report["schema"], "vibecrafted.usage-report.v1");
    assert_eq!(report["filter"]["since"], "24h");
    assert_eq!(report["filter"]["agent"], "codex");
    assert_eq!(report["totals"]["runs"], 1);
    assert_eq!(report["totals"]["tokens_total_known"], 25);
    assert_eq!(report["totals"]["cost_by_unit"]["USD"], 0.1);
    assert_eq!(report["dimensions"]["providers"][0]["name"], "openai");
    assert_eq!(report["runs"][0]["run_id"], "run-usd");

    let (status, cache, error) = get("/api/usage?window=yesterday").await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(cache, "no-store");
    assert!(error["error"].as_str().expect("error").contains("24h"));

    let (status, _, empty) = get("/api/usage?window=24h&agent=kimi").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(empty["totals"]["runs"], 0);
    assert!(empty["runs"].as_array().expect("runs").is_empty());
}
