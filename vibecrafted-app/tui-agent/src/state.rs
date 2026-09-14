use chrono::{DateTime, TimeZone, Utc};
use control_core::{
    ControlPlane, Event as CanonicalEvent, RunStatus as CanonicalRunStatus, is_active_state,
    is_final_state,
};
use serde::Deserialize;
use serde_json::Value;
use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
pub struct ControlPlaneState {
    pub root: PathBuf,
    /// Every retained `runs/*.json` snapshot, including operator-archived IDs.
    /// Mission Control settlement counts use this complete retained scope.
    pub retained_runs: Vec<RunSnapshot>,
    /// Operator-visible live/recent runs after archive-marker filtering.
    pub runs: Vec<RunSnapshot>,
    pub events: Vec<RunEvent>,
    pub archived_run_ids: HashSet<String>,
}

impl ControlPlaneState {
    pub fn load(root: impl AsRef<Path>) -> io::Result<Self> {
        let requested_root = root.as_ref().to_path_buf();
        let Some(root) = SafeControlPlaneRoot::new(root.as_ref())? else {
            return Ok(Self::empty(requested_root));
        };
        let archived_run_ids = root.load_archived_run_ids()?;
        let retained_runs = root.load_runs()?;
        let canonical =
            ControlPlane::from_control_plane_home(root.as_path()).compute_view(Utc::now());
        let canonical_runtime_authority = root.as_path().join("events.jsonl").is_file()
            || !canonical.active_runs.is_empty()
            || !canonical.stalled_runs.is_empty();
        let mut runs = retained_runs
            .iter()
            .filter(|snapshot| !archived_run_ids.contains(&snapshot.run_id))
            .filter(|snapshot| !canonical_runtime_authority || !snapshot.is_runtime_inflight())
            .cloned()
            .map(|snapshot| (snapshot.run_id.clone(), snapshot))
            .collect::<HashMap<_, _>>();
        for run in canonical
            .active_runs
            .into_iter()
            .chain(canonical.stalled_runs)
        {
            if !archived_run_ids.contains(&run.run_id) {
                runs.insert(run.run_id.clone(), canonical_run_snapshot(run));
            }
        }
        let runs = runs.into_values().collect();
        let events = canonical
            .events
            .into_iter()
            .map(canonical_run_event)
            .collect();
        Ok(Self {
            root: root.as_path().to_path_buf(),
            retained_runs,
            runs,
            events,
            archived_run_ids,
        })
    }

    pub fn empty(root: impl AsRef<Path>) -> Self {
        Self {
            root: root.as_ref().to_path_buf(),
            retained_runs: Vec::new(),
            runs: Vec::new(),
            events: Vec::new(),
            archived_run_ids: HashSet::new(),
        }
    }

    pub fn canonical_active_count(&self) -> usize {
        self.runs
            .iter()
            .filter(|snapshot| snapshot.is_runtime_active())
            .count()
    }

