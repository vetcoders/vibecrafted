use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use chrono::{TimeZone, Utc};
use control_core::{
    ClaudeModelRate, ClaudePricing, ClaudeTranscriptInput, analyze_claude_transcripts,
    normalize_claude_model,
};

static NEXT_ID: AtomicU64 = AtomicU64::new(0);

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let id = NEXT_ID.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "vibecrafted-claude-adapter-{}-{id}",
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

fn generated_at() -> chrono::DateTime<Utc> {
    Utc.with_ymd_and_hms(2026, 9, 22, 8, 0, 0).single().unwrap()
}

fn pricing() -> ClaudePricing {
    ClaudePricing {
        models: BTreeMap::from([(
            "claude-sonnet-4-6".to_string(),
            ClaudeModelRate {
                fresh_input_per_million: 3.0,
                cache_write_per_million: 3.75,
                cache_read_per_million: 0.30,
                output_per_million: 15.0,
            },
        )]),
    }
}

fn input(path: PathBuf, workspace: PathBuf) -> ClaudeTranscriptInput {
    ClaudeTranscriptInput {
        path,
        session_id: Some("claude-session".to_string()),
        workspace: Some(workspace),
        model: None,
    }
}

#[test]
fn preserves_provider_buckets_and_estimates_only_api_equivalent_cost() {
    let temp = TempDir::new();
    let workspace = temp.0.join("repo");
    fs::create_dir(&workspace).unwrap();
    let transcript = temp.0.join("session.jsonl");
    fs::write(
        &transcript,
        format!(
            concat!(
                "{{\"type\":\"assistant\",\"sessionId\":\"claude-session\",",
                "\"cwd\":{},\"message\":{{\"id\":\"message-1\",",
                "\"model\":\"claude-sonnet-4-6\",\"usage\":{{",
                "\"input_tokens\":100000,\"cache_creation_input_tokens\":20000,",
                "\"cache_read_input_tokens\":300000,\"output_tokens\":10000}}}}}}\n"
            ),
            serde_json::to_string(&workspace.display().to_string()).unwrap()
        ),
    )
    .unwrap();

    let report = analyze_claude_transcripts(
        &[input(transcript, workspace.clone())],
        &pricing(),
        generated_at(),
    );

    assert_eq!(report.schema, "vibecrafted.usage-provider.claude.v1");
    assert_eq!(report.slices.len(), 1);
    let slice = &report.slices[0];
    assert_eq!(slice.session_id.as_deref(), Some("claude-session"));
    assert_eq!(
        slice.workspace.as_deref(),
        Some(workspace.display().to_string().as_str())
    );
    assert_eq!(slice.model.as_deref(), Some("claude-sonnet-4-6"));
    assert_eq!(slice.messages, 1);
    assert_eq!(slice.tokens.fresh_input, Some(100_000));
    assert_eq!(slice.tokens.cache_write, Some(20_000));
    assert_eq!(slice.tokens.cache_read, Some(300_000));
    assert_eq!(slice.tokens.output, Some(10_000));
    assert_eq!(slice.tokens.total, Some(430_000));
    assert_eq!(slice.tokens.source, "provider_reported");
    assert_eq!(slice.cost.amount, Some(0.615));
    assert_eq!(slice.cost.source, "estimated");
    assert_eq!(slice.cost.unit, "api-equiv");
    assert!(slice.cost.method.contains("injected per-million"));
}

#[test]
fn deduplicates_message_ids_across_files_and_accepts_nested_workspace() {
    let temp = TempDir::new();
    let workspace = temp.0.join("repo");
    let nested = workspace.join("src");
    fs::create_dir_all(&nested).unwrap();
    let first = temp.0.join("one.jsonl");
    let second = temp.0.join("two.jsonl");
    let record = format!(
        concat!(
            "{{\"type\":\"assistant\",\"sessionId\":\"claude-session\",",
            "\"cwd\":{},\"message\":{{\"id\":\"same\",",
            "\"model\":\"claude-sonnet-4-6\",\"usage\":{{",
            "\"input_tokens\":3,\"cache_creation_input_tokens\":1,",
            "\"cache_read_input_tokens\":2,\"output_tokens\":4}}}}}}"
        ),
        serde_json::to_string(&nested.display().to_string()).unwrap()
    );
    fs::write(&first, format!("{record}\n")).unwrap();
    fs::write(&second, format!("{record}\n")).unwrap();

    let report = analyze_claude_transcripts(
        &[input(first, workspace.clone()), input(second, workspace)],
        &pricing(),
        generated_at(),
    );

    assert_eq!(report.slices.len(), 1);
    assert_eq!(report.slices[0].messages, 1);
    assert_eq!(report.slices[0].tokens.total, Some(10));
    assert_eq!(report.diagnostics.duplicate_messages, 1);
}

