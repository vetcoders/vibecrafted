//! Typed model of the Vibecrafted control plane.
//!
//! This is a field-for-field Rust mirror of the canonical Python writer
//! `vibecrafted-core/vibecrafted_core/control_plane.py`. The Python side is the
//! source of truth that *writes* `~/.vibecrafted/control_plane/`; this crate
//! only ever *reads* it. Where the Python derivation logic matters (state
//! classes, `health`, skill-code mapping, the three merge sources), it is
//! ported here so a Rust frontend never has to re-shell into Python.
//!
//! Drift policy: the on-disk JSON is runtime truth. When Python type hints and
//! the JSON disagree, fidelity tracks the JSON. Known divergences are recorded
//! in `docs/superpowers/specs/2026-05-31-control-core-design.md` and exercised
//! by `tests/schema_fidelity.rs`.
//!
//! Delivery-proof axes (`execution_state` / `proof_state` / `delivery_state`)
//! are a **read projection** of the Python kernel
//! (`vibecrafted_core.delivery.model` + `control_plane.DeliveryAxes`). This
//! crate never runs proof, never seals, and never invents `delivery_state`
//! from `completed` or `artifact_ok` (DELIVERY_PROOF_KERNEL_v1 §16/§17).

use std::collections::BTreeMap;
use std::path::Path;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// Delivery-proof kernel axes — 1:1 with vibecrafted_core.delivery.model
// ---------------------------------------------------------------------------

/// Process-execution axis. Wire strings match `ExecutionState` in
/// `vibecrafted_core.delivery.model` exactly.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionState {
    Created,
    Launched,
    Running,
    Exited,
    Interrupted,
    TimedOut,
    Failed,
}

impl ExecutionState {
    /// Canonical on-wire string (Python `.value`).
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            ExecutionState::Created => "created",
            ExecutionState::Launched => "launched",
            ExecutionState::Running => "running",
            ExecutionState::Exited => "exited",
            ExecutionState::Interrupted => "interrupted",
            ExecutionState::TimedOut => "timed_out",
            ExecutionState::Failed => "failed",
        }
    }
}

/// Proof axis. Wire strings match `ProofState` in
/// `vibecrafted_core.delivery.model` exactly.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProofState {
    Undeclared,
    Declared,
    Running,
    Passed,
    Failed,
    Invalid,
    Stale,
}

impl ProofState {
    /// Canonical on-wire string (Python `.value`).
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            ProofState::Undeclared => "undeclared",
            ProofState::Declared => "declared",
            ProofState::Running => "running",
            ProofState::Passed => "passed",
            ProofState::Failed => "failed",
            ProofState::Invalid => "invalid",
            ProofState::Stale => "stale",
        }
    }
}

/// Delivery axis. Wire strings match `DeliveryState` in
/// `vibecrafted_core.delivery.model` exactly.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryState {
    Unverified,
    Delivered,
    Sealed,
    Invalidated,
}

impl DeliveryState {
    /// Canonical on-wire string (Python `.value`).
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            DeliveryState::Unverified => "unverified",
            DeliveryState::Delivered => "delivered",
            DeliveryState::Sealed => "sealed",
            DeliveryState::Invalidated => "invalidated",
        }
    }
}

/// Orthogonal triple projected by `control_plane.DeliveryAxes.to_payload()`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeliveryAxes {
    pub execution_state: ExecutionState,
    pub proof_state: ProofState,
    pub delivery_state: DeliveryState,
}

/// Compact seal section from a kernel `delivery-seal.json` (identity fields).
///
/// Full seal contracts stay Python-owned; this is a read projection of the
/// fields an observer needs, not a seal issuer or verifier (§17).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct DeliverySealRef {
    #[serde(default)]
    pub schema: String,
    #[serde(default)]
    pub seal_id: String,
    #[serde(default)]
    pub issued_at: String,
    #[serde(default)]
    pub issuer: String,
    #[serde(default)]
    pub run_id: String,
    #[serde(default)]
    pub lifecycle_id: String,
    #[serde(default)]
    pub cut_id: String,
    #[serde(default)]
    pub proof_id: String,
    #[serde(default)]
    pub repo: String,
    #[serde(default)]
    pub branch: String,
    #[serde(default)]
    pub final_head: String,
    #[serde(default)]
    pub report_sha256: String,
}

/// Project legacy lifecycle/receipt truth onto the three independent axes.
///
/// Mirrors `lifecycle_runner.delivery_axes_for_receipt`:
/// - explicit axis fields win when present;
/// - otherwise `status` supplies **execution** only (`completed` → `exited`);
/// - proof defaults to `undeclared`, delivery to `unverified`;
/// - **never** promotes `completed` / `artifact_ok` into `delivered`/`sealed`.
#[must_use]
pub fn delivery_axes_for_receipt(
    status: &str,
    execution_state: Option<ExecutionState>,
    proof_state: Option<ProofState>,
    delivery_state: Option<DeliveryState>,
) -> DeliveryAxes {
    // Unknown / mid-flight statuses must not collapse to Failed — that lied about
    // lifecycle stages still in progress (promise, confirmed, initialized, …).
    let execution_default = match status {
        "created" | "initialized" => ExecutionState::Created,
        "launching" | "process_spawned" | "first_output_seen" => ExecutionState::Launched,
        "running"
        | "active"
        | "artifact_seen"
        | "report_started"
        | "promise"
        | "confirmed"
        | "paused"
        | "stalled" => ExecutionState::Running,
        "completed" | "closed" | "converged" | "report_validated" => ExecutionState::Exited,
        "interrupted" | "stopped" | "killed_by_operator" | "quota_exhausted" => {
            ExecutionState::Interrupted
        }
        "timed_out" => ExecutionState::TimedOut,
        "failed"
        | "blocked"
        | "contract_failed"
        | "report_missing"
        | "report_invalid"
        | "recovery_required"
        | "gc"
        | "ghost"
        | "process_dead" => ExecutionState::Failed,
        // Prefer Running over Failed for forward-compatible free-form states.
        _ => ExecutionState::Running,
    };
    DeliveryAxes {
        execution_state: execution_state.unwrap_or(execution_default),
        proof_state: proof_state.unwrap_or(ProofState::Undeclared),
        delivery_state: delivery_state.unwrap_or(DeliveryState::Unverified),
    }
}

/// Stall threshold in seconds. Mirrors `control_plane.RUN_STALL_SECONDS`
/// (`20 * 60`). A non-final run whose `updated_at` is older than this is
/// `Health::Stalled`.
pub const RUN_STALL_SECONDS: i64 = 1200;

