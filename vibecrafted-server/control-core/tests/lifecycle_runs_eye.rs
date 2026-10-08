//! Read-only Rust eye on lifecycle runs written by the Python lifecycle runner.

use std::fs;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use chrono::Utc;
use control_core::ControlPlane;
use serde_json::json;

fn temp_home(name: &str) -> PathBuf {
    static NEXT_ID: AtomicU64 = AtomicU64::new(0);

    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let base = std::env::var_os("TMPDIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/tmp"));
    for attempt in 0..100 {
        let nonce = NEXT_ID.fetch_add(1, Ordering::Relaxed);
        let candidate = base.join(format!(
            "control-core-{name}-{}-{nanos}-{nonce}-{attempt}",
            std::process::id()
        ));
        match fs::create_dir(&candidate) {
            Ok(()) => return candidate,
            Err(error) if error.kind() == ErrorKind::AlreadyExists => continue,
            Err(error) => panic!("create isolated fixture home: {error}"),
        }
    }
    panic!("could not allocate an isolated fixture home")
}

fn write_lifecycle_run(home: &Path, run_id: &str, state_dou: Option<i64>) {
    let run_dir = home
        .join("control_plane")
        .join("lifecycle_runs")
        .join(run_id);
    fs::create_dir_all(&run_dir).expect("lifecycle run dir");

    let state_path = run_dir.join("state.json");
    let report_path = run_dir.join("report.md");
    let transcript_path = run_dir.join("transcript.log");
    fs::write(&report_path, "---\ndou_index: 0\n---\n# lifecycle report\n").expect("report");
    fs::write(&transcript_path, "stage transcript\n").expect("transcript");

    let mut state = json!({
        "run_id": run_id,
        "workflow": "vc-ship",
        "agent": "codex",
        "root": "/tmp/vibecrafted-lifecycle",
        "status": "launching",
        "await_stages": false,
        "parent_run_id": null,
        "operator_actions": [
            {"action": "approve_transition", "at": "2026-07-02T12:00:00-0700", "details": {"next_stage": "implement"}}
        ],
        "spec": {"workflow_id": "vc-ship", "agent": "codex"},
        "supervisor": "vibecrafted_core.lifecycle_runner.LifecycleSupervisor",
        "human_controls": ["approve_transition", "interrupt_workflow", "accept_dou"],
        "state_path": state_path.to_string_lossy(),
        "report_path": report_path.to_string_lossy(),
        "transcript_path": transcript_path.to_string_lossy(),
        "context_atlas": {"ok": true},
        "manifest": {"id": "vc-ship"},
        "baton": {
            "from_stage": "scaffold",
            "from_phase": "read",
            "next_stage": "implement",
            "next_agent": "codex",
            "reason": "stage_launched_without_await",
            "previous_reports": [report_path.to_string_lossy()],
            "dou_index": null
        },
        "stages": [{
            "id": "scaffold",
            "name": "VC Scaffold",
            "workflow": "scaffold",
            "phase": "read",
            "agent": "codex",
            "status": "completed",
            "launch": {"report": report_path.to_string_lossy()},
            "await": {},
            "commit_before": "abc123",
            "commit_after": "def456",
            "changed_files": [],
            "new_commits": [],
            "transition": {
                "next_stage": "implement",
                "requested_next_stage": "",
                "next_agent": "codex",
                "requested_next_agent": "",
                "conditions": ["stage_completed"],
                "fallback_stage": "",
                "audit_after": ""
            }
        }],
        "accepted_dou": 2,
        "accepted_dou_findings": [{"id": "accepted-1"}]
    });

    if let Some(value) = state_dou {
        state["dou_index"] = json!({
            "value": value,
            "stage": "audit",
            "report": report_path.to_string_lossy()
        });
    }

    fs::write(
        &state_path,
        serde_json::to_string_pretty(&state).expect("serialise state"),
    )
    .expect("state");
}

