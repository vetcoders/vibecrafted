//! HTTP projection for the canonical control-core usage report.

use axum::Json;
use axum::extract::{Extension, Query};
use axum::http::{HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use chrono::{Duration, Utc};
use control_core::{ControlPlane, UsageFilter};
use serde::Deserialize;
use serde_json::json;

#[derive(Debug, Default, Deserialize)]
pub struct UsageQuery {
    window: Option<String>,
    provider: Option<String>,
    agent: Option<String>,
    model: Option<String>,
    collect: Option<String>,
}

pub async fn usage(
    Extension(plane): Extension<ControlPlane>,
    Query(query): Query<UsageQuery>,
) -> Response {
    // Explicit operator-requested collection: one bounded vc-server-owned pass
    // over the provider engines. Default stays read-only so refreshes never
    // trigger scans or network probes.
    let collection = match query.collect.as_deref() {
        Some("1" | "true" | "yes") => {
            let home = plane.home();
            Some(
                tokio::task::spawn_blocking(move || control_core::collect_local_usage(&home))
                    .await
                    .unwrap_or_else(|error| control_core::CollectionReport {
                        schema: control_core::COLLECTION_SCHEMA.to_string(),
                        generated_at: Utc::now().to_rfc3339(),
                        ok: false,
                        outcomes: Vec::new(),
                        error: Some(format!("collection task failed: {error}")),
                    }),
            )
        }
        _ => None,
    };
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
    let mut report = plane.usage_report(
        Utc::now(),
        UsageFilter {
            since,
            since_label: window.to_string(),
            provider: provider.expect("validated provider"),
            agent: agent.expect("validated agent"),
            model: model.expect("validated model"),
        },
    );
    report.collection = collection;
    no_store(Json(report).into_response())
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
