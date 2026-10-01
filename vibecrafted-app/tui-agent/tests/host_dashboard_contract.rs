//! Host projection acceptance. Guest process/organ preservation and Frame chrome
//! routing require the separate installed Frame scenario walkaround.
use control_core::{ControlPlane, FrameSessionInventory};
use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::{Terminal, backend::TestBackend};
use serde_json::json;
use std::{collections::BTreeMap, fs, path::Path, time::Duration};
use voc::{
    config::AppConfig,
    host::{
        DoctorReport, HostDashboard, HostRoute, HostSnapshot, RuntimeIdentity, draw,
        project_catalog, read_active_runtime,
    },
    launch::Presentation,
    observe::ConsoleView,
};

fn config(root: &Path) -> AppConfig {
    AppConfig {
        state_root: root.join("control_plane"),
        command_deck: root.join("never-invoked"),
        repo: root.join("arbitrary-cwd"),
        presentation: Presentation::Headless,
        tick_rate: Duration::from_millis(50),
        no_verify_gate: false,
        server: "http://127.0.0.1:1".into(),
        view: ConsoleView::Host(HostRoute::Dashboard),
    }
}
fn tree(root: &Path) -> BTreeMap<String, Vec<u8>> {
    let mut files = BTreeMap::new();
    if root.exists() {
        for entry in fs::read_dir(root).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                files.extend(tree(&path));
            } else {
                files.insert(path.to_string_lossy().into_owned(), fs::read(path).unwrap());
            }
        }
    }
    files
}
fn screen(host: &HostDashboard, cfg: &AppConfig) -> String {
    let mut term = Terminal::new(TestBackend::new(150, 40)).unwrap();
    term.draw(|f| draw(f, host, cfg)).unwrap();
    term.backend()
        .buffer()
        .content()
        .iter()
        .map(|cell| cell.symbol())
        .collect::<String>()
}
fn catalog(root: &Path) {
    let dir = root.join("workspaces");
    fs::create_dir_all(dir.join("sessions")).unwrap();
    let mut records = serde_json::Map::new();
    for (i, label) in [
        "Vibecrafted",
        "Operator",
        "Workflow",
        "Research",
        "Vibecrafted",
    ]
    .iter()
    .enumerate()
    {
        let id = format!("0198f84e-1234-7abc-8def-1234567890a{i}");
        records.insert(id.clone(), json!({"schema":"vibecrafted.workspace.v1", "workspace_id":id, "display_label":label, "canonical_root":format!("/work/{i}/{label}"), "status":"active", "updated_at":"2026-09-29T00:00:00Z"}));
        let sid = format!("0198f84e-2222-7abc-8def-1234567890a{i}");
        fs::write(dir.join("sessions").join(format!("{sid}.json")), json!({"schema":"vibecrafted.workspace-session.v1", "session_id":sid, "workspace_id":id, "workspace_instance_id":"0198f84e-3333-7abc-8def-1234567890ab", "updated_at":"2026-09-29T00:00:00Z", "attachments":[{"runtime":"vc-frame","runtime_session_id":format!("guest-{i}"),"state":"live","socket_dir":"/fixture/sockets"}]}).to_string()).unwrap();
    }
    fs::write(dir.join("catalog.json"), json!({"schema":"vibecrafted.workspace-catalog.v1", "updated_at":"2026-09-29T00:00:00Z", "selected_workspace_id":null,"workspaces":records}).to_string()).unwrap();
}
fn write_run(runs: &Path, run_id: &str, state: &str) {
    let now = chrono::Utc::now().to_rfc3339();
    fs::write(runs.join(format!("{run_id}.json")), json!({"run_id":run_id, "state":state,"agent":"codex","skill":"workflow","root":"/another/project", "updated_at":now,"started_at":now,"source":"fixture-receipt","mode":"headless","operator_session":"","latest_report":"","latest_transcript":"","last_error":"","health":"active","lock_present":false,"worker_pid":std::process::id(),"launcher_pid":std::process::id(),"worker_alive":state == "running"}).to_string()).unwrap();
}
fn marked(host: &HostDashboard, cfg: &AppConfig) -> Vec<String> {
    host.lines(cfg)
        .into_iter()
        .filter(|line| line.starts_with('▶'))
        .collect()
}

