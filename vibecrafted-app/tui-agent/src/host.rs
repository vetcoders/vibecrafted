//! Host-only presentation over the existing read models. Navigation never writes
//! the control plane. Explicit project/run actions use the existing product verbs.
//! This path intentionally does not construct App: its launcher/mux/memory panels
//! are guest tools, and their startup probes are not part of viewing the host.
use crate::config::AppConfig;
use control_core::{
    ControlPlane, FrameSessionInventory, RunStatus, StateView, WorkspaceProjection,
};
use crossterm::event::{
    self, Event, KeyCode, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use ratatui::{
    prelude::*,
    widgets::{Block, Borders, Paragraph, Tabs, Wrap},
};
use serde::Deserialize;
use std::{
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::mpsc,
    time::Duration,
};

/// PgUp/PgDn move the viewport a page; one wheel notch moves it a few lines.
const PAGE_STEP: isize = 8;
const WHEEL_STEP: isize = 3;
const ACTIVE_RUNTIME_SCHEMA: &str = "vibecrafted.active-runtime.v1";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HostRoute {
    Dashboard,
    ActiveRuns,
    Config,
    Doctor,
    Projects,
    Voc,
}
impl HostRoute {
    pub const ALL: [Self; 6] = [
        Self::Dashboard,
        Self::ActiveRuns,
        Self::Config,
        Self::Doctor,
        Self::Projects,
        Self::Voc,
    ];
    pub fn label(self) -> &'static str {
        match self {
            Self::Dashboard => "Dashboard",
            Self::ActiveRuns => "Active runs",
            Self::Config => "Config",
            Self::Doctor => "Doctor",
            Self::Projects => "Projects",
            Self::Voc => "Voc",
        }
    }
    fn index(self) -> usize {
        Self::ALL.iter().position(|r| *r == self).unwrap_or(0)
    }
    /// Route-local keys for the status bar: a hint appears only where the key acts.
    fn keys(self) -> &'static str {
        match self {
            Self::ActiveRuns | Self::Voc => "↑/↓ select · g open run · ",
            Self::Projects => "↑/↓ select · Enter open · ",
            Self::Doctor => "d run doctor · ",
            Self::Dashboard | Self::Config => "",
        }
    }
}

/// Identity of the installed Runtime Pack as its `active.json` pointer names it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActiveRuntime {
    pub version: Option<String>,
    pub runtime_root: String,
}

/// Where the pointer was looked up and what reading it produced. An unreadable
/// pointer is reported as unavailable, never replaced by a guessed version.
#[derive(Debug, Clone)]
pub struct RuntimeIdentity {
    pub pointer: Option<PathBuf>,
    pub active: Result<ActiveRuntime, String>,
}
impl RuntimeIdentity {
    pub fn load(pointer: Option<PathBuf>) -> Self {
        let active = match &pointer {
            Some(pointer) => read_active_runtime(pointer),
            None => {
                Err("no runtime home (VIBECRAFTED_RUNTIME_HOME, XDG_DATA_HOME, HOME unset)".into())
            }
        };
        Self { pointer, active }
    }
    pub fn line(&self) -> String {
        let source = self
            .pointer
            .as_ref()
            .map_or_else(|| "unresolved".into(), |p| p.display().to_string());
        match &self.active {
            Ok(runtime) => format!(
                "Active runtime: {} · generation {} · source: {source}",
                runtime.version.as_deref().unwrap_or("version not recorded"),
                runtime.runtime_root
            ),
            Err(error) => format!("Active runtime: unavailable · {error} · source: {source}"),
        }
    }
}

/// Same order as `runtime_paths.vibecrafted_runtime_home`: explicit runtime
/// home, then `$XDG_DATA_HOME/vibecrafted`, then `~/.local/share/vibecrafted`.
/// No home at all yields no pointer, never a cwd-relative guess.
pub fn default_runtime_pointer() -> Option<PathBuf> {
    let var = |name: &str| {
        std::env::var_os(name)
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
    };
    let home = var("VIBECRAFTED_RUNTIME_HOME")
        .or_else(|| var("XDG_DATA_HOME").map(|data| data.join("vibecrafted")));
    #[cfg(windows)]
    let home = home.or_else(|| var("LOCALAPPDATA").map(|data| data.join("Vibecrafted")));
    home.or_else(|| var("HOME").map(|h| h.join(".local").join("share").join("vibecrafted")))
        .map(|home| home.join("active.json"))
}

