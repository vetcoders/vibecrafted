#[cfg(feature = "ssr")]
pub mod api {
    use std::collections::{BTreeMap, BTreeSet};
    use std::path::{Component, Path, PathBuf};
    use std::process::{Command, Stdio};
    use std::time::Duration;

    use axum::extract::{Form, Query};
    use axum::http::{StatusCode, header};
    use axum::response::{Html, IntoResponse, Response};
    use axum::routing::{get, post};
    use axum::{Json, Router};
    use control_core::{
        ScaffoldArtifact, ScaffoldArtifactPatch, ScaffoldArtifactRole, ScaffoldArtifactStore,
        ScaffoldCheckpointPatch, ScaffoldDoctorReport, ScaffoldError, ScaffoldPlanSummary,
        ScaffoldStatusPatch, ScaffoldWorkspace, vibecrafted_home,
    };
    use serde::Deserialize;

    use crate::chrome::{ServerDocument, ServerSection, render_document};

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
    pub struct DispatchForm {
        pub org: String,
        pub repo: String,
        pub day: String,
        pub plan_id: String,
        pub artifact_id: String,
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
            .route("/artifacts", get(library))
            .route("/api/scaffold/plans", get(plans))
            .route("/api/scaffold/artifacts", get(artifacts))
            .route("/api/scaffold/export", get(export))
            .route("/api/scaffold/changes", get(changes))
            .route("/api/scaffold/artifact", post(save_artifact))
            .route("/api/scaffold/checkpoint", post(save_checkpoint))
            .route("/api/scaffold/status", post(save_status))
            .route("/api/scaffold/dispatch", post(dispatch_plan))
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

    const DISPATCH_BOOTSTRAP: &str =
        "from vibecrafted_core.cli import main; import sys; raise SystemExit(main(sys.argv[1:]))";

