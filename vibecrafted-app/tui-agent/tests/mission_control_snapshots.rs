//! PLAN_23 Wave D-2 — Mission Control snapshot armor.
//!
//! Freezes the seven dashboard panels (Active dispatches, Wave atlas,
//! Per-agent stats, Per-skill stats, Fleet health, Failure board,
//! Operator action queue) plus the stat strip and DataQuality receipt at
//! the ratatui buffer level, and the standalone `vc-admin` text renderer
//! at the process level.
//!
//! Theme truth (PLAN_23 §5 names a "mid-light / mid-dark tui-agent
//! palette"): no such in-app palette exists in the tree. `voc` emits only
//! named ANSI colors; the light/dark (and host-accent mesh) resolution
//! happens terminal-side via the vc_frame themes in
//! `config/vc-frame/themes/vetcoders-mesh.kdl`. Both themes therefore
//! consume the exact same buffer — the snapshots below freeze that buffer
//! once for content and once for the color map, and
//! `mission_control_palette_is_terminal_theme_adaptive` guards the
//! invariant that makes the single buffer correct in every theme.
//!
//! Terminal size is pinned to 120x40 (matches the existing `ui.rs` render
//! tests) so buffer snapshots stay stable across machines.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

use ratatui::Terminal;
use ratatui::backend::TestBackend;
use ratatui::buffer::Buffer;
use ratatui::style::Color;
use tempfile::tempdir;
use voc::app::{App, AppTab, DispatchFocus, LaunchFocus, QueueScope};
use voc::config::AppConfig;
use voc::launch::{LaunchKind, LaunchRuntime};
use voc::mission_control::{
    ActionPriority, ActionQueueItem, ActionQueueKind, ActiveDispatch, AgentStatsRow, DataQuality,
    FailureEntry, FleetHealthSignal, FleetHealthStatus, MissionControlState, SkillStatsRow,
    WaveSegment, WaveState,
};
use voc::state::ControlPlaneState;

const TERM_WIDTH: u16 = 120;
const TERM_HEIGHT: u16 = 40;

