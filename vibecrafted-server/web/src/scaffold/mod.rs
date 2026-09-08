#[cfg(feature = "ssr")]
pub mod api {
    use std::collections::BTreeSet;

    use axum::extract::{Form, Query};
    use axum::http::{StatusCode, header};
    use axum::response::{Html, IntoResponse, Response};
    use axum::routing::{get, post};
    use axum::{Json, Router};
    use control_core::{
        ScaffoldArtifact, ScaffoldArtifactPatch, ScaffoldArtifactStore, ScaffoldCheckpointPatch,
        ScaffoldDoctorReport, ScaffoldError, ScaffoldPlanSummary, ScaffoldStatusPatch,
        ScaffoldWorkspace, vibecrafted_home,
    };
    use serde::Deserialize;

    #[derive(Debug, Clone, Deserialize)]
    pub struct ScaffoldQuery {
        org: Option<String>,
        repo: Option<String>,
        day: Option<String>,
        plan_id: Option<String>,
        repo_root: Option<String>,
    }

    #[derive(Debug, Clone, Deserialize)]
    pub struct SaveArtifactForm {
        org: String,
        repo: String,
        day: String,
        plan_id: String,
        artifact_id: String,
        content: String,
        expected_hash: String,
    }

    #[derive(Debug, Clone, Deserialize)]
    pub struct CheckpointForm {
        org: String,
        repo: String,
        day: String,
        plan_id: String,
        artifact_id: String,
        #[serde(default)]
        approved: Option<String>,
        #[serde(default)]
        note: String,
    }

    #[derive(Debug, Clone, Deserialize)]
    pub struct SaveStatusForm {
        pub org: String,
        pub repo: String,
        pub day: String,
        pub plan_id: String,
        pub artifact_id: String,
        #[serde(default)]
        pub item_id: Option<String>,
        #[serde(default)]
        pub item_index: Option<usize>,
        pub status: String,
        #[serde(default)]
        pub note: Option<String>,
    }

    #[derive(Debug, Clone)]
    struct ScaffoldPlanCard {
        plan: ScaffoldPlanSummary,
        reviewable: bool,
    }

    pub fn scaffold_routes() -> Router<leptos::config::LeptosOptions> {
        Router::<leptos::config::LeptosOptions>::new()
            .route("/scaffold", get(editor))
            .route("/scaffold/", get(editor))
            .route("/scaffold/editor", get(editor))
            .route("/scaffold/library", get(library))
            .route("/api/scaffold/plans", get(plans))
            .route("/api/scaffold/artifacts", get(artifacts))
            .route("/api/scaffold/export", get(export))
            .route("/api/scaffold/changes", get(changes))
            .route("/api/scaffold/artifact", post(save_artifact))
            .route("/api/scaffold/checkpoint", post(save_checkpoint))
            .route("/api/scaffold/status", post(save_status))
    }

    async fn editor(Query(mut query): Query<ScaffoldQuery>) -> impl IntoResponse {
        let store = ScaffoldArtifactStore::new(vibecrafted_home());
        if query.org.is_none()
            && query.repo.is_none()
            && query.day.is_none()
            && query.plan_id.is_none()
            && let Some(plan) = store.catalog().into_iter().find(|plan| {
                store.is_plan_reviewable(&plan.org, &plan.repo, &plan.day, &plan.plan_id)
            })
        {
            query.org = Some(plan.org);
            query.repo = Some(plan.repo);
            query.day = Some(plan.day);
            query.plan_id = Some(plan.plan_id);
        }
        match load_workspace(query.clone()) {
            Ok(workspace) => Html(render_editor(&workspace)).into_response(),
            Err(ScaffoldError::SelectionRequired { plan_ids }) => {
                let detailed = store.catalog_detailed();
                let plans = matching_plans(&store, &query, &plan_ids);
                // Keep skipped list only when not filtering to a single plan_id.
                let skips = if query.plan_id.is_some() {
                    Vec::new()
                } else {
                    detailed.skipped
                };
                Html(render_plan_picker(&plan_card_views(&store, plans), &skips)).into_response()
            }
            Err(error) => {
                if let Some(plan) = selected_plan(&store, &query) {
                    let report = store
                        .doctor(&plan.org, &plan.repo, &plan.day, &plan.plan_id)
                        .ok();
                    Html(render_plan_blocked(
                        &plan,
                        report.as_ref(),
                        &error.to_string(),
                    ))
                    .into_response()
                } else {
                    (
                        StatusCode::NOT_FOUND,
                        Html(render_empty(&format!(
                            "Scaffold artifacts unavailable: {error}"
                        ))),
                    )
                        .into_response()
                }
            }
        }
    }

    async fn library() -> impl IntoResponse {
        let store = ScaffoldArtifactStore::new(vibecrafted_home());
        let detailed = store.catalog_detailed();
        Html(render_plan_picker(
            &plan_card_views(&store, detailed.plans),
            &detailed.skipped,
        ))
    }

    async fn plans(Query(query): Query<ScaffoldQuery>) -> impl IntoResponse {
        let store = ScaffoldArtifactStore::new(vibecrafted_home());
        let Some((org, repo, day)) = explicit_day(&query) else {
            let detailed = store.catalog_detailed();
            return Json(serde_json::json!({
                "plans": detailed.plans,
                "skipped": detailed.skipped,
            }))
            .into_response();
        };
        match store.plans(org, repo, day) {
            Ok(plans) => Json(serde_json::json!({"plans": plans})).into_response(),
            Err(error) => scaffold_error_response(error),
        }
    }

    async fn artifacts(Query(query): Query<ScaffoldQuery>) -> impl IntoResponse {
        match load_workspace(query) {
            Ok(workspace) => Json(workspace).into_response(),
            Err(error) => scaffold_error_response(error),
        }
    }

