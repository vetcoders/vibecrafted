//! Aggregator proof: prefixed tool lists, routed calls, timeout degradation,
//! and PID→run assignment from `runtime_runs/<id>/meta.json`.
//!
//! ```text
//! cargo test -p vibecrafted-server-web --features ssr --test mcp_aggregator
//! ```

#![cfg(feature = "ssr")]

use std::collections::HashMap;
use std::fs;
use std::io::ErrorKind;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode, header};
use leptos::config::{Env, LeptosOptions};
use serde_json::{Value, json};
use tower::ServiceExt;
use vibecrafted_server_web::mcp::api::mcp_routes_with_aggregator;
use vibecrafted_server_web::mcp_aggregator::{
    ANCESTOR_HEADER, Aggregator, HttpTransport, MapStarts, McpUpstreamConfig, PID_HEADER,
    RUN_HEADER, ScriptedTransport, UpstreamExchange, UpstreamFault, UpstreamSpec,
    UpstreamTransport, assign_from_claims, load_worker_claims, parse_mcp_upstreams,
    parse_started_unix,
};

const TOKEN: &str = "aggregator-bearer";

struct TempHome {
    path: PathBuf,
}

impl TempHome {
    fn new(label: &str) -> Self {
        static NEXT_ID: AtomicU64 = AtomicU64::new(0);
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        let path = (0..100)
            .find_map(|attempt| {
                let nonce = NEXT_ID.fetch_add(1, Ordering::Relaxed);
                let candidate = std::env::temp_dir().join(format!(
                    "vc-agg-{label}-{}-{nanos}-{nonce}-{attempt}",
                    std::process::id()
                ));
                match fs::create_dir(&candidate) {
                    Ok(()) => Some(candidate),
                    Err(error) if error.kind() == ErrorKind::AlreadyExists => None,
                    Err(error) => panic!("create isolated home: {error}"),
                }
            })
            .expect("allocate home");
        fs::create_dir_all(path.join("control_plane/runtime_runs")).expect("runtime_runs");
        Self { path }
    }
}

