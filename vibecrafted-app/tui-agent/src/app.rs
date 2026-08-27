use crate::config::{AppConfig, path_display};
use crate::launch::{
    LaunchCommand, LaunchKind, LaunchRequest, LaunchRuntime, build_launch_command,
};
use crate::memory::{self, MemoryState};
use crate::mission_control::{self, ActionQueueItem, ActionQueueKind, MissionControlState};
use crate::observe::{self, ObserveHealth, ObserveState};
use crate::polarize::{PolarizeBand, PolarizeIntent};
use crate::skills_catalog::{self, SkillAgent, SkillPayload, SkillPayloadKind};
use crate::state::{
    ControlPlaneState, RenderedRun, RunKind, is_actionable_kind, render_runs, workspace_matches,
};
use std::collections::BTreeMap;
use std::ffi::OsString;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AppTab {
    Monitor,
    Dispatch,
    Controls,
    MissionControl,
}

impl AppTab {
    pub const TITLES: [&'static str; 4] = ["Monitor", "Dispatch", "Controls", "Mission Control"];

    pub fn label(self) -> &'static str {
        match self {
            Self::Monitor => "Monitor",
            Self::Dispatch => "Dispatch",
            Self::Controls => "Controls",
            Self::MissionControl => "Mission Control",
        }
    }

    pub fn from_index(index: usize) -> Self {
        match index % Self::TITLES.len() {
            0 => Self::Monitor,
            1 => Self::Dispatch,
            2 => Self::Controls,
            _ => Self::MissionControl,
        }
    }

