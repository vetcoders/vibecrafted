//! Streamable HTTP MCP on `POST /mcp` and `GET /mcp`.
//!
//! JSON-RPC is implemented here, not via the `rmcp` crate. `rmcp` 2.1 is
//! already pinned by `rmcp-mux`, a different process with its own listener.
//! This server already owns the axum router; the pilot surface is
//! `initialize`, `tools/list`, and `tools/call`.
//!
//! The bearer is one server token (`VC_SERVER_MCP_BEARER`, else `[mcp].bearer`
//! in the operator config). It is not a `[server]` key: Python
//! `load_server_config` rejects unknown keys in that table. Upstream tools
//! and PID→run binding live in `mcp_aggregator` (W2-01).

#[cfg(feature = "ssr")]
pub mod api {
    use std::convert::Infallible;
    use std::path::{Path, PathBuf};
    use std::sync::Arc;
    use std::time::Duration;

    use axum::Router;
    use axum::body::{Body, to_bytes};
    use axum::extract::{Extension, Request};
    use axum::http::{HeaderMap, StatusCode, header};
    use axum::response::sse::{Event, KeepAlive, Sse};
    use axum::response::{IntoResponse, Response};
    use axum::routing::get;
    use chrono::{SecondsFormat, Utc};
    use control_core::{ControlPlane, RunStatus};
    use futures_util::StreamExt;
    use futures_util::stream;
    use serde_json::{Map, Value, json};

    use crate::mcp_aggregator::{self, Aggregator, RemoteCall};

    const DEFAULT_PROTOCOL: &str = "2025-03-26";
    const SUPPORTED_PROTOCOLS: [&str; 2] = ["2025-03-26", "2025-06-18"];
    const DEFAULT_SSE_KEEPALIVE: Duration = Duration::from_secs(15);
    const MAX_BODY: usize = 64 * 1024;
    const MAX_BATCH: usize = 16;
    const CONFIG_READ_LIMIT: u64 = 256 * 1024;
    /// Process env that overrides `[mcp].bearer`. Read once, when the router is built.
    pub const MCP_BEARER_ENV: &str = "VC_SERVER_MCP_BEARER";
    const TOOL_PING: &str = "vc_ping";
    const TOOL_RUN_STATUS: &str = "vc_run_status";

    #[derive(Clone)]
    struct McpAttach {
        token: String,
        plane: ControlPlane,
        keepalive: Duration,
        aggregator: Arc<Aggregator>,
    }

    enum Outcome {
        Accepted,
        Reply(Value),
    }

    /// Router mounted by `vc-server`. Token and keepalive are captured here.
    ///
    /// `VC_SERVER_MCP_BEARER` wins. Otherwise `[mcp].bearer` is read from the
    /// operator config file. An empty result fail-closes the endpoint (every
    /// call is 401) until a token is configured.
    pub fn mcp_routes() -> Router<leptos::config::LeptosOptions> {
        let env_token = std::env::var(MCP_BEARER_ENV).ok();
        let config_path = operator_config_path();
        let token = load_mcp_bearer(env_token.as_deref(), config_path.as_deref());
        let aggregator = Aggregator::from_config(&upstream_config(config_path.as_deref()));
        mcp_routes_with_aggregator(
            control_core::vibecrafted_home(),
            token,
            DEFAULT_SSE_KEEPALIVE,
            aggregator,
        )
    }

    /// Same router as [`mcp_routes`], with an explicit home, bearer, and SSE keepalive.
    ///
    /// Tests pass these in. They do not publish `VC_SERVER_MCP_BEARER` into
    /// the process, and they do not read the operator's real config file.
    pub fn mcp_routes_with(
        home: impl Into<PathBuf>,
        bearer: impl Into<String>,
        sse_keepalive: Duration,
    ) -> Router<leptos::config::LeptosOptions> {
        mcp_routes_with_aggregator(home, bearer, sse_keepalive, Aggregator::local_only())
    }

