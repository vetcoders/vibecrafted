//! Live agent-quota board for the usage page.
//!
//! Reads the files `agy-monitor` and `kimi-monitor` already write. No daemon,
//! no second accounting engine, no network. A missing file is an absent card,
//! not an error — the operator may not run that agent.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::Json;
use axum::extract::Query;
use axum::http::{HeaderValue, header};
use axum::response::{IntoResponse, Response};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use serde_json::Value;

const SCHEMA: &str = "vibecrafted.quota-dashboard.v1";
const STALE_SECS: u64 = 300;
const NEAR_LIMIT: f64 = 0.85;
const BLOCKED_LIMIT: f64 = 1.0;
const MAX_BYTES: u64 = 256 * 1024;
const AGY_ENV: &str = "VIBECRAFTED_AGY_QUOTA_JSON";
const KIMI_ENV: &str = "VIBECRAFTED_KIMI_QUOTA_JSON";

#[derive(Debug, Deserialize, Default)]
pub struct QuotaQuery {
    pub agent: Option<String>,
    pub provider: Option<String>,
    pub pool: Option<String>,
    pub model: Option<String>,
    pub account: Option<String>,
}

#[derive(Debug, Serialize)]
struct QuotaDashboard {
    schema: &'static str,
    generated_at: String,
    agents: Vec<QuotaAgent>,
}

