//! stdio ↔ streamable HTTP bridge for one MCP entry per agent CLI.
//!
//! The process speaks newline-delimited JSON-RPC on stdin/stdout and posts
//! each message to vc-server `/mcp`. Its own PID, ancestor chain, and
//! `VIBECRAFTED_RUN_ID` (when set) ride on every request. If vc-server cannot
//! be reached, `initialize` still completes and `tools/list` is empty; the
//! next message tries again.

use std::io::{BufRead, BufReader, Read, Write};
use std::process::ExitCode;
use std::time::Duration;

use serde_json::{Value, json};

const DEFAULT_URL: &str = "http://127.0.0.1:3024/mcp";
const DEFAULT_PROTOCOL: &str = "2025-03-26";
const SUPPORTED_PROTOCOLS: [&str; 2] = ["2025-03-26", "2025-06-18"];
const MAX_LINE: usize = 1024 * 1024;
const MAX_RESPONSE: usize = 8 * 1024 * 1024;
const MAX_ANCESTORS: usize = 16;

struct Bridge {
    url: String,
    bearer: Option<String>,
    client: reqwest::blocking::Client,
    session: Option<String>,
    protocol: String,
    pid: u32,
    ancestors: String,
    run_id: Option<String>,
}

fn main() -> ExitCode {
    let url = std::env::var("VC_MCP_URL").unwrap_or_else(|_| DEFAULT_URL.to_string());
    let timeout_ms = std::env::var("VC_MCP_TIMEOUT_MS")
        .ok()
        .and_then(|raw| raw.parse::<u64>().ok())
        .unwrap_or(800)
        .clamp(50, 30_000);
    let bearer = std::env::var("VC_SERVER_MCP_BEARER")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    let run_id = std::env::var("VIBECRAFTED_RUN_ID")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_millis(timeout_ms))
        .connect_timeout(Duration::from_millis(timeout_ms.min(300)))
        .http1_only()
        .build()
    {
        Ok(client) => client,
        Err(err) => {
            debug(&format!("http client: {err}"));
            return ExitCode::from(1);
        }
    };
    let (pid, ancestors) = ancestor_chain();
    let mut bridge = Bridge {
        url,
        bearer,
        client,
        session: None,
        protocol: DEFAULT_PROTOCOL.to_string(),
        pid,
        ancestors,
        run_id,
    };
    let stdin = std::io::stdin();
    let mut reader = BufReader::new(stdin.lock());
    let mut line: Vec<u8> = Vec::new();
    loop {
        line.clear();
        // Bounded accumulation: never retain more than MAX_LINE + 1 bytes of a
        // line, even when the peer streams an unterminated multi-gigabyte one.
        match Read::by_ref(&mut reader)
            .take(MAX_LINE as u64 + 1)
            .read_until(b'\n', &mut line)
        {
            Ok(0) => return ExitCode::SUCCESS,
            Ok(_) => {}
            Err(err) => {
                debug(&format!("stdin: {err}"));
                return ExitCode::from(1);
            }
        }
        if line.len() > MAX_LINE {
            write_stdout(&rpc_err(Value::Null, -32600, "invalid request"));
            if line.last() != Some(&b'\n') && !drain_oversized_line(&mut reader) {
                return ExitCode::SUCCESS;
            }
            continue;
        }
        let Ok(text) = std::str::from_utf8(&line) else {
            write_stdout(&rpc_err(Value::Null, -32700, "parse error"));
            continue;
        };
        let trimmed = text.trim();
        if trimmed.is_empty() {
            continue;
        }
        let message: Value = match serde_json::from_str(trimmed) {
            Ok(value) => value,
            Err(_) => {
                write_stdout(&rpc_err(Value::Null, -32700, "parse error"));
                continue;
            }
        };
        if let Some(reply) = bridge.handle(&message) {
            write_stdout(&reply);
        }
    }
}

impl Bridge {
    fn handle(&mut self, message: &Value) -> Option<Value> {
        let Some(object) = message.as_object() else {
            return Some(rpc_err(Value::Null, -32600, "invalid request"));
        };
        let id = object.get("id").cloned().unwrap_or(Value::Null);
        let method = object.get("method").and_then(Value::as_str).unwrap_or("");
        let notification = !object.contains_key("id") || id.is_null();
        match self.post(message) {
            Ok(Some(reply)) => {
                if method == "initialize" {
                    self.note_initialize(&reply);
                }
                if notification { None } else { Some(reply) }
            }
            Ok(None) => None,
            Err(_) if notification => None,
            Err(_) => Some(self.degrade(method, &id, object.get("params"))),
        }
    }

    fn degrade(&self, method: &str, id: &Value, params: Option<&Value>) -> Value {
        match method {
            "initialize" => initialize_result(id, params.unwrap_or(&Value::Null)),
            "tools/list" => json!({
                "jsonrpc": "2.0",
                "id": id,
                "result": {"tools": []}
            }),
            "ping" => json!({"jsonrpc": "2.0", "id": id, "result": {}}),
            "tools/call" => rpc_err_data(
                id.clone(),
                -32002,
                "vc-server unreachable",
                json!({"reason": "unreachable"}),
            ),
            _ => rpc_err(id.clone(), -32002, "vc-server unreachable"),
        }
    }

