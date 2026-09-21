//! Read-only usage and cost projection over canonical runtime run metadata.
//!
//! The Python runtime remains the sole writer of
//! `control_plane/runtime_runs/<run-id>/meta.json`. This module only projects
//! the structured telemetry already recorded there; it never turns a missing
//! measurement into zero and never combines currencies with provider credits.

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

use crate::{ControlPlane, is_safe_run_id};

pub const USAGE_REPORT_SCHEMA: &str = "vibecrafted.usage-report.v1";
/// Every provider launcher currently supported by Vibecrafted has a dedicated
/// analytical adapter. Keep this inventory explicit so adding a launcher
/// cannot silently leave cost analysis behind.
pub const USAGE_PROVIDER_ADAPTERS: &[&str] =
    &["agy", "claude", "codex", "cursor", "grok", "junie", "kimi"];
const MAX_META_BYTES: u64 = 1024 * 1024;

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct UsageFilter {
    pub since: Option<Duration>,
    pub since_label: String,
    pub provider: Option<String>,
    pub agent: Option<String>,
    pub model: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct UsageReport {
    pub schema: String,
    pub generated_at: String,
    pub filter: UsageReportFilter,
    pub runs: Vec<UsageRun>,
    pub totals: UsageTotals,
    pub dimensions: UsageDimensions,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct UsageReportFilter {
    pub since: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provider: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct UsageRun {
    pub run_id: String,
    pub provider: Value,
    pub agent: Value,
    pub model: Value,
    pub status: String,
    pub exit_code: Option<i64>,
    pub recorded_at: String,
    pub tokens: UsageTokens,
    pub cost: UsageCost,
    pub failure_kind: Option<String>,
    pub failure: Option<String>,
    pub provider_session_id: Value,
    pub telemetry_source: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct UsageTokens {
    pub schema: String,
    pub unit: String,
    pub source: String,
    pub events: u64,
    pub tokens_input: Value,
    pub tokens_cached_input: Value,
    pub tokens_cache_write: Value,
    pub tokens_output: Value,
    pub tokens_total: Value,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct UsageCost {
    pub amount: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub currency: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub unit: Option<String>,
    pub source: String,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct UsageTotals {
    pub runs: usize,
    pub runs_failed: usize,
    pub tokens_total_known: u64,
    pub runs_tokens_unknown: usize,
    pub cost_by_unit: BTreeMap<String, f64>,
    pub runs_cost_unknown: usize,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct UsageDimensions {
    pub providers: Vec<UsageDimension>,
    pub agents: Vec<UsageDimension>,
    pub models: Vec<UsageDimension>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct UsageDimension {
    pub name: String,
    #[serde(flatten)]
    pub totals: UsageTotals,
}

impl ControlPlane {
    /// Aggregate structured telemetry recorded by the runtime, newest first.
    ///
    /// Legacy runs without a structured `usage` or `cost` block remain
    /// explicitly unknown. Transcript parsing stays with the Python telemetry
    /// owner instead of being duplicated here with subtly different rules.
    pub fn usage_report(&self, now: DateTime<Utc>, filter: UsageFilter) -> UsageReport {
        let runtime_runs = self.control_plane_home().join("runtime_runs");
        let cutoff = filter.since.map(|duration| now - duration);
        let mut runs = fs::read_dir(&runtime_runs)
            .ok()
            .into_iter()
            .flatten()
            .filter_map(Result::ok)
            .filter_map(|entry| read_usage_run(&entry.path()))
            .filter(|run| {
                cutoff.is_none_or(|cutoff| {
                    parse_timestamp(&run.recorded_at).is_some_and(|stamp| stamp >= cutoff)
                })
            })
            .filter(|run| matches_filter(run, &filter))
            .collect::<Vec<_>>();
        runs.sort_by(|left, right| {
            right
                .recorded_at
                .cmp(&left.recorded_at)
                .then_with(|| left.run_id.cmp(&right.run_id))
        });

        let totals = totals(&runs);
        let dimensions = UsageDimensions {
            providers: dimensions(&runs, |run| display_value(&run.provider)),
            agents: dimensions(&runs, |run| display_value(&run.agent)),
            models: dimensions(&runs, |run| display_value(&run.model)),
        };
        UsageReport {
            schema: USAGE_REPORT_SCHEMA.to_string(),
            generated_at: now.to_rfc3339(),
            filter: UsageReportFilter {
                since: filter.since_label,
                provider: filter.provider,
                agent: filter.agent,
                model: filter.model,
            },
            runs,
            totals,
            dimensions,
        }
    }
}

fn read_usage_run(run_dir: &Path) -> Option<UsageRun> {
    let metadata = fs::symlink_metadata(run_dir).ok()?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return None;
    }
    let dir_name = run_dir.file_name()?.to_str()?;
    if !is_safe_run_id(dir_name) {
        return None;
    }
    let meta_path = run_dir.join("meta.json");
    let meta_info = fs::symlink_metadata(&meta_path).ok()?;
    if !meta_info.is_file()
        || meta_info.file_type().is_symlink()
        || meta_info.len() > MAX_META_BYTES
    {
        return None;
    }
    let meta = serde_json::from_slice::<Value>(&fs::read(meta_path).ok()?).ok()?;
    let object = meta.as_object()?;
    let run_id = text(object, "run_id").unwrap_or(dir_name).to_string();
    if run_id != dir_name || !is_safe_run_id(&run_id) {
        return None;
    }
    let recorded_at = text(object, "completed_at")
        .or_else(|| text(object, "updated_at"))
        .and_then(|stamp| parse_timestamp(stamp).map(|parsed| parsed.to_rfc3339()))?;

    let usage = object.get("usage").and_then(Value::as_object);
    let cost = object.get("cost").and_then(Value::as_object);
    let failure = object.get("failure").and_then(Value::as_object);
    let agent = value_or_unknown(object.get("agent"), "agent not recorded");
    // Existing runtime metadata identifies the provider through `agent`.
    // Newer writers may add an explicit provider, which wins when present.
    let provider = object
        .get("provider")
        .filter(|value| !value.is_null() && value.as_str().is_none_or(|text| !text.is_empty()))
        .cloned()
        .unwrap_or_else(|| agent.clone());
    let model = value_or_unknown(
        object.get("agent_model").or_else(|| object.get("model")),
        "model not reported by provider stream",
    );

    Some(UsageRun {
        run_id,
        provider,
        agent,
        model,
        status: text(object, "status").unwrap_or("unknown").to_string(),
        exit_code: integer(object.get("exit_code")),
        recorded_at,
        tokens: usage_from(usage),
        cost: cost_from(cost),
        failure_kind: failure
            .and_then(|block| text(block, "kind"))
            .map(str::to_string),
        failure: failure
            .and_then(|block| text(block, "summary"))
            .map(str::to_string),
        provider_session_id: value_or_unknown(
            object.get("provider_session_id"),
            "provider emitted no session id",
        ),
        telemetry_source: if usage.is_some() || cost.is_some() {
            "meta".to_string()
        } else {
            "meta(legacy-uninstrumented)".to_string()
        },
    })
}

fn usage_from(block: Option<&Map<String, Value>>) -> UsageTokens {
    let missing = unknown("provider emitted no usage events");
    UsageTokens {
        schema: block
            .and_then(|value| text(value, "schema"))
            .unwrap_or("vibecrafted.usage.v1")
            .to_string(),
        unit: block
            .and_then(|value| text(value, "unit"))
            .unwrap_or("tokens")
            .to_string(),
        source: block
            .and_then(|value| text(value, "source"))
            .unwrap_or("unknown")
            .to_string(),
        events: block
            .and_then(|value| value.get("events"))
            .and_then(Value::as_u64)
            .unwrap_or(0),
        tokens_input: block
            .and_then(|value| value.get("tokens_input"))
            .cloned()
            .unwrap_or_else(|| missing.clone()),
        tokens_cached_input: block
            .and_then(|value| value.get("tokens_cached_input"))
            .cloned()
            .unwrap_or_else(|| missing.clone()),
        tokens_cache_write: block
            .and_then(|value| value.get("tokens_cache_write"))
            .cloned()
            .unwrap_or_else(|| unknown("provider stream carried no cache-write field")),
        tokens_output: block
            .and_then(|value| value.get("tokens_output"))
            .cloned()
            .unwrap_or_else(|| missing.clone()),
        tokens_total: block
            .and_then(|value| value.get("tokens_total"))
            .cloned()
            .unwrap_or(missing),
    }
}

fn cost_from(block: Option<&Map<String, Value>>) -> UsageCost {
    UsageCost {
        amount: block
            .and_then(|value| value.get("amount"))
            .cloned()
            .unwrap_or_else(|| unknown("cost not recorded")),
        currency: block
            .and_then(|value| text(value, "currency"))
            .map(str::to_string),
        unit: block
            .and_then(|value| text(value, "unit"))
            .map(str::to_string),
        source: block
            .and_then(|value| text(value, "source"))
            .unwrap_or("unknown")
            .to_string(),
    }
}

fn matches_filter(run: &UsageRun, filter: &UsageFilter) -> bool {
    field_matches(&run.provider, filter.provider.as_deref())
        && field_matches(&run.agent, filter.agent.as_deref())
        && field_matches(&run.model, filter.model.as_deref())
}

fn field_matches(value: &Value, expected: Option<&str>) -> bool {
    expected.is_none_or(|expected| {
        value
            .as_str()
            .is_some_and(|actual| actual.eq_ignore_ascii_case(expected))
    })
}

fn totals(runs: &[UsageRun]) -> UsageTotals {
    let mut result = UsageTotals {
        runs: runs.len(),
        runs_failed: runs
            .iter()
            .filter(|run| run.exit_code.is_some_and(|code| code != 0) || run.failure_kind.is_some())
            .count(),
        ..UsageTotals::default()
    };
    for run in runs {
        if let Some(total) = run.tokens.tokens_total.as_u64() {
            result.tokens_total_known = result.tokens_total_known.saturating_add(total);
        } else {
            result.runs_tokens_unknown += 1;
        }
        if let Some(amount) = run.cost.amount.as_f64() {
            let unit = run
                .cost
                .unit
                .as_deref()
                .or(run.cost.currency.as_deref())
                .unwrap_or("USD");
            let sum = result.cost_by_unit.entry(unit.to_string()).or_default();
            *sum = round_six(*sum + amount);
        } else {
            result.runs_cost_unknown += 1;
        }
    }
    result
}

fn dimensions<F>(runs: &[UsageRun], label: F) -> Vec<UsageDimension>
where
    F: Fn(&UsageRun) -> String,
{
    let mut groups: BTreeMap<String, Vec<UsageRun>> = BTreeMap::new();
    for run in runs {
        groups.entry(label(run)).or_default().push(run.clone());
    }
    groups
        .into_iter()
        .map(|(name, runs)| UsageDimension {
            name,
            totals: totals(&runs),
        })
        .collect()
}

fn display_value(value: &Value) -> String {
    value.as_str().unwrap_or("unknown").to_string()
}

fn parse_timestamp(value: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(value)
        .ok()
        .map(|stamp| stamp.with_timezone(&Utc))
}

fn text<'a>(object: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    object
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
}

fn integer(value: Option<&Value>) -> Option<i64> {
    value.and_then(|value| {
        value.as_i64().or_else(|| {
            value
                .as_str()
                .filter(|text| !text.is_empty())
                .and_then(|text| text.parse().ok())
        })
    })
}

fn value_or_unknown(value: Option<&Value>, reason: &str) -> Value {
    value
        .filter(|value| !value.is_null() && value.as_str() != Some(""))
        .cloned()
        .unwrap_or_else(|| unknown(reason))
}

fn unknown(reason: &str) -> Value {
    json!({"value": "unknown", "reason": reason})
}

fn round_six(value: f64) -> f64 {
    (value * 1_000_000.0).round() / 1_000_000.0
}
