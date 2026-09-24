//! Live agent-quota board for the usage page.
//!
//! Reads the files `agy-monitor` and `kimi-monitor` already write. No daemon,
//! no second accounting engine, no network. A missing file is an absent card,
//! not an error — the operator may not run that agent.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::Json;
use axum::http::{HeaderValue, header};
use axum::response::{IntoResponse, Response};
use chrono::Utc;
use serde::Serialize;
use serde_json::Value;

const SCHEMA: &str = "vibecrafted.quota-dashboard.v1";
const STALE_SECS: u64 = 300;
const NEAR_LIMIT: f64 = 0.85;
const BLOCKED_LIMIT: f64 = 0.95;
const MAX_BYTES: u64 = 256 * 1024;
const AGY_ENV: &str = "VIBECRAFTED_AGY_QUOTA_JSON";
const KIMI_ENV: &str = "VIBECRAFTED_KIMI_QUOTA_JSON";

#[derive(Debug, Serialize)]
struct QuotaDashboard {
    schema: &'static str,
    generated_at: String,
    agents: Vec<QuotaAgent>,
}

#[derive(Debug, Serialize)]
struct QuotaAgent {
    id: &'static str,
    name: &'static str,
    present: bool,
    status: &'static str,
    headline: String,
    detail: String,
    stale: bool,
    age_s: Option<u64>,
    model: Option<String>,
    tokens: Option<u64>,
    cost_usd: Option<f64>,
    bars: Vec<QuotaBar>,
    source: String,
}

#[derive(Debug, Serialize)]
struct QuotaBar {
    id: &'static str,
    label: &'static str,
    ratio: Option<f64>,
    text: String,
    level: &'static str,
}

pub async fn quota() -> Response {
    let body = QuotaDashboard {
        schema: SCHEMA,
        generated_at: Utc::now().to_rfc3339(),
        agents: vec![read_agy(), read_kimi()],
    };
    no_store(Json(body).into_response())
}

