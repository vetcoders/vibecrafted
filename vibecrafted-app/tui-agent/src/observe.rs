//! Server-backed observation surface for `voc`.
//!
//! The LIVE chip used to dump `work-…` ids from a Python loop. This module
//! is the product reader: one origin (`/api/control/state` + transcript),
//! human labels, no second liveness census.

use serde::Deserialize;
use std::cmp::Ordering;
use std::time::{Duration, SystemTime};

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

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ObserveSort {
    #[default]
    Latest,
    Oldest,
}

impl ObserveSort {
    pub fn next(self) -> Self {
        match self {
            Self::Latest => Self::Oldest,
            Self::Oldest => Self::Latest,
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Latest => "latest",
            Self::Oldest => "oldest",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum TranscriptView {
    #[default]
    Human,
    Raw,
}

impl TranscriptView {
    pub fn next(self) -> Self {
        match self {
            Self::Human => Self::Raw,
            Self::Raw => Self::Human,
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Human => "human",
            Self::Raw => "raw",
        }
    }
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
    pub sort: ObserveSort,
    pub transcript_view: TranscriptView,
    pub transcript: String,
    pub transcript_raw: String,
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
    pub session_id: Option<String>,
    pub operator_session: Option<String>,
    pub transcript_path: Option<String>,
    pub agent: String,
    pub skill: String,
    pub repo: String,
    pub state: String,
    pub age: String,
    pub liveness: String,
    /// Canonical seconds used for latest/oldest ordering. Display `age` is
    /// never a sort key. `None` stays honest: missing timestamps sort last.
    pub sort_ts: Option<i64>,
}

impl ObserveRun {
    pub fn switch_target(&self) -> Option<&str> {
        self.operator_session
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .or_else(|| {
                self.session_id
                    .as_deref()
                    .filter(|value| !value.trim().is_empty())
            })
    }

    pub fn is_genuinely_active(&self) -> bool {
        self.state.eq_ignore_ascii_case("active")
            && !self.liveness.to_ascii_lowercase().contains("dead")
            && !self.liveness.eq_ignore_ascii_case("terminal")
            && !observe_age_is_archive(&self.age)
    }

    pub fn is_archive(&self) -> bool {
        observe_age_is_archive(&self.age)
            || self.liveness.eq_ignore_ascii_case("terminal")
            || self.state.eq_ignore_ascii_case("unknown")
    }

    pub fn kind_label(&self) -> &str {
        if self.is_genuinely_active() {
            "active"
        } else if self.state.eq_ignore_ascii_case("stalled") || self.liveness.contains("dead") {
            "stalled"
        } else if self.is_archive() {
            "archive"
        } else {
            self.state.as_str()
        }
    }

    pub fn list_line(&self) -> String {
        format!(
            "{:<7} {:<8} {:<10} {:<12} {:>4}",
            truncate(self.kind_label(), 7),
            truncate(&self.agent, 8),
            truncate(&self.skill, 10),
            truncate(&self.repo, 12),
            self.age
        )
    }

    pub fn title_line(&self) -> String {
        format!(
            "{} · {} · {} · {}",
            self.kind_label(),
            self.skill,
            self.repo,
            self.agent
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
        let run = ObserveRun {
            run_id,
            session_id: None,
            operator_session: None,
            transcript_path: None,
            agent: empty_as_unknown(dto.agent),
            skill: empty_as_unknown(dto.skill),
            repo: repo_label(&dto.root),
            state: empty_as_unknown(dto.state),
            age: age_label(&dto.started_at, now),
            liveness: dto.liveness,
            sort_ts: parse_canonical_ts(&dto.started_at),
        };
        if run.is_archive() && !run.state.eq_ignore_ascii_case("stalled") {
            continue;
        }
        if observe_age_is_archive(&run.age) {
            continue;
        }
        runs.push(run);
    }
    runs
}

/// Project the cockpit from the same canonical control-plane state used by
/// Monitor and Mission Control. The server remains a transcript transport,
/// never a second live-session registry.
pub fn project_control_plane(state: &crate::state::ControlPlaneState) -> Vec<ObserveRun> {
    let now = chrono::Utc::now();
    let runs = crate::state::render_runs(state)
        .into_iter()
        .filter(|run| crate::state::is_actionable_kind(run.kind, &run.snapshot, now))
        .collect::<Vec<_>>();
    project_rendered_runs(&runs)
}

/// Adapt the already-selected cockpit rows for Observe. Scope, workspace,
/// search, and asynchronous control-plane refresh are decided by `App::runs`;
/// Observe is a transcript/session projection of that same collection.
pub fn project_rendered_runs(runs: &[crate::state::RenderedRun]) -> Vec<ObserveRun> {
    runs.iter()
        .cloned()
        .map(|run| {
            let snapshot = run.snapshot;
            let sort_ts = canonical_snapshot_ts(&snapshot);
            let state_label = snapshot.display_state();
            let liveness = snapshot
                .extra
                .get("liveness")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_else(|| run.kind.label())
                .to_string();
            ObserveRun {
                run_id: snapshot.run_id,
                session_id: snapshot.session_id,
                operator_session: snapshot.operator_session,
                transcript_path: snapshot.latest_transcript,
                agent: empty_as_unknown(snapshot.agent.unwrap_or_default()),
                skill: empty_as_unknown(snapshot.skill.unwrap_or_default()),
                repo: crate::state::workspace_label(snapshot.root.as_deref()),
                state: state_label,
                age: run.age_label,
                liveness,
                sort_ts,
            }
        })
        .collect()
}

pub fn sort_observe_runs(runs: &mut [ObserveRun], sort: ObserveSort) {
    runs.sort_by(|left, right| compare_observe_runs(left, right, sort));
}

fn compare_observe_runs(left: &ObserveRun, right: &ObserveRun, sort: ObserveSort) -> Ordering {
    match (left.sort_ts, right.sort_ts) {
        (Some(left_ts), Some(right_ts)) => {
            let primary = match sort {
                ObserveSort::Latest => right_ts.cmp(&left_ts),
                ObserveSort::Oldest => left_ts.cmp(&right_ts),
            };
            primary.then_with(|| left.run_id.cmp(&right.run_id))
        }
        (Some(_), None) => Ordering::Less,
        (None, Some(_)) => Ordering::Greater,
        (None, None) => left.run_id.cmp(&right.run_id),
    }
}

fn canonical_snapshot_ts(snapshot: &crate::state::RunSnapshot) -> Option<i64> {
    snapshot
        .started_at
        .as_deref()
        .and_then(parse_canonical_ts)
        .or_else(|| snapshot.updated_at.as_deref().and_then(parse_canonical_ts))
        .or_else(|| {
            snapshot
                .last_heartbeat
                .as_deref()
                .and_then(parse_canonical_ts)
        })
}

pub fn parse_canonical_ts(raw: &str) -> Option<i64> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }
    if let Ok(parsed) = chrono::DateTime::parse_from_rfc3339(trimmed) {
        return Some(parsed.timestamp());
    }
    trimmed.parse::<i64>().ok()
}

fn observe_age_is_archive(age: &str) -> bool {
    let trimmed = age.trim();
    if trimmed.ends_with('d') {
        return true;
    }
    if let Some(hours) = trimmed.strip_suffix('h') {
        return hours.parse::<u64>().unwrap_or(0) >= 3;
    }
    false
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::UNIX_EPOCH;

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
        let now = UNIX_EPOCH + Duration::from_secs(1_786_911_243); // 2026-08-16T20:14:03Z
        let runs = parse_state_json(raw, now).unwrap();
        assert_eq!(runs.len(), 1);
        assert_eq!(runs[0].repo, "vibecrafted");
        assert_eq!(runs[0].agent, "grok");
        assert!(runs[0].is_genuinely_active());
        assert!(!runs[0].list_line().contains("work-260816-215903-94636"));
        assert!(runs[0].list_line().contains("grok"));
        assert!(runs[0].list_line().contains("workflow"));
        assert!(runs[0].list_line().contains("active"));
    }

    #[test]
    fn parse_state_drops_day_old_rows_and_does_not_call_them_live() {
        let raw = br#"{
          "generated_at": "2026-08-16T20:14:07+00:00",
          "active_runs": [{
            "run_id": "stale-active",
            "state": "active",
            "agent": "codex",
            "skill": "implement",
            "root": "/srv/vetcoders/vibecrafted",
            "started_at": "2026-07-12T19:59:03+00:00",
            "liveness": "unknown"
          }],
          "stalled_runs": [{
            "run_id": "fresh-stall",
            "state": "stalled",
            "agent": "claude",
            "skill": "review",
            "root": "/srv/vetcoders/vibecrafted",
            "started_at": "2026-08-16T20:10:00+00:00",
            "liveness": "pid_dead"
          }]
        }"#;
        let now = UNIX_EPOCH + Duration::from_secs(1_786_911_243); // 2026-08-16T20:14:03Z
        let runs = parse_state_json(raw, now).unwrap();
        assert!(runs.iter().all(|run| run.run_id != "stale-active"));
        assert_eq!(runs.len(), 1);
        assert_eq!(runs[0].run_id, "fresh-stall");
        assert_eq!(runs[0].kind_label(), "stalled");
        assert!(!runs[0].is_genuinely_active());
    }

