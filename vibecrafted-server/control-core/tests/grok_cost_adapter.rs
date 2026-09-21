#[path = "../src/usage_grok.rs"]
mod grok;

use std::fs;
use std::path::PathBuf;

use chrono::{TimeZone, Utc};
use grok::{GrokAnalysisInput, GrokMeasurementKind, GrokPricing, analyze_grok_cost};
use serde_json::json;

fn fixture_dir(label: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!(
        "grok-cost-{label}-{}-{}",
        std::process::id(),
        Utc::now().timestamp_nanos_opt().unwrap_or_default()
    ));
    fs::create_dir_all(&path).expect("fixture dir");
    path
}

fn pricing() -> GrokPricing {
    GrokPricing {
        source: "xai-injected-test-v1".to_string(),
        currency: "USD".to_string(),
        input_per_million: Some(1.0),
        cached_input_per_million: Some(0.2),
        output_per_million: Some(2.0),
        total_per_million: Some(1.25),
    }
}

fn analyze(paths: &[PathBuf], pricing: &GrokPricing) -> grok::GrokCostAnalysis {
    analyze_grok_cost(GrokAnalysisInput {
        evidence_paths: paths,
        session_id: "grok-session",
        started_at: Utc.with_ymd_and_hms(2026, 9, 21, 10, 0, 0).unwrap(),
        ended_at: Utc.with_ymd_and_hms(2026, 9, 21, 12, 0, 0).unwrap(),
        pricing,
    })
}

#[test]
fn exact_stream_usage_wins_and_duplicate_copies_count_once() {
    let root = fixture_dir("exact");
    let event = json!({
        "type": "end",
        "eventId": "end-1",
        "sessionId": "grok-session",
        "ts": "2026-09-21T11:00:00Z",
        "usage": {
            "input_tokens": 34113,
            "cache_read_input_tokens": 2752,
            "output_tokens": 151,
            "total_tokens": 37016
        },
        "modelUsage": {
            "grok-build": {
                "inputTokens": 34113,
                "cacheReadInputTokens": 2752,
                "outputTokens": 151,
                "modelCalls": 1
            }
        }
    });
    let first = root.join("stream.jsonl");
    let copied = root.join("stream-copy.jsonl");
    let line = format!("{}\n", serde_json::to_string(&event).unwrap());
    fs::write(&first, &line).expect("stream");
    fs::write(&copied, &line).expect("copy");

    let analysis = analyze(&[first, copied], &pricing());
    assert_eq!(analysis.usage.kind, GrokMeasurementKind::Exact);
    assert_eq!(analysis.usage.input_tokens, Some(34113));
    assert_eq!(analysis.usage.cached_input_tokens, Some(2752));
    assert_eq!(analysis.usage.output_tokens, Some(151));
    assert_eq!(analysis.usage.total_tokens, Some(37016));
    assert_eq!(analysis.cost.kind, GrokMeasurementKind::Estimated);
    assert_eq!(analysis.cost.amount, Some(0.034965));
    assert_eq!(analysis.model.as_deref(), Some("grok-build"));
    assert_eq!(analysis.evidence.duplicates_skipped, 1);
    assert_eq!(analysis.evidence.records_used, 1);
    fs::remove_dir_all(root).ok();
}

#[test]
fn provider_reported_cost_is_exact_when_every_exact_event_reports_it() {
    let root = fixture_dir("reported");
    let stream = root.join("stream.jsonl");
    let events = [
        json!({
            "type":"end", "eventId":"one", "sessionId":"grok-session",
            "ts":"2026-09-21T10:30:00Z", "cost_usd":0.25,
            "usage":{"input_tokens":100,"output_tokens":10,"total_tokens":110}
        }),
        json!({
            "type":"end", "eventId":"two", "sessionId":"grok-session",
            "ts":"2026-09-21T11:30:00Z", "cost_usd":0.5,
            "usage":{"input_tokens":200,"output_tokens":20,"total_tokens":220}
        }),
    ];
    fs::write(
        &stream,
        events
            .iter()
            .map(|event| serde_json::to_string(event).unwrap())
            .collect::<Vec<_>>()
            .join("\n"),
    )
    .expect("stream");

    let analysis = analyze(&[stream], &pricing());
    assert_eq!(analysis.usage.total_tokens, Some(330));
    assert_eq!(analysis.cost.kind, GrokMeasurementKind::Exact);
    assert_eq!(analysis.cost.amount, Some(0.75));
    assert_eq!(analysis.cost.source, "provider_reported");
    fs::remove_dir_all(root).ok();
}

