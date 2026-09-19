//! Per-run drill-down reader for `runtime_runs/<run_id>/`.
//!
//! The control-plane runtime writes each dispatched run into
//! `<state_root>/runtime_runs/<run_id>/` with `prompt.md`, `transcript.log`,
//! and — once the run finishes cleanly — `meta.json` + `report.md`. Mission
//! Control folds fleet-wide derived control-plane snapshots; this module
//! instead reads a *single* run's real artifacts so an operator can drill into
//! what actually happened in the inspector.
//!
//! Missing inputs degrade to typed-empty (no report, empty transcript tail) —
//! never a panic. This mirrors the empty-state discipline of
//! [`crate::mission_control::MissionControlState`].

use serde::Deserialize;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

/// Max bytes of `report.md` folded into memory. Reports are normally a few KB;
/// the cap protects the inspector from a pathologically large artifact.
const REPORT_BYTE_CAP: u64 = 200 * 1024;

/// Max bytes read from the *tail* of a transcript before line-slicing. The
/// transcript is JSONL and can reach megabytes; we only ever surface the tail,
/// so reading the whole file would be wasteful.
const TRANSCRIPT_TAIL_BYTE_CAP: u64 = 256 * 1024;

/// Number of trailing transcript lines surfaced to the inspector.
const TRANSCRIPT_TAIL_LINES: usize = 200;

/// Real artifacts for a single dispatched run, resolved from
/// `runtime_runs/<run_id>/`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RunDetail {
    pub run_id: String,
    pub agent: Option<String>,
    pub skill: Option<String>,
    /// The run's terminal/working state. Sourced from meta.json `state` when
    /// present, else `status` (the field the dispatch writer actually emits).
    pub state: Option<String>,
    pub report_md: Option<String>,
    pub transcript_tail: String,
    pub report_path: Option<String>,
    pub transcript_path: Option<String>,
}

/// The subset of `runtime_runs/<id>/meta.json` the inspector needs. This is
/// per-run dispatch meta, not the fleet stats corpus — Mission Control
/// reads derived control-plane snapshots, so this shape stays local.
#[derive(Debug, Default, Deserialize)]
struct RunMetaJson {
    #[serde(default)]
    agent: Option<String>,
    #[serde(default)]
    skill: Option<String>,
    #[serde(default)]
    status: Option<String>,
    #[serde(default)]
    state: Option<String>,
    #[serde(default)]
    report: Option<String>,
    #[serde(default)]
    transcript: Option<String>,
}

/// Resolve a run's drill-down artifacts under `<state_root>/runtime_runs/<run_id>`.
///
/// `state_root` is the control-plane root (e.g. `config::default_state_root()`
/// => `~/.vibecrafted/control_plane`). `run_id` is treated as a single path
/// component: any separator or parent reference collapses the result to
/// typed-empty so a hostile id cannot escape the `runtime_runs` tree.
#[must_use]
pub fn load_run_detail(state_root: &Path, run_id: &str) -> RunDetail {
    let mut detail = RunDetail {
        run_id: run_id.to_string(),
        ..Default::default()
    };

    if !is_safe_run_id(run_id) {
        return detail;
    }

    let run_dir = state_root.join("runtime_runs").join(run_id);

    // meta.json is optional — early dispatches and in-flight runs may carry
    // only transcript.log. When present it provides agent/skill/state plus the
    // canonical absolute artifact paths.
    let meta = read_meta(&run_dir.join("meta.json"));
    if let Some(meta) = meta.as_ref() {
        detail.agent = meta.agent.clone();
        detail.skill = meta.skill.clone();
        detail.state = meta.state.clone().or_else(|| meta.status.clone());
    }

    // report.md: prefer the meta-declared path, fall back to the conventional
    // sibling, and accept only an existing regular file.
    if let Some(path) = resolve_artifact(
        meta.as_ref().and_then(|m| m.report.as_deref()),
        &run_dir,
        "report.md",
    ) {
        detail.report_md = read_capped(&path, REPORT_BYTE_CAP);
        detail.report_path = Some(path.to_string_lossy().into_owned());
    }

    // Prefer the humanized projection written beside the raw JSONL log.
    let human = run_dir.join("transcript.human.log");
    if human.is_file() {
        detail.transcript_tail = humanize_transcript(&read_tail_lines(&human));
        detail.transcript_path = Some(human.to_string_lossy().into_owned());
    } else if let Some(path) = resolve_artifact(
        meta.as_ref().and_then(|m| m.transcript.as_deref()),
        &run_dir,
        "transcript.log",
    ) {
        detail.transcript_tail = humanize_transcript(&read_tail_lines(&path));
        detail.transcript_path = Some(path.to_string_lossy().into_owned());
    }

    detail
}

