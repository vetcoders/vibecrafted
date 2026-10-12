#![cfg(feature = "ssr")]

use std::fs;
use std::io::ErrorKind;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode, header};
use leptos::config::{Env, LeptosOptions};
use serde_json::{Value, json};
use tower::ServiceExt;
use vibecrafted_server_web::control::api::control_routes_for;

struct TestHome(PathBuf);

impl TestHome {
    fn new() -> Self {
        static NEXT_ID: AtomicU64 = AtomicU64::new(0);
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let path = (0..100)
            .find_map(|attempt| {
                let nonce = NEXT_ID.fetch_add(1, Ordering::Relaxed);
                let candidate = std::env::temp_dir().join(format!(
                    "vc-usage-http-{}-{nanos}-{nonce}-{attempt}",
                    std::process::id()
                ));
                match fs::create_dir(&candidate) {
                    Ok(()) => Some(candidate),
                    Err(error) if error.kind() == ErrorKind::AlreadyExists => None,
                    Err(error) => panic!("create isolated usage home: {error}"),
                }
            })
            .expect("allocate an isolated usage home");
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
        Self(path)
    }
}

impl Drop for TestHome {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).ok();
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
    control_routes_for(home).with_state(opts)
}

async fn get(home: &std::path::Path, uri: &str) -> (StatusCode, String, Value) {
    let response = test_app(home)
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
    let home = TestHome::new();

    let (status, cache, report) = get(&home.0, "/api/usage?window=24h&agent=codex").await;
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

    let (status, cache, error) = get(&home.0, "/api/usage?window=yesterday").await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(cache, "no-store");
    assert!(error["error"].as_str().expect("error").contains("24h"));

    let (status, _, empty) = get(&home.0, "/api/usage?window=24h&agent=kimi").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(empty["totals"]["runs"], 0);
    assert!(empty["runs"].as_array().expect("runs").is_empty());
}

#[tokio::test]
async fn usage_api_marks_origin_and_every_picker_option_round_trips() {
    let home = TestHome::new();
    // Host activity the collector left behind (the real fleet_engine entry shape).
    let dir = home.0.join("telemetry/claude");
    fs::create_dir_all(&dir).expect("telemetry dir");
    let mtime = chrono::Utc::now().timestamp() as f64 - 60.0;
    fs::write(
        dir.join("scan-cache.json"),
        serde_json::to_vec(
            &json!({"files": {"/s/claude/aaaa1111-2222-3333-4444-555566667777.jsonl": {
                "parser": 3,
                "days": [{"day": "2026-10-12", "model": "claude-opus-5-5",
                          "input_tokens": 10, "output_tokens": 5,
                          "cache_read_tokens": 0, "cache_creation_tokens": 0}],
                "record": {"id": "aaaa1111-2222-3333-4444-555566667777", "mtime": mtime}
            }}}),
        )
        .expect("json"),
    )
    .expect("scan cache");

    let (status, _, all) = get(&home.0, "/api/usage?window=all").await;
    assert_eq!(status, StatusCode::OK);
    let origins: Vec<_> = all["runs"]
        .as_array()
        .expect("runs")
        .iter()
        .map(|run| run["origin"].as_str().expect("origin").to_string())
        .collect();
    assert!(origins.contains(&"vc".to_string()) && origins.contains(&"local".to_string()));
    assert_eq!(all["local_sources"].as_array().expect("sources").len(), 8);

    // The picker lists exactly the dimension names the API reports; choosing any
    // of them must return exactly that dimension's runs.
    for (picker, param) in [
        ("providers", "provider"),
        ("agents", "agent"),
        ("models", "model"),
    ] {
        let names = all["dimensions"][picker].as_array().expect("dimension");
        assert!(!names.is_empty(), "{picker}");
        for dimension in names {
            let name = dimension["name"].as_str().expect("name");
            let uri = format!("/api/usage?window=all&{param}={name}");
            let (status, _, filtered) = get(&home.0, &uri).await;
            assert_eq!(status, StatusCode::OK, "{uri}");
            assert_eq!(filtered["totals"]["runs"], dimension["runs"], "{uri}");
        }
    }
}