/// Read-only view of the installer's pointer contract: a regular file carrying
/// `vibecrafted.active-runtime.v1` and an absolute `runtime_root`. A symlinked
/// pointer is refused, as `runtime_paths` refuses it.
pub fn read_active_runtime(pointer: &Path) -> Result<ActiveRuntime, String> {
    let meta = std::fs::symlink_metadata(pointer).map_err(|error| match error.kind() {
        std::io::ErrorKind::NotFound => "pointer absent".to_string(),
        _ => error.to_string(),
    })?;
    if meta.file_type().is_symlink() {
        return Err("active.json is a symlink".into());
    }
    let text = std::fs::read_to_string(pointer).map_err(|error| error.to_string())?;
    let value: serde_json::Value =
        serde_json::from_str(&text).map_err(|error| format!("invalid JSON: {error}"))?;
    if value.get("schema").and_then(|s| s.as_str()) != Some(ACTIVE_RUNTIME_SCHEMA) {
        return Err(format!("not {ACTIVE_RUNTIME_SCHEMA}"));
    }
    let runtime_root = value
        .get("runtime_root")
        .and_then(|root| root.as_str())
        .filter(|root| Path::new(root).is_absolute())
        .ok_or("runtime_root missing or not absolute")?;
    Ok(ActiveRuntime {
        version: value
            .get("version")
            .and_then(|v| v.as_str())
            .filter(|v| !v.is_empty())
            .map(str::to_owned),
        runtime_root: runtime_root.to_owned(),
    })
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct DoctorFinding {
    pub level: String,
    pub component: String,
    pub message: String,
}
#[derive(Deserialize)]
struct DoctorPayload {
    findings: Vec<DoctorFinding>,
}

/// One `vibecrafted doctor --json` run as the host renders it: the installer's
/// own findings, or why they could not be read.
#[derive(Debug, Clone)]
pub struct DoctorReport {
    /// The exact command line, so a failure names what to run by hand.
    pub command: String,
    pub sampled_at: String,
    pub outcome: Result<Vec<DoctorFinding>, String>,
}
impl DoctorReport {
    /// Findings come from stdout whatever the exit code: the doctor exits 1
    /// exactly when it reports failures. Only output without the payload fails.
    pub fn from_output(command: String, status: &str, stdout: &[u8], stderr: &[u8]) -> Self {
        let outcome = serde_json::from_slice::<DoctorPayload>(stdout)
            .map(|payload| payload.findings)
            .map_err(|error| {
                let stderr = String::from_utf8_lossy(stderr);
                let reason = stderr
                    .lines()
                    .rev()
                    .map(str::trim)
                    .find(|line| !line.is_empty())
                    .map(|line| line.chars().take(240).collect())
                    .unwrap_or_else(|| format!("no doctor JSON on stdout ({error})"));
                format!("{status} · {reason}")
            });
        Self::new(command, outcome)
    }
    pub fn new(command: String, outcome: Result<Vec<DoctorFinding>, String>) -> Self {
        Self {
            command,
            sampled_at: chrono::Utc::now().to_rfc3339(),
            outcome,
        }
    }
    fn count(&self, level: &str) -> usize {
        self.outcome
            .as_ref()
            .map_or(0, |f| f.iter().filter(|f| f.level == level).count())
    }
    fn counts(&self) -> String {
        format!(
            "{} ok · {} warnings · {} failures",
            self.count("ok"),
            self.count("warn"),
            self.count("fail")
        )
    }
    pub fn summary_line(&self) -> String {
        match &self.outcome {
            Ok(_) => format!("Doctor · {} · details in [4] Doctor", self.counts()),
            Err(_) => format!("Doctor unavailable · run manually: {}", self.command),
        }
    }
    /// Counts plus every non-ok check, failures first; passing checks stay a number.
    fn append_to(&self, lines: &mut Vec<String>) {
        match &self.outcome {
            Ok(findings) => {
                lines.push(format!("  {} · {}", self.sampled_at, self.counts()));
                let mut attention = findings
                    .iter()
                    .filter(|f| f.level != "ok")
                    .collect::<Vec<_>>();
                attention.sort_by_key(|f| f.level != "fail");
                lines.extend(
                    attention
                        .iter()
                        .map(|f| format!("  {} {} · {}", f.level, f.component, f.message)),
                );
            }
            Err(error) => {
                lines.push(format!(
                    "  {} · Doctor unavailable · {error}",
                    self.sampled_at
                ));
                lines.push(format!("  Run manually: {}", self.command));
            }
        }
    }
}

#[derive(Debug, Clone)]
pub struct HostProject {
    pub id: String,
    pub label: String,
    pub root: String,
    pub sessions: Vec<String>,
}

#[derive(Debug)]
pub struct HostSnapshot {
    pub runs: StateView,
    pub projects: Vec<HostProject>,
    pub workspace_error: Option<String>,
    pub server_status: String,
    pub runtime: RuntimeIdentity,
    pub sampled_at: String,
}
impl HostSnapshot {
    /// The same canonical StateView used by the server and its LIVE feed.
    pub fn load(config: &AppConfig) -> Self {
        let plane = ControlPlane::from_control_plane_home(&config.state_root);
        let (projects, workspace_error) = match plane.load_workspace_projection() {
            Ok(projection) => {
                let inventory = FrameSessionInventory::scan(projection.frame_socket_dirs());
                (project_catalog(&projection, &inventory), None)
            }
            Err(error) => (Vec::new(), Some(error.to_string())),
        };
        let agent = ureq::AgentBuilder::new()
            .timeout(Duration::from_secs(1))
            .build();
        let server_status = match agent.get(&format!("{}/api/health", config.server)).call() {
            Ok(response) => format!("ready · HTTP {}", response.status()),
            Err(error) => format!("unavailable · {error}"),
        };
        Self {
            runs: plane.compute_view(chrono::Utc::now()),
            projects,
            workspace_error,
            server_status,
            // One small pointer file per sample; the reader thread owns this IO.
            runtime: RuntimeIdentity::load(default_runtime_pointer()),
            sampled_at: chrono::Utc::now().to_rfc3339(),
        }
    }
    pub fn live_count(&self) -> usize {
        self.runs.active_runs.len()
    }
    pub fn workspace_count(&self) -> usize {
        self.projects.iter().map(|p| p.sessions.len()).sum()
    }
}

pub fn project_catalog(
    projection: &WorkspaceProjection,
    inventory: &FrameSessionInventory,
) -> Vec<HostProject> {
    let live = projection.live_frame_sessions(inventory);
    projection
        .catalog
        .as_ref()
        .into_iter()
        .flat_map(|c| &c.workspaces)
        .map(|p| HostProject {
            id: p.workspace_id.clone(),
            label: p.display_label.clone(),
            root: p.canonical_root.clone(),
            sessions: live
                .iter()
                .filter(|s| {
                    s.owner
                        .as_ref()
                        .is_some_and(|owner| owner.workspace_id == p.workspace_id)
                })
                .map(|s| s.runtime_session_id.clone())
                .collect(),
        })
        .collect()
}

#[derive(Debug)]
pub struct HostDashboard {
    pub route: HostRoute,
    pub snapshot: Option<HostSnapshot>,
    pub selected: usize,
    pub offset: usize,
    pub project_input: Option<String>,
    pub notice: String,
    pub action_pending: bool,
    /// Last installed-doctor report; only an explicit `d` on Doctor produces one.
    pub doctor: Option<DoctorReport>,
    pub doctor_running: bool,
}
impl HostDashboard {
    pub fn new(route: HostRoute) -> Self {
        Self {
            route,
            snapshot: None,
            selected: 0,
            offset: 0,
            project_input: None,
            notice: "Reading host projections…".into(),
            action_pending: false,
            doctor: None,
            doctor_running: false,
        }
    }
    pub fn navigate(&mut self, route: HostRoute) {
        self.route = route;
        self.offset = 0;
        self.selected = 0;
    }
    pub fn apply(&mut self, mut snapshot: HostSnapshot) {
        if snapshot.workspace_error.is_some()
            && let Some(previous) = &self.snapshot
        {
            snapshot.projects = previous.projects.clone();
        }
        if self.snapshot.is_none() {
            self.notice = "Host projections loaded · actions require an explicit selection".into();
        }
        // Keep selection by canonical identity, never by a displayed name.
        // Routes without a list hold no selection at all.
        let run_id = self.run_rows().get(self.selected).map(|r| r.run_id.clone());
        let project_id = self
            .snapshot
            .as_ref()
            .filter(|_| self.route == HostRoute::Projects)
            .and_then(|s| s.projects.get(self.selected))
            .map(|p| p.id.clone());
        self.snapshot = Some(snapshot);
        self.selected = match self.route {
            HostRoute::Projects => project_id
                .and_then(|id| {
                    self.snapshot
                        .as_ref()
                        .and_then(|s| s.projects.iter().position(|p| p.id == id))
                })
                .unwrap_or(0),
            HostRoute::ActiveRuns | HostRoute::Voc => run_id
                .and_then(|id| self.run_rows().iter().position(|r| r.run_id == id))
                .unwrap_or(0),
            HostRoute::Dashboard | HostRoute::Config | HostRoute::Doctor => 0,
        };
    }
    fn run_rows(&self) -> Vec<&RunStatus> {
        let Some(s) = &self.snapshot else {
            return Vec::new();
        };
        match self.route {
            HostRoute::ActiveRuns => {
                let now = run_timestamp(&s.sampled_at).unwrap_or_else(chrono::Utc::now);
                ordered_runs(&s.runs.active_runs.iter().collect::<Vec<_>>())
                    .into_iter()
                    .chain(ordered_runs(&attention_runs(&s.runs.stalled_runs, now)))
                    .collect()
            }
            HostRoute::Voc => s.runs.recent_runs.iter().collect(),
            _ => Vec::new(),
        }
    }
    /// Arrow keys move the highlight only on routes that render a selectable list.
    pub fn move_selection(&mut self, delta: isize) {
        let len = match self.route {
            HostRoute::Projects => self.snapshot.as_ref().map_or(0, |s| s.projects.len()),
            HostRoute::ActiveRuns | HostRoute::Voc => self.run_rows().len(),
            HostRoute::Dashboard | HostRoute::Config | HostRoute::Doctor => 0,
        };
        self.selected = self
            .selected
            .saturating_add_signed(delta)
            .min(len.saturating_sub(1));
    }
    /// PgUp/PgDn and the wheel move one viewport offset, clamped to the text.
    pub fn scroll(&mut self, config: &AppConfig, delta: isize) {
        let last = self.lines(config).len().saturating_sub(1);
        self.offset = self.offset.saturating_add_signed(delta).min(last);
    }
    /// Mouse capture covers the whole host, so the wheel must scroll here or it
    /// does nothing at all; a left click on the tab row switches route.
    pub fn handle_mouse(&mut self, config: &AppConfig, mouse: MouseEvent) {
        match mouse.kind {
            MouseEventKind::ScrollDown => self.scroll(config, WHEEL_STEP),
            MouseEventKind::ScrollUp => self.scroll(config, -WHEEL_STEP),
            MouseEventKind::Down(MouseButton::Left) if mouse.row == 1 => {
                // Tabs use one cell of padding at each end and a one-cell divider.
                let mut x = 0;
                for (route, label) in HostRoute::ALL.iter().zip(self.tab_labels()) {
                    let end = x + label.chars().count() + 2;
                    if (x..end).contains(&(mouse.column as usize)) {
                        self.navigate(*route);
                        break;
                    }
                    x = end + 1;
                }
            }
            _ => {}
        }
    }
    fn live_label(&self) -> String {
        self.snapshot
            .as_ref()
            .map(|s| s.live_count().to_string())
            .unwrap_or_else(|| "?".into())
    }
    fn tab_labels(&self) -> Vec<String> {
        let active = self.live_label();
        HostRoute::ALL
            .iter()
            .enumerate()
            .map(|(i, r)| {
                if *r == HostRoute::ActiveRuns {
                    format!("{} {} · {active}", i + 1, r.label())
                } else {
                    format!("{} {}", i + 1, r.label())
                }
            })
            .collect()
    }
    fn receive(&mut self, receipt: HostReceipt) {
        self.action_pending = false;
        match receipt {
            HostReceipt::Notice(notice) => self.notice = notice,
            HostReceipt::Doctor(report) => {
                self.notice = report.summary_line();
                self.doctor = Some(report);
                self.doctor_running = false;
            }
        }
    }
    pub fn lines(&self, config: &AppConfig) -> Vec<String> {
        let mut lines = vec![self.route.label().to_uppercase(), String::new()];
        let Some(s) = &self.snapshot else {
            lines.push("Reading host projections…".into());
            return lines;
        };
        match self.route {
            HostRoute::Dashboard => {
                lines.extend([
                    "Open a project to create or rejoin its workspace.".into(),
                    "[o] Open project    [5] Projects".into(),
                    String::new(),
                    if s.workspace_error.is_some() {
                        "Live workspaces unknown · catalog unavailable".into()
                    } else {
                        format!(
                            "Live workspaces {} · registered Frame socket projection",
                            s.workspace_count()
                        )
                    },
                    format!(
                        "Active runs {} · needs attention {}",
                        s.live_count(),
                        attention_runs(
                            &s.runs.stalled_runs,
                            run_timestamp(&s.sampled_at).unwrap_or_else(chrono::Utc::now)
                        )
                        .len()
                    ),
                    format!("Server {} · {}", config.server, s.server_status),
                    format!(
                        "Doctor: {} · [4] named checks",
                        if s.workspace_error.is_some() {
                            "catalog needs attention"
                        } else {
                            "catalog readable"
                        }
                    ),
                    String::new(),
                    "Recent/catalog projects (not a live workspace census):".into(),
                ]);
                lines.extend(
                    s.projects
                        .iter()
                        .take(8)
                        .map(|p| format!("{} · {}", p.label, p.root)),
                );
                if s.projects.is_empty() {
                    lines.push("No catalog projects. Press o to open a project.".into());
                }
            }
            HostRoute::ActiveRuns | HostRoute::Voc => {
                lines.push(format!(
                    "Active runs {} · needs attention {}",
                    s.live_count(),
                    s.runs.stalled_runs.len()
                ));
                // Rendering and actions share the same grouped, filtered order.
                if self.route == HostRoute::ActiveRuns {
                    let now = run_timestamp(&s.sampled_at).unwrap_or_else(chrono::Utc::now);
                    let active = ordered_runs(&s.runs.active_runs.iter().collect::<Vec<_>>());
                    let attention = ordered_runs(&attention_runs(&s.runs.stalled_runs, now));
                    lines[2] = format!(
                        "Active runs {} · needs attention {}",
                        active.len(),
                        attention.len()
                    );
                    lines.push("IN PROGRESS".into());
                    append_run_table(&mut lines, &active, Some(self.selected), now, true);
                    lines.push("NEEDS ATTENTION".into());
                    append_run_table(
                        &mut lines,
                        &attention,
                        self.selected.checked_sub(active.len()),
                        now,
                        false,
                    );
                    let archived = s.runs.stalled_runs.len() - attention.len();
                    if archived > 0 {
                        lines.push(format!("  +{archived} archiwalnych (starsze niż 48 h)"));
                    }
                } else {
                    lines.push(
                        "GLOBAL RUN FEED · recent/history · canonical state and evidence".into(),
                    );
                    append_runs(&mut lines, &s.runs.recent_runs, Some(self.selected));
                }
                lines.push("g: open selected run through goto-work · ↑/↓ select".into());
                if let Some(run) = self.run_rows().get(self.selected) {
                    lines.push(format!(
                        "Selected: {} · {} / {} · {}",
                        run.run_id, run.agent, run.skill, run.state
                    ));
                    lines.push(format!("Root: {}", run.root));
                    lines.push(format!("Evidence: {} · {}", run.liveness, run.source));
                    if !run.last_error.is_empty() {
                        lines.push(format!("Last error: {}", run.last_error));
                    }
                }
            }
            HostRoute::Config => lines.extend([
                "Scope: global · source: --view host (cwd is not a filter)".into(),
                format!(
                    "Control plane: {} · source: --state-root / VIBECRAFTED_HOME / default",
                    config.state_root.display()
                ),
                format!(
                    "Server: {} · source: --server / server environment / default",
                    config.server
                ),
                format!(
                    "Command deck: {} · source: --deck / installed launcher",
                    config.command_deck.display()
                ),
                s.runtime.line(),
                "Configuration help: vc-o --help · vibecrafted help".into(),
                "Values are read-only. No settings are changed by navigation.".into(),
            ]),
            HostRoute::Doctor => {
                lines.extend([
                    format!("Server readiness: {}", s.server_status),
                    format!(
                        "Workspace catalog: {}",
                        s.workspace_error.as_deref().unwrap_or("readable")
                    ),
                    format!(
                        "Control-plane directory: {}",
                        if config.state_root.is_dir() {
                            "present"
                        } else {
                            "absent · first-run state"
                        }
                    ),
                    format!("Run warnings: {}", s.runs.warnings.len()),
                    "Frame inventory: registered sockets; a stale socket can outlive its server."
                        .into(),
                    "Remediation: inspect vibecrafted doctor and vc-frame doctor.".into(),
                    "These are projection checks, not an installation certificate.".into(),
                ]);
                lines.extend(s.runs.warnings.iter().cloned());
                lines.push(String::new());
                lines.push(format!(
                    "INSTALLED DOCTOR · d: run {} doctor --json",
                    config.command_deck.display()
                ));
                if self.doctor_running {
                    lines.push("  Running · the report lands here when it finishes.".into());
                }
                match &self.doctor {
                    Some(report) => report.append_to(&mut lines),
                    None if !self.doctor_running => {
                        lines.push("  Not run in this view yet.".into())
                    }
                    None => {}
                }
            }
            HostRoute::Projects => {
                lines.push(
                    "o: Open project · ↑/↓ select · Enter: open/rejoin through vc-start".into(),
                );
                if s.projects.is_empty() {
                    lines.push("No catalog projects.".into());
                }
                for (i, p) in s.projects.iter().enumerate() {
                    lines.push(format!(
                        "{} {} · {}",
                        if i == self.selected { "▶" } else { " " },
                        p.label,
                        if p.sessions.is_empty() {
                            "Open workspace"
                        } else {
                            "Return to workspace"
                        }
                    ));
                    lines.push(format!("    {} · id {}", p.root, p.id));
                }
            }
        }
        if let Some(error) = &s.workspace_error {
            lines.push(format!("Projection unknown: {error}"));
        }
        lines
    }
}
/// Presentation-only location: canonical fleet roots carry namespace/repo/date/cut.
/// Living Trees use their basename; repo-local .worktrees use their parent repo.
fn run_location(root: &str) -> (String, String) {
    let parts = root
        .split('/')
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>();
    let label = parts.last().copied().unwrap_or("unknown");
    let repo = parts
        .windows(4)
        .find(|p| {
            p[0] == "worktrees"
                && p[3].len() == 9
                && p[3].as_bytes()[4] == b'_'
                && p[3]
                    .chars()
                    .filter(|c| *c != '_')
                    .all(|c| c.is_ascii_digit())
        })
        .map(|p| p[2])
        .or_else(|| {
            parts
                .windows(2)
                .find(|p| p[1] == ".worktrees")
                .map(|p| p[0])
        })
        .unwrap_or(label);
    (label.into(), repo.into())
}

fn run_timestamp(raw: &str) -> Option<chrono::DateTime<chrono::Utc>> {
    chrono::DateTime::parse_from_rfc3339(raw)
        .ok()
        .map(|t| t.with_timezone(&chrono::Utc))
}

fn attention_runs(runs: &[RunStatus], now: chrono::DateTime<chrono::Utc>) -> Vec<&RunStatus> {
    runs.iter()
        .filter(|r| {
            run_timestamp(&r.updated_at)
                .is_none_or(|t| now.signed_duration_since(t) <= chrono::Duration::hours(48))
        })
        .collect()
}

fn ordered_runs<'a>(runs: &[&'a RunStatus]) -> Vec<&'a RunStatus> {
    let mut rows = runs.to_vec();
    rows.sort_by(|a, b| {
        run_location(&a.root)
            .1
            .cmp(&run_location(&b.root).1)
            .then_with(|| run_timestamp(&b.updated_at).cmp(&run_timestamp(&a.updated_at)))
            .then_with(|| a.run_id.cmp(&b.run_id))
    });
    rows
}

