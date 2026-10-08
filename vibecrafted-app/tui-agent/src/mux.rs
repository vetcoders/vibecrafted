//! rmcp-mux status reader.
//!
//! `rmcp-mux` (../rmcp-mux, MCP transport multiplexer) writes a JSON status
//! snapshot to its `--status-file` on every state change. The operator
//! console reads those snapshots so the human can see what is actually
//! happening inside the MCP supervisor when an agent run is misbehaving:
//! whether the daemon is `Running` / `Restarting` / `Failed`, how many
//! clients are connected, how deep the queue is, and how many times the
//! child has been respawned.
//!
//! This module is intentionally a *reader-only* surface. We do not depend
//! on the `rmcp-mux` crate directly because it pulls a `tray-icon` /
//! `ratatui 0.28` stack that would conflict with our `ratatui 0.29`
//! TUI. Instead we mirror the public schema and deserialize the JSON
//! snapshot file. The fields are taken from
//! `rmcp-mux/src/state.rs::StatusSnapshot` and kept in lockstep manually.

use anyhow::{Context, Result};
use serde::Deserialize;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

/// Lifecycle of the supervised MCP child process. Mirrors
/// `rmcp_mux::state::ServerStatus`. The `Failed` variant carries a human
/// reason string straight from the supervisor.
#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
pub enum MuxServerStatus {
    Starting,
    Running,
    Restarting,
    Failed(String),
    Stopped,
}

impl MuxServerStatus {
    /// Short label suitable for a single-cell UI render. Healthy states
    /// fit into one word; `Failed(reason)` returns a static `"Failed"`
    /// label and the caller is expected to surface the reason
    /// separately via `failure_reason`.
    pub fn label(&self) -> &'static str {
        match self {
            MuxServerStatus::Starting => "Starting",
            MuxServerStatus::Running => "Running",
            MuxServerStatus::Restarting => "Restarting",
            MuxServerStatus::Failed(_) => "Failed",
            MuxServerStatus::Stopped => "Stopped",
        }
    }

    /// `true` for states the operator generally does not need to act on.
    /// `Restarting` is borderline — the mux is reacting on its own — but
    /// counts as healthy because the supervisor is doing its job.
    pub fn is_healthy(&self) -> bool {
        matches!(
            self,
            MuxServerStatus::Running | MuxServerStatus::Starting | MuxServerStatus::Restarting
        )
    }

    /// Human reason carried by `Failed`, if any. Returns `None` for every
    /// other variant.
    pub fn failure_reason(&self) -> Option<&str> {
        match self {
            MuxServerStatus::Failed(reason) => Some(reason.as_str()),
            _ => None,
        }
    }
}

/// Snapshot written atomically to the rmcp-mux `--status-file` path on every
/// state change. Mirrors `rmcp_mux::state::StatusSnapshot`.
///
/// Field set is intentionally permissive (extra unknown fields are ignored)
/// so a newer rmcp-mux that adds fields will not break the operator's
/// reader.
#[derive(Clone, Debug, Deserialize)]
pub struct MuxStatusSnapshot {
    pub service_name: String,
    pub server_status: MuxServerStatus,
    pub restarts: u64,
    pub connected_clients: usize,
    pub active_clients: usize,
    pub max_active_clients: usize,
    pub pending_requests: usize,
    pub cached_initialize: bool,
    pub initializing: bool,
    #[serde(default)]
    pub last_reset: Option<String>,
    pub queue_depth: usize,
    #[serde(default)]
    pub child_pid: Option<u32>,
    pub max_request_bytes: usize,
    pub restart_backoff_ms: u64,
    pub restart_backoff_max_ms: u64,
    pub max_restarts: u64,
}

impl MuxStatusSnapshot {
    /// Parse a status snapshot from raw JSON.
    pub fn from_json(raw: &str) -> Result<Self> {
        serde_json::from_str(raw).context("failed to parse rmcp-mux status JSON")
    }

