//! Read-only access to `~/.vibecrafted/control_plane/`.
//!
//! Mirrors the *read half* of `control_plane.sync_state` / `lookup_run`, but
//! never writes: no snapshot files, no event appends, no `.sync.lock`. Two
//! paths are offered:
//!
//! * [`ControlPlane::load_snapshots`] / [`ControlPlane::lookup_run`] — the cheap
//!   path that trusts the merged `runs/<id>.json` snapshots the Python writer
//!   already produced.
//! * [`ControlPlane::compute_view`] — the "merge in Rust" path (SCAFFOLD flaga,
//!   option a): read the three raw sources (`*.meta.json`, `*.lock`,
//!   `marbles/**/state.json`), normalise and merge them in-process, and project
//!   active/recent/warnings without ever depending on the Python sync having
//!   run. This is what lets the web/TUI frontends be self-sufficient.

use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use chrono::{DateTime, Utc};

use crate::events::EventStream;
use crate::model::{
    AgentMeta, DeliverySealRef, Event, FINAL_STATES, Health, LifecycleRun, LifecycleRunSummary,
    OperatorAgentPolicyProjection, OperatorAgentProjection, RECENT_RUN_LIMIT, RUN_STALL_SECONDS,
    RunStatus, SettlementBoard, SettlementTui, SettlementVerdict, SupervisionRelationProjection,
    TrustReceiptV1, coerce_int_value, is_final_state, merge_status, operator_session_name,
    parse_iso, skill_from_code, state_health,
};

/// Resolve `~`-prefixed paths against `$HOME`. Other paths pass through.
fn expanduser(path: PathBuf) -> PathBuf {
    if let Ok(stripped) = path.strip_prefix("~") {
        if let Some(home) = home_dir() {
            return home.join(stripped);
        }
    }
    path
}

/// `$HOME` as a path. Unix/macOS only — Vibecrafted runs on darwin/linux.
fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME").map(PathBuf::from)
}

/// Vibecrafted home. Mirrors `runtime_paths.vibecrafted_home`:
/// `$VIBECRAFTED_HOME` (expanded) if set & non-empty, else `~/.vibecrafted`.
#[must_use]
pub fn vibecrafted_home() -> PathBuf {
    if let Some(raw) = std::env::var_os("VIBECRAFTED_HOME") {
        if !raw.is_empty() {
            return expanduser(PathBuf::from(raw));
        }
    }
    home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".vibecrafted")
}

