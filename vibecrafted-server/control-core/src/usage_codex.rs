//! Read-only analytical adapter for Codex JSONL session and exec streams.
//!
//! Native Codex sessions publish cumulative `total_token_usage` snapshots,
//! while `codex exec --json` publishes per-turn usage. The adapter handles
//! both shapes without discovering `~/.codex`, writing provider state, or
//! contacting a pricing service. Paths, fallback identities, time and price
//! tables are supplied by the caller.

use std::collections::{BTreeMap, HashSet};
use std::fs::File;
use std::io::{self, BufRead, BufReader};
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

const MAX_LINE_BYTES: usize = 8 * 1024 * 1024;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CodexSessionInput {
    pub path: PathBuf,
    pub session_id: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct CodexPricing {
    pub models: BTreeMap<String, CodexModelRate>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CodexModelRate {
    pub fresh_input_per_million: f64,
    pub cache_read_per_million: f64,
    pub cache_write_per_million: f64,
    pub output_per_million: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct CodexUsageReport {
    pub schema: String,
    pub generated_at: String,
    pub provider: String,
    pub sessions: Vec<CodexSessionUsage>,
    pub diagnostics: CodexAdapterDiagnostics,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct CodexSessionUsage {
    pub session_id: Option<String>,
    pub model: Option<String>,
    pub usage_events: u64,
    pub tokens: CodexTokenBuckets,
    pub cost: CodexEstimatedCost,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct CodexTokenBuckets {
    pub fresh_input: Option<u64>,
    pub cache_read: Option<u64>,
    pub cache_write: Option<u64>,
    pub output: Option<u64>,
    pub reasoning_output: Option<u64>,
    pub total: Option<u64>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub unknown_reasons: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct CodexEstimatedCost {
    pub amount: Option<f64>,
    pub currency: String,
    pub unit: String,
    pub source: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct CodexAdapterDiagnostics {
    pub files_read: usize,
    pub complete_lines: u64,
    pub malformed_lines: u64,
    pub oversized_lines: u64,
    pub partial_trailing_lines: u64,
    pub duplicate_records: u64,
    pub cumulative_counter_resets: u64,
    pub file_errors: Vec<CodexFileError>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct CodexFileError {
    pub path: String,
    pub error: String,
}

#[derive(Clone, Debug, Default)]
struct Aggregate {
    session_id: Option<String>,
    model: Option<String>,
    events: u64,
    native_completed: CodexCounters,
    native_total: Option<CodexCounters>,
    exec_total: CodexCounters,
    saw_exec: bool,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct CodexCounters {
    input: u64,
    cached: u64,
    cache_write: u64,
    output: u64,
    reasoning: u64,
    total: u64,
}

/// Analyze explicitly supplied Codex JSONL logs.
pub fn analyze_codex_sessions(
    inputs: &[CodexSessionInput],
    pricing: &CodexPricing,
    generated_at: &str,
) -> CodexUsageReport {
    let mut diagnostics = CodexAdapterDiagnostics::default();
    let mut seen = HashSet::new();
    let mut sessions = Vec::new();

    for input in inputs {
        match read_session(input, &mut diagnostics, &mut seen) {
            Ok(aggregate) => {
                diagnostics.files_read += 1;
                sessions.push(finish(aggregate, pricing));
            }
            Err(error) => diagnostics.file_errors.push(CodexFileError {
                path: input.path.display().to_string(),
                error: error.to_string(),
            }),
        }
    }
    sessions.sort_by(|left, right| {
        left.session_id
            .cmp(&right.session_id)
            .then_with(|| left.model.cmp(&right.model))
    });
    CodexUsageReport {
        schema: "vibecrafted.usage-provider.codex.v1".to_string(),
        generated_at: generated_at.to_string(),
        provider: "codex".to_string(),
        sessions,
        diagnostics,
    }
}

fn read_session(
    input: &CodexSessionInput,
    diagnostics: &mut CodexAdapterDiagnostics,
    seen: &mut HashSet<String>,
) -> io::Result<Aggregate> {
    let file = File::open(&input.path)?;
    let mut reader = BufReader::new(file);
    let mut aggregate = Aggregate {
        session_id: input.session_id.clone(),
        ..Aggregate::default()
    };
    let mut buffer = Vec::new();
    loop {
        buffer.clear();
        let bytes = reader.read_until(b'\n', &mut buffer)?;
        if bytes == 0 {
            break;
        }
        let terminated = buffer.last() == Some(&b'\n');
        if !terminated {
            diagnostics.partial_trailing_lines += 1;
            break;
        }
        if buffer.len() > MAX_LINE_BYTES {
            diagnostics.oversized_lines += 1;
            continue;
        }
        diagnostics.complete_lines += 1;
        let Ok(event) = serde_json::from_slice::<Value>(&buffer) else {
            diagnostics.malformed_lines += 1;
            continue;
        };
        if is_usage_event(&event) {
            let digest = format!(
                "{}:{:x}",
                aggregate.session_id.as_deref().unwrap_or("unknown-session"),
                Sha256::digest(&buffer)
            );
            if !seen.insert(digest) {
                diagnostics.duplicate_records += 1;
                continue;
            }
        }
        consume_event(&event, &mut aggregate, diagnostics);
    }
    Ok(aggregate)
}

fn is_usage_event(event: &Value) -> bool {
    matches!(
        event.get("type").and_then(Value::as_str),
        Some("turn.completed" | "turn_completed")
    ) || (event.get("type").and_then(Value::as_str) == Some("event_msg")
        && event
            .get("payload")
            .and_then(|payload| text_ref(payload, "type"))
            == Some("token_count"))
}

fn consume_event(
    event: &Value,
    aggregate: &mut Aggregate,
    diagnostics: &mut CodexAdapterDiagnostics,
) {
    let event_type = event.get("type").and_then(Value::as_str).unwrap_or("");
    let payload = event.get("payload").unwrap_or(event);
    match event_type {
        "session_meta" => {
            if aggregate.session_id.is_none() {
                aggregate.session_id = text(payload, "id").or_else(|| text(payload, "session_id"));
            }
        }
        "turn_context" => aggregate.model = text(payload, "model").or(aggregate.model.take()),
        "event_msg" if text_ref(payload, "type") == Some("token_count") => {
            let Some(total) = payload
                .get("info")
                .and_then(|info| info.get("total_token_usage"))
                .and_then(counters)
            else {
                return;
            };
            if aggregate
                .native_total
                .is_some_and(|previous| total.total < previous.total)
            {
                diagnostics.cumulative_counter_resets += 1;
                if let Some(previous) = aggregate.native_total {
                    aggregate.native_completed.add(previous);
                }
            }
            aggregate.native_total = Some(total);
            aggregate.events += 1;
        }
        "thread.started" => {
            if aggregate.session_id.is_none() {
                aggregate.session_id = text(event, "thread_id");
            }
        }
        "turn.completed" | "turn_completed" => {
            if let Some(usage) = event.get("usage").and_then(counters) {
                aggregate.exec_total.add(usage);
                aggregate.saw_exec = true;
                aggregate.events += 1;
            }
        }
        _ => {}
    }
    if aggregate.model.is_none() {
        aggregate.model = text(event, "model");
    }
}

fn finish(aggregate: Aggregate, pricing: &CodexPricing) -> CodexSessionUsage {
    let counters = aggregate
        .native_total
        .map(|latest| {
            let mut combined = aggregate.native_completed;
            combined.add(latest);
            combined
        })
        .or(aggregate.saw_exec.then_some(aggregate.exec_total));
    let tokens = match counters {
        Some(value) => CodexTokenBuckets {
            fresh_input: Some(
                value
                    .input
                    .saturating_sub(value.cached)
                    .saturating_sub(value.cache_write),
            ),
            cache_read: Some(value.cached),
            cache_write: Some(value.cache_write),
            output: Some(value.output),
            reasoning_output: Some(value.reasoning),
            total: Some(value.total),
            unknown_reasons: Vec::new(),
        },
        None => CodexTokenBuckets {
            unknown_reasons: vec![
                "Codex log contained no token_count or turn.completed usage".into(),
            ],
            ..CodexTokenBuckets::default()
        },
    };
    let cost = estimate(aggregate.model.as_deref(), &tokens, pricing);
    CodexSessionUsage {
        session_id: aggregate.session_id,
        model: aggregate.model,
        usage_events: aggregate.events,
        tokens,
        cost,
    }
}

fn estimate(
    model: Option<&str>,
    tokens: &CodexTokenBuckets,
    pricing: &CodexPricing,
) -> CodexEstimatedCost {
    let Some(model) = model else {
        return unknown_cost("Codex log did not identify a model");
    };
    let Some(rate) = pricing.models.get(model) else {
        return unknown_cost("no injected price for the recorded Codex model");
    };
    let (Some(input), Some(cached), Some(cache_write), Some(output)) = (
        tokens.fresh_input,
        tokens.cache_read,
        tokens.cache_write,
        tokens.output,
    ) else {
        return unknown_cost("Codex token evidence is incomplete");
    };
    let amount = input as f64 * rate.fresh_input_per_million / 1_000_000.0
        + cached as f64 * rate.cache_read_per_million / 1_000_000.0
        + cache_write as f64 * rate.cache_write_per_million / 1_000_000.0
        + output as f64 * rate.output_per_million / 1_000_000.0;
    CodexEstimatedCost {
        amount: Some((amount * 1_000_000.0).round() / 1_000_000.0),
        currency: "USD".into(),
        unit: "api-equiv".into(),
        source: "estimated:caller-supplied-price-table".into(),
        reason: None,
    }
}

fn unknown_cost(reason: &str) -> CodexEstimatedCost {
    CodexEstimatedCost {
        amount: None,
        currency: "USD".into(),
        unit: "api-equiv".into(),
        source: "unknown".into(),
        reason: Some(reason.into()),
    }
}

fn counters(value: &Value) -> Option<CodexCounters> {
    let object = value.as_object()?;
    let input = number(object.get("input_tokens"))?;
    let cached = number(object.get("cached_input_tokens")).unwrap_or(0);
    let cache_write = number(object.get("cache_write_input_tokens")).unwrap_or(0);
    let output = number(object.get("output_tokens"))?;
    let reasoning = number(object.get("reasoning_output_tokens")).unwrap_or(0);
    let total = number(object.get("total_tokens")).unwrap_or_else(|| input.saturating_add(output));
    Some(CodexCounters {
        input,
        cached,
        cache_write,
        output,
        reasoning,
        total,
    })
}

impl CodexCounters {
    fn add(&mut self, other: Self) {
        self.input = self.input.saturating_add(other.input);
        self.cached = self.cached.saturating_add(other.cached);
        self.cache_write = self.cache_write.saturating_add(other.cache_write);
        self.output = self.output.saturating_add(other.output);
        self.reasoning = self.reasoning.saturating_add(other.reasoning);
        self.total = self.total.saturating_add(other.total);
    }
}

fn number(value: Option<&Value>) -> Option<u64> {
    value.and_then(|value| {
        value
            .as_u64()
            .or_else(|| value.as_str().and_then(|text| text.parse().ok()))
    })
}

fn text(value: &Value, key: &str) -> Option<String> {
    text_ref(value, key).map(str::to_string)
}

fn text_ref<'a>(value: &'a Value, key: &str) -> Option<&'a str> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|text| !text.is_empty())
}
