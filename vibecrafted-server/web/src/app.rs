// Application shell + root component.

use leptos::prelude::*;
use leptos_meta::{Link, Meta, Title};
use leptos_router::components::{Route, Router, Routes};
use leptos_router::path;
use serde::{Deserialize, Serialize};

use crate::chrome::{ServerFrame, ServerSection};
use crate::run_detail::RunDetailPage;

#[cfg(feature = "ssr")]
fn theme_head_script() -> &'static str {
    r#"(() => {
  try {
    const saved = localStorage.getItem('loct-theme');
    document.documentElement.dataset.theme = saved === 'light' ? 'light' : 'dark';
  } catch (_) {
    document.documentElement.dataset.theme = 'dark';
  }
})();"#
}

#[cfg(feature = "ssr")]
fn theme_control_script() -> &'static str {
    r#"(() => {
  const button = document.querySelector('.server-theme-toggle');
  if (!button) return;
  const apply = (theme) => {
    const next = theme === 'light' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    button.textContent = next;
    button.setAttribute('aria-pressed', String(next === 'light'));
    try { localStorage.setItem('loct-theme', next); } catch (_) {}
  };
  apply(document.documentElement.dataset.theme);
  button.addEventListener('click', () => {
    apply(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
  });
})();"#
}

const DASHBOARD_EMBED_ID: &str = "vc-dashboard-data";

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub(crate) struct DashboardData {
    control_plane: String,
    generated_at: String,
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
    state: String,
    health: String,
    agent: String,
    skill: String,
    mode: String,
    root: String,
    latest_report: String,
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
            state: run.state,
            health: run.health,
            agent: run.agent,
            skill: run.skill,
            mode: run.mode,
            root: run.root,
            latest_report: run.latest_report,
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
            updated_at: run.updated_at,
        }
    }

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

    DashboardData {
        control_plane: state.control_plane,
        generated_at: state.generated_at,
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
        warnings: state.warnings,
        events: state.events.into_iter().map(event_summary).collect(),
        loctree_report,
    }
}

fn encode_dashboard_embed(data: &DashboardData) -> String {
    serde_json::to_string(data)
        .unwrap_or_else(|_| "{}".to_string())
        .replace('<', "\\u003c")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029")
}

#[cfg(any(test, not(feature = "ssr")))]
fn decode_dashboard_embed(json: &str) -> Option<DashboardData> {
    let data: DashboardData = serde_json::from_str(json).ok()?;
    if data == DashboardData::default() {
        return None;
    }
    Some(data)
}

fn dashboard_embed_script(json: String) -> impl IntoView {
    view! {
        <script id=DASHBOARD_EMBED_ID type="application/json" inner_html=json></script>
    }
}

#[cfg(feature = "ssr")]
pub(crate) async fn dashboard_api() -> axum::Json<DashboardData> {
    axum::Json(load_dashboard_data())
}

#[cfg(not(feature = "ssr"))]
std::thread_local! {
    static CLIENT_DASHBOARD: std::cell::RefCell<Option<DashboardData>> =
        const { std::cell::RefCell::new(None) };
}

#[cfg(not(feature = "ssr"))]
fn store_client_dashboard(data: DashboardData) {
    if data == DashboardData::default() {
        return;
    }
    CLIENT_DASHBOARD.with(|slot| *slot.borrow_mut() = Some(data));
}

#[cfg(not(feature = "ssr"))]
fn client_dashboard_now() -> Option<DashboardData> {
    CLIENT_DASHBOARD.with(|slot| slot.borrow().clone())
}

#[cfg(feature = "hydrate")]
fn read_embedded_dashboard() -> Option<DashboardData> {
    let document = web_sys::window()?.document()?;
    let json = document
        .get_element_by_id(DASHBOARD_EMBED_ID)?
        .text_content()
        .filter(|text| !text.trim().is_empty())?;
    decode_dashboard_embed(&json)
}