/// Default number of events returned by an event tail. Mirrors
/// `control_plane.EVENT_TAIL_LIMIT`.
pub const EVENT_TAIL_LIMIT: usize = 16;

/// Number of most-recent runs surfaced in a state view. Mirrors
/// `control_plane.RECENT_RUN_LIMIT`.
pub const RECENT_RUN_LIMIT: usize = 12;

/// States that count as "in flight". Mirrors `control_plane.ACTIVE_STATES`.
pub const ACTIVE_STATES: [&str; 13] = [
    "created",
    "process_spawned",
    "first_output_seen",
    "active",
    "artifact_seen",
    "report_started",
    "initialized",
    "launching",
    "promise",
    "confirmed",
    "running",
    "paused",
    "stalled",
];

/// Terminal states. Mirrors `control_plane.FINAL_STATES`.
pub const FINAL_STATES: [&str; 15] = [
    "report_validated",
    "completed",
    "closed",
    "converged",
    "stopped",
    "blocked",
    "failed",
    "report_missing",
    "report_invalid",
    "contract_failed",
    "recovery_required",
    "timed_out",
    "quota_exhausted",
    "gc",
    "ghost",
];

/// Skill-code → skill-name map. Mirrors `control_plane.SKILL_CODE_MAP`
/// exactly (18 entries). Unknown codes such as `owne` deliberately fall
/// through to the code itself, matching the Python default.
pub const SKILL_CODE_MAP: [(&str, &str); 18] = [
    ("agnt", "agents"),
    ("deco", "decorate"),
    ("delg", "delegate"),
    ("vdou", "dou"),
    ("fwup", "followup"),
    ("hydr", "hydrate"),
    ("impl", "implement"),
    ("init", "init"),
    ("just", "justdo"),
    ("marb", "marbles"),
    ("prtn", "partner"),
    ("plan", "plan"),
    ("prun", "prune"),
    ("rels", "release"),
    ("rsch", "research"),
    ("rvew", "review"),
    ("scaf", "scaffold"),
    ("wflw", "workflow"),
];

/// Returns `true` when `state` is one of [`ACTIVE_STATES`].
#[must_use]
pub fn is_active_state(state: &str) -> bool {
    ACTIVE_STATES.contains(&state)
}

/// Returns `true` when `state` is one of [`FINAL_STATES`].
#[must_use]
pub fn is_final_state(state: &str) -> bool {
    FINAL_STATES.contains(&state)
}

/// Coarse classification of a run state.
///
/// The on-disk `state` field is a free-form string (forward-compatible with
/// states this build does not know), so this is a *derived* view rather than a
/// lossy enum sitting in [`RunStatus`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StateClass {
    /// One of [`ACTIVE_STATES`].
    Active,
    /// One of [`FINAL_STATES`].
    Final,
    /// A state string this build does not recognise.
    Unknown,
}

/// Classify a raw state string into [`StateClass`].
#[must_use]
pub fn classify_state(state: &str) -> StateClass {
    if is_final_state(state) {
        StateClass::Final
    } else if is_active_state(state) {
        StateClass::Active
    } else {
        StateClass::Unknown
    }
}

/// Derived health of a run. Mirrors the string values written by
/// `control_plane._state_health` (`"final" | "unknown" | "stalled" | "active"`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Health {
    /// Run is in a terminal state.
    Final,
    /// No parseable `updated_at`, so liveness is unknown.
    Unknown,
    /// Non-final but older than [`RUN_STALL_SECONDS`].
    Stalled,
    /// Non-final and recently updated.
    Active,
}

impl Health {
    /// The lowercase string form, matching the Python on-disk value.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Health::Final => "final",
            Health::Unknown => "unknown",
            Health::Stalled => "stalled",
            Health::Active => "active",
        }
    }
}

/// Parse an ISO-8601 / RFC-3339 timestamp the way `control_plane._parse_iso`
/// does. Returns `None` for empty or unparseable input. A trailing `Z` is
/// accepted (chrono handles it natively, matching the Python `Z` → `+00:00`
/// rewrite).
#[must_use]
pub fn parse_iso(raw: &str) -> Option<DateTime<Utc>> {
    if raw.is_empty() {
        return None;
    }
    DateTime::parse_from_rfc3339(raw)
        .ok()
        .map(|dt| dt.with_timezone(&Utc))
}

/// Coerce a JSON value to an `i64` the way `control_plane._coerce_int` does:
/// booleans are rejected, numbers pass through, digit-strings (optionally
/// sign-prefixed) parse, everything else is `None`.
#[must_use]
pub fn coerce_int_value(value: &serde_json::Value) -> Option<i64> {
    match value {
        serde_json::Value::Bool(_) => None,
        serde_json::Value::Number(n) => n.as_i64(),
        serde_json::Value::String(s) => {
            let trimmed = s.trim();
            let body = trimmed.strip_prefix('-').unwrap_or(trimmed);
            if !trimmed.is_empty() && !body.is_empty() && body.bytes().all(|b| b.is_ascii_digit()) {
                trimmed.parse::<i64>().ok()
            } else {
                None
            }
        }
        _ => None,
    }
}

/// Health derivation. Mirrors `control_plane._state_health`, but takes `now`
/// explicitly so callers (and tests) control the clock.
#[must_use]
pub fn state_health(state: &str, updated_at: &str, now: DateTime<Utc>) -> Health {
    if is_final_state(state) {
        return Health::Final;
    }
    match parse_iso(updated_at) {
        None => Health::Unknown,
        Some(updated) => {
            if (now - updated).num_seconds() > RUN_STALL_SECONDS {
                Health::Stalled
            } else {
                Health::Active
            }
        }
    }
}

/// Map a skill code to its long name. Mirrors `control_plane._skill_from_code`:
/// known code → mapped name; unknown non-empty code → the code itself; empty →
/// `"unknown"`.
#[must_use]
pub fn skill_from_code(skill_code: &str) -> String {
    for (code, name) in SKILL_CODE_MAP {
        if code == skill_code {
            return name.to_string();
        }
    }
    if skill_code.is_empty() {
        "unknown".to_string()
    } else {
        skill_code.to_string()
    }
}

fn session_base_name(root: &str) -> String {
    let source = if root.is_empty() { "vibecrafted" } else { root };
    let base = Path::new(source)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("vibecrafted")
        .to_lowercase();
    let cleaned: String = base
        .chars()
        .map(|c| if c.is_alphanumeric() { c } else { '-' })
        .collect();
    let trimmed = cleaned.trim_matches('-');
    if trimmed.is_empty() {
        "vibecrafted".to_string()
    } else {
        trimmed.to_string()
    }
}

