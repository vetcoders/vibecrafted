//! Real provider stores through the real `ccusage` adapters, end to end.
//!
//! Inputs under `tests/fixtures/ccusage/<provider>` are scrubbed excerpts of
//! real sessions. `oracle/<provider>.json` is what `npx ccusage@20.0.47` printed
//! for them with `--json --offline`, the release matching the pinned git rev.
//! Tokens must equal the oracle. Costs must too, except where [`EXPECTED`] says
//! why the oracle is wrong and which value replaces it.
//!
//! Provider stores are found through process-wide environment variables, so the
//! environment is set once before anything else runs, and collections are
//! serialized because the collector reports a concurrent call as skipped.

#[path = "support/ccusage_env.rs"]
mod ccusage_env;

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, Once, OnceLock, PoisonError};
use std::time::{Duration, Instant};

use chrono::{DateTime, Utc};
use control_core::usage::UsageRun;
use control_core::usage_collect::CollectOutcome;
use control_core::usage_local::LocalSource;
use control_core::{ControlPlane, UsageFilter, UsageReport};
use serde_json::{Value, json};

use ccusage_env::{point_provider_stores_at, private_dir};

const UNPRICED_SESSION: &str = "c1a0de00-0000-4000-8000-00000000000a";
const UNPRICED_MODEL: &str = "vendor-unreleased-99";
const PROJECTION: &str = "ccusage-sessions.json";

struct Expected {
    agent: &'static str,
    session: &'static str,
    model: &'static str,
    input: u64,
    cache_read: u64,
    cache_write: u64,
    output: u64,
    total: u64,
    cost: f64,
    oracle_cost: f64,
    /// Exact source, or the prefix when it ends with a colon.
    cost_source: &'static str,
    last_activity: &'static str,
}

const EXPECTED: [Expected; 5] = [
    Expected {
        agent: "claude",
        session: "c1a0de00-0000-4000-8000-000000000001",
        model: "claude-opus-5-5",
        input: 6,
        cache_read: 158_770,
        cache_write: 119_133,
        output: 1_109,
        total: 279_018,
        cost: 0.649_623,
        oracle_cost: 0.649_623,
        cost_source: "estimated:",
        last_activity: "2026-11-02T04:47:31Z",
    },
    Expected {
        agent: "codex",
        // The oracle keys this session by its store path. A report row carries
        // the same bare thread id a Vibecrafted run records, and no path.
        session: "01a0de00-0000-7000-8000-000000000002",
        model: "gpt-6.1-sol",
        input: 76_932,
        cache_read: 290_944,
        cache_write: 0,
        output: 2_273,
        total: 370_149,
        cost: 0.205_688_4,
        oracle_cost: 0.205_688_4,
        cost_source: "estimated:",
        last_activity: "2026-11-02T10:00:34Z",
    },
    Expected {
        agent: "grok",
        session: "01a0de00-0000-7000-8000-000000000003",
        model: "grok-4.7-build-fast",
        input: 829_274,
        cache_read: 7_437_056,
        cache_write: 0,
        output: 27_983,
        total: 8_294_313,
        // `costUsdTicks` written by the provider, not a price list.
        cost: 11.089_948,
        oracle_cost: 11.089_948,
        cost_source: "provider_reported",
        last_activity: "2026-11-02T10:49:28Z",
    },
    Expected {
        agent: "kimi",
        session: "session_01a0de00-0000-4000-8000-000000000004",
        model: "k3",
        input: 64_842,
        cache_read: 216_832,
        cache_write: 0,
        output: 2_254,
        total: 283_928,
        // Upstream resolves the bare `k3` fuzzily to `kimi-k3-fast` (input 4.5
        // per million). `kimi-code/k3` bills at Moonshot's k3 rate:
        // 64842*3e-6 + 216832*0.3e-6 + 2254*15e-6.
        cost: 0.293_385_6,
        oracle_cost: 0.440_078_4,
        cost_source: "estimated:",
        last_activity: "2026-10-14T17:06:16Z",
    },
    Expected {
        agent: "copilot",
        session: "01a0de00-0000-4000-8000-000000000005",
        model: "gpt-6.1-sol",
        input: 219,
        cache_read: 8_847_878,
        cache_write: 184_842,
        output: 33_298,
        total: 9_066_237,
        // Upstream prices one shutdown aggregate as a single request, so the
        // whole session crosses the 272K context tier. An aggregate says nothing
        // about any request's context, so it takes the base rate:
        // 219*2e-6 + 33298*1e-5 + 184842*2.5e-6 + 8847878*1e-7.
        // The session's billed credits (`totalNanoAiu`/1e11) are 1.51228.
        cost: 1.680_310_8,
        oracle_cost: 3.194_131_6,
        cost_source: "estimated:",
        last_activity: "2026-10-25T07:48:34Z",
    },
];

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/ccusage")
}