#[test]
fn resolve_lifecycle_run_returns_full_nested_state() {
    let home = temp_home("lifecycle-full");
    let run_id = "life-ship-smoke-full";
    write_lifecycle_run(&home, run_id, Some(0));

    let plane = ControlPlane::new(&home);
    let run = plane
        .resolve_lifecycle_run(run_id)
        .expect("lifecycle run resolved");

    assert_eq!(run.run_id, run_id);
    assert_eq!(run.workflow, "vc-ship");
    assert_eq!(run.baton.next_stage, "implement");
    assert_eq!(run.stages[0].id, "scaffold");
    assert_eq!(run.dou_index.and_then(|dou| dou.value), Some(0));
}

#[test]
fn lookup_run_projects_lifecycle_run_into_flat_status() {
    let home = temp_home("lifecycle-lookup");
    let run_id = "life-ship-smoke-lookup";
    write_lifecycle_run(&home, run_id, Some(0));

    let plane = ControlPlane::new(&home);
    let run = plane.lookup_run(run_id).expect("flat projection resolved");

    assert_eq!(run.run_id, run_id);
    assert_eq!(run.state, "launching");
    assert_eq!(run.skill, "vc-ship");
    assert_eq!(run.mode, "lifecycle");
    assert_eq!(run.source, "lifecycle_runs");
    assert!(run.latest_report.ends_with("report.md"));
    assert!(run.latest_transcript.ends_with("transcript.log"));
}

#[test]
fn compute_view_surfaces_lifecycle_projection() {
    let home = temp_home("lifecycle-view");
    let run_id = "life-ship-smoke-view";
    write_lifecycle_run(&home, run_id, Some(0));

    let plane = ControlPlane::new(&home);
    let view = plane.compute_view(Utc::now());

    assert!(
        view.active_runs
            .iter()
            .chain(view.stalled_runs.iter())
            .all(|run| run.run_id != run_id),
        "lifecycle containers do not claim worker liveness"
    );
    assert!(
        view.recent_runs.iter().any(|run| run.run_id == run_id
            && run.source == "lifecycle_runs"
            && run.health == "unknown"),
        "compute_view keeps lifecycle containers discoverable as recent/unknown"
    );
}

#[test]
fn snapshot_view_surfaces_durable_lifecycle_projection() {
    let home = temp_home("lifecycle-snapshot-view");
    let run_id = "life-ship-smoke-snapshot-view";
    write_lifecycle_run(&home, run_id, Some(0));

    let plane = ControlPlane::new(&home);
    let view = plane.read_state_view();

    assert!(
        view.active_runs
            .iter()
            .chain(view.stalled_runs.iter())
            .all(|run| run.run_id != run_id),
        "lifecycle containers do not claim worker liveness"
    );
    assert!(
        view.recent_runs.iter().any(|run| run.run_id == run_id
            && run.source == "lifecycle_runs"
            && run.health == "unknown"),
        "snapshot view keeps durable lifecycle containers discoverable"
    );
}

#[test]
fn lifecycle_summaries_surface_baton_and_report_dou_fallback() {
    let home = temp_home("lifecycle-summary");
    let run_id = "life-ship-smoke-summary";
    write_lifecycle_run(&home, run_id, None);

    let plane = ControlPlane::new(&home);
    let summaries = plane.load_lifecycle_run_summaries();
    let summary = summaries
        .iter()
        .find(|summary| summary.run_id == run_id)
        .expect("summary present");

    assert_eq!(summary.workflow, "vc-ship");
    assert_eq!(summary.current_stage, "scaffold");
    assert_eq!(summary.next_stage, "implement");
    assert_eq!(summary.next_agent, "codex");
    assert_eq!(summary.human_controls_count, 3);
    assert_eq!(summary.operator_actions_count, 1);
    assert_eq!(summary.dou_index, Some(0));
    assert_eq!(summary.dou_readiness, "zero");
    assert_eq!(summary.accepted_dou, 2, "explicit accepted_dou wins");
}

