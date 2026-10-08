use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use control_core::{CodexModelRate, CodexPricing, CodexSessionInput, analyze_codex_sessions};
static NEXT_ID: AtomicU64 = AtomicU64::new(0);

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let id = NEXT_ID.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "vibecrafted-codex-adapter-{}-{id}",
            std::process::id()
        ));
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn pricing() -> CodexPricing {
    CodexPricing {
        models: BTreeMap::from([(
            "gpt-test".to_string(),
            CodexModelRate {
                fresh_input_per_million: 2.0,
                cache_read_per_million: 0.5,
                cache_write_per_million: 1.0,
                output_per_million: 8.0,
            },
        )]),
    }
}

#[test]
fn native_session_uses_latest_cumulative_totals_without_double_counting() {
    let dir = TempDir::new();
    let path = dir.0.join("rollout.jsonl");
    fs::write(
        &path,
        concat!(
            "{\"type\":\"session_meta\",\"payload\":{\"id\":\"s-1\"}}\n",
            "{\"type\":\"turn_context\",\"payload\":{\"model\":\"gpt-test\"}}\n",
            "{\"type\":\"event_msg\",\"payload\":{\"type\":\"token_count\",\"info\":{\"total_token_usage\":{\"input_tokens\":100,\"cached_input_tokens\":40,\"cache_write_input_tokens\":5,\"output_tokens\":20,\"reasoning_output_tokens\":7,\"total_tokens\":120}}}}\n",
            "{\"type\":\"event_msg\",\"payload\":{\"type\":\"token_count\",\"info\":{\"total_token_usage\":{\"input_tokens\":250,\"cached_input_tokens\":100,\"cache_write_input_tokens\":10,\"output_tokens\":50,\"reasoning_output_tokens\":15,\"total_tokens\":300}}}}\n",
        ),
    )
    .unwrap();
    let report = analyze_codex_sessions(
        &[CodexSessionInput {
            path,
            session_id: None,
        }],
        &pricing(),
        "2026-09-22T00:00:00Z",
    );
    let session = &report.sessions[0];
    assert_eq!(session.session_id.as_deref(), Some("s-1"));
    assert_eq!(session.tokens.fresh_input, Some(140));
    assert_eq!(session.tokens.cache_read, Some(100));
    assert_eq!(session.tokens.output, Some(50));
    assert_eq!(session.tokens.reasoning_output, Some(15));
    assert_eq!(session.tokens.total, Some(300));
    assert_eq!(session.cost.amount, Some(0.00074));
}

#[test]
fn cumulative_counter_reset_starts_a_new_segment_without_losing_the_previous_one() {
    let dir = TempDir::new();
    let path = dir.0.join("rollout.jsonl");
    fs::write(
        &path,
        concat!(
            "{\"type\":\"event_msg\",\"payload\":{\"type\":\"token_count\",\"info\":{\"total_token_usage\":{\"input_tokens\":100,\"cached_input_tokens\":20,\"output_tokens\":10,\"total_tokens\":110}}}}\n",
            "{\"type\":\"event_msg\",\"payload\":{\"type\":\"token_count\",\"info\":{\"total_token_usage\":{\"input_tokens\":5,\"cached_input_tokens\":0,\"output_tokens\":2,\"total_tokens\":7}}}}\n",
        ),
    )
    .unwrap();
    let report = analyze_codex_sessions(
        &[CodexSessionInput {
            path,
            session_id: Some("reset".into()),
        }],
        &CodexPricing::default(),
        "now",
    );
    assert_eq!(report.sessions[0].tokens.fresh_input, Some(85));
    assert_eq!(report.sessions[0].tokens.output, Some(12));
    assert_eq!(report.sessions[0].tokens.total, Some(117));
    assert_eq!(report.diagnostics.cumulative_counter_resets, 1);
}

#[test]
fn exec_stream_sums_per_turn_usage() {
    let dir = TempDir::new();
    let path = dir.0.join("exec.jsonl");
    fs::write(
        &path,
        concat!(
            "{\"type\":\"thread.started\",\"thread_id\":\"t-1\",\"model\":\"gpt-test\"}\n",
            "{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":20,\"cached_input_tokens\":5,\"output_tokens\":3}}\n",
            "{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":30,\"cached_input_tokens\":10,\"output_tokens\":7}}\n",
        ),
    )
    .unwrap();
    let report = analyze_codex_sessions(
        &[CodexSessionInput {
            path,
            session_id: None,
        }],
        &pricing(),
        "now",
    );
    let session = &report.sessions[0];
    assert_eq!(session.session_id.as_deref(), Some("t-1"));
    assert_eq!(session.tokens.fresh_input, Some(35));
    assert_eq!(session.tokens.cache_read, Some(15));
    assert_eq!(session.tokens.output, Some(10));
    assert_eq!(session.usage_events, 2);
}

#[test]
fn missing_usage_and_price_remain_unknown() {
    let dir = TempDir::new();
    let path = dir.0.join("empty.jsonl");
    fs::write(
        &path,
        "{\"type\":\"session_meta\",\"payload\":{\"id\":\"s\"}}\n",
    )
    .unwrap();
    let report = analyze_codex_sessions(
        &[CodexSessionInput {
            path,
            session_id: None,
        }],
        &CodexPricing::default(),
        "now",
    );
    assert_eq!(report.sessions[0].tokens.total, None);
    assert_eq!(report.sessions[0].cost.amount, None);
    assert!(!report.sessions[0].tokens.unknown_reasons.is_empty());
}

#[test]
fn ignores_partial_tail_and_deduplicates_complete_records() {
    let dir = TempDir::new();
    let first = dir.0.join("one.jsonl");
    let second = dir.0.join("two.jsonl");
    let complete =
        "{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":4,\"output_tokens\":2}}\n";
    fs::write(&first, format!("{complete}{{\"type\":")).unwrap();
    fs::write(&second, complete).unwrap();
    let report = analyze_codex_sessions(
        &[
            CodexSessionInput {
                path: first,
                session_id: Some("a".into()),
            },
            CodexSessionInput {
                path: second,
                session_id: Some("a".into()),
            },
        ],
        &CodexPricing::default(),
        "now",
    );
    assert_eq!(report.diagnostics.partial_trailing_lines, 1);
    assert_eq!(report.diagnostics.duplicate_records, 1);
    assert_eq!(report.sessions[0].tokens.total, Some(6));
    assert_eq!(report.sessions[1].tokens.total, None);
}

#[test]
fn injected_session_id_is_authoritative() {
    let dir = TempDir::new();
    let path = dir.0.join("session.jsonl");
    fs::write(
        &path,
        "{\"type\":\"session_meta\",\"payload\":{\"id\":\"embedded\"}}\n",
    )
    .unwrap();
    let report = analyze_codex_sessions(
        &[CodexSessionInput {
            path,
            session_id: Some("injected".into()),
        }],
        &CodexPricing::default(),
        "now",
    );
    assert_eq!(report.sessions[0].session_id.as_deref(), Some("injected"));
}
