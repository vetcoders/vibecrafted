use crate::app::{App, AppTab, LaunchFocus};
use crate::mission_control::{
    ActionPriority, ActionQueueItem, ActionQueueKind, ActiveDispatch, AgentStatsRow, DataQuality,
    FailureEntry, FleetHealthSignal, FleetHealthStatus, SkillStatsRow, WaveSegment, WaveState,
};
use crate::observe::{ConsoleView, ObserveHealth};
use crate::state::RunKind;
use ratatui::prelude::*;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Clear, List, ListItem, Paragraph, Tabs, Wrap};

pub fn draw(frame: &mut Frame, app: &App) {
    let root = frame.area();
    let layout = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(2),
            Constraint::Length(3),
            Constraint::Min(12),
            Constraint::Length(3),
        ])
        .split(root);

    draw_header(frame, layout[0], app);
    draw_tabs(frame, layout[1], app);
    draw_body(frame, layout[2], app);
    draw_footer(frame, layout[3], app);

    match app.focus {
        LaunchFocus::Help => draw_help_overlay(frame, app),
        LaunchFocus::EditPrompt => draw_prompt_overlay(frame, app),
        LaunchFocus::Search => draw_search_overlay(frame, app),
        LaunchFocus::Error => draw_error_overlay(frame, app),
        LaunchFocus::Artifact => draw_artifact_overlay(frame, app),
        LaunchFocus::Memory => draw_memory_overlay(frame, app),
        _ => {}
    }
}

fn draw_header(frame: &mut Frame, area: Rect, app: &App) {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(1), Constraint::Length(1)])
        .split(area);

    let title = if app.config.view == ConsoleView::Observe {
        Line::from(vec![
            Span::styled(
                "voc",
                Style::default()
                    .fg(Color::White)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                format!("  {}  {}", app.observe.status.label(), app.observe.origin),
                Style::default().fg(Color::DarkGray),
            ),
            Span::raw("  "),
            Span::styled(app.status_summary(), Style::default().fg(Color::Gray)),
        ])
    } else {
        Line::from(vec![
            Span::styled(
                "Vibecrafted Operator Console",
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw("  "),
            Span::styled(app.status_summary(), Style::default().fg(Color::Gray)),
        ])
    };
    frame.render_widget(Paragraph::new(title), rows[0]);

    let workspace = app
        .config
        .launch_root
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("—");
    let context = format!(
        "workspace: {workspace}  |  active {}  stalled {}  |  scope: {}  |  {}",
        app.active_run_count(),
        app.stalled_run_count(),
        app.queue_scope.label(),
        app.active_tab().label()
    );
    frame.render_widget(
        Paragraph::new(context).style(Style::default().fg(Color::DarkGray)),
        rows[1],
    );
}

fn draw_tabs(frame: &mut Frame, area: Rect, app: &App) {
    let tabs = Tabs::new(
        app.tab_labels()
            .into_iter()
            .map(Line::from)
            .collect::<Vec<_>>(),
    )
    .block(Block::default().borders(Borders::ALL).title("Surface"))
    .select(app.active_tab)
    .divider("│")
    .style(Style::default().fg(Color::Gray))
    .highlight_style(
        Style::default()
            .fg(Color::Yellow)
            .add_modifier(Modifier::BOLD),
    );
    frame.render_widget(tabs, area);
}

fn draw_body(frame: &mut Frame, area: Rect, app: &App) {
    match app.active_tab() {
        AppTab::Monitor if app.config.view == ConsoleView::Observe => {
            draw_observe(frame, area, app);
        }
        AppTab::Monitor => draw_monitor(frame, area, app),
        AppTab::Dispatch => draw_dispatch(frame, area, app),
        AppTab::Controls => draw_controls(frame, area, app),
        AppTab::MissionControl => draw_mission_control(frame, area, app),
    }
}

fn draw_observe(frame: &mut Frame, area: Rect, app: &App) {
    let columns = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(38), Constraint::Percentage(62)])
        .split(area);

    let mut items = Vec::new();
    if app.observe.runs.is_empty() {
        let empty_message = match app.observe.status {
            ObserveHealth::Live => "no live sessions in canonical control plane",
            ObserveHealth::Degraded => "canonical state stale; showing no cached sessions",
            ObserveHealth::Offline => "canonical control plane unavailable",
        };
        items.push(ListItem::new(Line::from(Span::styled(
            empty_message,
            Style::default().fg(Color::DarkGray),
        ))));
    }
    for (index, run) in app.observe.runs.iter().enumerate() {
        let selected = index == app.observe.selected;
        let glyph = if run.is_genuinely_active() { "●" } else { "○" };
        let style = if selected {
            Style::default()
                .fg(Color::Black)
                .bg(Color::White)
                .add_modifier(Modifier::BOLD)
        } else if run.kind_label() == "stalled" {
            Style::default().fg(Color::Yellow)
        } else if run.is_genuinely_active() {
            Style::default().fg(Color::Green)
        } else {
            Style::default().fg(Color::Gray)
        };
        items.push(ListItem::new(Line::from(vec![
            Span::styled(format!("{glyph} "), style),
            Span::styled(run.list_line(), style),
        ])));
    }
    let active = app
        .observe
        .runs
        .iter()
        .filter(|run| run.is_genuinely_active())
        .count();
    let stalled = app
        .observe
        .runs
        .iter()
        .filter(|run| run.kind_label() == "stalled")
        .count();
    let title = format!(" Observe · {active} active · {stalled} stalled ");
    frame.render_widget(
        List::new(items).block(
            Block::default()
                .borders(Borders::ALL)
                .title(Span::styled(title, Style::default().fg(Color::White))),
        ),
        columns[0],
    );

    let mut body = Vec::new();
    if let Some(error) = &app.observe.error {
        body.push(Line::from(Span::styled(
            format!("server error: {error}"),
            Style::default().fg(Color::Yellow),
        )));
        body.push(Line::from(""));
    }
    if let Some(run) = app.observe.runs.get(app.observe.selected) {
        body.push(Line::from(Span::styled(
            run.title_line(),
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        )));
        body.push(Line::from(Span::styled(
            run.run_id.clone(),
            Style::default().fg(Color::DarkGray),
        )));
        body.push(Line::from(Span::styled(
            run.switch_target()
                .map(|target| format!("Enter switches to {target} · click row to select"))
                .unwrap_or_else(|| "session has no attach target".to_string()),
            Style::default().fg(Color::Cyan),
        )));
        body.push(Line::from(""));
        if app.observe.transcript.trim().is_empty() {
            body.push(Line::from(Span::styled(
                "No human transcript yet. Events appear here when the run writes them.",
                Style::default().fg(Color::DarkGray),
            )));
        } else {
            // Last 40 lines, in file order.
            let lines: Vec<&str> = app.observe.transcript.lines().collect();
            let tail = &lines[lines.len().saturating_sub(40)..];
            for line in tail {
                body.push(Line::from(line.to_string()));
            }
        }
    } else {
        body.push(Line::from(Span::styled(
            "Select a live session from the canonical control plane.",
            Style::default().fg(Color::DarkGray),
        )));
    }
    frame.render_widget(
        Paragraph::new(body).wrap(Wrap { trim: false }).block(
            Block::default().borders(Borders::ALL).title(Span::styled(
                " Transcript ",
                Style::default().fg(Color::White),
            )),
        ),
        columns[1],
    );
}