/// Terminal-cell widths, including wide Unicode; controls cannot create extra rows.
fn run_cell(value: &str, width: usize) -> String {
    let clean = value
        .chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect::<String>();
    let mut text = String::new();
    if Line::from(clean.as_str()).width() <= width {
        text = clean;
    } else {
        for c in clean.chars() {
            let mut candidate = text.clone();
            candidate.push(c);
            if Line::from(candidate.as_str()).width() + 1 > width {
                break;
            }
            text.push(c);
        }
        text.push('…');
    }
    let padding = width.saturating_sub(Line::from(text.as_str()).width());
    text.push_str(&" ".repeat(padding));
    text
}

fn run_time(run: &RunStatus, now: chrono::DateTime<chrono::Utc>, active: bool) -> String {
    let (raw, prefix) = if active && run_timestamp(&run.started_at).is_some() {
        (run.started_at.as_str(), "trwa")
    } else {
        (run.updated_at.as_str(), "ostatnio")
    };
    let Some(time) = run_timestamp(raw) else {
        return "czas nieznany".into();
    };
    let minutes = now.signed_duration_since(time).num_minutes().max(0);
    let age = if minutes < 60 {
        format!("{minutes} min")
    } else if minutes < 1440 {
        format!("{} h {} min", minutes / 60, minutes % 60)
    } else {
        format!("{} d {} h", minutes / 1440, minutes % 1440 / 60)
    };
    if prefix == "trwa" {
        format!("trwa {age}")
    } else {
        format!("ostatnio {age} temu")
    }
}