/// A run id must be a single, non-traversing path component. Reject anything
/// that could escape `runtime_runs/` or address a parent.
fn is_safe_run_id(run_id: &str) -> bool {
    !run_id.is_empty()
        && !run_id.contains('/')
        && !run_id.contains('\\')
        && !run_id.contains("..")
        && !run_id.contains('\0')
        && run_id != "."
}

/// Pick the artifact path: the meta-declared absolute path if it exists as a
/// regular file, else the conventional sibling under the run dir, else `None`.
fn resolve_artifact(declared: Option<&str>, run_dir: &Path, sibling: &str) -> Option<PathBuf> {
    if let Some(declared) = declared {
        let path = PathBuf::from(declared);
        if path.is_file() {
            return Some(path);
        }
    }
    let candidate = run_dir.join(sibling);
    candidate.is_file().then_some(candidate)
}

fn read_meta(path: &Path) -> Option<RunMetaJson> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

/// Read at most `cap` bytes from the start of a file as lossy UTF-8. Used for
/// `report.md`, which is small and read head-first.
fn read_capped(path: &Path, cap: u64) -> Option<String> {
    let file = File::open(path).ok()?;
    let mut buf = Vec::new();
    file.take(cap).read_to_end(&mut buf).ok()?;
    Some(String::from_utf8_lossy(&buf).into_owned())
}

/// Read the tail of a (possibly large) transcript and return the last
/// [`TRANSCRIPT_TAIL_LINES`] lines with ANSI escapes stripped. Reads at most
/// [`TRANSCRIPT_TAIL_BYTE_CAP`] bytes from the end so a megabyte JSONL log does
/// not get fully buffered. Returns an empty string on any read failure.
fn read_tail_lines(path: &Path) -> String {
    let Some(raw) = read_tail_bytes(path, TRANSCRIPT_TAIL_BYTE_CAP) else {
        return String::new();
    };
    let text = String::from_utf8_lossy(&raw);
    let mut lines: Vec<&str> = text.lines().collect();
    // When we truncated mid-file the first surviving line is likely a partial
    // record; drop it so the inspector never shows a sliced line.
    if raw.len() as u64 == TRANSCRIPT_TAIL_BYTE_CAP && lines.len() > 1 {
        lines.remove(0);
    }
    let start = lines.len().saturating_sub(TRANSCRIPT_TAIL_LINES);
    lines[start..]
        .iter()
        .map(|line| strip_ansi(line))
        .collect::<Vec<_>>()
        .join("\n")
}

/// Read up to `cap` bytes from the end of `path`.
fn read_tail_bytes(path: &Path, cap: u64) -> Option<Vec<u8>> {
    let mut file = File::open(path).ok()?;
    let len = file.metadata().ok()?.len();
    let start = len.saturating_sub(cap);
    if start > 0 {
        file.seek(SeekFrom::Start(start)).ok()?;
    }
    let mut buf = Vec::new();
    file.read_to_end(&mut buf).ok()?;
    Some(buf)
}

/// Project a raw control-plane transcript (JSONL, escaped shell, ANSI) into
/// wrapped operator-readable lines. Empty input stays empty so callers can
/// render an honest empty state.
///
/// Event shapes are mapped from `vibecrafted_core.agent_stream.AgentStreamParser`
/// (`_format_agy_event` and the shared message/text/content keys). This is
/// a display projection, not a second parser and not transcript execution.
pub fn humanize_transcript(text: &str) -> String {
    let mut lines = Vec::new();
    let mut pending = String::new();
    for raw in text.lines() {
        let cleaned = strip_ansi(raw).trim().to_string();
        if cleaned.is_empty() && pending.is_empty() {
            continue;
        }
        let candidate = if pending.is_empty() {
            cleaned.clone()
        } else if cleaned.is_empty() {
            pending.clone()
        } else {
            format!("{pending}\n{cleaned}")
        };
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(&candidate) {
            pending.clear();
            if let Some(human) = json_line_human(&value)
                && !human.is_empty()
            {
                lines.extend(wrap_human_line(&human, 88));
            }
            continue;
        }
        if looks_like_json_prefix(&candidate) {
            pending = candidate;
            if pending.len() > 64 * 1024 {
                lines.push(format!("[truncated event] {}", truncate_event(&pending)));
                pending.clear();
            }
            continue;
        }
        pending.clear();
        if let Some(unescaped) = unescape_json_string(&cleaned) {
            lines.extend(wrap_human_line(&unescaped, 88));
            continue;
        }
        lines.extend(wrap_human_line(&cleaned, 88));
    }
    if !pending.is_empty() {
        lines.push(format!("[truncated event] {}", truncate_event(&pending)));
    }
    lines.join("\n")
}

