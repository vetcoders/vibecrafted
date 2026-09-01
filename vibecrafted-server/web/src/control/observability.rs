//! `GET /api/control/observability` — the split-observability index.
//!
//! Logs, metrics and the run board are not three stores with three truths:
//! they are **projections of one control plane**. This route exists to say so
//! in machine-readable form. Every row names its projection, the HTTP route
//! that serves it, and the exact path inside the control-plane home it is
//! projected from, so a reader can verify the split is a fan-out of reads over
//! a single durable store rather than three independent silos that can drift.
//!
//! Two rules keep the index honest:
//!
//! * **Availability is observed, not asserted.** A projection whose source
//!   directory or stream does not exist yet says `available: false` with the
//!   reason; an absent projection is never rendered as a healthy empty one.
//! * **Answering is itself a fact.** Like the caretaker route, this index
//!   returns `200` even when every projection is absent — a reader that gets
//!   this response has proven the server is alive, which must stay
//!   distinguishable from "the plane has nothing published yet".
//!
//! The index derives no health. The caretaker envelope
//! (`vibecrafted.caretaker.v1`, served at `/api/control/caretaker`) remains
//! the sole owner of the health verdict; this route only names where each
//! observability surface reads from.

use std::path::PathBuf;

use axum::Json;
use axum::http::header;
use axum::response::IntoResponse;
use control_core::ControlPlane;
use serde_json::{Value, json};

/// View schema for the projections index. Distinct from the caretaker
/// envelope: this is a directory of surfaces, not a health reading.
const OBSERVABILITY_VIEW_SCHEMA: &str = "vibecrafted.observability-view.v1";

/// Filename the Python runtime publishes the caretaker envelope into.
const CARETAKER_SNAPSHOT_NAME: &str = "caretaker.json";

/// One named projection of the control plane.
fn projection(
    name: &str,
    kind: &str,
    route: &str,
    source_path: PathBuf,
    missing: &str,
) -> Value {
    let available = source_path.exists();
    json!({
        "name": name,
        "kind": kind,
        "route": route,
        "source": "control_plane",
        "source_path": source_path.display().to_string(),
        "available": available,
        "reason": if available { String::new() } else { missing.to_string() },
    })
}

/// Serve the projections index over the live control-plane home.
pub(crate) async fn observability() -> impl IntoResponse {
    let plane = ControlPlane::from_env();
    let control_plane = plane.control_plane_home();
    let caretaker_path = control_plane.join(CARETAKER_SNAPSHOT_NAME);

    let projections = vec![
        projection(
            "run-board",
            "settlement board (f/x/n) plus the active/stalled/recent run merge",
            "/api/control/state",
            plane.run_snapshot_dir(),
            "no run snapshots published yet",
        ),
        projection(
            "run-history",
            "every run snapshot, newest-first",
            "/api/control/runs",
            plane.run_snapshot_dir(),
            "no run snapshots published yet",
        ),
        projection(
            "lifecycle",
            "lifecycle run containers with per-stage delivery axes",
            "/api/control/lifecycle",
            plane.lifecycle_runs_dir(),
            "no lifecycle runs published yet",
        ),
        projection(
            "logs",
            "the control-plane event stream, served as a cursorable SSE feed; \
             the server leg's own supervisor logs are named in the caretaker envelope",
            "/api/control/events",
            plane.event_stream_path(),
            "the event stream has not been written yet",
        ),
        projection(
            "metrics",
            "counts, byte sizes and ages of the plane, carried by the \
             caretaker envelope's observability section",
            "/api/control/caretaker",
            caretaker_path.clone(),
            "not published: run `vibecrafted server caretaker`",
        ),
        projection(
            "caretaker",
            "the one caretaker truth: server identity, verdict, actions, upkeep",
            "/api/control/caretaker",
            caretaker_path,
            "not published: run `vibecrafted server caretaker`",
        ),
    ];

    (
        [(header::CACHE_CONTROL, "no-store")],
        Json(json!({
            "schema": OBSERVABILITY_VIEW_SCHEMA,
            "server_version": env!("VC_SERVER_VERSION"),
            "control_plane": control_plane.display().to_string(),
            "projections": projections,
        })),
    )
}
