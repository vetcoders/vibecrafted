pub mod app;
pub mod config;
pub mod launch;
pub mod memory;
pub mod mission_control;
pub mod mux;
pub mod observe;
pub mod polarize;
pub mod procs;
pub mod run_detail;
pub mod skills_catalog;
pub mod state;
pub mod ui;

use anyhow::Context;
use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyModifiers};
use crossterm::execute;
use crossterm::terminal::{
    EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode,
};
use notify::{Config as NotifyConfig, RecommendedWatcher, RecursiveMode, Watcher};
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use std::io;
use std::path::{Path, PathBuf};
use std::process::Output;
use std::sync::mpsc::{self, Sender};
use std::thread;
use std::time::{Duration, Instant};

const CHANGE_DEBOUNCE: Duration = Duration::from_millis(100);
const RENDER_REFRESH_INTERVAL: Duration = Duration::from_secs(1);
const OBSERVE_REFRESH_INTERVAL: Duration = Duration::from_secs(2);
const WATCHER_FALLBACK_INTERVAL: Duration = Duration::from_secs(30);
/// Watch events are debounced for 100 ms and serviced by the next UI poll.
/// The one-second bound includes the default 250 ms tick and watcher delivery jitter.
pub const MAX_CHANGE_LATENCY: Duration = Duration::from_secs(1);

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
struct RefreshPlan {
    control_plane: bool,
    polarize: bool,
    mission_control: bool,
    rendered_runs: bool,
    observe: bool,
}

#[derive(Debug)]
struct RefreshScheduler {
    state_watcher_active: bool,
    artifact_watcher_active: bool,
    state_dirty_since: Option<Instant>,
    polarize_dirty_since: Option<Instant>,
    mission_dirty_since: Option<Instant>,
    last_control_plane: Instant,
    last_artifacts: Instant,
    last_rendered_runs: Instant,
    last_observe: Instant,
}

impl RefreshScheduler {
    fn new(now: Instant, state_watcher_active: bool, artifact_watcher_active: bool) -> Self {
        Self {
            state_watcher_active,
            artifact_watcher_active,
            state_dirty_since: None,
            polarize_dirty_since: None,
            mission_dirty_since: None,
            last_control_plane: now,
            last_artifacts: now,
            last_rendered_runs: now,
            last_observe: now,
        }
    }

    fn mark_state_changed(&mut self, now: Instant) {
        self.state_dirty_since.get_or_insert(now);
    }

    fn mark_artifacts_changed(&mut self, change: ArtifactChange, now: Instant) {
        if change.polarize {
            self.polarize_dirty_since.get_or_insert(now);
        }
        if change.mission_control {
            self.mission_dirty_since.get_or_insert(now);
        }
    }

    fn plan(&mut self, now: Instant) -> RefreshPlan {
        let mut plan = RefreshPlan::default();
        if due(self.state_dirty_since, now, CHANGE_DEBOUNCE)
            || (!self.state_watcher_active
                && now.duration_since(self.last_control_plane) >= WATCHER_FALLBACK_INTERVAL)
        {
            plan.control_plane = true;
            self.state_dirty_since = None;
            self.last_control_plane = now;
        }
        if due(self.polarize_dirty_since, now, CHANGE_DEBOUNCE)
            || (!self.artifact_watcher_active
                && now.duration_since(self.last_artifacts) >= WATCHER_FALLBACK_INTERVAL)
        {
            plan.polarize = true;
            self.polarize_dirty_since = None;
            self.last_artifacts = now;
        }
        if due(self.mission_dirty_since, now, CHANGE_DEBOUNCE) {
            plan.mission_control = true;
            self.mission_dirty_since = None;
            self.last_artifacts = now;
        }
        if now.duration_since(self.last_rendered_runs) >= RENDER_REFRESH_INTERVAL {
            plan.rendered_runs = !plan.control_plane;
            self.last_rendered_runs = now;
        }
        if now.duration_since(self.last_observe) >= OBSERVE_REFRESH_INTERVAL {
            plan.observe = true;
            self.last_observe = now;
        }
        plan
    }
}

