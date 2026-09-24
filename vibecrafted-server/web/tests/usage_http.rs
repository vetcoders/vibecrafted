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

#[tokio::test]
async fn quota_dashboard_reads_monitor_snapshots_and_stays_quiet_when_absent() {
    let _home = TestHome::new();
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_secs();
    let agy = json!({
        "ts": ts,
        "model_id": "gemini-3.1-pro-high",
        "metrics": {"estimated_tokens": 12400, "cost_usd": 0.042},
        "quota": {"status": "RESOURCE_EXHAUSTED", "quota_reset_in": "2h"}
    });
    let kimi = json!({
        "ts": ts,
        "kind": "ok",
        "plan": "Coding",
        "limit5h": {"usedRatio": 0.97},
        "monthTotal": {"usedRatio": 0.41},
        "monthCode": {"usedRatio": 0.12}
    });
    unsafe {
        std::env::set_var("VIBECRAFTED_AGY_QUOTA_JSON", agy.to_string());
        std::env::set_var("VIBECRAFTED_KIMI_QUOTA_JSON", kimi.to_string());
    }

    let (status, cache, board) = get("/api/usage/quota").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(cache, "no-store");
    assert_eq!(board["schema"], "vibecrafted.quota-dashboard.v1");
    let agents = board["agents"].as_array().expect("agents");
    assert_eq!(agents.len(), 2);
    assert_eq!(agents[0]["id"], "agy");
    assert_eq!(agents[0]["status"], "blocked");
    assert_eq!(agents[0]["headline"], "EXHAUSTED");
    assert!(
        agents[0]["detail"]
            .as_str()
            .expect("detail")
            .contains("api-equiv")
    );
    assert_eq!(agents[0]["tokens"], 12400);
    assert_eq!(agents[1]["id"], "kimi");
    assert_eq!(agents[1]["status"], "blocked");
    assert_eq!(agents[1]["bars"][0]["label"], "5h");
    assert_eq!(agents[1]["bars"][0]["level"], "blocked");
    assert_eq!(agents[1]["bars"][1]["text"], "41%");

    unsafe {
        std::env::set_var("VIBECRAFTED_AGY_QUOTA_JSON", "/nonexistent/agy-quota.json");
        std::env::set_var(
            "VIBECRAFTED_KIMI_QUOTA_JSON",
            "/nonexistent/kimi-quota.json",
        );
    }
    let (status, _, quiet) = get("/api/usage/quota").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(quiet["agents"][0]["present"], false);
    assert_eq!(quiet["agents"][0]["status"], "absent");
    assert_eq!(quiet["agents"][1]["status"], "absent");

    unsafe {
        std::env::remove_var("VIBECRAFTED_AGY_QUOTA_JSON");
        std::env::remove_var("VIBECRAFTED_KIMI_QUOTA_JSON");
    }
}