#[test]
fn scene_a_empty_host_has_real_routes_and_no_writes_even_when_home_is_absent() {
    let tmp = tempfile::tempdir().unwrap();
    let cfg = config(tmp.path());
    let before = tree(tmp.path());
    let mut host = HostDashboard::new(HostRoute::Dashboard);
    host.apply(HostSnapshot::load(&cfg));
    let rendered = screen(&host, &cfg);
    for expected in [
        "Open project",
        "Live workspaces 0",
        "Active runs 0",
        "Server",
        "Doctor",
        "LIVE 0",
    ] {
        assert!(rendered.contains(expected), "missing {expected}");
    }
    assert!(!rendered.contains("Select a workspace"));
    let mut views = std::collections::BTreeSet::new();
    for route in HostRoute::ALL {
        host.navigate(route);
        views.insert(host.lines(&cfg).join("\n"));
    }
    assert_eq!(views.len(), 6);
    assert_eq!(tree(tmp.path()), before);
    assert!(
        !cfg.state_root.exists(),
        "viewing a new host must not create control_plane"
    );
}

#[test]
fn scenes_b_e_f_catalog_is_not_live_and_names_do_not_route() {
    let tmp = tempfile::tempdir().unwrap();
    let cfg = config(tmp.path());
    catalog(&cfg.state_root);
    let before = tree(tmp.path());
    let projection = ControlPlane::from_control_plane_home(&cfg.state_root)
        .load_workspace_projection()
        .unwrap();
    let empty = project_catalog(&projection, &FrameSessionInventory::default());
    assert_eq!(empty.len(), 5);
    assert!(
        empty.iter().all(|p| p.sessions.is_empty()),
        "saved entries are not live"
    );
    // One detached guest still has its server socket; client count is irrelevant.
    let live = project_catalog(
        &projection,
        &FrameSessionInventory::from_running([("/fixture/sockets", "guest-0")]),
    );
    assert_eq!(live.iter().map(|p| p.sessions.len()).sum::<usize>(), 1);
    let twins = live
        .iter()
        .filter(|p| p.label == "Vibecrafted")
        .collect::<Vec<_>>();
    assert_eq!(twins.len(), 2);
    assert_ne!(twins[0].id, twins[1].id);
    assert_ne!(twins[0].root, twins[1].root);
    assert_eq!(tree(tmp.path()), before);
}

#[test]
fn scenes_c_d_host_navigation_preserves_its_draft_and_does_not_touch_guest_records() {
    let tmp = tempfile::tempdir().unwrap();
    let cfg = config(tmp.path());
    catalog(&cfg.state_root);
    let before = tree(tmp.path());
    let mut host = HostDashboard::new(HostRoute::Dashboard);
    host.apply(HostSnapshot::load(&cfg));
    host.project_input = Some("/work/unsent draft".into());
    for route in HostRoute::ALL {
        host.navigate(route);
        screen(&host, &cfg);
    }
    assert_eq!(host.project_input.as_deref(), Some("/work/unsent draft"));
    assert_eq!(tree(tmp.path()), before);
}

