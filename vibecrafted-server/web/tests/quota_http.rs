#![cfg(feature = "ssr")]
//! `GET /api/usage/quota`: one shared observation board for all eight providers.
//!
//! Every reading comes from `<home>/telemetry/<id>/quota.json` (or the
//! engine-native file under `$HOME`); `HOME` is pointed at an empty temp tree
//! once for this whole test binary so a developer's real monitors never leak in.

use std::fs;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::http::{StatusCode, header};
use leptos::config::{Env, LeptosOptions};
use serde_json::{Value, json};
use vibecrafted_server_web::control::api::control_routes_for;

/// The engine-native fallback file lives under the shared `HOME`; tests that
/// create it, and tests that need it absent, take turns.
static NATIVE_FILE: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

const PROVIDERS: [&str; 8] = [
    "agy", "claude", "codex", "copilot", "cursor", "grok", "junie", "kimi",
];
const CONTRACT: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../vibecrafted-core/tests/dispatch/fixtures/quota_board_contract.json"
);

fn isolate_host() -> PathBuf {
    static HOST: OnceLock<PathBuf> = OnceLock::new();
    HOST.get_or_init(|| {
        let host = private_dir("host");
        // SAFETY: runs once, before any request of this binary reads the environment.
        // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- every test request passes through this OnceLock before reading HOME/overrides; no mutation after initialization.
        unsafe {
            std::env::set_var("HOME", &host);
            for id in PROVIDERS {
                std::env::remove_var(format!("VIBECRAFTED_{}_QUOTA_JSON", id.to_uppercase()));
            }
        }
        host
    })
    .clone()
}

fn private_dir(label: &str) -> PathBuf {
    static NEXT: AtomicU64 = AtomicU64::new(0);
    // nosemgrep: rust.lang.security.temp-dir.temp-dir -- create is atomic and rejects existing paths/symlinks; mode0700 is applied before Unix creation.
    let path = std::env::temp_dir().join(format!(
        "vc-quota-{label}-{}-{}-{}",
        std::process::id(),
        chrono::Utc::now().timestamp_nanos_opt().unwrap(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder
        .create(&path)
        .expect("exclusive private quota fixture");
    path
}

struct TestHome(PathBuf);

impl TestHome {
    fn new() -> Self {
        isolate_host();
        let path = private_dir("http");
        Self(path)
    }

    fn write(&self, id: &str, body: &str) {
        let dir = self.0.join("telemetry").join(id);
        fs::create_dir_all(&dir).expect("telemetry dir");
        fs::write(dir.join("quota.json"), body).expect("quota.json");
    }

    fn write_json(&self, id: &str, value: &Value) {
        self.write(id, &value.to_string());
    }
}

impl Drop for TestHome {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).ok();
    }
}

fn now_s() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock")
        .as_secs()
}

fn iso(seconds_ago: u64) -> String {
    chrono::DateTime::from_timestamp((now_s() - seconds_ago) as i64, 0)
        .expect("ts")
        .to_rfc3339()
}

fn native_window(observed: &str, reset: u64) -> Value {
    json!({
        "ts":now_s(),"generated_at":iso(0),
        "metrics":{"processed_total":999999},
        "quota":{"status":"observed","source":"codex.native.token_count.rate_limits",
          "provenance":"native_local_rate_limits","observed_at":observed,
          "scope":{"pool":"openai","limit_id":"codex","plan_type":"pro"},
          "windows":[{"id":"primary","used_ratio":0.39,"remaining_ratio":0.61,
            "window_minutes":10080,"resets_at":reset,"observed_at":observed,
            "source":"codex.native.token_count.rate_limits",
            "scope":{"pool":"openai","limit_id":"codex","plan_type":"pro"}}]}
    })
}

