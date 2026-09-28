//! Host projection acceptance. Guest process/organ preservation and Frame chrome
//! routing require the separate installed Frame scenario walkaround.
use control_core::{ControlPlane, FrameSessionInventory};
use ratatui::{Terminal, backend::TestBackend};
use serde_json::json;
use std::{collections::BTreeMap, fs, path::Path, time::Duration};
use voc::{
    config::AppConfig,
    host::{HostDashboard, HostRoute, HostSnapshot, draw, project_catalog},
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
