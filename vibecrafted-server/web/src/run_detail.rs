//! Per-run observability page: `/run/{run_id}`.
//!
//! The console dashboard shows the fleet; this page shows ONE run with every
//! axis the control plane persisted for it: state/health, the canonical
//! settlement projection, the delivery-proof kernel axes (execution / proof /
//! delivery, plus the seal reference), process identity, session identity, and
//! the artifact paths the Python runtime recorded. Everything is a **read** of
//! [`control_core::ControlPlane`]; absent axes render as absent — this page
//! never invents delivery truth from `completed` (DELIVERY_PROOF_KERNEL_v1).
//!
//! Lifecycle runs (baton relays) have no `runs/<id>.json` snapshot; for those
//! the page falls back to the lifecycle summary and points at the nested
//! lifecycle JSON instead of pretending a worker snapshot exists.

use leptos::prelude::*;
use leptos_meta::{Meta, Title};

#[cfg(feature = "ssr")]
use control_core::is_safe_run_id;

// Hydration does not link the server-only control-core crate. This exact,
// side-effect-free mirror only keeps the client DOM aligned with SSR when it
// decides whether to render the raw-run link; control-core remains the runtime
// authority for every lookup and filesystem read.
#[cfg(not(feature = "ssr"))]
fn is_safe_run_id(run_id: &str) -> bool {
    let bytes = run_id.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 255
        && bytes[0].is_ascii_alphanumeric()
        && bytes
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

use crate::chrome::{ServerFrame, ServerSection};

/// Everything the detail page knows about one run id. Exactly one of `run` /
/// `lifecycle` is populated on a hit; both empty renders the honest 404 body.
#[derive(Clone, Default)]
struct RunDetailData {
    run_id: String,
    control_plane: String,
    run: Option<RunDetailView>,
    lifecycle: Option<LifecycleDetailView>,
    transcript: TranscriptPreview,
    events: Vec<RunDetailEvent>,
}

#[derive(Clone, Default)]
pub(crate) struct TranscriptPreview {
    pub(crate) body: String,
    pub(crate) available: bool,
    pub(crate) truncated: bool,
}

/// Flat, render-ready projection of [`control_core::RunStatus`]. Strings stay
/// empty (not "unknown") when the snapshot omitted a field.
#[derive(Clone, Default)]
struct RunDetailView {
    state: String,
    health: String,
    agent: String,
    skill: String,
    mode: String,
    root: String,
    liveness: String,
    source: String,
    lock_present: bool,
    recovery_required: bool,
    stop_reason: String,
    exit_code: String,
    launcher_pid: String,
    worker_pid: String,
    worker_pgid: String,
    worker_alive: String,
    started_at: String,
    updated_at: String,
    completed_at: String,
    current_loop: String,
    total_loops: String,
    attempt: String,
    resume_of: String,
    commit_sha: String,
    operator_session: String,
    session_id: String,
    agent_session_id: String,
    runtime_session_id: String,
    latest_report: String,
    latest_transcript: String,
    last_error: String,
    settlement_verdict: String,
    settlement_tui: String,
    settlement_reason: String,
    settlement_source: String,
    settlement_at: String,
    execution_state: String,
    proof_state: String,
    delivery_state: String,
    seal: Option<SealView>,
    trust_receipt: Option<TrustReceiptView>,
}

#[derive(Clone, Default)]
struct SealView {
    seal_id: String,
    issued_at: String,
    issuer: String,
    cut_id: String,
    repo: String,
    branch: String,
    final_head: String,
}

#[derive(Clone, Default)]
struct TrustReceiptView {
    receipt_id: String,
    trust_verdict: String,
    commit_sha: String,
    settlement_revision: String,
}

/// Fallback identity for lifecycle (baton) run ids without a worker snapshot.
#[derive(Clone, Default)]
struct LifecycleDetailView {
    workflow: String,
    status: String,
    current_stage: String,
    next_stage: String,
    next_agent: String,
    updated_at: String,
}

#[derive(Clone, Default)]
struct RunDetailEvent {
    ts: String,
    kind: String,
    message: String,
}

#[cfg(feature = "ssr")]
fn load_run_detail(run_id: &str) -> RunDetailData {
    use chrono::Utc;
    use control_core::ControlPlane;

    load_run_detail_from(&ControlPlane::from_env(), run_id, Utc::now())
}

#[cfg(feature = "ssr")]
pub(crate) fn load_human_transcript(
    plane: &control_core::ControlPlane,
    run_id: &str,
) -> TranscriptPreview {
    use std::fs::{self, File};
    use std::io::{Read, Seek, SeekFrom};

    const MAX_BYTES: u64 = 48 * 1024;
    const MAX_LINES: usize = 160;

    fn strip_terminal_escapes(input: &str) -> String {
        let mut output = String::with_capacity(input.len());
        let mut chars = input.chars().peekable();
        while let Some(ch) = chars.next() {
            if ch != '\u{1b}' {
                output.push(ch);
                continue;
            }

            match chars.next() {
                Some('[') => {
                    for code in chars.by_ref() {
                        if ('@'..='~').contains(&code) {
                            break;
                        }
                    }
                }
                Some(']') => {
                    let mut escaped = false;
                    for code in chars.by_ref() {
                        if code == '\u{7}' || (escaped && code == '\\') {
                            break;
                        }
                        escaped = code == '\u{1b}';
                    }
                }
                Some(_) | None => {}
            }
        }
        output
    }

    if !is_safe_run_id(run_id) {
        return TranscriptPreview::default();
    }

    let runtime_root = plane.control_plane_home().join("runtime_runs");
    let Ok(canonical_root) = fs::canonicalize(&runtime_root) else {
        return TranscriptPreview::default();
    };
    let candidate = runtime_root.join(run_id).join("transcript.human.log");
    let Ok(metadata) = fs::symlink_metadata(&candidate) else {
        return TranscriptPreview::default();
    };
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return TranscriptPreview::default();
    }
    let Ok(canonical_candidate) = fs::canonicalize(&candidate) else {
        return TranscriptPreview::default();
    };
    if !canonical_candidate.starts_with(&canonical_root) {
        return TranscriptPreview::default();
    }

    let start = metadata.len().saturating_sub(MAX_BYTES);
    let Ok(mut file) = File::open(&canonical_candidate) else {
        return TranscriptPreview::default();
    };
    if file.seek(SeekFrom::Start(start)).is_err() {
        return TranscriptPreview::default();
    }
    let mut bytes = Vec::with_capacity((metadata.len() - start) as usize);
    if file.read_to_end(&mut bytes).is_err() {
        return TranscriptPreview::default();
    }
    if start > 0
        && let Some(first_newline) = bytes.iter().position(|byte| *byte == b'\n')
    {
        bytes.drain(..=first_newline);
    }

    let decoded = String::from_utf8_lossy(&bytes);
    let lines = decoded.lines().collect::<Vec<_>>();
    let line_start = lines.len().saturating_sub(MAX_LINES);
    TranscriptPreview {
        body: strip_terminal_escapes(&lines[line_start..].join("\n")),
        available: true,
        truncated: start > 0 || line_start > 0,
    }
}