/// Derive the operator tmux/session name for a run. Mirrors
/// `control_plane.operator_session_name`.
#[must_use]
pub fn operator_session_name(root: &str, run_id: &str) -> String {
    let base = session_base_name(root);
    if run_id.is_empty() {
        base
    } else {
        format!("{base}-{run_id}")
    }
}

fn nonempty_or(value: &str, fallback: &str) -> String {
    if value.is_empty() {
        fallback.to_string()
    } else {
        value.to_string()
    }
}

fn is_false(value: &bool) -> bool {
    !*value
}

/// Explicit native-agent identity that may be usable for an in-place resume.
///
/// This is evidence, not permission. The guardian still owns policy checks
/// such as manual-stop provenance, settlement, quiescence, runtime capability,
/// and retry budget before acting.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NativeResumeCandidate {
    pub agent: String,
    pub agent_session_id: String,
}

/// Action capabilities projected for a run.
///
/// `retry` is a cold redispatch from the retained launch specification.
/// `native_resume_candidate` is deliberately separate and is sourced only from
/// an explicit native agent session id. The legacy `RunStatus::session_id`
/// field is never promoted into this candidate.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunControls {
    #[serde(rename = "await")]
    pub await_run: bool,
    pub stop: bool,
    pub retry: bool,
    #[serde(default)]
    pub native_resume_candidate: Option<NativeResumeCandidate>,
}

/// A control-plane run projection.
///
/// The durable fields mirror `control_plane.RunStatus` plus retained snapshot
/// metadata. [`crate::read::ControlPlane`] then adds read-only process evidence
/// and typed controls without mutating the Python-owned files.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunStatus {
    pub run_id: String,
    pub state: String,
    pub agent: String,
    pub skill: String,
    pub mode: String,
    pub root: String,
    /// Exact commit identity projected independently from the trust receipt.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub commit_sha: String,
    pub operator_session: String,
    pub latest_report: String,
    pub latest_transcript: String,
    pub last_error: String,
    pub updated_at: String,
    pub started_at: String,
    pub health: String,
    pub source: String,
    pub lock_present: bool,
    #[serde(default)]
    pub exit_code: Option<i64>,
    #[serde(default)]
    pub liveness: String,
    #[serde(default)]
    pub launcher_pid: Option<i64>,
    #[serde(default)]
    pub completed_at: String,
    #[serde(default)]
    pub session_id: String,
    #[serde(default)]
    pub current_loop: Option<i64>,
    #[serde(default)]
    pub total_loops: Option<i64>,
    /// Explicit Vibecrafted lifecycle owner for supervised interactive runs.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub owner_pid: Option<i64>,
    /// Durable worker process identity from supervisor metadata.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub worker_pid: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub worker_pgid: Option<i64>,
    /// Observed worker liveness. Single-run reads refresh it from process
    /// identity; bulk reads retain the writer observation (or `None`) to avoid
    /// an N-process probe storm.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub worker_alive: Option<bool>,
    #[serde(default, skip_serializing_if = "is_false")]
    pub recovery_required: bool,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub stop_reason: String,
    /// Native agent session identity emitted explicitly by the runtime.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub agent_session_id: String,
    /// Vibecrafted supervisor/runtime session identity. This is not an agent
    /// resume identity.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub runtime_session_id: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub resume_of: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub attempt: Option<u64>,
    /// Persisted Python settlement verdict. Absent on legacy and live snapshots.
    /// Unknown future values are treated as absent, matching
    /// `settlement_from_payload()` rather than rejecting the whole snapshot.
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "de_optional_settlement_verdict"
    )]
    pub settlement_verdict: Option<SettlementVerdict>,
    /// Persisted Python TUI cell. Carried for schema fidelity; board totals are
    /// mapped from `settlement_verdict`, never inferred from process/proof data.
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "de_optional_settlement_tui"
    )]
    pub settlement_tui: Option<SettlementTui>,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub settlement_reason: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub settlement_source: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub settlement_at: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub settlement_claim_digest: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub settlement_waived: Option<bool>,
    /// Monotonic revision of the complete settlement fingerprint.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub settlement_revision: Option<u64>,
    /// Exact vc-trust resume authority receipt. Absent on legacy settlements.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trust_receipt: Option<TrustReceiptV1>,
    /// Read-model action projection. Old snapshots may omit it on disk; every
    /// ControlPlane read path materialises it before returning a run.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub controls: Option<RunControls>,
    /// Process axis from the delivery-proof kernel. Absent on pre-kernel
    /// snapshots — never invented from `state`/`completed` at read time.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub execution_state: Option<ExecutionState>,
    /// Proof axis. Absent when the snapshot has no delivery section.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub proof_state: Option<ProofState>,
    /// Delivery axis. Absent when the snapshot has no delivery section.
    /// Never derived from `completed` or `artifact_ok`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delivery_state: Option<DeliveryState>,
    /// Optional seal identity projection from `delivery-seal.json`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seal: Option<DeliverySealRef>,
}

impl RunStatus {
    /// `true` when this run is terminal. Mirrors `control_plane._run_is_terminal`:
    /// a final state, a `terminal` liveness, or any present `exit_code`.
    #[must_use]
    pub fn is_terminal(&self) -> bool {
        is_final_state(&self.state) || self.liveness == "terminal" || self.exit_code.is_some()
    }

    /// Classify this run's `state`.
    #[must_use]
    pub fn state_class(&self) -> StateClass {
        classify_state(&self.state)
    }

    /// Bundle present axis fields, or `None` when the snapshot has no delivery
    /// section at all (legacy runs).
    #[must_use]
    pub fn delivery_axes(&self) -> Option<DeliveryAxes> {
        match (self.execution_state, self.proof_state, self.delivery_state) {
            (Some(execution_state), Some(proof_state), Some(delivery_state)) => {
                Some(DeliveryAxes {
                    execution_state,
                    proof_state,
                    delivery_state,
                })
            }
            _ => None,
        }
    }

    /// Build native-resume evidence from explicit runtime identity only.
    ///
    /// `session_id` is intentionally absent from this function: historically
    /// that field has represented both runtime and agent identities.
    #[must_use]
    pub fn native_resume_candidate(&self) -> Option<NativeResumeCandidate> {
        let agent = self.agent.trim();
        let agent_session_id = self.agent_session_id.trim();
        let missing_agent_session = matches!(
            agent_session_id.to_ascii_lowercase().as_str(),
            "" | "pending" | "none" | "null" | "unknown"
        );
        if agent.is_empty() || agent.eq_ignore_ascii_case("unknown") || missing_agent_session {
            return None;
        }
        Some(NativeResumeCandidate {
            agent: agent.to_string(),
            agent_session_id: agent_session_id.to_string(),
        })
    }

