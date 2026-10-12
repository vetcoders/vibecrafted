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
    assert_eq!(legacy.provider, "moonshot");
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
    assert_eq!(report.dimensions.providers[0].name, "openai");
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
fn usage_report_recovers_transcript_totals_without_treating_missing_as_zero() {
    let home = fixture_home("transcript-recovery");
    let now = Utc.with_ymd_and_hms(2026, 9, 21, 12, 0, 0).unwrap();
    write_meta(
        &home,
        "measured-from-transcript",
        json!({
            "run_id": "measured-from-transcript",
            "agent": "codex",
            "agent_model": "gpt-5.6-terra",
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
fn local_provider_usage_ingested_and_deduplicated_against_vc_runs() {
    let home = fixture_home("local-dedup");
    let now = Utc.with_ymd_and_hms(2026, 10, 12, 12, 0, 0).unwrap();

    // 1. A VC run with provider_session_id "sess-shared-123"
    write_meta(
        &home,
        "vc-codex-1",
        json!({
            "run_id": "vc-codex-1",
            "agent": "codex",
            "provider": "openai",
            "agent_model": "gpt-5.6-terra",
            "status": "completed",
            "exit_code": 0,
            "completed_at": "2026-10-12T11:30:00Z",
            "usage": {
                "schema": "vibecrafted.usage.v1",
                "unit": "tokens",
                "source": "provider_stream",
                "events": 1,
                "tokens_input": 100,
                "tokens_cached_input": 20,
                "tokens_cache_write": 0,
                "tokens_output": 50,
                "tokens_total": 150
            },
            "cost": {"amount": 0.25, "currency": "USD", "source": "estimated:test"},
            "provider_session_id": "sess-shared-123"
        }),
    );

    // 2. Local telemetry scan-cache for codex containing both the shared session and a local-only session
    let codex_cache_dir = home.join("telemetry/codex");
    fs::create_dir_all(&codex_cache_dir).unwrap();
    let mtime = now.timestamp() as f64 - 1800.0;
    let codex_cache = json!({
        "files": {
            "/tmp/store/session_shared.jsonl": {
                "days": [{"day":"2026-10-12"}],
                "record": {
                    "agent": "codex",
                    "id": "sess-shared-123",
                    "model": "gpt-5.6-terra",
                    "mtime": mtime,
                    "processed_total": 150,
                    "native": {"input_tokens": 100, "output_tokens": 50}
                }
            },
            "/tmp/store/session_local_only.jsonl": {
                "days": [{"day":"2026-10-12"}],
                "record": {
                    "agent": "codex",
                    "id": "sess-local-only-456",
                    "model": "gpt-5.6-terra",
                    "mtime": mtime,
                    "processed_total": 200,
                    "native": {"input_tokens": 120, "output_tokens": 80}
                }
            }
        }
    });
    fs::write(
        codex_cache_dir.join("scan-cache.json"),
        serde_json::to_vec(&codex_cache).unwrap(),
    )
    .unwrap();

    // 3. Local telemetry scan-cache for claude
    let claude_cache_dir = home.join("telemetry/claude");
    fs::create_dir_all(&claude_cache_dir).unwrap();
    let claude_cache = json!({
        "files": {
            "/tmp/store/claude_sess.jsonl": {
                "days": [{"day":"2026-10-12"}],
                "record": {
                    "agent": "claude",
                    "id": "sess-claude-789",
                    "model": "claude-sonnet-4-terra",
                    "mtime": mtime,
                    "processed_total": 300,
                    "native": {"input_tokens": 200, "output_tokens": 100}
                }
            }
        }
    });
    fs::write(
        claude_cache_dir.join("scan-cache.json"),
        serde_json::to_vec(&claude_cache).unwrap(),
    )
    .unwrap();

    let report = ControlPlane::new(&home).usage_report(now, UsageFilter::default());

    // Total runs: 1 VC run + 2 non-duplicated local runs = 3
    assert_eq!(report.totals.runs, 3);
    assert_eq!(report.totals.tokens_total_known, 150 + 200 + 300);

    // Verify VC run retains its VC provenance
    let vc_run = report
        .runs
        .iter()
        .find(|r| r.provider_session_id == "sess-shared-123")
        .expect("vc run");
    assert_eq!(vc_run.origin, "local+vibecrafted");
    assert_eq!(vc_run.vc_run_ids, vec!["vc-codex-1"]);
    assert_eq!(vc_run.provider_session_id, "sess-shared-123");

    // Verify deduplication: sess-shared-123 is NOT duplicated as a local run
    let dup = report
        .runs
        .iter()
        .filter(|r| r.provider_session_id == "sess-shared-123")
        .count();
    assert_eq!(dup, 1);

    // Verify local-only sessions carry "local_session" provenance
    let local_codex = report
        .runs
        .iter()
        .find(|r| r.provider_session_id == "sess-local-only-456")
        .expect("local codex");
    assert_eq!(local_codex.telemetry_source, "local_session");
    assert_eq!(local_codex.agent, "codex");
    assert_eq!(local_codex.provider, "openai");
    assert_eq!(local_codex.tokens.tokens_total, 200);

    let local_claude = report
        .runs
        .iter()
        .find(|r| r.provider_session_id == "sess-claude-789")
        .expect("local claude");
    assert_eq!(local_claude.telemetry_source, "local_session");
    assert_eq!(local_claude.agent, "claude");
    assert_eq!(local_claude.provider, "anthropic");
    assert_eq!(local_claude.tokens.tokens_total, 300);

    fs::remove_dir_all(home).ok();
}