fn draw_memory_overlay(frame: &mut Frame, app: &App) {
    let area = centered_rect(80, 70, frame.area());
    frame.render_widget(Clear, area);
    let mut lines = vec![
        Line::from(Span::styled(
            format!("AICX · {}", app.memory.project),
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(Span::styled(
            "w opens aicx wizard   ·   Esc closes",
            Style::default().fg(Color::DarkGray),
        )),
        Line::from(""),
    ];
    if let Some(error) = &app.memory.error {
        lines.push(Line::from(Span::styled(
            error.clone(),
            Style::default().fg(Color::Yellow),
        )));
    }
    for line in &app.memory.lines {
        lines.push(Line::from(line.clone()));
    }
    frame.render_widget(
        Paragraph::new(lines)
            .wrap(Wrap { trim: true })
            .block(Block::default().borders(Borders::ALL).title(" Memory ")),
        area,
    );
}

fn draw_monitor(frame: &mut Frame, area: Rect, app: &App) {
    let mux_lines = app.mux_status_lines();
    let polarize_lines = app.polarize_status_lines();
    let mux_height = if mux_lines.is_empty() {
        0
    } else {
        // header + entries + 2 (top + bottom border). Capped so a noisy mux
        // setup with many services cannot starve the run table; the panel
        // scrolls with `Wrap` past the cap.
        (mux_lines.len() as u16 + 2).clamp(3, 10)
    };
    let polarize_height = if polarize_lines.is_empty() {
        0
    } else {
        (polarize_lines.len() as u16 + 2).clamp(3, 9)
    };

    let mut constraints = vec![Constraint::Length(5)];
    if !mux_lines.is_empty() {
        constraints.push(Constraint::Length(mux_height));
    }
    if !polarize_lines.is_empty() {
        constraints.push(Constraint::Length(polarize_height));
    }
    constraints.push(Constraint::Min(8));
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints(constraints)
        .split(area);

    draw_stat_strip(
        frame,
        rows[0],
        [
            (
                "Monitor pulse",
                vec![
                    format!("{} runs visible", app.runs.len()),
                    format!(
                        "{} active · {} stalled",
                        app.active_run_count(),
                        app.stalled_run_count()
                    ),
                ],
                Color::Green,
            ),
            (
                "Selection",
                app.selected_run()
                    .map(|run| {
                        vec![
                            run.operator_title(),
                            format!("{}  {}", run.kind.label(), run.snapshot.run_id),
                        ]
                    })
                    .unwrap_or_else(|| {
                        vec![
                            "No run selected".to_string(),
                            "Dispatch a worker to populate the board".to_string(),
                        ]
                    }),
                Color::Yellow,
            ),
            (
                "Filter",
                vec![
                    format!("{} scope", app.queue_scope.label()),
                    if app.search_query.is_empty() {
                        "f cycles live/history/all".to_string()
                    } else {
                        format!("/ {}", app.search_query)
                    },
                ],
                Color::Cyan,
            ),
        ],
    );

    let mut body_idx = 1;
    if !mux_lines.is_empty() {
        let state = app
            .mux_subscriber
            .as_ref()
            .and_then(|sub| sub.state.read().ok())
            .map(|s| s.clone());
        draw_mux_panel(
            frame,
            rows[body_idx],
            &mux_lines,
            app.mux_summaries.len(),
            state.as_ref(),
        );
        body_idx += 1;
    }
    if !polarize_lines.is_empty() {
        draw_polarize_panel(
            frame,
            rows[body_idx],
            &polarize_lines,
            app.polarize_intents.len(),
        );
        body_idx += 1;
    }

    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(36), Constraint::Percentage(64)])
        .split(rows[body_idx]);

    draw_runs(frame, body[0], app, true);

    let right = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Percentage(62), Constraint::Percentage(38)])
        .split(body[1]);
    draw_detail(frame, right[0], app, "Run dossier");
    draw_events(frame, right[1], app, "Recent timeline");
}

fn draw_mux_panel(
    frame: &mut Frame,
    area: Rect,
    lines: &[String],
    total_services: usize,
    state: Option<&crate::mux::SubscriberState>,
) {
    let any_unhealthy = lines.iter().any(|line| line.contains("! "));
    let title_text = match state {
        Some(crate::mux::SubscriberState::Connected) => {
            format!(" rmcp-mux ({total_services}) [Connected] ")
        }
        Some(crate::mux::SubscriberState::Reconnecting) => {
            format!(" rmcp-mux ({total_services}) [Reconnecting] ")
        }
        Some(crate::mux::SubscriberState::Polling) => {
            format!(" rmcp-mux ({total_services}) [Polling] ")
        }
        Some(crate::mux::SubscriberState::Failed) => {
            format!(" rmcp-mux ({total_services}) [Failed] ")
        }
        None => format!(" rmcp-mux ({total_services}) "),
    };
    let title_color = match state {
        Some(crate::mux::SubscriberState::Connected) => Color::Green,
        Some(crate::mux::SubscriberState::Reconnecting)
        | Some(crate::mux::SubscriberState::Polling) => Color::Yellow,
        Some(crate::mux::SubscriberState::Failed) => Color::Red,
        None => {
            if any_unhealthy {
                Color::Red
            } else {
                Color::Green
            }
        }
    };
    let block = Block::default()
        .title(Span::styled(
            title_text,
            Style::default()
                .fg(title_color)
                .add_modifier(Modifier::BOLD),
        ))
        .borders(Borders::ALL);
    let body_lines: Vec<Line> = lines
        .iter()
        .map(|raw| {
            if let Some(rest) = raw.strip_prefix("  ! ") {
                Line::from(vec![
                    Span::styled("  ! ", Style::default().fg(Color::Red)),
                    Span::raw(rest.to_string()),
                ])
            } else if let Some(rest) = raw.strip_prefix("  • ") {
                Line::from(vec![
                    Span::styled("  • ", Style::default().fg(Color::Green)),
                    Span::raw(rest.to_string()),
                ])
            } else {
                Line::from(raw.clone())
            }
        })
        .collect();
    let para = Paragraph::new(body_lines)
        .block(block)
        .wrap(Wrap { trim: false });
    frame.render_widget(para, area);
}