fn prepare_environment() {
    static ENVIRONMENT: Once = Once::new();
    ENVIRONMENT.call_once(|| {
        let host = private_dir("host");
        // SAFETY: every test passes through this Once before it reads anything,
        // and no test starts a collector or projection before that.
        // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- established under the Once above, before any collector or projection starts.
        unsafe { point_provider_stores_at(&fixtures(), &host) };
    });
}

fn fresh_home(label: &str) -> PathBuf {
    prepare_environment();
    private_dir(label)
}

fn collect(home: &Path) -> CollectOutcome {
    static COLLECTIONS: Mutex<()> = Mutex::new(());
    prepare_environment();
    let _flight = COLLECTIONS.lock().unwrap_or_else(PoisonError::into_inner);
    let outcome = ControlPlane::new(home).collect_local_usage(Duration::ZERO);
    assert!(outcome.started, "{outcome:?}");
    outcome
}

fn settled(home: &Path) -> UsageReport {
    let deadline = Instant::now() + Duration::from_secs(20);
    loop {
        let report = ControlPlane::new(home).usage_report(Utc::now(), UsageFilter::default());
        if !report.recovery.refreshing && !report.local_sources.iter().any(|item| item.refreshing) {
            return report;
        }
        assert!(Instant::now() < deadline, "usage projection did not settle");
        std::thread::sleep(Duration::from_millis(20));
    }
}

struct Collected {
    outcome: CollectOutcome,
    report: UsageReport,
}

/// One collection over the fixtures, read by every test that does not mutate it.
fn collected() -> &'static Collected {
    static SHARED: OnceLock<Collected> = OnceLock::new();
    SHARED.get_or_init(|| {
        let home = fresh_home("shared");
        let outcome = collect(&home);
        let report = settled(&home);
        Collected { outcome, report }
    })
}

fn source<'a>(report: &'a UsageReport, agent: &str) -> &'a LocalSource {
    report
        .local_sources
        .iter()
        .find(|item| item.agent == agent)
        .unwrap_or_else(|| panic!("local source for {agent}"))
}

fn local_row<'a>(report: &'a UsageReport, agent: &str, session: &str) -> &'a UsageRun {
    report
        .runs
        .iter()
        .find(|run| {
            run.origin == "local" && run.agent == agent && run.provider_session_id == session
        })
        .unwrap_or_else(|| {
            let seen = report
                .runs
                .iter()
                .map(|run| format!("{} {}", run.agent, run.provider_session_id))
                .collect::<Vec<_>>();
            panic!("no local {agent} row for {session}; rows: {seen:?}")
        })
}

fn assert_close(actual: Option<f64>, expected: f64, label: &str) {
    let actual = actual.unwrap_or_else(|| panic!("{label}: cost is not a number"));
    assert!(
        (actual - expected).abs() < 1e-9,
        "{label}: {actual} != {expected}"
    );
}

fn usd(run: &UsageRun) -> Option<&str> {
    run.cost.currency.as_deref().or(run.cost.unit.as_deref())
}

