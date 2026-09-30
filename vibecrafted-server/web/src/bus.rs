//! Front for the provider-message store.
//!
//! `POST /api/bus/messages` and the MCP tools `vc_message_send`,
//! `vc_message_reply`, and `vc_message_status` call `vibecrafted message`.
//! The receipt JSON is returned as the store printed it. Delivery states are
//! not invented here, and the message body is written to a `0600` file so it
//! never appears on the child argv.

#[cfg(feature = "ssr")]
pub mod api {
    use std::fs::{self, OpenOptions};
    use std::io::{self, Read, Write};
    use std::path::{Path, PathBuf};
    use std::process::{Child, Command, ExitStatus, Stdio};
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::thread;
    use std::time::{Duration, Instant};

    use axum::Router;
    use axum::body::{Body, to_bytes};
    use axum::extract::{Extension, Request};
    use axum::http::{HeaderMap, StatusCode, header};
    use axum::response::Response;
    use axum::routing::post;
    use serde_json::{Map, Value, json};

    const ENVELOPE_SPEC: &str = "fleet.envelope/0.1";
    const MAX_BODY: usize = 64 * 1024;
    const CLI_TIMEOUT: Duration = Duration::from_secs(20);
    const CLI_OUTPUT_LIMIT: usize = 256 * 1024;
    const FLEET_ROLES: [&str; 5] = ["fable", "kodeksik", "slack-projector", "monika", "maciej"];
    const TASK_STATES: [&str; 9] = [
        "submitted",
        "working",
        "input-required",
        "auth-required",
        "completed",
        "canceled",
        "failed",
        "rejected",
        "unknown",
    ];
    const ENVELOPE_KEYS: [&str; 18] = [
        "spec",
        "id",
        "source",
        "type",
        "time",
        "traceparent",
        "messageId",
        "contextId",
        "taskId",
        "referenceTaskIds",
        "role",
        "parts",
        "recipient",
        "hopCount",
        "maxHops",
        "status",
        "statusDetail",
        "metadata",
    ];
    const VIBECRAFTED_META_KEYS: [&str; 3] = ["run_id", "session", "retry"];

    pub const TOOL_MESSAGE_SEND: &str = "vc_message_send";
    pub const TOOL_MESSAGE_REPLY: &str = "vc_message_reply";
    pub const TOOL_MESSAGE_STATUS: &str = "vc_message_status";

    #[derive(Clone)]
    struct BusAttach {
        token: String,
    }

    struct Prepared {
        body: Vec<u8>,
        run_id: Option<String>,
        session: Option<String>,
        idempotency_key: String,
        retry: bool,
    }

    struct TempBody {
        path: PathBuf,
    }

    impl Drop for TempBody {
        fn drop(&mut self) {
            let _ = fs::remove_file(&self.path);
        }
    }

    struct CliOut {
        code: i32,
        stdout: Vec<u8>,
        stderr: Vec<u8>,
    }

    struct Fail {
        status: StatusCode,
        body: Value,
    }

    enum StoreResult {
        Receipt(Value),
        Fail(Fail),
    }

    /// Router mounted by `vc-server`. Same bearer as `POST /mcp`.
    pub fn bus_routes() -> Router<leptos::config::LeptosOptions> {
        bus_routes_with(crate::mcp::api::configured_bearer())
    }

    /// Same router as [`bus_routes`], with an explicit bearer.
    ///
    /// Tests pass the token in. They do not publish `VC_SERVER_MCP_BEARER`.
    pub fn bus_routes_with(bearer: impl Into<String>) -> Router<leptos::config::LeptosOptions> {
        let attach = BusAttach {
            token: bearer.into(),
        };
        Router::<leptos::config::LeptosOptions>::new()
            .route("/api/bus/messages", post(post_message))
            .layer(Extension(attach))
    }

