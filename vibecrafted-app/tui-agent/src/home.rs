//! Shared Home / Dashboard projection for the operator console.
//!
//! Home is the first surface: attention, then work in progress, then history.
//! It reads the existing control-plane snapshots. It does not own a second
//! register of truth, and selecting an agent never launches a process.

use crate::state::{ControlPlaneState, RenderedRun, RunKind, RunSnapshot, workspace_matches};
use serde_json::Value;
use std::path::Path;

/// Workspace filter for Home counters and rows.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum HomeScope {
    #[default]
    Global,
    Local,
}

impl HomeScope {
    pub fn label(self) -> &'static str {
        match self {
            Self::Global => "Global",
            Self::Local => "Local",
        }
    }

    pub fn next(self) -> Self {
        match self {
            Self::Global => Self::Local,
            Self::Local => Self::Global,
        }
    }
}

/// Landing board versus an existing conversation already on the canvas.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum HomeSurface {
    #[default]
    Landing,
    Conversation,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum HomeBand {
    Attention,
    Work,
    History,
}

impl HomeBand {
    pub fn title(self) -> &'static str {
        match self {
            Self::Attention => "Needs attention",
            Self::Work => "In progress",
            Self::History => "History",
        }
    }

    pub fn marker(self) -> &'static str {
        match self {
            Self::Attention => "!",
            Self::Work => "●",
            Self::History => "○",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HomeRow {
    pub run_id: String,
    pub agent: String,
    pub title: String,
    pub band: HomeBand,
    pub attention_reason: Option<String>,
    pub workspace: String,
    pub panel: Option<String>,
    /// Honest cost cell. Missing or null data is "—", never an invented zero.
    pub cost_label: String,
    pub state_label: String,
}

impl HomeRow {
    pub fn list_line(&self, width: usize) -> String {
        let reason = self
            .attention_reason
            .as_deref()
            .unwrap_or(self.state_label.as_str());
        let panel = self
            .panel
            .as_deref()
            .map(|value| format!("panel:{value}"))
            .unwrap_or_else(|| "no panel".to_string());
        let line = format!(
            "{} {:<8} {:<18} {:<10} {panel}  cost {}",
            self.band.marker(),
            truncate(&self.agent, 8),
            truncate(reason, 22),
            truncate(&self.workspace, 10),
            self.cost_label
        );
        if width == 0 || line.chars().count() <= width {
            return line;
        }
        truncate(&line, width)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HomeNavigation {
    ToPanel { run_id: String, panel: String },
    MissingTarget { run_id: String, reason: String },
}

impl HomeNavigation {
    pub fn status_line(&self) -> String {
        match self {
            Self::ToPanel { panel, .. } => {
                format!("navigate existing panel {panel} · no launch")
            }
            Self::MissingTarget { reason, .. } => reason.clone(),
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct HomeState {
    pub surface: HomeSurface,
    pub scope: HomeScope,
    pub selected: usize,
    pub navigations: Vec<HomeNavigation>,
    pub conversation_run_id: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct HomeCounts {
    pub attention: usize,
    pub work: usize,
    pub history: usize,
}

impl HomeCounts {
    pub fn from_rows(rows: &[HomeRow]) -> Self {
        Self {
            attention: rows
                .iter()
                .filter(|row| row.band == HomeBand::Attention)
                .count(),
            work: rows.iter().filter(|row| row.band == HomeBand::Work).count(),
            history: rows
                .iter()
                .filter(|row| row.band == HomeBand::History)
                .count(),
        }
    }
}

pub fn project_home(state: &ControlPlaneState, scope: HomeScope, workspace: &Path) -> Vec<HomeRow> {
    let now = chrono::Utc::now();
    let mut rows = state
        .runs
        .iter()
        .cloned()
        .map(|snapshot| {
            let kind = crate::state::classify_run(&snapshot, now);
            project_row(snapshot, kind)
        })
        .collect::<Vec<_>>();
    if matches!(scope, HomeScope::Local) {
        rows.retain(|row| local_workspace_matches(state, row, workspace));
    }
    rows.sort_by(|left, right| {
        left.band
            .cmp(&right.band)
            .then_with(|| left.agent.cmp(&right.agent))
            .then_with(|| left.run_id.cmp(&right.run_id))
    });
    rows
}

pub fn project_rendered(runs: &[RenderedRun], scope: HomeScope, workspace: &Path) -> Vec<HomeRow> {
    let mut rows = runs
        .iter()
        .map(|run| project_row(run.snapshot.clone(), run.kind))
        .collect::<Vec<_>>();
    if matches!(scope, HomeScope::Local) {
        rows.retain(|row| {
            runs.iter().any(|run| {
                run.snapshot.run_id == row.run_id && workspace_matches(&run.snapshot, workspace)
            })
        });
    }
    rows.sort_by(|left, right| {
        left.band
            .cmp(&right.band)
            .then_with(|| left.agent.cmp(&right.agent))
            .then_with(|| left.run_id.cmp(&right.run_id))
    });
    rows
}

fn local_workspace_matches(state: &ControlPlaneState, row: &HomeRow, workspace: &Path) -> bool {
    state
        .runs
        .iter()
        .find(|snapshot| snapshot.run_id == row.run_id)
        .is_some_and(|snapshot| workspace_matches(snapshot, workspace))
}

fn project_row(snapshot: RunSnapshot, kind: RunKind) -> HomeRow {
    let attention_reason = attention_reason(&snapshot, kind);
    let band = if attention_reason.is_some() {
        HomeBand::Attention
    } else if matches!(kind, RunKind::Active | RunKind::Paused | RunKind::Stalled) {
        HomeBand::Work
    } else {
        HomeBand::History
    };
    let agent = display_token(snapshot.agent.as_deref());
    let skill = display_token(snapshot.skill.as_deref());
    let workspace = crate::state::workspace_label(snapshot.root.as_deref());
    let panel = panel_destination(&snapshot);
    let cost = cost_label(&snapshot);
    let state_label = snapshot.display_state();
    HomeRow {
        run_id: snapshot.run_id,
        title: format!("{skill} · {agent}"),
        agent,
        band,
        attention_reason,
        workspace,
        panel,
        cost_label: cost,
        state_label,
    }
}

fn attention_reason(snapshot: &RunSnapshot, kind: RunKind) -> Option<String> {
    if let Some(reason) = snapshot
        .extra
        .get("attention_reason")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        return Some(reason.to_string());
    }
    if snapshot
        .extra
        .get("needs_attention")
        .and_then(Value::as_bool)
        == Some(true)
    {
        return Some("needs attention".to_string());
    }
    let state = snapshot.display_state().to_ascii_lowercase();
    if matches!(
        state.as_str(),
        "waiting" | "needs_input" | "question" | "blocked"
    ) {
        return Some(snapshot.display_state());
    }
    if kind == RunKind::Unknown || state == "unknown" {
        return Some("unknown".to_string());
    }
    if kind == RunKind::Stalled {
        return Some("stalled".to_string());
    }
    if snapshot
        .last_error
        .as_deref()
        .is_some_and(|value| !value.trim().is_empty())
    {
        return Some("error".to_string());
    }
    None
}

fn panel_destination(snapshot: &RunSnapshot) -> Option<String> {
    for key in ["panel", "panel_id", "panel_destination", "destination"] {
        if let Some(value) = snapshot
            .extra
            .get(key)
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty() && *value != "null")
        {
            return Some(value.to_string());
        }
    }
    snapshot
        .operator_session
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
}

/// Missing cost is an honest dash. A present numeric zero is real data and
/// may render as "0"; Home never invents that zero from an absent field.
pub fn cost_label(snapshot: &RunSnapshot) -> String {
    for key in ["cost", "usage_cost", "session_cost"] {
        match snapshot.extra.get(key) {
            None => continue,
            Some(Value::Null) => return "—".to_string(),
            Some(Value::String(raw)) => {
                let trimmed = raw.trim();
                if trimmed.is_empty()
                    || trimmed.eq_ignore_ascii_case("unknown")
                    || trimmed.eq_ignore_ascii_case("null")
                {
                    return "—".to_string();
                }
                return trimmed.to_string();
            }
            Some(Value::Number(number)) => return number.to_string(),
            Some(_) => return "—".to_string(),
        }
    }
    "—".to_string()
}

pub fn wrap_transcript_words(value: &str, width: usize) -> Vec<String> {
    if width == 0 {
        return vec![value.to_string()];
    }
    let mut lines = Vec::new();
    for raw in value.split_inclusive('\n') {
        let (line, had_nl) = raw
            .strip_suffix('\n')
            .map(|stripped| (stripped, true))
            .unwrap_or((raw, false));
        if line.is_empty() {
            lines.push(String::new());
            continue;
        }
        let mut current = String::new();
        for word in line.split(' ') {
            if word.chars().count() > width {
                if !current.is_empty() {
                    lines.push(std::mem::take(&mut current));
                }
                let mut chunk = String::new();
                for ch in word.chars() {
                    chunk.push(ch);
                    if chunk.chars().count() == width {
                        lines.push(std::mem::take(&mut chunk));
                    }
                }
                current = chunk;
                continue;
            }
            let extra = if current.is_empty() {
                word.chars().count()
            } else {
                word.chars().count() + 1
            };
            if current.chars().count() + extra > width && !current.is_empty() {
                lines.push(std::mem::take(&mut current));
            }
            if !current.is_empty() {
                current.push(' ');
            }
            current.push_str(word);
        }
        if !current.is_empty() || had_nl {
            lines.push(current);
        }
    }
    if lines.is_empty() {
        lines.push(String::new());
    }
    lines
}

fn display_token(value: Option<&str>) -> String {
    match value.map(str::trim) {
        Some(value) if !value.is_empty() && value != "unknown" && value != "None" => {
            value.to_string()
        }
        _ => "—".to_string(),
    }
}

fn truncate(value: &str, width: usize) -> String {
    if width == 0 {
        return String::new();
    }
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
    use crate::state::RunSnapshot;
    use std::collections::{HashMap, HashSet};
    use std::path::PathBuf;

    fn snapshot(
        run_id: &str,
        agent: &str,
        state: &str,
        root: &str,
        panel: Option<&str>,
        extra: &[(&str, Value)],
    ) -> RunSnapshot {
        let now = chrono::Utc::now().to_rfc3339();
        let mut map = HashMap::new();
        if let Some(panel) = panel {
            map.insert("panel".into(), Value::String(panel.into()));
        }
        for (key, value) in extra {
            map.insert((*key).into(), value.clone());
        }
        RunSnapshot {
            run_id: run_id.into(),
            session_id: None,
            agent: Some(agent.into()),
            skill: Some("implement".into()),
            mode: None,
            state: Some(state.into()),
            status: None,
            started_at: Some(now.clone()),
            updated_at: Some(now.clone()),
            last_heartbeat: Some(now),
            root: Some(root.into()),
            operator_session: panel.map(ToOwned::to_owned),
            latest_report: None,
            latest_transcript: Some(format!("{root}/transcript.log")),
            last_error: None,
            extra: map,
        }
    }

    fn plane(runs: Vec<RunSnapshot>) -> ControlPlaneState {
        ControlPlaneState {
            root: PathBuf::from("/tmp/control_plane"),
            retained_runs: runs.clone(),
            runs,
            events: Vec::new(),
            archived_run_ids: HashSet::new(),
            usage: Default::default(),
        }
    }

    #[test]
    fn home_orders_attention_then_work_then_history_and_keeps_them_apart() {
        let state = plane(vec![
            snapshot(
                "hist-1",
                "cursor",
                "completed",
                "/tmp/ws-alpha",
                Some("pane-old"),
                &[("exit_code", Value::from(0))],
            ),
            snapshot(
                "work-1",
                "claude",
                "running",
                "/tmp/ws-alpha",
                Some("pane-2"),
                &[],
            ),
            snapshot(
                "ask-1",
                "kimi",
                "waiting",
                "/tmp/ws-alpha",
                Some("pane-1"),
                &[(
                    "attention_reason",
                    Value::String("waiting on operator".into()),
                )],
            ),
        ]);
        let rows = project_home(&state, HomeScope::Global, Path::new("/tmp/ws-alpha"));
        assert_eq!(
            rows.iter().map(|row| row.band).collect::<Vec<_>>(),
            [HomeBand::Attention, HomeBand::Work, HomeBand::History]
        );
        assert_eq!(
            rows[0].attention_reason.as_deref(),
            Some("waiting on operator")
        );
        assert_eq!(rows[1].agent, "claude");
        assert_eq!(rows[2].band, HomeBand::History);
    }

    #[test]
    fn local_scope_hides_the_foreign_workspace_and_missing_cost_is_not_zero() {
        let mut foreign = snapshot(
            "ask-beta",
            "grok",
            "unknown",
            "/tmp/ws-beta",
            None,
            &[("needs_attention", Value::Bool(true))],
        );
        foreign.operator_session = None;
        let state = plane(vec![
            snapshot(
                "work-1",
                "claude",
                "running",
                "/tmp/ws-alpha",
                Some("pane-2"),
                &[],
            ),
            foreign,
        ]);
        let global = project_home(&state, HomeScope::Global, Path::new("/tmp/ws-alpha"));
        let local = project_home(&state, HomeScope::Local, Path::new("/tmp/ws-alpha"));
        assert_eq!(global.len(), 2);
        assert_eq!(local.len(), 1);
        assert_eq!(local[0].run_id, "work-1");
        assert_eq!(
            global
                .iter()
                .find(|row| row.run_id == "work-1")
                .unwrap()
                .cost_label,
            "—"
        );
        assert!(!global.iter().any(|row| row.cost_label == "0"));
    }

    #[test]
    fn present_cost_zero_is_kept_but_absent_cost_stays_a_dash() {
        let priced = snapshot(
            "priced",
            "claude",
            "running",
            "/tmp/ws-alpha",
            Some("pane-2"),
            &[("cost", Value::from(0))],
        );
        let bare = snapshot(
            "bare",
            "kimi",
            "running",
            "/tmp/ws-alpha",
            Some("pane-1"),
            &[],
        );
        assert_eq!(cost_label(&priced), "0");
        assert_eq!(cost_label(&bare), "—");
    }

    #[test]
    fn wrap_keeps_words_intact_until_a_word_exceeds_the_width() {
        let lines = wrap_transcript_words("hello beautiful world", 10);
        assert_eq!(lines, ["hello", "beautiful", "world"]);
        let long = wrap_transcript_words("supercalifragilistic", 8);
        assert!(long.iter().all(|line| line.chars().count() <= 8));
        assert_eq!(long.join(""), "supercalifragilistic");
    }

    #[test]
    fn navigation_status_never_mentions_launch_on_a_known_panel() {
        let nav = HomeNavigation::ToPanel {
            run_id: "work-1".into(),
            panel: "pane-2".into(),
        };
        assert!(nav.status_line().contains("navigate existing panel pane-2"));
        assert!(nav.status_line().contains("no launch"));
        let missing = HomeNavigation::MissingTarget {
            run_id: "ask-beta".into(),
            reason: "no existing panel for ask-beta · not launching".into(),
        };
        assert!(missing.status_line().contains("not launching"));
    }
}
