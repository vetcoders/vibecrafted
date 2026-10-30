//! Disposable projection over the collector's existing files. A request only
//! stats source versions and filters compact rows; native cache parsing and VC
//! deduplication have one background owner per telemetry root.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime};

use chrono::{DateTime, Utc};

use super::{LocalSource, LocalUsage, VcSessions, read_local_usage};
use crate::usage::{USAGE_PROVIDER_ADAPTERS, UsageFilter, matches_filter};

#[derive(Clone, PartialEq, Eq)]
enum Stamp {
    Missing,
    File(u64, SystemTime),
    Unreadable,
}

#[derive(Clone, PartialEq, Eq)]
struct Version {
    files: Vec<Stamp>,
    vc: VcSessions,
}

impl Version {
    fn read(root: &Path, vc: &VcSessions) -> Self {
        let files = USAGE_PROVIDER_ADAPTERS
            .iter()
            .flat_map(|agent| {
                [
                    root.join(agent).join("scan-cache.json"),
                    root.join("archive").join(format!("{agent}.jsonl")),
                ]
            })
            .map(|path| match fs::symlink_metadata(path) {
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => Stamp::Missing,
                Ok(meta) if meta.is_file() && !meta.file_type().is_symlink() => {
                    meta.modified().map_or(Stamp::Unreadable, |modified| {
                        Stamp::File(meta.len(), modified)
                    })
                }
                _ => Stamp::Unreadable,
            })
            .collect();
        Self {
            files,
            vc: vc.clone(),
        }
    }
}

#[derive(Default)]
struct Cache {
    measured: Option<(Version, Arc<LocalUsage>)>,
    attempted: Option<(Version, Instant)>,
    refreshing: bool,
    failed: bool,
}

type Shared = Arc<Mutex<Cache>>;
static ROOTS: OnceLock<Mutex<BTreeMap<PathBuf, Shared>>> = OnceLock::new();

fn scope(root: &Path) -> Option<Shared> {
    let mut roots = ROOTS
        .get_or_init(Mutex::default)
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if let Some(cache) = roots.get(root) {
        return Some(Arc::clone(cache));
    }
    if roots.len() >= 16 {
        let idle = roots
            .iter()
            .find(|(_, cache)| Arc::strong_count(cache) == 1)
            .map(|(root, _)| root.clone())?;
        roots.remove(&idle);
    }
    let cache = Arc::new(Mutex::new(Cache::default()));
    roots.insert(root.to_path_buf(), Arc::clone(&cache));
    Some(cache)
}

pub(crate) fn project_local_usage(
    root: &Path,
    vc: &VcSessions,
    cutoff: Option<DateTime<Utc>>,
    filter: &UsageFilter,
) -> LocalUsage {
    let version = Version::read(root, vc);
    // No provider ledger means no expensive work and no pending claim.
    if version.files.iter().all(|stamp| *stamp == Stamp::Missing) {
        return LocalUsage {
            runs: Vec::new(),
            sources: USAGE_PROVIDER_ADAPTERS
                .iter()
                .map(|agent| {
                    let (source, note) = match *agent {
                        "agy" => (
                            "unavailable",
                            "agy keeps estimated transcripts only; no provider token ledger",
                        ),
                        "cursor" => ("unavailable", "cursor usage lives in the Cursor cloud"),
                        _ => ("absent", "no local projection collected yet"),
                    };
                    LocalSource {
                        agent: (*agent).into(),
                        source: source.into(),
                        note: note.into(),
                        vc_without_session_id: vc.without_id(agent),
                        ..LocalSource::default()
                    }
                })
                .collect(),
        };
    }
    let Some(shared) = scope(root) else {
        return pending("Local projection capacity is busy", false);
    };
    let (snapshot, refreshing, stale) = {
        let mut cache = shared
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let due = cache.attempted.as_ref().is_none_or(|(attempted, at)| {
            *attempted != version || (cache.failed && at.elapsed() >= Duration::from_secs(60))
        });
        if due && !cache.refreshing {
            cache.refreshing = true;
            cache.attempted = Some((version.clone(), Instant::now()));
            let owner = Arc::clone(&shared);
            let root = root.to_path_buf();
            let selected = version.clone();
            if std::thread::Builder::new()
                .name("usage-local-projection".into())
                .spawn(move || refresh(owner, root, selected))
                .is_err()
            {
                cache.refreshing = false;
                cache.failed = true;
            }
        }
        // A changed VC deduplication scope must not count an old local twin
        // beside its newly recorded VC run while the replacement is loading.
        let snapshot = cache
            .measured
            .as_ref()
            .filter(|(key, _)| key.vc == version.vc)
            .map(|(_, rows)| Arc::clone(rows));
        let stale = cache
            .measured
            .as_ref()
            .is_some_and(|(key, _)| *key != version);
        (snapshot, cache.refreshing, stale)
    };
    let Some(snapshot) = snapshot else {
        return pending("Local provider measurements are loading", refreshing);
    };
    let runs = snapshot
        .runs
        .iter()
        .filter(|run| {
            cutoff.is_none_or(|since| {
                DateTime::parse_from_rfc3339(&run.recorded_at).is_ok_and(|stamp| stamp >= since)
            }) && matches_filter(run, filter)
        })
        .cloned()
        .collect::<Vec<_>>();
    let mut sources = snapshot.sources.clone();
    for source in &mut sources {
        source.refreshing = refreshing;
        source.stale |= stale;
        source.counted = runs
            .iter()
            .filter(|run| run.agent.as_str() == Some(source.agent.as_str()))
            .count();
    }
    LocalUsage { runs, sources }
}