/// Whether `run_id` is one canonical, non-traversing path component.
///
/// Control-plane readers interpolate run ids into snapshot and runtime paths,
/// so the accepted grammar is deliberately smaller than a generic filename:
/// ASCII alphanumerics plus `.`, `_`, and `-`, with an alphanumeric first
/// byte and a bounded length. HTTP/UI callers share this predicate instead of
/// growing weaker per-surface sanitizers.
#[must_use]
pub fn is_safe_run_id(run_id: &str) -> bool {
    let bytes = run_id.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 255
        && bytes[0].is_ascii_alphanumeric()
        && bytes
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

/// A read-only handle on a control-plane root directory.
#[derive(Debug, Clone)]
pub struct ControlPlane {
    home: PathBuf,
    control_plane_home: PathBuf,
}

/// Aggregate projection. Mirrors the read-shape of `control_plane.sync_state`'s
/// return payload, minus `generated_at` (callers stamp their own clock).
#[derive(Debug, Clone)]
pub struct StateView {
    /// Runs with current live activity evidence.
    pub active_runs: Vec<RunStatus>,
    /// Non-terminal runs whose last activity crossed the stall threshold.
    pub stalled_runs: Vec<RunStatus>,
    /// Up to [`RECENT_RUN_LIMIT`] most-recently-updated runs.
    pub recent_runs: Vec<RunStatus>,
    /// Human-readable warnings (stalls, locks without reports).
    pub warnings: Vec<String>,
    /// Newest-first event tail.
    pub events: Vec<Event>,
    /// Python-owned settlement truth across retained `runs/*.json` snapshots,
    /// plus the active count from this Rust projection.
    pub settlement_counts: SettlementBoard,
}

impl ControlPlane {
    /// Handle rooted at the given Vibecrafted home (the dir that *contains*
    /// `control_plane/`).
    #[must_use]
    pub fn new(home: impl Into<PathBuf>) -> Self {
        let home = home.into();
        let control_plane_home = home.join("control_plane");
        Self {
            home,
            control_plane_home,
        }
    }

    /// Handle rooted at an explicit control-plane directory.
    ///
    /// Frontends already receive `.../control_plane` as their state root. This
    /// constructor lets them consume the canonical reader without guessing a
    /// second state path or reparsing the same files.
    #[must_use]
    pub fn from_control_plane_home(control_plane_home: impl Into<PathBuf>) -> Self {
        let control_plane_home = control_plane_home.into();
        let home = control_plane_home
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| control_plane_home.clone());
        Self {
            home,
            control_plane_home,
        }
    }

    /// Handle rooted at [`vibecrafted_home`] (env-aware default).
    #[must_use]
    pub fn from_env() -> Self {
        Self::new(vibecrafted_home())
    }

    /// `<home>/control_plane`.
    #[must_use]
    pub fn control_plane_home(&self) -> PathBuf {
        self.control_plane_home.clone()
    }

    /// `<home>/control_plane/runs`.
    #[must_use]
    pub fn run_snapshot_dir(&self) -> PathBuf {
        self.control_plane_home().join("runs")
    }

    /// `<home>/control_plane/runtime_runs/<id>` — where the core runtime writes
    /// a run (`prompt.md`/`transcript.log`; `meta.json` optional) before the
    /// snapshot sync merges it into `runs/<id>.json`. Mirrors the Python
    /// `control_plane._runtime_run_dir`.
    #[must_use]
    pub fn runtime_run_dir(&self, run_id: &str) -> PathBuf {
        self.control_plane_home().join("runtime_runs").join(run_id)
    }

    /// `<home>/control_plane/lifecycle_runs`.
    #[must_use]
    pub fn lifecycle_runs_dir(&self) -> PathBuf {
        self.control_plane_home().join("lifecycle_runs")
    }

    /// `<home>/control_plane/lifecycle_runs/<id>`.
    #[must_use]
    pub fn lifecycle_run_dir(&self, run_id: &str) -> PathBuf {
        self.lifecycle_runs_dir().join(run_id)
    }

    /// `<home>/control_plane/events.jsonl`.
    #[must_use]
    pub fn event_stream_path(&self) -> PathBuf {
        self.control_plane_home().join("events.jsonl")
    }

    /// An [`EventStream`] over this plane's `events.jsonl` — the SSE substrate.
    #[must_use]
    pub fn events(&self) -> EventStream {
        EventStream::new(self.event_stream_path())
    }

    /// Load every `runs/<id>.json` snapshot, sorted newest-first by
    /// `updated_at`. Unreadable / malformed files are skipped, not fatal
    /// (mirrors the Python `_read_json` swallow-on-error behaviour).
    #[must_use]
    pub fn load_snapshots(&self) -> Vec<RunStatus> {
        let mut runs = self.read_snapshot_dir();
        sort_recent_first(&mut runs);
        runs
    }

    fn read_snapshot_dir(&self) -> Vec<RunStatus> {
        let dir = self.run_snapshot_dir();
        let Ok(entries) = fs::read_dir(&dir) else {
            return Vec::new();
        };
        let mut runs = Vec::new();
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("json") {
                continue;
            }
            if let Some(run) = read_run_status(&path, false) {
                if is_safe_run_id(&run.run_id) {
                    let run = self.attach_runtime_recovery_if_present(run, false);
                    runs.push(self.attach_seal_if_present(run));
                }
            }
        }
        runs
    }

    /// Look up a single run by id from the on-disk snapshots. Mirrors
    /// `control_plane.lookup_run` but without the write-side sync — reads the
    /// merged snapshot directly.
    #[must_use]
    pub fn lookup_run(&self, run_id: &str) -> Option<RunStatus> {
        let target = run_id.trim();
        if !is_safe_run_id(target) {
            return None;
        }
        let direct = self.run_snapshot_dir().join(format!("{target}.json"));
        if let Some(run) = read_run_status(&direct, true) {
            if run.run_id == target {
                let run = self.attach_runtime_recovery_if_present(run, true);
                return Some(self.attach_seal_if_present(run));
            }
        }
        if let Some(mut run) = self
            .load_snapshots()
            .into_iter()
            .find(|run| run.run_id == target)
        {
            refresh_worker_liveness(&mut run);
            return Some(run);
        }
        // Read-follows-write: a still-launching run lives in runtime_runs/ before
        // the snapshot sync merges it into runs/<id>.json. Mirror the first probe
        // of control_plane.resolve_run so this frontend eye reads the same place
        // the runtime wrote (Niezmiennik 3 — one contract, many eyes).
        if let Some(run) = self.resolve_runtime_run(target) {
            return Some(self.attach_seal_if_present(run));
        }
        self.resolve_lifecycle_run(target)
            .map(|run| self.lifecycle_run_status(&run))
    }

    /// Attach a seal projection from `runtime_runs/<id>/delivery-seal.json`
    /// when the snapshot does not already carry one. Read-only; never invents
    /// delivery axes from process state.
    fn attach_seal_if_present(&self, mut run: RunStatus) -> RunStatus {
        if run.seal.is_none() {
            run.seal = self.read_seal_ref(&run.run_id);
        }
        run
    }

    /// Join explicit supervisor recovery evidence from runtime `meta.json`
    /// onto a Python snapshot. The legacy snapshot `session_id` is preserved
    /// but never used as a fallback for either explicit identity.
    fn attach_runtime_recovery_if_present(
        &self,
        mut run: RunStatus,
        probe_worker_alive: bool,
    ) -> RunStatus {
        if !is_safe_run_id(&run.run_id) {
            return run;
        }
        let path = self.runtime_run_dir(&run.run_id).join("meta.json");
        let Some(payload) = read_json::<serde_json::Value>(&path) else {
            if probe_worker_alive {
                refresh_worker_liveness(&mut run);
            }
            return run;
        };
        let string = |key: &str| {
            payload
                .get(key)
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
        };
        if run.worker_pid.is_none() {
            run.worker_pid = payload.get("worker_pid").and_then(coerce_int_value);
        }
        if run.worker_pgid.is_none() {
            run.worker_pgid = payload.get("worker_pgid").and_then(coerce_int_value);
        }
        if run.worker_alive.is_none() {
            run.worker_alive = payload
                .get("worker_alive")
                .and_then(serde_json::Value::as_bool);
        }
        run.recovery_required |= payload
            .get("recovery_required")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false);
        if run.stop_reason.is_empty() {
            run.stop_reason = string("stop_reason").to_string();
        }
        if run.commit_sha.is_empty() {
            run.commit_sha = string("commit_sha").to_string();
        }
        if run.agent_session_id.is_empty() {
            run.agent_session_id = string("agent_session_id").to_string();
        }
        if run.runtime_session_id.is_empty() {
            run.runtime_session_id = string("runtime_session_id").to_string();
        }
        if run.resume_of.is_empty() {
            run.resume_of = string("resume_of").to_string();
        }
        if run.attempt.is_none() {
            run.attempt = payload.get("attempt").and_then(json_u64);
        }
        if run.trust_receipt.is_none() {
            run.trust_receipt = payload
                .get("trust_receipt")
                .and_then(|value| serde_json::from_value::<TrustReceiptV1>(value.clone()).ok());
        }

        let terminal = run.is_terminal();
        let await_run = !terminal
            && run
                .controls
                .as_ref()
                .map(|controls| controls.await_run)
                .unwrap_or(true);
        let retry_from_meta = terminal
            && (run.skill == "marbles"
                || !string("prompt").trim().is_empty()
                || !string("file").trim().is_empty());
        let retry = retry_from_meta || run.controls.as_ref().is_some_and(|controls| controls.retry);
        let stop = !terminal
            && run
                .controls
                .as_ref()
                .map(|controls| controls.stop)
                .unwrap_or(run.worker_alive == Some(true));
        run.set_controls(await_run, stop, retry);
        if probe_worker_alive {
            refresh_worker_liveness(&mut run);
        }
        run
    }

    /// Read `delivery-seal.json` under the runtime run directory, if present
    /// and well-formed enough to project.
    #[must_use]
    pub fn read_seal_ref(&self, run_id: &str) -> Option<DeliverySealRef> {
        let target = run_id.trim();
        if !is_safe_run_id(target) {
            return None;
        }
        let path = self.runtime_run_dir(target).join("delivery-seal.json");
        read_json::<DeliverySealRef>(&path).filter(|seal| !seal.seal_id.is_empty())
    }

    /// Resolve a run straight from `runtime_runs/<id>/` — the read-follows-write
    /// fallback mirroring `control_plane.resolve_run`. A just-launched run lives
    /// here (`transcript.log`; `meta.json` optional and frequently absent at
    /// launch) before the Python sync merges it into the `runs/` snapshots.
    /// Runtime metadata is projected when present so an old completed directory
    /// never masquerades as a fresh launch. `None` means the run has not reached
    /// the runtime tree yet (the "still launching → await" case).
    #[must_use]
    pub fn resolve_runtime_run(&self, run_id: &str) -> Option<RunStatus> {
        let target = run_id.trim();
        if !is_safe_run_id(target) {
            return None;
        }
        let dir = self.runtime_run_dir(target);
        if !dir.is_dir() {
            return None;
        }
        let meta = read_json::<serde_json::Value>(&dir.join("meta.json"));
        let value = |key: &str| {
            meta.as_ref()
                .and_then(|payload| payload.get(key))
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_string()
        };
        let integer = |key: &str| {
            meta.as_ref()
                .and_then(|payload| payload.get(key))
                .and_then(coerce_int_value)
        };
        let boolean = |key: &str| {
            meta.as_ref()
                .and_then(|payload| payload.get(key))
                .and_then(serde_json::Value::as_bool)
        };
        let state = {
            let status = value("status");
            if status.is_empty() {
                let state = value("state");
                if state.is_empty() {
                    "launching".to_string()
                } else {
                    state
                }
            } else {
                status
            }
        };
        let exit_code = meta
            .as_ref()
            .and_then(|payload| payload.get("exit_code"))
            .and_then(coerce_int_value);
        let completed_at = value("completed_at");
        let terminal = is_final_state(&state) || exit_code.is_some() || !completed_at.is_empty();
        let transcript = dir.join("transcript.log");
        let latest_transcript = {
            let declared = value("transcript");
            if !declared.is_empty() {
                declared
            } else if transcript.is_file() {
                transcript.to_string_lossy().into_owned()
            } else {
                String::new()
            }
        };
        let updated_at = {
            let updated = value("updated_at");
            if updated.is_empty() {
                completed_at.clone()
            } else {
                updated
            }
        };
        let mut status = RunStatus {
            run_id: target.to_string(),
            state,
            agent: value("agent"),
            skill: nonempty_runtime_value(&value("skill"), &value("workflow")),
            mode: value("mode"),
            root: value("root"),
            commit_sha: value("commit_sha"),
            operator_session: value("operator_session"),
            latest_report: value("report"),
            latest_transcript,
            last_error: nonempty_runtime_value(&value("message"), &value("reason")),
            updated_at,
            started_at: value("started_at"),
            health: if terminal { "final" } else { "active" }.to_string(),
            source: "runtime_runs".to_string(),
            lock_present: false,
            exit_code,
            liveness: if terminal {
                "terminal".to_string()
            } else {
                value("liveness")
            },
            launcher_pid: None,
            completed_at,
            session_id: value("session_id"),
            current_loop: None,
            total_loops: None,
            owner_pid: integer("owner_pid"),
            worker_pid: integer("worker_pid"),
            worker_pgid: integer("worker_pgid"),
            worker_alive: boolean("worker_alive"),
            operator_agent: meta.as_ref().and_then(operator_agent_projection),
            recovery_required: boolean("recovery_required").unwrap_or(false),
            stop_reason: value("stop_reason"),
            agent_session_id: value("agent_session_id"),
            runtime_session_id: value("runtime_session_id"),
            resume_of: value("resume_of"),
            attempt: meta
                .as_ref()
                .and_then(|payload| payload.get("attempt"))
                .and_then(json_u64),
            settlement_verdict: settlement_verdict(&value("settlement_verdict")),
            settlement_tui: settlement_tui(&value("settlement_tui")),
            settlement_reason: value("settlement_reason"),
            settlement_source: value("settlement_source"),
            settlement_at: value("settlement_at"),
            settlement_claim_digest: value("settlement_claim_digest"),
            settlement_waived: boolean("settlement_waived"),
            settlement_revision: meta
                .as_ref()
                .and_then(|payload| payload.get("settlement_revision"))
                .and_then(json_u64),
            trust_receipt: meta
                .as_ref()
                .and_then(|payload| payload.get("trust_receipt"))
                .and_then(|value| serde_json::from_value::<TrustReceiptV1>(value.clone()).ok()),
            controls: None,
            // No delivery section on a still-launching runtime dir unless a
            // seal file is later attached by attach_seal_if_present.
            execution_state: None,
            proof_state: None,
            delivery_state: None,
            seal: None,
        };
        enrich_run_status(
            &mut status,
            meta.as_ref().unwrap_or(&serde_json::Value::Null),
            true,
        );
        Some(status)
    }

    /// Every run currently materialised under `runtime_runs/` as a read-only
    /// [`RunStatus`]. The aggregate counterpart to
    /// [`Self::resolve_runtime_run`]; used by [`Self::compute_view`] so a fresh
    /// run shows on the dashboard before the snapshot sync merges it.
    fn iter_runtime_run_status(&self) -> Vec<RunStatus> {
        let root = self.control_plane_home().join("runtime_runs");
        let Ok(entries) = fs::read_dir(&root) else {
            return Vec::new();
        };
        let mut runs = Vec::new();
        for entry in entries.flatten() {
            if !entry.path().is_dir() {
                continue;
            }
            if let Some(name) = entry.file_name().to_str() {
                if let Some(run) = self.resolve_runtime_run(name) {
                    runs.push(run);
                }
            }
        }
        runs
    }

    /// Resolve a full nested lifecycle run from `lifecycle_runs/<id>/state.json`.
    ///
    /// Delivery-proof axes are projected onto the run and each stage (shape of
    /// `write_lifecycle_report`) so `/api/control/lifecycle/{run_id}` never
    /// has to re-derive them from `completed`/`artifact_ok` in the server.
    #[must_use]
    pub fn resolve_lifecycle_run(&self, run_id: &str) -> Option<LifecycleRun> {
        let target = run_id.trim();
        if !is_safe_run_id(target) {
            return None;
        }
        let state_path = self.lifecycle_run_dir(target).join("state.json");
        let mut run = read_json::<LifecycleRun>(&state_path)?;
        if run.run_id == target {
            run.project_delivery_axes();
            Some(run)
        } else {
            None
        }
    }

    /// Full nested lifecycle runs, newest first by `state.json` mtime.
    /// Each run carries projected delivery-proof axes (see
    /// [`LifecycleRun::project_delivery_axes`]).
    #[must_use]
    pub fn load_lifecycle_runs(&self) -> Vec<LifecycleRun> {
        let Ok(entries) = fs::read_dir(self.lifecycle_runs_dir()) else {
            return Vec::new();
        };
        let mut runs = Vec::new();
        for entry in entries.flatten() {
            if !entry.path().is_dir() {
                continue;
            }
            let state_path = entry.path().join("state.json");
            let Some(mut run) = read_json::<LifecycleRun>(&state_path) else {
                continue;
            };
            if !is_safe_run_id(&run.run_id) {
                continue;
            }
            run.project_delivery_axes();
            runs.push((modified_at(&state_path), run));
        }
        runs.sort_by_key(|(modified, _)| std::cmp::Reverse(*modified));
        runs.into_iter().map(|(_, run)| run).collect()
    }

    /// Compact lifecycle summaries for list APIs and dashboard cards.
    #[must_use]
    pub fn load_lifecycle_run_summaries(&self) -> Vec<LifecycleRunSummary> {
        self.load_lifecycle_runs()
            .iter()
            .map(|run| self.lifecycle_run_summary(run))
            .collect()
    }

    /// Compact summaries for only the newest lifecycle candidates.
    ///
    /// Directory metadata is cheap enough to rank the full set, while parsing
    /// every nested state and report is not. Dashboard callers use this bounded
    /// projection; the unbounded list API remains available above.
    #[must_use]
    pub fn load_recent_lifecycle_run_summaries(&self, limit: usize) -> Vec<LifecycleRunSummary> {
        if limit == 0 {
            return Vec::new();
        }
        let Ok(entries) = fs::read_dir(self.lifecycle_runs_dir()) else {
            return Vec::new();
        };
        let mut state_paths = entries
            .flatten()
            .filter_map(|entry| {
                entry
                    .path()
                    .is_dir()
                    .then(|| entry.path().join("state.json"))
            })
            .map(|state_path| (modified_at(&state_path), state_path))
            .collect::<Vec<_>>();
        state_paths.sort_by_key(|(modified, _)| std::cmp::Reverse(*modified));
        state_paths
            .into_iter()
            .filter_map(|(_, state_path)| {
                let mut run = read_json::<LifecycleRun>(&state_path)?;
                if run.run_id.is_empty() {
                    return None;
                }
                run.project_delivery_axes();
                Some(self.lifecycle_run_summary(&run))
            })
            .take(limit)
            .collect()
    }

    /// Every lifecycle run as a flat status projection for existing state views.
    #[must_use]
    pub fn iter_lifecycle_run_status(&self) -> Vec<RunStatus> {
        self.load_lifecycle_runs()
            .iter()
            .map(|run| self.lifecycle_run_status(run))
            .collect()
    }

    fn lifecycle_run_summary(&self, run: &LifecycleRun) -> LifecycleRunSummary {
        run.summary(
            self.lifecycle_run_updated_at(run),
            self.lifecycle_dou_index_from_reports(run),
        )
    }

    fn lifecycle_run_status(&self, run: &LifecycleRun) -> RunStatus {
        run.to_run_status(
            self.lifecycle_run_updated_at(run),
            self.lifecycle_dou_index_from_reports(run),
        )
    }

    fn lifecycle_dou_index_from_reports(&self, run: &LifecycleRun) -> Option<i64> {
        self.lifecycle_stage_report_path(run)
            .as_deref()
            .and_then(report_dou_index)
            .or_else(|| report_dou_index(&self.lifecycle_report_path(run)))
    }

    fn lifecycle_state_path(&self, run: &LifecycleRun) -> PathBuf {
        existing_or_canonical(
            &run.state_path,
            self.lifecycle_run_dir(&run.run_id).join("state.json"),
        )
    }

    fn lifecycle_report_path(&self, run: &LifecycleRun) -> PathBuf {
        existing_or_canonical(
            &run.report_path,
            self.lifecycle_run_dir(&run.run_id).join("report.md"),
        )
    }

    fn lifecycle_stage_report_path(&self, run: &LifecycleRun) -> Option<PathBuf> {
        let raw = run.stages.last()?.launch.get("report")?.as_str()?.trim();
        if raw.is_empty() {
            return None;
        }
        let path = PathBuf::from(raw);
        path.is_file().then_some(path)
    }

    fn lifecycle_run_updated_at(&self, run: &LifecycleRun) -> String {
        modified_at(&self.lifecycle_state_path(run))
            .map(DateTime::<Utc>::from)
            .map(|dt| dt.to_rfc3339())
            .unwrap_or_default()
    }

    /// Read the newest-first event tail (default [`crate::model::EVENT_TAIL_LIMIT`]).
    #[must_use]
    pub fn read_event_tail(&self, limit: usize) -> Vec<Event> {
        self.events().tail(limit).unwrap_or_default()
    }

    /// Build a [`StateView`] from the on-disk snapshots plus the event tail.
    /// The cheap path: assumes `runs/<id>.json` are already merged by the
    /// Python writer. Read-only.
    #[must_use]
    pub fn read_state_view(&self) -> StateView {
        let mut runs = self.load_snapshots();
        let settlement_counts = SettlementBoard::from_snapshots(&runs);
        self.append_discoverable_lifecycle_runs(&mut runs);
        sort_recent_first(&mut runs);
        self.project_view(runs, settlement_counts)
    }

    fn append_discoverable_lifecycle_runs(&self, merged: &mut Vec<RunStatus>) {
        for mut run in self.iter_lifecycle_run_status() {
            if !merged.iter().any(|existing| existing.run_id == run.run_id)
                && (run.is_terminal() || run.health == "active")
            {
                // Lifecycle containers remain discoverable in `recent`, but
                // they are neither workers nor heartbeat sources. Only their
                // dispatched worker runs may enter active/stalled projections.
                if !run.is_terminal() {
                    run.health = "unknown".to_string();
                }
                merged.push(run);
            }
        }
    }

    /// Build a [`StateView`] by merging the three raw sources in Rust
    /// (`*.meta.json`, `*.lock`, `marbles/**/state.json`) — option (a). Never
    /// writes snapshots; `now` drives health derivation. This is the
    /// frontend-self-sufficient path.
    #[must_use]
    pub fn compute_view(&self, now: DateTime<Utc>) -> StateView {
        // Verdict truth is Python's persisted snapshot projection. Raw meta,
        // lock, runtime, marbles, and lifecycle sources below can disagree on
        // process state, but they must never be used to invent a settlement.
        let retained_snapshots = self.load_snapshots();
        let settlement_counts = SettlementBoard::from_snapshots(&retained_snapshots);
        // Snapshots are also the durable run baseline. Event rotation is
        // allowed only after Python has projected the generation into these
        // files, so archived generations must not be replayed here. Only the
        // active segment can contain evidence newer than the snapshots.
        // Fresher raw evidence below is folded with the normal timestamp-aware
        // merge.
        let mut merged = retained_snapshots.clone();
        for snapshot in &mut merged {
            snapshot.health = if snapshot.is_terminal() {
                "final".to_string()
            } else {
                state_health(&snapshot.state, &snapshot.updated_at, now)
                    .as_str()
                    .to_string()
            };
        }

        for path in self.iter_meta_files() {
            if let Some(payload) = read_json::<serde_json::Value>(&path) {
                if let Ok(meta) = serde_json::from_value::<AgentMeta>(payload.clone()) {
                    if let Some(mut status) = meta.normalize(now) {
                        enrich_run_status(&mut status, &payload, false);
                        absorb_status(&mut merged, status);
                    }
                }
            }
        }
        for path in self.iter_lock_files() {
            if let Some(status) = normalize_lock(&path, now) {
                absorb_status(&mut merged, status);
            }
        }
        for path in self.iter_marbles_state_files() {
            if let Some(status) =
                read_json::<MarblesState>(&path).and_then(|state| state.normalize(now))
            {
                absorb_status(&mut merged, status);
            }
        }
        // Python sync_state folds the event stream after raw sources. The
        // Mission Control active total must use the same projection; counting
        // every durable runtime_runs/ directory resurrects old workers.
        let mut events = self
            .events()
            .read_since(0, &[])
            .map(|batch| batch.events)
            .unwrap_or_default();
        events.retain(|event| !event_has_test_provenance(event, &self.home));
        let worker_pid_candidates: HashSet<(String, i64)> = events
            .iter()
            .filter(|event| event_owner_pid(event).is_none_or(pid_is_alive))
            .flat_map(|event| event_worker_pids(event).map(|pid| (event.run_id.clone(), pid)))
            .collect();
        let live_worker_runs: HashSet<String> = worker_pid_candidates
            .into_iter()
            .filter(|(_, pid)| pid_is_alive(*pid))
            .map(|(run_id, _)| run_id)
            .collect();
        for event in &events {
            if event.run_id.trim().is_empty() {
                continue;
            }
            // Settlement outbox records describe an already-persisted snapshot.
            // They are notification evidence, never process/liveness state:
            // normalising one as a generic event would resurrect a terminal
            // run as `unknown` or `active`.
            if event.kind == "settlement.changed" {
                continue;
            }
            let existing = merged.iter().find(|run| run.run_id == event.run_id);
            let status = normalize_event(event, existing, now);
            absorb_status(&mut merged, status);
        }
        for run in &mut merged {
            let operator_stopped = run.state == "stopped" && !run.stop_reason.trim().is_empty();
            if operator_stopped {
                run.health = "final".to_string();
                run.last_error.clear();
                run.recovery_required = false;
                run.set_controls(false, false, false);
                continue;
            }
            let terminal = run.is_terminal();
            if terminal {
                run.health = "final".to_string();
            } else if live_worker_runs.contains(&run.run_id) {
                run.worker_alive = Some(true);
                run.health = "active".to_string();
            } else {
                if run.worker_pid.is_some() || run.worker_pgid.is_some() {
                    run.worker_alive = Some(false);
                }
                if run.owner_pid.is_some() && run.worker_alive == Some(false) {
                    run.health = "stalled".to_string();
                    run.liveness = "pid_gone".to_string();
                    run.recovery_required = true;
                }
            }
            let await_run = !terminal
                && run
                    .controls
                    .as_ref()
                    .map(|controls| controls.await_run)
                    .unwrap_or(true);
            let stop = !terminal
                && run.worker_alive == Some(true)
                && run
                    .controls
                    .as_ref()
                    .map(|controls| controls.stop)
                    .unwrap_or(true);
            let retry = run.controls.as_ref().is_some_and(|controls| controls.retry);
            run.set_controls(await_run, stop, retry);
        }
        // runtime_runs/: a just-launched run no richer source has surfaced yet.
        // Read-follows-write — keeps the dashboard from a silent gap before the
        // sync merges the run (Niezmiennik 3). Only a recently touched fallback
        // can be live; the durable directory itself is not liveness evidence.
        for run in self.iter_runtime_run_status() {
            if !merged.iter().any(|r| r.run_id == run.run_id)
                && self.runtime_run_is_fresh(&run.run_id, now)
            {
                merged.push(run);
            }
        }
        self.append_discoverable_lifecycle_runs(&mut merged);

        sort_recent_first(&mut merged);
        if events.len() > crate::model::EVENT_TAIL_LIMIT {
            let start = events.len() - crate::model::EVENT_TAIL_LIMIT;
            events.drain(..start);
        }
        events.reverse();
        Self::project_view_with_events(merged, settlement_counts, events)
    }

    fn project_view(&self, runs: Vec<RunStatus>, settlement_counts: SettlementBoard) -> StateView {
        Self::project_view_with_events(
            runs,
            settlement_counts,
            self.read_event_tail(crate::model::EVENT_TAIL_LIMIT),
        )
    }

    fn project_view_with_events(
        runs: Vec<RunStatus>,
        mut settlement_counts: SettlementBoard,
        events: Vec<Event>,
    ) -> StateView {
        let warnings = warnings_for_runs(&runs);
        let active_runs: Vec<RunStatus> = runs
            .iter()
            .filter(|run| run.health == "active" && !is_final_state(&run.state))
            .cloned()
            .collect();
        let stalled_runs: Vec<RunStatus> = runs
            .iter()
            .filter(|run| run.health == "stalled" && !is_final_state(&run.state))
            .cloned()
            .collect();
        settlement_counts.active = active_runs.len();
        let recent_runs = runs.into_iter().take(RECENT_RUN_LIMIT).collect();
        StateView {
            active_runs,
            stalled_runs,
            recent_runs,
            warnings,
            events,
            settlement_counts,
        }
    }

    fn runtime_run_is_fresh(&self, run_id: &str, now: DateTime<Utc>) -> bool {
        let dir = self.runtime_run_dir(run_id);
        [
            dir.clone(),
            dir.join("meta.json"),
            dir.join("transcript.log"),
        ]
        .into_iter()
        .filter_map(|path| modified_at(&path))
        .map(DateTime::<Utc>::from)
        .max()
        .is_some_and(|updated| (now - updated).num_seconds() <= RUN_STALL_SECONDS)
    }

    fn iter_meta_files(&self) -> Vec<PathBuf> {
        rglob(&self.home.join("artifacts"), &|p| {
            p.to_str().is_some_and(|s| s.ends_with(".meta.json"))
        })
    }

    fn iter_lock_files(&self) -> Vec<PathBuf> {
        rglob(&self.home.join("locks"), &|p| {
            p.extension().and_then(|e| e.to_str()) == Some("lock")
        })
    }

    fn iter_marbles_state_files(&self) -> Vec<PathBuf> {
        rglob(&self.home.join("marbles"), &|p| {
            p.file_name().and_then(|n| n.to_str()) == Some("state.json")
        })
    }
}