fn write_meta(home: &Path, run_id: &str, value: Value) -> PathBuf {
    let run = home.join("control_plane/runtime_runs").join(run_id);
    fs::create_dir_all(&run).expect("runtime run dir");
    fs::write(
        run.join("meta.json"),
        serde_json::to_vec_pretty(&value).expect("json"),
    )
    .expect("meta");
    run
}

fn recorded_usage(total: u64) -> Value {
    json!({
        "schema": "vibecrafted.usage.v1",
        "unit": "tokens",
        "source": "provider_stream",
        "events": 1,
        "tokens_input": total,
        "tokens_cached_input": 0,
        "tokens_cache_write": 0,
        "tokens_output": 0,
        "tokens_total": total
    })
}

#[test]
fn fixtures_still_match_the_oracle_that_measured_them() {
    for expected in &EXPECTED {
        let path = fixtures().join(format!("oracle/{}.json", expected.agent));
        let oracle: Value =
            serde_json::from_slice(&fs::read(&path).expect("oracle")).expect("json");
        let rows = oracle
            .get("session")
            .or_else(|| oracle.get("sessions"))
            .and_then(Value::as_array)
            .expect("oracle rows");
        let row = rows
            .iter()
            .find(|row| {
                row.get("period")
                    .or_else(|| row.get("sessionId"))
                    .and_then(Value::as_str)
                    .is_some_and(|id| id.contains(expected.session))
            })
            .unwrap_or_else(|| panic!("oracle row for {}", expected.session));
        assert_eq!(row["inputTokens"], expected.input, "{}", expected.agent);
        assert_eq!(row["outputTokens"], expected.output, "{}", expected.agent);
        assert_eq!(
            row["cacheReadTokens"], expected.cache_read,
            "{}",
            expected.agent
        );
        assert_eq!(
            row["cacheCreationTokens"], expected.cache_write,
            "{}",
            expected.agent
        );
        assert_eq!(row["totalTokens"], expected.total, "{}", expected.agent);
        assert_close(
            row.get("totalCost")
                .or_else(|| row.get("costUSD"))
                .and_then(Value::as_f64),
            expected.oracle_cost,
            expected.agent,
        );
    }
}

#[test]
fn every_provider_session_reports_measured_tokens_and_a_labelled_cost() {
    let report = &collected().report;
    for expected in &EXPECTED {
        let label = expected.agent;
        let row = local_row(report, label, expected.session);
        assert_eq!(row.status, "observed", "{label}");
        assert_eq!(row.exit_code, None, "{label}");
        assert_eq!(row.provider, label, "{label}");
        assert_eq!(row.model, expected.model, "{label}");
        assert_eq!(row.telemetry_source, format!("ccusage:{label}"), "{label}");
        assert_eq!(row.tokens.source, format!("ccusage:{label}"), "{label}");
        assert!(
            row.tokens.events > 0,
            "{label}: measured session has events"
        );
        assert_eq!(
            row.tokens.tokens_input.as_u64(),
            Some(expected.input),
            "{label}"
        );
        assert_eq!(
            row.tokens.tokens_cached_input.as_u64(),
            Some(expected.cache_read),
            "{label}"
        );
        assert_eq!(
            row.tokens.tokens_cache_write.as_u64(),
            Some(expected.cache_write),
            "{label}"
        );
        assert_eq!(
            row.tokens.tokens_output.as_u64(),
            Some(expected.output),
            "{label}"
        );
        assert_eq!(
            row.tokens.tokens_total.as_u64(),
            Some(expected.total),
            "{label}"
        );
        assert_close(row.cost.amount.as_f64(), expected.cost, label);
        assert_eq!(usd(row), Some("USD"), "{label}");
        assert!(
            row.cost.source.starts_with(expected.cost_source),
            "{label}: cost source {}",
            row.cost.source
        );
        assert!(row.unpriced_models.is_empty(), "{label}");
        let recorded = DateTime::parse_from_rfc3339(&row.recorded_at).expect("recorded_at");
        let activity = DateTime::parse_from_rfc3339(expected.last_activity).expect("fixture");
        assert_eq!(
            recorded.timestamp(),
            activity.timestamp(),
            "{label}: recorded_at is the session's last activity, not the collection"
        );
    }
}