    pub fn index(self) -> usize {
        match self {
            Self::Monitor => 0,
            Self::Dispatch => 1,
            Self::Controls => 2,
            Self::MissionControl => 3,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DispatchFocus {
    Kind,
    Agent,
    Runtime,
    Prompt,
}

impl DispatchFocus {
    pub const COUNT: usize = 4;

    pub fn from_index(index: usize) -> Self {
        match index % Self::COUNT {
            0 => Self::Kind,
            1 => Self::Agent,
            2 => Self::Runtime,
            _ => Self::Prompt,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LaunchFocus {
    Browse,
    EditPrompt,
    Help,
    Search,
    Error,
    Artifact,
    Memory,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QueueScope {
    Live,
    History,
    All,
}

impl QueueScope {
    pub fn label(self) -> &'static str {
        match self {
            QueueScope::Live => "live",
            QueueScope::History => "history",
            QueueScope::All => "all",
        }
    }

    pub fn title(self) -> &'static str {
        match self {
            QueueScope::Live => "Live · this workspace",
            QueueScope::History => "History / archive",
            QueueScope::All => "All runs",
        }
    }

    pub fn next(self) -> Self {
        match self {
            QueueScope::Live => QueueScope::History,
            QueueScope::History => QueueScope::All,
            QueueScope::All => QueueScope::Live,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DeepAction {
    AttachSession(String),
    ResumeSession {
        agent: String,
        session: String,
    },
    OpenReport(PathBuf),
    OpenTranscript(PathBuf),
    OpenRoot(PathBuf),
    /// Run `rmcp-mux health --service <name>` against a known MCP daemon.
    /// Available when at least one rmcp-mux status snapshot is cached on
    /// the App; not tied to the selected run, so the operator can health-
    /// check the supervisor even when no agent run is selected.
    MuxHealth {
        service: String,
    },
    MuxRestart(String),
    MuxVerifyClient(rmcp_mux::ipc::ClientKind),
    MuxFixClientDrift(rmcp_mux::ipc::ClientKind),
    /// Consumer-side rendering for a polarize prism emitted by Vibecrafted.
    /// The operator does not score or originate the band; it only surfaces
    /// the runner's prism payload and suggested action path.
    PolarizeIntent {
        band: PolarizeBand,
        score: u8,
        run_id: String,
        prism_path: PathBuf,
    },
    /// Launch a first-class Vibecrafted skill entrypoint.
    SkillLaunch {
        skill: String,
        agent: SkillAgent,
        payload: SkillPayload,
    },
}

impl DeepAction {
    pub fn label(&self) -> String {
        match self {
            DeepAction::AttachSession(session) => {
                format!("Attach operator session: vibecrafted dashboard attach {session}")
            }
            DeepAction::ResumeSession { agent, session } => {
                format!("Resume agent session: vibecrafted resume {agent} --session {session}")
            }
            DeepAction::OpenReport(path) => {
                format!("Open latest report: {}", path.to_string_lossy())
            }
            DeepAction::OpenTranscript(path) => {
                format!("Open latest transcript: {}", path.to_string_lossy())
            }
            DeepAction::OpenRoot(path) => format!("Open run root: {}", path.to_string_lossy()),
            DeepAction::MuxHealth { service } => {
                format!("Health-check MCP daemon: rmcp-mux health --service {service}")
            }
            DeepAction::MuxRestart(service) => {
                format!("Restart MCP daemon: rmcp-mux restart --service {service}")
            }
            DeepAction::MuxVerifyClient(_) => "Verify client routing through mux".to_string(),
            DeepAction::MuxFixClientDrift(_) => {
                "Fix client drift: rmcp-mux wizard --strategy auto-rewire".to_string()
            }
            DeepAction::PolarizeIntent {
                band,
                score,
                run_id,
                prism_path,
            } => format!(
                "Inspect polarize intent: {} score {} run {} -> {}",
                band.label(),
                score,
                run_id,
                prism_path.to_string_lossy()
            ),
            DeepAction::SkillLaunch {
                skill,
                agent,
                payload,
            } => {
                let payload_label = match payload {
                    SkillPayload::Prompt(prompt) if !prompt.trim().is_empty() => "prompt",
                    SkillPayload::File(_) => "file",
                    SkillPayload::Prompt(_) | SkillPayload::None => "no payload",
                };
                format!(
                    "Launch skill: vibecrafted {} {} ({payload_label})",
                    skill.trim_start_matches("vc-"),
                    agent.label()
                )
            }
        }
    }

    pub fn control_label(&self) -> String {
        match self {
            DeepAction::AttachSession(session) => {
                format!("Attach session {}", truncate_id(session, 24))
            }
            DeepAction::ResumeSession { agent, session } => {
                format!("Resume {agent} {}", truncate_id(session, 18))
            }
            DeepAction::OpenReport(_) => "Open latest report".to_string(),
            DeepAction::OpenTranscript(_) => "Open human transcript".to_string(),
            DeepAction::OpenRoot(_) => "Open run workspace".to_string(),
            DeepAction::MuxHealth { service } => format!("Health-check {service}"),
            DeepAction::MuxRestart(service) => format!("Restart {service}"),
            DeepAction::MuxVerifyClient(_) => "Verify mux client routing".to_string(),
            DeepAction::MuxFixClientDrift(_) => "Fix mux client drift".to_string(),
            DeepAction::PolarizeIntent {
                band, score, run_id, ..
            } => format!(
                "Polarize {} {} {}",
                band.label(),
                score,
                truncate_id(run_id, 18)
            ),
            DeepAction::SkillLaunch { skill, agent, .. } => {
                format!("Launch {} ({})", skill.trim_start_matches("vc-"), agent.label())
            }
        }
    }
}

#[derive(Debug)]
pub struct App {
    pub config: AppConfig,
    pub state: ControlPlaneState,
    pub runs: Vec<RenderedRun>,
    pub selected: usize,
    pub active_tab: usize,
    pub launch_kind: LaunchKind,
    pub launch_agent: usize,
    pub launch_prompt: String,
    pub launch_runtime: LaunchRuntime,
    pub dispatch_selected: usize,
    pub focus: LaunchFocus,
    pub status_line: String,
    pub launch_history: Vec<String>,
    pub deep_selected: usize,
    pub queue_scope: QueueScope,
    pub search_query: String,
    pub error_title: String,
    pub error_lines: Vec<String>,
    pub artifact_title: String,
    pub artifact_lines: Vec<String>,
    /// Cached rmcp-mux supervisor snapshots (from
    /// `crate::mux::current_summaries`). Refreshed from mux events and by an
    /// explicit full refresh so drawing never performs IO.
    pub mux_summaries: Vec<crate::mux::MuxSummary>,
    pub mux_subscriber: Option<crate::mux::MuxSubscriber>,
    /// Cached polarize prism intents discovered under
    /// `$VIBECRAFTED_HOME/artifacts/**/polarize/<run_id>/prism.json`.
    /// Refreshed when the artifact watcher observes a Polarize path or when
    /// the operator explicitly requests a full refresh.
    pub polarize_intents: Vec<PolarizeIntent>,
    /// Cached Mission Control view derived from
    /// `~/.vibecrafted/artifacts/**/*.meta.json` plus live control-plane
    /// runs. Rebuilt after relevant state/artifact changes so the dashboard
    /// tab can render without doing IO inside the draw path. The artifact root
    /// is resolved once via `mission_control::default_artifact_root()`.
    pub mission_control: MissionControlState,
    /// Selected panel index inside the Mission Control tab (0..7).
    pub mission_focus: usize,
    /// Resolved artifact root used by the mission control aggregator;
    /// kept on the App so tests can swap it explicitly.
    pub mission_artifact_root: PathBuf,
    pub observe: ObserveState,
    pub memory: MemoryState,
}

impl App {
    pub fn new(config: AppConfig) -> anyhow::Result<Self> {
        let state = ControlPlaneState::load(&config.state_root)
            .unwrap_or_else(|_| ControlPlaneState::empty(&config.state_root));
        trace_expensive_refresh("control_plane");
        let runs = render_runs(&state);
        let launch_runtime = config.launch_runtime;
        let mission_artifact_root = mission_control::default_artifact_root();
        let observe_origin = config.server.clone();
        let memory_project = memory::default_project(&config.launch_root);
        let mut app = Self {
            config,
            state,
            runs,
            selected: 0,
            active_tab: AppTab::Monitor.index(),
            launch_kind: LaunchKind::Workflow,
            launch_agent: 0,
            launch_prompt: default_prompt(LaunchKind::Workflow),
            launch_runtime,
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
            mux_subscriber: None,
            polarize_intents: Vec::new(),
            mission_control: MissionControlState::default(),
            mission_focus: 0,
            mission_artifact_root,
            observe: ObserveState {
                origin: observe_origin,
                ..ObserveState::default()
            },
            memory: MemoryState {
                project: memory_project,
                ..MemoryState::default()
            },
        };
        apply_run_filters(
            &mut app.runs,
            app.queue_scope,
            &app.search_query,
            &app.config.launch_root,
        );
        app.sync_selection();
        app.refresh_mux();
        app.refresh_polarize();
        app.refresh_mission_control();
        let _ = app.refresh_observe();
        app.refresh_memory();
        let path = rmcp_mux::ipc::server::socket_path();
        let summaries = std::sync::Arc::new(std::sync::RwLock::new(app.mux_summaries.clone()));
        app.mux_subscriber = Some(crate::mux::MuxSubscriber::start(path, summaries));
        Ok(app)
    }

    pub fn refresh(&mut self) {
        self.refresh_control_plane();
        self.refresh_mux();
        self.refresh_polarize();
        self.refresh_mission_control();
        let _ = self.refresh_observe();
    }

    /// Reload the canonical control-plane projection after an invalidation.
    /// Selection follows the stable run id across sorting/filter changes.
    pub fn refresh_control_plane(&mut self) {
        let selected_run_id = self.selected_run().map(|run| run.snapshot.run_id.clone());
        match ControlPlaneState::load(&self.config.state_root) {
            Ok(state) => {
                trace_expensive_refresh("control_plane");
                self.state = state;
            }
            Err(error) => {
                self.append_status(format!("control-plane unavailable: {error}"));
                if self.state.runs.is_empty() && self.state.retained_runs.is_empty() {
                    self.state = ControlPlaneState::empty(&self.config.state_root);
                }
            }
        }
        let mut runs = render_runs(&self.state);
        apply_run_filters(
            &mut runs,
            self.queue_scope,
            &self.search_query,
            &self.config.launch_root,
        );
        self.runs = runs;
        if let Some(run_id) = selected_run_id
            && let Some(index) = self
                .runs
                .iter()
                .position(|run| run.snapshot.run_id == run_id)
        {
            self.selected = index;
        }
        self.sync_selection();
    }

    /// Recompute only time-derived labels and filters from cached state.
    /// This keeps ages and stale classifications moving without re-reading
    /// the control-plane filesystem.
    pub fn refresh_rendered_runs(&mut self) {
        let selected_run_id = self.selected_run().map(|run| run.snapshot.run_id.clone());
        let mut runs = render_runs(&self.state);
        apply_run_filters(
            &mut runs,
            self.queue_scope,
            &self.search_query,
            &self.config.launch_root,
        );
        self.runs = runs;
        if let Some(run_id) = selected_run_id
            && let Some(index) = self
                .runs
                .iter()
                .position(|run| run.snapshot.run_id == run_id)
        {
            self.selected = index;
        }
        self.sync_selection();
    }

    /// Refresh the remote Observe projection on its own bounded cadence.
    /// Returns whether the fetch succeeded so the scheduler can back off.
    pub fn refresh_observe(&mut self) -> bool {
        let origin = self.config.server.clone();
        self.observe.origin = origin.clone();
        match observe::fetch_state(&origin) {
            Ok((generated_at, runs)) => {
                self.observe.generated_at = generated_at;
                self.observe.status = ObserveHealth::Live;
                self.observe.error = None;
                self.observe.runs = runs;
                if self.observe.selected >= self.observe.runs.len() {
                    self.observe.selected = self.observe.runs.len().saturating_sub(1);
                }
                self.refresh_observe_transcript();
                true
            }
            Err(error) => {
                if self.observe.runs.is_empty() {
                    self.observe.status = ObserveHealth::Offline;
                } else {
                    self.observe.status = ObserveHealth::Degraded;
                }
                self.observe.error = Some(error.to_string());
                false
            }
        }
    }

    pub fn refresh_observe_transcript(&mut self) {
        let Some(run) = self.observe.runs.get(self.observe.selected) else {
            self.observe.transcript.clear();
            self.observe.transcript_run_id = None;
            return;
        };
        let run_id = run.run_id.clone();
        if self.observe.transcript_run_id.as_deref() == Some(run_id.as_str())
            && !self.observe.transcript.is_empty()
        {
            return;
        }
        match observe::fetch_transcript(&self.config.server, &run_id) {
            Ok(body) => {
                self.observe.transcript = if body.trim().is_empty() {
                    String::new()
                } else {
                    crate::run_detail::humanize_transcript(&body)
                };
                self.observe.transcript_run_id = Some(run_id);
            }
            Err(error) => {
                self.observe.transcript = format!("transcript unavailable: {error}");
                self.observe.transcript_run_id = Some(run_id);
            }
        }
    }

    pub fn move_observe_selection(&mut self, delta: isize) {
        if self.observe.runs.is_empty() {
            return;
        }
        let count = self.observe.runs.len() as isize;
        let mut index = self.observe.selected as isize + delta;
        while index < 0 {
            index += count;
        }
        self.observe.selected = (index % count) as usize;
        self.observe.transcript.clear();
        self.observe.transcript_run_id = None;
        self.refresh_observe_transcript();
    }

    pub fn refresh_memory(&mut self) {
        let project = memory::default_project(&self.config.launch_root);
        self.memory = memory::load_continuity(&project);
    }

    pub fn open_aicx_wizard(&mut self) {
        let project = memory::default_project(&self.config.launch_root);
        if let Err(error) = memory::launch_wizard(&project) {
            self.show_error("aicx wizard failed", vec![error.to_string()]);
        }
    }

    pub fn refresh_mission_control(&mut self) {
        self.mission_control = MissionControlState::build_with_intents(
            &self.state,
            &self.mission_artifact_root,
            &self.polarize_intents,
        );
        if self.mission_focus >= mission_panel_count() {
            self.mission_focus = mission_panel_count().saturating_sub(1);
        }
    }

    pub fn move_mission_focus(&mut self, delta: isize) {
        let count = mission_panel_count() as isize;
        if count == 0 {
            self.mission_focus = 0;
            return;
        }
        let mut index = self.mission_focus as isize + delta;
        while index < 0 {
            index += count;
        }
        self.mission_focus = (index % count) as usize;
    }

    /// Refresh cached rmcp-mux status snapshots from the discovered
    /// status files. Cheap (a few small JSON reads) so it is safe to call
    /// on the same cadence as the run-state refresh.
    pub fn refresh_mux(&mut self) {
        self.mux_summaries = crate::mux::current_summaries();
    }

    pub fn refresh_polarize(&mut self) {
        self.polarize_intents = crate::polarize::current_intents(&self.config.launch_root);
        trace_expensive_refresh("polarize");
    }

    pub fn handle_ipc_event(&mut self, _event: rmcp_mux::ipc::IpcEvent) {
        // The subscriber pushes events. We can either do a full IO refresh,
        // or apply the diff. The safest and most robust path is just calling refresh_mux().
        self.refresh_mux();
    }

    /// Lines for the Monitor tab "MCP daemons" panel. Returns an empty
    /// vec when no mux services are known (operator may simply not be
    /// running rmcp-mux), otherwise one summary line per service plus a
    /// header.
    pub fn mux_status_lines(&self) -> Vec<String> {
        if self.mux_summaries.is_empty() {
            return Vec::new();
        }
        let total = self.mux_summaries.len();
        let unhealthy = self
            .mux_summaries
            .iter()
            .filter(|summary| !summary.is_healthy())
            .count();
        let mut lines = Vec::with_capacity(total + 1);
        if unhealthy == 0 {
            lines.push(format!("MCP daemons ({total} healthy):"));
        } else {
            lines.push(format!("MCP daemons ({unhealthy}/{total} need attention):"));
        }
        for summary in &self.mux_summaries {
            let marker = if summary.is_healthy() {
                "  • "
            } else {
                "  ! "
            };
            lines.push(format!("{marker}{}", summary.summary_line()));
        }
        lines
    }

    pub fn polarize_status_lines(&self) -> Vec<String> {
        if self.polarize_intents.is_empty() {
            return Vec::new();
        }
        let doctrine = self
            .polarize_intents
            .iter()
            .filter(|intent| intent.band == PolarizeBand::Doctrine)
            .count();
        let mut lines = Vec::with_capacity(self.polarize_intents.len() + 1);
        if doctrine == 0 {
            lines.push(format!(
                "Polarize intents ({}):",
                self.polarize_intents.len()
            ));
        } else {
            lines.push(format!("Polarize intents ({} doctrine):", doctrine));
        }
        lines.extend(self.polarize_intents.iter().map(|intent| {
            format!(
                "  {} {}",
                polarize_marker(intent.band),
                intent.summary_line()
            )
        }));
        lines
    }

    pub fn toggle_filter(&mut self) {
        self.queue_scope = self.queue_scope.next();
        self.refresh_rendered_runs();
        self.append_status(format!(
            "queue scope: {} ({} runs visible)",
            self.queue_scope.label(),
            self.runs.len()
        ));
    }

    pub fn set_search_query<S: Into<String>>(&mut self, query: S) {
        self.search_query = query.into();
        self.refresh_rendered_runs();
    }

    pub fn clear_search(&mut self) {
        if !self.search_query.is_empty() {
            self.search_query.clear();
            self.refresh_rendered_runs();
            self.append_status("search cleared");
        }
    }

    pub fn archive_selected_run(&mut self) -> anyhow::Result<()> {
        let Some(run_id) = self.selected_run().map(|run| run.snapshot.run_id.clone()) else {
            self.append_status("No run selected to archive.");
            return Ok(());
        };
        let archive_dir = self.config.state_root.join("runs/.archived");
        fs::create_dir_all(&archive_dir)?;
        let marker_path = archive_dir.join(format!("{}.json", safe_marker_name(&run_id)));
        let marker = serde_json::json!({
            "run_id": run_id,
            "archived_by": "vc-tui",
            "archived_at": chrono::Utc::now().to_rfc3339(),
        });
        fs::write(&marker_path, serde_json::to_vec_pretty(&marker)?)?;
        self.refresh();
        self.append_status(format!(
            "archived run from operator view: {}",
            marker
                .get("run_id")
                .and_then(|value| value.as_str())
                .unwrap_or("unknown")
        ));
        Ok(())
    }

    pub fn selected_run(&self) -> Option<&RenderedRun> {
        self.runs.get(self.selected)
    }

    pub fn active_tab(&self) -> AppTab {
        AppTab::from_index(self.active_tab)
    }

    pub fn next_tab(&mut self) {
        self.active_tab = (self.active_tab + 1) % AppTab::TITLES.len();
        self.focus = LaunchFocus::Browse;
    }

    pub fn previous_tab(&mut self) {
        self.active_tab = if self.active_tab == 0 {
            AppTab::TITLES.len() - 1
        } else {
            self.active_tab - 1
        };
        self.focus = LaunchFocus::Browse;
    }

    pub fn set_active_tab(&mut self, tab: AppTab) {
        self.active_tab = tab.index();
        self.focus = LaunchFocus::Browse;
    }

    pub fn set_launch_kind(&mut self, kind: LaunchKind) {
        self.launch_kind = kind;
        self.launch_prompt = default_prompt(kind);
        self.active_tab = AppTab::Dispatch.index();
        self.dispatch_selected = DispatchFocus::Kind as usize;
        self.focus = LaunchFocus::Browse;
    }

    pub fn cycle_agent(&mut self) {
        self.shift_agent(1);
    }

    pub fn cycle_runtime(&mut self) {
        self.shift_runtime(1);
    }

    pub fn selected_agent(&self) -> &'static str {
        agents()[self.launch_agent]
    }

    pub fn shift_agent(&mut self, delta: isize) {
        let len = agents().len() as isize;
        let mut index = self.launch_agent as isize + delta;
        while index < 0 {
            index += len;
        }
        self.launch_agent = (index % len) as usize;
    }

    pub fn shift_runtime(&mut self, delta: isize) {
        let runtimes = [
            LaunchRuntime::Headless,
            LaunchRuntime::Terminal,
            LaunchRuntime::Visible,
        ];
        let current = runtimes
            .iter()
            .position(|runtime| *runtime == self.launch_runtime)
            .unwrap_or(1) as isize;
        let len = runtimes.len() as isize;
        let mut index = current + delta;
        while index < 0 {
            index += len;
        }
        self.launch_runtime = runtimes[(index % len) as usize];
    }

    pub fn shift_launch_kind(&mut self, delta: isize) {
        let kinds = [
            LaunchKind::Workflow,
            LaunchKind::Research,
            LaunchKind::Review,
            LaunchKind::Marbles,
        ];
        let current = kinds
            .iter()
            .position(|kind| *kind == self.launch_kind)
            .unwrap_or(0) as isize;
        let len = kinds.len() as isize;
        let mut index = current + delta;
        while index < 0 {
            index += len;
        }
        self.launch_kind = kinds[(index % len) as usize];
        self.launch_prompt = default_prompt(self.launch_kind);
    }

    pub fn dispatch_focus(&self) -> DispatchFocus {
        DispatchFocus::from_index(self.dispatch_selected)
    }

    pub fn move_dispatch_selection(&mut self, delta: isize) {
        let len = DispatchFocus::COUNT as isize;
        let mut index = self.dispatch_selected as isize + delta;
        while index < 0 {
            index += len;
        }
        self.dispatch_selected = (index % len) as usize;
    }

    pub fn adjust_dispatch_selection(&mut self, delta: isize) {
        match self.dispatch_focus() {
            DispatchFocus::Kind => self.shift_launch_kind(delta),
            DispatchFocus::Agent => self.shift_agent(delta),
            DispatchFocus::Runtime => self.shift_runtime(delta),
            DispatchFocus::Prompt => {
                self.focus = LaunchFocus::EditPrompt;
            }
        }
    }

    pub fn launch_request(&self) -> LaunchRequest {
        LaunchRequest {
            kind: self.launch_kind,
            agent: self.selected_agent().to_string(),
            prompt: self.launch_prompt.clone(),
            runtime: self.launch_runtime,
            root: Some(self.config.launch_root.clone()),
            terminal_binary: Some(self.config.terminal_binary.clone()),
            env: self.launch_env(),
            count: Some(3),
            depth: Some(3),
            session_name: match self.launch_runtime {
                LaunchRuntime::Terminal | LaunchRuntime::Visible => {
                    Some(default_session_name(self.launch_kind))
                }
                LaunchRuntime::Headless => None,
            },
        }
    }

    pub fn launch_command(&self) -> LaunchCommand {
        build_launch_command(&self.config.command_deck, &self.launch_request())
    }

    pub fn append_status<S: Into<String>>(&mut self, status: S) {
        self.status_line = status.into();
    }

    pub fn show_error<S: Into<String>>(&mut self, title: S, lines: Vec<String>) {
        self.error_title = title.into();
        self.error_lines = if lines.is_empty() {
            vec!["No error detail was captured.".to_string()]
        } else {
            lines
        };
        self.status_line = self.error_title.clone();
        self.focus = LaunchFocus::Error;
    }

    pub fn error_lines(&self) -> Vec<String> {
        let mut lines = vec![self.error_title.clone(), String::new()];
        lines.extend(self.error_lines.clone());
        lines
    }

    pub fn finish_prompt_edit(&mut self) {
        self.focus = LaunchFocus::Browse;
        self.append_status(format!(
            "prompt updated: {} chars across {} line(s)",
            self.launch_prompt.chars().count(),
            self.launch_prompt.lines().count().max(1)
        ));
    }

    pub fn push_launch_history<S: Into<String>>(&mut self, entry: S) {
        self.launch_history.push(entry.into());
        if self.launch_history.len() > 6 {
            self.launch_history.drain(0..self.launch_history.len() - 6);
        }
    }

    pub fn move_selection(&mut self, delta: isize) {
        if self.runs.is_empty() {
            self.selected = 0;
            return;
        }
        let len = self.runs.len() as isize;
        let mut index = self.selected as isize + delta;
        if index < 0 {
            index = len - 1;
        }
        if index >= len {
            index = 0;
        }
        self.selected = index as usize;
    }

    pub fn sync_selection(&mut self) {
        if self.selected >= self.runs.len() && !self.runs.is_empty() {
            self.selected = self.runs.len() - 1;
        }
        let deep_len = self.deep_actions().len();
        if deep_len == 0 {
            self.deep_selected = 0;
        } else if self.deep_selected >= deep_len {
            self.deep_selected = deep_len - 1;
        }
    }

    pub fn status_summary(&self) -> String {
        if self.runs.is_empty() {
            return format!("no {} runs loaded", self.queue_scope.label());
        }
        let mut counts = BTreeMap::new();
        for run in &self.runs {
            *counts.entry(run.kind.label()).or_insert(0usize) += 1;
        }
        let mut parts = vec![format!("runs: {}", self.runs.len())];
        for label in [
            "active",
            "stalled",
            "failed",
            "paused",
            "recent",
            "completed",
            "unknown",
        ] {
            if let Some(count) = counts.get(label)
                && *count > 0
            {
                parts.push(format!("{label} {count}"));
            }
        }
        parts.join(" | ")
    }

    pub fn detail_lines(&self) -> Vec<String> {
        let Some(run) = self.selected_run() else {
            return vec![
                "No runs found in the control-plane state directory yet.".to_string(),
                String::new(),
                "Start here:".to_string(),
                "1 -> Workflow for the normal path".to_string(),
                "2 -> Research swarm if the surface is still unclear".to_string(),
                "3 -> Review if something already exists and needs truth".to_string(),
                "4 -> Marbles when the system works but still drifts".to_string(),
                String::new(),
                "Use a / v / e / Enter in the launch panel below.".to_string(),
                "Press ? for the in-app operator guide.".to_string(),
                String::new(),
                format!("State root: {}", path_display(&self.config.state_root)),
                format!("Launch root: {}", path_display(&self.config.launch_root)),
            ];
        };

        let snapshot = &run.snapshot;
        let mut lines = vec![
            format!("run: {}", run.operator_title()),
            format!("id: {}", snapshot.run_id),
            format!(
                "status: {} ({})",
                run.kind.label(),
                snapshot.display_state()
            ),
            format!("age: {}", run.age_label),
            format!(
                "operator_session: {}",
                display_optional(snapshot.operator_session.as_deref())
            ),
        ];
        if let Some(session_id) = snapshot.session_id.as_deref() {
            lines.push(format!("session_id: {session_id}"));
        }

        if let Some(root) = snapshot.root.as_deref() {
            lines.extend(wrap_operator_line(&format!("workspace: {root}"), 88));
        }
        if let Some(report) = snapshot.latest_report.as_deref() {
            lines.extend(wrap_operator_line(&format!("report: {report}"), 88));
        }
        if let Some(transcript) = snapshot.latest_transcript.as_deref() {
            lines.extend(wrap_operator_line(&format!("transcript: {transcript}"), 88));
        }
        if let Some(error) = snapshot.last_error.as_deref() {
            lines.push(format!("last_error: {error}"));
        }
        if let Some(session) = snapshot.operator_session.as_deref() {
            lines.push(String::new());
            lines.push(format!(
                "Attach hint: vibecrafted dashboard attach {session}"
            ));
            lines.push(format!("vc-frame hint: vc-frame attach {session}"));
        }
        if let Some(agent) = snapshot.agent.as_deref() {
            lines.push(format!("Resume hint: vibecrafted resume {agent}"));
        }
        lines.push(String::new());
        lines.push(format!(
            "State root: {}",
            path_display(&self.config.state_root)
        ));
        lines
    }

    pub fn event_lines(&self) -> Vec<String> {
        let Some(run) = self.selected_run() else {
            return vec!["Select a run to inspect its timeline.".to_string()];
        };
        if run.recent_events.is_empty() {
            return vec!["No recent events for this run.".to_string()];
        }
        run.recent_events
            .iter()
            .flat_map(|event| {
                let message = event.message.as_deref().unwrap_or(event.kind.as_str());
                wrap_operator_line(&format!("{}  {}", event.ts, message), 88)
            })
            .collect()
    }

    pub fn prompt_lines(&self) -> Vec<String> {
        let command_preview = self.launch_command().command_line();
        let mut lines = vec![
            dispatch_line(
                self.dispatch_focus() == DispatchFocus::Kind,
                format!(
                    "mission: {}  {}",
                    self.launch_kind.human_title(),
                    self.launch_kind.human_description()
                ),
            ),
            dispatch_line(
                self.dispatch_focus() == DispatchFocus::Agent,
                format!("agent: {}", self.selected_agent()),
            ),
            dispatch_line(
                self.dispatch_focus() == DispatchFocus::Runtime,
                format!("runtime: {}", self.launch_runtime.label()),
            ),
            dispatch_line(
                self.dispatch_focus() == DispatchFocus::Prompt,
                format!("prompt: {}", one_line_prompt(&self.launch_prompt)),
            ),
            String::new(),
            "Arrows: ↑/↓ choose field  ←/→ change field  Enter launch".to_string(),
            "Shortcuts: 1-4 mission  a agent  v runtime  e edit prompt  / search".to_string(),
            String::new(),
            format!("root: {}", path_display(&self.config.launch_root)),
            format!("command: {}", command_preview),
        ];
        if let Some(last) = self.launch_history.last() {
            lines.push(String::new());
            lines.push(format!("last launch: {last}"));
        }
        lines
    }

    pub fn help_lines(&self) -> Vec<String> {
        vec![
            "Operator guide".to_string(),
            String::new(),
            "This console is the human front door into Vibecrafted control-plane state.".to_string(),
            "Browse runs on the left, inspect truth on the right, and launch new work below.".to_string(),
            String::new(),
            "Quick start".to_string(),
            "1 Workflow  -> normal path for most tasks".to_string(),
            "2 Research  -> send a research swarm first".to_string(),
            "3 Review    -> audit an existing surface".to_string(),
            "4 Marbles   -> convergence loop for fragile systems".to_string(),
            String::new(),
            "Tabs".to_string(),
            "Tab / Shift+Tab switch between Monitor, Dispatch, and Controls.".to_string(),
            "Monitor keeps the live board. Dispatch shapes the next run. Controls opens attach/report actions.".to_string(),
            String::new(),
            "Keys".to_string(),
            "↑/↓ or j/k  navigate inside the active tab".to_string(),
            "a           cycle launch agent".to_string(),
            "v           cycle runtime (terminal / visible / headless)".to_string(),
            "e           edit launch prompt".to_string(),
            "Ctrl+S/Esc  save prompt edits; Enter inserts a prompt newline".to_string(),
            "Enter       launch selected action".to_string(),
            "d           selected-run deep controls".to_string(),
            "y           copy resume/report/run identity to clipboard".to_string(),
            "f           cycle queue scope: live, history, all".to_string(),
            "/           search runs by id, agent, skill, status, path".to_string(),
            "m           AICX memory overlay (continuity)".to_string(),
            "w           open aicx wizard search".to_string(),
            "x           archive selected run from the operator view".to_string(),
            "r           refresh control-plane state".to_string(),
            "?           close this guide".to_string(),
            "q / Esc     quit".to_string(),
            String::new(),
            "Operator rule".to_string(),
            "Use this to decide and launch. Let worker agents execute; do not overload the shell as your only dashboard.".to_string(),
        ]
    }

    pub fn active_run_count(&self) -> usize {
        self.runs
            .iter()
            .filter(|run| matches!(run.kind, RunKind::Active))
            .count()
    }

    pub fn stalled_run_count(&self) -> usize {
        self.runs
            .iter()
            .filter(|run| matches!(run.kind, RunKind::Stalled))
            .count()
    }

    pub fn tab_labels(&self) -> [String; 4] {
        let monitor = if self.search_query.is_empty() {
            format!("Monitor {} {}", self.queue_scope.label(), self.runs.len())
        } else {
            format!("Monitor {} {}?", self.queue_scope.label(), self.runs.len())
        };
        let dispatch = format!(
            "Dispatch {}/{}",
            self.launch_kind.label(),
            self.selected_agent()
        );
        let controls = format!("Controls {}", self.deep_actions().len());
        let mission = format!(
            "Mission {}|{}",
            self.mission_control.active_dispatches.len(),
            self.mission_control.action_queue.len()
        );
        [monitor, dispatch, controls, mission]
    }

    pub fn deep_actions(&self) -> Vec<DeepAction> {
        let mut actions = Vec::new();
        if let Some(run) = self.selected_run() {
            let snapshot = &run.snapshot;
            if let Some(session) = snapshot
                .operator_session
                .as_ref()
                .filter(|value| !value.is_empty())
            {
                actions.push(DeepAction::AttachSession(session.clone()));
            }
            if let (Some(agent), Some(session)) = (
                snapshot.agent.as_ref().filter(|value| !value.is_empty()),
                snapshot
                    .session_id
                    .as_ref()
                    .filter(|value| !value.is_empty()),
            ) {
                actions.push(DeepAction::ResumeSession {
                    agent: agent.clone(),
                    session: session.clone(),
                });
            }
            if let Some(report) = snapshot
                .latest_report
                .as_ref()
                .filter(|value| !value.is_empty())
            {
                actions.push(DeepAction::OpenReport(PathBuf::from(report)));
            }
            if let Some(transcript) = snapshot
                .latest_transcript
                .as_ref()
                .filter(|value| !value.is_empty())
            {
                actions.push(DeepAction::OpenTranscript(PathBuf::from(transcript)));
            }
            if let Some(root) = snapshot.root.as_ref().filter(|value| !value.is_empty()) {
                actions.push(DeepAction::OpenRoot(PathBuf::from(root)));
            }
        }
        // MCP daemon health-check actions are always available (one per
        // known rmcp-mux service), independent of whether a run is
        // selected. Operators commonly need to check the supervisor when
        // *no* run is healthy, so gating these on selection would defeat
        // the surface.
        for summary in &self.mux_summaries {
            if !summary.is_healthy() {
                actions.push(DeepAction::MuxHealth {
                    service: summary.display_name.clone(),
                });
            }
        }
        for intent in &self.polarize_intents {
            actions.push(DeepAction::PolarizeIntent {
                band: intent.band,
                score: intent.score,
                run_id: intent.run_id.clone(),
                prism_path: intent.prism_path.clone(),
            });
        }
        let selected_skill = self
            .selected_run()
            .and_then(|run| run.snapshot.skill.clone());
        for entry in skills_catalog::CATALOG.iter().filter(|entry| {
            matches!(
                entry.slug,
                "vc-workflow" | "vc-review" | "vc-marbles" | "vc-polarize"
            ) || selected_skill
                .as_deref()
                .is_some_and(|skill| entry.slug == skill || entry.slug.trim_start_matches("vc-") == skill)
        }) {
            let agent = resolve_skill_agent(entry.default_agent, self.selected_agent());
            let payload = match entry.accepts {
                SkillPayloadKind::None => SkillPayload::None,
                SkillPayloadKind::Optional | SkillPayloadKind::PromptOrFile => {
                    if self.launch_prompt.trim().is_empty() {
                        SkillPayload::None
                    } else {
                        SkillPayload::Prompt(self.launch_prompt.clone())
                    }
                }
            };
            if actions.iter().any(|action| {
                matches!(
                    action,
                    DeepAction::SkillLaunch { skill, .. } if skill == entry.slug
                )
            }) {
                continue;
            }
            actions.push(DeepAction::SkillLaunch {
                skill: entry.slug.to_string(),
                agent,
                payload,
            });
        }
        actions
    }

    pub fn selected_deep_action(&self) -> Option<DeepAction> {
        self.deep_actions().get(self.deep_selected).cloned()
    }

    pub fn deep_action_index_for_primary_mission_queue_item(&self) -> Option<usize> {
        let item = self.mission_control.action_queue.first()?;
        self.deep_actions()
            .iter()
            .position(|action| mission_queue_item_matches_deep_action(item, action))
    }

    pub fn preselect_controls_from_mission_queue(&mut self) -> bool {
        if let Some(index) = self.deep_action_index_for_primary_mission_queue_item() {
            self.deep_selected = index;
            return true;
        }
        false
    }

    pub fn move_deep_selection(&mut self, delta: isize) {
        let len = self.deep_actions().len();
        if len == 0 {
            self.deep_selected = 0;
            return;
        }
        let len = len as isize;
        let mut index = self.deep_selected as isize + delta;
        if index < 0 {
            index = len - 1;
        }
        if index >= len {
            index = 0;
        }
        self.deep_selected = index as usize;
    }

    pub fn deep_control_lines(&self) -> Vec<String> {
        let actions = self.deep_actions();
        if actions.is_empty() {
            return vec![
                "Deep controls".to_string(),
                "No attach/resume/report actions are available for the selected run.".to_string(),
                "Pick another run or launch a fresh one below.".to_string(),
            ];
        }
        let mut lines = vec![
            "Primary actions".to_string(),
            "Enter runs the focused row. f cycles live/history. IDs stay copyable.".to_string(),
            String::new(),
        ];
        if let Some(run) = self.selected_run() {
            lines.push(format!("focused: {}", run.operator_title()));
            lines.push(format!("id: {}", run.snapshot.run_id));
            lines.push(String::new());
        }
        lines.extend(actions.iter().enumerate().map(|(idx, action)| {
            let prefix = if self.active_tab() == AppTab::Controls && idx == self.deep_selected {
                "▶"
            } else {
                " "
            };
            format!("{prefix} {}", action.control_label())
        }));
        lines
    }

    pub fn prompt_edit_lines(&self) -> Vec<String> {
        let mut lines = vec![
            "Prompt editor".to_string(),
            format!(
                "{} chars across {} line(s)",
                self.launch_prompt.chars().count(),
                self.launch_prompt.lines().count().max(1)
            ),
            String::new(),
        ];
        if self.launch_prompt.is_empty() {
            lines.push("Type the worker prompt here.".to_string());
        } else {
            lines.extend(self.launch_prompt.lines().map(ToOwned::to_owned));
        }
        lines.push(String::new());
        lines.push("Enter inserts newline. Ctrl+S or Esc saves.".to_string());
        lines
    }

    pub fn open_artifact(&mut self, action: &DeepAction) -> anyhow::Result<()> {
        let (title, path) = match action {
            DeepAction::OpenReport(path) => ("Report", path),
            DeepAction::OpenTranscript(path) => ("Transcript", path),
            DeepAction::OpenRoot(path) => ("Run root", path),
            _ => return Ok(()),
        };
        self.artifact_title = format!("{title}: {}", path_display(path));
        let run_root = self
            .selected_run()
            .and_then(|run| run.snapshot.root.as_deref());
        self.artifact_lines = artifact_lines(path, run_root)?;
        self.focus = LaunchFocus::Artifact;
        self.append_status(format!("opened {} in operator viewer", path_display(path)));
        Ok(())
    }

    pub fn open_polarize_intent(&mut self, action: &DeepAction) -> anyhow::Result<()> {
        let DeepAction::PolarizeIntent {
            band,
            score,
            run_id,
            prism_path,
        } = action
        else {
            return Ok(());
        };
        self.artifact_title = format!(
            "Polarize prism: {} score {} run {}",
            band.label(),
            score,
            run_id
        );
        self.artifact_lines = crate::polarize::prism_preview_lines(prism_path)?;
        self.focus = LaunchFocus::Artifact;
        self.append_status(format!(
            "opened polarize prism {}",
            path_display(prism_path)
        ));
        Ok(())
    }

    pub fn artifact_lines(&self) -> Vec<String> {
        let mut lines = vec![self.artifact_title.clone(), String::new()];
        lines.extend(self.artifact_lines.clone());
        lines
    }

    pub fn clipboard_payload(&self) -> Option<String> {
        let run = self.selected_run()?;
        let snapshot = &run.snapshot;
        if let (Some(agent), Some(session)) =
            (snapshot.agent.as_deref(), snapshot.session_id.as_deref())
        {
            return Some(format!("vibecrafted resume {agent} --session {session}"));
        }
        if let Some(report) = snapshot.latest_report.as_deref() {
            return Some(report.to_string());
        }
        Some(snapshot.run_id.clone())
    }

    pub fn copy_selected_run_to_clipboard(&mut self) -> anyhow::Result<()> {
        let Some(payload) = self.clipboard_payload() else {
            self.append_status("No selected run to copy.");
            return Ok(());
        };
        let mut clipboard = arboard::Clipboard::new()?;
        clipboard.set_text(payload.clone())?;
        self.append_status(format!("copied to clipboard: {payload}"));
        Ok(())
    }

    pub(crate) fn launch_env(&self) -> BTreeMap<String, OsString> {
        let mut env = BTreeMap::new();
        env.insert(
            "VIBECRAFTED_ROOT".to_string(),
            self.config.launch_root.as_os_str().to_os_string(),
        );
        env.insert(
            "VIBECRAFT_OPERATOR_STATE_ROOT".to_string(),
            self.config.state_root.as_os_str().to_os_string(),
        );
        if let Some(config_dir) =
            std::env::var_os("VC_FRAME_CONFIG_DIR").filter(|value| !value.is_empty())
        {
            env.insert("VC_FRAME_CONFIG_DIR".to_string(), config_dir);
        }
        env
    }
}

fn trace_expensive_refresh(kind: &str) {
    let Some(path) = std::env::var_os("VOC_REFRESH_TRACE_PATH").filter(|value| !value.is_empty())
    else {
        return;
    };
    let unix_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let line = serde_json::json!({
        "kind": kind,
        "unix_ms": unix_ms,
    });
    if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{line}");
    }
}

fn dispatch_line(selected: bool, content: String) -> String {
    if selected {
        format!("▶ {content}")
    } else {
        format!("  {content}")
    }
}

/// Number of Mission Control panels rendered in the dashboard. Kept as a
/// const so navigation wrap-around stays in sync with PLAN_23 §4 (seven
/// panels: active dispatches, wave atlas, per-agent stats, per-skill
/// stats, fleet health, failure board, operator action queue).
pub const MISSION_PANEL_COUNT: usize = 7;

pub fn mission_panel_count() -> usize {
    MISSION_PANEL_COUNT
}

fn mission_queue_item_matches_deep_action(item: &ActionQueueItem, action: &DeepAction) -> bool {
    match (item.kind, action) {
        (ActionQueueKind::StalledRun, DeepAction::OpenRoot(path)) => {
            path_match(item.source_path.as_deref(), path)
        }
        (ActionQueueKind::Failure | ActionQueueKind::ReportReady, DeepAction::OpenReport(path)) => {
            path_match(item.source_path.as_deref(), path)
                || run_id_match(action_queue_run_id(item), path)
        }
        (
            ActionQueueKind::Polarize,
            DeepAction::PolarizeIntent {
                run_id, prism_path, ..
            },
        ) => {
            path_match(item.source_path.as_deref(), prism_path)
                || action_queue_run_id(item) == Some(run_id.as_str())
        }
        _ => false,
    }
}

fn action_queue_run_id(item: &ActionQueueItem) -> Option<&str> {
    let mut parts = item.summary.split_whitespace();
    parts.next()?;
    parts.next().map(|segment| segment.trim())
}

fn run_id_match(run_id: Option<&str>, path: &Path) -> bool {
    run_id
        .map(|value| path.to_string_lossy().contains(value))
        .unwrap_or(false)
}

fn path_match(expected: Option<&Path>, actual: &Path) -> bool {
    expected == Some(actual)
}

pub fn default_prompt(kind: LaunchKind) -> String {
    match kind {
        LaunchKind::Workflow => "Plan and implement the task I am looking at now.".to_string(),
        LaunchKind::Research => {
            "Research the task I am looking at now and report the ground truth.".to_string()
        }
        LaunchKind::Review => {
            "Review the selected surface and call out concrete risks.".to_string()
        }
        LaunchKind::Marbles => {
            "Run a convergence loop on the selected surface until the lies are exposed.".to_string()
        }
    }
}

pub fn agents() -> [&'static str; 3] {
    ["claude", "codex", "gemini"]
}

fn apply_run_filters(
    runs: &mut Vec<RenderedRun>,
    queue_scope: QueueScope,
    search_query: &str,
    workspace: &Path,
) {
    let now = chrono::Utc::now();
    let workspace_live = runs.iter().any(|run| {
        is_actionable_kind(run.kind, &run.snapshot, now) && workspace_matches(&run.snapshot, workspace)
    });
    match queue_scope {
        QueueScope::Live => runs.retain(|run| {
            is_actionable_kind(run.kind, &run.snapshot, now)
                && (!workspace_live || workspace_matches(&run.snapshot, workspace))
        }),
        QueueScope::History => runs.retain(|run| {
            !is_actionable_kind(run.kind, &run.snapshot, now)
                || (workspace_live && !workspace_matches(&run.snapshot, workspace))
        }),
        QueueScope::All => {}
    }
    let query = search_query.trim().to_ascii_lowercase();
    if !query.is_empty() {
        runs.retain(|run| run_matches_query(run, &query));
    }
}

fn run_matches_query(run: &RenderedRun, query: &str) -> bool {
    let snapshot = &run.snapshot;
    [
        Some(snapshot.run_id.as_str()),
        snapshot.session_id.as_deref(),
        snapshot.agent.as_deref(),
        snapshot.skill.as_deref(),
        snapshot.mode.as_deref(),
        snapshot.state.as_deref(),
        snapshot.status.as_deref(),
        snapshot.root.as_deref(),
        snapshot.latest_report.as_deref(),
        snapshot.latest_transcript.as_deref(),
    ]
    .into_iter()
    .flatten()
    .any(|value| value.to_ascii_lowercase().contains(query))
}

fn one_line_prompt(prompt: &str) -> String {
    let collapsed = prompt.split_whitespace().collect::<Vec<_>>().join(" ");
    if collapsed.chars().count() > 96 {
        let mut short = collapsed.chars().take(93).collect::<String>();
        short.push_str("...");
        short
    } else {
        collapsed
    }
}

fn display_optional(value: Option<&str>) -> &str {
    match value.map(str::trim) {
        Some(value) if !value.is_empty() && value != "None" && value != "null" => value,
        _ => "-",
    }
}

fn truncate_id(value: &str, width: usize) -> String {
    if value.chars().count() <= width {
        return value.to_string();
    }
    let mut short = value.chars().take(width.saturating_sub(1)).collect::<String>();
    short.push('…');
    short
}

fn wrap_operator_line(value: &str, width: usize) -> Vec<String> {
    if value.chars().count() <= width {
        return vec![value.to_string()];
    }
    let mut lines = Vec::new();
    let mut current = String::new();
    for ch in value.chars() {
        current.push(ch);
        if current.chars().count() >= width {
            lines.push(current.clone());
            current.clear();
        }
    }
    if !current.is_empty() {
        lines.push(current);
    }
    lines
}

fn safe_marker_name(run_id: &str) -> String {
    run_id
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.') {
                ch
            } else {
                '_'
            }
        })
        .collect()
}

fn polarize_marker(band: PolarizeBand) -> &'static str {
    match band {
        PolarizeBand::Abort => "!",
        PolarizeBand::Memo => "-",
        PolarizeBand::Pass => ">",
        PolarizeBand::Doctrine => "*",
    }
}

fn resolve_skill_agent(default_agent: SkillAgent, selected_agent: &str) -> SkillAgent {
    match default_agent {
        SkillAgent::Any => SkillAgent::from_cli_token(selected_agent),
        concrete => concrete,
    }
}

fn artifact_lines(path: &Path, run_root: Option<&str>) -> anyhow::Result<Vec<String>> {
    let path = safe_artifact_path(path, run_root)?;
    if path.is_dir() {
        let mut rows = Vec::new();
        // `safe_artifact_path` canonicalizes this path and constrains it to the selected run root.
        let entries = fs::read_dir(&path)?; // nosemgrep: rust.actix.path-traversal.tainted-path.tainted-path
        for entry in entries {
            let entry = entry?;
            let file_type = entry.file_type()?;
            let suffix = if file_type.is_dir() { "/" } else { "" };
            rows.push(format!("{}{}", entry.file_name().to_string_lossy(), suffix));
        }
        rows.sort();
        if rows.is_empty() {
            rows.push("(empty directory)".to_string());
        }
        return Ok(rows);
    }
    // `safe_artifact_path` canonicalizes this path and constrains it to the selected run root.
    let text = fs::read_to_string(&path)?; // nosemgrep: rust.actix.path-traversal.tainted-path.tainted-path
    let rendered = if path
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.contains("transcript"))
    {
        crate::run_detail::humanize_transcript(&text)
    } else {
        text
    };
    if rendered.trim().is_empty() {
        return Ok(vec!["No transcript or events in this artifact yet.".to_string()]);
    }
    let mut lines = rendered
        .lines()
        .take(400)
        .flat_map(|line| wrap_operator_line(line, 88))
        .collect::<Vec<_>>();
    if rendered.lines().count() > 400 {
        lines.push("[truncated after 400 lines]".to_string());
    }
    Ok(lines)
}

fn default_session_name(kind: LaunchKind) -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let suffix = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| (d.as_millis() % 100_000) as u32)
        .unwrap_or(0);
    format!("vc-op-{}-{:05}", kind.label(), suffix)
}

fn safe_artifact_path(path: &Path, run_root: Option<&str>) -> anyhow::Result<PathBuf> {
    let meta = fs::symlink_metadata(path)?;
    if meta.file_type().is_symlink() {
        anyhow::bail!(
            "refusing to open symlinked artifact: {}",
            path_display(path)
        );
    }
    let canonical = fs::canonicalize(path)?;
    let Some(run_root) = run_root.filter(|root| !root.trim().is_empty()) else {
        anyhow::bail!("selected run has no root; refusing artifact path");
    };
    let root = fs::canonicalize(run_root)?;
    if !canonical.starts_with(&root) {
        anyhow::bail!(
            "refusing artifact outside selected run root: {}",
            path_display(&canonical)
        );
    }
    Ok(canonical)
}
