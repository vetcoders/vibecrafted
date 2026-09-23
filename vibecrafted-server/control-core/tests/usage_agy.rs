use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use chrono::{TimeZone, Utc};
use control_core::{
    AgyModelRate, AgyPricing, AgySurface, AgyTranscriptInput, analyze_agy_transcripts,
    normalize_agy_model,
};

static NEXT_ID: AtomicU64 = AtomicU64::new(0);

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let id = NEXT_ID.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "vibecrafted-agy-adapter-{}-{id}",
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
    Utc.with_ymd_and_hms(2026, 9, 21, 12, 0, 0)
        .single()
        .unwrap()
}

fn pricing() -> AgyPricing {
    AgyPricing {
        models: BTreeMap::from([(
            "gemini-3.8-flash-medium".to_string(),
            AgyModelRate {
                fresh_input_per_million: 0.15,
                output_per_million: 0.60,
            },
        )]),
    }
}

#[test]
fn ide_transcript_projects_identity_tools_and_only_estimated_api_equivalent_cost() {
    let temp = TempDir::new();
    let transcript = temp.0.join("transcript.jsonl");
    let rows = [
        r#"{"id":"one","type":"USER_INPUT","created_at":"2026-09-21T10:00:00Z","content":"<user_information>\n/Users/founder/vibecrafted -> main\nModel Selection` from Auto to Gemini 3.8 Flash Medium. No need to mention"}"#,
        r#"{"id":"two","type":"PLANNER_RESPONSE","created_at":"2026-09-21T10:00:01Z","content":"done","tool_calls":[{},{}]}"#,
    ];
    fs::write(&transcript, format!("{}\n{}\n", rows[0], rows[1])).unwrap();

    let report = analyze_agy_transcripts(
        &[AgyTranscriptInput {
            path: transcript,
            surface: AgySurface::Ide,
            session_id: Some("ide-session".to_string()),
            workspace: None,
        }],
        &pricing(),
        generated_at(),
    );

    assert_eq!(report.schema, "vibecrafted.usage-provider.agy.v1");
    assert_eq!(report.sessions.len(), 1);
    let session = &report.sessions[0];
    assert_eq!(session.session_id.as_deref(), Some("ide-session"));
    assert_eq!(session.surface, AgySurface::Ide);
    assert_eq!(
        session.workspace.as_deref(),
        Some("/Users/founder/vibecrafted")
    );
    assert_eq!(session.model.as_deref(), Some("gemini-3.8-flash-medium"));
    assert_eq!(session.steps, 2);
    assert_eq!(session.user_turns, 1);
    assert_eq!(session.planner_turns, 1);
    assert_eq!(session.tool_calls, 2);
    assert_eq!(session.tokens.source, "estimated");
    assert_eq!(
        session.tokens.total,
        Some(((rows[0].chars().count() + rows[1].chars().count() + 2) / 4) as u64)
    );
    assert_eq!(session.cost.source, "estimated");
    assert_eq!(session.cost.unit, "api-equiv");
    assert_eq!(session.cost.currency, "USD");
    assert!(session.cost.amount.is_some_and(|amount| amount > 0.0));
    assert!(session.cost.method.contains("80% input"));
    assert_eq!(session.signals.quota_status, "ok");
}

#[test]
fn injected_cli_workspace_wins_and_unpriced_model_stays_unknown() {
    let temp = TempDir::new();
    let transcript = temp.0.join("transcript.jsonl");
    fs::write(
        &transcript,
        "{\"type\":\"USER_INPUT\",\"content\":\"Model Selection` from Auto to future-model. No need\"}\n",
    )
    .unwrap();

    let report = analyze_agy_transcripts(
        &[AgyTranscriptInput {
            path: transcript,
            surface: AgySurface::Cli,
            session_id: Some("cli-session".to_string()),
            workspace: Some("/injected/workspace".to_string()),
        }],
        &pricing(),
        generated_at(),
    );
    let session = &report.sessions[0];
    assert_eq!(session.surface, AgySurface::Cli);
    assert_eq!(session.workspace.as_deref(), Some("/injected/workspace"));
    assert_eq!(session.model.as_deref(), Some("future-model"));
    assert_eq!(session.cost.amount, None);
    assert_eq!(session.cost.source, "unknown");
    assert_eq!(
        session.cost.reason.as_deref(),
        Some("no injected price for normalized model")
    );
}