#[cfg(feature = "hydrate")]
fn hydrate_dashboard_cache() {
    if client_dashboard_now().is_some() {
        return;
    }
    if let Some(data) = read_embedded_dashboard() {
        store_client_dashboard(data);
    }
}

#[cfg(feature = "hydrate")]
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

#[cfg(feature = "hydrate")]
fn refresh_client_dashboard() {
    leptos::task::spawn_local(async {
        let _ = fetch_dashboard().await;
    });
}

#[cfg(not(feature = "ssr"))]
fn dashboard_loading() -> impl IntoView {
    view! {
        <ServerFrame active=ServerSection::Overview status="loading control plane".to_string()>
            <p class="control-empty">"Loading control plane…"</p>
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

#[cfg(feature = "hydrate")]
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

            view! {
                <article class="control-run-row">
                    <div class="control-run-primary">
                        <a class="control-run-id" href=detail_href.clone()>{run.run_id}</a>
                        <span class="control-run-root">{run.root}</span>
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
                        <a class="control-run-open" href=detail_href>"Open transcript →"</a>
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
            let next_action = if let Some(control) = run.human_controls.first() {
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

fn settlement_board(settlement: DashboardSettlement) -> impl IntoView {
    view! {
        <section
            class="operator-summary-strip"
            aria-label="Operator summary"
            data-scope=settlement.scope.clone()
            data-active=settlement.active
            data-f=settlement.f
            data-x=settlement.x
            data-n=settlement.n
            data-invalid=settlement.invalid
            data-unclassified=settlement.unclassified
            data-total-settled=settlement.total_settled
        >
            <div class="operator-summary-title">
                <span class="mono-cap">"runtime truth"</span>
                <strong>{settlement.scope.clone()}</strong>
            </div>
            <dl class="operator-summary-cells">
                <a class="operator-summary-cell" href="/runs">
                    <dt>"alive"</dt>
                    <dd>{settlement.active}</dd>
                </a>
                <a class="operator-summary-cell" href="/runs">
                    <dt>"final"</dt>
                    <dd>{settlement.f}</dd>
                </a>
                <a class="operator-summary-cell" href="/runs">
                    <dt>"failed"</dt>
                    <dd>{settlement.x}</dd>
                </a>
                <a class="operator-summary-cell" href="/runs">
                    <dt>"attention"</dt>
                    <dd>{settlement.n}</dd>
                </a>
            </dl>
            <div class="operator-summary-detail">
                <span>{format!("{} invalid", settlement.invalid)}</span>
                <span>{format!("{} unclassified", settlement.unclassified)}</span>
                <span>{format!("{} settled", settlement.total_settled)}</span>
            </div>
        </section>
    }
}

#[cfg(feature = "ssr")]
pub fn shell(_options: leptos::config::LeptosOptions) -> impl IntoView {
    use leptos_meta::MetaTags;

    const STYLE_TOKENS: &str = include_str!("../styles/tokens.css");
    const STYLE_FONTS: &str = include_str!("../styles/fonts.css");
    const STYLE_MAIN: &str = include_str!("../styles/main.css");

    view! {
        <!DOCTYPE html>
        <html lang="en">
            <head>
                <meta charset="utf-8"/>
                <meta name="viewport" content="width=device-width, initial-scale=1"/>
                <MetaTags/>
                <script inner_html=theme_head_script()></script>
                <style>{STYLE_TOKENS}</style>
                <style>{STYLE_FONTS}</style>
                <style>{STYLE_MAIN}</style>
            </head>
            <body>
                <App/>
                <script inner_html=theme_control_script()></script>
            </body>
        </html>
    }
}

#[component]
pub fn App() -> impl IntoView {
    leptos_meta::provide_meta_context();
    #[cfg(feature = "hydrate")]
    hydrate_dashboard_cache();

    view! {
        <Router>
            <Routes fallback=NotFoundPage>
                <Route path=path!("/") view=ConsolePage />
                <Route path=path!("/runs") view=RunsPage />
                <Route path=path!("/lifecycle") view=LifecyclePage />
                <Route path=path!("/activity") view=ActivityPage />
                <Route path=path!("/structure") view=StructurePage />
                <Route path=path!("/run/:run_id") view=RunDetailPage />
            </Routes>
        </Router>
    }
}

#[component]
pub fn ConsolePage() -> impl IntoView {
    view! {
        <Title text="vc-server - control plane" />
        <Meta name="description" content="Vibecrafted control-plane dashboard." />
        <Meta name="theme-color" content="#0a0a0b" />
        <Link rel="preload" as_="font" type_="font/woff2" href="/fonts/inter-var-latin.woff2" crossorigin="anonymous" />
        <Link rel="preload" as_="font" type_="font/woff2" href="/fonts/jetbrains-mono-var-latin.woff2" crossorigin="anonymous" />
        {control_dashboard(|dashboard| console_dashboard(dashboard).into_any())}
    }
}

fn console_dashboard(dashboard: DashboardData) -> impl IntoView {
    // Forgotten-gem filters: quarantine smoke/terminal/stale-marbles noise and
    // non-actionable lifecycle rows before they hit the operator hero.
    let active_runs = operator_active_runs(dashboard.active_runs);
    let action_runs = operator_action_runs(dashboard.lifecycle_runs);
    let loctree_report = dashboard.loctree_report;

    let active_count = active_runs.len();
    let stalled_count = dashboard.stalled_runs.len();
    let recent_count = dashboard.recent_runs.len();
    let warning_count = dashboard.warnings.len();
    let action_count = action_runs.len();

    let settlement = dashboard.settlement;
    let attention_count = settlement.n;
    let has_loctree_report = !loctree_report.is_empty();

    view! {
        <ServerFrame
            active=ServerSection::Overview
            status=format!("{active_count} live · {attention_count} attention")
        >
            <div class="server-console-shell">
                <section class="server-console-hero" id="now">
                    <div class="server-console-grid">
                        <div class="server-console-copy">
                            <p class="section-eyebrow">"Operator workspace"</p>
                            <h1>"Control plane"</h1>
                            <p>
                                "Live runs, lifecycle decisions, transcripts, and scaffold artifacts in one navigable operator desk."
                            </p>
                            <p class="server-console-links">
                                <a class="server-console-link server-console-link-primary" href="/runs">
                                    "Inspect live runs"
                                </a>
                                <a class="server-console-link" href="/lifecycle">
                                    "Lifecycle decisions"
                                </a>
                                <a class="server-console-link" href="/scaffold">
                                    "Open scaffold studio"
                                </a>
                                <a
                                    class="server-console-link"
                                    href="http://127.0.0.1:8033/?q=vibecrafted+server&sort=oldest"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                >
                                    "AICX context ↗"
                                </a>
                            </p>
                        </div>

                        <aside class="server-console-panel" aria-label="Console status preview">
                            <div class="server-console-panel-head">
                                <span class="mono-cap">"Right now"</span>
                                <span class="server-console-panel-state">"live projection"</span>
                            </div>
                            <dl class="operator-now-cells">
                                <a class="operator-summary-cell" href="/runs">
                                    <dt>"alive"</dt>
                                    <dd>{active_count}</dd>
                                </a>
                                <a class="operator-summary-cell" href="/lifecycle">
                                    <dt>"next"</dt>
                                    <dd>{action_count}</dd>
                                </a>
                                <a class="operator-summary-cell" href="/activity">
                                    <dt>"warnings"</dt>
                                    <dd>{warning_count}</dd>
                                </a>
                                <a class="operator-summary-cell" href="/runs">
                                    <dt>"stalled"</dt>
                                    <dd>{stalled_count}</dd>
                                </a>
                                <a class="operator-summary-cell" href="/runs">
                                    <dt>"recent"</dt>
                                    <dd>{recent_count}</dd>
                                </a>
                            </dl>
                        </aside>
                    </div>
                </section>

                <div class="settlement-board-wrap">
                    {settlement_board(settlement)}
                </div>

                <section class="control-panel control-panel-wide overview-structure" aria-label="Structure">
                    <div class="control-panel-head">
                        <h2>"Structure"</h2>
                        <span>"Loctree + scaffold"</span>
                    </div>
                    <div class="structure-links">
                        <a class="server-console-link" href="/structure">"Inspect structure"</a>
                        <a class="server-console-link" href="/scaffold">"Open scaffold review"</a>
                    </div>
                    <p class="control-empty" hidden=has_loctree_report>
                        "No Loctree report is known for the roots in the canonical state view."
                    </p>
                </section>

                <footer class="server-console-footer">
                    <span>"Vibecrafted · Vetcoders · LibraxisAI"</span>
                    <span>"control-plane truth · operator-safe reads"</span>
                </footer>
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
    let generated_at = dashboard.generated_at;
    let active = operator_active_runs(dashboard.active_runs);
    let stalled = dashboard.stalled_runs;
    let recent = dashboard.recent_runs;
    let active_count = active.len();
    let stalled_count = stalled.len();
    let recent_count = recent.len();

    view! {
        <ServerFrame active=ServerSection::Runs status=format!("{active_count} live")>
            <div class="server-console-shell route-page-shell">
                {route_header("Runtime", "Live runs", "Choose a current agent to open its bounded transcript.human.log tail and full control-plane detail.")}
                <p class="control-plane-meta"><span>{control_plane}</span><span>{generated_at}</span></p>
                <section class="control-panel control-panel-wide" aria-label="Active runs">
                    <div class="control-panel-head"><h2>"Current agents"</h2><span>{active_count}</span></div>
                    <p class="control-empty" hidden={active_count != 0}>"No live agents right now."</p>
                    <div class="control-run-list">{run_cards(active)}</div>
                </section>
                <section class="control-panel control-panel-wide" aria-label="Stalled runs">
                    <div class="control-panel-head"><h2>"Stalled"</h2><span>{stalled_count}</span></div>
                    <p class="control-empty" hidden={stalled_count != 0}>"No stalled runs."</p>
                    <div class="control-run-list">{run_cards(stalled)}</div>
                </section>
                <section class="control-panel control-panel-wide" aria-label="Recent state view">
                    <div class="control-panel-head"><h2>"Recent truth"</h2><span>{recent_count}</span></div>
                    <p class="control-empty" hidden={recent_count != 0}>"No recent settled runs."</p>
                    <div class="control-run-list">{run_cards(recent)}</div>
                </section>
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
                {route_header("Control plane", "Lifecycle", "Open a baton to inspect its current stage, next agent, controls, and delivery state.")}
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
    view! {
        <ServerFrame active=ServerSection::Structure status="structural evidence".to_string()>
            <div class="server-console-shell route-page-shell">
                {route_header("Repository", "Structure", "Structural evidence is shown as runtime truth. Local filesystem paths are never emitted as broken browser links.")}
                <section class="control-panel control-panel-wide" aria-label="Structural evidence">
                    <div class="control-panel-head"><h2>"Latest Loctree report"</h2><span>{if has_report { "available" } else { "not found" }}</span></div>
                    <p class="run-detail-artifact-path" hidden={!has_report}>{report}</p>
                    <p class="control-empty" hidden=has_report>"No Loctree report is known for the roots in the canonical state view."</p>
                    <p class="server-console-links"><a class="server-console-link server-console-link-primary" href="/scaffold">"Open scaffold studio"</a></p>
                </section>
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
    use std::sync::Mutex;
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
        ActivityPage, ConsolePage, DashboardData, DashboardRun, LifecyclePage, RunsPage,
        StructurePage, console_dashboard, decode_dashboard_embed, encode_dashboard_embed,
        load_dashboard_data_from, operator_active_runs, run_cards,
    };
    use crate::control::api::{control_routes, state_payload};
    use crate::theme::provide_theme_context;

    static DASHBOARD_ENV_LOCK: Mutex<()> = Mutex::new(());

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

    #[test]
    fn state_json_and_ssr_render_the_same_canonical_settlement_board() {
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
        let html = owner.with(|| {
            provide_theme_context();
            console_dashboard(dashboard).to_html()
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
                html.contains(&format!("data-{attribute}=\"{expected}\"")),
                "SSR {key} must equal API value {expected}: {html}"
            );
        }
        let scope = board["scope"].as_str().expect("scope string");
        assert!(html.contains(&format!("data-scope=\"{scope}\"")));
        assert!(html.contains("final"));
        assert!(html.contains("failed"));
        assert!(html.contains("attention"));
        assert!(html.contains("aria-label=\"Toggle color theme\""));
        assert!(html.contains("http://127.0.0.1:8033/"));
        assert!(html.contains("Vibecrafted server navigation"));
        assert!(html.contains("server-sidebar"));
        assert!(html.contains("href=\"/runs\""));
        assert!(html.contains("href=\"/lifecycle\""));
        assert!(html.contains("href=\"/activity\""));
        assert!(html.contains("href=\"/structure\""));
        assert!(html.contains("href=\"/scaffold\""));
        assert!(!html.contains("href=\"#fleet\""));
        let board_position = html
            .find("aria-label=\"Operator summary\"")
            .expect("summary");
        let structure_position = html.find("aria-label=\"Structure\"").expect("structure");
        assert!(board_position < structure_position);
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

    #[test]
    fn navigation_uses_dedicated_views_and_run_cards_open_human_transcripts() {
        let owner = Owner::new();
        let (runs, lifecycle, activity, structure, card) = owner.with(|| {
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
                RunsPage().to_html(),
                LifecyclePage().to_html(),
                ActivityPage().to_html(),
                StructurePage().to_html(),
                card,
            )
        });

        assert!(runs.contains("Live runs"));
        assert!(runs.contains("Current agents"));
        assert!(runs.contains("transcript.human.log"));
        assert!(lifecycle.contains("Action plan"));
        assert!(activity.contains("Runtime context"));
        assert!(activity.contains("Warnings"));
        assert!(structure.contains("Latest Loctree report"));
        assert!(!structure.contains("href=\"/Volumes/"));
        assert!(card.contains("href=\"/run/impl-live-agent\""));
        assert!(card.contains("Open transcript"));
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
        assert!(html.contains("Control plane"));
    }

    #[tokio::test]
    async fn dashboard_http_route_returns_ssr_payload_not_default() {
        let _guard = DASHBOARD_ENV_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let home = temp_home();
        let runs_dir = home.join("control_plane/runs");
        fs::create_dir_all(&runs_dir).expect("runs dir");
        write_snapshot(&runs_dir, "finalized", "finalized", "f");
        write_snapshot(&runs_dir, "failed", "failed", "x");
        write_snapshot(&runs_dir, "attention", "needs_attention", "n");
        // Safety: process-global env is serialised by DASHBOARD_ENV_LOCK.
        unsafe {
            std::env::set_var("VIBECRAFTED_HOME", &home);
        }

        let opts = LeptosOptions::builder()
            .output_name("vibecrafted-server-web-test")
            .site_root("target/site-test")
            .site_pkg_dir("pkg")
            .env(Env::PROD)
            .site_addr("127.0.0.1:0".parse::<SocketAddr>().expect("addr"))
            .reload_port(0)
            .build();
        let response = control_routes()
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

        unsafe {
            std::env::remove_var("VIBECRAFTED_HOME");
        }
        fs::remove_dir_all(home).ok();
    }
}
