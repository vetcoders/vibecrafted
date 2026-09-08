//! The frontend (Rust) eye on the runtime-state contract: a still-launching run
//! lives in `runtime_runs/<id>/` before the snapshot sync merges it into
//! `runs/<id>.json`. `lookup_run` must resolve it there (Niezmiennik 3 — one
//! contract, many eyes) instead of a silent miss, mirroring
//! `control_plane.resolve_run` on the Python side.

#![recursion_limit = "256"]

use std::fs;
use std::path::PathBuf;

use chrono::Utc;
use control_core::ControlPlane;
use serde_json::{Value, json};

fn temp_home(name: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    std::env::temp_dir().join(format!("control-core-{name}-{nanos}"))
}

fn write_snapshot(
    runs_dir: &std::path::Path,
    run_id: &str,
    state: &str,
    settlement: Option<(&str, &str)>,
) {
    let mut payload = json!({
        "run_id": run_id,
        "state": state,
        "agent": "codex",
        "skill": "implement",
        "mode": "implement",
        "root": "/tmp/repo",
        "commit_sha": "",
        "operator_session": format!("repo-{run_id}"),
        "latest_report": "",
        "latest_transcript": "",
        "last_error": "",
        "updated_at": "2026-07-22T12:00:00+00:00",
        "started_at": "2026-07-22T11:59:00+00:00",
        "health": if state == "running" { "active" } else { "final" },
        "source": "agent-meta",
        "lock_present": false,
        "exit_code": Value::Null,
        "liveness": if state == "running" { "heartbeat" } else { "terminal" },
        "launcher_pid": Value::Null,
        "completed_at": "",
        "session_id": "",
        "current_loop": Value::Null,
        "total_loops": Value::Null
    });
    if let Some((verdict, tui)) = settlement {
        let object = payload.as_object_mut().expect("snapshot object");
        object.insert(
            "settlement_verdict".to_string(),
            Value::String(verdict.to_string()),
        );
        object.insert("settlement_tui".to_string(), Value::String(tui.to_string()));
    }
    fs::write(
        runs_dir.join(format!("{run_id}.json")),
        serde_json::to_vec_pretty(&payload).expect("snapshot JSON"),
    )
    .expect("write snapshot");
}

#[test]
fn lookup_run_resolves_a_launching_run_from_runtime_runs() {
    let home = temp_home("runtime-runs-eye");
    let run_id = "marb-260615-194454-80000";
    let run_dir = home.join("control_plane").join("runtime_runs").join(run_id);
    fs::create_dir_all(&run_dir).expect("run dir");
    // The runtime writes transcript.log at launch; meta.json is often absent.
    fs::write(run_dir.join("transcript.log"), "launching-bytes\n").expect("transcript");
    // Deliberately no runs/<id>.json snapshot — the sync has not merged it yet.

    let plane = ControlPlane::new(&home);
    let run = plane
        .lookup_run(run_id)
        .expect("run resolved from runtime_runs");

    assert_eq!(run.run_id, run_id);
    assert_eq!(run.state, "launching");
    assert_eq!(run.source, "runtime_runs");
    assert!(!run.is_terminal(), "a launching run is not terminal");
    assert!(
        run.latest_transcript.ends_with("transcript.log"),
        "carries the runtime transcript path: {}",
        run.latest_transcript
    );
}

#[test]
fn compute_view_surfaces_a_launching_run_not_yet_synced() {
    let home = temp_home("runtime-runs-view");
    let run_id = "marb-view-launching-1";
    let run_dir = home.join("control_plane").join("runtime_runs").join(run_id);
    fs::create_dir_all(&run_dir).expect("run dir");
    fs::write(run_dir.join("transcript.log"), "fresh\n").expect("transcript");
    // No *.meta.json / *.lock / marbles state — the aggregate dashboard path
    // would otherwise show a silent gap until the snapshot sync runs.

    let plane = ControlPlane::new(&home);
    let view = plane.compute_view(Utc::now());

    assert!(
        view.active_runs
            .iter()
            .chain(view.recent_runs.iter())
            .any(|r| r.run_id == run_id && r.source == "runtime_runs"),
        "compute_view surfaces the launching run straight from runtime_runs"
    );
}