fn absorb_status(merged: &mut Vec<RunStatus>, incoming: RunStatus) {
    if let Some(idx) = merged.iter().position(|run| run.run_id == incoming.run_id) {
        let existing = merged.remove(idx);
        merged.push(merge_status(Some(existing), incoming));
    } else {
        merged.push(incoming);
    }
}

fn normalize_event(event: &Event, existing: Option<&RunStatus>, now: DateTime<Utc>) -> RunStatus {
    let accepted_stop_event = event.kind == "audit:stop"
        && event
            .payload
            .get("accepted")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false);
    let prior_operator_stop =
        existing.filter(|run| run.state == "stopped" && !run.stop_reason.trim().is_empty());
    let operator_stop_sticky = accepted_stop_event || prior_operator_stop.is_some();
    let payload_string = |key: &str| {
        event
            .payload
            .get(key)
            .map(json_scalar_string)
            .unwrap_or_default()
    };
    let existing_string = |value: &str, fallback: &str| {
        if value.is_empty() {
            fallback.to_string()
        } else {
            value.to_string()
        }
    };

    let mut state = payload_string("state");
    if state.is_empty() {
        if let Some(lifecycle_state) = event.kind.strip_prefix("lifecycle:") {
            state = lifecycle_state.to_string();
        } else if event.kind == "launch" {
            state = "created".to_string();
        } else if accepted_stop_event {
            state = "stopped".to_string();
        } else {
            state = existing
                .map(|run| run.state.clone())
                .unwrap_or_else(|| "unknown".to_string());
        }
    }

    let root = existing_string(
        &payload_string("root"),
        existing.map(|run| run.root.as_str()).unwrap_or_default(),
    );
    let agent = existing_string(
        &payload_string("agent"),
        existing.map(|run| run.agent.as_str()).unwrap_or("unknown"),
    );
    let skill = existing_string(
        &payload_string("skill"),
        existing.map(|run| run.skill.as_str()).unwrap_or("unknown"),
    );
    let mode = existing_string(
        &payload_string("mode"),
        existing.map(|run| run.mode.as_str()).unwrap_or("unknown"),
    );
    let updated_at = if event.ts.is_empty() {
        existing
            .map(|run| run.updated_at.clone())
            .unwrap_or_default()
    } else {
        event.ts.clone()
    };
    let started_at = existing_string(
        &payload_string("started_at"),
        existing
            .map(|run| run.started_at.as_str())
            .filter(|value| !value.is_empty())
            .unwrap_or(&updated_at),
    );
    let exit_code = event
        .payload
        .get("exit_code")
        .and_then(coerce_int_value)
        .or_else(|| existing.and_then(|run| run.exit_code));
    let launcher_pid = event
        .payload
        .get("launcher_pid")
        .and_then(coerce_int_value)
        .or_else(|| existing.and_then(|run| run.launcher_pid));
    let worker_pid = event
        .payload
        .get("worker_pid")
        .and_then(coerce_int_value)
        .or_else(|| existing.and_then(|run| run.worker_pid));
    let worker_pgid = event
        .payload
        .get("worker_pgid")
        .and_then(coerce_int_value)
        .or_else(|| existing.and_then(|run| run.worker_pgid));
    let owner_pid = event
        .payload
        .get("owner_pid")
        .and_then(coerce_int_value)
        .or_else(|| existing.and_then(|run| run.owner_pid));
    let payload_error = existing_string(&payload_string("error"), &payload_string("last_error"));
    let last_error = if !payload_error.is_empty() {
        payload_error
    } else if let Some(existing) = existing.filter(|run| !run.last_error.is_empty()) {
        existing.last_error.clone()
    } else if event.kind != "state"
        && matches!(
            state.as_str(),
            "blocked" | "failed" | "ghost" | "stalled" | "timed_out"
        )
    {
        event.message.clone()
    } else {
        String::new()
    };
    let declared_health = payload_string("health");
    let heartbeat_at = payload_string("heartbeat_at");
    let activity_at = if heartbeat_at.is_empty() {
        updated_at.as_str()
    } else {
        heartbeat_at.as_str()
    };
    let health = if matches!(declared_health.as_str(), "stalled" | "final" | "unknown") {
        declared_health
    } else {
        state_health(&state, activity_at, now).as_str().to_string()
    };

    let event_settlement_verdict = settlement_verdict(&payload_string("settlement_verdict"))
        .or_else(|| existing.and_then(|run| run.settlement_verdict));
    let event_settlement_tui = settlement_tui(&payload_string("settlement_tui"))
        .or_else(|| existing.and_then(|run| run.settlement_tui));
    let payload_bool = |key: &str| event.payload.get(key).and_then(serde_json::Value::as_bool);
    let payload_u64 = |key: &str| event.payload.get(key).and_then(json_u64);
    let mut status = RunStatus {
        run_id: event.run_id.trim().to_string(),
        state: state.clone(),
        agent,
        skill,
        mode,
        root: root.clone(),
        commit_sha: existing_string(
            &payload_string("commit_sha"),
            existing
                .map(|run| run.commit_sha.as_str())
                .unwrap_or_default(),
        ),
        operator_session: operator_session_name(&root, event.run_id.trim()),
        latest_report: existing_string(
            &payload_string("report"),
            existing
                .map(|run| run.latest_report.as_str())
                .unwrap_or_default(),
        ),
        latest_transcript: existing_string(
            &payload_string("transcript"),
            existing
                .map(|run| run.latest_transcript.as_str())
                .unwrap_or_default(),
        ),
        last_error,
        updated_at: updated_at.clone(),
        started_at,
        health,
        source: "event-stream".to_string(),
        lock_present: existing.is_some_and(|run| run.lock_present),
        exit_code,
        liveness: existing_string(
            &payload_string("liveness"),
            existing
                .map(|run| run.liveness.as_str())
                .unwrap_or_default(),
        ),
        launcher_pid,
        completed_at: existing_string(
            &payload_string("completed_at"),
            existing
                .map(|run| run.completed_at.as_str())
                .unwrap_or_default(),
        ),
        session_id: existing_string(
            &payload_string("session_id"),
            existing
                .map(|run| run.session_id.as_str())
                .unwrap_or_default(),
        ),
        current_loop: existing.and_then(|run| run.current_loop),
        total_loops: existing.and_then(|run| run.total_loops),
        owner_pid,
        worker_pid,
        worker_pgid,
        worker_alive: payload_bool("worker_alive")
            .or_else(|| existing.and_then(|run| run.worker_alive)),
        operator_agent: existing.and_then(|run| run.operator_agent.clone()),
        recovery_required: payload_bool("recovery_required")
            .unwrap_or_else(|| existing.is_some_and(|run| run.recovery_required)),
        stop_reason: existing_string(
            &payload_string("stop_reason"),
            existing
                .map(|run| run.stop_reason.as_str())
                .unwrap_or_default(),
        ),
        agent_session_id: existing_string(
            &payload_string("agent_session_id"),
            existing
                .map(|run| run.agent_session_id.as_str())
                .unwrap_or_default(),
        ),
        runtime_session_id: existing_string(
            &payload_string("runtime_session_id"),
            existing
                .map(|run| run.runtime_session_id.as_str())
                .unwrap_or_default(),
        ),
        resume_of: existing_string(
            &payload_string("resume_of"),
            existing
                .map(|run| run.resume_of.as_str())
                .unwrap_or_default(),
        ),
        attempt: payload_u64("attempt").or_else(|| existing.and_then(|run| run.attempt)),
        settlement_verdict: event_settlement_verdict,
        settlement_tui: event_settlement_tui,
        settlement_reason: existing_string(
            &payload_string("settlement_reason"),
            existing
                .map(|run| run.settlement_reason.as_str())
                .unwrap_or_default(),
        ),
        settlement_source: existing_string(
            &payload_string("settlement_source"),
            existing
                .map(|run| run.settlement_source.as_str())
                .unwrap_or_default(),
        ),
        settlement_at: existing_string(
            &payload_string("settlement_at"),
            existing
                .map(|run| run.settlement_at.as_str())
                .unwrap_or_default(),
        ),
        settlement_claim_digest: existing_string(
            &payload_string("settlement_claim_digest"),
            existing
                .map(|run| run.settlement_claim_digest.as_str())
                .unwrap_or_default(),
        ),
        settlement_waived: payload_bool("settlement_waived")
            .or_else(|| existing.and_then(|run| run.settlement_waived)),
        settlement_revision: payload_u64("settlement_revision")
            .or_else(|| existing.and_then(|run| run.settlement_revision)),
        trust_receipt: event
            .payload
            .get("trust_receipt")
            .and_then(|value| serde_json::from_value::<TrustReceiptV1>(value.clone()).ok())
            .or_else(|| existing.and_then(|run| run.trust_receipt.clone())),
        controls: None,
        execution_state: None,
        proof_state: None,
        delivery_state: None,
        seal: None,
    };
    let payload = serde_json::Value::Object(
        event
            .payload
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
    );
    enrich_run_status(&mut status, &payload, false);
    if operator_stop_sticky {
        status.state = "stopped".to_string();
        status.health = "final".to_string();
        status.last_error.clear();
        status.recovery_required = false;
        if status.liveness.is_empty() {
            status.liveness = "terminal".to_string();
        }
        if let Some(stopped) = prior_operator_stop {
            status.updated_at.clone_from(&stopped.updated_at);
            status.completed_at.clone_from(&stopped.completed_at);
            status.exit_code = stopped.exit_code;
            status.liveness.clone_from(&stopped.liveness);
            status.stop_reason.clone_from(&stopped.stop_reason);
        }
        status.set_controls(false, false, false);
    }
    status
}