fn looks_like_json_prefix(value: &str) -> bool {
    let trimmed = value.trim_start();
    trimmed.starts_with('{') || trimmed.starts_with('[')
}

fn truncate_event(value: &str) -> String {
    let mut chars = value.chars();
    let prefix: String = chars.by_ref().take(240).collect();
    if chars.next().is_some() {
        format!("{prefix}…")
    } else {
        prefix
    }
}

fn json_line_human(value: &serde_json::Value) -> Option<String> {
    if let Some(human) = claude_row_human(value) {
        return Some(human);
    }
    if let Some(human) = format_agy_event(value) {
        return Some(human);
    }
    for key in ["message", "text", "content", "delta", "body"] {
        if let Some(text) = value.get(key).and_then(json_text) {
            let trimmed = text.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }
    }
    if let Some(kind) = value
        .get("kind")
        .or(value.get("type"))
        .and_then(serde_json::Value::as_str)
    {
        if let Some(message) = value.get("event").and_then(serde_json::Value::as_str) {
            return Some(format!("{kind}: {message}"));
        }
        if matches!(kind, "init" | "tools" | "step_update" | "result" | "error") {
            return format_agy_event_by_kind(kind, value);
        }
        // Dig one level into the payload named after the kind before giving up
        // on content (`{"type":"thought","thought":{"content":…}}`).
        for key in ["thought", "data", "payload"] {
            if let Some(text) = value
                .get(key)
                .and_then(json_text)
                .map(|text| text.trim().to_string())
                .filter(|text| !text.is_empty())
            {
                return Some(format!("{kind}: {text}"));
            }
        }
        // Recognized noise: an event whose only payload is its own kind label
        // earns no line (the operator reads content, not heartbeats).
        if matches!(kind, "thought" | "thinking" | "ping" | "pong" | "heartbeat") {
            return Some(String::new());
        }
        return Some(kind.to_string());
    }
    None
}

/// Claude-style conversational row: `{"type":"user|assistant|system", …,
/// "message":{"role":…, "content": …}}`, or a bare row carrying a `content`
/// array. Walks `content[]` blocks the way aicx-parser's claude adapter
/// classifies them (`crates/aicx-parser/src/adapters/claude.rs`): text turns,
/// thinking blocks (empty/signature-only skipped), `tool_use` with an input
/// preview, `tool_result` with extracted text. Returns `None` for
/// non-conversational shapes; `Some("")` marks recognized noise the caller
/// drops. Timestamps render as `[HH:MM]` prefixes when the row carries one.
fn claude_row_human(value: &serde_json::Value) -> Option<String> {
    let top_type = value.get("type").and_then(serde_json::Value::as_str);
    let message = value.get("message").and_then(serde_json::Value::as_object);
    let content = message
        .and_then(|msg| msg.get("content"))
        .or_else(|| value.get("content"));
    let rowish = message.is_some()
        || matches!(top_type, Some("user" | "assistant" | "system"))
        || matches!(content, Some(serde_json::Value::Array(_)));
    if !rowish {
        return None;
    }
    let role = message
        .and_then(|msg| msg.get("role"))
        .and_then(serde_json::Value::as_str)
        .or(top_type)
        .unwrap_or("event");
    let stamp = value
        .get("timestamp")
        .and_then(serde_json::Value::as_str)
        .and_then(|ts| ts.split_once('T').map(|(_, clock)| clock))
        .and_then(|clock| clock.get(..5))
        .map(|hhmm| format!("[{hhmm}] "))
        .unwrap_or_default();
    let mut out: Vec<String> = Vec::new();
    match content {
        Some(serde_json::Value::String(text)) => push_role_line(&mut out, &stamp, role, text),
        Some(serde_json::Value::Array(blocks)) => {
            for block in blocks {
                render_claude_block(&mut out, &stamp, role, block);
            }
        }
        _ => {}
    }
    Some(out.join("\n"))
}

fn push_role_line(out: &mut Vec<String>, stamp: &str, role: &str, text: &str) {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return;
    }
    if role == "user" && is_harness_noise(trimmed) {
        return;
    }
    out.push(format!("{stamp}{role}: {trimmed}"));
}

