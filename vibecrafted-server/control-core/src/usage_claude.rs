//! Read-only analytical adapter for Claude provider-owned JSONL transcripts.
//!
//! Callers inject transcript paths, optional session/workspace identity, report
//! time, and pricing. The adapter does no home-directory discovery, networking,
//! or writes. Token buckets remain provider-reported; USD values are only
//! API-equivalent estimates derived from injected rates.

use std::collections::{BTreeMap, HashSet};
use std::fs::File;
use std::io::{self, BufRead, BufReader};
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

const MAX_TRANSCRIPT_LINE_BYTES: usize = 8 * 1024 * 1024;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ClaudeTranscriptInput {
    pub path: PathBuf,
    /// When supplied, events claiming another session are ignored.
    pub session_id: Option<String>,
    /// When supplied, only this workspace and its descendants are attributable.
    pub workspace: Option<PathBuf>,
    /// Fallback for transcript records that omit their model.
    pub model: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct ClaudePricing {
    pub models: BTreeMap<String, ClaudeModelRate>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ClaudeModelRate {
    pub fresh_input_per_million: f64,
    pub cache_write_per_million: f64,
    pub cache_read_per_million: f64,
    pub output_per_million: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ClaudeUsageReport {
    pub schema: String,
    pub generated_at: String,
    pub provider: String,
    pub slices: Vec<ClaudeUsageSlice>,
    pub diagnostics: ClaudeAdapterDiagnostics,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ClaudeUsageSlice {
    pub session_id: Option<String>,
    pub workspace: Option<String>,
    pub model: Option<String>,
    pub messages: u64,
    pub tokens: ClaudeTokenBuckets,
    pub cost: ClaudeEstimatedCost,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct ClaudeTokenBuckets {
    pub fresh_input: Option<u64>,
    pub cache_write: Option<u64>,
    pub cache_read: Option<u64>,
    pub output: Option<u64>,
    pub total: Option<u64>,
    pub source: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub unknown_reasons: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ClaudeEstimatedCost {
    pub amount: Option<f64>,
    pub currency: String,
    pub unit: String,
    pub source: String,
    pub method: String,
    pub evidence: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct ClaudeAdapterDiagnostics {
    pub files_read: usize,
    pub complete_lines: u64,
    pub malformed_lines: u64,
    pub oversized_lines: u64,
    pub partial_trailing_lines: u64,
    pub duplicate_messages: u64,
    pub ignored_non_usage_records: u64,
    pub foreign_session_records: u64,
    pub foreign_workspace_records: u64,
    pub invalid_usage_records: u64,
    pub file_errors: Vec<ClaudeFileError>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct ClaudeFileError {
    pub path: String,
    pub error: String,
}

type SliceKey = (Option<String>, Option<String>, Option<String>);
type SliceAggregates = BTreeMap<SliceKey, BucketAggregate>;

#[derive(Clone, Debug)]
struct BucketAggregate {
    messages: u64,
    fresh_input: u64,
    cache_write: u64,
    cache_read: u64,
    output: u64,
    fresh_known: bool,
    cache_write_known: bool,
    cache_read_known: bool,
    output_known: bool,
}

impl Default for BucketAggregate {
    fn default() -> Self {
        Self {
            messages: 0,
            fresh_input: 0,
            cache_write: 0,
            cache_read: 0,
            output: 0,
            fresh_known: true,
            cache_write_known: true,
            cache_read_known: true,
            output_known: true,
        }
    }
}

impl BucketAggregate {
    fn tokens(&self) -> ClaudeTokenBuckets {
        let mut unknown_reasons = Vec::new();
        if !self.fresh_known {
            unknown_reasons.push("missing input_tokens".to_string());
        }
        if !self.cache_write_known {
            unknown_reasons.push("missing cache_creation_input_tokens".to_string());
        }
        if !self.cache_read_known {
            unknown_reasons.push("missing cache_read_input_tokens".to_string());
        }
        if !self.output_known {
            unknown_reasons.push("missing output_tokens".to_string());
        }
        let fresh_input = self.fresh_known.then_some(self.fresh_input);
        let cache_write = self.cache_write_known.then_some(self.cache_write);
        let cache_read = self.cache_read_known.then_some(self.cache_read);
        let output = self.output_known.then_some(self.output);
        let total = match (fresh_input, cache_write, cache_read, output) {
            (Some(fresh), Some(write), Some(read), Some(output)) => Some(
                fresh
                    .saturating_add(write)
                    .saturating_add(read)
                    .saturating_add(output),
            ),
            _ => None,
        };
        ClaudeTokenBuckets {
            fresh_input,
            cache_write,
            cache_read,
            output,
            total,
            source: "provider_reported".to_string(),
            unknown_reasons,
        }
    }
}

/// Analyze injected Claude session or stream JSONL into attributable usage slices.
///
/// The provider session format stores usage under `message.usage`; Claude's
/// streaming result format stores it directly under `usage`. Complete records
/// are deduplicated by message/event identity, with a stable digest fallback.
pub fn analyze_claude_transcripts(
    inputs: &[ClaudeTranscriptInput],
    pricing: &ClaudePricing,
    generated_at: DateTime<Utc>,
) -> ClaudeUsageReport {
    let mut diagnostics = ClaudeAdapterDiagnostics::default();
    let mut seen = HashSet::new();
    let mut aggregates = SliceAggregates::new();

    for input in inputs {
        if let Err(error) = read_transcript(input, &mut diagnostics, &mut seen, &mut aggregates) {
            diagnostics.file_errors.push(ClaudeFileError {
                path: input.path.display().to_string(),
                error: error.to_string(),
            });
        } else {
            diagnostics.files_read += 1;
        }
    }

    let slices = aggregates
        .into_iter()
        .map(|((session_id, workspace, model), aggregate)| {
            let tokens = aggregate.tokens();
            let cost = estimated_cost(model.as_deref(), &tokens, pricing);
            ClaudeUsageSlice {
                session_id,
                workspace,
                model,
                messages: aggregate.messages,
                tokens,
                cost,
            }
        })
        .collect();

    ClaudeUsageReport {
        schema: "vibecrafted.usage-provider.claude.v1".to_string(),
        generated_at: generated_at.to_rfc3339(),
        provider: "claude".to_string(),
        slices,
        diagnostics,
    }
}

fn read_transcript(
    input: &ClaudeTranscriptInput,
    diagnostics: &mut ClaudeAdapterDiagnostics,
    seen: &mut HashSet<String>,
    aggregates: &mut SliceAggregates,
) -> io::Result<()> {
    let mut reader = BufReader::new(File::open(&input.path)?);
    loop {
        let mut line = Vec::new();
        let read = reader.read_until(b'\n', &mut line)?;
        if read == 0 {
            break;
        }
        if line.last() != Some(&b'\n') {
            diagnostics.partial_trailing_lines += 1;
            break;
        }
        diagnostics.complete_lines += 1;
        if line.len() > MAX_TRANSCRIPT_LINE_BYTES {
            diagnostics.oversized_lines += 1;
            continue;
        }
        while matches!(line.last(), Some(b'\n' | b'\r')) {
            line.pop();
        }
        let event: Value = match serde_json::from_slice(&line) {
            Ok(Value::Object(event)) => Value::Object(event),
            _ => {
                diagnostics.malformed_lines += 1;
                continue;
            }
        };
        consume_event(input, &event, &line, diagnostics, seen, aggregates);
    }
    Ok(())
}

fn consume_event(
    input: &ClaudeTranscriptInput,
    event: &Value,
    raw: &[u8],
    diagnostics: &mut ClaudeAdapterDiagnostics,
    seen: &mut HashSet<String>,
    aggregates: &mut SliceAggregates,
) {
    let Some((usage, message_id)) = usage_block(event) else {
        diagnostics.ignored_non_usage_records += 1;
        return;
    };
    let event_session = string_field(event, &["sessionId", "session_id"]);
    if input
        .session_id
        .as_deref()
        .is_some_and(|expected| event_session.as_deref() != Some(expected))
    {
        diagnostics.foreign_session_records += 1;
        return;
    }
    let session_id = input.session_id.clone().or(event_session);

    let event_workspace = string_field(event, &["cwd"]);
    if let Some(expected) = input.workspace.as_deref() {
        let attributable = event_workspace
            .as_deref()
            .map(Path::new)
            .is_some_and(|candidate| candidate.starts_with(expected));
        if !attributable {
            diagnostics.foreign_workspace_records += 1;
            return;
        }
    }
    let workspace = input
        .workspace
        .as_ref()
        .map(|path| path.display().to_string())
        .or(event_workspace);
    let model = input
        .model
        .as_deref()
        .map(normalize_claude_model)
        .or_else(|| event_model(event).map(|value| normalize_claude_model(&value)));
    let fields = match token_fields(usage) {
        Some(fields) => fields,
        None => {
            diagnostics.invalid_usage_records += 1;
            return;
        }
    };
    let key = dedupe_key(session_id.as_deref(), message_id.as_deref(), raw);
    if !seen.insert(key) {
        diagnostics.duplicate_messages += 1;
        return;
    }
    let aggregate = aggregates
        .entry((session_id, workspace, model))
        .or_default();
    aggregate.messages = aggregate.messages.saturating_add(1);
    accumulate(
        &mut aggregate.fresh_input,
        &mut aggregate.fresh_known,
        fields.fresh_input,
    );
    accumulate(
        &mut aggregate.cache_write,
        &mut aggregate.cache_write_known,
        fields.cache_write,
    );
    accumulate(
        &mut aggregate.cache_read,
        &mut aggregate.cache_read_known,
        fields.cache_read,
    );
    accumulate(
        &mut aggregate.output,
        &mut aggregate.output_known,
        fields.output,
    );
}

#[derive(Clone, Copy)]
struct TokenFields {
    fresh_input: Option<u64>,
    cache_write: Option<u64>,
    cache_read: Option<u64>,
    output: Option<u64>,
}

fn token_fields(usage: &Map<String, Value>) -> Option<TokenFields> {
    let fresh_input = optional_token(usage, "input_tokens")?;
    let cache_write = optional_token(usage, "cache_creation_input_tokens")?;
    let cache_read = optional_token(usage, "cache_read_input_tokens")?;
    let output = optional_token(usage, "output_tokens")?;
    if [fresh_input, cache_write, cache_read, output]
        .iter()
        .all(Option::is_none)
    {
        return None;
    }
    Some(TokenFields {
        fresh_input,
        cache_write,
        cache_read,
        output,
    })
}

fn optional_token(usage: &Map<String, Value>, key: &str) -> Option<Option<u64>> {
    match usage.get(key) {
        None | Some(Value::Null) => Some(None),
        Some(value) => value.as_u64().map(Some),
    }
}

fn usage_block(event: &Value) -> Option<(&Map<String, Value>, Option<String>)> {
    let object = event.as_object()?;
    if let Some(message) = object.get("message").and_then(Value::as_object)
        && let Some(usage) = message.get("usage").and_then(Value::as_object)
    {
        let id = message
            .get("id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_string);
        return Some((usage, id));
    }
    let usage = object.get("usage").and_then(Value::as_object)?;
    let id = string_field(event, &["id", "event_id", "request_id"]);
    Some((usage, id))
}

fn event_model(event: &Value) -> Option<String> {
    string_field(event, &["model", "model_id"]).or_else(|| {
        event
            .get("message")
            .and_then(Value::as_object)
            .and_then(|message| {
                ["model", "model_id"].iter().find_map(|key| {
                    message
                        .get(*key)
                        .and_then(Value::as_str)
                        .filter(|value| !value.is_empty())
                        .map(str::to_string)
                })
            })
    })
}

fn string_field(event: &Value, keys: &[&str]) -> Option<String> {
    keys.iter().find_map(|key| {
        event
            .get(*key)
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
    })
}

fn accumulate(total: &mut u64, known: &mut bool, value: Option<u64>) {
    if let Some(value) = value {
        *total = total.saturating_add(value);
    } else {
        *known = false;
    }
}

fn estimated_cost(
    model: Option<&str>,
    tokens: &ClaudeTokenBuckets,
    pricing: &ClaudePricing,
) -> ClaudeEstimatedCost {
    let method = "provider-reported token buckets multiplied by injected per-million API rates";
    let mut evidence = vec!["provider-reported Claude token buckets".to_string()];
    let unknown = |reason: &str, evidence: Vec<String>| ClaudeEstimatedCost {
        amount: None,
        currency: "USD".to_string(),
        unit: "api-equiv".to_string(),
        source: "unknown".to_string(),
        method: method.to_string(),
        evidence,
        reason: Some(reason.to_string()),
    };
    let Some(model) = model else {
        return unknown("model not recorded", evidence);
    };
    evidence.push(format!("normalized model: {model}"));
    let Some(rate) = pricing.models.get(model) else {
        return unknown("no injected price for normalized model", evidence);
    };
    let (Some(fresh), Some(write), Some(read), Some(output)) = (
        tokens.fresh_input,
        tokens.cache_write,
        tokens.cache_read,
        tokens.output,
    ) else {
        return unknown("provider token buckets incomplete", evidence);
    };
    let amount = (fresh as f64 * rate.fresh_input_per_million
        + write as f64 * rate.cache_write_per_million
        + read as f64 * rate.cache_read_per_million
        + output as f64 * rate.output_per_million)
        / 1_000_000.0;
    ClaudeEstimatedCost {
        amount: Some(round_six(amount)),
        currency: "USD".to_string(),
        unit: "api-equiv".to_string(),
        source: "estimated".to_string(),
        method: method.to_string(),
        evidence,
        reason: None,
    }
}

/// Normalize Claude aliases without collapsing distinct dated model ids.
pub fn normalize_claude_model(raw: &str) -> String {
    let clean = raw.trim().to_ascii_lowercase().replace([' ', '_'], "-");
    clean
        .strip_prefix("anthropic/")
        .or_else(|| clean.strip_prefix("anthropic:"))
        .unwrap_or(&clean)
        .to_string()
}

fn dedupe_key(session_id: Option<&str>, message_id: Option<&str>, raw: &[u8]) -> String {
    if let Some(message_id) = message_id {
        return format!("{}:message:{message_id}", session_id.unwrap_or("unknown"));
    }
    let mut digest = Sha256::new();
    digest.update(session_id.unwrap_or("unknown").as_bytes());
    digest.update([0]);
    digest.update(raw);
    format!("sha256:{:x}", digest.finalize())
}

fn round_six(value: f64) -> f64 {
    (value * 1_000_000.0).round() / 1_000_000.0
}