#[test]
fn run_counts_and_named_evidence_follow_one_projection_through_stop_and_settlement() {
    let tmp = tempfile::tempdir().unwrap();
    let cfg = config(tmp.path());
    let runs = cfg.state_root.join("runs");
    fs::create_dir_all(&runs).unwrap();
    for state in ["running", "stopped", "completed", "failed"] {
        let now = chrono::Utc::now().to_rfc3339();
        fs::write(runs.join("fixture-run.json"), json!({"run_id":"fixture-run", "state":state,"agent":"codex","skill":"workflow","root":"/another/project", "updated_at":now,"started_at":now,"source":"fixture-receipt","mode":"headless","operator_session":"","latest_report":"","latest_transcript":"","last_error":"","health":"active","lock_present":false,"worker_pid":std::process::id(),"launcher_pid":std::process::id(),"worker_alive":state == "running"}).to_string()).unwrap();
        let before = tree(tmp.path());
        let mut host = HostDashboard::new(HostRoute::ActiveRuns);
        let snapshot = HostSnapshot::load(&cfg);
        let count = snapshot.live_count();
        assert_eq!(count, usize::from(state == "running"), "{state}");
        host.apply(snapshot);
        let rendered = screen(&host, &cfg);
        assert!(rendered.contains(&format!("Active runs · {count}")));
        assert!(rendered.contains(&format!("Active runs {count}")));
        assert!(rendered.contains(&format!("LIVE {count}")));
        host.navigate(HostRoute::Voc);
        let rows = host.lines(&cfg).join("\n");
        assert!(rows.contains(state));
        assert!(rows.contains("fixture-receipt"));
        assert!(!rows.contains("HEALTH !!"));
        assert_eq!(tree(tmp.path()), before);
    }
}

#[test]
fn projection_unknown_does_not_present_a_healthy_zero_or_destroy_ui_state() {
    let tmp = tempfile::tempdir().unwrap();
    let cfg = config(tmp.path());
    catalog(&cfg.state_root);
    let mut host = HostDashboard::new(HostRoute::Projects);
    host.apply(HostSnapshot::load(&cfg));
    let selected_id = host.snapshot.as_ref().unwrap().projects[0].id.clone();
    fs::write(cfg.state_root.join("workspaces/catalog.json"), "broken").unwrap();
    host.apply(HostSnapshot::load(&cfg));
    assert_eq!(host.snapshot.as_ref().unwrap().projects[0].id, selected_id);
    host.navigate(HostRoute::Dashboard);
    let text = host.lines(&cfg).join("\n");
    assert!(text.contains("Projection unknown"));
    assert!(!text.contains("Live workspaces 0"));
    host.navigate(HostRoute::Doctor);
    assert!(host.lines(&cfg).join("\n").contains("invalid JSON"));
}

#[test]
fn run_views_mark_exactly_the_selected_row_and_list_less_routes_select_nothing() {
    let tmp = tempfile::tempdir().unwrap();
    let cfg = config(tmp.path());
    let runs = cfg.state_root.join("runs");
    fs::create_dir_all(&runs).unwrap();
    for id in ["fixture-run-a", "fixture-run-b"] {
        write_run(&runs, id, "running");
    }
    let before = tree(tmp.path());
    let mut host = HostDashboard::new(HostRoute::ActiveRuns);
    host.apply(HostSnapshot::load(&cfg));
    let order = host.snapshot.as_ref().unwrap().runs.active_runs.iter();
    let order = order.map(|r| r.run_id.clone()).collect::<Vec<_>>();
    assert_eq!(order.len(), 2);
    assert_eq!(marked(&host, &cfg).len(), 1);
    assert!(
        host.lines(&cfg)
            .iter()
            .any(|l| l.starts_with("Selected:") && l.contains(&order[0]))
    );
    let rendered = screen(&host, &cfg);
    assert!(rendered.contains("▶ running"));
    assert!(rendered.contains("g open run"));
    host.move_selection(1);
    assert!(
        host.lines(&cfg)
            .iter()
            .any(|l| l.starts_with("Selected:") && l.contains(&order[1]))
    );
    host.move_selection(5);
    assert_eq!(host.selected, 1, "selection clamps at the last row");
    host.apply(HostSnapshot::load(&cfg));
    assert!(
        host.lines(&cfg)
            .iter()
            .any(|l| l.starts_with("Selected:") && l.contains(&order[1])),
        "a refresh keeps the selected run by run_id"
    );
    host.navigate(HostRoute::Voc);
    assert_eq!(marked(&host, &cfg).len(), 1);
    for route in [HostRoute::Dashboard, HostRoute::Config, HostRoute::Doctor] {
        host.navigate(route);
        host.move_selection(1);
        host.apply(HostSnapshot::load(&cfg));
        assert_eq!(host.selected, 0, "{route:?} has no list to move through");
        let rendered = screen(&host, &cfg);
        assert!(!rendered.contains('▶'), "{route:?}");
        assert!(!rendered.contains("g open run"), "{route:?}");
        assert_eq!(
            rendered.contains("d run doctor"),
            route == HostRoute::Doctor,
            "{route:?}"
        );
    }
    assert_eq!(tree(tmp.path()), before);
}

