// Application shell + root component.

use leptos::prelude::*;
use leptos_meta::{Link, Meta, Title};
use leptos_router::components::{Route, Router, Routes};
use leptos_router::path;
use serde::{Deserialize, Serialize};

use crate::chrome::{ServerFrame, ServerSection, overview_welcome_line};
use crate::run_detail::RunDetailPage;

// The SSR page embeds the dashboard JSON and the hydrated client reads it
// back; the featureless build renders only the loading stub.
#[cfg(any(feature = "ssr", feature = "hydrate"))]
const DASHBOARD_EMBED_ID: &str = "vc-dashboard-data";

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub(crate) struct DashboardData {
    server_status: String,
    control_plane: String,
    control_status: String,
    control_error: String,
    generated_at: String,
    workspace_status: String,
    workspace_error: String,
    workspaces: Vec<DashboardWorkspace>,
    sessions: Vec<DashboardSession>,
    /// vc-frame sessions whose server runs right now — what the console
    /// calls workspaces — each joined with its catalog workspace when a
    /// session record claims it.
    #[serde(default)]
    live_frame_sessions: Vec<DashboardFrameSession>,
    settlement: DashboardSettlement,
    active_runs: Vec<DashboardRun>,
    stalled_runs: Vec<DashboardRun>,
    recent_runs: Vec<DashboardRun>,
    lifecycle_runs: Vec<DashboardLifecycleRun>,
    warnings: Vec<String>,
    events: Vec<DashboardEvent>,
    loctree_report: String,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
struct DashboardWorkspace {
    workspace_id: String,
    title: String,
    root: String,
    status: String,
    selected: bool,
    active_runs: usize,
    recent_runs: usize,
    updated_at: String,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
struct DashboardSession {
    session_id: String,
    workspace_id: String,
    workspace_title: String,
    workspace_instance_id: String,
    runtime: String,
    state: String,
    updated_at: String,
    runs: Vec<DashboardSessionRun>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
struct DashboardSessionRun {
    run_id: String,
    state: String,
    health: String,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
struct DashboardFrameSession {
    /// vc-frame session name, as `vc-frame list-sessions` prints it.
    name: String,
    /// Empty when the Frame runs but no workspace session record claims it.
    session_id: String,
    workspace_id: String,
    workspace_title: String,
    workspace_root: String,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
struct DashboardSettlement {
    scope: String,
    active: usize,
    f: usize,
    x: usize,
    n: usize,
    invalid: usize,
    unclassified: usize,
    total_settled: usize,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
struct DashboardRun {
    run_id: String,
    logical_session_id: String,
    state: String,
    health: String,
    agent: String,
    skill: String,
    mode: String,
    root: String,
    latest_report: String,
    latest_transcript: String,
    updated_at: String,
    /// Settlement tui cell when Python wrote one (`f`/`x`/`n`), else empty.
    settlement_tui: String,
    /// Python settlement verdict (`finalized` / `failed` / `needs_attention` /
    /// `invalid`). Empty when the snapshot omitted one. Never derived from
    /// an exit code or from the tui cell.
    #[serde(default)]
    settlement_verdict: String,
    /// Plan id already present on the recorded report path. Empty hides the plan door.
    #[serde(default)]
    plan_id: String,
    /// Scaffold href for [`Self::plan_id`], with org/repo/day when the path has them.
    #[serde(default)]
    plan_href: String,
    /// True only when a transcript file can actually be opened.
    #[serde(default)]
    transcript_open: bool,
    last_error: String,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
struct DashboardLifecycleRun {
    run_id: String,
    workflow: String,
    status: String,
    current_stage: String,
    next_stage: String,
    next_agent: String,
    dou_label: String,
    accepted_dou: i64,
    human_controls: Vec<String>,
    human_controls_count: usize,
    operator_actions_count: usize,
    next_action: String,
    updated_at: String,
    #[serde(default)]
    report_path: String,
    #[serde(default)]
    transcript_path: String,
    #[serde(default)]
    plan_id: String,
    #[serde(default)]
    plan_href: String,
    #[serde(default)]
    transcript_open: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
struct DashboardEvent {
    ts: String,
    run_id: String,
    kind: String,
    message: String,
}

#[cfg(feature = "ssr")]
fn unique_runtime_labels<I, S>(labels: I) -> String
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    labels
        .into_iter()
        .map(|label| label.as_ref().trim().to_string())
        .filter(|label| !label.is_empty())
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>()
        .join(", ")
}

/// Serde is the naming authority for the Python verdict. The tui cell is not
/// a substitute: `x` covers both `failed` and `invalid`.
#[cfg(feature = "ssr")]
fn settlement_verdict_wire(value: &Option<control_core::SettlementVerdict>) -> String {
    value
        .as_ref()
        .and_then(|inner| serde_json::to_value(inner).ok())
        .and_then(|json| json.as_str().map(str::to_owned))
        .unwrap_or_default()
}

/// A transcript door exists only when a log file can be opened. A recorded
/// path that is missing, or a symlink `transcript_open` refuses, stays hidden.
#[cfg(feature = "ssr")]
fn transcript_log_is_open(
    plane: &control_core::ControlPlane,
    run_id: &str,
    latest_transcript: &str,
) -> bool {
    if crate::run_detail::human_transcript_is_open(plane, run_id) {
        return true;
    }
    let path = latest_transcript.trim();
    if path.is_empty() {
        return false;
    }
    control_core::transcript_open::open_provider_transcript(std::path::Path::new(path)).is_ok()
}

#[cfg(feature = "ssr")]
fn load_dashboard_data() -> DashboardData {
    use chrono::Utc;
    use control_core::ControlPlane;

    load_dashboard_data_from(&ControlPlane::from_env(), Utc::now())
}

#[cfg(feature = "ssr")]
fn load_dashboard_data_from(
    plane: &control_core::ControlPlane,
    now: chrono::DateTime<chrono::Utc>,
) -> DashboardData {
    use control_core::{Event, LifecycleRunSummary, RunStatus};

    fn run_summary(plane: &control_core::ControlPlane, run: RunStatus) -> DashboardRun {
        let settlement_tui = run
            .settlement_tui
            .map(|cell| match cell {
                control_core::SettlementTui::F => "f",
                control_core::SettlementTui::X => "x",
                control_core::SettlementTui::N => "n",
            })
            .unwrap_or("")
            .to_string();
        let settlement_verdict = settlement_verdict_wire(&run.settlement_verdict);
        let (plan_id, plan_href) = plan_from_report_path(&run.latest_report);
        let transcript_open = transcript_log_is_open(plane, &run.run_id, &run.latest_transcript);
        DashboardRun {
            run_id: run.run_id,
            logical_session_id: run.logical_session_id,
            state: run.state,
            health: run.health,
            agent: run.agent,
            skill: run.skill,
            mode: run.mode,
            root: run.root,
            latest_report: run.latest_report,
            latest_transcript: run.latest_transcript,
            updated_at: run.updated_at,
            settlement_tui,
            settlement_verdict,
            plan_id,
            plan_href,
            transcript_open,
            last_error: run.last_error,
        }
    }

    fn event_summary(event: Event) -> DashboardEvent {
        DashboardEvent {
            ts: event.ts,
            run_id: event.run_id,
            kind: event.kind,
            message: event.message,
        }
    }

    fn lifecycle_summary(
        plane: &control_core::ControlPlane,
        run: LifecycleRunSummary,
    ) -> DashboardLifecycleRun {
        let dou_label = match (run.dou_readiness.as_str(), run.dou_index) {
            ("zero", Some(0)) => "ZERO DoU".to_string(),
            ("open", Some(value)) => format!("DoU {value}"),
            _ => "DoU unknown".to_string(),
        };
        let (plan_id, plan_href) = plan_from_report_path(&run.report_path);
        let transcript_open = transcript_log_is_open(plane, &run.run_id, &run.transcript_path);
        DashboardLifecycleRun {
            run_id: run.run_id,
            workflow: run.workflow,
            status: run.status,
            current_stage: run.current_stage,
            next_stage: run.next_stage,
            next_agent: run.next_agent,
            dou_label,
            accepted_dou: run.accepted_dou,
            human_controls: run.human_controls,
            human_controls_count: run.human_controls_count,
            operator_actions_count: run.operator_actions_count,
            next_action: run.next_action,
            updated_at: run.updated_at,
            report_path: run.report_path,
            transcript_path: run.transcript_path,
            plan_id,
            plan_href,
            transcript_open,
        }
    }

    let control_root = plane.control_plane_home();
    let (control_status, control_error) = match std::fs::read_dir(&control_root) {
        Ok(_) => ("available".to_string(), String::new()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            ("not_initialized".to_string(), String::new())
        }
        Err(error) => ("unavailable".to_string(), error.to_string()),
    };
    let state = crate::control::api::state_payload(plane, now);
    let lifecycle_runs = plane.load_recent_lifecycle_run_summaries(24);
    let settlement = state.settlement_counts;
    let loctree_report = state
        .active_runs
        .iter()
        .chain(state.recent_runs.iter())
        .filter_map(|run| {
            let path = std::path::Path::new(&run.root).join(".loctree/report.html");
            path.is_file().then(|| path.to_string_lossy().into_owned())
        })
        .next()
        .unwrap_or_default();

    let mut warnings = state.warnings;
    if control_status == "unavailable" {
        warnings.push(format!("Control-plane data unavailable: {control_error}"));
    }
    let (workspace_status, workspace_error, workspaces, sessions, live_frame_sessions) =
        match plane.load_workspace_projection() {
            Ok(projection) => {
                let catalog_labels = projection
                    .catalog
                    .as_ref()
                    .map(|catalog| {
                        catalog
                            .workspaces
                            .iter()
                            .map(|workspace| {
                                (
                                    workspace.workspace_id.clone(),
                                    (
                                        workspace.display_label.clone(),
                                        workspace.canonical_root.clone(),
                                    ),
                                )
                            })
                            .collect::<std::collections::HashMap<_, _>>()
                    })
                    .unwrap_or_default();
                // One bounded read of the recorded Frame socket roots per
                // refresh. No vc-frame client is spawned or connected: each
                // client connection re-renders every plugin of that session.
                let inventory =
                    control_core::FrameSessionInventory::scan(projection.frame_socket_dirs());
                let live_frames = projection.live_frame_sessions(&inventory);
                let live_session_ids = live_frames
                    .iter()
                    .filter_map(|frame| frame.owner.as_ref())
                    .map(|owner| owner.session_id.clone())
                    .collect::<std::collections::HashSet<_>>();
                let live_frame_sessions = live_frames
                    .into_iter()
                    .map(|frame| {
                        let (session_id, workspace_id) = frame
                            .owner
                            .map(|owner| (owner.session_id, owner.workspace_id))
                            .unwrap_or_default();
                        let (workspace_title, workspace_root) = catalog_labels
                            .get(&workspace_id)
                            .cloned()
                            .unwrap_or_default();
                        DashboardFrameSession {
                            name: frame.runtime_session_id,
                            session_id,
                            workspace_id,
                            workspace_title,
                            workspace_root,
                        }
                    })
                    .collect::<Vec<_>>();
                let mut runs_by_logical_session =
                    std::collections::HashMap::<String, Vec<DashboardSessionRun>>::new();
                for run in state.active_runs.iter().chain(state.recent_runs.iter()) {
                    let session_id = run.logical_session_id.trim();
                    if session_id.is_empty() {
                        continue;
                    }
                    let entries = runs_by_logical_session
                        .entry(session_id.to_string())
                        .or_default();
                    if !entries.iter().any(|entry| entry.run_id == run.run_id) {
                        entries.push(DashboardSessionRun {
                            run_id: run.run_id.clone(),
                            state: run.state.clone(),
                            health: run.health.clone(),
                        });
                    }
                }
                let sessions = projection
                    .sessions
                    .into_iter()
                    .map(|session| {
                        let runtime = unique_runtime_labels(
                            session
                                .attachments
                                .iter()
                                .map(|attachment| attachment.runtime.as_str()),
                        );
                        let runs = runs_by_logical_session
                            .remove(&session.session_id)
                            .unwrap_or_default();
                        // `live` means this session owns a vc-frame server
                        // that runs right now. A recorded `live` attachment is
                        // attach-time evidence that nothing downgrades when the
                        // Frame exits, and a canonical run keeps its own state
                        // on the transcript link instead of posing as a
                        // running terminal.
                        let state = if live_session_ids.contains(&session.session_id) {
                            "live"
                        } else if session.attachments.is_empty() {
                            "detached"
                        } else {
                            "inactive"
                        };
                        DashboardSession {
                            workspace_title: catalog_labels
                                .get(&session.workspace_id)
                                .map(|(title, _)| title.clone())
                                .unwrap_or_else(|| "Unknown workspace".into()),
                            session_id: session.session_id,
                            workspace_id: session.workspace_id,
                            workspace_instance_id: session.workspace_instance_id,
                            runtime,
                            state: state.into(),
                            updated_at: session.updated_at,
                            runs,
                        }
                    })
                    .collect();
                match projection.catalog {
                    Some(catalog) => {
                        let workspaces = catalog
                            .workspaces
                            .into_iter()
                            .map(|workspace| {
                                let active_runs = state
                                    .active_runs
                                    .iter()
                                    .filter(|run| run.root == workspace.canonical_root)
                                    .count();
                                let recent_runs = state
                                    .recent_runs
                                    .iter()
                                    .filter(|run| run.root == workspace.canonical_root)
                                    .count();
                                DashboardWorkspace {
                                    selected: catalog.selected_workspace_id.as_deref()
                                        == Some(workspace.workspace_id.as_str()),
                                    workspace_id: workspace.workspace_id,
                                    title: workspace.display_label,
                                    root: workspace.canonical_root,
                                    status: workspace.status,
                                    active_runs,
                                    recent_runs,
                                    updated_at: workspace.updated_at,
                                }
                            })
                            .collect();
                        (
                            "available".into(),
                            String::new(),
                            workspaces,
                            sessions,
                            live_frame_sessions,
                        )
                    }
                    None => (
                        "not_initialized".into(),
                        String::new(),
                        Vec::new(),
                        sessions,
                        live_frame_sessions,
                    ),
                }
            }
            Err(error) => {
                let message = error.to_string();
                warnings.push(format!("Workspace data unavailable: {message}"));
                (
                    "unavailable".into(),
                    message,
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                )
            }
        };

    DashboardData {
        server_status: "healthy".into(),
        control_plane: state.control_plane,
        control_status,
        control_error,
        generated_at: state.generated_at,
        workspace_status,
        workspace_error,
        workspaces,
        sessions,
        live_frame_sessions,
        settlement: DashboardSettlement {
            scope: serde_json::to_value(settlement.scope)
                .ok()
                .and_then(|value| value.as_str().map(str::to_owned))
                .unwrap_or_else(|| "unknown".to_string()),
            active: settlement.active,
            f: settlement.f,
            x: settlement.x,
            n: settlement.n,
            invalid: settlement.invalid,
            unclassified: settlement.unclassified,
            total_settled: settlement.total_settled,
        },
        active_runs: state
            .active_runs
            .into_iter()
            .map(|run| run_summary(plane, run))
            .collect(),
        stalled_runs: state
            .stalled_runs
            .into_iter()
            .map(|run| run_summary(plane, run))
            .collect(),
        recent_runs: state
            .recent_runs
            .into_iter()
            .map(|run| run_summary(plane, run))
            .collect(),
        lifecycle_runs: lifecycle_runs
            .into_iter()
            .map(|run| lifecycle_summary(plane, run))
            .collect(),
        warnings,
        events: state.events.into_iter().map(event_summary).collect(),
        loctree_report,
    }
}

#[cfg(any(feature = "ssr", feature = "hydrate"))]
fn encode_dashboard_embed(data: &DashboardData) -> String {
    serde_json::to_string(data)
        .unwrap_or_else(|_| "{}".to_string())
        .replace('<', "\\u003c")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029")
}

#[cfg(any(
    all(test, feature = "ssr"),
    all(feature = "hydrate", not(feature = "ssr"))
))]
fn decode_dashboard_embed(json: &str) -> Option<DashboardData> {
    let data: DashboardData = serde_json::from_str(json).ok()?;
    if data == DashboardData::default() {
        return None;
    }
    Some(data)
}

#[cfg(any(feature = "ssr", feature = "hydrate"))]
fn dashboard_embed_script(json: String) -> impl IntoView {
    view! {
        <script id=DASHBOARD_EMBED_ID type="application/json" inner_html=json></script>
    }
}

#[cfg(feature = "ssr")]
pub(crate) async fn dashboard_api(
    axum::extract::Extension(plane): axum::extract::Extension<control_core::ControlPlane>,
) -> axum::Json<DashboardData> {
    axum::Json(load_dashboard_data_from(&plane, chrono::Utc::now()))
}

#[cfg(all(feature = "hydrate", not(feature = "ssr")))]
std::thread_local! {
    static CLIENT_DASHBOARD: std::cell::RefCell<Option<DashboardData>> =
        const { std::cell::RefCell::new(None) };
}

#[cfg(all(feature = "hydrate", not(feature = "ssr")))]
fn store_client_dashboard(data: DashboardData) {
    if data == DashboardData::default() {
        return;
    }
    CLIENT_DASHBOARD.with(|slot| *slot.borrow_mut() = Some(data));
}

#[cfg(all(feature = "hydrate", not(feature = "ssr")))]
fn client_dashboard_now() -> Option<DashboardData> {
    CLIENT_DASHBOARD.with(|slot| slot.borrow().clone())
}

#[cfg(all(feature = "hydrate", not(feature = "ssr")))]
fn read_embedded_dashboard() -> Option<DashboardData> {
    let document = web_sys::window()?.document()?;
    let json = document
        .get_element_by_id(DASHBOARD_EMBED_ID)?
        .text_content()
        .filter(|text| !text.trim().is_empty())?;
    decode_dashboard_embed(&json)
}

#[cfg(all(feature = "hydrate", not(feature = "ssr")))]
fn hydrate_dashboard_cache() {
    if client_dashboard_now().is_some() {
        return;
    }
    if let Some(data) = read_embedded_dashboard() {
        store_client_dashboard(data);
    }
}

#[cfg(all(feature = "hydrate", not(feature = "ssr")))]
async fn fetch_dashboard() -> Option<DashboardData> {
    use wasm_bindgen::JsCast;
    use wasm_bindgen_futures::JsFuture;

    let window = web_sys::window()?;
    let response = JsFuture::from(window.fetch_with_str("/api/control/dashboard"))
        .await
        .ok()?;
    let response: web_sys::Response = response.dyn_into().ok()?;
    if !response.ok() {
        return None;
    }
    let json = JsFuture::from(response.text().ok()?)
        .await
        .ok()?
        .as_string()?;
    let data = decode_dashboard_embed(&json)?;
    store_client_dashboard(data.clone());
    Some(data)
}

#[cfg(all(feature = "hydrate", not(feature = "ssr")))]
fn refresh_client_dashboard() {
    leptos::task::spawn_local(async {
        let _ = fetch_dashboard().await;
    });
}

#[cfg(not(feature = "ssr"))]
fn dashboard_loading() -> impl IntoView {
    view! {
        <ServerFrame active=ServerSection::Overview status="loading".to_string()>
            <p class="control-empty">"Loading…"</p>
        </ServerFrame>
    }
}

#[cfg(feature = "ssr")]
fn control_dashboard(
    render: impl Fn(DashboardData) -> AnyView + Clone + Send + Sync + 'static,
) -> AnyView {
    let data = load_dashboard_data();
    let json = encode_dashboard_embed(&data);
    view! {
        {dashboard_embed_script(json)}
        {render(data)}
    }
    .into_any()
}

#[cfg(all(feature = "hydrate", not(feature = "ssr")))]
fn control_dashboard(
    render: impl Fn(DashboardData) -> AnyView + Clone + Send + Sync + 'static,
) -> AnyView {
    hydrate_dashboard_cache();
    if let Some(data) = client_dashboard_now() {
        refresh_client_dashboard();
        let json = encode_dashboard_embed(&data);
        return view! {
            {dashboard_embed_script(json)}
            {render(data)}
        }
        .into_any();
    }

    let render_view = render.clone();
    let dashboard = LocalResource::new(fetch_dashboard);
    view! {
        <Suspense fallback=move || dashboard_loading().into_any()>
            {move || {
                let render_view = render_view.clone();
                dashboard.get().flatten().map(move |data| {
                    let json = encode_dashboard_embed(&data);
                    view! {
                        {dashboard_embed_script(json)}
                        {render_view(data)}
                    }
                    .into_any()
                })
            }}
        </Suspense>
    }
    .into_any()
}

#[cfg(not(any(feature = "ssr", feature = "hydrate")))]
fn control_dashboard(
    render: impl Fn(DashboardData) -> AnyView + Clone + Send + Sync + 'static,
) -> AnyView {
    let _ = render;
    dashboard_loading().into_any()
}

fn settlement_badge(tui: &str) -> String {
    if tui.is_empty() {
        "settle:—".to_string()
    } else {
        format!("settle:{tui}")
    }
}

fn run_cards(runs: Vec<DashboardRun>) -> impl IntoView {
    runs.into_iter()
        .map(|run| {
            let report_label = if run.latest_report.is_empty() {
                "no report".to_string()
            } else {
                run.latest_report.clone()
            };
            // Civilized console link: every run id opens its observability page.
            let detail_href = format!("/run/{}", run.run_id);
            let transcript_url = format!("/api/control/runs/{}/transcript", run.run_id);
            let run_id = run.run_id.clone();
            let root = run.root.clone();
            let run_id_attr = run_id.clone();
            let run_id_copy = run_id.clone();
            let root_attr = root.clone();
            let href_attr = detail_href.clone();
            let href_link = detail_href.clone();
            let href_copy = detail_href.clone();
            view! {
                <article
                    class="control-run-row"
                    data-ppm="run"
                    data-run-id=run_id_attr
                    data-href=href_attr
                    data-focus-root=root_attr
                    data-transcript-url=transcript_url
                >
                    <div class="control-run-primary">
                        <a class="control-run-id" href=href_link data-copy=run_id_copy>{run_id}</a>
                        <span class="control-run-root">{root}</span>
                    </div>
                    <div class="control-run-tags">
                        <span class="control-badge">{run.state}</span>
                        <span class="control-badge">{run.health}</span>
                        <span class="control-badge">{settlement_badge(&run.settlement_tui)}</span>
                        <span class="control-badge">{run.agent}</span>
                        <span class="control-badge">{run.skill}</span>
                        <span class="control-badge">{run.mode}</span>
                    </div>
                    <div class="control-run-meta">
                        <span>{run.updated_at}</span>
                        <span>{report_label}</span>
                        <span class="control-run-error">{run.last_error}</span>
                        <button type="button" class="control-copy" data-copy=href_copy>"Copy"</button>
                    </div>
                </article>
            }
        })
        .collect_view()
}

fn is_terminal_state(state: &str) -> bool {
    matches!(
        state.to_ascii_lowercase().as_str(),
        "report_validated"
            | "completed"
            | "closed"
            | "converged"
            | "finalized"
            | "failed"
            | "blocked"
            | "cancelled"
            | "stopped"
    )
}

fn is_quarantined_run(run: &DashboardRun) -> bool {
    run.run_id == "smoke-nonexistent"
        || run.run_id.starts_with("smoke-")
        || (run.skill.eq_ignore_ascii_case("marbles") && run.health != "active")
}

fn operator_active_runs(runs: Vec<DashboardRun>) -> Vec<DashboardRun> {
    runs.into_iter()
        .filter(|run| {
            run.health == "active" && !is_terminal_state(&run.state) && !is_quarantined_run(run)
        })
        .take(8)
        .collect()
}

fn operator_action_runs(runs: Vec<DashboardLifecycleRun>) -> Vec<DashboardLifecycleRun> {
    runs.into_iter()
        .filter(|run| !is_terminal_state(&run.status) && !run.run_id.starts_with("smoke-"))
        .take(6)
        .collect()
}

fn action_cards(runs: Vec<DashboardLifecycleRun>) -> impl IntoView {
    runs.into_iter()
        .map(|run| {
            let stage_label = if run.current_stage.is_empty() {
                "stage unknown".to_string()
            } else {
                run.current_stage.clone()
            };
            let next_action = if !run.next_action.is_empty() {
                run.next_action.clone()
            } else if let Some(control) = run.human_controls.first() {
                format!("Operator: {control}")
            } else if !run.next_stage.is_empty() && !run.next_agent.is_empty() {
                format!("Launch {} with {}", run.next_stage, run.next_agent)
            } else if !run.next_stage.is_empty() {
                format!("Advance to {}", run.next_stage)
            } else if !run.next_agent.is_empty() {
                format!("Hand off to {}", run.next_agent)
            } else {
                "Inspect the latest runtime event".to_string()
            };

            let detail_href = format!("/run/{}", run.run_id);

            view! {
                <article class="operator-action-row">
                    <div class="control-run-primary">
                        <a class="control-run-id" href=detail_href>{run.run_id}</a>
                        <span class="control-run-root">{run.workflow}</span>
                    </div>
                    <div class="control-run-tags">
                        <span class="control-badge">{run.status}</span>
                        <span class="control-badge">{stage_label}</span>
                        <span class="control-badge">{run.dou_label}</span>
                        <span class="control-badge">{format!("accepted {}", run.accepted_dou)}</span>
                    </div>
                    <div class="operator-next-action">
                        <strong>"Next action"</strong>
                        <span>{next_action}</span>
                    </div>
                    <div class="control-run-meta">
                        <span>{run.updated_at}</span>
                        <span>{format!("{} controls / {} actions", run.human_controls_count, run.operator_actions_count)}</span>
                    </div>
                </article>
            }
        })
        .collect_view()
}

fn event_rows(events: Vec<DashboardEvent>) -> impl IntoView {
    events
        .into_iter()
        .map(|event| {
            view! {
                <li class="control-event-row">
                    <span>{event.ts}</span>
                    <strong>{event.kind}</strong>
                    <span>{event.run_id}</span>
                    <span>{event.message}</span>
                </li>
            }
        })
        .collect_view()
}

fn warning_rows(warnings: Vec<String>) -> impl IntoView {
    warnings
        .into_iter()
        .map(|warning| view! { <li>{warning}</li> })
        .collect_view()
}

/// Settlement counts over every run snapshot this host still retains. They
/// describe history, not the present, so they live on the runs page under a
/// plain label — never on the console home beside live counts.
fn retained_run_history(settlement: DashboardSettlement) -> impl IntoView {
    view! {
        <section
            class="control-panel control-panel-wide"
            aria-label="Retained run history"
            data-scope=settlement.scope.clone()
            data-active=settlement.active
            data-f=settlement.f
            data-x=settlement.x
            data-n=settlement.n
            data-invalid=settlement.invalid
            data-unclassified=settlement.unclassified
            data-total-settled=settlement.total_settled
        >
            <div class="control-panel-head">
                <h2>"Retained run history"</h2>
                <span>{format!("{} settled", settlement.total_settled)}</span>
            </div>
            <p class="control-plane-meta">
                "Every run snapshot this host still keeps, from all days — not what is running now. Open runs are in the buckets above."
            </p>
            <p class="control-plane-meta">
                <span>{format!("{} finished", settlement.f)}</span>
                <span>{format!("{} failed ({} invalid)", settlement.x, settlement.invalid)}</span>
                <span>{format!("{} need attention", settlement.n)}</span>
                <span>{format!("{} unclassified", settlement.unclassified)}</span>
                <span>{format!("{} still marked active", settlement.active)}</span>
            </p>
        </section>
    }
}

#[cfg(feature = "ssr")]
pub fn shell(_options: leptos::config::LeptosOptions) -> impl IntoView {
    use leptos_meta::MetaTags;

    use crate::chrome::{
        STYLE_FONTS, STYLE_MAIN, STYLE_TOKENS, operator_desk_script, operator_head_script,
        theme_control_script, theme_head_script,
    };

    view! {
        <!DOCTYPE html>
        <html lang="en">
            <head>
                <meta charset="utf-8"/>
                <meta name="viewport" content="width=device-width, initial-scale=1"/>
                <MetaTags/>
                <script inner_html=theme_head_script()></script>
                <script inner_html=operator_head_script()></script>
                <style>{STYLE_TOKENS}</style>
                <style>{STYLE_FONTS}</style>
                <style>{STYLE_MAIN}</style>
            </head>
            <body>
                <script id=CODE_REPORTS_EMBED_ID type="application/json" inner_html=code_reports_embed_json()></script>
                <App/>
                <script inner_html=theme_control_script()></script>
                <script inner_html=operator_desk_script()></script>
            </body>
        </html>
    }
}

#[component]
pub fn App() -> impl IntoView {
    leptos_meta::provide_meta_context();
    #[cfg(all(feature = "hydrate", not(feature = "ssr")))]
    hydrate_dashboard_cache();

    view! {
        <Router>
            <Routes fallback=NotFoundPage>
                <Route path=path!("/") view=ConsolePage />
                <Route path=path!("/workspaces") view=WorkspacesPage />
                <Route path=path!("/sessions") view=SessionsPage />
                <Route path=path!("/agents") view=AgentManagerPage />
                <Route path=path!("/runs") view=RunsPage />
                <Route path=path!("/usage") view=UsagePage />
                <Route path=path!("/transcripts") view=TranscriptsPage />
                <Route path=path!("/lifecycle") view=LifecyclePage />
                <Route path=path!("/activity") view=ActivityPage />
                <Route path=path!("/structure") view=StructurePage />
                <Route path=path!("/code") view=StructurePage />
                <Route path=path!("/aicx") view=AicxPage />
                <Route path=path!("/history") view=HistoryPage />
                <Route path=path!("/frame") view=FramePage />
                <Route path=path!("/guide") view=GuidePage />
                <Route path=path!("/help") view=HelpPage />
                <Route path=path!("/about") view=AboutPage />
                <Route path=path!("/projects") view=ProjectsPage />
                <Route path=path!("/projects/:org/:repo") view=ProjectPlansPage />
                <Route path=path!("/skills") view=SkillsPage />
                <Route path=path!("/skills/:name") view=SkillEditorPage />
                <Route path=path!("/settings") view=SettingsPage />
                <Route path=path!("/diagnostics") view=DiagnosticsPage />
                <Route path=path!("/run/:run_id") view=RunDetailPage />
            </Routes>
        </Router>
    }
}

#[component]
pub fn UsagePage() -> impl IntoView {
    view! {
        <Title text="Usage - vc-server" />
        <Meta name="description" content="Provider-neutral Vibecrafted token and cost telemetry." />
        <ServerFrame active=ServerSection::Usage status="usage telemetry".to_string()>
            <div class="server-console-shell route-page-shell usage-dashboard" data-usage-dashboard>
                <header class="usage-toolbar">
                    <div class="usage-toolbar-title">
                        <h1>"Cost & usage"</h1>
                        <p id="usage-status" role="status">"Loading canonical telemetry…"</p>
                    </div>
                    <form id="usage-filter-form" class="usage-filter-bar" aria-label="Usage filters">
                        <label><span>"Window"</span><select id="usage-window" name="window">
                            <option value="24h">"24 hours"</option>
                            <option value="7d">"7 days"</option>
                            <option value="30d">"30 days"</option>
                            <option value="all">"All recorded"</option>
                        </select></label>
                        <label><span>"Provider"</span><input id="usage-provider" name="provider" maxlength="128" placeholder="all" /></label>
                        <label><span>"Agent"</span><input id="usage-agent" name="agent" maxlength="128" placeholder="all" /></label>
                        <label><span>"Model"</span><input id="usage-model" name="model" maxlength="128" placeholder="all" /></label>
                        <button type="submit" class="server-console-link server-console-link-primary">"Apply"</button>
                    </form>
                </header>
                <section class="quota-board" aria-label="Live agent quota" data-quota-board>
                    <div class="control-panel-head">
                        <h2>"Live quota"</h2>
                        <span id="quota-status">"local monitors"</span>
                    </div>
                    <div id="quota-agents" class="quota-agents"></div>
                    <p class="control-plane-meta">"agy-monitor and kimi-monitor. Prices are api-equiv, not a bill. A missing file stays quiet."</p>
                    <script inner_html=quota_dashboard_script()></script>
                </section>
                <dl class="usage-summary-grid" aria-label="Usage totals">
                    <div class="usage-attention"><dt>"Failed"</dt><dd id="usage-total-failed">"—"</dd></div>
                    <div class="usage-attention"><dt>"Cost unknowns"</dt><dd id="usage-total-cost-unknown">"—"</dd></div>
                    <div class="usage-attention"><dt>"Token unknowns"</dt><dd id="usage-total-token-unknown">"—"</dd></div>
                    <div class="usage-summary-cost"><dt>"Cost by unit"</dt><dd id="usage-total-cost">"—"</dd></div>
                    <div><dt>"Runs"</dt><dd id="usage-total-runs">"—"</dd></div>
                    <div><dt>"Known tokens"</dt><dd id="usage-total-tokens">"—"</dd></div>
                </dl>
                <section class="usage-charts" aria-label="Usage over time">
                    <div class="usage-hero">
                        <p class="usage-hero-kicker">"Known tokens"</p>
                        <p class="usage-hero-figure" id="usage-hero-tokens">"—"</p>
                        <p class="usage-hero-caption" id="usage-hero-caption"></p>
                    </div>
                    <figure class="usage-chart" id="usage-chart-heat">
                        <figcaption id="usage-chart-heat-title">"Days"</figcaption>
                        <div class="usage-chart-plot" id="usage-chart-heat-plot"></div>
                    </figure>
                    <figure class="usage-chart" id="usage-chart-cost">
                        <figcaption id="usage-chart-cost-title">"Cost"</figcaption>
                        <div class="usage-chart-plot" id="usage-chart-cost-plot"></div>
                    </figure>
                </section>
                <section class="usage-breakdown" aria-label="Usage dimensions">
                    <div class="usage-breakdown-switch" role="tablist" aria-label="Breakdown">
                        <button type="button" role="tab" aria-selected="true" data-usage-dim="providers">"Providers"</button>
                        <button type="button" role="tab" aria-selected="false" data-usage-dim="agents">"Agents"</button>
                        <button type="button" role="tab" aria-selected="false" data-usage-dim="models">"Models"</button>
                    </div>
                    <div id="usage-providers" class="usage-dimension-list is-active" role="tabpanel"></div>
                    <div id="usage-agents" class="usage-dimension-list" role="tabpanel" hidden></div>
                    <div id="usage-models" class="usage-dimension-list" role="tabpanel" hidden></div>
                </section>
                <div class="usage-split">
                <section class="usage-runs-panel" aria-label="Recent usage runs">
                    <div class="usage-runs-head"><h2>"Recent runs"</h2><span id="usage-run-count">"0"</span></div>
                    <div class="usage-table-scroll">
                        <table class="usage-runs-table">
                            <thead><tr><th>"Run"</th><th>"Provider / agent"</th><th>"Model"</th><th>"Tokens"</th><th>"Cost"</th><th>"State"</th></tr></thead>
                            <tbody id="usage-runs-body"></tbody>
                        </table>
                    </div>
                    <p id="usage-empty" class="control-empty" hidden>"No canonical runtime runs match this window and filter."</p>
                    <p class="usage-footnote"><span id="usage-schema">"vibecrafted.usage-report.v1"</span><span id="usage-generated"></span><span>"Unknowns stay visible. Currencies are never combined."</span></p>
                </section>
                <aside class="overview-inspector doc-pane" id="overview-inspector" aria-label="Run document">
                    <div class="doc-tabs" role="tablist" aria-label="Document">
                        <button type="button" data-doc-tab="transcript" class="is-active">"Transcript"</button>
                        <button type="button" data-doc-tab="report">"Report"</button>
                        <button type="button" data-doc-tab="structure">"Structure"</button>
                    </div>
                    <article class="doc-sheet" data-doc-panel="transcript">
                        <p class="doc-kicker">"Run"</p>
                        <h2 data-inspector-id>"Nothing selected"</h2>
                        <p class="doc-meta" data-inspector-meta>"Select a run."</p>
                        <pre class="inspector-tail" data-inspector-tail>"Select a run."</pre>
                    </article>
                    <article class="doc-sheet" data-doc-panel="report" hidden>
                        <p class="doc-kicker">"Report"</p>
                        <p data-inspector-report>"No report on a usage row."</p>
                        <p class="control-run-error" data-inspector-error hidden></p>
                    </article>
                    <article class="doc-sheet" data-doc-panel="structure" hidden>
                        <p class="doc-kicker">"Path"</p>
                        <p data-inspector-root>"Select a run."</p>
                        <a class="doc-path" data-inspector-open href="/structure">"Open structure"</a>
                    </article>
                </aside>
                </div>
                <script inner_html=usage_dashboard_script()></script>
            </div>
        </ServerFrame>
    }
}

fn quota_dashboard_script() -> &'static str {
    r#"(() => {
  const root = document.querySelector('[data-quota-board]');
  const host = document.getElementById('quota-agents');
  const status = document.getElementById('quota-status');
  if (!root || !host || !status) return;
  const ageText = (seconds) => {
    if (seconds === null || seconds === undefined) return '';
    if (seconds < 60) return seconds + 's ago';
    if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
    return Math.floor(seconds / 3600) + 'h ago';
  };
  const money = (value) => value === null || value === undefined ? '' : '≈$' + Number(value).toFixed(3) + ' api-equiv';
  const card = (agent) => {
    const article = document.createElement('article');
    article.className = 'quota-card';
    article.dataset.status = agent.status || 'unknown';
    const head = document.createElement('div');
    head.className = 'quota-card-head';
    const name = document.createElement('h3');
    name.textContent = agent.name || agent.id;
    const pill = document.createElement('span');
    pill.className = 'quota-pill';
    pill.dataset.status = agent.status || 'unknown';
    pill.textContent = agent.status || 'unknown';
    head.append(name, pill);
    const headline = document.createElement('p');
    headline.className = 'quota-headline';
    headline.textContent = agent.headline || '—';
    const detail = document.createElement('p');
    detail.className = 'quota-detail';
    detail.textContent = agent.detail || '';
    article.append(head, headline, detail);
    const bars = document.createElement('div');
    bars.className = 'quota-bars';
    for (const bar of agent.bars || []) {
      const row = document.createElement('div');
      row.className = 'quota-bar';
      const label = document.createElement('span');
      label.textContent = bar.label || bar.id;
      const track = document.createElement('div');
      track.className = 'quota-track';
      track.dataset.level = bar.level || 'unknown';
      const fill = document.createElement('div');
      fill.className = 'quota-fill';
      const ratio = typeof bar.ratio === 'number' ? Math.max(0, Math.min(1, bar.ratio)) : 0;
      fill.style.setProperty('--ratio', String(ratio));
      track.append(fill);
      const text = document.createElement('span');
      text.textContent = bar.text || '';
      row.append(label, track, text);
      bars.append(row);
    }
    if ((agent.bars || []).length) article.append(bars);
    const meta = document.createElement('p');
    meta.className = 'quota-meta';
    const bits = [agent.model, agent.tokens !== null && agent.tokens !== undefined ? String(agent.tokens) + ' tok' : '', money(agent.cost_usd), ageText(agent.age_s), agent.source].filter(Boolean);
    meta.textContent = bits.join(' · ');
    article.append(meta);
    return article;
  };
  const render = (payload) => {
    host.replaceChildren();
    for (const agent of payload.agents || []) host.append(card(agent));
    status.textContent = payload.generated_at ? 'Updated ' + payload.generated_at : 'local monitors';
  };
  const load = async () => {
    try {
      const response = await fetch('/api/usage/quota', { credentials: 'same-origin', cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || ('HTTP ' + response.status));
      render(payload);
    } catch (error) {
      status.textContent = 'Live quota unavailable: ' + error.message;
    }
  };
  load();
  setInterval(load, 15000);
})();"#
}

fn usage_dashboard_script() -> &'static str {
    r#"(() => {
  const root = document.querySelector('[data-usage-dashboard]');
  if (!root) return;
  const form = document.getElementById('usage-filter-form');
  const status = document.getElementById('usage-status');
  const body = document.getElementById('usage-runs-body');
  const empty = document.getElementById('usage-empty');
  const byId = (id) => document.getElementById(id);
  const number = new Intl.NumberFormat();
  const params = new URLSearchParams(location.search);
  for (const key of ['window', 'provider', 'agent', 'model']) {
    const field = byId('usage-' + key);
    if (field && params.has(key)) field.value = params.get(key);
  }
  const unknown = (value) => value && typeof value === 'object' && value.value === 'unknown';
  const label = (value) => unknown(value) || value === null || value === '' || value === undefined ? 'unknown' : String(value);
  const reason = (value) => unknown(value) && value.reason ? value.reason : '';
  const costText = (cost) => {
    if (!cost || unknown(cost.amount)) return 'unknown';
    return label(cost.amount) + ' ' + (cost.unit || cost.currency || 'USD');
  };
  const costsText = (values) => {
    const entries = Object.entries(values || {});
    return entries.length ? entries.map(([unit, amount]) => label(amount) + ' ' + unit).join(' · ') : 'none known';
  };
  const set = (id, value) => { const node = byId(id); if (node) node.textContent = value; };
  const renderDimensions = (id, values) => {
    const target = byId(id);
    target.replaceChildren();
    if (!values || !values.length) {
      const p = document.createElement('p'); p.className = 'control-empty'; p.textContent = 'No measured runs.'; target.append(p); return;
    }
    for (const item of values) {
      const row = document.createElement('div'); row.className = 'usage-dimension-row';
      const failed = item.runs_failed || 0;
      const tokenUnknown = item.runs_tokens_unknown || 0;
      const costUnknown = item.runs_cost_unknown || 0;
      if (failed || tokenUnknown || costUnknown) row.classList.add('needs-attention');
      const name = document.createElement('strong'); name.textContent = item.name;
      const runs = document.createElement('span'); runs.textContent = number.format(item.runs || 0);
      const fail = document.createElement('span'); fail.textContent = number.format(failed); if (failed) fail.className = 'is-signal';
      const tokens = document.createElement('span'); tokens.textContent = number.format(item.tokens_total_known || 0);
      const unk = document.createElement('span'); unk.textContent = number.format(tokenUnknown + costUnknown); if (tokenUnknown || costUnknown) unk.className = 'is-signal';
      const cost = document.createElement('span'); cost.textContent = costsText(item.cost_by_unit);
      row.append(name, runs, fail, tokens, unk, cost); target.append(row);
    }
  };
  const showDimension = (name) => {
    for (const key of ['providers', 'agents', 'models']) {
      const list = byId('usage-' + key);
      const on = key === name;
      if (list) list.hidden = !on;
      if (list) list.classList.toggle('is-active', on);
    }
    root.querySelectorAll('[data-usage-dim]').forEach((button) => {
      const on = button.getAttribute('data-usage-dim') === name;
      button.classList.toggle('is-active', on);
      button.setAttribute('aria-selected', on ? 'true' : 'false');
    });
  };
  root.querySelectorAll('[data-usage-dim]').forEach((button) => {
    button.addEventListener('click', () => showDimension(button.getAttribute('data-usage-dim')));
  });
  const render = (report) => {
    const totals = report.totals || {};
    set('usage-total-runs', number.format(totals.runs || 0));
    set('usage-total-failed', number.format(totals.runs_failed || 0));
    set('usage-total-tokens', number.format(totals.tokens_total_known || 0));
    set('usage-total-token-unknown', number.format(totals.runs_tokens_unknown || 0));
    set('usage-total-cost', costsText(totals.cost_by_unit));
    set('usage-total-cost-unknown', number.format(totals.runs_cost_unknown || 0));
    renderDimensions('usage-providers', report.dimensions && report.dimensions.providers);
    renderDimensions('usage-agents', report.dimensions && report.dimensions.agents);
    renderDimensions('usage-models', report.dimensions && report.dimensions.models);
    body.replaceChildren();
    const runs = report.runs || [];
    for (const run of runs) {
      const tr = document.createElement('tr');
      tr.setAttribute('data-run-id', run.run_id || '');
      tr.setAttribute('data-href', '/run/' + encodeURIComponent(run.run_id || ''));
      tr.setAttribute('data-transcript-url', '/api/control/runs/' + encodeURIComponent(run.run_id || '') + '/transcript');
      tr.setAttribute('data-meta', label(run.provider) + ' / ' + label(run.agent) + ' · ' + label(run.model) + ' · ' + (run.status || ''));
      const runCell = document.createElement('td');
      const link = document.createElement('a'); link.href = '/run/' + encodeURIComponent(run.run_id); link.textContent = run.run_id; link.className = 'control-run-open';
      const stamp = document.createElement('small'); stamp.textContent = run.recorded_at || ''; runCell.append(link, stamp);
      const identity = document.createElement('td'); identity.textContent = label(run.provider) + ' / ' + label(run.agent);
      const model = document.createElement('td'); model.textContent = label(run.model); if (reason(run.model)) model.title = reason(run.model);
      const tokens = document.createElement('td'); tokens.textContent = label(run.tokens && run.tokens.tokens_total); if (run.tokens && reason(run.tokens.tokens_total)) tokens.title = reason(run.tokens.tokens_total);
      const cost = document.createElement('td'); cost.textContent = costText(run.cost); if (run.cost && reason(run.cost.amount)) cost.title = reason(run.cost.amount);
      const state = document.createElement('td'); state.textContent = run.failure_kind ? run.status + ' · ' + run.failure_kind : run.status;
      if (run.failure) state.title = run.failure;
      if (run.failure_kind || (run.status && run.status !== 'completed' && run.status !== 'ok')) tr.className = 'is-attention';
      if (unknown(run.tokens && run.tokens.tokens_total) || (run.cost && unknown(run.cost.amount))) tr.classList.add('is-unknown');
      tr.append(runCell, identity, model, tokens, cost, state); body.append(tr);
    }
    empty.hidden = runs.length !== 0;
    set('usage-run-count', String(runs.length));
    const first = body.querySelector('tr[data-run-id]');
    if (first) first.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    set('usage-schema', report.schema || 'unknown schema');
    set('usage-generated', report.generated_at ? 'Generated ' + report.generated_at : '');
    status.textContent = runs.length ? 'Live read-only projection · ' + runs.length + ' matching run(s)' : 'Live read-only projection · empty window';
    drawCharts(report);
  };
  const knownAmount = (cost) => {
    if (!cost || unknown(cost.amount)) return null;
    const amount = Number(cost.amount);
    return Number.isFinite(amount) ? amount : null;
  };
  const stampOf = (run) => {
    const ms = Date.parse(run.recorded_at || '');
    return Number.isFinite(ms) ? ms : null;
  };
  const grainFor = (since) => since === '24h' ? 'hour' : 'day';
  const bucketStart = (ms, grain) => {
    const date = new Date(ms);
    if (grain === 'hour') date.setUTCMinutes(0, 0, 0);
    else date.setUTCHours(0, 0, 0, 0);
    return date.getTime();
  };
  const stepMs = (grain) => grain === 'hour' ? 3600000 : 86400000;
  const axisLabel = (ms, grain) => {
    const date = new Date(ms);
    const hh = String(date.getUTCHours()).padStart(2, '0');
    const dd = String(date.getUTCDate()).padStart(2, '0');
    const mo = String(date.getUTCMonth() + 1).padStart(2, '0');
    return grain === 'hour' ? dd + ' ' + hh + ':00' : mo + '-' + dd;
  };
  const bucketSeries = (rows, since, pick) => {
    const grain = grainFor(since);
    const points = [];
    for (const run of rows) {
      const ms = stampOf(run);
      const value = pick(run);
      if (ms == null || value == null) continue;
      points.push({ ms, value });
    }
    if (!points.length) return null;
    let min = bucketStart(points[0].ms, grain);
    let max = min;
    for (const point of points) {
      const at = bucketStart(point.ms, grain);
      if (at < min) min = at;
      if (at > max) max = at;
    }
    const width = stepMs(grain);
    const buckets = [];
    for (let at = min; at <= max; at += width) buckets.push({ t: at, value: 0 });
    const index = new Map(buckets.map((bucket, i) => [bucket.t, i]));
    for (const point of points) {
      const slot = index.get(bucketStart(point.ms, grain));
      if (slot != null) buckets[slot].value += point.value;
    }
    return { grain, buckets };
  };
  const paintChart = (plotId, titleId, caption, built, color) => {
    const host = byId(plotId);
    const title = byId(titleId);
    if (title) title.textContent = caption;
    if (!host) return;
    host.replaceChildren();
    const values = built ? built.buckets.map((bucket) => bucket.value) : [];
    const sum = values.reduce((total, value) => total + value, 0);
    if (!built || !values.length || sum === 0) {
      const note = document.createElement('p');
      note.className = 'usage-chart-empty';
      note.textContent = 'No measured points in this window.';
      host.append(note);
      return;
    }
    const w = 640;
    const h = 148;
    const pad = 10;
    const max = Math.max(...values);
    const n = values.length;
    const xAt = (i) => n === 1 ? w / 2 : pad + (i / (n - 1)) * (w - pad * 2);
    const yAt = (value) => h - pad - (value / max) * (h - pad * 2);
    const coords = values.map((value, i) => xAt(i).toFixed(1) + ',' + yAt(value).toFixed(1));
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
    svg.setAttribute('role', 'img');
    const area = document.createElementNS(svg.namespaceURI, 'polygon');
    area.setAttribute('points', xAt(0).toFixed(1) + ',' + (h - pad) + ' ' + coords.join(' ') + ' ' + xAt(n - 1).toFixed(1) + ',' + (h - pad));
    area.setAttribute('fill', color);
    area.setAttribute('fill-opacity', '0.16');
    const line = document.createElementNS(svg.namespaceURI, 'polyline');
    line.setAttribute('points', coords.join(' '));
    line.setAttribute('fill', 'none');
    line.setAttribute('stroke', color);
    line.setAttribute('stroke-width', '1.5');
    svg.append(area, line);
    host.append(svg);
    const axis = document.createElement('p');
    axis.className = 'usage-chart-axis';
    const first = built.buckets[0];
    const last = built.buckets[built.buckets.length - 1];
    axis.textContent = axisLabel(first.t, built.grain) + '  ·  ' + axisLabel(last.t, built.grain);
    host.append(axis);
  };
  const dayStart = (ms) => {
    const date = new Date(ms);
    return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
  };
  const windowDays = (since, generated, rows) => {
    const end = dayStart(Date.parse(generated) || Date.now());
    let start = end;
    if (since === '7d') start = end - 6 * 86400000;
    else if (since === '30d') start = end - 29 * 86400000;
    else if (since !== '24h') {
      let earliest = end;
      for (const run of rows) {
        const ms = stampOf(run);
        if (ms != null) earliest = Math.min(earliest, dayStart(ms));
      }
      start = Math.max(earliest, end - 370 * 86400000);
    }
    const days = [];
    for (let at = start; at <= end; at += 86400000) days.push(at);
    return days;
  };
  const paintHeat = (rows, since, generated) => {
    const host = byId('usage-chart-heat-plot');
    const title = byId('usage-chart-heat-title');
    const windowName = since || 'window';
    if (title) title.textContent = 'Days · ' + windowName;
    if (!host) return;
    host.replaceChildren();
    const days = windowDays(since, generated, rows);
    const counts = new Map(days.map((day) => [day, 0]));
    for (const run of rows) {
      const ms = stampOf(run);
      if (ms == null) continue;
      const day = dayStart(ms);
      if (counts.has(day)) counts.set(day, counts.get(day) + 1);
    }
    const light = document.documentElement.dataset.theme === 'light';
    const emptyFill = light ? '#d5d5d5' : '#242424';
    const active = light ? '#2f6b32' : '#90a959';
    const max = Math.max(1, ...counts.values());
    const cell = 11;
    const gap = 3;
    const labelH = 14;
    const cols = [];
    let column = [];
    for (const day of days) {
      const weekday = new Date(day).getUTCDay();
      if (weekday === 0 && column.length) { cols.push(column); column = []; }
      column.push(day);
    }
    if (column.length) cols.push(column);
    const w = Math.max(cols.length, 1) * (cell + gap);
    const h = labelH + 7 * (cell + gap);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
    svg.setAttribute('role', 'img');
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    let lastMonth = -1;
    cols.forEach((week, col) => {
      const first = new Date(week[0]);
      if (first.getUTCDate() <= 7 && first.getUTCMonth() !== lastMonth) {
        lastMonth = first.getUTCMonth();
        const label = document.createElementNS(svg.namespaceURI, 'text');
        label.setAttribute('x', String(col * (cell + gap)));
        label.setAttribute('y', '9');
        label.setAttribute('fill', light ? '#6b6b6b' : '#6b6b6b');
        label.setAttribute('font-size', '9');
        label.textContent = months[lastMonth];
        svg.append(label);
      }
      week.forEach((day) => {
        const count = counts.get(day) || 0;
        const rect = document.createElementNS(svg.namespaceURI, 'rect');
        const row = new Date(day).getUTCDay();
        rect.setAttribute('x', String(col * (cell + gap)));
        rect.setAttribute('y', String(labelH + row * (cell + gap)));
        rect.setAttribute('width', String(cell));
        rect.setAttribute('height', String(cell));
        rect.setAttribute('rx', '2');
        if (count === 0) rect.setAttribute('fill', emptyFill);
        else {
          rect.setAttribute('fill', active);
          rect.setAttribute('fill-opacity', String(0.35 + 0.65 * (count / max)));
        }
        const stamp = new Date(day).toISOString().slice(0, 10);
        rect.setAttribute('title', stamp + ' · ' + count);
        svg.append(rect);
      });
    });
    host.append(svg);
  };
  const drawCharts = (report) => {
    const rows = report.runs || [];
    const totals = report.totals || {};
    const since = (report.filter && report.filter.since) || '';
    const windowName = since || 'window';
    set('usage-hero-tokens', number.format(totals.tokens_total_known || 0));
    const unknownRuns = (totals.runs_tokens_unknown || 0) + (totals.runs_cost_unknown || 0);
    set('usage-hero-caption', windowName + ' · ' + number.format(unknownRuns) + ' unknown');
    paintHeat(rows, since, report.generated_at || '');
    const byUnit = new Map();
    for (const run of rows) {
      const amount = knownAmount(run.cost);
      if (amount == null) continue;
      const unit = (run.cost && (run.cost.unit || run.cost.currency)) || 'USD';
      const list = byUnit.get(unit) || [];
      list.push(run);
      byUnit.set(unit, list);
    }
    let bestUnit = '';
    let bestSum = -1;
    let bestRows = [];
    for (const [unit, list] of byUnit) {
      const sum = list.reduce((total, run) => total + knownAmount(run.cost), 0);
      if (sum > bestSum) { bestSum = sum; bestUnit = unit; bestRows = list; }
    }
    const costBuilt = bestRows.length ? bucketSeries(bestRows, since, (run) => knownAmount(run.cost)) : null;
    paintChart(
      'usage-chart-cost-plot',
      'usage-chart-cost-title',
      bestUnit ? 'Cost · ' + bestUnit + ' · ' + windowName : 'Cost · ' + windowName,
      costBuilt,
      '#6a9fb5'
    );
  };
  const load = async () => {
    status.textContent = 'Loading canonical telemetry…';
    const query = new URLSearchParams(new FormData(form));
    for (const [key, value] of [...query.entries()]) if (!String(value).trim()) query.delete(key);
    try {
      const response = await fetch('/api/usage?' + query.toString(), { credentials: 'same-origin', cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || ('HTTP ' + response.status));
      history.replaceState(null, '', '/usage?' + query.toString());
      render(payload);
    } catch (error) {
      status.textContent = 'Usage telemetry unavailable: ' + error.message;
      empty.hidden = false;
      empty.textContent = 'The canonical usage projection could not be read.';
    }
  };
  form.addEventListener('submit', (event) => { event.preventDefault(); load(); });
  load();
})();"#
}

