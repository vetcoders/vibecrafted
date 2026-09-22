//! Read-only analytical adapter for Google Antigravity IDE and CLI transcripts.
//!
//! Antigravity transcripts do not carry provider billing usage. Consequently,
//! this adapter exposes only a visibly estimated token count and API-equivalent
//! shadow price. Callers inject transcript paths, identities, time, and pricing;
//! this module never discovers or writes `~/.gemini` state.

use std::collections::{BTreeMap, HashSet};
use std::fs::File;
use std::io::{self, BufRead, BufReader};
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

const MAX_TRANSCRIPT_LINE_BYTES: usize = 8 * 1024 * 1024;
const COST_METHOD: &str = "estimated transcript characters / 4 tokens; 80% input and 20% output";

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "lowercase")]
pub enum AgySurface {
    Ide,
    Cli,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AgyTranscriptInput {
    pub path: PathBuf,
    pub surface: AgySurface,
    pub session_id: Option<String>,
    pub workspace: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct AgyPricing {
    pub models: BTreeMap<String, AgyModelRate>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct AgyModelRate {
    pub fresh_input_per_million: f64,
    pub output_per_million: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct AgyUsageReport {
    pub schema: String,
    pub generated_at: String,
    pub provider: String,
    pub sessions: Vec<AgySessionUsage>,
    pub diagnostics: AgyAdapterDiagnostics,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct AgySessionUsage {
    pub session_id: Option<String>,
    pub surface: AgySurface,
    pub workspace: Option<String>,
    pub model: Option<String>,
    pub steps: u64,
    pub user_turns: u64,
    pub planner_turns: u64,
    pub tool_calls: u64,
    pub tokens: AgyEstimatedTokens,
    pub cost: AgyEstimatedCost,
    pub signals: AgySignals,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct AgyEstimatedTokens {
    pub input: Option<u64>,
    pub output: Option<u64>,
    pub total: Option<u64>,
    pub source: String,
    pub method: String,
    pub evidence: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct AgyEstimatedCost {
    pub amount: Option<f64>,
    pub currency: String,
    pub unit: String,
    pub source: String,
    pub method: String,
    pub evidence: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct AgySignals {
    pub last_success_at: Option<String>,
    pub last_error_at: Option<String>,
    pub last_error: Option<String>,
    pub last_quota_at: Option<String>,
    pub last_quota_error: Option<String>,
    pub quota_reset_in: Option<String>,
    pub quota_status: String,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct AgyAdapterDiagnostics {
    pub files_read: usize,
    pub complete_lines: u64,
    pub malformed_lines: u64,
    pub oversized_lines: u64,
    pub partial_trailing_lines: u64,
    pub duplicate_records: u64,
    pub file_errors: Vec<AgyFileError>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct AgyFileError {
    pub path: String,
    pub error: String,
}

#[derive(Clone, Debug)]
struct SessionAggregate {
    session_id: Option<String>,
    surface: AgySurface,
    workspace: Option<String>,
    model: Option<String>,
    steps: u64,
    user_turns: u64,
    planner_turns: u64,
    tool_calls: u64,
    transcript_chars: u64,
    last_success: Option<DateTime<Utc>>,
    last_error: Option<(DateTime<Utc>, String)>,
    last_quota: Option<(DateTime<Utc>, String, Option<String>)>,
}

impl SessionAggregate {
    fn new(input: &AgyTranscriptInput) -> Self {
        Self {
            session_id: input
                .session_id
                .clone()
                .or_else(|| session_from_path(&input.path)),
            surface: input.surface.clone(),
            workspace: input.workspace.clone(),
            model: None,
            steps: 0,
            user_turns: 0,
            planner_turns: 0,
            tool_calls: 0,
            transcript_chars: 0,
            last_success: None,
            last_error: None,
            last_quota: None,
        }
    }
}

/// Analyze injected Antigravity IDE/CLI transcripts without touching provider state.
///
/// Complete JSONL records are deduplicated across inputs. An incomplete final
/// line is ignored because the provider may still be writing it. All prices are
/// caller supplied; an absent session/model/price remains explicitly unknown.
pub fn analyze_agy_transcripts(
    inputs: &[AgyTranscriptInput],
    pricing: &AgyPricing,
    generated_at: DateTime<Utc>,
) -> AgyUsageReport {
    let mut diagnostics = AgyAdapterDiagnostics::default();
    let mut seen = HashSet::new();
    let mut aggregates: BTreeMap<(AgySurface, Option<String>, String), SessionAggregate> =
        BTreeMap::new();

    for input in inputs {
        let session_id = input
            .session_id
            .clone()
            .or_else(|| session_from_path(&input.path));
        let identity = session_id
            .clone()
            .unwrap_or_else(|| format!("path:{}", input.path.display()));
        let key = (input.surface.clone(), session_id, identity);
        let aggregate = aggregates
            .entry(key)
            .or_insert_with(|| SessionAggregate::new(input));
        match read_transcript(input, aggregate, &mut diagnostics, &mut seen) {
            Ok(()) => diagnostics.files_read += 1,
            Err(error) => diagnostics.file_errors.push(AgyFileError {
                path: input.path.display().to_string(),
                error: error.to_string(),
            }),
        }
    }

    let mut sessions = aggregates
        .into_values()
        .filter(|aggregate| aggregate.steps > 0)
        .map(|aggregate| session_usage(aggregate, pricing))
        .collect::<Vec<_>>();
    sessions.sort_by(|left, right| {
        left.surface
            .cmp(&right.surface)
            .then_with(|| left.session_id.cmp(&right.session_id))
            .then_with(|| left.workspace.cmp(&right.workspace))
    });

    AgyUsageReport {
        schema: "vibecrafted.usage-provider.agy.v1".to_string(),
        generated_at: generated_at.to_rfc3339(),
        provider: "agy".to_string(),
        sessions,
        diagnostics,
    }
}

fn read_transcript(
    input: &AgyTranscriptInput,
    aggregate: &mut SessionAggregate,
    diagnostics: &mut AgyAdapterDiagnostics,
    seen: &mut HashSet<String>,
) -> io::Result<()> {
    let mut reader = BufReader::new(File::open(&input.path)?);
    loop {
        let mut line = Vec::new();
        let read = reader.read_until(b'\n', &mut line)?;
        if read == 0 {
            break;
        }
        if line.last() != Some(&b'\n') {
            diagnostics.partial_trailing_lines += 1;
            break;
        }
        diagnostics.complete_lines += 1;
        if line.len() > MAX_TRANSCRIPT_LINE_BYTES {
            diagnostics.oversized_lines += 1;
            continue;
        }
        while matches!(line.last(), Some(b'\n' | b'\r')) {
            line.pop();
        }
        let event: Value = match serde_json::from_slice(&line) {
            Ok(event) => event,
            Err(_) => {
                diagnostics.malformed_lines += 1;
                continue;
            }
        };
        let record_key = dedupe_key(&event, aggregate.session_id.as_deref(), &line);
        if !seen.insert(record_key) {
            diagnostics.duplicate_records += 1;
            continue;
        }
        apply_event(aggregate, &event, &line);
    }
    Ok(())
}

fn apply_event(aggregate: &mut SessionAggregate, event: &Value, line: &[u8]) {
    aggregate.steps = aggregate.steps.saturating_add(1);
    aggregate.transcript_chars = aggregate
        .transcript_chars
        .saturating_add(String::from_utf8_lossy(line).chars().count() as u64 + 1);

    let content = event.get("content").and_then(Value::as_str).unwrap_or("");
    if let Some(model) = model_selection(content) {
        aggregate.model = Some(normalize_agy_model(&model));
    }
    if aggregate.workspace.is_none() {
        aggregate.workspace = workspace_from_content(content);
    }

    let timestamp = event
        .get("created_at")
        .and_then(Value::as_str)
        .and_then(parse_timestamp);
    match event.get("type").and_then(Value::as_str) {
        Some("USER_INPUT") => aggregate.user_turns = aggregate.user_turns.saturating_add(1),
        Some("PLANNER_RESPONSE") => {
            aggregate.planner_turns = aggregate.planner_turns.saturating_add(1);
            aggregate.tool_calls = aggregate.tool_calls.saturating_add(
                event
                    .get("tool_calls")
                    .and_then(Value::as_array)
                    .map_or(0, |calls| calls.len() as u64),
            );
            update_latest_time(&mut aggregate.last_success, timestamp);
        }
        Some("ERROR_MESSAGE") => apply_error(aggregate, event, content, timestamp),
        _ if event.get("status").and_then(Value::as_str) == Some("ERROR") => {
            apply_error(aggregate, event, content, timestamp);
        }
        _ => {}
    }
}

fn apply_error(
    aggregate: &mut SessionAggregate,
    event: &Value,
    content: &str,
    timestamp: Option<DateTime<Utc>>,
) {
    let Some(timestamp) = timestamp else {
        return;
    };
    let message = event
        .get("error")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(content);
    update_latest_message(&mut aggregate.last_error, timestamp, message);
    let lower = message.to_ascii_lowercase();
    if message.contains("429") || message.contains("RESOURCE_EXHAUSTED") || lower.contains("quota")
    {
        let reset = quota_reset_in(message);
        if aggregate
            .last_quota
            .as_ref()
            .is_none_or(|(current, _, _)| timestamp > *current)
        {
            aggregate.last_quota = Some((timestamp, truncate(message, 200), reset));
        }
    }
}

fn session_usage(aggregate: SessionAggregate, pricing: &AgyPricing) -> AgySessionUsage {
    let tokens = estimated_tokens(aggregate.steps, aggregate.transcript_chars);
    let cost = estimated_cost(aggregate.model.as_deref(), &tokens, pricing);
    let quota_active = aggregate.last_quota.as_ref().is_some_and(|(quota, _, _)| {
        aggregate
            .last_success
            .is_none_or(|success| *quota > success)
    });
    let signals = AgySignals {
        last_success_at: aggregate.last_success.map(|value| value.to_rfc3339()),
        last_error_at: aggregate
            .last_error
            .as_ref()
            .map(|(value, _)| value.to_rfc3339()),
        last_error: aggregate.last_error.map(|(_, value)| value),
        last_quota_at: aggregate
            .last_quota
            .as_ref()
            .map(|(value, _, _)| value.to_rfc3339()),
        last_quota_error: aggregate
            .last_quota
            .as_ref()
            .map(|(_, value, _)| value.clone()),
        quota_reset_in: aggregate.last_quota.and_then(|(_, _, reset)| reset),
        quota_status: if quota_active { "exhausted" } else { "ok" }.to_string(),
    };
    AgySessionUsage {
        session_id: aggregate.session_id,
        surface: aggregate.surface,
        workspace: aggregate.workspace,
        model: aggregate.model,
        steps: aggregate.steps,
        user_turns: aggregate.user_turns,
        planner_turns: aggregate.planner_turns,
        tool_calls: aggregate.tool_calls,
        tokens,
        cost,
        signals,
    }
}

fn estimated_tokens(steps: u64, transcript_chars: u64) -> AgyEstimatedTokens {
    if steps == 0 {
        return AgyEstimatedTokens {
            input: None,
            output: None,
            total: None,
            source: "unknown".to_string(),
            method: COST_METHOD.to_string(),
            evidence: Vec::new(),
            reason: Some("no complete transcript records".to_string()),
        };
    }
    let total = (transcript_chars / 4).max(1);
    let input = total.saturating_mul(80) / 100;
    let output = total.saturating_sub(input);
    AgyEstimatedTokens {
        input: Some(input),
        output: Some(output),
        total: Some(total),
        source: "estimated".to_string(),
        method: COST_METHOD.to_string(),
        evidence: vec![format!("{transcript_chars} complete transcript characters")],
        reason: None,
    }
}

fn estimated_cost(
    model: Option<&str>,
    tokens: &AgyEstimatedTokens,
    pricing: &AgyPricing,
) -> AgyEstimatedCost {
    let evidence = tokens.evidence.clone();
    let unknown = |reason: &str| AgyEstimatedCost {
        amount: None,
        currency: "USD".to_string(),
        unit: "api-equiv".to_string(),
        source: "unknown".to_string(),
        method: COST_METHOD.to_string(),
        evidence: evidence.clone(),
        reason: Some(reason.to_string()),
    };
    let Some(model) = model else {
        return unknown("model not recorded");
    };
    let Some(rate) = pricing.models.get(model) else {
        return unknown("no injected price for normalized model");
    };
    let (Some(input), Some(output)) = (tokens.input, tokens.output) else {
        return unknown("token estimate unavailable");
    };
    let amount = (input as f64 * rate.fresh_input_per_million
        + output as f64 * rate.output_per_million)
        / 1_000_000.0;
    AgyEstimatedCost {
        amount: Some(round_six(amount)),
        currency: "USD".to_string(),
        unit: "api-equiv".to_string(),
        source: "estimated".to_string(),
        method: COST_METHOD.to_string(),
        evidence,
        reason: None,
    }
}

/// Normalize display names used by Antigravity's model-selection events.
pub fn normalize_agy_model(raw: &str) -> String {
    let clean = raw.trim().to_ascii_lowercase().replace(' ', "-");
    let version = clean.replace('_', "-");
    if version.contains("flash") && (version.contains("3.8") || version.contains("3-8")) {
        return format!(
            "gemini-3.8-flash-{}",
            if version.contains("medium") {
                "medium"
            } else if version.contains("low") {
                "low"
            } else {
                "high"
            }
        );
    }
    if version.contains("flash") && (version.contains("3.7") || version.contains("3-7")) {
        return "gemini-3.7-flash-high".to_string();
    }
    if version.contains("flash") && (version.contains("3.6") || version.contains("3-6")) {
        return "gemini-3.6-flash-high".to_string();
    }
    if version.contains("pro") && (version.contains("3.1") || version.contains("3-1")) {
        return format!(
            "gemini-3.1-pro-{}",
            if version.contains("low") {
                "low"
            } else {
                "high"
            }
        );
    }
    if version.contains("sonnet") {
        return "claude-sonnet-4-6".to_string();
    }
    if version.contains("opus") {
        return "claude-opus-4-6-thinking".to_string();
    }
    if version.contains("gpt") {
        return "gpt-oss-120b-medium".to_string();
    }
    version
}

fn model_selection(content: &str) -> Option<String> {
    let marker = "Model Selection`";
    let rest = content.split_once(marker)?.1;
    let selected = rest.split_once(" to ")?.1;
    let end = selected
        .find(". No need")
        .or_else(|| selected.find(".<"))
        .or_else(|| selected.find('\n'))
        .unwrap_or(selected.len());
    let value = selected[..end].trim().trim_end_matches('.').trim();
    (!value.is_empty()).then(|| value.to_string())
}

fn workspace_from_content(content: &str) -> Option<String> {
    let block = content.split_once("<user_information>")?.1;
    let candidate = block.split_once(" ->")?.0.lines().last()?.trim();
    (candidate.starts_with('/')).then(|| candidate.to_string())
}

fn session_from_path(path: &Path) -> Option<String> {
    let logs = path.parent()?;
    let generated = logs.parent()?;
    let session = generated.parent()?;
    if generated.file_name()?.to_str()? != ".system_generated" {
        return None;
    }
    Some(session.file_name()?.to_string_lossy().into_owned())
}

fn dedupe_key(event: &Value, session_id: Option<&str>, raw: &[u8]) -> String {
    for key in ["id", "event_id", "request_id", "turn_id"] {
        if let Some(value) = event
            .get(key)
            .and_then(Value::as_str)
            .filter(|v| !v.is_empty())
        {
            return format!("{}:{key}:{value}", session_id.unwrap_or("unknown"));
        }
    }
    let mut digest = Sha256::new();
    digest.update(session_id.unwrap_or("unknown").as_bytes());
    digest.update([0]);
    digest.update(raw);
    format!("sha256:{:x}", digest.finalize())
}

fn parse_timestamp(value: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(value)
        .ok()
        .map(|value| value.with_timezone(&Utc))
}

fn update_latest_time(current: &mut Option<DateTime<Utc>>, candidate: Option<DateTime<Utc>>) {
    if let Some(candidate) = candidate
        && current.is_none_or(|value| candidate > value)
    {
        *current = Some(candidate);
    }
}

fn update_latest_message(
    current: &mut Option<(DateTime<Utc>, String)>,
    candidate: DateTime<Utc>,
    message: &str,
) {
    if current
        .as_ref()
        .is_none_or(|(timestamp, _)| candidate > *timestamp)
    {
        *current = Some((candidate, truncate(message, 200)));
    }
}

fn quota_reset_in(message: &str) -> Option<String> {
    let rest = message.split_once("Resets in ")?.1;
    let value = rest.split(['.', '\n']).next()?.trim();
    (!value.is_empty()).then(|| value.to_string())
}

fn truncate(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}

fn round_six(value: f64) -> f64 {
    (value * 1_000_000.0).round() / 1_000_000.0
}