    #[must_use]
    pub fn message_send_tool() -> Value {
        json!({
            "name": TOOL_MESSAGE_SEND,
            "title": "Send bus message",
            "description": "Send one fleet.envelope/0.1 to a control-plane run via the message store.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "envelope": {
                        "type": "object",
                        "description": "fleet.envelope/0.1 object. metadata.vibecrafted.run_id or metadata.vibecrafted.session addresses the store.",
                    },
                },
                "required": ["envelope"],
                "additionalProperties": false,
            },
        })
    }

    #[must_use]
    pub fn message_reply_tool() -> Value {
        json!({
            "name": TOOL_MESSAGE_REPLY,
            "title": "Reply to bus message",
            "description": "Reply to a message_id. The store receipt's run_id is the addressee; metadata.vibecrafted.run_id on the reply envelope is not.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "Receipt id to answer.",
                    },
                    "envelope": {
                        "type": "object",
                        "description": "fleet.envelope/0.1 reply. contextId must match the original envelope when the receipt text is one.",
                    },
                },
                "required": ["message_id", "envelope"],
                "additionalProperties": false,
            },
        })
    }

    #[must_use]
    pub fn message_status_tool() -> Value {
        json!({
            "name": TOOL_MESSAGE_STATUS,
            "title": "Bus message status",
            "description": "Read one message receipt by id. Unknown ids are a typed error, not an empty receipt.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "Receipt id to inspect.",
                    },
                },
                "required": ["message_id"],
                "additionalProperties": false,
            },
        })
    }

    /// MCP `tools/call` dispatch for the three bus tools.
    ///
    /// # Errors
    ///
    /// Returns `invalid params` when the arguments object does not match the
    /// tool schema. Store failures are tool results with `isError`, not
    /// JSON-RPC protocol errors.
    pub fn call_message_tool(name: &str, arguments: &Value) -> Result<Value, &'static str> {
        let Some(map) = arguments.as_object() else {
            return Err("invalid params");
        };
        let result = match name {
            TOOL_MESSAGE_SEND => {
                if map.len() != 1 {
                    return Err("invalid params");
                }
                let Some(envelope) = map.get("envelope") else {
                    return Err("invalid params");
                };
                send_envelope(envelope, None)
            }
            TOOL_MESSAGE_REPLY => {
                if map.len() != 2 {
                    return Err("invalid params");
                }
                let Some(message_id) = map.get("message_id").and_then(Value::as_str) else {
                    return Err("invalid params");
                };
                let Some(envelope) = map.get("envelope") else {
                    return Err("invalid params");
                };
                reply_to(message_id, envelope)
            }
            TOOL_MESSAGE_STATUS => {
                if map.len() != 1 {
                    return Err("invalid params");
                }
                let Some(message_id) = map.get("message_id").and_then(Value::as_str) else {
                    return Err("invalid params");
                };
                inspect(message_id)
            }
            _ => return Err("unknown tool"),
        };
        Ok(match result {
            StoreResult::Receipt(receipt) => tool_receipt(&receipt),
            StoreResult::Fail(fail) => tool_fail(&fail),
        })
    }

    /// Pending inbox receipts for one run. Does not consume them.
    ///
    /// `vibecrafted message --run-id <id> --receive --json`.
    ///
    /// # Errors
    ///
    /// The id is unsafe or the CLI did not return a JSON array.
    pub fn receive_pending(run_id: &str) -> Result<Vec<Value>, &'static str> {
        if !safe_id(run_id) {
            return Err("invalid_run_id");
        }
        let argv = vec![
            "message".to_string(),
            "--run-id".to_string(),
            run_id.to_string(),
            "--receive".to_string(),
            "--json".to_string(),
        ];
        let output = run_cli(&argv).map_err(|_| "message_cli_failed")?;
        if output.code != 0 {
            return Err("message_receive_failed");
        }
        parse_pending(&output.stdout).ok_or("message_receive_failed")
    }

    /// Record that one receipt was attached to a tool result.
    ///
    /// A non-zero CLI status means the store did not accept the mark.
    /// Callers must not attach the text in that case.
    ///
    /// # Errors
    ///
    /// The id or nonce is rejected, or `mark_context_injected` failed.
    pub fn mark_context_injected(message_id: &str, nonce: &str) -> Result<(), &'static str> {
        if !safe_id(message_id) {
            return Err("invalid_message_id");
        }
        if !crate::mcp_aggregator::valid_injection_nonce(nonce) {
            return Err("invalid_context_injection_nonce");
        }
        let argv = vec![
            "message".to_string(),
            "--mark-context-injected".to_string(),
            message_id.to_string(),
            "--nonce".to_string(),
            nonce.to_string(),
            "--json".to_string(),
        ];
        let output = run_cli(&argv).map_err(|_| "message_cli_failed")?;
        if output.code != 0 {
            return Err("message_mark_failed");
        }
        let text = std::str::from_utf8(&output.stdout).map_err(|_| "message_mark_failed")?;
        let value: Value = serde_json::from_str(text.trim()).map_err(|_| "message_mark_failed")?;
        if value.get("delivery_state").and_then(Value::as_str) != Some("context_injected") {
            return Err("message_mark_failed");
        }
        if value.get("context_injected_nonce").and_then(Value::as_str) != Some(nonce) {
            return Err("message_mark_failed");
        }
        Ok(())
    }

    async fn post_message(Extension(attach): Extension<BusAttach>, request: Request) -> Response {
        if let Some(response) =
            crate::mcp::api::reject_bus_request(&attach.token, request.headers())
        {
            return response;
        }
        if !is_json_content_type(request.headers()) {
            return fail_response(&Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "content_type",
            ));
        }
        let bytes = match to_bytes(request.into_body(), MAX_BODY).await {
            Ok(bytes) => bytes,
            Err(_) => {
                return fail_response(&Fail::code(StatusCode::BAD_REQUEST, "message_too_large"));
            }
        };
        let envelope: Value = match serde_json::from_slice(&bytes) {
            Ok(value) => value,
            Err(_) => {
                return fail_response(&Fail::detail(
                    StatusCode::BAD_REQUEST,
                    "invalid_envelope",
                    "json",
                ));
            }
        };
        match send_envelope(&envelope, None) {
            StoreResult::Receipt(receipt) => json_response(StatusCode::OK, &receipt),
            StoreResult::Fail(fail) => fail_response(&fail),
        }
    }

    fn send_envelope(envelope: &Value, forced_run: Option<&str>) -> StoreResult {
        let prepared = match prepare_envelope(envelope, forced_run) {
            Ok(prepared) => prepared,
            Err(fail) => return StoreResult::Fail(fail),
        };
        let file = match TempBody::create(&prepared.body) {
            Ok(file) => file,
            Err(fail) => return StoreResult::Fail(fail),
        };
        let argv = send_argv(&prepared, &file.path);
        dispatch(&argv)
    }

    fn reply_to(message_id: &str, envelope: &Value) -> StoreResult {
        if !safe_id(message_id) {
            return StoreResult::Fail(Fail::code(StatusCode::BAD_REQUEST, "invalid_message_id"));
        }
        let receipt = match inspect(message_id) {
            StoreResult::Receipt(receipt) => receipt,
            other => return other,
        };
        let Some(run_id) = receipt.get("run_id").and_then(Value::as_str) else {
            return StoreResult::Fail(Fail::code(StatusCode::BAD_REQUEST, "receipt_run_missing"));
        };
        if !safe_id(run_id) {
            return StoreResult::Fail(Fail::code(StatusCode::BAD_REQUEST, "receipt_run_missing"));
        }
        let run_id = run_id.to_string();
        if let Some(fail) = context_mismatch(&receipt, envelope) {
            return StoreResult::Fail(fail);
        }
        send_envelope(envelope, Some(&run_id))
    }

    fn inspect(message_id: &str) -> StoreResult {
        if !safe_id(message_id) {
            return StoreResult::Fail(Fail::code(StatusCode::BAD_REQUEST, "invalid_message_id"));
        }
        let argv = vec![
            "message".to_string(),
            "--inspect".to_string(),
            message_id.to_string(),
            "--json".to_string(),
        ];
        dispatch(&argv)
    }

    fn context_mismatch(receipt: &Value, envelope: &Value) -> Option<Fail> {
        let text = receipt.get("text").and_then(Value::as_str)?;
        let original: Value = serde_json::from_str(text).ok()?;
        if original.get("spec").and_then(Value::as_str) != Some(ENVELOPE_SPEC) {
            return None;
        }
        let original_context = original.get("contextId").and_then(Value::as_str)?;
        let reply_context = envelope.get("contextId").and_then(Value::as_str);
        if reply_context == Some(original_context) {
            None
        } else {
            Some(Fail::detail(
                StatusCode::BAD_REQUEST,
                "context_mismatch",
                "contextId",
            ))
        }
    }

    fn prepare_envelope(envelope: &Value, forced_run: Option<&str>) -> Result<Prepared, Fail> {
        let Some(object) = envelope.as_object() else {
            return Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "object",
            ));
        };
        if object.len() != ENVELOPE_KEYS.len()
            || ENVELOPE_KEYS.iter().any(|key| !object.contains_key(*key))
        {
            return Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "keys",
            ));
        }
        if object.get("spec").and_then(Value::as_str) != Some(ENVELOPE_SPEC) {
            return Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "spec",
            ));
        }
        let id = req_str(object, "id")?;
        let message_id = req_str(object, "messageId")?;
        if id != message_id || !safe_id(id) {
            return Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "id",
            ));
        }
        let source = req_str(object, "source")?;
        if !valid_source(source) {
            return Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "source",
            ));
        }
        let kind = req_str(object, "type")?;
        if kind != "fleet.message" && kind != "fleet.status" {
            return Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "type",
            ));
        }
        req_str(object, "time")?;
        req_str(object, "traceparent")?;
        req_str(object, "contextId")?;
        nullable_str(object, "taskId")?;
        string_array(object, "referenceTaskIds")?;
        let role = req_str(object, "role")?;
        if role != "agent" && role != "human" {
            return Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "role",
            ));
        }
        text_parts(object)?;
        let recipient = req_str(object, "recipient")?;
        if !FLEET_ROLES.contains(&recipient) {
            return Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "recipient",
            ));
        }
        let hop_count = nonneg(object, "hopCount")?;
        let max_hops = nonneg(object, "maxHops")?;
        if max_hops == 0 || max_hops > 8 || hop_count >= max_hops {
            return Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "hopCount",
            ));
        }
        task_status(object)?;
        nullable_str(object, "statusDetail")?;
        let (mut run_id, mut session, retry) = vibecrafted_target(object)?;
        if let Some(forced) = forced_run {
            if !safe_id(forced) {
                return Err(Fail::code(StatusCode::BAD_REQUEST, "receipt_run_missing"));
            }
            run_id = Some(forced.to_string());
            session = None;
        }
        if run_id.is_none() && session.is_none() {
            return Err(Fail::code(
                StatusCode::BAD_REQUEST,
                "run_id_or_session_required",
            ));
        }
        let body = serde_json::to_vec(envelope)
            .map_err(|_| Fail::detail(StatusCode::BAD_REQUEST, "invalid_envelope", "json"))?;
        if body.len() > MAX_BODY {
            return Err(Fail::code(StatusCode::BAD_REQUEST, "message_too_large"));
        }
        Ok(Prepared {
            body,
            run_id,
            session,
            idempotency_key: id.to_string(),
            retry,
        })
    }

    fn vibecrafted_target(
        object: &Map<String, Value>,
    ) -> Result<(Option<String>, Option<String>, bool), Fail> {
        let Some(metadata) = object.get("metadata").and_then(Value::as_object) else {
            return Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "metadata",
            ));
        };
        let Some(adapter) = metadata.get("vibecrafted") else {
            return Ok((None, None, false));
        };
        let Some(adapter) = adapter.as_object() else {
            return Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "metadata.vibecrafted",
            ));
        };
        if adapter
            .keys()
            .any(|key| !VIBECRAFTED_META_KEYS.contains(&key.as_str()))
        {
            return Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "metadata.vibecrafted",
            ));
        }
        let run_id = optional_safe_id(adapter, "run_id")?;
        let session = optional_safe_id(adapter, "session")?;
        let retry = match adapter.get("retry") {
            None | Some(Value::Bool(false)) => false,
            Some(Value::Bool(true)) => true,
            _ => {
                return Err(Fail::detail(
                    StatusCode::BAD_REQUEST,
                    "invalid_envelope",
                    "metadata.vibecrafted.retry",
                ));
            }
        };
        Ok((run_id, session, retry))
    }

    fn optional_safe_id(object: &Map<String, Value>, key: &str) -> Result<Option<String>, Fail> {
        match object.get(key) {
            None => Ok(None),
            Some(Value::String(value)) if safe_id(value) => Ok(Some(value.clone())),
            _ => Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "metadata.vibecrafted",
            )),
        }
    }

    fn req_str<'a>(object: &'a Map<String, Value>, key: &'static str) -> Result<&'a str, Fail> {
        match object.get(key).and_then(Value::as_str) {
            Some(value) if !value.is_empty() && !value.contains('\0') => Ok(value),
            _ => Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                key,
            )),
        }
    }

    fn nullable_str(object: &Map<String, Value>, key: &'static str) -> Result<(), Fail> {
        match object.get(key) {
            Some(Value::Null) => Ok(()),
            Some(Value::String(value)) if !value.contains('\0') => Ok(()),
            _ => Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                key,
            )),
        }
    }

    fn string_array(object: &Map<String, Value>, key: &'static str) -> Result<(), Fail> {
        let Some(items) = object.get(key).and_then(Value::as_array) else {
            return Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                key,
            ));
        };
        if items
            .iter()
            .all(|item| item.as_str().is_some_and(|value| !value.contains('\0')))
        {
            Ok(())
        } else {
            Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                key,
            ))
        }
    }

    fn text_parts(object: &Map<String, Value>) -> Result<(), Fail> {
        let Some(parts) = object.get("parts").and_then(Value::as_array) else {
            return Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "parts",
            ));
        };
        if parts.is_empty() {
            return Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "parts",
            ));
        }
        for part in parts {
            let Some(part) = part.as_object() else {
                return Err(Fail::detail(
                    StatusCode::BAD_REQUEST,
                    "invalid_envelope",
                    "parts",
                ));
            };
            if part.len() != 2 || part.get("kind").and_then(Value::as_str) != Some("text") {
                return Err(Fail::detail(
                    StatusCode::BAD_REQUEST,
                    "invalid_envelope",
                    "parts",
                ));
            }
            let Some(text) = part.get("text").and_then(Value::as_str) else {
                return Err(Fail::detail(
                    StatusCode::BAD_REQUEST,
                    "invalid_envelope",
                    "parts",
                ));
            };
            if text.contains('\0') {
                return Err(Fail::detail(
                    StatusCode::BAD_REQUEST,
                    "invalid_envelope",
                    "parts",
                ));
            }
        }
        Ok(())
    }

    fn nonneg(object: &Map<String, Value>, key: &'static str) -> Result<u64, Fail> {
        match object.get(key).and_then(Value::as_u64) {
            Some(value) => Ok(value),
            None => Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                key,
            )),
        }
    }

    fn task_status(object: &Map<String, Value>) -> Result<(), Fail> {
        match object.get("status") {
            Some(Value::Null) => Ok(()),
            Some(Value::String(value)) if TASK_STATES.contains(&value.as_str()) => Ok(()),
            _ => Err(Fail::detail(
                StatusCode::BAD_REQUEST,
                "invalid_envelope",
                "status",
            )),
        }
    }

    fn valid_source(value: &str) -> bool {
        if let Some(role) = value.strip_prefix("fleet:") {
            return FLEET_ROLES.contains(&role);
        }
        if let Some(user) = value.strip_prefix("slack:") {
            return !user.is_empty()
                && user
                    .chars()
                    .all(|ch| ch.is_ascii_alphanumeric() || ch == '-' || ch == '_');
        }
        false
    }

    fn safe_id(value: &str) -> bool {
        let mut chars = value.chars();
        let Some(first) = chars.next() else {
            return false;
        };
        if !first.is_ascii_alphanumeric() {
            return false;
        }
        value.len() <= 128
            && value
                .chars()
                .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '.' | '_' | ':' | '-'))
    }

    fn send_argv(prepared: &Prepared, path: &Path) -> Vec<String> {
        let mut argv = vec!["message".to_string()];
        if let Some(run_id) = &prepared.run_id {
            argv.push("--run-id".to_string());
            argv.push(run_id.clone());
        }
        if let Some(session) = &prepared.session {
            argv.push("--session".to_string());
            argv.push(session.clone());
        }
        argv.push("--idempotency-key".to_string());
        argv.push(prepared.idempotency_key.clone());
        if prepared.retry {
            argv.push("--retry".to_string());
        }
        argv.push("--file".to_string());
        argv.push(path.display().to_string());
        argv.push("--json".to_string());
        argv
    }

    fn dispatch(argv: &[String]) -> StoreResult {
        match run_cli(argv) {
            Ok(output) => classify_output(&output),
            Err(fail) => StoreResult::Fail(fail),
        }
    }

    fn classify_output(output: &CliOut) -> StoreResult {
        if let Some(receipt) = parse_receipt(&output.stdout) {
            if output.code == 0 || output.code == 1 {
                return StoreResult::Receipt(receipt);
            }
        }
        let stderr = String::from_utf8_lossy(&output.stderr);
        let line = stderr.lines().next().unwrap_or("").trim();
        let code = line.strip_prefix("error: ").unwrap_or(line).trim();
        if code.starts_with("message not found:") {
            let message_id = code
                .trim_start_matches("message not found:")
                .trim()
                .to_string();
            return StoreResult::Fail(Fail::not_found(&message_id));
        }
        if output.code == 2 && !code.is_empty() && safe_store_code(code) {
            return StoreResult::Fail(Fail::store(StatusCode::BAD_REQUEST, code));
        }
        StoreResult::Fail(Fail::code(StatusCode::BAD_GATEWAY, "message_store_failed"))
    }

    fn parse_pending(stdout: &[u8]) -> Option<Vec<Value>> {
        let text = std::str::from_utf8(stdout).ok()?.trim();
        let value: Value = serde_json::from_str(text).ok()?;
        value.as_array().cloned()
    }

    fn parse_receipt(stdout: &[u8]) -> Option<Value> {
        let text = std::str::from_utf8(stdout).ok()?.trim();
        if text.is_empty() {
            return None;
        }
        let value: Value = serde_json::from_str(text).ok()?;
        let message_id = value.get("message_id").and_then(Value::as_str)?;
        if safe_id(message_id)
            && value
                .get("delivery_state")
                .and_then(Value::as_str)
                .is_some()
        {
            Some(value)
        } else {
            None
        }
    }

    fn safe_store_code(value: &str) -> bool {
        !value.is_empty()
            && value.len() <= 160
            && value
                .chars()
                .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | ':' | '-' | '.'))
    }

    fn run_cli(argv: &[String]) -> Result<CliOut, Fail> {
        let mut child = Command::new("vibecrafted")
            .args(argv)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| {
                if error.kind() == io::ErrorKind::NotFound {
                    Fail::code(StatusCode::BAD_GATEWAY, "message_cli_missing")
                } else {
                    Fail::code(StatusCode::BAD_GATEWAY, "message_cli_spawn")
                }
            })?;
        let mut stdout = child
            .stdout
            .take()
            .ok_or_else(|| Fail::code(StatusCode::BAD_GATEWAY, "message_cli_spawn"))?;
        let mut stderr = child
            .stderr
            .take()
            .ok_or_else(|| Fail::code(StatusCode::BAD_GATEWAY, "message_cli_spawn"))?;
        let out_thread = thread::spawn(move || read_limited(&mut stdout, CLI_OUTPUT_LIMIT));
        let err_thread = thread::spawn(move || read_limited(&mut stderr, CLI_OUTPUT_LIMIT));
        let status = wait_timeout(&mut child)?;
        let stdout = out_thread.join().unwrap_or_default();
        let stderr = err_thread.join().unwrap_or_default();
        Ok(CliOut {
            code: status.code().unwrap_or(1),
            stdout,
            stderr,
        })
    }

    fn wait_timeout(child: &mut Child) -> Result<ExitStatus, Fail> {
        let started = Instant::now();
        loop {
            match child.try_wait() {
                Ok(Some(status)) => return Ok(status),
                Ok(None) if started.elapsed() >= CLI_TIMEOUT => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(Fail::code(StatusCode::BAD_GATEWAY, "message_cli_timeout"));
                }
                Ok(None) => thread::sleep(Duration::from_millis(10)),
                Err(_) => return Err(Fail::code(StatusCode::BAD_GATEWAY, "message_cli_wait")),
            }
        }
    }

    fn read_limited(reader: &mut impl Read, limit: usize) -> Vec<u8> {
        let mut buffer = Vec::new();
        let mut chunk = [0_u8; 4096];
        while buffer.len() < limit {
            let want = (limit - buffer.len()).min(chunk.len());
            match reader.read(&mut chunk[..want]) {
                Ok(0) => break,
                Ok(count) => buffer.extend_from_slice(&chunk[..count]),
                Err(_) => break,
            }
        }
        buffer
    }

    impl TempBody {
        fn create(body: &[u8]) -> Result<Self, Fail> {
            static NEXT: AtomicU64 = AtomicU64::new(1);
            let nonce = NEXT.fetch_add(1, Ordering::Relaxed);
            let path =
                std::env::temp_dir().join(format!("vc-bus-{}-{nonce}.json", std::process::id()));
            // Body stays off argv via a tempfile. Unix keeps mode 0600; Windows
            // uses the same path-based create_new write without POSIX mode bits
            // (temp dir is already per-user; no ACL identity pretence).
            let mut file = OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(&path)
                .map_err(|_| Fail::code(StatusCode::BAD_GATEWAY, "message_file_create"))?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                file.set_permissions(fs::Permissions::from_mode(0o600))
                    .map_err(|_| Fail::code(StatusCode::BAD_GATEWAY, "message_file_create"))?;
            }
            file.write_all(body)
                .map_err(|_| Fail::code(StatusCode::BAD_GATEWAY, "message_file_create"))?;
            Ok(Self { path })
        }
    }

    impl Fail {
        fn code(status: StatusCode, error: &'static str) -> Self {
            Self {
                status,
                body: json!({"error": error}),
            }
        }

        fn detail(status: StatusCode, error: &'static str, detail: &'static str) -> Self {
            Self {
                status,
                body: json!({"error": error, "detail": detail}),
            }
        }

        fn store(status: StatusCode, error: &str) -> Self {
            Self {
                status,
                body: json!({"error": error}),
            }
        }

        fn not_found(message_id: &str) -> Self {
            Self {
                status: StatusCode::NOT_FOUND,
                body: json!({"error": "message_not_found", "message_id": message_id}),
            }
        }
    }

    fn tool_receipt(receipt: &Value) -> Value {
        let message_id = receipt
            .get("message_id")
            .and_then(Value::as_str)
            .unwrap_or("");
        let state = receipt
            .get("delivery_state")
            .and_then(Value::as_str)
            .unwrap_or("");
        json!({
            "content": [{
                "type": "text",
                "text": format!("message_id={message_id} delivery_state={state}"),
            }],
            "structuredContent": receipt,
            "isError": false,
        })
    }

    fn tool_fail(fail: &Fail) -> Value {
        let error = fail
            .body
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("error");
        json!({
            "content": [{"type": "text", "text": error}],
            "structuredContent": fail.body,
            "isError": true,
        })
    }

    fn fail_response(fail: &Fail) -> Response {
        json_response(fail.status, &fail.body)
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

    fn is_json_content_type(headers: &HeaderMap) -> bool {
        headers
            .get(header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .is_some_and(|value| {
                value
                    .split(';')
                    .next()
                    .unwrap_or("")
                    .trim()
                    .eq_ignore_ascii_case("application/json")
            })
    }
}
