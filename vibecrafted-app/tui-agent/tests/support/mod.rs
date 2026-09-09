#![allow(dead_code)] // each suite uses a different slice of this module

//! Shared test support for the integration suites.
//!
//! Every fixture app is anchored to the frozen launcher catalog, never to a
//! probe of this host: the console's agent list, model-pin support and
//! environment availability must render identically on a machine with no
//! agents installed and on a fully provisioned one.

use voc::catalog::LauncherCatalog;

pub const CAPABILITIES_FIXTURE: &str = include_str!("../fixtures/capabilities.json");

/// The launcher catalog as the fixture declares it: agents `agy, claude,
/// codex, cursor, grok, junie` (grok deliberately unavailable), Living Tree
/// and Fleet Worktrees available, Fleet VM refused with the launcher's reason.
pub fn fixture_catalog() -> LauncherCatalog {
    LauncherCatalog::parse(CAPABILITIES_FIXTURE.as_bytes())
        .expect("the bundled capabilities fixture must parse")
}

use std::path::Path;
use std::time::Duration;

use voc::app::{App, AppTab, DispatchFocus, LaunchFocus, QueueScope};
use voc::catalog::CatalogState;
use voc::config::AppConfig;
use voc::launch::{Environment, LaunchKind, PermissionPolicy, Presentation, SandboxChoice};
use voc::state::ControlPlaneState;

/// A console anchored to `repo`, with the frozen catalog already loaded and
/// every root inside the caller's temporary directory. Nothing here reads the
/// operator's real `~/.vibecrafted`, so the suite leaves no production records.
pub fn fixture_app(repo: &Path, deck: &Path, roots: &Path) -> App {
    App {
        config: AppConfig {
            state_root: roots.join("state"),
            command_deck: deck.to_path_buf(),
            repo: repo.to_path_buf(),
            presentation: Presentation::Headless,
            tick_rate: Duration::from_millis(250),
            no_verify_gate: true,
            server: "http://127.0.0.1:3024".into(),
            view: voc::observe::ConsoleView::Full,
        },
        state: ControlPlaneState::empty(roots.join("state")),
        runs: Vec::new(),
        selected: 0,
        active_tab: AppTab::Dispatch.index(),
        launch_kind: LaunchKind::Workflow,
        launch_agent: 0,
        launch_prompt: "Ship the launcher contract.".to_string(),
        launch_model: String::new(),
        launch_presentation: Presentation::Headless,
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
        mux_subscriber: None,
        polarize_intents: Vec::new(),
        mission_control: voc::mission_control::MissionControlState::default(),
        mission_focus: 0,
        mission_artifact_root: roots.join("artifacts"),
        observe: voc::observe::ObserveState::default(),
        memory: Default::default(),
        interaction: Default::default(),
    }
}

/// Index of `agent` in the frozen catalog, so tests select by name instead of
/// hardcoding a position that the launcher owns.
pub fn agent_index(agent: &str) -> usize {
    fixture_catalog()
        .agents
        .iter()
        .position(|name| name == agent)
        .unwrap_or_else(|| panic!("{agent} is not in the fixture catalog"))
}