#[test]
fn compute_view_reads_settlement_board_only_from_retained_snapshots() {
    let home = temp_home("settlement-board");
    let control_plane = home.join("control_plane");
    let runs_dir = control_plane.join("runs");
    fs::create_dir_all(&runs_dir).expect("runs dir");

    write_snapshot(&runs_dir, "disagree", "completed", Some(("finalized", "f")));
    write_snapshot(&runs_dir, "failed", "completed", Some(("failed", "x")));
    write_snapshot(&runs_dir, "invalid", "completed", Some(("invalid", "x")));
    write_snapshot(
        &runs_dir,
        "attention",
        "completed",
        Some(("needs_attention", "n")),
    );
    write_snapshot(&runs_dir, "unsettled-terminal", "completed", None);
    write_snapshot(&runs_dir, "unsettled-live", "running", None);
    let live_path = runs_dir.join("unsettled-live.json");
    let mut unsettled_live: Value =
        serde_json::from_slice(&fs::read(&live_path).expect("read live snapshot"))
            .expect("live snapshot JSON");
    let live_object = unsettled_live
        .as_object_mut()
        .expect("live snapshot object");
    live_object.insert("proof_state".to_string(), json!("failed"));
    live_object.insert("delivery_state".to_string(), json!("invalidated"));
    live_object.insert("settlement_tui".to_string(), json!("f"));
    fs::write(
        &live_path,
        serde_json::to_vec_pretty(&unsettled_live).expect("live snapshot JSON"),
    )
    .expect("rewrite live snapshot");

    // Raw compute_view source disagrees with the persisted `disagree` snapshot:
    // Rust sees it as active, while settlement must remain Python's finalized.
    let artifacts = home.join("artifacts");
    fs::create_dir_all(&artifacts).expect("artifacts dir");
    fs::write(
        artifacts.join("disagree.meta.json"),
        serde_json::to_vec_pretty(&json!({
            "run_id": "disagree",
            "status": "running",
            "updated_at": "2026-07-22T12:00:00+00:00",
            "started_at": "2026-07-22T11:59:00+00:00",
            "agent": "codex",
            "mode": "implement",
            "root": "/tmp/repo",
            "skill_code": "impl",
            "liveness": "heartbeat"
        }))
        .expect("meta JSON"),
    )
    .expect("write raw meta");

    let sync_lock = control_plane.join(".sync.lock");
    fs::write(&sync_lock, "python-writer-sentinel").expect("lock sentinel");
    let now = chrono::DateTime::parse_from_rfc3339("2026-07-22T12:00:00+00:00")
        .expect("fixed now")
        .with_timezone(&Utc);
    let view = ControlPlane::new(&home).compute_view(now);

    let board = &view.settlement_counts;
    assert_eq!(
        board.active, 1,
        "existing Rust active count remains visible"
    );
    assert_eq!(board.f, 1, "raw running state must not erase snapshot f");
    assert_eq!(board.x, 2, "failed + invalid share the x bucket");
    assert_eq!(board.invalid, 1, "invalid remains an x subset");
    assert!(
        board.invalid <= board.x,
        "invalid cannot exceed the x bucket"
    );
    assert_eq!(board.n, 2, "needs_attention + unsettled terminal");
    assert_eq!(
        board.unclassified, 1,
        "live unsettled snapshot remains visible outside f/x/n"
    );
    assert_eq!(board.total_settled, board.f + board.x + board.n);
    assert_eq!(
        fs::read_to_string(&sync_lock).expect("read lock sentinel"),
        "python-writer-sentinel",
        "read model must not acquire or mutate Python's write lock"
    );

    let contract = serde_json::to_value(board).expect("board serialises");
    assert_eq!(contract["scope"], json!("retained_control_plane_snapshots"));
    assert!(
        contract.get("today").is_none(),
        "scope is not a daily claim"
    );

    fs::remove_dir_all(&home).ok();
}