    async fn export(Query(query): Query<ScaffoldQuery>) -> impl IntoResponse {
        let Some((org, repo, day)) = explicit_day(&query) else {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": "org, repo, day, and plan_id are required for portable export"
                })),
            )
                .into_response();
        };
        let Some(plan_id) = query.plan_id.as_deref() else {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": "plan_id is required for portable export"
                })),
            )
                .into_response();
        };
        let store = ScaffoldArtifactStore::new(vibecrafted_home());
        match store.export_bundle(org, repo, day, plan_id, query.repo_root.as_deref()) {
            Ok(bundle) => Json(bundle).into_response(),
            Err(error) => scaffold_error_response(error),
        }
    }

    async fn changes(Query(query): Query<ScaffoldQuery>) -> impl IntoResponse {
        let store = ScaffoldArtifactStore::new(vibecrafted_home());
        let workspace = match load_workspace(query) {
            Ok(workspace) => workspace,
            Err(error) => return scaffold_error_response(error),
        };
        match store.changes(
            &workspace.org,
            &workspace.repo,
            &workspace.day,
            &workspace.plan_id,
        ) {
            Ok(changes) => Json(changes).into_response(),
            Err(error) => (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({ "error": error.to_string() })),
            )
                .into_response(),
        }
    }

    async fn save_artifact(Form(form): Form<SaveArtifactForm>) -> impl IntoResponse {
        let store = ScaffoldArtifactStore::new(vibecrafted_home());
        let result = store.write_artifact(
            &form.org,
            &form.repo,
            &form.day,
            &form.plan_id,
            ScaffoldArtifactPatch {
                artifact_id: form.artifact_id,
                content: form.content,
                expected_hash: form.expected_hash,
            },
        );
        redirect_after_mutation(
            &form.org,
            &form.repo,
            &form.day,
            &form.plan_id,
            result.err(),
        )
    }

    async fn save_checkpoint(Form(form): Form<CheckpointForm>) -> impl IntoResponse {
        let store = ScaffoldArtifactStore::new(vibecrafted_home());
        let result = store.checkpoint(
            &form.org,
            &form.repo,
            &form.day,
            &form.plan_id,
            ScaffoldCheckpointPatch {
                artifact_id: form.artifact_id,
                approved: form.approved.is_some(),
                note: form.note,
            },
        );
        redirect_after_mutation(
            &form.org,
            &form.repo,
            &form.day,
            &form.plan_id,
            result.err(),
        )
    }

    async fn save_status(headers: axum::http::HeaderMap, body: String) -> impl IntoResponse {
        let is_json = headers
            .get(header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .map(|ct| ct.contains("application/json"))
            .unwrap_or(false);

        let form: SaveStatusForm = if is_json {
            match serde_json::from_str(&body) {
                Ok(form) => form,
                Err(error) => {
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(serde_json::json!({ "error": error.to_string() })),
                    )
                        .into_response();
                }
            }
        } else {
            match serde_urlencoded::from_str(&body) {
                Ok(form) => form,
                Err(error) => {
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(serde_json::json!({ "error": error.to_string() })),
                    )
                        .into_response();
                }
            }
        };

        let store = ScaffoldArtifactStore::new(vibecrafted_home());
        let result = store.write_status(
            &form.org,
            &form.repo,
            &form.day,
            &form.plan_id,
            ScaffoldStatusPatch {
                artifact_id: form.artifact_id,
                item_id: form.item_id,
                item_index: form.item_index,
                status: form.status,
                note: form.note,
            },
        );

        if is_json {
            match result {
                Ok(refreshed) => Json(serde_json::json!({
                    "status": "ok",
                    "artifact": refreshed
                }))
                .into_response(),
                Err(error) => scaffold_error_response(error),
            }
        } else {
            redirect_after_mutation(
                &form.org,
                &form.repo,
                &form.day,
                &form.plan_id,
                result.err(),
            )
        }
    }

    fn redirect_after_mutation(
        org: &str,
        repo: &str,
        day: &str,
        plan_id: &str,
        error: Option<ScaffoldError>,
    ) -> Response {
        let status = if let Some(error) = error {
            eprintln!("scaffold mutation failed: {error}");
            "status-error"
        } else {
            "status-saved"
        };
        let location = format!(
            "/scaffold?org={}&repo={}&day={}&plan_id={}#{status}",
            url_component(org),
            url_component(repo),
            url_component(day),
            url_component(plan_id),
        );
        (StatusCode::SEE_OTHER, [(header::LOCATION, location)]).into_response()
    }

    fn load_workspace(query: ScaffoldQuery) -> Result<ScaffoldWorkspace, ScaffoldError> {
        let store = ScaffoldArtifactStore::new(vibecrafted_home());
        if query.org.is_none() && query.repo.is_none() && query.day.is_none() {
            return store.latest_workspace();
        }
        let plan_id = query.plan_id.clone();
        let (org, repo, day) = resolve_query(&query, &store);
        store.workspace(&org, &repo, &day, plan_id.as_deref())
    }

    fn resolve_query(
        query: &ScaffoldQuery,
        store: &ScaffoldArtifactStore,
    ) -> (String, String, String) {
        let fallback = store.latest_workspace().ok();
        let org = query
            .org
            .clone()
            .or_else(|| fallback.as_ref().map(|workspace| workspace.org.clone()))
            .unwrap_or_else(|| "Vetcoders".to_string());
        let repo = query
            .repo
            .clone()
            .or_else(|| fallback.as_ref().map(|workspace| workspace.repo.clone()))
            .unwrap_or_else(|| "vibecrafted".to_string());
        let day = query
            .day
            .clone()
            .or_else(|| fallback.as_ref().map(|workspace| workspace.day.clone()))
            .unwrap_or_else(|| "2026_0606".to_string());
        (org, repo, day)
    }

    fn explicit_day(query: &ScaffoldQuery) -> Option<(&str, &str, &str)> {
        Some((
            query.org.as_deref()?,
            query.repo.as_deref()?,
            query.day.as_deref()?,
        ))
    }

    fn matching_plans(
        store: &ScaffoldArtifactStore,
        query: &ScaffoldQuery,
        plan_ids: &[String],
    ) -> Vec<ScaffoldPlanSummary> {
        store
            .catalog()
            .into_iter()
            .filter(|plan| plan_ids.contains(&plan.plan_id))
            .filter(|plan| {
                query.org.as_ref().is_none_or(|org| &plan.org == org)
                    && query.repo.as_ref().is_none_or(|repo| &plan.repo == repo)
                    && query.day.as_ref().is_none_or(|day| &plan.day == day)
            })
            .collect()
    }

    fn selected_plan(
        store: &ScaffoldArtifactStore,
        query: &ScaffoldQuery,
    ) -> Option<ScaffoldPlanSummary> {
        let org = query.org.as_deref()?;
        let repo = query.repo.as_deref()?;
        let day = query.day.as_deref()?;
        let plan_id = query.plan_id.as_deref()?;
        store.catalog().into_iter().find(|plan| {
            plan.org == org && plan.repo == repo && plan.day == day && plan.plan_id == plan_id
        })
    }

    fn plan_card_views(
        store: &ScaffoldArtifactStore,
        plans: Vec<ScaffoldPlanSummary>,
    ) -> Vec<ScaffoldPlanCard> {
        plans
            .into_iter()
            .map(|plan| ScaffoldPlanCard {
                reviewable: store.is_plan_reviewable(
                    &plan.org,
                    &plan.repo,
                    &plan.day,
                    &plan.plan_id,
                ),
                plan,
            })
            .collect()
    }

    fn scaffold_error_response(error: ScaffoldError) -> Response {
        let status = match error {
            ScaffoldError::Conflict { .. } => StatusCode::CONFLICT,
            ScaffoldError::SelectionRequired { .. } => StatusCode::MULTIPLE_CHOICES,
            ScaffoldError::ArtifactNotFound { .. } => StatusCode::NOT_FOUND,
            ScaffoldError::ReadOnly { .. }
            | ScaffoldError::UnsafePath { .. }
            | ScaffoldError::InvalidManifest { .. } => StatusCode::BAD_REQUEST,
            ScaffoldError::Io(_) | ScaffoldError::Json(_) => StatusCode::INTERNAL_SERVER_ERROR,
        };
        (
            status,
            Json(serde_json::json!({"error": error.to_string()})),
        )
            .into_response()
    }

    fn render_editor(workspace: &ScaffoldWorkspace) -> String {
        let first_id = workspace
            .artifacts
            .first()
            .map(|a| a.id.as_str())
            .unwrap_or("");
        let tabs = workspace
            .artifacts
            .iter()
            .map(|artifact| render_tab(artifact, artifact.id == first_id))
            .collect::<Vec<_>>()
            .join("");
        let panels = workspace
            .artifacts
            .iter()
            .map(|artifact| render_panel(workspace, artifact, artifact.id == first_id))
            .collect::<Vec<_>>()
            .join("");
        let approved = workspace
            .artifacts
            .iter()
            .filter(|artifact| artifact.checkpoint.approved)
            .count();
        let total = workspace.artifacts.len();
        format!(
            r#"<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scaffold review</title>
<style>{}</style>
</head>
<body>
<div class="studio-app-shell">
  <header class="studio-navbar">
    <a class="studio-navbar-brand" href="/" target="_top">
      <span class="studio-brand-mark" aria-hidden="true">⌁</span>
      <span><strong>Vibecrafted server</strong><small>scaffold studio</small></span>
    </a>
    <nav class="studio-global-nav" aria-label="Server routes">
      <a href="/" target="_top">Overview</a>
      <a href="/workspaces" target="_top">Workspaces</a>
      <a href="/sessions" target="_top">Sessions</a>
      <a href="/agents" target="_top">Agent Manager</a>
      <a href="/runs" target="_top">Runs</a>
      <a href="/lifecycle" target="_top">Control</a>
      <a href="/activity" target="_top">Activity</a>
      <a href="/structure" target="_top">Structure</a>
      <a class="is-active" href="/scaffold" target="_top">Scaffold</a>
    </nav>
    <a class="studio-back-link" href="/scaffold/library">All plans</a>
  </header>

<main class="review-shell" data-first-artifact="{}">
  <nav class="review-sidebar" aria-label="Scaffold artifacts">
    <div class="brand">Artifact index</div>
    <div class="summary">
      <strong>{}</strong>
      <span>{} / {} checkpointed</span>
      <span>{}</span>
    </div>
    <div class="tabs" role="tablist">{}</div>
  </nav>

  <div class="review-workspace">
    <header class="review-topbar" aria-label="Active artifact strip">
      <div class="review-topbar-id">
        <span class="mono-cap" id="active-role">—</span>
        <strong id="active-title">Select an artifact</strong>
        <span class="path" id="active-path"></span>
      </div>
      <div class="review-topbar-actions" id="active-actions"></div>
    </header>

    <section class="review-main" aria-label="Artifact canvas">
      <div id="status-saved" class="status">Saved. Agent endpoint is current.</div>
      <div id="status-error" class="status status-error">Save failed. Check server logs.</div>
      {}
    </section>

    <footer class="review-statusbar" aria-label="Plan statistics">
      <span><b id="stat-mode">rich</b> view</span>
      <span id="stat-chars">0 chars</span>
      <span>{} / {} checkpointed</span>
      <span class="stat-plan">{} · {}</span>
    </footer>
  </div>

  <aside class="review-inspector" aria-label="Tools and status">
    <div class="inspector-head mono-cap">Inspector</div>
    <div class="inspector-block">
      <h3>Status</h3>
      <p id="inspector-checkpoint" class="inspector-pill">—</p>
      <p class="inspector-meta" id="inspector-role">role —</p>
      <p class="inspector-meta" id="inspector-id">id —</p>
    </div>
    <div class="inspector-block" id="inspector-checkpoint-slot">
      <h3>Checkpoint</h3>
      <p class="inspector-hint">Switch artifact to load checkpoint controls.</p>
    </div>
    <div class="inspector-block">
      <h3>Endpoints</h3>
      <a class="api-link" href="/api/scaffold/artifacts?org={}&repo={}&day={}&plan_id={}" target="_blank" rel="noopener noreferrer">artifact endpoint ↗</a>
      <a class="api-link" href="/api/scaffold/changes?org={}&repo={}&day={}&plan_id={}" target="_blank" rel="noopener noreferrer">change endpoint ↗</a>
    </div>
  </aside>
</main>
</div>
{}
{}
{}
</body>
</html>"#,
            editor_css(),
            escape_attr(first_id),
            escape_html(&workspace.repo),
            approved,
            total,
            escape_html(&workspace.day),
            tabs,
            panels,
            approved,
            total,
            escape_html(&workspace.repo),
            escape_html(&workspace.day),
            url_component(&workspace.org),
            url_component(&workspace.repo),
            url_component(&workspace.day),
            url_component(&workspace.plan_id),
            url_component(&workspace.org),
            url_component(&workspace.repo),
            url_component(&workspace.day),
            url_component(&workspace.plan_id),
            save_on_close_guard(),
            render_mode_script(),
            panel_nav_script()
        )
    }

    fn render_plan_picker(
        plans: &[ScaffoldPlanCard],
        skipped: &[control_core::ScaffoldCatalogSkip],
    ) -> String {
        if plans.is_empty() && skipped.is_empty() {
            return render_empty("No manifest-backed scaffold plans are available.");
        }
        let repositories = plans
            .iter()
            .map(|card| format!("{}/{}", card.plan.org, card.plan.repo))
            .collect::<BTreeSet<_>>()
            .len();
        let artifact_count = plans
            .iter()
            .map(|card| card.plan.artifact_count)
            .sum::<usize>();
        let reviewable_count = plans.iter().filter(|card| card.reviewable).count();
        let cards = plans
            .iter()
            .enumerate()
            .map(|(index, card)| plan_picker_card(index, card))
            .collect::<Vec<_>>()
            .join("");
        let invalid_band = if skipped.is_empty() {
            String::new()
        } else {
            let rows = skipped
                .iter()
                .map(|skip| {
                    let id = skip
                        .guessed_plan_id
                        .as_deref()
                        .unwrap_or("(unknown plan_id)");
                    format!(
                        r#"<article class="plan-card plan-card-invalid" data-search="{}">
  <div class="plan-card-top">
    <span class="plan-number">!</span>
    <span class="plan-access">invalid</span>
  </div>
  <div class="plan-card-title">
    <p>manifest unreadable</p>
    <h3>{}</h3>
  </div>
  <p class="plan-skip-reason">{}</p>
  <p class="plan-skip-path"><code>{}</code></p>
</article>"#,
                        escape_attr(&format!("{} {}", id, skip.plan_root).to_ascii_lowercase()),
                        escape_html(&humanize_plan_id(id)),
                        escape_html(&skip.reason),
                        escape_html(&skip.plan_root),
                    )
                })
                .collect::<Vec<_>>()
                .join("");
            format!(
                r#"<section class="plan-field plan-invalid-field" aria-labelledby="plan-invalid-title">
  <div class="plan-toolbar">
    <div>
      <p class="eyebrow">Not in index</p>
      <h2 id="plan-invalid-title">Broken manifests ({})</h2>
    </div>
  </div>
  <p class="library-lede">These packages sit under <code>…/plans/&lt;id&gt;/manifest.json</code> but failed to load. Fix the role enum / schema — illegal values like <code>"mission"</code> must be <code>"other"</code> for MISSION.md.</p>
  <div class="plan-grid">{}</div>
</section>"#,
                skipped.len(),
                rows
            )
        };
        format!(
            r#"<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scaffold plans</title>
<style>{}</style>
</head>
<body>
<main class="plan-library">
  <header class="library-header">
    <nav class="library-nav" aria-label="Scaffold navigation">
      <a class="studio-navbar-brand" href="/" target="_top">
        <span class="studio-brand-mark" aria-hidden="true">⌁</span>
        <span><strong>Vibecrafted server</strong><small>scaffold library</small></span>
      </a>
      <div class="library-nav-links">
        <a href="/" target="_top">Overview</a>
        <a href="/runs" target="_top">Runs</a>
        <a href="/lifecycle" target="_top">Lifecycle</a>
        <a href="/activity" target="_top">Activity</a>
        <a href="/structure" target="_top">Structure</a>
        <span class="library-mode">Scaffold</span>
      </div>
    </nav>
    <div class="library-intro">
      <div>
        <p class="eyebrow">Plan control room</p>
        <h1>Choose the truth<br>you want to move.</h1>
      </div>
      <p class="library-lede">Every manifest-backed scaffold package available to this runtime. Search the field, open a plan, then edit and checkpoint its actual artifacts. Invalid packages no longer vanish — they surface below as broken manifests.</p>
    </div>
    <dl class="library-stats">
      <div><dt>plans</dt><dd>{}</dd></div>
      <div><dt>repositories</dt><dd>{}</dd></div>
      <div><dt>reviewable</dt><dd>{}</dd></div>
      <div><dt>artifacts</dt><dd>{}</dd></div>
      <div><dt>invalid</dt><dd>{}</dd></div>
    </dl>
  </header>

  <section class="plan-field" aria-labelledby="plan-field-title">
    <div class="plan-toolbar">
      <div>
        <p class="eyebrow">Manifest index</p>
        <h2 id="plan-field-title">Scaffold plans</h2>
      </div>
      <label class="plan-search">
        <span>Find a plan</span>
        <input id="plan-search" type="search" placeholder="repo, date, plan…" autocomplete="off">
        <kbd>/</kbd>
      </label>
    </div>
    <p class="result-count" aria-live="polite"><span id="visible-count">{}</span> plans visible</p>
    <div class="plan-grid" id="plan-grid">{}</div>
    <div class="plan-no-results" id="plan-no-results" hidden>
      <p class="eyebrow">No match</p>
      <strong>Nothing in the manifest index answers that search.</strong>
      <button type="button" id="clear-search">Clear search</button>
    </div>
  </section>
  {}
</main>
{}
</body>
</html>"#,
            editor_css(),
            plans.len(),
            repositories,
            reviewable_count,
            artifact_count,
            skipped.len(),
            plans.len(),
            cards,
            invalid_band,
            plan_picker_script(),
        )
    }

    fn plan_picker_card(index: usize, card: &ScaffoldPlanCard) -> String {
        let plan = &card.plan;
        let href = format!(
            "/scaffold?org={}&repo={}&day={}&plan_id={}",
            url_component(&plan.org),
            url_component(&plan.repo),
            url_component(&plan.day),
            url_component(&plan.plan_id),
        );
        let search = format!("{} {} {} {}", plan.org, plan.repo, plan.day, plan.plan_id);
        let (state_class, access) = if !card.reviewable {
            (" plan-card-blocked", "blocked")
        } else if plan.legacy_read_only {
            ("", "read only")
        } else {
            ("", "open")
        };
        format!(
            r#"<a class="plan-card{}" href="{}" data-search="{}">
  <div class="plan-card-top">
    <span class="plan-number">{:02}</span>
    <span class="plan-access">{}</span>
  </div>
  <div class="plan-card-title">
    <p>{} / {}</p>
    <h3>{}</h3>
  </div>
  <dl class="plan-card-meta">
    <div><dt>day</dt><dd>{}</dd></div>
    <div><dt>artifacts</dt><dd>{}</dd></div>
  </dl>
  <span class="plan-open">Open plan <b aria-hidden="true">↗</b></span>
</a>"#,
            state_class,
            escape_attr(&href),
            escape_attr(&search.to_ascii_lowercase()),
            index + 1,
            access,
            escape_html(&plan.org),
            escape_html(&plan.repo),
            escape_html(&humanize_plan_id(&plan.plan_id)),
            escape_html(&plan.day.replace('_', " · ")),
            plan.artifact_count,
        )
    }

    fn render_plan_blocked(
        plan: &ScaffoldPlanSummary,
        report: Option<&ScaffoldDoctorReport>,
        error: &str,
    ) -> String {
        let issues = report
            .map(|report| {
                report
                    .errors
                    .iter()
                    .map(|issue| {
                        let rule = issue
                            .rule
                            .as_deref()
                            .map(|rule| format!(" · {rule}"))
                            .unwrap_or_default();
                        let path = issue
                            .path
                            .as_deref()
                            .map(|path| format!("<code>{}</code>", escape_html(path)))
                            .unwrap_or_else(|| "<code>plan contract</code>".to_string());
                        format!(
                            r#"<li><div><span>{}{}</span>{}</div><p>{}</p></li>"#,
                            escape_html(&issue.code),
                            rule,
                            path,
                            escape_html(&issue.message),
                        )
                    })
                    .collect::<Vec<_>>()
                    .join("")
            })
            .filter(|issues| !issues.is_empty())
            .unwrap_or_else(|| {
                format!(
                    "<li><div><span>runtime refusal</span><code>workspace</code></div><p>{}</p></li>",
                    escape_html(error)
                )
            });
        let issue_count = report.map_or(1, |report| report.errors.len().max(1));
        format!(
            r#"<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scaffold plan blocked</title>
<style>{}</style>
</head>
<body>
<main class="blocked-plan-shell">
  <nav class="library-nav">
    <a class="studio-navbar-brand" href="/" target="_top">
      <span class="studio-brand-mark" aria-hidden="true">⌁</span>
      <span><strong>Vibecrafted server</strong><small>blocked scaffold</small></span>
    </a>
    <div class="library-nav-links">
      <a href="/" target="_top">Overview</a>
      <a class="back-link" href="/scaffold/library">← Scaffold library</a>
    </div>
  </nav>
  <header class="blocked-plan-head">
    <div>
      <p class="eyebrow">{}/{}</p>
      <h1>{}</h1>
    </div>
    <span class="blocked-pill">{} contract issues</span>
  </header>
  <div class="blocked-plan-grid">
    <section class="blocked-explainer">
      <p class="eyebrow">Runtime truth</p>
      <h2>The plan exists.<br>The editor refuses to lie.</h2>
      <p>The manifest is indexed, but its current artifact contract cannot be opened safely. Repair these findings and the same card will become reviewable automatically.</p>
      <dl>
        <div><dt>day</dt><dd>{}</dd></div>
        <div><dt>artifacts</dt><dd>{}</dd></div>
        <div><dt>root</dt><dd>{}</dd></div>
      </dl>
    </section>
    <section class="blocked-findings">
      <div class="blocked-findings-head">
        <p class="eyebrow">Scaffold doctor</p>
        <strong>{} findings</strong>
      </div>
      <ol>{}</ol>
    </section>
  </div>
</main>
</body>
</html>"#,
            editor_css(),
            escape_html(&plan.org),
            escape_html(&plan.repo),
            escape_html(&humanize_plan_id(&plan.plan_id)),
            issue_count,
            escape_html(&plan.day.replace('_', " · ")),
            plan.artifact_count,
            escape_html(&plan.plan_root),
            issue_count,
            issues,
        )
    }

    fn humanize_plan_id(plan_id: &str) -> String {
        plan_id
            .split(['-', '_'])
            .filter(|part| !part.is_empty())
            .map(|part| {
                let mut chars = part.chars();
                match chars.next() {
                    Some(first) => format!("{}{}", first.to_uppercase(), chars.as_str()),
                    None => String::new(),
                }
            })
            .collect::<Vec<_>>()
            .join(" ")
    }

    fn plan_picker_script() -> &'static str {
        r#"<script>