/// Fully populated seven-panel state with fixed strings only — no clock,
/// no filesystem, no host paths. This is the canonical render fixture.
fn populated_mission_state() -> MissionControlState {
    MissionControlState {
        generated_at: "2026-06-10T12:00:00+00:00".to_string(),
        settlement: voc::SettlementBoardCounts {
            scope: voc::SettlementBoardCounts::SCOPE_RETAINED_SNAPSHOTS.to_string(),
            f: 0,
            x: 2,
            n: 5,
            invalid: 0,
            unclassified: 2,
            active: 2,
            stalled: 3,
            orphans: 1,
            total_settled: 7,
        },
        active_dispatches: vec![
            ActiveDispatch {
                run_id: "just-091500-11111".to_string(),
                agent: "claude".to_string(),
                skill: "implement".to_string(),
                root: Some("/work/vetcoders/vibecrafted".to_string()),
                root_label: "vibecrafted".to_string(),
                wave: Some("W3-C".to_string()),
                started_at: Some("2026-06-10T11:48:00+00:00".to_string()),
                age_label: "12m ago".to_string(),
                eta_label: "3m since heartbeat".to_string(),
            },
            ActiveDispatch {
                run_id: "marb-090000-22222".to_string(),
                agent: "codex".to_string(),
                skill: "marbles".to_string(),
                root: Some("/work/example-app".to_string()),
                root_label: "example-app".to_string(),
                wave: None,
                started_at: Some("2026-06-10T11:58:00+00:00".to_string()),
                age_label: "2m ago".to_string(),
                eta_label: "fresh".to_string(),
            },
        ],
        wave_atlas: vec![
            WaveSegment {
                wave_id: "intents-zero-W1".to_string(),
                total: 4,
                completed: 4,
                failed: 0,
                active: 0,
                latest_state: WaveState::Completed,
            },
            WaveSegment {
                wave_id: "intents-zero-W2".to_string(),
                total: 3,
                completed: 2,
                failed: 1,
                active: 0,
                latest_state: WaveState::InProgress,
            },
            WaveSegment {
                wave_id: "intents-zero-W3".to_string(),
                total: 2,
                completed: 0,
                failed: 0,
                active: 2,
                latest_state: WaveState::InProgress,
            },
            WaveSegment {
                wave_id: "legacy-recovery".to_string(),
                total: 1,
                completed: 0,
                failed: 1,
                active: 0,
                latest_state: WaveState::Failed,
            },
            WaveSegment {
                wave_id: "parked-wave".to_string(),
                total: 1,
                completed: 0,
                failed: 0,
                active: 0,
                latest_state: WaveState::Pending,
            },
        ],
        agent_stats: vec![
            AgentStatsRow {
                agent: "claude".to_string(),
                total_runs: 42,
                completed: 39,
                failed: 3,
                success_rate: 0.93,
                avg_duration_s: Some(840.0),
                model_known_rate: 0.95,
            },
            AgentStatsRow {
                agent: "codex".to_string(),
                total_runs: 28,
                completed: 25,
                failed: 3,
                success_rate: 0.89,
                avg_duration_s: Some(660.0),
                model_known_rate: 0.5,
            },
            AgentStatsRow {
                agent: "gemini".to_string(),
                total_runs: 9,
                completed: 7,
                failed: 2,
                success_rate: 0.78,
                avg_duration_s: None,
                model_known_rate: 0.0,
            },
        ],
        skill_stats: vec![
            SkillStatsRow {
                skill: "implement".to_string(),
                invocations: 18,
                completed: 17,
                failed: 1,
                avg_duration_s: Some(640.0),
            },
            SkillStatsRow {
                skill: "marbles".to_string(),
                invocations: 9,
                completed: 8,
                failed: 1,
                avg_duration_s: Some(45.0),
            },
            SkillStatsRow {
                skill: "partner".to_string(),
                invocations: 1,
                completed: 1,
                failed: 0,
                avg_duration_s: None,
            },
        ],
        fleet_health: vec![
            FleetHealthSignal {
                label: "control-plane".to_string(),
                status: FleetHealthStatus::Ok,
                detail: "/fixture/state (3 runs)".to_string(),
            },
            FleetHealthSignal {
                label: "artifact-root".to_string(),
                status: FleetHealthStatus::Ok,
                detail: "/fixture/artifacts".to_string(),
            },
            FleetHealthSignal {
                label: "meta scan".to_string(),
                status: FleetHealthStatus::Ok,
                detail: "128 meta.json scanned".to_string(),
            },
            FleetHealthSignal {
                label: "model parity".to_string(),
                status: FleetHealthStatus::Warn,
                detail: "40/128 missing model".to_string(),
            },
            FleetHealthSignal {
                label: "duration parity".to_string(),
                status: FleetHealthStatus::Unknown,
                detail: "0/0 missing duration_s".to_string(),
            },
        ],
        failures: vec![
            FailureEntry {
                run_id: "rsrc-083000-77777".to_string(),
                agent: "gemini".to_string(),
                skill: "research".to_string(),
                reason: "exit_code 2".to_string(),
                occurred_at: Some("2026-06-08T15:00:00Z".to_string()),
                age_label: "3h ago".to_string(),
                source_path: Some(PathBuf::from("/fixture/artifacts/rsrc.meta.json")),
            },
            FailureEntry {
                run_id: "lost-070000-55555".to_string(),
                agent: "codex".to_string(),
                skill: "workflow".to_string(),
                reason: "stalled heartbeat".to_string(),
                occurred_at: Some("2026-06-08T13:00:00Z".to_string()),
                age_label: "5h ago".to_string(),
                source_path: None,
            },
        ],
        action_queue: vec![
            ActionQueueItem {
                kind: ActionQueueKind::Failure,
                summary: "investigate rsrc-083000-77777 (gemini / research)".to_string(),
                source_path: None,
                priority: ActionPriority::Critical,
            },
            ActionQueueItem {
                kind: ActionQueueKind::StalledRun,
                summary: "resume lost-070000-55555 (codex)".to_string(),
                source_path: None,
                priority: ActionPriority::High,
            },
            ActionQueueItem {
                kind: ActionQueueKind::Polarize,
                summary: "polarize polr-42 (pass / score 10)".to_string(),
                source_path: Some(PathBuf::from("/fixture/polarize/polr-42/prism.json")),
                priority: ActionPriority::High,
            },
            ActionQueueItem {
                kind: ActionQueueKind::ReportReady,
                summary: "open report just-091500-11111 (claude)".to_string(),
                source_path: Some(PathBuf::from("/fixture/artifacts/just.report.md")),
                priority: ActionPriority::Normal,
            },
        ],
        data_quality: DataQuality {
            scanned_meta_files: 128,
            capped: false,
            missing_model: 40,
            missing_duration: 2,
            parse_failures: 1,
            artifact_root: Some(PathBuf::from("/fixture/artifacts")),
            artifact_root_present: true,
        },
    }
}