/// Head-anchored markers of harness-injected synthetic user turns, ported from
/// `aicx-parser`'s conversation projection (`HARNESS_HEAD_MARKERS`): slash
/// command echoes, inline `! command` I/O, and hook reminders are transport,
/// not conversation.
const HARNESS_NOISE_HEADS: [&str; 7] = [
    "<command-message>",
    "<command-name>",
    "<local-command-caveat>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
    "<system-reminder>",
];

fn is_harness_noise(text: &str) -> bool {
    HARNESS_NOISE_HEADS
        .iter()
        .any(|marker| text.starts_with(marker))
}

fn render_claude_block(out: &mut Vec<String>, stamp: &str, role: &str, block: &serde_json::Value) {
    if let serde_json::Value::String(text) = block {
        push_role_line(out, stamp, role, text);
        return;
    }
    let Some(object) = block.as_object() else {
        return;
    };
    let kind = object
        .get("type")
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default();
    match kind {
        "text" => {
            if let Some(text) = object.get("text").and_then(serde_json::Value::as_str) {
                push_role_line(out, stamp, role, text);
            }
        }
        // Signature-only thinking (`{"thinking":"","signature":…}`) is a known
        // block with no body — consumed silently, exactly like aicx does.
        "thinking" => {
            if let Some(text) = object
                .get("thinking")
                .and_then(serde_json::Value::as_str)
                .map(str::trim)
                .filter(|text| !text.is_empty())
            {
                out.push(format!("{stamp}thinking: {}", clip(text, 240)));
            }
        }
        "tool_use" => {
            let name = object
                .get("name")
                .and_then(serde_json::Value::as_str)
                .filter(|name| !name.is_empty())
                .unwrap_or("tool");
            out.push(format!(
                "{stamp}[tool] {name}: {}",
                tool_input_preview(object.get("input"))
            ));
        }
        "tool_result" => {
            let text = tool_result_preview(object.get("content"));
            if !text.is_empty() {
                out.push(format!("{stamp}[result] {text}"));
            }
        }
        _ => {}
    }
}

/// One-line command/path preview of a tool call's input: the recognizable key
/// (`command`, `file_path`, …) wins over a compact JSON rendering.
fn tool_input_preview(input: Option<&serde_json::Value>) -> String {
    let Some(input) = input else {
        return String::new();
    };
    let text = match input {
        serde_json::Value::String(text) => text.clone(),
        serde_json::Value::Object(map) => {
            let mut picked = None;
            for key in [
                "command",
                "file_path",
                "path",
                "pattern",
                "query",
                "prompt",
                "url",
            ] {
                if let Some(value) = map.get(key).and_then(serde_json::Value::as_str) {
                    picked = Some(value.to_string());
                    break;
                }
            }
            picked.unwrap_or_else(|| serde_json::to_string(input).unwrap_or_default())
        }
        other => other.to_string(),
    };
    clip(&text, 110)
}

/// Text of a tool result: plain string, or the joined `text` blocks of a
/// content array (mirrors aicx `tool_result_text`, trimmed to one preview).
fn tool_result_preview(content: Option<&serde_json::Value>) -> String {
    let text = match content {
        Some(serde_json::Value::String(text)) => text.clone(),
        Some(serde_json::Value::Array(blocks)) => blocks
            .iter()
            .filter_map(|block| match block {
                serde_json::Value::String(text) => Some(text.trim()),
                serde_json::Value::Object(object) => object
                    .get("text")
                    .and_then(serde_json::Value::as_str)
                    .map(str::trim),
                _ => None,
            })
            .filter(|text| !text.is_empty())
            .collect::<Vec<_>>()
            .join(" · "),
        _ => String::new(),
    };
    clip(&text, 160)
}

/// Collapse whitespace and truncate to `max` chars with an ellipsis tail.
fn clip(text: &str, max: usize) -> String {
    let collapsed = text.split_whitespace().collect::<Vec<_>>().join(" ");
    let mut chars = collapsed.chars();
    let prefix: String = chars.by_ref().take(max).collect();
    if chars.next().is_some() {
        format!("{prefix}…")
    } else {
        prefix
    }
}

