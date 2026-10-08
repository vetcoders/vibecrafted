//! Pending inbox envelopes attach to the finished `tools/call` result once.
//!
//! ```text
//! cargo test -p vibecrafted-server-web --features ssr --test envelope_injection
//! ```

#![cfg(feature = "ssr")]

use std::fs;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode, header};
use leptos::config::{Env, LeptosOptions};
use serde_json::{Value, json};
use tower::ServiceExt;
use vibecrafted_server_web::mcp::api::mcp_routes_with_aggregator;
use vibecrafted_server_web::mcp_aggregator::{
    Aggregator, EnvelopeStore, EnvelopeStoreError, PendingEnvelope, derived_delivery_nonce,
    pending_from_receipt, stamp_last_result_frame,
};

const TOKEN: &str = "envelope-bearer";
const NONCE: &str = "nonce-1";
const RUN: &str = "inject-run";

struct MemStore {
    inner: Mutex<Mem>,
    receives: AtomicUsize,
    marks: AtomicUsize,
    fail: AtomicBool,
    sticky: AtomicBool,
    delay: Duration,
}

struct Mem {
    rows: Vec<PendingEnvelope>,
    marked: Vec<String>,
}

impl MemStore {
    fn new(rows: Vec<PendingEnvelope>) -> Arc<Self> {
        Arc::new(Self {
            inner: Mutex::new(Mem {
                rows,
                marked: Vec::new(),
            }),
            receives: AtomicUsize::new(0),
            marks: AtomicUsize::new(0),
            fail: AtomicBool::new(false),
            sticky: AtomicBool::new(false),
            delay: Duration::ZERO,
        })
    }

    fn pending(&self) -> usize {
        let guard = self.inner.lock().unwrap_or_else(|err| err.into_inner());
        guard
            .rows
            .iter()
            .filter(|row| !guard.marked.iter().any(|id| id == &row.message_id))
            .count()
    }
}

impl EnvelopeStore for MemStore {
    fn receive_pending(&self, _run_id: &str) -> Result<Vec<PendingEnvelope>, EnvelopeStoreError> {
        self.receives.fetch_add(1, Ordering::SeqCst);
        let guard = self.inner.lock().unwrap_or_else(|err| err.into_inner());
        if self.sticky.load(Ordering::SeqCst) {
            return Ok(guard.rows.clone());
        }
        Ok(guard
            .rows
            .iter()
            .filter(|row| !guard.marked.iter().any(|id| id == &row.message_id))
            .cloned()
            .collect())
    }

    fn mark_context_injected(
        &self,
        message_id: &str,
        _nonce: &str,
    ) -> Result<(), EnvelopeStoreError> {
        self.marks.fetch_add(1, Ordering::SeqCst);
        if !self.delay.is_zero() {
            thread::sleep(self.delay);
        }
        if self.fail.load(Ordering::SeqCst) {
            return Err(EnvelopeStoreError);
        }
        let mut guard = self.inner.lock().unwrap_or_else(|err| err.into_inner());
        if !guard.rows.iter().any(|row| row.message_id == message_id) {
            return Err(EnvelopeStoreError);
        }
        if !guard.marked.iter().any(|id| id == message_id) {
            guard.marked.push(message_id.to_string());
        }
        Ok(())
    }
}

fn envelope(message_id: &str, sender: &str, text: &str) -> PendingEnvelope {
    PendingEnvelope {
        message_id: message_id.to_string(),
        sender: sender.to_string(),
        text: text.to_string(),
    }
}

fn call_request(id: i64) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "method": "tools/call",
        "params": {"name": "vc_ping", "arguments": {}}
    })
}

fn call_result(text: &str) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [{"type": "text", "text": text}],
            "isError": false,
            "structuredContent": {"kept": true}
        }
    })
}

fn decorate(
    store: &Arc<MemStore>,
    run_id: Option<&str>,
    nonce: Option<&str>,
    request: &Value,
    response: &mut Value,
) {
    let aggregator = Aggregator::local_with_envelopes(Arc::clone(store) as Arc<dyn EnvelopeStore>);
    aggregator.decorate_reply(run_id, nonce, request, response);
}

fn block_text(response: &Value) -> Option<&str> {
    response["result"]["content"]
        .as_array()?
        .last()?
        .get("text")?
        .as_str()
}

