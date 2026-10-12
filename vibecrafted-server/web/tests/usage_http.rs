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

fn write_quota(home: &std::path::Path, agent: &str, observation: Value) {
    let dir = home.join("telemetry").join(agent);
    fs::create_dir_all(&dir).unwrap();
    fs::write(dir.join("quota.json"), observation.to_string()).unwrap();
}

#[tokio::test]
async fn all_eight_providers_roundtrip_scopes_freshness_and_malformed_readings() {
    let home = TestHome::new();
    let now = chrono::Utc::now();
    for agent in [
        "agy", "claude", "codex", "copilot", "cursor", "grok", "junie", "kimi",
    ] {
        write_quota(
            &home.0,
            agent,
            json!({"authoritative":true,"status":"ok","fraction":0.95,"source":"native-quota","observed_at":now.to_rfc3339(),"account":"default"}),
        );
    }
    let (status, cache, board) = get(&home.0, "/api/usage/quota").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(cache, "no-store");
    assert_eq!(board["agents"].as_array().unwrap().len(), 8);
    for agent in board["agents"].as_array().unwrap() {
        assert_eq!(agent["status"], "warn");
        assert_eq!(agent["fraction"], 0.95);
        assert!(agent["observed_at"].is_string());
        assert!(agent["provider"].is_string());
    }
    write_quota(
        &home.0,
        "codex",
        json!({"observations":[
            {"authoritative":true,"status":"exhausted","source":"native-quota","observed_at":now.to_rfc3339(),"pool":"shared-openai","account":"default","model":"gpt-requested"},
            {"authoritative":true,"status":"exhausted","source":"native-quota","observed_at":(now-chrono::Duration::seconds(600)).to_rfc3339(),"pool":"shared-openai","account":"default","model":"old-model"}
        ]}),
    );
    let (_, _, matched) = get(
        &home.0,
        "/api/usage/quota?agent=codex&model=gpt-requested&account=default&pool=shared-openai",
    )
    .await;
    assert_eq!(matched["agents"][0]["status"], "blocked");
    assert_eq!(matched["agents"][0]["applies"], true);
    assert_eq!(matched["agents"][1]["status"], "unknown");
    let (_, _, mismatch) = get(
        &home.0,
        "/api/usage/quota?agent=codex&model=other&account=wrong",
    )
    .await;
    assert!(
        mismatch["agents"]
            .as_array()
            .unwrap()
            .iter()
            .all(|a| a["status"] == "unknown")
    );
    write_quota(
        &home.0,
        "codex",
        json!({"authoritative":true,"status":"exhausted","source":"native-quota","observed_at":now.to_rfc3339(),"fraction":"invalid"}),
    );
    let (_, _, malformed) = get(&home.0, "/api/usage/quota?agent=codex").await;
    assert_eq!(malformed["agents"][0]["status"], "unknown");
    assert!(
        malformed["agents"][0]["detail"]
            .as_str()
            .unwrap()
            .contains("malformed")
    );
    // Actual Provider picker wire value, then Agent and Model, roundtrip usage.
    let (_, _, report) = get(
        &home.0,
        "/api/usage?window=all&provider=openai&agent=codex&model=gpt-5.6-terra",
    )
    .await;
    assert_eq!(report["totals"]["runs"], 1);
    assert_eq!(report["filter"]["provider"], "openai");
    assert_eq!(report["pickers"]["models"][0]["name"], "gpt-5.6-terra");
}