    /// Same router as [`mcp_routes_with`], with an explicit aggregator.
    ///
    /// Production loads upstreams from `[mcp.upstream.*]`. Tests pass a
    /// scripted transport or [`Aggregator::local_only`] so they never dial
    /// the operator's loctree/aicx ports.
    pub fn mcp_routes_with_aggregator(
        home: impl Into<PathBuf>,
        bearer: impl Into<String>,
        sse_keepalive: Duration,
        aggregator: Arc<Aggregator>,
    ) -> Router<leptos::config::LeptosOptions> {
        let keepalive = if sse_keepalive.is_zero() {
            DEFAULT_SSE_KEEPALIVE
        } else {
            sse_keepalive
        };
        let attach = McpAttach {
            token: bearer.into(),
            plane: ControlPlane::new(home),
            keepalive,
            aggregator,
        };
        Router::<leptos::config::LeptosOptions>::new()
            .route("/mcp", get(mcp_get).post(mcp_post))
            .layer(Extension(attach))
    }

    fn upstream_config(config_file: Option<&Path>) -> mcp_aggregator::McpUpstreamConfig {
        config_file
            .and_then(read_config_text)
            .map(|text| mcp_aggregator::parse_mcp_upstreams(&text))
            .unwrap_or(mcp_aggregator::McpUpstreamConfig {
                seen_upstream_table: false,
                upstreams: Vec::new(),
                cache_ttl: None,
            })
    }

    /// Resolve the server bearer. A non-empty env value wins, including when
    /// it is not a usable token: a bad explicit override fail-closes instead
    /// of silently reading the file. An empty env value falls through.
    #[must_use]
    pub fn load_mcp_bearer(env_token: Option<&str>, config_file: Option<&Path>) -> String {
        if let Some(raw) = env_token {
            let token = raw.trim();
            if !token.is_empty() {
                if acceptable_token(token) {
                    return token.to_string();
                }
                return String::new();
            }
        }
        let Some(path) = config_file else {
            return String::new();
        };
        read_config_text(path)
            .and_then(|text| bearer_from_mcp_table(&text))
            .unwrap_or_default()
    }

    fn operator_config_path() -> Option<PathBuf> {
        if let Some(xdg) = std::env::var_os("XDG_CONFIG_HOME").filter(|value| !value.is_empty()) {
            return Some(PathBuf::from(xdg).join("vibecrafted").join("config.toml"));
        }
        let home = std::env::var_os("HOME").filter(|value| !value.is_empty())?;
        Some(
            PathBuf::from(home)
                .join(".config")
                .join("vibecrafted")
                .join("config.toml"),
        )
    }

    fn read_config_text(path: &Path) -> Option<String> {
        let meta = std::fs::symlink_metadata(path).ok()?;
        if !meta.file_type().is_file() || meta.len() > CONFIG_READ_LIMIT {
            return None;
        }
        std::fs::read_to_string(path).ok()
    }

    fn bearer_from_mcp_table(text: &str) -> Option<String> {
        let mut in_mcp = false;
        let mut found = None;
        for raw in text.lines() {
            let line = strip_toml_comment(raw)
                .trim()
                .trim_start_matches('\u{feff}');
            if line.is_empty() {
                continue;
            }
            if line.starts_with('[') && line.ends_with(']') {
                in_mcp = line == "[mcp]";
                continue;
            }
            if !in_mcp {
                continue;
            }
            let Some((key, value)) = line.split_once('=') else {
                continue;
            };
            if key.trim() != "bearer" {
                continue;
            }
            found = parse_toml_string(value.trim());
        }
        found
    }

    fn strip_toml_comment(line: &str) -> &str {
        let mut in_basic = false;
        let mut in_literal = false;
        let mut escape = false;
        for (index, ch) in line.char_indices() {
            if in_basic {
                if escape {
                    escape = false;
                    continue;
                }
                if ch == '\\' {
                    escape = true;
                    continue;
                }
                if ch == '"' {
                    in_basic = false;
                }
                continue;
            }
            if in_literal {
                if ch == '\'' {
                    in_literal = false;
                }
                continue;
            }
            match ch {
                '"' => in_basic = true,
                '\'' => in_literal = true,
                '#' => return &line[..index],
                _ => {}
            }
        }
        line
    }