#[test]
fn recent_lifecycle_summaries_bound_the_dashboard_projection() {
    let home = temp_home("lifecycle-summary-limit");
    for suffix in ["one", "two", "three"] {
        write_lifecycle_run(&home, &format!("life-ship-{suffix}"), Some(0));
    }

    let plane = ControlPlane::new(&home);

    assert!(plane.load_recent_lifecycle_run_summaries(0).is_empty());
    assert_eq!(plane.load_recent_lifecycle_run_summaries(2).len(), 2);
}

#[test]
fn lifecycle_summary_reads_live_dou_from_latest_stage_report() {
    let home = temp_home("lifecycle-stage-report");
    let run_id = "life-ship-smoke-stage-report";
    write_lifecycle_run(&home, run_id, None);

    let run_dir = home
        .join("control_plane")
        .join("lifecycle_runs")
        .join(run_id);
    let state_path = run_dir.join("state.json");
    let lifecycle_report_path = run_dir.join("report.md");
    let worker_report_path = run_dir.join("worker-report.md");
    fs::write(&lifecycle_report_path, "# lifecycle report without DoU\n")
        .expect("lifecycle report");
    fs::write(
        &worker_report_path,
        "---\ndou_index: 0\n---\n# worker ZERO DoU report\n",
    )
    .expect("worker report");

    let mut state: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&state_path).expect("state text"))
            .expect("state json");
    state["dou_index"] = serde_json::Value::Null;
    state["stages"][0]["launch"]["report"] = json!(worker_report_path.to_string_lossy());
    fs::write(
        &state_path,
        serde_json::to_string_pretty(&state).expect("serialise state"),
    )
    .expect("rewrite state");

    let plane = ControlPlane::new(&home);
    let summary = plane
        .load_lifecycle_run_summaries()
        .into_iter()
        .find(|summary| summary.run_id == run_id)
        .expect("summary present");

    assert_eq!(
        summary.dou_index,
        Some(0),
        "no-await lifecycle status reads the launched worker report before the lifecycle report"
    );
    assert_eq!(summary.dou_readiness, "zero");
}

#[test]
fn lifecycle_summary_falls_back_to_canonical_files_when_embedded_paths_are_stale() {
    let home = temp_home("lifecycle-stale-paths");
    let run_id = "life-ship-smoke-stale-paths";
    write_lifecycle_run(&home, run_id, None);

    let state_path = home
        .join("control_plane")
        .join("lifecycle_runs")
        .join(run_id)
        .join("state.json");
    let mut state: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&state_path).expect("state text"))
            .expect("state json");
    state["state_path"] = json!("/tmp/vibecrafted-missing-state.json");
    state["report_path"] = json!("/tmp/vibecrafted-missing-report.md");
    fs::write(
        &state_path,
        serde_json::to_string_pretty(&state).expect("serialise stale-path state"),
    )
    .expect("rewrite state");

    let plane = ControlPlane::new(&home);
    let summary = plane
        .load_lifecycle_run_summaries()
        .into_iter()
        .find(|summary| summary.run_id == run_id)
        .expect("summary present");

    assert_eq!(
        summary.dou_index,
        Some(0),
        "canonical report.md remains the DoU fallback when embedded report_path is stale"
    );
    assert!(
        !summary.updated_at.is_empty(),
        "canonical state.json mtime remains the summary timestamp when embedded state_path is stale"
    );
}