fn draw_polarize_panel(frame: &mut Frame, area: Rect, lines: &[String], total_intents: usize) {
    let has_doctrine = lines.iter().any(|line| line.contains("doctrine"));
    let title_color = if has_doctrine {
        Color::Magenta
    } else {
        Color::Yellow
    };
    let title_text = format!(" polarize ({total_intents}) ");
    let block = Block::default()
        .title(Span::styled(
            title_text,
            Style::default()
                .fg(title_color)
                .add_modifier(Modifier::BOLD),
        ))
        .borders(Borders::ALL);
    let body_lines: Vec<Line> = lines
        .iter()
        .map(|raw| {
            if raw.contains(" doctrine ") {
                Line::from(vec![
                    Span::styled("* ", Style::default().fg(Color::Magenta)),
                    Span::raw(raw.trim_start_matches("  * ").to_string()),
                ])
            } else if raw.contains(" pass ") {
                Line::from(vec![
                    Span::styled("> ", Style::default().fg(Color::Green)),
                    Span::raw(raw.trim_start_matches("  > ").to_string()),
                ])
            } else {
                Line::from(raw.clone())
            }
        })
        .collect();
    let para = Paragraph::new(body_lines)
        .block(block)
        .wrap(Wrap { trim: false });
    frame.render_widget(para, area);
}

fn draw_dispatch(frame: &mut Frame, area: Rect, app: &App) {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(5), Constraint::Min(12)])
        .split(area);

    draw_stat_strip(
        frame,
        rows[0],
        [
            (
                "Mission",
                vec![
                    app.launch_kind.human_title().to_string(),
                    app.launch_kind.human_description().to_string(),
                ],
                Color::Yellow,
            ),
            (
                "Operator",
                vec![
                    format!("agent {}", app.selected_agent()),
                    format!("runtime {}", app.launch_runtime.label()),
                ],
                Color::Blue,
            ),
            (
                "Prompt",
                vec![
                    if app.focus == LaunchFocus::EditPrompt {
                        "Editing live prompt".to_string()
                    } else {
                        "Ready to launch".to_string()
                    },
                    format!("{} chars staged", app.launch_prompt.chars().count()),
                ],
                Color::Magenta,
            ),
        ],
    );

    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(60), Constraint::Percentage(40)])
        .split(rows[1]);

    draw_launch(frame, body[0], app);

    let right = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Percentage(44), Constraint::Percentage(56)])
        .split(body[1]);

    let guide_lines = vec![
        Line::from("Dispatch posture"),
        Line::from(""),
        Line::from("Shape the next worker before you launch it."),
        Line::from("Use mission kind for intent, agent for style, runtime for surface."),
        Line::from("Prompt edit is the last mile: keep it sharp and bounded."),
    ];
    let guide = Paragraph::new(guide_lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Dispatch playbook"),
        )
        .wrap(Wrap { trim: false });
    frame.render_widget(guide, right[0]);

    draw_launch_history(frame, right[1], app);
}

fn draw_controls(frame: &mut Frame, area: Rect, app: &App) {
    let actions = app.deep_actions();
    let selected_action = app
        .selected_deep_action()
        .map(|action| action.control_label())
        .unwrap_or_else(|| "No action primed".to_string());
    let artifact_count = actions
        .iter()
        .filter(|action| {
            matches!(
                action,
                crate::app::DeepAction::OpenReport(_)
                    | crate::app::DeepAction::OpenTranscript(_)
                    | crate::app::DeepAction::OpenRoot(_)
                    | crate::app::DeepAction::PolarizeIntent { .. }
            )
        })
        .count();

    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(5), Constraint::Min(12)])
        .split(area);

    draw_stat_strip(
        frame,
        rows[0],
        [
            (
                "Run access",
                app.selected_run()
                    .map(|run| {
                        vec![
                            run.operator_title(),
                            format!("{}  {}", run.kind.label(), run.snapshot.run_id),
                        ]
                    })
                    .unwrap_or_else(|| {
                        vec![
                            "No run selected".to_string(),
                            "Monitor chooses the source run".to_string(),
                        ]
                    }),
                Color::Yellow,
            ),
            (
                "Action deck",
                vec![
                    format!("{} contextual actions", actions.len()),
                    selected_action,
                ],
                Color::Cyan,
            ),
            (
                "Artifacts",
                vec![
                    format!("{artifact_count} file surfaces"),
                    "reports / transcripts / roots".to_string(),
                ],
                Color::Green,
            ),
        ],
    );

    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(46), Constraint::Percentage(54)])
        .split(rows[1]);

    draw_deep_controls(frame, body[0], app);

    let right = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Percentage(60), Constraint::Percentage(40)])
        .split(body[1]);

    draw_detail(frame, right[0], app, "Artifact access");
    draw_events(frame, right[1], app, "Selected timeline");
}

fn draw_footer(frame: &mut Frame, area: Rect, app: &App) {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),
            Constraint::Length(1),
            Constraint::Length(1),
        ])
        .split(area);

    let nav_hint = match (app.active_tab(), app.focus) {
        (AppTab::Monitor, _) => {
            "Monitor: ↑/↓ runs  / search  f scope  x archive  d controls  ? help"
        }
        (AppTab::Dispatch, LaunchFocus::EditPrompt) => {
            "Dispatch edit: type prompt  Enter newline  Ctrl+S/Esc save"
        }
        (_, LaunchFocus::Error) => "Error: Enter/Esc closes the failure details",
        (_, LaunchFocus::Artifact) => "Artifact viewer: Enter/Esc closes the native viewer",
        (AppTab::Dispatch, _) => {
            "Dispatch: ↑/↓ field  ←/→ change  e edit prompt  Enter launch  1-4 presets"
        }
        (AppTab::Controls, _) => {
            "Controls: ↑/↓ action  ←/→ run selection  Enter open  d jump here from Monitor"
        }
        (AppTab::MissionControl, _) => "Mission Control: ↑/↓ panel focus  r refresh  Tab next tab",
    };
    frame.render_widget(
        Paragraph::new(nav_hint).style(Style::default().fg(Color::Cyan)),
        rows[0],
    );

    let shortcuts = if app.config.view == ConsoleView::Observe {
        "Observe: j/k select  m memory  w aicx wizard  r refresh  q quit"
    } else {
        "Global: q quit  r refresh  a cycle agent  v cycle runtime  y copy  Ctrl+L clear search  ? help"
    };
    frame.render_widget(
        Paragraph::new(shortcuts).style(Style::default().fg(Color::DarkGray)),
        rows[1],
    );

    let status = if app.status_line.is_empty() {
        format!("state root: {}", app.config.state_root.to_string_lossy())
    } else {
        app.status_line.clone()
    };
    frame.render_widget(
        Paragraph::new(status).style(Style::default().fg(Color::Gray)),
        rows[2],
    );
}