fn due(since: Option<Instant>, now: Instant, delay: Duration) -> bool {
    since.is_some_and(|changed_at| now.duration_since(changed_at) >= delay)
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
struct ArtifactChange {
    polarize: bool,
    mission_control: bool,
}

pub use app::{App, AppTab, DeepAction, DispatchFocus, LaunchFocus, QueueScope};
pub use config::{AppConfig, CliOptions, build_config, parse_args};
pub use launch::{LaunchCommand, LaunchKind};
pub use mission_control::{
    ActionPriority, ActionQueueItem, ActionQueueKind, ActiveDispatch, AgentStatsRow, DataQuality,
    FailureEntry, FleetHealthSignal, FleetHealthStatus, MissionControlState, SettlementBoardCounts,
    SkillStatsRow, WaveSegment, WaveState, default_artifact_root,
};
pub use observe::{ConsoleView, ObserveHealth, ObserveRun, ObserveState};
pub use polarize::{PolarizeBand, PolarizeIntent};
pub use run_detail::{RunDetail, load_run_detail};
pub use skills_catalog::{SkillAgent, SkillEntry, SkillPayload, SkillPayloadKind};

pub fn run_cli() -> anyhow::Result<()> {
    let options = parse_args()?;
    let config = build_config(options);
    let rt = tokio::runtime::Runtime::new()?;
    let _guard = rt.enter();
    run_app(config)
}

fn run_app(config: AppConfig) -> anyhow::Result<()> {
    enable_raw_mode().context("failed to enable raw mode")?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let result = (|| -> anyhow::Result<()> {
        let mut app = App::new(config)?;
        let (state_tx, state_rx) = mpsc::channel();
        let state_watcher = match start_state_watcher(&app.config.state_root, state_tx) {
            Ok(watcher) => Some(watcher),
            Err(error) => {
                app.append_status(format!("state watcher unavailable: {error}"));
                None
            }
        };
        let (artifact_tx, artifact_rx) = mpsc::channel();
        let artifact_watcher =
            match start_artifact_watcher(&crate::polarize::vibecrafted_home(), artifact_tx) {
                Ok(watcher) => Some(watcher),
                Err(error) => {
                    app.append_status(format!("artifact watcher unavailable: {error}"));
                    None
                }
            };
        let mut scheduler = RefreshScheduler::new(
            Instant::now(),
            state_watcher.is_some(),
            artifact_watcher.is_some(),
        );
        loop {
            terminal.draw(|frame| ui::draw(frame, &app))?;
            let last_draw = Instant::now();
            let timeout = app
                .config
                .tick_rate
                .checked_sub(last_draw.elapsed())
                .unwrap_or(Duration::ZERO);

            if event::poll(timeout)?
                && let Event::Key(key) = event::read()?
                && handle_key(&mut app, key)?
            {
                break;
            }

            let now = Instant::now();
            while state_rx.try_recv().is_ok() {
                scheduler.mark_state_changed(now);
            }
            while let Ok(change) = artifact_rx.try_recv() {
                scheduler.mark_artifacts_changed(change, now);
            }
            let mut events = Vec::new();
            if let Some(sub) = &app.mux_subscriber {
                while let Ok(event) = sub.rx.try_recv() {
                    events.push(event);
                }
            }
            if !events.is_empty() {
                for event in events {
                    app.handle_ipc_event(event);
                }
            }
            let plan = scheduler.plan(now);
            if plan.control_plane {
                app.refresh_control_plane();
            }
            if plan.polarize {
                app.refresh_polarize();
            }
            if plan.control_plane || plan.mission_control || plan.polarize {
                app.refresh_mission_control();
            }
            if plan.rendered_runs {
                app.refresh_rendered_runs();
            }
            if plan.observe {
                app.refresh_observe();
            }
        }
        Ok(())
    })();

    shutdown_terminal(&mut terminal)?;
    result
}

fn shutdown_terminal(terminal: &mut Terminal<CrosstermBackend<io::Stdout>>) -> anyhow::Result<()> {
    disable_raw_mode().context("failed to disable raw mode")?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;
    Ok(())
}

fn handle_key(app: &mut App, key: KeyEvent) -> anyhow::Result<bool> {
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
        return Ok(true);
    }

    match app.focus {
        LaunchFocus::EditPrompt => match key.code {
            KeyCode::Char('?') => {
                app.focus = LaunchFocus::Help;
            }
            KeyCode::Esc => {
                app.finish_prompt_edit();
            }
            KeyCode::Char('s') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                app.finish_prompt_edit();
            }
            KeyCode::Enter => {
                app.launch_prompt.push('\n');
            }
            KeyCode::Backspace => {
                app.launch_prompt.pop();
            }
            KeyCode::Char(c) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                app.launch_prompt.push(c);
            }
            _ => {}
        },
        LaunchFocus::Memory => match key.code {
            KeyCode::Esc | KeyCode::Char('q') => {
                app.focus = LaunchFocus::Browse;
            }
            KeyCode::Char('m') => {
                app.refresh_memory();
            }
            KeyCode::Char('w') => {
                launch_aicx_wizard(app)?;
            }
            _ => {}
        },
        LaunchFocus::Search => match key.code {
            KeyCode::Esc | KeyCode::Enter => {
                app.focus = LaunchFocus::Browse;
                if app.search_query.is_empty() {
                    app.append_status("search closed");
                } else {
                    app.append_status(format!(
                        "search: {} ({} runs visible)",
                        app.search_query,
                        app.runs.len()
                    ));
                }
            }
            KeyCode::Backspace => {
                let mut query = app.search_query.clone();
                query.pop();
                app.set_search_query(query);
            }
            KeyCode::Char(c) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                let mut query = app.search_query.clone();
                query.push(c);
                app.set_search_query(query);
            }
            _ => {}
        },
        LaunchFocus::Error => match key.code {
            KeyCode::Char('f') | KeyCode::Char('F')
                if app
                    .error_lines
                    .iter()
                    .any(|l| l.contains("Client drift detected")) =>
            {
                let agent = app.selected_agent().to_string();
                let _ = std::process::Command::new("vc-frame")
                    .args([
                        "run",
                        "--name",
                        "auto-rewire",
                        "--",
                        "rmcp-mux",
                        "wizard",
                        "--strategy",
                        "auto-rewire",
                        &agent,
                    ])
                    .spawn();
                app.focus = LaunchFocus::Browse;
            }
            KeyCode::Esc | KeyCode::Enter | KeyCode::Char('q') => {
                app.focus = LaunchFocus::Browse;
            }
            _ => {}
        },
        LaunchFocus::Artifact => match key.code {
            KeyCode::Esc | KeyCode::Enter | KeyCode::Char('q') => {
                app.focus = LaunchFocus::Browse;
            }
            _ => {}
        },
        LaunchFocus::Browse => match key.code {
            KeyCode::Char('q') | KeyCode::Esc => return Ok(true),
            KeyCode::Char('?') => app.focus = LaunchFocus::Help,
            KeyCode::Tab => app.next_tab(),
            KeyCode::BackTab => app.previous_tab(),
            KeyCode::Up | KeyCode::Char('k') => match app.active_tab() {
                AppTab::Monitor if app.config.view == crate::observe::ConsoleView::Observe => {
                    app.move_observe_selection(-1);
                }
                AppTab::Monitor => app.move_selection(-1),
                AppTab::Dispatch => app.move_dispatch_selection(-1),
                AppTab::Controls => app.move_deep_selection(-1),
                AppTab::MissionControl => app.move_mission_focus(-1),
            },
            KeyCode::Down | KeyCode::Char('j') => match app.active_tab() {
                AppTab::Monitor if app.config.view == crate::observe::ConsoleView::Observe => {
                    app.move_observe_selection(1);
                }
                AppTab::Monitor => app.move_selection(1),
                AppTab::Dispatch => app.move_dispatch_selection(1),
                AppTab::Controls => app.move_deep_selection(1),
                AppTab::MissionControl => app.move_mission_focus(1),
            },
            KeyCode::Char('l') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                app.clear_search();
            }
            KeyCode::Left | KeyCode::Char('h') => match app.active_tab() {
                AppTab::Monitor => {}
                AppTab::Dispatch => app.adjust_dispatch_selection(-1),
                AppTab::Controls => app.move_selection(-1),
                AppTab::MissionControl => app.move_mission_focus(-1),
            },
            KeyCode::Right | KeyCode::Char('l') => match app.active_tab() {
                AppTab::Monitor => {}
                AppTab::Dispatch => app.adjust_dispatch_selection(1),
                AppTab::Controls => app.move_selection(1),
                AppTab::MissionControl => app.move_mission_focus(1),
            },
            KeyCode::Char('1') => app.set_launch_kind(LaunchKind::Workflow),
            KeyCode::Char('2') => app.set_launch_kind(LaunchKind::Research),
            KeyCode::Char('3') => app.set_launch_kind(LaunchKind::Review),
            KeyCode::Char('4') => app.set_launch_kind(LaunchKind::Marbles),
            KeyCode::Char('a') => {
                app.set_active_tab(AppTab::Dispatch);
                app.dispatch_selected = DispatchFocus::Agent as usize;
                app.cycle_agent();
            }
            KeyCode::Char('v') => {
                app.set_active_tab(AppTab::Dispatch);
                app.dispatch_selected = DispatchFocus::Runtime as usize;
                app.cycle_runtime();
            }
            KeyCode::Char('f') => app.toggle_filter(),
            KeyCode::Char('/') => {
                app.focus = LaunchFocus::Search;
                app.append_status("search: type to filter runs, Enter/Esc closes, Ctrl+L clears");
            }
            KeyCode::Char('x') => {
                app.archive_selected_run()?;
            }
            KeyCode::Char('y') => {
                if let Err(error) = app.copy_selected_run_to_clipboard() {
                    app.show_error("clipboard failed", vec![format!("{error:#}")]);
                }
            }
            KeyCode::Char('r') => app.refresh(),
            KeyCode::Char('m') => {
                app.refresh_memory();
                app.focus = LaunchFocus::Memory;
            }
            KeyCode::Char('w') => {
                launch_aicx_wizard(app)?;
            }
            KeyCode::Char('e') => {
                app.set_active_tab(AppTab::Dispatch);
                app.dispatch_selected = DispatchFocus::Prompt as usize;
                app.focus = LaunchFocus::EditPrompt;
            }
            KeyCode::Enter => match app.active_tab() {
                AppTab::Monitor => {
                    if app.selected_run().is_some() {
                        app.set_active_tab(AppTab::Controls);
                    }
                }
                AppTab::Dispatch => {
                    if app.dispatch_focus() == DispatchFocus::Prompt {
                        app.focus = LaunchFocus::EditPrompt;
                    } else {
                        launch_selected(app)?;
                    }
                }
                AppTab::Controls => {
                    run_selected_deep_control(app)?;
                }
                AppTab::MissionControl => {
                    // Mission Control is a read-only situational-awareness
                    // surface. Enter on a focused panel jumps the operator
                    // to the surface that owns the action: Controls (for
                    // action-queue items, failures, and stalls) or stays
                    // on the dashboard for stats-only panels.
                    let focus = app.mission_focus;
                    if focus == 6 && !app.mission_control.action_queue.is_empty() {
                        let preselected = app.preselect_controls_from_mission_queue();
                        app.set_active_tab(AppTab::Controls);
                        if preselected {
                            app.append_status(
                                "Mission Control → Controls: preselected most relevant action",
                            );
                        } else {
                            app.append_status(
                                "Mission Control → Controls: pick an action from the deck",
                            );
                        }
                    }
                }
            },
            KeyCode::Char('d') => {
                app.set_active_tab(AppTab::Controls);
                if app.deep_actions().is_empty() {
                    app.append_status("No operator actions are available.");
                } else {
                    app.append_status("Controls ready: ↑/↓ select action, Enter runs it.");
                }
            }
            _ => {}
        },
        LaunchFocus::Help => match key.code {
            KeyCode::Char('?') | KeyCode::Esc | KeyCode::Enter => {
                app.focus = LaunchFocus::Browse;
            }
            _ => {}
        },
    }
    Ok(false)
}

