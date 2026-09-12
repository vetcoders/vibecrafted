//! Snapshot coverage for Monitor and Controls hierarchy after the VVOC cut.
//!
//! Freezes the operator-visible board: focused row, scope, primary action,
//! and human labels. IDs stay secondary copyable metadata.

use std::time::Duration;

use ratatui::Terminal;
use ratatui::backend::TestBackend;
use ratatui::buffer::Buffer;
use voc::app::{App, AppTab, DispatchFocus, LaunchFocus, QueueScope};
use voc::config::AppConfig;
use voc::launch::{LaunchKind, LaunchRuntime};
use voc::state::{ControlPlaneState, RenderedRun, RunKind, RunSnapshot};

const TERM_WIDTH: u16 = 120;
const TERM_HEIGHT: u16 = 40;

fn sample_run(run_id: &str, agent: &str, skill: &str, kind: RunKind) -> RenderedRun {
    RenderedRun {
        snapshot: RunSnapshot {
            run_id: run_id.to_string(),
            session_id: Some(format!("sess-{run_id}")),
            agent: Some(agent.to_string()),
            skill: Some(skill.to_string()),
            mode: Some("implement".to_string()),
            state: Some("running".to_string()),
            status: None,
            started_at: Some("2026-08-27T12:00:00Z".to_string()),
            updated_at: Some("2026-08-27T12:01:00Z".to_string()),
            last_heartbeat: Some("2026-08-27T12:01:30Z".to_string()),
            root: Some("/work/vetcoders/vibecrafted".to_string()),
            operator_session: Some("operator-1".to_string()),
            latest_report: Some("/work/vetcoders/vibecrafted/report.md".to_string()),
            latest_transcript: Some("/work/vetcoders/vibecrafted/transcript.human.log".to_string()),
            last_error: None,
            extra: Default::default(),
        },
        kind,
        age_label: "1m ago".to_string(),
        recent_events: Vec::new(),
    }
}

fn board_app() -> App {
    App {
        mux_subscriber: None,
        config: AppConfig {
            no_verify_gate: false,
            state_root: "/fixture/state".into(),
            command_deck: "/usr/bin/vibecrafted".into(),
            launch_root: "/work/vetcoders/vibecrafted".into(),
            launch_runtime: LaunchRuntime::Terminal,
            terminal_binary: "vc-frame".into(),
            tick_rate: Duration::from_millis(250),
            server: "http://127.0.0.1:3024".into(),
            view: voc::observe::ConsoleView::Full,
        },
        state: ControlPlaneState::empty("/fixture/state"),
        runs: vec![
            sample_run("impl-1", "codex", "implement", RunKind::Active),
            sample_run("rev-2", "claude", "review", RunKind::Stalled),
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
        mission_control: voc::MissionControlState::default(),
        mission_focus: 0,
        mission_artifact_root: "/fixture/artifacts".into(),
        observe: Default::default(),
        memory: Default::default(),
    }
}

fn render(app: &App) -> Buffer {
    let backend = TestBackend::new(TERM_WIDTH, TERM_HEIGHT);
    let mut terminal = Terminal::new(backend).unwrap();
    terminal.draw(|frame| voc::ui::draw(frame, app)).unwrap();
    terminal.backend().buffer().clone()
}

fn buffer_text(buffer: &Buffer) -> String {
    let width = buffer.area.width as usize;
    buffer
        .content()
        .iter()
        .map(|cell| cell.symbol())
        .collect::<Vec<_>>()
        .chunks(width)
        .map(|row| row.concat().trim_end().to_string())
        .collect::<Vec<_>>()
        .join("\n")
}

#[test]
fn monitor_tab_hierarchy_snapshot() {
    let app = board_app();
    let text = buffer_text(&render(&app));
    insta::assert_snapshot!(text);
    assert!(text.contains("workspace: vibecrafted"));
    assert!(text.contains("active 1"));
    assert!(text.contains("stalled 1"));
    assert!(text.contains("implement · codex · vibecrafted"));
    assert!(text.contains("▶"));
    assert!(!text.contains("unknown unknown"));
    assert!(!text.contains("active runs: 2"));
}

#[test]
fn controls_tab_hierarchy_snapshot() {
    let mut app = board_app();
    app.set_active_tab(AppTab::Controls);
    let text = buffer_text(&render(&app));
    insta::assert_snapshot!(text);
    assert!(text.contains("Primary actions"));
    assert!(text.contains("focused:"));
    assert!(app.deep_actions().len() < 12);
    assert!(!text.contains("Launch skill: vibecrafted justdo"));
}