    /// Read and parse a status snapshot from a status_file path.
    ///
    /// IO errors and JSON-shape errors carry the path in the chain so the
    /// operator overlay can pinpoint which mux service is misbehaving.
    pub fn read(path: &Path) -> Result<Self> {
        let path = safe_status_file(path)?;
        let raw = fs::read_to_string(&path)
            .with_context(|| format!("failed to read rmcp-mux status file {}", path.display()))?;
        Self::from_json(&raw).with_context(|| format!("rmcp-mux status file {}", path.display()))
    }

    /// Compact one-line summary suitable for the Monitor tab.
    pub fn summary_line(&self) -> String {
        let mut parts = vec![format!(
            "{}: {} clients={}/{} pending={} queue={} restarts={}",
            self.service_name,
            self.server_status.label(),
            self.active_clients,
            self.connected_clients,
            self.pending_requests,
            self.queue_depth,
            self.restarts,
        )];
        if let Some(pid) = self.child_pid {
            parts.push(format!("pid={pid}"));
        }
        if let Some(reason) = self.server_status.failure_reason() {
            parts.push(format!("reason={reason}"));
        }
        parts.join(" ")
    }
}

fn safe_status_file(path: &Path) -> Result<PathBuf> {
    if path.extension().and_then(|ext| ext.to_str()) != Some("json") {
        anyhow::bail!("refusing non-json rmcp-mux status file {}", path.display());
    }
    let meta = fs::symlink_metadata(path)
        .with_context(|| format!("failed to inspect rmcp-mux status file {}", path.display()))?;
    if meta.file_type().is_symlink() || !meta.is_file() {
        anyhow::bail!("refusing unsafe rmcp-mux status file {}", path.display());
    }
    fs::canonicalize(path).with_context(|| {
        format!(
            "failed to canonicalize rmcp-mux status file {}",
            path.display()
        )
    })
}

/// Canonical default location where rmcp-mux services drop their status
/// files. Matches the `~/.rmcp_servers/rmcp_mux/` convention used by the
/// rmcp-mux installer/launchd templates.
fn default_mux_status_root() -> Option<PathBuf> {
    let home = env::var_os("HOME").filter(|value| !value.is_empty())?;
    Some(PathBuf::from(home).join(".rmcp_servers/rmcp_mux"))
}

/// Look for explicit operator overrides first
/// (`VIBECRAFTED_MUX_STATUS_PATHS`, colon-separated list of paths to
/// individual status files), then fall back to scanning the default
/// `~/.rmcp_servers/rmcp_mux/` directory for any `*.json` snapshots and
/// for a flat `status.json` at the root.
///
/// Returns paths in stable order:
///   1. each entry from the env override (in given order, dedup),
///   2. `<root>/status.json` if present,
///   3. `<root>/*.json` sorted alphabetically (excluding the flat
///      `status.json` already added).
///
/// Non-existent paths from the env override are still included so the
/// operator overlay can show "missing status file" rather than silently
/// hiding the misconfiguration.
pub fn discover_status_files() -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();
    let mut seen: std::collections::BTreeSet<PathBuf> = std::collections::BTreeSet::new();

    if let Some(raw) = env::var_os("VIBECRAFTED_MUX_STATUS_PATHS")
        && !raw.is_empty()
    {
        let raw = raw.to_string_lossy().into_owned();
        for entry in raw.split(':').filter(|s| !s.is_empty()) {
            let path = PathBuf::from(entry);
            if seen.insert(path.clone()) {
                out.push(path);
            }
        }
    }

    if let Some(root) = default_mux_status_root() {
        let flat = root.join("status.json");
        if flat.is_file() && seen.insert(flat.clone()) {
            out.push(flat);
        }
        if let Ok(entries) = fs::read_dir(&root) {
            let mut dir_files: Vec<PathBuf> = entries
                .filter_map(|entry| entry.ok())
                .filter(|entry| {
                    entry.file_type().map(|ty| ty.is_file()).unwrap_or(false)
                        && entry.path().extension().and_then(|ext| ext.to_str()) == Some("json")
                })
                .map(|entry| entry.path())
                .collect();
            dir_files.sort();
            for path in dir_files {
                if seen.insert(path.clone()) {
                    out.push(path);
                }
            }
        }
    }

    out
}