/// Client behaviour of the AICX search page. Injected through `inner_html`
/// like the theme scripts, so SSR and hydration agree on the DOM. Every hit
/// links to the server-owned reference route, never to a `file://` path.
fn aicx_search_panel() -> impl IntoView {
    view! {
        <section class="control-panel control-panel-wide" aria-label="AICX search">
            <div class="control-panel-head"><h2>"AICX"</h2><span>"intent"</span></div>
            <form id="aicx-search-form" class="server-console-links">
                <input id="aicx-search-query" name="q" type="search" required=true maxlength="512" placeholder="Search intent (query)" />
                <input id="aicx-search-project" name="project" type="text" maxlength="129" placeholder="owner/repo (optional)" />
                <button class="server-console-link server-console-link-primary" type="submit">"Search AICX"</button>
            </form>
            <p id="aicx-search-status" class="control-empty">"Enter a query to search the local AICX corpus."</p>
            <ul id="aicx-search-results" class="control-warning-list"></ul>
            <script inner_html=aicx_page_script()></script>
        </section>
    }
}

fn aicx_page_script() -> &'static str {
    r#"(() => {
  const form = document.getElementById('aicx-search-form');
  const q = document.getElementById('aicx-search-query');
  const project = document.getElementById('aicx-search-project');
  const status = document.getElementById('aicx-search-status');
  const results = document.getElementById('aicx-search-results');
  if (!form || !q || !status || !results) return;
  const initial = new URLSearchParams(location.search).get('q') || '';
  if (initial.trim() && !q.value) q.value = initial.trim();
  const runSearch = async () => {
    const query = q.value.trim();
    if (!query) return;
    const scope = project ? project.value.trim() : '';
    if (scope && !/^[\w][\w.-]{0,63}\/[\w][\w.-]{0,63}$/.test(scope)) {
      status.textContent = 'Project must be an owner/repo slug (for example vetcoders/vibecrafted).';
      return;
    }
    status.textContent = 'Searching AICX…';
    results.replaceChildren();
    const params = new URLSearchParams({ q: query });
    if (scope) params.set('project', scope);
    try {
      const response = await fetch('/api/aicx/search?' + params.toString(), { credentials: 'same-origin' });
      const payload = await response.json();
      if (!response.ok) {
        const message = payload.error || ('AICX search failed (HTTP ' + response.status + ')');
        if (payload.kind === 'validation' || response.status === 400) {
          status.textContent = message;
          return;
        }
        throw new Error(message);
      }
      const items = payload.items || [];
      status.textContent = items.length ? items.length + ' result(s)' : 'No AICX results.';
      for (const item of items) {
        const li = document.createElement('li');
        const label = [item.agent, item.date, item.session_id].filter(Boolean).join(' · ') || 'session';
        if (item.reference) {
          const a = document.createElement('a');
          a.href = item.reference;
          a.className = 'control-run-open';
          a.textContent = label + ' →';
          li.append(a);
        } else {
          const span = document.createElement('strong');
          span.textContent = label;
          li.append(span);
        }
        li.append(document.createTextNode(' — ' + (item.matches || []).join(' ')));
        results.append(li);
      }
    } catch (error) {
      status.textContent = 'AICX unavailable: ' + error.message;
    }
  };
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    runSearch();
  });
  if (q.value.trim()) runSearch();
})();"#
}