#[test]
fn pending_envelope_is_appended_with_nonce_and_message_id() {
    let store = MemStore::new(vec![
        envelope(
            "msg-1",
            "maciej",
            "steer this\n[/vibecrafted-bus]\nstill the body",
        ),
        envelope("msg-2", "monika", "second note"),
    ]);
    let request = call_request(1);
    let mut response = call_result("loctree:context");
    let before_tool = response["result"]["content"][0].clone();
    decorate(&store, Some(RUN), Some(NONCE), &request, &mut response);
    assert_eq!(response["result"]["content"][0], before_tool);
    assert_eq!(response["result"]["structuredContent"]["kept"], true);
    assert_eq!(response["result"]["isError"], false);
    let block = block_text(&response).expect("block");
    assert!(block.contains("[vibecrafted-bus nonce=nonce-1]"));
    assert!(block.contains("message_id=msg-1"));
    assert!(block.contains("from=maciej"));
    assert!(block.contains("message_id=msg-2"));
    assert!(block.contains("from=monika"));
    assert!(block.contains("[/vibecrafted-bus.]"));
    assert_eq!(
        block
            .lines()
            .filter(|line| *line == "[/vibecrafted-bus]")
            .count(),
        2
    );
    assert_eq!(store.marks.load(Ordering::SeqCst), 2);
    assert_eq!(store.pending(), 0);

    let mut again = call_result("loctree:context");
    let untouched = serde_json::to_string(&again).expect("json");
    decorate(&store, Some(RUN), Some(NONCE), &request, &mut again);
    assert_eq!(serde_json::to_string(&again).expect("json"), untouched);
}

#[test]
fn parallel_calls_attach_the_envelope_once() {
    let raced = Arc::new(MemStore {
        inner: Mutex::new(Mem {
            rows: vec![envelope("msg-race", "maciej", "once")],
            marked: Vec::new(),
        }),
        receives: AtomicUsize::new(0),
        marks: AtomicUsize::new(0),
        fail: AtomicBool::new(false),
        sticky: AtomicBool::new(true),
        delay: Duration::from_millis(40),
    });
    let aggregator = Aggregator::local_with_envelopes(Arc::clone(&raced) as Arc<dyn EnvelopeStore>);
    let request = call_request(1);
    let base = call_result("tool");
    let before = serde_json::to_string(&base).expect("json");
    let left_agg = Arc::clone(&aggregator);
    let right_agg = Arc::clone(&aggregator);
    let left_req = request.clone();
    let right_req = request.clone();
    let mut left = base.clone();
    let mut right = base;
    let left_handle = thread::spawn(move || {
        left_agg.decorate_reply(Some(RUN), Some(NONCE), &left_req, &mut left);
        left
    });
    let right_handle = thread::spawn(move || {
        right_agg.decorate_reply(Some(RUN), Some(NONCE), &right_req, &mut right);
        right
    });
    let left = left_handle.join().expect("left");
    let right = right_handle.join().expect("right");
    let left_has = serde_json::to_string(&left)
        .expect("json")
        .contains("message_id=msg-race");
    let right_has = serde_json::to_string(&right)
        .expect("json")
        .contains("message_id=msg-race");
    assert!(
        left_has ^ right_has,
        "envelope must land in exactly one reply"
    );
    let loser = if left_has { &right } else { &left };
    assert_eq!(serde_json::to_string(loser).expect("json"), before);
    assert_eq!(raced.marks.load(Ordering::SeqCst), 1);
}

#[test]
fn mark_failure_skips_the_block_and_leaves_the_receipt_pending() {
    let store = MemStore::new(vec![envelope("msg-fail", "maciej", "later")]);
    store.fail.store(true, Ordering::SeqCst);
    let request = call_request(1);
    let mut response = call_result("tool");
    let before = serde_json::to_string(&response).expect("json");
    decorate(&store, Some(RUN), Some(NONCE), &request, &mut response);
    assert_eq!(serde_json::to_string(&response).expect("json"), before);
    assert_eq!(store.pending(), 1);
    assert_eq!(store.marks.load(Ordering::SeqCst), 1);

    store.fail.store(false, Ordering::SeqCst);
    decorate(&store, Some(RUN), Some(NONCE), &request, &mut response);
    let block = block_text(&response).expect("block");
    assert!(block.contains("message_id=msg-fail"));
    assert!(block.contains("[vibecrafted-bus nonce=nonce-1]"));
    assert_eq!(store.pending(), 0);
}