#[cfg(feature = "ssr")]
fn load_run_detail_from(
    plane: &control_core::ControlPlane,
    run_id: &str,
    now: chrono::DateTime<chrono::Utc>,
) -> RunDetailData {
    if !is_safe_run_id(run_id) {
        return RunDetailData {
            run_id: run_id.to_string(),
            control_plane: plane.control_plane_home().display().to_string(),
            ..RunDetailData::default()
        };
    }

    /// Serde is the single naming authority for enum wire strings; rendering
    /// through it keeps this page drift-proof against model.rs enum changes.
    fn wire<T: serde::Serialize>(value: &Option<T>) -> String {
        value
            .as_ref()
            .and_then(|inner| serde_json::to_value(inner).ok())
            .and_then(|json| json.as_str().map(str::to_owned))
            .unwrap_or_default()
    }

    fn display<T: std::fmt::Display>(value: &Option<T>) -> String {
        value.as_ref().map(ToString::to_string).unwrap_or_default()
    }

    let run = plane.lookup_run(run_id).map(|run| RunDetailView {
        state: run.state,
        health: run.health,
        agent: run.agent,
        skill: run.skill,
        mode: run.mode,
        root: run.root,
        liveness: run.liveness,
        source: run.source,
        lock_present: run.lock_present,
        recovery_required: run.recovery_required,
        stop_reason: run.stop_reason,
        exit_code: display(&run.exit_code),
        launcher_pid: display(&run.launcher_pid),
        worker_pid: display(&run.worker_pid),
        worker_pgid: display(&run.worker_pgid),
        worker_alive: display(&run.worker_alive),
        started_at: run.started_at,
        updated_at: run.updated_at,
        completed_at: run.completed_at,
        current_loop: display(&run.current_loop),
        total_loops: display(&run.total_loops),
        attempt: display(&run.attempt),
        resume_of: run.resume_of,
        commit_sha: run.commit_sha,
        operator_session: run.operator_session,
        session_id: run.session_id,
        agent_session_id: run.agent_session_id,
        runtime_session_id: run.runtime_session_id,
        latest_report: run.latest_report,
        latest_transcript: run.latest_transcript,
        last_error: run.last_error,
        settlement_verdict: wire(&run.settlement_verdict),
        settlement_tui: wire(&run.settlement_tui),
        settlement_reason: run.settlement_reason,
        settlement_source: run.settlement_source,
        settlement_at: run.settlement_at,
        execution_state: wire(&run.execution_state),
        proof_state: wire(&run.proof_state),
        delivery_state: wire(&run.delivery_state),
        seal: run.seal.map(|seal| SealView {
            seal_id: seal.seal_id,
            issued_at: seal.issued_at,
            issuer: seal.issuer,
            cut_id: seal.cut_id,
            repo: seal.repo,
            branch: seal.branch,
            final_head: seal.final_head,
        }),
        trust_receipt: run.trust_receipt.map(|receipt| TrustReceiptView {
            receipt_id: receipt.receipt_id,
            trust_verdict: serde_json::to_value(receipt.trust_verdict)
                .ok()
                .and_then(|json| json.as_str().map(str::to_owned))
                .unwrap_or_default(),
            commit_sha: receipt.commit_sha,
            settlement_revision: receipt.settlement_revision.to_string(),
        }),
    });

    let lifecycle = if run.is_none() {
        plane
            .load_lifecycle_run_summaries()
            .into_iter()
            .find(|summary| summary.run_id == run_id)
            .map(|summary| LifecycleDetailView {
                workflow: summary.workflow,
                status: summary.status,
                current_stage: summary.current_stage,
                next_stage: summary.next_stage,
                next_agent: summary.next_agent,
                updated_at: summary.updated_at,
            })
    } else {
        None
    };

    // Recent tail only: the cached state view is what the console already
    // paid for; a full events.jsonl scan belongs to the SSE route.
    let events = crate::control::api::state_payload(plane, now)
        .events
        .into_iter()
        .filter(|event| event.run_id == run_id)
        .map(|event| RunDetailEvent {
            ts: event.ts,
            kind: event.kind,
            message: event.message,
        })
        .collect();

    RunDetailData {
        run_id: run_id.to_string(),
        control_plane: plane.control_plane_home().display().to_string(),
        run,
        lifecycle,
        transcript: load_human_transcript(plane, run_id),
        events,
    }
}

