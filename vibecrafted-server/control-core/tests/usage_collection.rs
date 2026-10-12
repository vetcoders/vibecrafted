use chrono::{Duration, Utc};
use control_core::{ControlPlane, UsageFilter};
use serde_json::json;
use std::fs;

#[test]
fn local_projection_keeps_full_session_usage_vc_origin_unknowns_and_all_pickers() {
    let home = std::env::temp_dir().join(format!(
        "vc-local-collection-{}-{}",
        std::process::id(),
        Utc::now().timestamp_nanos_opt().unwrap()
    ));
    let now = Utc::now();
    fs::create_dir_all(home.join("telemetry/archive")).unwrap();
    fs::write(
        home.join("telemetry/archive/codex.jsonl"),
        "{\"day\":\"2026-10-12\",\"input_tokens\":1000000}\n",
    )
    .unwrap();
    for run_id in ["vc-one", "vc-import"] {
        let dir = home.join("control_plane/runtime_runs").join(run_id);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("meta.json"),json!({"run_id":run_id,"agent":"codex","agent_model":"gpt-test","updated_at":now.to_rfc3339(),"provider_session_id":"shared","status":"completed","exit_code":0,"usage":{"tokens_total":20}}).to_string()).unwrap();
    }
    fs::write(home.join("telemetry/local-usage.json"),json!({"schema":"vibecrafted.local-usage.v1","generated_at":now.to_rfc3339(),"providers":{"codex":{"status":"observed"}},"records":[
        {"agent":"codex","id":"shared","mtime":now.timestamp(),"model":"gpt-test","processed_total":100,"measurement_observed":true,"native":{"input_tokens":90,"output_tokens":10}},
        {"agent":"codex","id":"shared","mtime":now.timestamp(),"model":"gpt-test","processed_total":100,"measurement_observed":true},
        {"agent":"claude","id":"shared","mtime":now.timestamp(),"measurement_observed":false}
    ]}).to_string()).unwrap();
    let report = ControlPlane::new(&home).usage_report(now, UsageFilter::default());
    assert_eq!(report.totals.runs, 2);
    assert_eq!(report.totals.tokens_total_known, 100);
    assert_eq!(report.totals.runs_tokens_unknown, 1);
    let codex = report.runs.iter().find(|r| r.agent == "codex").unwrap();
    assert_eq!(codex.origin, "local+vibecrafted");
    assert_eq!(codex.vc_run_ids.len(), 2);
    assert_eq!(codex.status, "unknown");
    assert_eq!(codex.exit_code, None);
    assert_eq!(report.local_collection["status"], "observed");
    let filtered = ControlPlane::new(&home).usage_report(
        now,
        UsageFilter {
            provider: Some("anthropic".into()),
            model: Some("unknown".into()),
            ..Default::default()
        },
    );
    assert_eq!(filtered.totals.runs, 1);
    assert_eq!(filtered.pickers.providers.len(), 2);
    assert_eq!(filtered.pickers.models.len(), 2);
    let stale =
        ControlPlane::new(&home).usage_report(now + Duration::seconds(301), UsageFilter::default());
    assert_eq!(stale.local_collection["status"], "stale");
    fs::remove_dir_all(home).unwrap();
}

#[test]
fn disjoint_vc_windows_and_active_created_runs_are_preserved() {
    let home = std::env::temp_dir().join(format!(
        "vc-windows-{}-{}",
        std::process::id(),
        Utc::now().timestamp_nanos_opt().unwrap()
    ));
    let now = Utc::now();
    for (id, start, end) in [("one", -120, -100), ("two", -80, -60)] {
        let dir = home.join("control_plane/runtime_runs").join(id);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("meta.json"),json!({"run_id":id,"agent":"codex","provider_session_id":"resumed","started_at":(now+Duration::seconds(start)).to_rfc3339(),"completed_at":(now+Duration::seconds(end)).to_rfc3339(),"usage":{"tokens_total":20}}).to_string()).unwrap();
    }
    let dir = home.join("control_plane/runtime_runs/active");
    fs::create_dir_all(&dir).unwrap();
    fs::write(dir.join("meta.json"),json!({"run_id":"active","agent":"claude","created_at":now.to_rfc3339(),"status":"launching"}).to_string()).unwrap();
    let report = ControlPlane::new(&home).usage_report(now, UsageFilter::default());
    assert_eq!(report.totals.runs, 3);
    assert_eq!(report.totals.tokens_total_known, 40);
    assert_eq!(report.totals.runs_tokens_unknown, 1);
    fs::remove_dir_all(home).unwrap();
}