#[test]
fn wheel_scrolls_like_page_keys_instead_of_vanishing_into_mouse_capture() {
    let tmp = tempfile::tempdir().unwrap();
    let cfg = config(tmp.path());
    catalog(&cfg.state_root);
    let mut host = HostDashboard::new(HostRoute::Projects);
    host.apply(HostSnapshot::load(&cfg));
    let mouse = |kind, column, row| MouseEvent {
        kind,
        column,
        row,
        modifiers: KeyModifiers::NONE,
    };
    host.handle_mouse(&cfg, mouse(MouseEventKind::ScrollDown, 20, 10));
    assert_eq!(host.offset, 3);
    host.handle_mouse(&cfg, mouse(MouseEventKind::ScrollUp, 20, 10));
    host.handle_mouse(&cfg, mouse(MouseEventKind::ScrollUp, 20, 10));
    assert_eq!(host.offset, 0);
    let last = host.lines(&cfg).len() - 1;
    for _ in 0..100 {
        host.handle_mouse(&cfg, mouse(MouseEventKind::ScrollDown, 20, 10));
    }
    assert_eq!(host.offset, last, "the wheel clamps where PgDn clamps");
    host.scroll(&cfg, -8);
    assert_eq!(host.offset, last - 8);
    assert_eq!(
        host.selected, 0,
        "the wheel scrolls; it never moves the selection"
    );
    host.handle_mouse(&cfg, mouse(MouseEventKind::Down(MouseButton::Left), 1, 1));
    assert_eq!(host.route, HostRoute::Dashboard, "tab clicks still route");
}

#[test]
fn config_names_the_active_runtime_generation_or_says_unavailable() {
    let tmp = tempfile::tempdir().unwrap();
    let cfg = config(tmp.path());
    let pointer = tmp.path().join("runtime/active.json");
    fs::create_dir_all(pointer.parent().unwrap()).unwrap();
    let root = tmp.path().join("runtime/releases/4.3.1+gfixture");
    fs::write(&pointer, json!({"schema":"vibecrafted.active-runtime.v1", "version":"4.3.1+gfixture", "runtime_root":root, "app_root":""}).to_string()).unwrap();
    let mut host = HostDashboard::new(HostRoute::Config);
    host.apply(HostSnapshot::load(&cfg));
    host.snapshot.as_mut().unwrap().runtime = RuntimeIdentity::load(Some(pointer.clone()));
    let text = host.lines(&cfg).join("\n");
    assert!(text.contains(&format!(
        "Active runtime: 4.3.1+gfixture · generation {} · source: {}",
        root.display(),
        pointer.display()
    )));
    assert!(text.contains("Values are read-only"));
    for (body, reason) in [
        ("{broken", "invalid JSON"),
        (
            r#"{"schema":"other","runtime_root":"/x"}"#,
            "not vibecrafted.active-runtime.v1",
        ),
        (
            r#"{"schema":"vibecrafted.active-runtime.v1","runtime_root":"relative"}"#,
            "runtime_root missing or not absolute",
        ),
    ] {
        fs::write(&pointer, body).unwrap();
        host.snapshot.as_mut().unwrap().runtime = RuntimeIdentity::load(Some(pointer.clone()));
        let text = host.lines(&cfg).join("\n");
        assert!(
            text.contains(&format!("Active runtime: unavailable · {reason}")),
            "{text}"
        );
        assert!(!text.contains("4.3.1"), "no guessed version: {text}");
    }
    fs::remove_file(&pointer).unwrap();
    assert_eq!(read_active_runtime(&pointer), Err("pointer absent".into()));
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(tmp.path().join("elsewhere.json"), &pointer).unwrap();
        assert_eq!(
            read_active_runtime(&pointer),
            Err("active.json is a symlink".into())
        );
    }
    assert!(
        RuntimeIdentity::load(None)
            .line()
            .starts_with("Active runtime: unavailable")
    );
}

