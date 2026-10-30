//! All-local provider activity for the usage report.
//!
//! The collector (see `usage_collect`) leaves two durable projections under
//! `<home>/telemetry/` that already carry the providers' own parsed formats:
//!
//! * `<agent>/scan-cache.json` — `files{<path>: {days:[{day,model,<buckets>}],
//!   record?:{id,mtime,..}}}`, written by `fleet_engine` for six providers and
//!   by the kimi harvest for kimi;
//! * `archive/<agent>.jsonl` — day/model aggregates, last-wins per key.
//!
//! This module only *reads* them. It never invents a measurement: a local
//! session is `observed` — not `completed`, no exit code — a provider with
//! no token ledger (agy: estimated transcripts) is reported as `unavailable`
//! in [`LocalSource`], never as zero, and a session whose rows carry no token
//! bucket keeps every token field `unknown` instead of posing as a measured 0.
//!
//! A local session that a Vibecrafted run already accounts for is not counted
//! again: the VC row (origin `vc`) wins and the match is counted in
//! [`LocalSource::matched_vc`].

use std::collections::{BTreeMap, HashSet};
use std::fs;
use std::path::Path;

use chrono::{DateTime, NaiveDate, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use crate::usage::{
    USAGE_PROVIDER_ADAPTERS, UsageCost, UsageFilter, UsageRun, UsageTokens, matches_filter,
    regular_file, unknown,
};

#[path = "usage_local_cache.rs"]
mod projection;
pub(crate) use projection::project_local_usage;

const MAX_CACHE_BYTES: u64 = 256 * 1024 * 1024;
const MIN_SESSION_ID_LEN: usize = 8;
const LONG_SESSION_ID_LEN: usize = 16;

/// What one provider contributed to the local side of the report.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct LocalSource {
    pub agent: String,
    /// `scan_cache`, `archive`, `loading`, `unavailable` or `absent`.
    pub source: String,
    pub sessions_seen: usize,
    pub counted: usize,
    /// Local sessions already carried by a VC run and therefore not re-counted.
    pub matched_vc: usize,
    /// VC runs of this agent with no recorded session id (cannot be matched).
    pub vc_without_session_id: usize,
    /// Archive day rows withheld because VC runs already cover that day.
    pub withheld_days: usize,
    pub note: String,
    #[serde(default)]
    pub refreshing: bool,
    /// Previous measurements retained during changed or unreadable input.
    #[serde(default)]
    pub stale: bool,
    /// Collection time of this read model, not a provider event timestamp.
    #[serde(default)]
    pub refreshed_at: Option<String>,
    /// Bounded reason code; never a raw file path or parser exception.
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Clone, Debug, Default)]
pub struct LocalUsage {
    pub runs: Vec<UsageRun>,
    pub sources: Vec<LocalSource>,
}

/// Sessions and days the runtime already accounted for, per agent.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct VcSessions {
    ids: BTreeMap<String, HashSet<String>>,
    without_id: BTreeMap<String, usize>,
    days: BTreeMap<String, HashSet<String>>,
}

impl VcSessions {
    #[must_use]
    pub fn from_runs(runs: &[UsageRun]) -> Self {
        let mut sessions = Self::default();
        for run in runs {
            let Some(agent) = run.agent.as_str().map(str::to_ascii_lowercase) else {
                continue;
            };
            if let Some(day) = run.recorded_at.get(..10) {
                sessions
                    .days
                    .entry(agent.clone())
                    .or_default()
                    .insert(day.to_string());
            }
            match run.provider_session_id.as_str() {
                Some(id) if id.len() >= MIN_SESSION_ID_LEN && id != "unknown" => {
                    sessions
                        .ids
                        .entry(agent)
                        .or_default()
                        .insert(id.to_string());
                }
                _ => *sessions.without_id.entry(agent).or_default() += 1,
            }
        }
        sessions
    }