#[test]
fn a_model_without_a_price_keeps_its_tokens_and_an_unknown_cost() {
    let report = &collected().report;
    let row = local_row(report, "claude", UNPRICED_SESSION);
    assert_eq!(row.model, UNPRICED_MODEL);
    assert_eq!(row.tokens.tokens_input.as_u64(), Some(4));
    assert_eq!(row.tokens.tokens_cached_input.as_u64(), Some(70_306));
    assert_eq!(row.tokens.tokens_cache_write.as_u64(), Some(88_464));
    assert_eq!(row.tokens.tokens_output.as_u64(), Some(525));
    assert_eq!(row.tokens.tokens_total.as_u64(), Some(159_299));
    assert_eq!(
        row.cost.amount["value"], "unknown",
        "the oracle says 0 here"
    );
    assert_eq!(row.cost.amount.as_f64(), None);
    assert_eq!(row.unpriced_models, vec![UNPRICED_MODEL.to_string()]);
    assert!(report.unpriced_models.iter().any(|m| m == UNPRICED_MODEL));
}

#[test]
fn totals_add_only_what_was_measured() {
    let totals = &collected().report.totals;
    assert_eq!(totals.runs, 6);
    assert_eq!(totals.runs_failed, 0);
    assert_eq!(totals.tokens_total_known, 18_452_944);
    assert_eq!(totals.runs_tokens_unknown, 0);
    assert_eq!(totals.runs_cost_unknown, 1);
    assert_close(
        totals.cost_by_unit.get("USD").copied(),
        13.918_955_8,
        "USD total",
    );
}

#[test]
fn every_provider_is_accounted_for_and_only_measured_ones_claim_ccusage() {
    let report = &collected().report;
    assert_eq!(report.local_sources.len(), 9);
    for (agent, seen) in [
        ("claude", 2),
        ("codex", 1),
        ("grok", 1),
        ("kimi", 1),
        ("copilot", 1),
    ] {
        let item = source(report, agent);
        assert_eq!(item.source, "ccusage", "{agent}");
        assert_eq!(item.sessions_seen, seen, "{agent}");
        assert_eq!(item.counted, seen, "{agent}");
        assert_eq!(item.matched_vc, 0, "{agent}");
        assert_eq!(item.error, None, "{agent}");
        assert!(!item.stale, "{agent}");
        assert!(item.refreshed_at.is_some(), "{agent}");
    }
    for agent in ["agy", "cursor"] {
        let item = source(report, agent);
        assert_eq!(
            item.source, "unavailable",
            "{agent}: no store is not a zero"
        );
        assert!(!item.note.is_empty(), "{agent}");
        assert_eq!(item.counted, 0, "{agent}");
    }
    for agent in ["junie", "omp"] {
        assert_ne!(source(report, agent).source, "ccusage", "{agent}");
    }
}

#[test]
fn the_outcome_names_each_provider_once() {
    let outcome = &collected().outcome;
    assert_eq!(outcome.agents.len(), 9, "{outcome:?}");
    for agent in [
        "agy", "claude", "codex", "copilot", "cursor", "grok", "junie", "kimi", "omp",
    ] {
        let rows = outcome
            .agents
            .iter()
            .filter(|item| item.agent == agent)
            .collect::<Vec<_>>();
        assert_eq!(rows.len(), 1, "{agent}: {outcome:?}");
    }
    let status = |agent: &str| {
        outcome
            .agents
            .iter()
            .find(|item| item.agent == agent)
            .map(|item| item.status.as_str())
    };
    for agent in ["claude", "codex", "copilot", "grok", "kimi"] {
        assert_eq!(status(agent), Some("ok"), "{agent}: {outcome:?}");
    }
    assert_eq!(status("agy"), Some("unavailable"), "{outcome:?}");
}

