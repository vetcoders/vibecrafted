//! Integration tests for `GET /api/control/events` (SSE).
//!
//! Run with:
//! ```text
//! cargo test -p vibecrafted-server-web --features ssr --test control_events_sse
//! ```
//!
//! Each test passes its own control-plane home into the router. SSE poll and
//! keepalive are router arguments, not process env.

#![cfg(feature = "ssr")]

use std::fs;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use std::net::SocketAddr;

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode, header};
use futures_util::StreamExt;
use leptos::config::{Env, LeptosOptions};
use tower::ServiceExt;
use vibecrafted_server_web::control::api::{SsePace, control_routes_with};

const TEST_EPOCH: &str = "123e4567-e89b-12d3-a456-426614174000";
const SEGMENT_SCHEMA: &str = "vibecrafted.event-stream-segment.v1";

struct TempHome {
    path: PathBuf,
}

impl TempHome {
    fn new(label: &str) -> Self {
        static NEXT_ID: AtomicU64 = AtomicU64::new(0);
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let path = (0..100)
            .find_map(|attempt| {
                let nonce = NEXT_ID.fetch_add(1, Ordering::Relaxed);
                let candidate = std::env::temp_dir().join(format!(
                    "vc-sse-{label}-{}-{nanos}-{nonce}-{attempt}",
                    std::process::id()
                ));
                match fs::create_dir(&candidate) {
                    Ok(()) => Some(candidate),
                    Err(error) if error.kind() == ErrorKind::AlreadyExists => None,
                    Err(error) => panic!("create isolated sse home: {error}"),
                }
            })
            .expect("allocate an isolated sse home");
        fs::create_dir_all(path.join("control_plane")).expect("create control_plane");
        Self { path }
    }

    fn control_plane(&self) -> PathBuf {
        self.path.join("control_plane")
    }

    fn events_path(&self) -> PathBuf {
        self.control_plane().join("events.jsonl")
    }

    fn write_generation(&self, generation: u64, lines: &[String]) {
        let mut records = vec![segment_header(generation)];
        records.extend_from_slice(lines);
        fs::write(self.events_path(), format!("{}\n", records.join("\n")))
            .expect("write generation");
    }

    fn rotate_to_generation(&self, generation: u64, lines: &[String]) {
        let archive = self.control_plane().join("events_archive");
        fs::create_dir_all(&archive).expect("create archive");
        let previous = generation.checked_sub(1).expect("successor generation");
        fs::rename(
            self.events_path(),
            archive.join(format!("events-{TEST_EPOCH}-g{previous:020}.jsonl")),
        )
        .expect("archive active generation");
        self.write_generation(generation, lines);
    }

    fn append_event_line(&self, line: &str) {
        use std::io::Write;
        if !self.events_path().exists() {
            self.write_generation(0, &[]);
        }
        let mut f = fs::OpenOptions::new()
            .append(true)
            .open(self.events_path())
            .expect("open events.jsonl");
        writeln!(f, "{line}").expect("append event line");
    }

    fn snapshot_tree(&self) -> Vec<PathBuf> {
        let mut out = Vec::new();
        walk(&self.path, &mut out);
        out.sort();
        out
    }
}

impl Drop for TempHome {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let p = entry.path();
        if p.is_dir() {
            walk(&p, out);
        }
        out.push(p);
    }
}

fn test_app(home: &TempHome) -> axum::Router {
    let opts = LeptosOptions::builder()
        .output_name("vibecrafted-server-web-test")
        .site_root("target/site-test")
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr("127.0.0.1:0".parse::<SocketAddr>().expect("addr"))
        .reload_port(0)
        .build();
    control_routes_with(
        &home.path,
        SsePace {
            poll: Duration::from_millis(50),
            keepalive: Duration::from_millis(200),
        },
    )
    .with_state(opts)
}

