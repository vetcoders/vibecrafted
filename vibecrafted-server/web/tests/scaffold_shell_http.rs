//! Route-level rendering of every scaffold HTML state through the real axum
//! router: empty runtime home, populated plan, blocked plan, unknown plan.
//!
//! Each state must ship inside the shared operator chrome (one navbar, one
//! global sidebar, one `<main>`), while the `/api/scaffold/*` endpoints stay
//! JSON. Every rendered document is also written to
//! `$CARGO_TARGET_TMPDIR/scaffold-shell/<state>.html` as a screenshot-ready
//! fixture (inline CSS, no network) for visual verification.

#![cfg(feature = "ssr")]

use std::fs;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode, header};
use leptos::config::{Env, LeptosOptions};
use tower::ServiceExt;
use vibecrafted_server_web::scaffold::api::scaffold_routes;

static ENV_LOCK: Mutex<()> = Mutex::new(());

/// Isolated runtime home: both `HOME` and `VIBECRAFTED_HOME` point at a fresh
/// directory under the crate's own build output (`CARGO_TARGET_TMPDIR`), so
/// the test never reads the developer's plans and never touches a shared
/// system temp directory.
struct TempHome {
    path: PathBuf,
    _guard: std::sync::MutexGuard<'static, ()>,
}

