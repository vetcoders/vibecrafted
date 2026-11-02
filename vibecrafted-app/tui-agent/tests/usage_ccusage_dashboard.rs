//! The TUI usage dashboard over a real `ccusage` collection of the scrubbed
//! session fixtures: the same report the server serves. One test on purpose: it
//! points `HOME` and the provider stores at the fixtures for the whole process.

#[path = "../../../vibecrafted-server/control-core/tests/support/ccusage_env.rs"]
mod ccusage_env;

use std::fs;
use std::path::Path;
use std::time::{Duration, Instant};

use control_core::ControlPlane;
use voc::usage::{UsageCost, UsageDashboard, UsageRefreshState, display_cost};

use ccusage_env::{point_provider_stores_at, private_dir};

#[test]
fn dashboard_shows_measured_sessions_and_keeps_an_unknown_cost_unknown() {
    let root = private_dir("tui");
    let host = root.join("host");
    let control_plane = root.join("vc/control_plane");
    fs::create_dir_all(&host).expect("host");
    fs::create_dir_all(&control_plane).expect("control plane");
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../vibecrafted-server/control-core/tests/fixtures/ccusage");
    // SAFETY: this binary contains exactly one test, nothing has started yet.
    // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- this binary owns its only test; the environment is established before any collector or projection starts.
    unsafe { point_provider_stores_at(&fixtures, &host) };

    let outcome =
        ControlPlane::from_control_plane_home(&control_plane).collect_local_usage(Duration::ZERO);
    assert!(outcome.started, "{outcome:?}");

    let deadline = Instant::now() + Duration::from_secs(20);
    let dashboard = loop {
        let dashboard = UsageDashboard::load(&control_plane).expect("dashboard");
        if dashboard.refresh_state != UsageRefreshState::Pending {
            break dashboard;
        }
        assert!(Instant::now() < deadline, "projection did not settle");
        std::thread::sleep(Duration::from_millis(20));
    };

    assert_eq!(dashboard.refresh_state, UsageRefreshState::Ready);
    assert_eq!(dashboard.runs.len(), 6);
    assert_eq!(dashboard.tokens_total_known, 18_452_944);
    assert_eq!(dashboard.runs_tokens_unknown, 0);
    assert_eq!(dashboard.runs_cost_unknown, 1);
    assert!(
        (dashboard.usd_total - 13.918_955_8).abs() < 1e-9,
        "{}",
        dashboard.usd_total
    );

    let unpriced = dashboard
        .runs
        .iter()
        .find(|run| run.model == "vendor-unreleased-99")
        .expect("unpriced row");
    assert_eq!(unpriced.cost, UsageCost::Unknown);
    assert_eq!(display_cost(unpriced), "unknown");
    assert_eq!(unpriced.tokens_total, Some(159_299));

    let grok = dashboard
        .runs
        .iter()
        .find(|run| run.agent == "grok")
        .expect("grok row");
    assert_eq!(grok.model, "grok-4.7-build-fast");
    match &grok.cost {
        UsageCost::Known { amount, unit } => {
            assert!((amount - 11.089_948).abs() < 1e-9, "{amount}");
            assert_eq!(unit, "USD");
        }
        UsageCost::Unknown => panic!("grok reports its own cost"),
    }
    // A bill and a price-list estimate must not read the same.
    assert_eq!(grok.cost_source, "provider_reported");
    assert_eq!(display_cost(grok), "$11.0899 USD");
    let claude = dashboard
        .runs
        .iter()
        .find(|run| run.model == "claude-opus-5-5")
        .expect("priced claude row");
    assert!(
        claude.cost_source.starts_with("estimated:"),
        "{}",
        claude.cost_source
    );
    assert_eq!(display_cost(claude), "~$0.6496 USD");
    assert!(
        (dashboard.usd_estimated - 2.829_007_8).abs() < 1e-9,
        "{}",
        dashboard.usd_estimated
    );
    fs::remove_dir_all(root).ok();
}