    /// Materialise the action projection while keeping retry and native resume
    /// as separate mechanisms.
    pub fn set_controls(&mut self, await_run: bool, stop: bool, retry: bool) {
        self.controls = Some(RunControls {
            await_run,
            stop,
            retry,
            native_resume_candidate: self.native_resume_candidate(),
        });
    }
}

/// Python-owned terminal verdict written into retained run snapshots.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SettlementVerdict {
    Finalized,
    Failed,
    NeedsAttention,
    Invalid,
}

impl SettlementVerdict {
    /// Canonical f/x/n projection from `settlement.tui_key_for`.
    #[must_use]
    pub fn tui(self) -> SettlementTui {
        match self {
            Self::Finalized => SettlementTui::F,
            Self::Failed | Self::Invalid => SettlementTui::X,
            Self::NeedsAttention => SettlementTui::N,
        }
    }
}

/// Persisted one-character settlement cell written by Python.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SettlementTui {
    F,
    X,
    N,
}

/// Explicit vc-trust verdict bound into a resume authority receipt.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum TrustVerdict {
    #[serde(rename = "pass")]
    Pass,
    #[serde(rename = "pass-with-gaps")]
    PassWithGaps,
    #[serde(rename = "block")]
    Block,
}

/// Typed projection of `vibecrafted.trust-receipt.v1`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TrustReceiptV1 {
    pub schema: String,
    pub receipt_id: String,
    pub repo_root: String,
    pub run_id: String,
    pub commit_sha: String,
    pub trust_verdict: TrustVerdict,
    pub settlement_verdict: SettlementVerdict,
    pub settlement_tui: SettlementTui,
    pub settlement_revision: u64,
    pub claim_digest: String,
}

/// Declared time/scope boundary for a settlement aggregate.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SettlementScope {
    /// Every snapshot currently retained under `control_plane/runs/`.
    RetainedControlPlaneSnapshots,
}

/// Typed f/x/n aggregate sourced only from persisted Python snapshots.
///
/// `invalid` is diagnostic detail inside `x`, not a fourth total bucket.
/// `unclassified` counts retained snapshots with no settlement verdict that
/// are not terminal enough to fall back to `n`.
/// `active` comes from the existing Rust live projection and is likewise not
/// part of `total_settled`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SettlementBoard {
    pub scope: SettlementScope,
    pub active: usize,
    pub f: usize,
    pub x: usize,
    pub n: usize,
    pub invalid: usize,
    pub unclassified: usize,
    pub total_settled: usize,
}

impl SettlementBoard {
    /// Count the Python settlement axis from retained snapshots.
    ///
    /// A missing/unreadable verdict contributes `n` only when Python's
    /// settlement terminal predicate would consider the snapshot terminal.
    /// Live unsettled snapshots are ignored. No exit/process/proof signal is
    /// ever promoted to `f`, `x`, or `invalid`.
    #[must_use]
    pub fn from_snapshots(runs: &[RunStatus]) -> Self {
        let mut board = Self {
            scope: SettlementScope::RetainedControlPlaneSnapshots,
            active: 0,
            f: 0,
            x: 0,
            n: 0,
            invalid: 0,
            unclassified: 0,
            total_settled: 0,
        };

        for run in runs {
            match run.settlement_verdict {
                Some(SettlementVerdict::Finalized) => board.f += 1,
                Some(SettlementVerdict::Failed) => board.x += 1,
                Some(SettlementVerdict::Invalid) => {
                    board.x += 1;
                    board.invalid += 1;
                }
                Some(SettlementVerdict::NeedsAttention) => board.n += 1,
                None if is_unsettled_settlement_terminal(run) => board.n += 1,
                None => board.unclassified += 1,
            }
        }
        board.total_settled = board.f + board.x + board.n;
        board
    }
}

fn is_unsettled_settlement_terminal(run: &RunStatus) -> bool {
    const TERMINAL_STATES: [&str; 18] = [
        "report_validated",
        "completed",
        "closed",
        "converged",
        "stopped",
        "blocked",
        "failed",
        "report_missing",
        "report_invalid",
        "contract_failed",
        "recovery_required",
        "timed_out",
        "quota_exhausted",
        "gc",
        "ghost",
        "stalled",
        "killed_by_operator",
        "process_dead",
    ];
    TERMINAL_STATES.contains(&run.state.as_str())
        || run.liveness == "terminal"
        || run.exit_code.is_some()
}

fn de_optional_settlement_verdict<'de, D>(
    deserializer: D,
) -> Result<Option<SettlementVerdict>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let value = serde_json::Value::deserialize(deserializer)?;
    Ok(
        match value
            .as_str()
            .unwrap_or_default()
            .trim()
            .to_lowercase()
            .as_str()
        {
            "finalized" => Some(SettlementVerdict::Finalized),
            "failed" => Some(SettlementVerdict::Failed),
            "needs_attention" => Some(SettlementVerdict::NeedsAttention),
            "invalid" => Some(SettlementVerdict::Invalid),
            _ => None,
        },
    )
}

fn de_optional_settlement_tui<'de, D>(deserializer: D) -> Result<Option<SettlementTui>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let value = serde_json::Value::deserialize(deserializer)?;
    Ok(
        match value
            .as_str()
            .unwrap_or_default()
            .trim()
            .to_lowercase()
            .as_str()
        {
            "f" => Some(SettlementTui::F),
            "x" => Some(SettlementTui::X),
            "n" => Some(SettlementTui::N),
            _ => None,
        },
    )
}

/// Nested lifecycle run state written by `lifecycle_runner.py`.
///
/// This intentionally sits beside [`RunStatus`]: lifecycle state is stageful and
/// nested, while `RunStatus` is the legacy flat dashboard/API projection.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LifecycleRun {
    #[serde(default)]
    pub schema: Option<String>,
    #[serde(default)]
    pub run_id: String,
    #[serde(default)]
    pub workflow: String,
    #[serde(default)]
    pub agent: String,
    #[serde(default)]
    pub root: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub await_stages: bool,
    #[serde(default)]
    pub parent_run_id: Option<String>,
    #[serde(default)]
    pub operator_actions: Vec<LifecycleOperatorAction>,
    #[serde(default)]
    pub spec: serde_json::Value,
    #[serde(default)]
    pub supervisor: String,
    #[serde(default)]
    pub human_controls: Vec<String>,
    #[serde(default)]
    pub state_path: String,
    #[serde(default)]
    pub report_path: String,
    #[serde(default)]
    pub transcript_path: String,
    #[serde(default)]
    pub context_atlas: serde_json::Value,
    #[serde(default)]
    pub manifest: serde_json::Value,
    #[serde(default)]
    pub baton: LifecycleBaton,
    #[serde(default)]
    pub stages: Vec<LifecycleStage>,
    #[serde(default)]
    pub next_stage: String,
    #[serde(default)]
    pub error: String,
    #[serde(default, deserialize_with = "de_optional_lifecycle_dou_index")]
    pub dou_index: Option<LifecycleDouIndex>,
    #[serde(default, deserialize_with = "de_nonnegative_int")]
    pub accepted_dou: Option<i64>,
    #[serde(default)]
    pub accepted_dou_findings: Vec<serde_json::Value>,
    /// Run-level execution axis (projected; not stored on disk historically).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub execution_state: Option<ExecutionState>,
    /// Run-level proof axis (projected).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub proof_state: Option<ProofState>,
    /// Run-level delivery axis (projected; never from `completed` alone).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delivery_state: Option<DeliveryState>,
}

