pub mod app;
pub mod catalog;
pub mod config;
pub mod home;
pub mod launch;
pub mod layout;
pub mod memory;
pub mod mission_control;
pub mod mux;
pub mod observe;
pub mod polarize;
pub mod procs;
pub mod refresh;
pub mod run_detail;
pub mod skills_catalog;
pub mod state;
pub mod ui;
pub mod usage;

use anyhow::Context;
use crossterm::event::{
    self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEvent, KeyModifiers,
    MouseButton, MouseEvent, MouseEventKind,
};
use crossterm::execute;
use crossterm::terminal::{
    EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode,
};
use notify::{Config as NotifyConfig, RecommendedWatcher, RecursiveMode, Watcher};
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use std::collections::hash_map::DefaultHasher;
use std::fs;
use std::hash::{Hash, Hasher};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::mpsc::{self, Sender};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

use crate::refresh::{
    CanonicalRefreshSource, CanonicalTranscriptSource, RefreshNeeds, RefreshSeed, RefreshSource,
    RefreshWorker, TranscriptSource, TranscriptWorker,
};

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
    observe_failures: u32,
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
            observe_failures: 0,
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

    fn note_observe_result(&mut self, ok: bool) {
        if ok {
            self.observe_failures = 0;
        } else {
            self.observe_failures = self.observe_failures.saturating_add(1).min(4);
        }
    }

    fn observe_interval(&self) -> Duration {
        let shift = self.observe_failures.min(4);
        OBSERVE_REFRESH_INTERVAL
            .saturating_mul(1 << shift)
            .min(WATCHER_FALLBACK_INTERVAL)
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
        if now.duration_since(self.last_observe) >= self.observe_interval() {
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
pub use catalog::{CatalogState, LauncherCatalog};
pub use config::{AppConfig, CliOptions, build_config, parse_args};
pub use home::{HomeBand, HomeCounts, HomeNavigation, HomeScope, HomeState, HomeSurface};
pub use launch::{
    Admission, Confirmation, DeclarationAudit, Environment, LaunchCommand, LaunchExpectation,
    LaunchKind, LaunchOutcome, LaunchReceipt, LauncherRun, PermissionPolicy, Presentation,
    SandboxChoice,
};
pub use mission_control::{
    ActionPriority, ActionQueueItem, ActionQueueKind, ActiveDispatch, AgentStatsRow, DataQuality,
    FailureEntry, FleetHealthSignal, FleetHealthStatus, MissionControlState, SettlementBoardCounts,
    SkillStatsRow, WaveSegment, WaveState, default_artifact_root,
};
pub use observe::{ConsoleView, ObserveHealth, ObserveRun, ObserveState};
pub use polarize::{PolarizeBand, PolarizeIntent};
pub use run_detail::{RunDetail, load_run_detail};
pub use skills_catalog::{SkillEntry, SkillPayloadKind};

/// Work that must not block the draw loop: the launcher catalog probe, every
/// launch, and control-plane refreshes. They run on their own threads and
/// report back as messages.
#[derive(Debug)]
pub enum BackgroundMessage {
    Catalog(CatalogState),
    Refresh(Box<refresh::RefreshResult>),
    Transcript(Box<refresh::TranscriptResult>),
    Launch(Box<LaunchOutcome>),
    /// Stopped on the worker thread before the launcher was started, so
    /// nothing was admitted and there is nothing to be uncertain about.
    LaunchHalted {
        summary: String,
        detail: Vec<String>,
    },
}

/// Ask the launcher for its catalog off the UI thread.
fn spawn_catalog_load(app: &App, tx: &Sender<BackgroundMessage>) {
    let deck = app.config.command_deck.clone();
    let env = app.launch_env();
    let tx = tx.clone();
    thread::spawn(move || {
        let state = match crate::catalog::LauncherCatalog::load(&deck, &env) {
            Ok(catalog) => CatalogState::Ready(catalog),
            Err(error) => CatalogState::Failed(format!("{error:#}")),
        };
        let _ = tx.send(BackgroundMessage::Catalog(state));
    });
}

pub fn run_cli() -> anyhow::Result<()> {
    let options = parse_args()?;
    let config = build_config(options);
    let rt = tokio::runtime::Runtime::new()?;
    let _guard = rt.enter();
    run_app(config)
}

/// How long exit waits for a refresh pass already in progress. A scan stuck
/// in the filesystem is not worth holding the operator's terminal for.
const REFRESH_SHUTDOWN_GRACE: Duration = Duration::from_millis(250);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LoopControl {
    Continue,
    /// A child had the terminal and gave it back: the physical screen no
    /// longer shows the last frame, so the next frame writes every cell.
    Repaint,
    Exit,
}

/// Change notifications the loop consumes. `run_app` feeds them from the
/// filesystem watchers.
struct ChangeFeeds {
    state: mpsc::Receiver<()>,
    artifacts: mpsc::Receiver<ArtifactChange>,
    state_watcher_active: bool,
    artifact_watcher_active: bool,
}

/// Everything the input loop keeps between frames. `run_app` drives it with a
/// terminal; the tests drive the same methods with synthetic events.
struct ConsoleLoop {
    background_tx: Sender<BackgroundMessage>,
    background_rx: mpsc::Receiver<BackgroundMessage>,
    refresh: RefreshWorker,
    transcripts: TranscriptWorker,
    scheduler: RefreshScheduler,
    feeds: ChangeFeeds,
    /// Set when the operator quit while a launch was still unanswered.
    exit_after_receipt: bool,
}

impl ConsoleLoop {
    fn start<S: RefreshSource, T: TranscriptSource>(
        app: &App,
        source: S,
        transcript_source: T,
        feeds: ChangeFeeds,
    ) -> anyhow::Result<Self> {
        let (background_tx, background_rx) = mpsc::channel::<BackgroundMessage>();
        let results = background_tx.clone();
        let refresh = RefreshWorker::spawn(
            source,
            RefreshSeed {
                state: app.state.clone(),
                intents: app.polarize_intents.clone(),
            },
            move |result| {
                results
                    .send(BackgroundMessage::Refresh(Box::new(result)))
                    .is_ok()
            },
        )
        .context("failed to start the control-plane refresh worker")?;
        let transcript_results = background_tx.clone();
        let transcripts = TranscriptWorker::spawn(transcript_source, move |result| {
            transcript_results
                .send(BackgroundMessage::Transcript(Box::new(result)))
                .is_ok()
        })
        .context("failed to start the transcript worker")?;
        let scheduler = RefreshScheduler::new(
            Instant::now(),
            feeds.state_watcher_active,
            feeds.artifact_watcher_active,
        );
        Ok(Self {
            background_tx,
            background_rx,
            refresh,
            transcripts,
            scheduler,
            feeds,
            exit_after_receipt: false,
        })
    }

    fn handle_event(&mut self, app: &mut App, event: Event) -> anyhow::Result<LoopControl> {
        let outcome = match event {
            Event::Key(key) => match handle_key(app, key, &self.background_tx)? {
                InputOutcome::Quit => return Ok(self.request_exit(app, key)),
                outcome => outcome,
            },
            Event::Mouse(mouse) => handle_mouse(app, mouse)?,
            _ => InputOutcome::Handled,
        };
        Ok(match outcome {
            InputOutcome::TerminalReturned => LoopControl::Repaint,
            InputOutcome::Handled | InputOutcome::Quit => LoopControl::Continue,
        })
    }

    /// Quit at once, unless a launch still waits for its receipt: leaving
    /// then drops the launcher's answer and closes the pipes it writes to. The
    /// console keeps serving input until the receipt lands; a second Ctrl+C
    /// leaves without it.
    fn request_exit(&mut self, app: &mut App, key: KeyEvent) -> LoopControl {
        let Some(summary) = app.pending_launch.clone() else {
            return LoopControl::Exit;
        };
        let abandon =
            key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c');
        if self.exit_after_receipt && abandon {
            return LoopControl::Exit;
        }
        self.exit_after_receipt = true;
        app.append_status(format!(
            "waiting for the launcher receipt of {summary} before exit · Ctrl+C again leaves without it"
        ));
        LoopControl::Continue
    }

    /// Service everything that is not a key press: background answers, change
    /// notifications and scheduled refreshes. It never waits for a read.
    fn tick(&mut self, app: &mut App, now: Instant) -> LoopControl {
        // Launcher answers and refresh results arrive here, never inside the
        // draw path: the console stays interactive while either is in flight.
        while let Ok(message) = self.background_rx.try_recv() {
            apply_background(app, message);
        }
        while self.feeds.state.try_recv().is_ok() {
            self.scheduler.mark_state_changed(now);
        }
        while let Ok(change) = self.feeds.artifacts.try_recv() {
            self.scheduler.mark_artifacts_changed(change, now);
        }
        let mut events = Vec::new();
        if let Some(sub) = &app.mux_subscriber {
            while let Ok(event) = sub.rx.try_recv() {
                events.push(event);
            }
        }
        for event in events {
            app.handle_ipc_event(event);
        }
        let plan = self.scheduler.plan(now);
        if plan.control_plane || plan.polarize || plan.mission_control {
            app.request_refresh(RefreshNeeds {
                control_plane: plan.control_plane,
                force_control_plane: false,
                polarize: plan.polarize,
                mission_control: true,
            });
        }
        if plan.rendered_runs {
            app.refresh_rendered_runs();
        }
        if plan.observe && app.config.view == crate::observe::ConsoleView::Observe {
            let ok = app.refresh_observe();
            self.scheduler.note_observe_result(ok);
        }
        if let Some(job) = app.take_refresh_job() {
            self.refresh.submit(job);
        }
        if let Some(job) = app.take_transcript_job() {
            self.transcripts.submit(job);
        }
        if self.exit_after_receipt && app.pending_launch.is_none() {
            LoopControl::Exit
        } else {
            LoopControl::Continue
        }
    }

    /// Apply background answers as they arrive until `done` holds. The running
    /// console never calls this; tests use it to wait on events, with
    /// `timeout` only as a failure guard.
    #[cfg(test)]
    fn wait_until(
        &mut self,
        app: &mut App,
        timeout: Duration,
        done: impl Fn(&App) -> bool,
    ) -> bool {
        let deadline = Instant::now() + timeout;
        while !done(app) {
            let Some(left) = deadline.checked_duration_since(Instant::now()) else {
                return false;
            };
            match self.background_rx.recv_timeout(left) {
                Ok(message) => apply_background(app, message),
                Err(_) => return false,
            }
        }
        true
    }

    fn shutdown(mut self) {
        let _ = self.refresh.shutdown(REFRESH_SHUTDOWN_GRACE);
        let _ = self.transcripts.shutdown(REFRESH_SHUTDOWN_GRACE);
    }
}

fn apply_background(app: &mut App, message: BackgroundMessage) {
    match message {
        BackgroundMessage::Catalog(state) => app.set_catalog(state),
        BackgroundMessage::Refresh(result) => {
            app.apply_refresh(*result);
        }
        BackgroundMessage::Transcript(result) => {
            app.apply_transcript(*result);
        }
        BackgroundMessage::Launch(outcome) => app.record_launch_outcome(*outcome),
        BackgroundMessage::LaunchHalted { summary, detail } => {
            app.pending_launch = None;
            app.show_error(format!("launch halted: {summary}"), detail);
        }
    }
}

fn run_app(config: AppConfig) -> anyhow::Result<()> {
    enable_raw_mode().context("failed to enable raw mode")?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
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
        let artifact_root = artifact_watch_root(&crate::polarize::vibecrafted_home());
        let artifact_watcher = match start_artifact_watcher(&artifact_root, artifact_tx) {
            Ok(watcher) => Some(watcher),
            Err(error) => {
                app.append_status(format!("artifact watcher unavailable: {error}"));
                None
            }
        };
        let mut console = ConsoleLoop::start(
            &app,
            CanonicalRefreshSource,
            CanonicalTranscriptSource,
            ChangeFeeds {
                state: state_rx,
                artifacts: artifact_rx,
                state_watcher_active: state_watcher.is_some(),
                artifact_watcher_active: artifact_watcher.is_some(),
            },
        )?;
        spawn_catalog_load(&app, &console.background_tx);
        let served = serve(&mut terminal, &mut app, &mut console);
        console.shutdown();
        drop((state_watcher, artifact_watcher));
        served
    })();

    shutdown_terminal(&mut terminal)?;
    result
}

