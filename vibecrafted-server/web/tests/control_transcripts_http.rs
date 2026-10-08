//! HTTP contract for `GET /api/control/transcripts?q=`.
//!
//! Search must find a needle that lives only in the *head* of a long human
//! log (past the live-page 48 KiB / 160-line tail window), a needle that lives
//! only past the old 256 KiB head cap, and a second page of more than 200
//! matching results.

#![cfg(feature = "ssr")]

use std::fs;
use std::io::ErrorKind;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode};
use leptos::config::{Env, LeptosOptions};
use serde_json::{Value, json};
use tower::ServiceExt;
use vibecrafted_server_web::control::api::control_routes_for;

struct TestHome(PathBuf);

impl TestHome {
    fn new() -> Self {
        static NEXT_ID: AtomicU64 = AtomicU64::new(0);
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let path = (0..100)
            .find_map(|attempt| {
                let nonce = NEXT_ID.fetch_add(1, Ordering::Relaxed);
                let candidate = std::env::temp_dir().join(format!(
                    "vc-transcripts-http-{}-{nanos}-{nonce}-{attempt}",
                    std::process::id()
                ));
                match fs::create_dir(&candidate) {
                    Ok(()) => Some(candidate),
                    Err(error) if error.kind() == ErrorKind::AlreadyExists => None,
                    Err(error) => panic!("create isolated transcript home: {error}"),
                }
            })
            .expect("allocate an isolated transcript home");
        fs::create_dir_all(path.join("control_plane/runs")).expect("runs dir");
        fs::create_dir_all(path.join("control_plane/runtime_runs")).expect("runtime runs");
        Self(path)
    }

    fn write_run(&self, run_id: &str, updated_at: &str, transcript: &str) {
        let payload = json!({
            "run_id": run_id,
            "state": "completed",
            "agent": "codex",
            "skill": "implement",
            "mode": "implement",
            "root": "/tmp/repo",
            "operator_session": format!("repo-{run_id}"),
            "latest_report": "",
            "latest_transcript": "",
            "last_error": "",
            "updated_at": updated_at,
            "started_at": updated_at,
            "health": "final",
            "source": "agent-meta",
            "lock_present": false,
            "liveness": "terminal",
        });
        fs::write(
            self.0
                .join("control_plane/runs")
                .join(format!("{run_id}.json")),
            serde_json::to_vec_pretty(&payload).expect("snapshot JSON"),
        )
        .expect("write snapshot");
        let dir = self.0.join("control_plane/runtime_runs").join(run_id);
        fs::create_dir_all(&dir).expect("transcript dir");
        fs::write(dir.join("transcript.human.log"), transcript).expect("write transcript");
    }
}

impl Drop for TestHome {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
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

async fn get_transcripts(home: &std::path::Path, q: &str) -> (StatusCode, Option<String>, Value) {
    get_transcripts_uri(
        home,
        &if q.is_empty() {
            "/api/control/transcripts".to_string()
        } else {
            format!("/api/control/transcripts?q={q}")
        },
    )
    .await
}

async fn get_transcripts_uri(
    home: &std::path::Path,
    uri: &str,
) -> (StatusCode, Option<String>, Value) {
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

fn long_log_with_head_needle(needle: &str) -> String {
    let mut body = format!("{needle}\n");
    for line in 0..250 {
        body.push_str(&format!("pad-{line:03} {}\n", "x".repeat(280)));
    }
    body
}

#[tokio::test]
async fn transcripts_search_finds_a_head_needle_beyond_the_tail_and_past_200_snapshots() {
    let home = TestHome::new();
    let needle = "HEAD-NEEDLE-unique-transcript-search";

    for index in 0..200 {
        home.write_run(
            &format!("run-decoy-{index:03}"),
            &format!("2026-09-18T12:{:02}:00+00:00", index % 60),
            "decoy body without the secret\n",
        );
    }
    // Oldest snapshot (sort is newest-first): a take(200) on snapshots would
    // drop this run. Search must still find the needle in its log head.
    home.write_run(
        "run-head-needle",
        "2026-01-01T00:00:00+00:00",
        &long_log_with_head_needle(needle),
    );

    let (status, cache_control, body) = get_transcripts(&home.0, needle).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(cache_control.as_deref(), Some("no-store"));
    assert_eq!(body["q"], needle);
    assert_eq!(body["count"], 1);
    assert_eq!(body["total"], 1);
    assert_eq!(body["offset"], 0);
    assert_eq!(body["has_more"], false);
    let items = body["items"].as_array().expect("items");
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["run_id"], "run-head-needle");
    let snippet = items[0]["snippet"].as_str().expect("snippet");
    assert!(
        snippet.contains(needle),
        "snippet should include the head needle, got {snippet:?}"
    );
    assert!(
        !snippet.contains("<script>"),
        "search payload is JSON text, not markup"
    );
}

fn long_log_with_deep_needle(needle: &str) -> String {
    let mut body = String::new();
    while body.len() < 256 * 1024 + 2048 {
        body.push_str("pad-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n");
    }
    body.push_str(needle);
    body.push('\n');
    body
}

#[tokio::test]
async fn transcripts_search_finds_a_needle_past_the_old_256kib_cap() {
    let home = TestHome::new();
    let needle = "DEEP-NEEDLE-unique-transcript-search";
    home.write_run(
        "run-deep-needle",
        "2026-09-18T00:00:00+00:00",
        &long_log_with_deep_needle(needle),
    );

    let (status, _, body) = get_transcripts(&home.0, needle).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["count"], 1);
    assert_eq!(body["total"], 1);
    assert_eq!(body["items"][0]["run_id"], "run-deep-needle");
    assert!(
        body["items"][0]["snippet"]
            .as_str()
            .expect("snippet")
            .contains(needle)
    );
}

#[tokio::test]
async fn transcripts_search_pages_past_two_hundred_matches() {
    let home = TestHome::new();
    let needle = "PAGE-NEEDLE-unique-transcript-search";
    for index in 0..201 {
        home.write_run(
            &format!("run-page-{index:03}"),
            &format!("2026-09-18T{:02}:{:02}:00+00:00", index / 60, index % 60),
            &format!("{needle}\n"),
        );
    }

    let (status, _, body) = get_transcripts_uri(
        &home.0,
        &format!("/api/control/transcripts?q={needle}&offset=200&limit=200"),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["total"], 201);
    assert_eq!(body["offset"], 200);
    assert_eq!(body["limit"], 200);
    assert_eq!(body["count"], 1);
    assert_eq!(body["has_more"], false);
    let items = body["items"].as_array().expect("items");
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["run_id"], "run-page-000");
}

#[tokio::test]
async fn transcripts_listing_pages_the_whole_corpus_without_a_query() {
    let home = TestHome::new();
    for index in 0..51 {
        home.write_run(
            &format!("run-list-{index:03}"),
            &format!("2026-09-18T{:02}:{:02}:00+00:00", index / 60, index % 60),
            "canonical human log body\n",
        );
    }

    let (status, _, first) = get_transcripts(&home.0, "").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(first["q"], "");
    assert_eq!(first["total"], 51);
    assert_eq!(first["offset"], 0);
    assert_eq!(first["limit"], 50);
    assert_eq!(first["count"], 50);
    assert_eq!(first["has_more"], true);

    let (status, _, second) =
        get_transcripts_uri(&home.0, "/api/control/transcripts?offset=50&limit=50").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(second["total"], 51);
    assert_eq!(second["offset"], 50);
    assert_eq!(second["count"], 1);
    assert_eq!(second["has_more"], false);
    assert_eq!(second["items"][0]["run_id"], "run-list-000");
}
