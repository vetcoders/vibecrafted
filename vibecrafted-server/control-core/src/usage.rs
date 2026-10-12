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
    /// Present only when the request explicitly asked for a collection pass.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub collection: Option<crate::collect::CollectionReport>,
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
        recover_lazy_transcripts(&runtime_runs, &mut runs);

        let telemetry_dir = self.home().join("telemetry");
        let local_runs = read_local_telemetry_runs(&telemetry_dir, &runs, cutoff, &filter);
        runs.extend(local_runs);

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
            unpriced_models: runs
                .iter()
                .flat_map(|run| run.unpriced_models.iter().cloned())
                .collect::<std::collections::BTreeSet<_>>()
                .into_iter()
                .collect(),
            runs,
            totals,
            dimensions,
            collection: None,
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
        value
            .as_str()
            .is_some_and(|actual| actual.eq_ignore_ascii_case(trimmed))
    })
}

fn canonical_provider_for(agent: &str) -> &'static str {
    match agent {
        "agy" => "google",
        "claude" => "anthropic",
        "codex" => "openai",
        "copilot" => "github",
        "cursor" => "cursor",
        "grok" => "xai",
        "junie" => "jetbrains",
        "kimi" => "moonshot",
        _ => "unknown",
    }
}

/// What the VC control-plane runs already represent, used to keep local
/// provider telemetry from silently double-counting VC-originated sessions.
#[derive(Default)]
struct VcAttribution {
    /// Provider session ids already represented by a VC run's meta.
    session_ids: std::collections::HashSet<String>,
    /// (agent, model, day) -> tokens already represented by VC runs whose
    /// model and token total are both measured; exact deduction is safe.
    known_by_agent_model_day: BTreeMap<(String, String, String), u64>,
    /// (agent, day) pairs containing VC runs whose token total or model is
    /// unmeasured: the archive remainder for that day cannot be recovered.
    unattributable_agent_days: std::collections::BTreeSet<(String, String)>,
}

fn vc_attribution(vc_runs: &[UsageRun]) -> VcAttribution {
    let mut attribution = VcAttribution::default();
    for run in vc_runs {
        if let Some(session_id) = run.provider_session_id.as_str() {
            if !session_id.is_empty() && session_id != "unknown" {
                attribution.session_ids.insert(session_id.to_string());
            }
        }
        let agent = run.agent.as_str().unwrap_or("").to_ascii_lowercase();
        let day = run.recorded_at.get(..10).unwrap_or("").to_string();
        if agent.is_empty() || day.is_empty() {
            continue;
        }
        let model = run.model.as_str().unwrap_or("").to_string();
        match (run.tokens.tokens_total.as_u64(), model.is_empty()) {
            (Some(total), false) => {
                let key = (agent, model, day);
                let entry = attribution.known_by_agent_model_day.entry(key).or_insert(0);
                *entry = entry.saturating_add(total);
            }
            _ => {
                attribution.unattributable_agent_days.insert((agent, day));
            }
        }
    }
    attribution
}

