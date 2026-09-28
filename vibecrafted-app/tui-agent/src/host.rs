//! Host-only presentation over the existing read models. Navigation never writes
//! the control plane. Explicit project/run actions use the existing product verbs.
//! This path intentionally does not construct App: its launcher/mux/memory panels
//! are guest tools, and their startup probes are not part of viewing the host.
use crate::config::AppConfig;
use control_core::{
    ControlPlane, FrameSessionInventory, RunStatus, StateView, WorkspaceProjection,
};
use crossterm::event::{self, Event, KeyCode, KeyModifiers, MouseEventKind};
use ratatui::{
    prelude::*,
    widgets::{Block, Borders, Paragraph, Tabs, Wrap},
};
use std::{
    process::{Command, Stdio},
    sync::mpsc,
    time::Duration,
};

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
        let run_id = self.run_rows().get(self.selected).map(|r| r.run_id.clone());
        let id = self
            .snapshot
            .as_ref()
            .and_then(|s| s.projects.get(self.selected))
            .map(|p| p.id.clone());
        self.selected = id
            .and_then(|id| snapshot.projects.iter().position(|p| p.id == id))
            .unwrap_or(0);
        self.snapshot = Some(snapshot);
        if matches!(self.route, HostRoute::ActiveRuns | HostRoute::Voc) {
            self.selected = run_id
                .and_then(|id| self.run_rows().iter().position(|r| r.run_id == id))
                .unwrap_or(0);
        }
    }
    fn run_rows(&self) -> Vec<&RunStatus> {
        let Some(s) = &self.snapshot else {
            return Vec::new();
        };
        if self.route == HostRoute::ActiveRuns {
            s.runs
                .active_runs
                .iter()
                .chain(&s.runs.stalled_runs)
                .collect()
        } else {
            s.runs.recent_runs.iter().collect()
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
                        s.runs.stalled_runs.len()
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
                if self.route == HostRoute::ActiveRuns {
                    lines.push("IN PROGRESS".into());
                    append_runs(&mut lines, &s.runs.active_runs);
                    lines.push("NEEDS ATTENTION".into());
                    append_runs(&mut lines, &s.runs.stalled_runs);
                } else {
                    lines.push(
                        "GLOBAL RUN FEED · recent/history · canonical state and evidence".into(),
                    );
                    append_runs(&mut lines, &s.runs.recent_runs);
                }
                lines.push("g: open selected run through goto-work · ↑/↓ select".into());
                if let Some(run) = self.run_rows().get(self.selected) {
                    lines.push(format!("Selected: {} · {}", run.run_id, run.root));
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
fn append_runs(lines: &mut Vec<String>, runs: &[RunStatus]) {
    if runs.is_empty() {
        lines.push("  None".into());
    }
    for r in runs {
        lines.push(format!(
            "{} · {} / {} · {} · {}",
            r.state, r.agent, r.skill, r.run_id, r.root
        ));
        lines.push(format!(
            "  {} · evidence: {} {} {}",
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
    let active = host
        .snapshot
        .as_ref()
        .map(|s| s.live_count().to_string())
        .unwrap_or_else(|| "?".into());
    let labels = HostRoute::ALL
        .iter()
        .enumerate()
        .map(|(i, r)| {
            if *r == HostRoute::ActiveRuns {
                format!("{} {} · {active}", i + 1, r.label())
            } else {
                format!("{} {}", i + 1, r.label())
            }
        })
        .collect::<Vec<_>>();
    frame.render_widget(
        Tabs::new(labels)
            .select(host.route.index())
            .highlight_style(Style::default().fg(Color::Yellow))
            .block(
                Block::default()
                    .borders(Borders::BOTTOM)
                    .title("Operator Frame · Global"),
            ),
        areas[0],
    );
    frame.render_widget(
        Paragraph::new(host.lines(config).join("\n"))
            .scroll((host.offset.min(u16::MAX as usize) as u16, 0))
            .wrap(Wrap { trim: false }),
        areas[1],
    );
    let status = host
        .project_input
        .as_ref()
        .map(|p| format!("Open project: {p}  · Enter confirm · Esc cancel"))
        .unwrap_or_else(|| {
            format!(
                "{}\n1–6 views · o Open project · PgUp/PgDn scroll · q close · LIVE {active}",
                host.notice
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
}
fn perform_action(config: &AppConfig, action: HostAction) -> String {
    match action {
        HostAction::OpenProject(root) => {
            let home = std::env::var_os("HOME").map(std::path::PathBuf::from);
            let root = match crate::config::resolve_destination_repo(&root, home.as_deref()) {
                Ok(root) => root,
                Err(error) => return format!("Open refused: {error}"),
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
                host.notice = receipt;
                host.action_pending = false;
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
                            KeyCode::Up => host.selected = host.selected.saturating_sub(1),
                            KeyCode::Down => {
                                let n = if host.route == HostRoute::Projects {
                                    host.snapshot
                                        .as_ref()
                                        .map(|s| s.projects.len())
                                        .unwrap_or(0)
                                } else {
                                    host.run_rows().len()
                                };
                                host.selected = (host.selected + 1).min(n.saturating_sub(1));
                            }
                            KeyCode::PageDown => {
                                host.offset = host
                                    .offset
                                    .saturating_add(8)
                                    .min(host.lines(&config).len().saturating_sub(1))
                            }
                            KeyCode::PageUp => host.offset = host.offset.saturating_sub(8),
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
                            _ => {}
                        }
                    }
                }
                Event::Mouse(mouse)
                    if mouse.kind == MouseEventKind::Down(event::MouseButton::Left)
                        && mouse.row == 1 =>
                {
                    // Tabs use one cell of padding at each end and a one-cell divider.
                    let active = host
                        .snapshot
                        .as_ref()
                        .map(|s| s.live_count().to_string())
                        .unwrap_or_else(|| "?".into());
                    let mut x = 0;
                    for (i, route) in HostRoute::ALL.iter().enumerate() {
                        let label = if *route == HostRoute::ActiveRuns {
                            format!("{} {} · {active}", i + 1, route.label())
                        } else {
                            format!("{} {}", i + 1, route.label())
                        };
                        let end = x + label.chars().count() + 2;
                        if (x..end).contains(&(mouse.column as usize)) {
                            host.navigate(*route);
                            break;
                        }
                        x = end + 1;
                    }
                }
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
        let receipt = perform_action(&config, HostAction::OpenProject(repo.display().to_string()));
        assert!(receipt.contains("guest-create refused: fixture denial"));
        assert!(receipt.contains("23"));
        assert!(!receipt.contains("Opened workspace"));
        assert!(!config.state_root.exists());
    }
}
