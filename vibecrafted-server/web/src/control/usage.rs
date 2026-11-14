//! HTTP projection for the canonical control-core usage report.

use axum::Json;
use axum::extract::{Extension, Query};
use axum::http::{HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use chrono::{Duration, Utc};
use control_core::{ControlPlane, UsageFilter};
use serde::Deserialize;
use serde_json::json;
use std::sync::{Arc, OnceLock};
use tokio::sync::{OwnedSemaphorePermit, Semaphore};

static USAGE_PROJECTION_SLOTS: OnceLock<Arc<Semaphore>> = OnceLock::new();

#[derive(Debug, Default, Deserialize)]
pub struct UsageQuery {
    window: Option<String>,
    provider: Option<String>,
    agent: Option<String>,
    model: Option<String>,
}

fn acquire_usage_projection_permit() -> Result<OwnedSemaphorePermit, StatusCode> {
    Arc::clone(USAGE_PROJECTION_SLOTS.get_or_init(|| Arc::new(Semaphore::new(4))))
        .try_acquire_owned()
        .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)
}

fn usage_projection_busy_response(status: StatusCode) -> Response {
    no_store(
        (
            status,
            Json(json!({
                "schema": "vibecrafted.error.v1",
                "error": "Usage projection capacity is busy; retry shortly.",
            })),
        )
            .into_response(),
    )
}

pub async fn usage(
    Extension(plane): Extension<ControlPlane>,
    Query(query): Query<UsageQuery>,
) -> Response {
    let window = query.window.as_deref().unwrap_or("24h");
    let since = match window {
        "24h" => Some(Duration::hours(24)),
        "7d" => Some(Duration::days(7)),
        "30d" => Some(Duration::days(30)),
        "all" => None,
        _ => {
            return no_store(
                (
                    StatusCode::BAD_REQUEST,
                    Json(json!({
                        "schema": "vibecrafted.error.v1",
                        "error": "window must be one of 24h, 7d, 30d, all",
                    })),
                )
                    .into_response(),
            );
        }
    };
    let provider = clean_filter(query.provider);
    let agent = clean_filter(query.agent);
    let model = clean_filter(query.model);
    if provider.is_err() || agent.is_err() || model.is_err() {
        return no_store((
            StatusCode::BAD_REQUEST,
            Json(json!({
                "schema": "vibecrafted.error.v1",
                "error": "provider, agent and model filters must be at most 128 printable characters",
            })),
        )
            .into_response());
    }
    let filter = UsageFilter {
        since,
        since_label: window.to_string(),
        provider: provider.expect("validated provider"),
        agent: agent.expect("validated agent"),
        model: model.expect("validated model"),
    };
    // The report reads bounded local files. Keep even slow filesystems off
    // the async reactor; legacy Python recovery is already single-flight and
    // asynchronous inside the canonical reader.
    let permit = match acquire_usage_projection_permit() {
        Ok(permit) => permit,
        Err(status) => return usage_projection_busy_response(status),
    };
    let projection = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        plane.usage_report(Utc::now(), filter)
    });
    match tokio::time::timeout(std::time::Duration::from_secs(5), projection).await {
        Ok(Ok(report)) => no_store(Json(report).into_response()),
        _ => no_store((
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({
                "schema": "vibecrafted.error.v1",
                "error": "Usage files could not be read promptly. Existing measurements remain available; retry shortly.",
            })),
        ).into_response()),
    }
}

fn no_store(mut response: Response) -> Response {
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

fn clean_filter(value: Option<String>) -> Result<Option<String>, ()> {
    let Some(value) = value else {
        return Ok(None);
    };
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Ok(None);
    }
    if trimmed.len() > 128 || trimmed.chars().any(char::is_control) {
        return Err(());
    }
    Ok(Some(trimmed.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn usage_projection_capacity_returns_no_store_when_full() {
        let slots = USAGE_PROJECTION_SLOTS.get_or_init(|| Arc::new(Semaphore::new(4)));
        let permits = (0..4)
            .map(|_| {
                Arc::clone(slots)
                    .try_acquire_owned()
                    .expect("available slot")
            })
            .collect::<Vec<_>>();
        let busy_status = acquire_usage_projection_permit()
            .expect_err("capacity exhaustion should return a busy status");
        let busy = usage_projection_busy_response(busy_status);
        assert_eq!(busy.status(), StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(busy.headers()[header::CACHE_CONTROL], "no-store");
        drop(permits);
        assert!(acquire_usage_projection_permit().is_ok());
    }
}
