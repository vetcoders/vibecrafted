//! Snapshot coverage for Monitor, Controls, and Dispatch hierarchy after the
//! VVOC cut.
//!
//! Freezes the operator-visible board: focused row, scope, primary action,
//! and human labels. IDs stay secondary copyable metadata. The Dispatch color
//! map freezes the border rule: focused pane carries the Yellow focus line,
//! every other border stays neutral.

use std::time::Duration;

use ratatui::Terminal;
use ratatui::backend::TestBackend;
use ratatui::buffer::Buffer;
use ratatui::style::Color;
use voc::app::{App, AppTab, DispatchFocus, LaunchFocus, QueueScope};
use voc::config::AppConfig;
use voc::launch::{Environment, LaunchKind, PermissionPolicy, Presentation, SandboxChoice};
use voc::state::{ControlPlaneState, RenderedRun, RunKind, RunSnapshot};
use voc::usage::{UsageCost, UsageDashboard, UsageRun};
mod support;
use support::{agent_index, fixture_catalog};
use voc::catalog::CatalogState;

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
            repo: "/work/vetcoders/vibecrafted".into(),
            presentation: Presentation::Terminal,
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
        // Pinned by name: the catalog owns the order, so an index would
        // silently redraw this board whenever the launcher adds an agent.
        launch_agent: agent_index("claude"),
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
        mission_control: voc::MissionControlState::default(),
        mission_focus: 0,
        mission_artifact_root: "/fixture/artifacts".into(),
        observe: Default::default(),
        memory: Default::default(),
        interaction: Default::default(),
        repo_edit: Default::default(),
        refresh: Default::default(),
        home_rows_memo: Default::default(),
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

/// One char per cell encoding the foreground color. Freezes color
/// *placement*; actual light/dark RGB values resolve terminal-side.
fn buffer_color_map(buffer: &Buffer) -> String {
    let width = buffer.area.width as usize;
    buffer
        .content()
        .iter()
        .map(|cell| color_char(cell.style().fg.unwrap_or(Color::Reset)))
        .collect::<Vec<_>>()
        .chunks(width)
        .map(|row| row.iter().collect::<String>())
        .collect::<Vec<_>>()
        .join("\n")
}

fn color_char(color: Color) -> char {
    match color {
        Color::Reset => '.',
        Color::Black => 'k',
        Color::White => 'W',
        Color::Gray => 'g',
        Color::DarkGray => 'd',
        Color::Red => 'R',
        Color::Green => 'G',
        Color::Yellow => 'Y',
        Color::Blue => 'B',
        Color::Magenta => 'M',
        Color::Cyan => 'C',
        _ => '?',
    }
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

#[test]
fn usage_tab_truthful_units_and_unknowns_snapshot() {
    let mut app = board_app();
    app.state.usage = UsageDashboard {
        runs: vec![
            UsageRun {
                run_id: "impl-usage".to_string(),
                provider: "openai".to_string(),
                agent: "codex".to_string(),
                model: "gpt-6".to_string(),
                status: "completed".to_string(),
                timestamp: "2026-09-21T16:10:00Z".to_string(),
                tokens_total: Some(12_345),
                cost: UsageCost::Known {
                    amount: 0.3125,
                    unit: "USD".to_string(),
                },
                failure: None,
            },
            UsageRun {
                run_id: "review-credit".to_string(),
                provider: "google".to_string(),
                agent: "agy".to_string(),
                model: "unknown".to_string(),
                status: "failed".to_string(),
                timestamp: "2026-09-21T16:12:00Z".to_string(),
                tokens_total: None,
                cost: UsageCost::Known {
                    amount: 7.0,
                    unit: "credits".to_string(),
                },
                failure: Some("quota_exhausted".to_string()),
            },
            UsageRun {
                run_id: "legacy-unknown".to_string(),
                provider: "moonshot".to_string(),
                agent: "kimi".to_string(),
                model: "unknown".to_string(),
                status: "completed".to_string(),
                timestamp: "2026-09-21T16:09:00Z".to_string(),
                tokens_total: None,
                cost: UsageCost::Unknown,
                failure: None,
            },
        ],
        tokens_total_known: 12_345,
        runs_tokens_unknown: 2,
        usd_total: 0.3125,
        credits_total: 7.0,
        runs_cost_unknown: 1,
        failures: 1,
        observed_from: Some("2026-09-21T16:09:00Z".to_string()),
        observed_to: Some("2026-09-21T16:12:00Z".to_string()),
    };
    app.set_active_tab(AppTab::Usage);
    let text = buffer_text(&render(&app));
    insta::assert_snapshot!(text);
    assert!(text.contains("12,345 known tokens"));
    assert!(text.contains("$0.3125 USD"));
    assert!(text.contains("7.00 credits"));
    assert!(text.contains("2 token unknown"));
    assert!(text.contains("1 cost unknown"));
    assert!(text.contains("failure quota_exhausted"));
}

#[test]
fn dispatch_tab_content_snapshot() {
    let mut app = board_app();
    app.set_active_tab(AppTab::Dispatch);
    let buffer = render(&app);
    let text = buffer_text(&buffer);
    insta::assert_snapshot!(text);
    assert!(text.contains(" Mission "));
    assert!(text.contains(" Operator "));
    assert!(text.contains(" Execution "));
    assert!(text.contains("Dispatch deck"));
}

#[test]
fn dispatch_tab_focus_border_color_map_snapshot() {
    let mut app = board_app();
    app.set_active_tab(AppTab::Dispatch);
    let buffer = render(&app);
    let colors = buffer_color_map(&buffer);
    insta::assert_snapshot!(colors);
    // The focused stat card border is the only pane border painted with the
    // focus color; the sibling card borders stay neutral.
    let stat_rows: Vec<&str> = colors.lines().skip(4).take(6).collect();
    let yellow_borders = stat_rows
        .iter()
        .map(|row| row.matches('Y').count())
        .sum::<usize>();
    assert!(
        yellow_borders > 0,
        "focused Dispatch card must carry the Yellow border line"
    );
}