#[tokio::test]
async fn health_is_constant_and_independent_of_control_plane_history() {
    let home = TempHome::new("health");
    fs::remove_dir_all(home.control_plane()).expect("remove control plane");

    let response = test_app(&home)
        .oneshot(
            Request::builder()
                .uri("/api/health")
                .body(Body::empty())
                .expect("health request"),
        )
        .await
        .expect("health response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), 1024)
        .await
        .expect("health body");
    let payload: serde_json::Value = serde_json::from_slice(&body).expect("health JSON");
    // `version` is the compile-time product stamp (build.rs: VERSION + git
    // sha), so health stays constant-time and history-independent with it.
    assert_eq!(
        payload,
        serde_json::json!({
            "schema": "vibecrafted.health.v1",
            "status": "ok",
            "version": env!("VC_SERVER_VERSION"),
        })
    );
}

async fn collect_sse_until(
    body: Body,
    mut pred: impl FnMut(&str) -> bool,
    timeout: Duration,
) -> String {
    let mut collected = String::new();
    let mut stream = body.into_data_stream();
    let deadline = tokio::time::Instant::now() + timeout;
    loop {
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            break;
        }
        match tokio::time::timeout(remaining, stream.next()).await {
            Ok(Some(Ok(chunk))) => {
                let text = String::from_utf8_lossy(&chunk);
                collected.push_str(&text);
                if pred(&collected) {
                    break;
                }
            }
            Ok(Some(Err(err))) => panic!("body stream error: {err}"),
            Ok(None) => break,
            Err(_) => break,
        }
    }
    collected
}

fn event_line(run_id: &str, kind: &str, message: &str) -> String {
    format!(
        r#"{{"ts":"2026-07-21T06:00:00+00:00","run_id":"{run_id}","kind":"{kind}","message":"{message}","payload":{{}}}}"#
    )
}

fn segment_header(generation: u64) -> String {
    format!(
        r#"{{"ts":"2026-07-26T06:00:00+00:00","run_id":"","kind":"stream.segment","message":"generation {generation}","payload":{{"schema":"{SEGMENT_SCHEMA}","epoch":"{TEST_EPOCH}","generation":{generation}}}}}"#
    )
}

fn settlement_event_line(run_id: &str, revision: u64) -> String {
    format!(
        r#"{{"ts":"2026-07-26T06:00:00+00:00","run_id":"{run_id}","kind":"settlement.changed","message":"settlement revision {revision}","payload":{{"schema":"vibecrafted.settlement-event.v1","run_id":"{run_id}","previous":null,"current":{{"verdict":"needs_attention","tui":"n"}},"reason":"report_without_seal","source":"await","settled_at":"2026-07-26T06:00:00+00:00","claim_digest":"claim-123","waived":false,"revision":{revision}}}}}"#
    )
}

fn sse_frames(body: &str) -> Vec<(String, String, String)> {
    let mut frames = Vec::new();
    let mut event = String::new();
    let mut id = String::new();
    let mut data = String::new();
    for line in body.lines() {
        let line = line.trim_end_matches('\r');
        if line.is_empty() {
            if !id.is_empty() || !data.is_empty() || !event.is_empty() {
                frames.push((event.clone(), id.clone(), data.clone()));
            }
            event.clear();
            id.clear();
            data.clear();
        } else if let Some(value) = line.strip_prefix("event: ") {
            event = value.to_string();
        } else if let Some(value) = line.strip_prefix("id: ") {
            id = value.to_string();
        } else if let Some(value) = line.strip_prefix("data: ") {
            data = value.to_string();
        }
    }
    if !id.is_empty() || !data.is_empty() || !event.is_empty() {
        frames.push((event, id, data));
    }
    frames
}

/// IDs belonging to control-plane data events (not stream protocol frames).
fn sse_ids(body: &str) -> Vec<String> {
    sse_frames(body)
        .into_iter()
        .filter_map(|(_, id, data)| {
            serde_json::from_str::<serde_json::Value>(&data)
                .ok()
                .filter(|value| value.get("run_id").is_some())
                .map(|_| id)
        })
        .collect()
}

fn sse_data_payloads(body: &str) -> Vec<String> {
    sse_frames(body)
        .into_iter()
        .filter_map(|(_, _, data)| {
            serde_json::from_str::<serde_json::Value>(&data)
                .ok()
                .filter(|value| value.get("run_id").is_some())
                .map(|_| data)
        })
        .collect()
}

fn named_sse_payloads(body: &str, name: &str) -> Vec<String> {
    sse_frames(body)
        .into_iter()
        .filter_map(|(event, _, data)| (event == name).then_some(data))
        .collect()
}