    async fn dispatch_plan(headers: axum::http::HeaderMap, body: String) -> impl IntoResponse {
        let is_json = headers
            .get(header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .map(|content_type| content_type.contains("application/json"))
            .unwrap_or(false);
        let form: DispatchForm = if is_json {
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
        let workspace = match store.workspace(&form.org, &form.repo, &form.day, Some(&form.plan_id))
        {
            Ok(workspace) => workspace,
            Err(error) => return scaffold_error_response(error),
        };
        let artifact = match workspace
            .artifacts
            .iter()
            .find(|artifact| artifact.id == form.artifact_id)
        {
            Some(artifact) => artifact.clone(),
            None => {
                return scaffold_error_response(ScaffoldError::ArtifactNotFound {
                    id: form.artifact_id,
                });
            }
        };
        let file = match dispatch_artifact_file(&workspace, &artifact) {
            Ok(file) => file,
            Err(error) => return scaffold_error_response(error),
        };
        let python = match generation_python() {
            Ok(python) => python,
            Err(error) => {
                return (
                    StatusCode::SERVICE_UNAVAILABLE,
                    Json(serde_json::json!({ "error": error })),
                )
                    .into_response();
            }
        };

        let doctor_python = python.clone();
        let doctor_file = file.clone();
        let doctor = tokio::time::timeout(
            Duration::from_secs(45),
            tokio::task::spawn_blocking(move || {
                run_dispatch_door(&doctor_python, &doctor_file, true)
            }),
        )
        .await;
        let doctor_output = match doctor {
            Ok(Ok(Ok(output))) => output,
            Ok(Ok(Err(error))) => {
                return (
                    StatusCode::SERVICE_UNAVAILABLE,
                    Json(serde_json::json!({
                        "error": format!("vibecrafted dispatch doctor failed to start: {error}")
                    })),
                )
                    .into_response();
            }
            Ok(Err(error)) => {
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(serde_json::json!({
                        "error": format!("vibecrafted dispatch doctor join failed: {error}")
                    })),
                )
                    .into_response();
            }
            Err(_) => {
                return (
                    StatusCode::GATEWAY_TIMEOUT,
                    Json(serde_json::json!({
                        "error": "vibecrafted dispatch doctor timed out"
                    })),
                )
                    .into_response();
            }
        };
        if !doctor_output.status.success() {
            let detail = [
                doctor_output.stderr.as_slice(),
                doctor_output.stdout.as_slice(),
            ]
            .into_iter()
            .find(|bytes| !bytes.is_empty())
            .map(|bytes| String::from_utf8_lossy(bytes).trim().to_string())
            .filter(|text| !text.is_empty())
            .unwrap_or_else(|| "dispatch doctor refused the plan".to_string());
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": detail,
                    "door": "vibecrafted dispatch",
                })),
            )
                .into_response();
        }

        let spawn_python = python.clone();
        let spawn_file = file.clone();
        match tokio::task::spawn_blocking(move || spawn_dispatch_door(&spawn_python, &spawn_file))
            .await
        {
            Ok(Ok(pid)) => (
                StatusCode::ACCEPTED,
                Json(serde_json::json!({
                    "status": "accepted",
                    "door": "vibecrafted dispatch",
                    "artifact_id": artifact.id,
                    "path": file.display().to_string(),
                    "pid": pid,
                })),
            )
                .into_response(),
            Ok(Err(error)) => (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(serde_json::json!({
                    "error": format!("vibecrafted dispatch failed to start: {error}")
                })),
            )
                .into_response(),
            Err(error) => (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({
                    "error": format!("vibecrafted dispatch join failed: {error}")
                })),
            )
                .into_response(),
        }
    }

    fn generation_python() -> Result<PathBuf, String> {
        let from_env = std::env::var("VIBECRAFTED_PYTHON")
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .map(PathBuf::from);
        let from_root = std::env::var("VIBECRAFTED_RUNTIME_ROOT")
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .map(|root| PathBuf::from(root).join("bin/python3"));
        let path = from_env.or(from_root).ok_or_else(|| {
            "generation Python is not available (set VIBECRAFTED_PYTHON or VIBECRAFTED_RUNTIME_ROOT)"
                .to_string()
        })?;
        if !path.is_absolute() {
            return Err("generation Python must be an absolute path".to_string());
        }
        let metadata = std::fs::metadata(&path)
            .map_err(|_| format!("generation Python is not available: {}", path.display()))?;
        if !metadata.is_file() {
            return Err(format!(
                "generation Python is not a file: {}",
                path.display()
            ));
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if metadata.permissions().mode() & 0o111 == 0 {
                return Err(format!(
                    "generation Python is not executable: {}",
                    path.display()
                ));
            }
        }
        Ok(path)
    }

    fn dispatch_artifact_file(
        workspace: &ScaffoldWorkspace,
        artifact: &ScaffoldArtifact,
    ) -> Result<PathBuf, ScaffoldError> {
        if artifact.role != ScaffoldArtifactRole::Dispatch {
            return Err(ScaffoldError::ReadOnly {
                message: format!(
                    "artifact is not a dispatch file: {} ({})",
                    artifact.id,
                    artifact.role.as_str()
                ),
            });
        }
        let relative = Path::new(&artifact.relative_path);
        if artifact.relative_path.is_empty()
            || relative.is_absolute()
            || artifact.relative_path.contains('\\')
            || !artifact.relative_path.ends_with(".dispatch.toml")
            || !relative
                .components()
                .all(|component| matches!(component, Component::Normal(_)))
        {
            return Err(ScaffoldError::UnsafePath {
                message: "refusing unsafe or non-dispatch scaffold artifact path".into(),
            });
        }
        let declared = PathBuf::from(&artifact.path);
        if !declared.is_absolute() {
            return Err(ScaffoldError::UnsafePath {
                message: "refusing unsafe scaffold dispatch path".into(),
            });
        }
        let plan_root = PathBuf::from(&workspace.plan_root)
            .canonicalize()
            .map_err(ScaffoldError::from)?;
        if dispatch_path_has_symlink(&plan_root, &declared) {
            return Err(ScaffoldError::UnsafePath {
                message: "refusing symlinked scaffold dispatch path".into(),
            });
        }
        let file = declared.canonicalize().map_err(ScaffoldError::from)?;
        if !file.starts_with(&plan_root) {
            return Err(ScaffoldError::UnsafePath {
                message: "refusing scaffold dispatch path outside the plan root".into(),
            });
        }
        if !file.is_file() {
            return Err(ScaffoldError::UnsafePath {
                message: "refusing missing scaffold dispatch file".into(),
            });
        }
        Ok(file)
    }

    fn dispatch_path_has_symlink(root: &Path, path: &Path) -> bool {
        let Ok(relative) = path.strip_prefix(root) else {
            return true;
        };
        let mut cursor = root.to_path_buf();
        if std::fs::symlink_metadata(&cursor)
            .is_ok_and(|metadata| metadata.file_type().is_symlink())
        {
            return true;
        }
        for component in relative.components() {
            cursor.push(component.as_os_str());
            if std::fs::symlink_metadata(&cursor)
                .is_ok_and(|metadata| metadata.file_type().is_symlink())
            {
                return true;
            }
        }
        false
    }

    fn dispatch_command(python: &Path, file: &Path, doctor: bool) -> Command {
        let mut command = Command::new(python);
        command.arg("-c").arg(DISPATCH_BOOTSTRAP).arg("dispatch");
        if doctor {
            command.arg("--doctor");
        }
        command.arg(file);
        command.env_remove("PYTHONPATH");
        command.stdin(Stdio::null());
        command
    }

    fn run_dispatch_door(
        python: &Path,
        file: &Path,
        doctor: bool,
    ) -> std::io::Result<std::process::Output> {
        dispatch_command(python, file, doctor).output()
    }

    fn spawn_dispatch_door(python: &Path, file: &Path) -> std::io::Result<u32> {
        let mut command = dispatch_command(python, file, false);
        command.stdout(Stdio::null());
        command.stderr(Stdio::null());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        let child = command.spawn()?;
        let pid = child.id();
        std::mem::forget(child);
        Ok(pid)
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

    /// One catalog row after alias paths that share `(org, repo, day, plan_id)`
    /// have been folded. The canonical display path wins; this does not walk
    /// the filesystem — callers pass `catalog_detailed` plans.
    pub(crate) struct ProjectShelfGroup {
        pub org: String,
        pub repo: String,
        pub last_activity: String,
        pub plans: Vec<ScaffoldPlanSummary>,
    }

    pub(crate) fn project_shelf(plans: &[ScaffoldPlanSummary]) -> Vec<ProjectShelfGroup> {
        let mut groups: BTreeMap<(String, String), ProjectShelfGroup> = BTreeMap::new();
        for plan in collapse_catalog(plans) {
            let key = (plan.org.clone(), plan.repo.clone());
            let group = groups.entry(key).or_insert_with(|| ProjectShelfGroup {
                org: plan.org.clone(),
                repo: plan.repo.clone(),
                last_activity: plan.day.clone(),
                plans: Vec::new(),
            });
            if plan.day > group.last_activity {
                group.last_activity = plan.day.clone();
            }
            group.plans.push(plan);
        }
        let mut rows: Vec<_> = groups.into_values().collect();
        rows.sort_by(|left, right| {
            right
                .last_activity
                .cmp(&left.last_activity)
                .then_with(|| left.org.cmp(&right.org))
                .then_with(|| left.repo.cmp(&right.repo))
        });
        for group in &mut rows {
            group.plans.sort_by(|left, right| {
                right
                    .day
                    .cmp(&left.day)
                    .then_with(|| left.plan_id.cmp(&right.plan_id))
            });
        }
        rows
    }

    fn collapse_catalog(plans: &[ScaffoldPlanSummary]) -> Vec<ScaffoldPlanSummary> {
        let mut best: BTreeMap<(String, String, String, String), ScaffoldPlanSummary> =
            BTreeMap::new();
        for plan in plans {
            let key = (
                plan.org.clone(),
                plan.repo.clone(),
                plan.day.clone(),
                plan.plan_id.clone(),
            );
            match best.get(&key) {
                Some(current)
                    if canonical_plan_display(current) || !canonical_plan_display(plan) => {}
                _ => {
                    best.insert(key, plan.clone());
                }
            }
        }
        best.into_values().collect()
    }

    fn canonical_plan_display(plan: &ScaffoldPlanSummary) -> bool {
        let root = plan.plan_root.replace('\\', "/");
        let needle = format!(
            "/artifacts/{}/{}/{}/plans/{}",
            plan.org, plan.repo, plan.day, plan.plan_id
        );
        root.contains(&needle)
    }

    pub(crate) fn plan_display_title(plan_id: &str) -> String {
        humanize_plan_id(plan_id)
    }

    pub(crate) fn scaffold_document_href(plan: &ScaffoldPlanSummary) -> String {
        format!(
            "/scaffold?org={}&repo={}&day={}&plan_id={}",
            url_component(&plan.org),
            url_component(&plan.repo),
            url_component(&plan.day),
            url_component(&plan.plan_id),
        )
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

    /// Every scaffold HTML state mounts inside the shared operator chrome
    /// (`chrome::ServerFrame`): one global sidebar, one navbar, one `<main>`.
    /// The studio owns only its canvas, its stylesheet and its scripts, so it
    /// can never replace the page or grow a second navigation vocabulary.
    fn studio_document(title: &str, status: &str, canvas: &str, scripts: &str) -> String {
        let head = format!("<style>{}</style>", editor_css());
        render_document(&ServerDocument {
            title,
            active: ServerSection::Scaffold,
            status,
            head_html: &head,
            body_html: canvas,
            tail_html: scripts,
        })
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
        let canvas = format!(
            r#"<div class="review-shell" data-first-artifact="{}">
  <nav class="review-sidebar" aria-label="Scaffold artifacts">
    <div class="review-sidebar-head">
      <div class="brand">Artifact index</div>
      <a class="review-library-link" href="/scaffold/library">All plans</a>
    </div>
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
    <div class="inspector-block" id="inspector-dispatch-slot">
      <h3>Dispatch</h3>
      <p class="inspector-hint">Switch to a dispatch artifact to run the real <code>vibecrafted dispatch</code> door.</p>
    </div>
    <div class="inspector-block">
      <h3>Endpoints</h3>
      <a class="api-link" href="/api/scaffold/artifacts?org={}&repo={}&day={}&plan_id={}" target="_blank" rel="noopener noreferrer">artifact endpoint ↗</a>
      <a class="api-link" href="/api/scaffold/changes?org={}&repo={}&day={}&plan_id={}" target="_blank" rel="noopener noreferrer">change endpoint ↗</a>
    </div>
  </aside>
</div>"#,
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
        );
        let scripts = format!(
            "{}\n{}\n{}",
            save_on_close_guard(),
            render_mode_script(),
            panel_nav_script()
        );
        studio_document(
            "scaffold review - vc-server",
            &format!("{approved} / {total} checkpointed"),
            &canvas,
            &scripts,
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
                        r#"<article class="plan-card plan-card-invalid" data-search="{}" data-ppm="plan" data-copy-id="{}">
  <span class="plan-number">!</span>
  <div class="plan-card-title">
    <h3>{}</h3>
    <p class="plan-skip-reason">{}</p>
  </div>
  <span class="plan-access">invalid</span>
  <p class="plan-skip-path"><code>{}</code></p>
</article>"#,
                        escape_attr(&format!("{} {}", id, skip.plan_root).to_ascii_lowercase()),
                        escape_attr(id),
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
    <h2 id="plan-invalid-title">Broken manifests ({})</h2>
  </div>
  <p class="plan-index-note">Failed to load under <code>…/plans/&lt;id&gt;/manifest.json</code>. Illegal role values like <code>"mission"</code> must be <code>"other"</code>.</p>
  <div class="plan-grid">{}</div>
</section>"#,
                skipped.len(),
                rows
            )
        };
        let canvas = format!(
            r#"<div class="plan-library">
  <header class="plan-index-head">
    <div class="plan-index-copy">
      <p class="eyebrow">Plans / Scaffold</p>
      <h1>Plans</h1>
    </div>
    <dl class="plan-index-stats">
      <div><dt>plans</dt><dd>{}</dd></div>
      <div><dt>repositories</dt><dd>{}</dd></div>
      <div><dt>reviewable</dt><dd>{}</dd></div>
      <div><dt>artifacts</dt><dd>{}</dd></div>
      <div><dt>invalid</dt><dd>{}</dd></div>
    </dl>
    <label class="plan-search">
      <span>Find a plan</span>
      <input id="plan-search" type="search" placeholder="repo, date, plan…" autocomplete="off">
      <kbd>/</kbd>
    </label>
  </header>

  <section class="plan-field" aria-labelledby="plan-field-title">
    <div class="plan-toolbar">
      <h2 id="plan-field-title">Manifest index</h2>
      <p class="result-count" aria-live="polite"><span id="visible-count">{}</span> visible</p>
    </div>
    <div class="plan-grid" id="plan-grid">{}</div>
    <div class="plan-no-results" id="plan-no-results" hidden>
      <p class="eyebrow">No match</p>
      <strong>No plan matches that search.</strong>
      <button type="button" id="clear-search">Clear search</button>
    </div>
  </section>
  {}
</div>"#,
            plans.len(),
            repositories,
            reviewable_count,
            artifact_count,
            skipped.len(),
            plans.len(),
            cards,
            invalid_band,
        );
        let status = match plans.len() {
            1 => "1 plan".to_string(),
            n => format!("{n} plans"),
        };
        studio_document(
            "scaffold plans - vc-server",
            &status,
            &canvas,
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
            r#"<a class="plan-card{}" href="{}" data-search="{}" data-ppm="plan" data-copy-id="{}" data-href="{}" data-focus-repo="{}">
  <span class="plan-number">{:02}</span>
  <div class="plan-card-title">
    <h3>{}</h3>
    <p>{} / {}</p>
  </div>
  <dl class="plan-card-meta">
    <div><dt>day</dt><dd>{}</dd></div>
    <div><dt>artifacts</dt><dd>{}</dd></div>
  </dl>
  <span class="plan-access">{}</span>
</a>"#,
            state_class,
            escape_attr(&href),
            escape_attr(&search.to_ascii_lowercase()),
            escape_attr(&plan.plan_id),
            escape_attr(&href),
            escape_attr(&plan.repo),
            index + 1,
            escape_html(&humanize_plan_id(&plan.plan_id)),
            escape_html(&plan.org),
            escape_html(&plan.repo),
            escape_html(&plan.day.replace('_', " · ")),
            plan.artifact_count,
            access,
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
        let canvas = format!(
            r#"<div class="blocked-plan-shell">
  <p class="blocked-plan-nav"><a class="back-link" href="/scaffold/library">← All plans</a></p>
  <header class="blocked-plan-head">
    <div>
      <p class="eyebrow">{}/{}</p>
      <h1>{}</h1>
    </div>
    <span class="blocked-pill">{} contract issues</span>
  </header>
  <div class="blocked-plan-grid">
    <section class="blocked-explainer">
      <p class="eyebrow">Contract</p>
      <h2>Cannot open this plan</h2>
      <p>The manifest is indexed, but its artifact contract failed. Repair the findings below and the plan becomes reviewable.</p>
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
</div>"#,
            escape_html(&plan.org),
            escape_html(&plan.repo),
            escape_html(&humanize_plan_id(&plan.plan_id)),
            issue_count,
            escape_html(&plan.day.replace('_', " · ")),
            plan.artifact_count,
            escape_html(&plan.plan_root),
            issue_count,
            issues,
        );
        studio_document(
            "scaffold plan blocked - vc-server",
            &format!("{issue_count} contract issues"),
            &canvas,
            "",
        )
    }

    fn humanize_plan_id(plan_id: &str) -> String {
        plan_id
            .split(['-', '_'])
            .filter(|part| !part.is_empty())
            .filter(|part| !part.eq_ignore_ascii_case("truth"))
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
      card.setAttribute("data-search-hit", match ? "1" : "0");
      if (match) visible += 1;
    });
    count.textContent = String(visible);
    empty.hidden = visible !== 0;
    document.documentElement.dispatchEvent(new Event("vc-focus-refresh"));
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

    /// Empty / unavailable state. It is a normal route page inside the shared
    /// chrome (same header and panel vocabulary as the Leptos pages), so an
    /// operator with no plans still has the sidebar, Home and the library.
    fn render_empty(message: &str) -> String {
        let canvas = format!(
            r#"<div class="server-console-shell route-page-shell scaffold-empty">
  <section class="run-detail-header route-page-header">
    <div>
      <p class="section-eyebrow">Plans / Scaffold</p>
      <h1 class="run-detail-title">Scaffold review</h1>
      <p class="route-page-description">{}</p>
    </div>
  </section>
  <section class="control-panel control-panel-wide" aria-label="Scaffold empty state">
    <div class="control-panel-head"><h2>No plan to open</h2><span>scaffold</span></div>
    <p class="control-empty">Manifest-backed plans appear here as soon as a scaffold package lands under <code>artifacts/&lt;org&gt;/&lt;repo&gt;/&lt;day&gt;/plans/&lt;plan_id&gt;/manifest.json</code> in the runtime home.</p>
    <p class="server-console-links"><a class="server-console-link server-console-link-primary" href="/scaffold/library">Open plan library</a><a class="server-console-link" href="/">Back to overview</a></p>
  </section>
</div>"#,
            escape_html(message)
        );
        studio_document(
            "scaffold review - vc-server",
            "no plan selected",
            &canvas,
            "",
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
        let dispatch_form = if artifact.role == ScaffoldArtifactRole::Dispatch {
            format!(
                r#"<form method="post" action="/api/scaffold/dispatch" class="dispatch-form">
    {}
    <p class="inspector-hint">Runs the real <code>vibecrafted dispatch</code> door after doctor. Closing this studio does not stop workers.</p>
    <button type="submit">Dispatch</button>
    <p class="dispatch-status inspector-meta" aria-live="polite"></p>
  </form>"#,
                hidden_context(workspace, artifact)
            )
        } else {
            String::new()
        };
        // Default view is formatted rich markdown. "Edit" opens the mono
        // source textarea; "Save" persists (if dirty) and returns to rich.
        // Only the active panel is visible (studio shell — one document).
        format!(
            r#"<article class="artifact-panel{}" id="{}" data-role="{}" data-render-mode="rich"{} aria-hidden="{}">
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
  {}
</article>"#,
            active_class,
            escape_attr(&artifact.id),
            escape_attr(artifact.role.as_str()),
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
            escape_attr(&artifact.checkpoint.note),
            dispatch_form
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
    fn render_mode_script() -> String {
        r#"<script>
(function () {
  // Tracker legend order (runtime-enclave style). Click advances one step —
  // same rolling-state affordance as Codescribe tray Auto Format.
  var STATUS_CYCLE = [" ", "~", "?", "!", "x"];
  var STATUS_META = {
    " ": { label: "todo", glyph: "\u00a0" },
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

"#.to_string() + include_str!("markdown.js") + r#"
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

    var dispatchSlot = document.getElementById("inspector-dispatch-slot");
    if (dispatchSlot) {
      var parkedDispatch = dispatchSlot.querySelector("form.dispatch-form");
      if (parkedDispatch && parkedDispatch.dataset.homePanel) {
        var dispatchHome = document.getElementById(parkedDispatch.dataset.homePanel);
        if (dispatchHome) dispatchHome.appendChild(parkedDispatch);
      }
      dispatchSlot.innerHTML = "<h3>Dispatch</h3>";
      var dispatchForm = panel.querySelector("form.dispatch-form");
      if (dispatchForm) {
        dispatchForm.dataset.homePanel = panel.id;
        dispatchSlot.appendChild(dispatchForm);
        if (dispatchForm.dataset.dispatchBound !== "1") {
          dispatchForm.dataset.dispatchBound = "1";
          dispatchForm.addEventListener("submit", function (ev) {
            ev.preventDefault();
            var statusEl = dispatchForm.querySelector(".dispatch-status");
            var payload = {};
            Array.prototype.slice.call(dispatchForm.querySelectorAll("input[name]")).forEach(function (input) {
              payload[input.name] = input.value;
            });
            if (statusEl) statusEl.textContent = "Doctoring…";
            fetch("/api/scaffold/dispatch", {
              method: "POST",
              headers: { "Content-Type": "application/json", "Accept": "application/json" },
              credentials: "same-origin",
              body: JSON.stringify(payload)
            }).then(function (res) {
              return res.json().then(function (body) { return { res: res, body: body }; });
            }).then(function (pair) {
              if (!statusEl) return;
              if (pair.res.status === 202) {
                statusEl.textContent = "Accepted. Workers keep running if you close this tab. Watch the tracker.";
              } else {
                statusEl.textContent = (pair.body && pair.body.error)
                  ? pair.body.error
                  : ("Dispatch refused (" + pair.res.status + ")");
              }
            }).catch(function (err) {
              if (statusEl) statusEl.textContent = "Dispatch unavailable: " + err.message;
            });
          });
        }
      } else {
        dispatchSlot.insertAdjacentHTML("beforeend", '<p class="inspector-hint">This artifact is not a dispatch file. Open the dispatch artifact to run <code>vibecrafted dispatch</code>.</p>');
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
        include_str!("studio.css")
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
            // Plan-level "All plans" lives inside the studio canvas; global
            // routes come from the shared frame, never from a studio navbar.
            assert!(html.contains(r#"class="review-library-link" href="/scaffold/library""#));
            assert!(html.contains("All plans"));
            assert!(html.contains(r##""#artifact/" + encodeURIComponent(panel.id)"##));
            assert_shared_server_frame(&html, "editor");
        }

        fn count(haystack: &str, needle: &str) -> usize {
            haystack.matches(needle).count()
        }

        /// One shared canvas: every scaffold HTML state is a route page inside
        /// `chrome::ServerFrame` — one navbar, one global sidebar, one `<main>`,
        /// Plans active, Home reachable, no second chrome vocabulary.
        fn assert_shared_server_frame(html: &str, state: &str) {
            assert!(
                html.starts_with("<!DOCTYPE html>"),
                "{state}: full document"
            );
            assert!(
                html.contains("<title>scaffold "),
                "{state}: route-owned title"
            );
            assert_eq!(
                count(html, r#"class="server-navbar""#),
                1,
                "{state}: one navbar"
            );
            assert_eq!(
                count(html, r#"class="server-sidebar""#),
                1,
                "{state}: one global sidebar"
            );
            assert_eq!(
                count(html, r#"class="server-mobile-nav""#),
                1,
                "{state}: one mobile nav"
            );
            assert_eq!(
                count(html, "<main"),
                1,
                "{state}: the frame owns the only <main>"
            );
            assert!(
                html.contains(
                    r#"<main class="server-route-main"><div class="server-route-document">"#
                ),
                "{state}: canvas mounts inside the frame main"
            );
            assert_eq!(
                count(
                    html,
                    r#"href="/artifacts" class="server-nav-link is-active""#
                ),
                2,
                "{state}: Artifacts active in sidebar and mobile nav"
            );
            assert!(
                html.contains(r#"href="/" aria-label="Vibecrafted server overview""#),
                "{state}: brand link is the browser Home"
            );
            assert!(
                html.contains(r#"class="server-theme-toggle""#),
                "{state}: theme toggle"
            );
            assert!(
                html.contains("server-theme-toggle');"),
                "{state}: theme control script"
            );
            assert!(
                html.contains(".review-shell{display:grid;grid-template-columns:280px"),
                "{state}: studio stylesheet is route-owned"
            );
            for dead in [
                "studio-app-shell",
                "studio-navbar",
                "studio-global-nav",
                "studio-back-link",
                "library-nav",
                "library-mode",
                r#"target="_top""#,
                "<body><main",
            ] {
                assert!(
                    !html.contains(dead),
                    "{state}: duplicate chrome vocabulary `{dead}`"
                );
            }
        }

        #[test]
        fn every_scaffold_html_state_shares_the_server_frame() {
            let plan = ScaffoldPlanSummary {
                plan_id: "runtime-truth-v1".into(),
                org: "vetcoders".into(),
                repo: "vibecrafted".into(),
                day: "2026_0727".into(),
                plan_root: "/tmp/runtime-truth-v1".into(),
                artifact_count: 12,
                legacy_read_only: false,
            };

            let editor = render_editor(&fixture());
            assert_shared_server_frame(&editor, "editor");
            assert!(editor.contains("1 / 1 checkpointed") || editor.contains("0 / 1 checkpointed"));

            let library = render_plan_picker(
                &[ScaffoldPlanCard {
                    plan: plan.clone(),
                    reviewable: true,
                }],
                &[],
            );
            assert_shared_server_frame(&library, "library");
            assert!(library.contains(r#"class="plan-library""#));
            assert!(library.contains(">1 plan</span>"));

            let empty = render_plan_picker(&[], &[]);
            assert_shared_server_frame(&empty, "empty");
            assert!(empty.contains("No manifest-backed scaffold plans are available."));
            assert!(
                empty.contains(r#"class="server-console-shell route-page-shell scaffold-empty""#)
            );
            assert!(empty.contains(r#"href="/scaffold/library""#));

            let blocked = render_plan_blocked(&plan, None, "invalid manifest");
            assert_shared_server_frame(&blocked, "blocked");
            assert!(blocked.contains("Cannot open this plan"));
            assert!(blocked.contains(r#"class="back-link" href="/scaffold/library""#));

            let unavailable = render_empty("Scaffold artifacts unavailable: <boom>");
            assert_shared_server_frame(&unavailable, "unavailable");
            assert!(unavailable.contains("Scaffold artifacts unavailable: &lt;boom&gt;"));
            assert!(!unavailable.contains("<boom>"));
        }

        #[test]
        fn editor_keeps_artifact_index_inside_the_canvas_with_one_active_document() {
            let mut workspace = fixture();
            let mut second = workspace.artifacts[0].clone();
            second.id = "tracker".into();
            second.title = "Tracker".into();
            second.role = ScaffoldArtifactRole::Tracker;
            workspace.artifacts.push(second);
            let html = render_editor(&workspace);

            // Global sidebar closes before the studio canvas opens, and the
            // artifact tabs sit inside that canvas — never in the global nav.
            let sidebar_start = html
                .find(r#"class="server-sidebar""#)
                .expect("global sidebar");
            let sidebar_end =
                html[sidebar_start..].find("</aside>").expect("sidebar end") + sidebar_start;
            let canvas = html
                .find(r#"class="server-route-document""#)
                .expect("canvas");
            let tabs = html
                .find(r#"class="tabs" role="tablist""#)
                .expect("artifact tabs");
            assert!(
                sidebar_end < canvas && canvas < tabs,
                "artifact index must live inside the canvas"
            );
            let sidebar = &html[sidebar_start..sidebar_end];
            assert!(!sidebar.contains("Master Dispatch") && !sidebar.contains("Tracker"));
            assert!(!sidebar.contains("Artifact index"));

            // One active document; the second panel stays hidden.
            assert_eq!(count(&html, r#"class="artifact-panel is-active""#), 1);
            assert_eq!(count(&html, r#"class="artifact-panel" id="tracker""#), 1);
            assert_eq!(count(&html, r#"class="review-inspector""#), 1);
            assert_eq!(count(&html, r#"class="review-statusbar""#), 1);
            // Machine endpoints open beside the studio (native host reference tab).
            assert!(
                html.contains(r#"target="_blank" rel="noopener noreferrer">artifact endpoint"#)
            );
            assert!(html.contains(r#"target="_blank" rel="noopener noreferrer">change endpoint"#));
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

        /// The supplied regression: in a narrow tracker column the `todo` chip
        /// rendered `[` and `]` on separate lines. The glyph was the only one
        /// carrying an internal space, and nothing stopped the flex items from
        /// shrinking below their content.
        #[test]
        fn status_chip_stays_one_unbreakable_control_in_a_narrow_column() {
            let html = render_editor(&fixture());

            assert!(
                html.contains("white-space:nowrap;overflow-wrap:normal;word-break:normal"),
                "the chip itself must never wrap, whatever the column width"
            );
            assert!(
                html.contains(
                    "button.md-status{display:inline-flex;align-items:center;flex:0 0 auto;"
                ),
                "the chip must not shrink below its content inside a table cell"
            );
            assert!(
                html.contains("button.md-status .md-status-glyph{flex:0 0 auto;white-space:pre;"),
                "the bracket glyph must be non-shrinking and keep its literal spacing"
            );
            assert!(
                html.contains(
                    "button.md-status .md-status-label{flex:0 0 auto;white-space:nowrap;"
                ),
                "the state label must not wrap away from its glyph"
            );
            // Belt and braces: even with the stylesheet stripped, the todo glyph
            // no longer offers a break opportunity between its brackets.
            assert!(
                html.contains(r#"" ": { label: "todo", glyph: "\u00a0" }"#),
                "todo must render a non-breaking space between its brackets"
            );
            assert!(
                !html.contains(r#"" ": { label: "todo", glyph: " " }"#),
                "a bare space between brackets is what split [ and ] across lines"
            );
        }

        /// Every state, not just the one that happened to break, is a cohesive
        /// control; and state colour rides the glyph so the label stays legible
        /// on the light theme too.
        #[test]
        fn every_status_state_is_a_cohesive_readable_control() {
            let html = render_editor(&fixture());

            for (state, token) in [
                ("todo", "var(--muted)"),
                ("run", "var(--status-warning)"),
                ("maybe", "var(--status-info)"),
                ("blocked", "var(--status-danger)"),
                ("done", "var(--status-success)"),
            ] {
                assert!(
                    html.contains(&format!(
                        "button.md-status.md-status-{state} .md-status-glyph{{color:{token}}}"
                    )),
                    "state {state} must carry its colour on the glyph"
                );
            }

            // The old palette coloured the *label*, using accents that collapsed
            // to a neutral grey — invisible on the light theme.
            assert!(
                !html.contains("color:var(--amber);background:rgba(216,166,64,.08)"),
                "stale chip palette must not come back"
            );
            assert!(
                !html.contains("border-color:rgba(169,177,180,.35)"),
                "stale chip palette must not come back"
            );
        }

        /// Presentation changed; the source markers and the save path did not.
        #[test]
        fn status_presentation_change_leaves_the_persistence_contract_intact() {
            let html = render_editor(&fixture());

            assert!(
                html.contains(r#"STATUS_CYCLE = [" ", "~", "?", "!", "x"]"#),
                "the cycle still moves through the real source markers"
            );
            assert!(
                html.contains("([ xX~!?])"),
                "the source-marker scanner must still match the raw tracker tokens"
            );
            assert!(
                html.contains("replaceStatusOcc"),
                "a click still rewrites the matching occurrence in the raw textarea"
            );
            assert!(
                html.contains(r#"chip.getAttribute("data-mark")"#),
                "the next mark is derived from the real marker, never from the glyph"
            );
            assert!(
                html.contains("markFormDirty"),
                "checkpoint/save dirty tracking is unchanged"
            );
            assert!(
                html.contains("statusRequestSeq"),
                "stale-response reconciliation is unchanged"
            );
        }

        /// Founder direction: keep the light theme. These surfaces were pinned to
        /// dark slabs, so light mode rendered near-black text on a dark card.
        #[test]
        fn studio_surfaces_follow_the_theme_contract_not_hardcoded_dark_slabs() {
            let html = render_editor(&fixture());

            assert!(
                !html.contains("background:rgba(27,31,32,.85)"),
                "the frontmatter card must not be pinned to a dark slab"
            );
            assert!(
                !html.contains("background:#162114") && !html.contains("background:#2b1717"),
                "save/error banners must not be pinned to dark fills"
            );
            assert!(
                html.contains("letter-spacing:.06em;background:var(--bg);position:sticky"),
                "a sticky header needs an opaque surface; --panel is alpha on light"
            );
            assert!(
                html.contains(".md-table tr:hover td{background:var(--btn-bg-hover)}"),
                "row hover must use the themed hover token, not white alpha"
            );
        }

        /// Parity with the native command deck, which already thickens its stroke
        /// at increased contrast and honours reduced motion.
        #[test]
        fn status_chip_honours_reduced_motion_and_increased_contrast() {
            let html = render_editor(&fixture());

            assert!(
                html.contains(
                    "@media (prefers-reduced-motion: reduce){button.md-status{transition:none}}"
                ),
                "chip motion must be opt-out"
            );
            assert!(
                html.contains(
                    "@media (prefers-contrast: more){button.md-status{border-width:1.5px}}"
                ),
                "increased contrast must thicken the chip stroke, as the native deck does"
            );
            assert!(
                html.contains("transition:border-color var(--motion-fast) var(--ease-ui)"),
                "chip motion must come from the shared motion dictionary"
            );
            assert!(
                html.contains("button.md-status:focus-visible{outline:2px solid var(--focus-ring)"),
                "keyboard focus must stay visible on both themes"
            );
        }

        /// Route-local adoption. The studio used to carry a fossil palette of a
        /// previous brand (teal glows, lime code, two hand-mixed greens) beside
        /// the shared tokens. Colour here must now come from the same semantic
        /// layer the native deck feeds.
        ///
        /// Scope: this asserts the stylesheet the studio *owns*. The rendered
        /// document also embeds `chrome::STYLE_MAIN`, whose first ~1400 lines
        /// are the marketing site (hero, blog, pricing) and carry one
        /// `linear-gradient` on `.blog-post-header` — a selector no console
        /// route ever matches. Asserting over the whole document measured that
        /// dead sheet instead of this one; the shared layer has its own owner
        /// in `chrome::tests`.
        #[test]
        fn studio_styling_converges_on_the_shared_deck_tokens() {
            let css = editor_css();

            for fossil in [
                "#5e7f47",
                "#22321f",          // hand-mixed checkpoint green
                "#4d7041",          // hand-mixed done green
                "rgba(184,239,125", // lime accent of a previous brand
                "rgba(77,155,142",  // teal accent of a previous brand
                "rgba(43,48,51",    // slab rule
                "#1b1914",
                "#1b1617", // slab card fills
            ] {
                assert!(
                    !css.contains(fossil),
                    "studio css still carries the fossil literal {fossil}"
                );
            }

            assert!(
                !css.contains("radial-gradient") && !css.contains("linear-gradient"),
                "the plan library is an index, not a landing page"
            );
            assert!(
                !css.contains("clamp(48px")
                    && !css.contains("102px")
                    && !css.contains("translateY(-")
                    && !css.contains("min-height:290px")
                    && !css.contains("min-height:230px"),
                "plan index must not keep landing-page type or magazine-card lift"
            );
            assert!(
                css.contains(".plan-index-copy h1{")
                    && css.contains("font:650 var(--deck-title)/1.2 var(--font-body)"),
                "plan index title must use the native deck title scale"
            );
            assert!(
                css.contains("--accent:var(--amber)"),
                "the studio bridge must point at the live accent, not plain text"
            );
            assert!(
                css.contains("box-shadow:inset 0 0 0 2px var(--focus-ring)"),
                "the raw editor focus ring must use the corrected global token"
            );
            assert!(
                render_editor(&fixture()).contains(css),
                "the studio must actually ship the stylesheet this test measures"
            );
        }

        /// The shared sheet installs one focus owner —
        /// `.server-app-shell :focus-visible` (`main.css`) — and the studio
        /// canvas renders inside that shell, so the ring should reach every
        /// control here for free. It did not: `editor_css()` is embedded in
        /// `head_html`, i.e. *after* the shared sheet, and these four rules
        /// carried `outline:none` at exactly equal specificity (0,2,0), so the
        /// later declaration won and the ring was suppressed in both themes.
        /// A border tint is not a focus indicator; `main.css` says so itself.
        ///
        /// The two suppressions that remain in the studio are deliberate and
        /// keep their own replacement: the raw textarea swaps the outline for
        /// an inset ring, and the tracker chip re-declares a real one.
        #[test]
        fn studio_controls_never_suppress_the_shared_focus_ring() {
            let css = editor_css();

            for control in [
                ".plan-card:hover,.plan-card:focus-visible{",
                ".review-library-link:hover,.review-library-link:focus-visible{",
                ".tab:hover,.tab:focus{",
                "button.render-mode-btn:hover,button.render-mode-btn:focus-visible{",
            ] {
                let mut seen = 0;
                for (at, _) in css.match_indices(control) {
                    let rest = &css[at..];
                    let end = rest.find('}').expect("every studio rule is closed");
                    assert!(
                        !rest[..end].contains("outline"),
                        "{control} suppresses the one focus ring the shell installs"
                    );
                    seen += 1;
                }
                assert!(seen > 0, "{control} vanished — re-point this contract");
            }

            // Deliberate, and each still shows focus.
            assert!(
                css.contains(
                    ".editor-form textarea.raw-pane:focus{outline:none;box-shadow:inset 0 0 0 2px var(--focus-ring)}"
                ),
                "the raw editor must keep its inset focus affordance"
            );
            assert!(
                css.contains(
                    "button.md-status:focus-visible{outline:2px solid var(--focus-ring);outline-offset:2px}"
                ),
                "the tracker chip must re-declare the ring it suppressed"
            );
        }

        #[test]
        fn studio_clickable_links_keep_the_arrow_cursor() {
            let css = editor_css();
            assert!(
                !css.contains("cursor:pointer"),
                "the studio must not transform the pointer into a hand"
            );
            assert!(
                css.contains(":where(.server-route-document) a{color:inherit;cursor:default}"),
                "studio links inherit the native arrow"
            );
        }

        /// Founder named this one directly: the checkpoint button was a pair of
        /// hand-mixed greens that broke in light theme. It stays green (commit
        /// semantics) but through --status-success, in the very same color-mix
        /// shape the status chips already use — one owner, not a second dialect.
        #[test]
        fn checkpoint_and_save_actions_read_from_the_semantic_palette() {
            let css = editor_css();
            const SUCCESS_EDGE: &str = "color-mix(in srgb,var(--status-success) 55%,transparent)";

            // Named owners, not a pinned total. Three surfaces mark "done" —
            // the sidebar tab, the inspector pill and the tracker chip — and
            // two commit actions carry the stroke: checkpoint and save. The
            // tracker chip joined in 00f03ce6, so a hard-coded "4" described a
            // studio that no longer existed. Splitting the count by the shape
            // each owner uses keeps this honest: a new dialect still fails.
            let done_edge = format!("border-color:{SUCCESS_EDGE}");
            let action_stroke = format!("border:var(--stroke-width) solid {SUCCESS_EDGE}");
            assert_eq!(
                css.matches(done_edge.as_str()).count(),
                3,
                "tab, inspector pill and tracker chip mark done with one edge"
            );
            assert_eq!(
                css.matches(action_stroke.as_str()).count(),
                2,
                "checkpoint and save carry the same success stroke"
            );
            assert_eq!(
                css.matches(SUCCESS_EDGE).count(),
                5,
                "five owners total — nothing else may mint a sixth success dialect"
            );
            assert_eq!(
                css.matches("background:color-mix(in srgb,var(--status-success) 14%,transparent)")
                    .count(),
                2,
                "checkpoint and save carry the same fill weight"
            );
            assert!(
                css.contains(
                    "border-radius:var(--radius-surface);padding:8px 12px;font-weight:700"
                ),
                "the commit action takes the deck's 8px radius"
            );
        }

        #[test]
        fn dispatch_artifact_ships_the_real_door_form() {
            let mut workspace = fixture();
            workspace.artifacts[0].id = "wave".into();
            workspace.artifacts[0].role = ScaffoldArtifactRole::Dispatch;
            workspace.artifacts[0].relative_path = "plan.dispatch.toml".into();
            let html = render_editor(&workspace);
            assert!(
                html.contains(r#"data-role="dispatch""#),
                "dispatch panel must declare its role"
            );
            assert!(
                html.contains(r#"action="/api/scaffold/dispatch""#),
                "inspector must post the real dispatch door"
            );
            assert!(
                html.contains("vibecrafted dispatch"),
                "copy must name the product CLI, not a fake launch button"
            );
            assert!(html.contains("inspector-dispatch-slot"));
            assert!(html.contains("Closing this studio does not stop workers"));
        }

        /// The repaint must not have touched a single byte of persistence. This
        /// asserts the write path end to end: source markers, the cycle, the
        /// request reconciliation, the save form and the checkpoint note field.
        #[test]
        fn the_repaint_left_every_write_path_untouched() {
            let html = render_editor(&fixture());

            for contract in [
                "replaceStatusOcc", // rewrites the raw markdown occurrence
                "statusRequestSeq", // reconciles out-of-order status POSTs
                "STATUS_CYCLE",     // the state machine, unchanged
                "data-mark",        // the source marker read back off the DOM
                "nextMark",
                "save-artifact-btn",
                "checkpoint-form",
                "name=note", // the checkpoint note actually posted
            ] {
                assert!(
                    html.contains(contract),
                    "presentation change dropped the persistence contract {contract}"
                );
            }
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

            assert!(html.contains("<h1>Plans</h1>"));
            assert!(!html.contains("Choose the truth"));
            assert!(!html.contains("Runtime Truth"));
            assert!(!html.contains("you want to move"));
            assert!(html.contains("data-ppm=\"plan\""));
            assert!(html.contains("data-focus-repo=\"vibecrafted\""));
            assert!(html.contains("vc-focus-refresh"));
            assert!(!html.contains("Plan control room"));
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
            assert!(html.contains("data-ppm=\"plan\""));
            assert!(html.contains("data-copy-id=\"vc-server-mcp-slack-gateway\""));
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

            assert!(html.contains("Cannot open this plan"));
            assert!(!html.contains("The editor refuses to lie"));
            assert!(html.contains("frontmatter_missing · R11"));
            assert!(html.contains("DRIVER.md"));
            assert!(html.contains("frontmatter is required"));
        }
    }
}
