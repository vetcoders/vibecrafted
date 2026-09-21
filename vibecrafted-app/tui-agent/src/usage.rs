//! VOC presentation mapping for the canonical `control-core` usage report.
//!
//! `control-core` owns receipt validation, filtering and aggregation. This
//! module only converts that public projection into compact terminal labels.

use chrono::Utc;
use control_core::{ControlPlane, UsageFilter};
use serde_json::Value;
use std::io;
use std::path::Path;

#[derive(Debug, Clone, Default, PartialEq)]
pub struct UsageDashboard {
    pub runs: Vec<UsageRun>,
    pub tokens_total_known: u64,
    pub runs_tokens_unknown: usize,
    pub usd_total: f64,
    pub credits_total: f64,
    pub runs_cost_unknown: usize,
    pub failures: usize,
    pub observed_from: Option<String>,
    pub observed_to: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct UsageRun {
    pub run_id: String,
    pub provider: String,
    pub agent: String,
    pub model: String,
    pub status: String,
    pub timestamp: String,
    pub tokens_total: Option<u64>,
    pub cost: UsageCost,
    pub failure: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum UsageCost {
    Known { amount: f64, unit: String },
    Unknown,
}

impl UsageDashboard {
    pub fn load(control_plane_root: &Path) -> io::Result<Self> {
        let report = ControlPlane::from_control_plane_home(control_plane_root).usage_report(
            Utc::now(),
            UsageFilter {
                since: None,
                since_label: "all retained".to_string(),
                ..UsageFilter::default()
            },
        );
        let observed_from = report.runs.last().map(|run| run.recorded_at.clone());
        let observed_to = report.runs.first().map(|run| run.recorded_at.clone());
        let usd_total = report
            .totals
            .cost_by_unit
            .get("USD")
            .copied()
            .unwrap_or(0.0);
        let credits_total = report
            .totals
            .cost_by_unit
            .get("credits")
            .copied()
            .unwrap_or(0.0);
        let runs = report
            .runs
            .into_iter()
            .map(|run| UsageRun {
                run_id: run.run_id,
                provider: display_value(&run.provider),
                agent: display_value(&run.agent),
                model: display_value(&run.model),
                status: run.status,
                timestamp: run.recorded_at,
                tokens_total: run.tokens.tokens_total.as_u64(),
                cost: run
                    .cost
                    .amount
                    .as_f64()
                    .map(|amount| UsageCost::Known {
                        amount,
                        unit: run
                            .cost
                            .currency
                            .or(run.cost.unit)
                            .unwrap_or_else(|| "unknown unit".to_string()),
                    })
                    .unwrap_or(UsageCost::Unknown),
                failure: run.failure_kind.or(run.failure),
            })
            .collect();
        Ok(Self {
            runs,
            tokens_total_known: report.totals.tokens_total_known,
            runs_tokens_unknown: report.totals.runs_tokens_unknown,
            usd_total,
            credits_total,
            runs_cost_unknown: report.totals.runs_cost_unknown,
            failures: report.totals.runs_failed,
            observed_from,
            observed_to,
        })
    }

    pub fn window_label(&self) -> String {
        match (&self.observed_from, &self.observed_to) {
            (Some(from), Some(to)) => {
                format!("all retained · {} → {}", short_time(from), short_time(to))
            }
            _ => "all retained · no telemetry receipts".to_string(),
        }
    }
}

fn display_value(value: &Value) -> String {
    value
        .as_str()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or("unknown")
        .to_string()
}

fn short_time(value: &str) -> &str {
    value.get(..16).unwrap_or(value)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn maps_canonical_report_without_combining_cost_units() {
        let dir = tempdir().unwrap();
        let runtime = dir.path().join("runtime_runs");
        for (id, meta) in [
            (
                "usd",
                serde_json::json!({"run_id":"usd","agent":"codex","model":"gpt","status":"completed","completed_at":"2026-09-21T10:00:00Z","exit_code":0,"usage":{"schema":"vibecrafted.usage.v1","unit":"tokens","source":"provider_stream","events":1,"tokens_input":30,"tokens_cached_input":0,"tokens_cache_write":{"value":"unknown"},"tokens_output":12,"tokens_total":42},"cost":{"amount":0.25,"currency":"USD","source":"provider_reported"}}),
            ),
            (
                "credits",
                serde_json::json!({"run_id":"credits","agent":"agy","status":"failed","completed_at":"2026-09-21T11:00:00Z","exit_code":1,"usage":{"schema":"vibecrafted.usage.v1","unit":"tokens","source":"provider_stream","events":0,"tokens_total":{"value":"unknown","reason":"no event"}},"cost":{"amount":7,"unit":"credits","source":"provider_reported"},"failure":{"kind":"quota_exhausted"}}),
            ),
        ] {
            let run = runtime.join(id);
            fs::create_dir_all(&run).unwrap();
            fs::write(run.join("meta.json"), serde_json::to_vec(&meta).unwrap()).unwrap();
        }
        let dashboard = UsageDashboard::load(dir.path()).unwrap();
        assert_eq!(dashboard.runs.len(), 2);
        assert_eq!(dashboard.tokens_total_known, 42);
        assert_eq!(dashboard.runs_tokens_unknown, 1);
        assert_eq!(dashboard.usd_total, 0.25);
        assert_eq!(dashboard.credits_total, 7.0);
        assert_eq!(dashboard.runs_cost_unknown, 0);
        assert_eq!(dashboard.failures, 1);
        assert_eq!(dashboard.runs[0].run_id, "credits");
    }

    #[test]
    fn missing_runtime_runs_is_a_truthful_empty_projection() {
        let dir = tempdir().unwrap();
        let dashboard = UsageDashboard::load(dir.path()).unwrap();
        assert!(dashboard.runs.is_empty());
        assert_eq!(
            dashboard.window_label(),
            "all retained · no telemetry receipts"
        );
    }
}
