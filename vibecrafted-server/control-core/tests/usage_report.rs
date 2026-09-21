use std::fs;

use chrono::{Duration, TimeZone, Utc};
use control_core::{ControlPlane, USAGE_REPORT_SCHEMA, UsageFilter};
use serde_json::json;

fn fixture_home(label: &str) -> std::path::PathBuf {
    std::env::temp_dir().join(format!(
        "control-core-usage-{label}-{}-{}",
        std::process::id(),
        Utc::now().timestamp_nanos_opt().unwrap_or_default()
    ))
}

fn write_meta(home: &std::path::Path, run_id: &str, value: serde_json::Value) {
    let run = home.join("control_plane/runtime_runs").join(run_id);
    fs::create_dir_all(&run).expect("runtime run dir");
    fs::write(
        run.join("meta.json"),
        serde_json::to_vec_pretty(&value).expect("json"),
    )
    .expect("meta");
}

#[test]
fn usage_report_keeps_units_separate_and_unknowns_unknown() {
    let home = fixture_home("honest-totals");
    let now = Utc.with_ymd_and_hms(2026, 9, 21, 12, 0, 0).unwrap();
    write_meta(
        &home,
        "codex-usd",
        json!({
            "run_id": "codex-usd",
            "agent": "codex",
            "provider": "openai",
            "agent_model": "gpt-5.6-terra",
            "status": "completed",
            "exit_code": 0,
            "completed_at": "2026-09-21T11:30:00Z",
            "usage": {
                "schema": "vibecrafted.usage.v1",
                "unit": "tokens",
                "source": "provider_stream",
                "events": 1,
                "tokens_input": 100,
                "tokens_cached_input": 20,
                "tokens_cache_write": {"value": "unknown", "reason": "not emitted"},
                "tokens_output": 50,
                "tokens_total": 150
            },
            "cost": {"amount": 0.25, "currency": "USD", "source": "estimated:test"},
            "provider_session_id": "session-1"
        }),
    );
    write_meta(
        &home,
        "junie-credits",
        json!({
            "run_id": "junie-credits",
            "agent": "junie",
            "agent_model": "junie-code",
            "status": "failed",
            "exit_code": 1,
            "completed_at": "2026-09-21T11:00:00Z",
            "usage": {
                "schema": "vibecrafted.usage.v1",
                "unit": "tokens",
                "source": "provider_stream",
                "events": 1,
                "tokens_input": 10,
                "tokens_cached_input": 0,
                "tokens_cache_write": 0,
                "tokens_output": 5,
                "tokens_total": 15
            },
            "cost": {"amount": 12, "unit": "credits", "source": "provider_reported"},
            "failure": {"kind": "quota_exhausted", "summary": "exit_code=1 (quota)"}
        }),
    );
    write_meta(
        &home,
        "legacy-run",
        json!({
            "run_id": "legacy-run",
            "agent": "kimi",
            "status": "completed",
            "exit_code": 0,
            "completed_at": "2026-09-21T10:00:00Z",
            "tokens_total": 0,
            "cost_usd": 0
        }),
    );

    let report = ControlPlane::new(&home).usage_report(
        now,
        UsageFilter {
            since: Some(Duration::hours(24)),
            since_label: "24h".into(),
            ..UsageFilter::default()
        },
    );

    assert_eq!(report.schema, USAGE_REPORT_SCHEMA);
    assert_eq!(report.totals.runs, 3);
    assert_eq!(report.totals.runs_failed, 1);
    assert_eq!(report.totals.tokens_total_known, 165);
    assert_eq!(report.totals.runs_tokens_unknown, 1);
    assert_eq!(report.totals.cost_by_unit.get("USD"), Some(&0.25));
    assert_eq!(report.totals.cost_by_unit.get("credits"), Some(&12.0));
    assert_eq!(report.totals.runs_cost_unknown, 1);
    let legacy = report
        .runs
        .iter()
        .find(|run| run.run_id == "legacy-run")
        .expect("legacy row");
    assert_eq!(legacy.tokens.tokens_total["value"], "unknown");
    assert_eq!(legacy.cost.amount["value"], "unknown");
    assert_eq!(legacy.provider, "kimi");
    assert_eq!(legacy.telemetry_source, "meta(legacy-uninstrumented)");
    fs::remove_dir_all(home).ok();
}

#[test]
fn usage_report_filters_window_and_dimensions_without_following_symlinks() {
    let home = fixture_home("filters");
    let now = Utc.with_ymd_and_hms(2026, 9, 21, 12, 0, 0).unwrap();
    for (run_id, stamp, agent, model) in [
        (
            "fresh-codex",
            "2026-09-21T11:00:00Z",
            "codex",
            "gpt-5.6-terra",
        ),
        ("fresh-kimi", "2026-09-21T10:00:00Z", "kimi", "kimi-k2"),
        (
            "old-codex",
            "2026-09-19T10:00:00Z",
            "codex",
            "gpt-5.6-terra",
        ),
    ] {
        write_meta(
            &home,
            run_id,
            json!({
                "run_id": run_id,
                "agent": agent,
                "agent_model": model,
                "status": "completed",
                "exit_code": 0,
                "completed_at": stamp,
                "usage": {"events": 1, "tokens_total": 10},
                "cost": {"amount": 1, "currency": "USD", "source": "provider_reported"}
            }),
        );
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::symlink;
        let target = home.join("outside");
        fs::create_dir_all(&target).expect("outside");
        fs::write(target.join("meta.json"), b"{}").expect("outside meta");
        symlink(&target, home.join("control_plane/runtime_runs/symlink-run")).expect("symlink");
    }

    let report = ControlPlane::new(&home).usage_report(
        now,
        UsageFilter {
            since: Some(Duration::hours(24)),
            since_label: "24h".into(),
            provider: Some("codex".into()),
            agent: Some("codex".into()),
            ..UsageFilter::default()
        },
    );
    assert_eq!(report.runs.len(), 1);
    assert_eq!(report.runs[0].run_id, "fresh-codex");
    assert_eq!(report.dimensions.providers[0].name, "codex");
    assert_eq!(report.dimensions.agents[0].name, "codex");
    assert_eq!(report.dimensions.models[0].name, "gpt-5.6-terra");
    fs::remove_dir_all(home).ok();
}