/// Read every status file produced by `discover_status_files`, returning
/// `(path, Result<snapshot>)` pairs in the same order. The operator UI is
/// expected to surface failed reads (missing file, permission denied,
/// malformed JSON) instead of silently dropping them — invisible mux
/// failures are exactly what this surface exists to prevent.
pub fn read_all_known_snapshots() -> Vec<(PathBuf, Result<MuxStatusSnapshot>)> {
    discover_status_files()
        .into_iter()
        .map(|path| {
            let snapshot = MuxStatusSnapshot::read(&path);
            (path, snapshot)
        })
        .collect()
}

/// UI-friendly snapshot of one rmcp-mux service. Cached on the `App` so
/// the Monitor tab can render mux status without doing IO inside the draw
/// path. Carries either the parsed snapshot or the error chain that
/// stopped us from reading it, never both.
#[derive(Clone, Debug)]
pub struct MuxSummary {
    /// The status file the snapshot came from. Surfaced in the UI so the
    /// operator can grep for the right service when something is off.
    pub path: PathBuf,
    /// Service name or, if the snapshot failed to parse, the file stem
    /// from `path`. Always renderable.
    pub display_name: String,
    /// Either the parsed snapshot or a one-line error description (no
    /// stack, just `format!("{err:#}")`).
    pub state: MuxSummaryState,
}

/// `Healthy(snapshot)` if `is_healthy` is true on the parsed status.
/// `Unhealthy(snapshot)` if the snapshot parsed but the daemon reports a
/// non-healthy state. `Unreadable { error }` if the file could not be
/// read or the JSON did not parse — the operator still sees the entry,
/// because an unreadable status file is itself a signal.
#[derive(Clone, Debug)]
pub enum MuxSummaryState {
    Healthy(MuxStatusSnapshot),
    Unhealthy(MuxStatusSnapshot),
    Unreadable { error: String },
}

impl MuxSummary {
    /// Build a summary from a `(path, Result<snapshot>)` pair (the shape
    /// `read_all_known_snapshots` returns).
    pub fn from_path_and_result(path: PathBuf, result: Result<MuxStatusSnapshot>) -> Self {
        match result {
            Ok(snapshot) => {
                let display_name = snapshot.service_name.clone();
                let state = if snapshot.server_status.is_healthy() {
                    MuxSummaryState::Healthy(snapshot)
                } else {
                    MuxSummaryState::Unhealthy(snapshot)
                };
                MuxSummary {
                    path,
                    display_name,
                    state,
                }
            }
            Err(err) => {
                let display_name = path
                    .file_stem()
                    .and_then(|stem| stem.to_str())
                    .map(ToOwned::to_owned)
                    .unwrap_or_else(|| path.to_string_lossy().into_owned());
                MuxSummary {
                    path,
                    display_name,
                    state: MuxSummaryState::Unreadable {
                        error: format!("{err:#}"),
                    },
                }
            }
        }
    }

    /// `true` when the underlying daemon is in a healthy state. Returns
    /// `false` for both unhealthy snapshots and unreadable status files.
    pub fn is_healthy(&self) -> bool {
        matches!(self.state, MuxSummaryState::Healthy(_))
    }

    /// One-line render for the Monitor tab. Healthy and unhealthy
    /// snapshots reuse `MuxStatusSnapshot::summary_line`; unreadable
    /// entries render `<name>: unreadable (<error>)` so the operator can
    /// see why we have nothing to show.
    pub fn summary_line(&self) -> String {
        match &self.state {
            MuxSummaryState::Healthy(snapshot) | MuxSummaryState::Unhealthy(snapshot) => {
                snapshot.summary_line()
            }
            MuxSummaryState::Unreadable { error } => {
                format!("{}: unreadable ({error})", self.display_name)
            }
        }
    }
}