#[cfg(not(feature = "ssr"))]
fn load_run_detail(run_id: &str) -> RunDetailData {
    RunDetailData {
        run_id: run_id.to_string(),
        ..RunDetailData::default()
    }
}

/// One `<dt>/<dd>` fact row; empty values render as an em-dash so a sparse
/// legacy snapshot still produces a scannable, honest grid.
fn fact(label: &'static str, value: String) -> impl IntoView {
    let rendered = if value.is_empty() {
        "—".to_string()
    } else {
        value
    };
    view! {
        <div class="run-detail-fact">
            <dt>{label}</dt>
            <dd>{rendered}</dd>
        </div>
    }
}

fn axis_badge(axis: &'static str, value: String) -> impl IntoView {
    let label = if value.is_empty() {
        format!("{axis}: absent")
    } else {
        format!("{axis}: {value}")
    };
    view! { <span class="control-badge">{label}</span> }
}

fn artifact_path(label: &'static str, path: String) -> impl IntoView {
    if path.is_empty() {
        leptos::either::Either::Left(view! {
            <li class="run-detail-artifact">
                <span class="mono-cap">{label}</span>
                <span>"—"</span>
            </li>
        })
    } else {
        leptos::either::Either::Right(view! {
            <li class="run-detail-artifact">
                <span class="mono-cap">{label}</span>
                <span class="run-detail-artifact-path">{path}</span>
            </li>
        })
    }
}