#[test]
fn lookup_run_projects_recovery_controls_without_promoting_legacy_session_id() {
    let home = temp_home("recovery-read-projection");
    let runs_dir = home.join("control_plane").join("runs");
    fs::create_dir_all(&runs_dir).expect("runs dir");

    let base = json!({
        "run_id": "recover-explicit",
        "state": "recovery_required",
        "agent": "grok",
        "skill": "implement",
        "mode": "implement",
        "root": "/tmp/repo",
        "commit_sha": "",
        "operator_session": "repo-recover-explicit",
        "latest_report": "/tmp/report.md",
        "latest_transcript": "/tmp/transcript.log",
        "last_error": "worker exited",
        "updated_at": "2026-07-26T05:01:02+00:00",
        "started_at": "2026-07-26T05:00:00+00:00",
        "health": "final",
        "source": "agent-meta",
        "lock_present": false,
        "exit_code": -9,
        "liveness": "terminal",
        "launcher_pid": Value::Null,
        "completed_at": "2026-07-26T05:01:02+00:00",
        "session_id": "ambiguous-legacy-value",
        "current_loop": Value::Null,
        "total_loops": Value::Null,
        "recovery_required": true,
        "stop_reason": "signal_exit",
        "settlement_verdict": "needs_attention",
        "settlement_tui": "n",
        "settlement_reason": "trust_pass_with_gaps:abc123",
        "settlement_source": "trust",
        "settlement_at": "2026-07-26T05:01:03+00:00",
        "settlement_claim_digest": "0123456789abcdef",
        "settlement_waived": false,
        "settlement_revision": 4,
        "lifecycle": {
            "await": false,
            "inspect": true,
            "stop": false,
            "cancel": false,
            "resume": true,
            "recovery_required": true
        }
    });
    fs::write(
        runs_dir.join("recover-explicit.json"),
        serde_json::to_vec_pretty(&base).expect("snapshot JSON"),
    )
    .expect("write explicit snapshot");
    let runtime_dir = home
        .join("control_plane")
        .join("runtime_runs")
        .join("recover-explicit");
    fs::create_dir_all(&runtime_dir).expect("runtime dir");
    fs::write(
        runtime_dir.join("meta.json"),
        serde_json::to_vec_pretty(&json!({
            "run_id": "recover-explicit",
            "worker_pid": 99999991,
            "worker_pgid": 99999990,
            "worker_alive": false,
            "agent_session_id": "019f-explicit-native",
            "runtime_session_id": "runtime-explicit",
            "resume_of": "recover-parent",
            "attempt": 2,
            "commit_sha": "a".repeat(40)
        }))
        .expect("runtime meta JSON"),
    )
    .expect("write runtime meta");

    let plane = ControlPlane::new(&home);
    let explicit = plane.lookup_run("recover-explicit").expect("explicit run");
    assert_eq!(explicit.worker_pid, Some(99999991));
    assert_eq!(explicit.worker_pgid, Some(99999990));
    assert_eq!(explicit.worker_alive, Some(false));
    assert_eq!(explicit.agent_session_id, "019f-explicit-native");
    assert_eq!(explicit.runtime_session_id, "runtime-explicit");
    assert_eq!(explicit.commit_sha, "a".repeat(40));
    assert_eq!(explicit.settlement_revision, Some(4));
    let controls = explicit.controls.as_ref().expect("typed controls");
    assert!(!controls.await_run);
    assert!(!controls.stop);
    assert!(
        controls.retry,
        "resume in legacy lifecycle means cold retry"
    );
    let native = controls
        .native_resume_candidate
        .as_ref()
        .expect("explicit native candidate");
    assert_eq!(native.agent, "grok");
    assert_eq!(native.agent_session_id, "019f-explicit-native");

    let mut legacy = base;
    let object = legacy.as_object_mut().expect("snapshot object");
    object.insert("run_id".to_string(), json!("recover-legacy"));
    object.insert("operator_session".to_string(), json!("repo-recover-legacy"));
    fs::write(
        runs_dir.join("recover-legacy.json"),
        serde_json::to_vec_pretty(&legacy).expect("legacy snapshot JSON"),
    )
    .expect("write legacy snapshot");

    let legacy = plane.lookup_run("recover-legacy").expect("legacy run");
    assert_eq!(legacy.session_id, "ambiguous-legacy-value");
    assert!(
        legacy
            .controls
            .as_ref()
            .expect("typed controls")
            .native_resume_candidate
            .is_none(),
        "legacy session_id must never become native resume evidence"
    );
    assert!(
        legacy.controls.as_ref().expect("typed controls").retry,
        "cold retry remains independent from native resume evidence"
    );
    assert!(
        serde_json::to_value(&legacy).expect("legacy run serialises")["controls"]
            ["native_resume_candidate"]
            .is_null(),
        "the API makes the missing native candidate explicit"
    );

    fs::remove_dir_all(home).ok();
}