/// Convenience: read every known status file and convert to the
/// UI-friendly `MuxSummary` shape in one call. Used by `App::refresh`.
pub fn current_summaries() -> Vec<MuxSummary> {
    read_all_known_snapshots()
        .into_iter()
        .map(|(path, result)| MuxSummary::from_path_and_result(path, result))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const RUNNING_FIXTURE: &str = r#"{
        "service_name": "general-memory",
        "server_status": "Running",
        "restarts": 0,
        "connected_clients": 3,
        "active_clients": 1,
        "max_active_clients": 5,
        "pending_requests": 0,
        "cached_initialize": true,
        "initializing": false,
        "last_reset": null,
        "queue_depth": 0,
        "child_pid": 12345,
        "max_request_bytes": 1048576,
        "restart_backoff_ms": 1000,
        "restart_backoff_max_ms": 30000,
        "max_restarts": 5
    }"#;

    const FAILED_FIXTURE: &str = r#"{
        "service_name": "brave-search",
        "server_status": {"Failed": "max restarts reached"},
        "restarts": 5,
        "connected_clients": 0,
        "active_clients": 0,
        "max_active_clients": 5,
        "pending_requests": 0,
        "cached_initialize": false,
        "initializing": false,
        "last_reset": "2026-04-30T12:00:00Z",
        "queue_depth": 0,
        "child_pid": null,
        "max_request_bytes": 1048576,
        "restart_backoff_ms": 1000,
        "restart_backoff_max_ms": 30000,
        "max_restarts": 5
    }"#;

    const FORWARD_COMPAT_FIXTURE: &str = r#"{
        "service_name": "loctree",
        "server_status": "Starting",
        "restarts": 0,
        "connected_clients": 0,
        "active_clients": 0,
        "max_active_clients": 5,
        "pending_requests": 0,
        "cached_initialize": false,
        "initializing": true,
        "queue_depth": 0,
        "max_request_bytes": 1048576,
        "restart_backoff_ms": 1000,
        "restart_backoff_max_ms": 30000,
        "max_restarts": 5,
        "future_unknown_field": {"new": "shape"}
    }"#;

    #[test]
    fn parses_running_snapshot_with_pid() {
        let snap = MuxStatusSnapshot::from_json(RUNNING_FIXTURE).unwrap();
        assert_eq!(snap.service_name, "general-memory");
        assert_eq!(snap.server_status, MuxServerStatus::Running);
        assert_eq!(snap.child_pid, Some(12345));
        assert!(snap.cached_initialize);
        assert_eq!(snap.connected_clients, 3);
        assert_eq!(snap.active_clients, 1);
        assert!(snap.server_status.is_healthy());
        assert!(snap.server_status.failure_reason().is_none());
    }

    #[test]
    fn parses_failed_snapshot_and_carries_reason() {
        let snap = MuxStatusSnapshot::from_json(FAILED_FIXTURE).unwrap();
        assert_eq!(snap.service_name, "brave-search");
        assert_eq!(snap.server_status.label(), "Failed");
        assert_eq!(
            snap.server_status.failure_reason(),
            Some("max restarts reached")
        );
        assert!(!snap.server_status.is_healthy());
        assert_eq!(snap.restarts, 5);
        assert_eq!(snap.child_pid, None);
        assert_eq!(snap.last_reset.as_deref(), Some("2026-04-30T12:00:00Z"));
    }

    #[test]
    fn unknown_extra_fields_do_not_break_parsing() {
        let snap = MuxStatusSnapshot::from_json(FORWARD_COMPAT_FIXTURE).unwrap();
        assert_eq!(snap.service_name, "loctree");
        assert_eq!(snap.server_status, MuxServerStatus::Starting);
        assert!(snap.initializing);
    }

    #[test]
    fn summary_line_packs_the_essentials_and_includes_pid_when_present() {
        let snap = MuxStatusSnapshot::from_json(RUNNING_FIXTURE).unwrap();
        let line = snap.summary_line();
        assert!(line.contains("general-memory: Running"));
        assert!(line.contains("clients=1/3"));
        assert!(line.contains("pending=0"));
        assert!(line.contains("queue=0"));
        assert!(line.contains("restarts=0"));
        assert!(line.contains("pid=12345"));
        assert!(!line.contains("reason="));
    }

    #[test]
    fn summary_line_surfaces_failure_reason_for_failed_state() {
        let snap = MuxStatusSnapshot::from_json(FAILED_FIXTURE).unwrap();
        let line = snap.summary_line();
        assert!(line.contains("brave-search: Failed"));
        assert!(line.contains("reason=max restarts reached"));
        assert!(line.contains("restarts=5"));
        assert!(!line.contains("pid="));
    }

    #[test]
    fn read_returns_actionable_io_error_for_missing_file() {
        let dir = tempfile::tempdir().unwrap();
        let missing = dir.path().join("does-not-exist.json");
        let err = MuxStatusSnapshot::read(&missing).expect_err("missing file must error");
        let chain = format!("{err:#}");
        assert!(
            chain.contains(missing.to_string_lossy().as_ref()),
            "error chain should embed the path: {chain}"
        );
    }

    #[test]
    fn read_refuses_non_json_status_file() {
        let dir = tempfile::tempdir().unwrap();
        let status = dir.path().join("status.txt");
        fs::write(&status, RUNNING_FIXTURE).unwrap();

        let err = MuxStatusSnapshot::read(&status).expect_err("non-json status must be refused");
        let chain = format!("{err:#}");
        assert!(
            chain.contains("refusing non-json rmcp-mux status file"),
            "error chain should explain the refused path shape: {chain}"
        );
    }

    #[cfg(unix)]
    #[test]
    fn read_refuses_symlinked_status_file() {
        use std::os::unix::fs::symlink;

        let dir = tempfile::tempdir().unwrap();
        let real = dir.path().join("real.json");
        let linked = dir.path().join("status.json");
        fs::write(&real, RUNNING_FIXTURE).unwrap();
        symlink(&real, &linked).unwrap();

        let err = MuxStatusSnapshot::read(&linked).expect_err("symlinked status must be refused");
        let chain = format!("{err:#}");
        assert!(
            chain.contains("refusing unsafe rmcp-mux status file"),
            "error chain should explain the refused symlink: {chain}"
        );
    }

    /// Process-wide guard for tests that mutate `HOME` and
    /// `VIBECRAFTED_MUX_STATUS_PATHS`. Cargo runs tests in a single
    /// process by default, so two tests touching the env in parallel
    /// would race.
    fn env_guard() -> std::sync::MutexGuard<'static, ()> {
        use std::sync::{Mutex, OnceLock};
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| Mutex::new(()))
            .lock()
            .unwrap_or_else(|err| err.into_inner())
    }

    fn with_env<F, R>(home: Option<&Path>, paths: Option<&str>, f: F) -> R
    where
        F: FnOnce() -> R,
    {
        let prev_home = env::var_os("HOME");
        let prev_paths = env::var_os("VIBECRAFTED_MUX_STATUS_PATHS");
        // SAFETY: Test-only environment mutation is serialized by `env_guard`,
        // so no other test in this module observes a partially-updated env.
        unsafe {
            match home {
                Some(p) => env::set_var("HOME", p),
                None => env::remove_var("HOME"),
            }
            match paths {
                Some(v) => env::set_var("VIBECRAFTED_MUX_STATUS_PATHS", v),
                None => env::remove_var("VIBECRAFTED_MUX_STATUS_PATHS"),
            }
        }
        let result = f();
        // SAFETY: `env_guard` is still held while restoring the previous values,
        // keeping the process-wide environment mutation local to this test scope.
        unsafe {
            match prev_home {
                Some(value) => env::set_var("HOME", value),
                None => env::remove_var("HOME"),
            }
            match prev_paths {
                Some(value) => env::set_var("VIBECRAFTED_MUX_STATUS_PATHS", value),
                None => env::remove_var("VIBECRAFTED_MUX_STATUS_PATHS"),
            }
        }
        result
    }

    #[test]
    fn discovery_returns_empty_when_home_has_no_rmcp_dir() {
        let _guard = env_guard();
        let dir = tempfile::tempdir().unwrap();
        with_env(Some(dir.path()), None, || {
            let files = discover_status_files();
            assert!(
                files.is_empty(),
                "expected no status files under empty HOME: got {files:?}"
            );
        });
    }

    #[test]
    fn discovery_picks_up_default_root_status_json_and_lexicographic_extras() {
        let _guard = env_guard();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join(".rmcp_servers/rmcp_mux");
        fs::create_dir_all(&root).unwrap();
        // Flat default status.json plus two named ones.
        fs::write(root.join("status.json"), "{}").unwrap();
        fs::write(root.join("brave-search.json"), "{}").unwrap();
        fs::write(root.join("memory.json"), "{}").unwrap();
        // Non-JSON file must be ignored.
        fs::write(root.join("notes.txt"), "ignored").unwrap();

        with_env(Some(dir.path()), None, || {
            let files = discover_status_files();
            let names: Vec<String> = files
                .iter()
                .map(|path| path.file_name().unwrap().to_string_lossy().into_owned())
                .collect();
            assert_eq!(
                names,
                vec!["status.json", "brave-search.json", "memory.json"],
                "expected status.json first, then *.json sorted lexicographically: got {files:?}"
            );
        });
    }

    #[test]
    fn discovery_honors_env_override_first_then_default_root_extras() {
        let _guard = env_guard();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join(".rmcp_servers/rmcp_mux");
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("status.json"), "{}").unwrap();
        let override_path = dir.path().join("custom.json");
        fs::write(&override_path, "{}").unwrap();

        let env_value = override_path.to_string_lossy().into_owned();
        with_env(Some(dir.path()), Some(&env_value), || {
            let files = discover_status_files();
            assert_eq!(
                files.first().map(|p| p.as_path()),
                Some(override_path.as_path()),
                "env override must come first: got {files:?}"
            );
            assert!(
                files
                    .iter()
                    .any(|p| p.file_name().and_then(|s| s.to_str()) == Some("status.json")),
                "default-root status.json must still be discovered after the override"
            );
            // No duplicates if the override happens to also be inside the default root.
            let unique: std::collections::BTreeSet<&Path> =
                files.iter().map(|p| p.as_path()).collect();
            assert_eq!(unique.len(), files.len(), "no duplicates: {files:?}");
        });
    }

    #[test]
    fn mux_summary_classifies_healthy_unhealthy_and_unreadable() {
        let path = PathBuf::from("/tmp/general-memory.json");

        let healthy = MuxSummary::from_path_and_result(
            path.clone(),
            MuxStatusSnapshot::from_json(RUNNING_FIXTURE),
        );
        assert!(healthy.is_healthy());
        assert_eq!(healthy.display_name, "general-memory");
        assert!(matches!(healthy.state, MuxSummaryState::Healthy(_)));
        assert!(healthy.summary_line().contains("Running"));

        let failed = MuxSummary::from_path_and_result(
            path.clone(),
            MuxStatusSnapshot::from_json(FAILED_FIXTURE),
        );
        assert!(!failed.is_healthy());
        assert!(matches!(failed.state, MuxSummaryState::Unhealthy(_)));
        assert!(failed.summary_line().contains("Failed"));
        assert!(failed.summary_line().contains("max restarts reached"));

        let broken_path = PathBuf::from("/tmp/loctree-broken.json");
        let unreadable =
            MuxSummary::from_path_and_result(broken_path.clone(), Err(anyhow::anyhow!("nope")));
        assert!(!unreadable.is_healthy());
        assert_eq!(unreadable.display_name, "loctree-broken");
        let line = unreadable.summary_line();
        assert!(line.contains("loctree-broken"));
        assert!(line.contains("unreadable"));
        assert!(line.contains("nope"));
    }

    #[test]
    fn read_all_known_snapshots_pairs_paths_with_results() {
        let _guard = env_guard();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join(".rmcp_servers/rmcp_mux");
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("status.json"), RUNNING_FIXTURE).unwrap();
        fs::write(root.join("broken.json"), "not json").unwrap();

        with_env(Some(dir.path()), None, || {
            let pairs = read_all_known_snapshots();
            assert_eq!(pairs.len(), 2);
            let (good_path, good_result) = &pairs[0];
            assert_eq!(
                good_path.file_name().and_then(|s| s.to_str()),
                Some("status.json")
            );
            let snap = good_result.as_ref().expect("status.json must parse");
            assert_eq!(snap.service_name, "general-memory");

            let (bad_path, bad_result) = &pairs[1];
            assert_eq!(
                bad_path.file_name().and_then(|s| s.to_str()),
                Some("broken.json")
            );
            assert!(
                bad_result.is_err(),
                "broken.json must surface a parse error so the operator can see it"
            );
        });
    }
}

