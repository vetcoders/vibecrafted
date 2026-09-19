//! `GET /api/control/events` — generation-aware SSE over the control plane.
//!
//! The response is read-only. Data events carry opaque v2 cursor IDs, while
//! `stream.boundary`, `stream.caught-up`, and `stream.gap` make connection and
//! recovery state explicit. Numeric cursors remain a deprecated compatibility
//! mode for clients that send numeric `?since=` / `Last-Event-ID`.

use std::collections::VecDeque;
use std::convert::Infallible;
use std::time::Duration;

use axum::extract::Query;
use axum::http::HeaderMap;
use axum::response::sse::{Event as SseEvent, KeepAlive, Sse};
use control_core::{
    ConnectionWindow, ControlPlane, Event as ControlEvent, StreamBoundary, StreamCursor, StreamGap,
    StreamItem, StreamRecord,
};
use futures_util::stream::{self, Stream};
use serde::Deserialize;

const DEFAULT_POLL_MS: u64 = 500;
const DEFAULT_KEEPALIVE_MS: u64 = 15_000;

#[derive(Debug, Default, Deserialize)]
pub(crate) struct EventsQuery {
    /// Opaque v2 cursor. Numeric values select deprecated legacy mode.
    pub since: Option<String>,
}

#[derive(Debug)]
enum Outbound {
    Event(StreamRecord),
    Boundary {
        boundary: StreamBoundary,
        reason: &'static str,
    },
    Gap(StreamGap),
    CaughtUp {
        cursor: StreamCursor,
        high_watermark: StreamCursor,
    },
}

struct StreamState {
    cursor: StreamCursor,
    high_watermark: StreamCursor,
    pending: VecDeque<Outbound>,
    caught_up: bool,
}

fn resolve_cursor_raw(query: &EventsQuery, headers: &HeaderMap) -> Option<String> {
    query.since.clone().or_else(|| {
        headers
            .get("last-event-id")
            .or_else(|| headers.get("Last-Event-ID"))
            .and_then(|value| value.to_str().ok())
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
    })
}

fn env_ms(name: &str, default: u64) -> u64 {
    std::env::var(name)
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(default)
}

fn event_data_json(event: &ControlEvent) -> String {
    let mut value = serde_json::to_value(event).unwrap_or_else(|_| serde_json::json!({}));
    if let Some(object) = value.as_object_mut() {
        object.remove("cursor");
    }
    value.to_string()
}

fn outbound_frame(outbound: Outbound) -> SseEvent {
    match outbound {
        Outbound::Event(record) => SseEvent::default()
            .id(record.cursor.to_string())
            .data(event_data_json(&record.event)),
        Outbound::Boundary { boundary, reason } => {
            let cursor = boundary.to.to_string();
            SseEvent::default()
                .event("stream.boundary")
                .id(cursor.clone())
                .data(
                    serde_json::json!({
                        "schema": "vibecrafted.stream-boundary.v1",
                        "kind": "stream.boundary",
                        "from": boundary.from.to_string(),
                        "to": cursor,
                        "reason": reason,
                    })
                    .to_string(),
                )
        }
        Outbound::Gap(gap) => {
            let cursor = gap.resumed_at.to_string();
            SseEvent::default()
                .event("stream.gap")
                .id(cursor.clone())
                .data(
                    serde_json::json!({
                        "schema": "vibecrafted.stream-gap.v1",
                        "kind": "stream.gap",
                        "requested": gap.requested,
                        "resumed_at": cursor,
                        "reason": gap.reason,
                        "action": "resnapshot",
                    })
                    .to_string(),
                )
        }
        Outbound::CaughtUp {
            cursor,
            high_watermark,
        } => SseEvent::default()
            .event("stream.caught-up")
            .id(cursor.to_string())
            .data(
                serde_json::json!({
                    "schema": "vibecrafted.stream-caught-up.v1",
                    "kind": "stream.caught-up",
                    "cursor": cursor.to_string(),
                    "high_watermark": high_watermark.to_string(),
                })
                .to_string(),
            ),
    }
}

