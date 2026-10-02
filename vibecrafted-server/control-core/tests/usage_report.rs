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

#[test]
fn analytical_adapter_inventory_covers_every_supported_provider() {
    assert_eq!(
        control_core::USAGE_PROVIDER_ADAPTERS,
        &[
            "agy", "claude", "codex", "cursor", "grok", "junie", "kimi", "copilot"
        ]
    );
}

#[test]
fn usage_replays_are_excluded_only_with_identical_attributable_windows() {
    let home = fixture_home("replay-windows");
    let now = Utc.with_ymd_and_hms(2026, 10, 3, 12, 0, 0).unwrap();
    for (id, start, end, amount, source) in [
        ("original", "10:00:00", "10:30:00", 1.0, "estimated:test"),
        ("replay", "10:00:00", "10:30:00", 1.0, "estimated:test"),
        ("resume", "10:30:00", "11:00:00", 2.0, "provider_reported"),
        ("overlap", "10:45:00", "11:30:00", 3.0, "estimated:test"),
    ] {
        write_meta(
            &home,
            id,
            json!({
                "run_id": id, "agent": "codex", "provider_session_id": "session",
                "started_at": format!("2026-10-03T{start}Z"),
                "completed_at": format!("2026-10-03T{end}Z"), "status": "completed",
                "root": "/project", "skill": "workflow", "parent_run_id": "swarm",
                "usage": {"tokens_total": 100, "events": 1, "source": "harness_log", "counting_version": 2},
                "cost": {"amount": amount, "currency": "USD", "source": source},
            }),
        );
    }
    let report = ControlPlane::new(&home).usage_report(now, UsageFilter::default());
    assert_eq!(report.totals.tokens_total_known, 300);
    assert_eq!(report.totals.duplicates_excluded, 1);
    assert_eq!(
        report.totals.cost_by_source_unit["estimated:test"]["USD"],
        4.0
    );
    assert_eq!(
        report.totals.cost_by_source_unit["provider_reported"]["USD"],
        2.0
    );
    assert_eq!(report.runs.len(), 4, "replay remains inspectable");
    let resume = report
        .runs
        .iter()
        .find(|run| run.run_id == "resume")
        .unwrap();
    assert!(resume.duplicate_of.is_none());
    assert_eq!(resume.duration_s, Some(1800.0));
    assert_eq!(resume.root, "/project");
    assert_eq!(resume.task, "workflow");
    assert_eq!(resume.parent_run_id, "swarm");
    assert!(
        resume.settlement_verdict.is_empty(),
        "completed is not an accepted result"
    );
    assert!(
        resume
            .signals
            .iter()
            .any(|signal| signal.starts_with("shared_session:"))
    );
    fs::remove_dir_all(home).unwrap();
}

#[test]
fn zero_unknown_and_nonterminal_attention_have_distinct_evidence() {
    let home = fixture_home("attention");
    let now = Utc.with_ymd_and_hms(2026, 10, 3, 12, 0, 0).unwrap();
    for (id, total) in [
        ("measured-zero", json!(0)),
        ("missing", json!({"value": "unknown"})),
    ] {
        write_meta(
            &home,
            id,
            json!({
                "run_id": id, "agent": "kimi", "status": "running",
                "started_at": "2026-10-03T08:00:00Z", "updated_at": "2026-10-03T09:00:00Z",
                "usage": {"tokens_total": total}, "cost": {"amount": 0, "unit": "credits", "source": "provider_reported"}
            }),
        );
    }
    let report = ControlPlane::new(&home).usage_report(
        now,
        UsageFilter {
            since: Some(Duration::hours(3)),
            ..UsageFilter::default()
        },
    );
    assert_eq!(report.runs.len(), 2, "lower time boundary is inclusive");
    assert_eq!(report.totals.tokens_total_known, 0);
    assert_eq!(report.totals.runs_tokens_unknown, 1);
    assert_eq!(
        report.totals.cost_by_source_unit["provider_reported"]["credits"],
        0.0
    );
    for run in &report.runs {
        assert!(run.signals.iter().any(|s| s.starts_with("stale:")));
        assert!(run.signals.iter().any(|s| s.starts_with("long_run:")));
    }
    fs::remove_dir_all(home).unwrap();
}