fn launch_aicx_wizard(app: &mut App) -> anyhow::Result<()> {
    disable_raw_mode().ok();
    let _ = execute!(io::stdout(), LeaveAlternateScreen);
    let result = crate::memory::launch_wizard(&app.memory.project);
    enable_raw_mode().ok();
    let _ = execute!(io::stdout(), EnterAlternateScreen);
    match result {
        Ok(()) => app.append_status("returned from aicx wizard"),
        Err(error) => app.show_error("aicx wizard failed", vec![error.to_string()]),
    }
    Ok(())
}

fn launch_selected(app: &mut App) -> anyhow::Result<()> {
    if !app.config.no_verify_gate && app.launch_runtime != launch::LaunchRuntime::Headless {
        let client_kind = match app.selected_agent() {
            "claude" => rmcp_mux::ipc::ClientKind::Claude,
            "codex" => rmcp_mux::ipc::ClientKind::Codex,
            "gemini" => rmcp_mux::ipc::ClientKind::Gemini,
            "junie" => rmcp_mux::ipc::ClientKind::Junie,
            other => rmcp_mux::ipc::ClientKind::Generic {
                name: other.to_string(),
            },
        };
        if let Err(halt) = launch::pre_launch_verify(client_kind) {
            let error = LaunchRunError::ClientDrift(halt);
            app.show_error(
                "launch failed: client drift",
                error.detail_lines("".to_string()),
            );

            return Ok(());
        }
    }
    let command = app.launch_command();
    let summary = command.command_line();
    if app.launch_runtime == launch::LaunchRuntime::Headless {
        match command.spawn_detached() {
            Ok(child) => {
                app.push_launch_history(summary.clone());
                app.append_status(format!("spawned pid {}: {summary}", child.id()));
            }
            Err(error) => app.show_error(
                "launch failed before spawn",
                vec![summary.clone(), format!("{error:#}")],
            ),
        }
    } else if let Err(error) = suspend_and_run(&command) {
        app.show_error("launch failed", error.detail_lines(summary));
    } else {
        app.push_launch_history(summary.clone());
        app.append_status(format!("launched: {summary}"));
    }
    app.refresh();
    Ok(())
}