fn draw_runs(frame: &mut Frame, area: Rect, app: &App, emphasize_live: bool) {
    let items: Vec<ListItem> = if app.runs.is_empty() {
        vec![ListItem::new(
            "No actionable runs in this workspace. Press f for history/archive.",
        )]
    } else {
        app.runs
            .iter()
            .enumerate()
            .map(|(idx, run)| {
                let status = status_style(run.kind);
                let selected = idx == app.selected;
                let label = format!("{}  {}", run.kind.label(), run.operator_title());
                let detail = format!("{}  {}", run.age_label, run.snapshot.run_id);
                let mut spans = vec![
                    Span::styled(label, status),
                    Span::raw("\n"),
                    Span::styled(detail, Style::default().fg(Color::DarkGray)),
                ];
                if selected {
                    spans.insert(0, Span::styled("▶ ", Style::default().fg(Color::Yellow)));
                } else {
                    spans.insert(0, Span::raw("  "));
                }
                ListItem::new(Line::from(spans))
            })
            .collect()
    };

    let title = if emphasize_live && !app.search_query.is_empty() {
        format!("{} (/ {})", app.queue_scope.title(), app.search_query)
    } else if emphasize_live {
        app.queue_scope.title().to_string()
    } else {
        "Runs".to_string()
    };
    let list = List::new(items).block(Block::default().borders(Borders::ALL).title(title));
    frame.render_widget(list, area);
}

fn draw_detail(frame: &mut Frame, area: Rect, app: &App, title: &str) {
    let lines = app
        .detail_lines()
        .into_iter()
        .map(Line::from)
        .collect::<Vec<_>>();
    let detail = Paragraph::new(lines)
        .block(Block::default().borders(Borders::ALL).title(title))
        .wrap(Wrap { trim: false });
    frame.render_widget(detail, area);
}

fn draw_events(frame: &mut Frame, area: Rect, app: &App, title: &str) {
    let lines = app
        .event_lines()
        .into_iter()
        .map(Line::from)
        .collect::<Vec<_>>();
    let events = Paragraph::new(lines)
        .block(Block::default().borders(Borders::ALL).title(title))
        .wrap(Wrap { trim: false });
    frame.render_widget(events, area);
}

fn draw_launch(frame: &mut Frame, area: Rect, app: &App) {
    let lines = app
        .prompt_lines()
        .into_iter()
        .map(Line::from)
        .collect::<Vec<_>>();

    let title = if app.focus == LaunchFocus::EditPrompt {
        "Dispatch deck (editing prompt)"
    } else {
        "Dispatch deck"
    };

    let launch = Paragraph::new(lines)
        .block(Block::default().borders(Borders::ALL).title(title))
        .wrap(Wrap { trim: false });
    frame.render_widget(launch, area);
}

fn draw_launch_history(frame: &mut Frame, area: Rect, app: &App) {
    let mut lines = if app.launch_history.is_empty() {
        vec![
            Line::from("No launches from this session yet."),
            Line::from(""),
            Line::from("Use Dispatch to stage a worker, then press Enter."),
        ]
    } else {
        app.launch_history
            .iter()
            .rev()
            .map(|entry| Line::from(entry.clone()))
            .collect::<Vec<_>>()
    };
    lines.push(Line::from(""));
    lines.push(Line::from(format!(
        "selected run: {}",
        app.selected_run()
            .map(|run| run.snapshot.run_id.as_str())
            .unwrap_or("none")
    )));
    let panel = Paragraph::new(lines)
        .block(Block::default().borders(Borders::ALL).title("Launch trail"))
        .wrap(Wrap { trim: false });
    frame.render_widget(panel, area);
}

fn draw_deep_controls(frame: &mut Frame, area: Rect, app: &App) {
    let lines = app
        .deep_control_lines()
        .into_iter()
        .map(Line::from)
        .collect::<Vec<_>>();
    let panel = Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Primary actions"),
        )
        .wrap(Wrap { trim: false });
    frame.render_widget(panel, area);
}

fn draw_mission_control(frame: &mut Frame, area: Rect, app: &App) {
    let mission = &app.mission_control;
    let focus = app.mission_focus;

    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(5),
            Constraint::Min(8),
            Constraint::Length(6),
        ])
        .split(area);

    draw_stat_strip(
        frame,
        rows[0],
        [
            (
                "Active dispatches",
                vec![
                    format!("{} running", mission.active_dispatches.len()),
                    format!("{} stalled queue items", mission.action_queue.len()),
                ],
                Color::Green,
            ),
            (
                "History (30d)",
                vec![
                    format!(
                        "{} meta.json scanned",
                        mission.data_quality.scanned_meta_files
                    ),
                    if mission.data_quality.capped {
                        "scan capped (load-shed)".to_string()
                    } else {
                        format!(
                            "{} agents · {} skills",
                            mission.agent_stats.len(),
                            mission.skill_stats.len()
                        )
                    },
                ],
                Color::Cyan,
            ),
            (
                "Mission posture",
                vec![
                    format!(
                        "model parity {}/{}",
                        mission.data_quality.missing_model,
                        mission.data_quality.scanned_meta_files.max(1)
                    ),
                    format!(
                        "duration parity {}/{}",
                        mission.data_quality.missing_duration,
                        mission.data_quality.scanned_meta_files.max(1)
                    ),
                ],
                Color::Magenta,
            ),
        ],
    );

    // Grid layout: 7 panels arranged as 3 rows × (varied columns) per
    // PLAN_23 §4 mock-up:
    //  ┌ active  │ wave-atlas ┐
    //  ├ per-agent           ┤
    //  ┌ per-skill │ fleet   ┐
    //  ┌ failures  │ action  ┐
    let body = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Ratio(1, 3),
            Constraint::Ratio(1, 3),
            Constraint::Ratio(1, 3),
        ])
        .split(rows[1]);

    let top = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
        .split(body[0]);
    draw_mc_active_dispatches(frame, top[0], &mission.active_dispatches, focus == 0);
    draw_mc_wave_atlas(frame, top[1], &mission.wave_atlas, focus == 1);

    let middle = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(55), Constraint::Percentage(45)])
        .split(body[1]);
    draw_mc_agent_stats(frame, middle[0], &mission.agent_stats, focus == 2);
    draw_mc_skill_stats(frame, middle[1], &mission.skill_stats, focus == 3);

    let bottom = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(34),
            Constraint::Percentage(33),
            Constraint::Percentage(33),
        ])
        .split(body[2]);
    draw_mc_fleet_health(frame, bottom[0], &mission.fleet_health, focus == 4);
    draw_mc_failure_board(frame, bottom[1], &mission.failures, focus == 5);
    draw_mc_action_queue(frame, bottom[2], &mission.action_queue, focus == 6);

    draw_mc_quality_footer(frame, rows[2], &mission.data_quality, &mission.generated_at);
}