#[test]
fn doctor_route_summarizes_installed_checks_and_keeps_the_projection_disclaimer() {
    let tmp = tempfile::tempdir().unwrap();
    let cfg = config(tmp.path());
    let mut host = HostDashboard::new(HostRoute::Doctor);
    host.apply(HostSnapshot::load(&cfg));
    let text = host.lines(&cfg).join("\n");
    assert!(text.contains("not an installation certificate"));
    assert!(text.contains(&format!(
        "INSTALLED DOCTOR · d: run {} doctor --json",
        cfg.command_deck.display()
    )));
    assert!(text.contains("Not run in this view yet."));
    let payload = json!({"ok":2, "warnings":0, "failures":1, "healthy":false, "findings":[
        {"level":"ok","component":"runtime","message":"ready"},
        {"level":"ok","component":"launchers","message":"ready"},
        {"level":"fail","component":"runtime-receipt","message":"receipt/disk live damage"}
    ], "delivery_receipt":{"schema":"fixture"}});
    host.doctor = Some(DoctorReport::from_output(
        "vibecrafted doctor --json".into(),
        "exit status: 1",
        payload.to_string().as_bytes(),
        b"",
    ));
    let rendered = screen(&host, &cfg);
    assert!(rendered.contains("2 ok · 0 warnings · 1 failures"));
    assert!(rendered.contains("fail runtime-receipt · receipt/disk live damage"));
    assert!(rendered.contains("not an installation certificate"));
    assert!(
        !rendered.contains("delivery_receipt"),
        "never a raw JSON dump"
    );
    host.doctor = Some(DoctorReport::from_output(
        "vibecrafted doctor --json".into(),
        "exit status: 2",
        b"",
        b"vibecrafted: error: unrecognized arguments: --json\n",
    ));
    let text = host.lines(&cfg).join("\n");
    assert!(text.contains(
        "Doctor unavailable · exit status: 2 · vibecrafted: error: unrecognized arguments: --json"
    ));
    assert!(text.contains("Run manually: vibecrafted doctor --json"));
}

#[test]
fn host_cli_routes_are_explicit_and_keep_cwd_out_of_the_projection_contract() {
    for (value, route) in [
        ("host", HostRoute::Dashboard),
        ("host-runs", HostRoute::ActiveRuns),
        ("host-config", HostRoute::Config),
        ("host-doctor", HostRoute::Doctor),
        ("host-projects", HostRoute::Projects),
        ("host-voc", HostRoute::Voc),
    ] {
        let parsed = voc::config::parse_args_from(["--view".into(), value.into()]).unwrap();
        assert_eq!(parsed.view, ConsoleView::Host(route));
    }
    assert!(
        voc::config::parse_args_from([
            "--view".into(),
            "host".into(),
            "--attention-working-rule".into()
        ])
        .is_err()
    );
}