    #[test]
    fn reject_path_run_ids() {
        assert!(!is_safe_run_id("../secret"));
        assert!(is_safe_run_id("work-260816-215903-94636"));
    }

    #[test]
    fn observe_sort_uses_canonical_timestamps_not_age_labels() {
        let mut runs = vec![
            ObserveRun {
                run_id: "work-older".into(),
                session_id: None,
                operator_session: None,
                transcript_path: None,
                agent: "agy".into(),
                skill: "workflow".into(),
                repo: "vibecrafted".into(),
                state: "completed".into(),
                age: "17h".into(),
                liveness: "terminal".into(),
                sort_ts: Some(1_000),
            },
            ObserveRun {
                run_id: "work-newer".into(),
                session_id: None,
                operator_session: None,
                transcript_path: None,
                agent: "agy".into(),
                skill: "workflow".into(),
                repo: "vibecrafted".into(),
                state: "completed".into(),
                age: "15h".into(),
                liveness: "terminal".into(),
                sort_ts: Some(3_000),
            },
            ObserveRun {
                run_id: "work-mid".into(),
                session_id: None,
                operator_session: None,
                transcript_path: None,
                agent: "agy".into(),
                skill: "workflow".into(),
                repo: "vibecrafted".into(),
                state: "completed".into(),
                age: "16h".into(),
                liveness: "terminal".into(),
                sort_ts: Some(2_000),
            },
        ];
        sort_observe_runs(&mut runs, ObserveSort::Latest);
        assert_eq!(
            runs.iter()
                .map(|run| run.run_id.as_str())
                .collect::<Vec<_>>(),
            ["work-newer", "work-mid", "work-older"]
        );
        sort_observe_runs(&mut runs, ObserveSort::Oldest);
        assert_eq!(
            runs.iter()
                .map(|run| run.run_id.as_str())
                .collect::<Vec<_>>(),
            ["work-older", "work-mid", "work-newer"]
        );
    }

