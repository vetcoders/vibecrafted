//! Read-only usage and cost projection over canonical runtime run metadata.
//!
//! The Python runtime remains the sole writer of
//! `control_plane/runtime_runs/<run-id>/meta.json`. This module only projects
//! the structured telemetry already recorded there; it never turns a missing
//! measurement into zero and never combines currencies with provider credits.

use std::collections::BTreeMap;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

use crate::{ControlPlane, is_safe_run_id};

pub const USAGE_REPORT_SCHEMA: &str = "vibecrafted.usage-report.v1";
/// Every provider launcher currently supported by Vibecrafted has a dedicated
/// analytical adapter. Keep this inventory explicit so adding a launcher
/// cannot silently leave cost analysis behind.
pub const USAGE_PROVIDER_ADAPTERS: &[&str] = &[
    "agy", "claude", "codex", "cursor", "grok", "junie", "kimi", "copilot",
];
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
    #[serde(default, rename = "unpricedModels")]
    pub unpriced_models: Vec<String>,
    #[serde(default)]
    pub local_collection: Value,
    #[serde(default)]
    pub pickers: UsageDimensions,
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
    #[serde(default)]
    pub origin: String,
    #[serde(default)]
    pub vc_run_ids: Vec<String>,
    #[serde(default)]
    pub usage_window_start: Option<String>,
    #[serde(default, rename = "unpricedModels")]
    pub unpriced_models: Vec<String>,
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
    #[serde(flatten)]
    pub semantics: BTreeMap<String, Value>,
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
    /// A run whose `meta.json` has no structured `usage` block stays unknown
    /// until the same Python transcript recovery the CLI uses
    /// (`run_telemetry_from_meta`) can read `transcript.log`. This projection
    /// does not invent a second parser and does not turn a missing total into
    /// zero. A recovered number is a measurement; a still-missing one stays
    /// unknown.
    pub fn usage_report(&self, now: DateTime<Utc>, filter: UsageFilter) -> UsageReport {
        let runtime_runs = self.control_plane_home().join("runtime_runs");
        let cutoff = filter.since.map(|duration| now - duration);
        let mut runs = fs::read_dir(&runtime_runs)
            .ok()
            .into_iter()
            .flatten()
            .filter_map(Result::ok)
            .filter_map(|entry| read_usage_run(&entry.path()))
            .collect::<Vec<_>>();
        recover_lazy_transcripts(&runtime_runs, &mut runs);
        let telemetry_dir = self.home().join("telemetry");
        let (local_runs, local_collection) = read_local_telemetry_runs(&telemetry_dir, now);
        // Deduplicate before filters. A whole local session includes activity
        // outside a VC cut; retain that measurement and mark its VC provenance.
        for mut local in local_runs {
            let matching = runs
                .iter()
                .filter(|r| {
                    r.agent == local.agent && r.provider_session_id == local.provider_session_id
                })
                .collect::<Vec<_>>();
            if !matching.is_empty() {
                local.vc_run_ids = matching.iter().map(|r| r.run_id.clone()).collect();
                local.origin = "local+vibecrafted".into();
                // If local evidence is unavailable, retain runtime measurements.
                if local.tokens.tokens_total.as_u64().is_none() {
                    continue;
                }
                runs.retain(|r| {
                    !(r.agent == local.agent && r.provider_session_id == local.provider_session_id)
                });
            }
            runs.push(local);
        }
        // Imported receipts for the same session and time window represent
        // one slice. Disjoint resumed run windows remain separate until the
        // local full-session evidence can replace them with one observation.
        let mut dedup: BTreeMap<String, UsageRun> = BTreeMap::new();
        for run in runs {
            let key = run
                .provider_session_id
                .as_str()
                .filter(|id| *id != "unknown" && !id.is_empty())
                .map(|id| {
                    if run.origin == "vibecrafted" {
                        format!(
                            "{}:{id}:{}:{}",
                            display_value(&run.agent),
                            run.usage_window_start.as_deref().unwrap_or(""),
                            run.recorded_at
                        )
                    } else {
                        format!("{}:{id}", display_value(&run.agent))
                    }
                })
                .unwrap_or_else(|| run.run_id.clone());
            if let Some(previous) = dedup.get_mut(&key) {
                let mut ids = previous.vc_run_ids.clone();
                ids.extend(run.vc_run_ids.clone());
                if run.tokens.tokens_total.as_u64().unwrap_or(0)
                    > previous.tokens.tokens_total.as_u64().unwrap_or(0)
                {
                    *previous = run;
                }
                ids.sort();
                ids.dedup();
                previous.vc_run_ids = ids;
            } else {
                dedup.insert(key, run);
            }
        }
        let unfiltered = dedup.into_values().collect::<Vec<_>>();
        let pickers = UsageDimensions {
            providers: dimensions(&unfiltered, |r| display_value(&r.provider)),
            agents: dimensions(&unfiltered, |r| display_value(&r.agent)),
            models: dimensions(&unfiltered, |r| display_value(&r.model)),
        };
        let mut runs = unfiltered
            .into_iter()
            .filter(|run| matches_filter(run, &filter))
            .filter(|run| {
                cutoff.is_none_or(|c| parse_timestamp(&run.recorded_at).is_some_and(|t| t >= c))
            })
            .collect::<Vec<_>>();
        runs.sort_by(|a, b| {
            b.recorded_at
                .cmp(&a.recorded_at)
                .then_with(|| a.run_id.cmp(&b.run_id))
        });
        let totals = totals(&runs);
        let dimensions = UsageDimensions {
            providers: dimensions(&runs, |run| display_value(&run.provider)),
            agents: dimensions(&runs, |run| display_value(&run.agent)),
            models: dimensions(&runs, |run| display_value(&run.model)),
        };
        UsageReport {
            local_collection,
            pickers,
            schema: USAGE_REPORT_SCHEMA.to_string(),
            generated_at: now.to_rfc3339(),
            filter: UsageReportFilter {
                since: filter.since_label,
                provider: filter.provider,
                agent: filter.agent,
                model: filter.model,
            },
            unpriced_models: runs
                .iter()
                .flat_map(|run| run.unpriced_models.iter().cloned())
                .collect::<std::collections::BTreeSet<_>>()
                .into_iter()
                .collect(),
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
        .or_else(|| text(object, "started_at"))
        .or_else(|| text(object, "created_at"))
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
        .unwrap_or_else(|| json!(canonical_provider_for(agent.as_str().unwrap_or(""))));
    let model = value_or_unknown(
        object.get("agent_model").or_else(|| object.get("model")),
        "model not reported by provider stream",
    );

    Some(UsageRun {
        unpriced_models: object
            .get("unpricedModels")
            .and_then(Value::as_array)
            .map(|values| {
                values
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default(),
        origin: "vibecrafted".into(),
        vc_run_ids: vec![run_id.clone()],
        usage_window_start: text(object, "started_at").map(str::to_string),
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
        semantics: [
            "counting_version",
            "model_usage",
            "input_semantics",
            "tokens_reasoning",
        ]
        .into_iter()
        .filter_map(|key| {
            block
                .and_then(|value| value.get(key))
                .map(|value| (key.to_string(), value.clone()))
        })
        .collect(),
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
    provider_matches(&run.provider, &run.agent, filter.provider.as_deref())
        && field_matches(&run.agent, filter.agent.as_deref())
        && field_matches(&run.model, filter.model.as_deref())
}

fn provider_matches(provider: &Value, agent: &Value, expected: Option<&str>) -> bool {
    expected.is_none_or(|expected| {
        let trimmed = expected.trim();
        if trimmed.is_empty() || trimmed.eq_ignore_ascii_case("all") {
            return true;
        }
        if trimmed == "unknown" {
            return provider.as_str().is_none_or(|v| v == "unknown");
        }
        provider
            .as_str()
            .is_some_and(|actual| actual.eq_ignore_ascii_case(trimmed))
            || agent
                .as_str()
                .is_some_and(|actual| actual.eq_ignore_ascii_case(trimmed))
    })
}

fn field_matches(value: &Value, expected: Option<&str>) -> bool {
    expected.is_none_or(|expected| {
        let trimmed = expected.trim();
        if trimmed.is_empty() || trimmed.eq_ignore_ascii_case("all") {
            return true;
        }
        if trimmed == "unknown" {
            return value.as_str().is_none_or(|v| v == "unknown");
        }
        value
            .as_str()
            .is_some_and(|actual| actual.eq_ignore_ascii_case(trimmed))
    })
}

pub fn canonical_provider_for(agent: &str) -> &'static str {
    match agent {
        "agy" => "google",
        "claude" => "anthropic",
        "codex" => "openai",
        "copilot" => "github",
        "cursor" => "anysphere",
        "grok" => "xai",
        "junie" => "jetbrains",
        "kimi" => "moonshot",
        _ => "unknown",
    }
}

fn read_local_telemetry_runs(telemetry: &Path, now: DateTime<Utc>) -> (Vec<UsageRun>, Value) {
    let projection = telemetry.join("local-usage.json");
    let value = if regular_file(&projection) {
        fs::read(&projection)
            .ok()
            .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
    } else {
        None
    };
    let mut records = Vec::new();
    let mut coverage = json!({"owner":"vc-server", "status":"unknown", "reason":"collector has not published", "archive":"daily aggregates excluded: session overlap cannot be proven"});
    if let Some(value) = value.filter(|v| v["schema"] == "vibecrafted.local-usage.v1") {
        let stamp = value["generated_at"].as_str().and_then(parse_timestamp);
        let stale = stamp.is_none_or(|t| now.signed_duration_since(t).num_seconds() > 300);
        coverage = json!({"owner":"vc-server", "status":if stale {"stale"} else {"observed"}, "generated_at": value["generated_at"], "providers":value["providers"], "archive":"daily aggregates excluded: session overlap cannot be proven"});
        records = value["records"].as_array().cloned().unwrap_or_default();
    } else {
        // Compatibility intake from the existing parser sidecars; never import
        // day aggregates as fake successful sessions or combine them with VC.
        for agent in USAGE_PROVIDER_ADAPTERS {
            let path = telemetry.join(agent).join("scan-cache.json");
            if !regular_file(&path) {
                continue;
            }
            if let Some(files) = fs::read(path)
                .ok()
                .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
                .and_then(|v| v["files"].as_object().cloned())
            {
                for entry in files.into_values() {
                    if let Some(mut record) = entry["record"].as_object().cloned() {
                        record.insert("agent".into(), json!(agent));
                        record.entry("measurement_observed").or_insert(json!(
                            entry["days"].as_array().is_some_and(|d| !d.is_empty())
                        ));
                        records.push(Value::Object(record));
                    }
                }
            }
        }
    }
    let mut runs = BTreeMap::new();
    for record in records {
        let Some(agent) = record["agent"]
            .as_str()
            .filter(|a| USAGE_PROVIDER_ADAPTERS.contains(a))
        else {
            continue;
        };
        let Some(id) = record["id"].as_str().filter(|s| !s.is_empty()) else {
            continue;
        };
        let recorded_at = record["mtime"]
            .as_f64()
            .filter(|n| n.is_finite() && *n > 0.0)
            .and_then(|t| DateTime::from_timestamp(t as i64, 0))
            .map(|t| t.to_rfc3339())
            .unwrap_or_default();
        let native = &record["native"];
        let measured = record["measurement_observed"] == true;
        let bucket = |keys: &[&str]| -> Value {
            if measured {
                for key in keys {
                    if let Some(n) = native[*key].as_u64() {
                        return json!(n);
                    }
                }
            }
            unknown("local session emitted no validated bucket")
        };
        let total = if measured {
            record["processed_total"].as_u64().map(Value::from)
        } else {
            None
        };
        let run = UsageRun {
            run_id: format!("local-{agent}-{id}"),
            provider: json!(canonical_provider_for(agent)),
            agent: json!(agent),
            model: value_or_unknown(record.get("model"), "model not reported by local source"),
            status: "unknown".into(),
            exit_code: None,
            recorded_at,
            tokens: UsageTokens {
                schema: "vibecrafted.usage.v1".into(),
                unit: "tokens".into(),
                source: "local_session".into(),
                events: u64::from(measured),
                tokens_input: bucket(&["input_tokens", "inputTokens"]),
                tokens_cached_input: bucket(&[
                    "cached_input_tokens",
                    "cache_read_input_tokens",
                    "cachedReadTokens",
                    "cacheReadTokens",
                    "cacheInputTokens",
                ]),
                tokens_cache_write: bucket(&[
                    "cache_write_input_tokens",
                    "cache_creation_input_tokens",
                    "cacheWriteTokens",
                    "cacheCreateTokens",
                ]),
                tokens_output: bucket(&["output_tokens", "outputTokens"]),
                tokens_total: total.unwrap_or_else(|| unknown("local usage unmeasured")),
                semantics: BTreeMap::from([(
                    "cache_semantics".into(),
                    record["cache_semantics"].clone(),
                )]),
            },
            cost: UsageCost {
                amount: unknown("local session cost unmeasured"),
                currency: None,
                unit: None,
                source: "local_session".into(),
            },
            failure_kind: None,
            failure: None,
            provider_session_id: json!(id),
            telemetry_source: "local_session".into(),
            origin: "local".into(),
            vc_run_ids: Vec::new(),
            usage_window_start: None,
            unpriced_models: Vec::new(),
        };
        let key = format!("{agent}:{id}");
        if runs
            .get(&key)
            .is_none_or(|p: &UsageRun| p.recorded_at < run.recorded_at)
        {
            runs.insert(key, run);
        }
    }
    (runs.into_values().collect(), coverage)
}

/// A bounded child invocation used only by the server's one local collector.
/// Reuses the same interpreter/source resolver as lazy transcript recovery.
pub fn local_usage_command(home: &Path) -> Option<Command> {
    let mut command = telemetry_python()?;
    command.arg("-c").arg("from pathlib import Path; import sys; from vibecrafted_core.runtime.telemetry.fleet_engine import collect_local_usage; collect_local_usage(Path(sys.argv[1]))").arg(home);
    Some(command)
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

const LAZY_TELEMETRY_SCRIPT: &str = r#"
import json, sys
from pathlib import Path
from vibecrafted_core.telemetry import run_telemetry_from_meta

items = json.load(sys.stdin)
out = []
for item in items:
    run_id = str(item.get("run_id") or "")
    try:
        meta = json.loads(Path(item["meta"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        continue
    if not isinstance(meta, dict):
        continue
    transcript = Path(item["transcript"])
    row = run_telemetry_from_meta(
        meta, transcript=transcript if transcript.is_file() else None
    )
    out.append({"run_id": run_id, "telemetry": row})
json.dump(out, sys.stdout)
"#;

/// Fill legacy rows from `transcript.log` using the CLI's Python recovery.
///
/// Missing measurements, including recorded unknowns, use the same Python
/// harness fallback as settle. If recovery is unavailable they stay unknown.
fn recover_lazy_transcripts(runtime_runs: &Path, runs: &mut [UsageRun]) {
    let mut jobs = Vec::new();
    for run in runs.iter() {
        if run.tokens.tokens_total.as_u64().is_some() && run.cost.amount.as_f64().is_some() {
            continue;
        }
        let meta = runtime_runs.join(&run.run_id).join("meta.json");
        let transcript = runtime_runs.join(&run.run_id).join("transcript.log");
        if !regular_file(&meta) {
            continue;
        }
        jobs.push(json!({
            "run_id": run.run_id,
            "meta": meta,
            "transcript": transcript,
        }));
    }
    if jobs.is_empty() {
        return;
    }
    let Some(recovered) = lazy_telemetry(&jobs) else {
        return;
    };
    for run in runs.iter_mut() {
        let Some(telemetry) = recovered.get(&run.run_id).and_then(Value::as_object) else {
            continue;
        };
        if telemetry
            .get("usage")
            .and_then(|usage| usage.get("tokens_total"))
            .and_then(Value::as_u64)
            .is_none()
        {
            continue;
        }
        apply_lazy_telemetry(run, telemetry);
    }
}

fn apply_lazy_telemetry(run: &mut UsageRun, telemetry: &Map<String, Value>) {
    if let Some(models) = telemetry.get("unpricedModels").and_then(Value::as_array) {
        run.unpriced_models = models
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect();
    }
    if let Some(usage) = telemetry.get("usage").and_then(Value::as_object) {
        run.tokens = usage_from(Some(usage));
    }
    if let Some(cost) = telemetry.get("cost").and_then(Value::as_object) {
        run.cost = cost_from(Some(cost));
    }
    if let Some(source) = telemetry
        .get("telemetry_source")
        .and_then(Value::as_str)
        .filter(|source| !source.is_empty())
    {
        run.telemetry_source = source.to_string();
    }
    if let Some(model) = telemetry.get("model") {
        let usable =
            model.as_str().is_some_and(|text| !text.is_empty()) || model.get("value").is_some();
        if usable {
            run.model = model.clone();
        }
    }
}

fn lazy_telemetry(jobs: &[Value]) -> Option<BTreeMap<String, Value>> {
    let payload = serde_json::to_vec(jobs).ok()?;
    let mut command = telemetry_python()?;
    command
        .arg("-c")
        .arg(LAZY_TELEMETRY_SCRIPT)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let mut child = command.spawn().ok()?;
    {
        let mut stdin = child.stdin.take()?;
        stdin.write_all(&payload).ok()?;
    }
    let output = child.wait_with_output().ok()?;
    if !output.status.success() {
        return None;
    }
    let rows = serde_json::from_slice::<Vec<Value>>(&output.stdout).ok()?;
    let mut by_id = BTreeMap::new();
    for row in rows {
        let Some(run_id) = row.get("run_id").and_then(Value::as_str) else {
            continue;
        };
        let Some(telemetry) = row.get("telemetry").cloned() else {
            continue;
        };
        by_id.insert(run_id.to_string(), telemetry);
    }
    Some(by_id)
}

fn telemetry_python() -> Option<Command> {
    let executable = explicit_python("VIBECRAFTED_PYTHON")
        .or_else(runtime_python)
        .unwrap_or_else(|| PathBuf::from("python3"));
    let mut command = Command::new(executable);
    if let Some(core) = source_core_dir() {
        // A source build must call the checkout's recovery, not an older
        // installed package that happens to sit on PYTHONPATH.
        command.env("PYTHONPATH", core);
    }
    Some(command)
}

fn runtime_python() -> Option<PathBuf> {
    let root = std::env::var("VIBECRAFTED_RUNTIME_ROOT").ok()?;
    let path = PathBuf::from(root.trim()).join("bin/python3");
    path.is_file().then_some(path)
}

fn explicit_python(name: &str) -> Option<PathBuf> {
    let value = std::env::var(name).ok()?;
    let path = PathBuf::from(value.trim());
    if path.is_absolute() && path.is_file() {
        Some(path)
    } else {
        None
    }
}

fn source_core_dir() -> Option<PathBuf> {
    // Release binaries must not carry the checkout. `env!("CARGO_MANIFEST_DIR")`
    // is a string literal rustc's `--remap-path-prefix` cannot rewrite, and one
    // copy of it in control-core showed up as the snapshot path in voc,
    // vc-admin, vc-o, vc-server and vibecrafted-server-web.
    if let Ok(root) = std::env::var("VIBECRAFTED_RUNTIME_ROOT") {
        let candidate = PathBuf::from(root.trim()).join("vibecrafted-core");
        if candidate.join("vibecrafted_core/telemetry.py").is_file() {
            return Some(candidate);
        }
    }
    #[cfg(debug_assertions)]
    {
        let candidate = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../vibecrafted-core");
        if candidate.join("vibecrafted_core/telemetry.py").is_file() {
            return Some(candidate);
        }
    }
    None
}

fn regular_file(path: &Path) -> bool {
    fs::symlink_metadata(path).is_ok_and(|meta| meta.is_file() && !meta.file_type().is_symlink())
}