    fn contains(&self, agent: &str, path_key: &str, record_id: &str) -> bool {
        let Some(ids) = self.ids.get(agent) else {
            return false;
        };
        if !record_id.is_empty() && ids.contains(record_id) {
            return true;
        }
        // Provider session ids appear as a path component or inside a file stem
        // (`rollout-<date>-<uuid>.jsonl`, `<uuid>.jsonl`, `<id>/wire.jsonl`).
        for component in Path::new(path_key).components() {
            let Some(name) = component.as_os_str().to_str() else {
                continue;
            };
            let stem = name.rsplit_once('.').map_or(name, |(stem, _)| stem);
            if ids.contains(name) || ids.contains(stem) {
                return true;
            }
            if ids
                .iter()
                .any(|id| id.len() >= LONG_SESSION_ID_LEN && stem.contains(id.as_str()))
            {
                return true;
            }
        }
        false
    }

    fn covers_day(&self, agent: &str, day: &str) -> bool {
        self.days.get(agent).is_some_and(|days| days.contains(day))
    }

    fn without_id(&self, agent: &str) -> usize {
        self.without_id.get(agent).copied().unwrap_or(0)
    }
}

/// Read every provider's local projection.
#[must_use]
pub fn read_local_usage(
    telemetry_dir: &Path,
    vc: &VcSessions,
    cutoff: Option<DateTime<Utc>>,
    filter: &UsageFilter,
) -> LocalUsage {
    let mut usage = LocalUsage::default();
    for agent in USAGE_PROVIDER_ADAPTERS {
        let mut source = LocalSource {
            agent: (*agent).to_string(),
            source: "absent".to_string(),
            vc_without_session_id: vc.without_id(agent),
            ..LocalSource::default()
        };
        let cache = telemetry_dir.join(agent).join("scan-cache.json");
        let archive = telemetry_dir.join("archive").join(format!("{agent}.jsonl"));
        let cached = read_scan_cache(&cache);
        let cache_failed = cached.is_err();
        if let Ok(Some(files)) = cached {
            source.source = "scan_cache".to_string();
            read_scan_entries(
                agent,
                &files,
                vc,
                cutoff,
                filter,
                &mut source,
                &mut usage.runs,
            );
        } else if regular_file(&archive) {
            source.source = "archive".to_string();
            read_archive(
                agent,
                &archive,
                vc,
                cutoff,
                filter,
                &mut source,
                &mut usage.runs,
            );
        } else if *agent == "agy" {
            source.source = "unavailable".to_string();
            source.note =
                "agy keeps estimated transcripts only; no provider token ledger".to_string();
        } else if *agent == "cursor" {
            source.source = "unavailable".to_string();
            source.note = "cursor usage lives in the Cursor cloud".to_string();
        } else {
            source.note = "no local projection collected yet".to_string();
        }
        if cache_failed {
            source.error = Some("scan_cache_unreadable".into());
        }
        usage.sources.push(source);
    }
    usage
}

fn read_scan_cache(path: &Path) -> Result<Option<Map<String, Value>>, ()> {
    if !regular_file(path) {
        return Ok(None);
    }
    if fs::metadata(path).map_err(|_| ())?.len() > MAX_CACHE_BYTES {
        return Err(());
    }
    let mut value =
        serde_json::from_slice::<Value>(&fs::read(path).map_err(|_| ())?).map_err(|_| ())?;
    // Move the provider map out: cloning it duplicated the complete 221MB
    // native cache on every foreground request.
    match value.as_object_mut().ok_or(())?.remove("files") {
        Some(Value::Object(files)) => Ok(Some(files)),
        None => Ok(None),
        _ => Err(()),
    }
}

const TOKEN_KEYS: [&str; 4] = [
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
];

fn bucket(row: &Value, key: &str) -> u64 {
    row.get(key).and_then(Value::as_u64).unwrap_or(0)
}

/// A row measures tokens only when it carries at least one token bucket. A
/// session the scanner saw without any bucket is real activity with an
/// unmeasured size — reported as unknown, never as a known zero.
fn measures_tokens(row: &Value) -> bool {
    TOKEN_KEYS
        .iter()
        .any(|key| row.get(key).and_then(Value::as_u64).is_some())
}