    fn parse_toml_string(raw: &str) -> Option<String> {
        if raw.starts_with("\"\"\"") || raw.starts_with("'''") {
            return None;
        }
        if let Some(body) = raw.strip_prefix('"') {
            let mut out = String::new();
            let mut chars = body.chars();
            while let Some(ch) = chars.next() {
                if ch == '\\' {
                    match chars.next()? {
                        '\\' => out.push('\\'),
                        '"' => out.push('"'),
                        'n' => out.push('\n'),
                        't' => out.push('\t'),
                        'r' => out.push('\r'),
                        _ => return None,
                    }
                    continue;
                }
                if ch == '"' {
                    if !chars.all(char::is_whitespace) {
                        return None;
                    }
                    return acceptable_token(&out).then_some(out);
                }
                out.push(ch);
            }
            return None;
        }
        let body = raw.strip_prefix('\'')?;
        let (value, rest) = body.split_once('\'')?;
        if !rest.chars().all(char::is_whitespace) {
            return None;
        }
        acceptable_token(value).then(|| value.to_string())
    }

    fn acceptable_token(token: &str) -> bool {
        let bytes = token.as_bytes();
        !bytes.is_empty() && bytes.len() <= 1024 && bytes.iter().all(u8::is_ascii_graphic)
    }

    async fn mcp_get(Extension(attach): Extension<McpAttach>, headers: HeaderMap) -> Response {
        if let Some(response) = reject(&attach, &headers) {
            return response;
        }
        if !accepts_sse(&headers) {
            return json_response(
                StatusCode::NOT_ACCEPTABLE,
                &json!({"error": "accept text/event-stream"}),
            );
        }
        let run = attach.aggregator.resolve_run(&attach.plane, &headers);
        stamp_run(
            open_sse(&stream_open_notification(), attach.keepalive),
            run.as_deref(),
        )
    }

    async fn mcp_post(Extension(attach): Extension<McpAttach>, request: Request) -> Response {
        let (parts, body) = request.into_parts();
        if let Some(response) = reject(&attach, &parts.headers) {
            return response;
        }
        if !is_json_content_type(&parts.headers) {
            return json_response(
                StatusCode::UNSUPPORTED_MEDIA_TYPE,
                &json!({"error": "content type must be application/json"}),
            );
        }
        let bytes = match to_bytes(body, MAX_BODY).await {
            Ok(bytes) => bytes,
            Err(_) => {
                return json_response(
                    StatusCode::PAYLOAD_TOO_LARGE,
                    &json!({"error": "body too large"}),
                );
            }
        };
        let payload: Value = match serde_json::from_slice(&bytes) {
            Ok(value) => value,
            Err(_) => {
                return json_response(
                    StatusCode::BAD_REQUEST,
                    &rpc_err(Value::Null, -32700, "parse error"),
                );
            }
        };
        let sse_only = wants_sse_only(&parts.headers);
        let run = attach.aggregator.resolve_run(&attach.plane, &parts.headers);
        let outcome = handle_payload(&attach, &payload).await;
        let response = match outcome {
            Outcome::Accepted => StatusCode::ACCEPTED.into_response(),
            Outcome::Reply(value) if sse_only => one_sse(&value),
            Outcome::Reply(value) => json_response(StatusCode::OK, &value),
        };
        stamp_run(response, run.as_deref())
    }

    fn reject(attach: &McpAttach, headers: &HeaderMap) -> Option<Response> {
        if !origin_allowed(headers) {
            return Some(json_response(
                StatusCode::FORBIDDEN,
                &json!({"error": "origin rejected"}),
            ));
        }
        if !protocol_header_ok(headers) {
            return Some(json_response(
                StatusCode::BAD_REQUEST,
                &json!({"error": "unsupported protocol version"}),
            ));
        }
        let presented = headers
            .get(header::AUTHORIZATION)
            .and_then(|value| value.to_str().ok())
            .and_then(presented_bearer);
        match presented {
            Some(token) if bearer_matches(&attach.token, token) => None,
            _ => Some(unauthorized()),
        }
    }

    fn presented_bearer(value: &str) -> Option<&str> {
        let (scheme, rest) = value.trim().split_once(' ')?;
        if !scheme.eq_ignore_ascii_case("bearer") {
            return None;
        }
        let token = rest.trim();
        if token.is_empty() || token.chars().any(char::is_whitespace) {
            return None;
        }
        Some(token)
    }