fn initial_state(query: &EventsQuery, headers: &HeaderMap) -> StreamState {
    let stream = ControlPlane::from_env().events();
    let raw = resolve_cursor_raw(query, headers);
    let (requested, invalid) = match raw {
        Some(raw) => match raw.parse::<StreamCursor>() {
            Ok(cursor) => (Some(cursor), None),
            Err(_) => (None, Some(raw)),
        },
        None => (None, None),
    };
    let fallback = requested.clone().unwrap_or(StreamCursor::Legacy(0));
    let window = stream
        .connection_window(requested.as_ref())
        .unwrap_or(ConnectionWindow {
            cursor: fallback.clone(),
            high_watermark: fallback,
        });
    let boundary_from = requested.clone().unwrap_or_else(|| window.cursor.clone());
    let cursor = if invalid.is_some() {
        window.high_watermark.clone()
    } else {
        window.cursor
    };
    let high_watermark = window.high_watermark;
    let invalid_cursor = invalid.is_some();
    let mut pending = VecDeque::new();
    if let Some(requested) = invalid {
        pending.push_back(Outbound::Gap(StreamGap {
            requested,
            resumed_at: cursor.clone(),
            reason: "invalid_cursor".to_string(),
        }));
    }
    pending.push_back(Outbound::Boundary {
        boundary: StreamBoundary {
            from: if invalid_cursor {
                cursor.clone()
            } else {
                boundary_from
            },
            to: cursor.clone(),
        },
        reason: "connection_start",
    });
    StreamState {
        cursor,
        high_watermark,
        pending,
        caught_up: false,
    }
}

/// Long-lived SSE response with a finite baseline and an explicit caught-up
/// marker independent of heartbeat traffic.
pub(crate) async fn events_sse(
    Query(query): Query<EventsQuery>,
    headers: HeaderMap,
) -> Sse<impl Stream<Item = Result<SseEvent, Infallible>>> {
    let poll_ms = env_ms("VC_CONTROL_SSE_POLL_MS", DEFAULT_POLL_MS);
    let keepalive_ms = env_ms("VC_CONTROL_SSE_KEEPALIVE_MS", DEFAULT_KEEPALIVE_MS);
    let state = initial_state(&query, &headers);

    let event_stream = stream::unfold(state, move |mut state| async move {
        loop {
            if let Some(outbound) = state.pending.pop_front() {
                return Some((Ok::<_, Infallible>(outbound_frame(outbound)), state));
            }
            if !state.caught_up && state.cursor.reaches(&state.high_watermark) {
                state.caught_up = true;
                let outbound = Outbound::CaughtUp {
                    cursor: state.cursor.clone(),
                    high_watermark: state.high_watermark.clone(),
                };
                return Some((Ok::<_, Infallible>(outbound_frame(outbound)), state));
            }

            let plane = ControlPlane::from_env();
            match plane.events().read_stream(&state.cursor, &[]) {
                Ok(batch) => {
                    state.cursor = batch.cursor;
                    let mut saw_gap = false;
                    for item in batch.items {
                        match item {
                            StreamItem::Event(record) => {
                                state.pending.push_back(Outbound::Event(record));
                            }
                            StreamItem::Boundary(boundary) => {
                                state.pending.push_back(Outbound::Boundary {
                                    boundary,
                                    reason: "generation_change",
                                });
                            }
                            StreamItem::Gap(gap) => {
                                saw_gap = true;
                                state.pending.push_back(Outbound::Gap(gap));
                            }
                        }
                    }
                    if saw_gap {
                        // Gap recovery cursor is a fresh active high-watermark.
                        // The client must resnapshot, then future stream effects
                        // resume from this explicit receipt.
                        state.high_watermark = state.cursor.clone();
                    }
                    if state.pending.is_empty()
                        && (!state.cursor.reaches(&state.high_watermark) || state.caught_up)
                    {
                        tokio::time::sleep(Duration::from_millis(poll_ms)).await;
                    }
                }
                Err(_) => {
                    // Rotation has a tiny rename/publish window. Retrying the
                    // same opaque cursor lets the archive bridge resolve it.
                    tokio::time::sleep(Duration::from_millis(poll_ms)).await;
                }
            }
        }
    });

    Sse::new(event_stream).keep_alive(
        KeepAlive::new()
            .interval(Duration::from_millis(keepalive_ms))
            .text("ping"),
    )
}