fn json_scalar_string(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::Null => String::new(),
        serde_json::Value::String(value) => value.clone(),
        other => other.to_string(),
    }
}

fn operator_agent_projection(payload: &serde_json::Value) -> Option<OperatorAgentProjection> {
    let role = payload.get("role")?.as_str()?.to_string();
    if role.is_empty() {
        return None;
    }
    let policy: OperatorAgentPolicyProjection =
        serde_json::from_value(payload.get("operator_policy")?.clone()).ok()?;
    let supervision: SupervisionRelationProjection =
        serde_json::from_value(payload.get("supervision")?.clone()).ok()?;
    Some(OperatorAgentProjection {
        role,
        prompt_role: payload
            .get("prompt_role")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default()
            .to_string(),
        provider_session_id: payload
            .get("provider_session_id")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default()
            .to_string(),
        policy,
        supervision,
        stop_actor_run_id: payload
            .get("stop_actor_run_id")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default()
            .to_string(),
    })
}

fn event_has_test_provenance(event: &Event, home: &Path) -> bool {
    if is_pytest_temp_path(home) {
        return false;
    }
    ["root", "source_dir", "report", "transcript", "meta"]
        .into_iter()
        .filter_map(|key| event.payload.get(key))
        .filter_map(serde_json::Value::as_str)
        .any(|value| is_pytest_temp_path(Path::new(value)))
}