    fn bearer_matches(expected: &str, presented: &str) -> bool {
        if expected.is_empty() || !acceptable_token(expected) {
            return false;
        }
        let left = expected.as_bytes();
        let right = presented.as_bytes();
        let mut diff = left.len() ^ right.len();
        let width = left.len().max(right.len());
        for index in 0..width {
            let a = left.get(index).copied().unwrap_or(0);
            let b = right.get(index).copied().unwrap_or(0);
            diff |= usize::from(a ^ b);
        }
        diff == 0
    }

    fn origin_allowed(headers: &HeaderMap) -> bool {
        let Some(origin) = headers
            .get(header::ORIGIN)
            .and_then(|value| value.to_str().ok())
        else {
            return true;
        };
        let origin = origin.trim();
        if origin.is_empty() || origin.eq_ignore_ascii_case("null") {
            return false;
        }
        let Some(rest) = origin
            .strip_prefix("http://")
            .or_else(|| origin.strip_prefix("https://"))
        else {
            return false;
        };
        let hostport = rest.split('/').next().unwrap_or("");
        let hostport = hostport.rsplit('@').next().unwrap_or(hostport);
        let hostname = if let Some(inner) = hostport.strip_prefix('[') {
            inner.split(']').next().unwrap_or("")
        } else {
            hostport.split(':').next().unwrap_or("")
        };
        matches!(hostname, "localhost" | "127.0.0.1" | "::1")
    }

    fn protocol_header_ok(headers: &HeaderMap) -> bool {
        let Some(value) = headers
            .get("mcp-protocol-version")
            .and_then(|value| value.to_str().ok())
        else {
            return true;
        };
        SUPPORTED_PROTOCOLS.contains(&value.trim())
    }

    fn accepts_sse(headers: &HeaderMap) -> bool {
        let Some(accept) = headers
            .get(header::ACCEPT)
            .and_then(|value| value.to_str().ok())
        else {
            return true;
        };
        accept.split(',').any(|part| {
            let media = part.split(';').next().unwrap_or("").trim();
            media == "text/event-stream" || media == "*/*"
        })
    }

    fn wants_sse_only(headers: &HeaderMap) -> bool {
        let Some(accept) = headers
            .get(header::ACCEPT)
            .and_then(|value| value.to_str().ok())
        else {
            return false;
        };
        let mut has_sse = false;
        let mut has_json = false;
        for part in accept.split(',') {
            let name = part.split(';').next().unwrap_or("").trim();
            if name == "text/event-stream" {
                has_sse = true;
            }
            if name == "application/json" || name == "*/*" {
                has_json = true;
            }
        }
        has_sse && !has_json
    }

