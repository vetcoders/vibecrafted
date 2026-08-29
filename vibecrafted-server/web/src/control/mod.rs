//! Control-plane read surface for the `vibecrafted server`.
//!
//! Mirrors the `scaffold::api` shape: an `ssr`-gated axum sub-router merged into
//! the Leptos app in `main.rs`. Every route is a **read** over the live
//! `~/.vibecrafted/control_plane/` (or `$VIBECRAFTED_HOME`) via
//! [`control_core::ControlPlane`] — the same typed read-model the future TUI
//! shares. Nothing here writes; this is remote observability of what the Python
//! runtime already produced.
//!
//! Routes:
//! * `GET /api/health` — constant-time process readiness; never scans the
//!   control plane.
//! * `GET /api/control/state` — cached [`StateView`](control_core::StateView)
//!   (canonical settlement board, active/recent runs, warnings, event tail)
//!   read from the Python-owned snapshots. The raw self-sufficient merge stays
//!   available to TUI/diagnostic consumers, but is too expensive for an HTTP
//!   request over a long-lived control plane.
//! * `GET /api/control/dashboard` — the exact JSON the Leptos console hydrates
//!   and client-navigates with (state + lifecycle summaries + loctree report).
//! * `GET /api/control/runs` — every `runs/<id>.json` snapshot, newest-first.
//!   Each run serialises optional delivery-proof axes (`execution_state`,
//!   `proof_state`, `delivery_state`) and optional `seal` when present on the
//!   kernel receipt / snapshot. Absent axes stay absent (never invented from
//!   `completed`).
//! * `GET /api/control/runs/{run_id}` — a single run, or `404` JSON. Same axis
//!   / seal projection as the list route.
//! * `GET /api/control/runs/{run_id}/observe` — versioned one-shot qualified
//!   run observation. It never creates a persistent monitor.
//! * `GET /api/control/runs/{run_id}/await` — blocking subscription fan-in to
//!   one ephemeral monitor per canonical control-plane home plus run id.
//! * `GET /api/control/runs/{run_id}/transcript` — bounded, no-store tail of
//!   the canonical `transcript.human.log` used by the live run detail view.
//! * `GET /api/control/lifecycle` — lifecycle run summaries, newest-first.
//! * `GET /api/control/lifecycle/{run_id}` — full nested lifecycle state with
//!   projected per-run and per-stage axes (shape of `write_lifecycle_report`).
//! * `GET /api/control/events` — Server-Sent Events stream of `events.jsonl`
//!   from a client-held cursor (`?since=` / `Last-Event-ID`), with `: ping`
//!   keepalives. Read-only; see [`events_sse`].
//! * `GET /api/control/caretaker` — the published `vibecrafted.caretaker.v1`
//!   envelope (server identity, observability, resume backlog, control-plane
//!   upkeep) wrapped in transport-level freshness. Answering it is itself the
//!   liveness proof; see [`caretaker`].

#[cfg(feature = "ssr")]
mod caretaker;
#[cfg(feature = "ssr")]
mod events_sse;
#[cfg(feature = "ssr")]
mod run_observation;