#[tokio::test]
async fn sse_streams_appended_event_with_id_and_json_data() {
    let home = TempHome::new("append");
    let line_a = event_line("run-a", "spawn", "started");
    home.append_event_line(&line_a);

    let app = test_app(&home);
    let response = app
        .oneshot(
            Request::builder()
                .uri("/api/control/events")
                .header(header::ACCEPT, "text/event-stream")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot");

    assert_eq!(response.status(), StatusCode::OK);
    let ct = response
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    assert!(
        ct.starts_with("text/event-stream"),
        "content-type must be text/event-stream, got {ct:?}"
    );

    let body = collect_sse_until(
        response.into_body(),
        |s| s.contains("data: ") && s.contains("run-a"),
        Duration::from_secs(3),
    )
    .await;

    assert!(
        body.contains("id: "),
        "SSE frame must carry id: cursor\n{body}"
    );
    let data = sse_data_payloads(&body);
    assert!(!data.is_empty(), "expected data: frames\n{body}");
    let parsed: serde_json::Value = serde_json::from_str(&data[0]).expect("data JSON must parse");
    assert_eq!(parsed["run_id"], "run-a");
    assert_eq!(parsed["kind"], "spawn");
    assert_eq!(parsed["message"], "started");
}

#[tokio::test]
async fn sse_streams_typed_settlement_frame() {
    let home = TempHome::new("settlement-frame");
    home.append_event_line(&settlement_event_line("run-settled", 7));

    let response = test_app(&home)
        .oneshot(
            Request::builder()
                .uri("/api/control/events")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot settlement");
    let body = collect_sse_until(
        response.into_body(),
        |stream| stream.contains("vibecrafted.settlement-event.v1"),
        Duration::from_secs(3),
    )
    .await;

    let data = sse_data_payloads(&body);
    assert_eq!(data.len(), 1, "expected exactly one typed frame\n{body}");
    let frame: serde_json::Value = serde_json::from_str(&data[0]).expect("typed data JSON");
    assert_eq!(frame["kind"], "settlement.changed");
    assert_eq!(
        frame["payload"]["schema"],
        "vibecrafted.settlement-event.v1"
    );
    assert_eq!(frame["payload"]["run_id"], "run-settled");
    assert_eq!(frame["payload"]["previous"], serde_json::Value::Null);
    assert_eq!(frame["payload"]["current"]["verdict"], "needs_attention");
    assert_eq!(frame["payload"]["current"]["tui"], "n");
    assert_eq!(frame["payload"]["source"], "await");
    assert_eq!(frame["payload"]["revision"], 7);
    assert!(
        frame.get("cursor").is_none(),
        "reader cursor belongs in SSE id, not data: {frame}"
    );
}

#[tokio::test]
async fn sse_reconnect_with_since_cursor_does_not_duplicate_or_skip() {
    let home = TempHome::new("reconnect");
    let line_a = event_line("run-1", "spawn", "one");
    let line_b = event_line("run-1", "progress", "two");
    let line_c = event_line("run-1", "done", "three");
    home.append_event_line(&line_a);
    home.append_event_line(&line_b);
    home.append_event_line(&line_c);

    // Full drain first to learn middle cursor (after event B).
    let app = test_app(&home);
    let response = app
        .oneshot(
            Request::builder()
                .uri("/api/control/events")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot full");
    let full = collect_sse_until(
        response.into_body(),
        |s| sse_ids(s).len() >= 3,
        Duration::from_secs(3),
    )
    .await;
    let ids = sse_ids(&full);
    assert_eq!(ids.len(), 3, "expected three id frames\n{full}");
    let mid_cursor = &ids[1]; // after event B

    // Reconnect from middle cursor: should see only event C (no A/B, no dup of B).
    let app = test_app(&home);
    let response = app
        .oneshot(
            Request::builder()
                .uri(format!("/api/control/events?since={mid_cursor}"))
                .header("Last-Event-ID", mid_cursor.as_str())
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot resume");
    let resumed = collect_sse_until(
        response.into_body(),
        |s| s.contains("\"message\":\"three\""),
        Duration::from_secs(3),
    )
    .await;

    let messages: Vec<_> = sse_data_payloads(&resumed)
        .into_iter()
        .filter_map(|d| {
            serde_json::from_str::<serde_json::Value>(&d)
                .ok()
                .and_then(|v| v["message"].as_str().map(str::to_string))
        })
        .collect();
    assert_eq!(
        messages,
        vec!["three".to_string()],
        "reconnect must emit only events after mid cursor\n{resumed}"
    );
    assert!(
        !resumed.contains("\"message\":\"one\"") && !resumed.contains("\"message\":\"two\""),
        "must not re-emit earlier events\n{resumed}"
    );
}

#[tokio::test]
async fn sse_reconnect_recovers_when_cursor_is_beyond_rotated_eof() {
    let home = TempHome::new("rotation-reconnect");
    let old_line = event_line("run-old", "progress", &"old".repeat(200));
    home.append_event_line(&old_line);

    let response = test_app(&home)
        .oneshot(
            Request::builder()
                .uri("/api/control/events")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot old generation");
    let old_body = collect_sse_until(
        response.into_body(),
        |stream| stream.contains("run-old"),
        Duration::from_secs(3),
    )
    .await;
    let old_cursor = sse_ids(&old_body)
        .into_iter()
        .next()
        .expect("old generation cursor");

    assert!(
        old_cursor.starts_with("v2:"),
        "new clients must receive an opaque v2 cursor: {old_cursor}"
    );
    let new_line = settlement_event_line("run-after-rotation", 1).replace(
        "\"message\":\"settlement",
        &format!(
            "\"padding\":\"{}\",\"message\":\"settlement",
            "x".repeat(800)
        ),
    );
    home.rotate_to_generation(1, &[new_line]);

    let response = test_app(&home)
        .oneshot(
            Request::builder()
                .uri("/api/control/events")
                .header("Last-Event-ID", &old_cursor)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot rotated generation");
    let recovered = collect_sse_until(
        response.into_body(),
        |stream| stream.contains("run-after-rotation"),
        Duration::from_secs(3),
    )
    .await;

    let payloads = sse_data_payloads(&recovered);
    assert_eq!(
        payloads.len(),
        1,
        "rotation recovery duplicated frame\n{recovered}"
    );
    let frame: serde_json::Value = serde_json::from_str(&payloads[0]).expect("recovered data JSON");
    assert_eq!(frame["run_id"], "run-after-rotation");
    assert_eq!(frame["payload"]["revision"], 1);
    assert!(
        !recovered.contains("run-old"),
        "old generation must not be replayed\n{recovered}"
    );
    assert!(
        !named_sse_payloads(&recovered, "stream.boundary").is_empty(),
        "archive -> active crossing needs an explicit boundary\n{recovered}"
    );
}

#[tokio::test]
async fn sse_last_event_id_header_resumes_without_query() {
    let home = TempHome::new("last-event-id");
    let line_a = event_line("run-x", "spawn", "alpha");
    let line_b = event_line("run-x", "done", "beta");
    home.append_event_line(&line_a);
    home.append_event_line(&line_b);

    let app = test_app(&home);
    let response = app
        .oneshot(
            Request::builder()
                .uri("/api/control/events")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot");
    let full = collect_sse_until(
        response.into_body(),
        |s| sse_ids(s).len() >= 2,
        Duration::from_secs(3),
    )
    .await;
    let first_id = sse_ids(&full).into_iter().next().expect("first id");

    let app = test_app(&home);
    let response = app
        .oneshot(
            Request::builder()
                .uri("/api/control/events")
                .header("Last-Event-ID", first_id.as_str())
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot last-event-id");
    let resumed = collect_sse_until(
        response.into_body(),
        |s| s.contains("\"message\":\"beta\""),
        Duration::from_secs(3),
    )
    .await;
    let messages: Vec<_> = sse_data_payloads(&resumed)
        .into_iter()
        .filter_map(|d| {
            serde_json::from_str::<serde_json::Value>(&d)
                .ok()
                .and_then(|v| v["message"].as_str().map(str::to_string))
        })
        .collect();
    assert_eq!(messages, vec!["beta".to_string()], "body:\n{resumed}");
}

#[tokio::test]
async fn legacy_zero_migrates_to_v2_and_catches_up_once() {
    let home = TempHome::new("legacy-zero-migration");
    home.write_generation(0, &[event_line("legacy-zero", "progress", "baseline")]);

    let response = test_app(&home)
        .oneshot(
            Request::builder()
                .uri("/api/control/events?since=0")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot legacy zero");
    let body = collect_sse_until(
        response.into_body(),
        |stream| stream.contains("event: stream.caught-up"),
        Duration::from_secs(3),
    )
    .await;

    assert_eq!(sse_data_payloads(&body).len(), 1, "baseline lost\n{body}");
    let boundaries = named_sse_payloads(&body, "stream.boundary");
    assert_eq!(
        boundaries.len(),
        1,
        "one migration boundary expected\n{body}"
    );
    let boundary: serde_json::Value = serde_json::from_str(&boundaries[0]).expect("boundary JSON");
    assert_eq!(boundary["from"], "0");
    assert!(
        boundary["to"]
            .as_str()
            .is_some_and(|cursor| cursor.starts_with("v2:"))
    );
    assert_eq!(
        named_sse_payloads(&body, "stream.caught-up").len(),
        1,
        "caught-up must be one receipt\n{body}"
    );
}

#[tokio::test]
async fn legacy_nonzero_segmented_emits_gap_not_infinite_baseline() {
    let home = TempHome::new("legacy-nonzero-gap");
    home.write_generation(
        0,
        &[event_line(
            "must-not-replay",
            "settlement.changed",
            "effect",
        )],
    );

    let response = test_app(&home)
        .oneshot(
            Request::builder()
                .uri("/api/control/events?since=42")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot legacy nonzero");
    let body = collect_sse_until(
        response.into_body(),
        |stream| stream.contains("event: stream.caught-up"),
        Duration::from_secs(3),
    )
    .await;

    assert!(
        sse_data_payloads(&body).is_empty(),
        "ambiguous legacy history must not replay effects\n{body}"
    );
    let gaps = named_sse_payloads(&body, "stream.gap");
    assert_eq!(gaps.len(), 1, "one resnapshot gap expected\n{body}");
    let gap: serde_json::Value = serde_json::from_str(&gaps[0]).expect("gap JSON");
    assert_eq!(gap["requested"], "42");
    assert_eq!(gap["reason"], "legacy_cursor_generation_unknown");
    assert_eq!(gap["action"], "resnapshot");
    assert_eq!(
        named_sse_payloads(&body, "stream.caught-up").len(),
        1,
        "gap recovery must converge instead of polling forever\n{body}"
    );
}

#[tokio::test]
async fn rotation_after_window_drains_archived_baseline_before_caught_up() {
    let home = TempHome::new("rotate-after-window");
    let baseline = vec![
        event_line("window-run", "progress", "base-0"),
        event_line("window-run", "progress", "base-1"),
        event_line("window-run", "progress", "base-2"),
    ];
    home.write_generation(0, &baseline);

    // `oneshot` constructs the SSE response and captures the connection
    // window; the body has not been polled yet.
    let response = test_app(&home)
        .oneshot(
            Request::builder()
                .uri("/api/control/events")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot connection window");
    home.rotate_to_generation(1, &[]);

    let body = collect_sse_until(
        response.into_body(),
        |stream| stream.contains("event: stream.caught-up"),
        Duration::from_secs(3),
    )
    .await;

    let messages: Vec<_> = sse_data_payloads(&body)
        .into_iter()
        .filter_map(|data| {
            serde_json::from_str::<serde_json::Value>(&data)
                .ok()
                .and_then(|value| value["message"].as_str().map(str::to_string))
        })
        .collect();
    assert_eq!(messages, vec!["base-0", "base-1", "base-2"]);
    assert!(
        !named_sse_payloads(&body, "stream.boundary").is_empty(),
        "rotation crossing must be explicit\n{body}"
    );
    let baseline_position = body.rfind("\"message\":\"base-2\"").expect("last baseline");
    let caught_position = body
        .find("event: stream.caught-up")
        .expect("caught-up frame");
    assert!(
        baseline_position < caught_position,
        "caught-up preceded archived baseline\n{body}"
    );
}

#[tokio::test]
async fn sse_caught_up_targets_connection_high_watermark_under_continuous_traffic() {
    let home = TempHome::new("caught-up");
    let baseline: Vec<_> = (0..200)
        .map(|index| event_line("run-busy", "progress", &format!("base-{index}")))
        .collect();
    home.write_generation(0, &baseline);

    let response = test_app(&home)
        .oneshot(
            Request::builder()
                .uri("/api/control/events")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot busy baseline");

    // Traffic continues after connection acceptance. Caught-up targets the
    // captured baseline, not a quiet EOF or a heartbeat.
    for index in 0..100 {
        home.append_event_line(&event_line(
            "run-busy",
            "progress",
            &format!("live-{index}"),
        ));
    }
    let body = collect_sse_until(
        response.into_body(),
        |stream| stream.contains("event: stream.caught-up"),
        Duration::from_secs(5),
    )
    .await;

    assert!(
        sse_data_payloads(&body).len() >= baseline.len(),
        "caught-up fired before the captured baseline drained\n{body}"
    );
    let caught = named_sse_payloads(&body, "stream.caught-up");
    assert_eq!(caught.len(), 1, "caught-up is a one-shot receipt\n{body}");
    let payload: serde_json::Value = serde_json::from_str(&caught[0]).expect("caught-up JSON");
    assert_eq!(payload["kind"], "stream.caught-up");
    assert!(
        payload["high_watermark"]
            .as_str()
            .is_some_and(|cursor| cursor.starts_with("v2:"))
    );
}

#[tokio::test]
async fn sse_expired_generation_emits_gap_without_effect_replay() {
    let home = TempHome::new("expired-generation");
    home.write_generation(
        2,
        &[event_line(
            "must-not-replay",
            "settlement.changed",
            "old effect",
        )],
    );
    let expired = format!("v2:{TEST_EPOCH}:0:128");
    let response = test_app(&home)
        .oneshot(
            Request::builder()
                .uri(format!("/api/control/events?since={expired}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot expired generation");
    let body = collect_sse_until(
        response.into_body(),
        |stream| stream.contains("event: stream.gap"),
        Duration::from_secs(3),
    )
    .await;

    assert!(
        sse_data_payloads(&body).is_empty(),
        "unknown history must not replay active effects\n{body}"
    );
    let gaps = named_sse_payloads(&body, "stream.gap");
    assert_eq!(gaps.len(), 1, "one explicit gap receipt expected\n{body}");
    let gap: serde_json::Value = serde_json::from_str(&gaps[0]).expect("gap JSON");
    assert_eq!(gap["requested"], expired);
    assert_eq!(gap["action"], "resnapshot");
    assert_eq!(gap["reason"], "generation_expired_or_unknown");
}

#[tokio::test]
async fn sse_heartbeat_comment_on_empty_stream() {
    let home = TempHome::new("heartbeat");
    // No events.jsonl — pure silence; keepalive must still fire.
    let app = test_app(&home);
    let response = app
        .oneshot(
            Request::builder()
                .uri("/api/control/events?since=0")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot empty");

    assert_eq!(response.status(), StatusCode::OK);
    let body = collect_sse_until(
        response.into_body(),
        |s| s.contains(": ping") || s.contains(":ping"),
        Duration::from_secs(3),
    )
    .await;
    assert!(
        body.contains(": ping") || body.contains(":ping"),
        "expected SSE comment heartbeat `: ping` under silence\n{body:?}"
    );
}

#[tokio::test]
async fn sse_session_writes_nothing_to_control_plane() {
    let home = TempHome::new("readonly");
    home.append_event_line(&event_line("run-ro", "spawn", "hi"));
    let before = home.snapshot_tree();
    let before_meta: Vec<_> = before
        .iter()
        .filter_map(|p| {
            fs::metadata(p)
                .ok()
                .map(|m| (p.clone(), m.len(), m.modified().ok()))
        })
        .collect();

    let app = test_app(&home);
    let response = app
        .oneshot(
            Request::builder()
                .uri("/api/control/events?since=0")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot");
    let _body = collect_sse_until(
        response.into_body(),
        |s| s.contains("run-ro"),
        Duration::from_secs(3),
    )
    .await;

    // Drop response so stream task ends; give a beat for any deferred write.
    tokio::time::sleep(Duration::from_millis(100)).await;

    let after = home.snapshot_tree();
    assert_eq!(
        before, after,
        "server must not create new files under control-plane during SSE"
    );
    for (path, len, mtime) in &before_meta {
        let meta = fs::metadata(path).expect("metadata after");
        assert_eq!(meta.len(), *len, "file size changed: {}", path.display());
        // events.jsonl is only read — mtime must not advance from a write.
        if path.file_name().and_then(|n| n.to_str()) == Some("events.jsonl") {
            assert_eq!(
                meta.modified().ok(),
                *mtime,
                "events.jsonl mtime changed (server wrote?): {}",
                path.display()
            );
        }
    }
}