    fn is_json_content_type(headers: &HeaderMap) -> bool {
        let Some(value) = headers
            .get(header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
        else {
            return false;
        };
        value
            .split(';')
            .next()
            .unwrap_or("")
            .trim()
            .eq_ignore_ascii_case("application/json")
    }

    async fn handle_payload(attach: &McpAttach, payload: &Value) -> Outcome {
        let Some(batch) = payload.as_array() else {
            return handle_message(attach, payload).await;
        };
        if batch.is_empty() || batch.len() > MAX_BATCH {
            return Outcome::Reply(rpc_err(Value::Null, -32600, "invalid request"));
        }
        let mut replies = Vec::new();
        for message in batch {
            if let Outcome::Reply(value) = handle_message(attach, message).await {
                replies.push(value);
            }
        }
        if replies.is_empty() {
            Outcome::Accepted
        } else {
            Outcome::Reply(Value::Array(replies))
        }
    }

    async fn handle_message(attach: &McpAttach, message: &Value) -> Outcome {
        let Some(object) = message.as_object() else {
            return Outcome::Reply(rpc_err(Value::Null, -32600, "invalid request"));
        };
        if let Some(version) = object.get("jsonrpc").filter(|value| !value.is_null())
            && version.as_str() != Some("2.0")
        {
            return Outcome::Reply(rpc_err(id_of(object), -32600, "invalid request"));
        }
        let Some(method) = object.get("method").and_then(Value::as_str) else {
            return Outcome::Reply(rpc_err(id_of(object), -32600, "invalid request"));
        };
        if method.is_empty() {
            return Outcome::Reply(rpc_err(id_of(object), -32600, "invalid request"));
        }
        let id = id_of(object);
        let notification =
            !object.contains_key("id") || object.get("id").is_some_and(Value::is_null);
        if method.starts_with("notifications/") {
            return if notification {
                Outcome::Accepted
            } else {
                Outcome::Reply(rpc_err(id, -32600, "invalid request"))
            };
        }
        let params = object.get("params").cloned().unwrap_or(Value::Null);
        if !matches!(params, Value::Null | Value::Object(_)) {
            return Outcome::Reply(rpc_err(id, -32602, "invalid params"));
        }
        match method {
            "initialize" => Outcome::Reply(rpc_ok(id, initialize_result(&params))),
            "ping" => Outcome::Reply(rpc_ok(id, json!({}))),
            "tools/list" => {
                let mut tools = vec![ping_tool(), run_status_tool()];
                tools.extend(attach.aggregator.list_remote().await);
                Outcome::Reply(rpc_ok(id, json!({"tools": tools})))
            }
            "tools/call" => match tools_call(attach, &params).await {
                Ok(result) => Outcome::Reply(rpc_ok(id, result)),
                Err(failure) => Outcome::Reply(failure.into_rpc(id)),
            },
            _ => Outcome::Reply(rpc_err(id, -32601, "method not found")),
        }
    }

    fn id_of(object: &Map<String, Value>) -> Value {
        match object.get("id") {
            Some(id) if !id.is_null() => id.clone(),
            _ => Value::Null,
        }
    }

    fn initialize_result(params: &Value) -> Value {
        let requested = params
            .get("protocolVersion")
            .and_then(Value::as_str)
            .unwrap_or("");
        let protocol = if SUPPORTED_PROTOCOLS.contains(&requested) {
            requested
        } else {
            DEFAULT_PROTOCOL
        };
        json!({
            "protocolVersion": protocol,
            "capabilities": {"tools": {"listChanged": false}},
            "serverInfo": {
                "name": "vc-server",
                "version": env!("VC_SERVER_VERSION"),
            },
            "instructions": "Pilot tools: vc_ping, vc_run_status.",
        })
    }

    fn ping_tool() -> Value {
        json!({
            "name": TOOL_PING,
            "title": "Ping",
            "description": "Return the vc-server version and the current UTC time.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": false,
            },
        })
    }