impl LifecycleRun {
    /// Project delivery-proof axes onto this run and every stage.
    ///
    /// Mirrors `lifecycle_runner.write_lifecycle_report` / `delivery_axes_for_receipt`:
    /// explicit on-disk axis fields win; otherwise status supplies execution only.
    /// Idempotent when axes are already present.
    pub fn project_delivery_axes(&mut self) {
        let run_axes = delivery_axes_for_receipt(
            &self.status,
            self.execution_state,
            self.proof_state,
            self.delivery_state,
        );
        self.execution_state = Some(run_axes.execution_state);
        self.proof_state = Some(run_axes.proof_state);
        self.delivery_state = Some(run_axes.delivery_state);
        for stage in &mut self.stages {
            stage.project_delivery_axes();
        }
    }

    /// Build the compact read-model used by `/api/control/lifecycle` and the
    /// dashboard. `updated_at` is normally the `state.json` mtime because the
    /// lifecycle state file does not carry a canonical timestamp.
    #[must_use]
    pub fn summary(
        &self,
        updated_at: String,
        report_dou_index: Option<i64>,
    ) -> LifecycleRunSummary {
        let state_dou = self.dou_index.as_ref().and_then(|dou| dou.value);
        let stage_dou = self.stages.last().and_then(|stage| stage.dou_index);
        let dou_index = state_dou
            .or(report_dou_index)
            .or(self.baton.dou_index)
            .or(stage_dou);
        let accepted_dou = self
            .accepted_dou
            .unwrap_or(self.accepted_dou_findings.len() as i64);
        let dou_readiness = match dou_index {
            Some(0) => "zero",
            Some(_) => "open",
            None => "unknown",
        }
        .to_string();

        LifecycleRunSummary {
            schema: self.schema.clone(),
            run_id: self.run_id.clone(),
            workflow: self.workflow.clone(),
            status: nonempty_or(&self.status, "unknown"),
            agent: nonempty_or(&self.agent, "unknown"),
            root: self.root.clone(),
            current_stage: self.current_stage(),
            next_stage: nonempty_or(&self.baton.next_stage, &self.next_stage),
            next_agent: self.baton.next_agent.clone(),
            exit_code: self.stages.last().and_then(|stage| stage.await_exit_code()),
            dou_index,
            baton_dou_index: self.baton.dou_index,
            accepted_dou,
            dou_readiness,
            human_controls: self.human_controls.clone(),
            human_controls_count: self.human_controls.len(),
            operator_actions_count: self.operator_actions.len(),
            state_path: self.state_path.clone(),
            report_path: self.report_path.clone(),
            transcript_path: self.transcript_path.clone(),
            updated_at,
            source: "lifecycle_runs".to_string(),
        }
    }

    /// Lossy projection into the existing flat [`RunStatus`] surface.
    #[must_use]
    pub fn to_run_status(&self, updated_at: String, report_dou_index: Option<i64>) -> RunStatus {
        let summary = self.summary(updated_at.clone(), report_dou_index);
        let state = summary.status.clone();
        let health = state_health(&state, &updated_at, Utc::now());
        // Lifecycle container terminal follows overall workflow status only.
        // A prior stage's exit_code (even 0) must not finalize a still-running
        // multi-stage workflow — workers remain separate run_ids.
        let final_health = if is_final_state(&state) {
            Health::Final
        } else {
            health
        };
        // Only surface stage exit_code on the flat projection when the workflow
        // itself is terminal; otherwise leave None so is_terminal() does not
        // fire from a completed stage.
        let exit_code = if is_final_state(&state) {
            summary.exit_code
        } else {
            None
        };
        let axes = delivery_axes_for_receipt(
            &state,
            self.execution_state,
            self.proof_state,
            self.delivery_state,
        );

        let terminal = final_health == Health::Final;
        let mut status = RunStatus {
            run_id: summary.run_id,
            state,
            agent: summary.agent,
            skill: nonempty_or(&summary.workflow, "lifecycle"),
            mode: "lifecycle".to_string(),
            root: summary.root.clone(),
            commit_sha: String::new(),
            operator_session: operator_session_name(&summary.root, &self.run_id),
            latest_report: summary.report_path,
            latest_transcript: summary.transcript_path,
            last_error: self.error.clone(),
            updated_at: summary.updated_at.clone(),
            started_at: summary.updated_at,
            health: final_health.as_str().to_string(),
            source: summary.source,
            lock_present: false,
            exit_code,
            liveness: if final_health == Health::Final {
                "terminal".to_string()
            } else {
                String::new()
            },
            launcher_pid: None,
            completed_at: String::new(),
            session_id: String::new(),
            current_loop: None,
            total_loops: None,
            owner_pid: None,
            worker_pid: None,
            worker_pgid: None,
            worker_alive: None,
            recovery_required: false,
            stop_reason: String::new(),
            agent_session_id: String::new(),
            runtime_session_id: String::new(),
            resume_of: String::new(),
            attempt: None,
            settlement_verdict: None,
            settlement_tui: None,
            settlement_reason: String::new(),
            settlement_source: String::new(),
            settlement_at: String::new(),
            settlement_claim_digest: String::new(),
            settlement_waived: None,
            settlement_revision: None,
            trust_receipt: None,
            controls: None,
            execution_state: Some(axes.execution_state),
            proof_state: Some(axes.proof_state),
            delivery_state: Some(axes.delivery_state),
            seal: None,
        };
        status.set_controls(!terminal, false, false);
        status
    }