fn mission_app(state: MissionControlState) -> App {
    App {
        mux_subscriber: None,
        config: AppConfig {
            no_verify_gate: false,
            state_root: "/fixture/state".into(),
            command_deck: "/usr/bin/vibecrafted".into(),
            launch_root: "/fixture/repo".into(),
            launch_runtime: LaunchRuntime::Terminal,
            terminal_binary: "vc-frame".into(),
            tick_rate: Duration::from_millis(250),
            server: "http://127.0.0.1:3024".into(),
            view: voc::observe::ConsoleView::Full,
        },
        state: ControlPlaneState::empty("/fixture/state"),
        runs: Vec::new(),
        selected: 0,
        active_tab: AppTab::MissionControl.index(),
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
        mission_control: state,
        mission_focus: 0,
        mission_artifact_root: PathBuf::from("/fixture/artifacts"),
        observe: Default::default(),
        memory: Default::default(),
        interaction: Default::default(),
    }
}

fn render_mission_tab(app: &App) -> Buffer {
    let backend = TestBackend::new(TERM_WIDTH, TERM_HEIGHT);
    let mut terminal = Terminal::new(backend).unwrap();
    terminal.draw(|frame| voc::ui::draw(frame, app)).unwrap();
    terminal.backend().buffer().clone()
}

/// Buffer content as text, one line per terminal row, trailing blanks
/// trimmed. This is what the operator literally sees (minus color).
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
fn mission_control_tab_populated_content_snapshot() {
    let app = mission_app(populated_mission_state());
    let buffer = render_mission_tab(&app);
    insta::assert_snapshot!("mission_control_tab_populated", buffer_text(&buffer));
}

#[test]
fn mission_control_tab_populated_color_map_snapshot() {
    let app = mission_app(populated_mission_state());
    let buffer = render_mission_tab(&app);
    insta::assert_snapshot!(
        "mission_control_tab_populated_colors",
        buffer_color_map(&buffer)
    );
}

#[test]
fn mission_control_tab_disk_probe_snapshot() {
    let mut state = MissionControlState {
        generated_at: "2026-06-10T12:00:00+00:00".to_string(),
        ..MissionControlState::default()
    };
    state.fleet_health = vec![
        FleetHealthSignal {
            label: "disk ~/.codex".to_string(),
            status: FleetHealthStatus::Ok,
            detail: "82.0% free".to_string(),
        },
        FleetHealthSignal {
            label: "disk ~/.aicx".to_string(),
            status: FleetHealthStatus::Warn,
            detail: "11.0% free".to_string(),
        },
        FleetHealthSignal {
            label: "disk ~/.vibecrafted/artifacts".to_string(),
            status: FleetHealthStatus::Blocked,
            detail: "4.0% free".to_string(),
        },
        FleetHealthSignal {
            label: "ulimit -f".to_string(),
            status: FleetHealthStatus::Blocked,
            detail: "finite 65336 blk (31.9 MiB)".to_string(),
        },
    ];
    let buffer = render_mission_tab(&mission_app(state));
    insta::assert_snapshot!("mission_control_tab_disk_probe", buffer_text(&buffer));
    insta::assert_snapshot!(
        "mission_control_tab_disk_probe_colors",
        buffer_color_map(&buffer)
    );
}