fn read_scan_entries(
    agent: &str,
    files: &Map<String, Value>,
    vc: &VcSessions,
    cutoff: Option<DateTime<Utc>>,
    filter: &UsageFilter,
    source: &mut LocalSource,
    out: &mut Vec<UsageRun>,
) {
    let mut seen_ids: HashSet<String> = HashSet::new();
    for (path_key, entry) in files {
        let Some(days) = entry.get("days").and_then(Value::as_array) else {
            continue;
        };
        let record = entry.get("record").and_then(Value::as_object);
        let record_id = record
            .and_then(|record| record.get("id"))
            .and_then(Value::as_str)
            .unwrap_or("");
        source.sessions_seen += 1;
        if vc.contains(agent, path_key, record_id) {
            source.matched_vc += 1;
            continue;
        }
        // A moved file keeps its session id; count it once.
        if !record_id.is_empty() && !seen_ids.insert(record_id.to_string()) {
            continue;
        }
        let (mut input, mut output, mut cache_read, mut cache_write) = (0u64, 0u64, 0u64, 0u64);
        let mut measured = false;
        let mut by_model: BTreeMap<String, u64> = BTreeMap::new();
        let mut last_day: Option<String> = None;
        for row in days {
            measured |= measures_tokens(row);
            let (row_input, row_output) =
                (bucket(row, "input_tokens"), bucket(row, "output_tokens"));
            let (row_read, row_write) = (
                bucket(row, "cache_read_tokens"),
                bucket(row, "cache_creation_tokens"),
            );
            input = input.saturating_add(row_input);
            output = output.saturating_add(row_output);
            cache_read = cache_read.saturating_add(row_read);
            cache_write = cache_write.saturating_add(row_write);
            let model = row.get("model").and_then(Value::as_str).unwrap_or("");
            if !model.is_empty() {
                let row_total = row_input
                    .saturating_add(row_output)
                    .saturating_add(row_read)
                    .saturating_add(row_write);
                *by_model.entry(model.to_string()).or_default() += row_total;
            }
            if let Some(day) = row.get("day").and_then(Value::as_str) {
                if last_day.as_deref().is_none_or(|last| day > last) {
                    last_day = Some(day.to_string());
                }
            }
        }
        let total = input
            .saturating_add(output)
            .saturating_add(cache_read)
            .saturating_add(cache_write);
        let tokens = measured.then_some([input, cache_read, cache_write, output, total]);
        let Some(recorded) = recorded_at(record, last_day.as_deref()) else {
            continue;
        };
        if cutoff.is_some_and(|cutoff| recorded < cutoff) {
            continue;
        }
        let model = by_model
            .iter()
            .max_by_key(|(_, tokens)| **tokens)
            .map(|(name, _)| json!(name))
            .unwrap_or_else(|| unknown("local session carried no model"));
        let session = if record_id.is_empty() {
            unknown("local session has no recorded id")
        } else {
            json!(record_id)
        };
        let run = local_run(
            agent,
            format!("local-{agent}-{}", slug(path_key, record_id)),
            recorded,
            model,
            tokens,
            "local_scan",
            session,
        );
        if matches_filter(&run, filter) {
            source.counted += 1;
            out.push(run);
        }
    }
}

fn recorded_at(
    record: Option<&Map<String, Value>>,
    last_day: Option<&str>,
) -> Option<DateTime<Utc>> {
    if let Some(mtime) = record
        .and_then(|record| record.get("mtime"))
        .and_then(Value::as_f64)
        .filter(|mtime| mtime.is_finite() && *mtime > 0.0)
    {
        return DateTime::from_timestamp(mtime as i64, 0);
    }
    end_of_day(last_day?)
}

fn end_of_day(day: &str) -> Option<DateTime<Utc>> {
    let date = NaiveDate::parse_from_str(day, "%Y-%m-%d").ok()?;
    Some(date.and_hms_opt(23, 59, 59)?.and_utc())
}

fn slug(path_key: &str, record_id: &str) -> String {
    let digest = Sha256::digest(path_key.as_bytes());
    let short: String = digest
        .iter()
        .take(6)
        .map(|byte| format!("{byte:02x}"))
        .collect();
    let id: String = record_id
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .take(24)
        .collect();
    if id.is_empty() {
        short
    } else {
        format!("{id}-{short}")
    }
}