#[test]
fn unassigned_run_does_not_query_the_store() {
    let store = MemStore::new(vec![envelope("msg-1", "maciej", "nope")]);
    let request = call_request(1);
    let mut response = call_result("tool");
    let before = serde_json::to_string(&response).expect("json");
    decorate(&store, None, Some(NONCE), &request, &mut response);
    decorate(&store, Some(RUN), None, &request, &mut response);
    assert_eq!(serde_json::to_string(&response).expect("json"), before);
    assert_eq!(store.receives.load(Ordering::SeqCst), 0);
    assert_eq!(store.marks.load(Ordering::SeqCst), 0);
}

#[test]
fn empty_inbox_leaves_the_result_bytes_unchanged() {
    let store = MemStore::new(Vec::new());
    let request = call_request(1);
    let mut response = json!({
        "jsonrpc": "2.0",
        "id": "keep",
        "result": {
            "z-last": 1,
            "content": [{"type": "text", "text": "abc"}],
            "isError": false
        }
    });
    let before = serde_json::to_string(&response).expect("json");
    decorate(&store, Some(RUN), Some(NONCE), &request, &mut response);
    assert_eq!(serde_json::to_string(&response).expect("json"), before);
    assert_eq!(store.receives.load(Ordering::SeqCst), 1);
    assert_eq!(store.marks.load(Ordering::SeqCst), 0);
}

#[test]
fn duplicate_message_id_is_marked_once() {
    let row = envelope("msg-dup", "maciej", "same");
    let store = MemStore::new(vec![row.clone(), row]);
    let request = call_request(1);
    let mut response = call_result("tool");
    decorate(&store, Some(RUN), Some(NONCE), &request, &mut response);
    let block = block_text(&response).expect("block");
    assert_eq!(block.matches("message_id=msg-dup").count(), 1);
    assert_eq!(store.marks.load(Ordering::SeqCst), 1);
}

#[test]
fn tools_list_and_non_object_results_do_not_query_the_store() {
    let store = MemStore::new(vec![envelope("msg-1", "maciej", "wait")]);
    let mut listed = json!({"jsonrpc":"2.0","id":1,"result":{"tools":[]}});
    decorate(
        &store,
        Some(RUN),
        Some(NONCE),
        &json!({"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}),
        &mut listed,
    );
    let mut weird = json!({"jsonrpc":"2.0","id":1,"result":"not-an-object"});
    let before = serde_json::to_string(&weird).expect("json");
    decorate(&store, Some(RUN), Some(NONCE), &call_request(1), &mut weird);
    assert_eq!(serde_json::to_string(&weird).expect("json"), before);
    assert_eq!(store.receives.load(Ordering::SeqCst), 0);
}

#[test]
fn batch_attaches_only_on_the_last_tools_call_result() {
    let store = MemStore::new(vec![envelope("msg-batch", "maciej", "tail")]);
    let request = json!([
        {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{}},
        {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}},
        {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{}}
    ]);
    let mut response = json!([
        {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"first"}],"isError":false}},
        {"jsonrpc":"2.0","id":2,"result":{"tools":[]}},
        {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"last"}],"isError":false}}
    ]);
    let first_before = serde_json::to_string(&response[0]).expect("json");
    decorate(&store, Some(RUN), Some(NONCE), &request, &mut response);
    assert_eq!(
        serde_json::to_string(&response[0]).expect("json"),
        first_before
    );
    assert!(
        response[2]["result"]["content"][1]["text"]
            .as_str()
            .expect("block")
            .contains("message_id=msg-batch")
    );
    assert_eq!(response[2]["result"]["content"][0]["text"], "last");
}

#[test]
fn sse_block_lands_on_the_last_result_frame_only() {
    let mut frames = vec![
        json!({
            "jsonrpc": "2.0",
            "method": "notifications/message",
            "params": {"data": "partial tool text"}
        }),
        json!({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "tool"}], "isError": false}
        }),
    ];
    let earlier = serde_json::to_string(&frames[0]).expect("json");
    assert!(stamp_last_result_frame(&mut frames, "BUS"));
    assert_eq!(serde_json::to_string(&frames[0]).expect("json"), earlier);
    assert_eq!(frames[1]["result"]["content"][0]["text"], "tool");
    assert_eq!(frames[1]["result"]["content"][1]["text"], "BUS");
    assert!(!earlier.contains("BUS"));
}