#[component]
pub fn AicxPage() -> impl IntoView {
    view! {
        <Title text="AICX search - vc-server" />
        <Meta name="description" content="Search the local AICX intent corpus through the installed CLI." />
        <ServerFrame active=ServerSection::Structure status="intent search".to_string()>
            <div class="server-console-shell route-page-shell">
                {route_header("Intent", "AICX search", "Search runs the installed AICX CLI on this host. The corpus is private: results are served to local peers only, and every hit opens through a server-owned reference route.")}
                {aicx_search_panel()}
                <p class="server-console-links"><a class="server-console-link" href="/structure">"Back to Structure"</a><a class="server-console-link" href="/agents">"Agent Manager"</a></p>
            </div>
        </ServerFrame>
    }
}

/// One remembered AICX hit. Plans and Loctree reports are not rows in this room.
struct HistoryMemory {
    session_id: String,
    kind: String,
    agent: String,
    date: String,
    summary: String,
    reference: String,
    plan_id: String,
}

impl HistoryMemory {
    #[cfg(test)]
    fn hit(kind: &str, session_id: &str) -> Self {
        Self {
            session_id: session_id.to_string(),
            kind: kind.to_string(),
            agent: "grok".to_string(),
            date: "2026-09-25".to_string(),
            summary: String::new(),
            reference: format!("/api/aicx/reference?path={session_id}"),
            plan_id: String::new(),
        }
    }

    /// Session or intent memory only. A plan id, a plan kind, or a report kind
    /// never becomes a row, even when a session id is also present.
    fn is_remembered(&self) -> bool {
        if !self.plan_id.trim().is_empty() {
            return false;
        }
        let kind = self.kind.trim().to_ascii_lowercase();
        if kind == "plan" || kind == "report" || kind.contains("loctree") {
            return false;
        }
        if self.session_id.trim().is_empty() {
            return false;
        }
        kind == "session" || kind == "intent" || kind.is_empty()
    }
}

fn history_page_script() -> &'static str {
    r#"(() => {
  const form = document.getElementById('history-search-form');
  const q = document.getElementById('history-search-query');
  const project = document.getElementById('history-search-project');
  const status = document.getElementById('history-memory-status');
  const empty = document.getElementById('history-memory-empty');
  const results = document.getElementById('history-memory-list');
  if (!form || !q || !status || !results) return;
  const roomEmpty = 'History & context has no remembered sessions or intents yet.';
  const remembered = (item) => {
    if (!item || item.plan_id) return false;
    const kind = String(item.kind || '').toLowerCase();
    if (kind === 'plan' || kind === 'report' || kind.indexOf('loctree') !== -1) return false;
    const session = String(item.session_id || '').trim();
    if (!session) return false;
    return kind === 'session' || kind === 'intent' || kind === '';
  };
  const paint = (items) => {
    results.replaceChildren();
    const rows = (items || []).filter(remembered);
    if (empty) empty.hidden = rows.length !== 0;
    status.textContent = rows.length ? (rows.length + ' remembered') : roomEmpty;
    for (const item of rows) {
      const li = document.createElement('li');
      const kind = String(item.kind || 'session').toLowerCase() === 'intent' ? 'intent' : 'session';
      li.className = 'history-memory-row';
      li.setAttribute('data-memory', kind);
      const reference = String(item.reference || '');
      const label = String(item.session_id);
      if (reference.indexOf('/api/aicx/reference') === 0) {
        const a = document.createElement('a');
        a.href = reference;
        a.className = 'control-run-open';
        a.textContent = label;
        li.append(a);
      } else {
        const strong = document.createElement('strong');
        strong.textContent = label;
        li.append(strong);
      }
      const meta = document.createElement('span');
      meta.textContent = [kind, item.agent, item.date].filter(Boolean).join(' · ');
      li.append(meta);
      const summary = document.createElement('span');
      summary.textContent = (item.matches || []).join(' ');
      li.append(summary);
      results.append(li);
    }
  };
  const runSearch = async () => {
    const query = q.value.trim();
    if (!query || query.startsWith('-') || query.length > 512) {
      status.textContent = 'History & context needs a query of at most 512 characters.';
      return;
    }
    const scope = project ? project.value.trim() : '';
    if (scope && !/^[\w][\w.-]{0,63}\/[\w][\w.-]{0,63}$/.test(scope)) {
      status.textContent = 'Project must be an owner/repo slug (for example vetcoders/vibecrafted).';
      return;
    }
    status.textContent = 'Reading AICX…';
    const params = new URLSearchParams({ q: query });
    if (scope) params.set('project', scope);
    try {
      const response = await fetch('/api/aicx/search?' + params.toString(), {
        credentials: 'same-origin',
        cache: 'no-store'
      });
      const payload = await response.json();
      if (!response.ok) {
        status.textContent = payload.error || ('AICX search failed (HTTP ' + response.status + ')');
        return;
      }
      paint(payload.items || []);
    } catch (error) {
      status.textContent = 'History & context could not read AICX: ' + error.message;
    }
  };
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    runSearch();
  });
  const initial = new URLSearchParams(location.search).get('q') || '';
  if (initial.trim()) {
    q.value = initial.trim();
    runSearch();
  }
})();"#
}

fn history_view(hits: Vec<HistoryMemory>) -> impl IntoView {
    let remembered: Vec<HistoryMemory> = hits
        .into_iter()
        .filter(HistoryMemory::is_remembered)
        .collect();
    let count = remembered.len();
    let show_empty = count == 0;
    view! {
        <Title text="History & context - vc-server" />
        <Meta name="description" content="Sessions and intents this host remembers, from the local AICX corpus." />
        <ServerFrame active=ServerSection::Structure status=format!("{count} remembered")>
            <div class="server-console-shell route-page-shell" data-history-room>
                {route_header(
                    "Trace",
                    "History & context",
                    "Sessions and intents the work remembered. This room queries the local AICX search and opens a hit only through the server reference route.",
                )}
                <section class="control-panel control-panel-wide" aria-label="Remembered sessions and intents">
                    <div class="control-panel-head">
                        <h2>"Remembered"</h2>
                        <span>{count}</span>
                    </div>
                    <form id="history-search-form" class="server-console-links" action="/api/aicx/search" method="get">
                        <input id="history-search-query" name="q" type="search" required=true maxlength="512" placeholder="Name a session or intent" />
                        <input id="history-search-project" name="project" type="text" maxlength="129" placeholder="owner/repo (optional)" />
                        <button class="server-console-link server-console-link-primary" type="submit">"Show memory"</button>
                    </form>
                    <p id="history-memory-status" class="control-plane-meta">"The list is AICX memory."</p>
                    {show_empty.then(|| view! {
                        <p id="history-memory-empty" class="control-empty">"History & context has no remembered sessions or intents yet."</p>
                    })}
                    <ul id="history-memory-list" class="control-warning-list">
                        {remembered.into_iter().map(|hit| {
                            let kind = if hit.kind.eq_ignore_ascii_case("intent") {
                                "intent".to_string()
                            } else {
                                "session".to_string()
                            };
                            let session_label = hit.session_id.clone();
                            let reference = hit.reference.clone();
                            let linked = reference.starts_with("/api/aicx/reference");
                            let agent = hit.agent.clone();
                            let date = hit.date.clone();
                            let summary = hit.summary.clone();
                            let memory_kind = kind.clone();
                            view! {
                                <li class="history-memory-row" data-memory=memory_kind>
                                    {linked.then(|| view! {
                                        <a class="control-run-open" href=reference>{session_label.clone()}</a>
                                    })}
                                    {(!linked).then(|| view! {
                                        <strong>{session_label.clone()}</strong>
                                    })}
                                    <span>{kind}</span>
                                    <span>{agent}</span>
                                    <span>{date}</span>
                                    <span>{summary}</span>
                                </li>
                            }
                        }).collect_view()}
                    </ul>
                    <script inner_html=history_page_script()></script>
                </section>
            </div>
        </ServerFrame>
    }
}

#[component]
pub fn HistoryPage() -> impl IntoView {
    history_view(Vec::new())
}

/// Crate-root test name. `cargo test -- --exact history_is_aicx_not_plans`
/// only matches a test at the lib root, not `app::tests::…`.
#[cfg(all(test, feature = "ssr"))]
pub(crate) fn history_is_aicx_not_plans_proof() {
    tests::history_room_proof();
}

#[component]
pub fn ConsolePage() -> impl IntoView {
    view! {
        <Title text="Overview - vc-server" />
        <Meta name="description" content="Vibecrafted operator overview." />
        <Meta name="theme-color" content="#21211f" />
        <Link rel="preload" as_="font" type_="font/woff2" href="/fonts/inter-var-latin.woff2" crossorigin="anonymous" />
        <Link rel="preload" as_="font" type_="font/woff2" href="/fonts/jetbrains-mono-var-latin.woff2" crossorigin="anonymous" />
        {control_dashboard(|dashboard| console_dashboard(dashboard).into_any())}
    }
}

fn console_dashboard(dashboard: DashboardData) -> impl IntoView {
    let runs_live = operator_active_runs(dashboard.active_runs).len();
    // Running vc-frame sessions, not the durable catalog. The attribute stays
    // so home still counts frames; the catalog itself is not this page.
    let live_workspace_count = dashboard.live_frame_sessions.len();
    let welcome = overview_welcome_line(&dashboard.server_status).to_string();
    let selected_root = dashboard
        .workspaces
        .iter()
        .find(|workspace| workspace.selected)
        .map(|workspace| workspace.root.clone())
        .unwrap_or_default();
    view! {
        <ServerFrame
            active=ServerSection::Overview
            status=welcome.clone()
        >
            <div
                class="server-console-shell overview-desk"
                data-live-workspaces=live_workspace_count
            >
                <div
                    id="vc-focus-context"
                    data-selected-workspace-root=selected_root
                    hidden
                ></div>
                <header class="overview-head">
                    <h1 class="run-detail-title">"Overview"</h1>
                    <p id="overview-status" class="overview-status" role="status">{welcome}</p>
                </header>
                <nav class="overview-doors" aria-label="Work">
                    <a class="overview-door" href="/" aria-current="page">
                        <strong>"Overview"</strong>
                    </a>
                    <a class="overview-door" href="/runs">
                        <strong>"Runs"</strong>
                        <small>{runs_live}</small>
                    </a>
                    <a class="overview-door" href="/projects">
                        <strong>"Projects"</strong>
                    </a>
                    <a class="overview-door" href="/usage">
                        <strong>"Costs & usage"</strong>
                    </a>
                </nav>
            </div>
        </ServerFrame>
    }
}

fn route_header(
    eyebrow: &'static str,
    title: &'static str,
    description: &'static str,
) -> impl IntoView {
    view! {
        <section class="run-detail-header route-page-header">
            <div>
                <p class="section-eyebrow">{eyebrow}</p>
                <h1 class="run-detail-title">{title}</h1>
                <p class="route-page-description">{description}</p>
            </div>
        </section>
    }
}

fn git_repo_name(root: &str) -> String {
    root.trim_end_matches(['/', '\\'])
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or("")
        .to_string()
}

fn workspace_cards(workspaces: Vec<DashboardWorkspace>) -> impl IntoView {
    workspaces
        .into_iter()
        .map(|workspace| {
            let selection = workspace.selected.then_some("selected");
            let workspace_id_attr = workspace.workspace_id.clone();
            let root = workspace.root.clone();
            let selected = workspace.selected;
            let repo = git_repo_name(&root);
            view! {
                <article
                    class="workspace-card"
                    data-workspace-id=workspace_id_attr
                    data-focus-root=root.clone()
                    data-focus-repo=repo
                    data-selected=selected.then_some("1")
                >
                    <div class="control-run-primary">
                        <strong class="workspace-title">{workspace.title}</strong>
                        <code class="control-run-root">{workspace.root}</code>
                    </div>
                    <div class="control-run-tags">
                        <span class="control-badge">{workspace.status}</span>
                        {selection.map(|label| view! { <span class="control-badge">{label}</span> })}
                        <span class="control-badge">{format!("{} live", workspace.active_runs)}</span>
                        <span class="control-badge">{format!("{} recent", workspace.recent_runs)}</span>
                    </div>
                    <div class="control-run-meta">
                        <span>{workspace.workspace_id}</span>
                        <span>{workspace.updated_at}</span>
                    </div>
                </article>
            }
        })
        .collect_view()
}

fn live_workspace_cards(frames: Vec<DashboardFrameSession>) -> impl IntoView {
    frames
        .into_iter()
        .map(|frame| {
            let unclaimed = frame.workspace_id.is_empty();
            let title = if frame.workspace_title.is_empty() {
                frame.name.clone()
            } else {
                frame.workspace_title.clone()
            };
            let frame_attr = frame.name.clone();
            view! {
                <article class="workspace-card" data-frame-session=frame_attr>
                    <div class="control-run-primary">
                        <strong class="workspace-title">{title}</strong>
                        <code class="control-run-root">{frame.workspace_root}</code>
                    </div>
                    <div class="control-run-tags">
                        <span class="control-badge">"live"</span>
                        <span class="control-badge">{format!("frame {}", frame.name)}</span>
                        {unclaimed.then(|| view! { <span class="control-badge">"no workspace record"</span> })}
                    </div>
                    <div class="control-run-meta">
                        <span>{frame.workspace_id}</span>
                        <span>{frame.session_id}</span>
                    </div>
                </article>
            }
        })
        .collect_view()
}

fn session_cards(sessions: Vec<DashboardSession>) -> impl IntoView {
    sessions
        .into_iter()
        .map(|session| {
            let session_id_attr = session.session_id.clone();
            view! {
                <article class="workspace-card" data-session-id=session_id_attr>
                    <div class="control-run-primary">
                        <strong class="workspace-title">{session.workspace_title}</strong>
                        <span class="control-run-root">{session.runtime}</span>
                    </div>
                    <div class="control-run-tags">
                        <span class="control-badge">{session.state}</span>
                        <span class="control-badge">{format!("instance {}", session.workspace_instance_id)}</span>
                    </div>
                    <div class="control-run-meta">
                        <span>{session.session_id}</span>
                        <span>{session.workspace_id}</span>
                        <span>{session.updated_at}</span>
                    </div>
                    <div class="session-run-links" aria-label="Canonical run transcripts">
                        {if session.runs.is_empty() {
                            view! { <span class="control-empty">"No canonical run transcript is linked to this session."</span> }.into_any()
                        } else {
                            session.runs.into_iter().map(|run| {
                                let href = format!("/run/{}", run.run_id);
                                view! {
                                    <a class="control-run-open" href=href>
                                        {format!("Open transcript {} ({}, {}) →", run.run_id, run.state, run.health)}
                                    </a>
                                }
                            }).collect_view().into_any()
                        }}
                    </div>
                </article>
            }
        })
        .collect_view()
}

fn transcripts_search_script() -> &'static str {
    r#"(() => {
  const form = document.getElementById('transcript-search-form');
  const q = document.getElementById('transcript-search-query');
  const status = document.getElementById('transcript-search-status');
  const list = document.getElementById('transcript-search-results');
  const more = document.getElementById('transcript-search-more');
  if (!form || !q || !status || !list || !more) return;
  const LIMIT = 50;
  let offset = 0;
  let accumulated = [];
  const text = (value) => (value == null ? '' : String(value));
  const render = (items) => {
    list.replaceChildren();
    for (const item of items || []) {
      const id = text(item.run_id);
      const row = document.createElement('article');
      row.className = 'control-run-row';
      row.setAttribute('data-ppm', 'run');
      row.setAttribute('data-run-id', id);
      row.setAttribute('data-href', '/run/' + encodeURIComponent(id));
      row.setAttribute('data-focus-root', text(item.root));
      row.setAttribute('data-transcript-url', '/api/control/runs/' + encodeURIComponent(id) + '/transcript');
      const primary = document.createElement('div');
      primary.className = 'control-run-primary';
      const link = document.createElement('a');
      link.className = 'control-run-id';
      link.href = '/run/' + encodeURIComponent(id);
      link.textContent = id;
      const root = document.createElement('span');
      root.className = 'control-run-root';
      root.textContent = text(item.agent) + ' · ' + text(item.skill);
      primary.append(link, root);
      const meta = document.createElement('div');
      meta.className = 'control-run-meta';
      const updated = document.createElement('span');
      updated.textContent = text(item.updated_at);
      const snippet = document.createElement('span');
      snippet.textContent = text(item.snippet);
      const copy = document.createElement('button');
      copy.type = 'button';
      copy.className = 'control-copy';
      copy.setAttribute('data-copy', id);
      copy.textContent = 'Copy';
      meta.append(updated, snippet, copy);
      row.addEventListener('click', (event) => {
        if (event.target.closest('a, button')) return;
        location.href = row.getAttribute('data-href');
      });
      row.append(primary, meta);
      list.append(row);
    }
  };
  const search = async (reset) => {
    const query = q.value.trim();
    if (reset) {
      offset = 0;
      accumulated = [];
    }
    status.textContent = query ? 'Searching…' : 'Listing transcripts…';
    const params = new URLSearchParams();
    if (query) params.set('q', query);
    params.set('offset', String(offset));
    params.set('limit', String(LIMIT));
    try {
      const response = await fetch('/api/control/transcripts?' + params.toString(), { credentials: 'same-origin' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || ('HTTP ' + response.status));
      const items = payload.items || [];
      accumulated = reset ? items : accumulated.concat(items);
      offset = (payload.offset || 0) + items.length;
      const total = payload.total || accumulated.length;
      status.textContent = total ? (accumulated.length + ' of ' + total + ' transcript(s)') : 'No transcripts.';
      more.hidden = !payload.has_more;
      render(accumulated);
      document.documentElement.dispatchEvent(new Event('vc-focus-refresh'));
    } catch (error) {
      status.textContent = 'Search unavailable: ' + error.message;
      more.hidden = true;
    }
  };
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    search(true);
  });
  more.addEventListener('click', () => search(false));
  search(true);
})();"#
}

#[component]
pub fn TranscriptsPage() -> impl IntoView {
    view! {
        <Title text="transcripts - vc-server" />
        <Meta name="description" content="Every human transcript on this host, searchable in the browser." />
        <ServerFrame active=ServerSection::Transcripts status="transcripts".to_string()>
            <div class="server-console-shell route-page-shell">
                {route_header("Runtime", "Transcripts", "Every canonical transcript.human.log on this host. Search streams each log from the start; pages of 50 keep the whole corpus reachable.")}
                <section class="control-panel control-panel-wide transcript-list" aria-label="Transcript search">
                    <form id="transcript-search-form" class="server-console-links">
                        <input id="transcript-search-query" name="q" type="search" maxlength="512" placeholder="Search transcripts" />
                        <button class="server-console-link server-console-link-primary" type="submit">"Search"</button>
                    </form>
                    <p id="transcript-search-status" class="control-empty">"Loading transcripts…"</p>
                    <div id="transcript-search-results" class="control-run-list"></div>
                    <p class="server-console-links">
                        <button id="transcript-search-more" class="server-console-link" type="button" hidden>"Load more"</button>
                    </p>
                    <script inner_html=transcripts_search_script()></script>
                </section>
            </div>
        </ServerFrame>
    }
}

#[component]
pub fn FramePage() -> impl IntoView {
    view! {
        <Title text="frame - vc-server" />
        <Meta name="description" content="Embedded vc-frame session (zellij web client)." />
        <ServerFrame active=ServerSection::Frame status="frame".to_string()>
            <div class="server-console-shell route-page-shell">
                {route_header("Session", "Frame", "The product multiplexer lives in vc-frame. Name its web client once; the App starts that origin and embeds it as a native tab. This page is the browser door to the same contract.")}
                <section class="control-panel control-panel-wide" aria-label="Frame session">
                    <div class="control-panel-head"><h2>"vc-frame web"</h2><span>"host session"</span></div>
                    <p class="route-page-description">
                        "Name the served origin in config. Vibecrafted.app starts `vc-frame web` on that host:port and opens it as a native tab. Tabs never start the service. Closing a tab does not kill the host session."
                    </p>
                    <ol class="operator-guide-list">
                        <li><strong>"Host session"</strong><span>"`vc-start` / `vc-frame` attaches the whole-host multiplexer (`/tmp/vc-frame-<uid>`). That session outlives any tab."</span></li>
                        <li><strong>"Name the origin"</strong><span>"`[tools.vc-frame] url = \"http://127.0.0.1:<port>/\"` in `~/.config/vibecrafted/config.toml`. No guessed port — the App binds exactly that loopback http origin."</span></li>
                        <li><strong>"Web client"</strong><span>"The App starts `vc-frame web --ip <host> --port <port>` on connect when that table is set. Browser-only operators start `vc-frame web` themselves."</span></li>
                        <li><strong>"Embed"</strong><span>"Vibecrafted.app → View → Open in Tab → Frame loads that origin as a native tab. Closing the tab detaches the view; workers keep running."</span></li>
                    </ol>
                    <p class="server-console-links">
                        <button type="button" class="server-console-link server-console-link-primary" data-copy="vc-frame web">"Copy start command"</button>
                        <button type="button" class="server-console-link" data-copy="[tools.vc-frame]\nurl = \"http://127.0.0.1:8082/\"">"Copy config snippet"</button>
                        <a class="server-console-link" href="/transcripts">"Transcripts"</a>
                    </p>
                </section>
            </div>
        </ServerFrame>
    }
}

#[component]
pub fn WorkspacesPage() -> impl IntoView {
    view! {
        <Title text="workspaces - vc-server" />
        <Meta name="description" content="Canonical Vibecrafted workspace identities and activity." />
        {control_dashboard(|dashboard| workspaces_dashboard(dashboard).into_any())}
    }
}

