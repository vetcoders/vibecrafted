//! The Rust eye on raw launcher evidence: `locks/**/*.lock`,
//! `artifacts/**/*.meta.json` and `marbles/**/state.json`.
//!
//! A raw source is liveness evidence only while it is fresh (younger than
//! `RUN_STALL_SECONDS`) or backed by a live process. Months-old locks, launch
//! sidecars and marbles loops must leave Live instead of haunting it as
//! `running`/`launching`, and an id whose terminal snapshot was archived under
//! `runs/.archived/` is closed history no raw file can resurrect.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration as StdDuration, SystemTime};

use chrono::{DateTime, Duration, Utc};
use control_core::read::{CacheStats, ProjectionCache};
use control_core::{ControlPlane, StateView};
use serde_json::json;
use std::sync::Arc;

const MONTH: i64 = 30 * 24 * 60 * 60;

fn temp_home(name: &str) -> PathBuf {
    let nanos = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let home = std::env::temp_dir().join(format!(
        "control-core-raw-{name}-{}-{nanos}",
        std::process::id()
    ));
    fs::create_dir_all(home.join("control_plane/runs")).expect("control plane");
    home
}

/// Backdate a raw file the way months of disk history would.
fn age_file(path: &Path, seconds: i64) {
    let age = StdDuration::from_secs(u64::try_from(seconds).expect("positive age"));
    fs::File::options()
        .write(true)
        .open(path)
        .expect("open raw file")
        .set_modified(SystemTime::now() - age)
        .expect("backdate raw file");
}

fn write_lock(home: &Path, run_id: &str, started: Option<&str>) -> PathBuf {
    let dir = home.join("locks/vetcoders/vibecrafted");
    fs::create_dir_all(&dir).expect("lock dir");
    let mut body = format!("run_id={run_id}\nagent=codex\nskill=impl\nroot=/repo\n");
    if let Some(started) = started {
        body.push_str(&format!("started={started}\n"));
    }
    body.push_str("status=running\n");
    let path = dir.join(format!("{run_id}.lock"));
    fs::write(&path, body).expect("write lock");
    path
}

fn write_meta(home: &Path, run_id: &str, payload: serde_json::Value) -> PathBuf {
    let dir = home.join("artifacts/vetcoders/vibecrafted/2026_0901/reports");
    fs::create_dir_all(&dir).expect("meta dir");
    let path = dir.join(format!("{run_id}.meta.json"));
    fs::write(&path, payload.to_string()).expect("write meta");
    path
}

fn write_marbles(home: &Path, run_id: &str, status: &str, updated_at: &str) -> PathBuf {
    let dir = home.join("marbles").join(run_id);
    fs::create_dir_all(&dir).expect("marbles dir");
    let path = dir.join("state.json");
    fs::write(
        &path,
        json!({
            "run_id": run_id,
            "agent": "codex",
            "status": status,
            "root": "/repo",
            "updated_at": updated_at,
            "started_at": updated_at,
            "loops": []
        })
        .to_string(),
    )
    .expect("write marbles state");
    path
}

fn iso_ago(now: DateTime<Utc>, seconds: i64) -> String {
    (now - Duration::seconds(seconds))
        .format("%Y-%m-%dT%H:%M:%SZ")
        .to_string()
}

fn is_live(view: &StateView, run_id: &str) -> bool {
    view.active_runs.iter().any(|run| run.run_id == run_id)
        || view.stalled_runs.iter().any(|run| run.run_id == run_id)
}

fn assert_left_live(plane: &ControlPlane, view: &StateView, run_id: &str, now: DateTime<Utc>) {
    assert!(!is_live(view, run_id), "{run_id} must leave Live");
    let run = plane
        .derived_run(run_id, now)
        .unwrap_or_else(|| panic!("{run_id} stays discoverable as history"));
    assert!(
        run.is_terminal() && run.health == "final",
        "{run_id} must settle to a final projection, got state={} health={}",
        run.state,
        run.health
    );
}

