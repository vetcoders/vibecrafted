//! Read-only Kimi `wire.jsonl` usage and API-equivalent cost projection.
//!
//! This adapter deliberately has no default path, clock, pricing table, daemon,
//! or network client. Callers inject the wire paths, session identity, report
//! timestamp, and prices. Costs are estimates based on public/API-equivalent
//! rates; they are never presented as provider-reported billing.

use std::collections::{BTreeMap, HashSet};
use std::io::{self, BufRead, BufReader};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

const MAX_WIRE_LINE_BYTES: usize = 1024 * 1024;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct KimiWireInput {
    pub path: PathBuf,
    /// The authoritative session id for this wire when its directory layout
    /// does not contain a `session_<id>` component.
    pub session_id: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct KimiPricing {
    pub models: BTreeMap<String, KimiModelRate>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct KimiModelRate {
    pub fresh_input_per_million: f64,
    pub cache_read_per_million: f64,
    pub output_per_million: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct KimiUsageReport {
    pub schema: String,
    pub generated_at: String,
    pub provider: String,
    pub slices: Vec<KimiUsageSlice>,
    pub diagnostics: KimiAdapterDiagnostics,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct KimiUsageSlice {
    pub session_id: Option<String>,
    pub model: Option<String>,
    pub usage_events: u64,
    pub tokens: KimiTokenBuckets,
    pub cost: KimiEstimatedCost,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct KimiTokenBuckets {
    pub fresh_input: Option<u64>,
    pub cache_read: Option<u64>,
    pub output: Option<u64>,
    pub total: Option<u64>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub unknown_reasons: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct KimiEstimatedCost {
    pub amount: Option<f64>,
    pub currency: String,
    pub unit: String,
    pub source: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct KimiAdapterDiagnostics {
    pub files_read: usize,
    pub complete_lines: u64,
    pub malformed_lines: u64,
    pub oversized_lines: u64,
    pub partial_trailing_lines: u64,
    pub duplicate_usage_records: u64,
    pub ignored_non_turn_usage_records: u64,
    pub file_errors: Vec<KimiFileError>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct KimiFileError {
    pub path: String,
    pub error: String,
}

#[derive(Clone, Debug)]
struct BucketAggregate {
    events: u64,
    fresh_input: u64,
    cache_read: u64,
    output: u64,
    fresh_known: bool,
    cache_known: bool,
    output_known: bool,
}

impl Default for BucketAggregate {
    fn default() -> Self {
        Self {
            events: 0,
            fresh_input: 0,
            cache_read: 0,
            output: 0,
            fresh_known: true,
            cache_known: true,
            output_known: true,
        }
    }
}

/// Parse the supplied Kimi wires into per-session, per-model usage slices.
///
/// A broken wire is isolated in `diagnostics.file_errors`; other wires still
/// contribute. The final unterminated JSONL fragment is ignored because Kimi
/// may be writing it concurrently. Usage records are deduplicated across wire
/// files using their stable event/request/turn id when available, otherwise a
/// digest of the complete event scoped to the resolved session.
pub fn analyze_kimi_wires(
    inputs: &[KimiWireInput],
    pricing: &KimiPricing,
    generated_at: &str,
) -> KimiUsageReport {
    let mut diagnostics = KimiAdapterDiagnostics::default();
    let mut seen = HashSet::new();
    let mut aggregates: BTreeMap<(Option<String>, Option<String>), BucketAggregate> =
        BTreeMap::new();

    for input in inputs {
        if let Err(error) = read_wire(input, &mut diagnostics, &mut seen, &mut aggregates) {
            diagnostics.file_errors.push(KimiFileError {
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
            let cost = estimated_cost(model.as_deref(), &tokens, pricing);
            KimiUsageSlice {
                session_id,
                model,
                usage_events: aggregate.events,
                tokens,
                cost,
            }
        })
        .collect();

    KimiUsageReport {
        schema: "vibecrafted.usage-provider.kimi.v1".to_string(),
        generated_at: generated_at.to_string(),
        provider: "kimi".to_string(),
        slices,
        diagnostics,
    }
}

fn read_wire(
    input: &KimiWireInput,
    diagnostics: &mut KimiAdapterDiagnostics,
    seen: &mut HashSet<String>,
    aggregates: &mut BTreeMap<(Option<String>, Option<String>), BucketAggregate>,
) -> io::Result<()> {
    let file = crate::transcript_open::open_provider_transcript(&input.path)?;
    let mut reader = BufReader::new(file);
    let path_session = input
        .session_id
        .clone()
        .or_else(|| session_from_path(&input.path));

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
        if line.len() > MAX_WIRE_LINE_BYTES {
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
        if event.get("type").and_then(Value::as_str) != Some("usage.record") {
            continue;
        }
        if event.get("usageScope").and_then(Value::as_str) != Some("turn") {
            diagnostics.ignored_non_turn_usage_records += 1;
            continue;
        }

        let session_id = event_session(&event).or_else(|| path_session.clone());
        let dedupe_key = usage_record_key(&event, session_id.as_deref());
        if !seen.insert(dedupe_key) {
            diagnostics.duplicate_usage_records += 1;
            continue;
        }
        let model = event
            .get("model")
            .and_then(Value::as_str)
            .filter(|model| !model.is_empty())
            .map(normalize_model);
        let aggregate = aggregates.entry((session_id, model)).or_default();
        aggregate.events = aggregate.events.saturating_add(1);
        let usage = event.get("usage").and_then(Value::as_object);

        let input_other = usage.and_then(|usage| token(usage.get("inputOther")));
        let cache_creation = usage.and_then(|usage| token(usage.get("inputCacheCreation")));
        if let (Some(input_other), Some(cache_creation)) = (input_other, cache_creation) {
            aggregate.fresh_input = aggregate
                .fresh_input
                .saturating_add(input_other.saturating_add(cache_creation));
        } else {
            aggregate.fresh_known = false;
        }

        if let Some(value) = usage.and_then(|usage| token(usage.get("inputCacheRead"))) {
            aggregate.cache_read = aggregate.cache_read.saturating_add(value);
        } else {
            aggregate.cache_known = false;
        }
        if let Some(value) = usage.and_then(|usage| token(usage.get("output"))) {
            aggregate.output = aggregate.output.saturating_add(value);
        } else {
            aggregate.output_known = false;
        }
    }
    Ok(())
}

impl BucketAggregate {
    fn tokens(&self) -> KimiTokenBuckets {
        let mut unknown_reasons = Vec::new();
        if !self.fresh_known {
            unknown_reasons.push("missing inputOther or inputCacheCreation".to_string());
        }
        if !self.cache_known {
            unknown_reasons.push("missing inputCacheRead".to_string());
        }
        if !self.output_known {
            unknown_reasons.push("missing output".to_string());
        }
        let fresh_input = self.fresh_known.then_some(self.fresh_input);
        let cache_read = self.cache_known.then_some(self.cache_read);
        let output = self.output_known.then_some(self.output);
        let total = match (fresh_input, cache_read, output) {
            (Some(fresh), Some(cache), Some(output)) => {
                Some(fresh.saturating_add(cache).saturating_add(output))
            }
            _ => None,
        };
        KimiTokenBuckets {
            fresh_input,
            cache_read,
            output,
            total,
            unknown_reasons,
        }
    }
}

fn estimated_cost(
    model: Option<&str>,
    tokens: &KimiTokenBuckets,
    pricing: &KimiPricing,
) -> KimiEstimatedCost {
    let unknown = |reason: &str| KimiEstimatedCost {
        amount: None,
        currency: "USD".to_string(),
        unit: "api-equiv".to_string(),
        source: "estimated".to_string(),
        reason: Some(reason.to_string()),
    };
    let Some(model) = model else {
        return unknown("model not recorded");
    };
    let Some(rate) = pricing.rate_for(model) else {
        return unknown("no injected price for model");
    };
    let (Some(fresh), Some(cache), Some(output)) =
        (tokens.fresh_input, tokens.cache_read, tokens.output)
    else {
        return unknown("token buckets incomplete");
    };
    let amount = (fresh as f64 * rate.fresh_input_per_million
        + cache as f64 * rate.cache_read_per_million
        + output as f64 * rate.output_per_million)
        / 1_000_000.0;
    KimiEstimatedCost {
        amount: Some(round_six(amount)),
        currency: "USD".to_string(),
        unit: "api-equiv".to_string(),
        source: "estimated".to_string(),
        reason: None,
    }
}

impl KimiPricing {
    fn rate_for(&self, model: &str) -> Option<&KimiModelRate> {
        self.models.get(model).or_else(|| {
            model
                .strip_prefix("kimi-")
                .and_then(|short| self.models.get(short))
        })
    }
}

fn normalize_model(model: &str) -> String {
    model
        .strip_prefix("kimi-code/")
        .unwrap_or(model)
        .to_string()
}

fn event_session(event: &Value) -> Option<String> {
    ["sessionId", "session_id"]
        .into_iter()
        .find_map(|key| event.get(key).and_then(Value::as_str))
        .filter(|session| !session.is_empty())
        .map(|session| {
            session
                .strip_prefix("session_")
                .unwrap_or(session)
                .to_string()
        })
}

fn session_from_path(path: &Path) -> Option<String> {
    path.components().find_map(|component| {
        component
            .as_os_str()
            .to_str()
            .and_then(|value| value.strip_prefix("session_"))
            .filter(|value| !value.is_empty())
            .map(str::to_string)
    })
}

fn usage_record_key(event: &Value, session_id: Option<&str>) -> String {
    let stable_id = [
        "id",
        "eventId",
        "event_id",
        "requestId",
        "request_id",
        "turnId",
        "turn_id",
    ]
    .into_iter()
    .find_map(|key| event.get(key).and_then(Value::as_str))
    .filter(|value| !value.is_empty());
    if let Some(stable_id) = stable_id {
        return format!("{}:id:{stable_id}", session_id.unwrap_or("unknown"));
    }
    let mut digest = Sha256::new();
    digest.update(session_id.unwrap_or("unknown").as_bytes());
    digest.update([0]);
    if let Ok(encoded) = serde_json::to_vec(event) {
        digest.update(encoded);
    }
    format!("digest:{:x}", digest.finalize())
}

fn token(value: Option<&Value>) -> Option<u64> {
    value.and_then(Value::as_u64)
}

fn round_six(value: f64) -> f64 {
    (value * 1_000_000.0).round() / 1_000_000.0
}
