#![recursion_limit = "512"]

pub mod app;
pub mod chrome;

#[cfg(all(test, feature = "ssr"))]
#[test]
fn skills_one_list_then_one_editor() {
    app::skills_one_list_then_one_editor();
}
pub mod control;
pub mod run_detail;
pub mod scaffold;
pub mod theme;
pub mod tools;

// Delivery gates filter with `--exact` on these bare names. A test nested
// under `app::tests` is `app::tests::…` and that filter runs nothing.
#[cfg(all(test, feature = "ssr"))]
#[test]
fn overview_welcome_status_and_miniatures() {
    app::overview_welcome_status_and_miniatures();
}

#[cfg(all(test, feature = "ssr"))]
#[test]
fn runs_five_buckets_use_settlement() {
    app::tests::runs_five_buckets_use_settlement();
}

#[cfg(all(test, feature = "ssr"))]
#[test]
fn projects_filter_deduped_shelf() {
    app::projects_filter_deduped_shelf();
}

#[cfg(all(test, feature = "ssr"))]
#[test]
fn code_intelligence_is_not_a_plan_library() {
    // `--lib code_intelligence_is_not_a_plan_library -- --exact` matches this
    // crate-root name, not `app::tests::…`.
    app::code_intelligence_is_not_a_plan_library();
}

#[cfg(all(test, feature = "ssr"))]
#[test]
fn history_is_aicx_not_plans() {
    crate::app::history_is_aicx_not_plans_proof();
}

#[cfg(feature = "hydrate")]
#[wasm_bindgen::prelude::wasm_bindgen]
pub fn hydrate() {
    use crate::app::App;

    console_error_panic_hook::set_once();
    leptos::mount::hydrate_body(App);
}

// The delivery gate filters with `--exact sidebar_groups_work_trace_machine`.
// Cargo's exact match is the full test path, so this has to live at the crate
// root. The assertions stay next to the chrome they describe.
#[cfg(all(test, feature = "ssr"))]
#[test]
fn sidebar_groups_work_trace_machine() {
    chrome::assert_sidebar_groups_work_trace_machine();
}

