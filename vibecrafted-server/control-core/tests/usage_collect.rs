//! The server-owned collector end to end, through the real Python parsers.
//!
//! One test on purpose: it points `HOME`/`VIBECRAFTED_HOME` at a temp tree for
//! the whole process, so nothing else may run beside it.

use std::fs;
use std::time::Duration;

use chrono::Utc;
use control_core::{ControlPlane, UsageFilter};

#[test]
fn collector_fills_the_projections_the_report_reads_without_resident_children() {
    // nosemgrep: rust.lang.security.temp-dir.temp-dir -- atomic private creation below rejects existing paths and symlinks before writing the inert executable or fixtures.
    let root = std::env::temp_dir().join(format!(
        "control-core-collect-{}-{}",
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
        .create(&root)
        .expect("exclusive private collector fixture");
    let host = root.join("host");
    let vc_home = root.join("vc");
    fs::create_dir_all(&vc_home).unwrap();
    let claude_dir = host.join(".claude/projects/p");
    fs::create_dir_all(&claude_dir).unwrap();
    fs::write(
        claude_dir.join("aaaa1111-2222-3333-4444-555566667777.jsonl"),
        concat!(
            r#"{"timestamp":"2026-10-12T11:00:00Z","message":{"model":"claude-opus-5-5","#,
            r#""usage":{"input_tokens":10,"output_tokens":5,"cache_read_input_tokens":3,"cache_creation_input_tokens":2}}}"#,
            "\n"
        ),
    )
    .unwrap();
    let kimi_dir = host.join(".kimi-code/sessions/sess-kimi-0001");
    fs::create_dir_all(&kimi_dir).unwrap();
    fs::write(
        kimi_dir.join("wire.jsonl"),
        concat!(
            r#"{"type":"usage.record","time":1791802800000,"model":"kimi-k3","#,
            r#""usage":{"inputOther":7,"output":3,"inputCacheCreation":0,"inputCacheRead":1}}"#,
            "\n"
        ),
    )
    .unwrap();
    let omp_agent_dir = host.join(".omp/agent");
    let omp_sessions = omp_agent_dir.join("sessions/private-project");
    fs::create_dir_all(&omp_sessions).unwrap();
    fs::write(
        omp_sessions.join("omp-session-0001.jsonl"),
        concat!(
            r#"{"type":"message","timestamp":"2026-10-12T11:00:00Z","message":{"role":"assistant","#,
            r#""provider":"fixture-provider","model":"fixture-model","usage":{"input":7,"output":3,"cacheRead":2,"cacheWrite":1,"totalTokens":13}}}"#,
            "\n"
        ),
    )
    .unwrap();
    // SAFETY: single test in this process; nothing reads the environment concurrently.
    // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- this binary owns its only test; fixture environment is established before any collector or projection starts.
    unsafe {
        std::env::set_var("HOME", &host);
        std::env::set_var("VIBECRAFTED_HOME", &vc_home);
        std::env::set_var("XDG_CONFIG_HOME", host.join(".config"));
        std::env::set_var("XDG_DATA_HOME", host.join(".local/share"));
        std::env::set_var("OMP_PROFILE", "default");
        std::env::set_var("PI_CODING_AGENT_DIR", &omp_agent_dir);
    }

    let plane = ControlPlane::new(&vc_home);
    let outcome = plane.collect_local_usage(Duration::ZERO);

    assert!(outcome.started, "{outcome:?}");
    let status = |agent: &str| {
        outcome
            .agents
            .iter()
            .find(|item| item.agent == agent)
            .map(|item| item.status.as_str())
            .unwrap_or("missing")
    };
    assert_eq!(status("claude"), "ok", "{outcome:?}");
    assert_eq!(status("kimi"), "ok", "{outcome:?}");
    assert_eq!(status("omp"), "ok", "{outcome:?}");
    assert_eq!(status("agy"), "unavailable", "no ledger is not a zero");
    assert_eq!(
        outcome.agents.len(),
        9,
        "every provider is collected by the one owner"
    );

    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    let report = loop {
        let report = plane.usage_report(Utc::now(), UsageFilter::default());
        if !report.local_sources.iter().any(|source| source.refreshing) {
            break report;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "collector projection did not settle"
        );
        std::thread::sleep(Duration::from_millis(10));
    };
    let claude = report
        .runs
        .iter()
        .find(|r| r.agent == "claude")
        .expect("claude row");
    assert_eq!(
        (claude.origin.as_str(), claude.tokens.tokens_total.as_u64()),
        ("local", Some(20))
    );
    let kimi = report
        .runs
        .iter()
        .find(|r| r.agent == "kimi")
        .expect("kimi row");
    assert_eq!(kimi.tokens.tokens_total.as_u64(), Some(11));
    let omp = report
        .runs
        .iter()
        .find(|r| r.agent == "omp")
        .expect("OMP native session reaches the existing local reader");
    assert_eq!(omp.origin, "local");
    assert_eq!(omp.status, "observed");
    assert_eq!(omp.exit_code, None);
    assert_eq!(omp.model, "fixture-provider/fixture-model");
    assert_eq!(omp.provider, "omp");
    assert_eq!(omp.tokens.tokens_input.as_u64(), Some(7));
    assert_eq!(omp.tokens.tokens_output.as_u64(), Some(3));
    assert_eq!(omp.tokens.tokens_cached_input.as_u64(), Some(2));
    assert_eq!(omp.tokens.tokens_cache_write.as_u64(), Some(1));
    assert_eq!(omp.tokens.tokens_total.as_u64(), Some(13));
    assert_eq!(omp.cost.amount["value"], "unknown");
    assert_eq!(control_core::usage::provider_pool("omp"), None);
    let omp_source = report
        .local_sources
        .iter()
        .find(|source| source.agent == "omp")
        .expect("OMP local source");
    assert_eq!(omp_source.source, "scan_cache");
    assert_eq!(omp_source.counted, 1);
    assert_eq!(report.totals.runs, 3);

    // A fresh collection exists: a second owner or a restart is a no-op.
    let again = plane.collect_local_usage(Duration::from_secs(3600));
    assert!(
        !again.started && again.reason.contains("recent"),
        "{again:?}"
    );

    // Quota refresh only touches what has an authoritative source; nothing is guessed.
    let quota =
        plane.refresh_quota(&["cursor".to_string(), "codex".to_string(), "omp".to_string()]);
    assert!(quota.started, "{quota:?}");
    assert!(
        quota.agents.iter().all(|a| a.status == "unavailable"),
        "{quota:?}"
    );
    assert!(
        quota
            .agents
            .iter()
            .any(|agent| agent.agent == "omp" && agent.status == "unavailable"),
        "OMP has no authoritative company quota: {quota:?}"
    );
    let omp_quota: serde_json::Value =
        serde_json::from_slice(&fs::read(vc_home.join("telemetry/omp/quota.json")).unwrap())
            .unwrap();
    assert_eq!(omp_quota["agent"], "omp");
    assert_eq!(omp_quota["metrics"]["fleet_processed_total"], 13);
    assert!(omp_quota["quota"].get("limit").is_none());
    assert!(omp_quota["quota"].get("remaining").is_none());

    // Observe the actual collector request at an inert executable boundary.
    // No fixture response claims that a provider was collected.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        let executable = root.join("inert-collector");
        fs::write(
            &executable,
            "#!/bin/sh\n/bin/cat > \"$VIBECRAFTED_HOME/collector-request.json\"\nprintf '[]'\n",
        )
        .unwrap();
        fs::set_permissions(&executable, fs::Permissions::from_mode(0o700)).unwrap();
        // SAFETY: this binary contains exactly one test.
        // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- the only test has awaited all local projection work above; no background environment reader remains when replacing the inert boundary.
        unsafe {
            std::env::set_var("VIBECRAFTED_PYTHON", &executable);
        }
        let captured = plane.collect_local_usage(Duration::ZERO);
        assert!(captured.started, "{captured:?}");
        let request: serde_json::Value =
            serde_json::from_slice(&fs::read(vc_home.join("collector-request.json")).unwrap())
                .unwrap();
        assert_eq!(request["mode"], "local");
        assert_eq!(
            request["agents"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|agent| agent.as_str() == Some("omp"))
                .count(),
            1,
            "the existing collector requests OMP exactly once"
        );
    }
    let none = plane.refresh_quota(&["not-a-provider".to_string()]);
    assert!(!none.started);
    fs::remove_dir_all(root).ok();
}