#[tokio::test]
async fn native_weekly_window_preserves_duration_reset_source_and_scope() {
    let home = TestHome::new();
    let observed = iso(2);
    let reset = now_s() + 3600;
    home.write_json("codex", &native_window(&observed, reset));
    let (_, _, board) = get(&home.0, "/api/usage/quota?agent=codex").await;
    let codex = &board["agents"][0];
    assert_eq!(codex["state"], "ok");
    assert_eq!(codex["bars"][0]["label"], "7d");
    assert_eq!(codex["bars"][0]["window_minutes"], 10080);
    assert_eq!(codex["bars"][0]["ratio"], 0.39);
    assert_eq!(codex["bars"][0]["remaining_ratio"], 0.61);
    assert_eq!(codex["bars"][0]["resets_at"], reset);
    assert_eq!(
        codex["bars"][0]["source"],
        "codex.native.token_count.rate_limits"
    );
    assert_eq!(codex["observed_at"], observed);
    assert_eq!(codex["scope"]["limit_id"], "codex");
    assert_eq!(codex["scope"]["plan_type"], "pro");
    assert!(codex["scope"].get("account").is_none_or(Value::is_null));
}

#[tokio::test]
async fn fresh_native_exhaustion_survives_an_unrelated_stale_window() {
    let home = TestHome::new();
    let mut value = native_window(&iso(0), now_s() + 3600);
    value["quota"]["windows"][0]["used_ratio"] = json!(1.0);
    value["quota"]["windows"][0]["remaining_ratio"] = json!(0.0);
    let mut stale = value["quota"]["windows"][0].clone();
    stale["id"] = json!("secondary");
    stale["observed_at"] = json!(iso(600));
    value["quota"]["windows"]
        .as_array_mut()
        .unwrap()
        .push(stale);
    home.write_json("codex", &value);
    let (_, _, board) = get(&home.0, "/api/usage/quota?agent=codex").await;
    let codex = &board["agents"][0];
    assert_eq!(codex["state"], "exhausted");
    assert_eq!(codex["stale"], false);
    assert_eq!(codex["bars"][1]["stale"], true);
    assert!(codex["age_s"].as_u64().unwrap() < 10);
}

#[tokio::test]
async fn native_model_scope_is_filtered_before_aggregation_and_unscoped_means_pool_wide() {
    let home = TestHome::new();
    let mut value = native_window(&iso(0), now_s() + 3600);
    value["last_session"] = json!({"model":"model-b"});
    value["quota"]["windows"][0]["scope"]["model"] = json!("model-a");
    value["quota"]["windows"][0]["used_ratio"] = json!(0.2);
    value["quota"]["windows"][0]["remaining_ratio"] = json!(0.8);
    let mut other = value["quota"]["windows"][0].clone();
    other["id"] = json!("model-b-limit");
    other["scope"]["model"] = json!("model-b");
    other["used_ratio"] = json!(1.0);
    other["remaining_ratio"] = json!(0.0);
    value["quota"]["windows"]
        .as_array_mut()
        .unwrap()
        .push(other);
    home.write_json("codex", &value);
    let (_, _, all) = get(&home.0, "/api/usage/quota?agent=codex").await;
    assert_eq!(all["agents"][0]["state"], "exhausted");
    assert_eq!(all["agents"][0]["scope"]["model"], "model-b");
    let (_, _, selected) = get(&home.0, "/api/usage/quota?agent=codex&model=model-a").await;
    assert_eq!(selected["agents"][0]["state"], "ok");
    assert_eq!(selected["agents"][0]["bars"].as_array().unwrap().len(), 1);
    assert_eq!(selected["agents"][0]["scope"]["model"], "model-a");
    value["quota"]["windows"]
        .as_array_mut()
        .unwrap()
        .truncate(1);
    value["quota"]["windows"][0]["scope"]
        .as_object_mut()
        .unwrap()
        .remove("model");
    home.write_json("codex", &value);
    let (_, _, global) = get(&home.0, "/api/usage/quota?agent=codex&model=terra").await;
    assert_eq!(global["agents"][0]["state"], "ok");
    assert_eq!(global["agents"][0]["scope"]["model"], Value::Null);
    assert_eq!(
        global["agents"][0]["model"],
        Value::Null,
        "last usage model cannot narrow an account-wide quota"
    );
}