fn read_archive(
    agent: &str,
    path: &Path,
    vc: &VcSessions,
    cutoff: Option<DateTime<Utc>>,
    filter: &UsageFilter,
    source: &mut LocalSource,
    out: &mut Vec<UsageRun>,
) {
    let Ok(text) = fs::read_to_string(path) else {
        source.error = Some("archive_unreadable".into());
        return;
    };
    // Last-wins per (day, model), exactly as `usage_archive.read_last_wins`.
    let mut rows: BTreeMap<(String, String), Value> = BTreeMap::new();
    for line in text.lines().map(str::trim).filter(|line| !line.is_empty()) {
        let Ok(row) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        let Some(day) = row.get("day").and_then(Value::as_str) else {
            continue;
        };
        let model = row.get("model").and_then(Value::as_str).unwrap_or("");
        rows.insert((day.to_string(), model.to_string()), row);
    }
    for ((day, model), row) in rows {
        source.sessions_seen += 1;
        if vc.covers_day(agent, &day) {
            // Day aggregates carry no session ids; counting them next to the
            // VC rows of the same day would double-count. Withheld, visibly.
            source.withheld_days += 1;
            continue;
        }
        let Some(recorded) = end_of_day(&day) else {
            continue;
        };
        if cutoff.is_some_and(|cutoff| recorded < cutoff) {
            continue;
        }
        let (input, output) = (bucket(&row, "input_tokens"), bucket(&row, "output_tokens"));
        let (read, write) = (
            bucket(&row, "cache_read_tokens"),
            bucket(&row, "cache_creation_tokens"),
        );
        let tokens = measures_tokens(&row).then_some([
            input,
            read,
            write,
            output,
            input
                .saturating_add(output)
                .saturating_add(read)
                .saturating_add(write),
        ]);
        let run = local_run(
            agent,
            format!("archive-{agent}-{day}-{}", slug(&model, "")),
            recorded,
            if model.is_empty() {
                unknown("archive row carried no model")
            } else {
                json!(model)
            },
            tokens,
            "local_archive",
            unknown("archive day aggregation has no session id"),
        );
        if matches_filter(&run, filter) {
            source.counted += 1;
            out.push(run);
        }
    }
    if source.withheld_days > 0 {
        source.note = format!(
            "{} archive day row(s) withheld: runs recorded by Vibecrafted already cover those days",
            source.withheld_days
        );
    }
}

/// `tokens` is `[input, cache_read, cache_write, output, total]`, or `None`
/// when the projection carried no token measurement at all: the fields then
/// stay `unknown` instead of posing as a measured zero.
fn local_run(
    agent: &str,
    run_id: String,
    recorded: DateTime<Utc>,
    model: Value,
    tokens: Option<[u64; 5]>,
    source: &str,
    session: Value,
) -> UsageRun {
    let unmeasured = || unknown("local projection carried no token measurement");
    let (events, input, cache_read, cache_write, output, total) = match tokens {
        Some([input, cache_read, cache_write, output, total]) => (
            1,
            json!(input),
            json!(cache_read),
            json!(cache_write),
            json!(output),
            json!(total),
        ),
        None => (
            0,
            unmeasured(),
            unmeasured(),
            unmeasured(),
            unmeasured(),
            unmeasured(),
        ),
    };
    UsageRun {
        run_id,
        provider: json!(agent),
        agent: json!(agent),
        model,
        // Observed on the host, not settled by Vibecrafted: no completed/exit code.
        status: "observed".to_string(),
        exit_code: None,
        recorded_at: recorded.to_rfc3339(),
        tokens: UsageTokens {
            schema: "vibecrafted.usage.v1".to_string(),
            unit: "tokens".to_string(),
            source: source.to_string(),
            events,
            tokens_input: input,
            tokens_cached_input: cache_read,
            tokens_cache_write: cache_write,
            tokens_output: output,
            tokens_total: total,
            semantics: BTreeMap::new(),
        },
        cost: UsageCost {
            amount: unknown("cost is not recorded for host-observed sessions"),
            currency: None,
            unit: Some("USD".to_string()),
            source: source.to_string(),
        },
        failure_kind: None,
        failure: None,
        provider_session_id: session,
        telemetry_source: source.to_string(),
        origin: "local".to_string(),
        unpriced_models: Vec::new(),
    }
}
