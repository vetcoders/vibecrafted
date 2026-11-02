#![cfg(feature = "ssr")]
//! `/api/usage` over a real `ccusage` collection of the scrubbed session
//! fixtures. One test on purpose: it points `HOME` and the provider stores at
//! the fixtures for the whole process.

#[path = "../../control-core/tests/support/ccusage_env.rs"]
mod ccusage_env;

use std::fs;
use std::net::SocketAddr;
use std::path::Path;
use std::time::{Duration, Instant};

use axum::http::StatusCode;
use control_core::ControlPlane;
use leptos::config::{Env, LeptosOptions};
use serde_json::Value;
use vibecrafted_server_web::control::api::control_routes_for;

use ccusage_env::{point_provider_stores_at, private_dir};

fn test_app(home: &Path) -> axum::Router {
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

async fn get(home: &Path, uri: &str) -> (StatusCode, Value) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("private listener");
    let addr = listener.local_addr().expect("private address");
    let app = test_app(home);
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
    let body = response.json().await.expect("JSON response");
    server.abort();
    let _ = server.await;
    (status, body)
}

fn rows<'a>(report: &'a Value, key: &str) -> &'a Vec<Value> {
    report[key].as_array().expect("array")
}

#[tokio::test]
async fn usage_api_serves_measured_sessions_and_keeps_an_unknown_cost_unknown() {
    let root = private_dir("http");
    let host = root.join("host");
    let home = root.join("vc");
    fs::create_dir_all(&host).expect("host");
    fs::create_dir_all(&home).expect("home");
    let fixtures =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../control-core/tests/fixtures/ccusage");
    // SAFETY: this binary contains exactly one test, and the runtime has spawned
    // nothing that reads the environment yet.
    // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- this binary owns its only test; the environment is established before any collector, projection or server starts.
    unsafe { point_provider_stores_at(&fixtures, &host) };

    let outcome = ControlPlane::new(&home).collect_local_usage(Duration::ZERO);
    assert!(outcome.started, "{outcome:?}");

    let deadline = Instant::now() + Duration::from_secs(20);
    let report = loop {
        let (status, report) = get(&home, "/api/usage?window=all").await;
        assert_eq!(status, StatusCode::OK);
        if !rows(&report, "local_sources")
            .iter()
            .any(|item| item["refreshing"] == true)
        {
            break report;
        }
        assert!(Instant::now() < deadline, "projection did not settle");
        tokio::time::sleep(Duration::from_millis(20)).await;
    };

    assert_eq!(report["totals"]["runs"], 6);
    assert_eq!(report["totals"]["tokens_total_known"], 18_452_944);
    assert_eq!(report["totals"]["runs_cost_unknown"], 1);
    let usd = report["totals"]["cost_by_unit"]["USD"]
        .as_f64()
        .expect("USD total");
    assert!((usd - 13.918_955_8).abs() < 1e-9, "{usd}");

    let sources = rows(&report, "local_sources");
    let source = |agent: &str| {
        sources
            .iter()
            .find(|item| item["agent"] == agent)
            .unwrap_or_else(|| panic!("source {agent}"))
    };
    assert_eq!(source("claude")["source"], "ccusage");
    assert_eq!(source("claude")["counted"], 2);
    assert_eq!(source("grok")["source"], "ccusage");
    assert_eq!(source("agy")["source"], "unavailable");

    let runs = rows(&report, "runs");
    let grok = runs
        .iter()
        .find(|run| run["telemetry_source"] == "ccusage:grok")
        .expect("grok row");
    assert_eq!(grok["origin"], "local");
    assert_eq!(grok["model"], "grok-4.7-build-fast");
    assert_eq!(grok["cost"]["source"], "provider_reported");
    let unpriced = runs
        .iter()
        .find(|run| run["model"] == "vendor-unreleased-99")
        .expect("unpriced row");
    assert_eq!(unpriced["cost"]["amount"]["value"], "unknown");
    assert_eq!(unpriced["unpricedModels"][0], "vendor-unreleased-99");
    assert_eq!(unpriced["tokens"]["tokens_total"], 159_299);
    fs::remove_dir_all(root).ok();
}