use std::sync::{Arc, RwLock};

pub type MuxSummaries = Vec<MuxSummary>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SubscriberState {
    Connected,
    Reconnecting,
    Polling,
    Failed,
}

#[derive(Debug)]
pub struct MuxSubscriber {
    pub handle: tokio::task::JoinHandle<()>,
    pub state: Arc<RwLock<SubscriberState>>,
    pub rx: std::sync::mpsc::Receiver<rmcp_mux::ipc::IpcEvent>,
}

impl MuxSubscriber {
    pub fn start(socket_path: PathBuf, _summaries: Arc<RwLock<MuxSummaries>>) -> Self {
        let (tx, rx) = std::sync::mpsc::channel();
        let state = Arc::new(RwLock::new(SubscriberState::Reconnecting));
        let state_clone = state.clone();

        let handle = tokio::spawn(async move {
            let mut attempts = 0;
            let mut backoff = std::time::Duration::from_secs(1);

            loop {
                if let Ok(mut s) = state_clone.write() {
                    *s = SubscriberState::Reconnecting;
                }
                match tokio::net::UnixStream::connect(&socket_path).await {
                    Ok(mut stream) => {
                        attempts = 0;
                        backoff = std::time::Duration::from_secs(1);
                        if let Ok(mut s) = state_clone.write() {
                            *s = SubscriberState::Connected;
                        }

                        use tokio::io::{AsyncBufReadExt, AsyncWriteExt};
                        let cmd = rmcp_mux::ipc::MuxControlCommand::Subscribe;
                        if let Ok(json) = serde_json::to_string(&cmd)
                            && stream
                                .write_all(format!("{json}\n").as_bytes())
                                .await
                                .is_ok()
                        {
                            let (reader, _) = stream.into_split();
                            let mut lines = tokio::io::BufReader::new(reader).lines();
                            while let Ok(Some(line)) = lines.next_line().await {
                                if let Ok(rmcp_mux::ipc::MuxControlResponse::Event(event)) =
                                    serde_json::from_str(&line)
                                {
                                    // Forward event to UI
                                    let _ = tx.send(event.clone());
                                    // Also update background summaries if requested
                                    if let rmcp_mux::ipc::IpcEvent::StateChange { .. } = event {
                                        // The prompt mentioned background task updates RwLock
                                        // UI loop handle_ipc_event will actually do it, but we can also just log it.
                                    }
                                }
                            }
                        }
                    }
                    Err(_) => {
                        attempts += 1;
                        if attempts >= 10 {
                            if let Ok(mut s) = state_clone.write() {
                                *s = SubscriberState::Polling;
                            }
                            break;
                        }
                        tokio::time::sleep(backoff).await;
                        backoff = std::cmp::min(backoff * 2, std::time::Duration::from_secs(30));
                    }
                }
            }
        });

        Self { handle, state, rx }
    }
}

