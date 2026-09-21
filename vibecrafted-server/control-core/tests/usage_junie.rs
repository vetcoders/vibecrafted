#[path = "../src/usage_junie.rs"]
mod usage_junie;

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use usage_junie::{JunieModelRate, JuniePricing, JunieTranscriptInput, analyze_junie_transcripts};

static NEXT_ID: AtomicU64 = AtomicU64::new(0);

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let id = NEXT_ID.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "vibecrafted-junie-adapter-{}-{id}",
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

fn pricing() -> JuniePricing {
    JuniePricing {
        models: BTreeMap::from([(
            "gpt-5.5".to_string(),
            JunieModelRate {
                fresh_input_per_million: 5.0,
                cache_input_per_million: 0.5,
                output_per_million: 30.0,
                source: "openai-api-test".to_string(),
            },
        )]),
    }
}

fn input(path: PathBuf) -> JunieTranscriptInput {
    JunieTranscriptInput {
        path,
        session_id: None,
        model: None,
    }
}

#[test]
fn maps_real_nested_junie_model_usage_and_provider_cost() {
    let temp = TempDir::new();
    let transcript = temp.0.join("junie.jsonl");
    fs::write(
        &transcript,
        concat!(
            "{\"type\":\"session\",\"sessionId\":\"session-junie-1\"}\n",
            "{\"kind\":\"SessionA2uxEvent\",\"event\":{\"state\":\"IN_PROGRESS\",",
            "\"agentEvent\":{\"kind\":\"LlmResponseMetadataEvent\",\"modelUsage\":[",
            "{\"model\":\"gpt-5.5\",\"cost\":0.045821,\"inputTokens\":807,",
            "\"cacheInputTokens\":49792,\"cacheCreateTokens\":0,",
            "\"outputTokens\":563,\"time\":0}]}}}\n"
        ),
    )
    .unwrap();

    let report = analyze_junie_transcripts(&[input(transcript)], &pricing(), "now");
    let slice = &report.slices[0];
    assert_eq!(slice.session_id.as_deref(), Some("session-junie-1"));
    assert_eq!(slice.model.as_deref(), Some("gpt-5.5"));
    assert_eq!(slice.tokens.fresh_input, Some(807));
    assert_eq!(slice.tokens.cache_input, Some(49_792));
    assert_eq!(slice.tokens.cache_create, Some(0));
    assert_eq!(slice.tokens.output, Some(563));
    assert_eq!(slice.tokens.total, Some(51_162));
    assert_eq!(slice.cost.amount, Some(0.045821));
    assert_eq!(slice.cost.source, "provider_reported");
}

#[test]
fn estimates_missing_cost_from_injected_model_rate() {
    let temp = TempDir::new();
    let transcript = temp.0.join("junie.jsonl");
    fs::write(
        &transcript,
        "{\"modelUsage\":[{\"model\":\"gpt-5.5\",\"inputTokens\":1000,\"cacheInputTokens\":2000,\"cacheCreateTokens\":0,\"outputTokens\":100}]}\n",
    )
    .unwrap();
    let report = analyze_junie_transcripts(&[input(transcript)], &pricing(), "now");
    let cost = &report.slices[0].cost;
    assert_eq!(cost.amount, Some(0.009));
    assert_eq!(cost.source, "estimated:openai-api-test");
    assert_eq!(cost.basis, "api-equiv");
}

#[test]
fn cost_only_entries_are_exact_without_inventing_token_usage() {
    let temp = TempDir::new();
    let transcript = temp.0.join("junie.jsonl");
    fs::write(
        &transcript,
        concat!(
            "{\"sessionId\":\"s\",\"modelUsage\":[{\"model\":\"gpt-5.5\",\"cost\":0.045821}]}\n",
            "{\"sessionId\":\"s\",\"modelUsage\":[{\"model\":\"gpt-5.5\",\"cost\":0.00029}]}\n"
        ),
    )
    .unwrap();
    let report = analyze_junie_transcripts(&[input(transcript)], &pricing(), "now");
    let slice = &report.slices[0];
    assert_eq!(slice.usage_events, 0);
    assert_eq!(slice.cost_events, 2);
    assert_eq!(slice.tokens.total, None);
    assert_eq!(slice.cost.amount, Some(0.046111));
    assert_eq!(slice.cost.source, "provider_reported");
}

#[test]
fn missing_bucket_or_model_remains_unknown() {
    let temp = TempDir::new();
    let transcript = temp.0.join("junie.jsonl");
    fs::write(
        &transcript,
        "{\"modelUsage\":[{\"inputTokens\":10,\"outputTokens\":2}]}\n",
    )
    .unwrap();
    let report = analyze_junie_transcripts(&[input(transcript)], &pricing(), "now");
    let slice = &report.slices[0];
    assert_eq!(slice.model, None);
    assert_eq!(slice.tokens.cache_input, None);
    assert_eq!(slice.tokens.total, None);
    assert_eq!(slice.cost.amount, None);
    assert_eq!(slice.cost.reason.as_deref(), Some("model not recorded"));
}

#[test]
fn deduplicates_nested_evidence_and_tolerates_bad_and_partial_lines() {
    let temp = TempDir::new();
    let one = temp.0.join("one.jsonl");
    let two = temp.0.join("two.jsonl");
    let event = "{\"id\":\"event-1\",\"sessionId\":\"s\",\"modelUsage\":[{\"model\":\"gpt-5.5\",\"cost\":0.01,\"inputTokens\":1,\"cacheInputTokens\":2,\"cacheCreateTokens\":0,\"outputTokens\":3}]}";
    fs::write(&one, format!("bad-json\n{event}\n")).unwrap();
    fs::write(&two, format!("{event}\n{{\"modelUsage\":[")).unwrap();

    let report = analyze_junie_transcripts(&[input(one), input(two)], &pricing(), "now");
    assert_eq!(report.slices[0].usage_events, 1);
    assert_eq!(report.diagnostics.duplicate_usage_entries, 1);
    assert_eq!(report.diagnostics.malformed_lines, 1);
    assert_eq!(report.diagnostics.partial_trailing_lines, 1);
}