    fn post(&mut self, message: &Value) -> Result<Option<Value>, ()> {
        let method = message
            .get("method")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let mut request = self
            .client
            .post(&self.url)
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .header(
                reqwest::header::ACCEPT,
                "application/json, text/event-stream",
            )
            .header("x-vibecrafted-pid", self.pid.to_string())
            .header("x-vibecrafted-ancestors", &self.ancestors);
        if method != "initialize" {
            request = request.header("mcp-protocol-version", &self.protocol);
        }
        if let Some(bearer) = &self.bearer {
            request = request.bearer_auth(bearer);
        }
        if let Some(run_id) = &self.run_id {
            request = request.header("x-vibecrafted-run-id", run_id);
        }
        if let Some(session) = &self.session {
            request = request.header("mcp-session-id", session);
        }
        let response = request.json(message).send().map_err(|err| {
            debug(&format!("post: {err}"));
        })?;
        if let Some(session) = response
            .headers()
            .get("mcp-session-id")
            .and_then(|value| value.to_str().ok())
            .map(str::trim)
            .filter(|value| !value.is_empty())
        {
            self.session = Some(session.to_string());
        }
        let status = response.status();
        if status.as_u16() == 202 || status.as_u16() == 204 {
            return Ok(None);
        }
        if !status.is_success() {
            return Err(());
        }
        let content_type = response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .unwrap_or("")
            .to_string();
        // Bounded body: the peer URL is configurable, so never buffer an
        // unbounded response (JSON or SSE) before parsing it.
        let mut body_bytes = Vec::new();
        response
            .take(MAX_RESPONSE as u64 + 1)
            .read_to_end(&mut body_bytes)
            .map_err(|_| ())?;
        if body_bytes.len() > MAX_RESPONSE {
            debug("response exceeds MAX_RESPONSE; refusing");
            return Err(());
        }
        let text = String::from_utf8(body_bytes).map_err(|_| ())?;
        if text.trim().is_empty() {
            return Ok(None);
        }
        let body = if content_type.contains("text/event-stream") {
            first_sse_json(&text).ok_or(())?
        } else {
            serde_json::from_str(&text).map_err(|_| ())?
        };
        if body.get("result").is_some() || body.get("error").is_some() {
            Ok(Some(body))
        } else {
            Ok(None)
        }
    }

    fn note_initialize(&mut self, reply: &Value) {
        if let Some(protocol) = reply
            .pointer("/result/protocolVersion")
            .and_then(Value::as_str)
            && SUPPORTED_PROTOCOLS.contains(&protocol)
        {
            self.protocol = protocol.to_string();
        }
    }
}

fn initialize_result(id: &Value, params: &Value) -> Value {
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
        "jsonrpc": "2.0",
        "id": id,
        "result": {
            "protocolVersion": protocol,
            "capabilities": {"tools": {"listChanged": false}},
            "serverInfo": {
                "name": "vc-mcp-shim",
                "version": env!("CARGO_PKG_VERSION"),
            },
            "instructions": "vc-server unreachable; tool list is empty until it returns."
        }
    })
}

fn ancestor_chain() -> (u32, String) {
    let self_pid = std::process::id();
    let mut system = sysinfo::System::new();
    system.refresh_processes(sysinfo::ProcessesToUpdate::All, true);
    let mut chain = Vec::new();
    let mut current = sysinfo::Pid::from_u32(self_pid);
    for _ in 0..MAX_ANCESTORS {
        let Some(process) = system.process(current) else {
            break;
        };
        let Some(parent) = process.parent() else {
            break;
        };
        let parent_pid = parent.as_u32();
        if parent_pid == 0 || parent_pid == self_pid || chain.contains(&parent_pid) {
            break;
        }
        chain.push(parent_pid);
        current = parent;
        if parent_pid == 1 {
            break;
        }
    }
    let header = chain
        .iter()
        .map(u32::to_string)
        .collect::<Vec<_>>()
        .join(",");
    (self_pid, header)
}

fn first_sse_json(body: &str) -> Option<Value> {
    for line in body.lines() {
        let Some(data) = line.trim().strip_prefix("data:") else {
            continue;
        };
        let data = data.trim();
        if data.is_empty() || data == "[DONE]" {
            continue;
        }
        if let Ok(value) = serde_json::from_str(data) {
            return Some(value);
        }
    }
    None
}

fn rpc_err(id: Value, code: i32, message: &str) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": {"code": code, "message": message}
    })
}

/// Discard the remainder of an oversized stdin line in bounded chunks.
///
/// Returns false when EOF arrives before the newline, so the caller can exit
/// instead of spinning on a closed pipe. At most 64 KiB is retained at a time.
fn drain_oversized_line(reader: &mut impl BufRead) -> bool {
    let mut chunk = Vec::new();
    loop {
        chunk.clear();
        match Read::by_ref(reader)
            .take(64 * 1024)
            .read_until(b'\n', &mut chunk)
        {
            Ok(0) => return false,
            Ok(_) => {
                if chunk.last() == Some(&b'\n') {
                    return true;
                }
            }
            Err(_) => return false,
        }
    }
}

fn rpc_err_data(id: Value, code: i32, message: &str, data: Value) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": {"code": code, "message": message, "data": data}
    })
}

fn write_stdout(value: &Value) {
    let mut stdout = std::io::stdout().lock();
    let _ = writeln!(stdout, "{value}");
    let _ = stdout.flush();
}

fn debug(message: &str) {
    if std::env::var("VC_MCP_SHIM_DEBUG").ok().as_deref() == Some("1") {
        eprintln!("vc-mcp-shim: {message}");
    }
}
