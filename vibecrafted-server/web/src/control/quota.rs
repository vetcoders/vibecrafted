//! Live agent-quota board for the usage page.
//!
//! Reads the files `agy-monitor` and `kimi-monitor` already write. No daemon,
//! no second accounting engine, no network. A missing file is an absent card,
//! not an error — the operator may not run that agent.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::Json;
use axum::extract::{Extension, Query};
use axum::http::{HeaderValue, header};
use axum::response::{IntoResponse, Response};
use chrono::Utc;
use control_core::ControlPlane;
use serde::{Deserialize, Serialize};
use serde_json::Value;

const SCHEMA: &str = "vibecrafted.quota-dashboard.v1";
const STALE_SECS: u64 = 300;
const NEAR_LIMIT: f64 = 0.85;
const BLOCKED_LIMIT: f64 = 1.0;
const MAX_BYTES: u64 = 256 * 1024;

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
    pub provider: String,
    pub observed_at: Option<String>,
    pub authoritative: bool,
    pub applies: bool,
    pub fraction: Option<f64>,
    pub covered_agents: Vec<String>,
    pub default_account: bool,
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
    pub source: String,
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

pub async fn quota(
    Extension(plane): Extension<ControlPlane>,
    Query(query): Query<QuotaQuery>,
) -> Response {
    let inventory = [
        ("agy", "Antigravity"),
        ("claude", "Claude"),
        ("codex", "Codex"),
        ("copilot", "Copilot"),
        ("cursor", "Cursor"),
        ("grok", "Grok"),
        ("junie", "Junie"),
        ("kimi", "Kimi"),
    ];
    let mut agents = Vec::new();
    for (id, name) in inventory {
        let env_name = format!("VIBECRAFTED_{}_QUOTA_JSON", id.to_ascii_uppercase());
        let path = plane.home().join("telemetry").join(id).join("quota.json");
        let path = if !path.exists() && id == "agy" {
            default_quota_path(".gemini/agy-monitor/runtime/quota.json")
        } else if !path.exists() && id == "kimi" {
            default_quota_path(".kimi-code/runtime/quota.json")
        } else {
            path
        };
        let source = load_source(&env_name, &path, &path.to_string_lossy());
        let parse = match id {
            "agy" => parse_agy,
            "kimi" => parse_kimi,
            _ => parse_fleet,
        };
        if let Source::Json { label, value } = &source {
            if let Some(observations) = value.get("observations").and_then(Value::as_array) {
                for observation in observations {
                    agents.push(project_source(
                        id,
                        name,
                        Source::Json {
                            label: label.clone(),
                            value: observation.clone(),
                        },
                        parse,
                    ));
                }
                if observations.is_empty() {
                    agents.push(absent(id, name, label.clone(), default_pool_for(id)));
                }
                continue;
            }
        }
        agents.push(project_source(id, name, source, parse));
    }
    if let Some(agent) = query
        .agent
        .as_deref()
        .filter(|s| !s.is_empty() && *s != "all")
    {
        // A provider-wide observation can cover another agent using the same
        // pool. A model-specific reading cannot become a provider-wide ban.
        let needed_pool = default_pool_for(agent);
        let needed_id = inventory
            .iter()
            .find(|(id, _)| *id == agent)
            .map(|(id, _)| *id);
        agents.retain(|a| {
            a.id == agent
                || a.covered_agents.iter().any(|member| member == agent)
                || (a.pool == needed_pool && a.model.is_none() && a.authoritative)
        });
        if let Some(id) = needed_id {
            for a in &mut agents {
                a.id = id;
            }
        }
    }
    for a in &mut agents {
        a.applies = a.applies
            && query
                .model
                .as_deref()
                .is_none_or(|m| a.model.as_deref().is_none_or(|scope| scope == m));
        a.applies = a.applies
            && query
                .account
                .as_deref()
                .map(|account| a.account.as_deref() == Some(account))
                .unwrap_or(a.default_account);
        a.applies = a.applies
            && query
                .pool
                .as_deref()
                .is_none_or(|pool| a.pool.as_deref() == Some(pool));
        if !a.applies && !matches!(a.status, "unknown" | "absent") {
            a.status = "unknown";
            a.detail = "observation does not cover effective model/account/pool".into();
        }
    }
    if let Some(provider) = query
        .provider
        .as_deref()
        .filter(|s| !s.is_empty() && *s != "all")
    {
        agents.retain(|a| a.provider == provider || a.id == provider);
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

fn project_source(
    id: &'static str,
    name: &'static str,
    source: Source,
    parse: fn(&Value) -> QuotaFields,
) -> QuotaAgent {
    match source {
        Source::Missing { label } => absent(id, name, label, default_pool_for(id)),
        Source::Unreadable { label, error } => QuotaAgent {
            id,
            provider: default_pool_for(id).unwrap_or_default(),
            observed_at: None,
            authoritative: false,
            applies: false,
            fraction: None,
            covered_agents: vec![id.to_string()],
            default_account: false,
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
            account: None,
            pool: default_pool_for(id),
            provenance: Some("unreadable".to_string()),
        },
        Source::Json { label, value } => {
            let mut fields = if value.get("authoritative").is_some() {
                parse_scoped(&value)
            } else {
                parse(&value)
            };
            let reported_fraction = value
                .get("fraction")
                .or_else(|| value.pointer("/quota/fraction"));
            if reported_fraction.is_some_and(|v| {
                !v.is_null() && v.as_f64().is_none_or(|f| !f.is_finite() || f < 0.0)
            }) || fields.bars.iter().any(|bar| bar.ratio.is_none())
            {
                fields.status = "unknown";
                fields.detail = "malformed quota reading".into();
            }
            let observed_ts = value
                .get("observed_at")
                .and_then(Value::as_str)
                .and_then(iso_to_ts)
                .or_else(|| json_ts(&value));
            let observed_at = observed_ts
                .and_then(|t| chrono::DateTime::from_timestamp(t as i64, 0))
                .map(|t| t.to_rfc3339());
            let age_s = observed_ts.map(age_secs);
            let invalid_time = observed_ts.is_none_or(|t| t > Utc::now().timestamp() as u64 + 30);
            let authoritative = !invalid_time
                && (id != "agy" || value["authoritative"] == true)
                && (value
                    .get("authoritative")
                    .and_then(Value::as_bool)
                    .unwrap_or(
                        fields.status == "blocked"
                            || fields.status == "ok"
                            || !fields.bars.is_empty(),
                    ));
            // Agy's legacy monitor derives RESOURCE_EXHAUSTED from log
            // text and republishes ts on every tick. That is not a fresh
            // authoritative provider quota response.
            if id == "agy" && value.get("authoritative").is_none() {
                fields.status = "unknown";
                fields.headline = "unconfirmed".into();
                fields.detail.push_str(
                    " · error-derived status; authoritative quota observation unavailable",
                );
            }
            let applies = value
                .get("applies")
                .and_then(Value::as_bool)
                .unwrap_or(true);
            let default_account = if value.get("authoritative").is_some() {
                value
                    .get("account")
                    .and_then(Value::as_str)
                    .is_some_and(|account| {
                        !account.is_empty()
                            && (account == "default" || value["default_account"] == true)
                    })
            } else {
                matches!(id, "agy" | "kimi") || value["default_account"] == true
            };
            if invalid_time {
                fields.status = "unknown";
                fields.detail.push_str(" · observation time unknown");
            }
            let fraction = value
                .get("fraction")
                .and_then(Value::as_f64)
                .or_else(|| fields.bars.iter().filter_map(|b| b.ratio).reduce(f64::max));
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
            QuotaAgent {
                id,
                provider: default_pool_for(id).unwrap_or_default(),
                observed_at,
                authoritative,
                applies,
                fraction,
                default_account,
                covered_agents: value
                    .get("agents")
                    .and_then(Value::as_array)
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(Value::as_str)
                            .map(str::to_string)
                            .collect()
                    })
                    .unwrap_or_else(|| vec![id.to_string()]),
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
                source: text_field(&value, "source").unwrap_or(label),
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
        provider: default_pool_for(id).unwrap_or_default(),
        observed_at: None,
        authoritative: false,
        applies: false,
        fraction: None,
        covered_agents: vec![id.to_string()],
        default_account: false,
        name,
        present: false,
        status: "absent",
        headline: "quiet".to_string(),
        detail: "quota unknown: no authoritative provider observation".to_string(),
        stale: false,
        age_s: None,
        model: None,
        tokens: None,
        cost_usd: None,
        bars: Vec::new(),
        source,
        account: None,
        pool,
        provenance: Some("absent".to_string()),
    }
}

fn parse_scoped(value: &Value) -> QuotaFields {
    let fraction = value
        .get("fraction")
        .and_then(Value::as_f64)
        .filter(|f| f.is_finite() && *f >= 0.0);
    let malformed = value
        .get("fraction")
        .is_some_and(|v| !v.is_null() && fraction.is_none());
    let status = if malformed || value["authoritative"] != true {
        "unknown"
    } else {
        match value["status"].as_str() {
            Some("blocked" | "exhausted" | "RESOURCE_EXHAUSTED") => "blocked",
            Some("warn" | "low") => "warn",
            Some("ok") => fraction.map(ratio_level).unwrap_or("ok"),
            _ => "unknown",
        }
    };
    QuotaFields {
        status,
        headline: status.into(),
        detail: if malformed {
            "malformed quota fraction".into()
        } else {
            value["detail"]
                .as_str()
                .unwrap_or("scoped provider observation")
                .into()
        },
        model: text_field(value, "model"),
        tokens: None,
        cost_usd: None,
        bars: Vec::new(),
        account: text_field(value, "account"),
        pool: text_field(value, "pool"),
        provenance: text_field(value, "source"),
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
        account: text_field(value, "account"),
        pool: text_field(value, "pool").or_else(|| Some("google".to_string())),
        provenance: Some("agy-monitor".to_string()),
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
            model: text_field(value, "model"),
            tokens: None,
            cost_usd: None,
            bars,
            account: text_field(value, "account"),
            pool: text_field(value, "pool").or_else(|| Some("moonshot".to_string())),
            provenance: Some("kimi-monitor".to_string()),
        };
    }
    let status = if bars.is_empty() {
        "unknown"
    } else {
        ratio_level(hottest)
    };
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
        model: text_field(value, "model"),
        tokens: None,
        cost_usd: None,
        bars,
        account: text_field(value, "account"),
        pool: text_field(value, "pool").or_else(|| Some("moonshot".to_string())),
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
        "cursor" => "cursor".to_string(),
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
    value.get("ts").and_then(Value::as_u64).or_else(|| {
        ["timestamp", "updated_at", "generated_at", "ts_iso"]
            .iter()
            .find_map(|k| value[*k].as_str().and_then(iso_to_ts))
    })
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