#[test]
fn incomplete_provider_buckets_and_unpriced_model_remain_unknown() {
    let temp = TempDir::new();
    let workspace = temp.0.join("repo");
    fs::create_dir(&workspace).unwrap();
    let transcript = temp.0.join("session.jsonl");
    fs::write(
        &transcript,
        format!(
            concat!(
                "{{\"sessionId\":\"claude-session\",\"cwd\":{},",
                "\"message\":{{\"id\":\"partial\",\"model\":\"future-claude\",",
                "\"usage\":{{\"input_tokens\":7,\"output_tokens\":2}}}}}}\n"
            ),
            serde_json::to_string(&workspace.display().to_string()).unwrap()
        ),
    )
    .unwrap();

    let report =
        analyze_claude_transcripts(&[input(transcript, workspace)], &pricing(), generated_at());
    let slice = &report.slices[0];
    assert_eq!(slice.tokens.fresh_input, Some(7));
    assert_eq!(slice.tokens.cache_write, None);
    assert_eq!(slice.tokens.cache_read, None);
    assert_eq!(slice.tokens.output, Some(2));
    assert_eq!(slice.tokens.total, None);
    assert_eq!(slice.cost.amount, None);
    assert_eq!(
        slice.cost.reason.as_deref(),
        Some("no injected price for normalized model")
    );
}

#[test]
fn rejects_foreign_session_and_workspace_from_attribution() {
    let temp = TempDir::new();
    let workspace = temp.0.join("repo");
    let foreign = temp.0.join("foreign");
    fs::create_dir(&workspace).unwrap();
    fs::create_dir(&foreign).unwrap();
    let transcript = temp.0.join("session.jsonl");
    let usage = "\"message\":{\"id\":\"m\",\"usage\":{\"input_tokens\":1,\"cache_creation_input_tokens\":0,\"cache_read_input_tokens\":0,\"output_tokens\":1}}";
    fs::write(
        &transcript,
        format!(
            "{{\"sessionId\":\"foreign-session\",\"cwd\":{},{} }}\n{{\"sessionId\":\"claude-session\",\"cwd\":{},{} }}\n",
            serde_json::to_string(&workspace.display().to_string()).unwrap(),
            usage,
            serde_json::to_string(&foreign.display().to_string()).unwrap(),
            usage
        ),
    )
    .unwrap();

    let report =
        analyze_claude_transcripts(&[input(transcript, workspace)], &pricing(), generated_at());

    assert!(report.slices.is_empty());
    assert_eq!(report.diagnostics.foreign_session_records, 1);
    assert_eq!(report.diagnostics.foreign_workspace_records, 1);
}

#[test]
fn tolerates_partial_tail_and_bad_complete_lines() {
    let temp = TempDir::new();
    let transcript = temp.0.join("stream.jsonl");
    fs::write(
        &transcript,
        concat!(
            "not-json\n",
            "{\"type\":\"result\",\"session_id\":\"stream-session\",",
            "\"model\":\"anthropic/claude-sonnet-4-6\",\"usage\":{",
            "\"input_tokens\":5,\"cache_creation_input_tokens\":1,",
            "\"cache_read_input_tokens\":2,\"output_tokens\":3}}\n",
            "{\"type\":\"result\""
        ),
    )
    .unwrap();

    let report = analyze_claude_transcripts(
        &[ClaudeTranscriptInput {
            path: transcript,
            session_id: None,
            workspace: None,
            model: None,
        }],
        &pricing(),
        generated_at(),
    );

    assert_eq!(report.diagnostics.malformed_lines, 1);
    assert_eq!(report.diagnostics.partial_trailing_lines, 1);
    assert_eq!(report.slices.len(), 1);
    assert_eq!(
        report.slices[0].session_id.as_deref(),
        Some("stream-session")
    );
    assert_eq!(report.slices[0].tokens.total, Some(11));
    assert_eq!(report.slices[0].cost.source, "estimated");
}

#[test]
fn invalid_usage_record_is_diagnosed_without_becoming_zero() {
    let temp = TempDir::new();
    let transcript = temp.0.join("session.jsonl");
    fs::write(
        &transcript,
        "{\"message\":{\"id\":\"bad\",\"usage\":{\"input_tokens\":-1}}}\n",
    )
    .unwrap();
    let report = analyze_claude_transcripts(
        &[ClaudeTranscriptInput {
            path: transcript,
            session_id: None,
            workspace: None,
            model: None,
        }],
        &pricing(),
        generated_at(),
    );

    assert!(report.slices.is_empty());
    assert_eq!(report.diagnostics.invalid_usage_records, 1);
}

#[test]
fn normalizes_namespaced_model_without_erasing_version() {
    assert_eq!(
        normalize_claude_model("Anthropic/Claude_Sonnet_4_6"),
        "claude-sonnet-4-6"
    );
    assert_eq!(
        normalize_claude_model("claude-opus-4-6-20260901"),
        "claude-opus-4-6-20260901"
    );
}