#[test]
fn usage_report_recovers_transcript_totals_without_treating_missing_as_zero() {
    let home = fixture_home("transcript-recovery");
    let now = Utc.with_ymd_and_hms(2026, 9, 21, 12, 0, 0).unwrap();
    write_meta(
        &home,
        "measured-from-transcript",
        json!({
            "run_id": "measured-from-transcript",
            "agent": "codex",
            "status": "completed",
            "exit_code": 0,
            "completed_at": "2026-09-21T11:00:00Z"
        }),
    );
    fs::write(
        home.join("control_plane/runtime_runs/measured-from-transcript/transcript.log"),
        "{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":40,\"cached_input_tokens\":0,\"output_tokens\":11},\"model\":\"gpt-5.6-terra\"}\n",
    )
    .expect("transcript");
    write_meta(
        &home,
        "still-unknown",
        json!({
            "run_id": "still-unknown",
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
            since: Some(Duration::days(7)),
            since_label: "7d".into(),
            ..UsageFilter::default()
        },
    );

    assert_eq!(report.totals.runs, 2);
    assert_eq!(report.totals.runs_tokens_unknown, 1);
    assert_eq!(report.totals.tokens_total_known, 51);
    let measured = report
        .runs
        .iter()
        .find(|run| run.run_id == "measured-from-transcript")
        .expect("recovered run");
    assert_eq!(measured.telemetry_source, "transcript(lazy)");
    assert_eq!(measured.tokens.tokens_total, 51);
    let usd = measured
        .cost
        .amount
        .as_f64()
        .expect("transcript-derived cost");
    assert!(
        (usd - 0.000265).abs() < 1e-9,
        "expected the CLI price-table cost, got {usd}"
    );
    let unknown = report
        .runs
        .iter()
        .find(|run| run.run_id == "still-unknown")
        .expect("unmeasured run");
    assert_eq!(unknown.tokens.tokens_total["value"], "unknown");
    assert_ne!(unknown.tokens.tokens_total, 0);
    let filtered = ControlPlane::new(&home).usage_report(
        now,
        UsageFilter {
            model: Some("gpt-5.6-terra".into()),
            ..UsageFilter::default()
        },
    );
    assert_eq!(
        filtered.runs.len(),
        1,
        "filter must see the recovered model"
    );
    assert_eq!(filtered.runs[0].run_id, "measured-from-transcript");
    fs::remove_dir_all(home).ok();
}

#[test]
fn harness_settlement_reaches_existing_dashboard_with_semantics() {
    let home = fixture_home("harness-settle");
    let now = Utc.with_ymd_and_hms(2026, 9, 28, 12, 0, 0).unwrap();
    write_meta(
        &home,
        "harness",
        json!({
            "run_id": "harness", "agent": "codex", "model": "unpriced-model",
            "status": "completed", "completed_at": "2026-09-28T11:00:00Z",
            "unpricedModels": ["unpriced-model"],
            "usage": {
                "schema": "vibecrafted.usage.v1", "counting_version": 2,
                "source": "harness_log", "events": 1, "unit": "tokens",
                "input_semantics": "includes_cache", "tokens_input": 100,
                "tokens_cached_input": 80, "tokens_cache_write": 0,
                "tokens_output": 10, "tokens_total": 110, "tokens_reasoning": 4,
                "model_usage": {"unpriced-model": {"fresh_input": 20, "cache_read": 80, "cache_creation": 0, "output": 10, "reasoning": 4}}
            }
        }),
    );
    let before = fs::read(home.join("control_plane/runtime_runs/harness/meta.json")).unwrap();
    let report = ControlPlane::new(&home).usage_report(now, UsageFilter::default());
    assert_eq!(report.totals.runs_tokens_unknown, 0);
    assert_eq!(report.totals.tokens_total_known, 110);
    assert_eq!(report.totals.runs_cost_unknown, 1);
    let json = serde_json::to_value(report).unwrap();
    assert_eq!(json["unpricedModels"], json!(["unpriced-model"]));
    assert_eq!(json["runs"][0]["tokens"]["counting_version"], 2);
    assert_eq!(json["runs"][0]["tokens"]["tokens_reasoning"], 4);
    assert_eq!(json["runs"][0]["tokens"]["source"], "harness_log");
    assert_eq!(
        before,
        fs::read(home.join("control_plane/runtime_runs/harness/meta.json")).unwrap()
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn cost_unit_and_metadata_coverage_are_never_invented() {
    let home = fixture_home("coverage-unit");
    let now = Utc.with_ymd_and_hms(2026, 9, 21, 12, 0, 0).unwrap();
    let empty = ControlPlane::new(&home).usage_report(now, UsageFilter::default());
    assert_eq!(empty.coverage["inventory_available"], false);
    for (id, amount) in [("unitless", 2.5), ("negative", -10.0)] {
        write_meta(
            &home,
            id,
            json!({
                "run_id":id,"agent":"codex","completed_at":"2026-09-21T11:00:00Z",
                "usage":{"tokens_total":0,"source":"provider_stream"},
                "cost":{"amount":amount,"source":"provider_reported"}
            }),
        );
    }
    let malformed = home.join("control_plane/runtime_runs/malformed");
    fs::create_dir_all(&malformed).unwrap();
    fs::write(malformed.join("meta.json"), "malformed JSON").unwrap();
    let report = ControlPlane::new(&home).usage_report(now, UsageFilter::default());
    assert_eq!(report.coverage["inventory_available"], true);
    assert_eq!(report.coverage["metadata_records_seen"], 3);
    assert_eq!(report.coverage["metadata_records_unreadable"], 1);
    assert_eq!(
        report.totals.cost_by_source_unit["provider_reported"]["unknown"],
        2.5
    );
    assert!(!report.totals.cost_by_unit.contains_key("USD"));
    assert_eq!(report.totals.runs_cost_unknown, 1);
    fs::remove_dir_all(home).unwrap();
}

#[test]
fn malformed_token_measurements_and_missing_event_counts_stay_unknown() {
    let home = fixture_home("invalid-counts");
    let now = Utc.with_ymd_and_hms(2026, 9, 21, 12, 0, 0).unwrap();
    for (index, count) in [
        json!(0.5),
        json!(true),
        json!("7"),
        json!(9_007_199_254_740_992u64),
    ]
    .into_iter()
    .enumerate()
    {
        let id = format!("invalid-{index}");
        write_meta(
            &home,
            &id,
            json!({
                "run_id":id,"agent":"codex","completed_at":"2026-09-21T11:00:00Z",
                "usage":{"tokens_total":count,"tokens_input":count,"source":"provider_stream"},
                "cost":{"amount":0,"currency":"USD","source":"provider_reported"}
            }),
        );
    }
    let report = ControlPlane::new(&home).usage_report(now, UsageFilter::default());
    assert_eq!(report.totals.tokens_total_known, 0);
    assert_eq!(report.totals.runs_tokens_unknown, 4);
    for run in report.runs {
        assert_eq!(run.tokens.tokens_total["value"], "unknown");
        assert_eq!(run.tokens.tokens_input["value"], "unknown");
        assert_eq!(run.tokens.semantics["events_recorded"], false);
    }
    fs::remove_dir_all(home).unwrap();
}