    fn current_stage(&self) -> String {
        self.stages
            .last()
            .map(|stage| stage.id.clone())
            .filter(|id| !id.is_empty())
            .unwrap_or_else(|| self.baton.from_stage.clone())
    }
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct LifecycleBaton {
    #[serde(default)]
    pub from_stage: String,
    #[serde(default)]
    pub from_phase: String,
    #[serde(default)]
    pub next_stage: String,
    #[serde(default)]
    pub next_agent: String,
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub previous_reports: Vec<String>,
    #[serde(default, deserialize_with = "de_nonnegative_int")]
    pub dou_index: Option<i64>,
    #[serde(default)]
    pub audit_after: String,
    #[serde(default)]
    pub fallback_stage: String,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct LifecycleStage {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub workflow: String,
    #[serde(default)]
    pub phase: String,
    #[serde(default)]
    pub agent: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub launch: serde_json::Value,
    #[serde(default, rename = "await")]
    pub await_result: serde_json::Value,
    #[serde(default)]
    pub commit_before: String,
    #[serde(default)]
    pub commit_after: String,
    #[serde(default)]
    pub changed_files: Vec<String>,
    #[serde(default)]
    pub new_commits: Vec<String>,
    #[serde(default)]
    pub transition: Option<LifecycleTransition>,
    #[serde(default, deserialize_with = "de_nonnegative_int")]
    pub dou_index: Option<i64>,
    /// Stage execution axis (projected from status / explicit fields).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub execution_state: Option<ExecutionState>,
    /// Stage proof axis (projected).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub proof_state: Option<ProofState>,
    /// Stage delivery axis (projected; never from `artifact_ok` alone).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delivery_state: Option<DeliveryState>,
}

impl LifecycleStage {
    fn await_exit_code(&self) -> Option<i64> {
        self.await_result
            .get("exit_code")
            .and_then(coerce_int_value)
    }

    /// Project the three axes for this stage (same rules as the run-level
    /// report lines in `write_lifecycle_report`).
    pub fn project_delivery_axes(&mut self) {
        let axes = delivery_axes_for_receipt(
            &self.status,
            self.execution_state,
            self.proof_state,
            self.delivery_state,
        );
        self.execution_state = Some(axes.execution_state);
        self.proof_state = Some(axes.proof_state);
        self.delivery_state = Some(axes.delivery_state);
    }
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct LifecycleTransition {
    #[serde(default)]
    pub next_stage: String,
    #[serde(default)]
    pub requested_next_stage: String,
    #[serde(default)]
    pub next_agent: String,
    #[serde(default)]
    pub requested_next_agent: String,
    #[serde(default)]
    pub conditions: Vec<String>,
    #[serde(default)]
    pub fallback_stage: String,
    #[serde(default)]
    pub audit_after: String,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct LifecycleOperatorAction {
    #[serde(default)]
    pub action: String,
    #[serde(default)]
    pub at: String,
    #[serde(default)]
    pub details: serde_json::Value,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct LifecycleDouIndex {
    #[serde(default, deserialize_with = "de_nonnegative_int")]
    pub value: Option<i64>,
    #[serde(default)]
    pub stage: String,
    #[serde(default)]
    pub report: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct LifecycleRunSummary {
    pub schema: Option<String>,
    pub run_id: String,
    pub workflow: String,
    pub status: String,
    pub agent: String,
    pub root: String,
    pub current_stage: String,
    pub next_stage: String,
    pub next_agent: String,
    pub exit_code: Option<i64>,
    pub dou_index: Option<i64>,
    pub baton_dou_index: Option<i64>,
    pub accepted_dou: i64,
    pub dou_readiness: String,
    pub human_controls: Vec<String>,
    pub human_controls_count: usize,
    pub operator_actions_count: usize,
    pub state_path: String,
    pub report_path: String,
    pub transcript_path: String,
    pub updated_at: String,
    pub source: String,
}

/// A control-plane event. Field-for-field mirror of `control_plane.Event`.
///
/// On disk in `events.jsonl` each line carries `ts/run_id/kind/message/payload`
/// but **not** `cursor` — the cursor is the byte offset assigned at read time
/// (see [`crate::events`]). Hence `cursor` defaults to `0` on deserialize and is
/// stamped by the reader.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Event {
    pub ts: String,
    pub run_id: String,
    pub kind: String,
    pub message: String,
    #[serde(default)]
    pub payload: BTreeMap<String, serde_json::Value>,
    #[serde(default)]
    pub cursor: u64,
}

/// Raw `*.meta.json` agent record — the richest of the three merge sources.
///
/// Field names differ from [`RunStatus`] (`status` not `state`, `skill_code`
/// not `skill`, `report`/`transcript` not `latest_*`), matching what the
/// launcher writes. Use [`AgentMeta::normalize`] to project into a
/// [`RunStatus`]; mirrors `control_plane._normalize_agent_meta`.
#[derive(Debug, Clone, Deserialize)]
pub struct AgentMeta {
    #[serde(default)]
    pub run_id: String,
    #[serde(default)]
    pub root: String,
    #[serde(default)]
    pub commit_sha: String,
    #[serde(default)]
    pub skill_code: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub updated_at: String,
    #[serde(default)]
    pub started_at: String,
    #[serde(default)]
    pub agent: String,
    #[serde(default)]
    pub mode: String,
    #[serde(default)]
    pub report: String,
    #[serde(default)]
    pub transcript: String,
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub reason: String,
    /// Present-but-`null` in flight; an integer once the process exits.
    #[serde(default, deserialize_with = "de_coerced_int")]
    pub exit_code: Option<i64>,
    #[serde(default)]
    pub liveness: String,
    #[serde(default, deserialize_with = "de_coerced_int")]
    pub launcher_pid: Option<i64>,
    #[serde(default)]
    pub completed_at: String,
    #[serde(default)]
    pub session_id: String,
    #[serde(default, deserialize_with = "de_coerced_int")]
    pub owner_pid: Option<i64>,
    #[serde(default, deserialize_with = "de_coerced_int")]
    pub worker_pid: Option<i64>,
    #[serde(default, deserialize_with = "de_coerced_int")]
    pub worker_pgid: Option<i64>,
    #[serde(default)]
    pub worker_alive: Option<bool>,
    #[serde(default)]
    pub recovery_required: bool,
    #[serde(default)]
    pub stop_reason: String,
    #[serde(default)]
    pub agent_session_id: String,
    #[serde(default)]
    pub runtime_session_id: String,
    #[serde(default)]
    pub resume_of: String,
    #[serde(default, deserialize_with = "de_optional_u64")]
    pub attempt: Option<u64>,
    #[serde(default)]
    pub prompt: String,
    #[serde(default)]
    pub file: String,
    #[serde(default, deserialize_with = "de_optional_settlement_verdict")]
    pub settlement_verdict: Option<SettlementVerdict>,
    #[serde(default, deserialize_with = "de_optional_settlement_tui")]
    pub settlement_tui: Option<SettlementTui>,
    #[serde(default)]
    pub settlement_reason: String,
    #[serde(default)]
    pub settlement_source: String,
    #[serde(default)]
    pub settlement_at: String,
    #[serde(default)]
    pub settlement_claim_digest: String,
    #[serde(default)]
    pub settlement_waived: Option<bool>,
    #[serde(default, deserialize_with = "de_optional_u64")]
    pub settlement_revision: Option<u64>,
    #[serde(default)]
    pub trust_receipt: Option<TrustReceiptV1>,
}

fn de_coerced_int<'de, D>(deserializer: D) -> Result<Option<i64>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let value = serde_json::Value::deserialize(deserializer)?;
    Ok(coerce_int_value(&value))
}

fn de_nonnegative_int<'de, D>(deserializer: D) -> Result<Option<i64>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let value = serde_json::Value::deserialize(deserializer)?;
    Ok(coerce_int_value(&value).filter(|value| *value >= 0))
}

fn de_optional_u64<'de, D>(deserializer: D) -> Result<Option<u64>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let value = serde_json::Value::deserialize(deserializer)?;
    Ok(match value {
        serde_json::Value::Number(number) => number.as_u64(),
        serde_json::Value::String(raw) => raw.trim().parse::<u64>().ok(),
        _ => None,
    })
}

fn de_optional_lifecycle_dou_index<'de, D>(
    deserializer: D,
) -> Result<Option<LifecycleDouIndex>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let value = serde_json::Value::deserialize(deserializer)?;
    if !value.is_object() {
        return Ok(None);
    }
    serde_json::from_value(value)
        .map(Some)
        .map_err(serde::de::Error::custom)
}