#[test]
fn mission_control_tab_mcp_probe_snapshot() {
    let mut state = MissionControlState {
        generated_at: "2026-06-10T12:00:00+00:00".to_string(),
        ..MissionControlState::default()
    };
    state.fleet_health = vec![
        FleetHealthSignal {
            label: "mcp loctree-mcp".to_string(),
            status: FleetHealthStatus::Ok,
            detail: "process alive".to_string(),
        },
        FleetHealthSignal {
            label: "mcp aicx-mcp".to_string(),
            status: FleetHealthStatus::Blocked,
            detail: "critical down".to_string(),
        },
        FleetHealthSignal {
            label: "mcp vibecrafted-mcp".to_string(),
            status: FleetHealthStatus::Warn,
            detail: "non-critical down".to_string(),
        },
        FleetHealthSignal {
            label: "mcp loctree-mcp snapshot".to_string(),
            status: FleetHealthStatus::Warn,
            detail: "snapshot stale".to_string(),
        },
    ];
    let buffer = render_mission_tab(&mission_app(state));
    insta::assert_snapshot!("mission_control_tab_mcp_probe", buffer_text(&buffer));
    insta::assert_snapshot!(
        "mission_control_tab_mcp_probe_colors",
        buffer_color_map(&buffer)
    );
}

#[test]
fn mission_control_tab_tailscale_probe_snapshot() {
    let mut state = MissionControlState {
        generated_at: "2026-06-10T12:00:00+00:00".to_string(),
        ..MissionControlState::default()
    };
    state.fleet_health = vec![
        FleetHealthSignal {
            label: "tailscale host-b".to_string(),
            status: FleetHealthStatus::Ok,
            detail: "online (100.64.0.11)".to_string(),
        },
        FleetHealthSignal {
            label: "tailscale host-a".to_string(),
            status: FleetHealthStatus::Blocked,
            detail: "dispatch target offline (100.64.0.10)".to_string(),
        },
        FleetHealthSignal {
            label: "tailscale blacky".to_string(),
            status: FleetHealthStatus::Warn,
            detail: "peer offline (100.64.0.12)".to_string(),
        },
        FleetHealthSignal {
            label: "tailscale status".to_string(),
            status: FleetHealthStatus::Unknown,
            detail: "tailscaled is not running".to_string(),
        },
    ];
    let buffer = render_mission_tab(&mission_app(state));
    insta::assert_snapshot!("mission_control_tab_tailscale_probe", buffer_text(&buffer));
    insta::assert_snapshot!(
        "mission_control_tab_tailscale_probe_colors",
        buffer_color_map(&buffer)
    );
}

#[test]
fn mission_control_tab_aicx_probe_snapshot() {
    let mut state = MissionControlState {
        generated_at: "2026-06-10T12:00:00+00:00".to_string(),
        ..MissionControlState::default()
    };
    state.fleet_health = vec![FleetHealthSignal {
        label: "aicx index".to_string(),
        status: FleetHealthStatus::Warn,
        detail: "index_freshness: semantic index lag 48h".to_string(),
    }];
    let buffer = render_mission_tab(&mission_app(state));
    insta::assert_snapshot!("mission_control_tab_aicx_probe", buffer_text(&buffer));
    insta::assert_snapshot!(
        "mission_control_tab_aicx_probe_colors",
        buffer_color_map(&buffer)
    );
}

#[test]
fn mission_control_tab_fleet_health_overflow_snapshot() {
    let mut state = MissionControlState {
        generated_at: "2026-06-10T12:00:00+00:00".to_string(),
        ..MissionControlState::default()
    };
    state.fleet_health = vec![
        FleetHealthSignal {
            label: "disk ~/.codex".to_string(),
            status: FleetHealthStatus::Ok,
            detail: "82.0% free".to_string(),
        },
        FleetHealthSignal {
            label: "tailscale exceptionally-long-hostname".to_string(),
            status: FleetHealthStatus::Blocked,
            detail: "dispatch target offline (100.64.0.10)".to_string(),
        },
        FleetHealthSignal {
            label: "mcp vibecrafted-mcp".to_string(),
            status: FleetHealthStatus::Warn,
            detail: "non-critical down".to_string(),
        },
        FleetHealthSignal {
            label: "aicx index".to_string(),
            status: FleetHealthStatus::Warn,
            detail: "semantic index lag 48h".to_string(),
        },
        FleetHealthSignal {
            label: "disk ~/.aicx".to_string(),
            status: FleetHealthStatus::Warn,
            detail: "11.0% free".to_string(),
        },
        FleetHealthSignal {
            label: "tailscale status".to_string(),
            status: FleetHealthStatus::Warn,
            detail: "daemon stale".to_string(),
        },
        FleetHealthSignal {
            label: "artifact-root".to_string(),
            status: FleetHealthStatus::Ok,
            detail: "/fixture/artifacts".to_string(),
        },
        FleetHealthSignal {
            label: "meta scan".to_string(),
            status: FleetHealthStatus::Ok,
            detail: "128 meta.json scanned".to_string(),
        },
        FleetHealthSignal {
            label: "model parity".to_string(),
            status: FleetHealthStatus::Ok,
            detail: "0/128 missing model".to_string(),
        },
    ];
    let buffer = render_mission_tab(&mission_app(state));
    insta::assert_snapshot!(
        "mission_control_tab_fleet_health_overflow",
        buffer_text(&buffer)
    );
    insta::assert_snapshot!(
        "mission_control_tab_fleet_health_overflow_colors",
        buffer_color_map(&buffer)
    );
}

