//! What collection costs over this machine's real provider stores.
//!
//! Ignored by default because it reads the real home. It writes only under cargo's
//! test scratch directory. Build first so the timing excludes compilation. A missing
//! `VIBECRAFTED_PYTHON` measures the in-process adapters alone:
//!
//! ```text
//! cargo test --release -p control-core --test usage_ccusage_real_store --no-run
//! VIBECRAFTED_PYTHON=/nonexistent /usr/bin/time -l cargo test --release \
//!   -p control-core --test usage_ccusage_real_store -- --ignored --nocapture
//! ```
//!
//! The first collection closes every old day. The second reads what every later one
//! reads, so its time is the recurring cost. Maximum RSS comes from the first.

use std::fs;
use std::path::Path;
use std::time::{Duration, Instant};

use control_core::ControlPlane;
use serde_json::Value;

fn report_one_collection(home: &Path, label: &str) {
    let started = Instant::now();
    let outcome = ControlPlane::new(home).collect_local_usage(Duration::ZERO);
    let elapsed = started.elapsed();

    eprintln!("{label} collection took {elapsed:?}");
    for row in &outcome.agents {
        let projection = home
            .join("telemetry")
            .join(&row.agent)
            .join("ccusage-sessions.json");
        let bytes = fs::read(&projection).unwrap_or_default();
        let sessions = serde_json::from_slice::<Value>(&bytes)
            .ok()
            .and_then(|value| value["sessions"].as_array().map(Vec::len))
            .unwrap_or(0);
        eprintln!(
            "{:<8} {:<12} {sessions:>7} sessions {:>11} bytes  {}",
            row.agent,
            row.status,
            bytes.len(),
            row.detail
        );
    }
    assert!(outcome.started, "{outcome:?}");
}

#[test]
#[ignore = "reads this machine's real provider stores"]
fn report_the_cost_of_collecting() {
    let home =
        Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!("ccusage-real-{}", std::process::id()));
    fs::create_dir_all(&home).expect("private home");

    report_one_collection(&home, "first");
    report_one_collection(&home, "recurring");

    fs::remove_dir_all(&home).ok();
}