fn read_local_telemetry_runs(
    telemetry_dir: &Path,
    vc_runs: &[UsageRun],
    cutoff: Option<DateTime<Utc>>,
    filter: &UsageFilter,
) -> Vec<UsageRun> {
    let mut local_runs = Vec::new();
    let mut seen_session_ids = std::collections::HashSet::new();
    let attribution = vc_attribution(vc_runs);

    for agent in USAGE_PROVIDER_ADAPTERS {
        let scan_cache_path = telemetry_dir.join(agent).join("scan-cache.json");
        let mut had_scan_cache = false;
        if regular_file(&scan_cache_path) {
            if let Ok(content) = fs::read(&scan_cache_path) {
                if let Ok(value) = serde_json::from_slice::<Value>(&content) {
                    if let Some(files) = value.get("files").and_then(Value::as_object) {
                        had_scan_cache = true;
                        for (_file_key, entry) in files {
                            let Some(record) = entry.get("record").and_then(Value::as_object)
                            else {
                                continue;
                            };
                            let session_id = record
                                .get("id")
                                .and_then(Value::as_str)
                                .unwrap_or("")
                                .to_string();

                            // Deduplication: skip if session was run via VC or already seen in another local file
                            if !session_id.is_empty() {
                                if attribution.session_ids.contains(&session_id)
                                    || seen_session_ids.contains(&session_id)
                                {
                                    continue;
                                }
                                seen_session_ids.insert(session_id.clone());
                            }

                            let mtime = record.get("mtime").and_then(Value::as_f64).unwrap_or(0.0);
                            let recorded_dt = DateTime::from_timestamp(
                                mtime as i64,
                                ((mtime.fract() * 1_000_000_000.0) as u32).min(999_999_999),
                            )
                            .unwrap_or_else(Utc::now);

                            if cutoff.is_some_and(|c| recorded_dt < c) {
                                continue;
                            }

                            let rec_agent = record
                                .get("agent")
                                .and_then(Value::as_str)
                                .unwrap_or(*agent);
                            let model_str = record.get("model").and_then(Value::as_str);
                            let model_val = match model_str {
                                Some(m) if !m.is_empty() => json!(m),
                                _ => unknown("model not reported by local scan"),
                            };

                            let provider_val = json!(canonical_provider_for(rec_agent));
                            let agent_val = json!(rec_agent);

                            let processed_total = record
                                .get("processed_total")
                                .and_then(Value::as_u64)
                                .or_else(|| {
                                    record.get("native").and_then(Value::as_object).and_then(
                                        |nat| {
                                            nat.get("total_tokens")
                                                .or_else(|| nat.get("totalTokens"))
                                                .and_then(Value::as_u64)
                                        },
                                    )
                                });

                            let native = record.get("native").and_then(Value::as_object);
                            let tokens_input = native
                                .and_then(|n| {
                                    n.get("input_tokens").or_else(|| n.get("inputTokens"))
                                })
                                .cloned()
                                .unwrap_or_else(|| {
                                    unknown("local session emitted no input token breakdown")
                                });
                            let tokens_output = native
                                .and_then(|n| {
                                    n.get("output_tokens").or_else(|| n.get("outputTokens"))
                                })
                                .cloned()
                                .unwrap_or_else(|| {
                                    unknown("local session emitted no output token breakdown")
                                });
                            let tokens_cached_input = native
                                .and_then(|n| {
                                    n.get("cached_input_tokens")
                                        .or_else(|| n.get("cachedReadTokens"))
                                        .or_else(|| n.get("cacheReadTokens"))
                                })
                                .cloned()
                                .unwrap_or_else(|| {
                                    unknown("local session carried no cache read count")
                                });
                            let tokens_cache_write = native
                                .and_then(|n| {
                                    n.get("cache_write_input_tokens")
                                        .or_else(|| n.get("cacheCreationTokens"))
                                        .or_else(|| n.get("cacheWriteTokens"))
                                })
                                .cloned()
                                .unwrap_or_else(|| {
                                    unknown("local session carried no cache write count")
                                });

                            let tokens_total = processed_total
                                .map(Value::from)
                                .unwrap_or_else(|| unknown("local session carried no token total"));

                            let tokens = UsageTokens {
                                schema: "vibecrafted.usage.v1".to_string(),
                                unit: "tokens".to_string(),
                                source: "local_scan".to_string(),
                                events: 1,
                                tokens_input,
                                tokens_cached_input,
                                tokens_cache_write,
                                tokens_output,
                                tokens_total,
                                semantics: BTreeMap::new(),
                            };

                            let cost = UsageCost {
                                amount: unknown("cost not recorded for local session"),
                                currency: None,
                                unit: Some("USD".to_string()),
                                source: "local_scan".to_string(),
                            };

                            let run_id = if session_id.is_empty() {
                                format!("local-{rec_agent}-{}", (mtime as u64))
                            } else {
                                let safe_suffix: String = session_id
                                    .chars()
                                    .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
                                    .take(24)
                                    .collect();
                                format!("local-{rec_agent}-{safe_suffix}")
                            };

                            let run = UsageRun {
                                run_id,
                                provider: provider_val,
                                agent: agent_val,
                                model: model_val,
                                // A local scan record proves a provider session file
                                // existed with tokens; it says nothing about how the
                                // session ended. Honest state, never a fabricated
                                // completed/exit-0 lifecycle truth.
                                status: "observed".to_string(),
                                exit_code: None,
                                recorded_at: recorded_dt.to_rfc3339(),
                                tokens,
                                cost,
                                failure_kind: None,
                                failure: None,
                                provider_session_id: if session_id.is_empty() {
                                    unknown("local session has no session id")
                                } else {
                                    json!(session_id)
                                },
                                telemetry_source: "local_session".to_string(),
                                unpriced_models: Vec::new(),
                            };

                            if matches_filter(&run, filter) {
                                local_runs.push(run);
                            }
                        }
                    }
                }
            }
        }

        // Also check archive JSONL if scan-cache wasn't found
        if !had_scan_cache {
            let archive_path = telemetry_dir.join("archive").join(format!("{agent}.jsonl"));
            if regular_file(&archive_path) {
                if let Ok(file_content) = fs::read_to_string(&archive_path) {
                    for line in file_content.lines() {
                        let trimmed = line.trim();
                        if trimmed.is_empty() {
                            continue;
                        }
                        let Ok(row) = serde_json::from_str::<Value>(trimmed) else {
                            continue;
                        };
                        let Some(day) = row.get("day").and_then(Value::as_str) else {
                            continue;
                        };
                        let model_str = row
                            .get("model")
                            .and_then(Value::as_str)
                            .unwrap_or("unknown");
                        let recorded_at = format!("{day}T12:00:00Z");
                        let recorded_dt = parse_timestamp(&recorded_at).unwrap_or_else(Utc::now);
                        if cutoff.is_some_and(|c| recorded_dt < c) {
                            continue;
                        }

                        let input_tokens =
                            row.get("input_tokens").and_then(Value::as_u64).unwrap_or(0);
                        let output_tokens = row
                            .get("output_tokens")
                            .and_then(Value::as_u64)
                            .unwrap_or(0);
                        let cache_create = row
                            .get("cache_creation_tokens")
                            .and_then(Value::as_u64)
                            .unwrap_or(0);
                        let cache_read = row
                            .get("cache_read_tokens")
                            .and_then(Value::as_u64)
                            .unwrap_or(0);
                        let total_tokens = input_tokens + output_tokens + cache_create + cache_read;

                        // The archive day row aggregates every session of that
                        // day, including VC-launched ones. Deduct exactly the
                        // VC-represented tokens for the same (agent, model,
                        // day); when any VC run on that day is unmeasured or
                        // unmodeled, the remainder is unrecoverable and stays
                        // unknown instead of becoming a fabricated number.
                        let agent_day = (agent.to_string(), day.to_string());
                        if attribution.unattributable_agent_days.contains(&agent_day) {
                            let tokens = UsageTokens {
                                schema: "vibecrafted.usage.v1".to_string(),
                                unit: "tokens".to_string(),
                                source: "local_archive".to_string(),
                                events: 1,
                                tokens_input: unknown(
                                    "archive day includes VC runs with unmeasured tokens or model",
                                ),
                                tokens_cached_input: unknown(
                                    "archive day includes VC runs with unmeasured tokens or model",
                                ),
                                tokens_cache_write: unknown(
                                    "archive day includes VC runs with unmeasured tokens or model",
                                ),
                                tokens_output: unknown(
                                    "archive day includes VC runs with unmeasured tokens or model",
                                ),
                                tokens_total: unknown(
                                    "archive day includes VC runs with unmeasured tokens or model",
                                ),
                                semantics: BTreeMap::new(),
                            };
                            let cost = UsageCost {
                                amount: unknown("cost not recorded for local archive"),
                                currency: None,
                                unit: Some("USD".to_string()),
                                source: "local_archive".to_string(),
                            };
                            let run = UsageRun {
                                run_id: format!("archive-{agent}-{day}-{model_str}"),
                                provider: json!(canonical_provider_for(agent)),
                                agent: json!(agent),
                                model: json!(model_str),
                                status: "aggregate".to_string(),
                                exit_code: None,
                                recorded_at,
                                tokens,
                                cost,
                                failure_kind: None,
                                failure: None,
                                provider_session_id: unknown(
                                    "archive day aggregation has no session id",
                                ),
                                telemetry_source: "local_archive".to_string(),
                                unpriced_models: Vec::new(),
                            };
                            if matches_filter(&run, filter) {
                                local_runs.push(run);
                            }
                            continue;
                        }

                        let deducted = attribution
                            .known_by_agent_model_day
                            .get(&(agent.to_string(), model_str.to_string(), day.to_string()))
                            .copied()
                            .unwrap_or(0)
                            .min(total_tokens);
                        if deducted == total_tokens && total_tokens > 0 {
                            // Fully represented by VC runs already in the report.
                            continue;
                        }
                        let remainder = total_tokens - deducted;

                        let tokens = if deducted > 0 {
                            let mut semantics = BTreeMap::new();
                            semantics.insert("vc_deducted_tokens".to_string(), json!(deducted));
                            UsageTokens {
                                schema: "vibecrafted.usage.v1".to_string(),
                                unit: "tokens".to_string(),
                                source: "local_archive".to_string(),
                                events: 1,
                                tokens_input: unknown(
                                    "component split not recoverable after VC deduction",
                                ),
                                tokens_cached_input: unknown(
                                    "component split not recoverable after VC deduction",
                                ),
                                tokens_cache_write: unknown(
                                    "component split not recoverable after VC deduction",
                                ),
                                tokens_output: unknown(
                                    "component split not recoverable after VC deduction",
                                ),
                                tokens_total: json!(remainder),
                                semantics,
                            }
                        } else {
                            UsageTokens {
                                schema: "vibecrafted.usage.v1".to_string(),
                                unit: "tokens".to_string(),
                                source: "local_archive".to_string(),
                                events: 1,
                                tokens_input: json!(input_tokens),
                                tokens_cached_input: json!(cache_read),
                                tokens_cache_write: json!(cache_create),
                                tokens_output: json!(output_tokens),
                                tokens_total: json!(total_tokens),
                                semantics: BTreeMap::new(),
                            }
                        };
                        let cost = UsageCost {
                            amount: unknown("cost not recorded for local archive"),
                            currency: None,
                            unit: Some("USD".to_string()),
                            source: "local_archive".to_string(),
                        };
                        let run = UsageRun {
                            run_id: format!("archive-{agent}-{day}-{model_str}"),
                            provider: json!(canonical_provider_for(agent)),
                            agent: json!(agent),
                            model: json!(model_str),
                            // A day aggregate is not a session lifecycle: it
                            // never claims completed/exit-0 truth.
                            status: "aggregate".to_string(),
                            exit_code: None,
                            recorded_at,
                            tokens,
                            cost,
                            failure_kind: None,
                            failure: None,
                            provider_session_id: unknown(
                                "archive day aggregation has no session id",
                            ),
                            telemetry_source: "local_archive".to_string(),
                            unpriced_models: Vec::new(),
                        };
                        if matches_filter(&run, filter) {
                            local_runs.push(run);
                        }
                    }
                }
            }
        }
    }
    local_runs
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

pub(crate) fn telemetry_python() -> Option<Command> {
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

pub(crate) fn runtime_python() -> Option<PathBuf> {
    let root = std::env::var("VIBECRAFTED_RUNTIME_ROOT").ok()?;
    let path = PathBuf::from(root.trim()).join("bin/python3");
    path.is_file().then_some(path)
}

pub(crate) fn explicit_python(name: &str) -> Option<PathBuf> {
    let value = std::env::var(name).ok()?;
    let path = PathBuf::from(value.trim());
    if path.is_absolute() && path.is_file() {
        Some(path)
    } else {
        None
    }
}

pub(crate) fn source_core_dir() -> Option<PathBuf> {
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
