use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use control_core::{KimiModelRate, KimiPricing, KimiWireInput, analyze_kimi_wires};

static NEXT_ID: AtomicU64 = AtomicU64::new(0);

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let id = NEXT_ID.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "vibecrafted-kimi-adapter-{}-{id}",
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

fn pricing() -> KimiPricing {
    KimiPricing {
        models: BTreeMap::from([(
            "kimi-k3".to_string(),
            KimiModelRate {
                fresh_input_per_million: 3.0,
                cache_read_per_million: 0.30,
                output_per_million: 15.0,
            },
        )]),
    }
}

#[test]
fn parses_exact_buckets_estimates_cost_and_deduplicates_across_wires() {
    let temp = TempDir::new();
    let first = temp.0.join("one.jsonl");
    let second = temp.0.join("two.jsonl");
    let record = r#"{"type":"usage.record","usageScope":"turn","id":"turn-1","model":"kimi-code/kimi-k3","usage":{"inputOther":100000,"inputCacheCreation":20000,"inputCacheRead":300000,"output":10000}}"#;
    fs::write(&first, format!("{record}\n")).unwrap();
    fs::write(&second, format!("{record}\n")).unwrap();

    let report = analyze_kimi_wires(
        &[
            KimiWireInput {
                path: first,
                session_id: Some("session-a".into()),
            },
            KimiWireInput {
                path: second,
                session_id: Some("session-a".into()),
            },
        ],
        &pricing(),
        "2026-09-21T12:00:00Z",
    );

    assert_eq!(report.slices.len(), 1);
    let slice = &report.slices[0];
    assert_eq!(slice.session_id.as_deref(), Some("session-a"));
    assert_eq!(slice.model.as_deref(), Some("kimi-k3"));
    assert_eq!(slice.usage_events, 1);
    assert_eq!(slice.tokens.fresh_input, Some(120_000));
    assert_eq!(slice.tokens.cache_read, Some(300_000));
    assert_eq!(slice.tokens.output, Some(10_000));
    assert_eq!(slice.tokens.total, Some(430_000));
    assert_eq!(slice.cost.amount, Some(0.6));
    assert_eq!(slice.cost.source, "estimated");
    assert_eq!(slice.cost.unit, "api-equiv");
    assert_eq!(report.diagnostics.duplicate_usage_records, 1);
}

#[test]
fn incomplete_buckets_and_missing_price_remain_unknown() {
    let temp = TempDir::new();
    let wire = temp.0.join("wire.jsonl");
    fs::write(
        &wire,
        concat!(
            "{\"type\":\"usage.record\",\"usageScope\":\"turn\",",
            "\"model\":\"unpriced\",\"usage\":{\"inputOther\":10,\"output\":2}}\n"
        ),
    )
    .unwrap();

    let report = analyze_kimi_wires(
        &[KimiWireInput {
            path: wire,
            session_id: Some("session-b".into()),
        }],
        &pricing(),
        "2026-09-21T12:00:00Z",
    );
    let slice = &report.slices[0];
    assert_eq!(slice.tokens.fresh_input, None);
    assert_eq!(slice.tokens.cache_read, None);
    assert_eq!(slice.tokens.output, Some(2));
    assert_eq!(slice.tokens.total, None);
    assert_eq!(slice.cost.amount, None);
    assert_eq!(
        slice.cost.reason.as_deref(),
        Some("no injected price for model")
    );
}

#[test]
fn one_incomplete_record_keeps_the_aggregate_bucket_unknown() {
    let temp = TempDir::new();
    let wire = temp.0.join("wire.jsonl");
    fs::write(
        &wire,
        concat!(
            "{\"type\":\"usage.record\",\"usageScope\":\"turn\",\"id\":\"a\",",
            "\"model\":\"kimi-k3\",\"usage\":{\"inputOther\":10,\"output\":2}}\n",
            "{\"type\":\"usage.record\",\"usageScope\":\"turn\",\"id\":\"b\",",
            "\"model\":\"kimi-k3\",\"usage\":{\"inputOther\":10,",
            "\"inputCacheCreation\":2,\"inputCacheRead\":3,\"output\":4}}\n"
        ),
    )
    .unwrap();

    let report = analyze_kimi_wires(
        &[KimiWireInput {
            path: wire,
            session_id: Some("session-b".into()),
        }],
        &pricing(),
        "2026-09-21T12:00:00Z",
    );
    let slice = &report.slices[0];
    assert_eq!(slice.usage_events, 2);
    assert_eq!(slice.tokens.fresh_input, None);
    assert_eq!(slice.tokens.cache_read, None);
    assert_eq!(slice.tokens.output, Some(6));
    assert_eq!(slice.cost.amount, None);
    assert_eq!(
        slice.cost.reason.as_deref(),
        Some("token buckets incomplete")
    );
}

#[test]
fn ignores_partial_trailing_json_and_isolates_bad_complete_lines() {
    let temp = TempDir::new();
    let wire = temp.0.join("wire.jsonl");
    fs::write(
        &wire,
        concat!(
            "not-json\n",
            "{\"type\":\"usage.record\",\"usageScope\":\"turn\",",
            "\"model\":\"kimi-k3\",\"usage\":{\"inputOther\":1,",
            "\"inputCacheCreation\":2,\"inputCacheRead\":3,\"output\":4}}\n",
            "{\"type\":\"usage.record\""
        ),
    )
    .unwrap();

    let report = analyze_kimi_wires(
        &[KimiWireInput {
            path: wire,
            session_id: Some("session-c".into()),
        }],
        &pricing(),
        "2026-09-21T12:00:00Z",
    );
    assert_eq!(report.slices.len(), 1);
    assert_eq!(report.slices[0].tokens.total, Some(10));
    assert_eq!(report.diagnostics.malformed_lines, 1);
    assert_eq!(report.diagnostics.partial_trailing_lines, 1);
}

#[test]
fn derives_session_from_injected_path_and_keeps_missing_model_unknown() {
    let temp = TempDir::new();
    let session_dir = temp.0.join("session_path-owned").join("agents/a");
    fs::create_dir_all(&session_dir).unwrap();
    let wire = session_dir.join("wire.jsonl");
    fs::write(
        &wire,
        "{\"type\":\"usage.record\",\"usageScope\":\"turn\",\"usage\":{\"inputOther\":1,\"inputCacheCreation\":0,\"inputCacheRead\":0,\"output\":1}}\n",
    )
    .unwrap();

    let report = analyze_kimi_wires(
        &[KimiWireInput {
            path: wire,
            session_id: None,
        }],
        &pricing(),
        "2026-09-21T12:00:00Z",
    );
    let slice = &report.slices[0];
    assert_eq!(slice.session_id.as_deref(), Some("path-owned"));
    assert_eq!(slice.model, None);
    assert_eq!(slice.cost.amount, None);
    assert_eq!(slice.cost.reason.as_deref(), Some("model not recorded"));
}