fn draw_mc_active_dispatches(
    frame: &mut Frame,
    area: Rect,
    items: &[ActiveDispatch],
    focused: bool,
) {
    let title = format!(" Active dispatches ({}) ", items.len());
    let block = panel_block(&title, focused, Color::Green);
    let lines: Vec<Line> = if items.is_empty() {
        vec![
            Line::from(Span::styled(
                "no live dispatches",
                Style::default().fg(Color::DarkGray),
            )),
            Line::from(""),
            Line::from("Launch a worker from the Dispatch tab to populate this panel."),
        ]
    } else {
        items
            .iter()
            .flat_map(|dispatch| {
                let header = Line::from(vec![
                    Span::styled(
                        format!("▶ {} ", dispatch.run_id),
                        Style::default()
                            .fg(Color::Yellow)
                            .add_modifier(Modifier::BOLD),
                    ),
                    Span::raw(format!("{} / {}", dispatch.agent, dispatch.skill)),
                ]);
                let mut detail = format!(
                    "  root {} · age {} · {}",
                    dispatch.root_label, dispatch.age_label, dispatch.eta_label
                );
                if let Some(wave) = dispatch.wave.as_deref() {
                    detail.push_str(&format!(" · wave {wave}"));
                }
                vec![header, Line::from(detail)]
            })
            .collect()
    };
    let para = Paragraph::new(lines)
        .block(block)
        .wrap(Wrap { trim: false });
    frame.render_widget(para, area);
}

fn draw_mc_wave_atlas(frame: &mut Frame, area: Rect, segments: &[WaveSegment], focused: bool) {
    let title = format!(" Wave atlas ({}) ", segments.len());
    let block = panel_block(&title, focused, Color::Cyan);
    let lines: Vec<Line> = if segments.is_empty() {
        vec![
            Line::from(Span::styled(
                "no waves in the last 30d",
                Style::default().fg(Color::DarkGray),
            )),
            Line::from(""),
            Line::from("Waves emerge from prompt_id groups in meta.json."),
        ]
    } else {
        segments
            .iter()
            .map(|segment| {
                let glyph = segment.latest_state.glyph();
                let color = match segment.latest_state {
                    WaveState::Completed => Color::Green,
                    WaveState::InProgress => Color::Yellow,
                    WaveState::Failed => Color::Red,
                    WaveState::Pending => Color::DarkGray,
                };
                Line::from(vec![
                    Span::styled(format!("{glyph} "), Style::default().fg(color)),
                    Span::styled(
                        format!("{:<22}", truncate(&segment.wave_id, 22)),
                        Style::default().add_modifier(Modifier::BOLD),
                    ),
                    Span::raw(format!(
                        " {}/{}  ✓{} ✗{} ⏳{}",
                        segment.completed,
                        segment.total,
                        segment.completed,
                        segment.failed,
                        segment.active
                    )),
                ])
            })
            .collect()
    };
    let para = Paragraph::new(lines)
        .block(block)
        .wrap(Wrap { trim: false });
    frame.render_widget(para, area);
}

fn draw_mc_agent_stats(frame: &mut Frame, area: Rect, rows: &[AgentStatsRow], focused: bool) {
    let title = format!(" Per-agent stats (30d, {} agents) ", rows.len());
    let block = panel_block(&title, focused, Color::Yellow);
    let lines: Vec<Line> = if rows.is_empty() {
        vec![Line::from(Span::styled(
            "no agent activity in window",
            Style::default().fg(Color::DarkGray),
        ))]
    } else {
        let mut out = Vec::with_capacity(rows.len() + 1);
        out.push(Line::from(Span::styled(
            "agent      runs    ✓    ✗   ✓%    ⌀dur model%",
            Style::default()
                .fg(Color::DarkGray)
                .add_modifier(Modifier::BOLD),
        )));
        for row in rows {
            let avg = row
                .avg_duration_s
                .map(format_duration_seconds)
                .unwrap_or_else(|| "  —  ".to_string());
            let model_pct = (row.model_known_rate * 100.0).round() as i32;
            let success_pct = (row.success_rate * 100.0).round() as i32;
            out.push(Line::from(format!(
                "{:<9} {:>4} {:>4} {:>4} {:>3}%  {:>6}  {:>4}%",
                truncate(&row.agent, 9),
                row.total_runs,
                row.completed,
                row.failed,
                success_pct,
                avg,
                model_pct,
            )));
        }
        out
    };
    let para = Paragraph::new(lines)
        .block(block)
        .wrap(Wrap { trim: false });
    frame.render_widget(para, area);
}

fn draw_mc_skill_stats(frame: &mut Frame, area: Rect, rows: &[SkillStatsRow], focused: bool) {
    let title = format!(" Per-skill stats ({}) ", rows.len());
    let block = panel_block(&title, focused, Color::Blue);
    let lines: Vec<Line> = if rows.is_empty() {
        vec![Line::from(Span::styled(
            "no skill invocations in window",
            Style::default().fg(Color::DarkGray),
        ))]
    } else {
        let mut out = Vec::with_capacity(rows.len() + 1);
        out.push(Line::from(Span::styled(
            "skill         inv   ✓    ✗   ⌀dur",
            Style::default()
                .fg(Color::DarkGray)
                .add_modifier(Modifier::BOLD),
        )));
        for row in rows {
            let avg = row
                .avg_duration_s
                .map(format_duration_seconds)
                .unwrap_or_else(|| "  —  ".to_string());
            let quiet_marker = if row.invocations <= 2 { " ⚠" } else { "" };
            out.push(Line::from(format!(
                "{:<12} {:>4} {:>4} {:>4} {:>6}{}",
                truncate(&row.skill, 12),
                row.invocations,
                row.completed,
                row.failed,
                avg,
                quiet_marker,
            )));
        }
        out
    };
    let para = Paragraph::new(lines)
        .block(block)
        .wrap(Wrap { trim: false });
    frame.render_widget(para, area);
}

fn draw_mc_fleet_health(
    frame: &mut Frame,
    area: Rect,
    signals: &[FleetHealthSignal],
    focused: bool,
) {
    let title = format!(" Fleet health ({}) ", signals.len());
    let block = panel_block(&title, focused, Color::Magenta);
    let inner_height = area.height.saturating_sub(2) as usize;
    let inner_width = area.width.saturating_sub(2) as usize;
    let lines: Vec<Line> = if signals.is_empty() {
        vec![Line::from(Span::styled(
            "fleet not probed",
            Style::default().fg(Color::DarkGray),
        ))]
    } else {
        fleet_health_lines(signals, inner_height, inner_width)
    };
    let para = Paragraph::new(lines)
        .block(block)
        .wrap(Wrap { trim: false });
    frame.render_widget(para, area);
}