fn append_run_table(
    lines: &mut Vec<String>,
    runs: &[&RunStatus],
    selected: Option<usize>,
    now: chrono::DateTime<chrono::Utc>,
    active: bool,
) {
    if runs.is_empty() {
        lines.push("  None".into());
        return;
    }
    let mut group = String::new();
    for (i, r) in runs.iter().enumerate() {
        let (label, repo) = run_location(&r.root);
        if group != repo {
            let count = runs
                .iter()
                .filter(|r| run_location(&r.root).1 == repo)
                .count();
            lines.push(format!("  {} · {count}", run_cell(&repo, 32).trim_end()));
            lines.push(format!(
                "  {} · {} · {} · {} · {} · {}",
                run_cell("STATE", 10),
                run_cell("AGENT", 9),
                run_cell("LABEL", 26),
                run_cell("REPO", 17),
                run_cell("TIME", 25),
                "SKILL"
            ));
            group = repo.clone();
        }
        lines.push(format!(
            "{} {} · {} · {} · {} · {} · {}",
            if selected == Some(i) { "▶" } else { " " },
            run_cell(&r.state, 10),
            run_cell(&r.agent, 9),
            run_cell(&label, 26),
            run_cell(&repo, 17),
            run_cell(&run_time(r, now, active), 25),
            run_cell(if r.skill == "implement" { "" } else { &r.skill }, 14).trim_end()
        ));
    }
}

