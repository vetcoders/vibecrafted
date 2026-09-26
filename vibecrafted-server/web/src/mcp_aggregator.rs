//! Upstream MCP aggregation and PID→run identity for `POST /mcp`.
//!
//! Local pilot tools stay in `mcp.rs`. This module prefixes upstream tools
//! (`loctree_*`, `aicx_*`), caches `tools/list` with a TTL, and forwards
//! `tools/call` without caching the response. A timed-out upstream drops out
//! of the list; calling one of its tools returns a typed JSON-RPC error.
//!
//! Run identity reads `runtime_runs/<id>/meta.json`. The launcher stores the
//! worker PID in the top-level `worker_pid` field (`spawn.py`); `worker_identity.pid`
//! is the same receipt when the top-level field is absent. There is no
//! `dispatcher` object in this tree — that word names the dispatcher process.
//! `VIBECRAFTED_RUN_ID` (header `x-vibecrafted-run-id`) wins when that run
//! exists. Otherwise the shim PID and its ancestor chain are matched against
//! `worker_pid`. Two runs sharing a PID are split by process start (sysinfo)
//! versus `started_at`; a tie or a stale incarnation is left unassigned.

#![cfg(feature = "ssr")]

use std::collections::HashMap;
use std::future::Future;
use std::path::Path;
use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use chrono::DateTime;
use control_core::{ControlPlane, coerce_int_value, is_safe_run_id};
use http::{HeaderMap, HeaderName};
use serde_json::{Value, json};

pub const PID_HEADER: HeaderName = HeaderName::from_static("x-vibecrafted-pid");
pub const ANCESTOR_HEADER: HeaderName = HeaderName::from_static("x-vibecrafted-ancestors");
pub const RUN_HEADER: HeaderName = HeaderName::from_static("x-vibecrafted-run-id");