#[test]
fn collection_writes_one_projection_per_measured_provider() {
    let home = fresh_home("projection");
    collect(&home);
    for agent in ["claude", "codex", "copilot", "grok", "kimi"] {
        let path = home.join("telemetry").join(agent).join(PROJECTION);
        let projection: Value =
            serde_json::from_slice(&fs::read(&path).expect("projection")).expect("json");
        assert_eq!(
            projection["schema"], "vibecrafted.ccusage-sessions.v1",
            "{agent}"
        );
    }
    assert!(
        !home.join("telemetry/agy").join(PROJECTION).exists(),
        "no store, no projection"
    );
}

#[test]
fn a_report_never_reads_a_provider_store_itself() {
    let home = fresh_home("hermetic");
    let report = settled(&home);
    assert!(report.runs.is_empty());
    assert_eq!(source(&report, "claude").source, "absent");
    assert!(
        !home.join("telemetry").exists(),
        "only an explicit collection may write projections"
    );
}

#[test]
fn a_report_carries_no_provider_store_paths() {
    let raw = serde_json::to_string(&collected().report).expect("json");
    for leak in [
        "fixtures/ccusage",
        "-fixture-project",
        "rollout-",
        "wd_fixture",
    ] {
        assert!(!raw.contains(leak), "report leaks {leak}");
    }
}

#[test]
fn a_recorded_run_wins_over_its_local_twin() {
    let home = fresh_home("twins");
    collect(&home);
    // Exactly what each provider's runs record as `provider_session_id`.
    let twins = [
        ("claude", "c1a0de00-0000-4000-8000-000000000001"),
        ("codex", "01a0de00-0000-7000-8000-000000000002"),
        ("grok", "01a0de00-0000-7000-8000-000000000003"),
        ("kimi", "01a0de00-0000-4000-8000-000000000004"),
        ("copilot", "01a0de00-0000-4000-8000-000000000005"),
    ];
    for (agent, session) in twins {
        write_meta(
            &home,
            &format!("vc-{agent}"),
            json!({
                "run_id": format!("vc-{agent}"),
                "agent": agent,
                "agent_model": "fixture-model",
                "status": "completed",
                "exit_code": 0,
                "completed_at": "2026-11-02T12:00:00Z",
                "usage": recorded_usage(1),
                "provider_session_id": session
            }),
        );
    }

    let report = settled(&home);

    let local = report
        .runs
        .iter()
        .filter(|run| run.origin == "local")
        .collect::<Vec<_>>();
    assert_eq!(local.len(), 1, "only the unmatched session stays");
    assert_eq!(local[0].provider_session_id, UNPRICED_SESSION);
    for (agent, _) in twins {
        assert_eq!(source(&report, agent).matched_vc, 1, "{agent}");
    }
    assert_eq!(source(&report, "claude").counted, 1);
    for agent in ["codex", "grok", "kimi", "copilot"] {
        assert_eq!(source(&report, agent).counted, 0, "{agent}");
    }
    assert_eq!(report.totals.runs, 6);
    assert_eq!(report.totals.tokens_total_known, 5 + 159_299);
}

fn write_projection(home: &Path, agent: &str, bytes: &[u8]) {
    let dir = home.join("telemetry").join(agent);
    fs::create_dir_all(&dir).expect("telemetry dir");
    fs::write(dir.join(PROJECTION), bytes).expect("projection");
}

#[test]
fn an_unreadable_projection_is_an_error_and_not_a_zero() {
    for (label, bytes) in [
        ("truncated", b"{".as_slice()),
        (
            "foreign-schema",
            br#"{"schema":"vibecrafted.other.v9","sessions":[]}"#.as_slice(),
        ),
    ] {
        let home = fresh_home(label);
        write_projection(&home, "claude", bytes);

        let report = settled(&home);

        let claude = source(&report, "claude");
        assert_eq!(
            claude.error.as_deref(),
            Some("ccusage_unreadable"),
            "{label}"
        );
        assert!(!claude.stale, "{label}: nothing to retain yet");
        assert_eq!(claude.counted, 0, "{label}");
        assert_eq!(report.totals.tokens_total_known, 0, "{label}");
        let raw = serde_json::to_string(&report).expect("json");
        assert!(!raw.contains(PROJECTION), "{label}: reason code only");
        assert!(
            !raw.contains(home.to_str().expect("utf-8 home")),
            "{label}: no raw path"
        );
    }
}

