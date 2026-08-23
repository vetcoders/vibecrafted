//! Server-backed observation surface for `voc`.
//!
//! The LIVE chip used to dump `work-…` ids from a Python loop. This module
//! is the product reader: one origin (`/api/control/state` + transcript),
//! human labels, no second liveness census.

use serde::Deserialize;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

/// Canonical product origin — the loopback bind vc-server and server_config
/// default to (`127.0.0.1:3024`; 3025 is only the leptos reload port). A
/// remote or tailnet server is an operator choice expressed through
/// `VC_SERVER_URL` / `--server`, never a host address baked into the binary.
pub const DEFAULT_SERVER: &str = "http://127.0.0.1:3024";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConsoleView {
    Observe,
    Full,
}

impl ConsoleView {
    pub fn parse(raw: &str) -> anyhow::Result<Self> {
        match raw {
            "observe" | "live" => Ok(Self::Observe),
            "full" | "classic" => Ok(Self::Full),
            other => anyhow::bail!("unknown --view {other} (observe|full)"),
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ObserveState {
    pub origin: String,
    pub generated_at: String,
    pub status: ObserveHealth,
    pub error: Option<String>,
    pub runs: Vec<ObserveRun>,
    pub selected: usize,
    pub transcript: String,
    pub transcript_run_id: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ObserveHealth {
    Live,
    Degraded,
    #[default]
    Offline,
}

impl ObserveHealth {
    pub fn label(self) -> &'static str {
        match self {
            Self::Live => "LIVE",
            Self::Degraded => "DEGRADED",
            Self::Offline => "OFFLINE",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObserveRun {
    pub run_id: String,
    pub agent: String,
    pub skill: String,
    pub repo: String,
    pub state: String,
    pub age: String,
    pub liveness: String,
}

impl ObserveRun {
    pub fn list_line(&self) -> String {
        format!(
            "{:<7} {:<10} {:<14} {:>4}",
            truncate(&self.agent, 7),
            truncate(&self.skill, 10),
            truncate(&self.repo, 14),
            self.age
        )
    }

    pub fn title_line(&self) -> String {
        format!(
            "{} · {} · {} · {}",
            self.agent, self.skill, self.repo, self.state
        )
    }
}

#[derive(Debug, Deserialize)]
struct StateEnvelope {
    generated_at: Option<String>,
    #[serde(default)]
    active_runs: Vec<RunDto>,
    #[serde(default)]
    stalled_runs: Vec<RunDto>,
}

#[derive(Debug, Deserialize)]
struct RunDto {
    run_id: Option<String>,
    #[serde(default)]
    state: String,
    #[serde(default)]
    agent: String,
    #[serde(default)]
    skill: String,
    #[serde(default)]
    root: String,
    #[serde(default)]
    started_at: String,
    #[serde(default)]
    liveness: String,
}

#[derive(Debug, Deserialize)]
struct TranscriptDto {
    body: Option<String>,
}

pub fn normalize_origin(raw: &str) -> String {
    raw.trim().trim_end_matches('/').to_string()
}

pub fn default_server_origin() -> String {
    for key in [
        "VC_SERVER_URL",
        "VC_SERVER_BROWSER_URL",
        "VIBECRAFTED_SERVER",
    ] {
        if let Ok(value) = std::env::var(key) {
            let trimmed = value.trim();
            if !trimmed.is_empty() {
                return normalize_origin(trimmed);
            }
        }
    }
    DEFAULT_SERVER.to_string()
}

pub fn repo_label(root: &str) -> String {
    root.trim_end_matches('/')
        .rsplit('/')
        .next()
        .unwrap_or("")
        .to_string()
}

pub fn age_label(started_at: &str, now: SystemTime) -> String {
    let parsed = chrono::DateTime::parse_from_rfc3339(started_at).ok();
    let Some(started) = parsed else {
        return "—".to_string();
    };
    let started = SystemTime::UNIX_EPOCH + Duration::from_secs(started.timestamp().max(0) as u64);
    let elapsed = now.duration_since(started).unwrap_or_default();
    if elapsed.as_secs() < 3600 {
        return format!("{}m", elapsed.as_secs() / 60);
    }
    if elapsed.as_secs() < 86400 {
        return format!("{}h", elapsed.as_secs() / 3600);
    }
    format!("{}d", elapsed.as_secs() / 86400)
}

pub fn parse_state_json(bytes: &[u8], now: SystemTime) -> anyhow::Result<Vec<ObserveRun>> {
    let envelope: StateEnvelope = serde_json::from_slice(bytes)?;
    Ok(runs_from_envelope(envelope, now))
}

fn runs_from_envelope(envelope: StateEnvelope, now: SystemTime) -> Vec<ObserveRun> {
    let mut runs = Vec::new();
    for dto in envelope
        .active_runs
        .into_iter()
        .chain(envelope.stalled_runs)
    {
        let run_id = dto.run_id.unwrap_or_default();
        if run_id.is_empty() {
            continue;
        }
        runs.push(ObserveRun {
            run_id,
            agent: empty_as_unknown(dto.agent),
            skill: empty_as_unknown(dto.skill),
            repo: repo_label(&dto.root),
            state: empty_as_unknown(dto.state),
            age: age_label(&dto.started_at, now),
            liveness: dto.liveness,
        });
    }
    runs
}

pub fn fetch_state(origin: &str) -> anyhow::Result<(String, Vec<ObserveRun>)> {
    let url = format!("{}/api/control/state", normalize_origin(origin));
    let response = ureq::get(&url).timeout(Duration::from_secs(2)).call()?;
    let body = response.into_string()?;
    let envelope: StateEnvelope = serde_json::from_str(&body)?;
    let generated = envelope.generated_at.clone().unwrap_or_default();
    let runs = runs_from_envelope(envelope, SystemTime::now());
    Ok((generated, runs))
}

pub fn fetch_transcript(origin: &str, run_id: &str) -> anyhow::Result<String> {
    if !is_safe_run_id(run_id) {
        anyhow::bail!("invalid run id");
    }
    let url = format!(
        "{}/api/control/runs/{run_id}/transcript",
        normalize_origin(origin)
    );
    let response = ureq::get(&url).timeout(Duration::from_secs(3)).call()?;
    let dto: TranscriptDto = response.into_json()?;
    Ok(dto.body.unwrap_or_default())
}

pub fn is_safe_run_id(run_id: &str) -> bool {
    !run_id.is_empty()
        && run_id.len() <= 80
        && run_id
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || ch == '-' || ch == '_')
}

fn empty_as_unknown(value: String) -> String {
    if value.trim().is_empty() {
        "—".to_string()
    } else {
        value
    }
}

fn truncate(value: &str, width: usize) -> String {
    if value.chars().count() <= width {
        return value.to_string();
    }
    value
        .chars()
        .take(width.saturating_sub(1))
        .collect::<String>()
        + "…"
}

#[allow(dead_code)]
fn _unix_now_for_tests() -> SystemTime {
    UNIX_EPOCH + Duration::from_secs(1_787_000_000)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_live_payload_uses_human_labels() {
        let raw = br#"{
          "generated_at": "2026-08-16T20:14:07+00:00",
          "active_runs": [{
            "run_id": "work-260816-215903-94636",
            "state": "active",
            "agent": "grok",
            "skill": "workflow",
            "root": "/srv/vetcoders/vibecrafted",
            "started_at": "2026-08-16T19:59:03.642175+00:00",
            "liveness": "pid_alive"
          }],
          "stalled_runs": []
        }"#;
        let now = UNIX_EPOCH + Duration::from_secs(1_787_000_000);
        let runs = parse_state_json(raw, now).unwrap();
        assert_eq!(runs.len(), 1);
        assert_eq!(runs[0].repo, "vibecrafted");
        assert_eq!(runs[0].agent, "grok");
        assert!(!runs[0].list_line().contains("work-260816-215903-94636"));
        assert!(runs[0].list_line().contains("grok"));
        assert!(runs[0].list_line().contains("workflow"));
    }

    #[test]
    fn reject_path_run_ids() {
        assert!(!is_safe_run_id("../secret"));
        assert!(is_safe_run_id("work-260816-215903-94636"));
    }
}