/// `selected` is relative to `runs`; `None` when the selection sits in another section.
fn append_runs(lines: &mut Vec<String>, runs: &[RunStatus], selected: Option<usize>) {
    if runs.is_empty() {
        lines.push("  None".into());
    }
    for (i, r) in runs.iter().enumerate() {
        lines.push(format!(
            "{} {} · {} / {} · {} · {}",
            if selected == Some(i) { "▶" } else { " " },
            r.state,
            r.agent,
            r.skill,
            r.run_id,
            r.root
        ));
        lines.push(format!(
            "    {} · evidence: {} {} {}",
            r.updated_at, r.liveness, r.source, r.last_error
        ));
    }
}

pub fn draw(frame: &mut Frame, host: &HostDashboard, config: &AppConfig) {
    let areas = Layout::vertical([
        Constraint::Length(3),
        Constraint::Min(1),
        Constraint::Length(3),
    ])
    .split(frame.area());
    let active = host.live_label();
    frame.render_widget(
        Tabs::new(host.tab_labels())
            .select(host.route.index())
            .highlight_style(Style::default().fg(Color::Yellow))
            .block(
                Block::default()
                    .borders(Borders::BOTTOM)
                    .title("Operator Frame · Global"),
            ),
        areas[0],
    );
    let mut lines = host.lines(config);
    if host.route == HostRoute::ActiveRuns {
        lines = lines
            .into_iter()
            .flat_map(|line| {
                if ["Selected:", "Root:", "Evidence:", "Last error:"]
                    .iter()
                    .any(|p| line.starts_with(p))
                {
                    let mut rows = vec![String::new()];
                    for c in line.chars() {
                        let last = rows.last_mut().expect("one detail row");
                        let mut next = last.clone();
                        next.push(c);
                        if Line::from(next.as_str()).width() > areas[1].width as usize
                            && !last.is_empty()
                        {
                            rows.push(c.to_string());
                        } else {
                            last.push(c);
                        }
                    }
                    rows
                } else {
                    vec![line]
                }
            })
            .collect();
    }
    let body =
        Paragraph::new(lines.join("\n")).scroll((host.offset.min(u16::MAX as usize) as u16, 0));
    let body = if host.route == HostRoute::ActiveRuns {
        body
    } else {
        body.wrap(Wrap { trim: false })
    };
    frame.render_widget(body, areas[1]);
    let status = host
        .project_input
        .as_ref()
        .map(|p| format!("Open project: {p}  · Enter confirm · Esc cancel"))
        .unwrap_or_else(|| {
            format!(
                "{}\n1–6 views · {}o Open project · PgUp/PgDn scroll · q close · LIVE {active}",
                host.notice,
                host.route.keys()
            )
        });
    frame.render_widget(
        Paragraph::new(status).block(Block::default().borders(Borders::TOP)),
        areas[2],
    );
}

