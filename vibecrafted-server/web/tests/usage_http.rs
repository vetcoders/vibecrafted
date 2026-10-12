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
async fn quota_dashboard_reads_monitor_snapshots_and_stays_quiet_when_absent() {
    let home = TestHome::new();
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
    // 0.97 (or 0.95) is warn, NOT blocked!
    let kimi_warn = json!({
        "ts": ts,
        "kind": "ok",
        "plan": "Coding",
        "limit5h": {"usedRatio": 0.97},
        "monthTotal": {"usedRatio": 0.41},
        "monthCode": {"usedRatio": 0.12}
    });
    let kimi_exhausted = json!({
        "ts": ts,
        "kind": "ok",
        "plan": "Coding",
        "limit5h": {"usedRatio": 1.0},
        "monthTotal": {"usedRatio": 0.41},
        "monthCode": {"usedRatio": 0.12}
    });
    let codex_snapshot = json!({
        "agent": "codex",
        "status": "ok",
        "last_session": {"id": "sess-codex-1", "model": "gpt-5.6-terra", "processed_total": 4500},
        "metrics": {"processed_total": 4500, "fleet_processed_total": 9000, "session_count": 2},
        "quota": {"status": "ok"}
    });
    let cursor_snapshot = json!({
        "agent": "cursor",
        "status": "unavailable",
        "reason": "usage lives in Cursor cloud"
    });

    unsafe {
        std::env::set_var("VIBECRAFTED_AGY_QUOTA_JSON", agy.to_string());
        std::env::set_var("VIBECRAFTED_KIMI_QUOTA_JSON", kimi_warn.to_string());
        std::env::set_var("VIBECRAFTED_CODEX_QUOTA_JSON", codex_snapshot.to_string());
        std::env::set_var("VIBECRAFTED_CURSOR_QUOTA_JSON", cursor_snapshot.to_string());
    }

    let (status, cache, board) = get(&home.0, "/api/usage/quota").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(cache, "no-store");
    assert_eq!(board["schema"], "vibecrafted.quota-dashboard.v1");
    let agents = board["agents"].as_array().expect("agents");
    // All 8 providers are returned equally:
    assert_eq!(agents.len(), 8);

    // agy: RESOURCE_EXHAUSTED -> blocked
    let agy_agent = agents.iter().find(|a| a["id"] == "agy").expect("agy");
    assert_eq!(agy_agent["status"], "blocked");
    assert_eq!(agy_agent["headline"], "EXHAUSTED");
    assert!(
        agy_agent["detail"]
            .as_str()
            .expect("detail")
            .contains("api-equiv")
    );
    assert_eq!(agy_agent["tokens"], 12400);

    // kimi at 97% -> warn, NOT blocked!
    let kimi_agent = agents.iter().find(|a| a["id"] == "kimi").expect("kimi");
    assert_eq!(kimi_agent["status"], "warn");
    assert_eq!(kimi_agent["bars"][0]["label"], "5h");
    assert_eq!(kimi_agent["bars"][0]["level"], "warn");
    assert_eq!(kimi_agent["bars"][1]["text"], "41%");

    // codex unmeasured quota reports unknown, transparent source/time/scope
    let codex_agent = agents.iter().find(|a| a["id"] == "codex").expect("codex");
    assert_eq!(codex_agent["status"], "unknown");
    assert_eq!(codex_agent["headline"], "unmeasured");
    assert!(
        codex_agent["detail"]
            .as_str()
            .expect("detail")
            .contains("unmeasured quota")
    );

    // cursor unavailable reports unknown with cloud reason
    let cursor_agent = agents.iter().find(|a| a["id"] == "cursor").expect("cursor");
    assert_eq!(cursor_agent["status"], "unknown");
    assert_eq!(cursor_agent["headline"], "cloud-only");
    assert!(
        cursor_agent["detail"]
            .as_str()
            .expect("detail")
            .contains("Cursor cloud")
    );

    // Test on-demand query filtering:
    let (status, _, filtered_agy) = get(&home.0, "/api/usage/quota?agent=agy").await;
    assert_eq!(status, StatusCode::OK);
    let filtered_agents = filtered_agy["agents"].as_array().expect("agents");
    assert_eq!(filtered_agents.len(), 1);
    assert_eq!(filtered_agents[0]["id"], "agy");

    let (status, _, filtered_pool) = get(&home.0, "/api/usage/quota?pool=moonshot").await;
    assert_eq!(status, StatusCode::OK);
    let moonshot_agents = filtered_pool["agents"].as_array().expect("agents");
    assert_eq!(moonshot_agents.len(), 1);
    assert_eq!(moonshot_agents[0]["id"], "kimi");

    // Test confirmed 100% exhaustion on Kimi:
    unsafe {
        std::env::set_var("VIBECRAFTED_KIMI_QUOTA_JSON", kimi_exhausted.to_string());
    }
    let (status, _, exhausted_board) = get(&home.0, "/api/usage/quota?agent=kimi").await;
    assert_eq!(status, StatusCode::OK);
    let kimi_exhausted_agent = &exhausted_board["agents"][0];
    assert_eq!(kimi_exhausted_agent["status"], "blocked");
    assert_eq!(kimi_exhausted_agent["bars"][0]["level"], "blocked");

    // Test stale degradation: stale blocked snapshot becomes warn · stale!
    let stale_agy = json!({
        "ts": ts.saturating_sub(600), // 10 minutes ago (> 300s)
        "model_id": "gemini-3.1-pro-high",
        "metrics": {"estimated_tokens": 12400},
        "quota": {"status": "RESOURCE_EXHAUSTED", "quota_reset_in": "2h"}
    });
    unsafe {
        std::env::set_var("VIBECRAFTED_AGY_QUOTA_JSON", stale_agy.to_string());
    }
    let (status, _, stale_board) = get(&home.0, "/api/usage/quota?agent=agy").await;
    assert_eq!(status, StatusCode::OK);
    let stale_agent = &stale_board["agents"][0];
    assert_eq!(stale_agent["status"], "warn"); // degraded from blocked!
    assert_eq!(stale_agent["stale"], true);
    assert!(
        stale_agent["detail"]
            .as_str()
            .expect("detail")
            .contains("stale")
    );

    // Test absent monitor:
    unsafe {
        std::env::set_var("VIBECRAFTED_AGY_QUOTA_JSON", "/nonexistent/agy-quota.json");
        std::env::set_var(
            "VIBECRAFTED_KIMI_QUOTA_JSON",
            "/nonexistent/kimi-quota.json",
        );
        std::env::set_var(
            "VIBECRAFTED_CODEX_QUOTA_JSON",
            "/nonexistent/codex-quota.json",
        );
        std::env::set_var(
            "VIBECRAFTED_CURSOR_QUOTA_JSON",
            "/nonexistent/cursor-quota.json",
        );
    }
    let (status, _, quiet) = get(&home.0, "/api/usage/quota").await;
    assert_eq!(status, StatusCode::OK);
    let quiet_agents = quiet["agents"].as_array().expect("agents");
    assert_eq!(quiet_agents.len(), 8);
    let quiet_agy = quiet_agents.iter().find(|a| a["id"] == "agy").expect("agy");
    assert_eq!(quiet_agy["present"], false);
    assert_eq!(quiet_agy["status"], "absent");

    unsafe {
        std::env::remove_var("VIBECRAFTED_AGY_QUOTA_JSON");
        std::env::remove_var("VIBECRAFTED_KIMI_QUOTA_JSON");
        std::env::remove_var("VIBECRAFTED_CODEX_QUOTA_JSON");
        std::env::remove_var("VIBECRAFTED_CURSOR_QUOTA_JSON");
    }
}