fn event_worker_pids(event: &Event) -> impl Iterator<Item = i64> + '_ {
    ["worker_pid", "worker_pgid"]
        .into_iter()
        .filter_map(|key| event.payload.get(key))
        .filter_map(coerce_int_value)
}

fn event_owner_pid(event: &Event) -> Option<i64> {
    event.payload.get("owner_pid").and_then(coerce_int_value)
}

fn pid_is_alive(pid: i64) -> bool {
    if pid <= 0 {
        return false;
    }
    Command::new("kill")
        .args(["-0", &pid.to_string()])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

fn is_pytest_temp_path(path: &Path) -> bool {
    path.components().any(|component| {
        component
            .as_os_str()
            .to_str()
            .is_some_and(|part| part.starts_with("pytest-of-"))
    })
}

fn sort_recent_first(runs: &mut [RunStatus]) {
    let epoch = DateTime::<Utc>::from_timestamp(0, 0).expect("epoch is valid");
    runs.sort_by(|a, b| {
        let a_dt = parse_iso(&a.updated_at).unwrap_or(epoch);
        let b_dt = parse_iso(&b.updated_at).unwrap_or(epoch);
        b_dt.cmp(&a_dt)
    });
}

/// Mirrors `control_plane._warnings_for_runs` (capped at 6).
fn warnings_for_runs(runs: &[RunStatus]) -> Vec<String> {
    let mut warnings = Vec::new();
    for run in runs {
        if run.health == "stalled" {
            warnings.push(format!("{} looks stalled ({}).", run.run_id, run.state));
        }
        if run.lock_present
            && run.latest_report.is_empty()
            && !FINAL_STATES.contains(&run.state.as_str())
        {
            warnings.push(format!(
                "{} still has a live lock but no report artifact yet.",
                run.run_id
            ));
        }
    }
    warnings.truncate(6);
    warnings
}

fn read_json<T: serde::de::DeserializeOwned>(path: &Path) -> Option<T> {
    let text = fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

fn read_run_status(path: &Path, probe_worker_alive: bool) -> Option<RunStatus> {
    let payload = read_json::<serde_json::Value>(path)?;
    let mut run = serde_json::from_value::<RunStatus>(payload.clone()).ok()?;
    enrich_run_status(&mut run, &payload, probe_worker_alive);
    Some(run)
}

fn object_bool(
    object: Option<&serde_json::Map<String, serde_json::Value>>,
    key: &str,
) -> Option<bool> {
    object
        .and_then(|values| values.get(key))
        .and_then(serde_json::Value::as_bool)
}

fn nested_string(payload: &serde_json::Value, object: &str, key: &str) -> String {
    payload
        .get(object)
        .and_then(serde_json::Value::as_object)
        .and_then(|values| values.get(key))
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn json_u64(value: &serde_json::Value) -> Option<u64> {
    match value {
        serde_json::Value::Number(number) => number.as_u64(),
        serde_json::Value::String(raw) => raw.trim().parse::<u64>().ok(),
        _ => None,
    }
}

fn settlement_verdict(value: &str) -> Option<SettlementVerdict> {
    match value.trim().to_lowercase().as_str() {
        "finalized" => Some(SettlementVerdict::Finalized),
        "failed" => Some(SettlementVerdict::Failed),
        "needs_attention" => Some(SettlementVerdict::NeedsAttention),
        "invalid" => Some(SettlementVerdict::Invalid),
        _ => None,
    }
}

fn settlement_tui(value: &str) -> Option<SettlementTui> {
    match value.trim().to_lowercase().as_str() {
        "f" => Some(SettlementTui::F),
        "x" => Some(SettlementTui::X),
        "n" => Some(SettlementTui::N),
        _ => None,
    }
}

fn enrich_run_status(run: &mut RunStatus, payload: &serde_json::Value, probe_worker_alive: bool) {
    if probe_worker_alive && (run.worker_pid.is_some() || run.worker_pgid.is_some()) {
        let provider_alive =
            [run.worker_pid, run.worker_pgid]
                .into_iter()
                .flatten()
                .any(pid_is_alive);
        let owner_alive = run.owner_pid.is_none_or(pid_is_alive);
        run.worker_alive = Some(provider_alive && owner_alive);
        if run.owner_pid.is_some() && run.worker_alive == Some(false) && !run.is_terminal() {
            run.health = "stalled".to_string();
            run.liveness = "pid_gone".to_string();
            run.recovery_required = true;
        }
    }

    let settlement = payload
        .get("settlement")
        .and_then(serde_json::Value::as_object);
    if run.settlement_verdict.is_none() {
        run.settlement_verdict = settlement
            .and_then(|values| values.get("verdict"))
            .and_then(serde_json::Value::as_str)
            .and_then(settlement_verdict);
    }
    if run.settlement_tui.is_none() {
        run.settlement_tui = settlement
            .and_then(|values| values.get("tui"))
            .and_then(serde_json::Value::as_str)
            .and_then(settlement_tui);
    }
    if run.settlement_reason.is_empty() {
        run.settlement_reason = nested_string(payload, "settlement", "reason");
    }
    if run.settlement_source.is_empty() {
        run.settlement_source = nested_string(payload, "settlement", "source");
    }
    if run.settlement_at.is_empty() {
        run.settlement_at = nested_string(payload, "settlement", "settled_at");
    }
    if run.settlement_claim_digest.is_empty() {
        run.settlement_claim_digest = nested_string(payload, "settlement", "claim_digest");
    }
    if run.settlement_waived.is_none() {
        run.settlement_waived = settlement
            .and_then(|values| values.get("waived"))
            .and_then(serde_json::Value::as_bool);
    }
    if run.settlement_revision.is_none() {
        run.settlement_revision = settlement
            .and_then(|values| values.get("revision"))
            .and_then(json_u64);
    }

    let controls = payload
        .get("controls")
        .and_then(serde_json::Value::as_object);
    let lifecycle = payload
        .get("lifecycle")
        .and_then(serde_json::Value::as_object);
    if !run.recovery_required {
        run.recovery_required = object_bool(lifecycle, "recovery_required").unwrap_or(false);
    }
    let await_run = object_bool(controls, "await")
        .or_else(|| object_bool(lifecycle, "await"))
        .unwrap_or_else(|| !run.is_terminal());
    let stop = object_bool(controls, "stop")
        .or_else(|| object_bool(lifecycle, "stop"))
        .unwrap_or_else(|| !run.is_terminal() && run.worker_alive == Some(true));
    let retry = object_bool(controls, "retry")
        .or_else(|| object_bool(lifecycle, "resume"))
        .unwrap_or_else(|| {
            run.is_terminal()
                && (run.skill == "marbles"
                    || payload
                        .get("prompt")
                        .and_then(serde_json::Value::as_str)
                        .is_some_and(|value| !value.trim().is_empty())
                    || payload
                        .get("file")
                        .and_then(serde_json::Value::as_str)
                        .is_some_and(|value| !value.trim().is_empty()))
        });
    run.set_controls(await_run, stop, retry);
}

fn refresh_worker_liveness(run: &mut RunStatus) {
    if run.worker_pid.is_none() && run.worker_pgid.is_none() {
        return;
    }
    let provider_alive =
        [run.worker_pid, run.worker_pgid]
            .into_iter()
            .flatten()
            .any(pid_is_alive);
    let owner_alive = run.owner_pid.is_none_or(pid_is_alive);
    run.worker_alive = Some(provider_alive && owner_alive);
    if run.owner_pid.is_some() && run.worker_alive == Some(false) && !run.is_terminal() {
        run.health = "stalled".to_string();
        run.liveness = "pid_gone".to_string();
        run.recovery_required = true;
    }
    let terminal = run.is_terminal();
    let await_run = !terminal
        && run
            .controls
            .as_ref()
            .map(|controls| controls.await_run)
            .unwrap_or(true);
    let stop = !terminal
        && run.worker_alive == Some(true)
        && run
            .controls
            .as_ref()
            .map(|controls| controls.stop)
            .unwrap_or(true);
    let retry = run.controls.as_ref().is_some_and(|controls| controls.retry);
    run.set_controls(await_run, stop, retry);
}

fn nonempty_runtime_value(primary: &str, fallback: &str) -> String {
    if primary.is_empty() {
        fallback.to_string()
    } else {
        primary.to_string()
    }
}

fn modified_at(path: &Path) -> Option<std::time::SystemTime> {
    fs::metadata(path).and_then(|meta| meta.modified()).ok()
}

fn existing_or_canonical(declared: &str, canonical: PathBuf) -> PathBuf {
    if declared.is_empty() {
        return canonical;
    }
    let declared = PathBuf::from(declared);
    if declared.is_file() {
        declared
    } else {
        canonical
    }
}

fn report_dou_index(path: &Path) -> Option<i64> {
    let text = fs::read_to_string(path).ok()?;
    let mut lines = text.lines();
    if lines.next()?.trim() != "---" {
        return None;
    }
    for line in lines {
        let trimmed = line.trim();
        if trimmed == "---" {
            break;
        }
        let Some((key, value)) = trimmed.split_once(':') else {
            continue;
        };
        if key.trim() == "dou_index" {
            return parse_nonnegative_i64(value.trim());
        }
    }
    None
}

fn parse_nonnegative_i64(raw: &str) -> Option<i64> {
    let value = raw.trim().trim_matches('"').trim_matches('\'');
    coerce_int_value(&serde_json::Value::String(value.to_string())).filter(|item| *item >= 0)
}

/// Recursively collect files under `root` matching `pred`. Empty when `root`
/// is absent. A small std-only stand-in for `Path.rglob`.
fn rglob(root: &Path, pred: &dyn Fn(&Path) -> bool) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            match entry.file_type() {
                Ok(ft) if ft.is_dir() => stack.push(path),
                Ok(ft) if ft.is_file() && pred(&path) => out.push(path),
                _ => {}
            }
        }
    }
    out
}