#[derive(Debug)]
enum HostAction {
    OpenProject(String),
    GotoRun(String),
    Doctor,
}
/// What an action thread hands back: a receipt line, or the doctor's report.
#[derive(Debug)]
enum HostReceipt {
    Notice(String),
    Doctor(DoctorReport),
}
fn perform_action(config: &AppConfig, action: HostAction) -> HostReceipt {
    let notice = match action {
        HostAction::Doctor => return HostReceipt::Doctor(run_doctor(&config.command_deck)),
        HostAction::OpenProject(root) => {
            let home = std::env::var_os("HOME").map(std::path::PathBuf::from);
            let root = match crate::config::resolve_destination_repo(&root, home.as_deref()) {
                Ok(root) => root,
                Err(error) => return HostReceipt::Notice(format!("Open refused: {error}")),
            };
            match Command::new(&config.command_deck)
                .args(["start", "resume", "--repo"])
                .arg(&root)
                .stdin(Stdio::null())
                .output()
            {
                Ok(output) => {
                    let detail = if output.status.success() {
                        &output.stdout
                    } else {
                        &output.stderr
                    };
                    // The product verb owns the receipt. Exit zero alone never means projected.
                    format!(
                        "Open {} · {} · {}",
                        root.display(),
                        output.status,
                        String::from_utf8_lossy(detail).trim()
                    )
                }
                Err(error) => format!("Open {} failed: {error}", root.display()),
            }
        }
        HostAction::GotoRun(id) => crate::goto_work::vc_frame_binary_from_env()
            .map_err(|e| e.to_string())
            .and_then(|frame| {
                crate::goto_work::goto_work(&config.state_root, &id, &frame, &config.command_deck)
                    .map(|r| r.status_line())
                    .map_err(|e| e.to_string())
            })
            .unwrap_or_else(|e| format!("Goto: {e}")),
    };
    HostReceipt::Notice(notice)
}
/// The installed doctor, through the same deck as every other host verb. Its
/// stdout is captured, so the probe can never draw over the host terminal.
fn run_doctor(deck: &Path) -> DoctorReport {
    let command = format!("{} doctor --json", deck.display());
    match Command::new(deck)
        .args(["doctor", "--json"])
        .stdin(Stdio::null())
        .output()
    {
        Ok(output) => DoctorReport::from_output(
            command,
            &output.status.to_string(),
            &output.stdout,
            &output.stderr,
        ),
        Err(error) => DoctorReport::new(command, Err(format!("could not start: {error}"))),
    }
}

