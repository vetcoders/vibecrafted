//! Read-only analytical adapter for Cursor Agent `stream-json` transcripts.
//!
//! Cursor's repository-backed contract is a Claude-shaped JSONL stream: an
//! init event carries session/model identity and result events may carry exact
//! token buckets. Paths, identity fallbacks, report time, and prices are all
//! caller supplied. This module performs no provider-home discovery, writes,
//! networking, or process management.

use std::collections::{BTreeMap, HashSet};
use std::io::{self, BufRead, BufReader};
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

const MAX_JSONL_LINE_BYTES: usize = 1024 * 1024;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CursorTranscriptInput {
    pub path: PathBuf,
    pub session_id: Option<String>,
    pub model: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct CursorPricing {
    pub models: BTreeMap<String, CursorModelRate>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct CursorModelRate {
    pub fresh_input_per_million: f64,
    pub cache_read_per_million: f64,
    pub output_per_million: f64,
    /// A stable, caller-owned label such as `openai-api-2026-07`.
    pub source: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct CursorUsageReport {
    pub schema: String,
    pub generated_at: String,
    pub provider: String,
    pub slices: Vec<CursorUsageSlice>,
    pub diagnostics: CursorAdapterDiagnostics,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct CursorUsageSlice {
    pub session_id: Option<String>,
    pub model: Option<String>,
    pub usage_events: u64,
    pub tokens: CursorTokenBuckets,
    pub cost: CursorCost,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct CursorTokenBuckets {
    /// Provider `inputTokens`, which includes cache hits for Cursor's
    /// Claude-shaped result contract.
    pub input_total: Option<u64>,
    /// Derived as `input_total - cache_read` when the evidence is consistent.
    pub fresh_input: Option<u64>,
    pub cache_read: Option<u64>,
    pub cache_write: Option<u64>,
    pub output: Option<u64>,
    pub total: Option<u64>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub unknown_reasons: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct CursorCost {
    pub amount: Option<f64>,
    pub currency: String,
    pub source: String,
    pub basis: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct CursorAdapterDiagnostics {
    pub files_read: usize,
    pub complete_lines: u64,
    pub malformed_lines: u64,
    pub oversized_lines: u64,
    pub partial_trailing_lines: u64,
    pub duplicate_results: u64,
    pub ignored_non_result_events: u64,
    pub file_errors: Vec<CursorFileError>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct CursorFileError {
    pub path: String,
    pub error: String,
}

#[derive(Clone, Debug)]
struct Aggregate {
    events: u64,
    input_total: u64,
    cache_read: u64,
    cache_write: u64,
    output: u64,
    input_known: bool,
    cache_read_known: bool,
    cache_write_known: bool,
    output_known: bool,
    reported_cost: f64,
    reported_cost_complete: bool,
}

impl Default for Aggregate {
    fn default() -> Self {
        Self {
            events: 0,
            input_total: 0,
            cache_read: 0,
            cache_write: 0,
            output: 0,
            input_known: true,
            cache_read_known: true,
            cache_write_known: true,
            output_known: true,
            reported_cost: 0.0,
            reported_cost_complete: true,
        }
    }
}

#[derive(Clone, Debug, Default)]
struct StreamIdentity {
    session_id: Option<String>,
    model: Option<String>,
}

/// Analyze explicitly supplied Cursor stream transcripts.
///
/// Complete duplicate result events are counted once across all supplied
/// files. The final unterminated JSONL fragment is ignored so a concurrently
/// written transcript cannot become a false malformed event.
pub fn analyze_cursor_transcripts(
    inputs: &[CursorTranscriptInput],
    pricing: &CursorPricing,
    generated_at: &str,
) -> CursorUsageReport {
    let mut diagnostics = CursorAdapterDiagnostics::default();
    let mut seen = HashSet::new();
    let mut aggregates: BTreeMap<(Option<String>, Option<String>), Aggregate> = BTreeMap::new();

    for input in inputs {
        if let Err(error) = read_transcript(input, &mut diagnostics, &mut seen, &mut aggregates) {
            diagnostics.file_errors.push(CursorFileError {
                path: input.path.display().to_string(),
                error: error.to_string(),
            });
        } else {
            diagnostics.files_read += 1;
        }
    }

    let slices = aggregates
        .into_iter()
        .map(|((session_id, model), aggregate)| {
            let tokens = aggregate.tokens();
            let cost = aggregate.cost(model.as_deref(), &tokens, pricing);
            CursorUsageSlice {
                session_id,
                model,
                usage_events: aggregate.events,
                tokens,
                cost,
            }
        })
        .collect();

    CursorUsageReport {
        schema: "vibecrafted.usage-provider.cursor.v1".to_string(),
        generated_at: generated_at.to_string(),
        provider: "cursor".to_string(),
        slices,
        diagnostics,
    }
}

fn read_transcript(
    input: &CursorTranscriptInput,
    diagnostics: &mut CursorAdapterDiagnostics,
    seen: &mut HashSet<String>,
    aggregates: &mut BTreeMap<(Option<String>, Option<String>), Aggregate>,
) -> io::Result<()> {
    let mut reader = BufReader::new(crate::transcript_open::open_provider_transcript(
        &input.path,
    )?);
    let mut identity = StreamIdentity {
        session_id: input.session_id.clone(),
        model: clean(input.model.as_deref()),
    };
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
        if line.len() > MAX_JSONL_LINE_BYTES {
            diagnostics.oversized_lines += 1;
            continue;
        }
        while matches!(line.last(), Some(b'\n' | b'\r')) {
            line.pop();
        }
        let event: Value = match serde_json::from_slice(&line) {
            Ok(event) => event,
            Err(_) => {
                diagnostics.malformed_lines += 1;
                continue;
            }
        };
        let Some(event) = event.as_object() else {
            diagnostics.malformed_lines += 1;
            continue;
        };
        update_identity(event, &mut identity);
        if text(event, "type") != Some("result") {
            diagnostics.ignored_non_result_events += 1;
            continue;
        }
        let Some(usage) = event.get("usage").and_then(Value::as_object) else {
            continue;
        };
        let key = result_key(event, &identity);
        if !seen.insert(key) {
            diagnostics.duplicate_results += 1;
            continue;
        }

        let aggregate = aggregates
            .entry((identity.session_id.clone(), identity.model.clone()))
            .or_default();
        aggregate.events = aggregate.events.saturating_add(1);
        accumulate(
            usage,
            "inputTokens",
            &mut aggregate.input_total,
            &mut aggregate.input_known,
        );
        accumulate(
            usage,
            "cacheReadTokens",
            &mut aggregate.cache_read,
            &mut aggregate.cache_read_known,
        );
        accumulate(
            usage,
            "cacheWriteTokens",
            &mut aggregate.cache_write,
            &mut aggregate.cache_write_known,
        );
        accumulate(
            usage,
            "outputTokens",
            &mut aggregate.output,
            &mut aggregate.output_known,
        );
        if let Some(cost) = reported_cost(event) {
            aggregate.reported_cost += cost;
        } else {
            aggregate.reported_cost_complete = false;
        }
    }
    Ok(())
}

impl Aggregate {
    fn tokens(&self) -> CursorTokenBuckets {
        let mut reasons = Vec::new();
        let input_total = known(
            self.input_known,
            self.input_total,
            "inputTokens",
            &mut reasons,
        );
        let cache_read = known(
            self.cache_read_known,
            self.cache_read,
            "cacheReadTokens",
            &mut reasons,
        );
        let cache_write = known(
            self.cache_write_known,
            self.cache_write,
            "cacheWriteTokens",
            &mut reasons,
        );
        let output = known(self.output_known, self.output, "outputTokens", &mut reasons);
        let fresh_input = match (input_total, cache_read) {
            (Some(input), Some(cached)) if cached <= input => Some(input - cached),
            (Some(_), Some(_)) => {
                reasons.push("cacheReadTokens exceeds inputTokens".to_string());
                None
            }
            _ => None,
        };
        let total = match (input_total, output) {
            (Some(input), Some(output)) => Some(input.saturating_add(output)),
            _ => None,
        };
        CursorTokenBuckets {
            input_total,
            fresh_input,
            cache_read,
            cache_write,
            output,
            total,
            unknown_reasons: reasons,
        }
    }

    fn cost(
        &self,
        model: Option<&str>,
        tokens: &CursorTokenBuckets,
        pricing: &CursorPricing,
    ) -> CursorCost {
        if self.reported_cost_complete {
            return CursorCost {
                amount: Some(round_six(self.reported_cost)),
                currency: "USD".to_string(),
                source: "provider_reported".to_string(),
                basis: "provider-billing".to_string(),
                reason: None,
            };
        }
        let unknown = |reason: &str| CursorCost {
            amount: None,
            currency: "USD".to_string(),
            source: "unknown".to_string(),
            basis: "unknown".to_string(),
            reason: Some(reason.to_string()),
        };
        let Some(model) = model else {
            return unknown("model not recorded");
        };
        let Some(rate) = pricing.rate_for(model) else {
            return unknown("no injected price for model");
        };
        let (Some(fresh), Some(cached), Some(output)) =
            (tokens.fresh_input, tokens.cache_read, tokens.output)
        else {
            return unknown("token evidence incomplete");
        };
        let amount = (fresh as f64 * rate.fresh_input_per_million
            + cached as f64 * rate.cache_read_per_million
            + output as f64 * rate.output_per_million)
            / 1_000_000.0;
        CursorCost {
            amount: Some(round_six(amount)),
            currency: "USD".to_string(),
            source: format!("estimated:{}", rate.source),
            basis: "api-equiv".to_string(),
            reason: None,
        }
    }
}

impl CursorPricing {
    fn rate_for(&self, model: &str) -> Option<&CursorModelRate> {
        let normalized = model.to_ascii_lowercase();
        self.models.get(&normalized).or_else(|| {
            self.models
                .iter()
                .find_map(|(alias, rate)| normalized.contains(alias).then_some(rate))
        })
    }
}

fn update_identity(event: &Map<String, Value>, identity: &mut StreamIdentity) {
    if let Some(session) = text(event, "session_id").or_else(|| text(event, "sessionId")) {
        identity.session_id = Some(session.to_string());
    }
    if let Some(model) = text(event, "model").and_then(|model| clean(Some(model))) {
        identity.model = Some(model);
    }
}

fn accumulate(usage: &Map<String, Value>, key: &str, total: &mut u64, complete: &mut bool) {
    if let Some(value) = usage.get(key).and_then(Value::as_u64) {
        *total = total.saturating_add(value);
    } else {
        *complete = false;
    }
}

fn known(complete: bool, value: u64, key: &str, reasons: &mut Vec<String>) -> Option<u64> {
    if complete {
        Some(value)
    } else {
        reasons.push(format!("missing or invalid {key}"));
        None
    }
}

fn reported_cost(event: &Map<String, Value>) -> Option<f64> {
    ["total_cost_usd", "cost_usd", "cost", "total_cost"]
        .into_iter()
        .find_map(|key| event.get(key).and_then(Value::as_f64))
        .filter(|cost| cost.is_finite() && *cost >= 0.0)
}

fn result_key(event: &Map<String, Value>, identity: &StreamIdentity) -> String {
    let stable_id = ["id", "event_id", "request_id", "message_id", "uuid"]
        .into_iter()
        .find_map(|key| text(event, key));
    if let Some(id) = stable_id {
        return format!(
            "{}:id:{id}",
            identity.session_id.as_deref().unwrap_or("unknown")
        );
    }
    let mut digest = Sha256::new();
    digest.update(
        identity
            .session_id
            .as_deref()
            .unwrap_or("unknown")
            .as_bytes(),
    );
    digest.update([0]);
    digest.update(identity.model.as_deref().unwrap_or("unknown").as_bytes());
    digest.update([0]);
    if let Ok(encoded) = serde_json::to_vec(event) {
        digest.update(encoded);
    }
    format!("digest:{:x}", digest.finalize())
}

fn text<'a>(object: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    object
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
}

fn clean(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

fn round_six(value: f64) -> f64 {
    (value * 1_000_000.0).round() / 1_000_000.0
}