#[tokio::test]
async fn native_account_filter_excludes_other_accounts_before_severity() {
    let home = TestHome::new();
    let mut value = native_window(&iso(0), now_s() + 3600);
    value["quota"]["windows"][0]["scope"]["account"] = json!("account-a");
    let mut other = value["quota"]["windows"][0].clone();
    other["scope"]["account"] = json!("account-b");
    other["used_ratio"] = json!(1.0);
    other["remaining_ratio"] = json!(0.0);
    value["quota"]["windows"]
        .as_array_mut()
        .unwrap()
        .push(other);
    home.write_json("codex", &value);
    let (_, _, selected) = get(&home.0, "/api/usage/quota?agent=codex&account=account-a").await;
    assert_eq!(selected["agents"][0]["state"], "ok");
    assert_eq!(selected["agents"][0]["scope"]["account"], "account-a");
    assert_eq!(selected["agents"][0]["bars"].as_array().unwrap().len(), 1);
}

#[tokio::test]
async fn collecting_old_or_expired_native_windows_does_not_freshen_quota() {
    let home = TestHome::new();
    home.write_json("codex", &native_window(&iso(600), now_s() + 3600));
    let (_, _, board) = get(&home.0, "/api/usage/quota?agent=codex").await;
    assert_eq!(board["agents"][0]["state"], "stale");
    assert!(board["agents"][0]["age_s"].as_u64().unwrap() >= 600);
    home.write_json("codex", &native_window(&iso(10), now_s() - 1));
    let (_, _, board) = get(&home.0, "/api/usage/quota?agent=codex").await;
    assert_eq!(
        board["agents"][0]["state"], "stale",
        "an ended window is not a fresh limit"
    );
    assert_eq!(board["agents"][0]["bars"][0]["stale"], true);
}

#[tokio::test]
async fn native_window_rejects_foreign_scope_and_never_invents_a_limit_from_tokens() {
    let home = TestHome::new();
    let mut value = native_window(&iso(0), now_s() + 3600);
    value["quota"]["windows"][0]["scope"]["pool"] = json!("anthropic");
    home.write_json("codex", &value);
    let (_, _, board) = get(&home.0, "/api/usage/quota?agent=codex").await;
    assert_eq!(board["agents"][0]["state"], "unknown");
    assert!(board["agents"][0]["bars"].as_array().unwrap().is_empty());
    home.write_json("codex", &json!({"ts":now_s(),"metrics":{"processed_total":999999},"quota":{"status":"observed","fleet_native":{"total_tokens":999999}}}));
    let (_, _, board) = get(&home.0, "/api/usage/quota?agent=codex").await;
    assert_eq!(board["agents"][0]["state"], "unknown");
    assert!(board["agents"][0]["bars"].as_array().unwrap().is_empty());
}

async fn get(home: &Path, uri: &str) -> (StatusCode, String, Value) {
    let opts = LeptosOptions::builder()
        .output_name("vibecrafted-server-web-test")
        .site_root("target/site-test")
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr("127.0.0.1:0".parse::<SocketAddr>().expect("addr"))
        .reload_port(0)
        .build();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("private listener");
    let addr = listener.local_addr().expect("private address");
    let app = control_routes_for(home).with_state(opts);
    let server = tokio::spawn(async move {
        axum::serve(listener, app).await.expect("private HTTP");
    });
    let response = reqwest::Client::builder()
        .no_proxy()
        .build()
        .expect("private client")
        .get(format!("http://{addr}{uri}"))
        .send()
        .await
        .expect("response");
    let status = response.status();
    let cache = response
        .headers()
        .get(header::CACHE_CONTROL)
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default()
        .to_string();
    let body = response.json().await.expect("JSON");
    server.abort();
    let _ = server.await;
    (status, cache, body)
}

fn card<'a>(board: &'a Value, id: &str) -> &'a Value {
    board["agents"]
        .as_array()
        .expect("agents")
        .iter()
        .find(|item| item["id"] == id)
        .unwrap_or_else(|| panic!("card {id}"))
}

