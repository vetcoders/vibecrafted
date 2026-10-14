//! Shared agent-quota observation board.
//!
//! One card per supported provider, all eight read through the same table:
//! `<home>/telemetry/<id>/quota.json` first, the engine-native file second.
//! A plain `GET` reads files only — no daemon, no network, no second
//! accounting engine. `?refresh=preflight&agents=a,b` asks the server-owned
//! collector to refresh exactly those providers' authoritative sources (the
//! only moment the network may be touched); anything without such a source
//! stays a visible `unknown`, never a guess from token totals. The collector's
//! outcome rides on the response as `refresh`, so an explicit ask is never
//! answered silently.
//!
//! Every card carries a `state` — `ok`, `low`, `exhausted`, `stale`,
//! `unknown`, `absent`, `malformed` — with source, age and `scope`
//! (pool/account/model). `exhausted` is only ever a *fresh* observation; an old
//! one is `stale`, whatever it said.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::Json;
use axum::extract::{Extension, Query};
use axum::http::{HeaderValue, header};
use axum::response::{IntoResponse, Response};
use chrono::Utc;
use control_core::ControlPlane;
use control_core::usage_collect::CollectOutcome;
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
    /// `preflight` (or `network`/`1`) — refresh authoritative sources first.
    pub refresh: Option<String>,
    /// Comma list of providers to refresh; defaults to the `agent` filter.
    pub agents: Option<String>,
}

#[derive(Debug, Serialize)]
struct QuotaDashboard {
    schema: &'static str,
    generated_at: String,
    agents: Vec<QuotaAgent>,
    /// Present only on an explicit refresh request: exactly what the
    /// server-owned collector did, per provider. An explicit ask is never
    /// answered silently — a failed or skipped refresh is as visible as a
    /// successful one.
    #[serde(skip_serializing_if = "Option::is_none")]
    refresh: Option<CollectOutcome>,
}

#[derive(Debug, Clone, Serialize)]
pub struct QuotaAgent {
    pub id: &'static str,
    /// Same value as `id`, under the key the dispatch doctor reads.
    pub agent: &'static str,
    pub name: &'static str,
    pub present: bool,
    /// Legacy display level: ok / warn / blocked / unknown / absent.
    pub status: &'static str,
    /// The distinct observation state consumers must branch on.
    pub state: &'static str,
    pub headline: String,
    pub detail: String,
    pub stale: bool,
    pub age_s: Option<u64>,
    pub model: Option<String>,
    pub tokens: Option<u64>,
    pub cost_usd: Option<f64>,
    pub bars: Vec<QuotaBar>,
    pub source: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub account: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pool: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provenance: Option<String>,
    /// RFC 3339 time of the observation the state rests on, when known.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub observed_at: Option<String>,
    pub scope: QuotaScope,
}

