use std::fs;

use chrono::{Duration, TimeZone, Utc};
use control_core::{ControlPlane, USAGE_REPORT_SCHEMA, UsageFilter};
use serde_json::json;

fn fixture_home(label: &str) -> std::path::PathBuf {
    // nosemgrep: rust.lang.security.temp-dir.temp-dir -- fresh atomic create below refuses pre-existing paths/symlinks; Unix mode is private before creation.
    let path = std::env::temp_dir().join(format!(
        "control-core-usage-{label}-{}-{}",
        std::process::id(),
        Utc::now().timestamp_nanos_opt().unwrap_or_default()
    ));
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder
        .create(&path)
        .expect("exclusive private usage fixture");
    path
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

    let plane = ControlPlane::new(&home);
    let filter = UsageFilter {
        since: Some(Duration::days(7)),
        since_label: "7d".into(),
        ..UsageFilter::default()
    };
    let first = plane.usage_report(now, filter.clone());
    assert_eq!(first.totals.tokens_total_known, 0);
    assert!(first.recovery.refreshing);
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    let report = loop {
        let report = plane.usage_report(now, filter.clone());
        if !report.recovery.refreshing {
            break report;
        }
        assert!(std::time::Instant::now() < deadline, "bounded recovery");
        std::thread::sleep(std::time::Duration::from_millis(20));
    };

    assert_eq!(report.totals.runs, 2);
    assert_eq!(
        report.totals.runs_tokens_unknown, 1,
        "{:?}",
        report.recovery
    );
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

// --- all-local activity -----------------------------------------------------
//
// Fixtures follow the formats the collector really leaves behind:
// `fleet_engine` entries (`days` rows + `record{id,mtime}`; codex ids are the
// rollout file stem) and the kimi harvest entry (`days` + `processed`, no
// record). The report must read them as they are.

const CODEX_UUID: &str = "0199c0de-1111-7222-8333-444455556666";

fn now() -> chrono::DateTime<Utc> {
    Utc.with_ymd_and_hms(2026, 10, 12, 12, 0, 0).unwrap()
}

fn day_row(
    day: &str,
    model: &str,
    input: u64,
    output: u64,
    read: u64,
    write: u64,
) -> serde_json::Value {
    json!({
        "day": day,
        "model": model,
        "input_tokens": input,
        "output_tokens": output,
        "cache_read_tokens": read,
        "cache_creation_tokens": write,
    })
}

fn settled_local_report(home: &std::path::Path, filter: UsageFilter) -> control_core::UsageReport {
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    loop {
        let report = ControlPlane::new(home).usage_report(now(), filter.clone());
        if !report.local_sources.iter().any(|source| source.refreshing) {
            return report;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "local projection did not settle"
        );
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
}

fn fleet_entry(
    id: &str,
    model: &str,
    mtime_offset_s: i64,
    rows: Vec<serde_json::Value>,
) -> serde_json::Value {
    let mtime = (now().timestamp() + mtime_offset_s) as f64;
    json!({
        "parser": 3,
        "size": 1,
        "mtime_ns": 1,
        "days": rows,
        "record": {"id": id, "path": "x", "mtime": mtime, "model": model,
                   "native": {}, "processed_total": 0},
    })
}

fn write_cache(home: &std::path::Path, agent: &str, files: serde_json::Value) {
    let dir = home.join("telemetry").join(agent);
    fs::create_dir_all(&dir).expect("telemetry dir");
    fs::write(
        dir.join("scan-cache.json"),
        serde_json::to_vec(&json!({"files": files})).expect("json"),
    )
    .expect("scan cache");
}

fn vc_codex_run(home: &std::path::Path, session: &str) {
    write_meta(
        home,
        "vc-codex-1",
        json!({
            "run_id": "vc-codex-1",
            "agent": "codex",
            "agent_model": "gpt-6.1-sol",
            "status": "completed",
            "exit_code": 0,
            "completed_at": "2026-10-12T11:30:00Z",
            "usage": {
                "schema": "vibecrafted.usage.v1", "unit": "tokens",
                "source": "provider_stream", "events": 1,
                "tokens_input": 100, "tokens_cached_input": 20,
                "tokens_cache_write": 0, "tokens_output": 50, "tokens_total": 150
            },
            "cost": {"amount": 0.25, "currency": "USD", "source": "estimated:test"},
            "provider_session_id": session
        }),
    );
}

fn source<'a>(
    report: &'a control_core::usage::UsageReport,
    agent: &str,
) -> &'a control_core::usage_local::LocalSource {
    report
        .local_sources
        .iter()
        .find(|item| item.agent == agent)
        .unwrap_or_else(|| panic!("local source for {agent}"))
}