fn workspaces_dashboard(dashboard: DashboardData) -> impl IntoView {
    let status = dashboard.workspace_status;
    let error = dashboard.workspace_error;
    let live = dashboard.live_frame_sessions;
    let live_count = live.len();
    let live_workspace_ids = live
        .iter()
        .filter(|frame| !frame.workspace_id.is_empty())
        .map(|frame| frame.workspace_id.clone())
        .collect::<std::collections::HashSet<_>>();
    let count = dashboard.workspaces.len();
    // History: catalog identities without a running Frame, newest first.
    let mut history = dashboard
        .workspaces
        .into_iter()
        .filter(|workspace| !live_workspace_ids.contains(&workspace.workspace_id))
        .collect::<Vec<_>>();
    history.sort_by(|left, right| {
        right
            .updated_at
            .cmp(&left.updated_at)
            .then_with(|| left.workspace_id.cmp(&right.workspace_id))
    });
    let history_count = history.len();
    let not_initialized = status == "not_initialized";
    let unavailable = status == "unavailable";
    view! {
        <ServerFrame active=ServerSection::Workspaces status=format!("{live_count} live · {count} in catalog")>
            <div class="server-console-shell route-page-shell">
                {route_header("Workspace", "Workspaces", "Workspaces with a running vc-frame session come first. The durable catalog below keeps every identity ever registered, worker worktrees and test roots included, as history.")}
                <section class="control-panel control-panel-wide" aria-label="Live workspaces" data-live-workspaces=live_count>
                    <div class="control-panel-head"><h2>"Live now"</h2><span>{format!("{live_count} running vc-frame sessions")}</span></div>
                    {(live_count == 0 && !unavailable).then(|| view! {
                        <p class="control-empty">"No vc-frame session is running on this host."</p>
                    })}
                    <div class="workspace-card-list">{live_workspace_cards(live)}</div>
                </section>
                <section class="control-panel control-panel-wide" aria-label="Canonical workspaces" data-source-status=status.clone() data-history-count=history_count>
                    <div class="control-panel-head"><h2>"Workspace catalog"</h2><span>{status.clone()}</span></div>
                    {not_initialized.then(|| view! {
                        <p class="control-empty">
                            "No workspace catalog exists yet. Create or select a workspace with the Vibecrafted workspace command; the server will project it here without inventing defaults."
                        </p>
                    })}
                    {unavailable.then(|| view! {
                        <p class="control-empty control-error">
                            {format!("Workspace data is unavailable: {error}")}
                        </p>
                    })}
                    {(count == 0 && !not_initialized && !unavailable).then(|| view! {
                        <p class="control-empty">
                            "The canonical catalog is healthy and contains no workspaces."
                        </p>
                    })}
                    <details class="workspace-history">
                        <summary>{format!("History · {history_count} catalog identities without a running vc-frame session")}</summary>
                        <div class="workspace-card-list">{workspace_cards(history)}</div>
                    </details>
                </section>
            </div>
        </ServerFrame>
    }
}

#[component]
pub fn SessionsPage() -> impl IntoView {
    view! {
        <Title text="sessions - vc-server" />
        <Meta name="description" content="Canonical workspace session attachments." />
        {control_dashboard(|dashboard| sessions_dashboard(dashboard).into_any())}
    }
}

fn sessions_dashboard(dashboard: DashboardData) -> impl IntoView {
    let source_status = dashboard.workspace_status;
    let error = dashboard.workspace_error;
    let sessions = dashboard.sessions;
    let count = sessions.len();
    let unavailable = source_status == "unavailable";
    view! {
        <ServerFrame active=ServerSection::Sessions status=format!("{count} sessions")>
            <div class="server-console-shell route-page-shell">
                {route_header("Workspace", "Sessions", "Logical workspace sessions and their real runtime attachments from canonical session records.")}
                <section class="control-panel control-panel-wide" aria-label="Canonical sessions" data-source-status=source_status>
                    <div class="control-panel-head"><h2>"Session attachments"</h2><span>{count}</span></div>
                    <p class="control-empty control-error" hidden={!unavailable}>
                        {format!("Session data is unavailable: {error}")}
                    </p>
                    <p class="control-empty" hidden={count != 0 || unavailable}>
                        "No canonical workspace sessions are recorded."
                    </p>
                    <div class="workspace-card-list">{session_cards(sessions)}</div>
                </section>
            </div>
        </ServerFrame>
    }
}

#[component]
pub fn AgentManagerPage() -> impl IntoView {
    view! {
        <Title text="agent manager - vc-server" />
        <Meta name="description" content="Supported agent providers, models, and canonical launchers." />
        <ServerFrame active=ServerSection::Agents status="catalog".to_string()>
            <div class="server-console-shell route-page-shell">
                {route_header("Catalog", "Agent Manager", "Supported providers, models, and canonical launch paths. Live work and history stay in Sessions and Live runs.")}
                <section class="control-panel control-panel-wide" aria-label="Supported agent launchers">
                    <div class="control-panel-head"><h2>"Supported launchers"</h2><span>"catalog"</span></div>
                    <ul class="operator-guide-list">
                        <li><strong>"Codex"</strong><span>"Use `vibecrafted implement codex` or the matching skill command; model selection is runtime-owned."</span></li>
                        <li><strong>"Claude"</strong><span>"Use the supported Vibecrafted worker launcher; provider-session identity remains distinct from a workspace session."</span></li>
                        <li><strong>"Gemini"</strong><span>"Use the supported Vibecrafted worker launcher when the configured provider is available."</span></li>
                        <li><strong>"AICX / Loctree"</strong><span>"Foundation tools: AICX retrieves intent; Loctree produces structural evidence. They are not agent-run records."</span></li>
                    </ul>
                    <p class="control-plane-meta">"Configuration and provider availability are read from the selected runtime at launch. This catalog does not fabricate a launch button or live state."</p>
                </section>
                <p class="server-console-links"><a class="server-console-link server-console-link-primary" href="/runs">"Open live runs"</a><a class="server-console-link" href="/sessions">"Open sessions"</a><a class="server-console-link" href="/aicx">"Search intent (AICX)"</a></p>
            </div>
        </ServerFrame>
    }
}

/// Plan id already written into a recorded report path
/// (`.../<org>/<repo>/<day>/plans/<plan_id>/...`). No id, no door.
fn plan_from_report_path(path: &str) -> (String, String) {
    let parts: Vec<&str> = path
        .split(['/', '\\'])
        .filter(|part| !part.is_empty())
        .collect();
    let Some(plans_at) = parts.iter().position(|part| *part == "plans") else {
        return (String::new(), String::new());
    };
    let Some(plan_id) = parts.get(plans_at + 1).copied().filter(|id| !id.is_empty()) else {
        return (String::new(), String::new());
    };
    let href = if plans_at >= 3 {
        format!(
            "/scaffold?org={}&repo={}&day={}&plan_id={}",
            url_component(parts[plans_at - 3]),
            url_component(parts[plans_at - 2]),
            url_component(parts[plans_at - 1]),
            url_component(plan_id),
        )
    } else {
        format!("/scaffold?plan_id={}", url_component(plan_id))
    };
    (plan_id.to_string(), href)
}

fn url_component(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char);
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

/// Success / Needs Attention / Failures come only from the settlement verdict.
/// `invalid` is not a sixth heading and is not folded into Failures. Queued is
/// the lifecycle state of that name. Current is the non-terminal active or
/// stalled set, so a stall does not invent another bucket. Exit codes are not read.
fn place_run(run: &DashboardRun) -> Option<&'static str> {
    match run.settlement_verdict.as_str() {
        "finalized" => return Some("success"),
        "needs_attention" => return Some("needs-attention"),
        "failed" => return Some("failures"),
        _ => {}
    }
    if run.state.eq_ignore_ascii_case("queued") {
        return Some("queued");
    }
    let current = matches!(run.health.as_str(), "active" | "stalled")
        && !is_terminal_state(&run.state)
        && !is_quarantined_run(run);
    current.then_some("current")
}

fn queued_lifecycle_run(run: &DashboardLifecycleRun) -> DashboardRun {
    DashboardRun {
        run_id: run.run_id.clone(),
        state: "queued".to_string(),
        health: "queued".to_string(),
        agent: run.next_agent.clone(),
        skill: run.workflow.clone(),
        latest_report: run.report_path.clone(),
        updated_at: run.updated_at.clone(),
        plan_id: run.plan_id.clone(),
        plan_href: run.plan_href.clone(),
        transcript_open: run.transcript_open,
        ..DashboardRun::default()
    }
}

fn partition_run_buckets(dashboard: &DashboardData) -> [Vec<DashboardRun>; 5] {
    let mut placed = std::collections::HashSet::new();
    let mut success = Vec::new();
    let mut attention = Vec::new();
    let mut failures = Vec::new();
    let mut current = Vec::new();
    let mut queued = Vec::new();
    for run in dashboard
        .active_runs
        .iter()
        .chain(dashboard.stalled_runs.iter())
        .chain(dashboard.recent_runs.iter())
    {
        if !placed.insert(run.run_id.clone()) {
            continue;
        }
        match place_run(run) {
            Some("success") => success.push(run.clone()),
            Some("needs-attention") => attention.push(run.clone()),
            Some("failures") => failures.push(run.clone()),
            Some("current") => current.push(run.clone()),
            Some("queued") => queued.push(run.clone()),
            _ => {}
        }
    }
    for run in &dashboard.lifecycle_runs {
        if !run.status.eq_ignore_ascii_case("queued") {
            continue;
        }
        if !placed.insert(run.run_id.clone()) {
            continue;
        }
        queued.push(queued_lifecycle_run(run));
    }
    [success, attention, failures, current, queued]
}

fn run_bucket_row(run: DashboardRun) -> impl IntoView {
    let detail_href = format!("/run/{}", run.run_id);
    let row_href = detail_href.clone();
    let transcript_href = detail_href.clone();
    let transcript_url = run
        .transcript_open
        .then(|| format!("/api/control/runs/{}/transcript", run.run_id));
    let plan_href = if run.plan_href.is_empty() && !run.plan_id.is_empty() {
        format!("/scaffold?plan_id={}", url_component(&run.plan_id))
    } else {
        run.plan_href.clone()
    };
    let show_plan = !plan_href.is_empty();
    let transcript_open = run.transcript_open;
    let meta = format!("{} · {} · {}", run.agent, run.skill, run.updated_at);
    let run_id = run.run_id.clone();
    view! {
        <tr
            data-ppm="run"
            data-run-id=run.run_id.clone()
            data-href=row_href
            data-focus-root=run.root.clone()
            data-transcript-url=transcript_url
            data-report=run.latest_report.clone()
            data-error=run.last_error.clone()
            data-meta=meta
        >
            <td>
                <a class="control-run-id" href=detail_href data-copy=run_id.clone()>{run_id.clone()}</a>
            </td>
            <td>{run.agent}</td>
            <td>{run.skill}</td>
            <td class="run-doors">
                {show_plan.then(|| view! {
                    <a class="server-console-link" data-run-door="plan" href=plan_href>"Plan"</a>
                })}
                {transcript_open.then(|| view! {
                    <a class="server-console-link" data-run-door="transcript" href=transcript_href>"Transcript"</a>
                })}
                <span class="run-door" data-run-door="dock">"Dock"</span>
            </td>
        </tr>
    }
}

fn run_bucket(title: &'static str, id: &'static str, runs: Vec<DashboardRun>) -> impl IntoView {
    let count = runs.len();
    view! {
        <section class="control-panel control-panel-wide" aria-label=title data-run-bucket=id>
            <div class="control-panel-head">
                <h2>{title}</h2>
                <span>{count}</span>
            </div>
            <p class="control-empty" hidden={count != 0}>"None."</p>
            <div class="run-table-wrap">
                <table class="run-table">
                    <tbody>{runs.into_iter().map(run_bucket_row).collect_view()}</tbody>
                </table>
            </div>
        </section>
    }
}

#[component]
pub fn RunsPage() -> impl IntoView {
    view! {
        <Title text="runs - vc-server" />
        <Meta name="description" content="Agent runs in five buckets, with plan, transcript, and dock." />
        {control_dashboard(|dashboard| runs_dashboard(dashboard).into_any())}
    }
}

fn runs_dashboard(dashboard: DashboardData) -> impl IntoView {
    let control_plane = dashboard.control_plane.clone();
    let control_status = dashboard.control_status.clone();
    let control_error = dashboard.control_error.clone();
    let generated_at = dashboard.generated_at.clone();
    let settlement = dashboard.settlement.clone();
    let [success, attention, failures, current, queued] = partition_run_buckets(&dashboard);
    let current_count = current.len();
    let not_initialized = control_status == "not_initialized";
    let unavailable = control_status == "unavailable";

    view! {
        <ServerFrame active=ServerSection::Runs status=format!("{current_count} current")>
            <div class="server-console-shell route-page-shell">
                {route_header(
                    "Work",
                    "Runs",
                    "Finalized runs sit in Success, Needs Attention, and Failures. Current and Queued have not settled. Open the plan when one was recorded, open transcript.human.log when that log is on disk, or dock the row.",
                )}
                <p class="control-plane-meta"><span>{control_plane}</span><span>{generated_at}</span><span>{control_status}</span></p>
                {not_initialized.then(|| view! {
                    <p class="control-empty">"The server is healthy, but the control plane is not initialized yet."</p>
                })}
                {unavailable.then(|| view! {
                    <p class="control-empty control-error">{format!("Control-plane data is unavailable: {control_error}")}</p>
                })}
                <div class="overview-desk-body">
                    <div class="overview-desk-main">
                        {run_bucket("Success", "success", success)}
                        {run_bucket("Needs Attention", "needs-attention", attention)}
                        {run_bucket("Failures", "failures", failures)}
                        {run_bucket("Current", "current", current)}
                        {run_bucket("Queued", "queued", queued)}
                    </div>
                    <aside class="overview-inspector doc-pane" id="overview-inspector" aria-label="Run document" hidden>
                        <div class="doc-tabs" role="tablist" aria-label="Document">
                            <button type="button" data-doc-tab="transcript" class="is-active">"Transcript"</button>
                            <button type="button" data-doc-tab="report">"Report"</button>
                            <button type="button" data-doc-tab="structure">"Structure"</button>
                        </div>
                        <article class="doc-sheet" data-doc-panel="transcript">
                            <p class="doc-kicker">"Run"</p>
                            <h2 data-inspector-id></h2>
                            <p class="doc-meta" data-inspector-meta>"Select a row."</p>
                            <pre class="inspector-tail" data-inspector-tail>"Select a row."</pre>
                        </article>
                        <article class="doc-sheet" data-doc-panel="report" hidden>
                            <p class="doc-kicker">"Report"</p>
                            <p data-inspector-report>"Select a row."</p>
                            <p class="control-run-error" data-inspector-error hidden></p>
                        </article>
                        <article class="doc-sheet" data-doc-panel="structure" hidden>
                            <p class="doc-kicker">"Path"</p>
                            <p data-inspector-root>"Select a row."</p>
                            <a class="doc-path" data-inspector-open href="/structure">"Open structure"</a>
                        </article>
                    </aside>
                </div>
                {retained_run_history(settlement)}
            </div>
        </ServerFrame>
    }
}

#[component]
pub fn LifecyclePage() -> impl IntoView {
    view! {
        <Title text="lifecycle - vc-server" />
        <Meta name="description" content="Lifecycle batons that need an operator decision." />
        {control_dashboard(|dashboard| lifecycle_dashboard(dashboard).into_any())}
    }
}

fn lifecycle_dashboard(dashboard: DashboardData) -> impl IntoView {
    let actions = operator_action_runs(dashboard.lifecycle_runs);
    let count = actions.len();
    view! {
        <ServerFrame active=ServerSection::Lifecycle status=format!("{count} next")>
            <div class="server-console-shell route-page-shell">
                {route_header("Control", "Lifecycle", "Open a baton to inspect its current stage, next agent, controls, and delivery state.")}
                <section class="control-panel control-panel-wide" aria-label="Action plan">
                    <div class="control-panel-head"><h2>"Action plan"</h2><span>{count}</span></div>
                    <p class="control-empty" hidden={count != 0}>"No lifecycle baton currently needs an operator action."</p>
                    <div class="operator-action-list">{action_cards(actions)}</div>
                </section>
            </div>
        </ServerFrame>
    }
}

#[component]
pub fn ActivityPage() -> impl IntoView {
    view! {
        <Title text="activity - vc-server" />
        <Meta name="description" content="Warnings and current control-plane event tail." />
        {control_dashboard(|dashboard| activity_dashboard(dashboard).into_any())}
    }
}

fn activity_dashboard(dashboard: DashboardData) -> impl IntoView {
    let warnings = dashboard.warnings;
    let events = dashboard.events;
    let warning_count = warnings.len();
    let event_count = events.len();
    view! {
        <ServerFrame active=ServerSection::Activity status=format!("{warning_count} warnings")>
            <div class="server-console-shell route-page-shell">
                {route_header("Runtime", "Activity", "Warnings and the current event tail, separated from agent selection and lifecycle decisions.")}
                <section class="control-panel control-panel-wide" aria-label="Warnings">
                    <div class="control-panel-head"><h2>"Warnings"</h2><span>{warning_count}</span></div>
                    <p class="control-empty" hidden={warning_count != 0}>"No warnings."</p>
                    <ul class="control-warning-list">{warning_rows(warnings)}</ul>
                </section>
                <section class="control-panel control-panel-wide" aria-label="Event tail">
                    <div class="control-panel-head"><h2>"Runtime context"</h2><span>{event_count}</span></div>
                    <p class="control-empty" hidden={event_count != 0}>"No events in the current tail."</p>
                    <ul class="control-event-list">{event_rows(events)}</ul>
                </section>
            </div>
        </ServerFrame>
    }
}

#[cfg(any(feature = "ssr", feature = "hydrate"))]
const CODE_REPORTS_EMBED_ID: &str = "vc-code-reports";

/// Report documents are a different noun from scaffold plans.
/// `VIBECRAFTED_LOCTREE_REPORTS`, then `loctree_reports` in the operator
/// config, then the Loctree checkout `reports` directory measured for this room.
#[cfg(feature = "ssr")]
fn configured_loctree_reports_dir() -> std::path::PathBuf {
    #[cfg(all(test, feature = "ssr"))]
    if let Some(path) = code_reports_override() {
        return path;
    }
    if let Some(value) = std::env::var_os("VIBECRAFTED_LOCTREE_REPORTS") {
        let path = std::path::PathBuf::from(value);
        if !path.as_os_str().is_empty() {
            return path;
        }
    }
    if let Some(path) = loctree_reports_from_operator_config() {
        return path;
    }
    std::path::PathBuf::from("/Volumes/vc-workspace/Loctree/loctree/reports")
}

#[cfg(all(test, feature = "ssr"))]
std::thread_local! {
    static CODE_REPORTS_OVERRIDE: std::cell::RefCell<Option<std::path::PathBuf>> =
        const { std::cell::RefCell::new(None) };
}

#[cfg(all(test, feature = "ssr"))]
fn code_reports_override() -> Option<std::path::PathBuf> {
    CODE_REPORTS_OVERRIDE.with(|slot| slot.borrow().clone())
}

#[cfg(all(test, feature = "ssr"))]
fn set_code_reports_override(path: Option<std::path::PathBuf>) {
    CODE_REPORTS_OVERRIDE.with(|slot| *slot.borrow_mut() = path);
}

#[cfg(feature = "ssr")]
fn loctree_reports_from_operator_config() -> Option<std::path::PathBuf> {
    let config_home = std::env::var_os("XDG_CONFIG_HOME")
        .map(std::path::PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME").map(|home| std::path::PathBuf::from(home).join(".config"))
        })?;
    let text = std::fs::read_to_string(config_home.join("vibecrafted/config.toml")).ok()?;
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') || line.starts_with('[') {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        if key.trim() != "loctree_reports" {
            continue;
        }
        let value = value.trim().trim_matches('"').trim_matches('\'').trim();
        if value.is_empty() {
            return None;
        }
        return Some(std::path::PathBuf::from(value));
    }
    None
}

#[cfg(feature = "ssr")]
fn is_loctree_report_document(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    lower.ends_with(".md")
        || lower.ends_with(".markdown")
        || lower.ends_with(".html")
        || lower.ends_with(".htm")
}

/// Top-level report documents only. Directories, symlinks, and non-documents
/// stay out so a checkout's source tree cannot masquerade as the list.
#[cfg(feature = "ssr")]
fn list_loctree_report_documents(dir: &std::path::Path) -> Vec<String> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut names = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(meta) = std::fs::symlink_metadata(&path) else {
            continue;
        };
        if !meta.is_file() || meta.file_type().is_symlink() {
            continue;
        }
        let Ok(name) = entry.file_name().into_string() else {
            continue;
        };
        if name.starts_with('.') || !is_loctree_report_document(&name) {
            continue;
        }
        names.push(name);
    }
    names.sort_by(|left, right| {
        left.to_ascii_lowercase()
            .cmp(&right.to_ascii_lowercase())
            .then_with(|| left.cmp(right))
    });
    names
}

#[cfg(feature = "ssr")]
fn code_reports_embed_json() -> String {
    let names = list_loctree_report_documents(&configured_loctree_reports_dir());
    serde_json::to_string(&names)
        .unwrap_or_else(|_| "[]".to_string())
        .replace('<', "\\u003c")
}

#[cfg(feature = "ssr")]
fn code_intelligence_report_names() -> Vec<String> {
    list_loctree_report_documents(&configured_loctree_reports_dir())
}

#[cfg(all(feature = "hydrate", not(feature = "ssr")))]
fn code_intelligence_report_names() -> Vec<String> {
    let Some(window) = web_sys::window() else {
        return Vec::new();
    };
    let Some(document) = window.document() else {
        return Vec::new();
    };
    let Some(element) = document.get_element_by_id(CODE_REPORTS_EMBED_ID) else {
        return Vec::new();
    };
    let Some(json) = element.text_content() else {
        return Vec::new();
    };
    serde_json::from_str(&json).unwrap_or_default()
}

#[cfg(not(any(feature = "ssr", feature = "hydrate")))]
fn code_intelligence_report_names() -> Vec<String> {
    Vec::new()
}

#[cfg(all(test, feature = "ssr"))]
pub fn code_intelligence_is_not_a_plan_library() {
    tests::code_intelligence_is_not_a_plan_library();
}

fn code_report_rows(names: Vec<String>) -> impl IntoView {
    names
        .into_iter()
        .map(|name| {
            let label = name.clone();
            view! { <li data-code-report=name>{label}</li> }
        })
        .collect_view()
}

#[component]
pub fn StructurePage() -> impl IntoView {
    let names = code_intelligence_report_names();
    let count = names.len();
    let status = if count == 0 {
        "no reports".to_string()
    } else {
        format!("{count} reports")
    };
    let body = if names.is_empty() {
        view! {
            <p class="control-empty" data-code-empty>
                "Code intelligence has no report documents in the configured directory."
            </p>
        }
        .into_any()
    } else {
        view! {
            <ul class="control-warning-list" data-code-reports aria-label="Loctree reports">
                {code_report_rows(names)}
            </ul>
        }
        .into_any()
    };
    view! {
        <Title text="Code intelligence - vc-server" />
        <Meta name="description" content="Loctree report documents. Not a plan shelf." />
        <ServerFrame active=ServerSection::Structure status=status>
            <div class="server-console-shell route-page-shell">
                {route_header(
                    "Loctree",
                    "Code intelligence",
                    "Report documents from the configured Loctree reports directory. File names only — this room is not a plan shelf.",
                )}
                <section class="control-panel control-panel-wide" aria-label="Code intelligence">
                    <div class="control-panel-head">
                        <h2>"Loctree reports"</h2>
                        <span>{count}</span>
                    </div>
                    {body}
                </section>
            </div>
        </ServerFrame>
    }
}

#[component]
pub fn GuidePage() -> impl IntoView {
    view! {
        <Title text="guide - vc-server" />
        <Meta name="description" content="Operator paths for Vibecrafted server." />
        <ServerFrame active=ServerSection::Guide status="operator guide".to_string()>
            <div class="server-console-shell route-page-shell">
                {route_header("Guide", "From workspace to delivery", "The server is a projection of canonical state. It does not create a second scheduler or claim actions that have no server transition.")}
                <section class="control-panel control-panel-wide" aria-label="Operator path">
                    <div class="control-panel-head"><h2>"Product path"</h2><span>"canonical"</span></div>
                    <ol class="operator-guide-list">
                        <li><strong>"Workspace"</strong><span>"Create or select durable identity with the Vibecrafted workspace command, then verify it under Workspaces."</span></li>
                        <li><strong>"Sessions"</strong><span>"Inspect the logical session and its real runtime attachment."</span></li>
                        <li><strong>"Agent Manager"</strong><span>"Open live and historical runs from the control-plane projection."</span></li>
                        <li><strong>"Plans"</strong><span>"Review exactly one active Scaffold document in the studio shell."</span></li>
                        <li><strong>"Dispatch"</strong><span>"Open a `.dispatch.toml` artifact in Plans and use Dispatch. The server runs `vibecrafted dispatch` on that file."</span></li>
                    </ol>
                </section>
                <p class="server-console-links">
                    <a class="server-console-link server-console-link-primary" href="/workspaces">"Open workspaces"</a>
                    <a class="server-console-link" href="/runs">"Open Agent Manager"</a>
                    <a class="server-console-link" href="/scaffold">"Open plans"</a>
                </p>
            </div>
        </ServerFrame>
    }
}