#[cfg(feature = "ssr")]
pub mod api {
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Mutex, OnceLock};
    use std::time::{Duration, Instant};

    use axum::Json;
    use axum::Router;
    use axum::extract::Path;
    use axum::http::{StatusCode, header};
    use axum::response::IntoResponse;
    use axum::routing::get;
    use chrono::{DateTime, Utc};
    use control_core::{ControlPlane, Event, RunStatus, SettlementBoard, is_safe_run_id};
    use serde::Serialize;
    use serde_json::json;

    use super::caretaker::caretaker;
    use super::events_sse::events_sse;
    use super::run_observation::{await_run as await_run_observation, observe as observe_run};

    const STATE_CACHE_TTL: Duration = Duration::from_secs(15);

    #[derive(Clone)]
    struct StateCacheEntry {
        control_plane: PathBuf,
        refreshed_at: Instant,
        payload: StateEnvelope,
    }

    static STATE_CACHE: OnceLock<Mutex<Option<StateCacheEntry>>> = OnceLock::new();
    static STATE_REFRESHING: AtomicBool = AtomicBool::new(false);

    /// The control-plane read router, keyed to the same `LeptosOptions` state the
    /// app router carries so it merges without a state-type mismatch.
    pub fn control_routes() -> Router<leptos::config::LeptosOptions> {
        Router::<leptos::config::LeptosOptions>::new()
            .route("/api/health", get(health))
            .route("/api/control/state", get(state))
            .route("/api/control/dashboard", get(crate::app::dashboard_api))
            .route("/api/control/runs", get(runs))
            .route("/api/control/runs/{run_id}/observe", get(observe_run))
            .route(
                "/api/control/runs/{run_id}/await",
                get(await_run_observation),
            )
            .route("/api/control/runs/{run_id}/transcript", get(transcript))
            .route("/api/control/runs/{run_id}", get(run))
            .route("/api/control/lifecycle", get(lifecycle))
            .route("/api/control/lifecycle/{run_id}", get(lifecycle_run))
            .route("/api/control/events", get(events_sse))
            .route("/api/control/caretaker", get(caretaker))
    }

    /// Cheap liveness/readiness contract for the local process supervisor.
    ///
    /// This deliberately does not read the control plane: retained history can
    /// make the full state projection expensive without making the HTTP
    /// process unhealthy.
    async fn health() -> impl IntoResponse {
        Json(json!({
            "schema": "vibecrafted.health.v1",
            "status": "ok",
            "version": env!("VC_SERVER_VERSION"),
        }))
    }

    /// Canonical server projection consumed by both JSON and dashboard SSR.
    /// Settlement classification remains wholly owned by `control-core`.
    #[derive(Clone, Serialize)]
    pub(crate) struct StateEnvelope {
        pub(crate) control_plane: String,
        pub(crate) generated_at: String,
        pub(crate) active_runs: Vec<RunStatus>,
        pub(crate) stalled_runs: Vec<RunStatus>,
        pub(crate) recent_runs: Vec<RunStatus>,
        pub(crate) warnings: Vec<String>,
        pub(crate) events: Vec<Event>,
        pub(crate) settlement_counts: SettlementBoard,
    }

    fn build_state_payload(plane: &ControlPlane, now: DateTime<Utc>) -> StateEnvelope {
        let view = plane.read_state_view();
        StateEnvelope {
            control_plane: plane.control_plane_home().display().to_string(),
            generated_at: now.to_rfc3339(),
            active_runs: view.active_runs,
            stalled_runs: view.stalled_runs,
            recent_runs: view.recent_runs,
            warnings: view.warnings,
            events: view.events,
            settlement_counts: view.settlement_counts,
        }
    }

    fn state_cache() -> &'static Mutex<Option<StateCacheEntry>> {
        STATE_CACHE.get_or_init(|| Mutex::new(None))
    }

    fn cached_state_payload(plane: &ControlPlane) -> Option<StateEnvelope> {
        let control_plane = plane.control_plane_home();
        state_cache()
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .as_ref()
            .filter(|entry| entry.control_plane == control_plane)
            .map(|entry| entry.payload.clone())
    }

    fn cache_is_stale(plane: &ControlPlane) -> bool {
        let control_plane = plane.control_plane_home();
        state_cache()
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .as_ref()
            .filter(|entry| entry.control_plane == control_plane)
            .is_none_or(|entry| entry.refreshed_at.elapsed() >= STATE_CACHE_TTL)
    }

    fn store_state_payload(plane: &ControlPlane, payload: StateEnvelope) {
        *state_cache()
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(StateCacheEntry {
            control_plane: plane.control_plane_home(),
            refreshed_at: Instant::now(),
            payload,
        });
    }

    pub(crate) fn state_payload(plane: &ControlPlane, now: DateTime<Utc>) -> StateEnvelope {
        if let Some(payload) = cached_state_payload(plane) {
            return payload;
        }
        let payload = build_state_payload(plane, now);
        store_state_payload(plane, payload.clone());
        payload
    }

    /// Snapshot-backed state view. A complete projection is cached in-process;
    /// stale data is returned immediately while one background refresh reads
    /// the durable snapshots. This keeps filesystem latency out of HTTP.
    async fn state() -> impl IntoResponse {
        let plane = ControlPlane::from_env();
        let payload = state_payload(&plane, Utc::now());
        if cache_is_stale(&plane)
            && STATE_REFRESHING
                .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
                .is_ok()
        {
            tokio::task::spawn_blocking(move || {
                let payload = build_state_payload(&plane, Utc::now());
                store_state_payload(&plane, payload);
                STATE_REFRESHING.store(false, Ordering::Release);
            });
        }
        Json(payload)
    }

    /// Every `runs/<id>.json` snapshot, newest-first.
    async fn runs() -> impl IntoResponse {
        let plane = ControlPlane::from_env();
        let snapshots = plane.load_snapshots();
        Json(json!({
            "control_plane": plane.control_plane_home().display().to_string(),
            "count": snapshots.len(),
            "runs": snapshots,
        }))
    }

    /// Bounded canonical human transcript tail for a run detail page.
    ///
    /// The filesystem confinement, symlink refusal, byte cap, line cap, and
    /// terminal escape stripping are shared with the initial SSR render.
    async fn transcript(Path(run_id): Path<String>) -> impl IntoResponse {
        if !is_safe_run_id(&run_id) {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({ "error": "invalid run id" })),
            )
                .into_response();
        }

        let preview = crate::run_detail::load_human_transcript(&ControlPlane::from_env(), &run_id);
        (
            [(header::CACHE_CONTROL, "no-store")],
            Json(json!({
                "run_id": run_id,
                "body": preview.body,
                "available": preview.available,
                "truncated": preview.truncated,
            })),
        )
            .into_response()
    }

    /// Lifecycle run summaries, newest-first by `state.json` mtime.
    async fn lifecycle() -> impl IntoResponse {
        let plane = ControlPlane::from_env();
        let lifecycle_runs = plane.load_lifecycle_run_summaries();
        Json(json!({
            "control_plane": plane.control_plane_home().display().to_string(),
            "count": lifecycle_runs.len(),
            "lifecycle_runs": lifecycle_runs,
        }))
    }

    /// Full nested lifecycle state by id, or a `404` JSON body when absent.
    ///
    /// The payload includes projected delivery-proof axes on the run and each
    /// stage (`execution_state` / `proof_state` / `delivery_state`). Projection
    /// is owned by `control_core` and never maps `completed` → delivered/sealed.
    async fn lifecycle_run(Path(run_id): Path<String>) -> impl IntoResponse {
        let plane = ControlPlane::from_env();
        match plane.resolve_lifecycle_run(&run_id) {
            Some(run) => Json(json!(run)).into_response(),
            None => (
                StatusCode::NOT_FOUND,
                Json(json!({ "error": format!("lifecycle run not found: {run_id}") })),
            )
                .into_response(),
        }
    }

    /// A single run by id, or a `404` JSON body when absent.
    ///
    /// Serialises typed delivery axes and seal when the snapshot/receipt carries
    /// them; omits those keys for legacy runs (no completed→delivery guess).
    async fn run(Path(run_id): Path<String>) -> impl IntoResponse {
        let plane = ControlPlane::from_env();
        match plane.lookup_run(&run_id) {
            Some(run) => Json(json!(run)).into_response(),
            None => (
                StatusCode::NOT_FOUND,
                Json(json!({ "error": format!("run not found: {run_id}") })),
            )
                .into_response(),
        }
    }
}