fn serve(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    app: &mut App,
    console: &mut ConsoleLoop,
) -> anyhow::Result<()> {
    loop {
        terminal.draw(|frame| ui::draw(frame, app))?;
        let last_draw = Instant::now();
        let timeout = app
            .config
            .tick_rate
            .checked_sub(last_draw.elapsed())
            .unwrap_or(Duration::ZERO);
        if event::poll(timeout)? {
            match console.handle_event(app, event::read()?)? {
                LoopControl::Exit => return Ok(()),
                // Whatever the child left on the physical screen is not the
                // frame Ratatui diffs against: drop that frame so the next draw
                // writes every cell instead of only the ones that changed.
                LoopControl::Repaint => terminal.clear()?,
                LoopControl::Continue => {}
            }
        }
        if console.tick(app, Instant::now()) == LoopControl::Exit {
            return Ok(());
        }
    }
}

fn shutdown_terminal(terminal: &mut Terminal<CrosstermBackend<io::Stdout>>) -> anyhow::Result<()> {
    disable_raw_mode().context("failed to disable raw mode")?;
    execute!(
        terminal.backend_mut(),
        DisableMouseCapture,
        LeaveAlternateScreen
    )?;
    terminal.show_cursor()?;
    Ok(())
}

/// What a key press or click did beyond changing the console's own state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum InputOutcome {
    Handled,
    Quit,
    /// A child process had the terminal and gave it back.
    TerminalReturned,
}

fn clamp_home_selection(app: &mut App) {
    let len = app.home_rows().len();
    app.observe.home.selected = app.observe.home.selected.min(len.saturating_sub(1));
}

fn resume_home_target(app: &mut App, target: &str) -> anyhow::Result<InputOutcome> {
    let row = match app.select_home_target(target) {
        Ok(row) => row,
        Err(reason) => {
            app.show_error("resume unavailable", vec![reason]);
            return Ok(InputOutcome::Handled);
        }
    };
    let command = match app.home_resume_command(&row.run_id) {
        Ok(command) => command,
        Err(reason) => {
            app.show_error("resume unavailable", vec![reason]);
            return Ok(InputOutcome::Handled);
        }
    };
    let summary = command.command_line();
    match suspend_and_run(&command)? {
        Err(error) => app.show_error("resume failed", error.detail_lines(summary)),
        Ok(()) => {
            app.push_launch_history(summary.clone());
            app.append_status(format!("ran: {summary}"));
            app.request_full_refresh();
        }
    }
    Ok(InputOutcome::TerminalReturned)
}

fn submit_home_input(app: &mut App) -> anyhow::Result<InputOutcome> {
    let input = app.observe.home.input.trim().to_string();
    if input.is_empty() {
        app.open_selected_home_row();
        return Ok(InputOutcome::Handled);
    }
    if let Some(query) = input.strip_prefix('/') {
        app.append_status(format!(
            "filter /{} · {} operational run(s)",
            query,
            app.home_rows().len()
        ));
        return Ok(InputOutcome::Handled);
    }
    let Some(command) = input.strip_prefix('!') else {
        app.show_error(
            "ZEN input",
            vec!["search starts with /; commands start with !".to_string()],
        );
        return Ok(InputOutcome::Handled);
    };
    let mut words = command.split_whitespace();
    let verb = words.next().unwrap_or_default();
    let target = words.next().unwrap_or_default();
    if words.next().is_some() {
        app.show_error(
            "ZEN command",
            vec!["expected one run id or prefix".to_string()],
        );
        return Ok(InputOutcome::Handled);
    }
    match verb {
        "observe" => match app.select_home_target(target) {
            Ok(_) => {
                app.observe.home.input.clear();
                app.open_selected_home_row();
                Ok(InputOutcome::Handled)
            }
            Err(reason) => {
                app.show_error("observe unavailable", vec![reason]);
                Ok(InputOutcome::Handled)
            }
        },
        "resume" => {
            app.observe.home.input.clear();
            resume_home_target(app, target)
        }
        _ => {
            app.show_error(
                "unknown ZEN command",
                vec![format!(
                    "!{verb} is not available; use !observe <run> or !resume <run>"
                )],
            );
            Ok(InputOutcome::Handled)
        }
    }
}

fn handle_home_landing_key(app: &mut App, key: KeyEvent) -> anyhow::Result<Option<InputOutcome>> {
    if app.focus != LaunchFocus::Browse
        || !app.config.view.is_home()
        || app.observe.home.surface != crate::home::HomeSurface::Landing
    {
        return Ok(None);
    }
    let input_is_empty = app.observe.home.input.is_empty();
    let outcome = match key.code {
        KeyCode::Up => {
            app.move_home_selection(-1);
            InputOutcome::Handled
        }
        KeyCode::Down => {
            app.move_home_selection(1);
            InputOutcome::Handled
        }
        KeyCode::Tab | KeyCode::BackTab if input_is_empty => {
            app.open_home_panels();
            InputOutcome::Handled
        }
        KeyCode::Char('f') if input_is_empty => {
            app.toggle_home_scope();
            InputOutcome::Handled
        }
        KeyCode::Char('r') if input_is_empty => resume_home_target(app, "")?,
        KeyCode::Char('?') if input_is_empty => {
            app.focus = LaunchFocus::Help;
            InputOutcome::Handled
        }
        KeyCode::Char('q') if input_is_empty => InputOutcome::Quit,
        KeyCode::Esc if input_is_empty => InputOutcome::Quit,
        KeyCode::Esc => {
            app.observe.home.input.clear();
            clamp_home_selection(app);
            app.append_status("ZEN input cleared");
            InputOutcome::Handled
        }
        KeyCode::Backspace => {
            app.observe.home.input.pop();
            clamp_home_selection(app);
            InputOutcome::Handled
        }
        KeyCode::Enter => submit_home_input(app)?,
        KeyCode::Char('l') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            app.observe.home.input.clear();
            clamp_home_selection(app);
            app.append_status("ZEN filter cleared");
            InputOutcome::Handled
        }
        KeyCode::Char(ch)
            if !key
                .modifiers
                .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) =>
        {
            app.observe.home.input.push(ch);
            clamp_home_selection(app);
            InputOutcome::Handled
        }
        _ => InputOutcome::Handled,
    };
    Ok(Some(outcome))
}