const DEFAULT_TIMEOUT: Duration = Duration::from_millis(800);
const DEFAULT_CACHE_TTL: Duration = Duration::from_secs(30);
const DEFAULT_NEGATIVE_TTL: Duration = Duration::from_secs(5);
const CLAIM_TTL: Duration = Duration::from_secs(2);
const MAX_CHAIN: usize = 16;
/// Process start and run `started_at` farther apart than this are different incarnations.
const START_WINDOW_SECS: u64 = 120;
/// Two in-window runs closer than this are ambiguous — do not guess.
const AMBIGUITY_SECS: u64 = 2;
const MAX_META_BYTES: u64 = 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UpstreamSpec {
    pub name: String,
    pub url: String,
    pub timeout: Duration,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UpstreamFault {
    Timeout,
    Unreachable,
    BadResponse,
}

impl UpstreamFault {
    #[must_use]
    pub const fn reason(self) -> &'static str {
        match self {
            Self::Timeout => "timeout",
            Self::Unreachable => "unreachable",
            Self::BadResponse => "bad_response",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct UpstreamExchange {
    pub body: Value,
    pub session_id: Option<String>,
}

pub trait UpstreamTransport: Send + Sync {
    fn exchange<'a>(
        &'a self,
        upstream: &'a UpstreamSpec,
        session: Option<String>,
        message: Value,
    ) -> Pin<Box<dyn Future<Output = Result<UpstreamExchange, UpstreamFault>> + Send + 'a>>;
}

pub trait ProcessStarts: Send + Sync {
    fn unix_start(&self, pid: i64) -> Option<i64>;
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorkerClaim {
    pub run_id: String,
    pub worker_pid: Option<i64>,
    pub started_unix: Option<i64>,
}

#[derive(Debug)]
pub enum RemoteCall {
    Local,
    Ready(Value),
    Failed {
        upstream: String,
        tool: String,
        fault: UpstreamFault,
    },
}

#[derive(Clone, Debug)]
pub struct McpUpstreamConfig {
    pub seen_upstream_table: bool,
    pub upstreams: Vec<UpstreamSpec>,
    pub cache_ttl: Option<Duration>,
}

struct ToolCacheEntry {
    stored: Instant,
    healthy: bool,
    tools: Vec<Value>,
}

struct IdentitySnap {
    at: Instant,
    claims: Arc<Vec<WorkerClaim>>,
    starts: HashMap<i64, Option<i64>>,
}

enum GroupPick {
    Matched(String),
    Ambiguous,
    Rejected,
}

pub struct Aggregator {
    upstreams: Vec<UpstreamSpec>,
    cache_ttl: Duration,
    negative_ttl: Duration,
    transport: Arc<dyn UpstreamTransport>,
    starts: Arc<dyn ProcessStarts>,
    tools: Mutex<HashMap<String, ToolCacheEntry>>,
    sessions: Mutex<HashMap<String, String>>,
    identity: Mutex<Option<IdentitySnap>>,
    ids: AtomicU64,
}

impl Aggregator {
    #[must_use]
    pub fn new(
        upstreams: Vec<UpstreamSpec>,
        cache_ttl: Duration,
        negative_ttl: Duration,
        transport: Arc<dyn UpstreamTransport>,
        starts: Arc<dyn ProcessStarts>,
    ) -> Self {
        Self {
            upstreams,
            cache_ttl,
            negative_ttl,
            transport,
            starts,
            tools: Mutex::new(HashMap::new()),
            sessions: Mutex::new(HashMap::new()),
            identity: Mutex::new(None),
            ids: AtomicU64::new(1),
        }
    }

    #[must_use]
    pub fn local_only() -> Arc<Self> {
        Arc::new(Self::new(
            Vec::new(),
            DEFAULT_CACHE_TTL,
            DEFAULT_NEGATIVE_TTL,
            Arc::new(DeadTransport),
            Arc::new(SysinfoStarts),
        ))
    }

    #[must_use]
    pub fn from_config(config: &McpUpstreamConfig) -> Arc<Self> {
        let upstreams = if config.seen_upstream_table {
            config.upstreams.clone()
        } else {
            default_upstreams()
        };
        let cache_ttl = config.cache_ttl.unwrap_or(DEFAULT_CACHE_TTL);
        Arc::new(Self::new(
            upstreams,
            cache_ttl,
            DEFAULT_NEGATIVE_TTL,
            Arc::new(HttpTransport::new()),
            Arc::new(SysinfoStarts),
        ))
    }

    pub async fn list_remote(&self) -> Vec<Value> {
        if self.upstreams.is_empty() {
            return Vec::new();
        }
        let mut slots: Vec<Vec<Value>> = vec![Vec::new(); self.upstreams.len()];
        let mut pending_idx = Vec::new();
        {
            let cache = lock(&self.tools);
            for (index, upstream) in self.upstreams.iter().enumerate() {
                if let Some(entry) = cache.get(&upstream.name)
                    && entry.fresh(self.cache_ttl, self.negative_ttl)
                {
                    if entry.healthy {
                        slots[index].clone_from(&entry.tools);
                    }
                    continue;
                }
                pending_idx.push(index);
            }
        }
        let pending: Vec<UpstreamSpec> = pending_idx
            .iter()
            .map(|index| self.upstreams[*index].clone())
            .collect();
        let fetched = futures_util::future::join_all(
            pending.iter().map(|upstream| self.fetch_tools(upstream)),
        )
        .await;
        let mut down = Vec::new();
        {
            let mut cache = lock(&self.tools);
            for (index, result) in pending_idx.into_iter().zip(fetched) {
                let name = self.upstreams[index].name.clone();
                match result {
                    Ok(tools) => {
                        slots[index].clone_from(&tools);
                        cache.insert(
                            name,
                            ToolCacheEntry {
                                stored: Instant::now(),
                                healthy: true,
                                tools,
                            },
                        );
                    }
                    Err(_) => {
                        down.push(name.clone());
                        cache.insert(
                            name,
                            ToolCacheEntry {
                                stored: Instant::now(),
                                healthy: false,
                                tools: Vec::new(),
                            },
                        );
                    }
                }
            }
        }
        for name in down {
            self.clear_session(&name);
        }
        slots.into_iter().flatten().collect()
    }

    pub async fn call(&self, name: &str, arguments: Value) -> RemoteCall {
        let Some((upstream, remote_name)) = self.split_name(name) else {
            return RemoteCall::Local;
        };
        match self.call_upstream(&upstream, &remote_name, arguments).await {
            Ok(value) => RemoteCall::Ready(value),
            Err(fault) => {
                self.remember_down(&upstream.name);
                RemoteCall::Failed {
                    upstream: upstream.name,
                    tool: name.to_string(),
                    fault,
                }
            }
        }
    }

    #[must_use]
    pub fn resolve_run(&self, plane: &ControlPlane, headers: &HeaderMap) -> Option<String> {
        let header_run = header_run_id(headers);
        let chain = pid_chain(headers);
        let claims = self.claims(plane);
        assign_from_claims(header_run.as_deref(), &chain, &claims, &|pid| {
            self.cached_start(pid)
        })
    }

    fn claims(&self, plane: &ControlPlane) -> Arc<Vec<WorkerClaim>> {
        {
            let guard = lock(&self.identity);
            if let Some(snap) = guard.as_ref()
                && snap.at.elapsed() < CLAIM_TTL
            {
                return Arc::clone(&snap.claims);
            }
        }
        let claims = Arc::new(load_worker_claims(plane));
        let mut guard = lock(&self.identity);
        *guard = Some(IdentitySnap {
            at: Instant::now(),
            claims: Arc::clone(&claims),
            starts: HashMap::new(),
        });
        claims
    }

    fn cached_start(&self, pid: i64) -> Option<i64> {
        {
            let guard = lock(&self.identity);
            if let Some(snap) = guard.as_ref()
                && let Some(cached) = snap.starts.get(&pid)
            {
                return *cached;
            }
        }
        let value = self.starts.unix_start(pid);
        let mut guard = lock(&self.identity);
        if let Some(snap) = guard.as_mut() {
            snap.starts.insert(pid, value);
        }
        value
    }

    async fn fetch_tools(&self, upstream: &UpstreamSpec) -> Result<Vec<Value>, UpstreamFault> {
        let session = self.ensure_session(upstream).await?;
        let message = json!({
            "jsonrpc": "2.0",
            "id": self.next_id(),
            "method": "tools/list",
            "params": {}
        });
        let exchange = self.transport.exchange(upstream, session, message).await?;
        let tools = exchange
            .body
            .pointer("/result/tools")
            .and_then(Value::as_array)
            .cloned()
            .ok_or(UpstreamFault::BadResponse)?;
        let mut prefixed = Vec::with_capacity(tools.len());
        for tool in tools {
            if let Some(tool) = prefix_tool(&upstream.name, tool) {
                prefixed.push(tool);
            }
        }
        Ok(prefixed)
    }

    async fn call_upstream(
        &self,
        upstream: &UpstreamSpec,
        remote_name: &str,
        arguments: Value,
    ) -> Result<Value, UpstreamFault> {
        let session = self.ensure_session(upstream).await?;
        let message = json!({
            "jsonrpc": "2.0",
            "id": self.next_id(),
            "method": "tools/call",
            "params": {"name": remote_name, "arguments": arguments}
        });
        let exchange = self.transport.exchange(upstream, session, message).await?;
        if exchange.body.get("error").is_some() {
            return Err(UpstreamFault::BadResponse);
        }
        exchange
            .body
            .get("result")
            .cloned()
            .ok_or(UpstreamFault::BadResponse)
    }

    async fn ensure_session(
        &self,
        upstream: &UpstreamSpec,
    ) -> Result<Option<String>, UpstreamFault> {
        if let Some(session) = lock(&self.sessions).get(&upstream.name).cloned() {
            return Ok(Some(session));
        }
        let init = json!({
            "jsonrpc": "2.0",
            "id": self.next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {
                    "name": "vc-server",
                    "version": env!("VC_SERVER_VERSION"),
                }
            }
        });
        let exchange = self.transport.exchange(upstream, None, init).await?;
        if exchange.body.get("result").is_none() {
            return Err(UpstreamFault::BadResponse);
        }
        let session = exchange.session_id.clone();
        if let Some(session) = &session {
            lock(&self.sessions).insert(upstream.name.clone(), session.clone());
        }
        let note = json!({
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        });
        if self
            .transport
            .exchange(upstream, session.clone(), note)
            .await
            .is_err()
        {
            self.clear_session(&upstream.name);
            return Err(UpstreamFault::Unreachable);
        }
        Ok(session)
    }

    fn remember_down(&self, name: &str) {
        self.clear_session(name);
        lock(&self.tools).insert(
            name.to_string(),
            ToolCacheEntry {
                stored: Instant::now(),
                healthy: false,
                tools: Vec::new(),
            },
        );
    }

    fn clear_session(&self, name: &str) {
        lock(&self.sessions).remove(name);
    }

    fn split_name(&self, name: &str) -> Option<(UpstreamSpec, String)> {
        let mut best: Option<(usize, usize)> = None;
        for (index, upstream) in self.upstreams.iter().enumerate() {
            let prefix = format!("{}_", upstream.name);
            let Some(rest) = name.strip_prefix(&prefix) else {
                continue;
            };
            if rest.is_empty() {
                continue;
            }
            if best.is_none_or(|(_, len)| rest.len() < len) {
                best = Some((index, rest.len()));
            }
        }
        let (index, _) = best?;
        let upstream = self.upstreams[index].clone();
        let rest = name
            .strip_prefix(&format!("{}_", upstream.name))?
            .to_string();
        Some((upstream, rest))
    }

    fn next_id(&self) -> u64 {
        self.ids.fetch_add(1, Ordering::Relaxed)
    }
}

impl ToolCacheEntry {
    fn fresh(&self, positive: Duration, negative: Duration) -> bool {
        let ttl = if self.healthy { positive } else { negative };
        !ttl.is_zero() && self.stored.elapsed() < ttl
    }
}

#[must_use]
pub fn default_upstreams() -> Vec<UpstreamSpec> {
    vec![
        UpstreamSpec {
            name: "loctree".to_string(),
            url: "http://127.0.0.1:5174/mcp".to_string(),
            timeout: DEFAULT_TIMEOUT,
        },
        UpstreamSpec {
            name: "aicx".to_string(),
            url: "http://127.0.0.1:8044/mcp".to_string(),
            timeout: DEFAULT_TIMEOUT,
        },
    ]
}

#[must_use]
pub fn parse_mcp_upstreams(text: &str) -> McpUpstreamConfig {
    let mut seen_upstream_table = false;
    let mut cache_ttl = None;
    let mut current: Option<String> = None;
    let mut drafts: HashMap<String, DraftUpstream> = HashMap::new();
    for raw in text.lines() {
        let line = strip_toml_comment(raw)
            .trim()
            .trim_start_matches('\u{feff}');
        if line.is_empty() {
            continue;
        }
        if line.starts_with('[') && line.ends_with(']') {
            current = None;
            let section = &line[1..line.len() - 1];
            if section == "mcp" {
                current = Some(String::new());
            } else if let Some(name) = section.strip_prefix("mcp.upstream.")
                && !name.is_empty()
                && !name.contains('.')
                && valid_server_name(name)
            {
                seen_upstream_table = true;
                current = Some(name.to_string());
                drafts.entry(name.to_string()).or_default();
            }
            continue;
        }
        let Some(section) = current.as_deref() else {
            continue;
        };
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        let key = key.trim();
        let value = value.trim();
        if section.is_empty() {
            if key == "tools_cache_ttl_ms"
                && let Some(ms) = parse_u64(value)
            {
                cache_ttl = Some(Duration::from_millis(ms.clamp(100, 300_000)));
            }
            continue;
        }
        let draft = drafts.entry(section.to_string()).or_default();
        if key == "url"
            && let Some(url) = parse_quoted(value)
            && (url.starts_with("http://") || url.starts_with("https://"))
        {
            draft.url = Some(url);
        } else if key == "timeout_ms"
            && let Some(ms) = parse_u64(value)
        {
            draft.timeout = Some(Duration::from_millis(ms.clamp(50, 30_000)));
        }
    }
    let mut upstreams = Vec::new();
    let mut names: Vec<String> = drafts.keys().cloned().collect();
    names.sort();
    for name in names {
        let Some(draft) = drafts.get(&name) else {
            continue;
        };
        let Some(url) = draft.url.clone() else {
            continue;
        };
        upstreams.push(UpstreamSpec {
            name,
            url,
            timeout: draft.timeout.unwrap_or(DEFAULT_TIMEOUT),
        });
    }
    McpUpstreamConfig {
        seen_upstream_table,
        upstreams,
        cache_ttl,
    }
}

#[derive(Default)]
struct DraftUpstream {
    url: Option<String>,
    timeout: Option<Duration>,
}

#[must_use]
pub fn load_worker_claims(plane: &ControlPlane) -> Vec<WorkerClaim> {
    let dir = plane.control_plane_home().join("runtime_runs");
    let Ok(entries) = std::fs::read_dir(&dir) else {
        return Vec::new();
    };
    let mut claims = Vec::new();
    for entry in entries.flatten() {
        let file_type = entry.file_type().ok();
        if file_type.is_none_or(|kind| !kind.is_dir()) {
            continue;
        }
        let dirname = entry.file_name();
        let Some(dirname) = dirname.to_str() else {
            continue;
        };
        if !is_safe_run_id(dirname) {
            continue;
        }
        let path = entry.path().join("meta.json");
        let Some(payload) = read_meta(&path) else {
            continue;
        };
        let run_id = payload
            .get("run_id")
            .and_then(Value::as_str)
            .filter(|value| is_safe_run_id(value))
            .unwrap_or(dirname)
            .to_string();
        let worker_pid = payload
            .get("worker_pid")
            .and_then(coerce_int_value)
            .or_else(|| {
                payload
                    .get("worker_identity")
                    .and_then(|value| value.get("pid"))
                    .and_then(coerce_int_value)
            });
        let started_unix = payload
            .get("started_at")
            .and_then(Value::as_str)
            .and_then(parse_started_unix);
        claims.push(WorkerClaim {
            run_id,
            worker_pid,
            started_unix,
        });
    }
    claims
}

#[must_use]
pub fn parse_started_unix(raw: &str) -> Option<i64> {
    DateTime::parse_from_rfc3339(raw.trim())
        .ok()
        .map(|stamp| stamp.timestamp())
        .or_else(|| {
            DateTime::parse_from_rfc3339(&format!("{}Z", raw.trim()))
                .ok()
                .map(|stamp| stamp.timestamp())
        })
}

#[must_use]
pub fn assign_from_claims(
    header_run: Option<&str>,
    chain: &[i64],
    claims: &[WorkerClaim],
    process_start: &dyn Fn(i64) -> Option<i64>,
) -> Option<String> {
    if let Some(run_id) = header_run.map(str::trim).filter(|value| !value.is_empty()) {
        return claims
            .iter()
            .any(|claim| claim.run_id == run_id)
            .then(|| run_id.to_string());
    }
    for pid in chain.iter().take(MAX_CHAIN) {
        let group: Vec<&WorkerClaim> = claims
            .iter()
            .filter(|claim| claim.worker_pid == Some(*pid))
            .collect();
        if group.is_empty() {
            continue;
        }
        match pick_group(*pid, &group, process_start) {
            GroupPick::Matched(run_id) => return Some(run_id),
            GroupPick::Ambiguous => return None,
            GroupPick::Rejected => continue,
        }
    }
    None
}

fn pick_group(
    pid: i64,
    group: &[&WorkerClaim],
    process_start: &dyn Fn(i64) -> Option<i64>,
) -> GroupPick {
    if group.len() == 1 {
        let only = group[0];
        if let (Some(start), Some(started)) = (process_start(pid), only.started_unix)
            && start.abs_diff(started) > START_WINDOW_SECS
        {
            return GroupPick::Rejected;
        }
        return GroupPick::Matched(only.run_id.clone());
    }
    let Some(start) = process_start(pid) else {
        return GroupPick::Ambiguous;
    };
    let mut ranked: Vec<(u64, &str)> = group
        .iter()
        .filter_map(|claim| {
            let started = claim.started_unix?;
            let distance = start.abs_diff(started);
            (distance <= START_WINDOW_SECS).then_some((distance, claim.run_id.as_str()))
        })
        .collect();
    if ranked.is_empty() {
        return GroupPick::Ambiguous;
    }
    ranked.sort_by(|left, right| left.0.cmp(&right.0).then(left.1.cmp(right.1)));
    if ranked.len() > 1 && ranked[1].0.abs_diff(ranked[0].0) <= AMBIGUITY_SECS {
        return GroupPick::Ambiguous;
    }
    GroupPick::Matched(ranked[0].1.to_string())
}

#[must_use]
pub fn header_run_id(headers: &HeaderMap) -> Option<String> {
    let raw = headers.get(RUN_HEADER)?.to_str().ok()?.trim();
    is_safe_run_id(raw).then(|| raw.to_string())
}

#[must_use]
pub fn pid_chain(headers: &HeaderMap) -> Vec<i64> {
    let mut chain = Vec::new();
    if let Some(pid) = header_pid(headers, &PID_HEADER) {
        chain.push(pid);
    }
    if let Some(raw) = headers
        .get(ANCESTOR_HEADER)
        .and_then(|value| value.to_str().ok())
    {
        for part in raw.split(',') {
            if chain.len() >= MAX_CHAIN {
                break;
            }
            if let Ok(pid) = part.trim().parse::<i64>()
                && pid > 0
                && !chain.contains(&pid)
            {
                chain.push(pid);
            }
        }
    }
    chain
}

fn header_pid(headers: &HeaderMap, name: &HeaderName) -> Option<i64> {
    let raw = headers.get(name)?.to_str().ok()?.trim();
    let pid = raw.parse::<i64>().ok()?;
    (pid > 0).then_some(pid)
}

fn prefix_tool(prefix: &str, mut tool: Value) -> Option<Value> {
    let name = tool.get("name")?.as_str()?.to_string();
    if !valid_tool_name(&name) {
        return None;
    }
    let exposed = format!("{prefix}_{name}");
    tool.as_object_mut()?
        .insert("name".to_string(), Value::String(exposed));
    Some(tool)
}

fn valid_tool_name(name: &str) -> bool {
    let bytes = name.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 128
        && bytes
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

fn valid_server_name(name: &str) -> bool {
    let mut chars = name.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    first.is_ascii_alphabetic()
        && name.len() <= 32
        && chars.all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-')
}

fn read_meta(path: &Path) -> Option<Value> {
    let meta = std::fs::symlink_metadata(path).ok()?;
    if !meta.file_type().is_file() || meta.len() > MAX_META_BYTES {
        return None;
    }
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

pub(crate) fn strip_toml_comment(line: &str) -> &str {
    let mut in_basic = false;
    let mut in_literal = false;
    let mut escape = false;
    for (index, ch) in line.char_indices() {
        if in_basic {
            if escape {
                escape = false;
                continue;
            }
            if ch == '\\' {
                escape = true;
                continue;
            }
            if ch == '"' {
                in_basic = false;
            }
            continue;
        }
        if in_literal {
            if ch == '\'' {
                in_literal = false;
            }
            continue;
        }
        match ch {
            '"' => in_basic = true,
            '\'' => in_literal = true,
            '#' => return &line[..index],
            _ => {}
        }
    }
    line
}

fn parse_quoted(raw: &str) -> Option<String> {
    if raw.starts_with("\"\"\"") || raw.starts_with("'''") {
        return None;
    }
    if let Some(body) = raw.strip_prefix('"') {
        let mut out = String::new();
        let mut chars = body.chars();
        while let Some(ch) = chars.next() {
            if ch == '\\' {
                match chars.next()? {
                    '\\' => out.push('\\'),
                    '"' => out.push('"'),
                    'n' => out.push('\n'),
                    't' => out.push('\t'),
                    'r' => out.push('\r'),
                    _ => return None,
                }
                continue;
            }
            if ch == '"' {
                if !chars.all(char::is_whitespace) {
                    return None;
                }
                return acceptable_url(&out).then_some(out);
            }
            out.push(ch);
        }
        return None;
    }
    let body = raw.strip_prefix('\'')?;
    let (value, rest) = body.split_once('\'')?;
    if !rest.chars().all(char::is_whitespace) {
        return None;
    }
    acceptable_url(value).then(|| value.to_string())
}

fn acceptable_url(value: &str) -> bool {
    let bytes = value.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 2048
        && bytes.iter().all(u8::is_ascii_graphic)
        && !value.contains('@')
}

fn parse_u64(raw: &str) -> Option<u64> {
    let digits = raw.trim().trim_end_matches(|ch: char| ch.is_whitespace());
    if digits.is_empty() || !digits.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    digits.parse().ok()
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|err| err.into_inner())
}

struct DeadTransport;

impl UpstreamTransport for DeadTransport {
    fn exchange<'a>(
        &'a self,
        _upstream: &'a UpstreamSpec,
        _session: Option<String>,
        _message: Value,
    ) -> Pin<Box<dyn Future<Output = Result<UpstreamExchange, UpstreamFault>> + Send + 'a>> {
        Box::pin(async { Err(UpstreamFault::Unreachable) })
    }
}

pub struct ScriptedTransport<F> {
    pub handler: F,
}

impl<F> UpstreamTransport for ScriptedTransport<F>
where
    F: Fn(&UpstreamSpec, Option<&str>, &Value) -> Result<UpstreamExchange, UpstreamFault>
        + Send
        + Sync,
{
    fn exchange<'a>(
        &'a self,
        upstream: &'a UpstreamSpec,
        session: Option<String>,
        message: Value,
    ) -> Pin<Box<dyn Future<Output = Result<UpstreamExchange, UpstreamFault>> + Send + 'a>> {
        let result = (self.handler)(upstream, session.as_deref(), &message);
        Box::pin(async move { result })
    }
}