#[test]
fn settlement_notification_never_resurrects_process_state() {
    let home = temp_home("settlement-not-liveness");
    let control_plane = home.join("control_plane");
    fs::create_dir_all(&control_plane).expect("control plane");
    let event = json!({
        "ts": "2026-07-26T05:01:03+00:00",
        "run_id": "settled-history",
        "kind": "settlement.changed",
        "message": "settlement changed to needs_attention",
        "payload": {
            "verdict": "needs_attention",
            "tui": "n",
            "reason": "trust_pass_with_gaps:abc123",
            "source": "trust",
            "settled_at": "2026-07-26T05:01:03+00:00",
            "claim_digest": "0123456789abcdef",
            "waived": false,
            "revision": 4
        }
    });
    fs::write(control_plane.join("events.jsonl"), format!("{event}\n")).expect("event stream");

    let view = ControlPlane::new(&home).compute_view(Utc::now());
    assert!(
        view.active_runs.is_empty() && view.stalled_runs.is_empty() && view.recent_runs.is_empty(),
        "settlement outbox event is not process state"
    );
    assert_eq!(
        view.events.len(),
        1,
        "notification remains visible to SSE/readers"
    );
    assert_eq!(view.events[0].kind, "settlement.changed");

    fs::remove_dir_all(home).ok();
}

#[test]
fn lookup_run_returns_none_when_run_not_on_disk_yet() {
    let home = temp_home("runtime-runs-eye-miss");
    fs::create_dir_all(home.join("control_plane")).expect("cp dir");

    let plane = ControlPlane::new(&home);
    assert!(
        plane.lookup_run("ghost-run").is_none(),
        "a run with nothing on disk resolves to None (still launching -> await)"
    );
}

#[test]
fn compute_view_does_not_count_completed_runtime_meta_as_active() {
    let home = temp_home("completed-runtime-meta");
    let runs_dir = home.join("control_plane").join("runs");
    fs::create_dir_all(&runs_dir).expect("runs dir");
    write_snapshot(&runs_dir, "work-ghost", "running", None);

    let run_dir = home
        .join("control_plane")
        .join("runtime_runs")
        .join("work-ghost");
    fs::create_dir_all(&run_dir).expect("runtime dir");
    fs::write(
        run_dir.join("meta.json"),
        serde_json::to_vec(&json!({
            "run_id": "work-ghost",
            "status": "completed",
            "state": "completed",
            "exit_code": 0,
            "completed_at": "2026-08-28T05:00:00+00:00",
            "worker_pid": 999_999_999i64,
        }))
        .expect("meta JSON"),
    )
    .expect("write runtime meta");

    let view = ControlPlane::new(&home).compute_view(Utc::now());
    assert!(
        view.active_runs
            .iter()
            .all(|run| run.run_id != "work-ghost"),
        "completed runtime meta with a dead pid must not stay in active_runs: {:?}",
        view.active_runs
            .iter()
            .map(|run| &run.run_id)
            .collect::<Vec<_>>()
    );

    fs::remove_dir_all(home).ok();
}

#[test]
fn overlay_does_not_stamp_final_from_contradictory_running_meta() {
    let home = temp_home("contradictory-runtime-meta");
    let runs_dir = home.join("control_plane").join("runs");
    fs::create_dir_all(&runs_dir).expect("runs dir");
    write_snapshot(&runs_dir, "work-live", "running", None);

    let run_dir = home
        .join("control_plane")
        .join("runtime_runs")
        .join("work-live");
    fs::create_dir_all(&run_dir).expect("runtime dir");
    fs::write(
        run_dir.join("meta.json"),
        serde_json::to_vec(&json!({
            "run_id": "work-live",
            "status": "running",
            "state": "running",
            "exit_code": 0,
            "completed_at": "2026-08-28T05:00:00+00:00",
            "worker_pid": 999_999_999i64,
        }))
        .expect("meta JSON"),
    )
    .expect("write runtime meta");

    let plane = ControlPlane::new(&home);
    let run = plane.lookup_run("work-live").expect("snapshot");
    assert_eq!(run.run_id, "work-live");
    assert_eq!(run.state, "running");
    assert_ne!(run.liveness, "terminal");
    assert_ne!(run.health, "final");
    let view = plane.compute_view(Utc::now());
    let seen = view
        .active_runs
        .iter()
        .chain(view.stalled_runs.iter())
        .chain(view.recent_runs.iter())
        .find(|item| item.run_id == "work-live")
        .expect("contradictory running meta remains visible");
    assert_eq!(seen.state, "running");
    assert_ne!(seen.liveness, "terminal");
    assert_ne!(seen.health, "final");

    fs::remove_dir_all(home).ok();
}