    #[test]
    fn observe_sort_keeps_missing_timestamps_honest_and_ties_on_run_id() {
        let mut runs = vec![
            ObserveRun {
                run_id: "b-same".into(),
                session_id: None,
                operator_session: None,
                transcript_path: None,
                agent: "codex".into(),
                skill: "review".into(),
                repo: "vibecrafted".into(),
                state: "unknown".into(),
                age: "—".into(),
                liveness: "unknown".into(),
                sort_ts: Some(5_000),
            },
            ObserveRun {
                run_id: "a-same".into(),
                session_id: None,
                operator_session: None,
                transcript_path: None,
                agent: "codex".into(),
                skill: "review".into(),
                repo: "vibecrafted".into(),
                state: "unknown".into(),
                age: "—".into(),
                liveness: "unknown".into(),
                sort_ts: Some(5_000),
            },
            ObserveRun {
                run_id: "missing".into(),
                session_id: None,
                operator_session: None,
                transcript_path: None,
                agent: "codex".into(),
                skill: "review".into(),
                repo: "vibecrafted".into(),
                state: "unknown".into(),
                age: "age unknown".into(),
                liveness: "unknown".into(),
                sort_ts: None,
            },
        ];
        sort_observe_runs(&mut runs, ObserveSort::Latest);
        assert_eq!(
            runs.iter()
                .map(|run| run.run_id.as_str())
                .collect::<Vec<_>>(),
            ["a-same", "b-same", "missing"]
        );
        assert!(parse_canonical_ts("").is_none());
        assert!(parse_canonical_ts("not-a-date").is_none());
        assert_eq!(
            parse_canonical_ts("2026-09-13T12:00:00Z"),
            Some(
                chrono::DateTime::parse_from_rfc3339("2026-09-13T12:00:00Z")
                    .unwrap()
                    .timestamp()
            )
        );
    }

    #[test]
    fn canonical_projection_keeps_every_actionable_interactive_face() {
        use crate::state::{ControlPlaneState, RunSnapshot};
        use std::collections::{HashMap, HashSet};

        let now = chrono::Utc::now().to_rfc3339();
        let providers = ["agy", "junie", "grok", "cursor", "codex", "claude"];
        let runs = providers
            .iter()
            .enumerate()
            .map(|(index, provider)| RunSnapshot {
                run_id: format!("live-{provider}"),
                session_id: Some(format!("provider-{index}")),
                agent: Some((*provider).to_string()),
                skill: Some("implement".to_string()),
                mode: Some("interactive".to_string()),
                state: Some("running".to_string()),
                status: None,
                started_at: Some(now.clone()),
                updated_at: Some(now.clone()),
                last_heartbeat: Some(now.clone()),
                root: Some(format!("/tmp/{provider}")),
                operator_session: Some(format!("frame-{index}")),
                latest_report: None,
                latest_transcript: Some(format!("/tmp/{provider}/transcript.log")),
                last_error: None,
                extra: HashMap::new(),
            })
            .collect::<Vec<_>>();
        let state = ControlPlaneState {
            root: "/tmp/control-plane".into(),
            retained_runs: runs.clone(),
            runs,
            events: Vec::new(),
            archived_run_ids: HashSet::new(),
        };

        let faces = project_control_plane(&state);
        assert_eq!(faces.len(), providers.len());
        for provider in providers {
            let face = faces
                .iter()
                .find(|face| face.agent == provider)
                .expect("provider face");
            assert!(face.switch_target().is_some());
        }
    }
}