fn event_rows(events: Vec<RunDetailEvent>) -> impl IntoView {
    events
        .into_iter()
        .map(|event| {
            view! {
                <li class="control-event-row">
                    <span>{event.ts}</span>
                    <strong>{event.kind}</strong>
                    <span>{event.message}</span>
                </li>
            }
        })
        .collect_view()
}

fn transcript_tail_script() -> &'static str {
    r#"(() => {
  const panel = document.querySelector('[data-live-transcript]');
  if (!panel || !panel.dataset.tailUrl) return;
  const output = panel.querySelector('[data-transcript-output]');
  const empty = panel.querySelector('[data-transcript-empty]');
  const state = panel.querySelector('[data-transcript-state]');
  let lastBody = output.textContent;
  let requestInFlight = false;

  async function refreshTranscript() {
    if (requestInFlight || document.hidden) return;
    requestInFlight = true;
    try {
      const response = await fetch(panel.dataset.tailUrl, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const tail = await response.json();
      const body = tail.body || '';
      const hasBody = Boolean(tail.available && body.trim());
      if (body !== lastBody) {
        const pinnedToBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 40;
        output.textContent = body;
        lastBody = body;
        if (pinnedToBottom) output.scrollTop = output.scrollHeight;
      }
      output.hidden = !hasBody;
      empty.hidden = hasBody;
      empty.textContent = tail.available
        ? 'The human transcript exists but is empty.'
        : 'No bounded human transcript is available for this run yet.';
      state.textContent = tail.available
        ? (tail.truncated ? 'live · latest 160 lines' : 'live · complete tail')
        : 'live · waiting';
    } catch (_) {
      state.textContent = 'live · reconnecting';
    } finally {
      requestInFlight = false;
    }
  }

  refreshTranscript();
  window.setInterval(refreshTranscript, 2000);
})();"#
}

fn transcript_panel(run_id: String, preview: TranscriptPreview) -> impl IntoView {
    let state = if !preview.available {
        "not recorded"
    } else if preview.truncated {
        "latest 160 lines"
    } else {
        "complete tail"
    };
    let empty = preview.body.trim().is_empty();
    let tail_url = if is_safe_run_id(&run_id) {
        format!("/api/control/runs/{run_id}/transcript")
    } else {
        String::new()
    };

    view! {
        <section
            class="control-panel control-panel-wide transcript-panel"
            aria-label="Human transcript preview"
            data-live-transcript
            data-tail-url=tail_url
        >
            <div class="control-panel-head">
                <div>
                    <p class="mono-cap">"Live worker voice"</p>
                    <h2>"transcript.human.log"</h2>
                </div>
                <span data-transcript-state>{format!("live · {state}")}</span>
            </div>
            <p class="server-console-links">
                <button type="button" class="server-console-link" data-copy=preview.body.clone()>"Copy transcript"</button>
                <button type="button" class="server-console-link" data-copy=format!("/run/{run_id}")>"Copy link"</button>
            </p>
            <p class="control-empty" data-transcript-empty hidden={preview.available && !empty}>
                {if preview.available {
                    "The human transcript exists but is empty."
                } else {
                    "No bounded human transcript is available for this run."
                }}
            </p>
            <pre class="transcript-preview" data-transcript-output hidden={!preview.available || empty}>{preview.body}</pre>
            <script inner_html=transcript_tail_script()></script>
        </section>
    }
}