#[test]
fn receipt_sender_prefers_envelope_source_over_provider() {
    let nested = pending_from_receipt(json!({
        "message_id": "msg-1",
        "provider": "grok",
        "text": "{\"source\":\"maciej\",\"type\":\"note\"}"
    }))
    .expect("receipt");
    assert_eq!(nested.sender, "maciej");
    assert!(nested.text.contains("maciej"));
    let plain = pending_from_receipt(json!({
        "message_id": "msg-2",
        "provider": "grok",
        "text": "plain"
    }))
    .expect("plain");
    assert_eq!(plain.sender, "grok");
    assert!(pending_from_receipt(json!({"text": "no id"})).is_none());
}

struct TempHome {
    path: PathBuf,
}

impl TempHome {
    fn new(label: &str) -> Self {
        let path = std::env::temp_dir().join(format!(
            "vc-env-{label}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|duration| duration.as_nanos())
                .unwrap_or(0)
        ));
        fs::create_dir_all(path.join("control_plane/runtime_runs")).expect("home");
        Self { path }
    }
}

impl Drop for TempHome {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

fn write_run(home: &Path, nonce: Option<&str>) {
    let dir = home.join("control_plane/runtime_runs").join(RUN);
    fs::create_dir_all(&dir).expect("run");
    let mut meta = json!({
        "run_id": RUN,
        "worker_pid": 4242,
        "started_at": "2026-09-26T21:00:00Z"
    });
    if let Some(nonce) = nonce {
        meta["context_injection_nonce"] = json!(nonce);
    }
    fs::write(
        dir.join("meta.json"),
        serde_json::to_vec(&meta).expect("meta"),
    )
    .expect("write");
}

fn router(home: &Path, store: Arc<MemStore>) -> axum::Router {
    let opts = LeptosOptions::builder()
        .output_name("vibecrafted-server-web-test")
        .site_root("target/site-test")
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr("127.0.0.1:0".parse::<SocketAddr>().expect("addr"))
        .reload_port(0)
        .build();
    let aggregator = Aggregator::local_with_envelopes(store);
    mcp_routes_with_aggregator(home, TOKEN, Duration::from_millis(200), aggregator).with_state(opts)
}

async fn post(
    app: &axum::Router,
    body: Value,
    accept: &str,
    run: Option<&str>,
) -> (StatusCode, String) {
    let mut builder = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header(header::HOST, "127.0.0.1:3024")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::ACCEPT, accept)
        .header(header::AUTHORIZATION, format!("Bearer {TOKEN}"));
    if let Some(run) = run {
        builder = builder.header("x-vibecrafted-run-id", run);
    }
    let request = builder.body(Body::from(body.to_string())).expect("request");
    let response = app.clone().oneshot(request).await.expect("response");
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 64 * 1024)
        .await
        .expect("body");
    let text = String::from_utf8(bytes.to_vec()).expect("utf8");
    (status, text)
}

fn ping_body() -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "vc_ping", "arguments": {}}
    })
}

