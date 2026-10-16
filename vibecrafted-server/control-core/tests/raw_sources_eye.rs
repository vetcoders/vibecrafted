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
use control_core::{ControlPlane, StateView};
use serde_json::json;

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