#[test]
fn a_failed_refresh_retains_previous_measurements_until_repaired() {
    let home = fresh_home("retain");
    collect(&home);
    let good = settled(&home);
    assert_eq!(source(&good, "claude").counted, 2);

    write_projection(&home, "claude", b"{");
    let failed = settled(&home);

    let claude = source(&failed, "claude");
    assert_eq!(claude.error.as_deref(), Some("ccusage_unreadable"));
    assert!(claude.stale);
    local_row(&failed, "claude", EXPECTED[0].session);
    assert_eq!(failed.totals.tokens_total_known, 18_452_944);

    collect(&home);
    let repaired = settled(&home);
    let claude = source(&repaired, "claude");
    assert_eq!(claude.error, None);
    assert!(!claude.stale);
    assert_eq!(claude.counted, 2);
}

#[test]
fn model_names_are_validated_before_they_reach_a_report() {
    let cases = [
        (
            "effective-wins",
            json!({"model_effective": "claude-opus-5-5", "model_requested": "claude-sonnet-5-5", "agent_model": "other"}),
            Some("claude-opus-5-5"),
        ),
        (
            "requested-next",
            json!({"model_requested": "gpt-6.1-sol", "model": "unknown"}),
            Some("gpt-6.1-sol"),
        ),
        (
            "garbage-effective-falls-through",
            json!({"model_effective": "&str)", "model_requested": "gpt-6.1-sol"}),
            Some("gpt-6.1-sol"),
        ),
        (
            "agent-model",
            json!({"agent_model": "grok-4.7-build-fast"}),
            Some("grok-4.7-build-fast"),
        ),
        (
            "context-suffix-is-a-real-label",
            json!({"agent_model": "claude-opus-5-5[1m]"}),
            Some("claude-opus-5-5[1m]"),
        ),
        (
            "display-name-is-a-real-label",
            json!({"agent_model": "Grok 4.7 256K Extra High Fast"}),
            Some("Grok 4.7 256K Extra High Fast"),
        ),
        (
            "provider-qualified",
            json!({"model": "fixture-provider/fixture-model"}),
            Some("fixture-provider/fixture-model"),
        ),
        ("code-fragment", json!({"agent_model": "&str)"}), None),
        (
            "glued-header",
            json!({"agent_model": "grok-4.7-build-fastmodel_source:clieffort:xhigh"}),
            None,
        ),
        ("synthetic", json!({"agent_model": "<synthetic>"}), None),
        (
            "launcher-default",
            json!({"agent_model": "codex-cli-default"}),
            None,
        ),
        (
            "agy-default",
            json!({"agent_model": "gemini-cli-default"}),
            None,
        ),
        ("angle-inside", json!({"agent_model": "foo<bar>"}), None),
        ("angle-trailing", json!({"agent_model": "model>"}), None),
        (
            "default-in-any-case",
            json!({"agent_model": "Codex-CLI-DEFAULT"}),
            None,
        ),
        (
            "glue-in-any-case",
            json!({"agent_model": "Grok_SOURCE:x"}),
            None,
        ),
        ("placeholder", json!({"model": "pending"}), None),
        ("python-none", json!({"model": "None"}), None),
        ("empty", json!({"model": ""}), None),
        (
            "already-unknown",
            json!({"model": {"value": "unknown", "reason": "fixture"}}),
            None,
        ),
    ];
    let home = fresh_home("models");
    for (index, (label, fields, _)) in cases.iter().enumerate() {
        let mut meta = json!({
            "run_id": format!("model-{index}"),
            "agent": "codex",
            "status": "completed",
            "exit_code": 0,
            "completed_at": "2026-11-02T12:00:00Z",
            "usage": recorded_usage(1),
            "case": label
        });
        let object = meta.as_object_mut().expect("object");
        object.extend(fields.as_object().expect("fields").clone());
        write_meta(&home, &format!("model-{index}"), meta);
    }

    let report = settled(&home);

    for (index, (label, _, expected)) in cases.iter().enumerate() {
        let run_id = format!("model-{index}");
        let run = report
            .runs
            .iter()
            .find(|run| run.run_id == run_id)
            .unwrap_or_else(|| panic!("{label}: row"));
        match expected {
            Some(model) => assert_eq!(run.model, *model, "{label}"),
            None => assert_eq!(run.model["value"], "unknown", "{label}: {}", run.model),
        }
    }
}