fn seed_every_state(home: &TestHome) {
    // fresh + confirmed exhausted, with account scope
    home.write_json(
        "codex",
        &json!({"agent": "codex", "generated_at": iso(5), "status": "ok",
                "account": "acct-1", "quota": {"status": "RESOURCE_EXHAUSTED"}}),
    );
    // 97% is low, never exhausted
    home.write_json(
        "claude",
        &json!({"agent": "claude", "generated_at": iso(5), "limit": {"usedRatio": 0.97}}),
    );
    // exhausted once, 10 minutes ago: stale, not a current confirmation
    home.write_json(
        "grok",
        &json!({"agent": "grok", "generated_at": iso(600), "quota": {"status": "RESOURCE_EXHAUSTED"}}),
    );
    // token totals only: unmeasured quota stays unknown, never inferred
    home.write_json(
        "junie",
        &json!({"agent": "junie", "generated_at": iso(5),
                "metrics": {"processed_total": 99_000_000_000u64}}),
    );
    home.write("copilot", "{ truncated");
    home.write_json(
        "cursor",
        &json!({"agent": "cursor", "generated_at": iso(5), "status": "unavailable",
                "reason": "usage lives in Cursor cloud"}),
    );
    // a kimi window with an unusable ratio measures nothing
    home.write_json(
        "kimi",
        &json!({"ts": now_s(), "kind": "ok", "plan": "Coding", "limit5h": {"usedRatio": "x"}}),
    );
    // agy: fresh snapshot, but the 429 it rests on is two hours old
    home.write_json(
        "agy",
        &json!({"ts": now_s(), "model_id": "gemini-3.1-pro-high",
                "quota": {"status": "RESOURCE_EXHAUSTED",
                          "last_429_ts": (now_s() - 7200) * 1000, "last_success_ts": 0}}),
    );
}

#[tokio::test]
async fn board_is_eight_equal_cards_with_agent_key_state_and_scope() {
    let _turn = NATIVE_FILE.lock().await;
    let home = TestHome::new();
    let (status, cache, board) = get(&home.0, "/api/usage/quota").await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(cache, "no-store");
    assert_eq!(board["schema"], "vibecrafted.quota-dashboard.v1");
    let ids: Vec<_> = board["agents"]
        .as_array()
        .unwrap()
        .iter()
        .map(|a| a["id"].as_str().unwrap())
        .collect();
    assert_eq!(ids, PROVIDERS, "all eight, alphabetical, none first");
    for item in board["agents"].as_array().unwrap() {
        assert_eq!(item["agent"], item["id"], "doctor reads `agent`");
        assert_eq!(item["state"], "absent");
        assert_eq!(item["status"], "absent");
        assert!(item["scope"]["pool"].is_string(), "pool scope on {item}");
        assert!(item["source"].as_str().unwrap().contains("telemetry"));
    }
}

#[tokio::test]
async fn every_state_is_distinct_and_exhausted_is_only_ever_fresh() {
    let home = TestHome::new();
    seed_every_state(&home);
    let (_, _, board) = get(&home.0, "/api/usage/quota").await;

    let codex = card(&board, "codex");
    assert_eq!(
        (codex["state"].as_str(), codex["status"].as_str()),
        (Some("exhausted"), Some("blocked"))
    );
    assert_eq!(codex["scope"]["account"], "acct-1");
    assert_eq!(codex["scope"]["pool"], "openai");
    assert!(codex["observed_at"].is_string());

    let claude = card(&board, "claude");
    assert_eq!(
        (claude["state"].as_str(), claude["status"].as_str()),
        (Some("low"), Some("warn"))
    );

    let grok = card(&board, "grok");
    assert_eq!(
        grok["state"], "stale",
        "an old exhaustion is not current confirmation"
    );
    assert_eq!(grok["status"], "warn");
    assert_eq!(grok["stale"], true);

    let junie = card(&board, "junie");
    assert_eq!(
        junie["state"], "unknown",
        "token totals never imply a quota"
    );
    assert_eq!(junie["headline"], "unmeasured");

    assert_eq!(card(&board, "copilot")["state"], "malformed");
    assert_eq!(card(&board, "cursor")["state"], "unknown");
    assert_eq!(
        card(&board, "kimi")["state"],
        "unknown",
        "unusable ratios measure nothing"
    );

    let agy = card(&board, "agy");
    assert_eq!(agy["state"], "stale", "fresh snapshot, two-hour-old 429");
    assert_eq!(agy["scope"]["model"], "gemini-3.1-pro-high");
    assert!(agy["age_s"].as_u64().unwrap() >= 7200);
}