#[test]
fn deterministically_deduplicates_records_across_paths_for_one_session() {
    let temp = TempDir::new();
    let first = temp.0.join("first.jsonl");
    let second = temp.0.join("second.jsonl");
    let duplicate = r#"{"id":"same-event","type":"USER_INPUT","content":"hello"}"#;
    let unique =
        r#"{"id":"next-event","type":"PLANNER_RESPONSE","content":"ok","tool_calls":[{}]}"#;
    fs::write(&first, format!("{duplicate}\n")).unwrap();
    fs::write(&second, format!("{duplicate}\n{unique}\n")).unwrap();
    let inputs = [first, second].map(|path| AgyTranscriptInput {
        path,
        surface: AgySurface::Ide,
        session_id: Some("same-session".to_string()),
        workspace: Some("/workspace".to_string()),
    });

    let report = analyze_agy_transcripts(&inputs, &pricing(), generated_at());

    assert_eq!(report.sessions.len(), 1);
    assert_eq!(report.sessions[0].steps, 2);
    assert_eq!(report.sessions[0].tool_calls, 1);
    assert_eq!(report.diagnostics.duplicate_records, 1);
}

#[test]
fn tolerates_partial_tail_and_tracks_quota_until_a_later_success() {
    let temp = TempDir::new();
    let transcript = temp.0.join("transcript.jsonl");
    fs::write(
        &transcript,
        concat!(
            "not-json\n",
            "{\"id\":\"quota\",\"type\":\"ERROR_MESSAGE\",",
            "\"created_at\":\"2026-09-21T10:00:00Z\",",
            "\"error\":\"RESOURCE_EXHAUSTED 429 quota. Resets in 16h.\"}\n",
            "{\"id\":\"success\",\"type\":\"PLANNER_RESPONSE\",",
            "\"created_at\":\"2026-09-21T10:01:00Z\",\"tool_calls\":[]}\n",
            "{\"id\":\"still-being-written\""
        ),
    )
    .unwrap();

    let report = analyze_agy_transcripts(
        &[AgyTranscriptInput {
            path: transcript,
            surface: AgySurface::Ide,
            session_id: Some("quota-session".to_string()),
            workspace: None,
        }],
        &pricing(),
        generated_at(),
    );

    assert_eq!(report.diagnostics.malformed_lines, 1);
    assert_eq!(report.diagnostics.partial_trailing_lines, 1);
    let signals = &report.sessions[0].signals;
    assert_eq!(signals.quota_status, "ok");
    assert_eq!(signals.quota_reset_in.as_deref(), Some("16h"));
    assert!(
        signals
            .last_quota_error
            .as_deref()
            .is_some_and(|value| value.contains("429"))
    );
}

#[test]
fn infers_session_from_standard_brain_path_and_missing_model_is_explicit_unknown() {
    let temp = TempDir::new();
    let transcript = temp
        .0
        .join("brain/session-from-path/.system_generated/logs/transcript.jsonl");
    fs::create_dir_all(transcript.parent().unwrap()).unwrap();
    fs::write(
        &transcript,
        "{\"type\":\"USER_INPUT\",\"content\":\"hi\"}\n",
    )
    .unwrap();

    let report = analyze_agy_transcripts(
        &[AgyTranscriptInput {
            path: transcript,
            surface: AgySurface::Cli,
            session_id: None,
            workspace: None,
        }],
        &pricing(),
        generated_at(),
    );

    let session = &report.sessions[0];
    assert_eq!(session.session_id.as_deref(), Some("session-from-path"));
    assert_eq!(session.cost.amount, None);
    assert_eq!(session.cost.reason.as_deref(), Some("model not recorded"));
}

#[test]
fn normalizes_monitor_model_families() {
    assert_eq!(
        normalize_agy_model("Gemini 3.8 Flash Low"),
        "gemini-3.8-flash-low"
    );
    assert_eq!(
        normalize_agy_model("Claude Sonnet 4.6"),
        "claude-sonnet-4-6"
    );
    assert_eq!(normalize_agy_model("GPT OSS 120B"), "gpt-oss-120b-medium");
}