impl Drop for TempHome {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

struct Mock {
    faults: HashSetNames,
    calls: Vec<(String, String)>,
}

struct HashSetNames(Vec<String>);

impl HashSetNames {
    fn contains(&self, name: &str) -> bool {
        self.0.iter().any(|item| item == name)
    }
}

fn scripted(mock: Arc<Mutex<Mock>>) -> Arc<dyn UpstreamTransport> {
    Arc::new(ScriptedTransport {
        handler: move |upstream: &UpstreamSpec, _session: Option<&str>, message: &Value| {
            let method = message
                .get("method")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let mut guard = mock.lock().unwrap_or_else(|err| err.into_inner());
            guard.calls.push((upstream.name.clone(), method.clone()));
            let fault = guard.faults.contains(&upstream.name);
            drop(guard);
            if fault && method != "initialize" && method != "notifications/initialized" {
                return Err(UpstreamFault::Timeout);
            }
            match method.as_str() {
                "initialize" => Ok(UpstreamExchange {
                    body: json!({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {"protocolVersion": "2025-03-26", "capabilities": {}}
                    }),
                    session_id: Some(format!("sess-{}", upstream.name)),
                }),
                "notifications/initialized" => Ok(UpstreamExchange {
                    body: Value::Null,
                    session_id: None,
                }),
                "tools/list" => {
                    let tools = match upstream.name.as_str() {
                        "loctree" => json!([{
                            "name": "context",
                            "description": "map",
                            "inputSchema": {"type": "object"}
                        }]),
                        "aicx" => json!([{
                            "name": "search",
                            "description": "memory",
                            "inputSchema": {"type": "object"}
                        }]),
                        _ => json!([]),
                    };
                    Ok(UpstreamExchange {
                        body: json!({"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}}),
                        session_id: None,
                    })
                }
                "tools/call" => {
                    let tool = message
                        .pointer("/params/name")
                        .and_then(Value::as_str)
                        .unwrap_or("");
                    Ok(UpstreamExchange {
                        body: json!({
                            "jsonrpc": "2.0",
                            "id": 1,
                            "result": {
                                "content": [{
                                    "type": "text",
                                    "text": format!("{}:{tool}", upstream.name)
                                }],
                                "isError": false
                            }
                        }),
                        session_id: None,
                    })
                }
                _ => Err(UpstreamFault::BadResponse),
            }
        },
    })
}

fn upstreams() -> Vec<UpstreamSpec> {
    vec![
        UpstreamSpec {
            name: "loctree".to_string(),
            url: "http://127.0.0.1:9/loctree".to_string(),
            timeout: Duration::from_millis(200),
        },
        UpstreamSpec {
            name: "aicx".to_string(),
            url: "http://127.0.0.1:9/aicx".to_string(),
            timeout: Duration::from_millis(200),
        },
    ]
}

fn aggregator(
    mock: Arc<Mutex<Mock>>,
    cache_ttl: Duration,
    negative_ttl: Duration,
) -> Arc<Aggregator> {
    Arc::new(Aggregator::new(
        upstreams(),
        cache_ttl,
        negative_ttl,
        scripted(mock),
        Arc::new(MapStarts::new(HashMap::new())),
    ))
}

fn router(home: &Path, aggregator: Arc<Aggregator>) -> axum::Router {
    let opts = LeptosOptions::builder()
        .output_name("vibecrafted-server-web-test")
        .site_root("target/site-test")
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr("127.0.0.1:0".parse::<SocketAddr>().expect("addr"))
        .reload_port(0)
        .build();
    mcp_routes_with_aggregator(home, TOKEN, Duration::from_millis(200), aggregator).with_state(opts)
}

async fn post(
    app: &axum::Router,
    body: Value,
    extra: &[(&str, &str)],
) -> (StatusCode, axum::http::HeaderMap, Value) {
    let mut builder = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header(header::HOST, "127.0.0.1:3024")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::ACCEPT, "application/json")
        .header(header::AUTHORIZATION, format!("Bearer {TOKEN}"));
    for (name, value) in extra {
        builder = builder.header(*name, *value);
    }
    let request = builder.body(Body::from(body.to_string())).expect("request");
    let response = app.clone().oneshot(request).await.expect("response");
    let status = response.status();
    let headers = response.headers().clone();
    let bytes = to_bytes(response.into_body(), 64 * 1024)
        .await
        .expect("body");
    let payload = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    (status, headers, payload)
}

fn tool_names(payload: &Value) -> Vec<String> {
    payload["result"]["tools"]
        .as_array()
        .expect("tools")
        .iter()
        .filter_map(|tool| tool["name"].as_str().map(str::to_string))
        .collect()
}

fn write_meta(home: &Path, run_id: &str, worker_pid: i64, started_at: &str) {
    let dir = home.join("control_plane/runtime_runs").join(run_id);
    fs::create_dir_all(&dir).expect("run dir");
    fs::write(
        dir.join("meta.json"),
        serde_json::to_vec(&json!({
            "run_id": run_id,
            "worker_pid": worker_pid,
            "started_at": started_at,
        }))
        .expect("meta"),
    )
    .expect("write meta");
}

#[tokio::test]
async fn tools_list_prefixes_both_mock_upstreams() {
    let home = TempHome::new("list");
    let mock = Arc::new(Mutex::new(Mock {
        faults: HashSetNames(Vec::new()),
        calls: Vec::new(),
    }));
    let app = router(
        &home.path,
        aggregator(
            Arc::clone(&mock),
            Duration::from_secs(30),
            Duration::from_secs(5),
        ),
    );
    let (status, _, payload) = post(
        &app,
        json!({"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        tool_names(&payload),
        vec![
            "vc_ping".to_string(),
            "vc_run_status".to_string(),
            "loctree_context".to_string(),
            "aicx_search".to_string(),
        ]
    );
    let calls = mock
        .lock()
        .unwrap_or_else(|err| err.into_inner())
        .calls
        .clone();
    assert!(
        calls
            .iter()
            .any(|(name, method)| name == "loctree" && method == "tools/list")
    );
    assert!(
        calls
            .iter()
            .any(|(name, method)| name == "aicx" && method == "tools/list")
    );
}

#[tokio::test]
async fn tools_call_reaches_the_prefixed_upstream() {
    let home = TempHome::new("call");
    let mock = Arc::new(Mutex::new(Mock {
        faults: HashSetNames(Vec::new()),
        calls: Vec::new(),
    }));
    let app = router(
        &home.path,
        aggregator(mock, Duration::from_secs(30), Duration::from_secs(5)),
    );
    let (status, _, payload) = post(
        &app,
        json!({
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "loctree_context", "arguments": {"scope": "web"}}
        }),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(payload["id"], 7);
    assert_eq!(payload["result"]["content"][0]["text"], "loctree:context");
    assert_eq!(payload["result"]["isError"], false);

    let (status, _, aicx) = post(
        &app,
        json!({
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "aicx_search", "arguments": {}}
        }),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(aicx["result"]["content"][0]["text"], "aicx:search");
}

#[tokio::test]
async fn timed_out_upstream_drops_from_the_list_and_call_is_typed() {
    let home = TempHome::new("down");
    let mock = Arc::new(Mutex::new(Mock {
        faults: HashSetNames(vec!["aicx".to_string()]),
        calls: Vec::new(),
    }));
    let app = router(
        &home.path,
        aggregator(
            Arc::clone(&mock),
            Duration::from_secs(30),
            Duration::from_secs(30),
        ),
    );
    let (status, _, payload) = post(
        &app,
        json!({"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let names = tool_names(&payload);
    assert!(names.contains(&"loctree_context".to_string()));
    assert!(names.contains(&"vc_ping".to_string()));
    assert!(!names.iter().any(|name| name.starts_with("aicx_")));

    let (status, _, failed) = post(
        &app,
        json!({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "aicx_search", "arguments": {}}
        }),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(failed["error"]["code"], -32002);
    assert_eq!(failed["error"]["message"], "upstream unavailable");
    assert_eq!(failed["error"]["data"]["upstream"], "aicx");
    assert_eq!(failed["error"]["data"]["reason"], "timeout");
    assert_eq!(failed["error"]["data"]["tool"], "aicx_search");

    let (status, _, ping) = post(
        &app,
        json!({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "vc_ping", "arguments": {}}
        }),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(ping["result"]["isError"], false);
    assert!(ping["result"]["structuredContent"]["version"].is_string());

    let (status, _, loctree) = post(
        &app,
        json!({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "loctree_context", "arguments": {}}
        }),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(loctree["result"]["content"][0]["text"], "loctree:context");
}

#[tokio::test]
async fn cached_list_does_not_refetch_until_ttl() {
    let home = TempHome::new("cache");
    let mock = Arc::new(Mutex::new(Mock {
        faults: HashSetNames(Vec::new()),
        calls: Vec::new(),
    }));
    let app = router(
        &home.path,
        aggregator(
            Arc::clone(&mock),
            Duration::from_secs(30),
            Duration::from_secs(5),
        ),
    );
    let body = json!({"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}});
    let _ = post(&app, body.clone(), &[]).await;
    let _ = post(&app, body, &[]).await;
    let calls = mock
        .lock()
        .unwrap_or_else(|err| err.into_inner())
        .calls
        .clone();
    let lists = calls
        .iter()
        .filter(|(name, method)| name == "loctree" && method == "tools/list")
        .count();
    assert_eq!(
        lists, 1,
        "second tools/list must hit the TTL cache: {calls:?}"
    );
}

#[tokio::test]
async fn pid_header_maps_ancestor_to_worker_and_unknown_is_passthrough() {
    let home = TempHome::new("pid");
    write_meta(&home.path, "run-alpha", 4242, "2026-09-26T12:00:00+00:00");
    let app = router(&home.path, Aggregator::local_only());
    let body = json!({"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}});

    let (status, headers, _) = post(
        &app,
        body.clone(),
        &[
            (PID_HEADER.as_str(), "9001"),
            (ANCESTOR_HEADER.as_str(), "4242,1"),
        ],
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        headers
            .get(RUN_HEADER)
            .and_then(|value| value.to_str().ok()),
        Some("run-alpha")
    );

    let (status, headers, _) = post(&app, body.clone(), &[(PID_HEADER.as_str(), "111")]).await;
    assert_eq!(status, StatusCode::OK);
    assert!(headers.get(RUN_HEADER).is_none());

    let (status, headers, _) = post(
        &app,
        body,
        &[
            (RUN_HEADER.as_str(), "missing-run"),
            (PID_HEADER.as_str(), "4242"),
        ],
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        headers.get(RUN_HEADER).is_none(),
        "a present but unknown run id must not fall through to a guessed PID"
    );
}

#[tokio::test]
async fn run_id_header_is_the_first_source() {
    let home = TempHome::new("env");
    write_meta(&home.path, "run-env", 50, "2026-09-26T12:00:00+00:00");
    write_meta(&home.path, "run-pid", 77, "2026-09-26T12:00:00+00:00");
    let app = router(&home.path, Aggregator::local_only());
    let (_, headers, _) = post(
        &app,
        json!({"jsonrpc":"2.0","id":1,"method":"ping"}),
        &[
            (RUN_HEADER.as_str(), "run-env"),
            (PID_HEADER.as_str(), "77"),
        ],
    )
    .await;
    assert_eq!(
        headers
            .get(RUN_HEADER)
            .and_then(|value| value.to_str().ok()),
        Some("run-env")
    );
}

#[test]
fn shared_pid_uses_process_start_and_refuses_a_tie() {
    let newer = "2026-09-26T12:00:10+00:00";
    let older = "2026-09-26T11:58:00+00:00";
    let tie_a = "2026-09-26T12:00:10+00:00";
    let tie_b = "2026-09-26T12:00:11+00:00";
    let process = parse_started_unix(newer).expect("stamp");
    let claims = vec![
        vibecrafted_server_web::mcp_aggregator::WorkerClaim {
            run_id: "older".to_string(),
            worker_pid: Some(42),
            started_unix: parse_started_unix(older),
        },
        vibecrafted_server_web::mcp_aggregator::WorkerClaim {
            run_id: "newer".to_string(),
            worker_pid: Some(42),
            started_unix: parse_started_unix(newer),
        },
    ];
    let assigned = assign_from_claims(None, &[42], &claims, &|_| Some(process));
    assert_eq!(assigned.as_deref(), Some("newer"));

    let tied = vec![
        vibecrafted_server_web::mcp_aggregator::WorkerClaim {
            run_id: "a".to_string(),
            worker_pid: Some(42),
            started_unix: parse_started_unix(tie_a),
        },
        vibecrafted_server_web::mcp_aggregator::WorkerClaim {
            run_id: "b".to_string(),
            worker_pid: Some(42),
            started_unix: parse_started_unix(tie_b),
        },
    ];
    assert_eq!(
        assign_from_claims(None, &[42], &tied, &|_| Some(process)),
        None
    );
    assert_eq!(
        assign_from_claims(None, &[42], &claims, &|_| None),
        None,
        "two runs and no process start is passthrough"
    );
}

#[test]
fn stale_unique_pid_is_rejected_and_the_ancestor_can_still_match() {
    let live = parse_started_unix("2026-09-26T12:00:00+00:00").expect("live");
    let stale = parse_started_unix("2026-09-25T12:00:00+00:00").expect("stale");
    let claims = vec![
        vibecrafted_server_web::mcp_aggregator::WorkerClaim {
            run_id: "stale-run".to_string(),
            worker_pid: Some(9),
            started_unix: Some(stale),
        },
        vibecrafted_server_web::mcp_aggregator::WorkerClaim {
            run_id: "live-run".to_string(),
            worker_pid: Some(8),
            started_unix: Some(live),
        },
    ];
    let assigned = assign_from_claims(None, &[9, 8], &claims, &|pid| match pid {
        9 => Some(live),
        8 => Some(live),
        _ => None,
    });
    assert_eq!(assigned.as_deref(), Some("live-run"));
}

#[test]
fn load_worker_claims_reads_top_level_worker_pid() {
    let home = TempHome::new("load");
    write_meta(&home.path, "run-disk", 5150, "2026-09-26T12:00:00+00:00");
    let plane = control_core::ControlPlane::new(&home.path);
    let claims = load_worker_claims(&plane);
    assert_eq!(claims.len(), 1);
    assert_eq!(claims[0].run_id, "run-disk");
    assert_eq!(claims[0].worker_pid, Some(5150));
    assert!(claims[0].started_unix.is_some());
}

#[test]
fn config_table_parses_upstreams_without_a_new_file_format() {
    let text = r#"
[server]
port = 3024

[mcp]
bearer = "abc"
tools_cache_ttl_ms = 1500

[mcp.upstream.loctree]
url = "http://127.0.0.1:5174/mcp"
timeout_ms = 250

[mcp.upstream.aicx]
url = "http://127.0.0.1:8044/mcp"
"#;
    let parsed = parse_mcp_upstreams(text);
    assert!(parsed.seen_upstream_table);
    assert_eq!(parsed.cache_ttl, Some(Duration::from_millis(1500)));
    assert_eq!(parsed.upstreams.len(), 2);
    assert_eq!(parsed.upstreams[0].name, "aicx");
    assert_eq!(parsed.upstreams[0].timeout, Duration::from_millis(800));
    assert_eq!(parsed.upstreams[1].name, "loctree");
    assert_eq!(parsed.upstreams[1].timeout, Duration::from_millis(250));
    let absent = parse_mcp_upstreams("[mcp]\nbearer = \"abc\"\n");
    assert!(!absent.seen_upstream_table);
    let _ = McpUpstreamConfig {
        seen_upstream_table: false,
        upstreams: Vec::new(),
        cache_ttl: None,
    };
}

#[tokio::test]
async fn cached_round_trip_stays_in_the_same_band_as_a_direct_mock() {
    let mock = Arc::new(Mutex::new(Mock {
        faults: HashSetNames(Vec::new()),
        calls: Vec::new(),
    }));
    let warm = aggregator(
        Arc::clone(&mock),
        Duration::from_secs(60),
        Duration::from_secs(5),
    );
    let _ = warm.list_remote().await;
    let before = mock
        .lock()
        .unwrap_or_else(|err| err.into_inner())
        .calls
        .len();
    let mut cached = Vec::with_capacity(200);
    for _ in 0..200 {
        let started = Instant::now();
        let tools = warm.list_remote().await;
        cached.push(started.elapsed().as_micros());
        assert_eq!(tools.len(), 2);
    }
    let after = mock
        .lock()
        .unwrap_or_else(|err| err.into_inner())
        .calls
        .len();
    assert_eq!(before, after, "cached list must not touch the upstream");

    let direct_upstream = upstreams().into_iter().next().expect("loctree");
    let direct_transport = scripted(Arc::clone(&mock));
    let mut direct = Vec::with_capacity(200);
    for _ in 0..200 {
        let started = Instant::now();
        let exchange = direct_transport
            .exchange(
                &direct_upstream,
                None,
                json!({"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}),
            )
            .await
            .expect("direct");
        assert!(exchange.body.pointer("/result/tools").is_some());
        direct.push(started.elapsed().as_micros());
    }
    let cold = aggregator(Arc::clone(&mock), Duration::ZERO, Duration::ZERO);
    let _ = cold.list_remote().await;
    let mut uncached = Vec::with_capacity(200);
    for _ in 0..200 {
        let started = Instant::now();
        let tools = cold.list_remote().await;
        uncached.push(started.elapsed().as_micros());
        assert_eq!(tools.len(), 2);
    }
    let cached_p50 = percentile(&mut cached.clone(), 0.50);
    let cached_p99 = percentile(&mut cached, 0.99);
    let direct_p50 = percentile(&mut direct.clone(), 0.50);
    let direct_p99 = percentile(&mut direct, 0.99);
    let uncached_p50 = percentile(&mut uncached.clone(), 0.50);
    let uncached_p99 = percentile(&mut uncached, 0.99);
    let overhead_p50 = uncached_p50 as i128 - direct_p50 as i128;
    let overhead_p99 = uncached_p99 as i128 - direct_p99 as i128;
    println!(
        "AGGREGATOR_TIMING cached_p50_us={cached_p50} cached_p99_us={cached_p99} direct_p50_us={direct_p50} direct_p99_us={direct_p99} uncached_p50_us={uncached_p50} uncached_p99_us={uncached_p99} overhead_p50_us={overhead_p50} overhead_p99_us={overhead_p99}"
    );
    assert!(
        cached_p99 < 20_000,
        "cached aggregator p99 {cached_p99}us exceeded 20ms"
    );
}

fn percentile(samples: &mut [u128], p: f64) -> u128 {
    assert!(!samples.is_empty());
    samples.sort_unstable();
    let index = ((samples.len() - 1) as f64 * p).round() as usize;
    samples[index]
}

#[tokio::test]
async fn http_transport_maps_a_silent_peer_to_timeout() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().expect("addr").port();
    thread_accept_and_hold(listener);
    let transport = HttpTransport::new();
    let upstream = UpstreamSpec {
        name: "slow".to_string(),
        url: format!("http://127.0.0.1:{port}/mcp"),
        timeout: Duration::from_millis(200),
    };
    let started = Instant::now();
    let err = transport
        .exchange(
            &upstream,
            None,
            json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}),
        )
        .await
        .expect_err("timeout");
    assert_eq!(
        err,
        UpstreamFault::Timeout,
        "elapsed {:?}",
        started.elapsed()
    );
    assert!(
        started.elapsed() < Duration::from_secs(2),
        "hung for {:?}",
        started.elapsed()
    );
}

fn thread_accept_and_hold(listener: std::net::TcpListener) {
    std::thread::spawn(move || {
        if let Ok((stream, _)) = listener.accept() {
            std::thread::sleep(Duration::from_secs(3));
            drop(stream);
        }
    });
}