#[derive(Debug, Clone, Serialize)]
pub struct QuotaAgent {
    pub id: &'static str,
    pub agent: &'static str,
    pub name: &'static str,
    pub present: bool,
    pub status: &'static str,
    pub headline: String,
    pub detail: String,
    pub stale: bool,
    pub age_s: Option<u64>,
    pub model: Option<String>,
    pub tokens: Option<u64>,
    pub cost_usd: Option<f64>,
    pub bars: Vec<QuotaBar>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fraction: Option<f64>,
    pub source: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub account: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pool: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provenance: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct QuotaBar {
    pub id: &'static str,
    pub label: &'static str,
    pub ratio: Option<f64>,
    pub text: String,
    pub level: &'static str,
}

pub async fn quota(Query(query): Query<QuotaQuery>) -> Response {
    let mut agents = vec![
        read_agy(),
        read_fleet_agent("claude", "Claude"),
        read_fleet_agent("codex", "Codex"),
        read_fleet_agent("copilot", "Copilot"),
        read_fleet_agent("cursor", "Cursor"),
        read_fleet_agent("grok", "Grok"),
        read_fleet_agent("junie", "Junie"),
        read_kimi(),
    ];

    if let Some(expected_agent) = query
        .agent
        .as_deref()
        .filter(|s| !s.is_empty() && *s != "all")
    {
        agents.retain(|a| a.id.eq_ignore_ascii_case(expected_agent));
    }
    if let Some(expected_provider) = query
        .provider
        .as_deref()
        .filter(|s| !s.is_empty() && *s != "all")
    {
        agents.retain(|a| {
            a.pool
                .as_deref()
                .is_some_and(|p| p.eq_ignore_ascii_case(expected_provider))
                || a.id.eq_ignore_ascii_case(expected_provider)
                || a.name.eq_ignore_ascii_case(expected_provider)
        });
    }
    if let Some(expected_pool) = query
        .pool
        .as_deref()
        .filter(|s| !s.is_empty() && *s != "all")
    {
        agents.retain(|a| {
            a.pool
                .as_deref()
                .is_some_and(|p| p.eq_ignore_ascii_case(expected_pool))
                || a.id.eq_ignore_ascii_case(expected_pool)
        });
    }
    if let Some(expected_model) = query
        .model
        .as_deref()
        .filter(|s| !s.is_empty() && *s != "all")
    {
        agents.retain(|a| {
            a.model.as_deref().is_some_and(|m| {
                m.to_ascii_lowercase()
                    .contains(&expected_model.to_ascii_lowercase())
            })
        });
    }
    if let Some(expected_account) = query
        .account
        .as_deref()
        .filter(|s| !s.is_empty() && *s != "all")
    {
        agents.retain(|a| {
            a.account
                .as_deref()
                .is_some_and(|acc| acc.eq_ignore_ascii_case(expected_account))
        });
    }

    let body = QuotaDashboard {
        schema: SCHEMA,
        generated_at: Utc::now().to_rfc3339(),
        agents,
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
    let mut default_path = default_quota_path(".gemini/agy-monitor/runtime/quota.json");
    if !default_path.exists() {
        let alt = vibecrafted_telemetry_quota_path("agy");
        if alt.exists() {
            default_path = alt;
        }
    }
    project(
        "agy",
        "Antigravity",
        AGY_ENV,
        default_path,
        "~/.gemini/agy-monitor/runtime/quota.json",
        parse_agy,
    )
}

fn read_kimi() -> QuotaAgent {
    let mut default_path = default_quota_path(".kimi-code/runtime/quota.json");
    if !default_path.exists() {
        let alt = vibecrafted_telemetry_quota_path("kimi");
        if alt.exists() {
            default_path = alt;
        }
    }
    project(
        "kimi",
        "Kimi",
        KIMI_ENV,
        default_path,
        "~/.kimi-code/runtime/quota.json",
        parse_kimi,
    )
}

fn read_fleet_agent(id: &'static str, name: &'static str) -> QuotaAgent {
    let env_name = format!("VIBECRAFTED_{}_QUOTA_JSON", id.to_ascii_uppercase());
    let default_path = vibecrafted_telemetry_quota_path(id);
    let default_label = format!("~/.vibecrafted/telemetry/{id}/quota.json");
    project(
        id,
        name,
        &env_name,
        default_path,
        &default_label,
        parse_fleet,
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
        Source::Missing { label } => absent(id, name, label, default_pool_for(id)),
        Source::Unreadable { label, error } => QuotaAgent {
            id,
            agent: id,
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
            fraction: None,
            source: label,
            account: None,
            pool: default_pool_for(id),
            provenance: Some("unreadable".to_string()),
        },
        Source::Json { label, value } => {
            let mut fields = parse(&value);
            let age_s = json_ts(&value)
                .or_else(|| {
                    value
                        .get("generated_at")
                        .and_then(Value::as_str)
                        .and_then(iso_to_ts)
                })
                .map(age_secs);
            let stale = age_s.is_some_and(|age| age > STALE_SECS);
            if stale {
                if fields.status == "blocked" {
                    fields.status = "warn";
                } else {
                    fields.status = worst(fields.status, "warn");
                }
                if !fields.detail.is_empty() && !fields.detail.ends_with(" · stale") {
                    fields.detail.push_str(" · stale");
                }
            }
            let fraction = fields.fraction.or_else(|| {
                fields
                    .bars
                    .iter()
                    .filter_map(|b| b.ratio)
                    .max_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
            });
            QuotaAgent {
                id,
                agent: id,
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
                fraction,
                source: label,
                account: fields.account,
                pool: fields.pool.or_else(|| default_pool_for(id)),
                provenance: fields.provenance,
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
    fraction: Option<f64>,
    account: Option<String>,
    pool: Option<String>,
    provenance: Option<String>,
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

fn absent(
    id: &'static str,
    name: &'static str,
    source: String,
    pool: Option<String>,
) -> QuotaAgent {
    QuotaAgent {
        id,
        agent: id,
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
        fraction: None,
        source,
        account: None,
        pool,
        provenance: Some("absent".to_string()),
    }
}

fn parse_agy(value: &Value) -> QuotaFields {
    let quota_status = value
        .pointer("/quota/status")
        .or_else(|| value.get("status"))
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
    let frac = value
        .pointer("/quota/fraction")
        .or_else(|| value.get("fraction"))
        .or_else(|| value.get("utilization"))
        .and_then(Value::as_f64)
        .filter(|n| n.is_finite() && *n >= 0.0);
    let (status, headline) = match quota_status.as_str() {
        "RESOURCE_EXHAUSTED" | "BLOCKED" | "EXHAUSTED" => ("blocked", "EXHAUSTED".to_string()),
        "OK" => {
            if frac.is_some_and(|f| f >= 1.0) {
                ("blocked", "EXHAUSTED".to_string())
            } else if frac.is_some_and(|f| f >= 0.8) {
                ("warn", "HIGH".to_string())
            } else {
                ("ok", "OK".to_string())
            }
        }
        "" => {
            if frac.is_some_and(|f| f >= 1.0) {
                ("blocked", "EXHAUSTED".to_string())
            } else if frac.is_some_and(|f| f >= 0.8) {
                ("warn", "HIGH".to_string())
            } else {
                ("unknown", "no status".to_string())
            }
        }
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
        fraction: frac,
        account: text_field(value, "account"),
        pool: Some("google".to_string()),
        provenance: Some("agy-monitor".to_string()),
    }
}

fn parse_kimi(value: &Value) -> QuotaFields {
    let kind = value
        .get("kind")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_ascii_lowercase();
    let mut bars = ["limit5h", "monthTotal", "monthCode"]
        .into_iter()
        .filter_map(|key| ratio_bar(value, key))
        .collect::<Vec<_>>();
    let mut extracted_frac = None;
    if bars.is_empty() {
        if let Some(limits) = value.get("limits").and_then(Value::as_object) {
            if let Some(f5) = limits
                .get("five_hour")
                .and_then(|v| v.get("fraction"))
                .and_then(Value::as_f64)
            {
                bars.push(QuotaBar {
                    id: "limit5h",
                    label: "5h",
                    ratio: Some(f5),
                    level: ratio_level(f5),
                    text: format!("{:.0}%", f5.clamp(0.0, 1.0) * 100.0),
                });
            }
            if let Some(fm) = limits
                .get("monthly")
                .and_then(|v| v.get("fraction"))
                .and_then(Value::as_f64)
            {
                bars.push(QuotaBar {
                    id: "monthTotal",
                    label: "month",
                    ratio: Some(fm),
                    level: ratio_level(fm),
                    text: format!("{:.0}%", fm.clamp(0.0, 1.0) * 100.0),
                });
            }
        } else if let Some(frac) = value.get("fraction").and_then(Value::as_f64) {
            bars.push(QuotaBar {
                id: "limit",
                label: "limit",
                ratio: Some(frac),
                level: ratio_level(frac),
                text: format!("{:.0}%", frac.clamp(0.0, 1.0) * 100.0),
            });
            extracted_frac = Some(frac);
        }
    }
    let hottest = bars
        .iter()
        .filter_map(|bar| bar.ratio)
        .fold(0.0_f64, f64::max);
    if kind != "ok" && !kind.is_empty() {
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
            fraction: extracted_frac.or(Some(hottest).filter(|&h| h > 0.0)),
            account: text_field(value, "account"),
            pool: Some("moonshot".to_string()),
            provenance: Some("kimi-monitor".to_string()),
        };
    }
    let status_str = value
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_ascii_lowercase();
    let status = if status_str == "blocked"
        || status_str == "exhausted"
        || hottest >= BLOCKED_LIMIT
    {
        "blocked"
    } else {
        ratio_level(hottest)
    };
    let headline = if status == "blocked" && hottest >= 1.0 {
        "100%".to_string()
    } else {
        bars.first()
            .map(|bar| bar.text.clone())
            .unwrap_or_else(|| "—".to_string())
    };
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
        fraction: extracted_frac.or(Some(hottest).filter(|&h| h > 0.0)),
        account: text_field(value, "account"),
        pool: Some("moonshot".to_string()),
        provenance: Some("kimi-monitor".to_string()),
    }
}

fn parse_fleet(value: &Value) -> QuotaFields {
    let agent = value.get("agent").and_then(Value::as_str).unwrap_or("");
    let status_val = value.get("status").and_then(Value::as_str).unwrap_or("");
    let reason = value.get("reason").and_then(Value::as_str);
    let quota_obj = value.get("quota").and_then(Value::as_object);
    let quota_status = quota_obj
        .and_then(|q| q.get("status"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_ascii_uppercase();

    let last_session = value.get("last_session").and_then(Value::as_object);
    let model = last_session
        .and_then(|s| s.get("model"))
        .and_then(Value::as_str)
        .map(str::to_string)
        .or_else(|| text_field(value, "model"));

    let tokens = value
        .pointer("/metrics/processed_total")
        .and_then(Value::as_u64)
        .or_else(|| {
            value
                .pointer("/metrics/fleet_processed_total")
                .and_then(Value::as_u64)
        })
        .or_else(|| {
            last_session
                .and_then(|s| s.get("processed_total"))
                .and_then(Value::as_u64)
        });

    let cost_usd = value
        .pointer("/metrics/cost_usd")
        .and_then(Value::as_f64)
        .filter(|n| n.is_finite() && *n >= 0.0);

    let pool = default_pool_for(agent);

    if status_val == "unavailable" {
        let msg = reason.unwrap_or("usage lives in Cursor cloud");
        return QuotaFields {
            status: "unknown",
            headline: "cloud-only".to_string(),
            detail: format!("{msg} · unmeasured quota"),
            model,
            tokens: None,
            cost_usd: None,
            bars: Vec::new(),
            fraction: None,
            account: None,
            pool,
            provenance: Some("cloud_unavailable".to_string()),
        };
    }

    if quota_status == "RESOURCE_EXHAUSTED" || quota_status == "BLOCKED" {
        let mut detail_parts = Vec::new();
        if let Some(t) = tokens {
            detail_parts.push(format!("{} tok", format_tokens(t)));
        }
        if let Some(m) = &model {
            detail_parts.push(m.clone());
        }
        detail_parts.push("quota exhausted".to_string());
        return QuotaFields {
            status: "blocked",
            headline: "EXHAUSTED".to_string(),
            detail: detail_parts.join(" · "),
            model,
            tokens,
            cost_usd,
            bars: Vec::new(),
            fraction: Some(1.0),
            account: text_field(value, "account"),
            pool,
            provenance: Some("fleet_engine".to_string()),
        };
    }

    let bars = ["limit", "daily", "monthly"]
        .into_iter()
        .filter_map(|key| ratio_bar(value, key))
        .collect::<Vec<_>>();

    if !bars.is_empty() {
        let hottest = bars.iter().filter_map(|b| b.ratio).fold(0.0_f64, f64::max);
        let status = ratio_level(hottest);
        let headline = bars
            .first()
            .map(|b| b.text.clone())
            .unwrap_or_else(|| "—".to_string());
        let detail = bars
            .iter()
            .map(|b| format!("{} {}", b.label, b.text))
            .collect::<Vec<_>>()
            .join(" · ");
        return QuotaFields {
            status,
            headline,
            detail,
            model,
            tokens,
            cost_usd,
            bars,
            fraction: Some(hottest),
            account: text_field(value, "account"),
            pool,
            provenance: Some("fleet_engine".to_string()),
        };
    }

    let mut parts = Vec::new();
    if let Some(t) = tokens {
        parts.push(format!("{} tok", format_tokens(t)));
    }
    if let Some(m) = &model {
        parts.push(m.clone());
    }
    parts.push("unmeasured quota".to_string());

    QuotaFields {
        status: "unknown",
        headline: "unmeasured".to_string(),
        detail: parts.join(" · "),
        model,
        tokens,
        cost_usd,
        bars: Vec::new(),
        fraction: None,
        account: text_field(value, "account"),
        pool,
        provenance: Some("local_sessions".to_string()),
    }
}

fn default_pool_for(id: &str) -> Option<String> {
    Some(match id {
        "agy" => "google".to_string(),
        "claude" => "anthropic".to_string(),
        "codex" => "openai".to_string(),
        "copilot" => "github".to_string(),
        "cursor" => "anysphere".to_string(),
        "grok" => "xai".to_string(),
        "junie" => "jetbrains".to_string(),
        "kimi" => "moonshot".to_string(),
        _ => id.to_string(),
    })
}

fn iso_to_ts(iso: &str) -> Option<u64> {
    chrono::DateTime::parse_from_rfc3339(iso)
        .ok()
        .map(|dt| dt.timestamp().max(0) as u64)
}

fn vibecrafted_telemetry_quota_path(agent: &str) -> PathBuf {
    if let Some(base) = std::env::var_os("VIBECRAFTED_HOME").filter(|v| !v.is_empty()) {
        PathBuf::from(base)
            .join("telemetry")
            .join(agent)
            .join("quota.json")
    } else {
        default_quota_path(&format!(".vibecrafted/telemetry/{agent}/quota.json"))
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