#[cfg(all(test, feature = "ssr"))]
#[test]
fn settings_one_room_six_groups() {
    use std::fs;
    use std::io::ErrorKind;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    use leptos::prelude::*;

    use crate::app::{SettingsPage, SettingsWrite, canonical_artifacts_root, save_settings_file};
    use crate::theme::provide_theme_context;

    fn temp_home() -> PathBuf {
        static NEXT_ID: AtomicU64 = AtomicU64::new(0);
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let base = std::env::var_os("TMPDIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/tmp"));
        for attempt in 0..100 {
            let nonce = NEXT_ID.fetch_add(1, Ordering::Relaxed);
            let candidate = base.join(format!(
                "vc-web-settings-{}-{nanos}-{nonce}-{attempt}",
                std::process::id()
            ));
            match fs::create_dir(&candidate) {
                Ok(()) => return candidate,
                Err(error) if error.kind() == ErrorKind::AlreadyExists => continue,
                Err(error) => panic!("create isolated fixture home: {error}"),
            }
        }
        panic!("could not allocate an isolated fixture home")
    }

    let owner = Owner::new();
    let html = owner.with(|| {
        leptos_meta::provide_meta_context();
        provide_theme_context();
        SettingsPage().to_html()
    });
    let room = html
        .split("data-settings-room=\"/settings\"")
        .nth(1)
        .expect("settings room on /settings");
    let mut cursor = 0;
    for label in [
        "Paths",
        "MCP",
        "Agents",
        "Defaults",
        "Artifacts location",
        "Git",
    ] {
        let needle = format!(">{label}<");
        let at = room[cursor..]
            .find(&needle)
            .unwrap_or_else(|| panic!("missing group {label}"));
        cursor += at + needle.len();
    }
    assert!(html.contains("Settings &amp; config"));
    assert!(room.contains("Default permissions"));
    assert!(room.contains("Default runtime"));
    assert!(room.contains(">Isolation<"));
    assert_eq!(room.matches("value=\"Worktrees\"").count(), 1);
    assert_eq!(room.matches("value=\"vm\"").count(), 1);
    assert_eq!(room.matches("value=\"cloud\"").count(), 1);
    assert!(
        !room.contains("selected=\"false\""),
        "a false isolation option must not stay selected"
    );
    assert!(!room.contains("value=\"living-tree\""));
    assert!(!room.contains("Fleet Worktrees"));
    let artifacts = canonical_artifacts_root()
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;");
    assert!(
        room.contains(&artifacts),
        "artifacts location should show the canonical root"
    );
    assert!(room.contains("id=\"artifacts-location\""));
    assert!(html.contains("action=\"/api/settings\""));
    assert!(html.contains("method=\"post\""));
    assert!(html.contains("class=\"server-app-shell\""));
    assert!(!html.contains("window.open"));
    assert!(!html.contains("target=\"_blank\""));
    assert_eq!(html.matches("data-settings-room").count(), 1);

    let home = temp_home();
    let path = home.join("config.toml");
    fs::write(
        &path,
        "# keep-me\n[server]\nbind_host = \"127.0.0.1\"\nport = 3024\n\n[repositories.\"team/project\"]\nremote = \"https://example.com/team/project.git\"\n",
    )
    .expect("seed config");
    let write = SettingsWrite {
        vc_frame_url: "http://127.0.0.1:8082/".into(),
        slack_console_url: String::new(),
        agents: "grok, codex".into(),
        permissions: "bypass".into(),
        runtime: "living-tree".into(),
        isolation: "vm".into(),
        git_identity: "new/repo".into(),
        git_remote: "https://example.com/new/repo.git".into(),
    };
    save_settings_file(&path, &write).expect("write existing config");
    let saved = fs::read_to_string(&path).expect("read saved config");
    assert!(saved.contains("# keep-me"));
    assert!(saved.contains("bind_host = \"127.0.0.1\""));
    assert!(saved.contains("port = 3024"));
    assert!(saved.contains("https://example.com/team/project.git"));
    assert!(saved.contains("https://example.com/new/repo.git"));
    assert!(saved.contains("isolation = \"vm\""));
    assert!(saved.contains("permissions = \"bypass\""));
    assert!(saved.contains("runtime = \"living-tree\""));
    assert!(saved.contains("default_agents = [\"grok\", \"codex\"]"));
    assert!(saved.contains("[tools.vc-frame]"));
    assert!(saved.contains("http://127.0.0.1:8082/"));
    assert!(!saved.contains("slack-console"));

    let rejected = SettingsWrite {
        isolation: "living-tree".into(),
        ..write
    };
    let error = save_settings_file(&path, &rejected).expect_err("bad isolation");
    assert!(error.contains("isolation"));
    assert_eq!(fs::read_to_string(&path).expect("unchanged"), saved);

    fs::remove_dir_all(home).ok();
}

// Crate-root name: the delivery gate filters `--exact diagnostics_is_not_runs`.
// A test inside `app::tests` would be `app::tests::diagnostics_is_not_runs` and
// that exact filter would run nothing.
#[cfg(all(test, feature = "ssr"))]
#[test]
fn diagnostics_is_not_runs() {
    use leptos::prelude::*;

    use crate::app::{DiagnosticsPage, diagnostics_room};
    use crate::theme::provide_theme_context;

    let owner = Owner::new();
    let page = owner.with(|| {
        leptos_meta::provide_meta_context();
        provide_theme_context();
        DiagnosticsPage().to_html()
    });
    let room_at = page
        .find("id=\"diagnostics-room\"")
        .expect("diagnostics room");
    let html = &page[room_at..];
    let room = owner.with(|| {
        leptos_meta::provide_meta_context();
        provide_theme_context();
        diagnostics_room().to_html()
    });
    for html in [html, room.as_str()] {
        assert!(html.contains("Diagnostics"), "{html}");
        assert!(html.contains("Logs"), "{html}");
        assert!(html.contains("Events"), "{html}");
        assert!(html.contains("Server health"), "{html}");
        assert!(html.contains("Process stats"), "{html}");
        assert!(html.contains("health"), "{html}");
        assert!(html.contains("logs"), "{html}");
        assert!(html.contains("events"), "{html}");
        assert!(html.contains("vc-monitor"), "{html}");
        assert!(html.contains(">cleanup<"), "{html}");
        for title in [
            "Success",
            "Needs Attention",
            "Failures",
            "Current",
            "Queued",
        ] {
            assert!(
                !html.contains(title),
                "run bucket {title} leaked onto diagnostics: {html}"
            );
        }
    }
}