(function () {
  var search = document.getElementById("plan-search");
  var cards = Array.prototype.slice.call(document.querySelectorAll(".plan-card"));
  var count = document.getElementById("visible-count");
  var empty = document.getElementById("plan-no-results");
  var clear = document.getElementById("clear-search");

  function normalize(value) {
    return value
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function filterPlans() {
    var needle = normalize(search.value);
    var visible = 0;
    cards.forEach(function (card) {
      var match = !needle || normalize(card.dataset.search).indexOf(needle) !== -1;
      card.hidden = !match;
      if (match) visible += 1;
    });
    count.textContent = String(visible);
    empty.hidden = visible !== 0;
  }

  search.addEventListener("input", filterPlans);
  clear.addEventListener("click", function () {
    search.value = "";
    filterPlans();
    search.focus();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "/" && document.activeElement !== search) {
      event.preventDefault();
      search.focus();
    } else if (event.key === "Escape" && document.activeElement === search) {
      search.value = "";
      filterPlans();
      search.blur();
    }
  });
})();
</script>"#
    }

    fn render_empty(message: &str) -> String {
        format!(
            r#"<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Scaffold review unavailable</title><style>{}</style></head>
<body><main class="empty"><a class="back-link" href="/" target="_top">← Back to console</a><h1>Scaffold review</h1><p>{}</p></main></body>
</html>"#,
            editor_css(),
            escape_html(message)
        )
    }

    fn render_tab(artifact: &ScaffoldArtifact, active: bool) -> String {
        let checkpoint = if artifact.checkpoint.approved {
            "done"
        } else {
            "open"
        };
        let active_class = if active { " is-active" } else { "" };
        let selected = if active { "true" } else { "false" };
        format!(
            r##"<a class="tab tab-{}{}" href="#{}" role="tab" aria-selected="{}"><span>{}</span><small>{}</small></a>"##,
            checkpoint,
            active_class,
            escape_attr(&artifact.id),
            selected,
            escape_html(&artifact.title),
            artifact.role.as_str()
        )
    }

    fn render_panel(
        workspace: &ScaffoldWorkspace,
        artifact: &ScaffoldArtifact,
        active: bool,
    ) -> String {
        let checked = if artifact.checkpoint.approved {
            " checked"
        } else {
            ""
        };
        let status = if artifact.checkpoint.approved {
            "checkpointed"
        } else {
            "needs checkpoint"
        };
        let active_class = if active { " is-active" } else { "" };
        let hidden_attr = if active { "" } else { " hidden" };
        // Default view is formatted rich markdown. "Edit" opens the mono
        // source textarea; "Save" persists (if dirty) and returns to rich.
        // Only the active panel is visible (studio shell — one document).
        format!(
            r#"<article class="artifact-panel{}" id="{}" data-render-mode="rich"{} aria-hidden="{}">
  <header class="artifact-head">
    <div>
      <p class="eyebrow">{}</p>
      <h2>{}</h2>
      <p class="path">{}</p>
    </div>
    <div class="artifact-head-actions">
      <button type="button" class="render-mode-btn" data-next="edit" title="Edit markdown source" aria-label="Edit markdown source">Edit</button>
      <span class="checkpoint-state">{}</span>
    </div>
  </header>
  <form method="post" action="/api/scaffold/artifact" class="editor-form">
    {}
    <div class="editor-body">
      <textarea name="content" class="raw-pane" spellcheck="false" hidden>{}</textarea>
      <div class="rich-pane md-body" aria-live="polite"></div>
    </div>
    <button type="submit" class="save-artifact-btn" hidden>Save artifact</button>
  </form>
  <form method="post" action="/api/scaffold/checkpoint" class="checkpoint-form">
    {}
    <label><input type="checkbox" name="approved" value="1"{}> Approved checkpoint</label>
    <input name="note" value="{}" placeholder="checkpoint note">
    <button type="submit">Update checkpoint</button>
  </form>
</article>"#,
            active_class,
            escape_attr(&artifact.id),
            hidden_attr,
            if active { "false" } else { "true" },
            artifact.role.as_str(),
            escape_html(&artifact.title),
            escape_html(&artifact.relative_path),
            status,
            hidden_context(workspace, artifact),
            escape_html(&artifact.content),
            hidden_context(workspace, artifact),
            checked,
            escape_attr(&artifact.checkpoint.note)
        )
    }

    /// App-wide save-on-close guard for the editable artifact panels.
    ///
    /// The editor persisted only on an explicit "Save artifact" click, so
    /// closing the window/tab (Cmd-W), or the SPA tearing the editor iframe
    /// down on navigation, silently dropped any Markdown typed in the moments
    /// before the click. This guard tracks dirty state per `editor-form` and
    /// flushes every dirty buffer to the existing `POST /api/scaffold/artifact`
    /// endpoint via `navigator.sendBeacon` on `pagehide` (iframe teardown / tab
    /// close) and `beforeunload` (Cmd-W). It introduces no new persistence
    /// path: it re-uses each form's own `action`, never the checkpoint form,
    /// and only warns natively when a beacon flush is impossible.
    fn save_on_close_guard() -> &'static str {
        r#"<script>
(function () {
  var forms = Array.prototype.slice.call(
    document.querySelectorAll("form.editor-form")
  );
  forms.forEach(function (form) {
    var ta = form.querySelector("textarea[name=content]");
    if (!ta) return;
    form.dataset.baseline = ta.value;
    ta.addEventListener("input", function () {
      form.dataset.dirty = ta.value !== form.dataset.baseline ? "1" : "";
    });
    form.addEventListener("submit", function () {
      form.dataset.baseline = ta.value;
      form.dataset.dirty = "";
    });
  });

  function flush() {
    if (!navigator.sendBeacon) return;
    forms.forEach(function (form) {
      if (form.dataset.dirty !== "1") return;
      var ta = form.querySelector("textarea[name=content]");
      try {
        var body = new URLSearchParams(new FormData(form));
        // sendBeacon serializes a URLSearchParams body as text/plain, which the
        // axum `Form` extractor rejects with 415 (the edit is silently lost).
        // Send an explicitly typed blob so the content type round-trips.
        var payload = new Blob([body.toString()], {
          type: "application/x-www-form-urlencoded",
        });
        if (navigator.sendBeacon(form.getAttribute("action"), payload)) {
          form.dataset.dirty = "";
          if (ta) form.dataset.baseline = ta.value;
        }
      } catch (e) {}
    });
  }

  function anyDirty() {
    return forms.some(function (form) {
      return form.dataset.dirty === "1";
    });
  }

  window.addEventListener("pagehide", flush);
  window.addEventListener("beforeunload", function (ev) {
    flush();
    if (anyDirty()) {
      ev.preventDefault();
      ev.returnValue = "";
    }
  });
})();
</script>"#
    }

    /// Per-panel raw↔rich toggle — Codescribe Agent-chat contract, ported to the
    /// scaffold artifact review surface, with Pensieve-ish document structure and
    /// Codescribe-tray rolling status chips for tracker glyphs.
    ///
    /// - Default mode is **raw** (textarea = disk bytes; mono source of truth).
    /// - Button label is the mode a click switches **to** (action verb).
    /// - Rich re-parses the live textarea on each switch so edits are never lost.
    /// - Tracker status tokens (`[ ]`/`[~]`/`[?]`/`[!]`/`[x]`) are clickable
    ///   chips that cycle like tray Auto Format (Off→…→Max); each click rewrites
    ///   the matching occurrence in the raw textarea and marks the form dirty.
    /// - Save always posts the textarea; rich is view + status-toggle only.
    fn render_mode_script() -> &'static str {
        r#"<script>