/// Map `AgentStreamParser._format_agy_event`: `{"event": ..., "<event>": {...}}`
/// plus older `type` discriminators. Empty means "recognized noise", not raw JSON.
fn format_agy_event(value: &serde_json::Value) -> Option<String> {
    let kind = value
        .get("event")
        .and_then(serde_json::Value::as_str)
        .or_else(|| value.get("type").and_then(serde_json::Value::as_str))?;
    if !matches!(
        kind,
        "init" | "tools" | "step_update" | "result" | "error" | "tool" | "tool_use"
    ) {
        return None;
    }
    format_agy_event_by_kind(kind, value)
}

fn format_agy_event_by_kind(kind: &str, value: &serde_json::Value) -> Option<String> {
    match kind {
        "init" => {
            let init = value.get("init").and_then(serde_json::Value::as_object);
            let session = init
                .and_then(|obj| obj.get("conversation_id"))
                .or_else(|| value.get("conversation_id"))
                .or_else(|| value.get("session_id"))
                .and_then(serde_json::Value::as_str)
                .filter(|value| !value.is_empty())
                .unwrap_or("?");
            let model = init
                .and_then(|obj| obj.get("model"))
                .or_else(|| value.get("model"))
                .and_then(serde_json::Value::as_str)
                .filter(|value| !value.is_empty());
            Some(match model {
                Some(model) => format!("session: {session} model: {model}"),
                None => format!("session: {session}"),
            })
        }
        "tools" | "tool" | "tool_use" => Some(format_tools_event(value)),
        "step_update" => Some(format_step_update(value)),
        "result" => {
            let result = value.get("result").unwrap_or(value);
            let status = value_text(result, "status").unwrap_or_else(|| "done".to_string());
            if let Some(error) = value_text(result, "error").filter(|text| text != "None") {
                return Some(format!("error: {error}"));
            }
            if let Some(response) = value_text(result, "response").filter(|text| text != "None") {
                return Some(response);
            }
            Some(status)
        }
        "error" => {
            let message = json_text(value.get("error").unwrap_or(&serde_json::Value::Null))
                .or_else(|| json_text(value.get("message").unwrap_or(&serde_json::Value::Null)))
                .unwrap_or_else(|| "unknown".to_string());
            Some(format!("error: {message}"))
        }
        _ => None,
    }
}

fn format_tools_event(value: &serde_json::Value) -> String {
    let mut names = Vec::new();
    collect_tool_names(value.get("tools"), &mut names);
    collect_tool_names(value.get("tool_calls"), &mut names);
    if let Some(name) = value_text(value, "name").or_else(|| value_text(value, "tool_name")) {
        names.push(name);
    }
    names.retain(|name| !name.is_empty());
    names.dedup();
    if names.is_empty() {
        "[tool]".to_string()
    } else {
        format!("[tool] {}", names.join(", "))
    }
}

fn collect_tool_names(value: Option<&serde_json::Value>, names: &mut Vec<String>) {
    let Some(value) = value else { return };
    match value {
        serde_json::Value::Array(items) => {
            for item in items {
                if let Some(name) = value_text(item, "name")
                    .or_else(|| value_text(item, "tool_name"))
                    .or_else(|| {
                        item.get("function")
                            .and_then(|function| value_text(function, "name"))
                    })
                {
                    names.push(name);
                } else if let Some(text) = item.as_str() {
                    names.push(text.to_string());
                }
            }
        }
        serde_json::Value::Object(_) => {
            if let Some(name) = value_text(value, "name").or_else(|| value_text(value, "tool_name"))
            {
                names.push(name);
            }
        }
        serde_json::Value::String(name) => names.push(name.clone()),
        _ => {}
    }
}

fn format_step_update(value: &serde_json::Value) -> String {
    let step = value.get("step_update").unwrap_or(value);
    let step_type = value_text(step, "step_type").unwrap_or_default();
    let text = value_text(step, "text_delta")
        .or_else(|| value_text(step, "text"))
        .or_else(|| value_text(step, "content"))
        .unwrap_or_default();
    if step_type == "user_input" {
        return if text.is_empty() {
            String::new()
        } else {
            format!("user: {text}")
        };
    }
    if step_type == "agent_response" {
        return if text.is_empty() {
            String::new()
        } else {
            format!("assistant: {text}")
        };
    }
    if matches!(step_type.as_str(), "thinking" | "thought" | "planning") {
        return if text.is_empty() {
            String::new()
        } else {
            format!("thinking: {text}")
        };
    }
    let state = value_text(step, "state").unwrap_or_default();
    if state == "ACTIVE" && text.is_empty() {
        let name = value_text(step, "tool_name")
            .or_else(|| value_text(step, "name"))
            .unwrap_or(step_type);
        return format!("[tool] {name}");
    }
    text
}