fn handle_key(
    app: &mut App,
    key: KeyEvent,
    tx: &Sender<BackgroundMessage>,
) -> anyhow::Result<InputOutcome> {
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
        return Ok(InputOutcome::Quit);
    }
    if let Some(outcome) = handle_home_landing_key(app, key)? {
        return Ok(outcome);
    }
    let mut outcome = InputOutcome::Handled;

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
        LaunchFocus::EditModel => match key.code {
            KeyCode::Esc => app.finish_model_edit(),
            KeyCode::Char('s') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                app.finish_model_edit();
            }
            KeyCode::Enter => app.finish_model_edit(),
            KeyCode::Backspace => {
                app.launch_model.pop();
            }
            KeyCode::Char(c) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                app.launch_model.push(c);
            }
            _ => {}
        },
        LaunchFocus::EditRepo => match key.code {
            KeyCode::Esc => app.cancel_repo_edit(),
            KeyCode::Enter => {
                app.commit_repo_edit();
            }
            KeyCode::Char('s') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                app.commit_repo_edit();
            }
            KeyCode::Char('u') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                app.repo_edit.input.clear();
            }
            KeyCode::Backspace => {
                app.repo_edit.input.pop();
            }
            KeyCode::Char(c) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                app.repo_edit.input.push(c);
            }
            _ => {}
        },
        LaunchFocus::Confirmation => match key.code {
            KeyCode::Esc | KeyCode::Enter | KeyCode::Char('q') => {
                app.focus = LaunchFocus::Browse;
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
                outcome = launch_aicx_wizard(app)?;
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
            KeyCode::Char('r') | KeyCode::Char('R') => {
                app.focus = LaunchFocus::Browse;
                launch_selected(app, tx)?;
            }
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
                    // It opens its own pane; its output must not land on the console.
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
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
            KeyCode::Char('q') => return Ok(InputOutcome::Quit),
            KeyCode::Esc
                if app.config.view.is_home()
                    && app.observe.home.surface != crate::home::HomeSurface::Landing =>
            {
                app.return_home();
            }
            KeyCode::Esc => return Ok(InputOutcome::Quit),
            KeyCode::Char('?') => app.focus = LaunchFocus::Help,
            KeyCode::Tab => app.next_tab(),
            KeyCode::BackTab => app.previous_tab(),
            KeyCode::Up | KeyCode::Char('k') => match app.active_tab() {
                AppTab::Monitor
                    if app.config.view.is_home()
                        && app.observe.home.surface == crate::home::HomeSurface::Landing =>
                {
                    app.move_home_selection(-1);
                }
                AppTab::Monitor if app.config.view == crate::observe::ConsoleView::Observe => {
                    app.move_observe_selection(-1);
                }
                AppTab::Monitor => app.move_selection(-1),
                AppTab::Usage => {}
                AppTab::Dispatch => app.move_dispatch_selection(-1),
                AppTab::Controls => app.move_deep_selection(-1),
                AppTab::MissionControl => app.move_mission_focus(-1),
            },
            KeyCode::Down | KeyCode::Char('j') => match app.active_tab() {
                AppTab::Monitor
                    if app.config.view.is_home()
                        && app.observe.home.surface == crate::home::HomeSurface::Landing =>
                {
                    app.move_home_selection(1);
                }
                AppTab::Monitor if app.config.view == crate::observe::ConsoleView::Observe => {
                    app.move_observe_selection(1);
                }
                AppTab::Monitor => app.move_selection(1),
                AppTab::Usage => {}
                AppTab::Dispatch => app.move_dispatch_selection(1),
                AppTab::Controls => app.move_deep_selection(1),
                AppTab::MissionControl => app.move_mission_focus(1),
            },
            KeyCode::Char('l') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                app.clear_search();
            }
            KeyCode::Left | KeyCode::Char('h') => match app.active_tab() {
                AppTab::Monitor => {}
                AppTab::Usage => {}
                AppTab::Dispatch => app.adjust_dispatch_selection(-1),
                AppTab::Controls => app.move_selection(-1),
                AppTab::MissionControl => app.move_mission_focus(-1),
            },
            KeyCode::Right | KeyCode::Char('l') => match app.active_tab() {
                AppTab::Monitor => {}
                AppTab::Usage => {}
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
                app.dispatch_selected = DispatchFocus::Presentation as usize;
                app.cycle_presentation();
            }
            KeyCode::Char('n') => {
                app.set_active_tab(AppTab::Dispatch);
                app.dispatch_selected = DispatchFocus::Environment as usize;
                app.shift_environment(1);
            }
            KeyCode::Char('p') => {
                app.set_active_tab(AppTab::Dispatch);
                app.dispatch_selected = DispatchFocus::Permissions as usize;
                app.shift_permissions(1);
            }
            KeyCode::Char('s') => {
                app.set_active_tab(AppTab::Dispatch);
                app.dispatch_selected = DispatchFocus::Sandbox as usize;
                app.shift_sandbox(1);
            }
            KeyCode::Char('M') => {
                app.set_active_tab(AppTab::Dispatch);
                app.dispatch_selected = DispatchFocus::Model as usize;
                app.focus = LaunchFocus::EditModel;
            }
            KeyCode::Char('f')
                if app.config.view.is_home()
                    && app.observe.home.surface != crate::home::HomeSurface::Panels =>
            {
                app.toggle_home_scope()
            }
            KeyCode::Char('f') => app.toggle_filter(),
            KeyCode::Char('H') if app.config.view.is_home() => app.return_home(),
            KeyCode::Char('o')
                if app.config.view == crate::observe::ConsoleView::Observe
                    && app.active_tab() == AppTab::Monitor =>
            {
                app.toggle_observe_sort();
            }
            KeyCode::Char('t')
                if app.config.view == crate::observe::ConsoleView::Observe
                    && app.active_tab() == AppTab::Monitor =>
            {
                app.toggle_observe_transcript_view();
            }
            KeyCode::Char('u')
                if app.config.view == crate::observe::ConsoleView::Observe
                    && app.active_tab() == AppTab::Monitor =>
            {
                app.toggle_observe_transcript_class(crate::observe::TranscriptLineClass::Content);
            }
            KeyCode::Char('i')
                if app.config.view == crate::observe::ConsoleView::Observe
                    && app.active_tab() == AppTab::Monitor =>
            {
                app.toggle_observe_transcript_class(crate::observe::TranscriptLineClass::Thinking);
            }
            KeyCode::Char('c')
                if app.config.view == crate::observe::ConsoleView::Observe
                    && app.active_tab() == AppTab::Monitor =>
            {
                app.toggle_observe_transcript_class(crate::observe::TranscriptLineClass::Command);
            }
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
            KeyCode::Char('r') => {
                app.request_full_refresh();
                app.append_status("refresh requested: control plane, prisms, Mission Control");
                // A failed catalog probe must be retryable without a restart.
                if app.catalog.ready().is_none() {
                    app.set_catalog(CatalogState::Loading);
                    spawn_catalog_load(app, tx);
                }
            }
            KeyCode::Char('m') => {
                app.refresh_memory();
                app.focus = LaunchFocus::Memory;
            }
            KeyCode::Char('w') => {
                outcome = launch_aicx_wizard(app)?;
            }
            KeyCode::Char('e') => {
                app.set_active_tab(AppTab::Dispatch);
                app.dispatch_selected = DispatchFocus::Prompt as usize;
                app.focus = LaunchFocus::EditPrompt;
            }
            KeyCode::Char('g') => app.begin_repo_edit(),
            KeyCode::Enter => match app.active_tab() {
                AppTab::Monitor
                    if app.config.view.is_home()
                        && app.observe.home.surface != crate::home::HomeSurface::Panels =>
                {
                    if app.observe.home.surface == crate::home::HomeSurface::Landing {
                        app.open_selected_home_row();
                    }
                }
                AppTab::Monitor => {
                    if app.config.view == crate::observe::ConsoleView::Observe {
                        outcome = switch_to_selected_observe_session(app)?;
                    } else if app.selected_run().is_some() {
                        app.set_active_tab(AppTab::Controls);
                    }
                }
                AppTab::Usage => {}
                AppTab::Dispatch => match app.dispatch_focus() {
                    DispatchFocus::Prompt => app.focus = LaunchFocus::EditPrompt,
                    DispatchFocus::Model => app.focus = LaunchFocus::EditModel,
                    DispatchFocus::Repo => app.begin_repo_edit(),
                    _ => launch_selected(app, tx)?,
                },
                AppTab::Controls => {
                    outcome = run_selected_deep_control(app, tx)?;
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
    Ok(outcome)
}

fn handle_mouse(app: &mut App, mouse: MouseEvent) -> anyhow::Result<InputOutcome> {
    let (width, height) = crossterm::terminal::size()?;
    apply_mouse(app, mouse, ratatui::layout::Rect::new(0, 0, width, height))
}

fn apply_mouse(
    app: &mut App,
    mouse: MouseEvent,
    area: ratatui::layout::Rect,
) -> anyhow::Result<InputOutcome> {
    if app.focus != LaunchFocus::Browse {
        return Ok(InputOutcome::Handled);
    }
    let mux_height = crate::layout::mux_panel_height(app.mux_status_lines().len());
    let polarize_height = crate::layout::polarize_panel_height(app.polarize_status_lines().len());
    let layout_view = if app.config.view.is_home()
        && app.observe.home.surface == crate::home::HomeSurface::Panels
    {
        crate::observe::ConsoleView::Full
    } else {
        app.config.view
    };
    let Some(hit) = crate::layout::hit_test(
        area,
        app.active_tab(),
        layout_view,
        mux_height,
        polarize_height,
        mouse.column,
        mouse.row,
    ) else {
        return Ok(InputOutcome::Handled);
    };
    match mouse.kind {
        MouseEventKind::ScrollUp => scroll_hit(app, area, hit, -1),
        MouseEventKind::ScrollDown => scroll_hit(app, area, hit, 1),
        MouseEventKind::Down(MouseButton::Left) => return click_hit(app, hit),
        _ => {}
    }
    Ok(InputOutcome::Handled)
}

fn scroll_hit(
    app: &mut App,
    area: ratatui::layout::Rect,
    hit: crate::layout::HitTarget,
    delta: i16,
) {
    use crate::layout::{inner_height, pane_for_hit};
    if matches!(hit, crate::layout::HitTarget::ObserveList { .. }) {
        app.move_observe_selection(delta.into());
        return;
    }
    if matches!(hit, crate::layout::HitTarget::HomeList { .. }) {
        app.move_home_selection(delta.into());
        return;
    }
    if matches!(hit, crate::layout::HitTarget::MonitorList { .. }) {
        app.move_selection(delta.into());
        return;
    }
    let Some(pane) = pane_for_hit(hit) else {
        return;
    };
    let Some(rect) = pane_rect(area, app, pane) else {
        return;
    };
    let view_height = inner_height(rect);
    let view_width = rect.width.saturating_sub(2);
    let content_len = pane_content_len(app, pane, view_width);
    app.interaction
        .scroll_pane(pane, delta, content_len, view_height);
}

fn click_hit(app: &mut App, hit: crate::layout::HitTarget) -> anyhow::Result<InputOutcome> {
    use crate::layout::{HitTarget, pane_for_hit};
    if let Some(pane) = pane_for_hit(hit) {
        app.interaction.focused = Some(pane);
    }
    match hit {
        HitTarget::Tab(index) => {
            app.set_active_tab(AppTab::from_index(index));
        }
        HitTarget::DispatchStat(0) => {
            app.dispatch_selected = DispatchFocus::Kind as usize;
        }
        HitTarget::DispatchStat(1) => {
            app.dispatch_selected = DispatchFocus::Agent as usize;
        }
        HitTarget::DispatchStat(_) => {
            app.dispatch_selected = DispatchFocus::Environment as usize;
        }
        HitTarget::DispatchDeck { inner_row } => {
            let row = usize::from(inner_row.saturating_add(app.interaction.scroll.deck));
            if row < DispatchFocus::COUNT {
                app.dispatch_selected = row;
            }
        }
        HitTarget::HomeList { inner_row } => {
            let visual = home_visual_index(app, usize::from(inner_row));
            if let Some(index) = visual {
                if index == app.observe.home.selected {
                    app.open_selected_home_row();
                } else {
                    app.observe.home.selected = index;
                }
            }
        }
        HitTarget::ObserveList { inner_row } => {
            let index = usize::from(inner_row.saturating_add(app.interaction.scroll.observe_list));
            if index < app.observe.runs.len() {
                if index == app.observe.selected {
                    return switch_to_selected_observe_session(app);
                }
                app.observe.selected = index;
                app.refresh_observe_transcript();
            }
        }
        HitTarget::MonitorList { inner_row } => {
            let index =
                usize::from(inner_row) / 2 + usize::from(app.interaction.scroll.monitor_list);
            if index < app.runs.len() {
                app.selected = index;
            }
        }
        HitTarget::MonitorStat(2) => {
            app.toggle_filter();
        }
        HitTarget::ControlsActions { inner_row } => {
            let index =
                usize::from(inner_row.saturating_add(app.interaction.scroll.controls_actions));
            if index < app.deep_actions().len() {
                app.deep_selected = index;
            }
        }
        HitTarget::MissionPanel(index) => {
            app.mission_focus = usize::from(index);
        }
        _ => {}
    }
    Ok(InputOutcome::Handled)
}

fn pane_rect(
    area: ratatui::layout::Rect,
    app: &App,
    pane: crate::layout::PaneId,
) -> Option<ratatui::layout::Rect> {
    use crate::layout::{
        PaneId, controls_layout, dispatch_layout, mission_layout, monitor_layout, mux_panel_height,
        observe_layout, polarize_panel_height, root_layout,
    };
    let body = if app.config.view.is_home()
        && app.observe.home.surface != crate::home::HomeSurface::Panels
    {
        crate::layout::home_root_layout(area).body
    } else {
        root_layout(area).body
    };
    match pane {
        PaneId::DispatchDeck => Some(dispatch_layout(body).deck),
        PaneId::DispatchPlaybook => Some(dispatch_layout(body).playbook),
        PaneId::DispatchTrail => Some(dispatch_layout(body).trail),
        PaneId::MonitorList => Some(
            monitor_layout(
                body,
                mux_panel_height(app.mux_status_lines().len()),
                polarize_panel_height(app.polarize_status_lines().len()),
            )
            .list,
        ),
        PaneId::MonitorDossier => Some(
            monitor_layout(
                body,
                mux_panel_height(app.mux_status_lines().len()),
                polarize_panel_height(app.polarize_status_lines().len()),
            )
            .dossier,
        ),
        PaneId::MonitorTimeline => Some(
            monitor_layout(
                body,
                mux_panel_height(app.mux_status_lines().len()),
                polarize_panel_height(app.polarize_status_lines().len()),
            )
            .timeline,
        ),
        PaneId::ObserveList => Some(observe_layout(body).list),
        PaneId::ObserveTranscript => Some(observe_layout(body).transcript),
        PaneId::HomeList => Some(crate::layout::home_layout(body, area.width).list),
        PaneId::HomeTranscript => Some(crate::layout::home_layout(body, area.width).transcript),
        PaneId::ControlsActions => Some(controls_layout(body).actions),
        PaneId::ControlsArtifacts => Some(controls_layout(body).artifacts),
        PaneId::ControlsTimeline => Some(controls_layout(body).timeline),
        PaneId::Mission(index) => mission_layout(body).panels.get(usize::from(index)).copied(),
    }
}

fn pane_content_len(app: &App, pane: crate::layout::PaneId, view_width: u16) -> usize {
    use crate::app::wrapped_line_count;
    use crate::layout::PaneId;
    match pane {
        PaneId::DispatchDeck => wrapped_line_count(app.prompt_lines(), view_width),
        PaneId::DispatchPlaybook => 8,
        PaneId::DispatchTrail => app.launch_history.len().max(3).saturating_add(2),
        PaneId::MonitorList => app.runs.len(),
        PaneId::MonitorDossier | PaneId::ControlsArtifacts => app.detail_lines().len(),
        PaneId::MonitorTimeline | PaneId::ControlsTimeline => app.event_lines().len(),
        PaneId::ObserveList => app.observe.runs.len(),
        PaneId::ObserveTranscript => app.observe.transcript.lines().count().saturating_add(6),
        PaneId::HomeList => crate::ui::home_board_line_count(app),
        PaneId::HomeTranscript => app.home_conversation_lines(view_width as usize).len(),
        PaneId::ControlsActions => app.deep_control_lines().len(),
        PaneId::Mission(0) => app
            .mission_control
            .active_dispatches
            .len()
            .saturating_mul(2),
        PaneId::Mission(1) => app.mission_control.wave_atlas.len(),
        PaneId::Mission(2) => app.mission_control.agent_stats.len(),
        PaneId::Mission(3) => app.mission_control.skill_stats.len(),
        PaneId::Mission(4) => app.mission_control.fleet_health.len(),
        PaneId::Mission(5) => app.mission_control.failures.len(),
        PaneId::Mission(6) => app.mission_control.action_queue.len(),
        PaneId::Mission(_) => 0,
    }
}

fn home_visual_index(app: &App, inner_row: usize) -> Option<usize> {
    crate::ui::home_row_index_at(app, inner_row)
}

fn switch_to_selected_observe_session(app: &mut App) -> anyhow::Result<InputOutcome> {
    let Some(command) = app.observe_switch_command() else {
        app.show_error(
            "session switch unavailable",
            vec!["The canonical session has no vc-frame attach target.".to_string()],
        );
        return Ok(InputOutcome::Handled);
    };
    let summary = command.command_line();
    match suspend_and_run(&command)? {
        Err(error) => app.show_error("session switch failed", error.detail_lines(summary)),
        Ok(()) => {
            app.append_status(format!("returned from {summary}"));
            app.request_full_refresh();
        }
    }
    Ok(InputOutcome::TerminalReturned)
}

fn launch_aicx_wizard(app: &mut App) -> anyhow::Result<InputOutcome> {
    let project = app.memory.project.clone();
    let repo = app.config.repo.clone();
    match with_terminal_handed_over(|| crate::memory::launch_wizard(&project, &repo))? {
        Ok(()) => app.append_status("returned from aicx wizard"),
        Err(error) => app.show_error("aicx wizard failed", vec![error.to_string()]),
    }
    Ok(InputOutcome::TerminalReturned)
}

/// Hand the operator's declaration to the canonical launcher.
///
/// Nothing about the worker is decided here: no session, no worktree, no
/// agent environment. VOC validates the declaration against the launcher's
/// catalog, spawns the launcher on a worker thread, and waits for its receipt
/// as a message. The UI keeps drawing throughout, and closing a preview never
/// touches the detached worker — the launcher owns its lifetime.
fn launch_selected(app: &mut App, tx: &Sender<BackgroundMessage>) -> anyhow::Result<()> {
    if let Some(summary) = app.pending_launch.clone() {
        app.append_status(format!(
            "already launching {summary}; waiting for its receipt"
        ));
        return Ok(());
    }
    let request = app.launch_request();
    let command = match app.launch_plan() {
        Ok(command) => command,
        Err(refusals) => {
            // Refused before any worker exists: the operator sees the exact
            // unsupported part of the declaration, not a half-started run.
            let mut lines = vec![format!("declared: {}", request.summary()), String::new()];
            lines.extend(refusals.into_iter().map(|reason| format!("· {reason}")));
            lines.push(String::new());
            lines.push("Change the declaration and launch again.".to_string());
            app.show_error("declaration refused before launch", lines);
            return Ok(());
        }
    };
    // The readiness probe talks to the mux over a unix socket with its own
    // timeouts. It runs on the worker thread with the launch, never in the
    // draw path: a slow or wedged mux must not freeze the console.
    let verify = (!app.config.no_verify_gate
        && app.launch_presentation != launch::Presentation::Headless)
        .then(|| match app.selected_agent() {
            "claude" => rmcp_mux::ipc::ClientKind::Claude,
            "codex" => rmcp_mux::ipc::ClientKind::Codex,
            "cursor" => rmcp_mux::ipc::ClientKind::Cursor,
            "junie" => rmcp_mux::ipc::ClientKind::Junie,
            other => rmcp_mux::ipc::ClientKind::Generic {
                name: other.to_string(),
            },
        });
    let summary = request.summary();
    let preview = command.preview();
    let expectation = app.launch_expectation();
    app.pending_launch = Some(summary.clone());
    app.append_status(format!("launching {summary}…"));
    let tx = tx.clone();
    thread::spawn(move || {
        if let Some(client_kind) = verify
            && let Err(halt) = launch::pre_launch_verify(client_kind)
        {
            // Halted before the launcher was ever started: nothing exists to
            // be uncertain about.
            let _ = tx.send(BackgroundMessage::LaunchHalted {
                summary,
                detail: LaunchRunError::ClientDrift(halt).detail_lines(String::new()),
            });
            return;
        }
        let outcome = launch::LaunchOutcome::from_run(
            preview,
            expectation,
            command.run_capturing(launch::LAUNCH_ANSWER_DEADLINE),
        );
        let _ = tx.send(BackgroundMessage::Launch(Box::new(outcome)));
    });
    Ok(())
}

fn run_selected_deep_control(
    app: &mut App,
    tx: &Sender<BackgroundMessage>,
) -> anyhow::Result<InputOutcome> {
    let Some(action) = app.selected_deep_action() else {
        app.append_status("No deep action is available for the selected run.");
        app.focus = LaunchFocus::Browse;
        return Ok(InputOutcome::Handled);
    };
    if matches!(
        action,
        DeepAction::OpenReport(_) | DeepAction::OpenTranscript(_) | DeepAction::OpenRoot(_)
    ) {
        if let Err(error) = app.open_artifact(&action) {
            app.show_error("artifact open failed", vec![format!("{error:#}")]);
        }
        return Ok(InputOutcome::Handled);
    }
    if matches!(action, DeepAction::PolarizeIntent { .. }) {
        if let Err(error) = app.open_polarize_intent(&action) {
            app.show_error("polarize prism open failed", vec![format!("{error:#}")]);
        }
        return Ok(InputOutcome::Handled);
    }
    // A skill launch IS a launch: same declaration, same launcher, same
    // receipt. It must not take a second path with its own argv shape.
    if let DeepAction::SkillLaunch { skill, agent } = &action {
        let Some(entry) = crate::skills_catalog::catalog_entry(skill) else {
            app.show_error(
                "unknown skill",
                vec![format!("{skill} is not in the VOC skill catalog")],
            );
            return Ok(InputOutcome::Handled);
        };
        let restore_kind = app.launch_kind;
        let restore_agent = app.launch_agent;
        app.launch_kind = LaunchKind::Skill(entry);
        if let Some(index) = app
            .agent_choices()
            .iter()
            .position(|candidate| candidate == agent)
        {
            app.launch_agent = index;
        }
        let result = launch_selected(app, tx);
        app.launch_kind = restore_kind;
        app.launch_agent = restore_agent;
        return result.map(|()| InputOutcome::Handled);
    }
    let command = deep_control_command(app, &action);
    let summary = command.command_line();
    match suspend_and_run(&command)? {
        Err(error) => app.show_error("action failed", error.detail_lines(summary)),
        Ok(()) => {
            app.push_launch_history(summary.clone());
            app.append_status(format!("ran: {summary}"));
            app.focus = LaunchFocus::Browse;
        }
    }
    app.request_full_refresh();
    Ok(InputOutcome::TerminalReturned)
}

fn deep_control_command(app: &App, action: &DeepAction) -> LaunchCommand {
    match action {
        DeepAction::AttachSession(session) => LaunchCommand {
            program: app.config.command_deck.clone(),
            args: vec!["dashboard".into(), "attach".into(), session.clone().into()],
            env: Default::default(),
            stdin: None,
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
            stdin: None,
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
            stdin: None,
        },
        DeepAction::SkillLaunch { .. }
        | DeepAction::OpenReport(_)
        | DeepAction::OpenTranscript(_)
        | DeepAction::OpenRoot(_)
        | DeepAction::PolarizeIntent { .. }
        | DeepAction::MuxRestart(_)
        | DeepAction::MuxVerifyClient(_)
        | DeepAction::MuxFixClientDrift(_) => {
            unreachable!(
                "artifact, skill-launch and polarize actions are handled before this point"
            )
        }
    }
}

/// Failure of an interactive deep control (attach / resume / mux health).
///
/// Launches no longer appear here: their truth is the launcher's receipt
/// (`LaunchOutcome`), not a locally observed process.
#[derive(Debug)]
pub enum LaunchRunError {
    Exec { message: String, stderr: String },
    ClientDrift(crate::launch::VerifyHalt),
}

impl LaunchRunError {
    pub fn detail_lines(&self, summary: String) -> Vec<String> {
        match self {
            Self::Exec { message, stderr } => {
                let mut lines = Vec::new();
                if !summary.trim().is_empty() {
                    lines.push(format!("command: {summary}"));
                }
                lines.push(format!("error: {message}"));
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

/// Give the terminal to an interactive child and take it back when it ends.
///
/// Every step that left the console's screen is undone whether the child ran,
/// failed or never started. The caller must repaint: re-entering the alternate
/// screen shows whatever the child left there, while Ratatui still holds the
/// last frame it drew and would only write the cells that changed since. The
/// outer `Err` means the terminal could not be restored at all.
fn with_terminal_handed_over<T>(
    child: impl FnOnce() -> anyhow::Result<T>,
) -> anyhow::Result<anyhow::Result<T>> {
    let mut stdout = io::stdout();
    let left = disable_raw_mode()
        .context("failed to disable raw mode before handing over the terminal")
        .and_then(|()| {
            execute!(stdout, DisableMouseCapture, LeaveAlternateScreen)
                .context("failed to leave the alternate screen")
        });
    let _ = stdout.flush();
    let outcome = left.and_then(|()| child());
    let screen = execute!(stdout, EnterAlternateScreen, EnableMouseCapture)
        .context("failed to restore the alternate screen and mouse capture");
    let raw = enable_raw_mode().context("failed to restore raw mode");
    screen?;
    raw?;
    Ok(outcome)
}

/// Run an interactive deep control (attach / resume / health) with the terminal.
fn suspend_and_run(command: &LaunchCommand) -> anyhow::Result<Result<(), LaunchRunError>> {
    let finished = with_terminal_handed_over(|| {
        let child = command.spawn_interactive_with_stderr()?;
        child.wait_with_output().context("process failed")
    })?;
    Ok(match finished {
        Ok(output) if output.status.success() => Ok(()),
        Ok(output) => Err(LaunchRunError::Exec {
            message: format!("command exited with {}", output.status),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        }),
        Err(error) => Err(launch_error(error)),
    })
}

fn launch_error(error: impl Into<anyhow::Error>) -> LaunchRunError {
    let error = error.into();
    LaunchRunError::Exec {
        message: format!("{error:#}"),
        stderr: String::new(),
    }
}

fn start_state_watcher(path: &Path, tx: Sender<()>) -> anyhow::Result<RecommendedWatcher> {
    let mut watcher = RecommendedWatcher::new(
        move |event: notify::Result<notify::Event>| {
            let Ok(event) = event else {
                return;
            };
            if event
                .paths
                .iter()
                .any(|candidate| is_projection_path(candidate))
            {
                let _ = tx.send(());
            }
        },
        NotifyConfig::default(),
    )?;
    for root in control_plane_watch_roots(path) {
        if root.exists() {
            watcher.watch(&root, RecursiveMode::NonRecursive)?;
        }
    }
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
            path.file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name == "prism.json")
                && path
                    .components()
                    .any(|component| component.as_os_str() == "polarize")
        }),
        mission_control: paths.iter().any(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .map(str::to_ascii_lowercase)
                .is_some_and(|name| name.starts_with("untitled") && name.ends_with(".md"))
        }),
    }
}

fn artifact_watch_root(home: &Path) -> PathBuf {
    home.join("artifacts")
}

fn control_plane_watch_roots(state_root: &Path) -> Vec<PathBuf> {
    vec![
        state_root.to_path_buf(),
        state_root.join("runs"),
        state_root.join("runs").join(".archived"),
        state_root.join("runtime_runs"),
    ]
}

fn is_projection_path(path: &Path) -> bool {
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    if name.ends_with(".log") || name.ends_with(".tmp") {
        return false;
    }
    if name == "events.jsonl" {
        return true;
    }
    if name.ends_with(".json") {
        return !path
            .components()
            .any(|component| component.as_os_str() == "runtime_runs");
    }
    path.components()
        .any(|component| component.as_os_str() == "runtime_runs")
}

fn projection_revision(root: &Path) -> u64 {
    let mut hasher = DefaultHasher::new();
    hash_mtime(&root.join("events.jsonl"), &mut hasher);
    hash_dir_entries(&root.join("runs"), &mut hasher);
    hash_dir_entries(&root.join("runs").join(".archived"), &mut hasher);
    hash_dir_names(&root.join("runtime_runs"), &mut hasher);
    hasher.finish()
}

fn hash_mtime(path: &Path, hasher: &mut DefaultHasher) {
    path.hash(hasher);
    // mtime alone misses a rewrite that lands inside one filesystem clock tick
    // (events.jsonl appended twice within ~1ms hashed identically); size is the
    // cheap second witness for append-only projection files.
    let (modified, len) = fs::metadata(path)
        .map(|meta| {
            (
                meta.modified().unwrap_or(SystemTime::UNIX_EPOCH),
                meta.len(),
            )
        })
        .unwrap_or((SystemTime::UNIX_EPOCH, 0));
    modified.hash(hasher);
    len.hash(hasher);
}

fn hash_dir_entries(path: &Path, hasher: &mut DefaultHasher) {
    path.hash(hasher);
    let Ok(entries) = fs::read_dir(path) else {
        return;
    };
    let mut names = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.extension().and_then(|ext| ext.to_str()) == Some("json"))
        .collect::<Vec<_>>();
    names.sort();
    for file in names {
        hash_mtime(&file, hasher);
    }
}