impl AgentMeta {
    /// Project into a [`RunStatus`] using `now` for health derivation.
    /// Returns `None` when `run_id` is blank (mirrors the Python guard).
    #[must_use]
    pub fn normalize(&self, now: DateTime<Utc>) -> Option<RunStatus> {
        let run_id = self.run_id.trim();
        if run_id.is_empty() {
            return None;
        }
        let state = nonempty_or(&self.status, "unknown");
        let exit_code = self.exit_code;
        let liveness = self.liveness.clone();
        let health = if exit_code.is_some() || liveness == "terminal" {
            Health::Final
        } else {
            state_health(&state, &self.updated_at, now)
        };
        let last_error = if self.message.is_empty() {
            self.reason.clone()
        } else {
            self.message.clone()
        };
        let started_at = if self.started_at.is_empty() {
            self.updated_at.clone()
        } else {
            self.started_at.clone()
        };
        let terminal = exit_code.is_some() || liveness == "terminal" || is_final_state(&state);
        let retry = terminal
            && (skill_from_code(&self.skill_code) == "marbles"
                || !self.prompt.trim().is_empty()
                || !self.file.trim().is_empty());
        let mut status = RunStatus {
            run_id: run_id.to_string(),
            state,
            agent: nonempty_or(&self.agent, "unknown"),
            skill: skill_from_code(&self.skill_code),
            mode: nonempty_or(&self.mode, "unknown"),
            root: self.root.clone(),
            commit_sha: self.commit_sha.clone(),
            operator_session: operator_session_name(&self.root, run_id),
            latest_report: self.report.clone(),
            latest_transcript: self.transcript.clone(),
            last_error,
            updated_at: self.updated_at.clone(),
            started_at,
            health: health.as_str().to_string(),
            source: "agent-meta".to_string(),
            lock_present: false,
            exit_code,
            liveness,
            launcher_pid: self.launcher_pid,
            completed_at: self.completed_at.clone(),
            session_id: self.session_id.clone(),
            current_loop: None,
            total_loops: None,
            owner_pid: self.owner_pid,
            worker_pid: self.worker_pid,
            worker_pgid: self.worker_pgid,
            worker_alive: self.worker_alive,
            recovery_required: self.recovery_required,
            stop_reason: self.stop_reason.clone(),
            agent_session_id: self.agent_session_id.clone(),
            runtime_session_id: self.runtime_session_id.clone(),
            resume_of: self.resume_of.clone(),
            attempt: self.attempt,
            settlement_verdict: self.settlement_verdict,
            settlement_tui: self.settlement_tui,
            settlement_reason: self.settlement_reason.clone(),
            settlement_source: self.settlement_source.clone(),
            settlement_at: self.settlement_at.clone(),
            settlement_claim_digest: self.settlement_claim_digest.clone(),
            settlement_waived: self.settlement_waived,
            settlement_revision: self.settlement_revision,
            trust_receipt: self.trust_receipt.clone(),
            controls: None,
            // Meta is not a delivery receipt; axes stay absent until a
            // snapshot or seal file provides them (never inferred here).
            execution_state: None,
            proof_state: None,
            delivery_state: None,
            seal: None,
        };
        status.set_controls(
            !terminal,
            !terminal && self.worker_alive == Some(true),
            retry,
        );
        Some(status)
    }
}

