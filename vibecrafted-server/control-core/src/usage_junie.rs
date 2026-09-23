//! Read-only analytical adapter for Junie nested `modelUsage` transcripts.
//!
//! Junie reports per-model call evidence inside nested `modelUsage` arrays.
//! `inputTokens` is fresh input and `cacheInputTokens` is additive cached
//! input. Callers inject transcript paths, identity fallbacks, timestamp, and
//! pricing. The adapter never discovers provider-home state or performs I/O
//! beyond reading the explicit transcript paths.

use std::collections::{BTreeMap, HashSet};
use std::io::{self, BufRead, BufReader};
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

const MAX_JSONL_LINE_BYTES: usize = 1024 * 1024;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct JunieTranscriptInput {
    pub path: PathBuf,
    pub session_id: Option<String>,
    pub model: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct JuniePricing {
    pub models: BTreeMap<String, JunieModelRate>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct JunieModelRate {
    pub fresh_input_per_million: f64,
    pub cache_input_per_million: f64,
    pub output_per_million: f64,
    pub source: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct JunieUsageReport {
    pub schema: String,
    pub generated_at: String,
    pub provider: String,
    pub slices: Vec<JunieUsageSlice>,
    pub diagnostics: JunieAdapterDiagnostics,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct JunieUsageSlice {
    pub session_id: Option<String>,
    pub model: Option<String>,
    pub usage_events: u64,
    pub cost_events: u64,
    pub tokens: JunieTokenBuckets,
    pub cost: JunieCost,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct JunieTokenBuckets {
    pub fresh_input: Option<u64>,
    pub cache_input: Option<u64>,
    pub cache_create: Option<u64>,
    pub output: Option<u64>,
    pub total: Option<u64>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub unknown_reasons: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct JunieCost {
    pub amount: Option<f64>,
    pub currency: String,
    pub source: String,
    pub basis: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct JunieAdapterDiagnostics {
    pub files_read: usize,
    pub complete_lines: u64,
    pub malformed_lines: u64,
    pub oversized_lines: u64,
    pub partial_trailing_lines: u64,
    pub duplicate_usage_entries: u64,
    pub model_usage_entries: u64,
    pub file_errors: Vec<JunieFileError>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct JunieFileError {
    pub path: String,
    pub error: String,
}

#[derive(Clone, Debug)]
struct Aggregate {
    usage_events: u64,
    cost_events: u64,
    fresh_input: u64,
    cache_input: u64,
    cache_create: u64,
    output: u64,
    fresh_known: bool,
    cache_known: bool,
    cache_create_known: bool,
    output_known: bool,
    reported_cost: f64,
    reported_cost_complete: bool,
}

impl Default for Aggregate {
    fn default() -> Self {
        Self {
            usage_events: 0,
            cost_events: 0,
            fresh_input: 0,
            cache_input: 0,
            cache_create: 0,
            output: 0,
            fresh_known: true,
            cache_known: true,
            cache_create_known: true,
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

/// Analyze caller-selected Junie JSONL transcripts.
pub fn analyze_junie_transcripts(
    inputs: &[JunieTranscriptInput],
    pricing: &JuniePricing,
    generated_at: &str,
) -> JunieUsageReport {
    let mut diagnostics = JunieAdapterDiagnostics::default();
    let mut seen = HashSet::new();
    let mut aggregates: BTreeMap<(Option<String>, Option<String>), Aggregate> = BTreeMap::new();
    for input in inputs {
        if let Err(error) = read_transcript(input, &mut diagnostics, &mut seen, &mut aggregates) {
            diagnostics.file_errors.push(JunieFileError {
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
            JunieUsageSlice {
                session_id,
                model,
                usage_events: aggregate.usage_events,
                cost_events: aggregate.cost_events,
                tokens,
                cost,
            }
        })
        .collect();
    JunieUsageReport {
        schema: "vibecrafted.usage-provider.junie.v1".to_string(),
        generated_at: generated_at.to_string(),
        provider: "junie".to_string(),
        slices,
        diagnostics,
    }
}

fn read_transcript(
    input: &JunieTranscriptInput,
    diagnostics: &mut JunieAdapterDiagnostics,
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
        let Some(event_object) = event.as_object() else {
            diagnostics.malformed_lines += 1;
            continue;
        };
        update_identity(event_object, &mut identity);
        let event_key = evidence_key(event_object, &identity);
        let mut entries = Vec::new();
        collect_model_usage(&event, &mut entries);
        for (ordinal, entry) in entries.into_iter().enumerate() {
            diagnostics.model_usage_entries += 1;
            let model = text(entry, "model")
                .and_then(|value| clean(Some(value)))
                .or_else(|| identity.model.clone());
            let dedupe_key = format!(
                "{}:{}:{}",
                identity.session_id.as_deref().unwrap_or("unknown"),
                event_key,
                ordinal
            );
            if !seen.insert(dedupe_key) {
                diagnostics.duplicate_usage_entries += 1;
                continue;
            }
            record_entry(entry, identity.session_id.clone(), model, aggregates);
        }
    }
    Ok(())
}

fn collect_model_usage<'a>(value: &'a Value, entries: &mut Vec<&'a Map<String, Value>>) {
    match value {
        Value::Object(object) => {
            for (key, child) in object {
                if matches!(key.as_str(), "modelUsage" | "model_usage") {
                    if let Value::Array(items) = child {
                        entries.extend(items.iter().filter_map(Value::as_object));
                    }
                    continue;
                }
                collect_model_usage(child, entries);
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_model_usage(item, entries);
            }
        }
        _ => {}
    }
}

fn record_entry(
    entry: &Map<String, Value>,
    session_id: Option<String>,
    model: Option<String>,
    aggregates: &mut BTreeMap<(Option<String>, Option<String>), Aggregate>,
) {
    let has_tokens = [
        "inputTokens",
        "input_tokens",
        "cacheInputTokens",
        "cache_input_tokens",
        "cacheCreateTokens",
        "cache_create_tokens",
        "outputTokens",
        "output_tokens",
    ]
    .into_iter()
    .any(|key| entry.get(key).is_some());
    let cost = reported_cost(entry);
    if !has_tokens && cost.is_none() {
        return;
    }
    let aggregate = aggregates.entry((session_id, model)).or_default();
    if has_tokens {
        aggregate.usage_events = aggregate.usage_events.saturating_add(1);
        accumulate_aliases(
            entry,
            &["inputTokens", "input_tokens"],
            &mut aggregate.fresh_input,
            &mut aggregate.fresh_known,
        );
        accumulate_aliases(
            entry,
            &["cacheInputTokens", "cache_input_tokens", "cacheReadTokens"],
            &mut aggregate.cache_input,
            &mut aggregate.cache_known,
        );
        accumulate_aliases(
            entry,
            &[
                "cacheCreateTokens",
                "cache_create_tokens",
                "cacheWriteTokens",
            ],
            &mut aggregate.cache_create,
            &mut aggregate.cache_create_known,
        );
        accumulate_aliases(
            entry,
            &["outputTokens", "output_tokens"],
            &mut aggregate.output,
            &mut aggregate.output_known,
        );
        if cost.is_none() {
            aggregate.reported_cost_complete = false;
        }
    }
    if let Some(cost) = cost {
        aggregate.cost_events = aggregate.cost_events.saturating_add(1);
        aggregate.reported_cost += cost;
    }
}

impl Aggregate {
    fn tokens(&self) -> JunieTokenBuckets {
        if self.usage_events == 0 {
            return JunieTokenBuckets {
                fresh_input: None,
                cache_input: None,
                cache_create: None,
                output: None,
                total: None,
                unknown_reasons: vec!["no token-bearing modelUsage entries".to_string()],
            };
        }
        let mut reasons = Vec::new();
        let fresh = known(
            self.fresh_known,
            self.fresh_input,
            "inputTokens",
            &mut reasons,
        );
        let cache = known(
            self.cache_known,
            self.cache_input,
            "cacheInputTokens",
            &mut reasons,
        );
        let cache_create = known(
            self.cache_create_known,
            self.cache_create,
            "cacheCreateTokens",
            &mut reasons,
        );
        let output = known(self.output_known, self.output, "outputTokens", &mut reasons);
        let total = match (fresh, cache, output) {
            (Some(fresh), Some(cache), Some(output)) => {
                Some(fresh.saturating_add(cache).saturating_add(output))
            }
            _ => None,
        };
        JunieTokenBuckets {
            fresh_input: fresh,
            cache_input: cache,
            cache_create,
            output,
            total,
            unknown_reasons: reasons,
        }
    }

    fn cost(
        &self,
        model: Option<&str>,
        tokens: &JunieTokenBuckets,
        pricing: &JuniePricing,
    ) -> JunieCost {
        if self.cost_events > 0 && (self.usage_events == 0 || self.reported_cost_complete) {
            return JunieCost {
                amount: Some(round_six(self.reported_cost)),
                currency: "USD".to_string(),
                source: "provider_reported".to_string(),
                basis: "provider-billing".to_string(),
                reason: None,
            };
        }
        let unknown = |reason: &str| JunieCost {
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
        let (Some(fresh), Some(cache), Some(output)) =
            (tokens.fresh_input, tokens.cache_input, tokens.output)
        else {
            return unknown("token evidence incomplete");
        };
        let amount = (fresh as f64 * rate.fresh_input_per_million
            + cache as f64 * rate.cache_input_per_million
            + output as f64 * rate.output_per_million)
            / 1_000_000.0;
        JunieCost {
            amount: Some(round_six(amount)),
            currency: "USD".to_string(),
            source: format!("estimated:{}", rate.source),
            basis: "api-equiv".to_string(),
            reason: None,
        }
    }
}

impl JuniePricing {
    fn rate_for(&self, model: &str) -> Option<&JunieModelRate> {
        let normalized = model.to_ascii_lowercase();
        self.models.iter().find_map(|(alias, rate)| {
            normalized
                .contains(&alias.to_ascii_lowercase())
                .then_some(rate)
        })
    }
}

fn update_identity(event: &Map<String, Value>, identity: &mut StreamIdentity) {
    for key in ["sessionId", "session_id"] {
        if let Some(session) = text(event, key) {
            identity.session_id = Some(session.to_string());
            break;
        }
    }
    if let Some(model) = text(event, "model").and_then(|value| clean(Some(value))) {
        identity.model = Some(model);
    }
}

fn accumulate_aliases(
    entry: &Map<String, Value>,
    aliases: &[&str],
    total: &mut u64,
    complete: &mut bool,
) {
    if let Some(value) = aliases
        .iter()
        .find_map(|key| entry.get(*key).and_then(Value::as_u64))
    {
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

fn reported_cost(entry: &Map<String, Value>) -> Option<f64> {
    ["cost", "cost_usd", "totalCostUsd", "total_cost_usd"]
        .into_iter()
        .find_map(|key| entry.get(key).and_then(Value::as_f64))
        .filter(|cost| cost.is_finite() && *cost >= 0.0)
}

fn evidence_key(event: &Map<String, Value>, identity: &StreamIdentity) -> String {
    let stable_id = [
        "id",
        "eventId",
        "event_id",
        "requestId",
        "request_id",
        "uuid",
    ]
    .into_iter()
    .find_map(|key| text(event, key));
    if let Some(id) = stable_id {
        return format!("id:{id}");
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