const PROJECTS_EMBED_ID: &str = "vc-projects-canvas";

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
struct ProjectsSnapshot {
    kind: String,
    org: String,
    repo: String,
    projects: Vec<ProjectShelfView>,
    plans: Vec<ProjectPlanView>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
struct ProjectShelfView {
    org: String,
    repo: String,
    last_activity: String,
    needs_attention: bool,
    plan_count: usize,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
struct ProjectPlanView {
    plan_id: String,
    title: String,
    day: String,
    href: String,
    needs_attention: bool,
}

#[cfg(any(feature = "ssr", feature = "hydrate"))]
fn encode_projects_embed(snapshot: &ProjectsSnapshot) -> String {
    serde_json::to_string(snapshot)
        .unwrap_or_else(|_| "{}".to_string())
        .replace('<', "\\u003c")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029")
}

#[cfg(all(feature = "hydrate", not(feature = "ssr")))]
fn decode_projects_embed(json: &str) -> Option<ProjectsSnapshot> {
    serde_json::from_str(json).ok()
}

#[cfg(all(feature = "hydrate", not(feature = "ssr")))]
fn read_embedded_projects() -> Option<ProjectsSnapshot> {
    let document = web_sys::window()?.document()?;
    let json = document
        .get_element_by_id(PROJECTS_EMBED_ID)?
        .text_content()
        .filter(|text| !text.trim().is_empty())?;
    decode_projects_embed(&json)
}

#[cfg(feature = "ssr")]
fn load_projects_snapshot(org: Option<&str>, repo: Option<&str>) -> ProjectsSnapshot {
    use control_core::ScaffoldArtifactStore;

    let store = ScaffoldArtifactStore::new(control_core::vibecrafted_home());
    let detailed = store.catalog_detailed();
    snapshot_from_plans(&detailed.plans, org, repo, |plan| {
        !store.is_plan_reviewable(&plan.org, &plan.repo, &plan.day, &plan.plan_id)
    })
}

#[cfg(feature = "ssr")]
fn snapshot_from_plans(
    plans: &[control_core::ScaffoldPlanSummary],
    org: Option<&str>,
    repo: Option<&str>,
    needs_attention: impl Fn(&control_core::ScaffoldPlanSummary) -> bool,
) -> ProjectsSnapshot {
    use crate::scaffold::api::{plan_display_title, project_shelf, scaffold_document_href};

    let shelf = project_shelf(plans);
    if let (Some(org), Some(repo)) = (org, repo) {
        let plans = shelf
            .into_iter()
            .find(|group| group.org == org && group.repo == repo)
            .map(|group| {
                group
                    .plans
                    .iter()
                    .map(|plan| ProjectPlanView {
                        plan_id: plan.plan_id.clone(),
                        title: plan_display_title(&plan.plan_id),
                        day: plan.day.clone(),
                        href: scaffold_document_href(plan),
                        needs_attention: needs_attention(plan),
                    })
                    .collect()
            })
            .unwrap_or_default();
        return ProjectsSnapshot {
            kind: "project".into(),
            org: org.to_string(),
            repo: repo.to_string(),
            projects: Vec::new(),
            plans,
        };
    }
    let projects = shelf
        .iter()
        .map(|group| ProjectShelfView {
            org: group.org.clone(),
            repo: group.repo.clone(),
            last_activity: group.last_activity.clone(),
            needs_attention: group.plans.iter().any(&needs_attention),
            plan_count: group.plans.len(),
        })
        .collect();
    ProjectsSnapshot {
        kind: "index".into(),
        org: String::new(),
        repo: String::new(),
        projects,
        plans: Vec::new(),
    }
}

#[cfg(feature = "ssr")]
fn projects_snapshot(org: Option<String>, repo: Option<String>) -> ProjectsSnapshot {
    load_projects_snapshot(org.as_deref(), repo.as_deref())
}

#[cfg(all(feature = "hydrate", not(feature = "ssr")))]
fn projects_snapshot(org: Option<String>, repo: Option<String>) -> ProjectsSnapshot {
    let _ = (org, repo);
    read_embedded_projects().unwrap_or_default()
}

#[cfg(not(any(feature = "ssr", feature = "hydrate")))]
fn projects_snapshot(org: Option<String>, repo: Option<String>) -> ProjectsSnapshot {
    let _ = (org, repo);
    ProjectsSnapshot::default()
}

fn activity_label(day: &str) -> String {
    let mut parts = day.split('_');
    let year = parts.next();
    let month_day = parts.next();
    if let (Some(year), Some(month_day), None) = (year, month_day, parts.next())
        && year.len() == 4
        && month_day.len() == 4
        && year.chars().all(|character| character.is_ascii_digit())
        && month_day
            .chars()
            .all(|character| character.is_ascii_digit())
    {
        return format!("{year}-{}-{}", &month_day[..2], &month_day[2..]);
    }
    if day.is_empty() {
        "No activity".to_string()
    } else {
        day.replace('_', " · ")
    }
}

fn path_segment(value: &str) -> String {
    let mut encoded = String::new();
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
            encoded.push(byte as char);
        } else {
            encoded.push_str(&format!("%{byte:02X}"));
        }
    }
    encoded
}

fn owned_route_header(eyebrow: String, title: String, description: String) -> impl IntoView {
    view! {
        <section class="run-detail-header route-page-header">
            <div>
                <p class="section-eyebrow">{eyebrow}</p>
                <h1 class="run-detail-title">{title}</h1>
                <p class="route-page-description">{description}</p>
            </div>
        </section>
    }
}

fn project_rows(projects: Vec<ProjectShelfView>) -> impl IntoView {
    projects
        .into_iter()
        .map(|project| {
            let href = format!(
                "/projects/{}/{}",
                path_segment(&project.org),
                path_segment(&project.repo)
            );
            let name = project.repo.clone();
            let org = project.org.clone();
            let project_key = format!("{org}/{name}");
            let activity = activity_label(&project.last_activity);
            let attention = if project.needs_attention {
                "Needs attention"
            } else {
                "Clear"
            };
            let plan_count = project.plan_count;
            view! {
                <a class="workspace-card project-row" href=href data-project=project_key>
                    <div class="control-run-primary">
                        <strong class="project-name">{name}</strong>
                    </div>
                    <div class="control-run-meta">
                        <span class="project-org">{org}</span>
                        <span class="project-activity">{activity}</span>
                        <span class="project-attention">{attention}</span>
                        <span>{format!("{plan_count} plans")}</span>
                    </div>
                </a>
            }
        })
        .collect_view()
}

fn plan_rows(plans: Vec<ProjectPlanView>) -> impl IntoView {
    plans
        .into_iter()
        .map(|plan| {
            let activity = activity_label(&plan.day);
            let attention = if plan.needs_attention {
                "Needs attention"
            } else {
                "Clear"
            };
            let plan_id = plan.plan_id.clone();
            view! {
                <a class="workspace-card project-plan" href=plan.href data-plan-id=plan_id>
                    <div class="control-run-primary">
                        <strong class="project-plan-name">{plan.title}</strong>
                    </div>
                    <div class="control-run-meta">
                        <span class="project-activity">{activity}</span>
                        <span class="project-attention">{attention}</span>
                    </div>
                </a>
            }
        })
        .collect_view()
}

fn projects_frame(snapshot: ProjectsSnapshot) -> impl IntoView {
    let project_page = snapshot.kind == "project";
    let plan_count = snapshot.plans.len();
    let project_count = snapshot.projects.len();
    let status = if project_page {
        format!("{plan_count} plans")
    } else {
        format!("{project_count} repositories")
    };
    let header = if project_page {
        let title = if snapshot.repo.is_empty() {
            "Project".to_string()
        } else {
            snapshot.repo.clone()
        };
        owned_route_header(
            snapshot.org.clone(),
            title,
            "Plans in this repository. Opening one uses the studio document. Checkpoint and status stay in the inspector.".to_string(),
        )
        .into_any()
    } else {
        route_header(
            "Work",
            "Projects",
            "A project is one repository. Open it to see that repository's plans, not a filesystem path.",
        )
        .into_any()
    };
    let body = if project_page {
        let empty = snapshot.plans.is_empty();
        view! {
            <section class="control-panel control-panel-wide" aria-label="Plans">
                <div class="control-panel-head">
                    <h2>"Plans"</h2>
                    <span>{plan_count}</span>
                </div>
                <p class="server-console-links">
                    <a class="server-console-link" href="/projects">"All projects"</a>
                </p>
                {empty.then(|| view! {
                    <p class="control-empty">"This project has no plans."</p>
                })}
                <div class="project-plan-list">{plan_rows(snapshot.plans)}</div>
            </section>
        }
        .into_any()
    } else {
        let empty = snapshot.projects.is_empty();
        view! {
            <section class="control-panel control-panel-wide" aria-label="Projects">
                <div class="control-panel-head">
                    <h2>"Repositories"</h2>
                    <span>{project_count}</span>
                </div>
                {empty.then(|| view! {
                    <p class="control-empty">"No projects yet. A project is one repository."</p>
                })}
                <div class="project-list">{project_rows(snapshot.projects)}</div>
            </section>
        }
        .into_any()
    };
    view! {
        <ServerFrame active=ServerSection::Overview status=status>
            <div class="server-console-shell route-page-shell">
                {header}
                {body}
            </div>
        </ServerFrame>
    }
}

#[cfg(any(feature = "ssr", feature = "hydrate"))]
fn projects_room(snapshot: ProjectsSnapshot) -> impl IntoView {
    let embed = encode_projects_embed(&snapshot);
    view! {
        <script id=PROJECTS_EMBED_ID type="application/json" inner_html=embed></script>
        {projects_frame(snapshot)}
    }
}

#[cfg(not(any(feature = "ssr", feature = "hydrate")))]
fn projects_room(snapshot: ProjectsSnapshot) -> impl IntoView {
    projects_frame(snapshot)
}

#[component]
pub fn ProjectsPage() -> impl IntoView {
    let snapshot = projects_snapshot(None, None);
    view! {
        <Title text="Projects - vc-server" />
        <Meta name="description" content="Repositories, then the plans that belong to each one." />
        {projects_room(snapshot)}
    }
}

#[component]
pub fn ProjectPlansPage() -> impl IntoView {
    let params = leptos_router::hooks::use_params_map();
    let org = params.read_untracked().get("org").unwrap_or_default();
    let repo = params.read_untracked().get("repo").unwrap_or_default();
    let snapshot = projects_snapshot(Some(org), Some(repo));
    let title = if snapshot.repo.is_empty() {
        "Projects - vc-server".to_string()
    } else {
        format!("{} - Projects - vc-server", snapshot.repo)
    };
    view! {
        <Title text=title />
        <Meta name="description" content="Plans for one repository, filtered from the artifact shelf." />
        {projects_room(snapshot)}
    }
}

#[component]
pub fn HelpPage() -> impl IntoView {
    view! {
        <Title text="Help & docs - vc-server" />
        <Meta name="description" content="Help for someone already stuck." />
        <ServerFrame active=ServerSection::Help status="help".to_string()>
            <div class="server-console-shell route-page-shell">
                {route_header(
                    "Help",
                    "Help & docs",
                    "For someone already stuck. The operator guide stays its own page.",
                )}
                <p class="server-console-links">
                    <a class="server-console-link" href="/guide">"Operator guide"</a>
                </p>
            </div>
        </ServerFrame>
    }
}

#[component]
pub fn AboutPage() -> impl IntoView {
    view! {
        <Title text="About - vc-server" />
        <Meta name="description" content="What this server window is." />
        <ServerFrame active=ServerSection::About status="about".to_string()>
            <div class="server-console-shell route-page-shell">
                {route_header(
                    "About",
                    "About",
                    "This window is the Vibecrafted server console for one workspace.",
                )}
            </div>
        </ServerFrame>
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
struct SkillsSnapshot {
    names: Vec<String>,
    #[serde(default)]
    open: Option<String>,
    #[serde(default)]
    text: String,
}

impl SkillsSnapshot {
    fn empty() -> Self {
        Self {
            names: Vec::new(),
            open: None,
            text: String::new(),
        }
    }
}

fn encode_skills_embed(snapshot: &SkillsSnapshot) -> String {
    serde_json::to_string(snapshot)
        .unwrap_or_else(|_| "{}".to_string())
        .replace('<', "\\u003c")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029")
}

/// Directory names `discover_skills` already accepts. Anything else is not a skill.
fn skill_dir_name_ok(name: &str) -> bool {
    (name.starts_with("vc-") || name.starts_with("vetcoders-"))
        && name
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || ch == '-' || ch == '_')
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct SkillFile {
    name: String,
    path: std::path::PathBuf,
}

#[cfg(feature = "ssr")]
fn product_skill_roots() -> Vec<std::path::PathBuf> {
    use std::path::PathBuf;

    // Installer search, not a new catalog: canonical `~/.agents/skills`,
    // each runtime view, `$VIBECRAFTED_HOME/skills`, then the package tree.
    let mut roots = Vec::new();
    if let Some(home) = std::env::var_os("HOME").map(PathBuf::from) {
        roots.push(home.join(".agents/skills"));
        for runtime in ["claude", "codex", "agy", "junie", "grok", "cursor"] {
            roots.push(home.join(format!(".{runtime}/skills")));
        }
    }
    roots.push(control_core::vibecrafted_home().join("skills"));
    roots.push(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../vibecrafted-core/vibecrafted_core/skills"),
    );
    let mut unique = Vec::new();
    for root in roots {
        if !unique.iter().any(|seen: &PathBuf| seen == &root) {
            unique.push(root);
        }
    }
    unique
}

#[cfg(feature = "ssr")]
fn discover_skill_files(roots: &[std::path::PathBuf]) -> Vec<SkillFile> {
    use std::collections::BTreeSet;

    let mut seen_paths = BTreeSet::new();
    let mut seen_names = BTreeSet::new();
    let mut found = Vec::new();
    for root in roots {
        let Ok(entries) = std::fs::read_dir(root) else {
            continue;
        };
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            if !skill_dir_name_ok(&name) {
                continue;
            }
            let file = entry.path().join("SKILL.md");
            if !file.is_file() {
                continue;
            }
            let canonical = std::fs::canonicalize(&file).unwrap_or_else(|_| file.clone());
            if !seen_paths.insert(canonical) || !seen_names.insert(name.clone()) {
                continue;
            }
            found.push(SkillFile {
                name,
                path: file,
            });
        }
    }
    found.sort_by(|left, right| left.name.cmp(&right.name));
    found
}

#[cfg(feature = "ssr")]
fn save_named_skill_in(roots: &[std::path::PathBuf], name: &str, text: &str) -> Result<(), ()> {
    if !skill_dir_name_ok(name) {
        return Err(());
    }
    let Some(file) = discover_skill_files(roots)
        .into_iter()
        .find(|file| file.name == name)
    else {
        return Err(());
    };
    let canonical = std::fs::canonicalize(&file.path).map_err(|_| ())?;
    let allowed = roots.iter().any(|root| {
        std::fs::canonicalize(root)
            .ok()
            .is_some_and(|root| canonical.starts_with(&root))
    });
    if !allowed || canonical.file_name().and_then(|part| part.to_str()) != Some("SKILL.md") {
        return Err(());
    }
    std::fs::write(&canonical, text).map_err(|_| ())
}

#[cfg(feature = "ssr")]
fn save_named_skill(name: &str, text: &str) -> Result<(), ()> {
    save_named_skill_in(&product_skill_roots(), name, text)
}

#[cfg(feature = "ssr")]
fn build_skills_snapshot(open: Option<&str>) -> SkillsSnapshot {
    let files = discover_skill_files(&product_skill_roots());
    let names: Vec<String> = files.iter().map(|file| file.name.clone()).collect();
    let open = open.and_then(|name| {
        names
            .iter()
            .any(|known| known == name)
            .then(|| name.to_string())
    });
    let text = open
        .as_ref()
        .and_then(|name| files.iter().find(|file| file.name == *name))
        .and_then(|file| std::fs::read_to_string(&file.path).ok())
        .unwrap_or_default();
    SkillsSnapshot { names, open, text }
}

#[cfg(feature = "hydrate")]
fn read_embedded_skills_snapshot() -> SkillsSnapshot {
    let Some(window) = web_sys::window() else {
        return SkillsSnapshot::empty();
    };
    let Some(document) = window.document() else {
        return SkillsSnapshot::empty();
    };
    let Some(element) = document.get_element_by_id("vc-skills-data") else {
        return SkillsSnapshot::empty();
    };
    serde_json::from_str(&element.text_content().unwrap_or_default())
        .unwrap_or_else(|_| SkillsSnapshot::empty())
}

fn skills_snapshot(open: Option<String>) -> SkillsSnapshot {
    #[cfg(feature = "hydrate")]
    {
        let _ = open;
        return read_embedded_skills_snapshot();
    }
    #[cfg(all(feature = "ssr", not(feature = "hydrate")))]
    {
        return build_skills_snapshot(open.as_deref());
    }
    #[cfg(not(any(feature = "ssr", feature = "hydrate")))]
    {
        let _ = open;
        SkillsSnapshot::empty()
    }
}

fn skills_room(snapshot: SkillsSnapshot) -> impl IntoView {
    if let Some(name) = snapshot.open.clone() {
        let text = snapshot.text;
        view! {
            <main class="skills-room" data-skills-room>
                <h1>"Skills"</h1>
                <aside class="doc-pane" data-skills-body="editor" aria-label="Skills">
                    <article class="doc-sheet">
                        <form class="skills-editor" method="post" action="/api/skills/file">
                            <input type="hidden" name="name" value=name.clone() />
                            <textarea class="skills-document" name="text">{text}</textarea>
                            <button type="submit">"Save"</button>
                        </form>
                    </article>
                </aside>
                <a class="skills-back" href="/skills" rel="external">"Skills"</a>
            </main>
        }
        .into_any()
    } else if snapshot.names.is_empty() {
        view! {
            <main class="skills-room" data-skills-room>
                <h1>"Skills"</h1>
                <p class="skills-empty" data-skills-state="empty">"Skills"</p>
            </main>
        }
        .into_any()
    } else {
        let names = snapshot.names;
        view! {
            <main class="skills-room" data-skills-room>
                <h1>"Skills"</h1>
                <ul class="skills-list" data-skills-body="list">
                    {names
                        .into_iter()
                        .map(|name| {
                            let href = format!("/skills/{name}");
                            view! {
                                <li><a href=href rel="external">{name}</a></li>
                            }
                        })
                        .collect_view()}
                </ul>
            </main>
        }
        .into_any()
    }
}

fn skills_page(snapshot: SkillsSnapshot) -> impl IntoView {
    let embed = encode_skills_embed(&snapshot);
    let room = skills_room(snapshot);
    view! {
        <Title text="Skills" />
        <Meta name="description" content="Skills" />
        <ServerFrame active=ServerSection::Overview status="skills".to_string()>
            {room}
            <script id="vc-skills-data" type="application/json" inner_html=embed></script>
        </ServerFrame>
    }
}

#[component]
pub fn SkillsPage() -> impl IntoView {
    skills_page(skills_snapshot(None))
}

#[component]
pub fn SkillEditorPage() -> impl IntoView {
    let params = leptos_router::hooks::use_params_map();
    let name = params.with(|params| params.get("name"));
    skills_page(skills_snapshot(name))
}

#[cfg(feature = "ssr")]
#[derive(Debug, Deserialize)]
struct SaveSkillForm {
    name: String,
    #[serde(default)]
    text: String,
}

#[cfg(feature = "ssr")]
async fn save_skill_form(
    axum::extract::Form(form): axum::extract::Form<SaveSkillForm>,
) -> impl axum::response::IntoResponse {
    use axum::response::IntoResponse;

    if save_named_skill(&form.name, &form.text).is_ok() && skill_dir_name_ok(&form.name) {
        axum::response::Redirect::to(&format!("/skills/{}", form.name)).into_response()
    } else {
        (axum::http::StatusCode::BAD_REQUEST, "Skills").into_response()
    }
}

#[cfg(feature = "ssr")]
pub fn skills_routes() -> axum::Router<leptos::config::LeptosOptions> {
    axum::Router::new().route("/api/skills/file", axum::routing::post(save_skill_form))
}

/// `/settings` reads and writes the one product file, `config.toml`.
mod settings_config {
    use std::path::{Path, PathBuf};

    #[derive(Clone, Debug)]
    pub(super) struct SettingsView {
        pub config_path: String,
        pub state_home: String,
        pub runtime_home: String,
        pub frame_url: String,
        pub slack_url: String,
        pub agents: String,
        pub permissions: String,
        pub default_runtime: String,
        pub isolation: String,
        pub artifacts_root: String,
        pub git_repos: Vec<(String, String)>,
        pub read_error: String,
    }

    #[cfg(feature = "ssr")]
    #[derive(Debug, Clone, serde::Deserialize)]
    pub(crate) struct SettingsWrite {
        pub vc_frame_url: String,
        pub slack_console_url: String,
        pub agents: String,
        pub permissions: String,
        pub runtime: String,
        pub isolation: String,
        pub git_identity: String,
        pub git_remote: String,
    }

    pub(crate) fn canonical_artifacts_root() -> String {
        display_path(&state_home().join("artifacts"))
    }

    pub(super) fn load() -> SettingsView {
        #[cfg(feature = "ssr")]
        {
            load_from_disk()
        }
        #[cfg(not(feature = "ssr"))]
        {
            shell()
        }
    }

    fn shell() -> SettingsView {
        SettingsView {
            config_path: display_path(&product_config_path()),
            state_home: display_path(&state_home()),
            runtime_home: display_path(&runtime_home()),
            frame_url: String::new(),
            slack_url: String::new(),
            agents: String::new(),
            permissions: String::new(),
            default_runtime: String::new(),
            isolation: String::new(),
            artifacts_root: canonical_artifacts_root(),
            git_repos: Vec::new(),
            read_error: String::new(),
        }
    }

    #[cfg(feature = "ssr")]
    fn load_from_disk() -> SettingsView {
        let mut view = shell();
        let path = product_config_path();
        match read_config_text(&path) {
            Ok(text) => {
                view.frame_url = table_value(&text, "tools.vc-frame", "url").unwrap_or_default();
                view.slack_url =
                    table_value(&text, "tools.slack-console", "url").unwrap_or_default();
                view.agents = table_value(&text, "runtime.picking.research", "default_agents")
                    .unwrap_or_default();
                view.permissions =
                    table_value(&text, "runtime.picking", "permissions").unwrap_or_default();
                view.default_runtime =
                    table_value(&text, "runtime.picking", "runtime").unwrap_or_default();
                view.isolation =
                    table_value(&text, "runtime.picking", "isolation").unwrap_or_default();
                view.git_repos = repository_remotes(&text);
            }
            Err(error) => view.read_error = error,
        }
        view
    }

    #[cfg(feature = "ssr")]
    pub(crate) fn save_settings_file(path: &Path, write: &SettingsWrite) -> Result<(), String> {
        let frame = write.vc_frame_url.trim();
        let slack = write.slack_console_url.trim();
        let agents = parse_agents(&write.agents)?;
        let permissions = optional_token("Default permissions", &write.permissions)?;
        let runtime = optional_token("Default runtime", &write.runtime)?;
        let isolation = normalize_isolation(&write.isolation)?;
        let git_identity = write.git_identity.trim();
        let git_remote = write.git_remote.trim();
        if git_identity.is_empty() != git_remote.is_empty() {
            return Err("connect git needs both org/name and a remote".into());
        }
        if !git_identity.is_empty() {
            if let Some(error) = identity_rejected(git_identity) {
                return Err(error.into());
            }
            if let Some(error) = remote_rejected(git_remote) {
                return Err(error.into());
            }
        }
        if let Some(error) = tool_url_rejected(frame) {
            return Err(error.into());
        }
        if let Some(error) = tool_url_rejected(slack) {
            return Err(error.into());
        }

        let mut text = read_config_text(path)?;
        text = upsert_tool(&text, "tools.vc-frame", frame);
        text = upsert_tool(&text, "tools.slack-console", slack);
        text = upsert(
            &text,
            "runtime.picking.research",
            "default_agents",
            &toml_string_array(&agents),
        );
        text = upsert(
            &text,
            "runtime.picking",
            "permissions",
            &toml_basic_string(&permissions),
        );
        text = upsert(
            &text,
            "runtime.picking",
            "runtime",
            &toml_basic_string(&runtime),
        );
        text = upsert(
            &text,
            "runtime.picking",
            "isolation",
            &toml_basic_string(&isolation),
        );
        if !git_identity.is_empty() {
            let table = format!("repositories.{}", toml_basic_string(git_identity));
            text = upsert(&text, &table, "remote", &toml_basic_string(git_remote));
        }
        atomic_write(path, &text)
    }

    #[cfg(feature = "ssr")]
    pub fn settings_routes() -> axum::Router<leptos::config::LeptosOptions> {
        use axum::extract::Form;
        use axum::response::IntoResponse;
        use axum::routing::post;
        use axum::Router;

        async fn save_settings(Form(form): Form<SettingsWrite>) -> impl IntoResponse {
            match save_settings_file(&product_config_path(), &form) {
                Ok(()) => axum::response::Redirect::to("/settings").into_response(),
                Err(error) => (axum::http::StatusCode::BAD_REQUEST, error).into_response(),
            }
        }

        Router::new().route("/api/settings", post(save_settings))
    }

    fn env_nonempty(name: &str) -> Option<std::ffi::OsString> {
        std::env::var_os(name).filter(|value| !value.is_empty())
    }

    fn home_dir() -> PathBuf {
        #[cfg(windows)]
        if let Some(profile) = env_nonempty("USERPROFILE") {
            return PathBuf::from(profile);
        }
        env_nonempty("HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."))
    }

    fn product_config_path() -> PathBuf {
        let base = env_nonempty("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir().join(".config"));
        base.join("vibecrafted").join("config.toml")
    }

    fn state_home() -> PathBuf {
        if let Some(home) = env_nonempty("VIBECRAFTED_HOME") {
            return PathBuf::from(home);
        }
        #[cfg(windows)]
        {
            return windows_local_app_data().join("Vibecrafted").join("home");
        }
        #[cfg(not(windows))]
        {
            home_dir().join(".vibecrafted")
        }
    }

    fn runtime_home() -> PathBuf {
        if let Some(home) = env_nonempty("VIBECRAFTED_RUNTIME_HOME") {
            return PathBuf::from(home);
        }
        if let Some(data) = env_nonempty("XDG_DATA_HOME") {
            return PathBuf::from(data).join("vibecrafted");
        }
        #[cfg(windows)]
        {
            return windows_local_app_data().join("Vibecrafted");
        }
        #[cfg(not(windows))]
        {
            home_dir().join(".local").join("share").join("vibecrafted")
        }
    }

    #[cfg(windows)]
    fn windows_local_app_data() -> PathBuf {
        env_nonempty("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir().join("AppData").join("Local"))
    }

    fn display_path(path: &Path) -> String {
        path.to_string_lossy().into_owned()
    }

    #[cfg(feature = "ssr")]
    fn read_config_text(path: &Path) -> Result<String, String> {
        match std::fs::symlink_metadata(path) {
            Ok(meta) => {
                if meta.file_type().is_symlink() {
                    return Err("config.toml is a symlink; refusing to follow it".into());
                }
                if !meta.is_file() {
                    return Err("config.toml is not a regular file".into());
                }
                std::fs::read_to_string(path)
                    .map_err(|err| format!("cannot read config.toml: {err}"))
            }
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(String::new()),
            Err(err) => Err(format!("cannot inspect config.toml: {err}")),
        }
    }

    #[cfg(feature = "ssr")]
    fn atomic_write(path: &Path, contents: &str) -> Result<(), String> {
        use std::io::Write;

        let parent = path
            .parent()
            .filter(|dir| !dir.as_os_str().is_empty())
            .ok_or_else(|| "config path has no directory".to_string())?;
        std::fs::create_dir_all(parent)
            .map_err(|err| format!("cannot create config directory: {err}"))?;

        let mut mode = 0o600;
        if std::fs::symlink_metadata(path).is_ok() {
            let meta = std::fs::symlink_metadata(path)
                .map_err(|err| format!("cannot inspect config.toml: {err}"))?;
            if meta.file_type().is_symlink() {
                return Err("config.toml is a symlink; refusing to replace it".into());
            }
            if !meta.is_file() {
                return Err("config.toml is not a regular file".into());
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                mode = meta.permissions().mode() & 0o777;
            }
        }

        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        let tmp = parent.join(format!(
            ".config.toml.{}.{}",
            std::process::id(),
            nanos
        ));
        let write_tmp = || -> Result<(), String> {
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&tmp)
                .map_err(|err| format!("cannot create config tempfile: {err}"))?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                file.set_permissions(std::fs::Permissions::from_mode(mode))
                    .map_err(|err| format!("cannot set config mode: {err}"))?;
            }
            file.write_all(contents.as_bytes())
                .map_err(|err| format!("cannot write config.toml: {err}"))?;
            file.sync_all()
                .map_err(|err| format!("cannot sync config.toml: {err}"))?;
            Ok(())
        };
        if let Err(error) = write_tmp() {
            let _ = std::fs::remove_file(&tmp);
            return Err(error);
        }
        if let Err(err) = std::fs::rename(&tmp, path) {
            let _ = std::fs::remove_file(&tmp);
            return Err(format!("cannot replace config.toml: {err}"));
        }
        Ok(())
    }

    #[cfg(feature = "ssr")]
    fn split_lines(source: &str) -> Vec<String> {
        if source.is_empty() {
            return Vec::new();
        }
        let mut lines: Vec<String> = source
            .split('\n')
            .map(|line| line.trim_end_matches('\r').to_string())
            .collect();
        if lines.last().is_some_and(String::is_empty) {
            lines.pop();
        }
        lines
    }

    #[cfg(feature = "ssr")]
    fn join_lines(lines: &[String]) -> String {
        if lines.is_empty() {
            return String::new();
        }
        let mut text = lines.join("\n");
        text.push('\n');
        text
    }

    #[cfg(feature = "ssr")]
    fn strip_unquoted_comment(input: &str) -> &str {
        let mut quote: Option<char> = None;
        let mut escaped = false;
        for (index, ch) in input.char_indices() {
            if escaped {
                escaped = false;
                continue;
            }
            if quote == Some('"') && ch == '\\' {
                escaped = true;
                continue;
            }
            if ch == '"' || ch == '\'' {
                quote = match quote {
                    Some(current) if current == ch => None,
                    Some(_) => quote,
                    None => Some(ch),
                };
                continue;
            }
            if ch == '#' && quote.is_none() {
                return &input[..index];
            }
        }
        input
    }

    #[cfg(feature = "ssr")]
    fn header_of(line: &str) -> Option<String> {
        let trimmed = strip_unquoted_comment(line).trim();
        let inner = trimmed.strip_prefix('[')?.strip_suffix(']')?.trim();
        if inner.is_empty() || inner.contains('[') || inner.contains(']') {
            return None;
        }
        Some(inner.to_string())
    }

    #[cfg(feature = "ssr")]
    fn assignment_key(line: &str) -> Option<String> {
        let trimmed = strip_unquoted_comment(line).trim();
        if trimmed.is_empty() || trimmed.starts_with('[') {
            return None;
        }
        let (key, _) = trimmed.split_once('=')?;
        let key = key.trim();
        if key.is_empty()
            || !key
                .chars()
                .all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-')
        {
            return None;
        }
        Some(key.to_string())
    }

    #[cfg(feature = "ssr")]
    fn table_end(lines: &[String], header_at: usize) -> usize {
        lines
            .iter()
            .enumerate()
            .skip(header_at + 1)
            .find_map(|(index, line)| header_of(line).map(|_| index))
            .unwrap_or(lines.len())
    }

    #[cfg(feature = "ssr")]
    fn table_value(source: &str, table: &str, key: &str) -> Option<String> {
        let lines = split_lines(source);
        let header_at = lines
            .iter()
            .position(|line| header_of(line).as_deref() == Some(table))?;
        let end = table_end(&lines, header_at);
        for line in &lines[header_at + 1..end] {
            if assignment_key(line).as_deref() == Some(key) {
                let raw = line.split_once('=')?.1;
                return Some(decode_toml_value(raw));
            }
        }
        None
    }

    #[cfg(feature = "ssr")]
    fn decode_toml_value(raw: &str) -> String {
        let value = strip_unquoted_comment(raw).trim();
        if let Some(inner) = value.strip_prefix('"').and_then(|rest| rest.strip_suffix('"')) {
            return unescape_basic(inner);
        }
        if let Some(inner) = value.strip_prefix('\'').and_then(|rest| rest.strip_suffix('\'')) {
            return inner.to_string();
        }
        if let Some(inner) = value.strip_prefix('[').and_then(|rest| rest.strip_suffix(']')) {
            return decode_string_array(inner).join(", ");
        }
        value.to_string()
    }

    #[cfg(feature = "ssr")]
    fn unescape_basic(input: &str) -> String {
        let mut out = String::with_capacity(input.len());
        let mut chars = input.chars();
        while let Some(ch) = chars.next() {
            if ch != '\\' {
                out.push(ch);
                continue;
            }
            match chars.next() {
                Some('n') => out.push('\n'),
                Some('r') => out.push('\r'),
                Some('t') => out.push('\t'),
                Some('\\') => out.push('\\'),
                Some('"') => out.push('"'),
                Some('u') => {
                    let hex: String = chars.by_ref().take(4).collect();
                    match u32::from_str_radix(&hex, 16)
                        .ok()
                        .and_then(char::from_u32)
                    {
                        Some(decoded) => out.push(decoded),
                        None => {
                            out.push('u');
                            out.push_str(&hex);
                        }
                    }
                }
                Some(other) => out.push(other),
                None => out.push('\\'),
            }
        }
        out
    }

    #[cfg(feature = "ssr")]
    fn decode_string_array(inner: &str) -> Vec<String> {
        let mut items = Vec::new();
        let mut chars = inner.chars().peekable();
        while chars.peek().is_some() {
            match chars.next() {
                Some('"') => items.push(take_basic_string(&mut chars)),
                Some('\'') => {
                    let mut literal = String::new();
                    for ch in chars.by_ref() {
                        if ch == '\'' {
                            break;
                        }
                        literal.push(ch);
                    }
                    items.push(literal);
                }
                _ => {}
            }
        }
        items
    }

    #[cfg(feature = "ssr")]
    fn take_basic_string(chars: &mut std::iter::Peekable<std::str::Chars<'_>>) -> String {
        let mut out = String::new();
        let mut escaped = false;
        for ch in chars.by_ref() {
            if escaped {
                match ch {
                    'n' => out.push('\n'),
                    'r' => out.push('\r'),
                    't' => out.push('\t'),
                    '\\' => out.push('\\'),
                    '"' => out.push('"'),
                    other => out.push(other),
                }
                escaped = false;
                continue;
            }
            if ch == '\\' {
                escaped = true;
                continue;
            }
            if ch == '"' {
                break;
            }
            out.push(ch);
        }
        out
    }

    #[cfg(feature = "ssr")]
    fn repository_identity(header: &str) -> Option<String> {
        let rest = header.strip_prefix("repositories.")?;
        if let Some(inner) = rest.strip_prefix('"').and_then(|value| value.strip_suffix('"')) {
            return Some(unescape_basic(inner));
        }
        rest.strip_prefix('\'')
            .and_then(|value| value.strip_suffix('\''))
            .map(ToOwned::to_owned)
    }

    #[cfg(feature = "ssr")]
    fn repository_remotes(source: &str) -> Vec<(String, String)> {
        let lines = split_lines(source);
        let mut repos = Vec::new();
        let mut index = 0;
        while index < lines.len() {
            let Some(header) = header_of(&lines[index]) else {
                index += 1;
                continue;
            };
            let end = table_end(&lines, index);
            if let Some(identity) = repository_identity(&header) {
                let mut remote = None;
                for line in &lines[index + 1..end] {
                    if assignment_key(line).as_deref() == Some("remote") {
                        remote = line.split_once('=').map(|(_, raw)| decode_toml_value(raw));
                    }
                }
                if let Some(remote) = remote.filter(|value| !value.is_empty()) {
                    repos.push((identity, redact_remote(&remote)));
                }
            }
            index = end;
        }
        repos.truncate(64);
        repos
    }

    #[cfg(feature = "ssr")]
    fn redact_remote(remote: &str) -> String {
        if remote_has_userinfo(remote) {
            "[redacted]".into()
        } else {
            remote.to_string()
        }
    }

    #[cfg(feature = "ssr")]
    fn remote_has_userinfo(remote: &str) -> bool {
        let Some((_, rest)) = remote.split_once("://") else {
            return false;
        };
        rest.split(['/', '?', '#'])
            .next()
            .unwrap_or("")
            .contains('@')
    }

    #[cfg(feature = "ssr")]
    fn table_exists(source: &str, table: &str) -> bool {
        split_lines(source)
            .iter()
            .any(|line| header_of(line).as_deref() == Some(table))
    }

    #[cfg(feature = "ssr")]
    fn upsert(source: &str, table: &str, key: &str, value_toml: &str) -> String {
        let mut lines = split_lines(source);
        let assignment = format!("{key} = {value_toml}");
        if let Some(header_at) = lines
            .iter()
            .position(|line| header_of(line).as_deref() == Some(table))
        {
            let end = table_end(&lines, header_at);
            let mut found = false;
            for line in &mut lines[header_at + 1..end] {
                if assignment_key(line).as_deref() == Some(key) {
                    *line = assignment.clone();
                    found = true;
                }
            }
            if !found {
                lines.insert(header_at + 1, assignment);
            }
        } else {
            if !lines.is_empty() {
                lines.push(String::new());
            }
            lines.push(format!("[{table}]"));
            lines.push(assignment);
        }
        join_lines(&lines)
    }

    #[cfg(feature = "ssr")]
    fn upsert_tool(source: &str, table: &str, url: &str) -> String {
        if url.is_empty() && !table_exists(source, table) {
            return source.to_string();
        }
        upsert(source, table, "url", &toml_basic_string(url))
    }

    #[cfg(feature = "ssr")]
    fn toml_basic_string(value: &str) -> String {
        let mut out = String::from("\"");
        for ch in value.chars() {
            match ch {
                '\\' => out.push_str("\\\\"),
                '"' => out.push_str("\\\""),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                other if other.is_control() => {
                    out.push_str(&format!("\\u{:04x}", other as u32));
                }
                other => out.push(other),
            }
        }
        out.push('"');
        out
    }

    #[cfg(feature = "ssr")]
    fn toml_string_array(tokens: &[String]) -> String {
        let parts: Vec<String> = tokens.iter().map(|token| toml_basic_string(token)).collect();
        format!("[{}]", parts.join(", "))
    }

    #[cfg(feature = "ssr")]
    fn parse_agents(raw: &str) -> Result<Vec<String>, String> {
        let mut agents = Vec::new();
        for token in raw.split(|ch: char| ch == ',' || ch.is_whitespace()) {
            if token.is_empty() {
                continue;
            }
            if !token
                .chars()
                .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '.' | '-'))
            {
                return Err(format!("agent name is not a token: {token}"));
            }
            let agent = token.to_ascii_lowercase();
            if !agents.contains(&agent) {
                agents.push(agent);
            }
        }
        Ok(agents)
    }

    #[cfg(feature = "ssr")]
    fn optional_token(label: &str, raw: &str) -> Result<String, String> {
        let value = raw.trim();
        if value.is_empty() {
            return Ok(String::new());
        }
        if value.len() > 64
            || !value
                .chars()
                .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '.' | '-'))
        {
            return Err(format!("{label} must be a short token"));
        }
        Ok(value.to_string())
    }

    #[cfg(feature = "ssr")]
    fn normalize_isolation(raw: &str) -> Result<String, String> {
        match raw.trim() {
            "Worktrees" => Ok("Worktrees".into()),
            "vm" => Ok("vm".into()),
            "cloud" => Ok("cloud".into()),
            _ => Err("isolation must be Worktrees, vm, or cloud".into()),
        }
    }

    #[cfg(feature = "ssr")]
    fn identity_rejected(identity: &str) -> Option<&'static str> {
        let Some((org, name)) = identity.split_once('/') else {
            return Some("repository identity must be org/name");
        };
        if identity.matches('/').count() != 1 || !identity_token(org) || !identity_token(name) {
            return Some("repository identity must be org/name");
        }
        None
    }

    #[cfg(feature = "ssr")]
    fn identity_token(value: &str) -> bool {
        let mut chars = value.chars();
        match chars.next() {
            Some(first) if first.is_ascii_alphanumeric() => {}
            _ => return false,
        }
        chars.all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '.' | '-'))
    }

    #[cfg(feature = "ssr")]
    fn remote_rejected(remote: &str) -> Option<&'static str> {
        if remote.is_empty() {
            return Some("git remote is empty");
        }
        if remote.starts_with('-') || remote.chars().any(|ch| ch.is_control() || ch.is_whitespace())
        {
            return Some("git remote contains whitespace, control characters, or starts with -");
        }
        if remote.contains('?') || remote.contains('#') {
            return Some("git remote must not contain a query or fragment");
        }
        if let Some((scheme, rest)) = remote.split_once("://") {
            if !matches!(scheme, "https" | "http" | "ssh" | "file") {
                return Some("unsupported Git remote transport");
            }
            let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
            if let Some((userinfo, _)) = authority.rsplit_once('@') {
                if userinfo.contains(':') || (matches!(scheme, "https" | "http") && !userinfo.is_empty())
                {
                    return Some("git remote must not contain credentials");
                }
            }
        }
        None
    }

    #[cfg(feature = "ssr")]
    fn tool_url_rejected(url: &str) -> Option<&'static str> {
        if url.is_empty() {
            return None;
        }
        if url.chars().any(|ch| ch.is_control() || ch.is_whitespace()) {
            return Some("tool url contains whitespace or control characters");
        }
        let Some((scheme, rest)) = url.split_once("://") else {
            return Some("tool url must be http or https");
        };
        if !matches!(scheme, "http" | "https") {
            return Some("tool url must be http or https");
        }
        if rest.contains('?') || rest.contains('#') || rest.contains('@') {
            return Some("tool url must not contain credentials, a query, or a fragment");
        }
        if rest.is_empty() || rest.starts_with('/') {
            return Some("tool url must include a host");
        }
        None
    }
}