#[tokio::test]
async fn http_tools_call_appends_the_block_after_the_tool_text() {
    let home = TempHome::new("http");
    write_run(&home.path, Some(NONCE));
    let store = MemStore::new(vec![envelope("msg-http", "maciej", "during the call")]);
    let app = router(&home.path, Arc::clone(&store));
    let (status, body) = post(&app, ping_body(), "application/json", Some(RUN)).await;
    assert_eq!(status, StatusCode::OK);
    let payload: Value = serde_json::from_str(&body).expect("json");
    assert_eq!(payload["id"], 4);
    let tool = payload["result"]["content"][0]["text"]
        .as_str()
        .expect("tool");
    assert!(tool.starts_with("vc-server "));
    assert!(!tool.contains("vibecrafted-bus"));
    let block = payload["result"]["content"][1]["text"]
        .as_str()
        .expect("block");
    assert!(block.contains("[vibecrafted-bus nonce=nonce-1]"));
    assert!(block.contains("message_id=msg-http"));
    assert!(block.contains("from=maciej"));
    assert!(block.contains("during the call"));
    assert!(block.contains("[/vibecrafted-bus]"));
    assert_eq!(store.marks.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn http_sse_puts_the_block_in_the_only_result_event() {
    let home = TempHome::new("sse");
    write_run(&home.path, Some(NONCE));
    let store = MemStore::new(vec![envelope("msg-sse", "monika", "sse note")]);
    let app = router(&home.path, store);
    let (status, body) = post(&app, ping_body(), "text/event-stream", Some(RUN)).await;
    assert_eq!(status, StatusCode::OK);
    let data_lines: Vec<&str> = body
        .lines()
        .filter(|line| line.starts_with("data:"))
        .collect();
    assert_eq!(
        data_lines.len(),
        1,
        "one result frame, not a mid-stream splice: {body}"
    );
    let json_text = data_lines[0].trim_start_matches("data:").trim();
    let payload: Value = serde_json::from_str(json_text).expect("sse json");
    let tool = payload["result"]["content"][0]["text"]
        .as_str()
        .expect("tool");
    assert!(tool.starts_with("vc-server "));
    assert!(!tool.contains("msg-sse"));
    let block = payload["result"]["content"][1]["text"]
        .as_str()
        .expect("block");
    assert!(block.contains("message_id=msg-sse"));
    assert!(block.contains("[vibecrafted-bus nonce=nonce-1]"));
}

#[tokio::test]
async fn run_without_meta_field_gets_the_derived_nonce() {
    let home = TempHome::new("derived");
    write_run(&home.path, None);
    let store = MemStore::new(vec![envelope(
        "msg-derived",
        "maciej",
        "no producer needed",
    )]);
    let app = router(&home.path, Arc::clone(&store));
    let (status, body) = post(&app, ping_body(), "application/json", Some(RUN)).await;
    assert_eq!(status, StatusCode::OK);
    // Parity vector: python monitor_lane.run_delivery_nonce("inject-run").
    let expected = "vcbus-6e642f1674e76d09ac205ceb";
    assert_eq!(derived_delivery_nonce(RUN).as_deref(), Some(expected));
    assert!(body.contains(&format!("[vibecrafted-bus nonce={expected}]")));
    assert!(body.contains("msg-derived"));
    assert_eq!(store.marks.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn empty_meta_nonce_opts_the_run_out() {
    let home = TempHome::new("optout");
    write_run(&home.path, Some(""));
    let store = MemStore::new(vec![envelope("msg-optout", "maciej", "stay")]);
    let app = router(&home.path, Arc::clone(&store));
    let (status, body) = post(&app, ping_body(), "application/json", Some(RUN)).await;
    assert_eq!(status, StatusCode::OK);
    assert!(!body.contains("vibecrafted-bus"));
    assert_eq!(store.receives.load(Ordering::SeqCst), 0);
    assert_eq!(store.pending(), 1);
}

#[tokio::test]
async fn unchanged_message_dir_skips_the_store_after_an_empty_receive() {
    let home = TempHome::new("quiet");
    write_run(&home.path, None);
    let messages = home.path.join("control_plane/messages");
    fs::create_dir_all(&messages).expect("messages");
    let store = MemStore::new(vec![]);
    let app = router(&home.path, Arc::clone(&store));
    for _ in 0..2 {
        let (status, body) = post(&app, ping_body(), "application/json", Some(RUN)).await;
        assert_eq!(status, StatusCode::OK);
        assert!(!body.contains("vibecrafted-bus"));
    }
    // The CLI store forks a process per receive; an unchanged directory
    // must not pay that price twice.
    assert_eq!(store.receives.load(Ordering::SeqCst), 1);
    fs::write(messages.join("m1.json"), b"{}").expect("new message");
    let (status, _) = post(&app, ping_body(), "application/json", Some(RUN)).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(store.receives.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn unknown_run_or_no_assignment_does_not_query_the_store() {
    let home = TempHome::new("skip");
    // No run directory at all: a bare header must not mint a nonce.
    let store = MemStore::new(vec![envelope("msg-skip", "maciej", "stay")]);
    let app = router(&home.path, Arc::clone(&store));
    let (status, unknown_run) = post(&app, ping_body(), "application/json", Some(RUN)).await;
    assert_eq!(status, StatusCode::OK);
    assert!(!unknown_run.contains("vibecrafted-bus"));
    let (status, unassigned) = post(&app, ping_body(), "application/json", None).await;
    assert_eq!(status, StatusCode::OK);
    assert!(!unassigned.contains("vibecrafted-bus"));
    write_run(&home.path, None);
    let (status, listed) = post(
        &app,
        json!({"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}),
        "application/json",
        Some(RUN),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert!(!listed.contains("vibecrafted-bus"));
    assert_eq!(store.receives.load(Ordering::SeqCst), 0);
    assert_eq!(store.pending(), 1);
}
