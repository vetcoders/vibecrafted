//! HTTP contract for `POST /mcp` and `GET /mcp`.
//!
//! Run with:
//! ```text
//! cargo test -p vibecrafted-server-web --features ssr --test mcp_http
//! ```
//!
//! Each test passes its own control-plane home and bearer into the router.
//! Nothing here publishes `VC_SERVER_MCP_BEARER` or reads the operator config.

#![cfg(feature = "ssr")]

use std::fs;
use std::io::ErrorKind;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode, header};
use futures_util::StreamExt;
use leptos::config::{Env, LeptosOptions};
use serde_json::{Value, json};
use tower::ServiceExt;
use vibecrafted_server_web::mcp::api::{load_mcp_bearer, mcp_routes_with};

const TOKEN: &str = "test-bearer-token";

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
                    "vc-mcp-{label}-{}-{nanos}-{nonce}-{attempt}",
                    std::process::id()
                ));
                match fs::create_dir(&candidate) {
                    Ok(()) => Some(candidate),
                    Err(error) if error.kind() == ErrorKind::AlreadyExists => None,
                    Err(error) => panic!("create isolated mcp home: {error}"),
                }
            })
            .expect("allocate an isolated mcp home");
        let runs = path.join("control_plane/runs");
        fs::create_dir_all(&runs).expect("runs dir");
        fs::write(
            runs.join("fixture-run.json"),
            serde_json::to_vec(&json!({
                "run_id": "fixture-run",
                "state": "completed",
                "agent": "codex",
                "skill": "implement",
                "mode": "implement",
                "root": path.join("repo").to_string_lossy(),
                "operator_session": "repo-fixture-run",
                "latest_report": "",
                "latest_transcript": "",
                "last_error": "",
                "updated_at": "2026-09-08T12:00:00+00:00",
                "started_at": "2026-09-08T11:59:00+00:00",
                "health": "final",
                "source": "agent-meta",
                "lock_present": false
            }))
            .expect("snapshot"),
        )
        .expect("snapshot file");
        Self { path }
    }
}

impl Drop for TempHome {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

fn router(home: &Path, token: &str) -> axum::Router {
    let opts = LeptosOptions::builder()
        .output_name("vibecrafted-server-web-test")
        .site_root("target/site-test")
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr("127.0.0.1:0".parse::<SocketAddr>().expect("addr"))
        .reload_port(0)
        .build();
    mcp_routes_with(home, token, Duration::from_millis(200)).with_state(opts)
}

async fn post(
    app: &axum::Router,
    token: Option<&str>,
    accept: &str,
    origin: Option<&str>,
    body: &str,
) -> (StatusCode, axum::http::HeaderMap, Vec<u8>) {
    let mut builder = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header(header::HOST, "127.0.0.1:3024")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::ACCEPT, accept);
    if let Some(origin) = origin {
        builder = builder.header(header::ORIGIN, origin);
    }
    if let Some(token) = token {
        builder = builder.header(header::AUTHORIZATION, format!("Bearer {token}"));
    }
    let request = builder.body(Body::from(body.to_string())).expect("request");
    let response = app.clone().oneshot(request).await.expect("response");
    let status = response.status();
    let headers = response.headers().clone();
    let body = to_bytes(response.into_body(), 1024 * 1024)
        .await
        .expect("body")
        .to_vec();
    (status, headers, body)
}

fn json_body(bytes: &[u8]) -> Value {
    serde_json::from_slice(bytes).expect("json body")
}

fn tool_named<'a>(payload: &'a Value, name: &str) -> &'a Value {
    payload["result"]["tools"]
        .as_array()
        .expect("tools")
        .iter()
        .find(|tool| tool["name"] == name)
        .unwrap_or_else(|| panic!("missing tool {name}"))
}