#[cfg(feature = "ssr")]
pub use settings_config::settings_routes;

#[cfg(all(test, feature = "ssr"))]
pub(crate) use settings_config::{SettingsWrite, canonical_artifacts_root, save_settings_file};

fn settings_git_list(repos: Vec<(String, String)>) -> impl IntoView {
    repos
        .into_iter()
        .map(|(identity, remote)| {
            view! {
                <li><code>{identity}</code><span>" "{remote}</span></li>
            }
        })
        .collect_view()
}

#[component]
pub fn SettingsPage() -> impl IntoView {
    let view_model = settings_config::load();
    let isolation_worktrees = view_model.isolation == "Worktrees";
    let isolation_vm = view_model.isolation == "vm";
    let isolation_cloud = view_model.isolation == "cloud";
    let settings_config::SettingsView {
        config_path,
        state_home,
        runtime_home,
        frame_url,
        slack_url,
        agents,
        permissions,
        default_runtime,
        artifacts_root,
        git_repos,
        read_error,
        ..
    } = view_model;

    view! {
        <Title text="Settings & config - vc-server" />
        <Meta name="description" content="Paths, MCP, agents, defaults, artifacts, and git in the existing Vibecrafted config file." />
        <ServerFrame active=ServerSection::Overview status="settings".to_string()>
            <div
                class="server-console-shell route-page-shell"
                data-settings-room="/settings"
                data-settings-path="/settings"
            >
                {route_header(
                    "Machine",
                    "Settings & config",
                    "One room for the config file this product already reads and writes.",
                )}
                <p id="settings-read-error" class="control-plane-meta">{read_error}</p>
                <form method="post" action="/api/settings">
                    <section class="control-panel control-panel-wide" aria-label="Paths">
                        <div class="control-panel-head"><h2 id="settings-paths">"Paths"</h2></div>
                        <p><span>"Config file"</span>" "<code>{config_path}</code></p>
                        <p><span>"State home"</span>" "<code>{state_home}</code></p>
                        <p><span>"Runtime home"</span>" "<code>{runtime_home}</code></p>
                    </section>
                    <section class="control-panel control-panel-wide" aria-label="MCP">
                        <div class="control-panel-head"><h2 id="settings-mcp">"MCP"</h2></div>
                        <p class="control-plane-meta">"Served tool origins already stored in the [tools] table."</p>
                        <label><span>"vc-frame"</span><input name="vc_frame_url" value=frame_url maxlength="2048" /></label>
                        <label><span>"slack-console"</span><input name="slack_console_url" value=slack_url maxlength="2048" /></label>
                    </section>
                    <section class="control-panel control-panel-wide" aria-label="Agents">
                        <div class="control-panel-head"><h2 id="settings-agents">"Agents"</h2></div>
                        <p class="control-plane-meta">"Default research agents. Stored as runtime.picking.research.default_agents."</p>
                        <label><span>"Default agents"</span><input name="agents" value=agents maxlength="400" /></label>
                    </section>
                    <section class="control-panel control-panel-wide" aria-label="Defaults">
                        <div class="control-panel-head"><h2 id="settings-defaults">"Defaults"</h2></div>
                        <label><span>"Default permissions"</span><input name="permissions" value=permissions maxlength="64" /></label>
                        <label><span>"Default runtime"</span><input name="runtime" value=default_runtime maxlength="64" /></label>
                        <label>
                            <span>"Isolation"</span>
                            <select name="isolation">
                                <option value="Worktrees" selected=isolation_worktrees>"Worktrees"</option>
                                <option value="vm" selected=isolation_vm>"vm"</option>
                                <option value="cloud" selected=isolation_cloud>"cloud"</option>
                            </select>
                        </label>
                    </section>
                    <section class="control-panel control-panel-wide" aria-label="Artifacts location">
                        <div class="control-panel-head"><h2 id="settings-artifacts">"Artifacts location"</h2></div>
                        <p class="control-plane-meta">"Canonical artifacts root."</p>
                        <p><code id="artifacts-location">{artifacts_root}</code></p>
                    </section>
                    <section class="control-panel control-panel-wide" aria-label="Git">
                        <div class="control-panel-head"><h2 id="settings-git">"Git"</h2></div>
                        <p class="control-plane-meta">"Repository remotes already stored as repositories.\"org/name\".remote."</p>
                        <ul>{settings_git_list(git_repos)}</ul>
                        <label><span>"org/name"</span><input name="git_identity" value="" maxlength="200" /></label>
                        <label><span>"remote"</span><input name="git_remote" value="" maxlength="2048" /></label>
                    </section>
                    <p class="server-console-links">
                        <button type="submit" class="server-console-link server-console-link-primary">"Save"</button>
                    </p>
                </form>
            </div>
        </ServerFrame>
    }
}

/// Machine room. Run buckets stay on Runs. Nav highlight stays on the
/// existing Activity rail until the sidebar cut owns the Diagnostics door.
pub(crate) fn diagnostics_room() -> impl IntoView {
    view! {
        <div id="diagnostics-room" class="server-console-shell route-page-shell">
            {route_header(
                "Machine",
                "Diagnostics",
                "When something is wrong, this is the machine. Logs, events, tools, server health, and process stats. Agent runs stay on their own door.",
            )}
            <section class="control-panel control-panel-wide" id="diagnostics-logs" aria-label="Logs">
                <div class="control-panel-head"><h2>"Logs"</h2><span>"transcripts"</span></div>
                <p class="route-page-description">
                    "Human logs stay on the transcript surface. The mobile nav already calls that door Logs. This room does not copy them into a second list."
                </p>
                <p class="server-console-links">
                    <a class="server-console-link server-console-link-primary" href="/transcripts">"Open logs"</a>
                    <a class="server-console-link" href="/api/control/transcripts">"Transcript index"</a>
                </p>
            </section>
            <section class="control-panel control-panel-wide" id="diagnostics-events" aria-label="Events">
                <div class="control-panel-head"><h2>"Events"</h2><span>"tail"</span></div>
                <p class="route-page-description">
                    "Control-plane events stay on the activity tail and the cursorable events feed."
                </p>
                <p class="server-console-links">
                    <a class="server-console-link server-console-link-primary" href="/activity">"Open events"</a>
                    <a class="server-console-link" href="/api/control/events">"Event stream"</a>
                </p>
            </section>
            <section class="control-panel control-panel-wide" id="diagnostics-tools" aria-label="Tools">
                <div class="control-panel-head"><h2>"Tools"</h2><span>"existing"</span></div>
                <ul class="operator-guide-list">
                    <li>
                        <strong>"cleanup"</strong>
                        <span>"The existing worktree cleanup removes a settled worker checkout. It is not a new binary and not a daemon."</span>
                    </li>
                </ul>
            </section>
            <section class="control-panel control-panel-wide" id="diagnostics-health" aria-label="Server health">
                <div class="control-panel-head"><h2>"Server health"</h2><span>"readiness"</span></div>
                <p class="route-page-description">
                    "Server health is the existing constant-time readiness check at /api/health. It does not scan retained history. The caretaker envelope remains the health verdict."
                </p>
                <p class="server-console-links">
                    <a class="server-console-link server-console-link-primary" href="/api/health">"Open server health"</a>
                    <a class="server-console-link" href="/api/control/caretaker">"Caretaker envelope"</a>
                </p>
            </section>
            <section class="control-panel control-panel-wide" id="diagnostics-process-stats" aria-label="Process stats">
                <div class="control-panel-head"><h2>"Process stats"</h2><span>"vc-monitor"</span></div>
                <p class="route-page-description">
                    "Process stats stay on vc-monitor. Run observation records that witness as unavailable in this server generation, and this page does not draw a second process table."
                </p>
                <pre class="control-plane-meta" data-monitor-source="vc-monitor">"vc-monitor"</pre>
                <p class="server-console-links">
                    <a class="server-console-link server-console-link-primary" href="/api/control/observability">"Observability index"</a>
                </p>
            </section>
        </div>
    }
}

#[component]
pub fn DiagnosticsPage() -> impl IntoView {
    view! {
        <Title text="diagnostics - vc-server" />
        <Meta name="description" content="Logs, events, tools, server health, and process stats." />
        <ServerFrame active=ServerSection::Activity status="diagnostics".to_string()>
            {diagnostics_room()}
        </ServerFrame>
    }
}

#[component]
pub fn NotFoundPage() -> impl IntoView {
    view! {
        <Title text="not found - vc-server" />
        <ServerFrame active=ServerSection::Overview status="route not found".to_string()>
            <div class="server-console-shell route-page-shell">
                {route_header("404", "Route not found", "This server route does not exist. Use the workspace navigation instead of falling back to a misleading dashboard.")}
                <p class="server-console-links"><a class="server-console-link server-console-link-primary" href="/">"Back to overview"</a></p>
            </div>
        </ServerFrame>
    }
}