fn pending(note: &str, refreshing: bool) -> LocalUsage {
    LocalUsage {
        runs: Vec::new(),
        sources: USAGE_PROVIDER_ADAPTERS
            .iter()
            .map(|agent| LocalSource {
                agent: (*agent).into(),
                source: if refreshing && !matches!(*agent, "agy" | "cursor") {
                    "loading"
                } else {
                    "unavailable"
                }
                .into(),
                refreshing: refreshing && !matches!(*agent, "agy" | "cursor"),
                note: match *agent {
                    "agy" => "agy keeps estimated transcripts only; no provider token ledger",
                    "cursor" => "cursor usage lives in the Cursor cloud",
                    _ => note,
                }
                .into(),
                error: (!refreshing && !matches!(*agent, "agy" | "cursor"))
                    .then(|| "local_projection_busy".into()),
                ..LocalSource::default()
            })
            .collect(),
    }
}

struct Flight(Shared);
impl Drop for Flight {
    fn drop(&mut self) {
        self.0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .refreshing = false;
    }
}

fn refresh(shared: Shared, root: PathBuf, version: Version) {
    let _flight = Flight(Arc::clone(&shared));
    let mut next = read_local_usage(&root, &version.vc, None, &UsageFilter::default());
    let mut cache = shared
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let previous = cache
        .measured
        .as_ref()
        .filter(|(key, _)| key.vc == version.vc)
        .map(|(_, rows)| Arc::clone(rows));
    let stamp = Utc::now().to_rfc3339();
    for (index, source) in next.sources.iter_mut().enumerate() {
        if version.files[index * 2..index * 2 + 2].contains(&Stamp::Unreadable) {
            source.error = Some("projection_source_unreadable".into());
        }
        if source.error.is_some() {
            if let Some(previous) = &previous {
                next.runs
                    .retain(|run| run.agent.as_str() != Some(source.agent.as_str()));
                next.runs.extend(
                    previous
                        .runs
                        .iter()
                        .filter(|run| run.agent.as_str() == Some(source.agent.as_str()))
                        .cloned(),
                );
                if let Some(old) = previous
                    .sources
                    .iter()
                    .find(|old| old.agent == source.agent)
                {
                    let error = source.error.clone();
                    *source = old.clone();
                    source.error = error;
                }
            }
            source.stale = true;
        } else {
            source.refreshed_at = Some(stamp.clone());
        }
    }
    cache.failed = next.sources.iter().any(|source| source.error.is_some());
    cache.measured = Some((version, Arc::new(next)));
}