#[component]
pub fn RunDetailPage() -> impl IntoView {
    let params = leptos_router::hooks::use_params_map();
    let run_id = params.read_untracked().get("run_id").unwrap_or_default();
    let detail = load_run_detail(&run_id);

    view! {
        <Title text=format!("run {run_id} - vc-server") />
        <Meta name="description" content="Vibecrafted single-run observability." />
        {run_detail_view(detail)}
    }
}

fn run_detail_view(detail: RunDetailData) -> impl IntoView {
    let run_id = detail.run_id.clone();
    let api_href = is_safe_run_id(&run_id).then(|| format!("/api/control/runs/{run_id}"));
    let nav_status = detail
        .run
        .as_ref()
        .map(|run| {
            if run.health.is_empty() {
                "run detail".to_string()
            } else {
                run.health.clone()
            }
        })
        .or_else(|| detail.lifecycle.as_ref().map(|run| run.status.clone()))
        .unwrap_or_else(|| "not found".to_string());
    let run = detail.run;
    let lifecycle = detail.lifecycle;
    let transcript = detail.transcript;
    let events = detail.events;
    let no_events = events.is_empty();
    let control_plane = detail.control_plane;

    view! {
        <ServerFrame active=ServerSection::Runs status=nav_status>
            <div class="server-console-shell run-detail-shell">
                <section class="run-detail-header">
                    <div>
                        <p class="section-eyebrow">"Run observability"</p>
                        <h1 class="run-detail-title">{run_id.clone()}</h1>
                    </div>
                    <p class="server-console-links">
                        <a class="server-console-link" href="/">"← Overview"</a>
                        {api_href.map(|href| view! {
                            <a class="server-console-link" href=href>"Raw run JSON"</a>
                        })}
                        <a class="server-console-link" href="/api/control/events">"Event stream"</a>
                    </p>
                </section>

                {transcript_panel(run_id.clone(), transcript)}

                {match (run, lifecycle) {
                    (Some(run), _) => leptos::either::EitherOf3::A(run_body(run)),
                    (None, Some(lifecycle)) => {
                        leptos::either::EitherOf3::B(lifecycle_body(run_id.clone(), lifecycle))
                    }
                    (None, None) => leptos::either::EitherOf3::C(missing_body(run_id.clone())),
                }}

                <section class="control-panel control-panel-wide" aria-label="Run events">
                    <div class="control-panel-head">
                        <h2>"Recent events"</h2>
                        <span>{events.len()}</span>
                    </div>
                    <p class="control-empty" hidden={!no_events}>
                        "No events for this run in the current tail."
                    </p>
                    <ul class="control-event-list">
                        {event_rows(events)}
                    </ul>
                </section>

                <p class="control-plane-meta run-detail-plane">
                    <span>{control_plane}</span>
                </p>
            </div>
        </ServerFrame>
    }
}