fn measured_meta(run_id: &str, agent: &str, model: &str, measured: Value) -> Value {
    json!({
        "run_id": run_id,
        "agent": agent,
        "status": "completed",
        "exit_code": 0,
        "completed_at": "2026-11-01T12:00:00Z",
        "model_effective": model,
        "model_requested": model,
        "usage": null,
        "cost": null,
        "measured_usage": measured,
        "usage_measurement": {
            "source": "claude-transcript-jsonl-v1",
            "attribution": "fixture",
            "monotonic": true
        }
    })
}

/// Shape of a real closed run: `usage` and `cost` null, tokens measured.
fn real_measured_usage() -> Value {
    json!({
        "input_tokens": 3974,
        "cache_creation_input_tokens": 4_802_133,
        "cache_read_input_tokens": 439_996_799,
        "output_tokens": 1_428_316,
        "total_tokens": 446_231_222,
        "messages": 1824,
        "foreign_workspace_events": 228
    })
}

const PROSE_TRANSCRIPT: &str = concat!(
    "Total cost: $0.0000\n",
    "cost_usd: 0.0\n",
    "pub fn model: &str) -> bool\n",
    "model: &str)\n",
);

#[test]
fn measured_usage_is_reported_as_recorded_and_ignores_the_transcript() {
    let home = fresh_home("measured");
    let run = write_meta(
        &home,
        "measured",
        measured_meta(
            "measured",
            "claude",
            "claude-opus-5-5",
            real_measured_usage(),
        ),
    );
    fs::write(run.join("transcript.log"), PROSE_TRANSCRIPT).expect("transcript");

    let report = ControlPlane::new(&home).usage_report(Utc::now(), UsageFilter::default());

    assert!(
        !report.recovery.refreshing,
        "a measured run needs no recovery"
    );
    assert_eq!(report.recovery.pending_runs, 0);
    let row = &report.runs[0];
    assert_eq!(row.model, "claude-opus-5-5");
    assert_eq!(row.telemetry_source, "meta");
    assert_eq!(row.tokens.source, "claude-transcript-jsonl-v1");
    assert_eq!(row.tokens.events, 1824);
    assert_eq!(row.tokens.tokens_input.as_u64(), Some(3974));
    assert_eq!(row.tokens.tokens_cached_input.as_u64(), Some(439_996_799));
    assert_eq!(row.tokens.tokens_cache_write.as_u64(), Some(4_802_133));
    assert_eq!(row.tokens.tokens_output.as_u64(), Some(1_428_316));
    assert_eq!(row.tokens.tokens_total.as_u64(), Some(446_231_222));
    // 3974*4e-6 + 4802133*5e-6 + 439996799*2e-7 + 1428316*2e-5
    assert_close(row.cost.amount.as_f64(), 140.592_240_8, "measured cost");
    assert_eq!(usd(row), Some("USD"));
    assert!(
        row.cost.source.starts_with("estimated:"),
        "{}",
        row.cost.source
    );
    assert_eq!(report.totals.tokens_total_known, 446_231_222);
}