#[cfg(all(test, feature = "ssr"))]
pub(crate) mod tests {
    use std::fs;
    use std::io::ErrorKind;
    use std::net::SocketAddr;
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicU64, Ordering};

    use axum::body::{Body, to_bytes};
    use axum::http::{Request, StatusCode};
    use chrono::Utc;
    use control_core::ControlPlane;
    use leptos::config::{Env, LeptosOptions};
    use leptos::prelude::*;
    use serde_json::{Value, json};
    use tower::ServiceExt;

    use super::{
        ActivityPage, AicxPage, ConsolePage, DashboardData, DashboardRun, DashboardSession,
        DashboardSessionRun, FramePage, HistoryMemory, HistoryPage, LifecyclePage, RunsPage, SessionsPage, StructurePage, TranscriptsPage, UsagePage, WorkspacesPage, aicx_page_script, console_dashboard, decode_dashboard_embed, encode_dashboard_embed, git_repo_name, history_view, load_dashboard_data_from, operator_active_runs, projects_frame, run_cards, runs_dashboard, session_cards, snapshot_from_plans, unique_runtime_labels, workspaces_dashboard,
    };
    use crate::control::api::{control_routes_for, state_payload};
    use crate::scaffold::api::project_shelf;
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
                "vc-web-settlement-{}-{nanos}-{nonce}-{attempt}",
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

    fn write_snapshot(runs_dir: &Path, run_id: &str, verdict: &str, tui: &str) {
        let payload = json!({
            "run_id": run_id,
            "state": "completed",
            "agent": "codex",
            "skill": "implement",
            "mode": "implement",
            "root": "/tmp/repo",
            "operator_session": format!("repo-{run_id}"),
            "latest_report": "",
            "latest_transcript": "",
            "last_error": "",
            "updated_at": "2026-07-22T12:00:00+00:00",
            "started_at": "2026-07-22T11:59:00+00:00",
            "health": "final",
            "source": "agent-meta",
            "lock_present": false,
            "exit_code": Value::Null,
            "liveness": "terminal",
            "launcher_pid": Value::Null,
            "completed_at": "2026-07-22T12:00:00+00:00",
            "session_id": "",
            "current_loop": Value::Null,
            "total_loops": Value::Null,
            "settlement_verdict": verdict,
            "settlement_tui": tui,
        });
        fs::write(
            runs_dir.join(format!("{run_id}.json")),
            serde_json::to_vec_pretty(&payload).expect("snapshot JSON"),
        )
        .expect("write snapshot");
    }

    fn write_workspace_catalog(home: &Path, schema: &str) {
        let root = home.join("control_plane/workspaces");
        fs::create_dir_all(root.join("sessions")).expect("workspace dirs");
        fs::write(
            root.join("catalog.json"),
            serde_json::to_vec_pretty(&json!({
                "schema": schema,
                "updated_at": "2026-08-27T10:00:00Z",
                "selected_workspace_id": "0198f84e-1234-7abc-8def-1234567890ab",
                "workspaces": {
                    "0198f84e-1234-7abc-8def-1234567890ab": {
                        "schema": "vibecrafted.workspace.v1",
                        "workspace_id": "0198f84e-1234-7abc-8def-1234567890ab",
                        "display_label": "Vibecrafted Product",
                        "canonical_root": "/work/vibecrafted",
                        "status": "active",
                        "updated_at": "2026-08-27T10:00:00Z"
                    }
                }
            }))
            .expect("catalog JSON"),
        )
        .expect("catalog");
    }

    #[test]
    fn workspace_page_renders_real_catalog_identity_and_activity() {
        let home = temp_home();
        let runs_dir = home.join("control_plane/runs");
        fs::create_dir_all(&runs_dir).expect("runs dir");
        write_snapshot(&runs_dir, "workspace-run", "finalized", "f");
        let mut snapshot: Value = serde_json::from_slice(
            &fs::read(runs_dir.join("workspace-run.json")).expect("snapshot"),
        )
        .expect("snapshot JSON");
        snapshot["root"] = Value::String("/work/vibecrafted".into());
        fs::write(
            runs_dir.join("workspace-run.json"),
            serde_json::to_vec_pretty(&snapshot).expect("snapshot JSON"),
        )
        .expect("snapshot");
        write_workspace_catalog(&home, "vibecrafted.workspace-catalog.v1");

        let plane = ControlPlane::new(&home);
        let now = chrono::DateTime::parse_from_rfc3339("2026-08-27T10:30:00Z")
            .expect("fixed now")
            .with_timezone(&Utc);
        let dashboard = load_dashboard_data_from(&plane, now);
        let owner = Owner::new();
        let html = owner.with(|| {
            provide_theme_context();
            workspaces_dashboard(dashboard).to_html()
        });

        assert!(html.contains("Vibecrafted Product"));
        assert!(html.contains("/work/vibecrafted"));
        assert!(html.contains("0198f84e-1234-7abc-8def-1234567890ab"));
        assert!(html.contains("1 recent"));
        assert!(html.contains("data-source-status=\"available\""));
        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn malformed_workspace_catalog_cannot_masquerade_as_healthy_empty_data() {
        let home = temp_home();
        write_workspace_catalog(&home, "demo.workspace-catalog");
        let plane = ControlPlane::new(&home);
        let now = chrono::DateTime::parse_from_rfc3339("2026-08-27T10:30:00Z")
            .expect("fixed now")
            .with_timezone(&Utc);
        let dashboard = load_dashboard_data_from(&plane, now);
        assert_eq!(dashboard.workspace_status, "unavailable");
        assert!(dashboard.workspaces.is_empty());
        assert!(
            dashboard
                .warnings
                .iter()
                .any(|warning| warning.contains("Workspace data unavailable"))
        );
        let owner = Owner::new();
        let html = owner.with(|| {
            provide_theme_context();
            workspaces_dashboard(dashboard).to_html()
        });
        assert!(html.contains("data-source-status=\"unavailable\""));
        assert!(html.contains("unsupported workspace catalog schema"));
        assert!(!html.contains("canonical catalog is healthy"));
        fs::remove_dir_all(home).ok();
    }

    fn canonical_id(group: u16, n: u64) -> String {
        format!("0198f84e-{group:04x}-7abc-8def-{n:012x}")
    }

    /// Short Frame socket root: macOS `sockaddr_un` holds 104 bytes, and the
    /// default TMPDIR plus `contract_version_N/<name>` already exhausts it.
    fn frame_socket_root() -> PathBuf {
        static NEXT_ROOT: AtomicU64 = AtomicU64::new(0);
        let root = PathBuf::from(format!(
            "/tmp/vcws-{}-{}",
            std::process::id(),
            NEXT_ROOT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::remove_dir_all(&root).ok();
        fs::create_dir_all(root.join("contract_version_2")).expect("frame socket root");
        root
    }

    #[test]
    fn console_home_counts_running_frame_workspaces_not_the_catalog_size() {
        use std::os::unix::net::UnixListener;

        let home = temp_home();
        let workspaces_root = home.join("control_plane/workspaces");
        fs::create_dir_all(workspaces_root.join("sessions")).expect("workspace dirs");

        // Two workspaces with a real terminal and forty worker/test identities
        // the catalog registered along the way.
        let studio = canonical_id(0x1000, 1);
        let services = canonical_id(0x1000, 2);
        let workers = (0..40).map(|n| canonical_id(0x2000, n)).collect::<Vec<_>>();
        let mut catalog = serde_json::Map::new();
        let mut register = |id: &str, label: &str, root: &str, updated_at: &str| {
            catalog.insert(
                id.to_string(),
                json!({
                    "schema": "vibecrafted.workspace.v1",
                    "workspace_id": id,
                    "display_label": label,
                    "canonical_root": root,
                    "status": "active",
                    "updated_at": updated_at,
                }),
            );
        };
        register(&studio, "Studio", "/work/studio", "2026-09-12T12:48:00Z");
        register(
            &services,
            "Services",
            "/work/services",
            "2026-09-13T15:27:00Z",
        );
        for (n, id) in workers.iter().enumerate() {
            register(
                id,
                &format!("worker-{n}"),
                &format!("/work/_integration/worker-{n}/vibecrafted"),
                "2026-09-10T00:00:00Z",
            );
        }
        fs::write(
            workspaces_root.join("catalog.json"),
            serde_json::to_vec_pretty(&json!({
                "schema": "vibecrafted.workspace-catalog.v1",
                "updated_at": "2026-09-13T18:49:06Z",
                "selected_workspace_id": studio,
                "workspaces": catalog,
            }))
            .expect("catalog JSON"),
        )
        .expect("catalog");

        let sockets = frame_socket_root();
        let socket_dir = sockets.to_str().expect("utf-8 socket root").to_string();
        let _studio_frame =
            UnixListener::bind(sockets.join("contract_version_2/studio")).expect("bind studio");
        let _services_frame =
            UnixListener::bind(sockets.join("contract_version_2/services")).expect("bind services");

        let instance = canonical_id(0x3000, 1);
        let write_session =
            |session_id: &str, workspace_id: &str, name: &str, state: &str, updated_at: &str| {
                fs::write(
                    workspaces_root.join(format!("sessions/{session_id}.json")),
                    serde_json::to_vec_pretty(&json!({
                        "schema": "vibecrafted.workspace-session.v1",
                        "session_id": session_id,
                        "workspace_id": workspace_id,
                        "workspace_instance_id": instance,
                        "updated_at": updated_at,
                        "attachments": [{
                            "runtime": "vc-frame",
                            "runtime_session_id": name,
                            "state": state,
                            "socket_dir": socket_dir,
                            "updated_at": updated_at,
                        }],
                    }))
                    .expect("session JSON"),
                )
                .expect("session");
            };
        let studio_session = canonical_id(0x4000, 1);
        let services_earlier = canonical_id(0x4000, 2);
        let services_current = canonical_id(0x4000, 3);
        write_session(
            &studio_session,
            &studio,
            "studio",
            "live",
            "2026-09-12T12:48:14+00:00",
        );
        write_session(
            &services_earlier,
            &services,
            "services",
            "live",
            "2026-09-13T15:27:24+00:00",
        );
        write_session(
            &services_current,
            &services,
            "services",
            "live",
            "2026-09-13T18:49:06+00:00",
        );
        // Six worker sessions still recorded `live` whose Frames exited long
        // ago, plus one discovery record that saw `studio` and called it dead.
        for (n, worker) in workers.iter().take(6).enumerate() {
            write_session(
                &canonical_id(0x5000, n as u64),
                worker,
                &format!("worker-{n}"),
                "live",
                "2026-09-10T00:00:00+00:00",
            );
        }
        write_session(
            &canonical_id(0x6000, 1),
            &workers[7],
            "studio",
            "dead",
            "2026-09-13T20:00:00+00:00",
        );

        let plane = ControlPlane::new(&home);
        let now = chrono::DateTime::parse_from_rfc3339("2026-09-14T04:00:00Z")
            .expect("fixed now")
            .with_timezone(&Utc);
        let dashboard = load_dashboard_data_from(&plane, now);

        assert_eq!(dashboard.workspaces.len(), 42, "the catalog stays whole");
        let live_frames = dashboard
            .live_frame_sessions
            .iter()
            .map(|frame| {
                (
                    frame.name.as_str(),
                    frame.workspace_title.as_str(),
                    frame.session_id.as_str(),
                )
            })
            .collect::<Vec<_>>();
        assert_eq!(
            live_frames,
            vec![
                ("services", "Services", services_current.as_str()),
                ("studio", "Studio", studio_session.as_str()),
            ]
        );
        let state_of = |session_id: &str| {
            dashboard
                .sessions
                .iter()
                .find(|session| session.session_id == session_id)
                .map(|session| session.state.clone())
                .expect("session projected")
        };
        assert_eq!(state_of(&studio_session), "live");
        assert_eq!(state_of(&services_current), "live");
        assert_eq!(
            state_of(&services_earlier),
            "inactive",
            "an older claim on a running Frame is superseded"
        );
        assert_eq!(
            dashboard
                .sessions
                .iter()
                .filter(|session| session.state == "live")
                .count(),
            2,
            "a `live` record without a running Frame is not live"
        );

        let owner = Owner::new();
        let (console, workspaces) = owner.with(|| {
            provide_theme_context();
            (
                console_dashboard(dashboard.clone()).to_html(),
                workspaces_dashboard(dashboard).to_html(),
            )
        });
        assert!(
            console.contains("data-live-workspaces=\"2\""),
            "home must count running Frame workspaces: {console}"
        );
        assert!(!console.contains("runtime truth"));
        assert!(!console.contains("aria-label=\"Operator summary\""));
        assert!(!console.contains("data-total-settled"));

        assert!(workspaces.contains("2 live · 42 in catalog"));
        assert!(workspaces.contains("data-live-workspaces=\"2\""));
        assert!(workspaces.contains("data-frame-session=\"studio\""));
        assert!(workspaces.contains("data-frame-session=\"services\""));
        assert!(workspaces.contains("data-history-count=\"40\""));
        let live_position = workspaces
            .find("aria-label=\"Live workspaces\"")
            .expect("live section");
        let history_position = workspaces
            .find("class=\"workspace-history\"")
            .expect("history section");
        assert!(live_position < history_position);
        let history = &workspaces[history_position..];
        assert!(history.contains("worker-39"), "catalog stays reachable");
        assert!(!history.contains(&format!("data-workspace-id=\"{studio}\"")));

        fs::remove_dir_all(home).ok();
        fs::remove_dir_all(sockets).ok();
    }

    #[test]
    fn state_json_and_runs_page_render_the_same_retained_history_counts() {
        let home = temp_home();
        let runs_dir = home.join("control_plane/runs");
        fs::create_dir_all(&runs_dir).expect("runs dir");
        write_snapshot(&runs_dir, "finalized", "finalized", "f");
        write_snapshot(&runs_dir, "failed", "failed", "x");
        write_snapshot(&runs_dir, "invalid", "invalid", "x");
        write_snapshot(&runs_dir, "attention", "needs_attention", "n");
        let locks_dir = home.join("locks");
        fs::create_dir_all(&locks_dir).expect("locks dir");
        fs::write(
            locks_dir.join("raw-only.lock"),
            "run_id=raw-only\nstatus=running\nagent=codex\n",
        )
        .expect("write raw lock");

        let plane = ControlPlane::new(&home);
        let now = chrono::DateTime::parse_from_rfc3339("2026-07-22T12:30:00+00:00")
            .expect("fixed now")
            .with_timezone(&Utc);
        let api = serde_json::to_value(state_payload(&plane, now)).expect("state JSON");
        let dashboard = load_dashboard_data_from(&plane, now);
        let owner = Owner::new();
        let (html, runs) = owner.with(|| {
            provide_theme_context();
            (
                console_dashboard(dashboard.clone()).to_html(),
                runs_dashboard(dashboard).to_html(),
            )
        });
        let board = &api["settlement_counts"];
        assert!(
            api["recent_runs"]
                .as_array()
                .expect("recent runs")
                .iter()
                .all(|run| run["run_id"] != "raw-only"),
            "the HTTP/SSR projection must use Python-owned snapshots, not rescan raw locks"
        );

        for key in [
            "active",
            "f",
            "x",
            "n",
            "invalid",
            "unclassified",
            "total_settled",
        ] {
            let expected = board[key].as_u64().expect("numeric settlement field");
            let attribute = key.replace('_', "-");
            assert!(
                runs.contains(&format!("data-{attribute}=\"{expected}\"")),
                "runs page {key} must equal API value {expected}: {runs}"
            );
        }
        let scope = board["scope"].as_str().expect("scope string");
        assert!(runs.contains(&format!("data-scope=\"{scope}\"")));
        assert!(runs.contains("aria-label=\"Retained run history\""));
        assert!(runs.contains("1 finished"));
        assert!(runs.contains("2 failed (1 invalid)"));
        assert!(runs.contains("1 need attention"));
        // The console home carries no history board: those counts describe
        // every retained snapshot, not the present.
        assert!(!html.contains("runtime truth"));
        assert!(!html.contains("aria-label=\"Operator summary\""));
        assert!(!html.contains("aria-label=\"Retained run history\""));
        assert!(!html.contains("data-total-settled"));
        assert!(html.contains("aria-label=\"Switch to light theme\""));
        assert!(html.contains("href=\"/structure\""));
        assert!(html.contains("id=\"overview-status\""));
        assert!(!html.contains("id=\"overview-inspector\""));
        assert!(!html.contains("class=\"run-table\""));
        assert!(!html.contains("id=\"usage-chart-heat\""));
        assert!(!html.contains("http://127.0.0.1:8033/"));
        assert!(!html.contains("AICX desk"));
        assert!(!html.contains("Choose the truth"));
        assert!(!html.contains("Open scaffold"));
        assert!(html.contains("Vibecrafted server navigation"));
        assert!(html.contains("server-sidebar"));
        assert!(html.contains("href=\"/runs\""));
        assert!(html.contains("href=\"/lifecycle\""));
        assert!(html.contains("href=\"/activity\""));
        assert!(html.contains("href=\"/scaffold\""));
        assert!(!html.contains("href=\"#fleet\""));
        assert!(html.contains("aria-label=\"Work\""));
        assert!(!html.contains("aria-label=\"Structure\""));
        assert!(!html.contains("aria-label=\"Active runs\""));
        assert!(!html.contains("aria-label=\"Warnings\""));
        assert!(!html.contains("aria-label=\"Action plan\""));
        assert!(!html.contains("aria-label=\"Recent state view\""));
        assert!(!html.contains("aria-label=\"Event tail\""));
        assert_eq!(board["f"], 1);
        assert_eq!(board["x"], 2);
        assert_eq!(board["invalid"], 1);
        assert_eq!(board["n"], 1);

        fs::remove_dir_all(home).ok();
    }

    #[tokio::test]
    async fn state_route_marks_stale_ownerless_lifecycle_abandoned_without_approve() {
        let home = temp_home();
        let runs_dir = home.join("control_plane/runs");
        fs::create_dir_all(&runs_dir).expect("runs dir");
        write_snapshot(&runs_dir, "finalized", "finalized", "f");
        let run_id = "life-stale-ownerless";
        let lifecycle_dir = home.join("control_plane/lifecycle_runs").join(run_id);
        fs::create_dir_all(&lifecycle_dir).expect("lifecycle run dir");
        let state_path = lifecycle_dir.join("state.json");
        fs::write(
            &state_path,
            serde_json::to_vec_pretty(&json!({
                "run_id": run_id,
                "workflow": "vc-ship",
                "agent": "codex",
                "root": "/tmp/repo",
                "status": "launching",
                "pid": 4_000_000,
                "owner_pid": 4_000_001,
                "launcher_pid": 4_000_002,
                "updated_at": "2026-09-01T00:00:00+00:00",
                "human_controls": ["approve_transition", "interrupt_workflow"],
            }))
            .expect("lifecycle state JSON"),
        )
        .expect("write lifecycle state");
        let stale = std::time::SystemTime::now()
            .checked_sub(std::time::Duration::from_secs(7 * 24 * 60 * 60))
            .expect("stale clock");
        fs::File::open(&state_path)
            .expect("state file")
            .set_modified(stale)
            .expect("stale mtime");
        let locks_dir = home.join("locks");
        fs::create_dir_all(&locks_dir).expect("locks dir");
        fs::write(
            locks_dir.join("raw-only.lock"),
            "run_id=raw-only\nstatus=running\nagent=codex\n",
        )
        .expect("write raw lock");

        let opts = LeptosOptions::builder()
            .output_name("vibecrafted-server-web-test")
            .site_root("target/site-test")
            .site_pkg_dir("pkg")
            .env(Env::PROD)
            .site_addr("127.0.0.1:0".parse::<SocketAddr>().expect("addr"))
            .reload_port(0)
            .build();
        let response = control_routes_for(&home)
            .with_state(opts)
            .oneshot(
                Request::builder()
                    .uri("/api/control/state")
                    .body(Body::empty())
                    .expect("state request"),
            )
            .await
            .expect("state response");
        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), 1024 * 1024)
            .await
            .expect("state body");
        let payload: Value = serde_json::from_slice(&body).expect("state JSON");
        let recent = payload["recent_runs"].as_array().expect("recent runs");
        let container = recent
            .iter()
            .find(|run| run["run_id"] == run_id)
            .expect("stale lifecycle container stays discoverable");
        assert_eq!(
            container["state"], "abandoned",
            "the snapshot-backed state route must carry the liveness overlay"
        );
        assert_eq!(container["health"], "stalled");
        assert_eq!(container["last_error"], "no live owner");
        assert!(
            payload["active_runs"]
                .as_array()
                .expect("active runs")
                .iter()
                .all(|run| run["run_id"] != run_id),
            "an ownerless lifecycle container must never advertise launching"
        );
        let raw = String::from_utf8(body.to_vec()).expect("state body utf8");
        assert!(
            !raw.contains("approve_transition"),
            "an abandoned run must not keep approve_transition as a human control"
        );
        assert!(
            recent.iter().all(|run| run["run_id"] != "raw-only"),
            "the state route reads Python-owned snapshots, never raw locks"
        );

        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn navigation_uses_dedicated_views_and_run_cards_open_human_transcripts() {
        let owner = Owner::new();
        let (workspaces, sessions, runs, lifecycle, activity, structure, card) = owner.with(|| {
            leptos_meta::provide_meta_context();
            provide_theme_context();
            let card = run_cards(vec![DashboardRun {
                run_id: "impl-live-agent".into(),
                agent: "codex".into(),
                health: "active".into(),
                state: "running".into(),
                ..DashboardRun::default()
            }])
            .to_html();
            (
                WorkspacesPage().to_html(),
                SessionsPage().to_html(),
                RunsPage().to_html(),
                LifecyclePage().to_html(),
                ActivityPage().to_html(),
                StructurePage().to_html(),
                card,
            )
        });

        assert!(workspaces.contains("Workspace catalog"));
        assert!(sessions.contains("Session attachments"));
        assert!(runs.contains("run-detail-title"));
        assert!(runs.contains(">Runs<"));
        assert!(runs.contains(">Success<"));
        assert!(runs.contains(">Needs Attention<"));
        assert!(runs.contains(">Failures<"));
        assert!(runs.contains(">Current<"));
        assert!(runs.contains(">Queued<"));
        assert!(runs.contains("transcript.human.log"));
        assert!(!runs.contains("Current agents"));
        assert!(!runs.contains("<h2>Stalled</h2>"));
        assert!(lifecycle.contains("Action plan"));
        assert!(activity.contains("Runtime context"));
        assert!(activity.contains("Warnings"));
        assert!(structure.contains("Code intelligence"));
        assert!(structure.contains("Loctree reports") || structure.contains("data-code-empty"));
        assert!(!structure.contains("id=\"loctree-generate\""));
        assert!(!structure.contains("id=\"aicx-search-form\""));
        assert!(!structure.contains("href=\"/Volumes/"));
        assert!(card.contains("href=\"/run/impl-live-agent\""));
        assert!(!card.contains("Open transcript"));
        assert!(card.contains("data-ppm=\"run\""));
        assert!(card.contains("control-copy"));
    }

    pub(super) fn code_intelligence_is_not_a_plan_library() {
        let home = temp_home();
        let plan_id = "shelf-plan-not-a-row";
        let plan_root = home
            .join("artifacts/vetcoders/vibecrafted/2026_0925/plans")
            .join(plan_id);
        fs::create_dir_all(&plan_root).expect("plan root");
        fs::write(
            plan_root.join("manifest.json"),
            r#"{
                "schema_version": "1",
                "plan_id": "shelf-plan-not-a-row",
                "org": "vetcoders",
                "repo": "vibecrafted",
                "day": "2026_0925",
                "artifacts": []
            }"#,
        )
        .expect("manifest");
        let shelf = control_core::ScaffoldArtifactStore::new(&home).catalog_detailed();
        assert!(
            shelf.plans.iter().any(|plan| plan.plan_id == plan_id),
            "catalog_detailed must see the planted plan so a leaked row can fail this test"
        );

        let reports = home.join("reports");
        fs::create_dir_all(reports.join("nested")).expect("nested");
        fs::write(reports.join("beta-notes.html"), "<p>report</p>").expect("html");
        fs::write(reports.join("alpha-project.md"), "# report\n").expect("md");
        fs::write(reports.join("notes.txt"), "not a report document").expect("txt");
        fs::write(reports.join("Cargo.toml"), "[package]\n").expect("toml");
        fs::create_dir_all(reports.join(plan_id)).expect("plan-named dir");
        std::os::unix::fs::symlink("alpha-project.md", reports.join("linked.md")).expect("symlink");

        super::set_code_reports_override(Some(reports.clone()));
        let _override = CodeReportsOverrideGuard;
        let html = render_structure_page();
        super::set_code_reports_override(None);

        let rows = code_report_rows_from(&html);
        assert_eq!(
            rows,
            vec![
                "alpha-project.md".to_string(),
                "beta-notes.html".to_string()
            ]
        );
        assert!(html.contains("Code intelligence"));
        assert!(
            html.contains("<h1 class=\"run-detail-title\">Code intelligence</h1>")
                || html.contains(">Code intelligence<")
        );
        for plan in &shelf.plans {
            assert!(
                !rows.iter().any(|row| row == &plan.plan_id),
                "plan_id {} rendered as a row on Code intelligence",
                plan.plan_id
            );
        }
        assert!(!html.contains(plan_id));
        assert!(!html.contains("catalog_detailed"));
        assert!(!html.contains("id=\"aicx-search-form\""));

        let empty = home.join("empty-reports");
        fs::create_dir_all(&empty).expect("empty reports");
        super::set_code_reports_override(Some(empty));
        let empty_html = render_structure_page();
        super::set_code_reports_override(None);
        assert!(empty_html.contains("Code intelligence"));
        assert!(empty_html.contains("data-code-empty"));
        assert!(
            code_report_rows_from(&empty_html).is_empty(),
            "an empty reports directory must not invent rows"
        );

        let missing = home.join("missing-reports");
        super::set_code_reports_override(Some(missing));
        let missing_html = render_structure_page();
        super::set_code_reports_override(None);
        assert!(missing_html.contains("Code intelligence"));
        assert!(missing_html.contains("data-code-empty"));
        assert!(code_report_rows_from(&missing_html).is_empty());

        fs::remove_dir_all(home).ok();
    }

    struct CodeReportsOverrideGuard;

    impl Drop for CodeReportsOverrideGuard {
        fn drop(&mut self) {
            super::set_code_reports_override(None);
        }
    }

    fn render_structure_page() -> String {
        let owner = Owner::new();
        owner.with(|| {
            leptos_meta::provide_meta_context();
            provide_theme_context();
            StructurePage().to_html()
        })
    }

    fn code_report_rows_from(html: &str) -> Vec<String> {
        let needle = "data-code-report=\"";
        let mut rows = Vec::new();
        let mut rest = html;
        while let Some(start) = rest.find(needle) {
            let after = &rest[start + needle.len()..];
            let Some(end) = after.find('"') else {
                break;
            };
            rows.push(after[..end].to_string());
            rest = &after[end + 1..];
        }
        rows
    }

    #[test]
    fn transcripts_and_frame_pages_name_their_doors() {
        let owner = Owner::new();
        let (transcripts, frame, aicx, usage) = owner.with(|| {
            leptos_meta::provide_meta_context();
            provide_theme_context();
            (
                TranscriptsPage().to_html(),
                FramePage().to_html(),
                AicxPage().to_html(),
                UsagePage().to_html(),
            )
        });
        assert!(transcripts.contains("id=\"transcript-search-form\""));
        assert!(transcripts.contains("/api/control/transcripts"));
        assert!(transcripts.contains("id=\"transcript-search-more\""));
        assert!(transcripts.contains("has_more"));
        assert!(transcripts.contains("vc-focus-refresh"));
        assert!(!transcripts.contains("row.innerHTML"));
        assert!(transcripts.contains("snippet.textContent"));
        assert!(frame.contains("vc-frame web"));
        assert!(frame.contains("[tools.vc-frame]"));
        assert!(frame.contains("Open in Tab"));
        assert!(frame.contains("--ip"));
        assert!(frame.contains("Tabs never start"));
        assert!(aicx.contains("id=\"aicx-search-form\""));
        assert!(aicx.contains("/api/aicx/search"));
        assert!(aicx.contains("location.search"));
        assert!(aicx.contains("Search AICX"));
        assert!(usage.contains("Cost &amp; usage"));
        assert!(usage.contains("id=\"usage-filter-form\""));
        assert!(usage.contains("/api/usage?"));
        assert!(usage.contains("data-quota-board"));
        assert!(usage.contains("/api/usage/quota"));
        assert!(usage.contains("Live quota"));
        assert!(usage.contains("api-equiv"));
        assert!(usage.contains("vibecrafted.usage-report.v1"));
        assert!(usage.contains("No canonical runtime runs match this window and filter."));
        assert!(usage.contains("id=\"usage-chart-cost\""));
        assert!(usage.contains("recorded_at"));
        assert!(!usage.contains("innerHTML"));
    }

    #[test]
    fn session_transcript_links_are_explicitly_logical_not_provider_identity() {
        let owner = Owner::new();
        let html = owner.with(|| {
            provide_theme_context();
            session_cards(vec![DashboardSession {
                session_id: "vibecrafted-session-01".into(),
                workspace_id: "workspace-01".into(),
                runtime: "vc-frame".into(),
                state: "live".into(),
                runs: vec![DashboardSessionRun {
                    run_id: "run-logical-01".into(),
                    state: "running".into(),
                    health: "active".into(),
                }],
                ..DashboardSession::default()
            }])
            .to_html()
        });
        assert!(html.contains("href=\"/run/run-logical-01\""));
        assert!(!html.contains("provider-session-01"));
    }

    #[test]
    fn session_runtime_labels_dedup_repeated_attachments() {
        assert_eq!(
            unique_runtime_labels(["vc-frame", "vc-frame", "vc-terminal", ""]),
            "vc-frame, vc-terminal"
        );
    }

    #[test]
    fn git_repo_name_uses_the_last_path_segment() {
        assert_eq!(git_repo_name("/work/vibecrafted"), "vibecrafted");
        assert_eq!(git_repo_name("/work/vibecrafted/"), "vibecrafted");
        assert_eq!(git_repo_name("vibecrafted"), "vibecrafted");
    }

    #[test]
    fn active_hero_quarantines_smoke_terminal_and_stale_marbles_noise() {
        fn run(run_id: &str, state: &str, health: &str, skill: &str) -> DashboardRun {
            DashboardRun {
                run_id: run_id.to_string(),
                state: state.to_string(),
                health: health.to_string(),
                skill: skill.to_string(),
                ..DashboardRun::default()
            }
        }

        let visible = operator_active_runs(vec![
            run("smoke-nonexistent", "running", "active", "implement"),
            run("smoke-old", "running", "active", "implement"),
            run("marb-old", "completed", "final", "marbles"),
            run(
                "terminal-but-marked-active",
                "completed",
                "active",
                "implement",
            ),
            run("real-worker", "running", "active", "ownership"),
        ]);

        assert_eq!(visible.len(), 1);
        assert_eq!(visible[0].run_id, "real-worker");
    }

    #[test]
    fn client_dashboard_wire_payload_is_not_default_and_survives_script_embed() {
        let home = temp_home();
        let runs_dir = home.join("control_plane/runs");
        fs::create_dir_all(&runs_dir).expect("runs dir");
        write_snapshot(&runs_dir, "finalized", "finalized", "f");
        write_snapshot(&runs_dir, "failed", "failed", "x");
        write_snapshot(&runs_dir, "attention", "needs_attention", "n");

        let plane = ControlPlane::new(&home);
        let now = chrono::DateTime::parse_from_rfc3339("2026-07-22T12:30:00+00:00")
            .expect("fixed now")
            .with_timezone(&Utc);
        let dashboard = load_dashboard_data_from(&plane, now);
        assert_ne!(
            dashboard,
            DashboardData::default(),
            "SSR/client payload must carry control-plane truth, not DashboardData::default zeros"
        );
        assert_eq!(dashboard.settlement.f, 1);
        assert_eq!(dashboard.settlement.x, 1);
        assert_eq!(dashboard.settlement.n, 1);

        let restored: DashboardData =
            serde_json::from_str(&serde_json::to_string(&dashboard).expect("serialize dashboard"))
                .expect("deserialize dashboard");
        assert_eq!(restored.settlement, dashboard.settlement);
        assert_eq!(restored.recent_runs.len(), dashboard.recent_runs.len());
        assert_eq!(
            decode_dashboard_embed(&encode_dashboard_embed(&dashboard)).as_ref(),
            Some(&dashboard)
        );

        let mut hostile = dashboard.clone();
        hostile
            .warnings
            .push("click <script>alert(1)</script>".into());
        let embed = encode_dashboard_embed(&hostile);
        assert!(
            !embed.contains('<'),
            "embedded JSON must not break out of <script>: {embed}"
        );
        let decoded = decode_dashboard_embed(&embed).expect("script-safe embed");
        assert_eq!(decoded.warnings, hostile.warnings);
        assert_eq!(
            decode_dashboard_embed(&encode_dashboard_embed(&DashboardData::default())),
            None,
            "default zeros must not be treated as a hydrated control-plane payload"
        );

        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn ssr_console_embeds_dashboard_json_instead_of_client_zeros() {
        let owner = Owner::new();
        let html = owner.with(|| {
            leptos_meta::provide_meta_context();
            provide_theme_context();
            ConsolePage().to_html()
        });
        assert!(html.contains("id=\"vc-dashboard-data\""));
        assert!(html.contains("type=\"application/json\""));
        assert!(!html.contains("Loading control plane"));
        assert!(html.contains("Overview"));
        assert!(html.contains("href=\"/transcripts\""));
        assert!(html.contains("href=\"/frame\""));
        assert!(!html.contains("Control plane"));
    }

    #[tokio::test]
    async fn dashboard_http_route_returns_ssr_payload_not_default() {
        let home = temp_home();
        let runs_dir = home.join("control_plane/runs");
        fs::create_dir_all(&runs_dir).expect("runs dir");
        write_snapshot(&runs_dir, "finalized", "finalized", "f");
        write_snapshot(&runs_dir, "failed", "failed", "x");
        write_snapshot(&runs_dir, "attention", "needs_attention", "n");

        let opts = LeptosOptions::builder()
            .output_name("vibecrafted-server-web-test")
            .site_root("target/site-test")
            .site_pkg_dir("pkg")
            .env(Env::PROD)
            .site_addr("127.0.0.1:0".parse::<SocketAddr>().expect("addr"))
            .reload_port(0)
            .build();
        let response = control_routes_for(&home)
            .with_state(opts)
            .oneshot(
                Request::builder()
                    .uri("/api/control/dashboard")
                    .body(Body::empty())
                    .expect("dashboard request"),
            )
            .await
            .expect("dashboard response");
        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), 1024 * 1024)
            .await
            .expect("dashboard body");
        let payload: DashboardData = serde_json::from_slice(&body).expect("dashboard JSON");
        assert_ne!(payload, DashboardData::default());
        assert_eq!(payload.settlement.f, 1);
        assert_eq!(payload.settlement.x, 1);
        assert_eq!(payload.settlement.n, 1);
        assert_eq!(
            decode_dashboard_embed(&encode_dashboard_embed(&payload)).as_ref(),
            Some(&payload)
        );

        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn aicx_client_maps_validation_without_service_unavailable_label() {
        let script = aicx_page_script();
        assert!(script.contains("payload.kind === 'validation' || response.status === 400"));
        assert!(script.contains("Project must be an owner/repo slug"));
        let validation = script
            .split("payload.kind === 'validation'")
            .nth(1)
            .expect("validation branch");
        let before_catch = validation.split("catch (error)").next().expect("pre-catch");
        assert!(before_catch.contains("status.textContent = message"));
        assert!(!before_catch.contains("AICX unavailable:"));
        let catch = script.split("catch (error)").nth(1).expect("catch");
        assert!(catch.contains("AICX unavailable:"));
    }

    fn bucket_html<'a>(html: &'a str, id: &str) -> &'a str {
        let marker = format!("data-run-bucket=\"{id}\"");
        let start = html
            .find(&marker)
            .unwrap_or_else(|| panic!("missing bucket {id}"));
        let rest = &html[start + marker.len()..];
        let end = rest.find("data-run-bucket=").unwrap_or(rest.len());
        &rest[..end]
    }

    #[test]
    pub(crate) fn runs_five_buckets_use_settlement() {
        let home = temp_home();
        let runs_dir = home.join("control_plane/runs");
        fs::create_dir_all(&runs_dir).expect("runs dir");
        let plan_report = "/var/artifacts/vetcoders/vibecrafted/2026_0925/plans/zen-rooms-ia/reports/w2-02.md";

        let write = |run_id: &str,
                     state: &str,
                     health: &str,
                     verdict: Option<&str>,
                     tui: Option<&str>,
                     report: &str,
                     transcript: &str,
                     exit_code: Option<i64>| {
            let payload = json!({
                "run_id": run_id,
                "state": state,
                "agent": "grok",
                "skill": "implement",
                "mode": "implement",
                "root": "/tmp/repo",
                "operator_session": format!("repo-{run_id}"),
                "latest_report": report,
                "latest_transcript": transcript,
                "last_error": "",
                "updated_at": "2026-07-22T12:29:30+00:00",
                "started_at": "2026-07-22T11:59:00+00:00",
                "health": health,
                "source": "agent-meta",
                "lock_present": false,
                "exit_code": exit_code,
                "liveness": if health == "active" || health == "stalled" { "live" } else { "terminal" },
                "launcher_pid": Value::Null,
                "completed_at": if health == "final" { "2026-07-22T12:00:00+00:00" } else { "" },
                "session_id": "",
                "current_loop": Value::Null,
                "total_loops": Value::Null,
                "settlement_verdict": verdict,
                "settlement_tui": tui,
            });
            fs::write(
                runs_dir.join(format!("{run_id}.json")),
                serde_json::to_vec_pretty(&payload).expect("snapshot JSON"),
            )
            .expect("write snapshot");
        };

        write(
            "settled-ok",
            "completed",
            "final",
            Some("finalized"),
            Some("f"),
            plan_report,
            "",
            Some(0),
        );
        write(
            "needs-a-look",
            "completed",
            "final",
            Some("needs_attention"),
            Some("n"),
            "",
            "/tmp/missing-transcript.log",
            Some(0),
        );
        write(
            "broke",
            "completed",
            "final",
            Some("failed"),
            Some("x"),
            "",
            "",
            None,
        );
        write(
            "bad-receipt",
            "completed",
            "final",
            Some("invalid"),
            Some("x"),
            "",
            "",
            Some(1),
        );
        write(
            "exit-zero-unsettled",
            "completed",
            "final",
            None,
            None,
            "",
            "",
            Some(0),
        );
        write(
            "exit-one-unsettled",
            "completed",
            "final",
            None,
            Some("x"),
            "",
            "",
            Some(1),
        );
        write(
            "live-now",
            "running",
            "active",
            None,
            None,
            plan_report,
            "",
            None,
        );
        write("stuck", "running", "stalled", None, None, "", "", None);

        let human = home.join("control_plane/runtime_runs/settled-ok");
        fs::create_dir_all(&human).expect("human transcript dir");
        fs::write(human.join("transcript.human.log"), "worker said hello\n")
            .expect("human transcript");

        let queued_id = "cut-waiting";
        let lifecycle_dir = home.join("control_plane/lifecycle_runs").join(queued_id);
        fs::create_dir_all(&lifecycle_dir).expect("lifecycle dir");
        fs::write(
            lifecycle_dir.join("state.json"),
            serde_json::to_vec_pretty(&json!({
                "run_id": queued_id,
                "workflow": "vc-ship",
                "agent": "grok",
                "root": "/tmp/repo",
                "status": "queued",
                "updated_at": "2026-07-22T12:29:30+00:00",
                "owner_pid": std::process::id() as i64,
                "report_path": plan_report,
                "transcript_path": "",
            }))
            .expect("lifecycle JSON"),
        )
        .expect("write lifecycle state");

        let plane = ControlPlane::new(&home);
        let now = chrono::DateTime::parse_from_rfc3339("2026-07-22T12:30:00+00:00")
            .expect("fixed now")
            .with_timezone(&Utc);
        let dashboard = load_dashboard_data_from(&plane, now);
        let owner = Owner::new();
        let html = owner.with(|| {
            provide_theme_context();
            runs_dashboard(dashboard).to_html()
        });

        for heading in [
            "Success",
            "Needs Attention",
            "Failures",
            "Current",
            "Queued",
        ] {
            assert!(
                html.contains(&format!("<h2>{heading}</h2>")),
                "missing heading {heading}"
            );
        }
        for extra in [
            "stalled",
            "recent",
            "invalid",
            "unclassified",
            "abandoned",
            "exit",
        ] {
            assert!(
                !html.contains(&format!("data-run-bucket=\"{extra}\"")),
                "sixth bucket {extra} must not be invented"
            );
        }
        assert!(!html.contains("<h2>Stalled</h2>"));
        assert!(!html.contains("<h2>Recent</h2>"));
        assert!(!html.contains("<h2>Invalid</h2>"));

        let success = bucket_html(&html, "success");
        let attention = bucket_html(&html, "needs-attention");
        let failures = bucket_html(&html, "failures");
        let current = bucket_html(&html, "current");
        let queued = bucket_html(&html, "queued");

        assert!(success.contains("settled-ok"));
        assert!(success.contains("data-run-door=\"plan\""));
        assert!(success.contains("plan_id=zen-rooms-ia"));
        assert!(success.contains("org=vetcoders"));
        assert!(success.contains("data-run-door=\"transcript\""));
        assert!(success.contains("data-run-door=\"dock\""));
        assert!(success.contains("data-transcript-url="));
        assert!(!attention.contains("data-run-door=\"transcript\""));
        assert!(!attention.contains("data-transcript-url="));
        assert!(attention.contains("data-run-door=\"dock\""));
        assert!(attention.contains("needs-a-look"));
        assert!(failures.contains("broke"));
        assert!(!failures.contains("bad-receipt"));
        assert!(!failures.contains("exit-one-unsettled"));
        assert!(!success.contains("exit-zero-unsettled"));
        assert!(!success.contains("bad-receipt"));
        assert!(current.contains("live-now"));
        assert!(
            current.contains("stuck"),
            "stalled stays in Current, not a sixth heading"
        );
        assert!(current.contains("data-run-door=\"plan\""));
        assert!(!current.contains("data-run-door=\"transcript\""));
        assert!(queued.contains("cut-waiting"));
        assert!(queued.contains("data-run-door=\"plan\""));
        assert!(html.contains("id=\"overview-inspector\""));
        assert!(html.contains("data-inspector-id"));

        for (label, slice) in [
            ("success", success),
            ("attention", attention),
            ("failures", failures),
            ("current", current),
            ("queued", queued),
        ] {
            assert!(
                !slice.contains("exit-zero-unsettled") || label == "nowhere",
                "exit 0 without a verdict is not Success ({label})"
            );
            assert!(
                !slice.contains("exit-one-unsettled"),
                "exit 1 / tui x without verdict failed is not Failures ({label})"
            );
            assert!(
                !slice.contains("bad-receipt"),
                "invalid is not a bucket ({label})"
            );
        }

        fs::remove_dir_all(home).ok();
    }

    fn write_catalog_plan(home: &Path, repo: &str, day: &str, plan_id: &str) -> PathBuf {
        let root = home
            .join("artifacts/vetcoders")
            .join(repo)
            .join(day)
            .join("plans")
            .join(plan_id);
        fs::create_dir_all(&root).expect("plan root");
        let manifest = json!({
            "schema_version": "1",
            "plan_id": plan_id,
            "org": "vetcoders",
            "repo": repo,
            "day": day,
            "artifacts": [{
                "id": "driver",
                "role": "driver",
                "path": "DRIVER.md",
                "editable": true,
                "required": true
            }]
        });
        fs::write(
            root.join("manifest.json"),
            serde_json::to_vec_pretty(&manifest).expect("manifest json"),
        )
        .expect("manifest");
        root
    }

    /// Two symlink aliases of one plan_id stay one row under the repository.
    /// The list is `catalog_detailed` after the repo predicate, not a second walk.
    pub(crate) fn projects_filter_deduped_shelf() {
        let home = temp_home();
        let real = write_catalog_plan(&home, "vibecrafted", "2026_0925", "one-plan");
        write_catalog_plan(&home, "vibecrafted", "2026_0925", "two-plan");
        write_catalog_plan(&home, "loctree", "2026_0901", "other-plan");
        let repo_dir = real
            .parent()
            .and_then(|plans| plans.parent())
            .and_then(|day| day.parent())
            .expect("repo dir");
        let suite_alias = repo_dir.with_file_name("vibecrafted-suite");
        let local_org = home.join("artifacts/local");
        fs::create_dir_all(&local_org).expect("local org");
        let local_alias = local_org.join("vibecrafted-suite");
        std::os::unix::fs::symlink(repo_dir, &suite_alias).expect("suite alias");
        std::os::unix::fs::symlink(repo_dir, &local_alias).expect("local alias");
        assert!(
            suite_alias
                .symlink_metadata()
                .unwrap()
                .file_type()
                .is_symlink()
        );
        assert!(
            local_alias
                .symlink_metadata()
                .unwrap()
                .file_type()
                .is_symlink()
        );

        let store = control_core::ScaffoldArtifactStore::new(&home);
        let detailed = store.catalog_detailed();
        let raw_one = detailed
            .plans
            .iter()
            .filter(|plan| plan.plan_id == "one-plan")
            .count();
        assert_eq!(
            raw_one, 3,
            "alias fixture produced {raw_one} catalog rows for one-plan"
        );
        let shelf = project_shelf(&detailed.plans);
        let vibecrafted = shelf
            .iter()
            .find(|group| group.org == "vetcoders" && group.repo == "vibecrafted")
            .expect("vibecrafted project");
        assert_eq!(
            vibecrafted
                .plans
                .iter()
                .filter(|plan| plan.plan_id == "one-plan")
                .count(),
            1,
            "alias-tripled plan_id must appear once"
        );
        assert_eq!(vibecrafted.plans.len(), 2);
        let loctree = shelf
            .iter()
            .find(|group| group.repo == "loctree")
            .expect("loctree project");
        assert_eq!(loctree.plans.len(), 1);

        let attention = |plan: &control_core::ScaffoldPlanSummary| {
            !store.is_plan_reviewable(&plan.org, &plan.repo, &plan.day, &plan.plan_id)
        };
        let index = snapshot_from_plans(&detailed.plans, None, None, &attention);
        let detail = snapshot_from_plans(
            &detailed.plans,
            Some("vetcoders"),
            Some("vibecrafted"),
            &attention,
        );
        assert_eq!(detail.plans.len(), vibecrafted.plans.len());
        assert_eq!(
            detail
                .plans
                .iter()
                .filter(|plan| plan.plan_id == "one-plan")
                .count(),
            1
        );
        assert!(
            detail
                .plans
                .iter()
                .all(|plan| plan.href.starts_with("/scaffold?"))
        );
        assert!(
            detail
                .plans
                .iter()
                .all(|plan| !plan.href.contains(&real.display().to_string()))
        );

        let owner = Owner::new();
        let (index_html, detail_html) = owner.with(|| {
            provide_theme_context();
            (
                projects_frame(index).to_html(),
                projects_frame(detail).to_html(),
            )
        });

        assert_eq!(
            index_html
                .matches("aria-label=\"Vibecrafted server navigation\"")
                .count(),
            1
        );
        assert_eq!(
            detail_html
                .matches("aria-label=\"Vibecrafted server navigation\"")
                .count(),
            1
        );
        assert!(!index_html.contains("review-sidebar"));
        assert!(!detail_html.contains("review-sidebar"));
        assert!(!detail_html.contains("aria-label=\"Scaffold artifacts\""));
        assert!(
            index_html.contains("<h1 class=\"run-detail-title\">Projects</h1>")
                || index_html.contains(">Projects<")
        );
        assert!(index_html.contains("class=\"project-name\">vibecrafted<"));
        assert!(index_html.contains("class=\"project-name\">loctree<"));
        assert!(!index_html.contains("vibecrafted-suite"));
        assert!(!index_html.contains("artifacts/vetcoders"));
        assert!(!detail_html.contains("vibecrafted-suite"));
        assert!(!detail_html.contains("artifacts/vetcoders"));
        assert!(!detail_html.contains(&real.display().to_string()));
        assert_eq!(detail_html.matches("data-plan-id=\"one-plan\"").count(), 1);
        assert_eq!(detail_html.matches("data-plan-id=\"two-plan\"").count(), 1);
        assert!(!detail_html.contains("data-plan-id=\"other-plan\""));
        assert!(detail_html.contains("/scaffold?"));
        assert!(detail_html.contains("plan_id=one-plan"));
        assert!(detail_html.contains("Needs attention"));
        assert!(
            detail_html.contains("<h1 class=\"run-detail-title\">vibecrafted</h1>")
                || detail_html.contains(">vibecrafted<")
        );
        assert!(!detail_html.contains("id=\"plan-search\""));
        assert!(index_html.contains("href=\"/projects/vetcoders/vibecrafted\""));

        fs::remove_dir_all(home).ok();
    }

    fn skills_room_html(html: &str) -> &str {
        let marker_at = html.find("data-skills-room").expect("skills room");
        let start = html[..marker_at].rfind("<main").expect("skills main");
        let end = html[start..]
            .find("</main>")
            .expect("skills room end")
            + start
            + "</main>".len();
        &html[start..end]
    }

    fn render_skills(snapshot: super::SkillsSnapshot) -> String {
        let owner = Owner::new();
        owner.with(|| {
            leptos_meta::provide_meta_context();
            provide_theme_context();
            super::skills_page(snapshot).to_html()
        })
    }

    pub(crate) fn skills_one_list_then_one_editor() {
        let home = temp_home();
        let root = home.join("skills");
        let other = home.join("view");
        fs::create_dir_all(root.join("vc-alpha")).expect("alpha dir");
        fs::create_dir_all(root.join("vc-beta")).expect("beta dir");
        fs::create_dir_all(root.join("not-a-skill")).expect("decoy dir");
        fs::create_dir_all(root.join("plans")).expect("plans dir");
        fs::create_dir_all(root.join("vc-gamma")).expect("gamma dir");
        fs::create_dir_all(&other).expect("second root");
        fs::write(root.join("vc-alpha/SKILL.md"), "alpha body").expect("alpha skill");
        fs::write(root.join("vc-alpha/NOTES.md"), "leave this file").expect("notes");
        fs::write(root.join("vc-beta/SKILL.md"), "beta body").expect("beta skill");
        fs::write(root.join("not-a-skill/SKILL.md"), "not listed").expect("decoy skill");
        fs::write(root.join("plans/plan.md"), "plan card").expect("plan file");
        fs::write(root.join("vc-gamma/README.md"), "no skill file").expect("gamma readme");
        std::os::unix::fs::symlink(root.join("vc-alpha"), other.join("vc-alpha")).expect("symlink");

        let files = super::discover_skill_files(&[root.clone(), other.clone()]);
        let names: Vec<&str> = files.iter().map(|file| file.name.as_str()).collect();
        assert_eq!(names, ["vc-alpha", "vc-beta"]);

        let list = render_skills(super::SkillsSnapshot {
            names: files.iter().map(|file| file.name.clone()).collect(),
            open: None,
            text: String::new(),
        });
        let list_room = skills_room_html(&list);
        assert_eq!(list.matches("<h1").count(), 1);
        assert_eq!(list_room.matches("<h1").count(), 1);
        assert!(list_room.contains(">Skills<"));
        assert_eq!(list_room.matches("<ul").count(), 1);
        assert!(list_room.contains("class=\"skills-list\""));
        assert!(list_room.contains("href=\"/skills/vc-alpha\""));
        assert!(list_room.contains("href=\"/skills/vc-beta\""));
        assert!(!list_room.contains("<textarea"));
        assert!(!list.contains("plan-card"));
        assert!(!list.contains("data-ppm=\"plan\""));
        assert!(!list_room.contains("not-a-skill"));
        assert!(!list_room.contains("vc-gamma"));

        let editor = render_skills(super::SkillsSnapshot {
            names: vec!["vc-alpha".into(), "vc-beta".into()],
            open: Some("vc-alpha".into()),
            text: "alpha body".into(),
        });
        let editor_room = skills_room_html(&editor);
        assert_eq!(editor.matches("<h1").count(), 1);
        assert!(editor_room.contains(">Skills<"));
        assert_eq!(editor_room.matches("<ul").count(), 0);
        assert!(!editor_room.contains("skills-list"));
        assert_eq!(editor_room.matches("<textarea").count(), 1);
        assert!(editor_room.contains("alpha body"));
        assert!(editor_room.contains("class=\"doc-pane\""));
        assert!(editor_room.contains("action=\"/api/skills/file\""));
        assert!(editor_room.contains("name=\"name\""));
        assert!(editor_room.contains("value=\"vc-alpha\""));
        assert!(editor_room.contains("name=\"text\""));
        assert!(!editor.contains("plan-card"));
        assert!(!editor_room.contains("vc-beta"));

        let empty_root = home.join("empty-skills");
        fs::create_dir_all(&empty_root).expect("empty skills dir");
        assert!(super::discover_skill_files(&[empty_root]).is_empty());
        let empty = render_skills(super::SkillsSnapshot::empty());
        let empty_room = skills_room_html(&empty);
        assert_eq!(empty.matches("<h1").count(), 1);
        assert!(empty_room.contains(">Skills<"));
        assert!(empty_room.contains("data-skills-state=\"empty\""));
        assert_eq!(empty_room.matches("<ul").count(), 0);
        assert!(!empty_room.contains("<textarea"));
        assert!(!empty.contains("plan-card"));

        super::save_named_skill_in(&[root.clone(), other], "vc-alpha", "rewritten skill")
            .expect("save the open skill");
        assert_eq!(
            fs::read_to_string(root.join("vc-alpha/SKILL.md")).expect("reread alpha"),
            "rewritten skill"
        );
        assert_eq!(
            fs::read_to_string(root.join("vc-alpha/NOTES.md")).expect("notes stay"),
            "leave this file"
        );
        assert_eq!(
            fs::read_to_string(root.join("vc-beta/SKILL.md")).expect("beta stays"),
            "beta body"
        );
        assert!(super::save_named_skill_in(&[root.clone()], "../vc-alpha", "nope").is_err());
        assert!(super::save_named_skill_in(&[root.clone()], "vc-missing", "nope").is_err());
        assert!(super::save_named_skill_in(&[root], "not-a-skill", "nope").is_err());

        fs::remove_dir_all(home).ok();
    }

    pub(super) fn history_room_proof() {
        let mut plan = HistoryMemory::hit("plan", "should-not-render");
        plan.plan_id = "zen-rooms-ia".to_string();
        plan.summary = "scaffold plan card".to_string();
        let mut report = HistoryMemory::hit("report", "loctree-report-session");
        report.summary = "loctree-report.html".to_string();
        let session = HistoryMemory::hit("session", "remembered-session-ac121475");
        let intent = HistoryMemory::hit("intent", "intent-cut-w4-03");

        let owner = Owner::new();
        let (remembered, empty) = owner.with(|| {
            leptos_meta::provide_meta_context();
            provide_theme_context();
            (
                history_view(vec![session, intent, plan, report]).to_html(),
                HistoryPage().to_html(),
            )
        });

        assert!(remembered.contains("<h1 class=\"run-detail-title\">History &amp; context</h1>"));
        assert_eq!(
            remembered.matches("class=\"history-memory-row\"").count(),
            2
        );
        assert!(remembered.contains("data-memory=\"session\""));
        assert!(remembered.contains("data-memory=\"intent\""));
        assert!(remembered.contains("remembered-session-ac121475"));
        assert!(remembered.contains("intent-cut-w4-03"));
        assert!(remembered.contains("/api/aicx/search"));
        assert!(remembered.contains("/api/aicx/reference?path=remembered-session-ac121475"));
        assert!(remembered.contains("/api/aicx/reference?path=intent-cut-w4-03"));
        assert!(!remembered.contains("zen-rooms-ia"));
        assert!(!remembered.contains("should-not-render"));
        assert!(!remembered.contains("loctree-report"));
        assert!(!remembered.contains("plan-card"));
        assert!(!remembered.contains("data-ppm=\"plan\""));
        assert!(!remembered.contains("id=\"history-memory-empty\""));

        assert!(empty.contains("<h1 class=\"run-detail-title\">History &amp; context</h1>"));
        assert!(empty.contains("History &amp; context has no remembered sessions or intents yet."));
        assert!(empty.contains("data-history-room"));
        assert!(empty.contains("/api/aicx/search"));
        assert!(empty.contains("/api/aicx/reference"));
        assert!(!empty.contains("class=\"history-memory-row\""));
        assert!(!empty.contains("plan-card"));
        assert!(!empty.contains("zen-rooms-ia"));
        assert!(!empty.contains("loctree-report"));
    }
}