/// Normalise a `*.lock` key=value file. Mirrors `control_plane._normalize_lock`.
fn normalize_lock(path: &Path, now: DateTime<Utc>) -> Option<RunStatus> {
    let text = fs::read_to_string(path).ok()?;
    let mut kv = std::collections::BTreeMap::new();
    for line in text.lines() {
        if let Some((key, value)) = line.split_once('=') {
            kv.insert(key.trim().to_string(), value.trim().to_string());
        }
    }
    let get = |k: &str| kv.get(k).cloned().unwrap_or_default();
    let run_id = get("run_id");
    let run_id = run_id.trim();
    if run_id.is_empty() {
        return None;
    }
    let root = get("root");
    let state = {
        let s = get("status");
        if s.is_empty() {
            "running".to_string()
        } else {
            s
        }
    };
    let started_at = get("started");
    let mode = {
        let m = get("mode");
        if m.is_empty() { get("runtime") } else { m }
    };
    let mode = if mode.is_empty() {
        "unknown".to_string()
    } else {
        mode
    };
    let agent = {
        let a = get("agent");
        if a.is_empty() {
            "unknown".to_string()
        } else {
            a
        }
    };
    let mut status = RunStatus {
        run_id: run_id.to_string(),
        state: state.clone(),
        agent,
        skill: skill_from_code(&get("skill")),
        mode,
        root: root.clone(),
        commit_sha: String::new(),
        operator_session: operator_session_name(&root, run_id),
        latest_report: String::new(),
        latest_transcript: String::new(),
        last_error: String::new(),
        updated_at: started_at.clone(),
        started_at: started_at.clone(),
        health: state_health(&state, &started_at, now).as_str().to_string(),
        source: "lock".to_string(),
        lock_present: true,
        exit_code: None,
        liveness: "lock_present".to_string(),
        launcher_pid: None,
        completed_at: String::new(),
        session_id: String::new(),
        current_loop: None,
        total_loops: None,
        owner_pid: None,
        worker_pid: None,
        worker_pgid: None,
        worker_alive: None,
        operator_agent: None,
        recovery_required: false,
        stop_reason: String::new(),
        agent_session_id: String::new(),
        runtime_session_id: String::new(),
        resume_of: String::new(),
        attempt: None,
        settlement_verdict: None,
        settlement_tui: None,
        settlement_reason: String::new(),
        settlement_source: String::new(),
        settlement_at: String::new(),
        settlement_claim_digest: String::new(),
        settlement_waived: None,
        settlement_revision: None,
        trust_receipt: None,
        controls: None,
        execution_state: None,
        proof_state: None,
        delivery_state: None,
        seal: None,
    };
    status.set_controls(true, false, false);
    Some(status)
}

/// Raw `marbles/**/state.json`. Only the fields used by
/// `control_plane._normalize_marbles_state` are modelled.
#[derive(Debug, Clone, serde::Deserialize)]
struct MarblesState {
    #[serde(default)]
    run_id: String,
    #[serde(default)]
    agent: String,
    #[serde(default)]
    mode: String,
    #[serde(default)]
    root: String,
    #[serde(default)]
    status: String,
    #[serde(default)]
    updated_at: String,
    #[serde(default)]
    started_at: String,
    #[serde(default)]
    failure_hint: String,
    #[serde(default)]
    current_loop: Option<i64>,
    #[serde(default)]
    total_loops: Option<i64>,
    #[serde(default)]
    loops: Vec<MarblesLoop>,
}

#[derive(Debug, Clone, serde::Deserialize)]
struct MarblesLoop {
    #[serde(default)]
    report: String,
    #[serde(default)]
    transcript: String,
    #[serde(default)]
    reason: String,
}