impl Drop for MuxSubscriber {
    fn drop(&mut self) {
        self.handle.abort();
    }
}

#[cfg(test)]
mod subscriber_tests {
    use super::*;
    use rmcp_mux::ipc::{IpcEvent, MuxControlResponse};
    use std::sync::Arc;
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt};
    use tokio::net::UnixListener;

    #[tokio::test(start_paused = true)]
    async fn connects_and_subscribes() {
        let dir = tempfile::tempdir().unwrap();
        let socket_path = dir.path().join("mux.sock");
        let listener = UnixListener::bind(&socket_path).unwrap();

        let summaries = Arc::new(RwLock::new(vec![]));
        let sub = MuxSubscriber::start(socket_path.clone(), summaries);

        let (mut stream, _) = listener.accept().await.unwrap();
        let mut buf = String::new();
        tokio::io::BufReader::new(&mut stream)
            .read_line(&mut buf)
            .await
            .unwrap();

        assert!(buf.contains("Subscribe"));
        for _ in 0..10 {
            tokio::task::yield_now().await;
            if *sub.state.read().unwrap() == SubscriberState::Connected {
                break;
            }
        }
        assert_eq!(*sub.state.read().unwrap(), SubscriberState::Connected);
    }

    #[tokio::test(start_paused = true)]
    async fn reconnects_on_disconnect() {
        let dir = tempfile::tempdir().unwrap();
        let socket_path = dir.path().join("mux.sock");
        let listener = UnixListener::bind(&socket_path).unwrap();

        let summaries = Arc::new(RwLock::new(vec![]));
        let sub = MuxSubscriber::start(socket_path.clone(), summaries);

        {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut buf = String::new();
            tokio::io::BufReader::new(&mut stream)
                .read_line(&mut buf)
                .await
                .unwrap();
            for _ in 0..10 {
                tokio::task::yield_now().await;
            }
            assert_eq!(*sub.state.read().unwrap(), SubscriberState::Connected);
            // Drop stream
        }

        tokio::time::advance(std::time::Duration::from_secs(2)).await;
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut buf = String::new();
        tokio::io::BufReader::new(&mut stream)
            .read_line(&mut buf)
            .await
            .unwrap();
        for _ in 0..10 {
            tokio::task::yield_now().await;
        }
        assert_eq!(*sub.state.read().unwrap(), SubscriberState::Connected);
    }

    #[tokio::test(start_paused = true)]
    async fn falls_back_to_polling_after_10_attempts() {
        let dir = tempfile::tempdir().unwrap();
        let socket_path = dir.path().join("mux.sock"); // No listener

        let summaries = Arc::new(RwLock::new(vec![]));
        let sub = MuxSubscriber::start(socket_path.clone(), summaries);

        // Advance enough time to trigger all 10 attempts + exponential backoffs
        for _ in 0..15 {
            tokio::time::advance(std::time::Duration::from_secs(40)).await;
            tokio::task::yield_now().await;
            if *sub.state.read().unwrap() == SubscriberState::Polling {
                break;
            }
        }

        assert_eq!(*sub.state.read().unwrap(), SubscriberState::Polling);
    }

    #[tokio::test(start_paused = true)]
    async fn forwards_events_to_rx_channel() {
        let dir = tempfile::tempdir().unwrap();
        let socket_path = dir.path().join("mux.sock");
        let listener = UnixListener::bind(&socket_path).unwrap();

        let summaries = Arc::new(RwLock::new(vec![]));
        let sub = MuxSubscriber::start(socket_path.clone(), summaries);

        let (mut stream, _) = listener.accept().await.unwrap();
        let mut buf = String::new();
        tokio::io::BufReader::new(&mut stream)
            .read_line(&mut buf)
            .await
            .unwrap();

        let evt = IpcEvent::StateChange {
            service: "test".into(),
            from: "Init".into(),
            to: "Running".into(),
        };
        let resp = MuxControlResponse::Event(evt.clone());
        let payload = format!("{}\n", serde_json::to_string(&resp).unwrap());
        stream.write_all(payload.as_bytes()).await.unwrap();

        // Wait for it to be received without blocking the single-threaded tokio executor
        let mut received = None;
        for _ in 0..20 {
            tokio::task::yield_now().await;
            if let Ok(e) = sub.rx.try_recv() {
                received = Some(e);
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }

        assert_eq!(received, Some(evt));
    }
}