#[test]
fn stale_ownerless_lock_leaves_live_even_without_a_parseable_stamp() {
    let home = temp_home("locks");
    let now = Utc::now();
    let stale = 25 * 24 * 60 * 60;
    let stamped = write_lock(&home, "lock-stamped", Some(&iso_ago(now, stale)));
    let legacy = write_lock(&home, "lock-legacy", None);
    let epoch = (now - Duration::seconds(stale)).timestamp().to_string();
    let epoch_lock = write_lock(&home, "lock-epoch", Some(&epoch));
    for path in [&stamped, &legacy, &epoch_lock] {
        age_file(path, stale);
    }
    write_lock(&home, "lock-fresh", Some(&iso_ago(now, 30)));

    let plane = ControlPlane::new(&home);
    let view = plane.compute_view(now);

    for run_id in ["lock-stamped", "lock-legacy", "lock-epoch"] {
        assert_left_live(&plane, &view, run_id, now);
    }
    assert!(
        view.active_runs
            .iter()
            .any(|run| run.run_id == "lock-fresh"),
        "a just-written lock is still the only evidence of a fresh start"
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn archived_terminal_snapshot_is_the_verdict_for_raw_sources() {
    let home = temp_home("archived");
    let now = Utc::now();
    let archive = home.join("control_plane/runs/.archived");
    fs::create_dir_all(&archive).expect("archive dir");
    fs::write(
        archive.join("meta-archived.json"),
        json!({
            "run_id": "meta-archived",
            "state": "completed",
            "agent": "codex",
            "skill": "implement",
            "mode": "headless",
            "root": "/repo",
            "updated_at": iso_ago(now, MONTH),
            "started_at": iso_ago(now, MONTH),
            "health": "final",
            "source": "agent-meta",
            "lock_present": false,
            "exit_code": 0,
            "liveness": "terminal",
            "completed_at": iso_ago(now, MONTH)
        })
        .to_string(),
    )
    .expect("archived terminal snapshot");
    let meta = write_meta(
        &home,
        "meta-archived",
        json!({
            "run_id": "meta-archived",
            "agent": "codex",
            "status": "launching",
            "root": "/repo",
            "updated_at": iso_ago(now, MONTH),
            "started_at": iso_ago(now, MONTH)
        }),
    );
    age_file(&meta, MONTH);
    // Even a freshly stamped lock cannot reopen an archived verdict.
    write_lock(&home, "meta-archived", Some(&iso_ago(now, 30)));
    write_marbles(&home, "meta-archived", "promise", &iso_ago(now, 30));
    // Suppression is id-scoped: an unarchived fresh start stays live.
    write_lock(&home, "lock-fresh", Some(&iso_ago(now, 30)));

    let plane = ControlPlane::new(&home);
    let view = plane.compute_view(now);

    assert!(!is_live(&view, "meta-archived"));
    assert!(
        view.recent_runs
            .iter()
            .all(|run| run.run_id != "meta-archived"),
        "archived history must not re-enter the recent window from raw files"
    );
    assert!(
        plane
            .derived_runs(now)
            .iter()
            .all(|run| run.run_id != "meta-archived"),
        "raw sources must not resurrect an archived id"
    );
    assert!(
        view.active_runs
            .iter()
            .any(|run| run.run_id == "lock-fresh")
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn stale_marbles_loop_leaves_live_while_a_fresh_loop_stays() {
    let home = temp_home("marbles");
    let now = Utc::now();
    for (run_id, status) in [
        ("marb-promise", "promise"),
        ("marb-confirmed", "confirmed"),
        ("marb-statusless", ""),
    ] {
        let path = write_marbles(&home, run_id, status, &iso_ago(now, MONTH));
        age_file(&path, MONTH);
    }
    write_marbles(&home, "marb-fresh", "promise", &iso_ago(now, 60));

    let plane = ControlPlane::new(&home);
    let view = plane.compute_view(now);

    for run_id in ["marb-promise", "marb-confirmed", "marb-statusless"] {
        assert_left_live(&plane, &view, run_id, now);
    }
    assert!(
        view.active_runs
            .iter()
            .any(|run| run.run_id == "marb-fresh")
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn stale_launch_sidecar_leaves_live_even_without_stamps() {
    let home = temp_home("meta");
    let now = Utc::now();
    let stamped = write_meta(
        &home,
        "meta-stamped",
        json!({
            "run_id": "meta-stamped",
            "agent": "codex",
            "status": "launching",
            "root": "/repo",
            "updated_at": iso_ago(now, MONTH),
            "started_at": iso_ago(now, MONTH)
        }),
    );
    let stampless = write_meta(
        &home,
        "meta-stampless",
        json!({
            "run_id": "meta-stampless",
            "agent": "codex",
            "status": "running",
            "root": "/repo"
        }),
    );
    for path in [&stamped, &stampless] {
        age_file(path, MONTH);
    }
    write_meta(
        &home,
        "meta-fresh",
        json!({
            "run_id": "meta-fresh",
            "agent": "codex",
            "status": "launching",
            "root": "/repo",
            "updated_at": iso_ago(now, 45),
            "started_at": iso_ago(now, 45)
        }),
    );

    let plane = ControlPlane::new(&home);
    let view = plane.compute_view(now);

    for run_id in ["meta-stamped", "meta-stampless"] {
        assert_left_live(&plane, &view, run_id, now);
    }
    assert!(
        view.active_runs
            .iter()
            .any(|run| run.run_id == "meta-fresh")
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn cached_raw_paths_preserve_in_place_changes_deletions_time_and_archive_truth() {
    let home = temp_home("cached-paths");
    let now = Utc::now();
    let lock = write_lock(&home, "cached-lock", Some(&iso_ago(now, 30)));
    let meta = write_meta(
        &home,
        "cached-meta",
        json!({"run_id":"cached-meta", "agent":"codex", "status":"running", "root":"/repo", "started_at":iso_ago(now, 30), "updated_at":iso_ago(now, 30)}),
    );
    write_marbles(&home, "cached-marbles", "promise", &iso_ago(now, 30));
    let plane = ControlPlane::new(&home);
    let sources = plane.discover_raw_sources();
    let assert_same = |at| {
        assert_eq!(
            format!("{:?}", plane.compute_view_with_raw_sources(at, &sources)),
            format!("{:?}", plane.compute_view(at)),
            "cached names must preserve the canonical projection"
        );
    };
    assert_same(now);
    fs::write(&meta, json!({"run_id":"cached-meta", "agent":"codex", "status":"completed", "root":"/repo", "started_at":iso_ago(now, 30), "finished_at":now.to_rfc3339()}).to_string()).unwrap();
    assert_same(now);
    fs::remove_file(&lock).unwrap();
    assert_same(now);
    let archive = home.join("control_plane/runs/.archived");
    fs::create_dir_all(&archive).unwrap();
    fs::write(archive.join("cached-meta.json"), "{}").unwrap();
    assert_same(now);
    assert!(
        !plane
            .compute_view_with_raw_sources(now, &sources)
            .recent_runs
            .iter()
            .any(|r| r.run_id == "cached-meta")
    );
    // No writes: advancing the clock still expires raw-only liveness.
    let later = now + Duration::seconds(control_core::RUN_STALL_SECONDS + 1);
    assert_same(later);
    assert!(!is_live(
        &plane.compute_view_with_raw_sources(later, &sources),
        "cached-marbles"
    ));
    // A new deep raw path appears only after rediscovery, then agrees fully.
    write_meta(
        &home,
        "deep-new",
        json!({"run_id":"deep-new", "status":"running", "agent":"codex", "root":"/new", "started_at":now.to_rfc3339()}),
    );
    let sources = plane.discover_raw_sources();
    assert_eq!(
        format!("{:?}", plane.compute_view_with_raw_sources(now, &sources)),
        format!("{:?}", plane.compute_view(now))
    );
    fs::remove_dir_all(home).ok();
}

#[cfg(unix)]
#[test]
fn cached_raw_paths_reprobe_process_exit_without_filesystem_writes() {
    let home = temp_home("cached-pid");
    let now = Utc::now();
    let mut child = std::process::Command::new("/bin/sleep")
        .arg("60")
        .spawn()
        .unwrap();
    // Stale timestamps mean the live PID is the only reason this stays Live.
    write_meta(
        &home,
        "cached-pid",
        json!({"run_id":"cached-pid", "agent":"codex", "status":"running", "root":"/repo", "worker_pid":child.id(), "started_at":iso_ago(now, MONTH), "updated_at":iso_ago(now, MONTH)}),
    );
    let plane = ControlPlane::new(&home);
    let sources = plane.discover_raw_sources();
    let live = plane.compute_view_with_raw_sources(now, &sources);
    child.kill().unwrap();
    child.wait().unwrap();
    assert!(
        is_live(&live, "cached-pid"),
        "live PID must be probed on cached paths"
    );
    let dead = plane.compute_view_with_raw_sources(now, &sources);
    assert!(!is_live(&dead, "cached-pid"));
    assert_eq!(
        format!("{dead:?}"),
        format!("{:?}", plane.compute_view(now))
    );
    fs::remove_dir_all(home).ok();
}

#[cfg(unix)]
#[test]
fn cached_raw_paths_keep_symlink_replacements_out_of_the_projection() {
    use std::os::unix::fs::symlink;
    let home = temp_home("cached-symlinks");
    let now = Utc::now();
    let original = write_meta(
        &home,
        "original",
        json!({"run_id":"original", "status":"running", "agent":"codex", "root":"/repo", "started_at":now.to_rfc3339()}),
    );
    let outside = home.join("external");
    fs::create_dir_all(&outside).unwrap();
    let target = outside.join("external.meta.json");
    fs::write(&target, json!({"run_id":"external", "status":"running", "agent":"codex", "root":"/external", "started_at":now.to_rfc3339()}).to_string()).unwrap();
    let plane = ControlPlane::new(&home);
    let sources = plane.discover_raw_sources();
    fs::remove_file(&original).unwrap();
    symlink(&target, &original).unwrap();
    assert_eq!(
        format!("{:?}", plane.compute_view_with_raw_sources(now, &sources)),
        format!("{:?}", plane.compute_view(now))
    );
    fs::remove_file(&original).unwrap();
    // Replacing an ancestor directory must not permit cached child paths to
    // read the external directory either.
    fs::copy(&target, &original).unwrap();
    let sources = plane.discover_raw_sources();
    let parent = original.parent().unwrap();
    let replacement = outside.join(original.file_name().unwrap());
    fs::copy(&target, replacement).unwrap();
    fs::remove_dir_all(parent).unwrap();
    symlink(&outside, parent).unwrap();
    assert_eq!(
        format!("{:?}", plane.compute_view_with_raw_sources(now, &sources)),
        format!("{:?}", plane.compute_view(now))
    );
    fs::remove_dir_all(home).ok();
}

// ---------------------------------------------------------------------------
// ProjectionCache: a watching reader's memo of receipt bytes. The oracle for
// every case is the uncached canonical projection at the same instant.
// ---------------------------------------------------------------------------

fn write_snapshot(home: &Path, run_id: &str, state: &str, updated_at: &str) -> PathBuf {
    let path = home.join(format!("control_plane/runs/{run_id}.json"));
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(
        &path,
        json!({
            "run_id": run_id, "state": state, "agent": "codex", "skill": "implement",
            "mode": "headless", "root": "/repo", "commit_sha": "", "operator_session": "",
            "latest_report": "", "latest_transcript": "", "last_error": "",
            "updated_at": updated_at, "started_at": updated_at,
            "health": if state == "running" { "active" } else { "final" },
            "source": "agent-meta", "lock_present": false,
            "exit_code": if state == "running" { serde_json::Value::Null } else { json!(0) },
            "liveness": "terminal", "launcher_pid": null,
            "completed_at": if state == "running" { "" } else { updated_at },
            "session_id": "", "current_loop": null, "total_loops": null
        })
        .to_string(),
    )
    .unwrap();
    path
}

fn write_runtime_meta(home: &Path, run_id: &str, status: &str, beat: u64) -> PathBuf {
    let dir = home.join("control_plane/runtime_runs").join(run_id);
    fs::create_dir_all(&dir).unwrap();
    fs::write(dir.join("transcript.log"), "x\n").unwrap();
    let path = dir.join("meta.json");
    let at = Utc::now().to_rfc3339();
    fs::write(
        &path,
        json!({
            "run_id": run_id, "status": status, "agent": "codex", "skill": "implement",
            "root": "/repo", "started_at": at, "updated_at": at, "beat": beat,
            "exit_code": if status == "completed" { json!(0) } else { serde_json::Value::Null },
            "completed_at": if status == "completed" { at.clone() } else { String::new() },
            "worker_pid": 999_999, "owner_pid": 999_998
        })
        .to_string(),
    )
    .unwrap();
    path
}

fn cached_plane(home: &Path, trust: StdDuration) -> (ControlPlane, Arc<ProjectionCache>) {
    let plain = ControlPlane::new(home);
    let cache = Arc::new(ProjectionCache::new(&plain, trust, |_| true));
    (plain.with_cache(cache.clone()), cache)
}

/// The cached projection must be byte-identical to a fresh uncached one.
fn assert_cached_matches_canonical(
    home: &Path,
    cached: &ControlPlane,
    sources: &control_core::read::RawRunSources,
    now: DateTime<Utc>,
) {
    assert_eq!(
        format!("{:?}", cached.compute_view_with_raw_sources(now, sources)),
        format!("{:?}", ControlPlane::new(home).compute_view(now)),
        "a cached read must never differ from the canonical projection"
    );
}

fn reads_since(cache: &ProjectionCache, before: CacheStats) -> u64 {
    cache.stats().reads - before.reads
}

#[test]
fn projection_cache_matches_canonical_projection_through_every_kind_of_change() {
    let home = temp_home("cache-parity");
    let now = Utc::now();
    let old = iso_ago(now, 3600);
    let snapshot = write_snapshot(&home, "snap-a", "completed", &old);
    write_snapshot(&home, "snap-b", "completed", &old);
    let runtime = write_runtime_meta(&home, "snap-a", "completed", 0);
    write_runtime_meta(&home, "solo", "running", 0);
    let lock = write_lock(&home, "lock-a", Some(&iso_ago(now, 30)));
    let meta = write_meta(
        &home,
        "raw-a",
        json!({"run_id":"raw-a", "status":"running", "agent":"codex", "root":"/repo", "started_at":iso_ago(now, 30), "updated_at":iso_ago(now, 30)}),
    );
    for path in [&snapshot, &runtime, &lock, &meta] {
        age_file(path, 3600);
    }
    // Watcher evidence on: only invalidations make the cache re-read.
    let (cached, cache) = cached_plane(&home, StdDuration::from_secs(30));
    cache.set_event_trust(true);
    let sources = cached.discover_raw_sources();
    assert_cached_matches_canonical(&home, &cached, &sources, now);

    // In-place content change, announced the way the host's watcher announces it.
    fs::write(&meta, json!({"run_id":"raw-a", "status":"completed", "agent":"codex", "root":"/repo-changed", "started_at":iso_ago(now, 30), "finished_at":now.to_rfc3339()}).to_string()).unwrap();
    cache.invalidate_paths(std::slice::from_ref(&meta));
    assert_cached_matches_canonical(&home, &cached, &sources, now);

    // Atomic replacement (new inode) of a runtime receipt.
    let staged = runtime.with_extension("tmp");
    fs::write(&staged, json!({"run_id":"snap-a", "status":"running", "agent":"codex", "root":"/replaced", "worker_pid": std::process::id()}).to_string()).unwrap();
    fs::rename(&staged, &runtime).unwrap();
    cache.invalidate_paths(&[staged, runtime.clone()]);
    assert_cached_matches_canonical(&home, &cached, &sources, now);

    // Deletion of a lock and of a snapshot.
    fs::remove_file(&lock).unwrap();
    fs::remove_file(&snapshot).unwrap();
    cache.invalidate_paths(&[lock.clone(), snapshot.clone()]);
    assert_cached_matches_canonical(&home, &cached, &sources, now);

    // Creation after a cached absence.
    let created = write_runtime_meta(&home, "snap-b", "running", 1);
    cache.invalidate_paths(std::slice::from_ref(&created));
    assert_cached_matches_canonical(&home, &cached, &sources, now);

    // A whole run directory published atomically over a cached absence is
    // announced by its directory, not by the files inside it.
    let staging = home.join("control_plane/staging-run");
    fs::create_dir_all(&staging).unwrap();
    fs::write(staging.join("meta.json"), json!({"run_id":"late", "status":"running", "agent":"codex", "root":"/late", "started_at":now.to_rfc3339(), "updated_at":now.to_rfc3339(), "worker_pid": std::process::id()}).to_string()).unwrap();
    let late = home.join("control_plane/runtime_runs/late");
    let _ = cached.compute_view_with_raw_sources(now, &sources);
    fs::rename(&staging, &late).unwrap();
    cache.invalidate_paths(std::slice::from_ref(&late));
    assert_cached_matches_canonical(&home, &cached, &sources, now);

    // Time alone expires liveness without any read.
    let later = now + Duration::seconds(control_core::RUN_STALL_SECONDS + 1);
    let before = cache.stats();
    assert_cached_matches_canonical(&home, &cached, &sources, later);
    assert_eq!(
        reads_since(&cache, before),
        0,
        "a clock tick re-reads nothing"
    );

    // An explicit refresh (empty path list) forgets everything.
    cache.invalidate_paths(&[]);
    let before = cache.stats();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    assert!(
        reads_since(&cache, before) > 0,
        "refresh re-reads the receipts"
    );
    fs::remove_dir_all(home).ok();
}

#[cfg(unix)]
#[test]
fn projection_cache_keeps_process_exit_and_identity_refusals_live() {
    use std::os::unix::fs::symlink;
    let home = temp_home("cache-liveness");
    let now = Utc::now();
    let mut child = std::process::Command::new("/bin/sleep")
        .arg("60")
        .spawn()
        .unwrap();
    // Stale stamps: the live PID is the only reason this stays Live.
    let stale = iso_ago(now, MONTH);
    let meta = write_meta(
        &home,
        "pid-run",
        json!({"run_id":"pid-run", "agent":"codex", "status":"running", "root":"/repo", "worker_pid":child.id(), "started_at":stale, "updated_at":stale}),
    );
    age_file(&meta, 3600);
    let (cached, cache) = cached_plane(&home, StdDuration::from_secs(30));
    cache.set_event_trust(true);
    let sources = cached.discover_raw_sources();
    assert!(is_live(
        &cached.compute_view_with_raw_sources(now, &sources),
        "pid-run"
    ));
    child.kill().unwrap();
    child.wait().unwrap();
    let before = cache.stats();
    let dead = cached.compute_view_with_raw_sources(now, &sources);
    assert!(!is_live(&dead, "pid-run"), "PID exit must not need a write");
    assert_eq!(reads_since(&cache, before), 0);
    assert_cached_matches_canonical(&home, &cached, &sources, now);

    // A cached receipt cannot outlive a symlink swap of its directory.
    let runtime = write_runtime_meta(&home, "swap", "running", 0);
    age_file(&runtime, 3600);
    cache.invalidate_paths(&[]);
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    let outside = home.join("external/swap");
    fs::create_dir_all(&outside).unwrap();
    fs::write(
        outside.join("meta.json"),
        json!({"run_id":"swap", "status":"running", "agent":"codex", "root":"/external"})
            .to_string(),
    )
    .unwrap();
    let directory = runtime.parent().unwrap().to_path_buf();
    fs::remove_dir_all(&directory).unwrap();
    symlink(&outside, &directory).unwrap();
    cache.invalidate_paths(&[directory]);
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    fs::remove_dir_all(home).ok();
}

#[test]
fn projection_cache_without_watcher_evidence_never_serves_a_changed_receipt() {
    let home = temp_home("cache-no-watcher");
    let now = Utc::now();
    let meta = write_runtime_meta(&home, "beat", "running", 0);
    let snapshot = write_snapshot(&home, "beat", "running", &iso_ago(now, 3600));
    age_file(&meta, 3600);
    age_file(&snapshot, 3600);
    // No set_event_trust(true): the reader has no evidence the watcher is alive.
    let (cached, cache) = cached_plane(&home, StdDuration::from_secs(30));
    let sources = cached.discover_raw_sources();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    let before = cache.stats();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    assert_eq!(
        reads_since(&cache, before),
        0,
        "unchanged settled stamps are reused"
    );
    assert_eq!(
        cache.stats().trusted_hits,
        0,
        "silence is not evidence here"
    );
    assert!(cache.stats().stamp_hits > 0);
    // Changed behind the cache's back and never announced.
    fs::write(&meta, json!({"run_id":"beat", "status":"completed", "exit_code":0, "completed_at":now.to_rfc3339(), "agent":"codex", "root":"/silent", "worker_pid":999_999}).to_string()).unwrap();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    fs::remove_dir_all(home).ok();
}

#[test]
fn projection_cache_never_trusts_a_stamp_inside_the_mtime_tick() {
    let home = temp_home("cache-racy");
    let now = Utc::now();
    let meta = write_runtime_meta(&home, "tick", "running", 1);
    let (cached, cache) = cached_plane(&home, StdDuration::ZERO);
    let sources = cached.discover_raw_sources();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    // Same length, rewritten immediately: size and (possibly) mtime tick match.
    let original = fs::read_to_string(&meta).unwrap();
    let rewritten = original.replace("\"beat\":1", "\"beat\":2");
    assert_eq!(original.len(), rewritten.len());
    fs::write(&meta, rewritten).unwrap();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    assert_eq!(cache.stats().stamp_hits, 0, "a fresh stamp proves nothing");
    fs::remove_dir_all(home).ok();
}

#[test]
fn projection_cache_cuts_reads_to_the_changed_receipt_at_realistic_scale() {
    const RUNS: usize = 300;
    let home = temp_home("cache-scale");
    let now = Utc::now();
    let old = iso_ago(now, 3600);
    for i in 0..RUNS {
        let id = format!("run-{i:04}");
        let snapshot = write_snapshot(&home, &id, "completed", &old);
        let runtime = write_runtime_meta(&home, &id, "completed", 0);
        age_file(&snapshot, 3600);
        age_file(&runtime, 3600);
    }
    let live = write_runtime_meta(&home, "run-live", "running", 0);
    for i in 0..60 {
        let path = write_meta(
            &home,
            &format!("raw-{i:03}"),
            json!({"run_id":format!("raw-{i:03}"), "status":"completed", "agent":"codex", "root":"/repo", "started_at":old, "finished_at":old}),
        );
        age_file(&path, 3600);
    }
    let (cached, cache) = cached_plane(&home, StdDuration::from_secs(30));
    cache.set_event_trust(true);
    let sources = cached.discover_raw_sources();

    let cold = cache.stats();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    let cold_reads = reads_since(&cache, CacheStats::default());
    assert!(
        cold_reads >= (2 * RUNS) as u64,
        "the first pass reads every receipt once: {cold_reads}"
    );
    let _ = cold;

    // Unchanged: no file is opened and none is stat-validated.
    let before = cache.stats();
    let _ = cached.compute_view_with_raw_sources(now, &sources);
    let after = cache.stats();
    assert_eq!(
        after.reads - before.reads,
        0,
        "unchanged receipts are not re-read"
    );
    assert_eq!(after.stamp_hits - before.stamp_hits, 0);
    assert!(after.trusted_hits - before.trusted_hits >= (2 * RUNS) as u64);

    // Heartbeats: only the touched receipt is re-read, however many passes run.
    let before = cache.stats();
    for beat in 1..=10 {
        fs::write(&live, json!({"run_id":"run-live", "status":"running", "agent":"codex", "root":"/repo", "updated_at":Utc::now().to_rfc3339(), "started_at":Utc::now().to_rfc3339(), "worker_pid":std::process::id(), "beat":beat}).to_string()).unwrap();
        cache.invalidate_paths(std::slice::from_ref(&live));
        let view = cached.compute_view_with_raw_sources(Utc::now(), &sources);
        assert!(is_live(&view, "run-live"));
    }
    let heartbeat_reads = reads_since(&cache, before);
    assert!(
        heartbeat_reads <= 10,
        "ten heartbeats read the live receipt once each, not {heartbeat_reads} files"
    );
    assert_cached_matches_canonical(&home, &cached, &sources, Utc::now());

    // A content change to one archived receipt is one read, not a rescan.
    let victim = home.join("control_plane/runs/run-0007.json");
    write_snapshot(&home, "run-0007", "failed", &old);
    cache.invalidate_paths(&[victim]);
    let before = cache.stats();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    assert_eq!(reads_since(&cache, before), 1);

    // A rotation of the event stream is not a cached input: it is always fresh.
    let events = home.join("control_plane/events.jsonl");
    fs::write(&events, "").unwrap();
    assert_cached_matches_canonical(&home, &cached, &sources, now);

    // Explicit refresh drops everything.
    cache.invalidate_paths(&[]);
    let before = cache.stats();
    let _ = cached.compute_view_with_raw_sources(now, &sources);
    assert!(reads_since(&cache, before) >= (2 * RUNS) as u64);
    fs::remove_dir_all(home).ok();
}

#[test]
fn projection_cache_detects_a_replacement_that_preserves_the_mtime() {
    let home = temp_home("cache-identity");
    let now = Utc::now();
    let meta = write_runtime_meta(&home, "same-mtime", "running", 1);
    age_file(&meta, 3600);
    let (cached, cache) = cached_plane(&home, StdDuration::ZERO);
    let sources = cached.discover_raw_sources();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    // A different file (inode, size, ctime) carrying the very same mtime, moved
    // into place: only the full stamp, not the mtime alone, tells them apart.
    let mtime = fs::metadata(&meta).unwrap().modified().unwrap();
    let staged = meta.with_extension("next");
    fs::write(&staged, json!({"run_id":"same-mtime", "status":"completed", "exit_code":0, "completed_at":now.to_rfc3339(), "agent":"codex", "root":"/different-and-longer-root", "worker_pid":999_999}).to_string()).unwrap();
    fs::File::options()
        .write(true)
        .open(&staged)
        .unwrap()
        .set_modified(mtime)
        .unwrap();
    fs::rename(&staged, &meta).unwrap();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    assert!(
        cache.stats().reads >= 2,
        "the replaced file must be re-read"
    );
    fs::remove_dir_all(home).ok();
}

fn big_noise(bytes: usize) -> serde_json::Value {
    json!(
        (0..bytes / 80)
            .map(|i| json!({"k": i, "v": "x".repeat(60)}))
            .collect::<Vec<_>>()
    )
}

/// A lifecycle container the size of the real ones (`p50` ~172 KB, `p95` ~933 KB).
fn write_big_lifecycle(
    home: &Path,
    run_id: &str,
    status: &str,
    updated_at: &str,
    pid: Option<u32>,
    bytes: usize,
) -> PathBuf {
    let dir = home.join("control_plane/lifecycle_runs").join(run_id);
    fs::create_dir_all(&dir).unwrap();
    let report = dir.join("report.md");
    fs::write(&report, "---\ndou_index: 0\n---\n# report\n").unwrap();
    let state = dir.join("state.json");
    fs::write(
        &state,
        json!({
            "run_id": run_id, "workflow": "vc-ship", "agent": "codex", "root": "/repo",
            "status": status, "updated_at": updated_at, "pid": pid,
            "human_controls": ["approve_transition"], "state_path": state.to_string_lossy(),
            "report_path": report.to_string_lossy(), "transcript_path": "",
            "spec": big_noise(bytes / 4), "context_atlas": big_noise(bytes / 4), "manifest": big_noise(bytes / 4),
            "baton": {"from_stage": "scaffold", "next_stage": "implement", "next_agent": "codex", "dou_index": 1},
            "stages": [{"id": "scaffold", "workflow": "scaffold", "phase": "read", "agent": "codex",
                "status": "completed", "launch": {"report": report.to_string_lossy(), "argv": big_noise(bytes / 8)},
                "await": {"exit_code": 0, "tail": big_noise(bytes / 8)}, "dou_index": 0}],
            "accepted_dou_findings": [], "error": ""
        })
        .to_string(),
    )
    .unwrap();
    state
}

fn event_line(run_id: &str, kind: &str, beat: u64) -> String {
    format!(
        "{}\n",
        json!({"ts": Utc::now().to_rfc3339(), "run_id": run_id, "kind": kind,
            "message": format!("beat {beat}"), "payload": {"beat": beat}})
    )
}

fn append(path: &Path, text: &str) {
    use std::io::Write;
    fs::OpenOptions::new()
        .append(true)
        .create(true)
        .open(path)
        .unwrap()
        .write_all(text.as_bytes())
        .unwrap();
}

#[test]
fn projection_cache_matches_canonical_at_real_lifecycle_and_event_sizes() {
    let home = temp_home("cache-realistic");
    let now = Utc::now();
    let old = iso_ago(now, 3600);
    let fresh = iso_ago(now, 5);
    let mut states = Vec::new();
    for i in 0..20 {
        states.push(write_big_lifecycle(
            &home,
            &format!("life-done-{i:02}"),
            "completed",
            &old,
            None,
            170 * 1024,
        ));
    }
    states.push(write_big_lifecycle(
        &home,
        "life-big",
        "completed",
        &old,
        None,
        900 * 1024,
    ));
    states.push(write_big_lifecycle(
        &home,
        "life-stale",
        "launching",
        &old,
        None,
        170 * 1024,
    ));
    states.push(write_big_lifecycle(
        &home,
        "life-fresh",
        "launching",
        &fresh,
        None,
        170 * 1024,
    ));
    states.push(write_big_lifecycle(
        &home,
        "life-owned",
        "running",
        &old,
        Some(std::process::id()),
        170 * 1024,
    ));
    let aged = SystemTime::now() - StdDuration::from_secs(3600);
    for state in &states {
        fs::File::options()
            .write(true)
            .open(state)
            .unwrap()
            .set_modified(aged)
            .unwrap();
    }
    let events = home.join("control_plane/events.jsonl");
    let mut seed = String::new();
    for i in 0..20_000 {
        seed.push_str(&event_line(&format!("life-done-{:02}", i % 20), "state", i));
    }
    fs::write(&events, seed).unwrap();
    assert!(fs::metadata(&events).unwrap().len() > 2_000_000);

    let (cached, cache) = cached_plane(&home, StdDuration::from_secs(30));
    cache.set_event_trust(true);
    let sources = cached.discover_raw_sources();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    assert_eq!(cache.stats().event_full_reads, 1);
    let cold = cache.stats().reads;
    assert!(
        cold >= states.len() as u64,
        "every state parsed once: {cold}"
    );
    // The retained estimate is far below the 21 MB of text a pass used to parse.
    let text_bytes: u64 = states.iter().map(|s| fs::metadata(s).unwrap().len()).sum();
    eprintln!(
        "lifecycle text {} KiB, cache charge {} KiB",
        text_bytes / 1024,
        cache.estimated_charge() / 1024
    );

    // Unchanged: nothing re-read or re-parsed, only the event tail asked for.
    let before = cache.stats();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    let after = cache.stats();
    assert_eq!(
        after.reads, before.reads,
        "unchanged lifecycle states stay parsed"
    );
    assert_eq!(after.event_full_reads, 1);
    assert_eq!(
        after.event_tail_reads, before.event_tail_reads,
        "an untouched stream is not read at all"
    );

    // Heartbeats append events; the prefix is never read again.
    for beat in 0..5 {
        append(&events, &event_line("life-owned", "heartbeat", beat));
        assert_cached_matches_canonical(&home, &cached, &sources, Utc::now());
    }
    assert_eq!(
        cache.stats().event_full_reads,
        1,
        "appends read only the tail"
    );

    // A half-written final line is not an event yet, and completes later.
    append(
        &events,
        "{\"ts\":\"2026-10-16T00:00:00Z\",\"run_id\":\"life-owned\",\"kind\":\"hea",
    );
    assert_cached_matches_canonical(&home, &cached, &sources, Utc::now());
    append(&events, "rtbeat\",\"message\":\"late\",\"payload\":{}}\n");
    assert_cached_matches_canonical(&home, &cached, &sources, Utc::now());
    assert_eq!(cache.stats().event_full_reads, 1);

    // A lifecycle rewrite announced by the watcher is one read.
    let before = cache.stats();
    write_big_lifecycle(&home, "life-fresh", "completed", &fresh, None, 170 * 1024);
    cache.invalidate_paths(std::slice::from_ref(&states[22]));
    assert_cached_matches_canonical(&home, &cached, &sources, Utc::now());
    assert_eq!(reads_since(&cache, before), 1);

    // Time alone turns the stale fresh container into an abandoned one, and a
    // live owner keeps its container: both are decided on every pass.
    let later = now + Duration::seconds(control_core::RUN_STALL_SECONDS + 10);
    let before = cache.stats();
    assert_cached_matches_canonical(&home, &cached, &sources, later);
    assert_eq!(reads_since(&cache, before), 0);

    // Rotation: the stream is replaced; the retained prefix must not survive it.
    fs::rename(&events, home.join("control_plane/events.jsonl.archived")).unwrap();
    fs::write(&events, event_line("life-owned", "state", 1)).unwrap();
    assert_cached_matches_canonical(&home, &cached, &sources, Utc::now());
    assert_eq!(
        cache.stats().event_full_reads,
        2,
        "a new inode reads in full"
    );

    // Truncation below the retained cursor, then regrowth past it.
    fs::write(&events, "").unwrap();
    assert_cached_matches_canonical(&home, &cached, &sources, Utc::now());
    for beat in 0..40 {
        append(&events, &event_line("life-owned", "state", beat));
    }
    assert_cached_matches_canonical(&home, &cached, &sources, Utc::now());

    // An in-place rewrite of the same length on the same inode.
    let text = fs::read_to_string(&events).unwrap();
    let rewritten = text.replacen("\"beat\":39", "\"beat\":38", 1);
    assert_eq!(text.len(), rewritten.len());
    assert_ne!(text, rewritten);
    let mut file = fs::OpenOptions::new().write(true).open(&events).unwrap();
    std::io::Write::write_all(&mut file, rewritten.as_bytes()).unwrap();
    drop(file);
    assert_cached_matches_canonical(&home, &cached, &sources, Utc::now());

    // An explicit refresh forgets the stream too.
    cache.invalidate_paths(&[]);
    let before = cache.stats();
    assert_cached_matches_canonical(&home, &cached, &sources, Utc::now());
    assert_eq!(cache.stats().event_full_reads, before.event_full_reads + 1);
    fs::remove_dir_all(home).ok();
}

#[test]
fn projection_cache_of_another_home_is_never_consulted_through_the_public_call() {
    let (home_a, home_b) = (temp_home("foreign-a"), temp_home("foreign-b"));
    let now = Utc::now();
    write_runtime_meta(&home_a, "only-in-a", "running", 0);
    write_runtime_meta(&home_b, "only-in-b", "running", 0);
    let (_, cache_a) = cached_plane(&home_a, StdDuration::from_secs(30));
    cache_a.set_event_trust(true);
    // A plane for B handed A's cache: it must read B, and never touch A's cache.
    let plane_b = ControlPlane::new(&home_b).with_cache(cache_a.clone());
    let sources = plane_b.discover_raw_sources();
    let view = plane_b.compute_view_with_raw_sources(now, &sources);
    assert_eq!(
        format!("{view:?}"),
        format!("{:?}", ControlPlane::new(&home_b).compute_view(now))
    );
    assert!(view.recent_runs.iter().any(|run| run.run_id == "only-in-b"));
    assert!(!view.recent_runs.iter().any(|run| run.run_id == "only-in-a"));
    assert_eq!(
        cache_a.stats(),
        CacheStats::default(),
        "foreign cache untouched"
    );
    fs::remove_dir_all(home_a).ok();
    fs::remove_dir_all(home_b).ok();
}

/// Back-date a directory's own mtime (the receipt writes inside it moved it).
#[cfg(unix)]
fn age_dir(path: &Path, seconds: i64) {
    let at = (SystemTime::now() - StdDuration::from_secs(u64::try_from(seconds).unwrap()))
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_secs() as libc::time_t;
    let c = std::ffi::CString::new(path.to_string_lossy().as_bytes()).unwrap();
    let tv = [
        libc::timeval {
            tv_sec: at,
            tv_usec: 0,
        },
        libc::timeval {
            tv_sec: at,
            tv_usec: 0,
        },
    ];
    // SAFETY: valid NUL-terminated path and a two-element timeval array.
    assert_eq!(unsafe { libc::utimes(c.as_ptr(), tv.as_ptr()) }, 0);
}

#[cfg(unix)]
#[test]
fn only_a_fresh_unmerged_runtime_directory_surfaces_as_a_launch() {
    let home = temp_home("cache-fresh-launch");
    let now = Utc::now();
    let stale = write_runtime_meta(&home, "stale-launch", "running", 0);
    let fresh = write_runtime_meta(&home, "fresh-launch", "running", 0);
    // A directory without a merged snapshot: only recent movement makes it live.
    age_file(&stale, 3600);
    age_file(&stale.with_file_name("transcript.log"), 3600);
    age_dir(stale.parent().unwrap(), 3600);
    let (cached, cache) = cached_plane(&home, StdDuration::from_secs(30));
    cache.set_event_trust(true);
    let sources = cached.discover_raw_sources();
    let view = cached.compute_view_with_raw_sources(now, &sources);
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    let seen = |id: &str| {
        view.active_runs
            .iter()
            .chain(&view.stalled_runs)
            .chain(&view.recent_runs)
            .any(|run| run.run_id == id)
    };
    assert!(seen("fresh-launch"), "a just-written launch surfaces");
    assert!(
        !seen("stale-launch"),
        "an old unmerged directory is history"
    );
    // The stale directory was refused before its receipt was ever read.
    let reads = cache.stats().reads;
    fs::write(&fresh, fs::read_to_string(&fresh).unwrap()).unwrap();
    cache.invalidate_paths(std::slice::from_ref(&fresh));
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    assert!(cache.stats().reads - reads <= 3, "{:?}", cache.stats());
    fs::remove_dir_all(home).ok();
}

fn event_n(i: usize) -> String {
    format!(
        "{}\n",
        json!({"ts": "2026-10-16T00:00:00Z", "run_id": "mid-run", "kind": "note",
            "message": format!("event {i:04}"), "payload": {"n": i}})
    )
}

/// `n` events of one run, each its own line; the view shows the newest 16.
fn numbered_events(n: usize) -> String {
    (0..n).map(event_n).collect()
}

/// Rewrite `from` to `to` in place (same length, same inode): a middle event
/// beyond the 128-byte ends the retained prefix is fingerprinted by.
fn rewrite_in_place(path: &Path, from: &str, to: &str) {
    use std::os::unix::fs::FileExt;
    assert_eq!(from.len(), to.len());
    let text = fs::read_to_string(path).unwrap();
    let at = text.find(from).expect("event to rewrite") as u64;
    let file = fs::OpenOptions::new().write(true).open(path).unwrap();
    file.write_all_at(to.as_bytes(), at).unwrap();
}

#[cfg(unix)]
#[test]
fn projection_cache_rejects_a_same_size_rewrite_of_a_middle_event() {
    let home = temp_home("cache-event-rewrite");
    let now = Utc::now();
    let events = home.join("control_plane/events.jsonl");
    fs::write(&events, numbered_events(40)).unwrap();
    let (cached, cache) = cached_plane(&home, StdDuration::from_secs(30));
    cache.set_event_trust(true);
    let sources = cached.discover_raw_sources();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    let before = cached.compute_view_with_raw_sources(now, &sources);
    assert!(before.events.iter().any(|e| e.message == "event 0028"));
    assert_eq!(cache.stats().event_full_reads, 1);

    // Event 28 is far from both 128-byte ends of the 40-line file, yet inside
    // the newest-16 window the view shows, so a stale prefix would be visible.
    rewrite_in_place(&events, "event 0028", "event 0099");
    let after = cached.compute_view_with_raw_sources(now, &sources);
    assert!(
        after.events.iter().any(|e| e.message == "event 0099")
            && !after.events.iter().any(|e| e.message == "event 0028"),
        "the rewritten middle event must reach the view, not the retained prefix"
    );
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    assert_eq!(cache.stats().event_full_reads, 2, "a rewrite re-parses");
    fs::remove_dir_all(home).ok();
}

#[cfg(unix)]
#[test]
fn projection_cache_converges_on_a_rewrite_that_is_followed_by_an_append() {
    let home = temp_home("cache-event-rewrite-append");
    let now = Utc::now();
    let events = home.join("control_plane/events.jsonl");
    fs::write(&events, numbered_events(40)).unwrap();
    // The prefix is re-verified from byte zero at least once per this interval.
    let recheck = StdDuration::from_millis(150);
    let (cached, cache) = cached_plane(&home, recheck);
    cache.set_event_trust(true);
    let sources = cached.discover_raw_sources();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    let pristine = format!("{:?}", ControlPlane::new(&home).compute_view(now).events);

    // A rewrite plus an append cannot be told from an append by size or stamp.
    rewrite_in_place(&events, "event 0028", "event 0099");
    append(&events, &event_n(40));
    let canonical = format!("{:?}", ControlPlane::new(&home).compute_view(now).events);
    assert_ne!(canonical, pristine, "the rewrite is a real change");
    let full_reads = cache.stats().event_full_reads;

    std::thread::sleep(recheck * 2);
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    assert_eq!(
        cache.stats().event_full_reads,
        full_reads + 1,
        "the bounded recheck re-parsed the whole stream"
    );

    // And the recovery is not a one-off: the next rewrite+append converges too.
    rewrite_in_place(&events, "event 0030", "event 0098");
    append(&events, &event_n(41));
    std::thread::sleep(recheck * 2);
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    fs::remove_dir_all(home).ok();
}

#[test]
fn projection_cache_leaves_public_typed_lifecycle_reads_full() {
    let home = temp_home("cache-typed-lifecycle");
    let now = Utc::now();
    let old = iso_ago(now, 3600);
    let state = write_big_lifecycle(&home, "typed-life", "completed", &old, None, 170 * 1024);
    fs::File::options()
        .write(true)
        .open(&state)
        .unwrap()
        .set_modified(SystemTime::now() - StdDuration::from_secs(3600))
        .unwrap();
    let (cached, cache) = cached_plane(&home, StdDuration::from_secs(30));
    cache.set_event_trust(true);
    let sources = cached.discover_raw_sources();
    // Warm the slimmed projection cache, then use the public typed API.
    let _ = cached.compute_view_with_raw_sources(now, &sources);
    let plain = ControlPlane::new(&home);
    for api in [
        format!("{:?}", cached.load_lifecycle_runs()),
        format!("{:?}", cached.resolve_lifecycle_run("typed-life")),
    ] {
        assert!(api.contains("context_atlas"), "full fields survive");
        assert!(api.len() > 100_000, "the 170 KB container is not slimmed");
    }
    assert_eq!(
        format!("{:?}", cached.load_lifecycle_runs()),
        format!("{:?}", plain.load_lifecycle_runs())
    );
    assert_eq!(
        format!("{:?}", cached.resolve_lifecycle_run("typed-life")),
        format!("{:?}", plain.resolve_lifecycle_run("typed-life"))
    );
    assert_eq!(
        format!("{:?}", cached.load_lifecycle_run_summaries()),
        format!("{:?}", plain.load_lifecycle_run_summaries())
    );
    fs::remove_dir_all(home).ok();
}

/// One ~32 KiB `note` record: 1 050 of them push the stream past the 32 MiB
/// event-file bound the cache advertises.
fn bulky_note(i: usize) -> String {
    format!(
        "{}\n",
        json!({"ts": "2026-10-16T00:00:00Z", "run_id": "mid-run", "kind": "note",
            "message": format!("bulk {i:05}"), "payload": {"pad": "x".repeat(32 * 1024)}})
    )
}

const EVENT_FILE_BOUND: u64 = 32 * 1024 * 1024;

/// Append enough 32 KiB notes to cross the bound; returns the new file length.
fn grow_past_event_bound(events: &Path) -> u64 {
    let batch: String = (0..1050).map(bulky_note).collect();
    append(events, &batch);
    let len = fs::metadata(events).unwrap().len();
    assert!(
        len > EVENT_FILE_BOUND,
        "fixture must cross the bound: {len}"
    );
    len
}

#[cfg(unix)]
#[test]
fn projection_cache_refuses_event_retention_when_a_warm_stream_grows_past_the_bound() {
    let home = temp_home("cache-event-bound-grown");
    let now = Utc::now();
    let events = home.join("control_plane/events.jsonl");
    fs::write(&events, numbered_events(1)).unwrap();
    let (cached, cache) = cached_plane(&home, StdDuration::from_secs(30));
    cache.set_event_trust(true);
    let sources = cached.discover_raw_sources();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    let warm = cache.estimated_charge();
    assert!(warm > 0, "a small stream is retained: {warm}");

    let len = grow_past_event_bound(&events);
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    assert_eq!(
        cache.estimated_charge(),
        0,
        "a stream of {len} bytes is over the bound and must not stay retained"
    );

    // The refusal holds on every later read, and the events still project.
    append(&events, &bulky_note(9_999));
    let view = cached.compute_view_with_raw_sources(now, &sources);
    assert!(view.events.iter().any(|e| e.message == "bulk 09999"));
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    assert_eq!(cache.estimated_charge(), 0);

    // A fresh cache on the identical oversized file agrees.
    let (fresh, fresh_cache) = cached_plane(&home, StdDuration::from_secs(30));
    fresh_cache.set_event_trust(true);
    assert_cached_matches_canonical(&home, &fresh, &fresh.discover_raw_sources(), now);
    assert_eq!(fresh_cache.estimated_charge(), 0);
    fs::remove_dir_all(home).ok();
}

#[cfg(unix)]
#[test]
fn projection_cache_resumes_event_retention_when_an_oversized_stream_is_rotated_small() {
    let home = temp_home("cache-event-bound-rotated");
    let now = Utc::now();
    let events = home.join("control_plane/events.jsonl");
    fs::write(&events, numbered_events(1)).unwrap();
    let (cached, cache) = cached_plane(&home, StdDuration::from_secs(30));
    cache.set_event_trust(true);
    let sources = cached.discover_raw_sources();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    grow_past_event_bound(&events);
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    assert_eq!(cache.estimated_charge(), 0);

    // Rotation back under the bound: retention and tail-only reads return.
    fs::rename(&events, home.join("control_plane/events.jsonl.archived")).unwrap();
    fs::write(&events, numbered_events(3)).unwrap();
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    assert!(
        cache.estimated_charge() > 0,
        "a small stream is retained again"
    );
    let before = cache.stats();
    append(&events, &event_n(3));
    assert_cached_matches_canonical(&home, &cached, &sources, now);
    let after = cache.stats();
    assert_eq!(after.event_full_reads, before.event_full_reads);
    assert_eq!(after.event_tail_reads, before.event_tail_reads + 1);
    fs::remove_dir_all(home).ok();
}