fn run_selected_deep_control(app: &mut App) -> anyhow::Result<()> {
    let Some(action) = app.selected_deep_action() else {
        app.append_status("No deep action is available for the selected run.");
        app.focus = LaunchFocus::Browse;
        return Ok(());
    };
    if matches!(
        action,
        DeepAction::OpenReport(_) | DeepAction::OpenTranscript(_) | DeepAction::OpenRoot(_)
    ) {
        if let Err(error) = app.open_artifact(&action) {
            app.show_error("artifact open failed", vec![format!("{error:#}")]);
        }
        return Ok(());
    }
    if matches!(action, DeepAction::PolarizeIntent { .. }) {
        if let Err(error) = app.open_polarize_intent(&action) {
            app.show_error("polarize prism open failed", vec![format!("{error:#}")]);
        }
        return Ok(());
    }
    let command = deep_control_command(app, &action);
    let summary = command.command_line();
    if let Err(error) = suspend_and_run(&command) {
        app.show_error("action failed", error.detail_lines(summary));
    } else {
        app.push_launch_history(summary.clone());
        app.append_status(format!("ran: {summary}"));
        app.focus = LaunchFocus::Browse;
    }
    app.refresh();
    Ok(())
}

fn deep_control_command(app: &App, action: &DeepAction) -> LaunchCommand {
    match action {
        DeepAction::AttachSession(session) => LaunchCommand {
            program: app.config.command_deck.clone(),
            args: vec!["dashboard".into(), "attach".into(), session.clone().into()],
            env: Default::default(),
        },
        DeepAction::ResumeSession { agent, session } => LaunchCommand {
            program: app.config.command_deck.clone(),
            args: vec![
                "resume".into(),
                agent.clone().into(),
                "--session".into(),
                session.clone().into(),
            ],
            env: Default::default(),
        },
        DeepAction::MuxHealth { service } => LaunchCommand {
            // `rmcp-mux` is expected on PATH (installed via the rmcp-mux
            // installer or `cargo install rmcp-mux`). The default config
            // path is `~/.codex/mcp.json`, which `rmcp-mux` resolves on
            // its own. Operators with a non-default config should set
            // `RMCP_MUX_CONFIG` (read by rmcp-mux directly) rather than
            // teach the operator console a second config surface.
            program: PathBuf::from("rmcp-mux"),
            args: vec!["health".into(), "--service".into(), service.clone().into()],
            env: Default::default(),
        },
        DeepAction::SkillLaunch {
            skill,
            agent,
            payload,
        } => crate::skills_catalog::build_skill_launch_command(
            &app.config.command_deck,
            skill,
            *agent,
            crate::skills_catalog::SkillAgent::from_cli_token(app.selected_agent()),
            payload,
            app.launch_env(),
        ),
        DeepAction::OpenReport(_)
        | DeepAction::OpenTranscript(_)
        | DeepAction::OpenRoot(_)
        | DeepAction::PolarizeIntent { .. }
        | DeepAction::MuxRestart(_)
        | DeepAction::MuxVerifyClient(_)
        | DeepAction::MuxFixClientDrift(_) => {
            unreachable!("artifact actions are handled by the native operator viewer")
        }
    }
}