fn run_body(run: RunDetailView) -> impl IntoView {
    let settlement = if run.settlement_tui.is_empty() {
        "settle:—".to_string()
    } else {
        format!("settle:{}", run.settlement_tui)
    };
    let seal = run.seal;
    let has_seal = seal.is_some();
    let trust = run.trust_receipt;
    let has_trust = trust.is_some();
    let last_error = run.last_error;
    let has_error = !last_error.is_empty();

    view! {
        <section class="control-panel control-panel-wide" aria-label="Run identity">
            <div class="control-run-tags">
                <span class="control-badge">{run.state}</span>
                <span class="control-badge">{run.health}</span>
                <span class="control-badge">{settlement}</span>
                <span class="control-badge">{run.agent}</span>
                <span class="control-badge">{run.skill}</span>
                <span class="control-badge">{run.mode}</span>
            </div>
            <p class="control-run-error" hidden={!has_error}>{last_error}</p>
            <dl class="run-detail-grid">
                {fact("root", run.root)}
                {fact("source", run.source)}
                {fact("operator session", run.operator_session)}
                {fact("started", run.started_at)}
                {fact("updated", run.updated_at)}
                {fact("completed", run.completed_at)}
            </dl>
        </section>

        <section class="control-panel control-panel-wide" aria-label="Delivery proof">
            <div class="control-panel-head">
                <h2>"Delivery proof"</h2>
                <span>{if has_seal { "sealed" } else { "no seal" }}</span>
            </div>
            <div class="control-run-tags">
                {axis_badge("execution", run.execution_state.clone())}
                {axis_badge("proof", run.proof_state.clone())}
                {axis_badge("delivery", run.delivery_state.clone())}
            </div>
            <p class="control-empty" hidden={!run.execution_state.is_empty()}>
                "Pre-kernel snapshot: delivery axes were never recorded, so they stay absent here."
            </p>
            {seal.map(|seal| view! {
                <dl class="run-detail-grid">
                    {fact("seal id", seal.seal_id)}
                    {fact("issued", seal.issued_at)}
                    {fact("issuer", seal.issuer)}
                    {fact("cut", seal.cut_id)}
                    {fact("repo", seal.repo)}
                    {fact("branch", seal.branch)}
                    {fact("final head", seal.final_head)}
                </dl>
            })}
            {trust.map(|receipt| view! {
                <dl class="run-detail-grid">
                    {fact("trust receipt", receipt.receipt_id)}
                    {fact("trust verdict", receipt.trust_verdict)}
                    {fact("trusted commit", receipt.commit_sha)}
                    {fact("settlement revision", receipt.settlement_revision)}
                </dl>
            })}
            <p class="control-empty" hidden={has_trust}>
                "No vc-trust receipt is attached to this run."
            </p>
        </section>

        <section class="control-panel control-panel-wide" aria-label="Settlement">
            <div class="control-panel-head">
                <h2>"Settlement"</h2>
                <span>{if run.settlement_verdict.is_empty() { "unsettled" } else { "settled" }}</span>
            </div>
            <dl class="run-detail-grid">
                {fact("verdict", run.settlement_verdict)}
                {fact("reason", run.settlement_reason)}
                {fact("source", run.settlement_source)}
                {fact("at", run.settlement_at)}
            </dl>
        </section>

        <section class="control-panel control-panel-wide" aria-label="Process and sessions">
            <div class="control-panel-head">
                <h2>"Process & sessions"</h2>
                <span>{run.liveness.clone()}</span>
            </div>
            <dl class="run-detail-grid">
                {fact("liveness", run.liveness)}
                {fact("exit code", run.exit_code)}
                {fact("launcher pid", run.launcher_pid)}
                {fact("worker pid", run.worker_pid)}
                {fact("worker pgid", run.worker_pgid)}
                {fact("worker alive", run.worker_alive)}
                {fact("lock present", run.lock_present.to_string())}
                {fact("recovery required", run.recovery_required.to_string())}
                {fact("stop reason", run.stop_reason)}
                {fact("loop", run.current_loop)}
                {fact("total loops", run.total_loops)}
                {fact("attempt", run.attempt)}
                {fact("resume of", run.resume_of)}
                {fact("commit", run.commit_sha)}
                {fact("session", run.session_id)}
                {fact("agent session", run.agent_session_id)}
                {fact("runtime session", run.runtime_session_id)}
            </dl>
        </section>

        <section class="control-panel control-panel-wide" aria-label="Artifacts">
            <div class="control-panel-head">
                <h2>"Artifacts"</h2>
            </div>
            <ul class="run-detail-artifacts">
                {artifact_path("report", run.latest_report)}
                {artifact_path("transcript", run.latest_transcript)}
            </ul>
        </section>
    }
}

