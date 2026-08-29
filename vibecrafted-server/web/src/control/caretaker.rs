//! `GET /api/control/caretaker` — the server's first-violin caretaker surface.
//!
//! The Python runtime fuses server identity, observability, resume backlog and
//! control-plane upkeep into one versioned envelope
//! (`vibecrafted.caretaker.v1`, written by `vibecrafted_core.caretaker`) and
//! publishes it into the control plane. This route serves those exact bytes.
//!
//! Two properties make it the surface a tray can trust:
//!
//! * **Answering is itself a fact.** A reader that gets this response has
//!   proven the server process is alive and serving — no separate liveness
//!   payload, no second subprocess. The body then says whether the runtime has
//!   published a caretaker envelope yet, which is a different question.
//! * **Freshness travels with the payload.** The route stats the published file
//!   and reports `age_seconds` plus an explicit `stale` verdict, so a consumer
//!   never has to decide for itself whether a snapshot still means anything.
//!   A supervisor that stopped refreshing leaves a file that still parses; only
//!   its age gives that away.
//!
//! The route never derives health. `vibecrafted_core.caretaker::derive_verdict`
//! is the sole owner of that call; a second opinion computed here would be the
//! exact truth competition the envelope exists to end.

use std::path::Path;
use std::time::SystemTime;

use axum::Json;
use axum::http::header;
use axum::response::IntoResponse;
use control_core::ControlPlane;
use serde_json::{Value, json};

/// Envelope schema this route emits. Distinct from the published snapshot's
/// own `vibecrafted.caretaker.v1`: this is the *view*, carrying transport-level
/// freshness around an unmodified payload.
const CARETAKER_VIEW_SCHEMA: &str = "vibecrafted.caretaker-view.v1";

/// Filename the Python runtime publishes into the control-plane home.
const CARETAKER_SNAPSHOT_NAME: &str = "caretaker.json";

/// Mirrors `vibecrafted_core.caretaker.SNAPSHOT_STALE_SECONDS`. Past this, a
/// published envelope is history rather than status.
const SNAPSHOT_STALE_SECONDS: f64 = 300.0;

/// Seconds since `path` was last modified, or `None` when it cannot be stat-ed
/// or the clock reports a modification in the future.
fn age_seconds(path: &Path) -> Option<f64> {
    let modified = std::fs::metadata(path).ok()?.modified().ok()?;
    SystemTime::now()
        .duration_since(modified)
        .ok()
        .map(|elapsed| elapsed.as_secs_f64())
}

/// Read the published envelope, returning `(payload, reason)`.
///
/// A corrupt file is reported as a reason rather than swallowed: "the runtime
/// never published" and "the runtime published something unparseable" are
/// different operator problems and must not collapse into one silence.
fn read_snapshot(path: &Path) -> (Option<Value>, String) {
    let raw = match std::fs::read_to_string(path) {
        Ok(raw) => raw,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            return (
                None,
                format!(
                    "not published: {} (run `vibecrafted server caretaker`)",
                    path.display()
                ),
            );
        }
        Err(err) => return (None, format!("unreadable: {err}")),
    };
    match serde_json::from_str::<Value>(&raw) {
        Ok(Value::Object(map)) => (Some(Value::Object(map)), String::new()),
        Ok(_) => (None, "published payload is not a JSON object".to_string()),
        Err(err) => (None, format!("corrupt JSON: {err}")),
    }
}

/// Serve the published caretaker envelope with transport-level freshness.
pub(crate) async fn caretaker() -> impl IntoResponse {
    let plane = ControlPlane::from_env();
    let control_plane = plane.control_plane_home();
    let path = control_plane.join(CARETAKER_SNAPSHOT_NAME);

    let (snapshot, reason) = read_snapshot(&path);
    let age = snapshot.as_ref().and_then(|_| age_seconds(&path));
    let stale = match age {
        Some(seconds) => seconds > SNAPSHOT_STALE_SECONDS,
        None => true,
    };

    (
        [(header::CACHE_CONTROL, "no-store")],
        Json(json!({
            "schema": CARETAKER_VIEW_SCHEMA,
            "server_version": env!("VC_SERVER_VERSION"),
            "control_plane": control_plane.display().to_string(),
            "path": path.display().to_string(),
            "published": snapshot.is_some(),
            "age_seconds": age,
            "stale": stale,
            "stale_after_seconds": SNAPSHOT_STALE_SECONDS,
            "reason": reason,
            "snapshot": snapshot,
        })),
    )
}