#[test]
fn active_runs_are_grouped_single_rows_with_fresh_attention_and_identity_selection() {
    let tmp = tempfile::tempdir().unwrap();
    let cfg = config(tmp.path());
    let runs = cfg.state_root.join("runs");
    fs::create_dir_all(&runs).unwrap();
    write_run(&runs, "template", "running");
    let mut snapshot = HostSnapshot::load(&cfg);
    let template = snapshot.runs.active_runs[0].clone();
    let now = chrono::Utc::now();
    snapshot.sampled_at = now.to_rfc3339();
    let run = |id: &str, root: &str, minutes: i64, state: &str, skill: &str| {
        let mut r = template.clone();
        r.run_id = id.into();
        r.root = root.into();
        r.updated_at = (now - chrono::Duration::minutes(minutes)).to_rfc3339();
        r.started_at = (now - chrono::Duration::minutes(125)).to_rfc3339();
        r.state = state.into();
        r.skill = skill.into();
        r.health = if state == "running" {
            "active"
        } else {
            "stalled"
        }
        .into();
        r.liveness = "pid_alive".into();
        r.last_error = format!("error-{id}");
        r
    };
    snapshot.runs.active_runs = vec![
        run(
            "v-old",
            "/fixture/.vibecrafted/worktrees/vetcoders/vibecrafted/2026_1001/red-tests",
            30,
            "running",
            "implement",
        ),
        run(
            "c-live",
            "/Volumes/work/vetcoders/codescribe/",
            20,
            "running",
            "implement",
        ),
        run(
            "v-new",
            "/fixture/.vibecrafted/worktrees/vetcoders/vibecrafted/2026_1001/d4-session-id-early",
            5,
            "running",
            "review",
        ),
        run(
            "s-live",
            "/work/Sentry-Selfhosted",
            10,
            "running",
            "implement",
        ),
    ];
    snapshot.runs.stalled_runs = vec![
        run(
            "archive",
            "/work/old",
            48 * 60 + 1,
            "abandoned",
            "implement",
        ),
        run(
            "fresh",
            "/work/vc-frame/.worktrees/Q-mic-toggle",
            48 * 60 - 1,
            "unknown",
            "implement",
        ),
        run("ancient", "/work/old", 20 * 24 * 60, "unknown", "implement"),
    ];
    let before = tree(tmp.path());
    let mut host = HostDashboard::new(HostRoute::ActiveRuns);
    host.apply(snapshot);
    let lines = host.lines(&cfg);
    let text = lines.join("\n");
    println!("INITIAL FIXTURE:\n{text}");
    assert!(text.contains("Active runs 4 · needs attention 1"), "{text}");
    assert!(text.contains("vibecrafted · 2"));
    assert!(text.contains("codescribe · 1"));
    assert!(text.contains("Sentry-Selfhosted · 1"));
    assert!(text.contains("vc-frame · 1"));
    assert!(text.contains("+2 archiwalnych (starsze niż 48 h)"));
    assert!(!text.contains("archive") && !text.contains("ancient"));
    let rows = lines
        .iter()
        .filter(|l| {
            l.starts_with("  running") || l.starts_with("▶ running") || l.starts_with("  unknown")
        })
        .collect::<Vec<_>>();
    assert_eq!(rows.len(), 5, "one line per visible run: {text}");
    assert!(rows[2].contains("d4-session-id-early"));
    assert!(rows[3].contains("red-tests"), "newest first within repo");
    for row in &rows {
        assert!(ratatui::text::Line::from(row.as_str()).width() <= 120);
        assert!(
            !row.contains("implement")
                && !row.contains("pid_alive")
                && !row.contains("fixture-receipt")
        );
        assert_eq!(row.split(" · ").nth(2).unwrap().chars().count(), 26);
        assert!(row.contains("trwa 2 h 5 min") || row.contains("ostatnio 1 d 23 h temu"));
    }
    for (i, id) in ["s-live", "c-live", "v-new", "v-old", "fresh"]
        .iter()
        .enumerate()
    {
        host.move_selection(if i == 0 { 0 } else { 1 });
        assert_eq!(marked(&host, &cfg).len(), 1);
        let selected = host.lines(&cfg).join("\n");
        assert!(selected.contains(&format!("Selected: {id}")), "{selected}");
        assert!(selected.contains(&format!("Last error: error-{id}")));
    }
    host.move_selection(20);
    assert_eq!(host.selected, 4, "archive is not a selectable row");
    let mut snapshot = HostSnapshot::load(&cfg);
    snapshot.runs.active_runs = host.snapshot.as_ref().unwrap().runs.active_runs.clone();
    snapshot.runs.stalled_runs = host.snapshot.as_ref().unwrap().runs.stalled_runs.clone();
    snapshot.runs.active_runs.reverse();
    host.apply(snapshot);
    assert!(host.lines(&cfg).join("\n").contains("Selected: fresh"));
    assert_eq!(host.snapshot.as_ref().unwrap().runs.stalled_runs.len(), 3);
    assert_eq!(
        tree(tmp.path()),
        before,
        "projection never writes source state"
    );
    println!("AFTER FIXTURE:\n{}", host.lines(&cfg).join("\n"));
}