#[test]
fn local_usage_reads_real_provider_formats_and_never_fabricates_completion() {
    let home = fixture_home("local-formats");
    write_cache(
        &home,
        "claude",
        json!({"/store/claude/projects/p/aaaa1111-2222-3333-4444-555566667777.jsonl":
            fleet_entry("aaaa1111-2222-3333-4444-555566667777", "claude-opus-5-5", -600,
                vec![day_row("2026-10-12", "claude-opus-5-5", 40, 10, 5, 5)])}),
    );
    write_cache(
        &home,
        "kimi",
        // The kimi harvest keeps `days` + `processed` and has no `record`.
        json!({"/home/u/.kimi-code/sessions/sess-kimi-1/wire.jsonl": {
            "parser": 1, "size": 1, "mtime_ns": 1, "processed": 90,
            "days": [day_row("2026-10-12", "kimi-k3", 60, 30, 0, 0)]}}),
    );

    let report = settled_local_report(&home, UsageFilter::default());

    assert_eq!(report.totals.runs, 2);
    assert_eq!(report.totals.tokens_total_known, 60 + 90);
    assert_eq!(report.totals.runs_failed, 0);
    for run in &report.runs {
        assert_eq!(run.origin, "local", "{}", run.run_id);
        assert_eq!(
            run.status, "observed",
            "no completed claim for host sessions"
        );
        assert_eq!(run.exit_code, None, "no fabricated exit 0");
        assert_eq!(run.cost.amount["value"], "unknown");
    }
    let claude = report
        .runs
        .iter()
        .find(|r| r.agent == "claude")
        .expect("claude");
    assert_eq!(claude.provider, "claude", "same vocabulary as VC rows");
    assert_eq!(claude.tokens.tokens_cache_write, 5);
    let kimi = report
        .runs
        .iter()
        .find(|r| r.agent == "kimi")
        .expect("kimi");
    assert_eq!(kimi.model, "kimi-k3");
    assert_eq!(kimi.provider_session_id["value"], "unknown");

    // Providers with no ledger stay visibly unavailable — never a zero row.
    let agy = source(&report, "agy");
    assert_eq!(agy.source, "unavailable");
    assert!(agy.note.contains("estimated"));
    assert_eq!(source(&report, "cursor").source, "unavailable");
    assert_eq!(source(&report, "codex").source, "absent");
    assert_eq!(
        report.local_sources.len(),
        9,
        "all supported providers are accounted for"
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn vc_run_wins_over_its_local_twin_and_is_counted_once() {
    let home = fixture_home("local-dedup");
    vc_codex_run(&home, CODEX_UUID);
    let stem = format!("rollout-2026-10-12T11-00-00-{CODEX_UUID}");
    write_cache(
        &home,
        "codex",
        json!({
            // The VC run's own session as the engine sees it: the id is the file stem.
            format!("/store/codex/sessions/2026/10/12/{stem}.jsonl"):
                fleet_entry(&stem, "gpt-6.1-sol", -1800, vec![day_row("2026-10-12", "gpt-6.1-sol", 80, 50, 20, 0)]),
            // Activity outside any VC run.
            "/store/codex/sessions/2026/10/12/rollout-2026-10-12T09-00-00-other-session-000000000001.jsonl":
                fleet_entry("rollout-2026-10-12T09-00-00-other-session-000000000001", "gpt-6.1-sol", -7200,
                    vec![day_row("2026-10-12", "gpt-6.1-sol", 120, 80, 0, 0)]),
        }),
    );

    let report = settled_local_report(&home, UsageFilter::default());

    assert_eq!(
        report.totals.runs, 2,
        "VC run + one genuinely local session"
    );
    assert_eq!(
        report.totals.tokens_total_known,
        150 + 200,
        "the twin adds nothing"
    );
    let vc = report
        .runs
        .iter()
        .find(|r| r.run_id == "vc-codex-1")
        .expect("vc");
    assert_eq!(vc.origin, "vc");
    let local: Vec<_> = report.runs.iter().filter(|r| r.origin == "local").collect();
    assert_eq!(local.len(), 1);
    assert_eq!(local[0].tokens.tokens_total, 200);
    let codex = source(&report, "codex");
    assert_eq!(
        (codex.sessions_seen, codex.matched_vc, codex.counted),
        (2, 1, 1)
    );
    // One provider row, VC and local together.
    let providers: Vec<_> = report
        .dimensions
        .providers
        .iter()
        .map(|d| d.name.as_str())
        .collect();
    assert_eq!(providers.iter().filter(|name| **name == "codex").count(), 1);
    fs::remove_dir_all(home).ok();
}

#[test]
fn a_filtered_out_vc_run_does_not_resurface_as_a_local_session() {
    let home = fixture_home("local-dedup-filtered");
    vc_codex_run(&home, CODEX_UUID);
    let stem = format!("rollout-2026-10-12T11-00-00-{CODEX_UUID}");
    write_cache(
        &home,
        "codex",
        json!({format!("/store/{stem}.jsonl"):
            fleet_entry(&stem, "gpt-6.1-sol", -1800, vec![day_row("2026-10-12", "gpt-6.1-sol", 80, 50, 20, 0)])}),
    );
    let filter = UsageFilter {
        model: Some("some-other-model".into()),
        ..UsageFilter::default()
    };

    let report = settled_local_report(&home, filter);

    assert_eq!(
        report.totals.runs, 0,
        "the twin must not come back under a filter"
    );
    assert_eq!(source(&report, "codex").matched_vc, 1);
    fs::remove_dir_all(home).ok();
}

#[test]
fn a_moved_session_file_is_counted_once() {
    let home = fixture_home("local-moved");
    let entry = fleet_entry(
        "moved-session-id-0001",
        "claude-opus-5-5",
        -60,
        vec![day_row("2026-10-12", "claude-opus-5-5", 10, 5, 0, 0)],
    );
    write_cache(
        &home,
        "claude",
        json!({"/old/moved-session-id-0001.jsonl": entry.clone(),
                                        "/new/moved-session-id-0001.jsonl": entry}),
    );

    let report = settled_local_report(&home, UsageFilter::default());

    assert_eq!(report.totals.runs, 1);
    assert_eq!(report.totals.tokens_total_known, 15);
    fs::remove_dir_all(home).ok();
}

#[test]
fn archive_fallback_is_last_wins_and_withholds_days_vc_already_covers() {
    let home = fixture_home("local-archive");
    // A VC claude run on 2026-10-12 (no session id recorded).
    write_meta(
        &home,
        "vc-claude-1",
        json!({
            "run_id": "vc-claude-1", "agent": "claude", "status": "completed", "exit_code": 0,
            "completed_at": "2026-10-12T10:00:00Z",
            "usage": {"schema": "vibecrafted.usage.v1", "unit": "tokens", "source": "provider_stream",
                      "events": 1, "tokens_input": 1, "tokens_cached_input": 0, "tokens_cache_write": 0,
                      "tokens_output": 1, "tokens_total": 2},
            "cost": {"amount": 0.01, "currency": "USD", "source": "estimated:test"}
        }),
    );
    let archive = home.join("telemetry/archive");
    fs::create_dir_all(&archive).unwrap();
    let rows = [
        // 10-12 is covered by the VC run: withheld, visibly.
        json!({"day": "2026-10-12", "model": "m", "input_tokens": 500, "output_tokens": 500}),
        // 10-11 appears twice: the later line wins.
        json!({"day": "2026-10-11", "model": "m", "input_tokens": 1, "output_tokens": 1}),
        json!({"day": "2026-10-11", "model": "m", "input_tokens": 30, "output_tokens": 10, "cache_read_tokens": 5}),
    ];
    fs::write(
        archive.join("claude.jsonl"),
        rows.iter()
            .map(|r| r.to_string())
            .collect::<Vec<_>>()
            .join("\n"),
    )
    .unwrap();

    let report = settled_local_report(&home, UsageFilter::default());

    let local: Vec<_> = report.runs.iter().filter(|r| r.origin == "local").collect();
    assert_eq!(local.len(), 1);
    assert_eq!(local[0].tokens.tokens_total, 45);
    assert_eq!(local[0].status, "observed");
    assert_eq!(local[0].exit_code, None);
    let claude = source(&report, "claude");
    assert_eq!(
        (
            claude.source.as_str(),
            claude.withheld_days,
            claude.vc_without_session_id
        ),
        ("archive", 1, 1)
    );
    assert!(claude.note.contains("withheld"));
    assert_eq!(report.totals.tokens_total_known, 2 + 45);
    fs::remove_dir_all(home).ok();
}

#[test]
fn malformed_local_projections_stay_absent_not_zero() {
    let home = fixture_home("local-malformed");
    let dir = home.join("telemetry/grok");
    fs::create_dir_all(&dir).unwrap();
    fs::write(dir.join("scan-cache.json"), b"{ not json").unwrap();
    fs::create_dir_all(home.join("telemetry/archive")).unwrap();
    fs::write(
        home.join("telemetry/archive/junie.jsonl"),
        "garbage\n{\"day\": 7}\n",
    )
    .unwrap();

    let report = settled_local_report(&home, UsageFilter::default());

    assert_eq!(report.totals.runs, 0);
    assert_eq!(source(&report, "grok").source, "absent");
    assert_eq!(source(&report, "junie").counted, 0);
    fs::remove_dir_all(home).ok();
}

#[test]
fn every_dimension_name_round_trips_through_its_filter_including_unknown() {
    let home = fixture_home("local-picker");
    vc_codex_run(&home, "sess-vc-only-1");
    write_meta(
        &home,
        "vc-unknown-model",
        json!({
            "run_id": "vc-unknown-model", "agent": "grok", "status": "completed", "exit_code": 0,
            "completed_at": "2026-10-12T11:00:00Z",
            "usage": {"schema": "vibecrafted.usage.v1", "unit": "tokens", "source": "provider_stream",
                      "events": 1, "tokens_input": 1, "tokens_cached_input": 0, "tokens_cache_write": 0,
                      "tokens_output": 1, "tokens_total": 2},
            "cost": {"amount": 0.01, "currency": "USD", "source": "estimated:test"}
        }),
    );
    write_cache(
        &home,
        "claude",
        json!({"/s/claude-local-0001.jsonl":
        fleet_entry("claude-local-0001", "claude-opus-5-5", -60, vec![day_row("2026-10-12", "claude-opus-5-5", 3, 3, 0, 0)])}),
    );
    let plane = ControlPlane::new(&home);
    let all = plane.usage_report(now(), UsageFilter::default());

    for dimension in &all.dimensions.providers {
        let filtered = plane.usage_report(
            now(),
            UsageFilter {
                provider: Some(dimension.name.clone()),
                ..UsageFilter::default()
            },
        );
        assert_eq!(
            filtered.totals.runs, dimension.totals.runs,
            "provider {}",
            dimension.name
        );
    }
    for dimension in &all.dimensions.agents {
        let filtered = plane.usage_report(
            now(),
            UsageFilter {
                agent: Some(dimension.name.clone()),
                ..UsageFilter::default()
            },
        );
        assert_eq!(
            filtered.totals.runs, dimension.totals.runs,
            "agent {}",
            dimension.name
        );
    }
    let models: Vec<_> = all
        .dimensions
        .models
        .iter()
        .map(|d| d.name.as_str())
        .collect();
    assert!(
        models.contains(&"unknown"),
        "unknown stays a selectable option: {models:?}"
    );
    for dimension in &all.dimensions.models {
        let filtered = plane.usage_report(
            now(),
            UsageFilter {
                model: Some(dimension.name.clone()),
                ..UsageFilter::default()
            },
        );
        assert_eq!(
            filtered.totals.runs, dimension.totals.runs,
            "model {}",
            dimension.name
        );
    }
    fs::remove_dir_all(home).ok();
}

#[test]
fn provider_filter_roundtrips_pool_names_and_launcher_names() {
    // The UI provider picker seeds pool vocabulary (anthropic, openai, …)
    // while most rows carry launcher names in `provider`/`agent`. Both names
    // for the same capacity must select the same runs, or the seeded picker
    // options silently filter to nothing.
    let home = fixture_home("provider-pools");
    write_meta(
        &home,
        "claude-run",
        json!({
            "run_id": "claude-run", "agent": "claude", "status": "completed", "exit_code": 0,
            "completed_at": "2026-10-12T11:00:00Z",
            "usage": {"schema": "vibecrafted.usage.v1", "unit": "tokens", "source": "provider_stream",
                      "events": 1, "tokens_input": 5, "tokens_cached_input": 0, "tokens_cache_write": 0,
                      "tokens_output": 2, "tokens_total": 7},
            "cost": {"amount": 0.01, "currency": "USD", "source": "estimated:test"}
        }),
    );
    write_meta(
        &home,
        "cursor-run",
        json!({
            "run_id": "cursor-run", "agent": "cursor", "status": "completed", "exit_code": 0,
            "completed_at": "2026-10-12T11:00:00Z",
            "usage": {"schema": "vibecrafted.usage.v1", "unit": "tokens", "source": "provider_stream",
                      "events": 1, "tokens_input": 1, "tokens_cached_input": 0, "tokens_cache_write": 0,
                      "tokens_output": 2, "tokens_total": 3},
            "cost": {"amount": 0.01, "currency": "USD", "source": "estimated:test"}
        }),
    );
    let plane = ControlPlane::new(&home);
    let filter = |provider: &str| UsageFilter {
        provider: Some(provider.into()),
        ..UsageFilter::default()
    };

    let by_pool = plane.usage_report(now(), filter("anthropic"));
    assert_eq!(
        by_pool
            .runs
            .iter()
            .map(|r| r.run_id.as_str())
            .collect::<Vec<_>>(),
        ["claude-run"],
        "pool name selects its launcher's rows"
    );
    assert_eq!(
        plane.usage_report(now(), filter("claude")).totals.runs,
        1,
        "launcher names stay valid"
    );
    let anysphere = plane.usage_report(now(), filter("anysphere"));
    assert_eq!(
        anysphere
            .runs
            .iter()
            .map(|r| r.run_id.as_str())
            .collect::<Vec<_>>(),
        ["cursor-run"]
    );
    assert_eq!(
        plane.usage_report(now(), filter("openai")).totals.runs,
        0,
        "a pool with no recorded runs is honestly empty"
    );
    // Case-insensitive, like the direct names.
    assert_eq!(
        plane.usage_report(now(), filter("Anthropic")).totals.runs,
        1
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn every_seeded_pool_name_selects_exactly_its_own_launcher() {
    // Each company-pool picker seed selects its own launcher's run.
    // Multiprovider launchers such as OMP need not have a company pool.
    let home = fixture_home("provider-pool-map");
    let pairs = [
        ("agy", "google"),
        ("claude", "anthropic"),
        ("codex", "openai"),
        ("copilot", "github"),
        ("cursor", "anysphere"),
        ("grok", "xai"),
        ("junie", "jetbrains"),
        ("kimi", "moonshot"),
    ];
    for agent in control_core::USAGE_PROVIDER_ADAPTERS {
        write_meta(
            &home,
            &format!("run-{agent}"),
            json!({
                "run_id": format!("run-{agent}"), "agent": agent, "status": "completed", "exit_code": 0,
                "completed_at": "2026-10-12T11:00:00Z",
                "usage": {"schema": "vibecrafted.usage.v1", "unit": "tokens", "source": "provider_stream",
                          "events": 1, "tokens_input": 1, "tokens_cached_input": 0, "tokens_cache_write": 0,
                          "tokens_output": 1, "tokens_total": 2},
                "cost": {"amount": 0.01, "currency": "USD", "source": "estimated:test"}
            }),
        );
    }
    let plane = ControlPlane::new(&home);
    let mut seen: std::collections::BTreeSet<&str> = std::collections::BTreeSet::new();
    for (agent, pool) in &pairs {
        assert!(seen.insert(pool), "pool {pool} listed twice");
        let report = plane.usage_report(
            now(),
            UsageFilter {
                provider: Some((*pool).into()),
                ..UsageFilter::default()
            },
        );
        let ids: Vec<_> = report.runs.iter().map(|run| run.run_id.as_str()).collect();
        assert_eq!(ids, [format!("run-{agent}")], "pool {pool}");
    }
    let omp = plane.usage_report(
        now(),
        UsageFilter {
            provider: Some("omp".into()),
            ..UsageFilter::default()
        },
    );
    assert_eq!(omp.runs.len(), 1);
    assert_eq!(omp.runs[0].run_id, "run-omp");
    fs::remove_dir_all(home).ok();
}

#[test]
fn a_local_session_without_token_measurements_stays_unknown_not_zero() {
    let home = fixture_home("local-unmeasured");
    // A real session the scanner saw, whose day rows carry day+model but no
    // token fields at all: the activity is counted, its tokens are not a
    // measured zero.
    write_cache(
        &home,
        "claude",
        json!({"/s/claude/unmeasured-0001.jsonl":
            fleet_entry("unmeasured-0001", "claude-opus-5-5", -60,
                vec![json!({"day": "2026-10-12", "model": "claude-opus-5-5"})])}),
    );
    // Same honesty on the archive fallback: a day/model row without any
    // token bucket is an unmeasured observation, never a known 0.
    let archive = home.join("telemetry/archive");
    fs::create_dir_all(&archive).unwrap();
    fs::write(
        archive.join("grok.jsonl"),
        "{\"day\": \"2026-10-12\", \"model\": \"grok-4\"}\n",
    )
    .unwrap();

    let report = settled_local_report(&home, UsageFilter::default());

    let local: Vec<_> = report.runs.iter().filter(|r| r.origin == "local").collect();
    assert_eq!(local.len(), 2, "unmeasured activity is still counted");
    for run in &local {
        assert_eq!(run.tokens.events, 0);
        for value in [
            &run.tokens.tokens_input,
            &run.tokens.tokens_cached_input,
            &run.tokens.tokens_cache_write,
            &run.tokens.tokens_output,
        ] {
            assert_eq!(value["value"], "unknown");
        }
        assert_eq!(
            run.tokens.tokens_total["value"], "unknown",
            "{} observed a session, not a 0-token measurement",
            run.run_id
        );
        assert_eq!(run.status, "observed");
        assert_eq!(run.exit_code, None);
    }
    assert_eq!(report.totals.runs_tokens_unknown, 2);
    assert_eq!(
        report.totals.tokens_total_known, 0,
        "no fabricated known zero enters the totals"
    );
    // A measured row next to an unmeasured one keeps its own numbers.
    assert_eq!(source(&report, "claude").counted, 1);
    assert_eq!(source(&report, "grok").counted, 1);
    fs::remove_dir_all(home).ok();
}

#[test]
fn a_measured_zero_token_bucket_stays_known_in_scan_and_archive() {
    let home = fixture_home("local-measured-zero");
    write_cache(
        &home,
        "claude",
        json!({"/s/claude/measured-zero.jsonl": fleet_entry("measured-zero", "private-model", -60,
        vec![json!({"day":"2026-10-12", "model":"private-model", "input_tokens":0, "output_tokens":0, "cache_read_tokens":0, "cache_creation_tokens":0})])}),
    );
    let archive = home.join("telemetry/archive");
    fs::create_dir_all(&archive).expect("private archive");
    fs::write(archive.join("grok.jsonl"), json!({"day":"2026-10-12", "model":"private-model", "input_tokens":0, "output_tokens":0, "cache_read_tokens":0, "cache_creation_tokens":0}).to_string()+"\n").expect("zero row");
    let report = settled_local_report(&home, UsageFilter::default());
    assert_eq!(report.runs.len(), 2);
    for run in &report.runs {
        assert_eq!(run.tokens.events, 1, "a real zero is measured");
        for value in [
            &run.tokens.tokens_input,
            &run.tokens.tokens_cached_input,
            &run.tokens.tokens_cache_write,
            &run.tokens.tokens_output,
            &run.tokens.tokens_total,
        ] {
            assert_eq!(value.as_u64(), Some(0));
        }
        assert_eq!(run.status, "observed");
        assert_eq!(run.exit_code, None);
    }
    assert_eq!(report.totals.runs_tokens_unknown, 0);
    fs::remove_dir_all(home).ok();
}

#[test]
fn omp_missing_projection_and_unmeasured_archive_never_become_known_zero() {
    let home = fixture_home("omp-local-unknown");
    let plane = ControlPlane::new(&home);
    let filter = UsageFilter {
        agent: Some("omp".to_string()),
        ..UsageFilter::default()
    };
    let absent = plane.usage_report(now(), filter.clone());
    assert_eq!(source(&absent, "omp").source, "absent");
    assert_eq!(absent.totals.runs, 0);

    let archive = home.join("telemetry/archive");
    fs::create_dir_all(&archive).unwrap();
    fs::write(
        archive.join("omp.jsonl"),
        concat!(
            "{\"day\":\"2026-10-12\",\"model\":\"unmeasured-provider-model\"}\n",
            "{\"day\":\"2026-10-12\",\"model\":\"other-provider-model\",",
            "\"input_tokens\":7,\"output_tokens\":3,\"cache_read_tokens\":2,\"cache_creation_tokens\":1}\n"
        ),
    )
    .unwrap();
    let report = settled_local_report(&home, filter);
    assert_eq!(source(&report, "omp").source, "archive");
    assert_eq!(source(&report, "omp").counted, 2);
    assert_eq!(report.totals.runs, 2);
    assert_eq!(report.totals.runs_tokens_unknown, 1);
    assert_eq!(report.totals.tokens_total_known, 13);
    let unmeasured = report
        .runs
        .iter()
        .find(|run| run.model == "unmeasured-provider-model")
        .expect("unmeasured OMP activity remains visible");
    assert_eq!(unmeasured.tokens.tokens_total["value"], "unknown");
    for run in &report.runs {
        assert_eq!(run.agent, "omp");
        assert_eq!(run.origin, "local");
        assert_eq!(run.status, "observed");
        assert_eq!(run.exit_code, None);
        assert_eq!(run.cost.amount["value"], "unknown");
    }
    assert_eq!(control_core::usage::provider_pool("omp"), None);
    fs::remove_dir_all(home).ok();
}

#[test]
fn a_native_sized_local_cache_never_blocks_requests_and_retains_failed_refresh() {
    use std::io::Write;
    use std::time::{Duration as StdDuration, Instant};
    let home = fixture_home("large-cache");
    let dir = home.join("telemetry/claude");
    fs::create_dir_all(&dir).unwrap();
    let path = dir.join("scan-cache.json");
    // Same native fleet shape as production: useful day buckets beside large
    // parser state in record.native. Only synthetic repeated bytes are stored.
    let mut file = fs::File::create(&path).unwrap();
    write!(file, "{{\"files\":{{\"/synthetic/large-session.jsonl\":{{\"days\":[{{\"day\":\"2026-10-12\",\"model\":\"model-A\",\"input_tokens\":40,\"output_tokens\":11}}],\"record\":{{\"id\":\"large-session\",\"mtime\":{},\"native\":\"", now().timestamp()).unwrap();
    let block = vec![b'x'; 1024 * 1024];
    for _ in 0..211 {
        file.write_all(&block).unwrap();
    }
    file.write_all(b"\"}}}}").unwrap();
    drop(file);
    let start = Instant::now();
    let first = ControlPlane::new(&home).usage_report(now(), UsageFilter::default());
    assert!(
        start.elapsed() < StdDuration::from_millis(500),
        "foreground read the native-sized ledger"
    );
    assert!(first.local_sources.iter().any(|s| s.refreshing));
    assert_eq!(source(&first, "agy").source, "unavailable");
    assert_eq!(source(&first, "cursor").source, "unavailable");
    assert_eq!(
        first.totals.runs, 0,
        "unknown pending data must not masquerade as a measured session"
    );
    let mut requests = Vec::new();
    for _ in 0..8 {
        let home = home.clone();
        requests.push(std::thread::spawn(move || {
            let start = Instant::now();
            let report = ControlPlane::new(&home).usage_report(now(), UsageFilter::default());
            assert!(start.elapsed() < StdDuration::from_millis(500));
            report
        }));
    }
    for request in requests {
        request.join().unwrap();
    }
    let deadline = Instant::now() + StdDuration::from_secs(30);
    let complete = loop {
        let report = ControlPlane::new(&home).usage_report(now(), UsageFilter::default());
        if !report.local_sources.iter().any(|s| s.refreshing) {
            break report;
        }
        assert!(Instant::now() < deadline);
        std::thread::sleep(StdDuration::from_millis(20));
    };
    assert_eq!(complete.totals.tokens_total_known, 51);
    assert_eq!(source(&complete, "claude").counted, 1);
    let stamp = source(&complete, "claude").refreshed_at.clone();
    for _ in 0..3 {
        let report = settled_local_report(
            &home,
            UsageFilter {
                model: Some("model-A".into()),
                ..Default::default()
            },
        );
        assert_eq!(report.totals.tokens_total_known, 51);
        assert_eq!(
            source(&report, "claude").refreshed_at,
            stamp,
            "unchanged source must reuse the completed projection"
        );
        let other = settled_local_report(
            &home,
            UsageFilter {
                model: Some("model-B".into()),
                ..Default::default()
            },
        );
        assert_eq!(
            other.totals.runs, 0,
            "one cached projection must preserve filter scope"
        );
    }
    fs::write(&path, b"invalid replacement").unwrap();
    let previous = ControlPlane::new(&home).usage_report(now(), UsageFilter::default());
    assert_eq!(previous.totals.tokens_total_known, 51);
    assert!(source(&previous, "claude").stale);
    let failed = settled_local_report(&home, UsageFilter::default());
    assert_eq!(failed.totals.tokens_total_known, 51);
    assert!(source(&failed, "claude").stale);
    assert_eq!(
        source(&failed, "claude").error.as_deref(),
        Some("scan_cache_unreadable")
    );
    write_cache(
        &home,
        "claude",
        json!({"/synthetic/large-session.jsonl": fleet_entry("large-session", "model-A", -1, vec![day_row("2026-10-12", "model-A", 71, 11, 0, 0)])}),
    );
    let repaired = settled_local_report(&home, UsageFilter::default());
    assert_eq!(repaired.totals.tokens_total_known, 82);
    assert!(!source(&repaired, "claude").stale);
    assert!(source(&repaired, "claude").error.is_none());
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        struct Restore(std::path::PathBuf);
        impl Drop for Restore {
            fn drop(&mut self) {
                fs::set_permissions(&self.0, fs::Permissions::from_mode(0o700)).unwrap();
            }
        }
        let restore = Restore(dir.clone());
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o000)).unwrap();
        let denied = settled_local_report(&home, UsageFilter::default());
        assert_eq!(
            denied.totals.tokens_total_known, 82,
            "unreadable is not absent"
        );
        assert!(source(&denied, "claude").stale);
        assert_eq!(
            source(&denied, "claude").error.as_deref(),
            Some("projection_source_unreadable")
        );
        drop(restore);
        let restored = settled_local_report(&home, UsageFilter::default());
        assert_eq!(restored.totals.tokens_total_known, 82);
        assert!(!source(&restored, "claude").stale);
    }
    write_meta(
        &home,
        "new-vc-twin",
        json!({
            "run_id":"new-vc-twin", "agent":"claude", "provider_session_id":"large-session",
            "completed_at":now().to_rfc3339(), "usage":{"tokens_total":82},
            "cost":{"amount":0.1,"currency":"USD"}
        }),
    );
    let pending = ControlPlane::new(&home).usage_report(now(), UsageFilter::default());
    assert_eq!(
        pending.totals.tokens_total_known, 82,
        "an old local snapshot must not double-count a newly recorded VC twin"
    );
    let deduplicated = settled_local_report(&home, UsageFilter::default());
    assert_eq!(deduplicated.totals.tokens_total_known, 82);
    assert_eq!(source(&deduplicated, "claude").matched_vc, 1);
    assert_eq!(source(&deduplicated, "claude").counted, 0);
    fs::remove_dir_all(home).unwrap();
}