    pub fn canonical_stalled_count(&self) -> usize {
        self.runs
            .iter()
            .filter(|snapshot| snapshot.is_runtime_stalled())
            .count()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RunKind {
    Active,
    Recent,
    Completed,
    Failed,
    Stalled,
    Paused,
    Unknown,
}

impl RunKind {
    pub fn label(self) -> &'static str {
        match self {
            RunKind::Active => "active",
            RunKind::Recent => "recent",
            RunKind::Completed => "completed",
            RunKind::Failed => "failed",
            RunKind::Stalled => "stalled",
            RunKind::Paused => "paused",
            RunKind::Unknown => "unknown",
        }
    }

    pub fn sort_rank(self) -> u8 {
        match self {
            RunKind::Active => 0,
            RunKind::Stalled => 1,
            RunKind::Failed => 2,
            RunKind::Paused => 3,
            RunKind::Recent => 4,
            RunKind::Completed => 5,
            RunKind::Unknown => 6,
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct RunSnapshot {
    #[serde(alias = "runId")]
    pub run_id: String,
    #[serde(default, alias = "session_id")]
    pub session_id: Option<String>,
    #[serde(default)]
    pub agent: Option<String>,
    #[serde(default)]
    pub skill: Option<String>,
    #[serde(default)]
    pub mode: Option<String>,
    #[serde(default)]
    pub state: Option<String>,
    #[serde(default, alias = "status")]
    pub status: Option<String>,
    #[serde(default, alias = "startedAt")]
    pub started_at: Option<String>,
    #[serde(default, alias = "updatedAt")]
    pub updated_at: Option<String>,
    #[serde(default, alias = "lastHeartbeat")]
    pub last_heartbeat: Option<String>,
    #[serde(default)]
    pub root: Option<String>,
    #[serde(default, alias = "operatorSession")]
    pub operator_session: Option<String>,
    #[serde(default, alias = "latestReport")]
    pub latest_report: Option<String>,
    #[serde(default, alias = "latestTranscript")]
    pub latest_transcript: Option<String>,
    #[serde(default, alias = "lastError")]
    pub last_error: Option<String>,
    #[serde(flatten)]
    pub extra: HashMap<String, Value>,
}

impl RunSnapshot {
    pub fn display_state(&self) -> String {
        self.state
            .as_deref()
            .or(self.status.as_deref())
            .unwrap_or("unknown")
            .to_string()
    }

    fn is_runtime_inflight(&self) -> bool {
        let state = self.display_state().to_lowercase();
        let terminal = is_final_state(&state)
            || self
                .extra
                .get("liveness")
                .and_then(Value::as_str)
                .is_some_and(|value| value == "terminal")
            || self
                .extra
                .get("exit_code")
                .is_some_and(|value| !value.is_null());
        if terminal {
            return false;
        }
        if let Some(health) = self.extra.get("health").and_then(Value::as_str) {
            return matches!(health, "active" | "stalled");
        }
        is_active_state(&state)
    }

    fn is_runtime_active(&self) -> bool {
        self.is_runtime_inflight()
            && self
                .extra
                .get("health")
                .and_then(Value::as_str)
                .is_none_or(|health| health == "active")
    }

    fn is_runtime_stalled(&self) -> bool {
        self.is_runtime_inflight()
            && self
                .extra
                .get("health")
                .and_then(Value::as_str)
                .is_some_and(|health| health == "stalled")
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct RunEvent {
    #[serde(alias = "timestamp")]
    pub ts: String,
    #[serde(alias = "runId")]
    pub run_id: Option<String>,
    pub kind: String,
    #[serde(default)]
    pub message: Option<String>,
    #[serde(default)]
    pub payload: Option<Value>,
}

#[derive(Debug, Clone)]
pub struct RenderedRun {
    pub snapshot: RunSnapshot,
    pub kind: RunKind,
    pub age_label: String,
    pub recent_events: Vec<RunEvent>,
}

impl RenderedRun {
    pub fn workspace_label(&self) -> String {
        workspace_label(self.snapshot.root.as_deref())
    }

    pub fn operator_title(&self) -> String {
        format!(
            "{} · {} · {}",
            display_token(self.snapshot.skill.as_deref()),
            display_token(self.snapshot.agent.as_deref()),
            self.workspace_label()
        )
    }

    pub fn operator_identity(&self) -> String {
        self.snapshot.run_id.clone()
    }
}

pub fn workspace_label(root: Option<&str>) -> String {
    root.and_then(|value| {
        Path::new(value)
            .file_name()
            .and_then(|name| name.to_str())
            .filter(|name| !name.is_empty())
            .map(ToOwned::to_owned)
    })
    .unwrap_or_else(|| "—".to_string())
}

fn display_token(value: Option<&str>) -> String {
    match value.map(str::trim) {
        Some(value) if !value.is_empty() && value != "unknown" && value != "None" => {
            value.to_string()
        }
        _ => "—".to_string(),
    }
}

pub fn workspace_matches(snapshot: &RunSnapshot, workspace: &Path) -> bool {
    let Some(root) = snapshot
        .root
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    else {
        return true;
    };
    let root = Path::new(root);
    root == workspace
        || root.starts_with(workspace)
        || workspace.starts_with(root)
        || root.file_name() == workspace.file_name()
}

pub fn is_actionable_kind(kind: RunKind, snapshot: &RunSnapshot, now: DateTime<Utc>) -> bool {
    match kind {
        RunKind::Active | RunKind::Paused | RunKind::Recent => true,
        RunKind::Stalled => !stalled_is_archive(snapshot, now),
        RunKind::Completed | RunKind::Failed | RunKind::Unknown => false,
    }
}

fn stalled_is_archive(snapshot: &RunSnapshot, now: DateTime<Utc>) -> bool {
    let timestamp = snapshot
        .last_heartbeat
        .as_deref()
        .and_then(parse_timestamp)
        .or_else(|| snapshot.updated_at.as_deref().and_then(parse_timestamp))
        .or_else(|| snapshot.started_at.as_deref().and_then(parse_timestamp));
    match timestamp {
        Some(ts) => now.signed_duration_since(ts).num_hours() >= 2,
        None => false,
    }
}

pub fn render_runs(state: &ControlPlaneState) -> Vec<RenderedRun> {
    let now = Utc::now();
    let mut runs: Vec<RenderedRun> = state
        .runs
        .iter()
        .cloned()
        .map(|snapshot| {
            let kind = classify_run(&snapshot, now);
            let recent_events = recent_events_for(&state.events, &snapshot.run_id);
            let age_label = age_label(&snapshot, now);
            RenderedRun {
                snapshot,
                kind,
                age_label,
                recent_events,
            }
        })
        .collect();

    runs.sort_by(compare_runs);
    runs
}

pub fn classify_run(snapshot: &RunSnapshot, now: DateTime<Utc>) -> RunKind {
    let state = snapshot.display_state().to_lowercase();
    let canonical_health = snapshot.extra.get("health").and_then(Value::as_str);
    let heartbeat = snapshot
        .last_heartbeat
        .as_deref()
        .and_then(parse_timestamp)
        .or_else(|| snapshot.updated_at.as_deref().and_then(parse_timestamp));
    // Recency for completed success uses completed_at when present so a
    // watchdog restamp of updated_at cannot keep a day-old finish "fresh".
    let success_ts = completed_at_timestamp(snapshot).or(heartbeat);

    // Success evidence beats a stale last_error left by the heartbeat
    // watchdog. Order: success > last_error/fail > stalled > active.
    if has_success_evidence(snapshot) {
        return if is_recent(success_ts, now) {
            RunKind::Recent
        } else {
            RunKind::Completed
        };
    }
    if snapshot.last_error.is_some() || state.contains("fail") || state.contains("error") {
        return RunKind::Failed;
    }
    if canonical_health == Some("stalled") || state.contains("stalled") {
        return RunKind::Stalled;
    }
    if state.contains("pause") {
        return RunKind::Paused;
    }
    if state.contains("done")
        || state.contains("complete")
        || state.contains("succeed")
        || state.contains("converged")
        || state.contains("stopped")
        || state.contains("gc")
    {
        return if is_recent(success_ts, now) {
            RunKind::Recent
        } else {
            RunKind::Completed
        };
    }
    if is_active_like(&state) {
        if is_stale(heartbeat, now) {
            return RunKind::Stalled;
        }
        return RunKind::Active;
    }
    if canonical_health == Some("active") && !is_stale(heartbeat, now) {
        return RunKind::Active;
    }
    RunKind::Unknown
}

/// Disk/process proof that a run finished successfully, even if the watchdog
/// stamped `last_error` / `recovery_required` during the heartbeat gap.
fn has_success_evidence(snapshot: &RunSnapshot) -> bool {
    if snapshot.extra.get("artifact_ok").and_then(Value::as_bool) == Some(false) {
        return false;
    }
    if let Some(Value::Array(errors)) = snapshot.extra.get("artifact_errors")
        && !errors.is_empty()
    {
        return false;
    }

    let exit_code = coerce_exit_code(snapshot);
    if matches!(exit_code, Some(code) if code != 0) {
        return false;
    }
    // exit 0 is hard success evidence — beats stale last_error.
    if exit_code == Some(0) {
        return true;
    }

    let state = snapshot.display_state().to_lowercase();
    if matches!(
        state.as_str(),
        "report_validated" | "completed" | "closed" | "converged"
    ) {
        return true;
    }
    if completed_at_timestamp(snapshot).is_some() {
        return true;
    }
    snapshot.extra.get("liveness").and_then(Value::as_str) == Some("terminal")
}

fn coerce_exit_code(snapshot: &RunSnapshot) -> Option<i64> {
    let value = snapshot.extra.get("exit_code")?;
    if value.is_null() {
        return None;
    }
    value
        .as_i64()
        .or_else(|| value.as_u64().map(|code| code as i64))
        .or_else(|| value.as_str().and_then(|raw| raw.trim().parse().ok()))
}

fn completed_at_timestamp(snapshot: &RunSnapshot) -> Option<DateTime<Utc>> {
    snapshot
        .extra
        .get("completed_at")
        .and_then(Value::as_str)
        .and_then(parse_timestamp)
}

fn compare_runs(left: &RenderedRun, right: &RenderedRun) -> Ordering {
    left.kind
        .sort_rank()
        .cmp(&right.kind.sort_rank())
        .then_with(|| compare_timestamp(&right.snapshot.updated_at, &left.snapshot.updated_at))
        .then_with(|| compare_timestamp(&right.snapshot.started_at, &left.snapshot.started_at))
        .then_with(|| {
            compare_timestamp(
                &right.snapshot.last_heartbeat,
                &left.snapshot.last_heartbeat,
            )
        })
        .then_with(|| left.snapshot.run_id.cmp(&right.snapshot.run_id))
}

fn compare_timestamp(left: &Option<String>, right: &Option<String>) -> Ordering {
    let left = left.as_deref().and_then(parse_timestamp);
    let right = right.as_deref().and_then(parse_timestamp);
    match (left, right) {
        (Some(left), Some(right)) => right.cmp(&left),
        (Some(_), None) => Ordering::Less,
        (None, Some(_)) => Ordering::Greater,
        (None, None) => Ordering::Equal,
    }
}

fn recent_events_for(events: &[RunEvent], run_id: &str) -> Vec<RunEvent> {
    let mut recent: Vec<RunEvent> = events
        .iter()
        .filter(|event| event.run_id.as_deref() == Some(run_id))
        .cloned()
        .collect();
    recent.sort_by(|left, right| right.ts.cmp(&left.ts));
    recent.truncate(8);
    recent
}

fn age_label(snapshot: &RunSnapshot, now: DateTime<Utc>) -> String {
    let timestamp = snapshot
        .last_heartbeat
        .as_deref()
        .and_then(parse_timestamp)
        .or_else(|| snapshot.updated_at.as_deref().and_then(parse_timestamp))
        .or_else(|| snapshot.started_at.as_deref().and_then(parse_timestamp));
    timestamp
        .map(|ts| relative_age(ts, now))
        .unwrap_or_else(|| "age unknown".to_string())
}

#[derive(Debug, Clone)]
struct SafeControlPlaneRoot {
    path: PathBuf,
}

impl SafeControlPlaneRoot {
    fn new(root: &Path) -> io::Result<Option<Self>> {
        if !root.exists() {
            return Ok(None);
        }
        let canonical = fs::canonicalize(root)?;
        if canonical.is_dir() {
            Ok(Some(Self { path: canonical }))
        } else {
            Ok(None)
        }
    }

    fn as_path(&self) -> &Path {
        &self.path
    }

    fn runs_dir(&self) -> PathBuf {
        self.path.join("runs")
    }

    fn archived_dir(&self) -> PathBuf {
        self.runs_dir().join(".archived")
    }

    fn run_snapshot_files(&self) -> io::Result<Vec<PathBuf>> {
        let runs_dir = self.runs_dir();
        let mut files = Vec::new();
        if !runs_dir.exists() {
            return Ok(files);
        }
        for entry in fs::read_dir(runs_dir)? {
            let entry = entry?;
            let path = entry.path();
            if !is_json_file(&path) {
                continue;
            }
            let Some(path) = self.safe_file(&path)? else {
                continue;
            };
            files.push(path);
        }
        Ok(files)
    }

    fn load_runs(&self) -> io::Result<Vec<RunSnapshot>> {
        let mut snapshots = HashMap::<String, RunSnapshot>::new();
        for path in self.run_snapshot_files()? {
            let Ok(text) = fs::read_to_string(&path) else {
                continue;
            };
            let parsed: Result<RunSnapshot, _> = serde_json::from_str(&text);
            if let Ok(snapshot) = parsed {
                snapshots.insert(snapshot.run_id.clone(), snapshot);
            }
        }
        Ok(snapshots.into_values().collect())
    }

    fn archived_marker_files(&self) -> io::Result<Vec<PathBuf>> {
        let archived_dir = self.archived_dir();
        let mut files = Vec::new();
        if !archived_dir.exists() {
            return Ok(files);
        }
        for entry in fs::read_dir(archived_dir)? {
            let entry = entry?;
            let path = entry.path();
            if !is_json_file(&path) {
                continue;
            }
            let Some(path) = self.safe_file(&path)? else {
                continue;
            };
            files.push(path);
        }
        Ok(files)
    }

    fn load_archived_run_ids(&self) -> io::Result<HashSet<String>> {
        let mut archived = HashSet::new();
        for path in self.archived_marker_files()? {
            if let Some(id) = archived_id_from_marker(&path) {
                archived.insert(id);
            }
        }
        Ok(archived)
    }

    fn safe_file(&self, path: &Path) -> io::Result<Option<PathBuf>> {
        let meta = match fs::symlink_metadata(path) {
            Ok(meta) => meta,
            Err(_) => return Ok(None),
        };
        if meta.file_type().is_symlink() {
            return Ok(None);
        }
        let Some(parent) = path.parent() else {
            return Ok(None);
        };
        if fs::symlink_metadata(parent)?.file_type().is_symlink() {
            return Ok(None);
        }
        let canonical = fs::canonicalize(path)?;
        if canonical.starts_with(&self.path) {
            Ok(Some(canonical))
        } else {
            Ok(None)
        }
    }
}

fn canonical_run_snapshot(run: CanonicalRunStatus) -> RunSnapshot {
    let mut extra = HashMap::new();
    extra.insert("health".to_string(), Value::String(run.health.clone()));
    extra.insert("source".to_string(), Value::String(run.source.clone()));
    if !run.liveness.is_empty() {
        extra.insert("liveness".to_string(), Value::String(run.liveness.clone()));
    }
    if let Some(exit_code) = run.exit_code {
        extra.insert("exit_code".to_string(), Value::from(exit_code));
    }
    if let Some(current_loop) = run.current_loop {
        extra.insert("current_loop".to_string(), Value::from(current_loop));
    }
    if let Some(total_loops) = run.total_loops {
        extra.insert("total_loops".to_string(), Value::from(total_loops));
    }

    RunSnapshot {
        run_id: run.run_id,
        session_id: nonempty(run.session_id),
        agent: nonempty(run.agent),
        skill: nonempty(run.skill),
        mode: nonempty(run.mode),
        state: nonempty(run.state),
        status: None,
        started_at: nonempty(run.started_at),
        updated_at: nonempty(run.updated_at),
        last_heartbeat: None,
        root: nonempty(run.root),
        operator_session: nonempty(run.operator_session),
        latest_report: nonempty(run.latest_report),
        latest_transcript: nonempty(run.latest_transcript),
        last_error: nonempty(run.last_error),
        extra,
    }
}

fn canonical_run_event(event: CanonicalEvent) -> RunEvent {
    RunEvent {
        ts: event.ts,
        run_id: nonempty(event.run_id),
        kind: event.kind,
        message: nonempty(event.message),
        payload: if event.payload.is_empty() {
            None
        } else {
            Some(Value::Object(event.payload.into_iter().collect()))
        },
    }
}

fn nonempty(value: String) -> Option<String> {
    if value.trim().is_empty() {
        None
    } else {
        Some(value)
    }
}

fn is_json_file(path: &Path) -> bool {
    path.extension().and_then(|ext| ext.to_str()) == Some("json")
}

fn archived_id_from_marker(path: &Path) -> Option<String> {
    let text = fs::read_to_string(path).ok()?;
    if let Ok(value) = serde_json::from_str::<Value>(&text)
        && let Some(run_id) = value.get("run_id").and_then(Value::as_str)
    {
        return Some(run_id.to_string());
    }
    path.file_stem()
        .and_then(|stem| stem.to_str())
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
}

fn parse_timestamp(raw: &str) -> Option<DateTime<Utc>> {
    if let Ok(parsed) = DateTime::parse_from_rfc3339(raw) {
        return Some(parsed.with_timezone(&Utc));
    }
    if let Ok(seconds) = raw.parse::<i64>() {
        return Utc.timestamp_opt(seconds, 0).single();
    }
    None
}

fn relative_age(timestamp: DateTime<Utc>, now: DateTime<Utc>) -> String {
    let delta = now.signed_duration_since(timestamp);
    let minutes = delta.num_minutes().max(0);
    let hours = delta.num_hours().max(0);
    if hours >= 24 {
        let days = delta.num_days().max(0);
        return format!("{days}d ago");
    }
    if hours > 0 {
        return format!("{hours}h ago");
    }
    format!("{minutes}m ago")
}

fn is_recent(timestamp: Option<DateTime<Utc>>, now: DateTime<Utc>) -> bool {
    timestamp
        .map(|value| now.signed_duration_since(value).num_hours() < 24)
        .unwrap_or(false)
}

fn is_stale(timestamp: Option<DateTime<Utc>>, now: DateTime<Utc>) -> bool {
    timestamp
        .map(|value| now.signed_duration_since(value).num_minutes() > 15)
        .unwrap_or(false)
}

fn is_active_like(state: &str) -> bool {
    matches!(
        state,
        "active"
            | "launching"
            | "launch"
            | "running"
            | "run"
            | "watching"
            | "queued"
            | "pending"
            | "in-progress"
            | "in_progress"
            | "progress"
            | "loop"
    ) || state.starts_with("running")
        || state.starts_with("launch")
}

#[cfg(test)]
mod tests {
    use super::ControlPlaneState;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn runtime_only_run_is_active_until_retained_terminal_snapshot_supersedes_it() {
        let dir = tempdir().expect("tempdir");
        let root = dir.path().join("control_plane");
        let run_id = "pola-runtime-only-L1";
        let runtime_dir = root.join("runtime_runs").join(run_id);
        fs::create_dir_all(&runtime_dir).expect("runtime dir");
        fs::write(runtime_dir.join("transcript.log"), "working\n").expect("transcript");

        let state = ControlPlaneState::load(&root).expect("runtime state");
        assert_eq!(state.canonical_active_count(), 1);
        let projected = state
            .runs
            .iter()
            .filter(|run| run.run_id == run_id)
            .collect::<Vec<_>>();
        assert_eq!(projected.len(), 1);
        assert_eq!(projected[0].state.as_deref(), Some("launching"));

        fs::create_dir_all(root.join("runs")).expect("runs dir");
        fs::write(
            root.join("runs").join(format!("{run_id}.json")),
            format!(
                r#"{{
                    "run_id":"{run_id}",
                    "state":"completed",
                    "agent":"codex",
                    "skill":"polarize",
                    "mode":"headless",
                    "root":"/tmp/repo",
                    "operator_session":"",
                    "latest_report":"",
                    "latest_transcript":"",
                    "last_error":"",
                    "updated_at":"2026-07-23T08:00:00Z",
                    "started_at":"2026-07-23T07:59:00Z",
                    "health":"final",
                    "source":"agent-meta",
                    "lock_present":false,
                    "exit_code":0,
                    "liveness":"terminal",
                    "launcher_pid":null,
                    "completed_at":"2026-07-23T08:00:00Z",
                    "session_id":"",
                    "current_loop":null,
                    "total_loops":null
                }}"#
            ),
        )
        .expect("terminal snapshot");

        let state = ControlPlaneState::load(&root).expect("terminal state");
        let projected = state
            .runs
            .iter()
            .filter(|run| run.run_id == run_id)
            .collect::<Vec<_>>();
        assert_eq!(projected.len(), 1, "one run id, never a state twin");
        assert_eq!(projected[0].state.as_deref(), Some("completed"));
        assert_eq!(state.retained_runs.len(), 1);
        assert_eq!(state.canonical_active_count(), 0);
    }

    #[test]
    fn canonical_counts_keep_active_and_stalled_as_separate_labels() {
        let dir = tempdir().expect("tempdir");
        let root = dir.path().join("control_plane");
        fs::create_dir_all(&root).expect("control plane");
        let now = chrono::Utc::now();
        let events = [
            serde_json::json!({
                "ts": now.to_rfc3339(),
                "run_id": "live-worker",
                "kind": "lifecycle:active",
                "message": "heartbeat",
                "payload": {
                    "state": "active",
                    "root": "/srv/checkout/vibecrafted",
                    "launcher_pid": std::process::id()
                }
            }),
            serde_json::json!({
                "ts": now.to_rfc3339(),
                "run_id": "definitely-missing",
                "kind": "state",
                "message": "stale event only",
                "payload": {
                    "state": "running",
                    "health": "active",
                    "heartbeat_at": (now - chrono::Duration::hours(2)).to_rfc3339(),
                    "root": "/srv/checkout/vibecrafted"
                }
            }),
            serde_json::json!({
                "ts": now.to_rfc3339(),
                "run_id": "pytest-fixture-run",
                "kind": "lifecycle:active",
                "message": "fixture leak",
                "payload": {
                    "state": "active",
                    "root": "/private/tmp/pytest-of-operator/pytest-1/test_board0"
                }
            }),
        ];
        let encoded = events
            .iter()
            .map(serde_json::Value::to_string)
            .collect::<Vec<_>>()
            .join("\n");
        fs::write(root.join("events.jsonl"), format!("{encoded}\n")).expect("event stream");

        let state = ControlPlaneState::load(&root).expect("runtime state");

        assert_eq!(state.canonical_active_count(), 1);
        assert_eq!(state.canonical_stalled_count(), 1);
        let projected = super::render_runs(&state);
        assert!(projected.iter().any(|run| {
            run.snapshot.run_id == "live-worker" && run.kind == super::RunKind::Active
        }));
        assert!(projected.iter().any(|run| {
            run.snapshot.run_id == "definitely-missing" && run.kind == super::RunKind::Stalled
        }));
        assert!(
            projected
                .iter()
                .all(|run| run.snapshot.run_id != "pytest-fixture-run")
        );
    }

    #[test]
    fn classify_run_does_not_treat_unknown_or_day_old_stalls_as_active() {
        let now = chrono::Utc::now();
        let mut blank = super::RunSnapshot {
            run_id: "blank".into(),
            session_id: None,
            agent: None,
            skill: None,
            mode: None,
            state: None,
            status: None,
            started_at: None,
            updated_at: None,
            last_heartbeat: None,
            root: None,
            operator_session: None,
            latest_report: None,
            latest_transcript: None,
            last_error: None,
            extra: Default::default(),
        };
        assert_eq!(super::classify_run(&blank, now), super::RunKind::Unknown);

        blank.state = Some("unknown".into());
        blank.updated_at = Some((now - chrono::Duration::hours(16)).to_rfc3339());
        assert_eq!(super::classify_run(&blank, now), super::RunKind::Unknown);
        assert!(!super::is_actionable_kind(
            super::classify_run(&blank, now),
            &blank,
            now
        ));

        blank.state = Some("running".into());
        blank.updated_at = Some((now - chrono::Duration::hours(16)).to_rfc3339());
        let kind = super::classify_run(&blank, now);
        assert_eq!(kind, super::RunKind::Stalled);
        assert!(!super::is_actionable_kind(kind, &blank, now));

        blank.updated_at = Some((now - chrono::Duration::minutes(2)).to_rfc3339());
        blank.last_heartbeat = blank.updated_at.clone();
        assert_eq!(super::classify_run(&blank, now), super::RunKind::Active);
    }
}