/// One reader thread, one bounded snapshot slot; no polling IO on the input loop.
/// No writer, launcher catalog, global settings or session registration is opened
/// just by entering a host route.
pub fn run(config: AppConfig, route: HostRoute) -> anyhow::Result<()> {
    let (snapshot_tx, snapshot_rx) = mpsc::sync_channel(1);
    let (stop_tx, stop_rx) = mpsc::channel();
    let reader_config = config.clone();
    let reader = std::thread::spawn(move || {
        loop {
            if let Err(mpsc::TrySendError::Disconnected(_)) =
                snapshot_tx.try_send(HostSnapshot::load(&reader_config))
            {
                break;
            }
            if !matches!(
                stop_rx.recv_timeout(Duration::from_secs(3)),
                Err(mpsc::RecvTimeoutError::Timeout)
            ) {
                break;
            }
        }
    });
    let (action_tx, action_rx) = mpsc::channel();
    let mut host = HostDashboard::new(route);
    crossterm::terminal::enable_raw_mode()?;
    let mut stdout = std::io::stdout();
    crossterm::execute!(
        stdout,
        crossterm::terminal::EnterAlternateScreen,
        event::EnableMouseCapture
    )?;
    let mut terminal = Terminal::new(CrosstermBackend::new(stdout))?;
    let result = (|| -> anyhow::Result<()> {
        loop {
            while let Ok(snapshot) = snapshot_rx.try_recv() {
                host.apply(snapshot);
            }
            while let Ok(receipt) = action_rx.try_recv() {
                host.receive(receipt);
            }
            terminal.draw(|f| draw(f, &host, &config))?;
            if !event::poll(config.tick_rate)? {
                continue;
            }
            let mut action = None;
            match event::read()? {
                Event::Key(key) if key.kind != event::KeyEventKind::Release => {
                    if key.code == KeyCode::Char('c')
                        && key.modifiers.contains(KeyModifiers::CONTROL)
                    {
                        break;
                    }
                    if let Some(input) = &mut host.project_input {
                        match key.code {
                            KeyCode::Esc => host.project_input = None,
                            KeyCode::Enter => {
                                action = host.project_input.take().map(HostAction::OpenProject)
                            }
                            KeyCode::Backspace => {
                                input.pop();
                            }
                            KeyCode::Char(c) => input.push(c),
                            _ => {}
                        }
                    } else {
                        match key.code {
                            KeyCode::Char('q') => break,
                            KeyCode::Char(c @ '1'..='6') => {
                                host.navigate(HostRoute::ALL[(c as u8 - b'1') as usize])
                            }
                            KeyCode::Tab => {
                                host.navigate(HostRoute::ALL[(host.route.index() + 1) % 6])
                            }
                            KeyCode::Char('o') => host.project_input = Some(String::new()),
                            KeyCode::Up => host.move_selection(-1),
                            KeyCode::Down => host.move_selection(1),
                            KeyCode::PageDown => host.scroll(&config, PAGE_STEP),
                            KeyCode::PageUp => host.scroll(&config, -PAGE_STEP),
                            KeyCode::Enter if host.route == HostRoute::Projects => {
                                action = host
                                    .snapshot
                                    .as_ref()
                                    .and_then(|s| s.projects.get(host.selected))
                                    .map(|p| HostAction::OpenProject(p.root.clone()));
                            }
                            KeyCode::Char('g')
                                if matches!(host.route, HostRoute::ActiveRuns | HostRoute::Voc) =>
                            {
                                action = host
                                    .run_rows()
                                    .get(host.selected)
                                    .map(|r| HostAction::GotoRun(r.run_id.clone()));
                            }
                            KeyCode::Char('d') if host.route == HostRoute::Doctor => {
                                action = Some(HostAction::Doctor)
                            }
                            _ => {}
                        }
                    }
                }
                Event::Mouse(mouse) => host.handle_mouse(&config, mouse),
                _ => {}
            }
            if let Some(action) = action {
                if host.action_pending {
                    host.notice = "An action is pending; waiting for its receipt.".into();
                    continue;
                }
                host.action_pending = true;
                host.notice = match &action {
                    HostAction::OpenProject(root) => {
                        format!("Opening {root} · waiting for product receipt")
                    }
                    HostAction::GotoRun(id) => {
                        format!("Opening run {id} · waiting for product receipt")
                    }
                    HostAction::Doctor => {
                        host.doctor_running = true;
                        format!(
                            "Running {} doctor --json · waiting for its report",
                            config.command_deck.display()
                        )
                    }
                };
                let tx = action_tx.clone();
                let config = config.clone();
                std::thread::spawn(move || {
                    let _ = tx.send(perform_action(&config, action));
                });
            }
        }
        Ok(())
    })();
    let _ = stop_tx.send(());
    drop(snapshot_rx);
    // An in-progress read has a bounded HTTP timeout; never hold the terminal for it.
    if reader.is_finished() {
        let _ = reader.join();
    }
    crossterm::terminal::disable_raw_mode()?;
    crossterm::execute!(
        terminal.backend_mut(),
        event::DisableMouseCapture,
        crossterm::terminal::LeaveAlternateScreen
    )?;
    terminal.show_cursor()?;
    result
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn refused_project_verb_preserves_the_exact_failure_and_creates_no_projection() {
        let temp = tempfile::tempdir().unwrap();
        let repo = temp.path().join("Operator with spaces");
        std::fs::create_dir(&repo).unwrap();
        let deck = temp.path().join("deck");
        std::fs::write(&deck, "#!/bin/sh\n[ \"$1\" = start ] && [ \"$2\" = resume ] && [ \"$3\" = --repo ] && [ -d \"$4\" ] || exit 99\nprintf 'guest-create refused: fixture denial' >&2\nexit 23\n").unwrap();
        std::fs::set_permissions(&deck, std::fs::Permissions::from_mode(0o700)).unwrap();
        let config = AppConfig {
            state_root: temp.path().join("control_plane"),
            command_deck: deck,
            repo: temp.path().to_path_buf(),
            presentation: crate::launch::Presentation::Headless,
            tick_rate: Duration::from_millis(50),
            no_verify_gate: false,
            server: "http://127.0.0.1:1".into(),
            view: crate::observe::ConsoleView::Host(HostRoute::Dashboard),
        };
        let HostReceipt::Notice(receipt) =
            perform_action(&config, HostAction::OpenProject(repo.display().to_string()))
        else {
            panic!("an open verb answers with a receipt line");
        };
        assert!(receipt.contains("guest-create refused: fixture denial"));
        assert!(receipt.contains("23"));
        assert!(!receipt.contains("Opened workspace"));
        assert!(!config.state_root.exists());
    }

    fn deck(dir: &Path, body: &str) -> PathBuf {
        let deck = dir.join("deck");
        std::fs::write(&deck, format!("#!/bin/sh\n{body}\n")).unwrap();
        std::fs::set_permissions(&deck, std::fs::Permissions::from_mode(0o700)).unwrap();
        deck
    }

    #[test]
    fn doctor_reads_findings_through_the_deck_even_when_it_exits_red() {
        let temp = tempfile::tempdir().unwrap();
        // The real doctor exits 1 exactly when it reports failures.
        let deck = deck(
            temp.path(),
            r#"[ "$1" = doctor ] && [ "$2" = --json ] || exit 99
printf '%s' '{"ok":1,"warnings":1,"failures":1,"findings":[{"level":"ok","component":"runtime","message":"ready"},{"level":"warn","component":"slack-provider","message":"optional"},{"level":"fail","component":"runtime-receipt","message":"live damage"}]}'
exit 1"#,
        );
        let report = run_doctor(&deck);
        let findings = report.outcome.as_ref().expect("stdout JSON is the report");
        assert_eq!(findings.len(), 3);
        assert_eq!(report.command, format!("{} doctor --json", deck.display()));
        assert_eq!(
            report.summary_line(),
            "Doctor · 1 ok · 1 warnings · 1 failures · details in [4] Doctor"
        );
        let mut lines = Vec::new();
        report.append_to(&mut lines);
        let text = lines.join("\n");
        let fail = text.find("fail runtime-receipt · live damage").unwrap();
        let warn = text.find("warn slack-provider · optional").unwrap();
        assert!(fail < warn, "failures lead: {text}");
        assert!(
            !text.contains("runtime · ready"),
            "passing checks stay a count"
        );
        assert!(!text.contains("\"level\""), "never a raw JSON dump");
    }

    #[test]
    fn doctor_that_cannot_run_or_speak_json_names_the_manual_command() {
        let temp = tempfile::tempdir().unwrap();
        let missing = run_doctor(&temp.path().join("absent-deck"));
        let error = missing.outcome.as_ref().unwrap_err();
        assert!(error.starts_with("could not start"), "{error}");
        assert!(missing.summary_line().contains("absent-deck doctor --json"));

        let deck = deck(
            temp.path(),
            "echo 'usage: vibecrafted' >&2\necho 'vibecrafted: error: unrecognized arguments: --json' >&2\nexit 2",
        );
        let old = run_doctor(&deck);
        let error = old.outcome.as_ref().unwrap_err();
        assert!(error.contains("unrecognized arguments: --json"), "{error}");
        assert!(error.contains('2'), "exit status stays visible: {error}");
        let mut lines = Vec::new();
        old.append_to(&mut lines);
        assert!(
            lines
                .iter()
                .any(|l| l == &format!("  Run manually: {} doctor --json", deck.display()))
        );
    }
}