fn hash_dir_names(path: &Path, hasher: &mut DefaultHasher) {
    path.hash(hasher);
    let Ok(entries) = fs::read_dir(path) else {
        return;
    };
    let mut names = entries
        .flatten()
        .map(|entry| entry.file_name())
        .collect::<Vec<_>>();
    names.sort();
    names.len().hash(hasher);
    for name in names {
        name.hash(hasher);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::catalog::fixture_catalog;
    use crate::state::{ControlPlaneState, RenderedRun, RunKind, RunSnapshot};

    fn sample_run(run_id: &str, agent: &str, session: &str) -> RenderedRun {
        let now = chrono::Utc::now();
        RenderedRun {
            snapshot: RunSnapshot {
                run_id: run_id.to_string(),
                session_id: Some(format!("sess-{run_id}")),
                agent: Some(agent.to_string()),
                skill: Some("workflow".to_string()),
                mode: Some("implement".to_string()),
                state: Some("running".to_string()),
                status: None,
                started_at: Some((now - chrono::Duration::minutes(2)).to_rfc3339()),
                updated_at: Some((now - chrono::Duration::minutes(1)).to_rfc3339()),
                last_heartbeat: Some(now.to_rfc3339()),
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
                repo: "/tmp/repo".into(),
                presentation: Presentation::Terminal,
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
            launch_model: String::new(),
            launch_presentation: Presentation::Terminal,
            launch_environment: Environment::LivingTree,
            launch_permissions: PermissionPolicy::Default,
            launch_sandbox: SandboxChoice::Default,
            catalog: CatalogState::Ready(fixture_catalog()),
            pending_launch: None,
            launch_outcome: None,
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
            interaction: Default::default(),
            repo_edit: Default::default(),
            refresh: Default::default(),
        }
    }

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }

    fn mouse(kind: MouseEventKind, column: u16, row: u16) -> MouseEvent {
        MouseEvent {
            kind,
            column,
            row,
            modifiers: KeyModifiers::NONE,
        }
    }

    fn dispatch_area() -> ratatui::layout::Rect {
        ratatui::layout::Rect::new(0, 0, 120, 40)
    }

    #[test]
    fn handle_key_home_observes_rows_with_or_without_a_panel_and_returns() {
        let mut app = sample_app();
        app.config.view = crate::observe::ConsoleView::HomeAttention;
        app.state.runs = vec![sample_run("work-1", "claude", "pane-2").snapshot, {
            let mut missing = sample_run("ask-beta", "grok", "").snapshot;
            missing.operator_session = None;
            missing
                .extra
                .insert("needs_attention".into(), serde_json::Value::Bool(true));
            missing
        }];
        app.state.retained_runs = app.state.runs.clone();
        let (tx, rx) = std::sync::mpsc::channel::<BackgroundMessage>();
        let work_at = app
            .home_rows()
            .iter()
            .position(|row| row.run_id == "work-1")
            .expect("work row");
        app.observe.home.selected = work_at;

        handle_key(&mut app, key(KeyCode::Enter), &tx).unwrap();
        assert_eq!(
            app.observe.home.surface,
            crate::home::HomeSurface::Conversation
        );
        assert!(app.status_line.contains("panel pane-2 available"));
        assert!(app.status_line.contains("no launch"));
        assert!(rx.try_recv().is_err(), "Home must not enqueue a launch");

        handle_key(&mut app, key(KeyCode::Esc), &tx).unwrap();
        assert_eq!(app.observe.home.surface, crate::home::HomeSurface::Landing);
        assert!(app.status_line.contains("returned to Home"));

        let missing_at = app
            .home_rows()
            .iter()
            .position(|row| row.run_id == "ask-beta")
            .expect("missing-panel row");
        app.observe.home.selected = missing_at;
        handle_key(&mut app, key(KeyCode::Enter), &tx).unwrap();
        assert_eq!(
            app.observe.home.surface,
            crate::home::HomeSurface::Conversation
        );
        assert_eq!(
            app.observe.home.conversation_run_id.as_deref(),
            Some("ask-beta")
        );
        assert!(app.status_line.contains("no panel route yet"));
        assert!(rx.try_recv().is_err(), "missing panel must not launch");
    }

    #[test]
    fn zen_input_filters_and_bang_observe_selects_a_run() {
        let mut app = sample_app();
        app.config.view = crate::observe::ConsoleView::HomeAttention;
        app.state.runs = vec![
            sample_run("run-codex", "codex", "pane-codex").snapshot,
            sample_run("run-kimi", "kimi", "pane-kimi").snapshot,
        ];
        app.state.retained_runs = app.state.runs.clone();
        let (tx, _rx) = std::sync::mpsc::channel::<BackgroundMessage>();

        for ch in "/kimi".chars() {
            handle_key(&mut app, key(KeyCode::Char(ch)), &tx).unwrap();
        }
        assert_eq!(app.observe.home.input, "/kimi");
        assert_eq!(app.home_rows().len(), 1);
        assert_eq!(app.home_rows()[0].run_id, "run-kimi");

        handle_key(&mut app, key(KeyCode::Esc), &tx).unwrap();
        assert!(app.observe.home.input.is_empty());
        for ch in "!observe run-codex".chars() {
            handle_key(&mut app, key(KeyCode::Char(ch)), &tx).unwrap();
        }
        handle_key(&mut app, key(KeyCode::Enter), &tx).unwrap();
        assert_eq!(
            app.observe.home.surface,
            crate::home::HomeSurface::Conversation
        );
        assert_eq!(
            app.observe.home.conversation_run_id.as_deref(),
            Some("run-codex")
        );
        assert!(app.observe.home.input.is_empty());
    }

    #[test]
    fn zen_resume_uses_the_canonical_launcher_contract() {
        let mut app = sample_app();
        app.config.view = crate::observe::ConsoleView::Home;
        app.state.runs = vec![sample_run("run-1", "codex", "pane-1").snapshot];
        let command = app.home_resume_command("run-1").unwrap();
        assert_eq!(
            command.program,
            std::path::PathBuf::from("/usr/bin/vibecrafted")
        );
        assert_eq!(
            command
                .args
                .iter()
                .map(|arg| arg.to_string_lossy().into_owned())
                .collect::<Vec<_>>(),
            ["resume", "codex", "--session", "sess-run-1"]
        );
    }

    #[test]
    fn zen_tab_opens_existing_console_at_all_seven_mission_panels() {
        let mut app = sample_app();
        app.config.view = crate::observe::ConsoleView::Home;
        let (tx, _rx) = std::sync::mpsc::channel::<BackgroundMessage>();
        handle_key(&mut app, key(KeyCode::Tab), &tx).unwrap();
        assert_eq!(app.observe.home.surface, crate::home::HomeSurface::Panels);
        assert_eq!(app.active_tab(), AppTab::MissionControl);
        assert_eq!(
            crate::layout::mission_layout(ratatui::layout::Rect::new(0, 0, 120, 35))
                .panels
                .len(),
            7
        );
        handle_key(&mut app, key(KeyCode::Char('H')), &tx).unwrap();
        assert_eq!(app.observe.home.surface, crate::home::HomeSurface::Landing);
    }

    #[test]
    fn handle_key_home_toggles_global_local_scope() {
        let mut app = sample_app();
        app.config.view = crate::observe::ConsoleView::Home;
        app.config.repo = std::path::PathBuf::from("/tmp/ws-alpha");
        app.state.runs = vec![
            {
                let mut run = sample_run("work-1", "claude", "pane-2").snapshot;
                run.root = Some("/tmp/ws-alpha".into());
                run
            },
            {
                let mut run = sample_run("ask-beta", "grok", "").snapshot;
                run.root = Some("/tmp/ws-beta".into());
                run.operator_session = None;
                run.state = Some("unknown".into());
                run
            },
        ];
        app.state.retained_runs = app.state.runs.clone();
        let (tx, _rx) = std::sync::mpsc::channel::<BackgroundMessage>();
        assert_eq!(app.home_rows().len(), 2);
        handle_key(&mut app, key(KeyCode::Char('f')), &tx).unwrap();
        assert_eq!(app.observe.home.scope, crate::home::HomeScope::Local);
        assert_eq!(app.home_rows().len(), 1);
        assert!(app.status_line.contains("[Local]"));
    }

    #[test]
    fn handle_key_cycles_tabs_with_tab_and_shift_tab() {
        let mut app = sample_app();
        let (tx, _rx) = std::sync::mpsc::channel::<BackgroundMessage>();

        assert_eq!(app.active_tab(), AppTab::Monitor);
        handle_key(&mut app, key(KeyCode::Tab), &tx).unwrap();
        assert_eq!(app.active_tab(), AppTab::Usage);
        handle_key(&mut app, key(KeyCode::Tab), &tx).unwrap();
        assert_eq!(app.active_tab(), AppTab::Dispatch);

        handle_key(&mut app, key(KeyCode::BackTab), &tx).unwrap();
        assert_eq!(app.active_tab(), AppTab::Usage);
        handle_key(&mut app, key(KeyCode::BackTab), &tx).unwrap();
        assert_eq!(app.active_tab(), AppTab::Monitor);
    }

    #[test]
    fn handle_key_routes_arrows_inside_the_active_tab() {
        let mut app = sample_app();
        let (tx, _rx) = std::sync::mpsc::channel::<BackgroundMessage>();

        handle_key(&mut app, key(KeyCode::Down), &tx).unwrap();
        assert_eq!(app.selected, 1);

        app.set_active_tab(AppTab::Dispatch);
        handle_key(&mut app, key(KeyCode::Down), &tx).unwrap();
        assert_eq!(app.dispatch_focus(), DispatchFocus::Agent);

        handle_key(&mut app, key(KeyCode::Right), &tx).unwrap();
        // The agent list belongs to the launcher catalog; ← / → walk it.
        assert_eq!(app.selected_agent(), app.agent_choices()[1].as_str());

        app.set_active_tab(AppTab::Controls);
        handle_key(&mut app, key(KeyCode::Down), &tx).unwrap();
        assert_eq!(app.deep_selected, 1);
    }

    #[test]
    fn handle_key_enters_prompt_edit_from_dispatch_prompt_row() {
        let mut app = sample_app();
        let (tx, _rx) = std::sync::mpsc::channel::<BackgroundMessage>();
        app.set_active_tab(AppTab::Dispatch);
        app.dispatch_selected = DispatchFocus::Prompt as usize;

        handle_key(&mut app, key(KeyCode::Enter), &tx).unwrap();

        assert_eq!(app.focus, LaunchFocus::EditPrompt);
    }

    #[test]
    fn handle_key_shortcuts_jump_to_dispatch_controls_and_prime_selection() {
        let mut app = sample_app();
        let (tx, _rx) = std::sync::mpsc::channel::<BackgroundMessage>();

        handle_key(&mut app, key(KeyCode::Char('a')), &tx).unwrap();
        assert_eq!(app.active_tab(), AppTab::Dispatch);
        assert_eq!(app.dispatch_focus(), DispatchFocus::Agent);
        assert_eq!(app.selected_agent(), app.agent_choices()[1].as_str());

        handle_key(&mut app, key(KeyCode::Char('v')), &tx).unwrap();
        assert_eq!(app.active_tab(), AppTab::Dispatch);
        assert_eq!(app.dispatch_focus(), DispatchFocus::Presentation);
        assert_eq!(app.launch_presentation, Presentation::Headless);

        app.set_active_tab(AppTab::Monitor);
        handle_key(&mut app, key(KeyCode::Char('d')), &tx).unwrap();
        assert_eq!(app.active_tab(), AppTab::Controls);
        assert!(app.status_line.contains("Controls ready"));
    }

    #[test]
    fn handle_key_controls_can_move_across_run_list_and_prompt_edit_saves_multiline_prompt() {
        let mut app = sample_app();
        let (tx, _rx) = std::sync::mpsc::channel::<BackgroundMessage>();
        app.set_active_tab(AppTab::Controls);

        handle_key(&mut app, key(KeyCode::Right), &tx).unwrap();
        assert_eq!(app.selected, 1);

        handle_key(&mut app, key(KeyCode::Left), &tx).unwrap();
        assert_eq!(app.selected, 0);

        app.set_active_tab(AppTab::Dispatch);
        app.focus = LaunchFocus::EditPrompt;
        handle_key(&mut app, key(KeyCode::Enter), &tx).unwrap();
        handle_key(&mut app, key(KeyCode::Char('n')), &tx).unwrap();
        handle_key(&mut app, key(KeyCode::Esc), &tx).unwrap();
        assert!(app.launch_prompt.contains("\nn"));
        assert_eq!(app.focus, LaunchFocus::Browse);
        assert!(app.status_line.contains("prompt updated"));
    }

    #[test]
    fn mouse_click_selects_a_dispatch_stat_cell() {
        let mut app = sample_app();
        app.set_active_tab(AppTab::Dispatch);
        let area = dispatch_area();
        let operator =
            crate::layout::dispatch_layout(crate::layout::root_layout(area).body).stats[1];
        apply_mouse(
            &mut app,
            mouse(
                MouseEventKind::Down(MouseButton::Left),
                operator.x + 2,
                operator.y + 1,
            ),
            area,
        )
        .unwrap();
        assert_eq!(app.dispatch_focus(), DispatchFocus::Agent);
    }

    #[test]
    fn mouse_wheel_scrolls_only_the_pane_under_the_cursor() {
        let mut app = sample_app();
        app.set_active_tab(AppTab::Dispatch);
        app.launch_prompt = "keep the cut bounded. ".repeat(80);
        app.launch_history = (0..40).map(|i| format!("launch-{i}")).collect();
        // The declaration deck collapses the prompt to a single row, so a tall
        // terminal shows it whole. Use a short one, where it really overflows.
        let area = ratatui::layout::Rect::new(0, 0, 120, 24);
        let layout = crate::layout::dispatch_layout(crate::layout::root_layout(area).body);
        apply_mouse(
            &mut app,
            mouse(
                MouseEventKind::ScrollDown,
                layout.deck.x + 2,
                layout.deck.y + 2,
            ),
            area,
        )
        .unwrap();
        apply_mouse(
            &mut app,
            mouse(
                MouseEventKind::ScrollDown,
                layout.deck.x + 2,
                layout.deck.y + 2,
            ),
            area,
        )
        .unwrap();
        assert!(
            app.interaction.scroll.deck > 0,
            "deck under the cursor must scroll"
        );
        assert_eq!(
            app.interaction.scroll.trail, 0,
            "trail must stay put while the wheel is over the deck"
        );

        let deck_after = app.interaction.scroll.deck;
        apply_mouse(
            &mut app,
            mouse(
                MouseEventKind::ScrollDown,
                layout.trail.x + 2,
                layout.trail.y + 2,
            ),
            area,
        )
        .unwrap();
        assert_eq!(
            app.interaction.scroll.deck, deck_after,
            "deck must stay put while the wheel is over the trail"
        );
        assert!(
            app.interaction.scroll.trail > 0,
            "trail under the cursor must scroll"
        );
        assert_eq!(
            app.interaction.focused,
            Some(crate::layout::PaneId::DispatchTrail)
        );
    }

    #[test]
    fn mouse_click_selects_a_monitor_run_row() {
        let mut app = sample_app();
        app.set_active_tab(AppTab::Monitor);
        let area = dispatch_area();
        let list = crate::layout::monitor_layout(crate::layout::root_layout(area).body, 0, 0).list;
        apply_mouse(
            &mut app,
            mouse(
                MouseEventKind::Down(MouseButton::Left),
                list.x + 2,
                list.y + 3,
            ),
            area,
        )
        .unwrap();
        assert_eq!(app.selected, 1);
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
    fn launch_exec_failure_reaches_the_operator_with_command_and_stderr() {
        // The readiness probe this test used to cover is gone with the rest of
        // VOC's parallel launch runtime: the launcher's receipt, not a probe,
        // decides whether a run started. What must survive is that a failure to
        // even execute the launcher is reported in full.
        let error = LaunchRunError::Exec {
            message: "command exited with status: 1".to_string(),
            stderr: "boom\nstack\n".to_string(),
        };
        let lines = error.detail_lines("vibecrafted workflow claude --json".to_string());
        assert_eq!(lines[0], "command: vibecrafted workflow claude --json");
        assert_eq!(lines[1], "error: command exited with status: 1");
        assert!(lines.iter().any(|line| line == "stderr:"));
        assert!(lines.iter().any(|line| line == "boom"));
        assert!(lines.iter().any(|line| line == "stack"));
    }

    #[test]
    fn launch_exec_failure_without_stderr_renders_no_empty_section() {
        let error = LaunchRunError::Exec {
            message: "command exited with status: 2".to_string(),
            stderr: String::new(),
        };
        let lines = error.detail_lines("vibecrafted workflow claude --json".to_string());
        assert!(
            !lines.iter().any(|line| line == "stderr:"),
            "an empty stderr must not render a header: lines={lines:?}"
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

        let leftover_meta =
            classify_artifact_change(&[PathBuf::from("/tmp/home/artifacts/run/report.meta.json")]);
        assert_eq!(leftover_meta, ArtifactChange::default());

        let relevant = classify_artifact_change(&[
            PathBuf::from("/tmp/home/artifacts/project/polarize/run/prism.json"),
            PathBuf::from("/tmp/home/artifacts/run/Untitled note.md"),
        ]);
        assert!(relevant.polarize);
        assert!(relevant.mission_control);
    }

    #[test]
    fn transcript_churn_does_not_invalidate_the_control_plane_projection() {
        assert!(!is_projection_path(&PathBuf::from(
            "/tmp/control_plane/runtime_runs/impl-1/transcript.log"
        )));
        assert!(!is_projection_path(&PathBuf::from(
            "/tmp/control_plane/runtime_runs/impl-1/transcript.human.log"
        )));
        assert!(is_projection_path(&PathBuf::from(
            "/tmp/control_plane/events.jsonl"
        )));
        assert!(is_projection_path(&PathBuf::from(
            "/tmp/control_plane/runs/impl-1.json"
        )));
        assert!(is_projection_path(&PathBuf::from(
            "/tmp/control_plane/runtime_runs/impl-1"
        )));
    }

    #[test]
    fn projection_revision_ignores_transcript_appends() {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path().join("control_plane");
        let run_dir = root.join("runtime_runs").join("impl-1");
        std::fs::create_dir_all(root.join("runs")).expect("runs");
        std::fs::create_dir_all(&run_dir).expect("runtime run");
        std::fs::write(root.join("events.jsonl"), "{}\n").expect("events");
        std::fs::write(
            root.join("runs/impl-1.json"),
            r#"{"run_id":"impl-1","state":"running"}"#,
        )
        .expect("snapshot");
        std::fs::write(run_dir.join("transcript.log"), "hello\n").expect("transcript");
        let before = projection_revision(&root);
        std::fs::write(run_dir.join("transcript.log"), "hello\nworld\n").expect("append");
        assert_eq!(before, projection_revision(&root));
        std::fs::write(root.join("events.jsonl"), "{}\n{}\n").expect("events grew");
        assert_ne!(before, projection_revision(&root));
    }

    #[test]
    fn control_plane_and_artifact_watch_roots_are_disjoint() {
        let home = PathBuf::from("/tmp/vc-home");
        let state = home.join("control_plane");
        let artifact = artifact_watch_root(&home);
        for root in control_plane_watch_roots(&state) {
            assert_ne!(root, artifact);
            assert!(!artifact.starts_with(&root));
            assert!(!root.starts_with(&artifact));
        }
    }

    #[test]
    fn dropping_the_state_watcher_disconnects_the_channel() {
        let dir = tempfile::tempdir().expect("tempdir");
        let (tx, rx) = mpsc::channel();
        let watcher = start_state_watcher(dir.path(), tx).expect("watcher");
        drop(watcher);
        assert!(matches!(
            rx.try_recv(),
            Err(std::sync::mpsc::TryRecvError::Disconnected)
                | Err(std::sync::mpsc::TryRecvError::Empty)
        ));
        // After drop the notify callback is gone; a later send path cannot exist.
        assert!(rx.recv_timeout(Duration::from_millis(50)).is_err());
    }

    #[test]
    fn observe_polling_backs_off_after_failures_and_resets_on_success() {
        let start = Instant::now();
        let mut scheduler = RefreshScheduler::new(start, true, true);
        scheduler.note_observe_result(false);
        scheduler.note_observe_result(false);
        assert_eq!(scheduler.observe_interval(), Duration::from_secs(8));
        let idle = scheduler.plan(start + Duration::from_secs(4));
        assert!(!idle.observe);
        let due = scheduler.plan(start + Duration::from_secs(8));
        assert!(due.observe);
        scheduler.note_observe_result(true);
        assert_eq!(scheduler.observe_interval(), OBSERVE_REFRESH_INTERVAL);
    }

    #[test]
    fn explicit_refresh_bypasses_debounce_and_loads_new_control_plane_truth() {
        let dir = tempfile::tempdir().expect("tempdir");
        let state_root = dir.path().join("control_plane");
        std::fs::create_dir_all(state_root.join("runs")).expect("runs dir");
        let heartbeat = chrono::Utc::now().to_rfc3339();
        std::fs::write(
            state_root.join("runs/forced-refresh.json"),
            format!(
                r#"{{
                "run_id": "forced-refresh",
                "agent": "codex",
                "skill": "hydrate",
                "state": "running",
                "updated_at": "{heartbeat}",
                "last_heartbeat": "{heartbeat}"
            }}"#
            ),
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

    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// Failure guard for barrier waits; no assertion depends on elapsed time.
    const GUARD: Duration = Duration::from_secs(10);

    fn ctrl(c: char) -> KeyEvent {
        KeyEvent::new(KeyCode::Char(c), KeyModifiers::CONTROL)
    }

    fn state_with_runs(root: &str, runs: &[(&str, &str)]) -> ControlPlaneState {
        let mut state = ControlPlaneState::empty(root);
        state.runs = runs
            .iter()
            .map(|(run_id, agent)| sample_run(run_id, agent, &format!("op-{run_id}")).snapshot)
            .collect();
        state.retained_runs = state.runs.clone();
        state
    }

    fn run_ids(app: &App) -> Vec<String> {
        app.runs
            .iter()
            .map(|run| run.snapshot.run_id.clone())
            .collect()
    }

    /// A refresh source whose projection load parks at a barrier until the
    /// test releases it — a slow `runs/` scan without a clock.
    struct HeldScan {
        entered: mpsc::Sender<()>,
        release: mpsc::Receiver<()>,
        loaded: ControlPlaneState,
        loads: Arc<AtomicUsize>,
    }

    impl RefreshSource for HeldScan {
        fn projection_revision(&mut self, _state_root: &Path) -> u64 {
            7
        }

        fn control_plane(&mut self, _state_root: &Path) -> io::Result<ControlPlaneState> {
            self.loads.fetch_add(1, Ordering::SeqCst);
            let _ = self.entered.send(());
            let _ = self.release.recv();
            Ok(self.loaded.clone())
        }

        fn polarize(&mut self, _repo: &Path) -> Vec<crate::polarize::PolarizeIntent> {
            Vec::new()
        }

        fn mission_control(
            &mut self,
            _state: &ControlPlaneState,
            _artifact_root: &Path,
            _intents: &[crate::polarize::PolarizeIntent],
        ) -> MissionControlState {
            MissionControlState::default()
        }
    }

    struct HeldConsole {
        console: ConsoleLoop,
        state_changes: mpsc::Sender<()>,
        entered: mpsc::Receiver<()>,
        release: mpsc::Sender<()>,
        loads: Arc<AtomicUsize>,
    }

    /// Transcripts that answer at once, for tests that do not look at them.
    struct FixtureTranscripts;

    impl TranscriptSource for FixtureTranscripts {
        fn read(&mut self, job: &refresh::TranscriptJob) -> Result<String, String> {
            Ok(format!("transcript of {}", job.run_id))
        }
    }

    /// A transcript source whose first read parks at a barrier; later reads
    /// answer at once.
    struct HeldTranscripts {
        entered: mpsc::Sender<String>,
        release: mpsc::Receiver<()>,
        held_once: bool,
    }

    impl TranscriptSource for HeldTranscripts {
        fn read(&mut self, job: &refresh::TranscriptJob) -> Result<String, String> {
            if !self.held_once {
                self.held_once = true;
                let _ = self.entered.send(job.run_id.clone());
                let _ = self.release.recv();
            }
            Ok(format!("transcript of {}", job.run_id))
        }
    }

    fn held_console(app: &App, loaded: ControlPlaneState) -> HeldConsole {
        held_console_with(app, loaded, FixtureTranscripts)
    }

    fn held_console_with<T: TranscriptSource>(
        app: &App,
        loaded: ControlPlaneState,
        transcripts: T,
    ) -> HeldConsole {
        let (entered_tx, entered) = mpsc::channel();
        let (release, release_rx) = mpsc::channel();
        let (state_changes, state_rx) = mpsc::channel();
        let (_artifact_tx, artifact_rx) = mpsc::channel();
        let loads = Arc::new(AtomicUsize::new(0));
        let console = ConsoleLoop::start(
            app,
            HeldScan {
                entered: entered_tx,
                release: release_rx,
                loaded,
                loads: Arc::clone(&loads),
            },
            transcripts,
            ChangeFeeds {
                state: state_rx,
                artifacts: artifact_rx,
                state_watcher_active: true,
                artifact_watcher_active: true,
            },
        )
        .expect("console loop");
        HeldConsole {
            console,
            state_changes,
            entered,
            release,
            loads,
        }
    }

    fn press(held: &mut HeldConsole, app: &mut App, key: KeyEvent) -> LoopControl {
        held.console
            .handle_event(app, Event::Key(key))
            .expect("key handled")
    }

    fn type_text(held: &mut HeldConsole, app: &mut App, text: &str) {
        for c in text.chars() {
            press(held, app, super::tests::key(KeyCode::Char(c)));
        }
    }

    #[test]
    fn keys_are_served_while_a_control_plane_scan_is_held_and_its_answer_lands_after() {
        let mut app = sample_app();
        app.selected = 1;
        let selected = app.selected_run().unwrap().snapshot.run_id.clone();
        let loaded = state_with_runs(
            "/tmp/state",
            &[
                ("run-1", "codex"),
                ("run-2", "claude"),
                ("run-fresh", "grok"),
            ],
        );
        let mut held = held_console(&app, loaded);
        let started = Instant::now();

        // The watcher reports a projection change; once the debounce passes the
        // loop hands the read to the worker, which parks inside the scan.
        held.state_changes.send(()).unwrap();
        assert_eq!(held.console.tick(&mut app, started), LoopControl::Continue);
        held.console.tick(&mut app, started + CHANGE_DEBOUNCE);
        held.entered
            .recv_timeout(GUARD)
            .expect("the scan started and is held at the barrier");
        assert!(app.is_refreshing());

        // Everything below is handled while the scan is still blocked.
        press(&mut held, &mut app, key(KeyCode::Char('e')));
        assert_eq!(app.focus, LaunchFocus::EditPrompt);
        type_text(&mut held, &mut app, " while the scan is held");
        press(&mut held, &mut app, ctrl('s'));
        assert!(app.launch_prompt.ends_with(" while the scan is held"));
        assert_eq!(app.focus, LaunchFocus::Browse);

        press(&mut held, &mut app, key(KeyCode::Up));
        assert_eq!(app.dispatch_focus(), DispatchFocus::Sandbox);

        press(&mut held, &mut app, key(KeyCode::Char('g')));
        assert_eq!(app.focus, LaunchFocus::EditRepo);
        press(&mut held, &mut app, key(KeyCode::Esc));
        assert_eq!(app.focus, LaunchFocus::Browse);
        assert_eq!(app.config.repo, PathBuf::from("/tmp/repo"));

        assert_eq!(
            held.console.tick(
                &mut app,
                started + CHANGE_DEBOUNCE + Duration::from_millis(10)
            ),
            LoopControl::Continue
        );
        assert!(!run_ids(&app).contains(&"run-fresh".to_string()));

        // Released, the answer arrives as a message and reaches the board.
        held.release.send(()).unwrap();
        assert!(held.console.wait_until(&mut app, GUARD, |app| {
            run_ids(app).contains(&"run-fresh".to_string())
        }));
        assert!(run_ids(&app).contains(&"run-fresh".to_string()));
        assert_eq!(
            app.selected_run().unwrap().snapshot.run_id,
            selected,
            "selection follows the run id across the refresh"
        );
        assert!(
            app.launch_prompt.ends_with(" while the scan is held"),
            "the prompt typed during the scan survives its answer"
        );
        assert!(!app.is_refreshing());
        assert_eq!(held.loads.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn an_answer_older_than_the_board_is_refused() {
        let mut app = sample_app();
        app.request_refresh(RefreshNeeds {
            control_plane: true,
            ..RefreshNeeds::default()
        });
        let older = app.take_refresh_job().unwrap();
        app.request_refresh(RefreshNeeds {
            force_control_plane: true,
            ..RefreshNeeds::default()
        });
        let newer = app.take_refresh_job().unwrap();
        assert!(newer.generation > older.generation);

        let answer = |generation, run_id| refresh::RefreshResult {
            generation,
            repo: PathBuf::from("/tmp/repo"),
            control_plane: Some(Ok(state_with_runs("/tmp/state", &[(run_id, "codex")]))),
            polarize: None,
            mission_control: None,
        };
        assert!(app.apply_refresh(answer(newer.generation, "run-newer")));
        assert!(!app.apply_refresh(answer(older.generation, "run-older")));
        assert_eq!(run_ids(&app), vec!["run-newer".to_string()]);
    }

    #[test]
    fn automatic_and_explicit_requests_share_one_pass_and_a_failed_load_keeps_the_board() {
        let mut app = sample_app();
        app.state = state_with_runs("/tmp/state", &[("run-1", "codex"), ("run-2", "claude")]);
        app.refresh_rendered_runs();
        let board = run_ids(&app);
        app.pending_launch = Some("workflow claude".to_string());

        // A watcher invalidation and the operator's `r` land before the loop
        // hands anything off: one pass carries both.
        app.request_refresh(RefreshNeeds {
            control_plane: true,
            mission_control: true,
            ..RefreshNeeds::default()
        });
        app.request_full_refresh();
        let pass = app.take_refresh_job().expect("one hand-off");
        assert_eq!(pass.needs, RefreshNeeds::everything());
        assert!(app.take_refresh_job().is_none());

        assert!(app.apply_refresh(refresh::RefreshResult {
            generation: pass.generation,
            repo: app.config.repo.clone(),
            control_plane: Some(Err("runs/ is unreadable".to_string())),
            polarize: None,
            mission_control: None,
        }));
        assert_eq!(run_ids(&app), board, "last good data stays on the board");
        assert!(
            app.status_line
                .contains("control-plane unavailable: runs/ is unreadable"),
            "{}",
            app.status_line
        );
        assert_eq!(app.pending_launch.as_deref(), Some("workflow claude"));
    }

    #[test]
    fn quitting_with_an_unanswered_launch_waits_for_its_receipt() {
        let mut app = sample_app();
        let mut held = held_console(&app, ControlPlaneState::empty("/tmp/state"));
        app.pending_launch = Some("workflow claude".to_string());

        assert_eq!(
            press(&mut held, &mut app, key(KeyCode::Char('q'))),
            LoopControl::Continue
        );
        assert!(app.status_line.contains("waiting for the launcher receipt"));
        assert_eq!(
            held.console.tick(&mut app, Instant::now()),
            LoopControl::Continue
        );
        held.console
            .background_tx
            .send(BackgroundMessage::LaunchHalted {
                summary: "workflow claude".to_string(),
                detail: vec!["halted before the launcher started".to_string()],
            })
            .unwrap();
        assert_eq!(
            held.console.tick(&mut app, Instant::now()),
            LoopControl::Exit
        );

        let mut app = sample_app();
        let mut held = held_console(&app, ControlPlaneState::empty("/tmp/state"));
        app.pending_launch = Some("workflow claude".to_string());
        assert_eq!(press(&mut held, &mut app, ctrl('c')), LoopControl::Continue);
        assert_eq!(
            press(&mut held, &mut app, ctrl('c')),
            LoopControl::Exit,
            "a second Ctrl+C leaves without the receipt"
        );

        let mut app = sample_app();
        let mut held = held_console(&app, ControlPlaneState::empty("/tmp/state"));
        assert_eq!(
            press(&mut held, &mut app, key(KeyCode::Char('q'))),
            LoopControl::Exit
        );
    }

    #[cfg(unix)]
    #[test]
    fn the_form_launches_into_an_edited_repository_after_refusing_invalid_ones() {
        use std::os::unix::fs::PermissionsExt;

        let dir = tempfile::tempdir().unwrap();
        let startup = dir.path().join("startup");
        let destination = dir.path().join("destination");
        std::fs::create_dir_all(startup.join(".git")).unwrap();
        std::fs::create_dir_all(destination.join(".git")).unwrap();
        let startup = startup.canonicalize().unwrap();
        let destination = destination.canonicalize().unwrap();
        let deck = dir.path().join("deck.sh");
        std::fs::write(
            &deck,
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$(dirname \"$0\")/argv.txt\"\ncat > \"$(dirname \"$0\")/stdin.txt\"\nprintf '%s\\n' '{\"schema\":\"vibecrafted.launch_receipt.v1\",\"accepted\":true,\"run_id\":\"work-1\",\"status\":\"launching\"}'\n",
        )
        .unwrap();
        std::fs::set_permissions(&deck, std::fs::Permissions::from_mode(0o755)).unwrap();
        let argv_file = dir.path().join("argv.txt");

        let mut app = sample_app();
        app.config.repo = startup.clone();
        app.config.command_deck = deck;
        app.config.state_root = dir.path().join("state");
        app.config.no_verify_gate = true;
        app.launch_presentation = Presentation::Headless;
        app.launch_agent = app
            .agent_choices()
            .iter()
            .position(|agent| agent == "claude")
            .expect("fixture catalog lists claude");
        app.launch_model = "claude-opus-5".to_string();
        app.launch_prompt = "Ship the destination fix".to_string();
        let mut held = held_console(&app, ControlPlaneState::empty("/tmp/state"));

        // Invalid destinations are refused in the editor: the current repository
        // stays, the refusal is on screen, and nothing reaches the launcher.
        press(&mut held, &mut app, key(KeyCode::Char('g')));
        assert_eq!(app.repo_edit.input, startup.to_string_lossy());
        let missing = dir.path().join("missing");
        for (typed, reason) in [
            ("relative/project".to_string(), "relative"),
            (missing.to_string_lossy().into_owned(), "not reachable"),
        ] {
            press(&mut held, &mut app, ctrl('u'));
            type_text(&mut held, &mut app, &typed);
            press(&mut held, &mut app, key(KeyCode::Enter));
            assert_eq!(app.focus, LaunchFocus::EditRepo);
            let refusal = app.repo_edit.error.clone().unwrap_or_default();
            assert!(refusal.contains(reason), "{typed}: {refusal}");
            assert!(
                app.repo_edit_lines()
                    .iter()
                    .any(|line| line.starts_with("refused:"))
            );
            assert_eq!(app.config.repo, startup);
        }
        assert!(
            !argv_file.exists(),
            "a refused destination launches nothing"
        );

        // A valid destination replaces the startup directory; the rest of the
        // declaration is untouched.
        press(&mut held, &mut app, ctrl('u'));
        type_text(&mut held, &mut app, &destination.to_string_lossy());
        press(&mut held, &mut app, key(KeyCode::Enter));
        assert_eq!(app.focus, LaunchFocus::Browse);
        assert_eq!(app.config.repo, destination);
        assert_eq!(app.launch_prompt, "Ship the destination fix");
        assert_eq!(app.launch_model, "claude-opus-5");
        assert_eq!(app.selected_agent(), "claude");
        assert_eq!(app.launch_presentation, Presentation::Headless);
        assert!(
            app.prompt_lines()
                .iter()
                .any(|line| line == &format!("▶ repo: {}", destination.display()))
        );
        let reread = app
            .take_refresh_job()
            .expect("repository-derived reads are asked for again");
        assert!(reread.needs.polarize && reread.needs.mission_control);
        assert_eq!(reread.repo, destination);

        // A refresh answer for the new destination does not undo the choice.
        assert!(app.apply_refresh(refresh::RefreshResult {
            generation: reread.generation,
            repo: destination.clone(),
            control_plane: None,
            polarize: Some(Vec::new()),
            mission_control: Some(MissionControlState::default()),
        }));
        assert_eq!(app.config.repo, destination);

        // Launch through the real handler: Repo → Prompt → Sandbox, then Enter.
        press(&mut held, &mut app, key(KeyCode::Up));
        press(&mut held, &mut app, key(KeyCode::Up));
        press(&mut held, &mut app, key(KeyCode::Enter));
        assert!(app.pending_launch.is_some(), "status: {}", app.status_line);
        assert!(
            held.console
                .wait_until(&mut app, Duration::from_secs(60), |app| {
                    app.pending_launch.is_none()
                }),
            "the launcher receipt arrives"
        );
        assert!(app.pending_launch.is_none());

        let argv = std::fs::read_to_string(&argv_file).unwrap();
        let argv = argv.lines().collect::<Vec<_>>();
        let repo_at = argv
            .iter()
            .position(|arg| *arg == "--repo")
            .expect("--repo");
        assert_eq!(argv[repo_at + 1], destination.to_string_lossy());
        assert!(
            !argv.iter().any(|arg| *arg == startup.to_string_lossy()),
            "the startup directory never reaches the launcher: {argv:?}"
        );
        assert_eq!(
            std::fs::read_to_string(dir.path().join("stdin.txt")).unwrap(),
            "Ship the destination fix"
        );
    }

    #[test]
    fn observe_navigation_is_served_while_a_transcript_read_is_held_and_a_late_answer_is_dropped() {
        let mut app = sample_app();
        app.config.view = crate::observe::ConsoleView::Observe;
        app.queue_scope = QueueScope::All;
        app.state = state_with_runs(
            "/tmp/state",
            &[("run-1", "codex"), ("run-2", "claude"), ("run-3", "grok")],
        );
        let (entered_tx, entered) = mpsc::channel();
        let (release, release_rx) = mpsc::channel();
        let loaded = app.state.clone();
        let mut held = held_console_with(
            &app,
            loaded,
            HeldTranscripts {
                entered: entered_tx,
                release: release_rx,
                held_once: false,
            },
        );

        // Projecting the board asks for the selected run's transcript; the
        // loop hands that read to the worker, which parks inside it.
        app.refresh_rendered_runs();
        let first = app.observe.runs[app.observe.selected].run_id.clone();
        assert_eq!(
            app.observe.transcript,
            format!("loading transcript for {first}…")
        );
        held.console.tick(&mut app, Instant::now());
        assert_eq!(
            entered
                .recv_timeout(GUARD)
                .expect("the transcript read is held"),
            first
        );

        // Navigation, a view toggle and a cancelled search are served meanwhile.
        press(&mut held, &mut app, key(KeyCode::Char('j')));
        let second = app.observe.runs[app.observe.selected].run_id.clone();
        assert_ne!(second, first);
        assert_eq!(
            app.observe.transcript,
            format!("loading transcript for {second}…")
        );
        press(&mut held, &mut app, key(KeyCode::Char('t')));
        press(&mut held, &mut app, key(KeyCode::Char('/')));
        assert_eq!(app.focus, LaunchFocus::Search);
        press(&mut held, &mut app, key(KeyCode::Esc));
        assert_eq!(app.focus, LaunchFocus::Browse);
        held.console.tick(&mut app, Instant::now());

        // Released: the read for the run already left must not land; the read
        // for the current selection does.
        let stale_generation = app.observe.transcript_generation - 1;
        release.send(()).unwrap();
        assert!(held.console.wait_until(&mut app, GUARD, |app| {
            app.observe.transcript_raw == format!("transcript of {second}")
        }));
        assert_eq!(app.observe.runs[app.observe.selected].run_id, second);
        assert!(app.observe.transcript_loading.is_none());

        // The same late answer, delivered again, is refused outright.
        assert!(!app.apply_transcript(refresh::TranscriptResult {
            generation: stale_generation,
            run_id: first.clone(),
            body: Ok(refresh::LoadedTranscript {
                raw: format!("transcript of {first}"),
                human: String::new(),
            }),
        }));
        assert_eq!(
            app.observe.transcript_raw,
            format!("transcript of {second}")
        );
    }

    #[test]
    fn the_repository_row_follows_the_prompt_in_the_declaration_deck() {
        let mut app = sample_app();
        app.set_active_tab(AppTab::Dispatch);
        app.dispatch_selected = DispatchFocus::Prompt as usize;
        app.move_dispatch_selection(1);
        assert_eq!(app.dispatch_focus(), DispatchFocus::Repo);
        let lines = app.prompt_lines();
        assert!(lines[DispatchFocus::Prompt as usize].starts_with("  prompt:"));
        assert_eq!(lines[DispatchFocus::Repo as usize], "▶ repo: /tmp/repo");
        app.move_dispatch_selection(1);
        assert_eq!(app.dispatch_focus(), DispatchFocus::Kind, "the deck wraps");
    }
}