#[derive(Debug)]
pub enum LaunchRunError {
    Exec {
        message: String,
        stderr: String,
        /// First error observed by the vc_frame readiness probe before the launch
        /// gave up. Distinguishes "session not visible" from "probe could not
        /// run" (bad flags, socket/config errors, missing binary). When None,
        /// the probe either succeeded or was never attempted.
        probe_error: Option<String>,
        /// Probe diagnostic captured at the deadline-kill branch, where stderr
        /// from the killed child is intentionally not drained.
        probe_error_at_deadline: Option<String>,
    },
    ClientDrift(crate::launch::VerifyHalt),
}

impl LaunchRunError {
    pub fn detail_lines(&self, summary: String) -> Vec<String> {
        match self {
            Self::Exec {
                message,
                stderr,
                probe_error,
                probe_error_at_deadline,
            } => {
                let mut lines = vec![format!("command: {summary}"), format!("error: {message}")];
                if let Some(pe) = probe_error {
                    lines.push(format!("readiness probe: {pe}"));
                }
                if let Some(pe) = probe_error_at_deadline {
                    lines.push(format!("readiness timeout probe: {pe}"));
                }
                if !stderr.trim().is_empty() {
                    lines.push(String::new());
                    lines.push("stderr:".to_string());
                    lines.extend(stderr.lines().map(ToOwned::to_owned));
                }
                lines
            }
            Self::ClientDrift(halt) => {
                let mut lines = vec![
                    "Client drift detected. Dispatch halted.".to_string(),
                    "Non-mux servers found:".to_string(),
                ];
                match halt {
                    crate::launch::VerifyHalt::Drift(servers) => {
                        for entry in servers {
                            lines.push(format!(
                                "  {} ({}:{})",
                                entry.client, entry.path, entry.line
                            ));
                        }
                    }
                    crate::launch::VerifyHalt::Timeout => {
                        lines.push(
                            "  Timeout waiting for verify response from rmcp-mux.".to_string(),
                        );
                    }
                }
                lines.push(String::new());
                lines.push("Press F to auto-fix (spawns rmcp-mux wizard).".to_string());
                lines
            }
        }
    }
}

fn suspend_and_run(command: &LaunchCommand) -> Result<(), LaunchRunError> {
    let mut stdout = io::stdout();
    disable_raw_mode()
        .context("failed to disable raw mode before launch")
        .map_err(launch_error)?;
    execute!(stdout, LeaveAlternateScreen).map_err(launch_error)?;

    let launch_result: Result<Output, LaunchRunError> =
        match command.spawn_interactive_with_stderr() {
            Ok(child) => wait_for_interactive_launch(command, child),
            Err(error) => Err(launch_error(error)),
        };

    let leave_result =
        execute!(stdout, EnterAlternateScreen).context("failed to restore alternate screen");
    let raw_result = enable_raw_mode().context("failed to re-enable raw mode after launch");

    leave_result.map_err(launch_error)?;
    raw_result.map_err(launch_error)?;
    let output = launch_result?;
    if output.status.success() {
        Ok(())
    } else {
        Err(LaunchRunError::Exec {
            message: format!("command exited with {}", output.status),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
            probe_error: None,
            probe_error_at_deadline: None,
        })
    }
}

/// How long `wait_for_interactive_launch` will keep polling the vc_frame
/// readiness probe before giving up. Kept short so the operator does not
/// freeze on a launch that never came up; long enough that real interactive
/// launches on the host can register their named socket.
/// Bounded startup window for a freshly launched vc-frame session.
///
/// Two seconds produced false failures under normal concurrent workspace load:
/// the child was healthy but had not been scheduled early enough for the
/// visibility probe. Five seconds remains fail-closed while tolerating brief
/// host pressure from builds, indexing, and existing terminal sessions.
pub const READINESS_DEADLINE: Duration = Duration::from_secs(5);