fn value_text(value: &serde_json::Value, key: &str) -> Option<String> {
    json_text(value.get(key)?)
}

fn json_text(value: &serde_json::Value) -> Option<String> {
    match value {
        serde_json::Value::Null => None,
        serde_json::Value::String(text) => Some(text.clone()),
        serde_json::Value::Number(number) => Some(number.to_string()),
        serde_json::Value::Bool(flag) => Some(flag.to_string()),
        serde_json::Value::Object(map) => {
            for key in ["message", "error", "detail", "text", "content"] {
                if let Some(inner) = map.get(key)
                    && let Some(text) = json_text(inner)
                    && !text.is_empty()
                {
                    return Some(text);
                }
            }
            None
        }
        serde_json::Value::Array(_) => None,
    }
}

fn unescape_json_string(raw: &str) -> Option<String> {
    if !(raw.contains("\\n") || raw.contains("\\\"")) {
        return None;
    }
    let wrapped = format!("\"{raw}\"");
    serde_json::from_str::<String>(&wrapped).ok()
}

fn wrap_human_line(value: &str, width: usize) -> Vec<String> {
    let mut lines = Vec::new();
    for segment in value.split('\n') {
        let mut rest = segment;
        loop {
            if rest.chars().count() <= width {
                lines.push(rest.to_string());
                break;
            }
            // Word boundary when one exists inside the window; hard cut only
            // for unbroken runs longer than the width.
            let window: String = rest.chars().take(width).collect();
            let cut = window
                .rfind(char::is_whitespace)
                .filter(|index| *index > 0)
                .unwrap_or(window.len());
            lines.push(window[..cut].trim_end().to_string());
            rest = rest[cut..].trim_start();
        }
    }
    lines
}

