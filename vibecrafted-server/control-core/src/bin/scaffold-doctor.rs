//! Deterministic scaffold plan gate.
//!
//! ```text
//! scaffold-doctor --plan <plan_root> [--repo <git-root>] [--json]
//! scaffold-doctor <vibecrafted-home> <org> <repo> <day> <plan-id> [--repo <git-root>] [--json]
//! ```
//!
//! Exit 0 = pass · Exit 1 = refuse (rule violations) · Exit 2 = usage / not a plan.

use std::path::PathBuf;
use std::process::ExitCode;

use control_core::{ScaffoldArtifactStore, ScaffoldDoctorReport, doctor_plan_root_in_repo};

fn main() -> ExitCode {
    // argv values select local inputs; they never establish executable trust.
    let arguments = std::env::args_os().skip(1).collect::<Vec<_>>(); // nosemgrep: rust.lang.security.args-os.args-os
    let mut json = false;
    let mut plan: Option<PathBuf> = None;
    let mut repo: Option<PathBuf> = None;
    let mut positional = Vec::new();

    let mut index = 0usize;
    while index < arguments.len() {
        let arg = arguments[index].to_string_lossy();
        match arg.as_ref() {
            "--json" => json = true,
            "--plan" => {
                index += 1;
                let Some(value) = arguments.get(index) else {
                    eprintln!("scaffold-doctor: --plan requires a path");
                    return usage();
                };
                plan = Some(PathBuf::from(value));
            }
            "--repo" => {
                index += 1;
                let Some(value) = arguments.get(index) else {
                    eprintln!("scaffold-doctor: --repo requires a path");
                    return usage();
                };
                repo = Some(PathBuf::from(value));
            }
            "-h" | "--help" => return usage(),
            other if other.starts_with('-') => {
                eprintln!("scaffold-doctor: unknown flag {other}");
                return usage();
            }
            _ => positional.push(arguments[index].clone()),
        }
        index += 1;
    }

    let report = if let Some(plan_root) = plan {
        doctor_plan_root_in_repo(plan_root, repo.as_deref())
    } else if positional.len() == 5 {
        let values = positional
            .iter()
            .map(|value| value.to_string_lossy().into_owned())
            .collect::<Vec<_>>();
        ScaffoldArtifactStore::new(&values[0]).doctor_with_repo(
            &values[1],
            &values[2],
            &values[3],
            &values[4],
            repo.as_deref(),
        )
    } else {
        return usage();
    };

    match report {
        Ok(report) => emit(&report, json),
        Err(error) => {
            if json {
                let payload = serde_json::json!({
                    "valid": false,
                    "error": error.to_string(),
                });
                println!("{}", serde_json::to_string_pretty(&payload).expect("json"));
            } else {
                eprintln!("scaffold-doctor: REFUSE — {error}");
            }
            ExitCode::from(2)
        }
    }
}

fn emit(report: &ScaffoldDoctorReport, json: bool) -> ExitCode {
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(report).expect("report serializes")
        );
    } else {
        print_human(report);
    }
    if report.valid {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(1)
    }
}

fn print_human(report: &ScaffoldDoctorReport) {
    if report.valid {
        println!(
            "scaffold-doctor: PASS plan_id={} root={}",
            report.plan_id, report.plan_root
        );
        println!("  artifacts: {}", report.artifact_ids.len());
        return;
    }
    println!(
        "scaffold-doctor: REFUSE plan_id={} ({} violation{})",
        report.plan_id,
        report.errors.len(),
        if report.errors.len() == 1 { "" } else { "s" }
    );
    println!("  root: {}", report.plan_root);
    for error in &report.errors {
        let rule = error.rule.as_deref().unwrap_or("—");
        let where_ = error
            .path
            .as_deref()
            .or(error.artifact_id.as_deref())
            .unwrap_or("(package)");
        println!(
            "  [{rule} {code}] {where_}: {message}",
            code = error.code,
            message = error.message
        );
    }
}

fn usage() -> ExitCode {
    eprintln!(
        "usage:\n  scaffold-doctor --plan <plan_root> [--repo <git-root>] [--json]\n  scaffold-doctor <vibecrafted-home> <org> <repo> <day> <plan-id> [--repo <git-root>] [--json]"
    );
    ExitCode::from(2)
}