/// What a quota observation is about; `None` means "not scoped to this".
#[derive(Debug, Clone, Serialize, Default)]
pub struct QuotaScope {
    pub pool: Option<String>,
    pub account: Option<String>,
    pub model: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct QuotaBar {
    pub id: &'static str,
    pub label: &'static str,
    pub ratio: Option<f64>,
    pub text: String,
    pub level: &'static str,
}

struct Provider {
    id: &'static str,
    name: &'static str,
    /// Engine-native quota file (relative to `$HOME`), read after the shared
    /// `<home>/telemetry/<id>/quota.json`. Present for the two engines that
    /// predate the shared projection; it is a source path, not a privilege.
    native: Option<&'static str>,
    /// Parses the snapshot found in this provider's own slot. The slot id —
    /// never the file's self-declared fields — decides pool scope, because
    /// admission blocks on pools.
    parse: fn(&'static str, &Value) -> QuotaFields,
}

/// All supported providers, one table, one reader. Alphabetical on purpose.
const PROVIDERS: [Provider; 8] = [
    Provider {
        id: "agy",
        name: "Antigravity",
        native: Some(".gemini/agy-monitor/runtime/quota.json"),
        parse: parse_agy,
    },
    Provider {
        id: "claude",
        name: "Claude",
        native: None,
        parse: parse_fleet,
    },
    Provider {
        id: "codex",
        name: "Codex",
        native: None,
        parse: parse_fleet,
    },
    Provider {
        id: "copilot",
        name: "Copilot",
        native: None,
        parse: parse_fleet,
    },
    Provider {
        id: "cursor",
        name: "Cursor",
        native: None,
        parse: parse_fleet,
    },
    Provider {
        id: "grok",
        name: "Grok",
        native: None,
        parse: parse_fleet,
    },
    Provider {
        id: "junie",
        name: "Junie",
        native: None,
        parse: parse_fleet,
    },
    Provider {
        id: "kimi",
        name: "Kimi",
        native: Some(".kimi-code/runtime/quota.json"),
        parse: parse_kimi,
    },
];

pub async fn quota(
    Extension(plane): Extension<ControlPlane>,
    Query(query): Query<QuotaQuery>,
) -> Response {
    let refresh = if wants_refresh(&query) {
        let targets = refresh_targets(&query);
        let refresher = plane.clone();
        // Blocking one-shot of the server-owned collector; never on the runtime.
        // The outcome rides on the board, so a preflight can tell "no source"
        // apart from "the refresh never ran or failed".
        Some(
            tokio::task::spawn_blocking(move || refresher.refresh_quota(&targets))
                .await
                .unwrap_or_else(|error| CollectOutcome {
                    mode: "quota".to_string(),
                    started: false,
                    reason: format!("collector task failed: {error}"),
                    agents: Vec::new(),
                }),
        )
    } else {
        None
    };
    let home = plane.home();
    let mut agents = PROVIDERS
        .iter()
        .map(|provider| read_provider(provider, &home))
        .collect::<Vec<_>>();

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
        let wanted = expected_model.to_ascii_lowercase();
        agents.retain(|a| {
            [a.scope.model.as_deref(), a.model.as_deref()]
                .into_iter()
                .flatten()
                .any(|m| m.to_ascii_lowercase().contains(&wanted))
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
        refresh,
    };
    no_store(Json(body).into_response())
}

fn wants_refresh(query: &QuotaQuery) -> bool {
    matches!(
        query.refresh.as_deref().map(str::trim),
        Some("preflight" | "network" | "1" | "true")
    )
}

/// Providers to refresh: the explicit `agents` list, else the `agent` filter,
/// else every provider (an operator asked for a full refresh).
fn refresh_targets(query: &QuotaQuery) -> Vec<String> {
    let mut listed = query
        .agents
        .as_deref()
        .into_iter()
        .flat_map(|list| list.split(','))
        .chain(query.agent.as_deref())
        .map(|name| name.trim().to_ascii_lowercase())
        .filter(|name| !name.is_empty() && name != "all")
        .collect::<Vec<_>>();
    // A filter and an explicit list can name the same provider. Normalize
    // before collection so that one selected source is probed only once.
    listed.sort();
    listed.dedup();
    if listed.is_empty() {
        PROVIDERS.iter().map(|p| p.id.to_string()).collect()
    } else {
        listed
    }
}

fn no_store(mut response: Response) -> Response {
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

fn read_provider(provider: &Provider, home: &Path) -> QuotaAgent {
    let env_name = format!(
        "VIBECRAFTED_{}_QUOTA_JSON",
        provider.id.to_ascii_uppercase()
    );
    let mut candidates = vec![(
        home.join("telemetry").join(provider.id).join("quota.json"),
        format!("~/.vibecrafted/telemetry/{}/quota.json", provider.id),
    )];
    if let Some(relative) = provider.native {
        candidates.push((default_quota_path(relative), format!("~/{relative}")));
    }
    project(
        provider.id,
        provider.name,
        &env_name,
        &candidates,
        provider.parse,
    )
}

/// The one place a reading becomes a `state`.
///
/// `exhausted` needs a *fresh, timed* observation. An old one is `stale` and
/// an untimed one is `unknown` — neither may pose as current confirmation.
fn observation_state(status: &str, stale: bool, timed: bool) -> &'static str {
    if !timed {
        return "unknown";
    }
    if stale {
        return "stale";
    }
    match status {
        "blocked" => "exhausted",
        "warn" => "low",
        "ok" => "ok",
        _ => "unknown",
    }
}

fn project(
    id: &'static str,
    name: &'static str,
    env_name: &str,
    candidates: &[(PathBuf, String)],
    parse: fn(&'static str, &Value) -> QuotaFields,
) -> QuotaAgent {
    match load_source(env_name, candidates) {
        Source::Missing { label } => absent(id, name, label, default_pool_for(id)),
        Source::Unreadable { label, error } => QuotaAgent {
            id,
            agent: id,
            name,
            present: true,
            status: "unknown",
            state: "malformed",
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
            observed_at: None,
            scope: QuotaScope {
                pool: default_pool_for(id),
                ..QuotaScope::default()
            },
        },
        Source::Json { label, value } => {
            let mut fields = parse(id, &value);
            // AGY rewrites snapshot time when it re-reads an old conversation.
            // Only the matching status event can establish its observation age.
            let observed_ts = if id == "agy" {
                fields.observed_ts
            } else {
                fields.observed_ts.or_else(|| json_ts(&value)).or_else(|| {
                    value
                        .get("generated_at")
                        .and_then(Value::as_str)
                        .and_then(iso_to_ts)
                })
            };
            let age_s = observed_ts.map(age_secs);
            let stale = age_s.is_some_and(|age| age > STALE_SECS);
            let state = observation_state(fields.status, stale, observed_ts.is_some());
            if stale || observed_ts.is_none() {
                if fields.status == "blocked" {
                    fields.status = "warn";
                } else {
                    fields.status = worst(fields.status, "warn");
                }
                let tag = if stale {
                    " · stale"
                } else {
                    " · no observation time"
                };
                if !fields.detail.ends_with(tag) {
                    fields.detail.push_str(tag);
                }
            }
            let pool = fields.pool.or_else(|| default_pool_for(id));
            QuotaAgent {
                id,
                agent: id,
                name,
                present: true,
                status: fields.status,
                state,
                headline: fields.headline,
                detail: fields.detail,
                stale,
                age_s,
                model: fields.model,
                tokens: fields.tokens,
                cost_usd: fields.cost_usd,
                bars: fields.bars,
                source: label,
                account: fields.account.clone(),
                pool: pool.clone(),
                provenance: fields.provenance,
                observed_at: observed_ts.and_then(|ts| {
                    chrono::DateTime::from_timestamp(i64::try_from(ts).ok()?, 0)
                        .map(|stamp| stamp.to_rfc3339())
                }),
                scope: QuotaScope {
                    pool,
                    account: fields.account,
                    model: fields.scope_model,
                },
            }
        }
    }
}

#[derive(Default)]
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
    /// Time the status rests on, when it differs from the snapshot time.
    observed_ts: Option<u64>,
    /// Model the quota is scoped to (None = the whole pool).
    scope_model: Option<String>,
}

enum Source {
    Missing { label: String },
    Unreadable { label: String, error: String },
    Json { label: String, value: Value },
}

/// Env override first (inline JSON or a path), then the candidate files in
/// order; the first one that exists wins, even if it is unreadable.
fn load_source(env_name: &str, candidates: &[(PathBuf, String)]) -> Source {
    let default_label = candidates
        .first()
        .map(|(_, label)| label.clone())
        .unwrap_or_default();
    match std::env::var(env_name) {
        Ok(raw) if raw.trim_start().starts_with('{') => parse_inline(env_name, raw),
        Ok(path) if !path.trim().is_empty() => read_file(Path::new(path.trim()), path.trim()),
        Ok(_) => Source::Missing {
            label: default_label,
        },
        Err(_) => {
            for (path, label) in candidates {
                match read_file(path, label) {
                    Source::Missing { .. } => {}
                    found => return found,
                }
            }
            Source::Missing {
                label: default_label,
            }
        }
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
        state: "absent",
        headline: "no observation".to_string(),
        detail: "no quota observation has been collected for this provider".to_string(),
        stale: false,
        age_s: None,
        model: None,
        tokens: None,
        cost_usd: None,
        bars: Vec::new(),
        source,
        account: None,
        pool: pool.clone(),
        provenance: Some("absent".to_string()),
        observed_at: None,
        scope: QuotaScope {
            pool,
            ..QuotaScope::default()
        },
    }
}

fn parse_agy(id: &'static str, value: &Value) -> QuotaFields {
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
    let model_for_scope = model.clone();
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
        pool: default_pool_for(id),
        provenance: Some("agy-transcript".to_string()),
        // Success and exhaustion use their actual status-event times. Missing
        // or zero event evidence never becomes fresh through snapshot rewrite.
        observed_ts: match status {
            "blocked" => value.pointer("/quota/last_429_ts"),
            "ok" => value.pointer("/quota/last_success_ts"),
            _ => None,
        }
        .and_then(Value::as_u64)
        .map(|millis| millis / 1000)
        .filter(|seconds| *seconds > 0),
        scope_model: model_for_scope,
    }
}

fn parse_kimi(id: &'static str, value: &Value) -> QuotaFields {
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
    let measured = bars.iter().any(|bar| bar.ratio.is_some());
    if kind != "ok" {
        let message = value
            .pointer("/error/message")
            .and_then(Value::as_str)
            .or_else(|| value.get("error").and_then(Value::as_str))
            .or_else(|| value.get("last_poll_error").and_then(Value::as_str))
            .unwrap_or("quota snapshot error");
        return QuotaFields {
            status: "unknown",
            headline: "error".to_string(),
            detail: truncate(message),
            model: text_field(value, "plan"),
            tokens: None,
            cost_usd: None,
            bars,
            account: text_field(value, "account"),
            pool: default_pool_for(id),
            provenance: Some("kimi-probe".to_string()),
            ..QuotaFields::default()
        };
    }
    // Windows without a usable ratio measure nothing: unknown, never ok.
    let status = if measured {
        ratio_level(hottest)
    } else {
        "unknown"
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
        model: text_field(value, "plan"),
        tokens: None,
        cost_usd: None,
        bars,
        account: text_field(value, "account"),
        pool: default_pool_for(id),
        provenance: Some("kimi-probe".to_string()),
        ..QuotaFields::default()
    }
}

fn parse_fleet(id: &'static str, value: &Value) -> QuotaFields {
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

    // The pool is this slot's canonical pool. A fleet file's own `agent`
    // field is provider self-description, not scope authority: a snapshot
    // sitting in the wrong slot must not redefine which pool it speaks for.
    let pool = default_pool_for(id);

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
            ..QuotaFields::default()
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
            ..QuotaFields::default()
        };
    }

    let bars = ["limit", "daily", "monthly"]
        .into_iter()
        .filter_map(|key| ratio_bar(value, key))
        .collect::<Vec<_>>();

    if bars.iter().any(|b| b.ratio.is_some()) {
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
            ..QuotaFields::default()
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
        ..QuotaFields::default()
    }
}

fn default_pool_for(id: &str) -> Option<String> {
    // One owner of the launcher→pool vocabulary (control-core); the usage
    // report filter and the UI picker seeds speak the same names.
    Some(
        control_core::usage::provider_pool(id)
            .unwrap_or(id)
            .to_string(),
    )
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