#[test]
fn mission_control_tab_empty_state_snapshot() {
    let app = mission_app(MissionControlState::default());
    let buffer = render_mission_tab(&app);
    insta::assert_snapshot!("mission_control_tab_empty", buffer_text(&buffer));
}

/// Loud, named check that every one of the seven PLAN_23 panels actually
/// renders. A panel silently dropped from the grid fails here with its
/// name, not with a 4000-char snapshot diff.
#[test]
fn mission_control_tab_renders_all_seven_panels() {
    let app = mission_app(populated_mission_state());
    let rendered = buffer_text(&render_mission_tab(&app));
    let panels = [
        "Active dispatches",
        "Wave atlas",
        "Per-agent stats",
        "Per-skill stats",
        "Fleet health",
        "Failure board 24h",
        "Operator action queue",
    ];
    for panel in panels {
        assert!(
            rendered.contains(panel),
            "panel `{panel}` missing from the Mission Control tab"
        );
    }
    // The receipt footer is panel-adjacent armor: DataQuality must stay
    // visible so the operator knows which panels are partially blind.
    assert!(rendered.contains("Mission Control receipt"));
}

/// The "both themes" invariant. There is no in-app light/dark palette in
/// `voc` — PLAN_23's mid-light / mid-dark pair resolves terminal-side
/// (vc_frame `vetcoders-mesh.kdl` remaps the named ANSI slots). That only
/// works while the dashboard emits exclusively named ANSI colors; a
/// hardcoded Rgb/Indexed color would render identically in both themes
/// and break the mesh identity-accent contract. Guard it.
#[test]
fn mission_control_palette_is_terminal_theme_adaptive() {
    let app = mission_app(populated_mission_state());
    let buffer = render_mission_tab(&app);
    for (index, cell) in buffer.content().iter().enumerate() {
        let style = cell.style();
        for color in [style.fg, style.bg].into_iter().flatten() {
            assert!(
                !matches!(color, Color::Rgb(..) | Color::Indexed(..)),
                "cell {index} uses non-ANSI color {color:?}; \
                 Mission Control must stay terminal-theme adaptive"
            );
        }
    }
}

/// F3 guard: a summary longer than the action-queue panel must end in an
/// ellipsis on its own line instead of wrapping past the panel edge.
#[test]
fn action_queue_truncates_long_summaries_at_panel_width() {
    let mut state = populated_mission_state();
    state.action_queue.insert(
        0,
        ActionQueueItem {
            kind: ActionQueueKind::Failure,
            summary: "investigate the extremely long dispatch summary that previously \
                      wrapped past the panel edge and ended with the word OVERFLOWTAIL"
                .to_string(),
            source_path: None,
            priority: ActionPriority::Critical,
        },
    );
    let app = mission_app(state);
    let rendered = buffer_text(&render_mission_tab(&app));
    assert!(
        rendered.contains("investigate the extremely"),
        "long-summary queue item must render at all"
    );
    assert!(
        rendered.contains('…'),
        "long summary must truncate with an ellipsis"
    );
    assert!(
        !rendered.contains("OVERFLOWTAIL"),
        "summary tail must not render past the truncation point"
    );
}