/// Merge two projections for the same `run_id`, preferring the newer
/// `updated_at`. Mirrors `control_plane._merge_status`: the newer record wins
/// field-by-field, missing values fall back to the other record, and
/// `lock_present` / `exit_code` are sticky.
#[must_use]
pub fn merge_status(existing: Option<RunStatus>, incoming: RunStatus) -> RunStatus {
    let Some(existing) = existing else {
        return incoming;
    };
    let existing_dt = parse_iso(&existing.updated_at);
    let incoming_dt = parse_iso(&incoming.updated_at);
    // Prefer existing when its timestamp is present and >= incoming's
    // (a missing incoming timestamp sorts as the epoch floor, like Python).
    let prefer_existing = match (existing_dt, incoming_dt) {
        (Some(e), Some(i)) => e >= i,
        (Some(_), None) => true,
        _ => false,
    };
    let (preferred, other) = if prefer_existing {
        (&existing, &incoming)
    } else {
        (&incoming, &existing)
    };

    let preferred_controls = preferred.controls.as_ref();
    let other_controls = other.controls.as_ref();
    let retry = preferred_controls.is_some_and(|controls| controls.retry)
        || other_controls.is_some_and(|controls| controls.retry);
    let settlement_owner = match (preferred.settlement_revision, other.settlement_revision) {
        (Some(preferred_revision), Some(other_revision)) if other_revision > preferred_revision => {
            other
        }
        (None, Some(_)) => other,
        (None, None) if preferred.settlement_verdict.is_none() => other,
        _ => preferred,
    };
    let mut merged = RunStatus {
        run_id: preferred.run_id.clone(),
        state: preferred.state.clone(),
        agent: nonempty_or(&preferred.agent, &other.agent),
        skill: nonempty_or(&preferred.skill, &other.skill),
        mode: nonempty_or(&preferred.mode, &other.mode),
        root: nonempty_or(&preferred.root, &other.root),
        commit_sha: nonempty_or(&preferred.commit_sha, &other.commit_sha),
        operator_session: nonempty_or(&preferred.operator_session, &other.operator_session),
        latest_report: nonempty_or(&preferred.latest_report, &other.latest_report),
        latest_transcript: nonempty_or(&preferred.latest_transcript, &other.latest_transcript),
        last_error: nonempty_or(&preferred.last_error, &other.last_error),
        updated_at: nonempty_or(&preferred.updated_at, &other.updated_at),
        started_at: nonempty_or(&preferred.started_at, &other.started_at),
        health: preferred.health.clone(),
        source: preferred.source.clone(),
        lock_present: existing.lock_present || incoming.lock_present,
        exit_code: preferred.exit_code.or(other.exit_code),
        liveness: nonempty_or(&preferred.liveness, &other.liveness),
        launcher_pid: preferred.launcher_pid.or(other.launcher_pid),
        completed_at: nonempty_or(&preferred.completed_at, &other.completed_at),
        session_id: nonempty_or(&preferred.session_id, &other.session_id),
        current_loop: preferred.current_loop.or(other.current_loop),
        total_loops: preferred.total_loops.or(other.total_loops),
        owner_pid: preferred.owner_pid.or(other.owner_pid),
        worker_pid: preferred.worker_pid.or(other.worker_pid),
        worker_pgid: preferred.worker_pgid.or(other.worker_pgid),
        worker_alive: preferred.worker_alive.or(other.worker_alive),
        recovery_required: preferred.recovery_required || other.recovery_required,
        stop_reason: nonempty_or(&preferred.stop_reason, &other.stop_reason),
        agent_session_id: nonempty_or(&preferred.agent_session_id, &other.agent_session_id),
        runtime_session_id: nonempty_or(&preferred.runtime_session_id, &other.runtime_session_id),
        resume_of: nonempty_or(&preferred.resume_of, &other.resume_of),
        attempt: preferred.attempt.or(other.attempt),
        settlement_verdict: settlement_owner.settlement_verdict,
        settlement_tui: settlement_owner.settlement_tui,
        settlement_reason: settlement_owner.settlement_reason.clone(),
        settlement_source: settlement_owner.settlement_source.clone(),
        settlement_at: settlement_owner.settlement_at.clone(),
        settlement_claim_digest: settlement_owner.settlement_claim_digest.clone(),
        settlement_waived: settlement_owner.settlement_waived,
        settlement_revision: settlement_owner.settlement_revision,
        trust_receipt: settlement_owner.trust_receipt.clone(),
        controls: None,
        // Prefer explicit axes; never synthesise from the other side's state.
        execution_state: preferred.execution_state.or(other.execution_state),
        proof_state: preferred.proof_state.or(other.proof_state),
        delivery_state: preferred.delivery_state.or(other.delivery_state),
        seal: preferred.seal.clone().or_else(|| other.seal.clone()),
    };
    let terminal = merged.is_terminal();
    let await_run = !terminal
        && preferred_controls
            .map(|controls| controls.await_run)
            .unwrap_or(true);
    let stop = !terminal
        && preferred_controls
            .map(|controls| controls.stop)
            .unwrap_or(merged.worker_alive == Some(true));
    merged.set_controls(await_run, stop, retry);
    merged
}


#[cfg(test)]
mod status_thread_tests {
    use super::*;

    #[test]
    fn delivery_axes_mid_flight_are_not_failed() {
        let axes = delivery_axes_for_receipt("promise", None, None, None);
        assert_eq!(axes.execution_state, ExecutionState::Running);
        let axes = delivery_axes_for_receipt("timed_out", None, None, None);
        assert_eq!(axes.execution_state, ExecutionState::TimedOut);
        let axes = delivery_axes_for_receipt("interrupted", None, None, None);
        assert_eq!(axes.execution_state, ExecutionState::Interrupted);
        let axes = delivery_axes_for_receipt("quota_exhausted", None, None, None);
        assert_eq!(axes.execution_state, ExecutionState::Interrupted);
        assert!(is_final_state("quota_exhausted"));
        let axes = delivery_axes_for_receipt("failed", None, None, None);
        assert_eq!(axes.execution_state, ExecutionState::Failed);
        let axes = delivery_axes_for_receipt("completed", None, None, None);
        assert_eq!(axes.execution_state, ExecutionState::Exited);
        assert_eq!(axes.proof_state, ProofState::Undeclared);
        assert_eq!(axes.delivery_state, DeliveryState::Unverified);
    }

    #[test]
    fn lifecycle_stage_exit_does_not_finalize_running_workflow() {
        let stage = LifecycleStage {
            id: "stage-a".into(),
            status: "completed".into(),
            await_result: serde_json::json!({ "exit_code": 0 }),
            ..Default::default()
        };

        let mut run = LifecycleRun {
            schema: None,
            run_id: "lc-1".into(),
            workflow: "ship".into(),
            agent: "claude".into(),
            root: "/tmp/x".into(),
            status: "running".into(),
            await_stages: true,
            parent_run_id: None,
            operator_actions: vec![],
            spec: serde_json::Value::Null,
            supervisor: String::new(),
            human_controls: vec![],
            state_path: String::new(),
            report_path: String::new(),
            transcript_path: String::new(),
            context_atlas: serde_json::Value::Null,
            manifest: serde_json::Value::Null,
            baton: LifecycleBaton {
                next_stage: "stage-b".into(),
                ..Default::default()
            },
            stages: vec![stage],
            next_stage: "stage-b".into(),
            error: String::new(),
            dou_index: None,
            accepted_dou: None,
            accepted_dou_findings: vec![],
            execution_state: None,
            proof_state: None,
            delivery_state: None,
        };
        // summary still exposes stage exit for observers of nested state
        let summary = run.summary("2026-08-03T12:00:00Z".into(), None);
        assert_eq!(summary.exit_code, Some(0));

        let flat = run.to_run_status("2026-08-03T12:00:00Z".into(), None);
        assert!(!flat.is_terminal(), "running lifecycle must not be terminal");
        assert_ne!(flat.health, "final");
        assert!(flat.exit_code.is_none());
        assert_ne!(flat.liveness, "terminal");
        assert_eq!(flat.execution_state, Some(ExecutionState::Running));

        run.status = "completed".into();
        let flat_done = run.to_run_status("2026-08-03T12:00:00Z".into(), None);
        assert!(flat_done.is_terminal());
        assert_eq!(flat_done.health, "final");
        assert_eq!(flat_done.exit_code, Some(0));
    }
}
