//! VOC presentation mapping for the canonical `control-core` usage report.
//!
//! `control-core` owns receipt validation, filtering and aggregation. This
//! module only converts that public projection into compact terminal labels.

use chrono::Utc;
use control_core::{ControlPlane, UsageFilter, UsageReport};
use serde_json::Value;
use std::io;
use std::path::Path;
use std::time::Duration;

/// Reader state from the canonical projection, independent of unknown prices
/// or providers that do not expose measurements.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum UsageRefreshState {
    #[default]
    Ready,
    Pending,
    Stale,
}

impl UsageRefreshState {
    fn from_report(report: &UsageReport) -> Self {
        if report.recovery.refreshing
            || report.recovery.pending_runs > 0
            || report.local_sources.iter().any(|source| source.refreshing)
        {
            Self::Pending
        } else if report.recovery.stale_runs > 0
            || !report.recovery.failure_reasons.is_empty()
            || report.recovery.last_error.is_some()
            || report
                .local_sources
                .iter()
                .any(|source| source.stale || source.error.is_some())
        {
            Self::Stale
        } else {
            Self::Ready
        }
    }
}

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
    pub refresh_state: UsageRefreshState,
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
        let refresh_state = UsageRefreshState::from_report(&report);
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
            refresh_state,
        })
    }

    /// Background readers publish in memory, so a pending answer needs another
    /// call even when no file changed. Failed readers retain measurements and
    /// retry slowly; complete but unmeasured answers do not spin.
    pub fn refresh_interval(&self) -> Option<Duration> {
        match self.refresh_state {
            UsageRefreshState::Ready => None,
            UsageRefreshState::Pending => Some(Duration::from_secs(1)),
            UsageRefreshState::Stale => Some(Duration::from_secs(30)),
        }
    }

    pub fn window_label(&self) -> String {
        let window = match (&self.observed_from, &self.observed_to) {
            (Some(from), Some(to)) => {
                format!("all retained · {} → {}", short_time(from), short_time(to))
            }
            _ if self.refresh_state == UsageRefreshState::Pending => {
                return "all retained · loading telemetry".to_string();
            }
            _ => "all retained · no telemetry receipts".to_string(),
        };
        match self.refresh_state {
            UsageRefreshState::Ready => window,
            UsageRefreshState::Pending => format!("{window} · updating"),
            UsageRefreshState::Stale => format!("{window} · stale · retrying"),
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

    #[test]
    fn canonical_reader_state_distinguishes_pending_stale_and_complete_unknown() {
        let dir = tempdir().unwrap();
        let mut report = ControlPlane::from_control_plane_home(dir.path())
            .usage_report(Utc::now(), UsageFilter::default());
        // Providers without a native ledger and successfully unknown costs are
        // complete answers; absence must not cause endless recovery polling.
        report.recovery.unavailable_runs = 3;
        assert_eq!(
            UsageRefreshState::from_report(&report),
            UsageRefreshState::Ready
        );

        report.recovery.pending_runs = 1;
        assert_eq!(
            UsageRefreshState::from_report(&report),
            UsageRefreshState::Pending
        );
        report.recovery.pending_runs = 0;
        report.recovery.refreshing = true;
        assert_eq!(
            UsageRefreshState::from_report(&report),
            UsageRefreshState::Pending
        );
        report.recovery.refreshing = false;
        report.recovery.stale_runs = 1;
        assert_eq!(
            UsageRefreshState::from_report(&report),
            UsageRefreshState::Stale
        );
        report.recovery.stale_runs = 0;
        report
            .recovery
            .failure_reasons
            .insert("parser_failed".into(), 1);
        assert_eq!(
            UsageRefreshState::from_report(&report),
            UsageRefreshState::Stale
        );
        report.recovery.failure_reasons.clear();
        report.local_sources[0].stale = true;
        assert_eq!(
            UsageRefreshState::from_report(&report),
            UsageRefreshState::Stale
        );
        report.local_sources[0].refreshing = true;
        assert_eq!(
            UsageRefreshState::from_report(&report),
            UsageRefreshState::Pending
        );
        report.local_sources[0].refreshing = false;
        report.local_sources[0].stale = false;
        report.local_sources[0].error = Some("unreadable".into());
        assert_eq!(
            UsageRefreshState::from_report(&report),
            UsageRefreshState::Stale
        );
    }

    #[test]
    fn pending_is_polled_faster_than_stale_and_known_values_remain_visible() {
        let mut dashboard = UsageDashboard {
            refresh_state: UsageRefreshState::Pending,
            tokens_total_known: 42,
            ..UsageDashboard::default()
        };
        assert_eq!(dashboard.refresh_interval(), Some(Duration::from_secs(1)));
        assert_eq!(dashboard.window_label(), "all retained · loading telemetry");
        dashboard.refresh_state = UsageRefreshState::Stale;
        assert_eq!(dashboard.refresh_interval(), Some(Duration::from_secs(30)));
        assert!(dashboard.window_label().ends_with("stale · retrying"));
        assert_eq!(dashboard.tokens_total_known, 42);
        dashboard.refresh_state = UsageRefreshState::Ready;
        assert_eq!(dashboard.refresh_interval(), None);
    }
}