#[cfg(all(test, feature = "ssr"))]
pub(crate) fn overview_welcome_status_and_miniatures() {
    use leptos::prelude::*;

    use crate::theme::provide_theme_context;

    fn work_door(html: &str, href: &str, label: &str) -> bool {
        html.split("<a ").any(|chunk| {
            let anchor = chunk.split("</a>").next().unwrap_or("");
            anchor.contains("overview-door")
                && anchor.contains(&format!("href=\"{href}\""))
                && anchor.contains(label)
        })
    }

    assert_eq!(overview_welcome_line(""), "still starting");
    assert_eq!(overview_welcome_line("loading"), "still starting");
    assert_eq!(overview_welcome_line("starting"), "still starting");
    assert_eq!(overview_welcome_line("healthy"), "server up");
    assert_eq!(overview_welcome_line("healthy · 2 live"), "server up");

    let owner = Owner::new();
    let (welcome, starting, usage) = owner.with(|| {
        leptos_meta::provide_meta_context();
        provide_theme_context();
        let mut up = DashboardData::default();
        up.server_status = "healthy".into();
        up.active_runs = vec![DashboardRun {
            run_id: "impl-live".into(),
            state: "running".into(),
            health: "active".into(),
            ..DashboardRun::default()
        }];
        let welcome = console_dashboard(up).to_html();
        let starting = console_dashboard(DashboardData {
            server_status: "loading".into(),
            ..DashboardData::default()
        })
        .to_html();
        (welcome, starting, UsagePage().to_html())
    });

    assert_eq!(welcome.matches("id=\"overview-status\"").count(), 1);
    assert!(welcome.contains("server up"));
    assert!(!welcome.contains("still starting"));
    assert_eq!(welcome.matches("class=\"overview-door\"").count(), 4);
    assert!(work_door(&welcome, "/", "Overview"));
    assert!(work_door(&welcome, "/runs", "Runs"));
    assert!(work_door(&welcome, "/projects", "Projects"));
    assert!(work_door(&welcome, "/usage", "Costs &amp; usage"));
    assert!(!welcome.contains("id=\"usage-chart-heat\""));
    assert!(!welcome.contains("usage-chart-heat"));
    assert!(!welcome.contains("class=\"run-table\""));
    assert!(
        welcome.contains(">1<"),
        "runs miniature keeps a short count"
    );

    assert_eq!(starting.matches("id=\"overview-status\"").count(), 1);
    assert!(starting.contains("still starting"));
    assert!(!starting.contains("usage-chart-heat"));

    assert!(usage.contains("id=\"usage-chart-heat\""));
    assert!(usage.contains("Known tokens"));
    assert!(usage.contains("id=\"usage-total-tokens\""));
}

// The delivery gate filters `--exact projects_filter_deduped_shelf`.
// Nested module paths never match that literal, so the crate root re-exports the proof.
#[cfg(all(test, feature = "ssr"))]
pub(crate) use tests::projects_filter_deduped_shelf;

#[cfg(all(test, feature = "ssr"))]
pub(crate) use tests::skills_one_list_then_one_editor;
