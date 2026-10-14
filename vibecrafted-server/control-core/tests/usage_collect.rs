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
    let root = std::env::temp_dir().join(format!(
        "control-core-collect-{}-{}",
        std::process::id(),
        Utc::now().timestamp_nanos_opt().unwrap_or_default()
    ));
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
    // SAFETY: single test in this process; nothing reads the environment concurrently.
    unsafe {
        std::env::set_var("HOME", &host);
        std::env::set_var("VIBECRAFTED_HOME", &vc_home);
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
    assert_eq!(status("agy"), "unavailable", "no ledger is not a zero");
    assert_eq!(
        outcome.agents.len(),
        8,
        "every provider is collected by the one owner"
    );

    let report = plane.usage_report(Utc::now(), UsageFilter::default());
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
    assert_eq!(report.totals.runs, 2);

    // A fresh collection exists: a second owner or a restart is a no-op.
    let again = plane.collect_local_usage(Duration::from_secs(3600));
    assert!(
        !again.started && again.reason.contains("recent"),
        "{again:?}"
    );

    // Quota refresh only touches what has an authoritative source; nothing is guessed.
    let quota = plane.refresh_quota(&["cursor".to_string(), "codex".to_string()]);
    assert!(quota.started, "{quota:?}");
    assert!(
        quota.agents.iter().all(|a| a.status == "unavailable"),
        "{quota:?}"
    );
    let none = plane.refresh_quota(&["not-a-provider".to_string()]);
    assert!(!none.started);
    fs::remove_dir_all(root).ok();
}
