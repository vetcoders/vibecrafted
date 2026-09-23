use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use control_core::{
    CursorModelRate, CursorPricing, CursorTranscriptInput, analyze_cursor_transcripts,
};

static NEXT_ID: AtomicU64 = AtomicU64::new(0);

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let id = NEXT_ID.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "vibecrafted-cursor-adapter-{}-{id}",
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

fn pricing() -> CursorPricing {
    CursorPricing {
        models: BTreeMap::from([(
            "gpt-5".to_string(),
            CursorModelRate {
                fresh_input_per_million: 1.25,
                cache_read_per_million: 0.125,
                output_per_million: 10.0,
                source: "openai-api-test".to_string(),
            },
        )]),
    }
}

fn input(path: PathBuf) -> CursorTranscriptInput {
    CursorTranscriptInput {
        path,
        session_id: None,
        model: None,
    }
}

#[test]
fn reads_repo_supported_cursor_shape_and_prefers_reported_cost() {
    let temp = TempDir::new();
    let transcript = temp.0.join("cursor.jsonl");
    fs::write(
        &transcript,
        concat!(
            "{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"abc-123\",\"model\":\"gpt-5\"}\n",
            "{\"type\":\"result\",\"subtype\":\"success\",\"id\":\"r1\",",
            "\"usage\":{\"inputTokens\":100,\"cacheReadTokens\":20,",
            "\"cacheWriteTokens\":5,\"outputTokens\":10},\"total_cost_usd\":0.42}\n"
        ),
    )
    .unwrap();

    let report =
        analyze_cursor_transcripts(&[input(transcript)], &pricing(), "2026-09-22T00:00:00Z");
    let slice = &report.slices[0];
    assert_eq!(slice.session_id.as_deref(), Some("abc-123"));
    assert_eq!(slice.model.as_deref(), Some("gpt-5"));
    assert_eq!(slice.tokens.input_total, Some(100));
    assert_eq!(slice.tokens.fresh_input, Some(80));
    assert_eq!(slice.tokens.cache_read, Some(20));
    assert_eq!(slice.tokens.cache_write, Some(5));
    assert_eq!(slice.tokens.output, Some(10));
    assert_eq!(slice.tokens.total, Some(110));
    assert_eq!(slice.cost.amount, Some(0.42));
    assert_eq!(slice.cost.source, "provider_reported");
    assert_eq!(slice.cost.basis, "provider-billing");
}

#[test]
fn estimates_api_equivalent_cost_from_injected_rates() {
    let temp = TempDir::new();
    let transcript = temp.0.join("cursor.jsonl");
    fs::write(
        &transcript,
        concat!(
            "{\"type\":\"result\",\"id\":\"r2\",\"usage\":{",
            "\"inputTokens\":100000,\"cacheReadTokens\":20000,",
            "\"cacheWriteTokens\":0,\"outputTokens\":10000}}\n"
        ),
    )
    .unwrap();
    let mut source = input(transcript);
    source.session_id = Some("injected-session".to_string());
    source.model = Some("cursor/gpt-5".to_string());

    let report = analyze_cursor_transcripts(&[source], &pricing(), "now");
    let cost = &report.slices[0].cost;
    assert_eq!(cost.amount, Some(0.2025));
    assert_eq!(cost.source, "estimated:openai-api-test");
    assert_eq!(cost.basis, "api-equiv");
}

#[test]
fn missing_model_or_token_fields_remain_unknown() {
    let temp = TempDir::new();
    let transcript = temp.0.join("cursor.jsonl");
    fs::write(
        &transcript,
        "{\"type\":\"result\",\"usage\":{\"inputTokens\":10,\"outputTokens\":2}}\n",
    )
    .unwrap();

    let report = analyze_cursor_transcripts(&[input(transcript)], &pricing(), "now");
    let slice = &report.slices[0];
    assert_eq!(slice.tokens.cache_read, None);
    assert_eq!(slice.tokens.cache_write, None);
    assert_eq!(slice.tokens.fresh_input, None);
    assert_eq!(slice.cost.amount, None);
    assert_eq!(slice.cost.source, "unknown");
    assert_eq!(slice.cost.reason.as_deref(), Some("model not recorded"));
}

#[test]
fn deduplicates_results_and_tolerates_malformed_and_partial_lines() {
    let temp = TempDir::new();
    let one = temp.0.join("one.jsonl");
    let two = temp.0.join("two.jsonl");
    let result = "{\"type\":\"result\",\"id\":\"same\",\"usage\":{\"inputTokens\":10,\"cacheReadTokens\":2,\"cacheWriteTokens\":0,\"outputTokens\":3}}";
    fs::write(&one, format!("not-json\n{result}\n")).unwrap();
    fs::write(&two, format!("{result}\n{{\"type\":\"result\"")).unwrap();
    let sources = [one, two]
        .into_iter()
        .map(|path| CursorTranscriptInput {
            path,
            session_id: Some("same-session".to_string()),
            model: Some("gpt-5".to_string()),
        })
        .collect::<Vec<_>>();

    let report = analyze_cursor_transcripts(&sources, &pricing(), "now");
    assert_eq!(report.slices.len(), 1);
    assert_eq!(report.slices[0].usage_events, 1);
    assert_eq!(report.diagnostics.duplicate_results, 1);
    assert_eq!(report.diagnostics.malformed_lines, 1);
    assert_eq!(report.diagnostics.partial_trailing_lines, 1);
}

#[test]
fn inconsistent_cache_evidence_refuses_to_invent_fresh_input_or_cost() {
    let temp = TempDir::new();
    let transcript = temp.0.join("cursor.jsonl");
    fs::write(
        &transcript,
        "{\"type\":\"result\",\"usage\":{\"inputTokens\":10,\"cacheReadTokens\":20,\"cacheWriteTokens\":0,\"outputTokens\":2}}\n",
    )
    .unwrap();
    let source = CursorTranscriptInput {
        path: transcript,
        session_id: Some("s".to_string()),
        model: Some("gpt-5".to_string()),
    };

    let report = analyze_cursor_transcripts(&[source], &pricing(), "now");
    let slice = &report.slices[0];
    assert_eq!(slice.tokens.fresh_input, None);
    assert_eq!(slice.cost.amount, None);
    assert_eq!(
        slice.cost.reason.as_deref(),
        Some("token evidence incomplete")
    );
}