#[tokio::test]
async fn fresh_agy_429_is_exhausted_and_scoped_to_its_model() {
    let home = TestHome::new();
    home.write_json(
        "agy",
        &json!({"ts": now_s(), "model_id": "gemini-3.1-pro-high",
                "quota": {"status": "RESOURCE_EXHAUSTED", "last_429_ts": now_s() * 1000,
                          "last_success_ts": 0}}),
    );
    let (_, _, board) = get(&home.0, "/api/usage/quota?agent=agy").await;

    let agy = &board["agents"][0];
    assert_eq!(agy["state"], "exhausted");
    assert_eq!(agy["scope"]["model"], "gemini-3.1-pro-high");
}

#[tokio::test]
async fn an_untimed_reading_cannot_claim_freshness() {
    let home = TestHome::new();
    home.write_json(
        "codex",
        &json!({"agent": "codex", "quota": {"status": "RESOURCE_EXHAUSTED"}}),
    );
    let (_, _, board) = get(&home.0, "/api/usage/quota?agent=codex").await;

    assert_eq!(board["agents"][0]["state"], "unknown");
    assert!(
        board["agents"][0]["detail"]
            .as_str()
            .unwrap()
            .contains("no observation time")
    );
}

#[tokio::test]
async fn shared_projection_wins_and_engine_native_file_is_the_fallback() {
    let _turn = NATIVE_FILE.lock().await;
    let home = TestHome::new();
    let host = isolate_host();
    let native = host.join(".kimi-code/runtime");
    fs::create_dir_all(&native).unwrap();
    fs::write(
        native.join("quota.json"),
        json!({"ts": now_s(), "kind": "ok", "plan": "Coding", "limit5h": {"usedRatio": 0.41}})
            .to_string(),
    )
    .unwrap();

    let (_, _, fallback) = get(&home.0, "/api/usage/quota?agent=kimi").await;
    assert_eq!(fallback["agents"][0]["state"], "ok");
    assert!(
        fallback["agents"][0]["source"]
            .as_str()
            .unwrap()
            .contains(".kimi-code")
    );

    home.write_json(
        "kimi",
        &json!({"ts": now_s(), "kind": "ok", "limit5h": {"usedRatio": 1.0}}),
    );
    let (_, _, shared) = get(&home.0, "/api/usage/quota?agent=kimi").await;
    assert_eq!(shared["agents"][0]["state"], "exhausted");
    assert!(
        shared["agents"][0]["source"]
            .as_str()
            .unwrap()
            .contains(".vibecrafted/telemetry/kimi")
    );
    fs::remove_dir_all(native).ok();
}

#[tokio::test]
async fn query_filters_roundtrip_on_agent_provider_pool_and_model() {
    let home = TestHome::new();
    seed_every_state(&home);

    let ids = |board: &Value| -> Vec<String> {
        board["agents"]
            .as_array()
            .unwrap()
            .iter()
            .map(|a| a["id"].as_str().unwrap().to_string())
            .collect()
    };
    let (_, _, by_agent) = get(&home.0, "/api/usage/quota?agent=grok").await;
    assert_eq!(ids(&by_agent), ["grok"]);
    let (_, _, by_pool) = get(&home.0, "/api/usage/quota?pool=anthropic").await;
    assert_eq!(ids(&by_pool), ["claude"]);
    let (_, _, by_provider) = get(&home.0, "/api/usage/quota?provider=Antigravity").await;
    assert_eq!(ids(&by_provider), ["agy"]);
    let (_, _, by_model) = get(&home.0, "/api/usage/quota?model=gemini-3.1").await;
    assert_eq!(ids(&by_model), ["agy"]);
    let (_, _, all) = get(
        &home.0,
        "/api/usage/quota?agent=all&provider=all&pool=all&model=all",
    )
    .await;
    assert_eq!(ids(&all).len(), 8);
}