#[test]
fn measured_usage_of_an_unpriced_model_keeps_an_unknown_cost() {
    let home = fresh_home("measured-unpriced");
    write_meta(
        &home,
        "measured",
        measured_meta("measured", "claude", UNPRICED_MODEL, real_measured_usage()),
    );

    let report = ControlPlane::new(&home).usage_report(Utc::now(), UsageFilter::default());

    let row = &report.runs[0];
    assert_eq!(row.tokens.tokens_total.as_u64(), Some(446_231_222));
    assert_eq!(row.cost.amount["value"], "unknown");
    assert_eq!(row.unpriced_models, vec![UNPRICED_MODEL.to_string()]);
    assert_eq!(report.totals.runs_cost_unknown, 1);
    assert_eq!(report.totals.cost_by_unit.get("USD"), None);
}

#[test]
fn a_partial_measurement_is_not_priced_as_if_the_rest_were_zero() {
    let home = fresh_home("measured-partial");
    write_meta(
        &home,
        "measured",
        measured_meta(
            "measured",
            "claude",
            "claude-opus-5-5",
            json!({"output_tokens": 1000}),
        ),
    );

    let report = ControlPlane::new(&home).usage_report(Utc::now(), UsageFilter::default());

    let row = &report.runs[0];
    assert_eq!(row.tokens.tokens_output.as_u64(), Some(1000));
    assert_eq!(row.tokens.tokens_input["value"], "unknown");
    assert_eq!(row.tokens.tokens_total["value"], "unknown");
    assert_eq!(row.cost.amount["value"], "unknown", "{}", row.cost.amount);
    assert!(row.unpriced_models.is_empty(), "the model has a price");
    assert_eq!(report.totals.runs_cost_unknown, 1);
}

#[test]
fn a_projection_cannot_carry_a_model_name_the_report_would_refuse() {
    let home = fresh_home("projection-model");
    collect(&home);
    let path = home.join("telemetry").join("claude").join(PROJECTION);
    let mut value: Value =
        serde_json::from_slice(&fs::read(&path).expect("projection")).expect("json");
    for session in value["sessions"].as_array_mut().expect("sessions") {
        session["model"] = json!("<synthetic>");
    }
    write_projection(
        &home,
        "claude",
        &serde_json::to_vec(&value).expect("encode"),
    );

    let report = settled(&home);

    let row = local_row(&report, "claude", EXPECTED[0].session);
    assert_eq!(row.model["value"], "unknown", "{}", row.model);
}

#[test]
fn a_whole_run_aggregate_is_priced_at_base_rates_and_never_at_a_context_tier() {
    let home = fresh_home("measured-tier");
    write_meta(
        &home,
        "measured",
        measured_meta(
            "measured",
            "codex",
            "gpt-6.1-sol",
            json!({
                "input_tokens": 1_000_000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 1_000_000,
                "output_tokens": 100_000,
                "total_tokens": 2_100_000,
                "messages": 10
            }),
        ),
    );

    let report = ControlPlane::new(&home).usage_report(Utc::now(), UsageFilter::default());

    // Base 2 + 0.1 + 1 per the snapshot; the 272K tier would say 4 + 0.2 + 1.5.
    assert_close(report.runs[0].cost.amount.as_f64(), 3.1, "aggregate cost");
}

#[test]
fn prose_in_a_legacy_transcript_yields_no_cost_and_no_model() {
    let home = fresh_home("prose");
    let run = write_meta(
        &home,
        "legacy-prose",
        json!({
            "run_id": "legacy-prose",
            "agent": "agy",
            "status": "completed",
            "exit_code": 0,
            "completed_at": "2026-11-01T12:00:00Z"
        }),
    );
    fs::write(run.join("transcript.log"), PROSE_TRANSCRIPT).expect("transcript");

    let report = settled(&home);

    let row = &report.runs[0];
    assert_eq!(row.cost.amount["value"], "unknown", "{:?}", row.cost);
    assert_eq!(row.model["value"], "unknown", "{}", row.model);
    assert_eq!(report.totals.cost_by_unit.get("USD"), None);
    assert_eq!(report.totals.runs_cost_unknown, 1);
}