fn no_store(mut response: Response) -> Response {
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

fn read_agy() -> QuotaAgent {
    project(
        "agy",
        "Antigravity",
        AGY_ENV,
        default_quota_path(".gemini/agy-monitor/runtime/quota.json"),
        "~/.gemini/agy-monitor/runtime/quota.json",
        parse_agy,
    )
}

fn read_kimi() -> QuotaAgent {
    project(
        "kimi",
        "Kimi",
        KIMI_ENV,
        default_quota_path(".kimi-code/runtime/quota.json"),
        "~/.kimi-code/runtime/quota.json",
        parse_kimi,
    )
}

fn project(
    id: &'static str,
    name: &'static str,
    env_name: &str,
    default_path: PathBuf,
    default_label: &str,
    parse: fn(&Value) -> QuotaFields,
) -> QuotaAgent {
    match load_source(env_name, &default_path, default_label) {
        Source::Missing { label } => absent(id, name, label),
        Source::Unreadable { label, error } => QuotaAgent {
            id,
            name,
            present: true,
            status: "unknown",
            headline: "unreadable".to_string(),
            detail: error,
            stale: false,
            age_s: None,
            model: None,
            tokens: None,
            cost_usd: None,
            bars: Vec::new(),
            source: label,
        },
        Source::Json { label, value } => {
            let mut fields = parse(&value);
            let age_s = json_ts(&value).map(age_secs);
            let stale = age_s.is_some_and(|age| age > STALE_SECS);
            if stale && fields.status != "blocked" {
                fields.status = worst(fields.status, "warn");
                if !fields.detail.is_empty() {
                    fields.detail.push_str(" · stale");
                }
            }
            QuotaAgent {
                id,
                name,
                present: true,
                status: fields.status,
                headline: fields.headline,
                detail: fields.detail,
                stale,
                age_s,
                model: fields.model,
                tokens: fields.tokens,
                cost_usd: fields.cost_usd,
                bars: fields.bars,
                source: label,
            }
        }
    }
}

struct QuotaFields {
    status: &'static str,
    headline: String,
    detail: String,
    model: Option<String>,
    tokens: Option<u64>,
    cost_usd: Option<f64>,
    bars: Vec<QuotaBar>,
}

enum Source {
    Missing { label: String },
    Unreadable { label: String, error: String },
    Json { label: String, value: Value },
}

fn load_source(env_name: &str, default_path: &Path, default_label: &str) -> Source {
    match std::env::var(env_name) {
        Ok(raw) if raw.trim_start().starts_with('{') => parse_inline(env_name, raw),
        Ok(path) if !path.trim().is_empty() => read_file(Path::new(path.trim()), path.trim()),
        Ok(_) => Source::Missing {
            label: default_label.to_string(),
        },
        Err(_) => read_file(default_path, default_label),
    }
}

fn parse_inline(env_name: &str, raw: String) -> Source {
    let label = env_name.to_string();
    match serde_json::from_str::<Value>(&raw) {
        Ok(value) if value.is_object() => Source::Json { label, value },
        Ok(_) => Source::Unreadable {
            label,
            error: "quota JSON must be an object".to_string(),
        },
        Err(err) => Source::Unreadable {
            label,
            error: format!("invalid quota JSON: {err}"),
        },
    }
}

fn read_file(path: &Path, label: &str) -> Source {
    let label = label.to_string();
    match fs::symlink_metadata(path) {
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Source::Missing { label },
        Err(err) => Source::Unreadable {
            label,
            error: err.to_string(),
        },
        Ok(meta) if meta.file_type().is_symlink() => Source::Unreadable {
            label,
            error: "quota path is a symlink".to_string(),
        },
        Ok(meta) if meta.len() > MAX_BYTES => Source::Unreadable {
            label,
            error: "quota file is too large".to_string(),
        },
        Ok(_) => match fs::read_to_string(path) {
            Ok(raw) => parse_inline(&label, raw),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => Source::Missing { label },
            Err(err) => Source::Unreadable {
                label,
                error: err.to_string(),
            },
        },
    }
}

fn absent(id: &'static str, name: &'static str, source: String) -> QuotaAgent {
    QuotaAgent {
        id,
        name,
        present: false,
        status: "absent",
        headline: "quiet".to_string(),
        detail: "no quota.json — monitor not running".to_string(),
        stale: false,
        age_s: None,
        model: None,
        tokens: None,
        cost_usd: None,
        bars: Vec::new(),
        source,
    }
}

fn parse_agy(value: &Value) -> QuotaFields {
    let quota_status = value
        .pointer("/quota/status")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_ascii_uppercase();
    let model = text_field(value, "model_id");
    let tokens = value
        .pointer("/metrics/estimated_tokens")
        .and_then(Value::as_f64)
        .filter(|n| n.is_finite() && *n >= 0.0)
        .map(|n| n as u64);
    let cost_usd = value
        .pointer("/metrics/cost_usd")
        .and_then(Value::as_f64)
        .filter(|n| n.is_finite() && *n >= 0.0);
    let (status, headline) = match quota_status.as_str() {
        "RESOURCE_EXHAUSTED" => ("blocked", "EXHAUSTED".to_string()),
        "OK" => ("ok", "OK".to_string()),
        "" => ("unknown", "no status".to_string()),
        other => ("unknown", other.to_string()),
    };
    let mut parts = Vec::new();
    if let Some(tokens) = tokens {
        parts.push(format!("{} tok", format_tokens(tokens)));
    }
    if let Some(model) = &model {
        parts.push(model.clone());
    }
    if let Some(cost) = cost_usd {
        parts.push(format!("≈${cost:.3} api-equiv"));
    }
    let reset = value
        .pointer("/quota/quota_reset_in")
        .or_else(|| value.pointer("/quota/reset_in"))
        .and_then(Value::as_str)
        .filter(|text| !text.is_empty());
    if status == "blocked" {
        if let Some(reset) = reset {
            parts.push(format!("reset {reset}"));
        }
    }
    QuotaFields {
        status,
        headline,
        detail: if parts.is_empty() {
            "snapshot has no session metrics".to_string()
        } else {
            parts.join(" · ")
        },
        model,
        tokens,
        cost_usd,
        bars: Vec::new(),
    }
}

fn parse_kimi(value: &Value) -> QuotaFields {
    let kind = value
        .get("kind")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_ascii_lowercase();
    let bars = ["limit5h", "monthTotal", "monthCode"]
        .into_iter()
        .filter_map(|key| ratio_bar(value, key))
        .collect::<Vec<_>>();
    let hottest = bars
        .iter()
        .filter_map(|bar| bar.ratio)
        .fold(0.0_f64, f64::max);
    if kind != "ok" {
        let message = value
            .pointer("/error/message")
            .and_then(Value::as_str)
            .or_else(|| value.get("error").and_then(Value::as_str))
            .or_else(|| value.get("last_poll_error").and_then(Value::as_str))
            .unwrap_or("quota snapshot error");
        return QuotaFields {
            status: "warn",
            headline: "error".to_string(),
            detail: truncate(message),
            model: text_field(value, "plan"),
            tokens: None,
            cost_usd: None,
            bars,
        };
    }
    let status = ratio_level(hottest);
    let headline = bars
        .first()
        .map(|bar| bar.text.clone())
        .unwrap_or_else(|| "—".to_string());
    let detail = if bars.is_empty() {
        "snapshot has no usage windows".to_string()
    } else {
        bars.iter()
            .map(|bar| format!("{} {}", bar.label, bar.text))
            .collect::<Vec<_>>()
            .join(" · ")
    };
    let mut detail = detail;
    if let Some(err) = value
        .get("last_poll_error")
        .and_then(Value::as_str)
        .filter(|err| !err.is_empty())
    {
        detail.push_str(" · poll ");
        detail.push_str(&truncate(err));
    }
    QuotaFields {
        status,
        headline,
        detail,
        model: text_field(value, "plan"),
        tokens: None,
        cost_usd: None,
        bars,
    }
}

fn ratio_bar(value: &Value, key: &'static str) -> Option<QuotaBar> {
    let entry = value.get(key)?;
    if !entry.is_object() {
        return None;
    }
    let ratio = entry
        .get("usedRatio")
        .and_then(Value::as_f64)
        .filter(|n| n.is_finite() && *n >= 0.0);
    let text = match ratio {
        Some(ratio) => format!("{:.0}%", ratio.clamp(0.0, 1.0) * 100.0),
        None => "—".to_string(),
    };
    let label = match key {
        "limit5h" => "5h",
        "monthTotal" => "month",
        "monthCode" => "code",
        _ => key,
    };
    Some(QuotaBar {
        id: key,
        label,
        ratio,
        level: ratio.map(ratio_level).unwrap_or("unknown"),
        text,
    })
}

fn ratio_level(ratio: f64) -> &'static str {
    if ratio >= BLOCKED_LIMIT {
        "blocked"
    } else if ratio >= NEAR_LIMIT {
        "warn"
    } else {
        "ok"
    }
}

fn worst<'a>(left: &'a str, right: &'a str) -> &'a str {
    if rank(right) > rank(left) {
        right
    } else {
        left
    }
}

fn rank(status: &str) -> u8 {
    match status {
        "ok" => 0,
        "absent" | "unknown" => 1,
        "warn" => 2,
        "blocked" => 3,
        _ => 1,
    }
}

fn text_field(value: &Value, key: &str) -> Option<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .map(truncate)
}

fn json_ts(value: &Value) -> Option<u64> {
    value.get("ts").and_then(Value::as_u64)
}

fn age_secs(ts: u64) -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0)
        .saturating_sub(ts)
}

fn format_tokens(n: u64) -> String {
    if n >= 1_000_000 {
        format!("{:.1}M", n as f64 / 1_000_000.0)
    } else if n >= 1000 {
        format!("{:.0}k", n as f64 / 1000.0)
    } else {
        n.to_string()
    }
}

fn truncate(text: &str) -> String {
    let mut out = text.chars().take(180).collect::<String>();
    if text.chars().count() > 180 {
        out.push('…');
    }
    out
}

fn default_quota_path(relative: &str) -> PathBuf {
    std::env::var_os("HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/nonexistent"))
        .join(relative)
}
