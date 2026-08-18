//! `control-core` — typed model of the Vibecrafted control plane.
//!
//! One core, two frontends. The Python writer
//! (`vibecrafted-core/vibecrafted_core/control_plane.py`) owns
//! `~/.vibecrafted/control_plane/`; this crate gives Rust callers a typed,
//! **read-only** view of the same data so the `vibecrafted server` web UI
//! (W1-b/W2) and the future `vc-agent` TUI can share one contract instead of
//! re-parsing JSON ad hoc.
//!
//! The scaffold editor is the deliberate exception to the read-only rule: it
//! edits manifest-declared Markdown artifacts under canonical
//! `artifacts/<org>/<repo>/<day>/plans/<plan_id>/` roots and records
//! change/checkpoint sidecars there. Legacy `operator/` roots are read-only.
//! It never writes Python
//! control-plane snapshots.
//!
//! Three layers:
//!
//! * [`model`] — `RunStatus`, `Event`, state classes, `health` derivation, the
//!   skill-code map, and the `*.meta.json` → `RunStatus` normalisation. A
//!   field-for-field mirror of `control_plane.py`.
//! * [`read`] — [`ControlPlane`], a handle that loads `runs/<id>.json`
//!   snapshots, looks up a single run, and (option a) merges the three raw
//!   sources in Rust. Never writes.
//! * [`events`] — [`EventStream`], the generation-aware cursor substrate a W2
//!   axum SSE route drains.
//! * [`scaffold`] — typed discovery, artifact writes, change feed, and
//!   checkpoints for vc-scaffold review artifacts.
//!
//! ```no_run
//! use control_core::ControlPlane;
//!
//! let plane = ControlPlane::from_env();
//! for run in plane.load_snapshots() {
//!     println!("{} {} ({})", run.run_id, run.state, run.health);
//! }
//! let stream = plane.events();
//! let batch = stream.read_stream(&stream.start_cursor().unwrap(), &[]).unwrap();
//! println!("{} stream items, next cursor {}", batch.items.len(), batch.cursor);
//! ```

pub mod events;
pub mod model;
pub mod read;
pub mod scaffold;

pub use events::{
    ConnectionWindow, EventBatch, EventStream, STREAM_BATCH_MAX_BYTES, STREAM_BATCH_MAX_EVENTS,
    STREAM_LINE_MAX_BYTES, STREAM_SEGMENT_SCHEMA, StreamBatch, StreamBoundary, StreamCursor,
    StreamGap, StreamItem, StreamRecord,
};
pub use model::{
    ACTIVE_STATES, AgentMeta, DeliveryAxes, DeliverySealRef, DeliveryState, EVENT_TAIL_LIMIT,
    Event, ExecutionState, FINAL_STATES, Health, LifecycleBaton, LifecycleDouIndex,
    LifecycleOperatorAction, LifecycleRun, LifecycleRunSummary, LifecycleStage,
    LifecycleTransition, NativeResumeCandidate, ProofState, RECENT_RUN_LIMIT, RUN_STALL_SECONDS,
    RunControls, RunStatus, SKILL_CODE_MAP, SettlementBoard, SettlementScope, SettlementTui,
    SettlementVerdict, StateClass, classify_state, coerce_int_value, delivery_axes_for_receipt,
    is_active_state, is_final_state, merge_status, operator_session_name, parse_iso,
    skill_from_code, state_health,
};
pub use read::{is_safe_run_id, vibecrafted_home, ControlPlane, StateView};
pub use scaffold::{
    SCAFFOLD_EXPORT_SCHEMA_VERSION, SCAFFOLD_MANIFEST_SCHEMA_JSON, SCAFFOLD_SCHEMA_VERSION,
    ScaffoldArtifact, ScaffoldArtifactDeclaration, ScaffoldArtifactPatch, ScaffoldArtifactRole,
    ScaffoldArtifactStore, ScaffoldCatalog, ScaffoldCatalogSkip, ScaffoldChange,
    ScaffoldCheckpoint, ScaffoldCheckpointPatch, ScaffoldDoctorError, ScaffoldDoctorReport,
    ScaffoldError, ScaffoldExportArtifact, ScaffoldExportBundle, ScaffoldManifest,
    ScaffoldPlanSummary, ScaffoldResult, ScaffoldStatusPatch, ScaffoldVerifierProbe,
    ScaffoldWorkspace, apply_plan_geometry, collect_delivery_verifiers, doctor_plan_root,
    doctor_plan_root_in_repo,
};
