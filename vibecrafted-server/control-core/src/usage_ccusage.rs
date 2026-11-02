//! In-process provider usage, read by the `ccusage` adapters.
//!
//! Pricing comes only from the deflated snapshots in `pricing/`. This module
//! never reaches the network. One adapter failure becomes that provider's
//! `error` row and leaves its previous projection untouched.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use ccusage_core::LoadedEntry;
use ccusage_core::cli::{AgentReportKind, CodexSpeed, CostMode, PricingOverride, SharedArgs};
use ccusage_core::pricing::{EmbeddedSnapshots, Pricing, PricingMap};
use chrono::{DateTime, SecondsFormat, Utc};
use serde::{Deserialize, Serialize};

pub(crate) const PROJECTION_FILE: &str = "ccusage-sessions.json";
const SCHEMA: &str = "vibecrafted.ccusage-sessions.v1";

#[derive(Clone, Copy)]
enum Provider {
    Claude,
    Codex,
    Grok,
    Kimi,
    Copilot,
    Agy,
}

const PROVIDERS: [Provider; 6] = [
    Provider::Claude,
    Provider::Codex,
    Provider::Grok,
    Provider::Kimi,
    Provider::Copilot,
    Provider::Agy,
];

impl Provider {
    fn agent(self) -> &'static str {
        match self {
            Self::Claude => "claude",
            Self::Codex => "codex",
            Self::Grok => "grok",
            Self::Kimi => "kimi",
            Self::Copilot => "copilot",
            Self::Agy => "agy",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ProviderRow {
    pub agent: &'static str,
    pub status: String,
    pub detail: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub(crate) struct ProjectedSession {
    pub id: String,
    pub model: String,
    pub recorded_at: String,
    pub events: u64,
    pub input: u64,
    pub cache_read: u64,
    pub cache_write: u64,
    pub output: u64,
    pub total: u64,
    pub cost_amount: Option<f64>,
    pub cost_source: String,
    pub unpriced_models: Vec<String>,
}

#[derive(Serialize, Deserialize)]
struct ProjectionFile {
    schema: String,
    sessions: Vec<ProjectedSession>,
}

pub(crate) enum LoadedProjection {
    Absent,
    Empty,
    Sessions(Vec<ProjectedSession>),
    Unreadable,
}

pub(crate) fn read_projection(path: &Path) -> LoadedProjection {
    if !crate::usage::regular_file(path) {
        return LoadedProjection::Absent;
    }
    let Ok(bytes) = fs::read(path) else {
        return LoadedProjection::Unreadable;
    };
    let Ok(file) = serde_json::from_slice::<ProjectionFile>(&bytes) else {
        return LoadedProjection::Unreadable;
    };
    if file.schema != SCHEMA {
        return LoadedProjection::Unreadable;
    }
    if file.sessions.is_empty() {
        LoadedProjection::Empty
    } else {
        LoadedProjection::Sessions(file.sessions)
    }
}

/// Base-rate price for one whole-run aggregate. Tier fields are ignored:
/// a summed run says nothing about any single request's context.
pub(crate) fn price_base(
    model: &str,
    input: u64,
    cache_read: u64,
    cache_write: u64,
    output: u64,
) -> Option<f64> {
    pricing_map()
        .find(model)
        .map(|price| priced(input, output, cache_read, cache_write, &price))
}

pub(crate) fn collect(home: &Path) -> Vec<ProviderRow> {
    if catch_unwind(AssertUnwindSafe(|| {
        install_snapshots();
        let _ = pricing_map();
    }))
    .is_err()
    {
        return PROVIDERS
            .into_iter()
            .map(|provider| ProviderRow {
                agent: provider.agent(),
                status: "error".to_string(),
                detail: "pricing snapshots are unavailable".to_string(),
            })
            .collect();
    }
    let shared = shared_args();
    let pricing = pricing_map();
    PROVIDERS
        .into_iter()
        .map(|provider| collect_provider(home, provider, &shared, pricing))
        .collect()
}

fn collect_provider(
    home: &Path,
    provider: Provider,
    shared: &SharedArgs,
    pricing: &PricingMap,
) -> ProviderRow {
    let agent = provider.agent();
    if !store_present(provider) {
        return ProviderRow {
            agent,
            status: "unavailable".to_string(),
            detail: format!("no {agent} session store"),
        };
    }
    let loaded = catch_unwind(AssertUnwindSafe(|| {
        load_provider(provider, shared, pricing)
    }));
    let sessions = match loaded {
        Ok(Ok(sessions)) => sessions,
        _ => {
            return ProviderRow {
                agent,
                status: "error".to_string(),
                detail: "ccusage adapter failed".to_string(),
            };
        }
    };
    let detail = if sessions.is_empty() {
        "store has no measured sessions".to_string()
    } else {
        String::new()
    };
    let file = ProjectionFile {
        schema: SCHEMA.to_string(),
        sessions,
    };
    let Ok(bytes) = serde_json::to_vec(&file) else {
        return ProviderRow {
            agent,
            status: "error".to_string(),
            detail: "ccusage projection could not be encoded".to_string(),
        };
    };
    if !write_atomic(&home.join("telemetry").join(agent), &bytes) {
        return ProviderRow {
            agent,
            status: "error".to_string(),
            detail: "ccusage projection could not be recorded".to_string(),
        };
    }
    ProviderRow {
        agent,
        status: "ok".to_string(),
        detail,
    }
}

fn load_provider(
    provider: Provider,
    shared: &SharedArgs,
    pricing: &PricingMap,
) -> Result<Vec<ProjectedSession>, ()> {
    match provider {
        Provider::Claude => {
            let entries = ccusage_adapter_claude::load_entries(shared, None).map_err(|_| ())?;
            Ok(sessions_from_entries("claude", &entries, pricing, false))
        }
        Provider::Codex => load_codex(shared, pricing),
        Provider::Grok => {
            let entries = ccusage_adapter_grok::load_entries(shared, pricing).map_err(|_| ())?;
            Ok(sessions_from_entries("grok", &entries, pricing, false))
        }
        Provider::Kimi => {
            let entries = ccusage_adapter_kimi::load_entries(shared, pricing).map_err(|_| ())?;
            Ok(sessions_from_entries("kimi", &entries, pricing, false))
        }
        Provider::Copilot => {
            let entries = ccusage_adapter_copilot::load_entries(shared, pricing).map_err(|_| ())?;
            Ok(sessions_from_entries("copilot", &entries, pricing, true))
        }
        Provider::Agy => {
            let (entries, _) =
                ccusage_adapter_antigravity::load_entries_with_warnings(shared, pricing)
                    .map_err(|_| ())?;
            Ok(sessions_from_entries("agy", &entries, pricing, false))
        }
    }
}

fn load_codex(shared: &SharedArgs, pricing: &PricingMap) -> Result<Vec<ProjectedSession>, ()> {
    let groups =
        ccusage_adapter_codex::load_groups(shared, AgentReportKind::Session).map_err(|_| ())?;
    let speed = ccusage_adapter_codex::resolve_codex_speed(CodexSpeed::Auto);
    let event_counts = catch_unwind(AssertUnwindSafe(|| {
        let mut counts: BTreeMap<String, u64> = BTreeMap::new();
        if let Ok((events, _)) = ccusage_adapter_codex::load_codex_events_with_detection(shared) {
            for event in events {
                let id = session_key("codex", &event.session_id);
                *counts.entry(id).or_default() += 1;
            }
        }
        counts
    }))
    .unwrap_or_default();
    let mut sessions = Vec::new();
    for (raw_id, group) in groups {
        let id = session_key("codex", &raw_id);
        let input = ccusage_adapter_codex::non_cached_input_tokens(
            group.input_tokens,
            group.cached_input_tokens,
            group.cache_creation_tokens,
        );
        let cache_read = group.cached_input_tokens;
        let cache_write = group.cache_creation_tokens;
        let output = group.output_tokens;
        let total = input
            .saturating_add(cache_read)
            .saturating_add(cache_write)
            .saturating_add(output);
        let mut unpriced = Vec::new();
        let mut model_tokens: BTreeMap<String, u64> = BTreeMap::new();
        for (model, usage) in &group.models {
            if ccusage_adapter_codex::codex_model_missing_pricing(model, usage, pricing) {
                unpriced.push(model.clone());
            }
            let model_input = ccusage_adapter_codex::non_cached_input_tokens(
                usage.input_tokens,
                usage.cached_input_tokens,
                usage.cache_creation_tokens,
            );
            let tokens = model_input
                .saturating_add(usage.cached_input_tokens)
                .saturating_add(usage.cache_creation_tokens)
                .saturating_add(usage.output_tokens);
            *model_tokens.entry(model.clone()).or_default() += tokens;
        }
        let (cost_amount, cost_source) = if unpriced.is_empty() {
            (
                Some(ccusage_adapter_codex::calculate_group_cost(
                    &group, pricing, speed,
                )),
                "estimated:ccusage".to_string(),
            )
        } else {
            (None, "estimated:ccusage".to_string())
        };
        let recorded_at = group.last_activity.unwrap_or_default();
        let events = event_counts.get(&id).copied().unwrap_or(0).max(1);
        sessions.push(ProjectedSession {
            id,
            model: dominant(&model_tokens),
            recorded_at: stamp_text(&recorded_at, 0),
            events,
            input,
            cache_read,
            cache_write,
            output,
            total,
            cost_amount,
            cost_source,
            unpriced_models: unpriced,
        });
    }
    Ok(sessions)
}

fn sessions_from_entries(
    agent: &str,
    entries: &[LoadedEntry],
    pricing: &PricingMap,
    copilot: bool,
) -> Vec<ProjectedSession> {
    let mut grouped: BTreeMap<String, Vec<&LoadedEntry>> = BTreeMap::new();
    for entry in entries {
        let id = session_key(agent, entry.session_id.as_ref());
        grouped.entry(id).or_default().push(entry);
    }
    grouped
        .into_iter()
        .map(|(id, session_entries)| project_session(id, &session_entries, pricing, copilot))
        .collect()
}

fn project_session(
    id: String,
    entries: &[&LoadedEntry],
    pricing: &PricingMap,
    copilot: bool,
) -> ProjectedSession {
    let mut input = 0_u64;
    let mut cache_read = 0_u64;
    let mut cache_write = 0_u64;
    let mut output = 0_u64;
    let mut events = 0_u64;
    let mut model_tokens: BTreeMap<String, u64> = BTreeMap::new();
    let mut best_ms = i64::MIN;
    let mut best_text = String::new();
    for entry in entries {
        let usage = entry.data.message.usage;
        let write = usage.cache_creation_token_count();
        input = input.saturating_add(usage.input_tokens);
        cache_read = cache_read.saturating_add(usage.cache_read_input_tokens);
        cache_write = cache_write.saturating_add(write);
        output = output.saturating_add(usage.output_tokens);
        let row = usage
            .input_tokens
            .saturating_add(usage.cache_read_input_tokens)
            .saturating_add(write)
            .saturating_add(usage.output_tokens);
        if let Some(model) = entry
            .model
            .as_deref()
            .or(entry.data.message.model.as_deref())
            .filter(|model| !model.is_empty())
        {
            *model_tokens.entry(model.to_string()).or_default() += row;
        }
        let counted = entry.message_count.filter(|count| *count > 0).unwrap_or(1);
        events = events.saturating_add(counted);
        let ms = entry.timestamp.as_millis();
        if ms >= best_ms {
            best_ms = ms;
            best_text = entry.data.timestamp.clone();
        }
    }
    let (cost_amount, cost_source, unpriced_models) = session_cost(entries, pricing, copilot);
    ProjectedSession {
        id,
        model: dominant(&model_tokens),
        recorded_at: stamp_text(&best_text, best_ms),
        events: events.max(1),
        input,
        cache_read,
        cache_write,
        output,
        total: input
            .saturating_add(cache_read)
            .saturating_add(cache_write)
            .saturating_add(output),
        cost_amount,
        cost_source,
        unpriced_models,
    }
}

fn session_cost(
    entries: &[&LoadedEntry],
    pricing: &PricingMap,
    copilot: bool,
) -> (Option<f64>, String, Vec<String>) {
    let mut unpriced = BTreeSet::new();
    for entry in entries {
        if let Some(model) = &entry.missing_pricing_model {
            unpriced.insert(model.clone());
        }
    }
    if !unpriced.is_empty() {
        return (
            None,
            "estimated:ccusage".to_string(),
            unpriced.into_iter().collect(),
        );
    }
    if copilot {
        return copilot_cost(entries, pricing);
    }
    if !entries.is_empty() && entries.iter().all(|entry| entry.stored_cost.is_some()) {
        let amount = entries.iter().filter_map(|entry| entry.stored_cost).sum();
        return (Some(amount), "provider_reported".to_string(), Vec::new());
    }
    let amount = entries.iter().map(|entry| entry.cost).sum();
    (Some(amount), "estimated:ccusage".to_string(), Vec::new())
}

/// A Copilot shutdown entry is one session's sum, so the context tier must
/// not see it. Credit-gap rows keep the provider's stored amount.
fn copilot_cost(
    entries: &[&LoadedEntry],
    pricing: &PricingMap,
) -> (Option<f64>, String, Vec<String>) {
    let mut amount = 0.0;
    let mut estimated = false;
    let mut unpriced = BTreeSet::new();
    for entry in entries {
        if let Some(stored) = entry.stored_cost {
            amount += stored;
            continue;
        }
        estimated = true;
        match base_from_entry(entry, pricing) {
            Some(cost) => amount += cost,
            None => {
                unpriced.insert(entry_model(entry));
            }
        }
    }
    if !unpriced.is_empty() {
        return (
            None,
            "estimated:ccusage".to_string(),
            unpriced.into_iter().collect(),
        );
    }
    let source = if estimated {
        "estimated:ccusage"
    } else {
        "provider_reported"
    };
    (Some(amount), source.to_string(), Vec::new())
}

fn base_from_entry(entry: &LoadedEntry, pricing: &PricingMap) -> Option<f64> {
    let model = entry
        .model
        .as_deref()
        .or(entry.data.message.model.as_deref())?;
    let price = pricing
        .find_at(model, entry.timestamp)
        .or_else(|| pricing.find(model))?;
    let usage = entry.data.message.usage;
    Some(priced(
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_input_tokens,
        usage.cache_creation_token_count(),
        &price,
    ))
}

fn priced(input: u64, output: u64, cache_read: u64, cache_write: u64, price: &Pricing) -> f64 {
    input as f64 * price.input
        + output as f64 * price.output
        + cache_read as f64 * price.cache_read
        + cache_write as f64 * price.cache_creation_input_token_cost()
}

fn entry_model(entry: &LoadedEntry) -> String {
    entry
        .model
        .clone()
        .or(entry.data.message.model.clone())
        .filter(|model| !model.is_empty())
        .unwrap_or_else(|| "unknown".to_string())
}

fn dominant(tokens: &BTreeMap<String, u64>) -> String {
    tokens
        .iter()
        .max_by(|left, right| left.1.cmp(right.1).then_with(|| left.0.cmp(right.0)))
        .map(|(model, _)| model.clone())
        .unwrap_or_default()
}

fn session_key(agent: &str, raw: &str) -> String {
    let leaf = raw.rsplit(['/', '\\']).next().unwrap_or(raw);
    let leaf = leaf.strip_suffix(".jsonl").unwrap_or(leaf);
    let leaf = leaf.strip_suffix(".json").unwrap_or(leaf);
    let leaf = leaf.strip_suffix(".zst").unwrap_or(leaf);
    if agent == "codex" {
        thread_uuid(leaf).unwrap_or_else(|| leaf.to_string())
    } else {
        leaf.to_string()
    }
}

fn thread_uuid(name: &str) -> Option<String> {
    let bytes = name.as_bytes();
    if bytes.len() < 36 {
        return None;
    }
    let start = bytes.len() - 36;
    let candidate = &name[start..];
    if !is_uuid(candidate) {
        return None;
    }
    if start > 0 && bytes[start - 1] != b'-' && bytes[start - 1] != b'/' {
        return None;
    }
    Some(candidate.to_string())
}

fn is_uuid(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() == 36
        && bytes.iter().enumerate().all(|(index, byte)| match index {
            8 | 13 | 18 | 23 => *byte == b'-',
            _ => byte.is_ascii_hexdigit(),
        })
}

fn stamp_text(text: &str, millis: i64) -> String {
    if DateTime::parse_from_rfc3339(text).is_ok() {
        return text.to_string();
    }
    let Some(stamp) = DateTime::from_timestamp(
        millis.div_euclid(1000),
        u32::try_from(millis.rem_euclid(1000)).unwrap_or(0) * 1_000_000,
    ) else {
        return "1970-01-01T00:00:00Z".to_string();
    };
    stamp.to_rfc3339_opts(SecondsFormat::Secs, true)
}

fn write_atomic(dir: &Path, bytes: &[u8]) -> bool {
    if fs::create_dir_all(dir).is_err() {
        return false;
    }
    let target = dir.join(PROJECTION_FILE);
    let temporary = dir.join(".ccusage-sessions.json.tmp");
    if fs::write(&temporary, bytes).is_err() {
        return false;
    }
    if fs::rename(&temporary, &target).is_err() {
        let _ = fs::remove_file(&temporary);
        return false;
    }
    true
}

fn install_snapshots() {
    static INSTALLED: OnceLock<()> = OnceLock::new();
    INSTALLED.get_or_init(|| {
        ccusage_core::pricing::set_embedded_snapshots(EmbeddedSnapshots {
            litellm_pricing: include_bytes!("../pricing/litellm-pricing.json.deflate"),
            models_dev_pricing: include_bytes!("../pricing/models-dev-pricing.json.deflate"),
            models_dev_catalog_rules: include_bytes!(
                "../pricing/models-dev-catalog-rules.json.deflate"
            ),
        });
    });
}

fn pricing_map() -> &'static PricingMap {
    static MAP: OnceLock<PricingMap> = OnceLock::new();
    MAP.get_or_init(|| {
        install_snapshots();
        let shared = shared_args();
        PricingMap::load_with_overrides(true, false, shared.pricing_overrides.iter())
    })
}

fn shared_args() -> SharedArgs {
    let mut shared = SharedArgs::with_defaults();
    shared.json = true;
    shared.offline = true;
    shared.single_thread = true;
    shared.mode = CostMode::Auto;
    let rate = k3_rate();
    // `k3` is tried after `moonshot/k3` and `kimi/k3`. An exact override on
    // each of those ids stops the fuzzy fallthrough to `kimi-k3-fast`.
    for model in ["k3", "moonshot/k3", "kimi/k3"] {
        shared
            .pricing_overrides
            .insert(model.to_string(), rate.clone());
    }
    shared
}

/// Moonshot's published k3 rates, per token: input 3, cache read 0.3, output 15
/// dollars per million. Cache creation follows the 1.25× input default.
fn k3_rate() -> PricingOverride {
    PricingOverride {
        input_cost_per_token: Some(3e-6),
        output_cost_per_token: Some(15e-6),
        cache_read_input_token_cost: Some(0.3e-6),
        cache_creation_input_token_cost: Some(3.75e-6),
        ..PricingOverride::default()
    }
}

fn store_present(provider: Provider) -> bool {
    match provider {
        Provider::Claude => claude_store_present(),
        Provider::Codex => named_store("CODEX_HOME", ".codex"),
        Provider::Grok => named_store("GROK_HOME", ".grok"),
        Provider::Kimi => kimi_store_present(),
        Provider::Copilot => named_store("COPILOT_HOME", ".copilot"),
        Provider::Agy => agy_store_present(),
    }
}

fn claude_store_present() -> bool {
    if let Ok(raw) = std::env::var("CLAUDE_CONFIG_DIR") {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return trimmed
                .split(',')
                .map(str::trim)
                .filter(|path| !path.is_empty())
                .any(claude_dir_has_projects);
        }
    }
    let Some(home) = ccusage_core::home::home_dir() else {
        return false;
    };
    let xdg = std::env::var("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| home.join(".config"));
    [xdg.join("claude"), home.join(".claude")]
        .into_iter()
        .any(|path| path.join("projects").is_dir())
}

fn claude_dir_has_projects(raw: &str) -> bool {
    let path = PathBuf::from(raw);
    if path.file_name().is_some_and(|name| name == "projects") && path.is_dir() {
        return true;
    }
    path.join("projects").is_dir()
}

fn named_store(variable: &str, home_dir_name: &str) -> bool {
    if let Ok(raw) = std::env::var(variable) {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return trimmed
                .split(',')
                .map(str::trim)
                .filter(|path| !path.is_empty())
                .any(|path| Path::new(path).is_dir());
        }
    }
    ccusage_core::home::home_dir().is_some_and(|home| home.join(home_dir_name).is_dir())
}

fn kimi_store_present() -> bool {
    if let Ok(raw) = std::env::var("KIMI_DATA_DIR") {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return trimmed
                .split(',')
                .map(str::trim)
                .filter(|path| !path.is_empty())
                .any(|path| Path::new(path).is_dir());
        }
    }
    ccusage_core::home::home_dir()
        .is_some_and(|home| home.join(".kimi").is_dir() || home.join(".kimi-code").is_dir())
}

fn agy_store_present() -> bool {
    if let Ok(raw) = std::env::var("ANTIGRAVITY_DATA_DIR") {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return trimmed
                .split(',')
                .map(str::trim)
                .filter(|path| !path.is_empty())
                .any(|path| Path::new(path).is_dir());
        }
    }
    const ROOTS: [&str; 6] = [
        ".gemini/antigravity",
        ".gemini/antigravity-cli",
        ".gemini/antigravity-ide",
        ".gemini/antigravity-backup",
        ".gemini/antigravity-acp",
        ".config/antigravity",
    ];
    ccusage_core::home::home_dir()
        .is_some_and(|home| ROOTS.iter().any(|root| home.join(root).is_dir()))
}
