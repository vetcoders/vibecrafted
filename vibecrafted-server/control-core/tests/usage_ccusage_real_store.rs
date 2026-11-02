//! What collection costs over this machine's real provider stores.
//!
//! Ignored by default because it reads the real home. It writes only under cargo's
//! test scratch directory, or under `VIBECRAFTED_REAL_STORE_HOME` when that is set.
//! Build first so the timing excludes compilation. A missing `VIBECRAFTED_PYTHON`
//! measures the in-process adapters alone:
//!
//! ```text
//! cargo test --release -p control-core --test usage_ccusage_real_store --no-run
//! VIBECRAFTED_PYTHON=/nonexistent /usr/bin/time -l cargo test --release \
//!   -p control-core --test usage_ccusage_real_store -- --ignored --nocapture
//! ```
//!
//! The first collection closes every old day. The second reads what every later one
//! reads, so its time is the recurring cost. Maximum RSS belongs to the first. To get
//! the recurring maximum, set `VIBECRAFTED_REAL_STORE_HOME` to a directory, run once to
//! fill it, and run again: the second process closes nothing. That directory also
//! keeps the projections for comparison with another reader of the same stores.
//! `VIBECRAFTED_REAL_STORE_RUNS` repeats the collection in one process, to show
//! whether resident memory settles the way a long-lived server needs it to.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

use control_core::ControlPlane;
use serde_json::Value;

fn resident_mb() -> u64 {
    let output = Command::new("ps")
        .args(["-o", "rss=", "-p", &std::process::id().to_string()])
        .output()
        .expect("ps");
    let kilobytes = String::from_utf8_lossy(&output.stdout)
        .trim()
        .parse::<u64>()
        .expect("resident size in kilobytes");
    kilobytes / 1024
}

fn report_one_collection(home: &Path, label: &str) {
    let started = Instant::now();
    let outcome = ControlPlane::new(home).collect_local_usage(Duration::ZERO);
    let elapsed = started.elapsed();

    eprintln!(
        "{label} collection took {elapsed:?}, {} MB resident after it",
        resident_mb()
    );
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
    let kept = std::env::var_os("VIBECRAFTED_REAL_STORE_HOME");
    let home = kept.as_ref().map_or_else(
        || {
            Path::new(env!("CARGO_TARGET_TMPDIR"))
                .join(format!("ccusage-real-{}", std::process::id()))
        },
        PathBuf::from,
    );
    fs::create_dir_all(&home).expect("private home");

    let runs = std::env::var("VIBECRAFTED_REAL_STORE_RUNS")
        .ok()
        .and_then(|raw| raw.parse::<usize>().ok())
        .unwrap_or(2);
    for run in 1..=runs {
        report_one_collection(&home, &format!("run {run}:"));
    }

    if kept.is_none() {
        fs::remove_dir_all(&home).ok();
    }
}
