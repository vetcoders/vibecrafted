// Application shell + root component.

use leptos::prelude::*;
use leptos_meta::{Link, Meta, Title};
use leptos_router::components::{Route, Router, Routes};
use leptos_router::path;
use serde::{Deserialize, Serialize};

use crate::chrome::{ServerFrame, ServerSection};
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

    fn run_summary(run: RunStatus) -> DashboardRun {
        let settlement_tui = run
            .settlement_tui
            .map(|cell| match cell {
                control_core::SettlementTui::F => "f",
                control_core::SettlementTui::X => "x",
                control_core::SettlementTui::N => "n",
            })
            .unwrap_or("")
            .to_string();
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

    fn lifecycle_summary(run: LifecycleRunSummary) -> DashboardLifecycleRun {
        let dou_label = match (run.dou_readiness.as_str(), run.dou_index) {
            ("zero", Some(0)) => "ZERO DoU".to_string(),
            ("open", Some(value)) => format!("DoU {value}"),
            _ => "DoU unknown".to_string(),
        };
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
        active_runs: state.active_runs.into_iter().map(run_summary).collect(),
        stalled_runs: state.stalled_runs.into_iter().map(run_summary).collect(),
        recent_runs: state.recent_runs.into_iter().map(run_summary).collect(),
        lifecycle_runs: lifecycle_runs.into_iter().map(lifecycle_summary).collect(),
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

fn run_table(
    title: &'static str,
    aria: &'static str,
    band: &'static str,
    runs: Vec<DashboardRun>,
) -> impl IntoView {
    let count = runs.len();
    let empty = count == 0;
    view! {
        <section class="overview-band run-table-band" aria-label=aria data-rail-band=band hidden=empty>
            <header class="run-table-head">
                <h2>{title}</h2>
                <span>{count}</span>
            </header>
            <div class="run-table-wrap">
                <table class="run-table">
                    <thead>
                        <tr>
                            <th>"Run"</th>
                            <th>"Agent"</th>
                            <th>"Skill"</th>
                            <th>"Age"</th>
                            <th>"Heartbeat"</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr class="run-table-empty" hidden={!empty}>
                            <td colspan="5">"None."</td>
                        </tr>
                        {runs.into_iter().map(|run| {
                            let detail_href = format!("/run/{}", run.run_id);
                            let transcript_url = format!("/api/control/runs/{}/transcript", run.run_id);
                            let live = run.health == "active";
                            let beat = if live { "live" } else { "none" };
                            let beat_class = if live { "run-beat is-live" } else { "run-beat" };
                            let meta = format!("{} · {} · {}", run.agent, run.skill, run.updated_at);
                            view! {
                                <tr
                                    data-ppm="run"
                                    data-run-id=run.run_id.clone()
                                    data-href=detail_href.clone()
                                    data-focus-root=run.root.clone()
                                    data-transcript-url=transcript_url
                                    data-report=run.latest_report.clone()
                                    data-error=run.last_error.clone()
                                    data-meta=meta
                                >
                                    <td>
                                        <a class="control-run-id" href=detail_href.clone() data-copy=run.run_id.clone()>{run.run_id.clone()}</a>
                                    </td>
                                    <td>{run.agent}</td>
                                    <td>{run.skill}</td>
                                    <td>{run.updated_at}</td>
                                    <td>
                                        <span class=beat_class>
                                            <i aria-hidden="true"></i>
                                            {beat}
                                        </span>
                                    </td>
                                </tr>
                            }
                        }).collect_view()}
                    </tbody>
                </table>
            </div>
        </section>
    }
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
                "Every run snapshot this host still keeps, from all days — not what is running now. Current agents are listed above."
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
                <Route path=path!("/aicx") view=AicxPage />
                <Route path=path!("/frame") view=FramePage />
                <Route path=path!("/guide") view=GuidePage />
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
                    <p class="usage-footnote"><span id="usage-schema">"vibecrafted.usage-report.v1"</span><span id="usage-generated"></span><span>"Unknowns stay visible. Currencies are never combined. This projection is not a bill."</span></p>
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
  const tokensKnownText = (totals) => {
    const runs = Number(totals && totals.runs) || 0;
    const unknown = Number(totals && totals.runs_tokens_unknown) || 0;
    if (!runs) return '—';
    if (unknown === runs) return 'missing';
    return number.format(totals.tokens_total_known);
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
      const tokens = document.createElement('span'); tokens.textContent = tokensKnownText(item);
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
    set('usage-total-tokens', tokensKnownText(totals));
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
    for (let at = min; at <= max; at += width) buckets.push({ t: at, value: 0, measured: false });
    const index = new Map(buckets.map((bucket, i) => [bucket.t, i]));
    for (const point of points) {
      const slot = index.get(bucketStart(point.ms, grain));
      if (slot != null) {
        buckets[slot].value += point.value;
        buckets[slot].measured = true;
      }
    }
    return { grain, buckets };
  };
  const paintChart = (plotId, titleId, caption, built, color) => {
    const host = byId(plotId);
    const title = byId(titleId);
    if (title) title.textContent = caption;
    if (!host) return;
    host.replaceChildren();
    const measured = built ? built.buckets.filter((bucket) => bucket.measured) : [];
    if (!built || !measured.length) {
      const note = document.createElement('p');
      note.className = 'usage-chart-empty';
      note.textContent = 'No measured points in this window.';
      host.append(note);
      return;
    }
    const w = 640;
    const h = 148;
    const pad = 10;
    const peak = Math.max(...measured.map((bucket) => bucket.value));
    const scale = peak > 0 ? peak : 1;
    const n = built.buckets.length;
    const xAt = (i) => n === 1 ? w / 2 : pad + (i / (n - 1)) * (w - pad * 2);
    const yAt = (value) => h - pad - (value / scale) * (h - pad * 2);
    const coords = [];
    let firstMeasured = 0;
    let lastMeasured = 0;
    built.buckets.forEach((bucket, i) => {
      if (!bucket.measured) return;
      if (!coords.length) firstMeasured = i;
      lastMeasured = i;
      coords.push(xAt(i).toFixed(1) + ',' + yAt(bucket.value).toFixed(1));
    });
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
    svg.setAttribute('role', 'img');
    const area = document.createElementNS(svg.namespaceURI, 'polygon');
    area.setAttribute('points', xAt(firstMeasured).toFixed(1) + ',' + (h - pad) + ' ' + coords.join(' ') + ' ' + xAt(lastMeasured).toFixed(1) + ',' + (h - pad));
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
    set('usage-hero-tokens', tokensKnownText(totals));
    const runs = Number(totals.runs) || 0;
    const tokenUnknown = Number(totals.runs_tokens_unknown) || 0;
    let caption = windowName + ' · ' + number.format(tokenUnknown) + ' token totals missing';
    if (!runs) caption = windowName + ' · no runs';
    else if (tokenUnknown === runs) caption = number.format(tokenUnknown) + ' runs · token totals not recorded';
    set('usage-hero-caption', caption);
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
    let active_runs = operator_active_runs(dashboard.active_runs);
    let stalled_runs = dashboard.stalled_runs;
    let recent_runs = dashboard.recent_runs;
    let generated_at = dashboard.generated_at.clone();
    let loctree_report = dashboard.loctree_report;

    let active_count = active_runs.len();
    let stalled_count = stalled_runs.len();
    let recent_count = recent_runs.len();
    let warning_count = dashboard.warnings.len();
    let action_count = operator_action_runs(dashboard.lifecycle_runs).len();
    // Workspaces with a running vc-frame session, not the durable catalog:
    // the catalog keeps every identity ever registered (worker worktrees and
    // test roots included) and lives on /workspaces as history.
    let live_workspace_count = dashboard.live_frame_sessions.len();
    let workspace_status = dashboard.workspace_status;
    let server_status = dashboard.server_status;
    let selected_root = dashboard
        .workspaces
        .iter()
        .find(|workspace| workspace.selected)
        .map(|workspace| workspace.root.clone())
        .unwrap_or_default();
    let has_loctree_report = !loctree_report.is_empty();
    let loctree_note = if has_loctree_report {
        "Loctree report available"
    } else {
        "Loctree empty"
    };
    view! {
        <ServerFrame
            active=ServerSection::Overview
            status=format!("{server_status} · {active_count} live")
        >
            <div class="server-console-shell overview-desk">
                <div
                    id="vc-focus-context"
                    data-selected-workspace-root=selected_root
                    hidden
                ></div>
                <header class="overview-head">
                    <p class="overview-context">{format!("{server_status} · {workspace_status}")}</p>
                    <dl class="overview-head-stats">
                        <div><dt>"live"</dt><dd>{active_count}</dd></div>
                        <div><dt>"failures"</dt><dd>{stalled_count}</dd></div>
                        <div><dt>"next"</dt><dd>{action_count}</dd></div>
                        <div><dt>"warnings"</dt><dd>{warning_count}</dd></div>
                        <div><dt>"recent"</dt><dd>{recent_count}</dd></div>
                        <div data-live-workspaces=live_workspace_count><dt>"workspaces"</dt><dd>{live_workspace_count}</dd></div>
                    </dl>
                    <p class="overview-generated">{format!("Generated {generated_at}")}</p>
                </header>

                <div class="overview-desk-body">
                    <div class="overview-desk-main">
                        {run_table("Active dispatches", "Active dispatches", "active", active_runs)}
                        {run_table("Failures", "Failures", "failures", stalled_runs)}
                        {run_table("Recent", "Recent", "recent", recent_runs)}
                        <p class="overview-structure-line" aria-label="Structure">
                            <a href="/structure">"Structure"</a>
                            " · "
                            {loctree_note}
                            " · "
                            <a href="/scaffold">"Plans"</a>
                        </p>
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

#[component]
pub fn RunsPage() -> impl IntoView {
    view! {
        <Title text="live runs - vc-server" />
        <Meta name="description" content="Current agents and their human transcript tails." />
        {control_dashboard(|dashboard| runs_dashboard(dashboard).into_any())}
    }
}

fn runs_dashboard(dashboard: DashboardData) -> impl IntoView {
    let control_plane = dashboard.control_plane;
    let control_status = dashboard.control_status;
    let control_error = dashboard.control_error;
    let generated_at = dashboard.generated_at;
    let active = operator_active_runs(dashboard.active_runs);
    let stalled = dashboard.stalled_runs;
    let recent = dashboard.recent_runs;
    let settlement = dashboard.settlement;
    let active_count = active.len();
    let stalled_count = stalled.len();
    let recent_count = recent.len();
    let not_initialized = control_status == "not_initialized";
    let unavailable = control_status == "unavailable";
    let available = control_status == "available";

    view! {
        <ServerFrame active=ServerSection::Runs status=format!("{active_count} live")>
            <div class="server-console-shell route-page-shell">
                {route_header("Runtime", "Live runs", "Choose a current agent to open its bounded transcript.human.log tail and full control-plane detail.")}
                <p class="control-plane-meta"><span>{control_plane}</span><span>{generated_at}</span><span>{control_status.clone()}</span></p>
                {not_initialized.then(|| view! {
                    <p class="control-empty">"The server is healthy, but the control plane is not initialized yet."</p>
                })}
                {unavailable.then(|| view! {
                    <p class="control-empty control-error">{format!("Control-plane data is unavailable: {control_error}")}</p>
                })}
                <section class="control-panel control-panel-wide" aria-label="Active runs" data-source-status=control_status>
                    <div class="control-panel-head"><h2>"Current agents"</h2><span>{active_count}</span></div>
                    {(available && active_count == 0).then(|| view! { <p class="control-empty">"No live agents right now."</p> })}
                    <div class="control-run-list">{run_cards(active)}</div>
                </section>
                <section class="control-panel control-panel-wide" aria-label="Stalled runs">
                    <div class="control-panel-head"><h2>"Stalled"</h2><span>{stalled_count}</span></div>
                    {(available && stalled_count == 0).then(|| view! { <p class="control-empty">"No stalled runs."</p> })}
                    <div class="control-run-list">{run_cards(stalled)}</div>
                </section>
                <section class="control-panel control-panel-wide" aria-label="Recent state view">
                    <div class="control-panel-head"><h2>"Recent"</h2><span>{recent_count}</span></div>
                    {(available && recent_count == 0).then(|| view! { <p class="control-empty">"No recent settled runs."</p> })}
                    <div class="control-run-list">{run_cards(recent)}</div>
                </section>
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

#[component]
pub fn StructurePage() -> impl IntoView {
    view! {
        <Title text="structure - vc-server" />
        <Meta name="description" content="Current structural evidence and scaffold entry points." />
        {control_dashboard(|dashboard| structure_dashboard(dashboard).into_any())}
    }
}

fn structure_dashboard(dashboard: DashboardData) -> impl IntoView {
    let report = dashboard.loctree_report;
    let has_report = !report.is_empty();
    let selected_root = dashboard
        .workspaces
        .iter()
        .find(|workspace| workspace.selected)
        .map(|workspace| workspace.root.clone())
        .unwrap_or_default();
    view! {
        <ServerFrame active=ServerSection::Structure status="structural evidence".to_string()>
            <div class="server-console-shell route-page-shell">
                <div
                    id="vc-focus-context"
                    data-selected-workspace-root=selected_root
                    hidden
                ></div>
                {route_header("Repository", "Structure", "Loctree report for the selected workspace. Generate it here; tabs never start this process. Local filesystem paths are never emitted as broken browser links.")}
                <section class="control-panel control-panel-wide" aria-label="Structural evidence">
                    <div class="control-panel-head"><h2>"Latest Loctree report"</h2><span>{if has_report { "available" } else { "not found" }}</span></div>
                    <p class="run-detail-artifact-path" hidden={!has_report}>{report}</p>
                    <p class="server-console-links" hidden={!has_report}>
                        <a class="server-console-link server-console-link-primary" href="/structure/report" target="_blank" rel="noopener noreferrer">"Open Loctree report ↗"</a>
                    </p>
                    <p class="control-empty" hidden=has_report>"No Loctree report is known for the roots in the canonical state view."</p>
                    <p class="server-console-links">
                        <button id="loctree-generate" class="server-console-link server-console-link-primary" type="button">"Generate Loctree report"</button>
                    </p>
                    <p id="loctree-generate-status" class="control-empty"></p>
                    <p class="control-plane-meta" hidden={!has_report}>"The report opens sandboxed: its scripts run, but it holds no control-plane authority."</p>
                    {aicx_search_panel()}
                    <script inner_html=loctree_generate_script()></script>
                </section>
            </div>
        </ServerFrame>
    }
}

fn loctree_generate_script() -> &'static str {
    r#"(() => {
  const btn = document.getElementById('loctree-generate');
  const status = document.getElementById('loctree-generate-status');
  if (!btn || !status) return;
  btn.addEventListener('click', async () => {
    let root = '';
    try { root = localStorage.getItem('vc-focus-root') || ''; } catch (_) {}
    const ctx = document.getElementById('vc-focus-context');
    const live = ctx && ctx.getAttribute('data-selected-workspace-root');
    if (live) root = live;
    status.textContent = 'Generating…';
    btn.disabled = true;
    try {
      const response = await fetch('/api/structure/report', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(root ? { root } : {}),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || ('HTTP ' + response.status));
      if (payload.href) {
        location.href = payload.href;
        return;
      }
      location.reload();
    } catch (error) {
      status.textContent = 'Generate failed: ' + error.message;
      btn.disabled = false;
    }
  });
})();"#
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
mod tests {
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
        DashboardSessionRun, FramePage, LifecyclePage, RunsPage, SessionsPage, StructurePage,
        TranscriptsPage, UsagePage, WorkspacesPage, aicx_page_script, console_dashboard,
        decode_dashboard_embed, encode_dashboard_embed, git_repo_name, load_dashboard_data_from,
        operator_active_runs, run_cards, runs_dashboard, session_cards, unique_runtime_labels,
        workspaces_dashboard,
    };
    use crate::control::api::{control_routes_for, state_payload};
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
        assert!(html.contains("id=\"overview-inspector\""));
        assert!(html.contains("class=\"run-table\""));
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
        assert!(html.contains("aria-label=\"Structure\""));
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
        assert!(runs.contains("Live runs"));
        assert!(runs.contains("Current agents"));
        assert!(runs.contains("transcript.human.log"));
        assert!(lifecycle.contains("Action plan"));
        assert!(activity.contains("Runtime context"));
        assert!(activity.contains("Warnings"));
        assert!(structure.contains("Latest Loctree report"));
        assert!(structure.contains("id=\"loctree-generate\""));
        assert!(structure.contains("/api/structure/report"));
        assert!(structure.contains("id=\"aicx-search-form\""));
        assert!(structure.contains("/api/aicx/search"));
        assert!(!structure.contains("href=\"/Volumes/"));
        assert!(card.contains("href=\"/run/impl-live-agent\""));
        assert!(!card.contains("Open transcript"));
        assert!(card.contains("data-ppm=\"run\""));
        assert!(card.contains("control-copy"));
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
        assert!(usage.contains("token totals not recorded"));
        assert!(usage.contains("return 'missing'"));
        assert!(!usage.contains("tokens_total_known || 0"));
        assert!(usage.contains("This projection is not a bill."));
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

        let fleet = operator_active_runs(
            (0..9)
                .map(|index| run(&format!("live-{index}"), "launching", "active", "implement"))
                .collect(),
        );
        assert_eq!(fleet.len(), 9);
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
}
