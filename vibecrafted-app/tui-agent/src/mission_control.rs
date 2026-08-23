use chrono::{DateTime, Duration as ChronoDuration, NaiveDate, Utc};
use serde::Deserialize;
use serde_json::Value;
use std::cmp::Ordering;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime};

use crate::polarize::{PolarizeBand, PolarizeIntent};
use crate::state::{ControlPlaneState, RunKind, RunSnapshot, classify_run};

/// Maximum number of `*.meta.json` files we will fold per refresh. Large
/// artifact roots can hold tens of thousands of files; the dashboard
/// refresh cadence (~250ms tick) must not stall the operator on disk IO.
/// Treat the cap as a load-shed marker rather than a hard truth — the
/// `data_quality.scanned_meta_files == capped` signal warns the operator.
const META_SCAN_CAP: usize = 5_000;

/// Aggregation window for per-agent and per-skill statistics. Wider
/// windows dilute the per-agent attribution signal; narrower windows make
/// quiet skills look dead. 30d matches PLAN_23 §4 panel labels.
const STATS_WINDOW_DAYS: i64 = 30;

/// Failure board lookback. 24h matches the PLAN_23 §4 mock-up; older
/// failures should be reasoned about from the wider per-agent panel.
const FAILURE_WINDOW_HOURS: i64 = 24;

/// Active-dispatch ETA is computed from heartbeat-vs-start. Anything older
/// than this is considered stalled in the dashboard and contributes an
/// `ActionQueue` entry instead of an `ActiveDispatch` entry.
const STALL_AFTER_MINUTES: i64 = 15;

const DISK_WARN_FREE_PERCENT: f64 = 15.0;
const DISK_BLOCKED_FREE_PERCENT: f64 = 5.0;
const ULIMIT_FSIZE_BLOCKED_MB: u64 = 64;
const ULIMIT_FSIZE_BLOCKED_MB_ENV: &str = "VIBECRAFTED_ULIMIT_FSIZE_BLOCKED_MB";
const TRACKED_FILE_CAP_PERCENT: f64 = 80.0;
const TRACKED_FILE_SCAN_CAP: usize = 2_048;
const MCP_SERVERS: &[(&str, bool)] = &[
    ("loctree-mcp", true),
    ("aicx-mcp", true),
    ("vibecrafted-mcp", false),
];
const LOCTREE_CONTEXT_ATLAS_MANIFEST: &str = ".loctree/context-atlas/manifest.json";
const PROBE_CACHE_TTL_SECS: u64 = 60;
const TAILSCALE_STATUS_TIMEOUT_MS: u64 = 750;
const TAILSCALE_STATUS_JSON_ENV: &str = "VIBECRAFTED_TAILSCALE_STATUS_JSON";
/// Comma-separated dispatch hostnames; overrides `mesh.conf` when set.
const DISPATCH_TARGETS_ENV: &str = "VIBECRAFTED_DISPATCH_TARGETS";
/// `${VIBECRAFTED_HOME}/mesh.conf` — one `host theme` pair per line, `#` comments.
const MESH_CONF_FILE: &str = "mesh.conf";
const AICX_HEALTH_TIMEOUT_MS: u64 = 750;
const AICX_HEALTH_JSON_ENV: &str = "VIBECRAFTED_AICX_HEALTH_JSON";
const DISK_HEALTH_JSON_ENV: &str = "VIBECRAFTED_DISK_HEALTH_JSON";
const MCP_PROCESS_SCAN_ENV: &str = "VIBECRAFTED_MCP_PROCESS_SCAN";
const LOCTREE_SNAPSHOT_FRESHNESS_JSON_ENV: &str = "VIBECRAFTED_LOCTREE_SNAPSHOT_FRESHNESS_JSON";

static TAILSCALE_STATUS_CACHE: OnceLock<Mutex<ProbeCache<Result<String, String>>>> =
    OnceLock::new();
static AICX_HEALTH_CACHE: OnceLock<Mutex<ProbeCache<Result<String, String>>>> = OnceLock::new();
static ORPHAN_COUNT_CACHE: OnceLock<Mutex<HashMap<PathBuf, (Instant, usize)>>> = OnceLock::new();

/// Settlement f/x/n board for retained control-plane snapshots.
///
/// Source of truth for counting semantics:
/// `vibecrafted-server/control-core/src/model.rs` — `SettlementBoard::from_snapshots`.
/// The local projection preserves arbitrary future snapshot fields while the
/// linked control-core reader owns active/stalled runtime reconciliation.
/// Scope is honest: f/x/n uses retained `control_plane/runs/*.json` only.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SettlementBoardCounts {
    /// Human-readable scope boundary (never claim full meta history).
    pub scope: String,
    pub f: usize,
    pub x: usize,
    pub n: usize,
    /// Diagnostic detail inside `x`, not a fourth total bucket.
    pub invalid: usize,
    /// Retained snapshots with no verdict and no terminal `n` fallback.
    pub unclassified: usize,
    /// Canonical runtime-aware live runs (not part of `total_settled`).
    pub active: usize,
    /// Non-terminal runs without current live activity evidence.
    pub stalled: usize,
    /// Legacy `Untitled*.md` artifacts, reported separately from retained f/x/n.
    pub orphans: usize,
    pub total_settled: usize,
}

impl SettlementBoardCounts {
    pub const SCOPE_RETAINED_SNAPSHOTS: &'static str =
        "retained control_plane/runs snapshots (≠ full meta history)";

    /// Count settlement axis from retained run snapshots.
    ///
    /// Mirrors `SettlementBoard::from_snapshots` / Python `board_fxn_counts`:
    /// missing verdict contributes `n` only when the run is terminal; live
    /// unsettled runs are ignored. No exit/process signal promotes to `f`.
    #[must_use]
    pub fn from_snapshots(runs: &[RunSnapshot], active: usize, stalled: usize) -> Self {
        let mut board = Self {
            scope: Self::SCOPE_RETAINED_SNAPSHOTS.to_string(),
            active,
            stalled,
            f: 0,
            x: 0,
            n: 0,
            invalid: 0,
            unclassified: 0,
            orphans: 0,
            total_settled: 0,
        };
        for run in runs {
            match settlement_verdict_of(run) {
                Some(SettlementCell::Finalized) => board.f += 1,
                Some(SettlementCell::Failed) => board.x += 1,
                Some(SettlementCell::Invalid) => {
                    board.x += 1;
                    board.invalid += 1;
                }
                Some(SettlementCell::NeedsAttention) => board.n += 1,
                None if is_unsettled_settlement_terminal(run) => board.n += 1,
                None => board.unclassified += 1,
            }
        }
        board.total_settled = board.f + board.x + board.n;
        board
    }