fn lifecycle_body(run_id: String, lifecycle: LifecycleDetailView) -> impl IntoView {
    let lifecycle_href = format!("/api/control/lifecycle/{run_id}");
    view! {
        <section class="control-panel control-panel-wide" aria-label="Lifecycle run">
            <div class="control-panel-head">
                <h2>"Lifecycle run"</h2>
                <span>{lifecycle.status.clone()}</span>
            </div>
            <p>
                "This id is a lifecycle baton relay, not a worker snapshot — "
                "stage state below, full nested JSON behind the JSON link."
            </p>
            <dl class="run-detail-grid">
                {fact("workflow", lifecycle.workflow)}
                {fact("status", lifecycle.status)}
                {fact("current stage", lifecycle.current_stage)}
                {fact("next stage", lifecycle.next_stage)}
                {fact("next agent", lifecycle.next_agent)}
                {fact("updated", lifecycle.updated_at)}
            </dl>
            <p class="server-console-links">
                <a class="server-console-link" href=lifecycle_href>"Full lifecycle JSON"</a>
            </p>
        </section>
    }
}

fn missing_body(run_id: String) -> impl IntoView {
    view! {
        <section class="control-panel control-panel-wide" aria-label="Run not found">
            <div class="control-panel-head">
                <h2>"Run not found"</h2>
            </div>
            <p>
                {format!(
                    "No run or lifecycle snapshot named `{run_id}` exists in this control plane."
                )}
            </p>
        </section>
    }
}

