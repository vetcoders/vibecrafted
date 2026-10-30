//! Disposable read model for legacy transcript recovery. The Python telemetry
//! parser remains the parser; canonical run metadata is never written here.
//! Source fingerprints invalidate results and negative results back off. Each
//! home has one bounded background batch, shared across filters and callers.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::{Arc, Mutex, OnceLock, mpsc};
use std::time::{Duration, Instant, SystemTime};

use chrono::Utc;
use serde_json::{Map, Value, json};

use super::{UsageRecovery, UsageRun, apply_lazy_telemetry, telemetry_python};

const BATCH_SIZE: usize = 128;
const MAX_SCOPES: usize = 32;
const MAX_ENTRIES: usize = 4096;
const MAX_OUTPUT: u64 = 4 * 1024 * 1024;
const DEADLINE: Duration = Duration::from_secs(10);
const RETRY_AFTER: Duration = Duration::from_secs(60);

#[derive(Clone, Debug, PartialEq, Eq)]
struct FileStamp {
    length: u64,
    modified: Option<SystemTime>,
}

impl FileStamp {
    fn read(path: &Path) -> Option<Self> {
        let meta = fs::symlink_metadata(path).ok()?;
        (meta.is_file() && !meta.file_type().is_symlink()).then(|| Self {
            length: meta.len(),
            modified: meta.modified().ok(),
        })
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct Version {
    meta: FileStamp,
    transcript: FileStamp,
    generation: Generation,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct Generation {
    core: Option<PathBuf>,
    parser_files: Vec<Option<FileStamp>>,
    python: Option<PathBuf>,
}

impl Generation {
    fn read() -> Self {
        let core = super::source_core_dir();
        // telemetry.py owns the pricebook as well as the recovery contract.
        let parser_files = [
            "telemetry.py",
            "harness_usage.py",
            "agent_stream.py",
            "failure_attribution.py",
        ]
        .iter()
        .map(|name| {
            core.as_ref()
                .and_then(|root| FileStamp::read(&root.join("vibecrafted_core").join(name)))
        })
        .collect();
        Self {
            core,
            parser_files,
            python: telemetry_python().map(|command| PathBuf::from(command.get_program())),
        }
    }
}

#[derive(Clone)]
struct Job {
    index: usize,
    id: String,
    meta: PathBuf,
    transcript: PathBuf,
    version: Version,
}

struct Entry {
    attempted: Version,
    attempted_at: Instant,
    measured: Option<Measured>,
    complete: bool,
    failure: Option<String>,
}

struct Measured {
    version: Version,
    telemetry: Map<String, Value>,
    stale_fields: BTreeSet<String>,
}

#[derive(Default)]
struct Cache {
    entries: BTreeMap<String, Entry>,
    refreshing: bool,
    refreshed_at: Option<String>,
    error: Option<String>,
}

type SharedCache = Arc<Mutex<Cache>>;
static SCOPES: OnceLock<Mutex<BTreeMap<PathBuf, SharedCache>>> = OnceLock::new();

fn scope(root: &Path) -> Option<SharedCache> {
    let mut scopes = SCOPES
        .get_or_init(Mutex::default)
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if let Some(cache) = scopes.get(root) {
        return Some(Arc::clone(cache));
    }
    if scopes.len() >= MAX_SCOPES {
        let idle = scopes
            .iter()
            .find(|(_, cache)| Arc::strong_count(cache) == 1)
            .map(|(path, _)| path.clone())?;
        scopes.remove(&idle);
    }
    let cache = Arc::new(Mutex::new(Cache::default()));
    scopes.insert(root.to_path_buf(), Arc::clone(&cache));
    Some(cache)
}

pub(super) fn project(root: &Path, runs: &mut [UsageRun]) -> UsageRecovery {
    let generation = Generation::read();
    let candidates = runs
        .iter()
        .enumerate()
        .filter(|(_, run)| {
            run.tokens.tokens_total.as_u64().is_none() || run.cost.amount.as_f64().is_none()
        })
        .filter_map(|(index, run)| {
            let meta = root.join(&run.run_id).join("meta.json");
            let transcript = root.join(&run.run_id).join("transcript.log");
            let version = Version {
                meta: FileStamp::read(&meta)?,
                transcript: FileStamp::read(&transcript)?,
                generation: generation.clone(),
            };
            (version.transcript.length != 0).then(|| Job {
                index,
                id: run.run_id.clone(),
                meta,
                transcript,
                version,
            })
        })
        .collect::<Vec<_>>();
    if candidates.is_empty() {
        return UsageRecovery::default();
    }
    let Some(shared) = scope(root) else {
        return UsageRecovery {
            unavailable_runs: candidates.len(),
            last_error: Some(
                "Transcript recovery is busy; recorded usage remains available".into(),
            ),
            ..UsageRecovery::default()
        };
    };
    let mut cache = shared
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let mut report = UsageRecovery::default();
    let mut jobs = Vec::new();
    for job in candidates {
        let entry = cache.entries.get(&job.id);
        if let Some(measured) = entry.and_then(|entry| entry.measured.as_ref())
            && measured.version.meta == job.version.meta
            && let Some(run) = runs.get_mut(job.index)
        {
            let stale = [
                ("usage", run.tokens.tokens_total.as_u64().is_none()),
                ("cost", run.cost.amount.as_f64().is_none()),
            ]
            .into_iter()
            .filter(|(field, missing)| *missing && known(&measured.telemetry, field))
            .filter(|(field, _)| {
                measured.version != job.version || measured.stale_fields.contains(*field)
            })
            .map(|(field, _)| field.to_string())
            .collect::<Vec<_>>();
            apply_lazy_telemetry(run, &measured.telemetry);
            report.cached_runs += 1;
            if !stale.is_empty() {
                report.stale_runs += 1;
                report.stale_fields.insert(job.id.clone(), stale);
            }
        }
        let due = entry.is_none_or(|entry| needs_refresh(entry, &job.version));
        if let Some(reason) = entry.and_then(|entry| entry.failure.as_ref()) {
            *report.failure_reasons.entry(reason.clone()).or_default() += 1;
        }
        if due {
            report.pending_runs += 1;
            jobs.push(job);
        } else if entry.is_some_and(|entry| entry.measured.is_none()) {
            report.unavailable_runs += 1;
        }
    }
    // Untouched small sources advance before a repeatedly failing large one.
    jobs.sort_by_key(|job| {
        (
            cache
                .entries
                .get(&job.id)
                .is_some_and(|entry| entry.failure.is_some()),
            job.version.transcript.length,
        )
    });
    jobs.truncate(BATCH_SIZE);
    if !cache.refreshing && !jobs.is_empty() {
        cache.refreshing = true;
        let worker_cache = Arc::clone(&shared);
        if std::thread::Builder::new()
            .name("usage-transcript-recovery".into())
            .spawn(move || refresh(worker_cache, jobs))
            .is_err()
        {
            cache.refreshing = false;
            cache.error = Some("Background transcript recovery could not start".into());
        }
    }
    report.refreshing = cache.refreshing;
    report.refreshed_at.clone_from(&cache.refreshed_at);
    report.last_error.clone_from(&cache.error);
    report
}

fn refresh(shared: SharedCache, jobs: Vec<Job>) {
    struct Flight(SharedCache);
    impl Drop for Flight {
        fn drop(&mut self) {
            self.0
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .refreshing = false;
        }
    }
    let _flight = Flight(Arc::clone(&shared));
    let Batch {
        mut rows,
        mut failures,
        error,
        started,
        timed_out,
    } = recover(&jobs);
    let mut cache = shared
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    for job in jobs {
        if timed_out
            && !started.is_empty()
            && !started.contains(&job.id)
            && !rows.contains_key(&job.id)
            && !failures.contains_key(&job.id)
        {
            continue; // Python never reached it: do not invent an attempt/backoff.
        }
        let row = rows.remove(&job.id);
        let complete = row.as_ref().is_some_and(complete_observation);
        let failure = failures.remove(&job.id).or_else(|| {
            (!complete).then(|| {
                if timed_out {
                    "timeout"
                } else if error.is_some() {
                    "batch_failed"
                } else {
                    "incomplete_evidence"
                }
                .to_string()
            })
        });
        let previous = cache
            .entries
            .remove(&job.id)
            .and_then(|entry| entry.measured);
        let measured = match row {
            Some(row) => merge_measurement(row, previous, &job.version),
            None => previous,
        };
        if cache.entries.len() >= MAX_ENTRIES
            && let Some(oldest) = cache.entries.keys().next().cloned()
        {
            cache.entries.remove(&oldest);
        }
        cache.entries.insert(
            job.id,
            Entry {
                attempted: job.version,
                attempted_at: Instant::now(),
                measured,
                complete,
                failure,
            },
        );
    }
    cache.refreshed_at = Some(Utc::now().to_rfc3339());
    cache.error = error;
}

fn known(row: &Map<String, Value>, field: &str) -> bool {
    match field {
        "usage" => row
            .get(field)
            .and_then(|row| row.get("tokens_total"))
            .and_then(Value::as_u64)
            .is_some(),
        "cost" => row
            .get(field)
            .and_then(|row| row.get("amount"))
            .and_then(Value::as_f64)
            .is_some(),
        _ => false,
    }
}

fn merge_measurement(
    mut row: Map<String, Value>,
    previous: Option<Measured>,
    version: &Version,
) -> Option<Measured> {
    let mut stale_fields = BTreeSet::new();
    if let Some(previous) = previous.filter(|previous| previous.version.meta == version.meta) {
        for field in ["usage", "cost"] {
            if !known(&row, field) && known(&previous.telemetry, field) {
                row.insert(field.into(), previous.telemetry[field].clone());
                if previous.version != *version || previous.stale_fields.contains(field) {
                    stale_fields.insert(field.into());
                }
            }
        }
    }
    (known(&row, "usage") || known(&row, "cost")).then(|| Measured {
        version: version.clone(),
        telemetry: row,
        stale_fields,
    })
}

fn needs_refresh(entry: &Entry, version: &Version) -> bool {
    entry.attempted != *version || (!entry.complete && entry.attempted_at.elapsed() >= RETRY_AFTER)
}

fn complete_observation(row: &Map<String, Value>) -> bool {
    let tokens = row
        .get("usage")
        .and_then(|usage| usage.get("tokens_total"))
        .is_some_and(|tokens| {
            tokens.as_u64().is_some()
                || tokens.get("value").and_then(Value::as_str) == Some("unknown")
        });
    tokens
        && row
            .get("cost")
            .and_then(|cost| cost.get("amount"))
            .is_some_and(|amount| {
                amount.as_f64().is_some()
                    || amount.get("value").and_then(Value::as_str) == Some("unknown")
            })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_successfully_unpriced_source_stays_complete_after_failure_backoff_expires() {
        let row = json!({"usage":{"tokens_total":51},"cost":{"amount":{"value":"unknown","reason":"model is unpriced"}}});
        let version = Version {
            meta: FileStamp {
                length: 1,
                modified: None,
            },
            transcript: FileStamp {
                length: 2,
                modified: None,
            },
            generation: Generation {
                core: None,
                parser_files: Vec::new(),
                python: None,
            },
        };
        let entry = Entry {
            attempted: version.clone(),
            attempted_at: Instant::now() - RETRY_AFTER * 2,
            measured: Some(Measured {
                version: version.clone(),
                telemetry: row.as_object().unwrap().clone(),
                stale_fields: BTreeSet::new(),
            }),
            complete: complete_observation(row.as_object().unwrap()),
            failure: None,
        };
        assert!(
            entry.complete,
            "unknown price is a complete observation, not parser failure"
        );
        assert!(
            !needs_refresh(&entry, &version),
            "unchanged unpriced transcripts are not repeatedly parsed"
        );
        let mut changed = version;
        changed.generation.parser_files.push(Some(FileStamp {
            length: 3,
            modified: None,
        }));
        assert!(
            needs_refresh(&entry, &changed),
            "parser/pricebook changes invalidate even a complete observation"
        );
    }

    #[test]
    fn partial_recovery_keeps_previous_fields_without_calling_them_fresh() {
        let mut version = Version {
            meta: FileStamp {
                length: 1,
                modified: None,
            },
            transcript: FileStamp {
                length: 1,
                modified: None,
            },
            generation: Generation {
                core: None,
                parser_files: Vec::new(),
                python: None,
            },
        };
        let row = json!({"usage":{"tokens_total":51,"source":"prior-provider-usage"},"cost":{"amount":{"value":"unknown"}}});
        let first = merge_measurement(row.as_object().unwrap().clone(), None, &version);
        version.transcript.length = 2;
        let cost_only = json!({"usage":{"tokens_total":{"value":"unknown"}},"cost":{"amount":0.2,"currency":"USD","source":"provider-cost"}});
        let second =
            merge_measurement(cost_only.as_object().unwrap().clone(), first, &version).unwrap();
        assert_eq!(second.telemetry["usage"]["tokens_total"], 51);
        assert_eq!(second.telemetry["cost"]["amount"], 0.2);
        assert_eq!(second.stale_fields, BTreeSet::from(["usage".into()]));
        version.transcript.length = 3;
        let tokens_only =
            json!({"usage":{"tokens_total":71},"cost":{"amount":{"value":"unknown"}}});
        let third = merge_measurement(
            tokens_only.as_object().unwrap().clone(),
            Some(second),
            &version,
        )
        .unwrap();
        assert_eq!(third.telemetry["usage"]["tokens_total"], 71);
        assert_eq!(third.telemetry["cost"]["amount"], 0.2);
        assert_eq!(third.stale_fields, BTreeSet::from(["cost".into()]));
    }
}

const SCRIPT: &str = r#"
import json, sys
from pathlib import Path
from vibecrafted_core.telemetry import run_telemetry_from_meta
for item in json.load(sys.stdin):
    print(json.dumps({"run_id": item["run_id"], "event": "started"}), flush=True)
    try:
        meta = json.loads(Path(item["meta"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(json.dumps({"run_id": item["run_id"], "error_code": "metadata_read_failed"}), flush=True)
        continue
    if not isinstance(meta, dict):
        print(json.dumps({"run_id": item["run_id"], "error_code": "metadata_invalid"}), flush=True)
        continue
    try:
        row = run_telemetry_from_meta(meta, transcript=Path(item["transcript"]))
        print(json.dumps({"run_id": item["run_id"], "telemetry": row}), flush=True)
    except Exception:
        print(json.dumps({"run_id": item["run_id"], "error_code": "parser_failed"}), flush=True)
"#;

#[derive(Default)]
struct Batch {
    rows: BTreeMap<String, Map<String, Value>>,
    failures: BTreeMap<String, String>,
    error: Option<String>,
    started: BTreeSet<String>,
    timed_out: bool,
}

fn recover(jobs: &[Job]) -> Batch {
    let failure = |message: &str| Batch {
        error: Some(message.to_string()),
        ..Batch::default()
    };
    let Some(mut command) = telemetry_python() else {
        return failure("Transcript recovery interpreter unavailable");
    };
    command
        .arg("-c")
        .arg(SCRIPT)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let Ok(mut child) = command.spawn() else {
        return failure("Transcript recovery could not start");
    };
    let payload = serde_json::to_vec(
        &jobs
            .iter()
            .map(|job| {
                json!({
                    "run_id": job.id, "meta": job.meta, "transcript": job.transcript,
                })
            })
            .collect::<Vec<_>>(),
    )
    .unwrap_or_default();
    if let Some(mut stdin) = child.stdin.take() {
        std::thread::spawn(move || {
            let _ = stdin.write_all(&payload);
        });
    }
    let (send, receive) = mpsc::sync_channel(1);
    if let Some(stdout) = child.stdout.take() {
        std::thread::spawn(move || {
            let mut bytes = Vec::new();
            let _ = stdout.take(MAX_OUTPUT).read_to_end(&mut bytes);
            let _ = send.send(bytes);
        });
    }
    let deadline = Instant::now() + DEADLINE;
    let mut error = None;
    let mut timed_out = false;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                if !status.success() {
                    error = Some(format!(
                        "Transcript recovery process failed (exit {})",
                        status.code().unwrap_or(-1)
                    ));
                }
                break;
            }
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(20)),
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                error = Some("Transcript recovery process could not be observed".into());
                break;
            }
            Ok(None) => {
                // The canonical telemetry parser reads files only and the
                // product interpreter launcher execs it in this owned child.
                let _ = child.kill();
                let _ = child.wait();
                error = Some(
                    "Transcript recovery timed out; recorded measurements are preserved".into(),
                );
                timed_out = true;
                break;
            }
        }
    }
    let bytes = receive
        .recv_timeout(Duration::from_millis(250))
        .unwrap_or_default();
    if bytes.len() as u64 >= MAX_OUTPUT {
        error = Some("Transcript recovery output exceeded its bound".into());
    }
    let mut batch = Batch {
        error,
        timed_out,
        ..Batch::default()
    };
    for row in bytes
        .split(|byte| *byte == b'\n')
        .filter_map(|line| serde_json::from_slice::<Value>(line).ok())
    {
        let Some(id) = row.get("run_id").and_then(Value::as_str) else {
            continue;
        };
        if !jobs.iter().any(|job| job.id == id) {
            continue;
        }
        if row.get("event").and_then(Value::as_str) == Some("started") {
            batch.started.insert(id.to_string());
        } else if let Some(telemetry) = row.get("telemetry").and_then(Value::as_object) {
            batch.rows.insert(id.to_string(), telemetry.clone());
        } else if let Some(code) = row
            .get("error_code")
            .and_then(Value::as_str)
            .filter(|code| {
                ["metadata_read_failed", "metadata_invalid", "parser_failed"].contains(code)
            })
        {
            batch.failures.insert(id.to_string(), code.to_string());
        }
    }
    if batch.error.is_none() && !batch.failures.is_empty() {
        batch.error = Some(format!(
            "Transcript recovery failed for {} source(s); see failure_reasons",
            batch.failures.len()
        ));
    }
    batch
}