(function () {
  // Tracker legend order (runtime-enclave style). Click advances one step —
  // same rolling-state affordance as Codescribe tray Auto Format.
  var STATUS_CYCLE = [" ", "~", "?", "!", "x"];
  var STATUS_META = {
    " ": { label: "todo", glyph: " " },
    "~": { label: "running", glyph: "~" },
    "?": { label: "done?", glyph: "?" },
    "!": { label: "blocked", glyph: "!" },
    x: { label: "green", glyph: "x" },
    X: { label: "green", glyph: "x" },
  };
  // Matches backtick-wrapped tracker tokens and bare [x] task markers.
  // Intentionally does NOT match [links](url).
  var STATUS_RE = /`?\[([ xX~!?])\]`?/g;

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function normalizeMark(m) {
    return m === "X" ? "x" : m;
  }

  function nextMark(m) {
    var cur = normalizeMark(m);
    var idx = STATUS_CYCLE.indexOf(cur);
    if (idx < 0) idx = 0;
    return STATUS_CYCLE[(idx + 1) % STATUS_CYCLE.length];
  }

  function statusChip(mark, occ) {
    var m = normalizeMark(mark);
    var meta = STATUS_META[m] || STATUS_META[" "];
    return (
      '<button type="button" class="md-status md-status-' +
      (m === " " ? "todo" : m === "?" ? "maybe" : m === "!" ? "blocked" : m === "~" ? "run" : "done") +
      '" data-task-occ="' +
      occ +
      '" data-mark="' +
      esc(m) +
      '" title="Cycle status (now: ' +
      meta.label +
      ')" aria-label="Status ' +
      meta.label +
      ', click to cycle">' +
      "<span class=\"md-status-glyph\">[" +
      esc(meta.glyph) +
      "]</span>" +
      '<span class="md-status-label">' +
      esc(meta.label) +
      "</span></button>"
    );
  }

  // Enumerate every status token in source order so click can rewrite the
  // matching occurrence without ambiguous search. Fenced code is skipped so
  // occurrence indices stay in lockstep with mdToHtml (which also lifts fences
  // before scanning body tokens).
  function findStatusMatches(src) {
    var fenceRanges = [];
    var fre = /```[\s\S]*?```/g;
    var fm;
    while ((fm = fre.exec(src)) !== null) {
      fenceRanges.push([fm.index, fm.index + fm[0].length]);
    }
    function inFence(pos) {
      for (var fi = 0; fi < fenceRanges.length; fi++) {
        if (pos >= fenceRanges[fi][0] && pos < fenceRanges[fi][1]) return true;
      }
      return false;
    }
    var re = new RegExp(STATUS_RE.source, "g");
    var hits = [];
    var m;
    while ((m = re.exec(src)) !== null) {
      if (inFence(m.index)) continue;
      hits.push({
        index: m.index,
        length: m[0].length,
        mark: normalizeMark(m[1]),
        raw: m[0],
        backticked: m[0].charAt(0) === "`",
      });
    }
    return hits;
  }

  function replaceStatusOcc(src, occ, newMark) {
    var hits = findStatusMatches(src);
    if (occ < 0 || occ >= hits.length) return src;
    var hit = hits[occ];
    var repl = hit.backticked
      ? "`[" + newMark + "]`"
      : "[" + newMark + "]";
    return src.slice(0, hit.index) + repl + src.slice(hit.index + hit.length);
  }

  function mdToHtml(src) {
    var text = String(src || "").replace(/\r\n/g, "\n");
    var occCounter = { n: 0 };

    // Extract fenced code first (placeholders survive block scan).
    var fences = [];
    text = text.replace(/```([^\n`]*)\n([\s\S]*?)```/g, function (_, lang, body) {
      var i = fences.length;
      fences.push(
        '<pre class="md-code"><code' +
          (lang && lang.trim()
            ? ' data-lang="' + esc(lang.trim()) + '"'
            : "") +
          ">" +
          esc(body.replace(/\n$/, "")) +
          "</code></pre>"
      );
      return "\n%%FENCE" + i + "%%\n";
    });

    var lines = text.split("\n");
    var out = [];
    var i = 0;

    // YAML frontmatter: leading --- ... ---
    if (lines[0] === "---") {
      var fm = [];
      i = 1;
      while (i < lines.length && lines[i] !== "---") {
        fm.push(lines[i]);
        i++;
      }
      if (i < lines.length && lines[i] === "---") i++;
      var rows = fm
        .filter(function (l) {
          return l.trim().length;
        })
        .map(function (l) {
          var colon = l.indexOf(":");
          if (colon < 0) {
            return (
              '<div class="md-fm-row"><span class="md-fm-val">' +
              inline(l, occCounter) +
              "</span></div>"
            );
          }
          return (
            '<div class="md-fm-row"><span class="md-fm-key">' +
            esc(l.slice(0, colon).trim()) +
            '</span><span class="md-fm-val">' +
            inline(l.slice(colon + 1).trim(), occCounter) +
            "</span></div>"
          );
        })
        .join("");
      out.push('<section class="md-frontmatter">' + rows + "</section>");
    }

    function isTableSep(line) {
      return /^\s*\|?[\s:|-]+\|[\s|:|-]+\|?\s*$/.test(line) && /\|/.test(line) && /-/.test(line);
    }
    function isTableRow(line) {
      return /\|/.test(line) && !isTableSep(line);
    }
    function splitRow(line) {
      var s = line.trim();
      if (s.charAt(0) === "|") s = s.slice(1);
      if (s.charAt(s.length - 1) === "|") s = s.slice(0, -1);
      return s.split("|").map(function (c) {
        return c.trim();
      });
    }

    while (i < lines.length) {
      var line = lines[i];
      var fence = line.match(/^%%FENCE(\d+)%%$/);
      if (fence) {
        out.push(fences[Number(fence[1])]);
        i++;
        continue;
      }
      if (/^\s*$/.test(line)) {
        i++;
        continue;
      }
      var h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        var lvl = h[1].length;
        out.push(
          "<h" + lvl + ">" + inline(h[2], occCounter) + "</h" + lvl + ">"
        );
        i++;
        continue;
      }
      if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
        out.push("<hr>");
        i++;
        continue;
      }
      // GFM table: header + separator + body rows
      if (
        isTableRow(line) &&
        i + 1 < lines.length &&
        isTableSep(lines[i + 1])
      ) {
        var header = splitRow(line);
        i += 2;
        var bodyRows = [];
        while (i < lines.length && isTableRow(lines[i])) {
          bodyRows.push(splitRow(lines[i]));
          i++;
        }
        var thead =
          "<thead><tr>" +
          header
            .map(function (c) {
              return "<th>" + inline(c, occCounter) + "</th>";
            })
            .join("") +
          "</tr></thead>";
        var tbody =
          "<tbody>" +
          bodyRows
            .map(function (row) {
              return (
                "<tr>" +
                row
                  .map(function (c) {
                    return "<td>" + inline(c, occCounter) + "</td>";
                  })
                  .join("") +
                "</tr>"
              );
            })
            .join("") +
          "</tbody>";
        out.push('<div class="md-table-wrap"><table class="md-table">' + thead + tbody + "</table></div>");
        continue;
      }
      if (/^>\s?/.test(line)) {
        var q = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          q.push(lines[i].replace(/^>\s?/, ""));
          i++;
        }
        // Preserve newlines inside quotes as <br>, never flatten to one line.
        out.push(
          "<blockquote>" +
            q
              .map(function (ql) {
                return inline(ql, occCounter);
              })
              .join("<br>") +
            "</blockquote>"
        );
        continue;
      }
      if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
        var ordered = /^\s*\d+\.\s+/.test(line);
        var tag = ordered ? "ol" : "ul";
        var items = [];
        while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
          var rawItem = lines[i];
          var task = rawItem.match(/^(\s*[-*+]\s+)\[([ xX~!?])\]\s+(.*)$/);
          if (task) {
            var mark = normalizeMark(task[2]);
            var chip = statusChip(mark, occCounter.n++);
            items.push(
              '<li class="md-task md-task-' +
                (mark === " " ? "todo" : mark === "?" ? "maybe" : mark === "!" ? "blocked" : mark === "~" ? "run" : "done") +
                '">' +
                chip +
                " " +
                inline(task[3], occCounter) +
                "</li>"
            );
          } else {
            items.push(
              "<li>" +
                inline(rawItem.replace(/^\s*([-*+]|\d+\.)\s+/, ""), occCounter) +
                "</li>"
            );
          }
          i++;
        }
        out.push("<" + tag + ' class="md-list">' + items.join("") + "</" + tag + ">");
        continue;
      }
      // Paragraph: keep line breaks as <br> (do NOT join with spaces — that
      // is what flattened YAML/table leftovers into one soup).
      var para = [];
      while (
        i < lines.length &&
        !/^\s*$/.test(lines[i]) &&
        !/^%%FENCE\d+%%$/.test(lines[i]) &&
        !/^(#{1,6})\s+/.test(lines[i]) &&
        !/^>\s?/.test(lines[i]) &&
        !/^\s*([-*+]|\d+\.)\s+/.test(lines[i]) &&
        !/^(-{3,}|\*{3,}|_{3,})\s*$/.test(lines[i]) &&
        !(
          isTableRow(lines[i]) &&
          i + 1 < lines.length &&
          isTableSep(lines[i + 1])
        )
      ) {
        para.push(lines[i]);
        i++;
      }
      out.push(
        "<p>" +
          para
            .map(function (pl) {
              return inline(pl, occCounter);
            })
            .join("<br>") +
          "</p>"
      );
    }
    return out.join("\n");

    function inline(s, counter) {
      // Lift status tokens first so backtick/code pass does not eat them.
      var parts = [];
      var re = new RegExp(STATUS_RE.source, "g");
      var last = 0;
      var m;
      var str = String(s);
      while ((m = re.exec(str)) !== null) {
        if (m.index > last) parts.push({ t: "text", v: str.slice(last, m.index) });
        parts.push({ t: "status", mark: normalizeMark(m[1]), occ: counter.n++ });
        last = m.index + m[0].length;
      }
      if (last < str.length) parts.push({ t: "text", v: str.slice(last) });
      if (!parts.length) parts.push({ t: "text", v: str });

      return parts
        .map(function (p) {
          if (p.t === "status") return statusChip(p.mark, p.occ);
          var t = esc(p.v);
          t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
          t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
          t = t.replace(/(^|[^\*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
          t = t.replace(
            /\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
            '<a href="$2" rel="noopener noreferrer" target="_blank">$1</a>'
          );
          return t;
        })
        .join("");
    }
  }

  function markFormDirty(panel) {
    var form = panel.querySelector("form.editor-form");
    var ta = panel.querySelector("textarea.raw-pane");
    if (!form || !ta) return;
    if (typeof form.dataset.baseline === "undefined") {
      form.dataset.baseline = ta.value;
    }
    form.dataset.dirty = ta.value !== form.dataset.baseline ? "1" : "";
  }

  function renderRich(panel) {
    var ta = panel.querySelector("textarea.raw-pane");
    var rich = panel.querySelector(".rich-pane");
    if (!ta || !rich) return;
    rich.innerHTML = mdToHtml(ta.value);
  }

  function syncTopbarEditProxy(btn) {
    // Studio topbar mirrors the live panel button; keep label/state in lockstep.
    var actions = document.getElementById("active-actions");
    if (!actions || !btn) return;
    var proxy = actions.querySelector(".render-mode-btn");
    if (!proxy) return;
    proxy.textContent = btn.textContent;
    proxy.dataset.next = btn.dataset.next;
    proxy.title = btn.title || "";
    proxy.setAttribute("aria-label", btn.getAttribute("aria-label") || "");
  }

  function setMode(panel, mode) {
    var ta = panel.querySelector("textarea.raw-pane");
    var rich = panel.querySelector(".rich-pane");
    var btn = panel.querySelector(".render-mode-btn");
    var saveBtn = panel.querySelector(".save-artifact-btn");
    if (!ta || !rich || !btn) return;
    // Normalize legacy "raw" alias to edit mode.
    if (mode === "raw") mode = "edit";
    panel.setAttribute("data-render-mode", mode);
    if (mode === "rich") {
      renderRich(panel);
      rich.hidden = false;
      ta.hidden = true;
      if (saveBtn) saveBtn.hidden = true;
      btn.dataset.next = "edit";
      btn.textContent = "Edit";
      btn.title = "Edit markdown source";
      btn.setAttribute("aria-label", "Edit markdown source");
    } else {
      rich.hidden = true;
      rich.innerHTML = "";
      ta.hidden = false;
      if (saveBtn) saveBtn.hidden = false;
      btn.dataset.next = "rich";
      btn.textContent = "Save";
      btn.title = "Save and show formatted view";
      btn.setAttribute("aria-label", "Save and show formatted view");
      ta.focus({ preventScroll: true });
    }
    if (panel.classList.contains("is-active") || !document.querySelector(".artifact-panel.is-active")) {
      syncTopbarEditProxy(btn);
    }
    var statMode = document.getElementById("stat-mode");
    if (statMode && (panel.classList.contains("is-active") || !document.querySelector(".artifact-panel.is-active"))) {
      statMode.textContent = mode;
    }
  }

  document.querySelectorAll(".artifact-panel").forEach(function (panel) {
    var btn = panel.querySelector(".render-mode-btn");
    var form = panel.querySelector("form.editor-form");
    if (btn) {
      btn.addEventListener("click", function () {
        var next = btn.dataset.next === "edit" || btn.dataset.next === "raw" ? "edit" : "rich";
        if (next === "rich") {
          // Leaving edit: persist if dirty, then return to formatted view.
          markFormDirty(panel);
          if (form && form.dataset.dirty === "1") {
            form.requestSubmit();
            return;
          }
        }
        setMode(panel, next);
      });
    }
    // Default: formatted rich view (not mono source).
    setMode(panel, "rich");
    // Event delegation: status chips rewrite raw + re-render rich + post typed status update.
    var rich = panel.querySelector(".rich-pane");
    var ta = panel.querySelector("textarea.raw-pane");
    if (rich) {
      // A raw edit supersedes any in-flight chip write. Its response must not
      // bless newer textarea bytes as saved.
      if (form && ta) {
        ta.addEventListener("input", function () {
          form.dataset.statusRequestSeq = String(Number(form.dataset.statusRequestSeq || "0") + 1);
        });
      }
      rich.addEventListener("click", function (ev) {
        var chip = ev.target.closest(".md-status");
        if (!chip || !rich.contains(chip)) return;
        ev.preventDefault();
        if (!ta) return;
        var org = form ? (form.querySelector("input[name=org]") || {}).value : "";
        var repo = form ? (form.querySelector("input[name=repo]") || {}).value : "";
        var day = form ? (form.querySelector("input[name=day]") || {}).value : "";
        var plan_id = form ? (form.querySelector("input[name=plan_id]") || {}).value : "";
        var artifact_id = form ? (form.querySelector("input[name=artifact_id]") || {}).value : "";

        var occ = Number(chip.getAttribute("data-task-occ"));
        var cur = chip.getAttribute("data-mark") || " ";
        var nxt = nextMark(cur);
        ta.value = replaceStatusOcc(ta.value, occ, nxt);
        markFormDirty(panel);
        // Keep rich open and re-parse so indices stay consistent.
        renderRich(panel);

        if (org && repo && day && plan_id && artifact_id) {
          // Multiple clicks and raw edits can overtake this request. Only the
          // latest untouched textarea may adopt the server's canonical bytes.
          var requestSeq = Number(form.dataset.statusRequestSeq || "0") + 1;
          form.dataset.statusRequestSeq = String(requestSeq);
          var requestedContent = ta.value;
          fetch("/api/scaffold/status", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              org: org,
              repo: repo,
              day: day,
              plan_id: plan_id,
              artifact_id: artifact_id,
              item_index: occ,
              status: nxt
            })
          }).then(function (res) {
            if (!res.ok) throw new Error("status update failed");
            return res.json();
          }).then(function (payload) {
            if (Number(form.dataset.statusRequestSeq || "0") === requestSeq && ta.value === requestedContent) {
              var canonical = payload && payload.artifact && payload.artifact.content;
              if (typeof canonical === "string") ta.value = canonical;
              form.dataset.baseline = ta.value;
              form.dataset.dirty = "";
            }
          }).catch(function () {});
        }
      });
    }
  });
})();
</script>"#
    }

    /// Single-document studio navigation (GlyphPulse / unicode-puzzles-portal shape).
    ///
    /// Hash + left tabs drive one `.artifact-panel.is-active`. Everything else
    /// stays hidden so the page never becomes a 30m scroll of every artifact.
    /// Topbar, inspector, and statusbar mirror the active panel.
    fn panel_nav_script() -> &'static str {
        r##"<script>
(function () {
  var shell = document.querySelector(".review-shell");
  if (!shell) return;

  var tabs = Array.prototype.slice.call(document.querySelectorAll(".tabs .tab"));
  var panels = Array.prototype.slice.call(document.querySelectorAll(".artifact-panel"));
  var firstId = shell.getAttribute("data-first-artifact") || (panels[0] && panels[0].id) || "";

  var elRole = document.getElementById("active-role");
  var elTitle = document.getElementById("active-title");
  var elPath = document.getElementById("active-path");
  var elActions = document.getElementById("active-actions");
  var elCheckpoint = document.getElementById("inspector-checkpoint");
  var elRoleMeta = document.getElementById("inspector-role");
  var elIdMeta = document.getElementById("inspector-id");
  var slot = document.getElementById("inspector-checkpoint-slot");
  var statMode = document.getElementById("stat-mode");
  var statChars = document.getElementById("stat-chars");

  var homeMarkup = slot
    ? slot.innerHTML
    : '<h3>Checkpoint</h3><p class="inspector-hint">Switch artifact to load checkpoint controls.</p>';

  function panelIdFromHash() {
    var h = (location.hash || "").replace(/^#/, "");
    if (!h || h.indexOf("status-") === 0) return "";
    if (h.indexOf("artifact/") === 0) {
      try { return decodeURIComponent(h.slice("artifact/".length)); }
      catch (_) { return ""; }
    }
    // Legacy `#mission` links remain readable, but new hashes deliberately do
    // not equal a panel DOM id so browsers cannot anchor-scroll the studio.
    return h;
  }

  function findPanel(id) {
    for (var i = 0; i < panels.length; i++) {
      if (panels[i].id === id) return panels[i];
    }
    return null;
  }

  function updateStats(panel) {
    if (!panel) return;
    var ta = panel.querySelector("textarea.raw-pane");
    var mode = panel.getAttribute("data-render-mode") || "rich";
    var text = ta ? ta.value : "";
    if (statMode) statMode.textContent = mode;
    if (statChars) {
      var lines = text ? text.split(/\n/).length : 0;
      statChars.textContent = text.length + " chars · " + lines + " lines";
    }
  }

  function bindStats(panel) {
    var ta = panel.querySelector("textarea.raw-pane");
    if (!ta || ta.dataset.statsBound === "1") return;
    ta.dataset.statsBound = "1";
    ta.addEventListener("input", function () {
      if (panel.classList.contains("is-active")) updateStats(panel);
    });
    var btn = panel.querySelector(".render-mode-btn");
    if (btn) {
      btn.addEventListener("click", function () {
        // setMode runs first on the other listener; defer one tick.
        setTimeout(function () {
          if (panel.classList.contains("is-active")) updateStats(panel);
        }, 0);
      });
    }
  }

  function activate(id, opts) {
    opts = opts || {};
    var restorePageX = opts.resetPageScroll ? 0 : window.scrollX;
    var restorePageY = opts.resetPageScroll ? 0 : window.scrollY;
    var panel = findPanel(id);
    if (!panel) {
      if (firstId && id !== firstId) return activate(firstId, opts);
      return;
    }

    panels.forEach(function (p) {
      var on = p.id === panel.id;
      p.classList.toggle("is-active", on);
      p.hidden = !on;
      p.setAttribute("aria-hidden", on ? "false" : "true");
    });

    tabs.forEach(function (t) {
      var href = (t.getAttribute("href") || "").replace(/^#/, "");
      var on = href === panel.id;
      t.classList.toggle("is-active", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
    });

    var eyebrow = panel.querySelector(".eyebrow");
    var h2 = panel.querySelector(".artifact-head h2");
    var path = panel.querySelector(".path");
    var state = panel.querySelector(".checkpoint-state");
    var actions = panel.querySelector(".artifact-head-actions");

    if (elRole) elRole.textContent = eyebrow ? eyebrow.textContent : "—";
    if (elTitle) elTitle.textContent = h2 ? h2.textContent : panel.id;
    if (elPath) elPath.textContent = path ? path.textContent : "";
    if (elActions) {
      elActions.innerHTML = "";
      if (actions) {
        // Mirror Edit + checkpoint pill into the fixed topbar (same plane).
        Array.prototype.slice.call(actions.children).forEach(function (node) {
          if (node.classList && node.classList.contains("render-mode-btn")) {
            // Keep the live button in the panel head (hidden by CSS in studio);
            // clone a proxy that forwards clicks so topbar stays interactive.
            var proxy = node.cloneNode(true);
            proxy.addEventListener("click", function (ev) {
              ev.preventDefault();
              node.click();
              // Refresh proxy label after setMode mutates the source button.
              setTimeout(function () {
                proxy.textContent = node.textContent;
                proxy.dataset.next = node.dataset.next;
                proxy.title = node.title || "";
                proxy.setAttribute("aria-label", node.getAttribute("aria-label") || "");
                updateStats(panel);
              }, 0);
            });
            elActions.appendChild(proxy);
          } else {
            elActions.appendChild(node.cloneNode(true));
          }
        });
      }
    }

    if (elCheckpoint) {
      elCheckpoint.textContent = state ? state.textContent : "—";
      elCheckpoint.classList.toggle(
        "is-done",
        !!(state && /checkpointed/i.test(state.textContent || ""))
      );
    }
    if (elRoleMeta) elRoleMeta.textContent = "role " + (eyebrow ? eyebrow.textContent : "—");
    if (elIdMeta) elIdMeta.textContent = "id " + panel.id;

    // Relocate the live checkpoint form into the inspector (one at a time).
    if (slot) {
      // Return any previously parked form to its home panel.
      var parked = slot.querySelector("form.checkpoint-form");
      if (parked && parked.dataset.homePanel) {
        var home = document.getElementById(parked.dataset.homePanel);
        if (home) home.appendChild(parked);
      }
      slot.innerHTML = "<h3>Checkpoint</h3>";
      var form = panel.querySelector("form.checkpoint-form");
      if (form) {
        form.dataset.homePanel = panel.id;
        slot.appendChild(form);
      } else {
        slot.insertAdjacentHTML("beforeend", '<p class="inspector-hint">No checkpoint controls on this artifact.</p>');
      }
    }

    bindStats(panel);
    updateStats(panel);

    if (!opts.skipHash) {
      var next = "#artifact/" + encodeURIComponent(panel.id);
      if (location.hash !== next) {
        if (history.replaceState) history.replaceState(null, "", next);
        else location.hash = "artifact/" + encodeURIComponent(panel.id);
      }
    }

    // Keep the active tab visible inside the rail without scrolling the whole
    // document. `scrollIntoView` moved the mobile studio down by the sidebar
    // height on boot because the tabs become a horizontal strip there.
    var activeTab = document.querySelector('.tabs .tab.is-active');
    var tabRail = activeTab && activeTab.closest(".tabs");
    if (activeTab && tabRail) {
      var tabBox = activeTab.getBoundingClientRect();
      var railBox = tabRail.getBoundingClientRect();
      if (tabBox.left < railBox.left) tabRail.scrollLeft -= railBox.left - tabBox.left;
      else if (tabBox.right > railBox.right) tabRail.scrollLeft += tabBox.right - railBox.right;
      if (tabBox.top < railBox.top) tabRail.scrollTop -= railBox.top - tabBox.top;
      else if (tabBox.bottom > railBox.bottom) tabRail.scrollTop += tabBox.bottom - railBox.bottom;
    }

    // Fragment updates select artifacts; they must never turn into page-level
    // navigation. Restore the studio viewport after the browser processes the
    // new hash so the global navbar remains visible on mobile.
    window.scrollTo(restorePageX, restorePageY);
    if (window.requestAnimationFrame) {
      window.requestAnimationFrame(function () {
        window.scrollTo(restorePageX, restorePageY);
      });
    }
  }

  tabs.forEach(function (t) {
    t.addEventListener("click", function (ev) {
      var href = (t.getAttribute("href") || "").replace(/^#/, "");
      if (!href || !findPanel(href)) return;
      ev.preventDefault();
      activate(href);
    });
  });

  window.addEventListener("hashchange", function () {
    var id = panelIdFromHash();
    if (id) activate(id, { skipHash: true });
  });

  // Init: hash wins, else first artifact. Never leave every panel visible.
  var boot = panelIdFromHash() || firstId;
  if (boot) activate(boot, { resetPageScroll: true });
  else panels.forEach(function (p) { p.hidden = true; });
})();
</script>"##
    }

    fn hidden_context(workspace: &ScaffoldWorkspace, artifact: &ScaffoldArtifact) -> String {
        format!(
            r#"<input type="hidden" name="org" value="{}">
<input type="hidden" name="repo" value="{}">
<input type="hidden" name="day" value="{}">
<input type="hidden" name="plan_id" value="{}">
<input type="hidden" name="artifact_id" value="{}">
<input type="hidden" name="expected_hash" value="{}">"#,
            escape_attr(&workspace.org),
            escape_attr(&workspace.repo),
            escape_attr(&workspace.day),
            escape_attr(&workspace.plan_id),
            escape_attr(&artifact.id),
            escape_attr(&artifact.content_hash),
        )
    }

    fn editor_css() -> &'static str {
        r#"
:root{color-scheme:dark;--bg:#0a0a0b;--panel:#121214;--panel-lift:#1a1a1e;--line:#27272a;--text:#f4f4f5;--muted:#a1a1aa;--accent:#d4d4d8;--teal:#d4d4d8;--amber:#d4d4d8;--warn:#fbbf24;--bad:#f87171}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 Inter,ui-sans-serif,system-ui,sans-serif}
a{color:inherit}
/* Studio editor locks the viewport; plan library / blocked pages still scroll. */
body:has(.review-shell){height:100vh;overflow:hidden}
.plan-library{min-height:100vh;background:radial-gradient(circle at 83% 7%,rgba(77,155,142,.13),transparent 31rem),var(--bg)}
.library-header{padding:26px clamp(24px,5vw,76px) 54px;border-bottom:1px solid var(--line)}
.library-nav{display:flex;align-items:center;justify-content:space-between;gap:24px;margin-bottom:clamp(64px,9vw,130px)}
.library-nav .brand{text-decoration:none}.library-mode{color:var(--muted);font:11px ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.14em}
.library-intro{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(280px,.6fr);gap:clamp(28px,6vw,90px);align-items:end}
.library-intro h1{max-width:850px;margin:12px 0 0;font:400 clamp(48px,7vw,102px)/.89 Georgia,'Times New Roman',serif;letter-spacing:-.055em}
.library-lede{max-width:540px;margin:0 0 8px;color:var(--muted);font-size:clamp(15px,1.5vw,19px);line-height:1.55}
.library-stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));max-width:920px;margin:54px 0 0;border-top:1px solid var(--line)}
.plan-card-invalid{border-color:rgba(255,209,102,.35);background:linear-gradient(145deg,#1b1914,#111415);cursor:default}
.plan-card-invalid:hover{transform:none;border-color:rgba(255,209,102,.45)}
.plan-skip-reason{margin:0;color:var(--warn);font-size:13px;line-height:1.45}
.plan-skip-path{margin:8px 0 0;color:var(--muted);font:11px ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}
.plan-invalid-field{padding-top:8px;border-top:1px solid var(--line)}
.library-stats div{padding:14px 24px 0 0}.library-stats dt,.plan-card-meta dt{color:var(--muted);font:10px ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.12em}
.library-stats dd{margin:2px 0 0;color:var(--amber);font:28px ui-monospace,SFMono-Regular,Menlo,monospace}
.plan-field{padding:42px clamp(24px,5vw,76px) 80px}
.plan-toolbar{display:flex;align-items:end;justify-content:space-between;gap:30px}.plan-toolbar h2{margin:5px 0 0;font:400 34px/1.05 Georgia,'Times New Roman',serif}
.plan-search{position:relative;display:grid;gap:7px;width:min(100%,390px);color:var(--muted);font:10px ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.12em}
.plan-search input{width:100%;border:0;border-bottom:1px solid var(--line);outline:0;background:transparent;color:var(--text);padding:8px 34px 10px 0;font:15px Inter,ui-sans-serif,system-ui,sans-serif;text-transform:none;letter-spacing:0}
.plan-search input:focus{border-color:var(--teal)}.plan-search kbd{position:absolute;right:0;bottom:10px;border:1px solid var(--line);border-radius:4px;padding:1px 6px;color:var(--muted);font:11px ui-monospace,SFMono-Regular,Menlo,monospace}
.result-count{margin:32px 0 14px;color:var(--muted);font:11px ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.1em}
.plan-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:12px}
.plan-card{min-height:290px;display:flex;flex-direction:column;justify-content:space-between;gap:28px;padding:22px;border:1px solid var(--line);border-radius:9px;background:linear-gradient(145deg,var(--panel),#111415);text-decoration:none;transition:transform .18s ease,border-color .18s ease,background .18s ease}
.plan-card:hover,.plan-card:focus-visible{transform:translateY(-3px);border-color:var(--teal);background:var(--panel-lift);outline:none}
.plan-card-blocked{border-color:rgba(255,138,138,.28);background:linear-gradient(145deg,#1b1617,#111415)}.plan-card-blocked .plan-access{border-color:rgba(255,138,138,.35);color:var(--bad)}
.plan-card[hidden]{display:none}.plan-card-top,.plan-card-meta,.plan-open{display:flex;align-items:center;justify-content:space-between;gap:16px}
.plan-number{color:var(--amber);font:12px ui-monospace,SFMono-Regular,Menlo,monospace}.plan-access{border:1px solid var(--line);border-radius:99px;padding:4px 8px;color:var(--muted);font:9px ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.1em}
.plan-card-title p{margin:0 0 9px;color:var(--teal);font:11px ui-monospace,SFMono-Regular,Menlo,monospace}.plan-card-title h3{max-width:470px;margin:0;font:400 28px/1.03 Georgia,'Times New Roman',serif;letter-spacing:-.025em}
.plan-card-meta{margin:0;padding-top:14px;border-top:1px solid var(--line)}.plan-card-meta div{display:grid;gap:3px}.plan-card-meta div:last-child{text-align:right}.plan-card-meta dd{margin:0;color:var(--text);font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
.plan-open{color:var(--muted);font-weight:700}.plan-open b{color:var(--accent);font-size:18px}.plan-card:hover .plan-open{color:var(--text)}
.plan-no-results{margin-top:12px;border:1px dashed var(--line);border-radius:9px;padding:50px 24px;text-align:center;color:var(--muted)}.plan-no-results strong{display:block;color:var(--text);font:400 24px Georgia,'Times New Roman',serif}.plan-no-results button{justify-self:auto;margin:20px 0 0}
.blocked-plan-shell{min-height:100vh;padding:26px clamp(24px,5vw,76px) 80px;background:radial-gradient(circle at 85% 5%,rgba(255,138,138,.08),transparent 32rem),var(--bg)}.blocked-plan-shell .library-nav{margin-bottom:clamp(60px,8vw,110px)}.back-link{color:var(--muted);font:11px ui-monospace,SFMono-Regular,Menlo,monospace;text-decoration:none;text-transform:uppercase;letter-spacing:.12em}.back-link:hover{color:var(--text)}
.blocked-plan-head{display:flex;align-items:end;justify-content:space-between;gap:30px;padding-bottom:36px;border-bottom:1px solid var(--line)}.blocked-plan-head h1{max-width:900px;margin:10px 0 0;font:400 clamp(44px,6.5vw,88px)/.92 Georgia,'Times New Roman',serif;letter-spacing:-.045em}.blocked-pill{flex:0 0 auto;border:1px solid rgba(255,138,138,.35);border-radius:99px;padding:7px 11px;color:var(--bad);font:10px ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.1em}
.blocked-plan-grid{display:grid;grid-template-columns:minmax(280px,.72fr) minmax(0,1.28fr);gap:clamp(36px,7vw,110px);padding-top:42px}.blocked-explainer h2{margin:8px 0 18px;font:400 clamp(31px,4vw,52px)/.98 Georgia,'Times New Roman',serif}.blocked-explainer>p:not(.eyebrow){max-width:520px;color:var(--muted);font-size:16px;line-height:1.6}.blocked-explainer dl{display:grid;gap:12px;margin:36px 0 0}.blocked-explainer dl div{display:grid;grid-template-columns:80px 1fr;gap:16px;padding-top:10px;border-top:1px solid var(--line)}.blocked-explainer dt{color:var(--muted);font:10px ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase}.blocked-explainer dd{min-width:0;margin:0;overflow-wrap:anywhere;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
.blocked-findings{border:1px solid var(--line);border-radius:9px;background:var(--panel);overflow:hidden}.blocked-findings-head{display:flex;align-items:end;justify-content:space-between;gap:20px;padding:18px 20px;border-bottom:1px solid var(--line)}.blocked-findings-head p{margin:0}.blocked-findings-head strong{color:var(--bad);font:11px ui-monospace,SFMono-Regular,Menlo,monospace}.blocked-findings ol{max-height:68vh;margin:0;padding:0;overflow:auto;list-style:none}.blocked-findings li{padding:17px 20px;border-bottom:1px solid var(--line)}.blocked-findings li:last-child{border:0}.blocked-findings li div{display:flex;justify-content:space-between;gap:14px}.blocked-findings li span{color:var(--bad);font:11px ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase}.blocked-findings li code{color:var(--teal);font:11px ui-monospace,SFMono-Regular,Menlo,monospace}.blocked-findings li p{margin:8px 0 0;color:var(--muted);line-height:1.5}
/* --- Scaffold studio shell (GlyphPulse shape: nav | canvas | inspector + stats) --- */
.review-shell{display:grid;grid-template-columns:280px minmax(0,1fr) 300px;height:100vh;overflow:hidden;background:var(--bg)}
.review-sidebar{border-right:1px solid var(--line);padding:18px 14px;height:100vh;display:flex;flex-direction:column;gap:14px;background:#101314;min-height:0;overflow:hidden}
.brand{font:700 12px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.08em;color:var(--accent)}
.summary{display:grid;gap:3px;color:var(--muted);flex:0 0 auto}.summary strong{color:var(--text);font-size:16px}
.tabs{display:flex;flex-direction:column;gap:6px;overflow:auto;padding-right:4px;min-height:0;flex:1 1 auto}
.tab{display:grid;gap:2px;text-decoration:none;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:9px 10px;background:#171b1d}
.tab:hover,.tab:focus{border-color:var(--accent);outline:none}
.tab.is-active{border-color:var(--teal);background:var(--panel-lift);box-shadow:inset 2px 0 0 var(--accent)}
.tab small{color:var(--muted);font:11px ui-monospace,SFMono-Regular,Menlo,monospace}.tab-done{border-color:#4d7041}
.tab-done.is-active{border-color:var(--accent)}
.api-link{display:block;color:var(--accent);font:12px ui-monospace,SFMono-Regular,Menlo,monospace;text-decoration:none;margin:6px 0}
.api-link:hover{text-decoration:underline}
/* Center column: topbar + one document + statusbar */
.review-workspace{display:grid;grid-template-rows:auto minmax(0,1fr) auto;min-width:0;min-height:0;height:100vh;overflow:hidden;border-right:1px solid var(--line)}
.review-topbar{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:10px 16px;border-bottom:1px solid var(--line);background:#101314;min-height:52px;flex:0 0 auto}
.review-topbar-id{display:grid;gap:2px;min-width:0}
.review-topbar-id .mono-cap{color:var(--teal);font:10px ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.12em}
.review-topbar-id strong{font:600 15px/1.2 Inter,ui-sans-serif,system-ui,sans-serif;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.review-topbar-id .path{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.review-topbar-actions{display:flex;align-items:center;gap:8px;flex:0 0 auto}
.review-main{position:relative;min-height:0;overflow:hidden;padding:0;display:block;background:var(--bg)}
.review-main>.status{position:absolute;z-index:5;left:16px;right:16px;top:12px;margin:0}
.status{display:none;border:1px solid #4d7041;background:#162114;padding:10px;border-radius:8px}.status:target{display:block}.status-error{border-color:var(--bad);background:#2b1717}
/* One active document only — never stack every artifact */
/* Rows: editor-form fills, trailing forms (pre-JS checkpoint) auto. The
 * .artifact-head is permanently display:none (chrome lives in the topbar) and
 * the checkpoint form is relocated into the inspector by JS, so the form must
 * own the flexible track — `auto 1fr` collapsed it to 0 (empty document). */
.artifact-panel{display:none;height:100%;min-height:0;border:0;border-radius:0;background:var(--panel);overflow:hidden;grid-template-rows:minmax(0,1fr) auto}
.artifact-panel.is-active{display:grid}
.artifact-panel.is-active .editor-form{display:grid;grid-template-rows:minmax(0,1fr) auto;min-height:0;height:100%}
/* Absolute fill — Safari %height on textarea inside grid-fr collapses to content (black void). */
.artifact-panel.is-active .editor-body{position:relative;min-height:0;height:100%;overflow:hidden}
/* Panel chrome lives in topbar; keep DOM for JS but hide visual double-header */
.artifact-panel .artifact-head{display:none}
.artifact-head{display:flex;justify-content:space-between;gap:16px;padding:16px;border-bottom:1px solid var(--line)}.artifact-head h2{margin:3px 0 0;font-size:22px;letter-spacing:0}
.artifact-head-actions{display:flex;align-items:center;gap:8px;flex:0 0 auto}
/* Edit/Save + checkpoint pills share the same plane (radius/padding/border) */
.render-mode-btn,.checkpoint-state,.inspector-pill{
  display:inline-flex;align-items:center;justify-content:center;align-self:center;
  margin:0;border:1px solid var(--line);border-radius:999px;padding:5px 11px;
  font:12px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.02em;
  background:#171b1d;color:var(--muted);white-space:nowrap
}
button.render-mode-btn{cursor:pointer;font-weight:500;color:var(--text);background:#1b1f20}
button.render-mode-btn:hover,button.render-mode-btn:focus-visible{border-color:var(--teal);color:var(--text);outline:none;background:var(--panel-lift)}
button.render-mode-btn[data-next="rich"]{border-color:rgba(184,239,125,.45);color:var(--accent);background:rgba(184,239,125,.08)}
.checkpoint-state{color:var(--warn)}.checkpoint-state:empty{display:none}
.inspector-pill{color:var(--warn)}.inspector-pill.is-done{color:var(--accent);border-color:#4d7041}
.eyebrow,.path{margin:0;color:var(--muted);font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
.editor-form{display:grid;grid-template-rows:1fr auto;min-height:0}
.editor-body{position:relative;min-height:0}
.editor-form textarea.raw-pane{
  position:absolute;inset:0;box-sizing:border-box;width:100%;height:100%;min-height:0;
  resize:none;border:0;margin:0;outline:none;
  background:#0f1213;color:var(--text);-webkit-text-fill-color:var(--text);caret-color:var(--accent);
  padding:16px 18px;font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;overflow:auto;white-space:pre-wrap
}
.editor-form textarea.raw-pane:focus{outline:none;box-shadow:inset 0 0 0 1px rgba(77,155,142,.35)}
.rich-pane.md-body{
  position:absolute;inset:0;box-sizing:border-box;min-height:0;
  padding:22px clamp(18px,3vw,36px) 36px;border:0;
  background:linear-gradient(180deg,#101314 0%,#0c0e0f 100%);color:var(--text);
  font:14.5px/1.6 Inter,ui-sans-serif,system-ui,sans-serif;overflow:auto
}
.rich-pane.md-body h1,.rich-pane.md-body h2,.rich-pane.md-body h3,.rich-pane.md-body h4{margin:1.25em 0 .5em;line-height:1.22;letter-spacing:-.02em;color:var(--text);font-weight:600}
.rich-pane.md-body h1{font-size:1.65em;padding-bottom:.35em;border-bottom:1px solid var(--line)}
.rich-pane.md-body h2{font-size:1.32em;padding-bottom:.28em;border-bottom:1px solid rgba(43,48,51,.85)}
.rich-pane.md-body h3{font-size:1.12em}
.rich-pane.md-body p{margin:.65em 0;max-width:78ch}
.rich-pane.md-body ul.md-list,.rich-pane.md-body ol.md-list{margin:.55em 0;padding-left:1.35em}
.rich-pane.md-body li{margin:.28em 0}
.rich-pane.md-body li.md-task{list-style:none;margin-left:-.4em;display:flex;align-items:flex-start;gap:8px}
.rich-pane.md-body blockquote{margin:.8em 0;padding:.2em 0 .2em 14px;border-left:3px solid rgba(77,155,142,.55);color:var(--muted)}
.rich-pane.md-body hr{border:0;border-top:1px solid var(--line);margin:1.2em 0}
.rich-pane.md-body code{font:12.5px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--accent);background:rgba(184,239,125,.08);padding:.1em .35em;border-radius:4px}
.rich-pane.md-body pre.md-code{margin:.85em 0;padding:12px 14px;border:1px solid var(--line);border-radius:8px;background:#0a0c0d;overflow:auto}
.rich-pane.md-body pre.md-code code{background:transparent;padding:0;color:var(--text);font-size:12.5px;line-height:1.5;white-space:pre}
.rich-pane.md-body a{color:var(--teal)}.rich-pane.md-body strong{color:#fff;font-weight:650}
/* Frontmatter as meta card (Notion property table vibe) */
.md-frontmatter{display:grid;gap:6px;margin:0 0 1.4em;padding:12px 14px;border:1px solid var(--line);border-radius:10px;background:rgba(27,31,32,.85)}
.md-fm-row{display:grid;grid-template-columns:minmax(96px,160px) minmax(0,1fr);gap:10px;align-items:baseline;padding:3px 0}
.md-fm-key{color:var(--muted);font:11px ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.06em}
.md-fm-val{font:12.5px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--text);overflow-wrap:anywhere}
/* GFM tables */
.md-table-wrap{margin:.9em 0 1.1em;overflow:auto;border:1px solid var(--line);border-radius:10px;background:#0f1213}
.md-table{width:100%;border-collapse:collapse;font-size:13px;line-height:1.45}
.md-table th,.md-table td{padding:9px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
.md-table th{color:var(--muted);font:11px ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.06em;background:rgba(255,255,255,.02);position:sticky;top:0}
.md-table tr:last-child td{border-bottom:0}
.md-table tr:hover td{background:rgba(255,255,255,.015)}
/* Rolling status chips — Codescribe tray Auto Format affordance */
button.md-status{display:inline-flex;align-items:center;gap:6px;margin:0 2px;padding:2px 8px 2px 6px;border:1px solid var(--line);border-radius:999px;background:#171b1d;color:var(--muted);font:11px ui-monospace,SFMono-Regular,Menlo,monospace;cursor:pointer;vertical-align:middle;line-height:1.3;transition:border-color .12s ease,color .12s ease,background .12s ease}
button.md-status:hover,button.md-status:focus-visible{border-color:var(--teal);color:var(--text);outline:none}
button.md-status .md-status-glyph{font-weight:700;letter-spacing:.02em}
button.md-status .md-status-label{opacity:.85;text-transform:lowercase}
button.md-status.md-status-todo{border-color:rgba(169,177,180,.35);color:var(--muted)}
button.md-status.md-status-run{border-color:rgba(216,166,64,.55);color:var(--amber);background:rgba(216,166,64,.08)}
button.md-status.md-status-maybe{border-color:rgba(77,155,142,.5);color:var(--teal);background:rgba(77,155,142,.08)}
button.md-status.md-status-blocked{border-color:rgba(255,138,138,.55);color:var(--bad);background:rgba(255,138,138,.08)}
button.md-status.md-status-done{border-color:rgba(184,239,125,.55);color:var(--accent);background:rgba(184,239,125,.08)}
button{justify-self:start;margin:12px 16px;border:1px solid #5e7f47;background:#22321f;color:var(--text);border-radius:7px;padding:8px 12px;font-weight:700;cursor:pointer}
/* Save sits in the form's bottom auto-row (not floating in the black void). */
.artifact-panel .save-artifact-btn{
  margin:0;padding:8px 14px;justify-self:start;align-self:center;
  border-radius:7px;border:1px solid #5e7f47;background:#22321f;color:var(--text);font-weight:700
}
.artifact-panel.is-active .editor-form>.save-artifact-btn{margin:8px 16px 12px}
.checkpoint-form{display:flex;flex-direction:column;align-items:stretch;gap:10px;padding:0;margin:0}
.checkpoint-form label{display:flex;align-items:center;gap:8px;color:var(--text);font-size:13px}
.checkpoint-form input[name=note]{width:100%;min-width:0;border:1px solid var(--line);background:#0f1213;color:var(--text);border-radius:7px;padding:8px;font:13px Inter,ui-sans-serif,system-ui,sans-serif}
.checkpoint-form button{margin:0;width:100%;justify-self:stretch}
/* Right inspector (tools + status) */
.review-inspector{height:100vh;min-height:0;overflow:auto;padding:14px 14px 20px;background:#0f1213;display:flex;flex-direction:column;gap:14px}
.inspector-head{color:var(--muted);font:10px ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.14em;padding-bottom:6px;border-bottom:1px solid var(--line)}
.inspector-block{display:grid;gap:8px;padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--panel)}
.inspector-block h3{margin:0;font:600 12px/1.2 Inter,ui-sans-serif,system-ui,sans-serif;color:var(--text);letter-spacing:.02em}
.inspector-meta{margin:0;color:var(--muted);font:11px ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}
.inspector-hint{margin:0;color:var(--muted);font-size:12px;line-height:1.45}
.mono-cap{font:10px ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.12em;color:var(--muted)}
/* Bottom stats bar */
.review-statusbar{display:flex;flex-wrap:wrap;align-items:center;gap:14px;padding:8px 16px;border-top:1px solid var(--line);background:#101314;color:var(--muted);font:11px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace;flex:0 0 auto}
.review-statusbar b{color:var(--text);font-weight:600}
.review-statusbar .stat-plan{margin-left:auto;color:var(--teal)}
.empty{max-width:720px;margin:12vh auto;border:1px solid var(--line);border-radius:8px;padding:24px;background:var(--panel)}
@media(max-width:1100px){
  .review-shell{grid-template-columns:240px minmax(0,1fr) 260px}
}
@media(max-width:820px){
  .library-intro,.blocked-plan-grid{grid-template-columns:1fr}
  .library-nav{margin-bottom:64px}.library-stats{max-width:none}
  .plan-toolbar{align-items:stretch;flex-direction:column}.plan-search{width:100%}
  .blocked-plan-head{align-items:start;flex-direction:column}
  body:has(.review-shell){height:auto;overflow:auto}
  .review-shell{grid-template-columns:1fr;grid-template-rows:auto minmax(60vh,1fr) auto;height:auto;min-height:100vh;overflow:visible}
  .review-sidebar{position:relative;height:auto;max-height:40vh;border-right:0;border-bottom:1px solid var(--line)}
  .review-workspace{height:auto;min-height:60vh;border-right:0}
  .review-inspector{height:auto;border-top:1px solid var(--line)}
  .artifact-panel.is-active{min-height:50vh}
  .review-statusbar .stat-plan{margin-left:0}
}
/* Shared vc-server chrome: the scaffold renderer is raw HTML, so it mirrors
 * the Leptos ServerFrame contract without importing a second routing system. */
.studio-app-shell{display:grid;grid-template-rows:58px minmax(0,1fr);height:100vh;min-height:100vh;overflow:hidden;background:var(--bg)}
.studio-navbar{position:relative;z-index:30;display:flex;align-items:center;justify-content:space-between;gap:18px;height:58px;padding:0 18px;border-bottom:1px solid var(--line);background:rgba(10,10,11,.94);backdrop-filter:blur(18px)}
.studio-navbar-brand{display:inline-flex;align-items:center;gap:10px;min-width:0;color:var(--text);text-decoration:none}
.studio-navbar-brand>span:last-child{display:grid;min-width:0;line-height:1.15}
.studio-navbar-brand strong{font:650 13px/1.2 Inter,ui-sans-serif,system-ui,sans-serif;white-space:nowrap}
.studio-navbar-brand small{color:var(--muted);font:10px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:nowrap}
.studio-brand-mark{display:grid;place-items:center;flex:0 0 auto;width:30px;height:30px;border:1px solid var(--line);border-radius:8px;background:var(--panel-lift);color:var(--muted);font:700 16px/1 ui-monospace,SFMono-Regular,Menlo,monospace}
.studio-global-nav,.library-nav-links{display:flex;align-items:center;gap:5px}
.studio-global-nav a,.library-nav-links a,.library-nav-links span,.studio-back-link{min-height:32px;display:inline-flex;align-items:center;padding:0 10px;border:1px solid transparent;border-radius:8px;color:var(--muted);font:550 11px/1 Inter,ui-sans-serif,system-ui,sans-serif;text-decoration:none;white-space:nowrap}
.studio-global-nav a:hover,.studio-global-nav a:focus-visible,.library-nav-links a:hover,.library-nav-links a:focus-visible,.studio-back-link:hover,.studio-back-link:focus-visible{border-color:var(--line);background:var(--panel-lift);color:var(--text);outline:none}
.studio-global-nav a.is-active,.library-nav-links span{border-color:var(--line);background:var(--panel);color:var(--text)}
.studio-back-link{border-color:var(--line);background:var(--panel);color:var(--text)}
.review-shell{height:100%;min-height:0}
.review-sidebar,.review-workspace,.review-inspector{height:100%}
.review-sidebar,.review-topbar,.review-statusbar{background:#0f0f11}
.review-inspector{background:#0d0d0f}
.tab,.render-mode-btn,.checkpoint-state,.inspector-pill{background:#18181b}
.tab.is-active{border-color:#52525b;background:#202024;box-shadow:inset 2px 0 0 #d4d4d8}
.tab:hover,.tab:focus,.api-link{color:var(--text)}
.plan-library{background:radial-gradient(700px 320px at 15% -8%,rgba(63,63,70,.2),transparent 64%),var(--bg)}
.library-header{padding:0 0 30px;border-bottom:1px solid var(--line)}
.library-nav{position:sticky;top:0;z-index:20;height:58px;margin:0 0 30px;padding:0 clamp(18px,3vw,38px);border-bottom:1px solid var(--line);background:rgba(10,10,11,.94);backdrop-filter:blur(18px)}
.library-intro,.library-stats{margin-left:auto;margin-right:auto;width:min(calc(100% - 48px),1152px)}
.library-intro{grid-template-columns:minmax(0,1fr) minmax(280px,.72fr);gap:40px}
.library-intro h1{max-width:760px;margin:9px 0 0;font:650 clamp(32px,4.4vw,54px)/.98 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:-.055em}
.library-lede{font-size:14px;line-height:1.6}
.library-stats{margin-top:30px;border-top:1px solid var(--line)}
.library-stats div{padding-top:12px}.library-stats dd{color:var(--text);font-size:22px}
.plan-field{width:min(100%,1200px);margin:0 auto;padding:28px 24px 68px}
.plan-toolbar h2{font:650 26px/1.05 ui-monospace,SFMono-Regular,Menlo,monospace}
.plan-card{min-height:230px;border-radius:16px;background:linear-gradient(145deg,var(--panel),#0f0f11);box-shadow:0 1px 0 rgba(255,255,255,.025)}
.plan-card:hover,.plan-card:focus-visible{transform:translateY(-2px);border-color:#52525b;background:var(--panel-lift)}
.plan-card-title h3{font:600 22px/1.08 Inter,ui-sans-serif,system-ui,sans-serif}
.plan-card-title p,.plan-number,.plan-open b{color:var(--text)}
.blocked-plan-shell{padding:0 clamp(24px,5vw,76px) 80px;background:radial-gradient(700px 320px at 15% -8%,rgba(63,63,70,.2),transparent 64%),var(--bg)}
.blocked-plan-shell .library-nav{margin-inline:calc(clamp(24px,5vw,76px) * -1);margin-bottom:42px}
.blocked-plan-head h1,.blocked-explainer h2{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.empty{border-radius:16px}
@media(max-width:820px){
  .studio-app-shell{height:auto;min-height:100vh;overflow:visible}
  .studio-navbar{height:auto;min-height:54px;padding:8px 12px;flex-wrap:wrap}
  .studio-global-nav{order:3;flex:1 1 100%;overflow-x:auto;overflow-y:hidden;padding-bottom:4px;scrollbar-width:thin}
  .review-shell{height:auto;min-height:calc(100vh - 54px)}
  .review-sidebar{height:auto;max-height:none}
  .tabs{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(190px,72vw);overflow-x:auto;overflow-y:hidden;padding:0 0 5px}
  .review-workspace{min-height:64vh}
  .review-inspector{height:auto}
  .library-intro{grid-template-columns:1fr}
  .library-nav{margin-bottom:24px}
}
@media(max-width:620px){
  .studio-navbar-brand small{display:none}
  .studio-back-link{padding-inline:8px;font-size:10px}
  .library-nav-links a:not(.back-link){display:none}
  .library-intro,.library-stats{width:min(calc(100% - 32px),1152px)}
  .library-stats{grid-template-columns:repeat(2,minmax(0,1fr))}
  .library-intro h1{font-size:32px}
  .plan-grid{grid-template-columns:1fr}
}
@media(max-width:520px){.library-header,.plan-field{padding-left:18px;padding-right:18px}.library-stats dd{font-size:20px}}
"#
    }

    fn escape_html(value: &str) -> String {
        value
            .replace('&', "&amp;")
            .replace('<', "&lt;")
            .replace('>', "&gt;")
    }

    fn escape_attr(value: &str) -> String {
        escape_html(value).replace('"', "&quot;")
    }

    fn url_component(value: &str) -> String {
        value
            .chars()
            .map(|c| {
                if c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.') {
                    c
                } else {
                    '-'
                }
            })
            .collect()
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use control_core::{
            ScaffoldArtifact, ScaffoldArtifactRole, ScaffoldCheckpoint, ScaffoldPlanSummary,
            ScaffoldWorkspace,
        };

        fn fixture() -> ScaffoldWorkspace {
            // org/repo casing follows git ground truth (github.com/vetcoders/*),
            // not historical branding mutants.
            ScaffoldWorkspace {
                org: "vetcoders".into(),
                repo: "vibecrafted".into(),
                day: "2026_0615".into(),
                plan_id: "plan-a".into(),
                plan_root: "/tmp/plan-a".into(),
                legacy_read_only: false,
                changes_path: "/tmp/op/.scaffold-changes.jsonl".into(),
                checkpoints_path: "/tmp/op/.scaffold-checkpoints.json".into(),
                artifacts: vec![ScaffoldArtifact {
                    id: "master-dispatch".into(),
                    title: "Master Dispatch".into(),
                    role: ScaffoldArtifactRole::WaveAtlas,
                    path: "/tmp/op/master-dispatch.md".into(),
                    relative_path: "operator/master-dispatch.md".into(),
                    editable: true,
                    required: true,
                    content: "# atlas\n\n**bold** and `code`\n".into(),
                    content_hash: "sha256:test".into(),
                    bytes: 7,
                    modified_at: "2026-06-15T00:00:00Z".into(),
                    checkpoint: ScaffoldCheckpoint::default(),
                }],
            }
        }

        #[test]
        fn editor_embeds_save_on_close_guard() {
            let html = render_editor(&fixture());
            // Flush on teardown, not just on the explicit Save click.
            assert!(
                html.contains("navigator.sendBeacon"),
                "editor must flush unsaved edits via sendBeacon on close"
            );
            // pagehide covers iframe teardown on SPA nav + tab close.
            assert!(
                html.contains("\"pagehide\""),
                "missing pagehide handler (sidebar/SPA teardown + tab close)"
            );
            // beforeunload covers Cmd-W / window close.
            assert!(
                html.contains("\"beforeunload\""),
                "missing beforeunload handler (Cmd-W / window close)"
            );
            // Dirty tracking is scoped to the editable artifact panels.
            assert!(
                html.contains("form.editor-form"),
                "guard must target the editable artifact panels"
            );
        }

        #[test]
        fn guard_reuses_typed_endpoint_and_skips_checkpoints() {
            let guard = save_on_close_guard();
            // No parallel persistence path: the guard posts to each form's own
            // action (the typed /api/scaffold/artifact endpoint), nothing new.
            assert!(
                guard.contains("form.getAttribute(\"action\")"),
                "guard must reuse the form's own typed save endpoint"
            );
            assert!(
                !guard.contains("checkpoint"),
                "checkpoint approval is a deliberate action and must not auto-flush"
            );
        }

        #[test]
        fn conflict_maps_to_http_409_and_editor_carries_revision() {
            let response = scaffold_error_response(ScaffoldError::Conflict {
                expected: "sha256:old".into(),
                actual: "sha256:new".into(),
            });
            assert_eq!(response.status(), StatusCode::CONFLICT);
            let html = render_editor(&fixture());
            assert!(html.contains("name=\"plan_id\" value=\"plan-a\""));
            assert!(html.contains("name=\"expected_hash\" value=\"sha256:test\""));
        }

        #[test]
        fn form_mutation_error_redirects_back_to_editor_status() {
            let response = redirect_after_mutation(
                "vetcoders",
                "vibecrafted",
                "2026_0615",
                "plan-a",
                Some(ScaffoldError::Conflict {
                    expected: "sha256:old".into(),
                    actual: "sha256:new".into(),
                }),
            );

            assert_eq!(response.status(), StatusCode::SEE_OTHER);
            assert_eq!(
                response.headers().get(header::LOCATION).unwrap(),
                "/scaffold?org=vetcoders&repo=vibecrafted&day=2026_0615&plan_id=plan-a#status-error"
            );
        }

        #[test]
        fn editor_embeds_raw_rich_render_mode_toggle() {
            let html = render_editor(&fixture());
            // Formatted rich is the default; Edit opens mono source, Save
            // returns to rich (and posts if dirty).
            assert!(
                html.contains(r#"data-render-mode="rich""#),
                "artifact panels default to formatted rich view"
            );
            assert!(
                html.contains(r#"class="render-mode-btn""#),
                "missing per-panel Edit/Save control"
            );
            assert!(
                html.contains(r#"data-next="edit""#),
                "view mode must offer Edit"
            );
            assert!(
                html.contains(">Edit</button>"),
                "meta button label must be Edit in default view"
            );
            assert!(
                html.contains(r#"class="rich-pane md-body""#),
                "missing rich markdown pane"
            );
            assert!(
                html.contains("function mdToHtml"),
                "editor must ship the client markdown renderer for live rich toggle"
            );
            assert!(
                html.contains(r#"setMode(panel, "rich")"#),
                "panels must init into rich view on load"
            );
            // Source textarea remains the write path (hidden until Edit).
            assert!(html.contains(r#"name="content" class="raw-pane""#));
        }

        #[test]
        fn editor_ships_single_document_studio_shell() {
            let html = render_editor(&fixture());
            // GlyphPulse / unicode-puzzles-portal shape: left nav, canvas, right
            // inspector, bottom stats — never a 30m scroll of every artifact.
            assert!(
                html.contains(r#"class="review-shell""#),
                "missing studio shell root"
            );
            assert!(
                html.contains(r#"class="review-workspace""#),
                "missing center workspace column"
            );
            assert!(
                html.contains(r#"class="review-topbar""#),
                "missing top status strip"
            );
            assert!(
                html.contains(r#"class="review-inspector""#),
                "missing right tools/status inspector"
            );
            assert!(
                html.contains(r#"class="review-statusbar""#),
                "missing bottom statistics bar"
            );
            assert!(
                html.contains("panel_nav") || html.contains("function activate"),
                "missing panel navigation script that activates one document"
            );
            assert!(
                html.contains("artifact-panel.is-active") || html.contains(r#".is-active"#),
                "CSS must gate visibility on .is-active (one document)"
            );
            assert!(
                html.contains("grid-template-columns:280px minmax(0,1fr) 300px")
                    || html.contains("review-inspector"),
                "studio must be a three-column shell"
            );
            // Edit shares the pill plane with checkpoint-state (not a ghost link).
            assert!(
                html.contains(".render-mode-btn,.checkpoint-state")
                    || html.contains("border-radius:999px"),
                "Edit control must share pill geometry with checkpoint-state"
            );
            assert!(html.contains(r#"class="studio-navbar""#));
            assert!(html.contains(r#"href="/" target="_top""#));
            assert!(html.contains(r#"href="/scaffold/library""#));
            assert!(html.contains("All plans"));
            assert!(html.contains(r#"href="/runs" target="_top""#));
            assert!(html.contains(r##""#artifact/" + encodeURIComponent(panel.id)"##));
        }

        #[test]
        fn rich_mode_ships_rolling_tracker_status_and_structured_blocks() {
            let html = render_editor(&fixture());
            // Rolling cycle matches tracker legend / Codescribe tray Auto Format.
            assert!(
                html.contains(r#"STATUS_CYCLE = [" ", "~", "?", "!", "x"]"#),
                "status cycle must follow tracker legend order"
            );
            assert!(
                html.contains("replaceStatusOcc"),
                "click must rewrite the matching occurrence in raw textarea"
            );
            assert!(
                html.contains("data-task-occ"),
                "each status chip needs a stable occurrence index"
            );
            assert!(
                html.contains("md-status"),
                "missing clickable status chip class"
            );
            // Structure upgrades that stop the tracker from flattening.
            assert!(
                html.contains("md-table"),
                "GFM tables required for trackers"
            );
            assert!(
                html.contains("md-frontmatter"),
                "YAML frontmatter must render as a meta card, not a soup line"
            );
            assert!(
                html.contains(r#".join("<br>")"#),
                "paragraph newlines must become <br>, not spaces"
            );
            assert!(
                html.contains("markFormDirty"),
                "status click must mark editor-form dirty for save-on-close"
            );
            assert!(
                html.contains("statusRequestSeq"),
                "a stale chip response must not clear a newer local edit"
            );
            assert!(
                html.contains("return res.json()"),
                "a successful chip write must reconcile with canonical server bytes"
            );
        }

        #[test]
        fn selection_renders_searchable_plan_library_with_typed_editor_links() {
            let html = render_plan_picker(
                &[ScaffoldPlanCard {
                    plan: ScaffoldPlanSummary {
                        plan_id: "runtime-truth-v1".into(),
                        org: "vetcoders".into(),
                        repo: "vibecrafted".into(),
                        day: "2026_0727".into(),
                        plan_root: "/tmp/runtime-truth-v1".into(),
                        artifact_count: 12,
                        legacy_read_only: false,
                    },
                    reviewable: true,
                }],
                &[],
            );

            assert!(html.contains("Choose the truth"));
            assert!(html.contains("id=\"plan-search\""));
            assert!(html.contains(".normalize(\"NFD\")"));
            assert!(html.contains(
                "/scaffold?org=vetcoders&amp;repo=vibecrafted&amp;day=2026_0727&amp;plan_id=runtime-truth-v1"
            ));
            assert!(!html.contains("scaffold plan selection required"));
        }

        #[test]
        fn invalid_manifests_surface_in_plan_picker() {
            let skipped = [control_core::ScaffoldCatalogSkip {
                plan_root: "/tmp/plans/vc-server-mcp-slack-gateway".into(),
                reason: "manifest unreadable: unknown variant `mission`".into(),
                guessed_plan_id: Some("vc-server-mcp-slack-gateway".into()),
            }];
            let html = render_plan_picker(&[], &skipped);
            assert!(html.contains("Broken manifests"));
            assert!(html.contains("vc-server-mcp-slack-gateway"));
            assert!(html.contains("unknown variant `mission`"));
            assert!(html.contains("plan-card-invalid"));
        }

        #[test]
        fn blocked_plan_renders_doctor_truth_instead_of_dead_error_card() {
            let plan = ScaffoldPlanSummary {
                plan_id: "broken-plan".into(),
                org: "vetcoders".into(),
                repo: "vibecrafted".into(),
                day: "2026_0727".into(),
                plan_root: "/tmp/broken-plan".into(),
                artifact_count: 2,
                legacy_read_only: false,
            };
            let report = ScaffoldDoctorReport {
                valid: false,
                plan_id: plan.plan_id.clone(),
                plan_root: plan.plan_root.clone(),
                artifact_ids: vec!["driver".into()],
                errors: vec![control_core::ScaffoldDoctorError {
                    code: "frontmatter_missing".into(),
                    rule: Some("R11".into()),
                    artifact_id: Some("driver".into()),
                    path: Some("DRIVER.md".into()),
                    message: "frontmatter is required".into(),
                }],
            };
            let html = render_plan_blocked(&plan, Some(&report), "invalid manifest");

            assert!(html.contains("The plan exists."));
            assert!(html.contains("frontmatter_missing · R11"));
            assert!(html.contains("DRIVER.md"));
            assert!(html.contains("frontmatter is required"));
        }
    }
}