#[test]
fn session_updates_produce_labelled_estimate_with_time_session_and_event_dedupe() {
    let root = fixture_dir("snapshots");
    let updates = root.join("updates.jsonl");
    let record = |event: &str, session: &str, stamp: i64, prompt: &str, total: u64| {
        json!({
            "timestamp": stamp / 1000,
            "params": {
                "sessionId": session,
                "_meta": {
                    "eventId": event,
                    "agentTimestampMs": stamp,
                    "promptId": prompt,
                    "totalTokens": total
                },
                "update": {"_meta":{"modelId":"grok-4.6"}}
            }
        })
    };
    let values = [
        record("before", "grok-session", 1_794_999_999_000, "p1", 100),
        record("a", "grok-session", 1_795_000_400_000, "p1", 1000),
        record("a", "grok-session", 1_795_000_400_000, "p1", 1000),
        record("b", "grok-session", 1_795_002_000_000, "p1", 1400),
        record("c", "other-session", 1_795_002_100_000, "p1", 9000),
    ];
    let mut body = values
        .iter()
        .map(|value| serde_json::to_string(value).unwrap())
        .collect::<Vec<_>>()
        .join("\n");
    body.push_str("\n{partial");
    fs::write(&updates, body).expect("updates");

    let start = Utc.timestamp_millis_opt(1_795_000_000_000).unwrap();
    let end = Utc.timestamp_millis_opt(1_795_003_000_000).unwrap();
    let table = pricing();
    let analysis = analyze_grok_cost(GrokAnalysisInput {
        evidence_paths: &[updates],
        session_id: "grok-session",
        started_at: start,
        ended_at: end,
        pricing: &table,
    });
    assert_eq!(analysis.usage.kind, GrokMeasurementKind::Estimated);
    assert_eq!(analysis.usage.total_tokens, Some(1400));
    assert_eq!(analysis.cost.kind, GrokMeasurementKind::Estimated);
    assert_eq!(analysis.cost.amount, Some(0.00175));
    assert_eq!(analysis.model.as_deref(), Some("grok-4.6"));
    assert_eq!(analysis.evidence.duplicates_skipped, 1);
    assert_eq!(analysis.evidence.malformed_records, 1);
    assert!(
        analysis
            .warnings
            .iter()
            .any(|warning| warning.contains("malformed"))
    );
    fs::remove_dir_all(root).ok();
}

#[test]
fn partial_inputs_and_missing_price_remain_visible_unknowns() {
    let root = fixture_dir("unknown");
    let summary = root.join("summary.json");
    fs::write(
        &summary,
        serde_json::to_vec(&json!({
            "session_id":"grok-session",
            "current_model_id":"grok-4.6",
            "created_at":"2026-09-21T10:30:00Z"
        }))
        .unwrap(),
    )
    .expect("summary");
    let missing = root.join("missing.jsonl");
    let mut table = pricing();
    table.total_per_million = None;

    let analysis = analyze(&[summary, missing], &table);
    assert_eq!(analysis.usage.kind, GrokMeasurementKind::Unknown);
    assert_eq!(analysis.cost.kind, GrokMeasurementKind::Unknown);
    assert!(analysis.usage.reason.contains("no exact usage"));
    assert_eq!(analysis.evidence.files_requested, 2);
    assert_eq!(analysis.evidence.files_readable, 1);
    assert!(
        analysis
            .warnings
            .iter()
            .any(|warning| warning.contains("unreadable"))
    );
    fs::remove_dir_all(root).ok();
}