/// F3 guard: degenerate terminal sizes must render without panicking —
/// width-aware truncation may never underflow or index past the panel.
#[test]
fn mission_control_tab_survives_narrow_terminals() {
    let app = mission_app(populated_mission_state());
    for (width, height) in [(80u16, 24u16), (40, 20), (20, 10), (8, 4)] {
        let backend = TestBackend::new(width, height);
        let mut terminal = Terminal::new(backend).unwrap();
        terminal
            .draw(|frame| voc::ui::draw(frame, &app))
            .unwrap_or_else(|err| panic!("draw must not fail at {width}x{height}: {err}"));
    }
}

// ─── vc-admin: standalone snapshot renderer e2e ─────────────────────────

fn write_meta(path: &Path, contents: &str) {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).unwrap();
    }
    fs::write(path, contents).unwrap();
}

/// Runs the real `vc-admin status` binary against disk fixtures and
/// freezes its full seven-section text output. The binary computes
/// "now" itself, so fixture timestamps are derived from the wall clock
/// at safe offsets and volatile fragments (paths, timestamps, relative
/// ages) are redacted via insta filters.
#[test]
fn vc_admin_status_renders_all_panels_from_disk_fixtures() {
    let dir = tempdir().unwrap();
    let state_root = dir.path().join("vcadmin-state");
    let artifact_root = dir.path().join("vcadmin-artifacts");
    fs::create_dir_all(state_root.join("runs")).unwrap();

    let now = chrono::Utc::now();
    let two_hours_ago = (now - chrono::Duration::hours(2)).to_rfc3339();
    let three_hours_ago = (now - chrono::Duration::hours(3)).to_rfc3339();

    // Dated bucket must sit inside STATS_WINDOW_DAYS (30d) of wall clock, or
    // directory_within_window prunes the walk and the snapshot goes silent.
    let bucket_day = now.format("%Y_%m%d").to_string();
    let bucket = artifact_root.join(format!("vetcoders/vibecrafted/{bucket_day}/reports"));
    write_meta(
        &bucket.join("just-001.meta.json"),
        &format!(
            r#"{{
                "run_id": "just-001",
                "agent": "claude",
                "skill_code": "implement",
                "exit_code": 0,
                "model": "claude-opus-4-7",
                "duration_s": 120.0,
                "completed_at": "{two_hours_ago}",
                "prompt_id": "wave-a",
                "report": "/fixture/just-001/report.md"
            }}"#
        ),
    );
    write_meta(
        &bucket.join("just-002.meta.json"),
        &format!(
            r#"{{
                "run_id": "just-002",
                "agent": "codex",
                "skill_code": "marbles",
                "exit_code": 1,
                "model": "unknown",
                "status": "failed",
                "completed_at": "{three_hours_ago}",
                "prompt_id": "wave-a"
            }}"#
        ),
    );
    write_meta(
        &bucket.join("just-003.meta.json"),
        &format!(
            r#"{{
                "run_id": "just-003",
                "agent": "claude",
                "skill_code": "implement",
                "exit_code": 0,
                "model": "claude-opus-4-7",
                "duration_s": 45.0,
                "completed_at": "{two_hours_ago}",
                "prompt_id": "wave-b"
            }}"#
        ),
    );

    let output = std::process::Command::new(env!("CARGO_BIN_EXE_vc-admin"))
        .arg("--state-root")
        .arg(&state_root)
        .arg("--artifact-root")
        .arg(&artifact_root)
        .env(
            "VIBECRAFTED_DISK_HEALTH_JSON",
            r#"{
                "paths": [
                    {
                        "label": "disk ~/.vibecrafted/control_plane",
                        "free_bytes": 10737418240,
                        "total_bytes": 107374182400
                    },
                    {
                        "label": "disk ~/.codex",
                        "free_bytes": 10737418240,
                        "total_bytes": 107374182400
                    },
                    {
                        "label": "disk ~/.aicx",
                        "free_bytes": 10737418240,
                        "total_bytes": 107374182400
                    },
                    {
                        "label": "disk ~/.vibecrafted/artifacts",
                        "free_bytes": 10737418240,
                        "total_bytes": 107374182400
                    }
                ],
                "ulimit_unlimited": true
            }"#,
        )
        .env(
            "VIBECRAFTED_MCP_PROCESS_SCAN",
            "/opt/vetcoders/bin/loctree-mcp\n/opt/vetcoders/bin/aicx-mcp\n",
        )
        .env(
            "VIBECRAFTED_LOCTREE_SNAPSHOT_FRESHNESS_JSON",
            r#"{"fresh": true, "head_label": "refs/heads/feat/runtime-integration"}"#,
        )
        .env("VIBECRAFTED_DISPATCH_TARGETS", "host-a,host-b")
        .env(
            "VIBECRAFTED_TAILSCALE_STATUS_JSON",
            r#"{
                "Peer": {
                    "node-host-b": {
                        "HostName": "host-b",
                        "Online": true,
                        "TailscaleIPs": ["100.64.0.11"]
                    },
                    "node-host-a": {
                        "HostName": "host-a",
                        "Online": false,
                        "TailscaleIPs": ["100.64.0.10"]
                    }
                }
            }"#,
        )
        .env(
            "VIBECRAFTED_AICX_HEALTH_JSON",
            r#"{
                "schema_version": 2,
                "index_freshness": {
                    "name": "index_freshness",
                    "severity": "green",
                    "detail": "index lag 1h",
                    "recommendation": null
                },
                "sidecar_coverage": {
                    "name": "sidecars",
                    "severity": "green",
                    "detail": "0 missing sidecars",
                    "recommendation": null
                },
                "overall": "green"
            }"#,
        )
        .arg("status")
        .output()
        .expect("vc-admin binary must run");
    assert!(
        output.status.success(),
        "vc-admin status failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8(output.stdout).expect("vc-admin emits UTF-8");

    insta::with_settings!({filters => vec![
        (r"\S*vcadmin-(state|artifacts)\S*", "[PATH]"),
        (r"\d{4}-\d{2}-\d{2}T[0-9:.]+[0-9:+Zz-]*", "[TS]"),
        (r"\b\d+[smhd] ago", "[AGE] ago"),
        (r"\bjust now\b", "[AGE] ago"),
        (r"== Fleet health \(\d+\) ==", "== Fleet health ([N]) =="),
        (r"(✓|!|✗|\?)         disk ~/.vibecra\.\.\.\s+.*", "$1         disk ~/.vibecra... [DISK]"),
        (r"(✓|!|✗|\?)         disk ~/.vibecrafted/control_plane\s+.*", "$1         disk ~/.vibecrafted/control_plane [DISK]"),
        (r"(✓|!|✗|\?)         disk ~/.codex\s+.*", "$1         disk ~/.codex      [DISK]"),
        (r"(✓|!|✗|\?)         disk ~/.aicx\s+.*", "$1         disk ~/.aicx       [DISK]"),
        (r"(✓|!|✗|\?)         disk ~/.vibecrafted/artifacts\s+.*", "$1         disk ~/.vibecrafted/artifacts [DISK]"),
        (r"(✓|!|✗|\?)         ulimit -f\s+.*", "$1         ulimit -f          [ULIMIT]"),
        (r"(✓|!|✗|\?)         mcp loctree-mcp\.\.\.\s+.*", "$1         mcp loctree-mcp... [MCP]"),
        (r"(✓|!|✗|\?)         mcp vibecrafted\.\.\.\s+.*", "$1         mcp vibecrafted... [MCP]"),
        (r"(✓|!|✗|\?)         mcp loctree-mcp snapshot\s+.*", "$1         mcp loctree-mcp snapshot [MCP]"),
        (r"(✓|!|✗|\?)         mcp loctree-mcp\s+.*", "$1         mcp loctree-mcp [MCP]"),
        (r"(✓|!|✗|\?)         mcp aicx-mcp\s+.*", "$1         mcp aicx-mcp    [MCP]"),
        (r"(✓|!|✗|\?)         mcp vibecrafted-mcp\s+.*", "$1         mcp vibecrafted-mcp [MCP]"),
        (r"(✓|!|✗|\?)         aicx index\s+.*", "$1         aicx index      [AICX]"),
    ]}, {
        insta::assert_snapshot!("vc_admin_status", stdout);
    });
}