impl MarblesState {
    fn normalize(&self, now: DateTime<Utc>) -> Option<RunStatus> {
        let run_id = self.run_id.trim();
        if run_id.is_empty() {
            return None;
        }
        let latest = self.loops.last();
        let updated_at = if self.updated_at.is_empty() {
            self.started_at.clone()
        } else {
            self.updated_at.clone()
        };
        let state = if self.status.is_empty() {
            "unknown".to_string()
        } else {
            self.status.clone()
        };
        let mode = if self.mode.is_empty() {
            "steered".to_string()
        } else {
            self.mode.clone()
        };
        let agent = if self.agent.is_empty() {
            "unknown".to_string()
        } else {
            self.agent.clone()
        };
        let last_error = if !self.failure_hint.is_empty() {
            self.failure_hint.clone()
        } else {
            latest.map(|l| l.reason.clone()).unwrap_or_default()
        };
        let health = if is_final_state(&state) {
            Health::Final
        } else {
            state_health(&state, &updated_at, now)
        };
        let _ = RUN_STALL_SECONDS; // documented threshold lives in state_health
        let terminal = is_final_state(&state);
        let mut status = RunStatus {
            run_id: run_id.to_string(),
            state,
            agent,
            skill: "marbles".to_string(),
            mode,
            root: self.root.clone(),
            commit_sha: String::new(),
            operator_session: operator_session_name(&self.root, run_id),
            latest_report: latest.map(|l| l.report.clone()).unwrap_or_default(),
            latest_transcript: latest.map(|l| l.transcript.clone()).unwrap_or_default(),
            last_error,
            updated_at,
            started_at: self.started_at.clone(),
            health: health.as_str().to_string(),
            source: "marbles-state".to_string(),
            lock_present: false,
            exit_code: None,
            liveness: String::new(),
            launcher_pid: None,
            completed_at: String::new(),
            session_id: String::new(),
            current_loop: self.current_loop,
            total_loops: self.total_loops,
            owner_pid: None,
            worker_pid: None,
            worker_pgid: None,
            worker_alive: None,
            operator_agent: None,
            recovery_required: false,
            stop_reason: String::new(),
            agent_session_id: String::new(),
            runtime_session_id: String::new(),
            resume_of: String::new(),
            attempt: None,
            settlement_verdict: None,
            settlement_tui: None,
            settlement_reason: String::new(),
            settlement_source: String::new(),
            settlement_at: String::new(),
            settlement_claim_digest: String::new(),
            settlement_waived: None,
            settlement_revision: None,
            trust_receipt: None,
            controls: None,
            execution_state: None,
            proof_state: None,
            delivery_state: None,
            seal: None,
        };
        status.set_controls(!terminal, false, terminal);
        Some(status)
    }
}

