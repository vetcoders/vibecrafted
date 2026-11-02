//! The `ccusage` collection runs in process, so a broken Python runtime costs
//! the scanners that still need it and nothing else. One test on purpose: it
//! replaces the interpreter for the whole process.

#![cfg(unix)]

#[path = "support/ccusage_env.rs"]
mod ccusage_env;

use std::fs;
use std::path::Path;
use std::time::{Duration, Instant};

use chrono::Utc;
use control_core::{ControlPlane, UsageFilter};

use ccusage_env::{point_provider_stores_at, private_dir};

#[test]
fn measured_providers_survive_a_collector_python_that_dies() {
    use std::os::unix::fs::PermissionsExt;

    let root = private_dir("python-down");
    let host = root.join("host");
    let home = root.join("vc");
    fs::create_dir_all(&host).unwrap();
    fs::create_dir_all(&home).unwrap();
    let python = root.join("dead-python");
    fs::write(&python, "#!/bin/sh\nexit 1\n").unwrap();
    fs::set_permissions(&python, fs::Permissions::from_mode(0o700)).unwrap();
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/ccusage");
    // SAFETY: this binary contains exactly one test, nothing has started yet.
    // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- this binary owns its only test; the environment is established before any collector or projection starts.
    unsafe {
        point_provider_stores_at(&fixtures, &host);
        std::env::set_var("VIBECRAFTED_PYTHON", &python);
    }

    let plane = ControlPlane::new(&home);
    let outcome = plane.collect_local_usage(Duration::ZERO);

    assert!(outcome.started, "{outcome:?}");
    assert_eq!(outcome.agents.len(), 9, "{outcome:?}");
    let status = |agent: &str| {
        outcome
            .agents
            .iter()
            .find(|item| item.agent == agent)
            .map(|item| item.status.as_str())
    };
    for agent in ["claude", "codex", "copilot", "grok", "kimi"] {
        assert_eq!(status(agent), Some("ok"), "{agent}: {outcome:?}");
    }
    for agent in ["junie", "omp"] {
        assert_ne!(status(agent), Some("ok"), "{agent}: {outcome:?}");
    }

    let deadline = Instant::now() + Duration::from_secs(10);
    let report = loop {
        let report = plane.usage_report(Utc::now(), UsageFilter::default());
        if !report.local_sources.iter().any(|item| item.refreshing) {
            break report;
        }
        assert!(Instant::now() < deadline, "projection did not settle");
        std::thread::sleep(Duration::from_millis(20));
    };
    assert_eq!(report.totals.runs, 6);
    assert_eq!(report.totals.tokens_total_known, 18_452_944);
    fs::remove_dir_all(root).ok();
}
