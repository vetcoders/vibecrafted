//! Provider-owned analytical usage and cost adapter for Grok evidence.
//!
//! Callers inject every authority: evidence paths, session identity, time
//! bounds and a versioned price table. The adapter does not discover Grok's
//! home, access the network or write state. Streaming `end` records carrying
//! `usage`/`modelUsage` are exact. Grok session `updates.jsonl` records expose
//! cumulative `_meta.totalTokens`; those can only yield a labelled analytical
//! estimate and require an injected total-token rate.

use std::collections::{BTreeMap, HashSet};
use std::fs::File;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};

use chrono::{DateTime, TimeZone, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

const MAX_JSON_BYTES: u64 = 16 * 1024 * 1024;
const MAX_JSONL_LINE_BYTES: usize = 2 * 1024 * 1024;

#[derive(Clone, Debug)]
pub struct GrokAnalysisInput<'a> {
    pub evidence_paths: &'a [PathBuf],
    pub session_id: &'a str,
    pub started_at: DateTime<Utc>,
    pub ended_at: DateTime<Utc>,
    pub pricing: &'a GrokPricing,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct GrokPricing {
    pub source: String,
    pub currency: String,
    pub input_per_million: Option<f64>,
    pub cached_input_per_million: Option<f64>,
    pub output_per_million: Option<f64>,
    /// Blended rate used only for `_meta.totalTokens` analytical estimates.
    pub total_per_million: Option<f64>,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum GrokMeasurementKind {
    Exact,
    Estimated,
    Unknown,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct GrokUsageMeasurement {
    pub kind: GrokMeasurementKind,
    pub input_tokens: Option<u64>,
    pub cached_input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub total_tokens: Option<u64>,
    pub source: String,
    pub reason: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct GrokCostMeasurement {
    pub kind: GrokMeasurementKind,
    pub amount: Option<f64>,
    pub currency: String,
    pub source: String,
    pub reason: String,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct GrokEvidenceStats {
    pub files_requested: usize,
    pub files_readable: usize,
    pub records_seen: usize,
    pub records_used: usize,
    pub duplicates_skipped: usize,
    pub malformed_records: usize,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct GrokCostAnalysis {
    pub schema: String,
    pub provider: String,
    pub session_id: String,
    pub started_at: String,
    pub ended_at: String,
    pub model: Option<String>,
    pub usage: GrokUsageMeasurement,
    pub cost: GrokCostMeasurement,
    pub evidence: GrokEvidenceStats,
    pub warnings: Vec<String>,
}

#[derive(Clone, Debug, Default)]
struct UsageCounts {
    input: Option<u64>,
    cached: Option<u64>,
    output: Option<u64>,
    total: Option<u64>,
}

#[derive(Clone, Debug, Default)]
struct EvidenceRecord {
    session_id: Option<String>,
    model: Option<String>,
    timestamp: Option<DateTime<Utc>>,
    event_id: Option<String>,
    prompt_id: Option<String>,
    exact_usage: Option<UsageCounts>,
    reported_cost: Option<f64>,
    total_tokens_snapshot: Option<u64>,
    fingerprint: String,
}

/// Analyze explicitly supplied Grok evidence without discovering or mutating
/// provider state.
pub fn analyze_grok_cost(input: GrokAnalysisInput<'_>) -> GrokCostAnalysis {
    let mut stats = GrokEvidenceStats {
        files_requested: input.evidence_paths.len(),
        ..GrokEvidenceStats::default()
    };
    let mut warnings = Vec::new();
    let mut records = Vec::new();
    for path in input.evidence_paths {
        read_evidence(path, &mut records, &mut stats, &mut warnings);
    }

    let valid_window = input.started_at <= input.ended_at;
    if !valid_window {
        warnings.push("started_at is after ended_at; no evidence can match".to_string());
    }
    let expected_session = input.session_id.trim();
    if expected_session.is_empty() {
        warnings.push("session_id is empty; evidence identity cannot be qualified".to_string());
    }

    let mut seen = HashSet::new();
    let mut filtered = Vec::new();
    for record in records {
        if !valid_window || expected_session.is_empty() {
            continue;
        }
        if record
            .session_id
            .as_deref()
            .is_some_and(|session| session != expected_session)
        {
            continue;
        }
        if record
            .timestamp
            .is_some_and(|stamp| stamp < input.started_at || stamp > input.ended_at)
        {
            continue;
        }
        let identity = record.event_id.as_ref().map_or_else(
            || format!("content:{}", record.fingerprint),
            |event_id| format!("event:{expected_session}:{event_id}"),
        );
        if !seen.insert(identity) {
            stats.duplicates_skipped += 1;
            continue;
        }
        stats.records_used += 1;
        filtered.push(record);
    }

    let model = filtered.iter().find_map(|record| record.model.clone());
    let exact = filtered
        .iter()
        .filter_map(|record| record.exact_usage.clone())
        .collect::<Vec<_>>();
    let (usage, cost) = if exact.is_empty() {
        estimate_from_snapshots(&filtered, input.pricing)
    } else {
        exact_measurements(&filtered, &exact, input.pricing)
    };

    GrokCostAnalysis {
        schema: "vibecrafted.provider-cost.grok.v1".to_string(),
        provider: "grok".to_string(),
        session_id: expected_session.to_string(),
        started_at: input.started_at.to_rfc3339(),
        ended_at: input.ended_at.to_rfc3339(),
        model,
        usage,
        cost,
        evidence: stats,
        warnings,
    }
}

fn read_evidence(
    path: &Path,
    records: &mut Vec<EvidenceRecord>,
    stats: &mut GrokEvidenceStats,
    warnings: &mut Vec<String>,
) {
    let Ok(metadata) = path.metadata() else {
        warnings.push(format!("unreadable evidence: {}", path.display()));
        return;
    };
    if !metadata.is_file() {
        warnings.push(format!("evidence is not a file: {}", path.display()));
        return;
    }
    if metadata.len() > MAX_JSON_BYTES {
        warnings.push(format!("evidence exceeds 16 MiB limit: {}", path.display()));
        return;
    }
    let Ok(file) = File::open(path) else {
        warnings.push(format!("unreadable evidence: {}", path.display()));
        return;
    };
    stats.files_readable += 1;
    if path.extension().and_then(|value| value.to_str()) == Some("json") {
        let mut bytes = Vec::with_capacity(metadata.len() as usize);
        if BufReader::new(file).read_to_end(&mut bytes).is_err() {
            warnings.push(format!("failed to read evidence: {}", path.display()));
            return;
        }
        stats.records_seen += 1;
        match serde_json::from_slice::<Value>(&bytes) {
            Ok(value) => records.push(extract_record(&value)),
            Err(_) => stats.malformed_records += 1,
        }
        return;
    }

    let mut malformed = 0usize;
    for line in BufReader::new(file).split(b'\n') {
        let Ok(line) = line else {
            malformed += 1;
            continue;
        };
        if line.is_empty() {
            continue;
        }
        stats.records_seen += 1;
        if line.len() > MAX_JSONL_LINE_BYTES {
            malformed += 1;
            continue;
        }
        match serde_json::from_slice::<Value>(&line) {
            Ok(value) => records.push(extract_record(&value)),
            Err(_) => malformed += 1,
        }
    }
    stats.malformed_records += malformed;
    if malformed > 0 {
        warnings.push(format!(
            "{} malformed or oversized record(s) skipped in {}",
            malformed,
            path.display()
        ));
    }
}

fn extract_record(value: &Value) -> EvidenceRecord {
    let usage = value
        .get("usage")
        .or_else(|| value.pointer("/result/usage"))
        .and_then(extract_usage)
        .or_else(|| extract_model_usage(value.get("modelUsage")))
        .or_else(|| extract_model_usage(value.get("model_usage")));
    let model = string_at(value, &["model", "model_id", "current_model_id"])
        .or_else(|| {
            value
                .pointer("/params/update/_meta/modelId")
                .and_then(Value::as_str)
        })
        .map(str::to_string)
        .or_else(|| model_usage_name(value));
    let timestamp = timestamp_at(value);
    let session_id = string_at(value, &["sessionId", "session_id"])
        .or_else(|| value.pointer("/params/sessionId").and_then(Value::as_str))
        .map(str::to_string);
    let event_id = string_at(value, &["eventId"])
        .or_else(|| {
            value
                .pointer("/params/_meta/eventId")
                .and_then(Value::as_str)
        })
        .map(str::to_string);
    let prompt_id = string_at(value, &["promptId"])
        .or_else(|| {
            value
                .pointer("/params/_meta/promptId")
                .and_then(Value::as_str)
        })
        .map(str::to_string);
    let reported_cost = float_at(value, &["cost_usd", "cost"]).or_else(|| {
        value
            .get("usage")
            .and_then(|usage| float_at(usage, &["cost_usd", "cost"]))
    });
    let total_tokens_snapshot = value
        .pointer("/params/_meta/totalTokens")
        .and_then(nonnegative_u64)
        .or_else(|| value.get("totalTokens").and_then(nonnegative_u64));
    let fingerprint = format!("{:x}", Sha256::digest(canonical_json(value).as_bytes()));
    EvidenceRecord {
        session_id,
        model,
        timestamp,
        event_id,
        prompt_id,
        exact_usage: usage,
        reported_cost,
        total_tokens_snapshot,
        fingerprint,
    }
}

fn extract_usage(value: &Value) -> Option<UsageCounts> {
    let object = value.as_object()?;
    let input = integer_from(object, &["input_tokens", "inputTokens", "prompt_tokens"]);
    let cached = integer_from(
        object,
        &[
            "cache_read_input_tokens",
            "cached_input_tokens",
            "cacheReadInputTokens",
            "cached_prompt_tokens",
        ],
    );
    let output = integer_from(
        object,
        &["output_tokens", "outputTokens", "completion_tokens"],
    );
    let total = integer_from(object, &["total_tokens", "totalTokens"]);
    (input.is_some() || cached.is_some() || output.is_some() || total.is_some()).then_some(
        UsageCounts {
            input,
            cached,
            output,
            total,
        },
    )
}

fn extract_model_usage(value: Option<&Value>) -> Option<UsageCounts> {
    let value = value?;
    if let Some(object) = value.as_object() {
        return object.values().find_map(extract_usage);
    }
    value.as_array()?.iter().find_map(extract_usage)
}

fn model_usage_name(value: &Value) -> Option<String> {
    value
        .get("modelUsage")
        .or_else(|| value.get("model_usage"))?
        .as_object()?
        .keys()
        .next()
        .cloned()
}

fn exact_measurements(
    records: &[EvidenceRecord],
    exact: &[UsageCounts],
    pricing: &GrokPricing,
) -> (GrokUsageMeasurement, GrokCostMeasurement) {
    let input = sum_known(exact, |usage| usage.input);
    let cached = sum_known(exact, |usage| usage.cached);
    let output = sum_known(exact, |usage| usage.output);
    let explicit_total = sum_known(exact, |usage| usage.total);
    let total = explicit_total.or_else(|| match (input, output) {
        (Some(input), Some(output)) => Some(input.saturating_add(output)),
        _ => None,
    });
    let usage = GrokUsageMeasurement {
        kind: GrokMeasurementKind::Exact,
        input_tokens: input,
        cached_input_tokens: cached,
        output_tokens: output,
        total_tokens: total,
        source: "grok_stream_usage".to_string(),
        reason: String::new(),
    };

    let exact_records = records
        .iter()
        .filter(|record| record.exact_usage.is_some())
        .collect::<Vec<_>>();
    let reported = exact_records
        .iter()
        .filter_map(|record| record.reported_cost)
        .collect::<Vec<_>>();
    if !exact_records.is_empty() && reported.len() == exact_records.len() {
        return (
            usage,
            GrokCostMeasurement {
                kind: GrokMeasurementKind::Exact,
                amount: Some(round_six(reported.into_iter().sum())),
                currency: pricing.currency.clone(),
                source: "provider_reported".to_string(),
                reason: String::new(),
            },
        );
    }
    let cost = price_exact_usage(&usage, pricing).map_or_else(
        |reason| unknown_cost(pricing, reason),
        |amount| GrokCostMeasurement {
            kind: GrokMeasurementKind::Estimated,
            amount: Some(amount),
            currency: pricing.currency.clone(),
            source: format!("estimated:{}", pricing.source),
            reason: "exact Grok usage priced with injected rates".to_string(),
        },
    );
    (usage, cost)
}

fn estimate_from_snapshots(
    records: &[EvidenceRecord],
    pricing: &GrokPricing,
) -> (GrokUsageMeasurement, GrokCostMeasurement) {
    let mut snapshots = records
        .iter()
        .filter_map(|record| {
            Some((
                record.timestamp?,
                record.prompt_id.as_deref().unwrap_or("unknown"),
                record.total_tokens_snapshot?,
            ))
        })
        .collect::<Vec<_>>();
    snapshots.sort_by_key(|(timestamp, _, _)| *timestamp);
    let mut previous_by_prompt: BTreeMap<&str, u64> = BTreeMap::new();
    let mut estimated = 0u64;
    for (_, prompt, current) in snapshots {
        let increment = match previous_by_prompt.insert(prompt, current) {
            Some(previous) if current >= previous => current - previous,
            Some(_) | None => current,
        };
        estimated = estimated.saturating_add(increment);
    }
    if estimated == 0 {
        return (
            unknown_usage("Grok evidence carried no exact usage or totalTokens snapshots"),
            unknown_cost(pricing, "usage is unknown".to_string()),
        );
    }
    let usage = GrokUsageMeasurement {
        kind: GrokMeasurementKind::Estimated,
        input_tokens: None,
        cached_input_tokens: None,
        output_tokens: None,
        total_tokens: Some(estimated),
        source: "grok_session_updates.totalTokens".to_string(),
        reason: "analytical estimate from deduplicated cumulative totalTokens snapshots"
            .to_string(),
    };
    let cost = valid_rate(pricing.total_per_million).map_or_else(
        || {
            unknown_cost(
                pricing,
                "total_per_million is required to price analytical totalTokens".to_string(),
            )
        },
        |rate| GrokCostMeasurement {
            kind: GrokMeasurementKind::Estimated,
            amount: Some(round_six(estimated as f64 * rate / 1_000_000.0)),
            currency: pricing.currency.clone(),
            source: format!("estimated:{}", pricing.source),
            reason: "analytical totalTokens estimate priced with injected blended rate".to_string(),
        },
    );
    (usage, cost)
}

fn price_exact_usage(usage: &GrokUsageMeasurement, pricing: &GrokPricing) -> Result<f64, String> {
    if let (Some(input), Some(output), Some(input_rate), Some(output_rate)) = (
        usage.input_tokens,
        usage.output_tokens,
        valid_rate(pricing.input_per_million),
        valid_rate(pricing.output_per_million),
    ) {
        let cached = usage.cached_input_tokens.unwrap_or(0);
        let cached_rate = if cached == 0 {
            Some(0.0)
        } else {
            valid_rate(pricing.cached_input_per_million)
        };
        if let Some(cached_rate) = cached_rate {
            return Ok(round_six(
                (input as f64 * input_rate
                    + cached as f64 * cached_rate
                    + output as f64 * output_rate)
                    / 1_000_000.0,
            ));
        }
    }
    if let (Some(total), Some(rate)) = (usage.total_tokens, valid_rate(pricing.total_per_million)) {
        return Ok(round_six(total as f64 * rate / 1_000_000.0));
    }
    Err("neither complete category rates nor a total-token rate are available".to_string())
}

fn unknown_usage(reason: &str) -> GrokUsageMeasurement {
    GrokUsageMeasurement {
        kind: GrokMeasurementKind::Unknown,
        input_tokens: None,
        cached_input_tokens: None,
        output_tokens: None,
        total_tokens: None,
        source: "unknown".to_string(),
        reason: reason.to_string(),
    }
}

fn unknown_cost(pricing: &GrokPricing, reason: String) -> GrokCostMeasurement {
    GrokCostMeasurement {
        kind: GrokMeasurementKind::Unknown,
        amount: None,
        currency: pricing.currency.clone(),
        source: "unknown".to_string(),
        reason,
    }
}

fn sum_known<F>(usage: &[UsageCounts], field: F) -> Option<u64>
where
    F: Fn(&UsageCounts) -> Option<u64>,
{
    let values = usage.iter().map(field).collect::<Option<Vec<_>>>()?;
    Some(
        values
            .into_iter()
            .fold(0u64, |total, value| total.saturating_add(value)),
    )
}

fn integer_from(object: &serde_json::Map<String, Value>, keys: &[&str]) -> Option<u64> {
    keys.iter()
        .find_map(|key| object.get(*key).and_then(nonnegative_u64))
}

fn nonnegative_u64(value: &Value) -> Option<u64> {
    value.as_u64().or_else(|| {
        value
            .as_str()
            .filter(|text| !text.starts_with('-'))
            .and_then(|text| text.parse().ok())
    })
}

fn string_at<'a>(value: &'a Value, keys: &[&str]) -> Option<&'a str> {
    keys.iter()
        .find_map(|key| value.get(*key).and_then(Value::as_str))
        .filter(|value| !value.is_empty())
}

fn float_at(value: &Value, keys: &[&str]) -> Option<f64> {
    keys.iter().find_map(|key| {
        value
            .get(*key)
            .and_then(|value| value.as_f64().or_else(|| value.as_str()?.parse().ok()))
            .filter(|value| value.is_finite() && *value >= 0.0)
    })
}

fn timestamp_at(value: &Value) -> Option<DateTime<Utc>> {
    if let Some(milliseconds) = value
        .pointer("/params/_meta/agentTimestampMs")
        .and_then(Value::as_i64)
    {
        return Utc.timestamp_millis_opt(milliseconds).single();
    }
    if let Some(seconds) = value.get("timestamp").and_then(Value::as_i64) {
        return Utc.timestamp_opt(seconds, 0).single();
    }
    string_at(value, &["ts", "timestamp", "created_at", "updated_at"])
        .and_then(|stamp| DateTime::parse_from_rfc3339(stamp).ok())
        .map(|stamp| stamp.with_timezone(&Utc))
}

fn canonical_json(value: &Value) -> String {
    serde_json::to_string(value).unwrap_or_default()
}

fn valid_rate(rate: Option<f64>) -> Option<f64> {
    rate.filter(|rate| rate.is_finite() && *rate >= 0.0)
}

fn round_six(value: f64) -> f64 {
    (value * 1_000_000.0).round() / 1_000_000.0
}
