//! Server-owned schedule for local usage collection.
//!
//! `vc-server` is the single owner of provider usage metrics: this task runs a
//! bounded one-shot of the existing parsers (see
//! `control_core::usage_collect`) on a fixed cadence and leaves the result in
//! the shared telemetry projections. There is no resident per-provider child,
//! and the schedule only reads files on this host — the network is touched
//! solely by an explicit quota refresh (`GET /api/usage/quota?refresh=…`).

use std::time::Duration;

use control_core::ControlPlane;

/// Seconds between local collections. `0` disables the schedule.
const INTERVAL_ENV: &str = "VIBECRAFTED_USAGE_COLLECT_INTERVAL_S";
const DEFAULT_INTERVAL_S: u64 = 300;
const STARTUP_DELAY: Duration = Duration::from_secs(5);

fn interval_from_env() -> Duration {
    let seconds = std::env::var(INTERVAL_ENV)
        .ok()
        .and_then(|raw| raw.trim().parse::<u64>().ok())
        .unwrap_or(DEFAULT_INTERVAL_S);
    Duration::from_secs(seconds)
}

/// Start the schedule. Call once from the server binary, never from a router
/// builder: tests that build routes must not spawn collectors.
pub fn spawn_local_usage_collector(plane: ControlPlane) {
    let interval = interval_from_env();
    if interval.is_zero() {
        return;
    }
    tokio::spawn(async move {
        tokio::time::sleep(STARTUP_DELAY).await;
        loop {
            let collector = plane.clone();
            // `min_interval` makes a restart storm or a second owner a no-op.
            let _ =
                tokio::task::spawn_blocking(move || collector.collect_local_usage(interval)).await;
            tokio::time::sleep(interval).await;
        }
    });
}