#[cfg(test)]
mod tests {
    use super::{ControlPlane, is_safe_run_id};
    use crate::events::STREAM_SEGMENT_SCHEMA;
    use chrono::{DateTime, Duration, Utc};
    use serde_json::json;
    use std::fs;
    use std::io::ErrorKind;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    fn temp_home(prefix: &str) -> PathBuf {
        static NEXT_ID: AtomicU64 = AtomicU64::new(0);

        let base = std::env::var_os("TMPDIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/tmp"));
        let nanos = Utc::now().timestamp_nanos_opt().unwrap_or_default();
        for attempt in 0..100 {
            let nonce = NEXT_ID.fetch_add(1, Ordering::Relaxed);
            let candidate = base.join(format!(
                "control-core-{prefix}-{}-{nanos}-{nonce}-{attempt}",
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

    #[test]
    fn run_id_validation_rejects_traversal_and_unsafe_tokens() {
        for valid in ["impl-260811-123456-00001", "life_demo.2", "a"] {
            assert!(is_safe_run_id(valid), "expected valid run id: {valid}");
        }
        for invalid in [
            "",
            ".",
            "../secret",
            r"..\secret",
            "/absolute",
            "javascript:alert(1)",
            "two words",
        ] {
            assert!(
                !is_safe_run_id(invalid),
                "expected unsafe run id: {invalid}"
            );
        }
        assert!(!is_safe_run_id(&"a".repeat(256)));
    }

    #[test]
    fn lookup_run_rejects_a_snapshot_path_escape() {
        let home = temp_home("unsafe-run-id");
        let control_plane = home.join("control_plane");
        fs::create_dir_all(control_plane.join("runs")).expect("snapshot directory");
        fs::write(
            control_plane.join("secret.json"),
            serde_json::to_vec(&json!({
                "run_id": "../secret",
                "state": "completed",
                "agent": "codex",
                "skill": "workflow",
                "mode": "workflow",
                "root": "/tmp/repo",
                "updated_at": Utc::now().to_rfc3339(),
                "health": "final",
                "source": "fixture",
                "lock_present": false
            }))
            .expect("snapshot json"),
        )
        .expect("escaped snapshot fixture");

        assert!(ControlPlane::new(&home).lookup_run("../secret").is_none());

        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn accepted_operator_stop_survives_later_supervisor_failures() {
        let home = temp_home("sticky-stop");
        let control_plane = home.join("control_plane");
        let snapshots = control_plane.join("runs");
        fs::create_dir_all(&snapshots).expect("snapshots");
        fs::write(
            snapshots.join("sticky-stop.json"),
            serde_json::to_vec(&json!({
                "run_id": "sticky-stop",
                "state": "failed",
                "agent": "codex",
                "skill": "workflow",
                "mode": "workflow",
                "root": "/srv/checkout/vibecrafted",
                "updated_at": (Utc::now() - Duration::seconds(4)).to_rfc3339(),
                "health": "final",
                "liveness": "terminal",
                "recovery_required": true,
                "last_error": "stale snapshot failure"
            }))
            .expect("snapshot json"),
        )
        .expect("snapshot");
        let now = Utc::now();
        let records = [
            json!({
                "ts": (now - Duration::seconds(3)).to_rfc3339(),
                "run_id": "sticky-stop",
                "kind": "lifecycle:active",
                "message": "worker active",
                "payload": {
                    "state": "active",
                    "root": "/srv/checkout/vibecrafted",
                    "worker_pid": 987654,
                    "worker_pgid": 987654,
                    "liveness": "pid_alive"
                }
            }),
            json!({
                "ts": (now - Duration::seconds(2)).to_rfc3339(),
                "run_id": "sticky-stop",
                "kind": "audit:stop",
                "message": "run stopped",
                "payload": {
                    "accepted": true,
                    "state": "stopped",
                    "operator_stop_accepted": true,
                    "stop_reason": "manual operator stop",
                    "health": "final",
                    "liveness": "terminal",
                    "exit_code": 143
                }
            }),
            json!({
                "ts": (now - Duration::seconds(1)).to_rfc3339(),
                "run_id": "sticky-stop",
                "kind": "lifecycle:failed",
                "message": "process failed with exit code -15",
                "payload": {
                    "state": "failed",
                    "exit_code": -15,
                    "liveness": "terminal",
                    "recovery_required": true,
                    "error": "supervisor observed SIGTERM"
                }
            }),
            json!({
                "ts": now.to_rfc3339(),
                "run_id": "sticky-stop",
                "kind": "lifecycle:report_missing",
                "message": "artifact contract failed",
                "payload": {
                    "state": "report_missing",
                    "recovery_required": true,
                    "errors": ["report_missing"]
                }
            }),
        ];
        let encoded = records
            .iter()
            .map(serde_json::Value::to_string)
            .collect::<Vec<_>>()
            .join("\n");
        fs::write(control_plane.join("events.jsonl"), format!("{encoded}\n"))
            .expect("event stream");

        let view = ControlPlane::new(&home).compute_view(now);
        let run = view
            .recent_runs
            .iter()
            .find(|run| run.run_id == "sticky-stop")
            .expect("stopped run");

        assert_eq!(run.state, "stopped");
        assert_eq!(run.stop_reason, "manual operator stop");
        assert_eq!(run.exit_code, Some(143));
        assert!(!run.recovery_required);
        let controls = run.controls.as_ref().expect("controls");
        assert!(!controls.await_run);
        assert!(!controls.stop);
        assert!(!controls.retry);

        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn event_only_run_survives_rotation_via_snapshot() {
        let home = temp_home("snapshot-after-rotation");
        let control_plane = home.join("control_plane");
        let snapshots = control_plane.join("runs");
        fs::create_dir_all(&snapshots).expect("snapshots");
        let now = Utc::now();
        fs::write(
            snapshots.join("event-only.json"),
            serde_json::to_vec(&json!({
                "run_id": "event-only",
                "state": "active",
                "agent": "codex",
                "skill": "implement",
                "mode": "implement",
                "root": "/srv/checkout/vibecrafted",
                "operator_session": "vibecrafted-event-only",
                "latest_report": "",
                "latest_transcript": "",
                "last_error": "",
                "updated_at": now.to_rfc3339(),
                "started_at": now.to_rfc3339(),
                "health": "active",
                "source": "event-stream",
                "lock_present": false
            }))
            .expect("snapshot json"),
        )
        .expect("snapshot");
        fs::write(
            control_plane.join("events.jsonl"),
            format!(
                "{}\n",
                json!({
                    "ts": now.to_rfc3339(),
                    "run_id": "",
                    "kind": "stream.segment",
                    "message": "rotated generation",
                    "payload": {
                        "schema": STREAM_SEGMENT_SCHEMA,
                        "epoch": "epoch-snapshot",
                        "generation": 1
                    }
                })
            ),
        )
        .expect("header-only active generation");

        let view = ControlPlane::new(&home).compute_view(now);

        assert!(
            view.recent_runs
                .iter()
                .any(|run| run.run_id == "event-only"),
            "durable snapshot must outlive its rotated event generation"
        );
        assert!(
            view.active_runs
                .iter()
                .any(|run| run.run_id == "event-only")
        );
        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn compute_view_reads_only_active_events_after_rotation() {
        let home = temp_home("active-segment-only");
        let control_plane = home.join("control_plane");
        let archive = control_plane.join("events_archive");
        fs::create_dir_all(&archive).expect("event archive");
        let now = Utc::now();
        let segment = |generation| {
            json!({
                "ts": now.to_rfc3339(),
                "run_id": "",
                "kind": "stream.segment",
                "message": "event generation",
                "payload": {
                    "schema": STREAM_SEGMENT_SCHEMA,
                    "epoch": "epoch-active-only",
                    "generation": generation
                }
            })
        };
        let active = |run_id: &str| {
            json!({
                "ts": now.to_rfc3339(),
                "run_id": run_id,
                "kind": "lifecycle:active",
                "message": "worker active",
                "payload": {
                    "state": "active",
                    "root": "/srv/checkout/vibecrafted",
                    "worker_pid": std::process::id(),
                    "liveness": "pid_alive"
                }
            })
        };
        fs::write(
            archive.join("events-epoch-active-only-g00000000000000000000.jsonl"),
            format!("{}\n{}\n", segment(0), active("archived-without-snapshot")),
        )
        .expect("archived generation");
        fs::write(
            control_plane.join("events.jsonl"),
            format!("{}\n{}\n", segment(1), active("active-generation")),
        )
        .expect("active generation");

        let view = ControlPlane::new(&home).compute_view(now);

        assert!(
            view.active_runs
                .iter()
                .any(|run| run.run_id == "active-generation"),
            "active generation must still contribute fresher evidence"
        );
        assert!(
            view.recent_runs
                .iter()
                .all(|run| run.run_id != "archived-without-snapshot"),
            "archived generations are already owned by durable snapshots"
        );
        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn active_truth_separates_stalls_and_quarantines_pytest_events() {
        let home = temp_home("active-truth");
        let control_plane = home.join("control_plane");
        fs::create_dir_all(&control_plane).expect("control plane");
        let now = Utc::now();
        let records = [
            json!({
                "ts": now.to_rfc3339(),
                "run_id": "live-worker",
                "kind": "lifecycle:active",
                "message": "worker heartbeat",
                "payload": {
                    "state": "active",
                    "root": "/srv/checkout/vibecrafted",
                    "worker_pid": std::process::id(),
                    "liveness": "pid_alive",
                    "heartbeat_at": (now - Duration::hours(2)).to_rfc3339()
                }
            }),
            json!({
                "ts": now.to_rfc3339(),
                "run_id": "definitely-missing",
                "kind": "state",
                "message": "stale event only",
                "payload": {
                    "state": "running",
                    "health": "active",
                    "liveness": "pid_alive",
                    "heartbeat_at": (now - Duration::hours(2)).to_rfc3339(),
                    "root": "/srv/checkout/vibecrafted"
                }
            }),
            json!({
                "ts": now.to_rfc3339(),
                "run_id": "pytest-fixture-run",
                "kind": "lifecycle:active",
                "message": "fixture leak",
                "payload": {
                    "state": "active",
                    "root": "/private/tmp/pytest-of-operator/pytest-1/test_board0",
                    "launcher_pid": std::process::id()
                }
            }),
        ];
        let encoded = records
            .iter()
            .map(serde_json::Value::to_string)
            .collect::<Vec<_>>()
            .join("\n");
        fs::write(control_plane.join("events.jsonl"), format!("{encoded}\n"))
            .expect("event stream");

        let view = ControlPlane::new(&home).compute_view(now);

        assert_eq!(
            view.active_runs
                .iter()
                .map(|run| run.run_id.as_str())
                .collect::<Vec<_>>(),
            ["live-worker"]
        );
        assert_eq!(
            view.stalled_runs
                .iter()
                .map(|run| run.run_id.as_str())
                .collect::<Vec<_>>(),
            ["definitely-missing"]
        );
        assert!(
            view.recent_runs
                .iter()
                .all(|run| run.run_id != "pytest-fixture-run")
        );
        assert!(
            view.events
                .iter()
                .all(|event| event.run_id != "pytest-fixture-run")
        );
        assert_eq!(view.settlement_counts.active, 1);
        assert_eq!(view.settlement_counts.total_settled, 0);

        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn interactive_owner_and_provider_must_both_be_live_for_active_projection() {
        let home = temp_home("interactive-owner-liveness");
        let runtime = home.join("control_plane/runtime_runs/interactive-owner-dead");
        fs::create_dir_all(&runtime).expect("runtime run");
        let now = Utc::now();
        fs::write(
            runtime.join("meta.json"),
            serde_json::to_vec(&json!({
                "run_id": "interactive-owner-dead",
                "status": "active",
                "agent": "codex",
                "skill": "init",
                "root": "/srv/checkout/vibecrafted",
                "updated_at": now.to_rfc3339(),
                "liveness": "active",
                "owner_pid": 999999999_i64,
                "worker_pid": std::process::id()
            }))
            .expect("meta json"),
        )
        .expect("meta");

        let view = ControlPlane::new(&home).compute_view(now);
        let run = view
            .recent_runs
            .iter()
            .find(|run| run.run_id == "interactive-owner-dead")
            .expect("interactive run remains inspectable");

        assert_eq!(run.owner_pid, Some(999999999));
        assert_eq!(run.worker_alive, Some(false));
        assert!(
            view.active_runs
                .iter()
                .all(|run| run.run_id != "interactive-owner-dead")
        );
        assert_eq!(view.settlement_counts.active, 0);
        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn settlement_needs_attention_counts_and_stalled_bucket_is_orthogonal() {
        let unique = format!(
            "control-core-settle-stall-{}-{}",
            std::process::id(),
            Utc::now().timestamp_nanos_opt().unwrap_or_default()
        );
        let home = std::env::temp_dir().join(unique);
        let runs_dir = home.join("control_plane/runs");
        fs::create_dir_all(&runs_dir).expect("runs");
        let stamp = "2026-07-22T12:00:00+00:00";
        let write = |run_id: &str, state: &str, verdict: Option<&str>, tui: Option<&str>| {
            let mut payload = json!({
                "run_id": run_id,
                "state": state,
                "agent": "claude",
                "skill": "implement",
                "mode": "implement",
                "root": "/tmp/repo",
                "operator_session": format!("repo-{run_id}"),
                "latest_report": "",
                "latest_transcript": "",
                "last_error": "",
                "updated_at": stamp,
                "started_at": stamp,
                "health": "final",
                "source": "agent-meta",
                "lock_present": false,
                "exit_code": null,
                "liveness": "terminal",
                "completed_at": stamp,
                "session_id": "",
            });
            if let Some(v) = verdict {
                payload["settlement_verdict"] = json!(v);
            }
            if let Some(c) = tui {
                payload["settlement_tui"] = json!(c);
            }
            fs::write(
                runs_dir.join(format!("{run_id}.json")),
                serde_json::to_vec_pretty(&payload).unwrap(),
            )
            .unwrap();
        };
        write("snap-n", "failed", None, None);
        write("snap-attn", "completed", Some("needs_attention"), Some("n"));
        write("snap-f", "completed", Some("finalized"), Some("f"));
        write("snap-x", "failed", Some("failed"), Some("x"));

        let now = DateTime::parse_from_rfc3339("2026-07-22T12:30:00+00:00")
            .unwrap()
            .with_timezone(&Utc);
        let view = ControlPlane::new(&home).compute_view(now);
        assert_eq!(view.settlement_counts.f, 1, "{:?}", view.settlement_counts);
        assert_eq!(view.settlement_counts.x, 1, "{:?}", view.settlement_counts);
        // needs_attention + unsettled terminal(failed without verdict) → n >= 2
        assert!(
            view.settlement_counts.n >= 2,
            "expected n>=2 got {:?}",
            view.settlement_counts
        );
        // first-class stalled list always present (empty when nothing stalled)
        assert!(
            view.stalled_runs.is_empty() || view.stalled_runs.iter().all(|r| r.health == "stalled")
        );
        assert!(view.recent_runs.iter().any(|r| r.run_id == "snap-f"));

        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn lookup_run_projects_typed_trust_receipt_from_runtime_meta() {
        let home = temp_home("trust-receipt");
        let control_plane = home.join("control_plane");
        let runtime = control_plane.join("runtime_runs/receipt-run");
        let snapshots = control_plane.join("runs");
        fs::create_dir_all(&runtime).expect("runtime");
        fs::create_dir_all(&snapshots).expect("snapshots");
        let receipt = json!({
            "schema": "vibecrafted.trust-receipt.v1",
            "receipt_id": "a".repeat(64),
            "repo_root": "/srv/checkout/vibecrafted",
            "run_id": "receipt-run",
            "commit_sha": "b".repeat(40),
            "trust_verdict": "pass-with-gaps",
            "settlement_verdict": "needs_attention",
            "settlement_tui": "n",
            "settlement_revision": 9,
            "claim_digest": "c".repeat(64)
        });
        fs::write(
            runtime.join("meta.json"),
            serde_json::to_vec(&json!({
                "run_id": "receipt-run",
                "status": "failed",
                "exit_code": 9,
                "agent": "codex",
                "root": "/srv/checkout/vibecrafted",
                "settlement_verdict": "needs_attention",
                "settlement_tui": "n",
                "settlement_source": "trust",
                "settlement_revision": 9,
                "trust_receipt": receipt
            }))
            .expect("meta json"),
        )
        .expect("meta");
        fs::write(
            snapshots.join("receipt-run.json"),
            serde_json::to_vec(&json!({
                "run_id": "receipt-run",
                "state": "failed",
                "agent": "codex",
                "skill": "implement",
                "mode": "workflow",
                "root": "/srv/checkout/vibecrafted",
                "operator_session": "vibecrafted-receipt-run",
                "latest_report": "",
                "latest_transcript": "",
                "last_error": "",
                "updated_at": "2026-07-26T00:00:00Z",
                "started_at": "2026-07-26T00:00:00Z",
                "health": "final",
                "source": "agent-meta",
                "lock_present": false,
                "exit_code": 9,
                "settlement_verdict": "needs_attention",
                "settlement_tui": "n",
                "settlement_source": "trust",
                "settlement_revision": 9
            }))
            .expect("snapshot json"),
        )
        .expect("snapshot");

        let run = ControlPlane::new(&home)
            .lookup_run("receipt-run")
            .expect("run");
        let projected = run.trust_receipt.expect("typed trust receipt");
        assert_eq!(projected.schema, "vibecrafted.trust-receipt.v1");
        assert_eq!(projected.receipt_id, "a".repeat(64));
        assert_eq!(projected.commit_sha, "b".repeat(40));
        assert_eq!(projected.settlement_revision, 9);

        fs::remove_dir_all(home).ok();
    }
}