fn fleet_health_lines(
    signals: &[FleetHealthSignal],
    inner_height: usize,
    inner_width: usize,
) -> Vec<Line<'static>> {
    let mut ordered = signals.iter().enumerate().collect::<Vec<_>>();
    ordered.sort_by_key(|(index, signal)| (fleet_health_status_rank(signal.status), *index));

    let overflow = ordered.len() > inner_height;
    let visible_signal_count = if overflow {
        inner_height.saturating_sub(1)
    } else {
        ordered.len().min(inner_height)
    };

    let mut lines = ordered
        .iter()
        .take(visible_signal_count)
        .map(|(_, signal)| fleet_health_signal_line(signal, inner_width))
        .collect::<Vec<_>>();

    if overflow && inner_height > 0 {
        let hidden = &ordered[visible_signal_count..];
        let hidden_non_ok = hidden
            .iter()
            .filter(|(_, signal)| signal.status != FleetHealthStatus::Ok)
            .count();
        let detail = format!("… +{} more ({} warn)", hidden.len(), hidden_non_ok);
        let color = if hidden_non_ok == 0 {
            Color::DarkGray
        } else {
            Color::Yellow
        };
        lines.push(Line::from(Span::styled(
            truncate(&detail, inner_width),
            Style::default().fg(color).add_modifier(Modifier::BOLD),
        )));
    }

    lines
}

fn fleet_health_signal_line(signal: &FleetHealthSignal, inner_width: usize) -> Line<'static> {
    const MARKER_WIDTH: usize = 2;
    const LABEL_WIDTH: usize = 18;

    let color = match signal.status {
        FleetHealthStatus::Ok => Color::Green,
        FleetHealthStatus::Warn => Color::Yellow,
        FleetHealthStatus::Blocked => Color::Red,
        FleetHealthStatus::Unknown => Color::Gray,
    };
    let label_width = LABEL_WIDTH.min(inner_width.saturating_sub(MARKER_WIDTH));
    let detail_width = inner_width.saturating_sub(MARKER_WIDTH + label_width);
    let label = truncate(&signal.label, label_width.saturating_sub(1));

    Line::from(vec![
        Span::styled(
            format!("{} ", signal.status.marker()),
            Style::default().fg(color).add_modifier(Modifier::BOLD),
        ),
        Span::styled(
            format!("{label:<label_width$}"),
            Style::default().add_modifier(Modifier::BOLD),
        ),
        Span::styled(
            truncate(&signal.detail, detail_width),
            Style::default().fg(Color::DarkGray),
        ),
    ])
}

fn fleet_health_status_rank(status: FleetHealthStatus) -> u8 {
    match status {
        FleetHealthStatus::Blocked => 0,
        FleetHealthStatus::Warn => 1,
        FleetHealthStatus::Unknown => 2,
        FleetHealthStatus::Ok => 3,
    }
}

fn draw_mc_failure_board(frame: &mut Frame, area: Rect, entries: &[FailureEntry], focused: bool) {
    let title = format!(" Failure board 24h ({}) ", entries.len());
    let block = panel_block(&title, focused, Color::Red);
    let lines: Vec<Line> = if entries.is_empty() {
        vec![Line::from(Span::styled(
            "no failures in window",
            Style::default().fg(Color::DarkGray),
        ))]
    } else {
        entries
            .iter()
            .flat_map(|entry| {
                vec![
                    Line::from(vec![
                        Span::styled(
                            format!("✗ {} ", entry.run_id),
                            Style::default().fg(Color::Red).add_modifier(Modifier::BOLD),
                        ),
                        Span::raw(format!("{} / {}", entry.agent, entry.skill)),
                    ]),
                    Line::from(Span::styled(
                        format!("  {} · {}", entry.reason, entry.age_label),
                        Style::default().fg(Color::DarkGray),
                    )),
                ]
            })
            .collect()
    };
    let para = Paragraph::new(lines)
        .block(block)
        .wrap(Wrap { trim: false });
    frame.render_widget(para, area);
}

fn draw_mc_action_queue(frame: &mut Frame, area: Rect, items: &[ActionQueueItem], focused: bool) {
    let title = format!(" Operator action queue ({}) ", items.len());
    let block = panel_block(&title, focused, Color::White);
    // Inner text width = panel minus the left/right border cells.
    let inner_width = area.width.saturating_sub(2) as usize;
    let lines: Vec<Line> = if items.is_empty() {
        vec![Line::from(Span::styled(
            "nothing to press",
            Style::default().fg(Color::DarkGray),
        ))]
    } else {
        items
            .iter()
            .map(|item| {
                let priority_color = match item.priority {
                    ActionPriority::Critical => Color::Red,
                    ActionPriority::High => Color::Yellow,
                    ActionPriority::Normal => Color::Cyan,
                };
                let kind_label = match item.kind {
                    ActionQueueKind::StalledRun => "stall",
                    ActionQueueKind::Failure => "fail",
                    ActionQueueKind::Polarize => "polarize",
                    ActionQueueKind::ReportReady => "report",
                };
                let priority_prefix = format!("{} ", item.priority.marker());
                let kind_prefix = format!("[{kind_label}] ");
                // Truncate at the panel width so long summaries end with an
                // ellipsis instead of wrapping past the panel edge; .max(1)
                // keeps degenerate widths from producing an empty span.
                let summary_width = inner_width
                    .saturating_sub(priority_prefix.chars().count() + kind_prefix.chars().count())
                    .max(1);
                Line::from(vec![
                    Span::styled(
                        priority_prefix,
                        Style::default()
                            .fg(priority_color)
                            .add_modifier(Modifier::BOLD),
                    ),
                    Span::styled(kind_prefix, Style::default().fg(Color::DarkGray)),
                    Span::raw(truncate(&item.summary, summary_width)),
                ])
            })
            .collect()
    };
    let para = Paragraph::new(lines)
        .block(block)
        .wrap(Wrap { trim: false });
    frame.render_widget(para, area);
}

fn draw_mc_quality_footer(
    frame: &mut Frame,
    area: Rect,
    quality: &DataQuality,
    generated_at: &str,
) {
    let root_label = quality
        .artifact_root
        .as_ref()
        .map(|root| root.to_string_lossy().into_owned())
        .unwrap_or_else(|| "unset".to_string());
    let mut lines = vec![
        Line::from(vec![
            Span::styled(
                "artifact root: ",
                Style::default()
                    .fg(Color::DarkGray)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(root_label),
        ]),
        Line::from(vec![
            Span::styled(
                "generated at:  ",
                Style::default()
                    .fg(Color::DarkGray)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(generated_at.to_string()),
        ]),
    ];
    if !quality.artifact_root_present {
        lines.push(Line::from(Span::styled(
            "artifact root missing — only live runs feed this dashboard",
            Style::default().fg(Color::Yellow),
        )));
    }
    if quality.capped {
        lines.push(Line::from(Span::styled(
            "meta scan capped — older history may not be folded",
            Style::default().fg(Color::Yellow),
        )));
    }
    if quality.parse_failures > 0 {
        lines.push(Line::from(Span::styled(
            format!(
                "{} meta.json parse failures skipped",
                quality.parse_failures
            ),
            Style::default().fg(Color::Yellow),
        )));
    }
    let para = Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Mission Control receipt "),
        )
        .wrap(Wrap { trim: false });
    frame.render_widget(para, area);
}

fn panel_block(title: &str, focused: bool, accent: Color) -> Block<'_> {
    let style = if focused {
        Style::default().fg(accent).add_modifier(Modifier::BOLD)
    } else {
        Style::default().fg(accent)
    };
    Block::default()
        .borders(Borders::ALL)
        .border_style(style)
        .title(Span::styled(
            format!(" {} ", title.trim()),
            Style::default().fg(accent).add_modifier(Modifier::BOLD),
        ))
}