    fn run_status_tool() -> Value {
        json!({
            "name": TOOL_RUN_STATUS,
            "title": "Run status",
            "description": "Read one control-plane run by id.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "Control-plane run id.",
                    },
                },
                "required": ["run_id"],
                "additionalProperties": false,
            },
        })
    }

    async fn tools_call(attach: &McpAttach, params: &Value) -> Result<Value, CallFailure> {
        let Some(object) = params.as_object() else {
            return Err(CallFailure::params("invalid params"));
        };
        let Some(name) = object.get("name").and_then(Value::as_str) else {
            return Err(CallFailure::params("invalid params"));
        };
        let arguments = object.get("arguments").cloned().unwrap_or(Value::Null);
        if name == TOOL_PING {
            return ping_call(&arguments);
        }
        if name == TOOL_RUN_STATUS {
            return run_status_call(&attach.plane, &arguments);
        }
        match attach.aggregator.call(name, arguments).await {
            RemoteCall::Local => Err(CallFailure::params("unknown tool")),
            RemoteCall::Ready(result) => Ok(result),
            RemoteCall::Failed {
                upstream,
                tool,
                fault,
            } => Err(CallFailure::Upstream {
                upstream,
                tool,
                reason: fault.reason(),
            }),
        }
    }

    fn ping_call(arguments: &Value) -> Result<Value, CallFailure> {
        match arguments {
            Value::Null => {}
            Value::Object(map) if map.is_empty() => {}
            _ => return Err(CallFailure::params("invalid params")),
        }
        let version = env!("VC_SERVER_VERSION");
        let time = Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true);
        Ok(tool_ok(
            format!("vc-server {version} {time}"),
            json!({"version": version, "time": time}),
        ))
    }

    fn run_status_call(plane: &ControlPlane, arguments: &Value) -> Result<Value, CallFailure> {
        let Some(map) = arguments.as_object() else {
            return Err(CallFailure::params("invalid params"));
        };
        if map.len() != 1 {
            return Err(CallFailure::params("invalid params"));
        }
        let Some(run_id) = map.get("run_id").and_then(Value::as_str) else {
            return Err(CallFailure::params("invalid params"));
        };
        let Some(run) = plane.lookup_run(run_id) else {
            return Ok(tool_err(format!("run not found: {run_id}")));
        };
        let structured = run_projection(&run);
        let text = format!(
            "run_id={} state={} health={} agent={} skill={}",
            run.run_id, run.state, run.health, run.agent, run.skill
        );
        Ok(tool_ok(text, structured))
    }

    fn run_projection(run: &RunStatus) -> Value {
        json!({
            "run_id": run.run_id,
            "state": run.state,
            "health": run.health,
            "agent": run.agent,
            "skill": run.skill,
            "mode": run.mode,
            "updated_at": run.updated_at,
            "started_at": run.started_at,
            "source": run.source,
            "lock_present": run.lock_present,
            "last_error": run.last_error,
        })
    }

    fn tool_ok(text: String, structured: Value) -> Value {
        json!({
            "content": [{"type": "text", "text": text}],
            "structuredContent": structured,
            "isError": false,
        })
    }

    fn tool_err(text: String) -> Value {
        json!({
            "content": [{"type": "text", "text": text}],
            "isError": true,
        })
    }

    fn stream_open_notification() -> Value {
        json!({
            "jsonrpc": "2.0",
            "method": "notifications/message",
            "params": {
                "level": "info",
                "logger": "vc-server",
                "data": "mcp stream open",
            },
        })
    }

    fn rpc_ok(id: Value, result: Value) -> Value {
        json!({"jsonrpc": "2.0", "id": id, "result": result})
    }

    enum CallFailure {
        Params(&'static str),
        Upstream {
            upstream: String,
            tool: String,
            reason: &'static str,
        },
    }

    impl CallFailure {
        fn params(message: &'static str) -> Self {
            Self::Params(message)
        }

        fn into_rpc(self, id: Value) -> Value {
            match self {
                Self::Params(message) => rpc_err(id, -32602, message),
                Self::Upstream {
                    upstream,
                    tool,
                    reason,
                } => json!({
                    "jsonrpc": "2.0",
                    "id": id,
                    "error": {
                        "code": -32002,
                        "message": "upstream unavailable",
                        "data": {
                            "upstream": upstream,
                            "tool": tool,
                            "reason": reason,
                        }
                    }
                }),
            }
        }
    }

    fn rpc_err(id: Value, code: i32, message: &str) -> Value {
        json!({
            "jsonrpc": "2.0",
            "id": id,
            "error": {"code": code, "message": message},
        })
    }

    fn stamp_run(mut response: Response, run: Option<&str>) -> Response {
        if let Some(run) = run
            && let Ok(value) = header::HeaderValue::from_str(run)
        {
            response
                .headers_mut()
                .insert(mcp_aggregator::RUN_HEADER, value);
        }
        response
    }

    fn json_response(status: StatusCode, value: &Value) -> Response {
        let mut response = Response::new(Body::from(value.to_string()));
        *response.status_mut() = status;
        response.headers_mut().insert(
            header::CONTENT_TYPE,
            header::HeaderValue::from_static("application/json"),
        );
        response.headers_mut().insert(
            header::CACHE_CONTROL,
            header::HeaderValue::from_static("no-store"),
        );
        response
    }

    fn unauthorized() -> Response {
        let mut response =
            json_response(StatusCode::UNAUTHORIZED, &json!({"error": "unauthorized"}));
        response.headers_mut().insert(
            header::WWW_AUTHENTICATE,
            header::HeaderValue::from_static("Bearer realm=\"vc-server\""),
        );
        response
    }

    fn one_sse(payload: &Value) -> Response {
        let data = payload.to_string();
        let events = stream::once(async move {
            Ok::<Event, Infallible>(Event::default().event("message").data(data))
        });
        Sse::new(events).into_response()
    }

    fn open_sse(payload: &Value, keepalive: Duration) -> Response {
        let data = payload.to_string();
        let events = stream::once(async move {
            Ok::<Event, Infallible>(Event::default().event("message").data(data))
        })
        .chain(stream::pending::<Result<Event, Infallible>>());
        Sse::new(events)
            .keep_alive(KeepAlive::new().interval(keepalive).text("keepalive"))
            .into_response()
    }
}