#[tokio::test]
async fn native_formats_are_local_and_absent_malformed_and_stale_are_distinct() {
    let home = TestHome::new();
    let ts = chrono::Utc::now().timestamp();
    write_quota(
        &home.0,
        "agy",
        json!({"ts":ts,"model_id":"selected-model","quota":{"status":"RESOURCE_EXHAUSTED"}}),
    );
    write_quota(
        &home.0,
        "kimi",
        json!({"ts":ts,"kind":"ok","limit5h":{"usedRatio":1.0}}),
    );
    let (_, _, agy) = get(&home.0, "/api/usage/quota?agent=agy&model=selected-model").await;
    assert_eq!(agy["agents"][0]["status"], "unknown");
    assert_eq!(agy["agents"][0]["authoritative"], false);
    let (_, _, kimi) = get(&home.0, "/api/usage/quota?agent=kimi").await;
    assert_eq!(kimi["agents"][0]["status"], "blocked");
    write_quota(
        &home.0,
        "kimi",
        json!({"ts":ts-600,"kind":"ok","limit5h":{"usedRatio":1.0}}),
    );
    let (_, _, stale) = get(&home.0, "/api/usage/quota?agent=kimi").await;
    assert_eq!(stale["agents"][0]["status"], "warn");
    assert_eq!(stale["agents"][0]["stale"], true);
    write_quota(
        &home.0,
        "kimi",
        json!({"ts":ts,"kind":"ok","limit5h":{"usedRatio":"invalid"}}),
    );
    let (_, _, bad) = get(&home.0, "/api/usage/quota?agent=kimi").await;
    assert_eq!(bad["agents"][0]["status"], "unknown");
    let (_, _, absent) = get(&home.0, "/api/usage/quota?agent=claude").await;
    assert_eq!(absent["agents"][0]["status"], "absent");
    assert_eq!(absent["agents"][0]["authoritative"], false);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn real_server_response_reaches_public_doctor_and_dispatch_zero_start() {
    let home = TestHome::new();
    write_quota(
        &home.0,
        "codex",
        json!({"authoritative":true,"status":"exhausted","source":"native-quota","observed_at":chrono::Utc::now().to_rfc3339(),"pool":"shared-openai","account":"default"}),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let router = test_app(&home.0);
    let server = tokio::spawn(async move {
        axum::serve(listener, router).await.unwrap();
    });
    let plan = home.0.join("plan.toml");
    fs::write(&plan,"schema = \"vibecrafted.dispatch.v1\"\n[meta]\nrepo = \"/tmp/repo\"\n[[cuts]]\nid = \"one\"\nagent = \"codex\"\nworkflow = \"implement\"\nprompt = \"work\"\n[[cuts.verify]]\nrun = \"true\"\nexpect = { exit_code = 0 }\n").unwrap();
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../vibecrafted-core");
    let python = std::env::var("VIBECRAFTED_PYTHON").unwrap_or_else(|_| "python3.14".into());
    let output = tokio::process::Command::new(python)
        .arg("-c")
        .arg(
            r#"
import sys
from vibecrafted_core.dispatch import cli
from vibecrafted_core.dispatch.doctor import main
assert main([sys.argv[1], '--json', '--check-quotas']) == 1
cli.run_dispatch = lambda *a, **k: (_ for _ in ()).throw(AssertionError('partial batch start'))
assert cli.main([sys.argv[1], '--json']) == 1
print('PUBLIC_QUOTA_ZERO_START_PASS')
"#,
        )
        .arg(plan)
        .env("PYTHONPATH", root)
        .env("VIBECRAFTED_HOME", &home.0)
        .env("VIBECRAFTED_SERVER_URL", format!("http://{addr}"))
        .output()
        .await
        .unwrap();
    server.abort();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(String::from_utf8_lossy(&output.stdout).contains("PUBLIC_QUOTA_ZERO_START_PASS"));
}

#[tokio::test]
async fn one_explicit_shared_pool_observation_covers_required_agents() {
    let home = TestHome::new();
    write_quota(
        &home.0,
        "codex",
        json!({"authoritative":true,"status":"exhausted","source":"shared provider response","observed_at":chrono::Utc::now().to_rfc3339(),"pool":"shared-pool","account":"default","agents":["codex","cursor"]}),
    );
    let (_, _, board) = get(&home.0, "/api/usage/quota?agent=cursor&pool=shared-pool").await;
    assert!(
        board["agents"]
            .as_array()
            .unwrap()
            .iter()
            .any(|a| a["id"] == "cursor"
                && a["pool"] == "shared-pool"
                && a["status"] == "blocked"
                && a["applies"] == true)
    );
}

#[tokio::test]
async fn missing_or_other_account_scope_cannot_block_default_batch() {
    let home = TestHome::new();
    write_quota(
        &home.0,
        "codex",
        json!({"authoritative":true,"status":"exhausted","source":"provider response","observed_at":chrono::Utc::now().to_rfc3339(),"pool":"paid-pool"}),
    );
    let (_, _, unscoped) = get(&home.0, "/api/usage/quota?agent=codex").await;
    assert_eq!(unscoped["agents"][0]["status"], "unknown");
    assert_eq!(unscoped["agents"][0]["account"], Value::Null);
    write_quota(
        &home.0,
        "codex",
        json!({"authoritative":true,"status":"exhausted","source":"provider response","observed_at":chrono::Utc::now().to_rfc3339(),"pool":"paid-pool","account":"other"}),
    );
    let (_, _, other) = get(&home.0, "/api/usage/quota?agent=codex").await;
    assert_eq!(other["agents"][0]["status"], "unknown");
    let (_, _, explicit) = get(&home.0, "/api/usage/quota?agent=codex&account=other").await;
    assert_eq!(explicit["agents"][0]["status"], "blocked");
}

#[tokio::test]
async fn republished_old_agy_error_is_never_fresh_confirmed_exhaustion() {
    let home = TestHome::new();
    let ts = chrono::Utc::now().timestamp();
    write_quota(
        &home.0,
        "agy",
        json!({"ts":ts,"model_id":"selected-model","quota":{"status":"RESOURCE_EXHAUSTED","last_429_ts":(ts-3600)*1000}}),
    );
    let (_, _, board) = get(&home.0, "/api/usage/quota?agent=agy&model=selected-model").await;
    assert_eq!(board["agents"][0]["status"], "unknown");
    assert_eq!(board["agents"][0]["authoritative"], false);
    assert!(
        board["agents"][0]["detail"]
            .as_str()
            .unwrap()
            .contains("error-derived")
    );
}