fn truncate(value: &str, max: usize) -> String {
    if max == 0 {
        return String::new();
    }
    if value.chars().count() <= max {
        value.to_string()
    } else {
        let mut out: String = value.chars().take(max.saturating_sub(1)).collect();
        out.push('…');
        out
    }
}

fn format_duration_seconds(seconds: f64) -> String {
    if seconds < 60.0 {
        format!("{:.0}s", seconds)
    } else if seconds < 3600.0 {
        format!("{:.1}m", seconds / 60.0)
    } else {
        format!("{:.1}h", seconds / 3600.0)
    }
}

fn draw_stat_strip(frame: &mut Frame, area: Rect, cards: [(&str, Vec<String>, Color); 3]) {
    let columns = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Ratio(1, 3); 3])
        .split(area);

    for ((title, lines, accent), column) in cards.into_iter().zip(columns.iter().copied()) {
        let content = lines.into_iter().map(Line::from).collect::<Vec<_>>();
        let panel = Paragraph::new(content)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(title)
                    .border_style(Style::default().fg(accent)),
            )
            .style(Style::default().fg(Color::White))
            .wrap(Wrap { trim: false });
        frame.render_widget(panel, column);
    }
}

fn status_style(kind: RunKind) -> Style {
    match kind {
        RunKind::Active => Style::default()
            .fg(Color::Green)
            .add_modifier(Modifier::BOLD),
        RunKind::Recent | RunKind::Completed => Style::default().fg(Color::Blue),
        RunKind::Failed => Style::default().fg(Color::Red).add_modifier(Modifier::BOLD),
        RunKind::Stalled => Style::default()
            .fg(Color::Yellow)
            .add_modifier(Modifier::BOLD),
        RunKind::Paused => Style::default().fg(Color::Magenta),
        RunKind::Unknown => Style::default().fg(Color::Gray),
    }
}

fn draw_help_overlay(frame: &mut Frame, app: &App) {
    let area = centered_rect(72, 70, frame.area());
    frame.render_widget(Clear, area);
    let lines = app
        .help_lines()
        .into_iter()
        .map(Line::from)
        .collect::<Vec<_>>();
    let help = Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Help")
                .border_style(Style::default().fg(Color::Yellow)),
        )
        .wrap(Wrap { trim: false });
    frame.render_widget(help, area);
}

fn draw_search_overlay(frame: &mut Frame, app: &App) {
    let area = centered_rect(64, 24, frame.area());
    frame.render_widget(Clear, area);
    let query = if app.search_query.is_empty() {
        "type to filter runs".to_string()
    } else {
        app.search_query.clone()
    };
    let lines = vec![
        Line::from(vec![
            Span::styled("/", Style::default().fg(Color::Yellow)),
            Span::raw(query),
        ]),
        Line::from(""),
        Line::from(format!(
            "{} runs visible in {} scope",
            app.runs.len(),
            app.queue_scope.label()
        )),
        Line::from("Enter/Esc closes. Ctrl+L clears search from browse."),
    ];
    let search = Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Run search")
                .border_style(Style::default().fg(Color::Cyan)),
        )
        .wrap(Wrap { trim: false });
    frame.render_widget(search, area);
}

fn draw_prompt_overlay(frame: &mut Frame, app: &App) {
    let area = centered_rect(76, 60, frame.area());
    frame.render_widget(Clear, area);
    let lines = app
        .prompt_edit_lines()
        .into_iter()
        .map(Line::from)
        .collect::<Vec<_>>();
    let prompt = Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Prompt editor")
                .border_style(Style::default().fg(Color::Magenta)),
        )
        .wrap(Wrap { trim: false });
    frame.render_widget(prompt, area);
}

fn draw_error_overlay(frame: &mut Frame, app: &App) {
    let area = centered_rect(76, 56, frame.area());
    frame.render_widget(Clear, area);
    let lines = app
        .error_lines()
        .into_iter()
        .map(Line::from)
        .collect::<Vec<_>>();
    let error = Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Launch error")
                .border_style(Style::default().fg(Color::Red)),
        )
        .wrap(Wrap { trim: false });
    frame.render_widget(error, area);
}

fn draw_artifact_overlay(frame: &mut Frame, app: &App) {
    let area = centered_rect(82, 72, frame.area());
    frame.render_widget(Clear, area);
    let lines = app
        .artifact_lines()
        .into_iter()
        .map(Line::from)
        .collect::<Vec<_>>();
    let artifact = Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Artifact viewer")
                .border_style(Style::default().fg(Color::Green)),
        )
        .wrap(Wrap { trim: false });
    frame.render_widget(artifact, area);
}

fn centered_rect(percent_x: u16, percent_y: u16, area: Rect) -> Rect {
    let popup_layout = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage((100 - percent_y) / 2),
            Constraint::Percentage(percent_y),
            Constraint::Percentage((100 - percent_y) / 2),
        ])
        .split(area);

    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - percent_x) / 2),
            Constraint::Percentage(percent_x),
            Constraint::Percentage((100 - percent_x) / 2),
        ])
        .split(popup_layout[1])[1]
}