/// Strip ANSI/VT control sequences (CSI `ESC [ … final` and OSC `ESC ] … BEL/ST`)
/// so a terminal-captured transcript renders cleanly in a plain text view.
fn strip_ansi(input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    let mut chars = input.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '\u{1b}' {
            out.push(c);
            continue;
        }
        match chars.peek() {
            Some('[') => {
                chars.next();
                // CSI: consume until a final byte in 0x40..=0x7E.
                while let Some(&next) = chars.peek() {
                    chars.next();
                    if ('\u{40}'..='\u{7e}').contains(&next) {
                        break;
                    }
                }
            }
            Some(']') => {
                chars.next();
                // OSC: consume until BEL or the ST terminator (ESC \).
                while let Some(&next) = chars.peek() {
                    if next == '\u{07}' {
                        chars.next();
                        break;
                    }
                    if next == '\u{1b}' {
                        chars.next();
                        if chars.peek() == Some(&'\\') {
                            chars.next();
                        }
                        break;
                    }
                    chars.next();
                }
            }
            // Lone ESC or a 2-byte escape we do not model: drop the ESC only.
            _ => {}
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    fn run_dir(state_root: &Path, run_id: &str) -> PathBuf {
        let dir = state_root.join("runtime_runs").join(run_id);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn reads_meta_report_and_transcript_tail_with_meta_paths() {
        let root = tempdir().unwrap();
        let dir = run_dir(root.path(), "impl-1");
        let report = dir.join("report.md");
        let transcript = dir.join("transcript.log");
        fs::write(&report, "# Done\nreal report body").unwrap();
        fs::write(&transcript, "line one\n\u{1b}[32mline two\u{1b}[0m\n").unwrap();
        fs::write(
            dir.join("meta.json"),
            serde_json::json!({
                "agent": "codex",
                "skill": "implement",
                "status": "completed",
                "report": report.to_string_lossy(),
                "transcript": transcript.to_string_lossy(),
            })
            .to_string(),
        )
        .unwrap();

        let detail = load_run_detail(root.path(), "impl-1");

        assert_eq!(detail.run_id, "impl-1");
        assert_eq!(detail.agent.as_deref(), Some("codex"));
        assert_eq!(detail.skill.as_deref(), Some("implement"));
        assert_eq!(detail.state.as_deref(), Some("completed"));
        assert_eq!(
            detail.report_md.as_deref(),
            Some("# Done\nreal report body")
        );
        assert_eq!(
            detail.report_path.as_deref(),
            Some(&*report.to_string_lossy())
        );
        // ANSI stripped, both lines present.
        assert_eq!(detail.transcript_tail, "line one\nline two");
        assert_eq!(
            detail.transcript_path.as_deref(),
            Some(&*transcript.to_string_lossy())
        );
    }

    #[test]
    fn humanize_transcript_projects_jsonl_and_wraps_long_commands() {
        let raw = concat!(
            r#"{"kind":"tool","message":"cargo test -p voc"}"#,
            "\n",
            r#"{"text":"ok"}"#,
            "\n",
            r#"echo \"/very/long/path/to/a/workspace/and/then/some/more/segments/that/should/wrap\""#,
            "\n"
        );
        let human = humanize_transcript(raw);
        assert!(human.contains("cargo test -p voc"));
        assert!(human.contains("ok"));
        assert!(!human.contains("{\"kind\""));
        assert!(human.lines().any(|line| line.chars().count() <= 88));
    }

    #[test]
    fn humanize_transcript_maps_agy_events_and_keeps_unicode() {
        let raw = concat!(
            r#"{"event":"init","init":{"conversation_id":"conv-1","model":"gemini-pro"}}"#,
            "\n",
            r#"{"event":"tools","tools":[{"name":"read_file"}]}"#,
            "\n",
            r#"{"event":"step_update","step_update":{"step_type":"user_input","text_delta":"cześć"}}"#,
            "\n",
            r#"{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"żółć"}}"#,
            "\n",
            r#"{"type":"init","session_id":"old-format"}"#,
            "\n"
        );
        let human = humanize_transcript(raw);
        assert!(human.contains("session: conv-1 model: gemini-pro"));
        assert!(human.contains("[tool] read_file"));
        assert!(human.contains("user: cześć"));
        assert!(human.contains("assistant: żółć"));
        assert!(human.contains("session: old-format"));
        assert!(!human.contains("\"event\":\"init\""));
        assert!(!human.contains("step_update"));
    }

    #[test]
    fn humanize_transcript_keeps_malformed_and_truncated_events_honest() {
        let raw = "{\n  \"event\": \"init\",\n  \"init\": {\"conversation_id\": \"open\"\n";
        let human = humanize_transcript(raw);
        assert!(human.contains("[truncated event]"));
        assert!(human.contains("\"event\": \"init\""));
        let complete = "not json at all\n{\"event\":\"result\",\"result\":{\"status\":\"SUCCESS\",\"response\":\"ok\"}}\n";
        let human = humanize_transcript(raw);
        let done = humanize_transcript(complete);
        assert!(done.contains("ok"));
        assert!(done.contains("not json at all"));
        assert!(!done.contains("\"event\":\"result\""));
        let _ = human;
    }

    #[test]
    fn humanize_transcript_walks_claude_content_blocks() {
        let raw = concat!(
            r#"{"type":"user","timestamp":"2026-09-19T10:12:37.000Z","message":{"role":"user","content":[{"type":"text","text":"napraw transkrypt"}]}}"#,
            "\n",
            r#"{"type":"assistant","timestamp":"2026-09-19T10:12:41.000Z","message":{"role":"assistant","content":[{"type":"thinking","thinking":""},{"type":"text","text":"patrzę w kod"},{"type":"tool_use","name":"Bash","input":{"command":"loct find render_line"}},{"type":"tool_use","name":"Read","input":{"file_path":"/tmp/x.rs"}}]}}"#,
            "\n",
            r#"{"type":"user","timestamp":"2026-09-19T10:12:42.000Z","message":{"role":"user","content":[{"type":"tool_result","content":[{"type":"text","text":"found 3"}]}]}}"#,
            "\n",
            r#"{"type":"system","subtype":"hook","content":null}"#,
            "\n",
            r#"{"type":"user","message":{"role":"user","content":"<command-message>vc-init</command-message>"}}"#,
            "\n"
        );
        let human = humanize_transcript(raw);
        assert!(human.contains("[10:12] user: napraw transkrypt"));
        assert!(human.contains("[10:12] assistant: patrzę w kod"));
        assert!(human.contains("[tool] Bash: loct find render_line"));
        assert!(human.contains("[tool] Read: /tmp/x.rs"));
        assert!(human.contains("[result] found 3"));
        // Signature-only thinking, content-less system rows, and harness
        // noise earn no line — the pane shows content, not scaffolding.
        assert!(!human.contains("thinking"));
        assert!(!human.contains("system"));
        assert!(!human.contains("command-message"));
        assert!(
            !human.lines().any(|line| line.trim() == "user"),
            "bare role label leaked into the human transcript"
        );
    }

    #[test]
    fn humanize_transcript_skips_empty_thoughts_and_keeps_unknown_kinds_honest() {
        let raw = concat!(
            r#"{"type":"thought"}"#,
            "\n",
            r#"{"type":"thought","thought":{"content":"kontekst rośnie"}}"#,
            "\n",
            r#"{"kind":"custom_event"}"#,
            "\n"
        );
        let human = humanize_transcript(raw);
        assert!(
            !human.lines().any(|line| line.trim() == "thought"),
            "content-less thought earned a line: {human:?}"
        );
        assert!(human.contains("thought: kontekst rośnie"));
        assert!(human.contains("custom_event"));
    }

    #[test]
    fn wrap_human_line_breaks_on_word_boundaries() {
        let wrapped = wrap_human_line("ala ma kota a kot ma ale i coś jeszcze długiego", 20);
        assert!(wrapped.iter().all(|line| line.chars().count() <= 20));
        assert_eq!(
            wrapped.first().map(String::as_str),
            Some("ala ma kota a kot")
        );
        assert_eq!(
            wrapped.join(" "),
            "ala ma kota a kot ma ale i coś jeszcze długiego"
        );
        // Unbroken runs still hard-cut at the width.
        let hard = wrap_human_line(&"x".repeat(50), 20);
        assert_eq!(
            hard.iter().map(String::len).collect::<Vec<_>>(),
            vec![20, 20, 10]
        );
        // Embedded newlines become separate lines.
        assert_eq!(
            wrap_human_line("a\nb", 88),
            vec!["a".to_string(), "b".to_string()]
        );
    }

    #[test]
    fn state_prefers_explicit_state_over_status() {
        let root = tempdir().unwrap();
        let dir = run_dir(root.path(), "impl-2");
        fs::write(
            dir.join("meta.json"),
            serde_json::json!({"state": "stalled", "status": "running"}).to_string(),
        )
        .unwrap();

        let detail = load_run_detail(root.path(), "impl-2");
        assert_eq!(detail.state.as_deref(), Some("stalled"));
    }

    #[test]
    fn falls_back_to_sibling_files_without_meta() {
        let root = tempdir().unwrap();
        let dir = run_dir(root.path(), "impl-3");
        fs::write(dir.join("transcript.log"), "only a transcript\n").unwrap();
        fs::write(dir.join("report.md"), "sibling report").unwrap();

        let detail = load_run_detail(root.path(), "impl-3");
        assert_eq!(detail.agent, None);
        assert_eq!(detail.report_md.as_deref(), Some("sibling report"));
        assert!(detail.report_path.is_some());
        assert_eq!(detail.transcript_tail, "only a transcript");
    }

    #[test]
    fn missing_run_is_typed_empty_not_a_panic() {
        let root = tempdir().unwrap();
        let detail = load_run_detail(root.path(), "does-not-exist");
        assert_eq!(detail.run_id, "does-not-exist");
        assert_eq!(detail.agent, None);
        assert_eq!(detail.report_md, None);
        assert_eq!(detail.report_path, None);
        assert!(detail.transcript_tail.is_empty());
        assert_eq!(detail.transcript_path, None);
    }

    #[test]
    fn rejects_traversing_run_ids() {
        let root = tempdir().unwrap();
        // Plant a file the traversal would try to reach.
        fs::write(root.path().join("secret"), "nope").unwrap();
        for hostile in ["../secret", "..", "a/b", "", "."] {
            let detail = load_run_detail(root.path(), hostile);
            assert!(detail.report_md.is_none(), "leaked for {hostile:?}");
            assert!(detail.transcript_tail.is_empty(), "leaked for {hostile:?}");
        }
    }

    #[test]
    fn transcript_tail_caps_line_count() {
        let root = tempdir().unwrap();
        let dir = run_dir(root.path(), "impl-4");
        let body: String = (0..500).map(|i| format!("line {i}\n")).collect();
        fs::write(dir.join("transcript.log"), body).unwrap();

        let detail = load_run_detail(root.path(), "impl-4");
        let lines: Vec<&str> = detail.transcript_tail.lines().collect();
        assert_eq!(lines.len(), TRANSCRIPT_TAIL_LINES);
        assert_eq!(*lines.last().unwrap(), "line 499");
    }

    #[test]
    fn strip_ansi_removes_csi_and_osc() {
        assert_eq!(strip_ansi("\u{1b}[1;31mred\u{1b}[0m"), "red");
        assert_eq!(strip_ansi("\u{1b}]0;title\u{07}body"), "body");
        assert_eq!(strip_ansi("plain"), "plain");
    }
}