    /// One-line operator strip for `vc-admin status` / Mission Control.
    #[must_use]
    pub fn render_strip(&self) -> String {
        format!(
            "settlement  f={} x={} n={} (+invalid={}) · unclassified={} · active={} · stalled={} · orphans={} · total_settled={} · scope: {}",
            self.f,
            self.x,
            self.n,
            self.invalid,
            self.unclassified,
            self.active,
            self.stalled,
            self.orphans,
            self.total_settled,
            self.scope
        )
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SettlementCell {
    Finalized,
    Failed,
    NeedsAttention,
    Invalid,
}

fn settlement_verdict_of(run: &RunSnapshot) -> Option<SettlementCell> {
    // Flat field first (Rust-reader compatible), then nested settlement.verdict.
    let flat = run
        .extra
        .get("settlement_verdict")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_ascii_lowercase);
    if let Some(raw) = flat {
        return parse_settlement_verdict(&raw);
    }
    run.extra
        .get("settlement")
        .and_then(Value::as_object)
        .and_then(|obj| obj.get("verdict"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_ascii_lowercase)
        .and_then(|raw| parse_settlement_verdict(&raw))
}

fn parse_settlement_verdict(raw: &str) -> Option<SettlementCell> {
    match raw {
        "finalized" => Some(SettlementCell::Finalized),
        "failed" => Some(SettlementCell::Failed),
        "needs_attention" => Some(SettlementCell::NeedsAttention),
        "invalid" => Some(SettlementCell::Invalid),
        _ => None,
    }
}

/// Mirrors control-core `is_unsettled_settlement_terminal` / Python `_is_terminal`.
fn is_unsettled_settlement_terminal(run: &RunSnapshot) -> bool {
    const TERMINAL_STATES: &[&str] = &[
        "report_validated",
        "completed",
        "closed",
        "converged",
        "stopped",
        "blocked",
        "failed",
        "report_missing",
        "report_invalid",
        "contract_failed",
        "recovery_required",
        "timed_out",
        "gc",
        "ghost",
        "stalled",
        "killed_by_operator",
        "process_dead",
    ];
    let state = run.display_state().to_ascii_lowercase();
    if TERMINAL_STATES.contains(&state.as_str()) {
        return true;
    }
    let liveness = run
        .extra
        .get("liveness")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_ascii_lowercase();
    if liveness == "terminal" {
        return true;
    }
    run.extra.get("exit_code").is_some_and(|v| !v.is_null())
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct MissionControlState {
    pub generated_at: String,
    pub settlement: SettlementBoardCounts,
    pub active_dispatches: Vec<ActiveDispatch>,
    pub wave_atlas: Vec<WaveSegment>,
    pub agent_stats: Vec<AgentStatsRow>,
    pub skill_stats: Vec<SkillStatsRow>,
    pub fleet_health: Vec<FleetHealthSignal>,
    pub failures: Vec<FailureEntry>,
    pub action_queue: Vec<ActionQueueItem>,
    pub data_quality: DataQuality,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActiveDispatch {
    pub run_id: String,
    pub agent: String,
    pub skill: String,
    pub root: Option<String>,
    pub root_label: String,
    pub wave: Option<String>,
    pub started_at: Option<String>,
    pub age_label: String,
    pub eta_label: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WaveSegment {
    pub wave_id: String,
    pub total: usize,
    pub completed: usize,
    pub failed: usize,
    pub active: usize,
    pub latest_state: WaveState,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WaveState {
    Pending,
    InProgress,
    Completed,
    Failed,
}

impl WaveState {
    pub fn glyph(self) -> &'static str {
        match self {
            WaveState::Pending => "·",
            WaveState::InProgress => "⏳",
            WaveState::Completed => "✓",
            WaveState::Failed => "!",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            WaveState::Pending => "pending",
            WaveState::InProgress => "in-progress",
            WaveState::Completed => "completed",
            WaveState::Failed => "failed",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct AgentStatsRow {
    pub agent: String,
    pub total_runs: usize,
    pub completed: usize,
    pub failed: usize,
    pub success_rate: f32,
    pub avg_duration_s: Option<f64>,
    pub model_known_rate: f32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SkillStatsRow {
    pub skill: String,
    pub invocations: usize,
    pub completed: usize,
    pub failed: usize,
    pub avg_duration_s: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FleetHealthSignal {
    pub label: String,
    pub status: FleetHealthStatus,
    pub detail: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FleetHealthStatus {
    Ok,
    Warn,
    Blocked,
    Unknown,
}

impl FleetHealthStatus {
    pub fn marker(self) -> &'static str {
        match self {
            FleetHealthStatus::Ok => "✓",
            FleetHealthStatus::Warn => "!",
            FleetHealthStatus::Blocked => "✗",
            FleetHealthStatus::Unknown => "?",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FailureEntry {
    pub run_id: String,
    pub agent: String,
    pub skill: String,
    pub reason: String,
    pub occurred_at: Option<String>,
    pub age_label: String,
    pub source_path: Option<PathBuf>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActionQueueItem {
    pub kind: ActionQueueKind,
    pub summary: String,
    pub source_path: Option<PathBuf>,
    pub priority: ActionPriority,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ActionQueueKind {
    StalledRun,
    Failure,
    Polarize,
    ReportReady,
}

impl ActionQueueKind {
    pub fn label(self) -> &'static str {
        match self {
            ActionQueueKind::StalledRun => "stalled",
            ActionQueueKind::Failure => "failure",
            ActionQueueKind::Polarize => "polarize",
            ActionQueueKind::ReportReady => "report",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum ActionPriority {
    Critical,
    High,
    Normal,
}

impl ActionPriority {
    pub fn marker(self) -> &'static str {
        match self {
            ActionPriority::Critical => "!!",
            ActionPriority::High => "!",
            ActionPriority::Normal => "-",
        }
    }
}

/// Explicit data-quality markers. We refuse to silently coerce missing
/// upstream fields into fake successes; the dashboard displays these
/// counters so the operator knows which panels are partially blind.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DataQuality {
    pub scanned_meta_files: usize,
    pub capped: bool,
    pub missing_model: usize,
    pub missing_duration: usize,
    pub parse_failures: usize,
    pub artifact_root: Option<PathBuf>,
    pub artifact_root_present: bool,
}

#[derive(Debug, Clone, Deserialize, Default)]
struct MetaJson {
    #[serde(default)]
    run_id: Option<String>,
    #[serde(default)]
    agent: Option<String>,
    #[serde(default)]
    skill_code: Option<String>,
    #[serde(default)]
    mode: Option<String>,
    #[serde(default)]
    status: Option<String>,
    #[serde(default)]
    exit_code: Option<i64>,
    #[serde(default)]
    model: Option<String>,
    #[serde(default)]
    duration_s: Option<f64>,
    #[serde(default)]
    completed_at: Option<String>,
    #[serde(default)]
    updated_at: Option<String>,
    #[serde(default)]
    report: Option<String>,
    #[serde(default)]
    prompt_id: Option<String>,
}

impl MissionControlState {
    /// Build the mission-control view from real local sources. Caller
    /// owns the `ControlPlaneState` snapshot for live runs and supplies
    /// the artifact root where `*.meta.json` history lives.
    pub fn build(state: &ControlPlaneState, artifact_root: &Path) -> Self {
        Self::build_with_intents(state, artifact_root, &[])
    }

    /// Build mission-control state while enriching the action queue with
    /// current `vc-polarize` intents discovered by the app refresh flow.
    pub fn build_with_intents(
        state: &ControlPlaneState,
        artifact_root: &Path,
        intents: &[PolarizeIntent],
    ) -> Self {
        let now = Utc::now();
        Self::build_at_with_intents(state, artifact_root, intents, now)
    }

    /// Deterministic build entrypoint that takes the "now" timestamp
    /// explicitly. Tests use this to keep time-based classifications
    /// stable across CI machines.
    pub fn build_at(state: &ControlPlaneState, artifact_root: &Path, now: DateTime<Utc>) -> Self {
        Self::build_at_with_intents(state, artifact_root, &[], now)
    }

    pub fn build_at_with_intents(
        state: &ControlPlaneState,
        artifact_root: &Path,
        intents: &[PolarizeIntent],
        now: DateTime<Utc>,
    ) -> Self {
        Self::build_at_with_scope(state, artifact_root, intents, now, None)
    }

    /// Explicit mission-root filtered view. The default builders stay
    /// fleet-wide; callers must opt in when they want a local-root slice.
    pub fn build_at_for_root(
        state: &ControlPlaneState,
        artifact_root: &Path,
        now: DateTime<Utc>,
        mission_root: &Path,
    ) -> Self {
        Self::build_at_with_scope(state, artifact_root, &[], now, Some(mission_root))
    }

    fn build_at_with_scope(
        state: &ControlPlaneState,
        artifact_root: &Path,
        intents: &[PolarizeIntent],
        now: DateTime<Utc>,
        mission_root: Option<&Path>,
    ) -> Self {
        let (meta_records, mut data_quality) = collect_meta_records(artifact_root, now);
        data_quality.artifact_root = Some(artifact_root.to_path_buf());
        data_quality.artifact_root_present = artifact_root.exists();

        let active_dispatches = active_dispatches_from_state(state, now, mission_root);
        let wave_atlas = wave_atlas_from_meta(&meta_records, state, now);
        let agent_stats = agent_stats_from_meta(&meta_records, now);
        let skill_stats = skill_stats_from_meta(&meta_records, now);
        let failures = failure_board_from_meta(&meta_records, state, now);
        let fleet_health = fleet_health_from_inputs(state, artifact_root, &data_quality);
        let action_queue = action_queue_from_inputs(state, &failures, &meta_records, intents, now);
        let mut settlement = SettlementBoardCounts::from_snapshots(
            &state.retained_runs,
            state.canonical_active_count(),
            state.canonical_stalled_count(),
        );
        settlement.orphans = cached_orphan_markdown_count(artifact_root);

        Self {
            generated_at: now.to_rfc3339(),
            settlement,
            active_dispatches,
            wave_atlas,
            agent_stats,
            skill_stats,
            fleet_health,
            failures,
            action_queue,
            data_quality,
        }
    }

    /// Convenience: total entries surfaced across all panels. Used by
    /// the tab badge.
    pub fn total_entries(&self) -> usize {
        self.active_dispatches.len()
            + self.wave_atlas.len()
            + self.agent_stats.len()
            + self.skill_stats.len()
            + self.fleet_health.len()
            + self.failures.len()
            + self.action_queue.len()
    }

    pub fn is_empty(&self) -> bool {
        self.total_entries() == 0
    }
}

#[derive(Debug, Clone)]
struct MetaRecord {
    meta: MetaJson,
    path: PathBuf,
    completed_at: DateTime<Utc>,
}

fn collect_meta_records(
    artifact_root: &Path,
    now: DateTime<Utc>,
) -> (Vec<MetaRecord>, DataQuality) {
    let mut quality = DataQuality::default();
    let mut records = Vec::new();
    if !artifact_root.exists() {
        return (records, quality);
    }
    let window_floor = now - ChronoDuration::days(STATS_WINDOW_DAYS);
    let mut files = Vec::new();
    walk_meta_files(artifact_root, &mut files, &window_floor.date_naive());
    // Most-recent-first so the scan cap keeps the FRESHEST runs (the operator's
    // live activity), not whatever the directory walk reached first. Without this
    // the cap was filled in directory order, so new runs in late-sorted dirs fell
    // off it and the panel went silent about them.
    files.sort_by_key(|path| {
        std::cmp::Reverse(fs::metadata(path).and_then(|meta| meta.modified()).ok())
    });

    for path in files.into_iter().take(META_SCAN_CAP) {
        let text = match fs::read_to_string(&path) {
            Ok(text) => text,
            Err(_) => {
                quality.parse_failures += 1;
                continue;
            }
        };
        let parsed: MetaJson = match serde_json::from_str(&text) {
            Ok(value) => value,
            Err(_) => {
                quality.parse_failures += 1;
                continue;
            }
        };
        quality.scanned_meta_files += 1;
        if parsed
            .model
            .as_deref()
            .map(str::trim)
            .unwrap_or("")
            .is_empty()
            || parsed.model.as_deref() == Some("unknown")
        {
            quality.missing_model += 1;
        }
        if parsed.duration_s.is_none() {
            quality.missing_duration += 1;
        }
        let completed_at = parsed
            .completed_at
            .as_deref()
            .and_then(parse_rfc3339)
            .or_else(|| parsed.updated_at.as_deref().and_then(parse_rfc3339))
            .unwrap_or(window_floor);
        if completed_at < window_floor {
            continue;
        }
        records.push(MetaRecord {
            meta: parsed,
            path,
            completed_at,
        });
    }
    // If the directory walk produced more than the cap before the take
    // applied above, mark the data-quality flag so the operator sees
    // load-shed truth instead of a "5000 runs" claim.
    if quality.scanned_meta_files >= META_SCAN_CAP {
        quality.capped = true;
    }
    (records, quality)
}

fn walk_meta_files(dir: &Path, out: &mut Vec<PathBuf>, window_floor: &NaiveDate) {
    // Collect ALL in-window meta files — NO cap during the walk. The caller sorts
    // newest-first and then applies META_SCAN_CAP, so the cap must see the whole
    // in-window set; capping here (in directory order) hid fresh runs in late-
    // walked directories. The date-window filter below still bounds the walk.
    let entries = match fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(metadata) = entry.file_type() else {
            continue;
        };
        // Refuse to follow symlinks; matches the existing
        // `safe_artifact_path` posture in `app.rs`.
        if metadata.is_symlink() {
            continue;
        }
        if metadata.is_dir() {
            if !directory_within_window(&path, window_floor) {
                continue;
            }
            walk_meta_files(&path, out, window_floor);
        } else if metadata.is_file()
            && path
                .file_name()
                .and_then(|name| name.to_str())
                .map(|name| name.ends_with(".meta.json"))
                .unwrap_or(false)
        {
            out.push(path);
        }
    }
}

fn cached_orphan_markdown_count(artifact_root: &Path) -> usize {
    let now = Instant::now();
    let cache = ORPHAN_COUNT_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let Ok(mut cache) = cache.lock() else {
        return count_orphan_markdown_files(artifact_root);
    };
    if let Some((last_run, count)) = cache.get(artifact_root)
        && now
            .checked_duration_since(*last_run)
            .is_some_and(|age| age < Duration::from_secs(PROBE_CACHE_TTL_SECS))
    {
        return *count;
    }
    let count = count_orphan_markdown_files(artifact_root);
    if cache.len() >= 64 {
        cache.retain(|_, (last_run, _)| {
            now.checked_duration_since(*last_run)
                .is_some_and(|age| age < Duration::from_secs(PROBE_CACHE_TTL_SECS))
        });
    }
    cache.insert(artifact_root.to_path_buf(), (now, count));
    count
}

fn count_orphan_markdown_files(artifact_root: &Path) -> usize {
    fn walk(dir: &Path, count: &mut usize) {
        let entries = match fs::read_dir(dir) {
            Ok(entries) => entries,
            Err(_) => return,
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let Ok(file_type) = entry.file_type() else {
                continue;
            };
            if file_type.is_symlink() {
                continue;
            }
            if file_type.is_dir() {
                walk(&path, count);
                continue;
            }
            if !file_type.is_file() {
                continue;
            }
            let is_orphan = path
                .file_name()
                .and_then(|name| name.to_str())
                .map(str::to_ascii_lowercase)
                .is_some_and(|name| name.starts_with("untitled") && name.ends_with(".md"));
            if is_orphan {
                *count += 1;
            }
        }
    }

    let mut count = 0;
    walk(artifact_root, &mut count);
    count
}

fn directory_within_window(path: &Path, window_floor: &NaiveDate) -> bool {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return true;
    };
    if name.len() == 8
        && name.bytes().all(|b| b.is_ascii_digit())
        && let Ok(date) = NaiveDate::parse_from_str(name, "%Y%m%d")
    {
        return date >= *window_floor;
    }
    if name.len() == 9 && name.as_bytes().get(4) == Some(&b'_') {
        let trimmed = name.replace('_', "");
        if let Ok(date) = NaiveDate::parse_from_str(&trimmed, "%Y%m%d") {
            return date >= *window_floor;
        }
    }
    // Anything that does not look like a YYYYMMDD/YYYY_MMDD bucket is
    // walked unconditionally — it might be an org/project node that
    // hosts the dated buckets below it.
    true
}

fn active_dispatches_from_state(
    state: &ControlPlaneState,
    now: DateTime<Utc>,
    mission_root: Option<&Path>,
) -> Vec<ActiveDispatch> {
    let mut out = Vec::new();
    for snapshot in &state.runs {
        if !matches_root_filter(snapshot.root.as_deref(), mission_root) {
            continue;
        }
        let kind = classify_run(snapshot, now);
        if !matches!(kind, RunKind::Active) {
            continue;
        }
        let started_at = snapshot.started_at.clone();
        let started = started_at.as_deref().and_then(parse_rfc3339);
        let age_label = match started {
            Some(start) => relative_age(start, now),
            None => "age unknown".to_string(),
        };
        let eta_label = compute_eta_label(snapshot.last_heartbeat.as_deref(), now);
        let wave = snapshot
            .extra
            .get("wave")
            .and_then(|value| value.as_str())
            .map(ToOwned::to_owned);
        let root = snapshot
            .root
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(ToOwned::to_owned);
        let root_label = root_label(root.as_deref());
        out.push(ActiveDispatch {
            run_id: snapshot.run_id.clone(),
            agent: snapshot
                .agent
                .clone()
                .unwrap_or_else(|| "unknown".to_string()),
            skill: snapshot
                .skill
                .clone()
                .or_else(|| snapshot.mode.clone())
                .unwrap_or_else(|| "unknown".to_string()),
            root,
            root_label,
            wave,
            started_at,
            age_label,
            eta_label,
        });
    }
    out.sort_by(|left, right| left.age_label.cmp(&right.age_label));
    out
}

fn matches_root_filter(run_root: Option<&str>, mission_root: Option<&Path>) -> bool {
    let Some(mission_root) = mission_root else {
        return true;
    };
    let Some(run_root) = run_root.map(str::trim).filter(|value| !value.is_empty()) else {
        return false;
    };
    Path::new(run_root) == mission_root
}

fn root_label(root: Option<&str>) -> String {
    let Some(root) = root.map(str::trim).filter(|value| !value.is_empty()) else {
        return "root unknown".to_string();
    };
    Path::new(root)
        .file_name()
        .and_then(|name| name.to_str())
        .filter(|name| !name.is_empty())
        .unwrap_or(root)
        .to_string()
}

fn compute_eta_label(last_heartbeat: Option<&str>, now: DateTime<Utc>) -> String {
    let Some(heartbeat) = last_heartbeat.and_then(parse_rfc3339) else {
        return "no heartbeat".to_string();
    };
    let lag = now.signed_duration_since(heartbeat);
    let lag_minutes = lag.num_minutes();
    if lag_minutes >= STALL_AFTER_MINUTES {
        format!("stalled {}m", lag_minutes)
    } else if lag_minutes <= 0 {
        "fresh".to_string()
    } else {
        format!("{}m since heartbeat", lag_minutes)
    }
}

fn wave_atlas_from_meta(
    records: &[MetaRecord],
    state: &ControlPlaneState,
    now: DateTime<Utc>,
) -> Vec<WaveSegment> {
    let mut groups: BTreeMap<String, WaveAccumulator> = BTreeMap::new();
    for record in records {
        let Some(wave_id) = derive_wave_id(&record.meta) else {
            continue;
        };
        let entry = groups.entry(wave_id).or_default();
        entry.total += 1;
        // exit_code and status are two spellings of the same outcome, so
        // each record contributes at most one count; a failure signal from
        // either side wins over completion.
        let status = record.meta.status.as_deref().map(str::to_ascii_lowercase);
        let status_failed = status
            .as_deref()
            .is_some_and(|status| status.contains("fail") || status.contains("error"));
        let status_completed = status
            .as_deref()
            .is_some_and(|status| status.contains("complete") || status.contains("done"));
        if matches!(record.meta.exit_code, Some(code) if code != 0) || status_failed {
            entry.failed += 1;
        } else if matches!(record.meta.exit_code, Some(0)) || status_completed {
            entry.completed += 1;
        }
    }
    // Live runs contribute to the wave atlas too — an in-progress wave
    // should show its active dispatches even when no meta.json has been
    // written yet.
    for snapshot in &state.runs {
        let Some(prompt_id) = snapshot
            .extra
            .get("prompt_id")
            .and_then(|value| value.as_str())
            .map(ToOwned::to_owned)
        else {
            continue;
        };
        if !matches!(
            classify_run(snapshot, now),
            RunKind::Active | RunKind::Stalled
        ) {
            continue;
        }
        let entry = groups.entry(prompt_id).or_default();
        entry.active += 1;
        entry.total += 1;
    }

    let mut segments: Vec<WaveSegment> = groups
        .into_iter()
        .map(|(wave_id, acc)| WaveSegment {
            wave_id,
            total: acc.total,
            completed: acc.completed,
            failed: acc.failed,
            active: acc.active,
            latest_state: acc.classify(),
        })
        .collect();
    segments.sort_by(|left, right| {
        right
            .total
            .cmp(&left.total)
            .then(left.wave_id.cmp(&right.wave_id))
    });
    segments.truncate(8);
    segments
}

#[derive(Debug, Default)]
struct WaveAccumulator {
    total: usize,
    completed: usize,
    failed: usize,
    active: usize,
}

impl WaveAccumulator {
    fn classify(&self) -> WaveState {
        if self.active > 0 {
            WaveState::InProgress
        } else if self.failed > 0 && self.completed == 0 {
            WaveState::Failed
        } else if self.completed == self.total && self.total > 0 {
            WaveState::Completed
        } else if self.completed > 0 && self.completed < self.total {
            WaveState::InProgress
        } else {
            WaveState::Pending
        }
    }
}

fn derive_wave_id(meta: &MetaJson) -> Option<String> {
    if let Some(prompt) = meta.prompt_id.as_deref() {
        return Some(prompt.to_string());
    }
    if let (Some(skill), Some(run_id)) = (meta.skill_code.as_deref(), meta.run_id.as_deref()) {
        let prefix = run_id.split('-').next().unwrap_or(run_id);
        return Some(format!("{skill}/{prefix}"));
    }
    meta.skill_code.clone()
}

fn agent_stats_from_meta(records: &[MetaRecord], _now: DateTime<Utc>) -> Vec<AgentStatsRow> {
    let mut buckets: HashMap<String, AgentBucket> = HashMap::new();
    for record in records {
        let agent = record
            .meta
            .agent
            .clone()
            .unwrap_or_else(|| "unknown".to_string());
        let bucket = buckets.entry(agent).or_default();
        bucket.total += 1;
        match record.meta.exit_code {
            Some(0) => bucket.completed += 1,
            Some(code) if code != 0 => bucket.failed += 1,
            _ => {}
        }
        if let Some(duration) = record.meta.duration_s {
            bucket.duration_sum_s += duration;
            bucket.duration_count += 1;
        }
        if let Some(model) = record.meta.model.as_deref()
            && !model.is_empty()
            && model != "unknown"
        {
            bucket.model_known += 1;
        }
    }
    let mut rows: Vec<AgentStatsRow> = buckets
        .into_iter()
        .map(|(agent, bucket)| {
            let success_rate = if bucket.total == 0 {
                0.0
            } else {
                bucket.completed as f32 / bucket.total as f32
            };
            let model_known_rate = if bucket.total == 0 {
                0.0
            } else {
                bucket.model_known as f32 / bucket.total as f32
            };
            let avg_duration_s = if bucket.duration_count == 0 {
                None
            } else {
                Some(bucket.duration_sum_s / bucket.duration_count as f64)
            };
            AgentStatsRow {
                agent,
                total_runs: bucket.total,
                completed: bucket.completed,
                failed: bucket.failed,
                success_rate,
                avg_duration_s,
                model_known_rate,
            }
        })
        .collect();
    rows.sort_by(|left, right| {
        right
            .total_runs
            .cmp(&left.total_runs)
            .then(left.agent.cmp(&right.agent))
    });
    rows
}

#[derive(Debug, Default)]
struct AgentBucket {
    total: usize,
    completed: usize,
    failed: usize,
    duration_sum_s: f64,
    duration_count: usize,
    model_known: usize,
}

fn skill_stats_from_meta(records: &[MetaRecord], _now: DateTime<Utc>) -> Vec<SkillStatsRow> {
    let mut buckets: HashMap<String, SkillBucket> = HashMap::new();
    for record in records {
        let skill = record
            .meta
            .skill_code
            .clone()
            .or_else(|| record.meta.mode.clone())
            .unwrap_or_else(|| "unknown".to_string());
        let bucket = buckets.entry(skill).or_default();
        bucket.invocations += 1;
        match record.meta.exit_code {
            Some(0) => bucket.completed += 1,
            Some(code) if code != 0 => bucket.failed += 1,
            _ => {}
        }
        if let Some(duration) = record.meta.duration_s {
            bucket.duration_sum_s += duration;
            bucket.duration_count += 1;
        }
    }
    let mut rows: Vec<SkillStatsRow> = buckets
        .into_iter()
        .map(|(skill, bucket)| SkillStatsRow {
            skill,
            invocations: bucket.invocations,
            completed: bucket.completed,
            failed: bucket.failed,
            avg_duration_s: if bucket.duration_count == 0 {
                None
            } else {
                Some(bucket.duration_sum_s / bucket.duration_count as f64)
            },
        })
        .collect();
    rows.sort_by(|left, right| {
        right
            .invocations
            .cmp(&left.invocations)
            .then(left.skill.cmp(&right.skill))
    });
    rows
}

#[derive(Debug, Default)]
struct SkillBucket {
    invocations: usize,
    completed: usize,
    failed: usize,
    duration_sum_s: f64,
    duration_count: usize,
}

fn failure_board_from_meta(
    records: &[MetaRecord],
    state: &ControlPlaneState,
    now: DateTime<Utc>,
) -> Vec<FailureEntry> {
    let cutoff = now - ChronoDuration::hours(FAILURE_WINDOW_HOURS);
    // Timestamp travels next to the entry so ordering is chronological;
    // `age_label` is a display string and must never drive the sort
    // ("11h ago" < "2m ago" lexicographically would evict fresh failures).
    let mut failures: Vec<(Option<DateTime<Utc>>, FailureEntry)> = Vec::new();
    let mut seen_run_ids: HashSet<String> = HashSet::new();

    for record in records {
        let is_failure = match record.meta.exit_code {
            Some(code) if code != 0 => true,
            Some(_) => false,
            None => record
                .meta
                .status
                .as_deref()
                .map(|status| {
                    let status = status.to_ascii_lowercase();
                    status.contains("fail") || status.contains("error")
                })
                .unwrap_or(false),
        };
        if !is_failure {
            continue;
        }
        if record.completed_at < cutoff {
            continue;
        }
        let run_id = record
            .meta
            .run_id
            .clone()
            .unwrap_or_else(|| "unknown".to_string());
        if run_id != "unknown" && !seen_run_ids.insert(run_id.clone()) {
            continue;
        }
        failures.push((
            Some(record.completed_at),
            FailureEntry {
                run_id,
                agent: record
                    .meta
                    .agent
                    .clone()
                    .unwrap_or_else(|| "unknown".to_string()),
                skill: record
                    .meta
                    .skill_code
                    .clone()
                    .or_else(|| record.meta.mode.clone())
                    .unwrap_or_else(|| "unknown".to_string()),
                reason: record
                    .meta
                    .status
                    .clone()
                    .unwrap_or_else(|| match record.meta.exit_code {
                        Some(code) => format!("exit_code {code}"),
                        None => "failed".to_string(),
                    }),
                occurred_at: Some(record.completed_at.to_rfc3339()),
                age_label: relative_age(record.completed_at, now),
                source_path: Some(record.path.clone()),
            },
        ));
    }

    for snapshot in &state.runs {
        if !matches!(classify_run(snapshot, now), RunKind::Failed) {
            continue;
        }
        // Prefer completed_at (or first settlement stamp) over updated_at.
        // Watchdog/sync restamps updated_at every pass, which made day-old
        // fails show as "10m ago" and kept them inside the 24h window forever.
        let timestamp = snapshot
            .extra
            .get("completed_at")
            .and_then(|value| value.as_str())
            .and_then(parse_rfc3339)
            .or_else(|| {
                snapshot
                    .extra
                    .get("settlement_at")
                    .and_then(|value| value.as_str())
                    .and_then(parse_rfc3339)
            })
            .or_else(|| snapshot.updated_at.as_deref().and_then(parse_rfc3339))
            .or_else(|| snapshot.started_at.as_deref().and_then(parse_rfc3339));
        // Same 24h window as the meta loop — without it, long-dead
        // garbage-collected snapshots permanently occupy the 20-row board.
        if matches!(timestamp, Some(ts) if ts < cutoff) {
            continue;
        }
        if !seen_run_ids.insert(snapshot.run_id.clone()) {
            continue;
        }
        failures.push((
            timestamp,
            FailureEntry {
                run_id: snapshot.run_id.clone(),
                agent: snapshot
                    .agent
                    .clone()
                    .unwrap_or_else(|| "unknown".to_string()),
                skill: snapshot
                    .skill
                    .clone()
                    .or_else(|| snapshot.mode.clone())
                    .unwrap_or_else(|| "unknown".to_string()),
                reason: snapshot
                    .last_error
                    .clone()
                    .or_else(|| snapshot.status.clone())
                    .or_else(|| snapshot.state.clone())
                    .unwrap_or_else(|| "failed".to_string()),
                occurred_at: timestamp.map(|ts| ts.to_rfc3339()),
                age_label: timestamp
                    .map(|ts| relative_age(ts, now))
                    .unwrap_or_else(|| "age unknown".to_string()),
                source_path: snapshot
                    .latest_report
                    .as_deref()
                    .map(PathBuf::from)
                    .or_else(|| snapshot.root.as_deref().map(PathBuf::from)),
            },
        ));
    }

    // Freshest first; entries without a parsable timestamp sink to the end.
    failures.sort_by(|(left, _), (right, _)| match (right, left) {
        (Some(r), Some(l)) => r.cmp(l),
        (Some(_), None) => Ordering::Greater,
        (None, Some(_)) => Ordering::Less,
        (None, None) => Ordering::Equal,
    });
    failures.truncate(20);
    failures.into_iter().map(|(_, entry)| entry).collect()
}

fn fleet_health_from_inputs(
    state: &ControlPlaneState,
    artifact_root: &Path,
    data_quality: &DataQuality,
) -> Vec<FleetHealthSignal> {
    let mut signals = Vec::new();

    let control_plane_status = if state.root.exists() {
        FleetHealthStatus::Ok
    } else {
        FleetHealthStatus::Blocked
    };
    signals.push(FleetHealthSignal {
        label: "control-plane".to_string(),
        status: control_plane_status,
        detail: format!(
            "{} ({} runs)",
            state.root.to_string_lossy(),
            state.runs.len()
        ),
    });

    let artifact_status = if data_quality.artifact_root_present {
        FleetHealthStatus::Ok
    } else {
        FleetHealthStatus::Warn
    };
    signals.push(FleetHealthSignal {
        label: "artifact-root".to_string(),
        status: artifact_status,
        detail: artifact_root.to_string_lossy().into_owned(),
    });

    let scan_status = if data_quality.capped {
        FleetHealthStatus::Warn
    } else if data_quality.scanned_meta_files == 0 {
        FleetHealthStatus::Unknown
    } else {
        FleetHealthStatus::Ok
    };
    let scan_detail = if data_quality.capped {
        format!("{} scanned (capped)", data_quality.scanned_meta_files)
    } else {
        format!("{} meta.json scanned", data_quality.scanned_meta_files)
    };
    signals.push(FleetHealthSignal {
        label: "meta scan".to_string(),
        status: scan_status,
        detail: scan_detail,
    });

    let model_status = if data_quality.scanned_meta_files == 0 {
        FleetHealthStatus::Unknown
    } else if data_quality.missing_model == 0 {
        FleetHealthStatus::Ok
    } else if data_quality.missing_model * 4 > data_quality.scanned_meta_files {
        FleetHealthStatus::Warn
    } else {
        FleetHealthStatus::Ok
    };
    signals.push(FleetHealthSignal {
        label: "model parity".to_string(),
        status: model_status,
        detail: format!(
            "{}/{} missing model",
            data_quality.missing_model,
            data_quality.scanned_meta_files.max(1)
        ),
    });

    let duration_status = if data_quality.scanned_meta_files == 0 {
        FleetHealthStatus::Unknown
    } else if data_quality.missing_duration == 0 {
        FleetHealthStatus::Ok
    } else if data_quality.missing_duration * 4 > data_quality.scanned_meta_files {
        FleetHealthStatus::Warn
    } else {
        FleetHealthStatus::Ok
    };
    signals.push(FleetHealthSignal {
        label: "duration parity".to_string(),
        status: duration_status,
        detail: format!(
            "{}/{} missing duration_s",
            data_quality.missing_duration,
            data_quality.scanned_meta_files.max(1)
        ),
    });

    signals.extend(disk_health_signals());
    signals.extend(mcp_health_signals());
    signals.extend(tailscale_health_signals());
    signals.extend(aicx_health_signals());

    signals
}

#[derive(Debug, Clone)]
struct ProbeCache<T> {
    ttl: Duration,
    last_run: Option<Instant>,
    result: Option<T>,
}

impl<T: Clone> ProbeCache<T> {
    fn new(ttl: Duration) -> Self {
        Self {
            ttl,
            last_run: None,
            result: None,
        }
    }

    fn get_or_refresh<F>(&mut self, now: Instant, refresh: F) -> T
    where
        F: FnOnce() -> T,
    {
        if let (Some(last_run), Some(result)) = (self.last_run, self.result.as_ref())
            && now
                .checked_duration_since(last_run)
                .is_some_and(|age| age < self.ttl)
        {
            return result.clone();
        }
        let result = refresh();
        self.last_run = Some(now);
        self.result = Some(result.clone());
        result
    }
}

fn cached_probe_result<F>(
    cache: &'static OnceLock<Mutex<ProbeCache<Result<String, String>>>>,
    refresh: F,
) -> Result<String, String>
where
    F: FnOnce() -> Result<String, String>,
{
    let cache = cache
        .get_or_init(|| Mutex::new(ProbeCache::new(Duration::from_secs(PROBE_CACHE_TTL_SECS))));
    match cache.lock() {
        Ok(mut cache) => cache.get_or_refresh(Instant::now(), refresh),
        Err(err) => Err(format!("probe cache unavailable: {err}")),
    }
}

fn tailscale_health_signals() -> Vec<FleetHealthSignal> {
    let targets = configured_dispatch_targets();
    tailscale_health_signals_from_status(cached_tailscale_status_json(), &targets)
}

/// Dispatch targets in precedence order: `VIBECRAFTED_DISPATCH_TARGETS`
/// (comma-separated) first, then `${VIBECRAFTED_HOME}/mesh.conf`. No config
/// yields an empty list — the probe then reports a neutral "not configured"
/// signal instead of guessing hostnames.
fn configured_dispatch_targets() -> Vec<String> {
    if let Some(raw) = env::var_os(DISPATCH_TARGETS_ENV) {
        let raw = raw.to_string_lossy();
        return dedupe_preserving_order(raw.split(',').map(str::trim));
    }
    let mesh_conf = crate::config::default_vibecrafted_home().join(MESH_CONF_FILE);
    match fs::read_to_string(&mesh_conf) {
        Ok(contents) => parse_mesh_conf_hosts(&contents),
        Err(_) => Vec::new(),
    }
}

/// First whitespace-separated token (host) of every non-comment, non-blank line.
fn parse_mesh_conf_hosts(contents: &str) -> Vec<String> {
    dedupe_preserving_order(
        contents
            .lines()
            .map(str::trim)
            .filter(|line| !line.is_empty() && !line.starts_with('#'))
            .filter_map(|line| line.split_whitespace().next()),
    )
}

fn dedupe_preserving_order<'a>(hosts: impl Iterator<Item = &'a str>) -> Vec<String> {
    let mut seen = HashSet::new();
    hosts
        .filter(|host| !host.is_empty())
        .filter(|host| seen.insert(normalize_tailscale_name(host)))
        .map(str::to_string)
        .collect()
}

fn is_dispatch_target(key: &str, targets: &[String]) -> bool {
    targets
        .iter()
        .any(|target| key == normalize_tailscale_name(target))
}

fn tailscale_health_signals_from_status(
    status_json: Result<String, String>,
    targets: &[String],
) -> Vec<FleetHealthSignal> {
    let raw = match status_json {
        Ok(raw) => raw,
        Err(err) => {
            return vec![FleetHealthSignal {
                label: "tailscale status".to_string(),
                status: FleetHealthStatus::Unknown,
                detail: err,
            }];
        }
    };
    let status = match serde_json::from_str::<TailscaleStatus>(&raw) {
        Ok(status) => status,
        Err(err) => {
            return vec![FleetHealthSignal {
                label: "tailscale status".to_string(),
                status: FleetHealthStatus::Unknown,
                detail: format!("invalid status JSON: {err}"),
            }];
        }
    };

    let mut peers = status
        .peers
        .iter()
        .map(|(key, peer)| (peer.display_name(key), peer))
        .collect::<Vec<_>>();
    peers.sort_by(|(left, _), (right, _)| left.cmp(right));

    let mut reported_peer_keys = Vec::new();
    let mut signals = Vec::new();
    for (name, peer) in peers {
        reported_peer_keys.extend(peer.match_keys(&name));
        signals.push(tailscale_peer_signal(&name, peer, targets));
    }

    for target in targets {
        if !reported_peer_keys
            .iter()
            .any(|key| key == &normalize_tailscale_name(target))
        {
            signals.push(FleetHealthSignal {
                label: format!("tailscale {target}"),
                status: FleetHealthStatus::Blocked,
                detail: "dispatch target missing from tailscale status".to_string(),
            });
        }
    }

    if signals.is_empty() {
        signals.push(FleetHealthSignal {
            label: "tailscale peers".to_string(),
            status: FleetHealthStatus::Unknown,
            detail: "tailscale status reported no peers".to_string(),
        });
    }
    if targets.is_empty() {
        signals.push(FleetHealthSignal {
            label: "tailscale targets".to_string(),
            status: FleetHealthStatus::Unknown,
            detail: format!("no dispatch targets configured ({MESH_CONF_FILE})"),
        });
    }
    signals
}

fn tailscale_peer_signal(
    name: &str,
    peer: &TailscalePeer,
    targets: &[String],
) -> FleetHealthSignal {
    let critical = peer
        .match_keys(name)
        .iter()
        .any(|key| is_dispatch_target(key, targets));
    match peer.online {
        Some(true) => FleetHealthSignal {
            label: format!("tailscale {name}"),
            status: FleetHealthStatus::Ok,
            detail: tailscale_peer_detail("online", peer),
        },
        Some(false) => FleetHealthSignal {
            label: format!("tailscale {name}"),
            status: if critical {
                FleetHealthStatus::Blocked
            } else {
                FleetHealthStatus::Warn
            },
            detail: tailscale_peer_detail(
                if critical {
                    "dispatch target offline"
                } else {
                    "peer offline"
                },
                peer,
            ),
        },
        None => FleetHealthSignal {
            label: format!("tailscale {name}"),
            status: FleetHealthStatus::Unknown,
            detail: tailscale_peer_detail("online state missing", peer),
        },
    }
}

fn tailscale_peer_detail(prefix: &str, peer: &TailscalePeer) -> String {
    match peer.tailscale_ips.first() {
        Some(ip) => format!("{prefix} ({ip})"),
        None => prefix.to_string(),
    }
}

fn cached_tailscale_status_json() -> Result<String, String> {
    if let Ok(raw) = env::var(TAILSCALE_STATUS_JSON_ENV) {
        return Ok(raw);
    }
    cached_probe_result(&TAILSCALE_STATUS_CACHE, tailscale_status_json_uncached)
}

fn tailscale_status_json_uncached() -> Result<String, String> {
    bounded_command_stdout(
        "tailscale",
        &["status", "--json"],
        TAILSCALE_STATUS_TIMEOUT_MS,
    )
}

fn aicx_health_signals() -> Vec<FleetHealthSignal> {
    aicx_health_signals_from_output(cached_aicx_health_json())
}

fn cached_aicx_health_json() -> Result<String, String> {
    if let Ok(raw) = env::var(AICX_HEALTH_JSON_ENV) {
        return Ok(raw);
    }
    cached_probe_result(&AICX_HEALTH_CACHE, aicx_health_json_uncached)
}

fn aicx_health_json_uncached() -> Result<String, String> {
    bounded_command_stdout("aicx", &["health"], AICX_HEALTH_TIMEOUT_MS)
}

fn bounded_command_stdout(program: &str, args: &[&str], timeout_ms: u64) -> Result<String, String> {
    let command_label = if args.is_empty() {
        program.to_string()
    } else {
        format!("{} {}", program, args.join(" "))
    };
    let mut child = Command::new(program)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| {
            if err.kind() == std::io::ErrorKind::NotFound {
                format!("{program} binary not found on PATH")
            } else {
                format!("{command_label} failed to start: {err}")
            }
        })?;
    let deadline = Instant::now() + Duration::from_millis(timeout_ms);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => {
                let output = child.wait_with_output().map_err(|err| err.to_string())?;
                if !output.status.success() {
                    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
                    return Err(if stderr.is_empty() {
                        format!("{command_label} exited {}", output.status)
                    } else {
                        stderr
                    });
                }
                return String::from_utf8(output.stdout).map_err(|err| err.to_string());
            }
            Ok(None) if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("{command_label} timed out after {timeout_ms}ms"));
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(10)),
            Err(err) => return Err(format!("{command_label} wait failed: {err}")),
        }
    }
}

fn aicx_health_signals_from_output(output: Result<String, String>) -> Vec<FleetHealthSignal> {
    let raw = match output {
        Ok(raw) => raw,
        Err(err) => {
            return vec![FleetHealthSignal {
                label: "aicx index".to_string(),
                status: FleetHealthStatus::Unknown,
                detail: err,
            }];
        }
    };
    let report = match serde_json::from_str::<serde_json::Value>(&raw) {
        Ok(report) => report,
        Err(err) => {
            return vec![FleetHealthSignal {
                label: "aicx index".to_string(),
                status: FleetHealthStatus::Unknown,
                detail: format!("invalid health JSON: {err}"),
            }];
        }
    };
    vec![aicx_health_signal_from_report(&report)]
}

fn aicx_health_signal_from_report(report: &serde_json::Value) -> FleetHealthSignal {
    let checks = aicx_relevant_checks(report);
    if checks.is_empty() {
        return FleetHealthSignal {
            label: "aicx index".to_string(),
            status: FleetHealthStatus::Unknown,
            detail: "health report contained no index/sidecar/extractor checks".to_string(),
        };
    }

    let mut status = FleetHealthStatus::Ok;
    let mut bad_details = Vec::new();
    for check in &checks {
        let check_status = aicx_check_status(check);
        status = worst_fleet_health_status(status, check_status);
        if check_status != FleetHealthStatus::Ok {
            bad_details.push(format!("{}: {}", check.name, check.detail));
        }
    }

    let detail = if bad_details.is_empty() {
        "index fresh; sidecars covered".to_string()
    } else {
        bad_details.truncate(2);
        bad_details.join("; ")
    };
    FleetHealthSignal {
        label: "aicx index".to_string(),
        status,
        detail,
    }
}

#[derive(Debug, Clone)]
struct AicxHealthCheck {
    name: String,
    severity: String,
    detail: String,
}

fn aicx_relevant_checks(report: &serde_json::Value) -> Vec<AicxHealthCheck> {
    let Some(entries) = report.as_object() else {
        return Vec::new();
    };
    entries
        .iter()
        .filter_map(|(key, value)| {
            let severity = value.get("severity")?.as_str()?.to_string();
            let name = value
                .get("name")
                .and_then(|value| value.as_str())
                .unwrap_or(key)
                .to_string();
            let detail = value
                .get("detail")
                .and_then(|value| value.as_str())
                .unwrap_or("")
                .to_string();
            let needle = format!("{key} {name} {detail}").to_ascii_lowercase();
            let relevant = needle.contains("index")
                || needle.contains("sidecar")
                || needle.contains("extract")
                || key == "canonical_store"
                || key == "state";
            relevant.then_some(AicxHealthCheck {
                name,
                severity,
                detail,
            })
        })
        .collect()
}

fn aicx_check_status(check: &AicxHealthCheck) -> FleetHealthStatus {
    let severity_status = match check.severity.to_ascii_lowercase().as_str() {
        "green" | "ok" | "healthy" | "success" => FleetHealthStatus::Ok,
        "warning" | "warn" | "yellow" | "degraded" => FleetHealthStatus::Warn,
        "red" | "error" | "critical" | "fail" | "failed" | "blocked" => FleetHealthStatus::Blocked,
        "skipped" | "notconfigured" | "unknown" => FleetHealthStatus::Unknown,
        _ => FleetHealthStatus::Unknown,
    };

    worst_fleet_health_status(severity_status, aicx_threshold_status(&check.detail))
}

fn aicx_threshold_status(detail: &str) -> FleetHealthStatus {
    let detail = detail.to_ascii_lowercase();
    if detail.contains("extract") && (detail.contains("fail") || detail.contains("error")) {
        return FleetHealthStatus::Blocked;
    }
    if detail.contains("missing")
        && (detail.contains("sidecar") || detail.contains("extract"))
        && first_number(&detail).is_some_and(|count| count > 0.0)
    {
        return FleetHealthStatus::Warn;
    }
    if detail.contains("lag")
        && let Some(hours) = first_number(&detail)
    {
        if hours > 72.0 {
            return FleetHealthStatus::Blocked;
        }
        if hours >= 24.0 {
            return FleetHealthStatus::Warn;
        }
    }
    FleetHealthStatus::Ok
}

fn first_number(text: &str) -> Option<f64> {
    text.split(|ch: char| !(ch.is_ascii_digit() || ch == '.'))
        .find_map(|token| {
            if token.is_empty() {
                None
            } else {
                token.parse::<f64>().ok()
            }
        })
}

fn worst_fleet_health_status(
    left: FleetHealthStatus,
    right: FleetHealthStatus,
) -> FleetHealthStatus {
    fn rank(status: FleetHealthStatus) -> u8 {
        match status {
            FleetHealthStatus::Ok => 0,
            FleetHealthStatus::Unknown => 1,
            FleetHealthStatus::Warn => 2,
            FleetHealthStatus::Blocked => 3,
        }
    }
    if rank(right) > rank(left) {
        right
    } else {
        left
    }
}

#[derive(Debug, Deserialize)]
struct TailscaleStatus {
    #[serde(rename = "Peer", default)]
    peers: BTreeMap<String, TailscalePeer>,
}

#[derive(Debug, Deserialize)]
struct TailscalePeer {
    #[serde(rename = "HostName", default)]
    host_name: String,
    #[serde(rename = "DNSName", default)]
    dns_name: String,
    #[serde(rename = "Online", default)]
    online: Option<bool>,
    #[serde(rename = "TailscaleIPs", default)]
    tailscale_ips: Vec<String>,
}

impl TailscalePeer {
    fn display_name(&self, fallback: &str) -> String {
        if !self.host_name.trim().is_empty() {
            return self.host_name.trim().to_string();
        }
        if !self.dns_name.trim().is_empty() {
            return self
                .dns_name
                .trim()
                .trim_end_matches('.')
                .split('.')
                .next()
                .unwrap_or(fallback)
                .to_string();
        }
        fallback.to_string()
    }

    fn match_keys(&self, display_name: &str) -> Vec<String> {
        let mut keys = vec![normalize_tailscale_name(display_name)];
        if !self.host_name.trim().is_empty() {
            keys.push(normalize_tailscale_name(&self.host_name));
        }
        if !self.dns_name.trim().is_empty() {
            keys.push(normalize_tailscale_name(&self.dns_name));
            if let Some(first_label) = self.dns_name.trim().trim_end_matches('.').split('.').next()
            {
                keys.push(normalize_tailscale_name(first_label));
            }
        }
        keys.sort();
        keys.dedup();
        keys
    }
}

fn normalize_tailscale_name(name: &str) -> String {
    name.trim()
        .trim_end_matches('.')
        .split('.')
        .next()
        .unwrap_or(name)
        .to_ascii_lowercase()
}

fn mcp_health_signals() -> Vec<FleetHealthSignal> {
    let process_scan = mcp_process_scan();
    let mut signals = Vec::new();
    let loctree_alive = process_scan
        .as_deref()
        .ok()
        .map(|scan| mcp_process_alive(scan, "loctree-mcp"));

    for (server, critical) in MCP_SERVERS {
        signals.push(mcp_server_signal(
            server,
            *critical,
            process_scan.as_deref(),
        ));
    }
    signals.push(loctree_snapshot_signal(loctree_alive));
    signals
}

fn mcp_server_signal(
    server: &str,
    critical: bool,
    process_scan: Result<&str, &String>,
) -> FleetHealthSignal {
    match process_scan {
        Ok(scan) if mcp_process_alive(scan, server) => FleetHealthSignal {
            label: format!("mcp {server}"),
            status: FleetHealthStatus::Ok,
            detail: "process alive".to_string(),
        },
        Ok(_) => FleetHealthSignal {
            label: format!("mcp {server}"),
            status: if critical {
                FleetHealthStatus::Blocked
            } else {
                FleetHealthStatus::Warn
            },
            detail: if critical {
                "critical process not found".to_string()
            } else {
                "non-critical process not found".to_string()
            },
        },
        Err(err) => FleetHealthSignal {
            label: format!("mcp {server}"),
            status: FleetHealthStatus::Unknown,
            detail: format!("process scan unavailable: {err}"),
        },
    }
}

fn loctree_snapshot_signal(loctree_alive: Option<bool>) -> FleetHealthSignal {
    if matches!(loctree_alive, Some(false)) {
        return FleetHealthSignal {
            label: "mcp loctree-mcp snapshot".to_string(),
            status: FleetHealthStatus::Blocked,
            detail: "loctree-mcp process not found; freshness untrusted".to_string(),
        };
    }
    if loctree_alive.is_none() {
        return FleetHealthSignal {
            label: "mcp loctree-mcp snapshot".to_string(),
            status: FleetHealthStatus::Unknown,
            detail: "process scan unavailable; freshness untrusted".to_string(),
        };
    }

    match loctree_snapshot_freshness() {
        Ok((true, head_label)) => FleetHealthSignal {
            label: "mcp loctree-mcp snapshot".to_string(),
            status: FleetHealthStatus::Ok,
            detail: format!("fresh vs git HEAD {head_label}"),
        },
        Ok((false, head_label)) => FleetHealthSignal {
            label: "mcp loctree-mcp snapshot".to_string(),
            status: FleetHealthStatus::Warn,
            detail: format!("snapshot stale vs git HEAD {head_label}"),
        },
        Err(err) => FleetHealthSignal {
            label: "mcp loctree-mcp snapshot".to_string(),
            status: FleetHealthStatus::Unknown,
            detail: err,
        },
    }
}

fn mcp_process_scan() -> Result<String, String> {
    if let Ok(raw) = env::var(MCP_PROCESS_SCAN_ENV) {
        return Ok(raw);
    }

    let output = Command::new("ps")
        .args(["-axo", "command="])
        .output()
        .map_err(|err| err.to_string())?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            "ps process scan failed".to_string()
        } else {
            stderr
        });
    }
    String::from_utf8(output.stdout).map_err(|err| err.to_string())
}

fn mcp_process_alive(process_scan: &str, server: &str) -> bool {
    process_scan
        .lines()
        .any(|line| process_line_mentions_server(line, server))
}

fn process_line_mentions_server(line: &str, server: &str) -> bool {
    let Some(command) = line.split_whitespace().next() else {
        return false;
    };
    let trimmed = command.trim_matches(|ch| matches!(ch, '"' | '\''));
    trimmed == server
        || Path::new(trimmed)
            .file_name()
            .and_then(|name| name.to_str())
            == Some(server)
}

fn loctree_snapshot_freshness() -> Result<(bool, String), String> {
    if let Ok(raw) = env::var(LOCTREE_SNAPSHOT_FRESHNESS_JSON_ENV) {
        let fixture = serde_json::from_str::<LoctreeSnapshotFreshnessFixture>(&raw)
            .map_err(|err| format!("invalid loctree snapshot fixture: {err}"))?;
        return Ok((fixture.fresh, fixture.head_label));
    }

    let cwd = env::current_dir().map_err(|err| err.to_string())?;
    let repo_root = find_git_root(&cwd).ok_or_else(|| "git root not found".to_string())?;
    let snapshot_path = repo_root.join(LOCTREE_CONTEXT_ATLAS_MANIFEST);
    let snapshot_modified = fs::metadata(&snapshot_path)
        .map_err(|err| format!("{}: {}", compact_home_path(&snapshot_path), err))?
        .modified()
        .map_err(|err| format!("{}: {}", compact_home_path(&snapshot_path), err))?;
    let (head_modified, head_label) = git_head_modified(&repo_root)?;
    Ok((snapshot_modified >= head_modified, head_label))
}

#[derive(Debug, Deserialize)]
struct LoctreeSnapshotFreshnessFixture {
    fresh: bool,
    head_label: String,
}

fn find_git_root(start: &Path) -> Option<PathBuf> {
    let mut candidate = start;
    loop {
        if candidate.join(".git").exists() {
            return Some(candidate.to_path_buf());
        }
        candidate = candidate.parent()?;
    }
}

fn git_head_modified(repo_root: &Path) -> Result<(SystemTime, String), String> {
    let git_dir = git_dir(repo_root)?;
    let head_path = git_dir.join("HEAD");
    let head_raw = fs::read_to_string(&head_path)
        .map_err(|err| format!("{}: {}", head_path.display(), err))?;
    let head = head_raw.trim();
    if let Some(reference) = head.strip_prefix("ref: ") {
        let ref_path = git_dir.join(reference);
        let modified = fs::metadata(&ref_path)
            .or_else(|_| fs::metadata(&head_path))
            .map_err(|err| format!("{}: {}", ref_path.display(), err))?
            .modified()
            .map_err(|err| format!("{}: {}", ref_path.display(), err))?;
        return Ok((modified, reference.to_string()));
    }
    let modified = fs::metadata(&head_path)
        .map_err(|err| format!("{}: {}", head_path.display(), err))?
        .modified()
        .map_err(|err| format!("{}: {}", head_path.display(), err))?;
    Ok((modified, head.chars().take(12).collect()))
}

fn git_dir(repo_root: &Path) -> Result<PathBuf, String> {
    let dot_git = repo_root.join(".git");
    if dot_git.is_dir() {
        return Ok(dot_git);
    }
    let raw =
        fs::read_to_string(&dot_git).map_err(|err| format!("{}: {}", dot_git.display(), err))?;
    let path = raw
        .trim()
        .strip_prefix("gitdir: ")
        .ok_or_else(|| format!("{} is not a gitdir pointer", dot_git.display()))?;
    let git_dir = PathBuf::from(path);
    if git_dir.is_absolute() {
        Ok(git_dir)
    } else {
        Ok(repo_root.join(git_dir))
    }
}

fn disk_health_signals() -> Vec<FleetHealthSignal> {
    if let Ok(raw) = env::var(DISK_HEALTH_JSON_ENV) {
        return disk_health_signals_from_fixture(&raw);
    }

    disk_health_signals_from_paths(&substrate_disk_paths())
}

fn disk_health_signals_from_paths(
    substrates: &[(&'static str, PathBuf)],
) -> Vec<FleetHealthSignal> {
    let mut signals = Vec::new();
    for (label, path) in substrates {
        signals.push(disk_path_signal(label, path));
    }
    signals.push(ulimit_fsize_signal(substrates));
    signals
}

fn disk_health_signals_from_fixture(raw: &str) -> Vec<FleetHealthSignal> {
    let fixture = match serde_json::from_str::<DiskHealthFixture>(raw) {
        Ok(fixture) => fixture,
        Err(err) => {
            return vec![FleetHealthSignal {
                label: "disk fixture".to_string(),
                status: FleetHealthStatus::Unknown,
                detail: format!("invalid disk fixture: {err}"),
            }];
        }
    };

    let mut signals = fixture
        .paths
        .iter()
        .map(|path| {
            disk_path_signal_from_stats(
                &path.label,
                DiskStats {
                    free_bytes: path.free_bytes,
                    total_bytes: path.total_bytes,
                },
            )
        })
        .collect::<Vec<_>>();
    signals.push(ulimit_fsize_signal_from_fixture(&fixture));
    signals
}

fn substrate_disk_paths() -> Vec<(&'static str, PathBuf)> {
    let vibecrafted_home = crate::config::default_vibecrafted_home();
    let mut paths = vec![
        (
            "disk ~/.vibecrafted/control_plane",
            vibecrafted_home.join("control_plane"),
        ),
        (
            "disk ~/.vibecrafted/artifacts",
            vibecrafted_home.join("artifacts"),
        ),
    ];
    if let Some(home) = home_dir() {
        paths.insert(1, ("disk ~/.codex", home.join(".codex")));
        paths.insert(2, ("disk ~/.aicx", home.join(".aicx")));
    }
    paths
}

fn home_dir() -> Option<PathBuf> {
    env::var_os("HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

fn existing_probe_path(path: &Path) -> PathBuf {
    let mut candidate = path;
    loop {
        if candidate.exists() {
            return candidate.to_path_buf();
        }
        let Some(parent) = candidate.parent() else {
            return path.to_path_buf();
        };
        candidate = parent;
    }
}

fn disk_path_signal(label: &'static str, path: &Path) -> FleetHealthSignal {
    match statvfs_probe(&existing_probe_path(path)) {
        Ok(stats) => disk_path_signal_from_stats(label, stats),
        Err(err) => FleetHealthSignal {
            label: label.to_string(),
            status: FleetHealthStatus::Unknown,
            detail: format!("{}: {}", path.display(), err),
        },
    }
}

fn disk_path_signal_from_stats(label: &str, stats: DiskStats) -> FleetHealthSignal {
    let free_percent = stats.free_percent();
    let status = if free_percent < DISK_BLOCKED_FREE_PERCENT {
        FleetHealthStatus::Blocked
    } else if free_percent <= DISK_WARN_FREE_PERCENT {
        FleetHealthStatus::Warn
    } else {
        FleetHealthStatus::Ok
    };
    FleetHealthSignal {
        label: label.to_string(),
        status,
        detail: format!(
            "{free_percent:.1}% free ({}/{})",
            format_bytes(stats.free_bytes),
            format_bytes(stats.total_bytes)
        ),
    }
}

fn ulimit_fsize_signal(substrates: &[(&'static str, PathBuf)]) -> FleetHealthSignal {
    match rlimit_fsize_bytes() {
        Ok(cap_bytes) => {
            ulimit_fsize_signal_from_inputs(cap_bytes, largest_tracked_file(substrates))
        }
        Err(err) => FleetHealthSignal {
            label: "ulimit -f".to_string(),
            status: FleetHealthStatus::Unknown,
            detail: err,
        },
    }
}

fn ulimit_fsize_signal_from_fixture(fixture: &DiskHealthFixture) -> FleetHealthSignal {
    let largest = fixture
        .largest_tracked_file
        .as_ref()
        .map(|file| TrackedFile {
            label: file.label.clone(),
            size_bytes: file.size_bytes,
        });
    if fixture.ulimit_unlimited {
        ulimit_fsize_signal_from_inputs(None, largest)
    } else if let Some(cap_bytes) = fixture.ulimit_fsize_bytes {
        ulimit_fsize_signal_from_inputs(Some(cap_bytes), largest)
    } else {
        FleetHealthSignal {
            label: "ulimit -f".to_string(),
            status: FleetHealthStatus::Unknown,
            detail: "disk fixture missing ulimit_fsize_bytes or ulimit_unlimited".to_string(),
        }
    }
}

fn ulimit_fsize_signal_from_inputs(
    cap_bytes: Option<u64>,
    largest: Option<TrackedFile>,
) -> FleetHealthSignal {
    match cap_bytes {
        None => FleetHealthSignal {
            label: "ulimit -f".to_string(),
            status: FleetHealthStatus::Ok,
            detail: "unlimited".to_string(),
        },
        Some(cap_bytes) => {
            let blocked_threshold = ulimit_blocked_threshold_bytes();
            let file_ratio = largest
                .as_ref()
                .map(|file| file.size_bytes as f64 / cap_bytes.max(1) as f64 * 100.0)
                .unwrap_or(0.0);
            let status = if cap_bytes <= blocked_threshold || file_ratio >= TRACKED_FILE_CAP_PERCENT
            {
                FleetHealthStatus::Blocked
            } else {
                FleetHealthStatus::Warn
            };
            let mut detail = format!(
                "finite {} blk ({})",
                cap_bytes / 512,
                format_bytes(cap_bytes)
            );
            if let Some(file) = largest {
                detail.push_str(&format!(
                    "; largest {} {} ({file_ratio:.0}%)",
                    file.label,
                    format_bytes(file.size_bytes)
                ));
            }
            FleetHealthSignal {
                label: "ulimit -f".to_string(),
                status,
                detail,
            }
        }
    }
}

fn ulimit_blocked_threshold_bytes() -> u64 {
    let mb = env::var(ULIMIT_FSIZE_BLOCKED_MB_ENV)
        .ok()
        .and_then(|raw| raw.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(ULIMIT_FSIZE_BLOCKED_MB);
    mb.saturating_mul(1024 * 1024)
}

#[derive(Debug, Clone, Copy)]
struct DiskStats {
    free_bytes: u64,
    total_bytes: u64,
}

impl DiskStats {
    fn free_percent(self) -> f64 {
        if self.total_bytes == 0 {
            return 0.0;
        }
        self.free_bytes as f64 / self.total_bytes as f64 * 100.0
    }
}

#[derive(Debug, Deserialize)]
struct DiskHealthFixture {
    paths: Vec<DiskPathFixture>,
    #[serde(default)]
    ulimit_unlimited: bool,
    #[serde(default)]
    ulimit_fsize_bytes: Option<u64>,
    #[serde(default)]
    largest_tracked_file: Option<TrackedFileFixture>,
}

#[derive(Debug, Deserialize)]
struct DiskPathFixture {
    label: String,
    free_bytes: u64,
    total_bytes: u64,
}

#[derive(Debug, Deserialize)]
struct TrackedFileFixture {
    label: String,
    size_bytes: u64,
}

#[derive(Debug, Clone)]
struct TrackedFile {
    label: String,
    size_bytes: u64,
}

fn largest_tracked_file(substrates: &[(&'static str, PathBuf)]) -> Option<TrackedFile> {
    let mut scanned = 0usize;
    let mut largest = None;
    for (_, root) in substrates {
        scan_tracked_files(root, &mut scanned, &mut largest);
        if scanned >= TRACKED_FILE_SCAN_CAP {
            break;
        }
    }
    largest
}

fn scan_tracked_files(path: &Path, scanned: &mut usize, largest: &mut Option<TrackedFile>) {
    if *scanned >= TRACKED_FILE_SCAN_CAP {
        return;
    }
    let Ok(entries) = fs::read_dir(path) else {
        return;
    };
    for entry in entries.flatten() {
        if *scanned >= TRACKED_FILE_SCAN_CAP {
            return;
        }
        *scanned += 1;
        let path = entry.path();
        let Ok(metadata) = entry.metadata() else {
            continue;
        };
        if metadata.is_dir() {
            scan_tracked_files(&path, scanned, largest);
            continue;
        }
        if !metadata.is_file() || !is_tracked_log_or_db(&path) {
            continue;
        }
        let size_bytes = metadata.len();
        let is_larger = match largest.as_ref() {
            Some(current) => size_bytes > current.size_bytes,
            None => true,
        };
        if is_larger {
            *largest = Some(TrackedFile {
                label: compact_home_path(&path),
                size_bytes,
            });
        }
    }
}

fn is_tracked_log_or_db(path: &Path) -> bool {
    let Some(ext) = path.extension().and_then(|value| value.to_str()) else {
        return false;
    };
    matches!(
        ext.to_ascii_lowercase().as_str(),
        "db" | "jsonl" | "log" | "sqlite" | "sqlite3"
    )
}

fn compact_home_path(path: &Path) -> String {
    if let Some(home) = home_dir()
        && let Ok(stripped) = path.strip_prefix(home)
    {
        return format!("~/{}", stripped.display());
    }
    path.display().to_string()
}

fn format_bytes(bytes: u64) -> String {
    const KIB: f64 = 1024.0;
    const MIB: f64 = KIB * 1024.0;
    const GIB: f64 = MIB * 1024.0;
    const TIB: f64 = GIB * 1024.0;
    let bytes_f = bytes as f64;
    if bytes_f >= TIB {
        format!("{:.1} TiB", bytes_f / TIB)
    } else if bytes_f >= GIB {
        format!("{:.1} GiB", bytes_f / GIB)
    } else if bytes_f >= MIB {
        format!("{:.1} MiB", bytes_f / MIB)
    } else if bytes_f >= KIB {
        format!("{:.1} KiB", bytes_f / KIB)
    } else {
        format!("{bytes} B")
    }
}

#[cfg(unix)]
fn statvfs_probe(path: &Path) -> Result<DiskStats, String> {
    unix_probe::statvfs_probe(path)
}

#[cfg(not(unix))]
fn statvfs_probe(_path: &Path) -> Result<DiskStats, String> {
    Err("statvfs unavailable on this platform".to_string())
}

#[cfg(unix)]
fn rlimit_fsize_bytes() -> Result<Option<u64>, String> {
    unix_probe::rlimit_fsize_bytes()
}

#[cfg(not(unix))]
fn rlimit_fsize_bytes() -> Result<Option<u64>, String> {
    Err("getrlimit unavailable on this platform".to_string())
}

#[cfg(unix)]
mod unix_probe {
    use super::DiskStats;
    use std::ffi::CString;
    use std::mem::MaybeUninit;
    use std::os::unix::ffi::OsStrExt;
    use std::path::Path;

    pub fn statvfs_probe(path: &Path) -> Result<DiskStats, String> {
        let raw_path = CString::new(path.as_os_str().as_bytes())
            .map_err(|_| "path contains an interior nul byte".to_string())?;
        let mut stats = MaybeUninit::<libc::statvfs>::uninit();
        // SAFETY: `raw_path` is a nul-terminated C string and `stats` points to
        // writable memory for the kernel to initialize.
        let code = unsafe { libc::statvfs(raw_path.as_ptr(), stats.as_mut_ptr()) };
        if code != 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        // SAFETY: `statvfs` returned success, so the struct has been filled.
        let stats = unsafe { stats.assume_init() };
        let block_size = if stats.f_frsize > 0 {
            stats.f_frsize
        } else {
            stats.f_bsize
        };
        Ok(DiskStats {
            free_bytes: (stats.f_bavail as u64).saturating_mul(block_size),
            total_bytes: (stats.f_blocks as u64).saturating_mul(block_size),
        })
    }

    pub fn rlimit_fsize_bytes() -> Result<Option<u64>, String> {
        let mut limit = MaybeUninit::<libc::rlimit>::uninit();
        // SAFETY: `limit` points to writable memory and RLIMIT_FSIZE is the
        // platform resource id for the inherited file-size soft cap.
        let code = unsafe { libc::getrlimit(libc::RLIMIT_FSIZE, limit.as_mut_ptr()) };
        if code != 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        // SAFETY: `getrlimit` returned success, so the struct has been filled.
        let limit = unsafe { limit.assume_init() };
        if limit.rlim_cur == libc::RLIM_INFINITY {
            Ok(None)
        } else {
            Ok(Some(limit.rlim_cur))
        }
    }
}

fn action_queue_from_inputs(
    state: &ControlPlaneState,
    failures: &[FailureEntry],
    records: &[MetaRecord],
    intents: &[PolarizeIntent],
    now: DateTime<Utc>,
) -> Vec<ActionQueueItem> {
    let mut items = Vec::new();

    for snapshot in &state.runs {
        let kind = classify_run(snapshot, now);
        if matches!(kind, RunKind::Stalled) {
            items.push(ActionQueueItem {
                kind: ActionQueueKind::StalledRun,
                summary: format!(
                    "resume {} ({})",
                    snapshot.run_id,
                    snapshot.agent.as_deref().unwrap_or("unknown")
                ),
                source_path: snapshot
                    .latest_report
                    .as_deref()
                    .map(PathBuf::from)
                    .or_else(|| snapshot.root.as_deref().map(PathBuf::from)),
                priority: ActionPriority::High,
            });
        }
    }

    for failure in failures {
        items.push(ActionQueueItem {
            kind: ActionQueueKind::Failure,
            summary: format!(
                "investigate {} ({} / {})",
                failure.run_id, failure.agent, failure.skill
            ),
            source_path: failure.source_path.clone(),
            priority: ActionPriority::Critical,
        });
    }

    for intent in intents {
        items.push(ActionQueueItem {
            kind: ActionQueueKind::Polarize,
            summary: format!(
                "polarize {} ({} / score {})",
                intent.run_id,
                intent.band.label(),
                intent.score
            ),
            source_path: Some(intent.prism_path.clone()),
            priority: action_priority_from_polarize_band(intent.band),
        });
    }

    // Surface freshly completed reports that haven't been touched yet —
    // operators want to know which artifacts are ready to read without
    // grepping the artifact tree. We cap to keep the queue actionable.
    let mut recent_reports = records
        .iter()
        .filter(|record| matches!(record.meta.exit_code, Some(0)))
        .filter(|record| record.meta.report.is_some())
        .filter(|record| now.signed_duration_since(record.completed_at).num_hours() < 12)
        .collect::<Vec<_>>();
    recent_reports.sort_by_key(|record| std::cmp::Reverse(record.completed_at));
    for record in recent_reports.into_iter().take(5) {
        items.push(ActionQueueItem {
            kind: ActionQueueKind::ReportReady,
            summary: format!(
                "open report {} ({})",
                record.meta.run_id.as_deref().unwrap_or("unknown"),
                record.meta.agent.as_deref().unwrap_or("unknown")
            ),
            source_path: record.meta.report.clone().map(PathBuf::from),
            priority: ActionPriority::Normal,
        });
    }

    items.sort_by_key(|item| item.priority);
    items.truncate(12);
    items
}

fn action_priority_from_polarize_band(band: PolarizeBand) -> ActionPriority {
    match band {
        PolarizeBand::Abort | PolarizeBand::Doctrine => ActionPriority::Critical,
        PolarizeBand::Pass => ActionPriority::High,
        PolarizeBand::Memo => ActionPriority::Normal,
    }
}

fn parse_rfc3339(raw: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(raw)
        .ok()
        .map(|ts| ts.with_timezone(&Utc))
}

fn relative_age(ts: DateTime<Utc>, now: DateTime<Utc>) -> String {
    let delta = now.signed_duration_since(ts);
    let minutes = delta.num_minutes();
    if minutes < 1 {
        return "just now".to_string();
    }
    if minutes < 60 {
        return format!("{minutes}m ago");
    }
    let hours = delta.num_hours();
    if hours < 24 {
        return format!("{hours}h ago");
    }
    let days = delta.num_days();
    format!("{days}d ago")
}

/// Default location for canonical artifact metadata. Resolves the
/// operator's `VIBECRAFTED_HOME` (or `~/.vibecrafted`) and points at the
/// `artifacts/` subtree where every dispatched skill writes its
/// `*.meta.json`.
pub fn default_artifact_root() -> PathBuf {
    crate::config::default_vibecrafted_home().join("artifacts")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::{ControlPlaneState, RunEvent, RunSnapshot};
    use std::collections::HashMap;
    use tempfile::tempdir;

    fn ts(value: &str) -> DateTime<Utc> {
        DateTime::parse_from_rfc3339(value)
            .unwrap()
            .with_timezone(&Utc)
    }

    fn empty_state(root: &Path) -> ControlPlaneState {
        ControlPlaneState {
            root: root.to_path_buf(),
            retained_runs: Vec::new(),
            runs: Vec::new(),
            events: Vec::new(),
            archived_run_ids: Default::default(),
        }
    }

    fn write_meta(path: &Path, contents: &str) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, contents).unwrap();
    }

    #[test]
    fn missing_artifact_root_reports_typed_empty_state() {
        let now = ts("2026-05-20T00:00:00Z");
        let dir = tempdir().unwrap();
        let state = empty_state(dir.path());
        let mission = MissionControlState::build_at(&state, &dir.path().join("missing"), now);
        assert!(
            mission.is_empty()
                || mission
                    .fleet_health
                    .iter()
                    .any(|s| s.label == "artifact-root")
        );
        let artifact_signal = mission
            .fleet_health
            .iter()
            .find(|signal| signal.label == "artifact-root")
            .expect("artifact-root signal");
        assert_eq!(artifact_signal.status, FleetHealthStatus::Warn);
        assert_eq!(mission.data_quality.scanned_meta_files, 0);
    }

    #[test]
    fn aggregates_per_agent_and_skill_from_meta_json() {
        let dir = tempdir().unwrap();
        let artifact = dir.path().join("artifacts");
        let bucket = artifact.join("vetcoders/vc-tui/2026_0519/reports");
        write_meta(
            &bucket.join("run-a.meta.json"),
            r#"{
                "run_id": "run-a",
                "agent": "claude",
                "skill_code": "owne",
                "exit_code": 0,
                "model": "claude-opus-4-7",
                "duration_s": 120.5,
                "completed_at": "2026-05-19T10:00:00Z",
                "prompt_id": "wave-1",
                "report": "/tmp/report-a.md"
            }"#,
        );
        write_meta(
            &bucket.join("run-b.meta.json"),
            r#"{
                "run_id": "run-b",
                "agent": "claude",
                "skill_code": "owne",
                "exit_code": 1,
                "model": "unknown",
                "duration_s": null,
                "completed_at": "2026-05-19T11:00:00Z",
                "prompt_id": "wave-1"
            }"#,
        );
        write_meta(
            &bucket.join("run-c.meta.json"),
            r#"{
                "run_id": "run-c",
                "agent": "codex",
                "skill_code": "marb",
                "exit_code": 0,
                "model": "gpt-5-codex",
                "duration_s": 60.0,
                "completed_at": "2026-05-19T12:00:00Z",
                "prompt_id": "wave-2"
            }"#,
        );

        let now = ts("2026-05-19T13:00:00Z");
        let state = empty_state(dir.path());
        let mission = MissionControlState::build_at(&state, &artifact, now);

        assert_eq!(mission.data_quality.scanned_meta_files, 3);
        assert_eq!(mission.data_quality.missing_model, 1);
        assert_eq!(mission.data_quality.missing_duration, 1);

        let claude = mission
            .agent_stats
            .iter()
            .find(|row| row.agent == "claude")
            .expect("claude row present");
        assert_eq!(claude.total_runs, 2);
        assert_eq!(claude.completed, 1);
        assert_eq!(claude.failed, 1);
        assert!((claude.success_rate - 0.5).abs() < 1e-3);
        assert!(claude.avg_duration_s.is_some());
        assert!((claude.model_known_rate - 0.5).abs() < 1e-3);

        let codex = mission
            .agent_stats
            .iter()
            .find(|row| row.agent == "codex")
            .expect("codex row present");
        assert_eq!(codex.total_runs, 1);
        assert!((codex.success_rate - 1.0).abs() < 1e-3);

        let owne = mission
            .skill_stats
            .iter()
            .find(|row| row.skill == "owne")
            .expect("owne skill row present");
        assert_eq!(owne.invocations, 2);
        assert_eq!(owne.failed, 1);

        // Wave atlas should surface the prompt_id groups.
        let wave1 = mission
            .wave_atlas
            .iter()
            .find(|seg| seg.wave_id == "wave-1")
            .expect("wave-1 segment");
        assert_eq!(wave1.total, 2);
        assert_eq!(wave1.completed, 1);
        assert_eq!(wave1.failed, 1);
    }

    #[test]
    fn wave_atlas_counts_each_run_once_when_exit_code_and_status_agree() {
        let dir = tempdir().unwrap();
        let artifact = dir.path().join("artifacts");
        let bucket = artifact.join("vetcoders/vc-tui/2026_0519/reports");
        // The shape every launcher meta.json actually emits: BOTH the
        // exit_code and the status field carry the same outcome.
        write_meta(
            &bucket.join("run-fail.meta.json"),
            r#"{
                "run_id": "run-fail",
                "agent": "codex",
                "skill_code": "marb",
                "exit_code": 1,
                "status": "failed",
                "completed_at": "2026-05-19T11:00:00Z",
                "prompt_id": "wave-dup"
            }"#,
        );
        write_meta(
            &bucket.join("run-ok.meta.json"),
            r#"{
                "run_id": "run-ok",
                "agent": "claude",
                "skill_code": "impl",
                "exit_code": 0,
                "status": "completed",
                "completed_at": "2026-05-19T10:00:00Z",
                "prompt_id": "wave-dup"
            }"#,
        );

        let now = ts("2026-05-19T13:00:00Z");
        let state = empty_state(dir.path());
        let mission = MissionControlState::build_at(&state, &artifact, now);

        let wave = mission
            .wave_atlas
            .iter()
            .find(|seg| seg.wave_id == "wave-dup")
            .expect("wave-dup segment");
        assert_eq!(wave.total, 2);
        assert_eq!(
            wave.failed, 1,
            "a run with exit_code!=0 AND status=failed must count as one failure"
        );
        assert_eq!(
            wave.completed, 1,
            "a run with exit_code=0 AND status=completed must count as one completion"
        );
    }

    #[test]
    fn failure_board_buckets_within_24h_window() {
        let dir = tempdir().unwrap();
        let artifact = dir.path().join("artifacts");
        let bucket = artifact.join("vetcoders/vc-tui/2026_0519/reports");
        write_meta(
            &bucket.join("recent-fail.meta.json"),
            r#"{
                "run_id": "recent-fail",
                "agent": "gemini",
                "skill_code": "rev",
                "exit_code": 2,
                "status": "failed",
                "completed_at": "2026-05-19T12:30:00Z"
            }"#,
        );
        write_meta(
            &bucket.join("old-fail.meta.json"),
            r#"{
                "run_id": "old-fail",
                "agent": "gemini",
                "skill_code": "rev",
                "exit_code": 1,
                "status": "failed",
                "completed_at": "2026-05-15T08:00:00Z"
            }"#,
        );

        let now = ts("2026-05-19T13:00:00Z");
        let state = empty_state(dir.path());
        let mission = MissionControlState::build_at(&state, &artifact, now);
        assert_eq!(mission.failures.len(), 1);
        assert_eq!(mission.failures[0].run_id, "recent-fail");
    }

    #[test]
    fn failure_board_excludes_exit_zero_with_stale_last_error() {
        // Success evidence on the snapshot must not feed the failure board
        // even when the watchdog left last_error / recovery_required.
        let dir = tempdir().unwrap();
        let artifact = dir.path().join("artifacts");
        let mut extra = HashMap::new();
        extra.insert("exit_code".into(), serde_json::json!(0));
        extra.insert(
            "completed_at".into(),
            serde_json::json!("2026-05-19T03:12:57Z"),
        );
        extra.insert("health".into(), serde_json::json!("final"));
        extra.insert("liveness".into(), serde_json::json!("terminal"));

        let mut state = empty_state(dir.path());
        state.runs.push(RunSnapshot {
            run_id: "work-false-fail".to_string(),
            session_id: None,
            agent: Some("grok".to_string()),
            skill: Some("workflow".to_string()),
            mode: Some("workflow".to_string()),
            state: Some("completed".to_string()),
            status: Some("completed".to_string()),
            started_at: Some("2026-05-19T03:00:00Z".to_string()),
            // Fresh updated_at restamp — must not put this on the failure board.
            updated_at: Some("2026-05-19T12:55:00Z".to_string()),
            last_heartbeat: None,
            root: None,
            operator_session: None,
            latest_report: Some("/tmp/report.md".to_string()),
            latest_transcript: None,
            last_error: Some("launcher_pid gone; heartbeat stale; recovery_required".to_string()),
            extra,
        });

        // Real failure: age should follow completed_at, not a restamped updated_at.
        // completed_at is 12h old (inside the 24h board window); updated_at is
        // restamped to 10m ago — the bug that made day-old fails look fresh.
        let mut fail_extra = HashMap::new();
        fail_extra.insert("exit_code".into(), serde_json::json!(2));
        fail_extra.insert(
            "completed_at".into(),
            serde_json::json!("2026-05-19T01:00:00Z"),
        );
        state.runs.push(RunSnapshot {
            run_id: "real-fail".to_string(),
            session_id: None,
            agent: Some("codex".to_string()),
            skill: Some("impl".to_string()),
            mode: None,
            state: Some("failed".to_string()),
            status: Some("failed".to_string()),
            started_at: Some("2026-05-19T00:30:00Z".to_string()),
            updated_at: Some("2026-05-19T12:50:00Z".to_string()),
            last_heartbeat: None,
            root: None,
            operator_session: None,
            latest_report: None,
            latest_transcript: None,
            last_error: Some("nonzero exit".to_string()),
            extra: fail_extra,
        });

        let now = ts("2026-05-19T13:00:00Z");
        let mission = MissionControlState::build_at(&state, &artifact, now);
        assert!(
            mission
                .failures
                .iter()
                .all(|entry| entry.run_id != "work-false-fail"),
            "exit 0 + completed_at must not appear on failure board: {:?}",
            mission
                .failures
                .iter()
                .map(|e| e.run_id.as_str())
                .collect::<Vec<_>>()
        );
        let real = mission
            .failures
            .iter()
            .find(|entry| entry.run_id == "real-fail")
            .expect("real failure stays on board");
        assert_eq!(
            real.age_label, "12h ago",
            "failure age must use completed_at, not restamped updated_at"
        );
    }

    #[test]
    fn failure_board_keeps_freshest_and_dedups_run_ids() {
        let dir = tempdir().unwrap();
        let artifact = dir.path().join("artifacts");
        let bucket = artifact.join("vetcoders/vc-tui/2026_0519/reports");
        // 22 failures at 11h old: lexicographic age_label sort ("11h ago" <
        // "2m ago") used to keep exactly these and evict the fresh one.
        for index in 0..22 {
            write_meta(
                &bucket.join(format!("stale-{index}.meta.json")),
                &format!(
                    r#"{{
                        "run_id": "stale-{index}",
                        "agent": "codex",
                        "skill_code": "impl",
                        "exit_code": 1,
                        "status": "failed",
                        "completed_at": "2026-05-19T02:00:00Z"
                    }}"#
                ),
            );
        }
        write_meta(
            &bucket.join("fresh-fail.meta.json"),
            r#"{
                "run_id": "fresh-fail",
                "agent": "gemini",
                "skill_code": "rvew",
                "exit_code": 2,
                "status": "failed",
                "completed_at": "2026-05-19T12:58:00Z"
            }"#,
        );
        // Duplicate of a meta-derived failure arriving via a retained
        // snapshot must not occupy a second board row.
        let mut state = empty_state(dir.path());
        state.runs.push(RunSnapshot {
            run_id: "fresh-fail".to_string(),
            session_id: None,
            agent: Some("gemini".to_string()),
            skill: Some("rvew".to_string()),
            mode: None,
            state: Some("failed".to_string()),
            status: None,
            started_at: None,
            updated_at: Some("2026-05-19T12:58:30Z".to_string()),
            last_heartbeat: None,
            root: None,
            operator_session: None,
            latest_report: None,
            latest_transcript: None,
            last_error: Some("duplicate snapshot".to_string()),
            extra: HashMap::new(),
        });

        let now = ts("2026-05-19T13:00:00Z");
        let mission = MissionControlState::build_at(&state, &artifact, now);
        assert_eq!(mission.failures.len(), 20, "board stays capped at 20");
        assert_eq!(
            mission.failures[0].run_id, "fresh-fail",
            "freshest failure must lead the board, not be evicted by lexicographic age labels"
        );
        assert_eq!(
            mission
                .failures
                .iter()
                .filter(|entry| entry.run_id == "fresh-fail")
                .count(),
            1,
            "meta-derived and snapshot-derived rows for one run must dedup"
        );
    }

    #[test]
    fn active_dispatches_split_stalled_into_action_queue() {
        let now = ts("2026-05-19T13:00:00Z");
        let active = RunSnapshot {
            run_id: "live".to_string(),
            session_id: None,
            agent: Some("claude".to_string()),
            skill: Some("workflow".to_string()),
            mode: None,
            state: Some("active".to_string()),
            status: None,
            started_at: Some("2026-05-19T12:50:00Z".to_string()),
            updated_at: Some("2026-05-19T12:59:00Z".to_string()),
            last_heartbeat: Some("2026-05-19T12:59:30Z".to_string()),
            root: None,
            operator_session: None,
            latest_report: None,
            latest_transcript: None,
            last_error: None,
            extra: HashMap::new(),
        };
        let stalled = RunSnapshot {
            run_id: "lost".to_string(),
            session_id: None,
            agent: Some("codex".to_string()),
            skill: Some("workflow".to_string()),
            mode: None,
            state: Some("active".to_string()),
            status: None,
            started_at: Some("2026-05-19T10:00:00Z".to_string()),
            updated_at: Some("2026-05-19T10:30:00Z".to_string()),
            last_heartbeat: Some("2026-05-19T10:30:00Z".to_string()),
            root: Some("/tmp/lost".to_string()),
            operator_session: None,
            latest_report: None,
            latest_transcript: None,
            last_error: None,
            extra: HashMap::new(),
        };
        let state = ControlPlaneState {
            root: PathBuf::from("/tmp/state"),
            retained_runs: vec![active.clone(), stalled.clone()],
            runs: vec![active, stalled],
            events: Vec::<RunEvent>::new(),
            archived_run_ids: Default::default(),
        };
        let dir = tempdir().unwrap();
        let mission =
            MissionControlState::build_at(&state, &dir.path().join("missing-artifacts"), now);
        assert_eq!(mission.active_dispatches.len(), 1);
        assert_eq!(mission.active_dispatches[0].run_id, "live");
        assert!(
            mission
                .action_queue
                .iter()
                .any(|item| item.kind == ActionQueueKind::StalledRun
                    && item.summary.contains("lost"))
        );
    }

    fn run_with_settlement(
        run_id: &str,
        state: &str,
        verdict: Option<&str>,
        exit_code: Option<i64>,
    ) -> RunSnapshot {
        let mut extra = HashMap::new();
        if let Some(v) = verdict {
            extra.insert(
                "settlement_verdict".to_string(),
                Value::String(v.to_string()),
            );
        }
        if let Some(code) = exit_code {
            extra.insert("exit_code".to_string(), Value::from(code));
        }
        RunSnapshot {
            run_id: run_id.to_string(),
            session_id: None,
            agent: Some("claude".to_string()),
            skill: Some("implement".to_string()),
            mode: None,
            state: Some(state.to_string()),
            status: None,
            started_at: None,
            updated_at: None,
            last_heartbeat: None,
            root: None,
            operator_session: None,
            latest_report: None,
            latest_transcript: None,
            last_error: None,
            extra,
        }
    }

    #[test]
    fn settlement_board_counts_fxn_and_unsettled_terminal_fallback() {
        // Mirrors SettlementBoard::from_snapshots: f/x/n + invalid-in-x +
        // unsettled terminal → n; live unsettled ignored.
        let runs = vec![
            run_with_settlement("f1", "report_validated", Some("finalized"), Some(0)),
            run_with_settlement("x1", "failed", Some("failed"), Some(1)),
            run_with_settlement("inv", "failed", Some("invalid"), Some(1)),
            run_with_settlement("n1", "report_validated", Some("needs_attention"), Some(0)),
            // terminal, no verdict → n
            run_with_settlement("n2", "completed", None, Some(0)),
            // live, no verdict → ignored
            run_with_settlement("live", "running", None, None),
        ];
        let board = SettlementBoardCounts::from_snapshots(&runs, 1, 2);
        assert_eq!(board.f, 1);
        assert_eq!(board.x, 2); // failed + invalid
        assert_eq!(board.invalid, 1);
        assert_eq!(board.n, 2); // needs_attention + unsettled terminal
        assert_eq!(board.unclassified, 1); // live snapshot without a verdict
        assert_eq!(board.total_settled, 5);
        assert_eq!(board.active, 1);
        assert_eq!(board.stalled, 2);
        assert_eq!(board.orphans, 0);
        assert!(board.scope.contains("retained"));
        let strip = board.render_strip();
        assert!(strip.contains("f=1"));
        assert!(strip.contains("x=2"));
        assert!(strip.contains("n=2"));
        assert!(strip.contains("unclassified=1"));
        assert!(strip.contains("active=1"));
        assert!(strip.contains("stalled=2"));
        assert!(strip.contains("orphans=0"));
    }

    #[test]
    fn settlement_board_uses_all_retained_snapshots_and_reports_orphans() {
        let dir = tempdir().unwrap();
        let artifact_root = dir.path().join("artifacts");
        fs::create_dir_all(artifact_root.join("nested")).unwrap();
        fs::write(artifact_root.join("Untitled.md"), "legacy\n").unwrap();
        fs::write(
            artifact_root.join("nested/Untitled report.MD"),
            "legacy nested\n",
        )
        .unwrap();
        fs::write(artifact_root.join("nested/titled.md"), "not orphan\n").unwrap();

        let archived_finalized =
            run_with_settlement("archived-f", "report_validated", Some("finalized"), Some(0));
        let state = ControlPlaneState {
            root: dir.path().join("control-plane"),
            retained_runs: vec![archived_finalized],
            runs: Vec::new(),
            events: Vec::new(),
            archived_run_ids: Default::default(),
        };
        let board =
            MissionControlState::build_at(&state, &artifact_root, ts("2026-05-20T00:00:00Z"))
                .settlement;

        assert_eq!(board.f, 1);
        assert_eq!(board.x, 0);
        assert_eq!(board.n, 0);
        assert_eq!(board.orphans, 2);
        assert!(board.render_strip().contains("orphans=2"));
    }

    #[test]
    fn meta_scan_cap_marks_data_quality_capped() {
        // Real bounds (5000 files) would be slow in CI; we synthesize a
        // mini run that proves the field is wired into DataQuality.
        let dir = tempdir().unwrap();
        let artifact = dir.path().join("artifacts");
        let bucket = artifact.join("vetcoders/vc-tui/2026_0519/reports");
        for idx in 0..3 {
            write_meta(
                &bucket.join(format!("run-{idx}.meta.json")),
                &format!(
                    r#"{{
                        "run_id": "run-{idx}",
                        "agent": "claude",
                        "skill_code": "owne",
                        "exit_code": 0,
                        "completed_at": "2026-05-19T10:00:00Z"
                    }}"#
                ),
            );
        }
        let state = empty_state(dir.path());
        let mission = MissionControlState::build_at(&state, &artifact, ts("2026-05-19T13:00:00Z"));
        assert_eq!(mission.data_quality.scanned_meta_files, 3);
        assert!(!mission.data_quality.capped);
        assert!(mission.data_quality.artifact_root_present);
    }

    #[test]
    fn mcp_process_match_uses_executable_token_only() {
        assert!(process_line_mentions_server(
            "/Users/tester/.local/bin/loctree-mcp --transport stdio",
            "loctree-mcp"
        ));
        assert!(process_line_mentions_server(
            "aicx-mcp --transport stdio",
            "aicx-mcp"
        ));
        assert!(
            !process_line_mentions_server(
                "python /repo/vibecrafted-mcp/tests/test_server.py",
                "vibecrafted-mcp"
            ),
            "a path argument named like the server is not a live server process"
        );
    }

    #[test]
    fn tailscale_probe_maps_reported_peers_and_missing_dispatch_targets() {
        let targets = vec!["host-a".to_string(), "host-b".to_string()];
        let signals = tailscale_health_signals_from_status(
            Ok(r#"{
                "Peer": {
                    "node-a": {
                        "HostName": "host-a",
                        "DNSName": "host-a.tailnet.ts.net.",
                        "Online": false,
                        "TailscaleIPs": ["100.64.0.1"]
                    },
                    "node-b": {
                        "HostName": "blacky",
                        "DNSName": "blacky.tailnet.ts.net.",
                        "Online": false,
                        "TailscaleIPs": ["100.64.0.2"]
                    },
                    "node-c": {
                        "HostName": "spare",
                        "Online": true,
                        "TailscaleIPs": ["100.64.0.3"]
                    }
                }
            }"#
            .to_string()),
            &targets,
        );

        let host_a = signals
            .iter()
            .find(|signal| signal.label == "tailscale host-a")
            .expect("host-a signal");
        assert_eq!(host_a.status, FleetHealthStatus::Blocked);
        assert!(host_a.detail.contains("dispatch target offline"));

        let blacky = signals
            .iter()
            .find(|signal| signal.label == "tailscale blacky")
            .expect("blacky signal");
        assert_eq!(blacky.status, FleetHealthStatus::Warn);

        let host_b = signals
            .iter()
            .find(|signal| signal.label == "tailscale host-b")
            .expect("missing host-b signal");
        assert_eq!(host_b.status, FleetHealthStatus::Blocked);
        assert!(host_b.detail.contains("missing from tailscale status"));
        assert!(
            !signals
                .iter()
                .any(|signal| signal.label == "tailscale targets"),
            "configured targets must not emit the not-configured signal"
        );
    }

    #[test]
    fn tailscale_probe_reports_neutral_signal_without_dispatch_targets() {
        let signals = tailscale_health_signals_from_status(
            Ok(r#"{
                "Peer": {
                    "node-a": {
                        "HostName": "host-a",
                        "Online": false,
                        "TailscaleIPs": ["100.64.0.1"]
                    }
                }
            }"#
            .to_string()),
            &[],
        );
        let host_a = signals
            .iter()
            .find(|signal| signal.label == "tailscale host-a")
            .expect("host-a signal");
        assert_eq!(
            host_a.status,
            FleetHealthStatus::Warn,
            "not a dispatch target"
        );
        let targets = signals
            .iter()
            .find(|signal| signal.label == "tailscale targets")
            .expect("neutral targets signal");
        assert_eq!(targets.status, FleetHealthStatus::Unknown);
        assert_eq!(targets.detail, "no dispatch targets configured (mesh.conf)");
    }

    #[test]
    fn mesh_conf_hosts_take_first_token_skip_comments_and_dedupe() {
        let hosts = parse_mesh_conf_hosts(
            "# mesh hosts\n\nhost-a ember\n  host-b  dusk  \nHost-A ember\n#host-c x\nhost-d\n",
        );
        assert_eq!(hosts, vec!["host-a", "host-b", "host-d"]);
        assert!(parse_mesh_conf_hosts("").is_empty());
    }

    #[test]
    fn dispatch_targets_env_is_comma_separated_and_deduped() {
        let hosts = dedupe_preserving_order("host-b, host-a,,host-b ".split(',').map(str::trim));
        assert_eq!(hosts, vec!["host-b", "host-a"]);
    }

    #[test]
    fn tailscale_probe_degrades_to_one_signal_when_status_is_unavailable() {
        let signals = tailscale_health_signals_from_status(
            Err("tailscaled is not running".to_string()),
            &["host-a".to_string()],
        );
        assert_eq!(signals.len(), 1);
        assert_eq!(signals[0].label, "tailscale status");
        assert_eq!(signals[0].status, FleetHealthStatus::Unknown);
        assert_eq!(signals[0].detail, "tailscaled is not running");
    }

    #[test]
    fn aicx_probe_maps_index_lag_and_sidecar_health() {
        let signals = aicx_health_signals_from_output(Ok(r#"{
            "schema_version": 2,
            "index_freshness": {
                "name": "index_freshness",
                "severity": "warning",
                "detail": "semantic index lag 48h",
                "recommendation": "run aicx store"
            },
            "sidecar_coverage": {
                "name": "sidecars",
                "severity": "green",
                "detail": "0 missing sidecars",
                "recommendation": null
            },
            "semantic_health": {
                "name": "semantic_health",
                "severity": "warning",
                "detail": "optional semantic model not found",
                "recommendation": null
            },
            "overall": "warning"
        }"#
        .to_string()));

        assert_eq!(signals.len(), 1);
        assert_eq!(signals[0].label, "aicx index");
        assert_eq!(signals[0].status, FleetHealthStatus::Warn);
        assert!(signals[0].detail.contains("48h"));
    }

    #[test]
    fn aicx_probe_blocks_on_extractor_failure() {
        let signals = aicx_health_signals_from_output(Ok(r#"{
            "schema_version": 2,
            "index_freshness": {
                "name": "index_freshness",
                "severity": "green",
                "detail": "index lag 2h",
                "recommendation": null
            },
            "extractor_failures": {
                "name": "extractor_failures",
                "severity": "error",
                "detail": "3 extractor failures in postcompact hooks",
                "recommendation": "inspect diagnostics"
            }
        }"#
        .to_string()));

        assert_eq!(signals.len(), 1);
        assert_eq!(signals[0].status, FleetHealthStatus::Blocked);
        assert!(signals[0].detail.contains("extractor"));
    }

    #[test]
    fn aicx_probe_warns_on_missing_sidecars() {
        let signals = aicx_health_signals_from_output(Ok(r#"{
            "schema_version": 2,
            "index_freshness": {
                "name": "index_freshness",
                "severity": "green",
                "detail": "index lag 2h",
                "recommendation": null
            },
            "sidecar_coverage": {
                "name": "sidecars",
                "severity": "green",
                "detail": "3 missing sidecars",
                "recommendation": "run aicx store"
            }
        }"#
        .to_string()));

        assert_eq!(signals.len(), 1);
        assert_eq!(signals[0].status, FleetHealthStatus::Warn);
        assert!(signals[0].detail.contains("sidecars"));
    }

    #[test]
    fn aicx_probe_degrades_to_one_signal_when_health_is_unavailable() {
        let signals =
            aicx_health_signals_from_output(Err("aicx binary not found on PATH".to_string()));
        assert_eq!(signals.len(), 1);
        assert_eq!(signals[0].label, "aicx index");
        assert_eq!(signals[0].status, FleetHealthStatus::Unknown);
        assert_eq!(signals[0].detail, "aicx binary not found on PATH");
    }

    #[test]
    fn probe_cache_reuses_result_inside_ttl_without_refreshing() {
        let mut cache = ProbeCache::new(std::time::Duration::from_secs(60));
        let start = Instant::now();
        let mut refreshes = 0;

        let first = cache.get_or_refresh(start, || {
            refreshes += 1;
            "first".to_string()
        });
        let second = cache.get_or_refresh(start + std::time::Duration::from_millis(5), || {
            refreshes += 1;
            "second".to_string()
        });
        let third = cache.get_or_refresh(start + std::time::Duration::from_secs(61), || {
            refreshes += 1;
            "third".to_string()
        });

        assert_eq!(first, "first");
        assert_eq!(second, "first");
        assert_eq!(third, "third");
        assert_eq!(refreshes, 2);
    }
}