pub fn draw_client_drift_overlay(frame: &mut Frame, area: Rect, halt: &crate::launch::VerifyHalt) {
    let block = Block::default()
        .title(" Client Drift Detected ")
        .borders(Borders::ALL)
        .style(Style::default().fg(Color::Red).bg(Color::Black));
    let area = ratatui::layout::Layout::default()
        .direction(ratatui::layout::Direction::Vertical)
        .constraints([
            ratatui::layout::Constraint::Percentage(20),
            ratatui::layout::Constraint::Percentage(60),
            ratatui::layout::Constraint::Percentage(20),
        ])
        .split(area)[1];
    let area = ratatui::layout::Layout::default()
        .direction(ratatui::layout::Direction::Horizontal)
        .constraints([
            ratatui::layout::Constraint::Percentage(10),
            ratatui::layout::Constraint::Percentage(80),
            ratatui::layout::Constraint::Percentage(10),
        ])
        .split(area)[1];

    let mut lines = vec![
        ratatui::text::Line::from(
            "Dispatch halted because client configuration does not route through rmcp-mux.",
        ),
        ratatui::text::Line::from(""),
    ];

    match halt {
        crate::launch::VerifyHalt::Drift(servers) => {
            lines.push(ratatui::text::Line::from("Non-mux servers found:"));
            for s in servers {
                lines.push(ratatui::text::Line::from(format!(
                    "  {} ({}:{})",
                    s.client, s.path, s.line
                )));
            }
        }
        crate::launch::VerifyHalt::Timeout => {
            lines.push(ratatui::text::Line::from(
                "Timeout waiting for verify response.",
            ));
        }
    }

    lines.push(ratatui::text::Line::from(""));
    lines.push(ratatui::text::Line::from(ratatui::text::Span::styled(
        "Press F to auto-fix (spawns wizard)",
        Style::default().add_modifier(Modifier::BOLD),
    )));
    lines.push(ratatui::text::Line::from("Press Esc to cancel."));

    let para = Paragraph::new(lines)
        .block(block)
        .wrap(Wrap { trim: false });
    frame.render_widget(ratatui::widgets::Clear, area);
    frame.render_widget(para, area);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::app::{DispatchFocus, LaunchFocus, QueueScope};
    use crate::config::AppConfig;
    use crate::launch::{LaunchKind, LaunchRuntime};
    use crate::state::{ControlPlaneState, RenderedRun, RunKind, RunSnapshot};
    use ratatui::Terminal;
    use ratatui::backend::TestBackend;
    use std::time::Duration;

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
            launch_history: vec!["vc workflow --agent codex".to_string()],
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

    fn render_to_string(app: &App) -> String {
        let backend = TestBackend::new(120, 40);
        let mut terminal = Terminal::new(backend).unwrap();
        terminal.draw(|frame| draw(frame, app)).unwrap();
        terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>()
    }

    #[test]
    fn monitor_tab_renders_monitor_surface() {
        let app = sample_app();
        let rendered = render_to_string(&app);

        assert!(rendered.contains("Monitor pulse"));
        assert!(rendered.contains("Live · this workspace"));
        assert!(rendered.contains("Run dossier"));
        assert!(rendered.contains("Recent timeline"));
        assert!(!rendered.contains("Dispatch playbook"));
        assert!(rendered.contains("active 2"));
        assert!(!rendered.contains("unknown unknown"));
    }

    #[test]
    fn dispatch_tab_renders_dispatch_surface() {
        let mut app = sample_app();
        app.set_active_tab(AppTab::Dispatch);

        let rendered = render_to_string(&app);

        assert!(rendered.contains("Dispatch deck"));
        assert!(rendered.contains("Dispatch playbook"));
        assert!(rendered.contains("Launch trail"));
        assert!(!rendered.contains("Primary actions"));
    }

    #[test]
    fn monitor_tab_renders_mux_panel_when_summaries_exist() {
        use crate::mux::{MuxStatusSnapshot, MuxSummary};
        use std::path::PathBuf;
        let healthy_json = r#"{
            "service_name": "general-memory",
            "server_status": "Running",
            "restarts": 0,
            "connected_clients": 2,
            "active_clients": 1,
            "max_active_clients": 5,
            "pending_requests": 0,
            "cached_initialize": true,
            "initializing": false,
            "queue_depth": 0,
            "child_pid": 4242,
            "max_request_bytes": 1048576,
            "restart_backoff_ms": 1000,
            "restart_backoff_max_ms": 30000,
            "max_restarts": 5
        }"#;
        let failed_json = r#"{
            "service_name": "brave-search",
            "server_status": {"Failed": "max restarts reached"},
            "restarts": 5,
            "connected_clients": 0,
            "active_clients": 0,
            "max_active_clients": 5,
            "pending_requests": 0,
            "cached_initialize": false,
            "initializing": false,
            "queue_depth": 0,
            "max_request_bytes": 1048576,
            "restart_backoff_ms": 1000,
            "restart_backoff_max_ms": 30000,
            "max_restarts": 5
        }"#;

        let mut app = sample_app();
        app.mux_summaries = vec![
            MuxSummary::from_path_and_result(
                PathBuf::from("/tmp/memory.json"),
                MuxStatusSnapshot::from_json(healthy_json),
            ),
            MuxSummary::from_path_and_result(
                PathBuf::from("/tmp/brave.json"),
                MuxStatusSnapshot::from_json(failed_json),
            ),
        ];

        let rendered = render_to_string(&app);

        assert!(
            rendered.contains("rmcp-mux"),
            "panel title must mark this as the rmcp-mux surface"
        );
        assert!(
            rendered.contains("(2)") || rendered.contains("(1/2 need attention)"),
            "panel must surface either total count or attention header: {rendered}"
        );
        assert!(
            rendered.contains("general-memory"),
            "healthy service must render verbatim"
        );
        assert!(
            rendered.contains("brave-search"),
            "failed service must render verbatim"
        );
        assert!(
            rendered.contains("Failed"),
            "failed status must surface in the panel"
        );
        // Existing Monitor sections must still be present underneath.
        assert!(rendered.contains("Run dossier"));
        assert!(rendered.contains("Recent timeline"));
    }

    #[test]
    fn monitor_tab_renders_polarize_intent_panel() {
        use crate::polarize::{PolarizeBand, PolarizeIntent};
        use std::path::PathBuf;

        let mut app = sample_app();
        app.polarize_intents = vec![PolarizeIntent {
            band: PolarizeBand::Doctrine,
            score: 14,
            run_id: "polr-123".to_string(),
            prism_path: PathBuf::from("/tmp/polarize/polr-123/prism.json"),
        }];

        let rendered = render_to_string(&app);

        assert!(rendered.contains("polarize"));
        assert!(rendered.contains("doctrine"));
        assert!(rendered.contains("score 14"));
        assert!(rendered.contains("polr-123"));
    }

    #[test]
    fn monitor_tab_skips_mux_panel_when_summaries_empty() {
        let app = sample_app();
        let rendered = render_to_string(&app);
        assert!(
            !rendered.contains("rmcp-mux"),
            "no panel should render when there are no mux summaries"
        );
    }

    #[test]
    fn controls_tab_renders_controls_surface() {
        let mut app = sample_app();
        app.set_active_tab(AppTab::Controls);

        let rendered = render_to_string(&app);

        assert!(rendered.contains("Action deck"));
        assert!(rendered.contains("Primary actions"));
        assert!(rendered.contains("Artifact access"));
        assert!(rendered.contains("Selected timeline"));
        assert!(!rendered.contains("Dispatch playbook"));
        assert!(rendered.contains("contextual actions"));
        assert!(app.deep_actions().len() < 12);
    }
}