#[tokio::test]
async fn post_initialize_returns_capabilities_and_server_info() {
    let home = TempHome::new("initialize");
    let app = router(&home.path, TOKEN);
    let (status, _, body) = post(
        &app,
        Some(TOKEN),
        "application/json, text/event-stream",
        Some("http://127.0.0.1:3024"),
        &json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "probe", "version": "0"}
            }
        })
        .to_string(),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    let payload = json_body(&body);
    assert_eq!(payload["jsonrpc"], "2.0");
    assert_eq!(payload["id"], 1);
    assert_eq!(payload["result"]["protocolVersion"], "2025-03-26");
    assert_eq!(
        payload["result"]["capabilities"]["tools"]["listChanged"],
        false
    );
    assert_eq!(payload["result"]["serverInfo"]["name"], "vc-server");
    assert_eq!(
        payload["result"]["serverInfo"]["version"],
        env!("VC_SERVER_VERSION")
    );

    let (status, _, body) = post(
        &app,
        Some(TOKEN),
        "application/json",
        None,
        &json!({
            "jsonrpc": "2.0",
            "id": "neg",
            "method": "initialize",
            "params": {"protocolVersion": "1999-01-01"}
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let payload = json_body(&body);
    assert_eq!(payload["id"], "neg");
    assert_eq!(payload["result"]["protocolVersion"], "2025-03-26");
}

#[tokio::test]
async fn tools_list_returns_pilot_tool_schemas() {
    let home = TempHome::new("list");
    let app = router(&home.path, TOKEN);
    let (status, _, body) = post(
        &app,
        Some(TOKEN),
        "application/json, text/event-stream",
        Some("http://localhost:3024"),
        &json!({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        })
        .to_string(),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    let payload = json_body(&body);
    let tools = payload["result"]["tools"].as_array().expect("tools");
    assert_eq!(tools.len(), 2);

    let ping = tool_named(&payload, "vc_ping");
    assert_eq!(
        ping["description"],
        "Return the vc-server version and the current UTC time."
    );
    assert_eq!(
        ping["inputSchema"],
        json!({
            "type": "object",
            "properties": {},
            "additionalProperties": false,
        })
    );

    let status_tool = tool_named(&payload, "vc_run_status");
    assert_eq!(
        status_tool["description"],
        "Read one control-plane run by id."
    );
    assert_eq!(
        status_tool["inputSchema"],
        json!({
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "Control-plane run id.",
                },
            },
            "required": ["run_id"],
            "additionalProperties": false,
        })
    );
}

#[tokio::test]
async fn tools_call_ping_returns_version_and_run_status_reads_fixture() {
    let home = TempHome::new("call");
    let app = router(&home.path, TOKEN);

    let (status, _, body) = post(
        &app,
        Some(TOKEN),
        "application/json",
        None,
        &json!({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "vc_ping", "arguments": {}}
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let payload = json_body(&body);
    assert_eq!(payload["result"]["isError"], false);
    assert_eq!(
        payload["result"]["structuredContent"]["version"],
        env!("VC_SERVER_VERSION")
    );
    let time = payload["result"]["structuredContent"]["time"]
        .as_str()
        .expect("time");
    assert!(time.contains('T'), "utc timestamp, got {time}");
    let text = payload["result"]["content"][0]["text"]
        .as_str()
        .expect("text");
    assert!(text.contains(env!("VC_SERVER_VERSION")));

    let (status, _, body) = post(
        &app,
        Some(TOKEN),
        "application/json",
        None,
        &json!({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "vc_run_status",
                "arguments": {"run_id": "fixture-run"}
            }
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let payload = json_body(&body);
    assert_eq!(payload["result"]["isError"], false);
    assert_eq!(
        payload["result"]["structuredContent"]["run_id"],
        "fixture-run"
    );
    assert_eq!(payload["result"]["structuredContent"]["state"], "completed");
    assert_eq!(payload["result"]["structuredContent"]["health"], "final");
    assert_eq!(payload["result"]["structuredContent"]["agent"], "codex");
    assert_eq!(payload["result"]["structuredContent"]["skill"], "implement");
    let text = payload["result"]["content"][0]["text"]
        .as_str()
        .expect("text");
    assert!(text.contains("state=completed"));

    let (status, _, body) = post(
        &app,
        Some(TOKEN),
        "application/json",
        None,
        &json!({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "vc_run_status",
                "arguments": {"run_id": "../secret"}
            }
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let payload = json_body(&body);
    assert_eq!(payload["result"]["isError"], true);
    let text = payload["result"]["content"][0]["text"]
        .as_str()
        .expect("text");
    assert!(text.contains("not found"));
    assert!(!text.contains("control_plane"));
}

#[tokio::test]
async fn bearer_missing_or_wrong_is_401_and_valid_bearer_passes() {
    let home = TempHome::new("auth");
    let app = router(&home.path, TOKEN);
    let body = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list"
    })
    .to_string();

    let (status, headers, _) = post(&app, None, "application/json", None, &body).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    let challenge = headers
        .get(header::WWW_AUTHENTICATE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("");
    assert!(challenge.starts_with("Bearer "));

    let (status, _, _) = post(&app, Some("other-token"), "application/json", None, &body).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);

    let request = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::ACCEPT, "application/json")
        .header(header::AUTHORIZATION, format!("bearer {TOKEN}"))
        .body(Body::from(body))
        .expect("request");
    let response = app.clone().oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);

    let (status, _, _) = post(
        &app,
        Some(TOKEN),
        "application/json",
        Some("https://evil.example"),
        &json!({"jsonrpc":"2.0","id":1,"method":"tools/list"}).to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);

    let request = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::AUTHORIZATION, format!("Bearer {TOKEN}"))
        .header("mcp-protocol-version", "1999-01-01")
        .body(Body::from(
            json!({"jsonrpc":"2.0","id":1,"method":"tools/list"}).to_string(),
        ))
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn get_mcp_streams_a_server_notification() {
    let home = TempHome::new("sse");
    let app = router(&home.path, TOKEN);
    let request = Request::builder()
        .method("GET")
        .uri("/mcp")
        .header(header::ACCEPT, "text/event-stream")
        .header(header::AUTHORIZATION, format!("Bearer {TOKEN}"))
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let content_type = response
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("");
    assert!(
        content_type.starts_with("text/event-stream"),
        "{content_type}"
    );

    let mut collected = String::new();
    let mut stream = response.into_body().into_data_stream();
    let deadline = tokio::time::Instant::now() + Duration::from_secs(2);
    while !collected.contains("data:") {
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            break;
        }
        match tokio::time::timeout(remaining, stream.next()).await {
            Ok(Some(Ok(chunk))) => collected.push_str(&String::from_utf8_lossy(&chunk)),
            Ok(Some(Err(error))) => panic!("sse stream: {error}"),
            Ok(None) | Err(_) => break,
        }
    }
    let data = collected
        .lines()
        .find_map(|line| line.strip_prefix("data:"))
        .expect("sse data")
        .trim();
    let payload: Value = serde_json::from_str(data).expect("sse json");
    assert_eq!(payload["method"], "notifications/message");
    assert_eq!(payload["params"]["data"], "mcp stream open");
    assert!(payload.get("id").is_none());
}

#[tokio::test]
async fn post_event_stream_accept_wraps_the_result() {
    let home = TempHome::new("post-sse");
    let app = router(&home.path, TOKEN);
    let (status, headers, body) = post(
        &app,
        Some(TOKEN),
        "text/event-stream",
        None,
        &json!({
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/list"
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let content_type = headers
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("");
    assert!(
        content_type.starts_with("text/event-stream"),
        "{content_type}"
    );
    let text = String::from_utf8(body).expect("utf8");
    assert!(text.contains("event: message") || text.contains("event:message"));
    let data = text
        .lines()
        .find_map(|line| line.strip_prefix("data:"))
        .expect("data")
        .trim();
    let payload: Value = serde_json::from_str(data).expect("json");
    assert_eq!(payload["id"], 9);
    assert!(payload["result"]["tools"].is_array());
}

#[tokio::test]
async fn notification_initialized_is_accepted() {
    let home = TempHome::new("notify");
    let app = router(&home.path, TOKEN);
    let (status, _, body) = post(
        &app,
        Some(TOKEN),
        "application/json",
        None,
        &json!({
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        })
        .to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED);
    assert!(body.is_empty());
}

#[tokio::test]
async fn mcp_bearer_comes_from_env_or_mcp_table_not_server_table() {
    let home = TempHome::new("config");
    let config = home.path.join("config.toml");
    fs::write(
        &config,
        "# comment\n[server]\nbind_host = \"127.0.0.1\"\nport = 3024\n\n[mcp]\n# bearer = \"nope\"\nbearer = \"abc#def\" # trailing\n",
    )
    .expect("config");

    assert_eq!(load_mcp_bearer(None, Some(&config)), "abc#def");
    assert_eq!(load_mcp_bearer(Some(""), Some(&config)), "abc#def");
    assert_eq!(
        load_mcp_bearer(Some("  from-env  "), Some(&config)),
        "from-env"
    );
    assert_eq!(
        load_mcp_bearer(Some("bad token"), Some(&config)),
        "",
        "a set but unusable env override fail-closes"
    );

    let server_only = home.path.join("server-only.toml");
    fs::write(
        &server_only,
        "[server]\nmcp_bearer = \"hidden-in-server-table\"\n",
    )
    .expect("server only");
    assert_eq!(load_mcp_bearer(None, Some(&server_only)), "");

    let missing = home.path.join("missing.toml");
    assert_eq!(load_mcp_bearer(None, Some(&missing)), "");

    std::os::unix::fs::symlink(&config, home.path.join("link.toml")).expect("symlink");
    assert_eq!(
        load_mcp_bearer(None, Some(&home.path.join("link.toml"))),
        "",
        "symlink config is not a bearer source"
    );
}