#[tokio::test]
async fn refresh_is_explicit_and_unsupported_agents_are_ignored() {
    let _turn = NATIVE_FILE.lock().await;
    let home = TestHome::new();
    seed_every_state(&home);

    // `cursor` has no authoritative quota source: the collector reports it
    // unavailable and the stored observation is left exactly as it was.
    let (status, _, board) = get(
        &home.0,
        "/api/usage/quota?refresh=preflight&agents=cursor,not-a-provider",
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(card(&board, "cursor")["state"], "unknown");
    assert_eq!(card(&board, "codex")["state"], "exhausted");
}

/// The wire contract shared with the Python dispatch doctor: the dispatch tests
/// serve this same file and must read every state from it.
#[tokio::test]
async fn board_matches_the_committed_wire_contract() {
    let home = TestHome::new();
    seed_every_state(&home);
    let (_, _, mut board) = get(&home.0, "/api/usage/quota").await;

    board["generated_at"] = json!("<generated_at>");
    for item in board["agents"].as_array_mut().unwrap() {
        if item["age_s"].is_number() {
            item["age_s"] = json!(if item["stale"] == true {
                "<old>"
            } else {
                "<fresh>"
            });
        }
        if item["observed_at"].is_string() {
            item["observed_at"] = json!("<observed_at>");
        }
    }
    let rendered = serde_json::to_string_pretty(&board).unwrap() + "\n";
    if std::env::var_os("VC_UPDATE_QUOTA_CONTRACT").is_some() {
        fs::write(CONTRACT, &rendered).expect("write contract");
    }
    let committed = fs::read_to_string(CONTRACT).expect("committed quota contract");
    assert_eq!(
        rendered, committed,
        "wire shape drifted; rerun with VC_UPDATE_QUOTA_CONTRACT=1 and review the diff"
    );
}

#[tokio::test]
async fn public_http_missing_sources_preserve_all_eight_peers() {
    let _guard = NATIVE_FILE.lock().await;
    let home = TestHome::new();
    let opts = LeptosOptions::builder()
        .output_name("quota-private-http-test")
        .site_root("target/site-test")
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr("127.0.0.1:0".parse::<SocketAddr>().expect("addr"))
        .reload_port(0)
        .build();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("private listener");
    let addr = listener.local_addr().expect("private addr");
    let app = control_routes_for(&home.0).with_state(opts);
    let server =
        tokio::spawn(async move { axum::serve(listener, app).await.expect("private HTTP") });
    let response = reqwest::Client::builder()
        .no_proxy()
        .build()
        .expect("client")
        .get(format!("http://{addr}/api/usage/quota"))
        .send()
        .await
        .expect("private response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers()[header::CACHE_CONTROL], "no-store");
    let board: Value = response.json().await.expect("quota JSON");
    server.abort();
    let _ = server.await;
    let ids: Vec<&str> = board["agents"]
        .as_array()
        .expect("agents")
        .iter()
        .map(|row| row["id"].as_str().expect("id"))
        .collect();
    println!("private endpoint provider ids: {ids:?}");
    assert_eq!(ids, PROVIDERS);
    for row in board["agents"].as_array().expect("agents") {
        assert_eq!(row["state"], "absent");
        assert_eq!(row["age_s"], Value::Null);
        assert_eq!(row["tokens"], Value::Null);
        assert_eq!(row["cost_usd"], Value::Null);
    }
}

#[tokio::test]
async fn pool_vocabulary_is_the_canonical_company_names_the_picker_seeds() {
    // The usage picker seeds google/anthropic/openai/github/anysphere/xai/
    // jetbrains/moonshot. The quota board's pool scope must speak the same
    // names, or cross-surface filters and pool accounting disagree.
    let home = TestHome::new();
    let (_, _, board) = get(&home.0, "/api/usage/quota").await;
    let pools: Vec<_> = board["agents"]
        .as_array()
        .expect("agents")
        .iter()
        .map(|item| {
            (
                item["id"].as_str().expect("id").to_string(),
                item["scope"]["pool"].as_str().expect("pool").to_string(),
            )
        })
        .collect();
    let expected: Vec<(String, String)> = [
        ("agy", "google"),
        ("claude", "anthropic"),
        ("codex", "openai"),
        ("copilot", "github"),
        ("cursor", "anysphere"),
        ("grok", "xai"),
        ("junie", "jetbrains"),
        ("kimi", "moonshot"),
    ]
    .into_iter()
    .map(|(id, pool)| (id.to_string(), pool.to_string()))
    .collect();
    assert_eq!(pools, expected, "one pool vocabulary across surfaces");

    let ids = |board: &Value| -> Vec<String> {
        board["agents"]
            .as_array()
            .unwrap()
            .iter()
            .map(|a| a["id"].as_str().unwrap().to_string())
            .collect()
    };
    let (_, _, by_pool) = get(&home.0, "/api/usage/quota?pool=anysphere").await;
    assert_eq!(ids(&by_pool), ["cursor"]);
    let (_, _, by_provider) = get(&home.0, "/api/usage/quota?provider=anysphere").await;
    assert_eq!(ids(&by_provider), ["cursor"]);
}

#[tokio::test]
async fn explicit_refresh_reports_real_parser_and_skipped_outcomes_without_new_observation() {
    let _turn = NATIVE_FILE.lock().await;
    let home = TestHome::new();
    home.write_json("cursor", &json!({"agent":"cursor", "generated_at":iso(148 * 3600), "status":"unavailable", "model":"private-old-model"}));
    let path = home.0.join("telemetry/cursor/quota.json");
    let before = fs::read(&path).expect("private observation");
    let (_, _, plain) = get(&home.0, "/api/usage/quota?agent=cursor").await;
    assert!(plain.get("refresh").is_none(), "ordinary GET is file-only");
    let (status, cache, refreshed) = get(
        &home.0,
        "/api/usage/quota?agent=cursor&refresh=preflight&agents=cursor,not-a-provider",
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(cache, "no-store");
    println!("private parser refresh outcome: {}", refreshed["refresh"]);
    assert_eq!(refreshed["refresh"]["mode"], "quota");
    assert_eq!(refreshed["refresh"]["started"], true);
    assert_eq!(
        refreshed["refresh"]["agents"]
            .as_array()
            .expect("outcomes")
            .len(),
        1
    );
    assert_eq!(refreshed["refresh"]["agents"][0]["agent"], "cursor");
    assert_eq!(refreshed["refresh"]["agents"][0]["status"], "unavailable");
    assert_eq!(refreshed["agents"][0]["state"], "stale");
    assert!(refreshed["agents"][0]["age_s"].as_u64().expect("old age") >= 148 * 3600);
    assert_eq!(fs::read(&path).expect("preserved observation"), before);
    let (_, _, skipped) = get(
        &home.0,
        "/api/usage/quota?refresh=preflight&agents=not-a-provider",
    )
    .await;
    assert_eq!(skipped["refresh"]["started"], false);
    assert_eq!(
        skipped["refresh"]["reason"],
        "no supported provider requested"
    );
    assert!(
        skipped["refresh"]["agents"]
            .as_array()
            .expect("skipped outcomes")
            .is_empty()
    );
    assert_eq!(fs::read(&path).expect("preserved after skip"), before);
}

#[tokio::test]
async fn a_files_self_declared_agent_cannot_redefine_its_pool_scope() {
    let home = TestHome::new();
    // A fleet snapshot sitting in claude's slot that claims to be codex: the
    // card is still the claude observation. Pool scope comes from the provider
    // slot being read, never from untrusted file content — admission blocks on
    // pools, so a misplaced file must not contaminate another provider's pool.
    home.write_json(
        "claude",
        &json!({"agent": "codex", "generated_at": iso(5),
                "quota": {"status": "RESOURCE_EXHAUSTED"}}),
    );
    let (_, _, board) = get(&home.0, "/api/usage/quota?agent=claude").await;

    let claude = &board["agents"][0];
    assert_eq!(claude["state"], "exhausted");
    assert_eq!(claude["scope"]["pool"], "anthropic");
    assert_eq!(claude["pool"], "anthropic");
}

#[tokio::test]
async fn an_explicit_refresh_reports_its_outcome_on_the_board() {
    let _turn = NATIVE_FILE.lock().await;
    let home = TestHome::new();

    // cursor has no authoritative quota source: the collector answers
    // `unavailable` and the board says so — a preflight can tell "no source"
    // apart from "the refresh never ran".
    let (status, _, board) = get(
        &home.0,
        "/api/usage/quota?refresh=preflight&agents=cursor,not-a-provider",
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let refresh = &board["refresh"];
    assert_eq!(refresh["mode"], "quota");
    assert_eq!(refresh["started"], true);
    let agents = refresh["agents"].as_array().expect("refresh agents");
    let cursor = agents
        .iter()
        .find(|item| item["agent"] == "cursor")
        .expect("cursor row");
    assert_eq!(cursor["status"], "unavailable");
    assert!(
        agents.iter().all(|item| item["agent"] != "not-a-provider"),
        "unsupported providers are not probed"
    );

    let (_, _, skipped) = get(
        &home.0,
        "/api/usage/quota?refresh=preflight&agents=not-a-provider",
    )
    .await;
    assert_eq!(skipped["refresh"]["mode"], "quota");
    assert_eq!(skipped["refresh"]["started"], false);
    assert!(
        skipped["refresh"]["reason"]
            .as_str()
            .expect("reason")
            .contains("no supported provider")
    );

    // A plain read carries no refresh block at all.
    let (_, _, plain) = get(&home.0, "/api/usage/quota").await;
    assert!(plain.get("refresh").is_none());
}

#[tokio::test]
async fn agy_status_event_age_survives_a_new_snapshot_timestamp() {
    let home = TestHome::new();
    let old_success = now_s() - 148 * 3600;
    home.write_json("agy", &json!({"ts":now_s(), "model_id":"private-model", "quota":{"status":"OK", "last_success_ts":old_success * 1000, "last_429_ts":0}}));
    let (_, _, old) = get(&home.0, "/api/usage/quota?agent=agy").await;
    println!(
        "old success under fresh snapshot: state={} age={}",
        old["agents"][0]["state"], old["agents"][0]["age_s"]
    );
    assert_eq!(old["agents"][0]["state"], "stale");
    assert_eq!(old["agents"][0]["status"], "warn");
    assert!(old["agents"][0]["age_s"].as_u64().expect("event age") >= 148 * 3600);
    assert_eq!(
        old["agents"][0]["observed_at"],
        chrono::DateTime::from_timestamp(old_success as i64, 0)
            .expect("old event")
            .to_rfc3339()
    );
    // Matching positive: the same parser and slot admit a fresh success.
    home.write_json("agy", &json!({"ts":now_s(), "model_id":"private-model", "quota":{"status":"OK", "last_success_ts":now_s() * 1000, "last_429_ts":0}}));
    let (_, _, fresh) = get(&home.0, "/api/usage/quota?agent=agy").await;
    assert_eq!(fresh["agents"][0]["state"], "ok");
    assert!(fresh["agents"][0]["age_s"].as_u64().expect("fresh event") <= 10);
}

#[tokio::test]
async fn agy_status_missing_event_time_cannot_use_snapshot_timestamp() {
    let home = TestHome::new();
    for status in ["OK", "RESOURCE_EXHAUSTED"] {
        for event in [Value::Null, json!(0), json!(-1), json!("unusable")] {
            home.write_json("agy", &json!({"ts":now_s(), "model_id":"private-model", "quota":{"status":status, "last_success_ts":event, "last_429_ts":event}}));
            let (_, _, board) = get(&home.0, "/api/usage/quota?agent=agy").await;
            assert_eq!(
                board["agents"][0]["state"], "unknown",
                "{status}, event {event}"
            );
            assert_eq!(board["agents"][0]["age_s"], Value::Null);
            assert!(board["agents"][0].get("observed_at").is_none());
        }
    }
}

#[tokio::test]
async fn normalized_refresh_targets_are_collected_once() {
    let _turn = NATIVE_FILE.lock().await;
    let home = TestHome::new();
    for uri in [
        "/api/usage/quota?agent=CURSOR&refresh=preflight&agents=cursor,not-a-provider,CuRsOr",
        "/api/usage/quota?refresh=preflight&agents=%20CURSOR%20,cursor,all,",
    ] {
        let (status, _, board) = get(&home.0, uri).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(board["refresh"]["started"], true);
        let outcomes = board["refresh"]["agents"]
            .as_array()
            .expect("real parser outcomes");
        assert_eq!(outcomes.len(), 1, "{uri}");
        assert_eq!(outcomes[0]["agent"], "cursor");
        assert_eq!(outcomes[0]["status"], "unavailable");
    }
}
