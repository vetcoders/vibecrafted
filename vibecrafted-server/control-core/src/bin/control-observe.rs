//! Read-only observation from the same `compute_view` derivation voc uses.
//!
//! ```text
//! control-observe --home <vibecrafted-home> --run-id <id> [--json]
//! ```
//!
//! Exit 0 = printed a payload (found or not). Exit 2 = usage.

use std::path::PathBuf;
use std::process::ExitCode;

use chrono::Utc;
use control_core::ControlPlane;
use serde_json::json;

fn main() -> ExitCode {
    // argv values select local inputs; they never establish executable trust.
    let arguments = std::env::args_os().skip(1).collect::<Vec<_>>(); // nosemgrep: rust.lang.security.args-os.args-os
    let mut home: Option<PathBuf> = None;
    let mut run_id = String::new();
    let mut index = 0usize;
    while index < arguments.len() {
        let arg = arguments[index].to_string_lossy();
        match arg.as_ref() {
            "--json" => {
                // JSON is the only wire; the flag stays so `observe --json` callers
                // can pass it without a usage error.
            }
            "--home" => {
                index += 1;
                let Some(value) = arguments.get(index) else {
                    eprintln!("control-observe: --home requires a path");
                    return usage();
                };
                home = Some(PathBuf::from(value));
            }
            "--run-id" => {
                index += 1;
                let Some(value) = arguments.get(index) else {
                    eprintln!("control-observe: --run-id requires an id");
                    return usage();
                };
                run_id = value.to_string_lossy().into_owned();
            }
            "-h" | "--help" => return usage(),
            other if other.starts_with('-') => {
                eprintln!("control-observe: unknown flag {other}");
                return usage();
            }
            _ => {
                eprintln!("control-observe: unexpected argument {arg}");
                return usage();
            }
        }
        index += 1;
    }
    if run_id.trim().is_empty() {
        return usage();
    }
    let home = home.unwrap_or_else(control_core::vibecrafted_home);
    let plane = ControlPlane::new(home);
    let now = Utc::now();
    let run = plane.derived_run(&run_id, now);
    let found = run.is_some();
    let terminal = run
        .as_ref()
        .is_some_and(control_core::RunStatus::is_terminal);
    let worker_alive = run.as_ref().and_then(|item| item.worker_alive);
    let process_truth = run
        .as_ref()
        .map(|item| {
            if terminal {
                "terminal"
            } else if matches!(item.process_truth.as_str(), "live" | "ghost" | "unknown")
                && !item.process_truth.is_empty()
            {
                item.process_truth.as_str()
            } else if worker_alive == Some(true) {
                "live"
            } else if item.liveness == "pid_gone" {
                "ghost"
            } else {
                "unknown"
            }
        })
        .unwrap_or("unknown");
    let payload = json!({
        "schema": "vibecrafted.run-observation.v1",
        "run_id": run_id,
        "source": "control_core_compute_view",
        "found": found,
        "terminal": terminal,
        "worker_alive": worker_alive,
        "process_truth": process_truth,
        "run": run,
        "writer_revalidation": "control_core_read",
    });
    println!("{}", serde_json::to_string_pretty(&payload).expect("json"));
    ExitCode::SUCCESS
}

fn usage() -> ExitCode {
    eprintln!("control-observe --home <vibecrafted-home> --run-id <id> [--json]");
    ExitCode::from(2)
}