pub fn wait_for_interactive_launch(
    command: &LaunchCommand,
    mut child: std::process::Child,
) -> Result<Output, LaunchRunError> {
    if let Some(probe) = command.readiness_probe() {
        let deadline = Instant::now() + READINESS_DEADLINE;
        let mut probe_error: Option<String> = None;
        while Instant::now() < deadline {
            match probe.is_session_visible() {
                Ok(true) => {
                    return child
                        .wait_with_output()
                        .map_err(|err| LaunchRunError::Exec {
                            message: format!("launch process failed: {err}"),
                            stderr: String::new(),
                            probe_error: probe_error.clone(),
                            probe_error_at_deadline: None,
                        });
                }
                Ok(false) => {}
                Err(error) => {
                    // Preserve the FIRST probe error (P2-02). Bad flags,
                    // socket/config errors, or permission failures should
                    // surface in the error overlay instead of being
                    // collapsed into a generic "session not visible".
                    if probe_error.is_none() {
                        probe_error = Some(format!("{error:#}"));
                    }
                }
            }
            match child.try_wait() {
                Ok(Some(_)) => {
                    let output = child
                        .wait_with_output()
                        .map_err(|err| LaunchRunError::Exec {
                            message: format!("launch process failed: {err}"),
                            stderr: String::new(),
                            probe_error: probe_error.clone(),
                            probe_error_at_deadline: None,
                        })?;
                    if output.status.success() {
                        return Err(LaunchRunError::Exec {
                            message: format!(
                                "vc_frame session '{}' exited before the readiness probe saw it",
                                probe.session_name
                            ),
                            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
                            probe_error,
                            probe_error_at_deadline: None,
                        });
                    }
                    return Ok(output);
                }
                Ok(None) => {}
                Err(err) => {
                    return Err(LaunchRunError::Exec {
                        message: format!("failed to inspect launch child: {err}"),
                        stderr: String::new(),
                        probe_error,
                        probe_error_at_deadline: None,
                    });
                }
            }
            thread::sleep(Duration::from_millis(100));
        }
        // Deadline exceeded with the named session never visible AND the
        // child still running. The README contract says a launch that exits
        // before its session appears is reported as failure; we extend that
        // to "a launch whose session never appears within the readiness
        // window is also a failure", and we do NOT silently fall through to
        // `child.wait_with_output()` (which would either hang on a healthy
        // vc_frame forever or report success once the operator finally quits
        // it manually — both produce false-success class outcomes).
        //
        // Kill the child so we do not leave a hanging vc_frame socket pointing
        // at the same session name; subsequent launches with the same
        // `--session` value would fight an orphan otherwise.
        let _ = child.kill();
        // Reap the killed child without `wait_with_output()`: any
        // grandchild process (e.g. a long `sleep` inside a launched shell)
        // that inherited our piped stderr would keep the pipe alive past
        // the SIGKILL, defeating the whole readiness timeout. `wait()`
        // blocks only on the direct child's exit, which the kill
        // guarantees promptly.
        let _ = child.wait();
        let probe_error_at_deadline = probe_error.as_ref().map(|error| {
            format!(
                "killed after {}ms, last probe error: {error}",
                READINESS_DEADLINE.as_millis()
            )
        });
        return Err(LaunchRunError::Exec {
            message: format!(
                "vc_frame session '{}' did not appear within the {}ms readiness window",
                probe.session_name,
                READINESS_DEADLINE.as_millis()
            ),
            stderr: String::new(),
            probe_error,
            probe_error_at_deadline,
        });
    }
    child
        .wait_with_output()
        .map_err(|err| LaunchRunError::Exec {
            message: format!("launch process failed: {err}"),
            stderr: String::new(),
            probe_error: None,
            probe_error_at_deadline: None,
        })
}

fn launch_error(error: impl Into<anyhow::Error>) -> LaunchRunError {
    let error = error.into();
    LaunchRunError::Exec {
        message: format!("{error:#}"),
        stderr: String::new(),
        probe_error: None,
        probe_error_at_deadline: None,
    }
}

fn start_state_watcher(path: &Path, tx: Sender<()>) -> anyhow::Result<RecommendedWatcher> {
    let mut watcher = RecommendedWatcher::new(
        move |_| {
            let _ = tx.send(());
        },
        NotifyConfig::default(),
    )?;
    watcher.watch(path, RecursiveMode::Recursive)?;
    Ok(watcher)
}

fn start_artifact_watcher(
    path: &Path,
    tx: Sender<ArtifactChange>,
) -> anyhow::Result<RecommendedWatcher> {
    let mut watcher = RecommendedWatcher::new(
        move |event: notify::Result<notify::Event>| {
            let Ok(event) = event else {
                return;
            };
            let change = classify_artifact_change(&event.paths);
            if change.polarize || change.mission_control {
                let _ = tx.send(change);
            }
        },
        NotifyConfig::default(),
    )?;
    watcher.watch(path, RecursiveMode::Recursive)?;
    Ok(watcher)
}

