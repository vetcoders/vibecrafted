#![cfg(feature = "ssr")]
//! Real-browser regression: opt in with a preinstalled Chromium and Node22+.
//! No npm dependencies, user profile, real provider data or resident server.
use leptos::prelude::*;
use std::{fs, process::Command};
use vibecrafted_server_web::{app::UsagePage, theme::provide_theme_context};

#[test]
#[ignore = "requires VIBECRAFTED_TEST_BROWSER (Chromium) and VIBECRAFTED_TEST_NODE (Node22+)"]
fn usage_refresh_preserves_dom_state_at_all_recorded_scale() {
    let browser =
        std::env::var_os("VIBECRAFTED_TEST_BROWSER").expect("set preinstalled Chromium executable");
    let node = std::env::var_os("VIBECRAFTED_TEST_NODE").expect("set Node22+ executable");
    // nosemgrep: rust.lang.security.temp-dir.temp-dir -- exclusive mode0700 directory rejects existing paths and symlinks before browser profile/fixture writes.
    let dir = std::env::temp_dir().join(format!(
        "vc-usage-dom-{}-{}",
        std::process::id(),
        chrono::Utc::now().timestamp_nanos_opt().unwrap()
    ));
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(&dir).unwrap();
    let owner = Owner::new();
    let html = owner.with(|| {
        leptos_meta::provide_meta_context();
        provide_context(leptos_meta::ServerMetaContext::new().0);
        provide_theme_context();
        UsagePage().to_html()
    });
    let input = dir.join("usage.html");
    fs::write(&input, html).unwrap();
    let result = Command::new(node)
        .arg(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/usage_refresh_dom.mjs"
        ))
        .arg(&input)
        .arg(browser)
        .arg(dir.join("profile"))
        .env(
            "VIBECRAFTED_TEST_CSS",
            concat!(env!("CARGO_MANIFEST_DIR"), "/styles/main.css"),
        )
        .output()
        .expect("run browser regression");
    let output = String::from_utf8_lossy(&result.stdout);
    let error = String::from_utf8_lossy(&result.stderr);
    if let Some(path) = std::env::var_os("VIBECRAFTED_USAGE_DOM_REPORT") {
        fs::write(path, output.as_bytes()).unwrap();
    }
    fs::remove_dir_all(&dir).unwrap();
    assert!(
        result.status.success(),
        "DOM regression failed:\n{output}\n{error}"
    );
    let report: serde_json::Value =
        serde_json::from_str(&output).expect("structured browser result");
    assert_eq!(report["ok"], true);
    assert_eq!(report["fixture_rows"], 17223);
    assert!(report["fixture_bytes"].as_u64().unwrap() >= 12_000_000);
}