pub struct SysinfoStarts;

impl ProcessStarts for SysinfoStarts {
    fn unix_start(&self, pid: i64) -> Option<i64> {
        let pid_u = u32::try_from(pid).ok()?;
        if pid_u == 0 {
            return None;
        }
        let mut system = sysinfo::System::new();
        let sys_pid = sysinfo::Pid::from_u32(pid_u);
        system.refresh_processes(sysinfo::ProcessesToUpdate::Some(&[sys_pid]), true);
        let process = system.process(sys_pid)?;
        let start = process.start_time();
        if start == 0 {
            return None;
        }
        i64::try_from(start).ok()
    }
}

pub struct MapStarts {
    starts: HashMap<i64, i64>,
}

impl MapStarts {
    #[must_use]
    pub fn new(starts: HashMap<i64, i64>) -> Self {
        Self { starts }
    }
}

impl ProcessStarts for MapStarts {
    fn unix_start(&self, pid: i64) -> Option<i64> {
        self.starts.get(&pid).copied()
    }
}

pub struct HttpTransport {
    client: reqwest::Client,
}

impl HttpTransport {
    #[must_use]
    pub fn new() -> Self {
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_millis(250))
            .pool_idle_timeout(Duration::from_secs(30))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new());
        Self { client }
    }

    async fn post(
        client: &reqwest::Client,
        upstream: &UpstreamSpec,
        session: Option<String>,
        message: Value,
    ) -> Result<UpstreamExchange, UpstreamFault> {
        let method = message
            .get("method")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let mut request = client
            .post(&upstream.url)
            .timeout(upstream.timeout)
            .header(http::header::CONTENT_TYPE, "application/json")
            .header(http::header::ACCEPT, "application/json, text/event-stream");
        if method != "initialize" {
            request = request.header("mcp-protocol-version", "2025-03-26");
        }
        if let Some(session) = session {
            request = request.header("mcp-session-id", session);
        }
        let response = match request.json(&message).send().await {
            Ok(response) => response,
            Err(err) if err.is_timeout() => return Err(UpstreamFault::Timeout),
            Err(_) => return Err(UpstreamFault::Unreachable),
        };
        let session_id = response
            .headers()
            .get("mcp-session-id")
            .and_then(|value| value.to_str().ok())
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string);
        let status = response.status();
        if status.as_u16() == 202 || status.as_u16() == 204 {
            return Ok(UpstreamExchange {
                body: Value::Null,
                session_id,
            });
        }
        if !status.is_success() {
            return Err(if status.is_server_error() {
                UpstreamFault::Unreachable
            } else {
                UpstreamFault::BadResponse
            });
        }
        let content_type = response
            .headers()
            .get(http::header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .unwrap_or("")
            .to_string();
        let text = response
            .text()
            .await
            .map_err(|_| UpstreamFault::BadResponse)?;
        if text.len() > 8 * 1024 * 1024 {
            return Err(UpstreamFault::BadResponse);
        }
        let body = if content_type.contains("text/event-stream") {
            first_sse_json(&text).ok_or(UpstreamFault::BadResponse)?
        } else if text.trim().is_empty() {
            Value::Null
        } else {
            serde_json::from_str(&text).map_err(|_| UpstreamFault::BadResponse)?
        };
        Ok(UpstreamExchange { body, session_id })
    }
}

impl Default for HttpTransport {
    fn default() -> Self {
        Self::new()
    }
}

impl UpstreamTransport for HttpTransport {
    fn exchange<'a>(
        &'a self,
        upstream: &'a UpstreamSpec,
        session: Option<String>,
        message: Value,
    ) -> Pin<Box<dyn Future<Output = Result<UpstreamExchange, UpstreamFault>> + Send + 'a>> {
        let client = self.client.clone();
        Box::pin(async move { Self::post(&client, upstream, session, message).await })
    }
}

fn first_sse_json(body: &str) -> Option<Value> {
    for line in body.lines() {
        let Some(data) = line.trim().strip_prefix("data:") else {
            continue;
        };
        let data = data.trim();
        if data.is_empty() || data == "[DONE]" {
            continue;
        }
        if let Ok(value) = serde_json::from_str::<Value>(data) {
            return Some(value);
        }
    }
    None
}