impl TempHome {
    fn new(label: &str) -> Self {
        let guard = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let path = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!(
            "scaffold-shell-home-{}-{}-{}",
            label,
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        fs::create_dir_all(&path).expect("create temp home");
        unsafe {
            std::env::set_var("HOME", &path);
            std::env::set_var("VIBECRAFTED_HOME", &path);
        }
        Self {
            path,
            _guard: guard,
        }
    }

    fn plan_dir(&self, org: &str, repo: &str, day: &str, plan_id: &str) -> PathBuf {
        self.path
            .join("artifacts")
            .join(org)
            .join(repo)
            .join(day)
            .join("plans")
            .join(plan_id)
    }

    /// A reviewable manifest-backed plan with two artifacts.
    fn setup_plan(&self, org: &str, repo: &str, day: &str, plan_id: &str) -> PathBuf {
        let plan_dir = self.plan_dir(org, repo, day, plan_id);
        fs::create_dir_all(&plan_dir).expect("create plan dir");
        let manifest = serde_json::json!({
            "schema_version": "1",
            "org": org,
            "repo": repo,
            "day": day,
            "plan_id": plan_id,
            "created_at": "2026-09-08T08:00:00Z",
            "artifacts": [
                {"id": "driver", "path": "DRIVER.md", "role": "driver", "editable": true, "required": true},
                {"id": "tracker", "path": "tracker.md", "role": "tracker", "editable": true, "required": true}
            ]
        });
        fs::write(
            plan_dir.join("manifest.json"),
            serde_json::to_string_pretty(&manifest).unwrap(),
        )
        .expect("write manifest");
        fs::write(
            plan_dir.join("DRIVER.md"),
            format!(
                "---\nplan_id: {plan_id}\nsession_id: shell-test\nrole: driver\nagent: claude\ndate: 2026-09-08\nproject: {org}/{repo}\n---\n\n# Driver\n\nOne shared canvas, one active document.\n"
            ),
        )
        .expect("write DRIVER.md");
        fs::write(
            plan_dir.join("tracker.md"),
            format!(
                "---\nplan_id: {plan_id}\nsession_id: shell-test\nrole: tracker\nagent: claude\ndate: 2026-09-08\nproject: {org}/{repo}\n---\n\n# Tracker\n\n- [ ] T1 Keep the sidebar\n- [x] T2 Keep one document\n"
            ),
        )
        .expect("write tracker.md");
        plan_dir
    }

    /// A manifest whose declared artifact file is missing: indexed, not reviewable.
    fn setup_blocked_plan(&self, org: &str, repo: &str, day: &str, plan_id: &str) -> PathBuf {
        let plan_dir = self.plan_dir(org, repo, day, plan_id);
        fs::create_dir_all(&plan_dir).expect("create plan dir");
        let manifest = serde_json::json!({
            "schema_version": "1",
            "org": org,
            "repo": repo,
            "day": day,
            "plan_id": plan_id,
            "created_at": "2026-09-08T08:00:00Z",
            "artifacts": [
                {"id": "driver", "path": "DRIVER.md", "role": "driver", "editable": true, "required": true}
            ]
        });
        fs::write(
            plan_dir.join("manifest.json"),
            serde_json::to_string_pretty(&manifest).unwrap(),
        )
        .expect("write manifest");
        plan_dir
    }
}

fn test_app() -> axum::Router {
    let opts = LeptosOptions::builder()
        .output_name("vibecrafted-server-web-test")
        .site_root("target/site-test")
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr("127.0.0.1:0".parse::<SocketAddr>().expect("addr"))
        .reload_port(0)
        .build();
    axum::Router::new()
        .merge(scaffold_routes())
        .with_state(opts)
}

async fn get(app: &axum::Router, uri: &str) -> (StatusCode, String, String) {
    let response = app
        .clone()
        .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
        .await
        .expect("request");
    let status = response.status();
    let content_type = response
        .headers()
        .get(header::CONTENT_TYPE)
        .map(|v| v.to_str().unwrap_or_default().to_string())
        .unwrap_or_default();
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("body");
    (
        status,
        content_type,
        String::from_utf8_lossy(&bytes).into_owned(),
    )
}

fn count(haystack: &str, needle: &str) -> usize {
    haystack.matches(needle).count()
}

/// Screenshot-ready fixture: the document is self-contained (inline CSS and
/// scripts), so `open target/tmp/scaffold-shell/<state>.html` or a headless
/// browser on the `file://` URL shows exactly what the route served.
fn write_fixture(state: &str, html: &str) -> PathBuf {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("scaffold-shell");
    fs::create_dir_all(&dir).expect("fixture dir");
    let path = dir.join(format!("{state}.html"));
    fs::write(&path, html).expect("write fixture");
    println!("scaffold shell fixture: {}", path.display());
    path
}

/// The shared-canvas contract every scaffold HTML response must satisfy.
fn assert_shared_chrome(state: &str, content_type: &str, html: &str) {
    assert!(
        content_type.starts_with("text/html"),
        "{state}: HTML content type, got {content_type}"
    );
    assert!(
        html.starts_with("<!DOCTYPE html>"),
        "{state}: full document"
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
        count(html, "<main"),
        1,
        "{state}: frame owns the only <main>"
    );
    assert!(
        html.contains(r#"<main class="server-route-main"><div class="server-route-document">"#),
        "{state}: canvas inside frame main"
    );
    assert_eq!(
        count(
            html,
            r#"href="/scaffold" class="server-nav-link is-active""#
        ),
        2,
        "{state}: Plans / Scaffold active in sidebar + mobile nav"
    );
    assert!(
        html.contains(r#"href="/" aria-label="Vibecrafted server overview""#),
        "{state}: Home"
    );
    assert!(
        html.contains(r#"href="/runs" class="server-nav-link""#),
        "{state}: global routes"
    );
    assert!(
        html.contains(r#"class="server-theme-toggle""#),
        "{state}: theme toggle"
    );
    for dead in [
        "studio-navbar",
        "studio-global-nav",
        "library-nav",
        r#"target="_top""#,
    ] {
        assert!(!html.contains(dead), "{state}: duplicate chrome `{dead}`");
    }
}

#[tokio::test]
async fn empty_runtime_home_keeps_global_navigation_on_every_scaffold_route() {
    let _home = TempHome::new("empty");
    let app = test_app();

    let (status, content_type, html) = get(&app, "/scaffold").await;
    assert_eq!(status, StatusCode::OK, "{html}");
    assert_shared_chrome("empty", &content_type, &html);
    assert!(html.contains("No manifest-backed scaffold plans are available."));
    assert!(html.contains(r#"class="server-console-shell route-page-shell scaffold-empty""#));
    assert!(html.contains(r#"href="/scaffold/library""#));
    write_fixture("empty", &html);

    let (status, content_type, html) = get(&app, "/scaffold/library").await;
    assert_eq!(status, StatusCode::OK);
    assert_shared_chrome("empty-library", &content_type, &html);
    assert!(html.contains("No manifest-backed scaffold plans are available."));

    let (status, content_type, html) = get(&app, "/scaffold/editor").await;
    assert_eq!(status, StatusCode::OK);
    assert_shared_chrome("empty-editor", &content_type, &html);
}

#[tokio::test]
async fn populated_plan_renders_studio_inside_the_shared_frame() {
    let home = TempHome::new("populated");
    home.setup_plan("vetcoders", "vibecrafted", "2026_0908", "shell-plan");
    let app = test_app();

    // Bare /scaffold auto-selects the single reviewable plan: editor state.
    let (status, content_type, html) = get(&app, "/scaffold").await;
    assert_eq!(status, StatusCode::OK, "{html}");
    assert_shared_chrome("editor", &content_type, &html);
    assert!(html.contains(r#"class="review-shell""#));
    assert!(html.contains(r#"class="review-inspector""#));
    assert!(html.contains(r#"class="review-statusbar""#));
    assert_eq!(
        count(&html, r#"class="artifact-panel is-active""#),
        1,
        "one active document"
    );
    assert!(html.contains(r#"class="artifact-panel" id="tracker" data-render-mode="rich" hidden"#));
    // Artifact index lives inside the canvas, after the global sidebar.
    let sidebar_end = html.find("</aside>").expect("global sidebar end");
    let tabs = html
        .find(r#"class="tabs" role="tablist""#)
        .expect("artifact tabs");
    assert!(
        sidebar_end < tabs,
        "artifact tabs must not replace the global sidebar"
    );
    assert!(html.contains(r#"class="review-library-link" href="/scaffold/library""#));
    assert!(html.contains("1 / 2 checkpointed") || html.contains("0 / 2 checkpointed"));
    assert!(html.contains(
        r#"href="/api/scaffold/artifacts?org=vetcoders&repo=vibecrafted&day=2026_0908&plan_id=shell-plan" target="_blank" rel="noopener noreferrer""#
    ));
    write_fixture("editor", &html);

    // Explicit selection renders the same studio.
    let (status, content_type, html) = get(
        &app,
        "/scaffold?org=vetcoders&repo=vibecrafted&day=2026_0908&plan_id=shell-plan",
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_shared_chrome("editor-explicit", &content_type, &html);
    assert!(html.contains(r#"class="review-shell""#));

    // Library lists the plan as a card inside the same frame.
    let (status, content_type, html) = get(&app, "/scaffold/library").await;
    assert_eq!(status, StatusCode::OK);
    assert_shared_chrome("library", &content_type, &html);
    assert!(html.contains(r#"class="plan-library""#));
    assert!(html.contains("Choose the truth"));
    assert!(html.contains(
        r#"href="/scaffold?org=vetcoders&amp;repo=vibecrafted&amp;day=2026_0908&amp;plan_id=shell-plan""#
    ));
    write_fixture("library", &html);
}

#[tokio::test]
async fn error_states_stay_inside_the_shared_frame_and_are_recoverable() {
    let home = TempHome::new("error");
    home.setup_blocked_plan("vetcoders", "vibecrafted", "2026_0908", "broken-plan");
    let app = test_app();

    // Indexed but not reviewable: doctor truth, still inside the chrome.
    let (status, content_type, html) = get(
        &app,
        "/scaffold?org=vetcoders&repo=vibecrafted&day=2026_0908&plan_id=broken-plan",
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{html}");
    assert_shared_chrome("blocked", &content_type, &html);
    assert!(html.contains("The plan exists."));
    assert!(html.contains(r#"class="back-link" href="/scaffold/library""#));
    write_fixture("blocked", &html);

    // Unknown plan: 404, but the page keeps sidebar, Home and the library.
    let (status, content_type, html) = get(
        &app,
        "/scaffold?org=vetcoders&repo=vibecrafted&day=2026_0908&plan_id=missing-plan",
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND, "{html}");
    assert_shared_chrome("not-found", &content_type, &html);
    assert!(html.contains("Scaffold artifacts unavailable:"));
    assert!(html.contains(r#"href="/scaffold/library""#));
    write_fixture("not-found", &html);

    // Library surfaces the blocked plan without losing the frame.
    let (status, content_type, html) = get(&app, "/scaffold/library").await;
    assert_eq!(status, StatusCode::OK);
    assert_shared_chrome("library-blocked", &content_type, &html);
    assert!(html.contains("plan-card-blocked"));
}

#[tokio::test]
async fn api_endpoints_remain_json_documents() {
    let home = TempHome::new("api");
    home.setup_plan("vetcoders", "vibecrafted", "2026_0908", "shell-plan");
    let app = test_app();

    let (status, content_type, body) = get(&app, "/api/scaffold/plans").await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        content_type.starts_with("application/json"),
        "{content_type}"
    );
    let json: serde_json::Value = serde_json::from_str(&body).expect("plans json");
    assert_eq!(json["plans"][0]["plan_id"], "shell-plan");

    let (status, content_type, body) = get(
        &app,
        "/api/scaffold/artifacts?org=vetcoders&repo=vibecrafted&day=2026_0908&plan_id=shell-plan",
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        content_type.starts_with("application/json"),
        "{content_type}"
    );
    let json: serde_json::Value = serde_json::from_str(&body).expect("artifacts json");
    assert_eq!(json["artifacts"].as_array().map(Vec::len), Some(2));

    // Errors on machine endpoints are JSON too — never an HTML page.
    let (status, content_type, body) = get(
        &app,
        "/api/scaffold/artifacts?org=vetcoders&repo=vibecrafted&day=2026_0908&plan_id=missing-plan",
    )
    .await;
    assert!(status.is_client_error(), "{status}");
    assert!(
        content_type.starts_with("application/json"),
        "{content_type}"
    );
    assert!(!body.contains("<html"), "{body}");
    assert!(serde_json::from_str::<serde_json::Value>(&body).is_ok());
}