#[cfg(all(test, feature = "ssr"))]
mod tests {
    use std::fs;
    use std::io::ErrorKind;
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicU64, Ordering};

    use chrono::Utc;
    use control_core::ControlPlane;
    use leptos::prelude::*;
    use serde_json::{Value, json};

    use super::{load_run_detail_from, run_detail_view};
    use crate::theme::provide_theme_context;

    fn temp_home() -> PathBuf {
        static NEXT_ID: AtomicU64 = AtomicU64::new(0);

        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let base = std::env::var_os("TMPDIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/tmp"));

        for attempt in 0..100 {
            let nonce = NEXT_ID.fetch_add(1, Ordering::Relaxed);
            let candidate = base.join(format!(
                "vc-web-run-detail-{}-{nanos}-{nonce}-{attempt}",
                std::process::id()
            ));
            match fs::create_dir(&candidate) {
                Ok(()) => return candidate,
                Err(error) if error.kind() == ErrorKind::AlreadyExists => continue,
                Err(error) => panic!("create isolated fixture home: {error}"),
            }
        }

        panic!("could not allocate an isolated fixture home")
    }

    fn write_snapshot(runs_dir: &Path, run_id: &str, report: &str) {
        let payload = json!({
            "run_id": run_id,
            "state": "completed",
            "agent": "codex",
            "skill": "implement",
            "mode": "implement",
            "root": "/tmp/repo",
            "operator_session": format!("repo-{run_id}"),
            "latest_report": report,
            "latest_transcript": "",
            "last_error": "",
            "updated_at": "2026-08-09T12:00:00+00:00",
            "started_at": "2026-08-09T11:59:00+00:00",
            "health": "final",
            "source": "agent-meta",
            "lock_present": false,
            "exit_code": 0,
            "liveness": "terminal",
            "launcher_pid": Value::Null,
            "completed_at": "2026-08-09T12:00:00+00:00",
            "session_id": "sess-1234",
            "current_loop": Value::Null,
            "total_loops": Value::Null,
            "settlement_verdict": "finalized",
            "settlement_tui": "f",
            "delivery": {
                "execution_state": "exited",
                "proof_state": "passed",
                "delivery_state": "delivered",
            },
            "execution_state": "exited",
            "proof_state": "passed",
            "delivery_state": "delivered",
        });
        fs::write(
            runs_dir.join(format!("{run_id}.json")),
            serde_json::to_vec_pretty(&payload).expect("snapshot JSON"),
        )
        .expect("write snapshot");
    }

    fn render(home: &Path, run_id: &str) -> String {
        let plane = ControlPlane::new(home);
        let now = chrono::DateTime::parse_from_rfc3339("2026-08-09T12:30:00+00:00")
            .expect("fixed now")
            .with_timezone(&Utc);
        let detail = load_run_detail_from(&plane, run_id, now);
        let owner = Owner::new();
        owner.with(|| {
            provide_theme_context();
            run_detail_view(detail).to_html()
        })
    }

    #[test]
    fn run_detail_renders_identity_axes_and_artifact_links() {
        let home = temp_home();
        let runs_dir = home.join("control_plane/runs");
        fs::create_dir_all(&runs_dir).expect("runs dir");
        write_snapshot(
            &runs_dir,
            "impl-260809-120000-1",
            "/tmp/repo/reports/final.md",
        );

        let html = render(&home, "impl-260809-120000-1");

        assert!(html.contains("impl-260809-120000-1"));
        assert!(html.contains("settle:f"));
        assert!(html.contains("execution: exited"));
        assert!(html.contains("proof: passed"));
        assert!(html.contains("delivery: delivered"));
        assert!(html.contains("/api/control/runs/impl-260809-120000-1"));
        assert!(html.contains("/tmp/repo/reports/final.md"));
        assert!(html.contains("← Overview"));
        assert!(html.contains("Vibecrafted server navigation"));
        assert!(html.contains("transcript.human.log"));
        assert!(html.contains("aria-label=\"Delivery proof\""));
        assert!(html.contains("aria-label=\"Process and sessions\""));

        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn artifact_paths_are_text_not_navigation() {
        let home = temp_home();
        let runs_dir = home.join("control_plane/runs");
        fs::create_dir_all(&runs_dir).expect("runs dir");
        write_snapshot(&runs_dir, "unsafe-artifact", "javascript:alert(1)");

        let html = render(&home, "unsafe-artifact");

        assert!(html.contains("javascript:alert(1)"));
        assert!(!html.contains("href=\"javascript:alert(1)\""));

        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn human_transcript_preview_reads_only_the_bounded_canonical_tail() {
        let home = temp_home();
        let run_id = "impl-260809-120000-tail";
        let runs_dir = home.join("control_plane/runs");
        let transcript_dir = home.join("control_plane/runtime_runs").join(run_id);
        fs::create_dir_all(&runs_dir).expect("runs dir");
        fs::create_dir_all(&transcript_dir).expect("transcript dir");
        write_snapshot(&runs_dir, run_id, "/tmp/repo/reports/final.md");
        let transcript = (0..240)
            .map(|line| format!("\u{1b}[36mhuman-line-{line:03}\u{1b}[0m"))
            .collect::<Vec<_>>()
            .join("\n");
        fs::write(transcript_dir.join("transcript.human.log"), transcript)
            .expect("human transcript");

        let html = render(&home, run_id);

        assert!(html.contains("latest 160 lines"));
        assert!(html.contains("human-line-239"));
        assert!(!html.contains("human-line-000"));
        assert!(!html.contains("\u{1b}[36m"));
        assert!(html.contains(&format!("/api/control/runs/{run_id}/transcript")));
        assert!(html.contains("setInterval(refreshTranscript, 2000)"));
        assert!(html.contains("data-transcript-output"));

        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn invalid_run_id_never_becomes_a_control_plane_lookup_or_api_link() {
        let home = temp_home();
        fs::create_dir_all(home.join("control_plane/runs")).expect("runs dir");

        let html = render(&home, "../secret");

        assert!(html.contains("Run not found"));
        assert!(!html.contains("/api/control/runs/../secret"));
        assert!(!html.contains("aria-label=\"Delivery proof\""));

        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn run_detail_is_honest_about_missing_runs() {
        let home = temp_home();
        fs::create_dir_all(home.join("control_plane/runs")).expect("runs dir");

        let html = render(&home, "ghost-run");

        assert!(html.contains("Run not found"));
        assert!(html.contains("ghost-run"));
        assert!(!html.contains("aria-label=\"Delivery proof\""));

        fs::remove_dir_all(home).ok();
    }
}