#[test]
fn stale_launching_lifecycle_without_pid_is_abandoned_not_approve() {
    let home = temp_home("lifecycle-abandoned");
    let run_id = "life-audi-stale-launching";
    write_lifecycle_run(&home, run_id, None);
    let state_path = home
        .join("control_plane")
        .join("lifecycle_runs")
        .join(run_id)
        .join("state.json");
    let stale = std::time::SystemTime::now()
        .checked_sub(std::time::Duration::from_secs(7 * 24 * 60 * 60))
        .expect("stale clock");
    fs::File::open(&state_path)
        .expect("state file")
        .set_modified(stale)
        .expect("mtime");

    let plane = ControlPlane::new(&home);
    let summary = plane
        .load_lifecycle_run_summaries()
        .into_iter()
        .find(|summary| summary.run_id == run_id)
        .expect("summary present");

    assert_eq!(summary.status, "abandoned");
    assert!(
        summary.human_controls.is_empty(),
        "abandoned runs must not keep approve_transition"
    );
    assert!(
        summary.next_action.starts_with("Abandoned ·"),
        "next action names abandonment with age, got {:?}",
        summary.next_action
    );
    assert!(
        !summary.next_action.contains("approve_transition"),
        "next action must not be Operator: approve_transition"
    );

    let resolved = plane
        .resolve_lifecycle_run(run_id)
        .expect("nested lifecycle");
    assert_eq!(resolved.status, "abandoned");
    assert!(resolved.human_controls.is_empty());

    let view = plane.compute_view(Utc::now());
    let derived = plane.derived_run(run_id, Utc::now()).expect("derived run");
    let listed = plane.derived_runs(Utc::now());
    assert_eq!(derived.state, "abandoned");
    assert!(
        listed
            .iter()
            .any(|run| run.run_id == run_id && run.state == "abandoned"),
        "list derivation must show the same abandoned overlay as detail"
    );
    assert_eq!(
        view.recent_runs
            .iter()
            .find(|run| run.run_id == run_id)
            .map(|run| run.state.as_str())
            .or(Some(derived.state.as_str())),
        Some("abandoned"),
        "reader surfaces agree on abandoned"
    );
}

#[test]
fn read_state_view_marks_stale_ownerless_lifecycle_abandoned() {
    let home = temp_home("lifecycle-snapshot-abandoned");
    let run_id = "life-audi-stale-snapshot";
    write_lifecycle_run(&home, run_id, None);
    let state_path = home
        .join("control_plane")
        .join("lifecycle_runs")
        .join(run_id)
        .join("state.json");
    let stale = std::time::SystemTime::now()
        .checked_sub(std::time::Duration::from_secs(7 * 24 * 60 * 60))
        .expect("stale clock");
    fs::File::open(&state_path)
        .expect("state file")
        .set_modified(stale)
        .expect("mtime");

    let plane = ControlPlane::new(&home);
    let view = plane.read_state_view();

    let recent = view
        .recent_runs
        .iter()
        .find(|run| run.run_id == run_id)
        .expect("snapshot view keeps the stale lifecycle container discoverable");
    assert_eq!(
        recent.state, "abandoned",
        "the snapshot read must carry the same liveness overlay as compute_view"
    );
    assert_eq!(recent.health, "stalled");
    assert_eq!(recent.last_error, "no live owner");
    assert!(
        view.active_runs.iter().all(|run| run.run_id != run_id),
        "an ownerless lifecycle container must never read as launching/active"
    );
    assert!(
        view.stalled_runs
            .iter()
            .any(|run| run.run_id == run_id && run.state == "abandoned"),
        "the abandoned container reads stalled, not active"
    );
    assert!(
        !serde_json::to_string(&view.recent_runs)
            .expect("recent runs JSON")
            .contains("approve_transition"),
        "the state payload must not advertise approve_transition for an ownerless run"
    );
}

#[test]
fn fresh_launching_lifecycle_without_pid_is_not_abandoned() {
    let home = temp_home("lifecycle-fresh-launching");
    let run_id = "life-audi-fresh-launching";
    write_lifecycle_run(&home, run_id, None);

    let plane = ControlPlane::new(&home);
    let summary = plane
        .load_lifecycle_run_summaries()
        .into_iter()
        .find(|summary| summary.run_id == run_id)
        .expect("summary present");

    assert_eq!(summary.status, "launching");
    assert!(
        summary
            .human_controls
            .contains(&"approve_transition".to_string()),
        "fresh launching still exposes the written human control"
    );
}