#[test]
fn active_run_columns_clip_unicode_and_keep_invalid_times_visible() {
    let tmp = tempfile::tempdir().unwrap();
    let cfg = config(tmp.path());
    let runs = cfg.state_root.join("runs");
    fs::create_dir_all(&runs).unwrap();
    write_run(&runs, "unicode", "running");
    let mut snapshot = HostSnapshot::load(&cfg);
    let mut r = snapshot.runs.active_runs[0].clone();
    r.root = format!("/work/repo/.worktrees/{}", "界".repeat(40));
    r.agent = "agent\nname-with-long-suffix".into();
    r.updated_at = "invalid".into();
    r.started_at.clear();
    r.skill = "implement".into();
    snapshot.runs.active_runs = vec![r.clone()];
    r.run_id = "unknown-age".into();
    snapshot.runs.stalled_runs = vec![r];
    let mut host = HostDashboard::new(HostRoute::ActiveRuns);
    host.apply(snapshot);
    let lines = host.lines(&cfg);
    let row = marked(&host, &cfg).remove(0);
    assert!(row.contains('…') && row.contains("czas nieznany"));
    assert!(ratatui::text::Line::from(row.as_str()).width() <= 120);
    assert!(!row.contains('\n'));
    assert!(lines.join("\n").contains("needs attention 1"));
    let mut term = Terminal::new(TestBackend::new(120, 40)).unwrap();
    term.draw(|f| draw(f, &host, &cfg)).unwrap();
    let physical_rows = term
        .backend()
        .buffer()
        .content()
        .chunks(120)
        .map(|row| row.iter().map(|c| c.symbol()).collect::<String>())
        .collect::<Vec<_>>();
    assert_eq!(
        physical_rows
            .iter()
            .filter(|l| l.contains("czas nieznany"))
            .count(),
        2
    );
}

#[test]
fn attention_cutoff_is_exact_and_offset_timestamps_share_one_sample_clock() {
    let tmp = tempfile::tempdir().unwrap();
    let cfg = config(tmp.path());
    let runs = cfg.state_root.join("runs");
    fs::create_dir_all(&runs).unwrap();
    write_run(&runs, "template", "running");
    let mut snapshot = HostSnapshot::load(&cfg);
    let now = chrono::DateTime::parse_from_rfc3339("2026-10-01T02:00:00+02:00").unwrap();
    snapshot.sampled_at = now.to_rfc3339();
    let mut boundary = snapshot.runs.active_runs[0].clone();
    boundary.run_id = "boundary".into();
    boundary.root = "/work/vibecrafted".into();
    boundary.state = "abandoned".into();
    boundary.updated_at = (now - chrono::Duration::hours(48)).to_rfc3339();
    let mut old = boundary.clone();
    old.run_id = "older-by-one-second".into();
    old.updated_at =
        (now - chrono::Duration::hours(48) - chrono::Duration::seconds(1)).to_rfc3339();
    snapshot.runs.active_runs.clear();
    snapshot.runs.stalled_runs = vec![old, boundary];
    let mut host = HostDashboard::new(HostRoute::ActiveRuns);
    host.apply(snapshot);
    let text = host.lines(&cfg).join("\n");
    assert!(text.contains("needs attention 1"));
    assert!(text.contains("+1 archiwalnych"));
    assert!(text.contains("Selected: boundary"));
    assert!(text.contains("ostatnio 2 d 0 h temu"));
    // The historical sample is intentional: selection must not re-filter using wall time.
    host.move_selection(100);
    assert_eq!(host.selected, 0);
    host.navigate(HostRoute::Dashboard);
    assert!(host.lines(&cfg).join("\n").contains("needs attention 1"));
}