fn classify_artifact_change(paths: &[PathBuf]) -> ArtifactChange {
    ArtifactChange {
        polarize: paths.iter().any(|path| {
            path.components()
                .any(|component| component.as_os_str() == "polarize")
        }),
        mission_control: paths.iter().any(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.ends_with(".meta.json"))
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::launch::LaunchRuntime;
    use crate::state::{ControlPlaneState, RenderedRun, RunKind, RunSnapshot};

    fn sample_run(run_id: &str, agent: &str, session: &str) -> RenderedRun {
        RenderedRun {
            snapshot: RunSnapshot {
                run_id: run_id.to_string(),
                session_id: Some(format!("sess-{run_id}")),
                agent: Some(agent.to_string()),
                skill: Some("workflow".to_string()),
                mode: Some("implement".to_string()),
                state: Some("running".to_string()),
                status: None,
                started_at: Some("2026-04-19T10:00:00Z".to_string()),
                updated_at: Some("2026-04-19T10:01:00Z".to_string()),
                last_heartbeat: Some("2026-04-19T10:01:30Z".to_string()),
                root: Some(format!("/tmp/{run_id}")),
                operator_session: Some(session.to_string()),
                latest_report: Some(format!("/tmp/{run_id}/report.md")),
                latest_transcript: Some(format!("/tmp/{run_id}/transcript.log")),
                last_error: None,
                extra: Default::default(),
            },
            kind: RunKind::Active,
            age_label: "just now".to_string(),
            recent_events: Vec::new(),
        }
    }

    fn sample_app() -> App {
        App {
            mux_subscriber: None,
            config: AppConfig {
                no_verify_gate: false,
                state_root: "/tmp/state".into(),
                command_deck: "/usr/bin/vibecrafted".into(),
                launch_root: "/tmp/repo".into(),
                launch_runtime: LaunchRuntime::Terminal,
                terminal_binary: "vc-frame".into(),
                tick_rate: Duration::from_millis(250),
                server: "http://127.0.0.1:3024".into(),
                view: crate::observe::ConsoleView::Full,
            },
            state: ControlPlaneState::empty("/tmp/state"),
            runs: vec![
                sample_run("run-1", "codex", "operator-1"),
                sample_run("run-2", "claude", "operator-2"),
            ],
            selected: 0,
            active_tab: AppTab::Monitor.index(),
            launch_kind: LaunchKind::Workflow,
            launch_agent: 0,
            launch_prompt: "Ship the operator surface.".to_string(),
            launch_runtime: LaunchRuntime::Terminal,
            dispatch_selected: DispatchFocus::Kind as usize,
            focus: LaunchFocus::Browse,
            status_line: String::new(),
            launch_history: Vec::new(),
            deep_selected: 0,
            queue_scope: QueueScope::Live,
            search_query: String::new(),
            error_title: String::new(),
            error_lines: Vec::new(),
            artifact_title: String::new(),
            artifact_lines: Vec::new(),
            mux_summaries: Vec::new(),
            polarize_intents: Vec::new(),
            mission_control: crate::mission_control::MissionControlState::default(),
            mission_focus: 0,
            mission_artifact_root: std::path::PathBuf::from("/tmp/vc-op-mission-test"),
            observe: Default::default(),
            memory: Default::default(),
        }
    }

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }

    #[test]
    fn handle_key_cycles_tabs_with_tab_and_shift_tab() {
        let mut app = sample_app();

        assert_eq!(app.active_tab(), AppTab::Monitor);
        handle_key(&mut app, key(KeyCode::Tab)).unwrap();
        assert_eq!(app.active_tab(), AppTab::Dispatch);

        handle_key(&mut app, key(KeyCode::BackTab)).unwrap();
        assert_eq!(app.active_tab(), AppTab::Monitor);
    }

    #[test]
    fn handle_key_routes_arrows_inside_the_active_tab() {
        let mut app = sample_app();

        handle_key(&mut app, key(KeyCode::Down)).unwrap();
        assert_eq!(app.selected, 1);

        app.set_active_tab(AppTab::Dispatch);
        handle_key(&mut app, key(KeyCode::Down)).unwrap();
        assert_eq!(app.dispatch_focus(), DispatchFocus::Agent);

        handle_key(&mut app, key(KeyCode::Right)).unwrap();
        assert_eq!(app.selected_agent(), "codex");

        app.set_active_tab(AppTab::Controls);
        handle_key(&mut app, key(KeyCode::Down)).unwrap();
        assert_eq!(app.deep_selected, 1);
    }

    #[test]
    fn handle_key_enters_prompt_edit_from_dispatch_prompt_row() {
        let mut app = sample_app();
        app.set_active_tab(AppTab::Dispatch);
        app.dispatch_selected = DispatchFocus::Prompt as usize;

        handle_key(&mut app, key(KeyCode::Enter)).unwrap();

        assert_eq!(app.focus, LaunchFocus::EditPrompt);
    }

    #[test]
    fn handle_key_shortcuts_jump_to_dispatch_controls_and_prime_selection() {
        let mut app = sample_app();

        handle_key(&mut app, key(KeyCode::Char('a'))).unwrap();
        assert_eq!(app.active_tab(), AppTab::Dispatch);
        assert_eq!(app.dispatch_focus(), DispatchFocus::Agent);
        assert_eq!(app.selected_agent(), "codex");

        handle_key(&mut app, key(KeyCode::Char('v'))).unwrap();
        assert_eq!(app.active_tab(), AppTab::Dispatch);
        assert_eq!(app.dispatch_focus(), DispatchFocus::Runtime);
        assert_eq!(app.launch_runtime, LaunchRuntime::Visible);

        app.set_active_tab(AppTab::Monitor);
        handle_key(&mut app, key(KeyCode::Char('d'))).unwrap();
        assert_eq!(app.active_tab(), AppTab::Controls);
        assert!(app.status_line.contains("Controls ready"));
    }

    #[test]
    fn handle_key_controls_can_move_across_run_list_and_prompt_edit_saves_multiline_prompt() {
        let mut app = sample_app();
        app.set_active_tab(AppTab::Controls);

        handle_key(&mut app, key(KeyCode::Right)).unwrap();
        assert_eq!(app.selected, 1);

        handle_key(&mut app, key(KeyCode::Left)).unwrap();
        assert_eq!(app.selected, 0);

        app.set_active_tab(AppTab::Dispatch);
        app.focus = LaunchFocus::EditPrompt;
        handle_key(&mut app, key(KeyCode::Enter)).unwrap();
        handle_key(&mut app, key(KeyCode::Char('n'))).unwrap();
        handle_key(&mut app, key(KeyCode::Esc)).unwrap();
        assert!(app.launch_prompt.contains("\nn"));
        assert_eq!(app.focus, LaunchFocus::Browse);
        assert!(app.status_line.contains("prompt updated"));
    }

    #[test]
    fn set_active_tab_resets_focus_to_browse() {
        let mut app = sample_app();
        app.focus = LaunchFocus::EditPrompt;

        app.set_active_tab(AppTab::Controls);

        assert_eq!(app.active_tab(), AppTab::Controls);
        assert_eq!(app.focus, LaunchFocus::Browse);
    }

    #[test]
    fn launch_run_error_detail_lines_render_probe_error_when_present() {
        let error = LaunchRunError::Exec {
            message: "command exited with status: 1".to_string(),
            stderr: "boom\nstack\n".to_string(),
            probe_error: Some(
                "failed to run vc_frame readiness probe: No such file or directory".to_string(),
            ),
            probe_error_at_deadline: None,
        };
        let lines = error.detail_lines("vc_frame --session foo".to_string());
        assert_eq!(lines[0], "command: vc_frame --session foo");
        assert_eq!(lines[1], "error: command exited with status: 1");
        assert!(
            lines.iter().any(|line| line.contains("readiness probe:")
                && line.contains("No such file or directory")),
            "probe_error must be surfaced in the operator error overlay: lines={lines:?}"
        );
        assert!(lines.iter().any(|line| line == "stderr:"));
        assert!(lines.iter().any(|line| line == "boom"));
    }

    #[test]
    fn launch_run_error_detail_lines_skip_probe_section_when_none() {
        let error = LaunchRunError::Exec {
            message: "command exited with status: 2".to_string(),
            stderr: String::new(),
            probe_error: None,
            probe_error_at_deadline: None,
        };
        let lines = error.detail_lines("vc_frame --session foo".to_string());
        assert!(
            !lines.iter().any(|line| line.contains("readiness probe:")),
            "probe_error=None must not render an empty probe section: lines={lines:?}"
        );
    }

    #[test]
    fn ui_ticks_do_not_schedule_expensive_projection_or_prism_discovery() {
        let start = Instant::now();
        let mut scheduler = RefreshScheduler::new(start, true, true);
        let mut control_plane_refreshes = 0;
        let mut prism_discoveries = 0;

        for tick in 1..=400 {
            let plan = scheduler.plan(start + Duration::from_millis(tick * 10));
            control_plane_refreshes += usize::from(plan.control_plane);
            prism_discoveries += usize::from(plan.polarize);
        }

        assert_eq!(control_plane_refreshes, 0);
        assert_eq!(prism_discoveries, 0);
    }

    #[test]
    fn changed_state_and_prism_are_scheduled_inside_the_documented_bound() {
        let start = Instant::now();
        let changed_at = start + Duration::from_secs(1);
        let mut scheduler = RefreshScheduler::new(start, true, true);
        scheduler.mark_state_changed(changed_at);
        scheduler.mark_artifacts_changed(
            ArtifactChange {
                polarize: true,
                mission_control: true,
            },
            changed_at,
        );

        let before_debounce = scheduler.plan(changed_at + CHANGE_DEBOUNCE / 2);
        assert!(!before_debounce.control_plane);
        assert!(!before_debounce.polarize);

        let visible_at = changed_at + MAX_CHANGE_LATENCY;
        let due = scheduler.plan(visible_at);
        assert!(due.control_plane);
        assert!(due.polarize);
        assert!(due.mission_control);

        let unchanged_tick = scheduler.plan(visible_at + Duration::from_millis(10));
        assert!(!unchanged_tick.control_plane);
        assert!(!unchanged_tick.polarize);
        assert!(!unchanged_tick.mission_control);
    }

    #[test]
    fn artifact_invalidation_ignores_unrelated_churn() {
        let unrelated = classify_artifact_change(&[
            PathBuf::from("/tmp/home/artifacts/run/transcript.log"),
            PathBuf::from("/tmp/home/cache.json"),
        ]);
        assert_eq!(unrelated, ArtifactChange::default());

        let relevant = classify_artifact_change(&[
            PathBuf::from("/tmp/home/artifacts/project/polarize/run/prism.json"),
            PathBuf::from("/tmp/home/artifacts/run/report.meta.json"),
        ]);
        assert!(relevant.polarize);
        assert!(relevant.mission_control);
    }

    #[test]
    fn explicit_refresh_bypasses_debounce_and_loads_new_control_plane_truth() {
        let dir = tempfile::tempdir().expect("tempdir");
        let state_root = dir.path().join("control_plane");
        std::fs::create_dir_all(state_root.join("runs")).expect("runs dir");
        std::fs::write(
            state_root.join("runs/forced-refresh.json"),
            r#"{
                "run_id": "forced-refresh",
                "agent": "codex",
                "skill": "hydrate",
                "state": "running",
                "updated_at": "2026-08-25T21:27:30Z"
            }"#,
        )
        .expect("run snapshot");

        let now = Instant::now();
        let mut scheduler = RefreshScheduler::new(now, true, true);
        scheduler.mark_state_changed(now);
        assert!(
            !scheduler.plan(now).control_plane,
            "change remains debounced"
        );

        let mut app = sample_app();
        app.config.state_root = state_root;
        app.refresh_control_plane();

        assert!(
            app.runs
                .iter()
                .any(|run| run.snapshot.run_id == "forced-refresh"),
            "the explicit refresh path must load disk truth without waiting for the scheduler"
        );
    }

    #[test]
    fn render_only_refresh_preserves_selection_by_run_id() {
        let mut app = sample_app();
        app.selected = 1;
        let expected = app.runs[1].snapshot.run_id.clone();
        app.state.runs = app
            .runs
            .iter()
            .rev()
            .map(|run| run.snapshot.clone())
            .collect();
        app.state.retained_runs = app.state.runs.clone();

        app.refresh_rendered_runs();

        assert_eq!(app.selected_run().unwrap().snapshot.run_id, expected);
    }
}
