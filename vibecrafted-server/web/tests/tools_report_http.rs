//! The served Loctree report against a report Loctree itself generated.
//!
//! `tools_http.rs` pins the boundary with a hand-written stand-in document.
//! This binary asks the real `loct` for a report of a two-file Python fixture
//! and checks the serving contract on what Loctree actually writes: every
//! relative `<script src>` in the document must answer on the asset route the
//! document URL resolves it to, the `file://` meta policy must be gone, the
//! storage stand-in must precede the report's first script, and the sandbox
//! header policy must name the asset route.
//!
//! Without `loct` on `PATH` the test reports itself as skipped on stdout and
//! passes — the hand-written contract in `tools_http.rs` still runs.
//!
//! One test, one process: the handler reads `VIBECRAFTED_HOME`, so this binary
//! owns the process environment for its lifetime.

#![cfg(feature = "ssr")]

use std::fs;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use axum::Router;
use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode, header};
use axum::routing::get;
use leptos::config::{Env, LeptosOptions};
use serde_json::json;
use tower::ServiceExt;
use vibecrafted_server_web::tools::api::{
    loctree_report, loctree_report_asset, loctree_report_redirect,
};

struct Fixture {
    home: PathBuf,
    report_dir: PathBuf,
}

impl Fixture {
    /// `None` when `loct` is not available on this host.
    fn generate() -> Option<Self> {
        let home = std::env::temp_dir().join(format!(
            "vc-tools-report-http-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        let workspace = home.join("workspace");
        fs::create_dir_all(&workspace).expect("workspace");
        fs::write(
            workspace.join("greeting.py"),
            "def greet(name: str) -> str:\n    return f\"hello {name}\"\n",
        )
        .expect("greeting.py");
        fs::write(
            workspace.join("main.py"),
            "from greeting import greet\n\nprint(greet(\"vibecrafted\"))\n",
        )
        .expect("main.py");

        // Loctree's own cache lands under the fixture, not the host's home.
        let loct_home = home.join("loct-home");
        fs::create_dir_all(&loct_home).expect("loct home");
        let output = Command::new("loct")
            .args(["report", "--output", ".loctree/report.html", "."])
            .current_dir(&workspace)
            .env("LOCT_OPEN_BROWSER", "0")
            .env("HOME", &loct_home)
            .env("XDG_CACHE_HOME", loct_home.join(".cache"))
            .output();
        let output = match output {
            Ok(output) => output,
            Err(error) => {
                println!("skipped: `loct` is not runnable on this host ({error})");
                let _ = fs::remove_dir_all(&home);
                return None;
            }
        };
        assert!(
            output.status.success(),
            "loct report failed: {}\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        let report_dir = workspace.join(".loctree");
        assert!(
            report_dir.join("report.html").is_file(),
            "no report written"
        );

        let runs = home.join("control_plane/runs");
        fs::create_dir_all(&runs).expect("runs");
        fs::write(
            runs.join("fixture-run.json"),
            serde_json::to_vec(&json!({
                "run_id": "fixture-run",
                "state": "completed",
                "agent": "codex",
                "skill": "implement",
                "mode": "implement",
                "root": workspace.to_string_lossy(),
                "operator_session": "repo-fixture-run",
                "latest_report": "",
                "latest_transcript": "",
                "last_error": "",
                "updated_at": "2026-09-08T12:00:00+00:00",
                "started_at": "2026-09-08T11:59:00+00:00",
                "health": "final",
                "source": "agent-meta",
                "lock_present": false
            }))
            .expect("snapshot"),
        )
        .expect("snapshot file");

        // Safety: this integration binary holds one test, so it is the single
        // owner of process-wide environment for its lifetime.
        unsafe {
            std::env::set_var("VIBECRAFTED_HOME", &home);
        }
        Some(Self { home, report_dir })
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.home);
    }
}

fn router() -> Router {
    let opts = LeptosOptions::builder()
        .output_name("vibecrafted-server-web-test")
        .site_root("target/site-test")
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr("127.0.0.1:3024".parse::<SocketAddr>().expect("bind"))
        .reload_port(0)
        .build();
    Router::new()
        .route("/structure/report", get(loctree_report_redirect))
        .route("/structure/report/", get(loctree_report))
        .route("/structure/report/{asset}", get(loctree_report_asset))
        .with_state(opts)
}

async fn call(app: &Router, uri: &str) -> (StatusCode, axum::http::HeaderMap, Vec<u8>) {
    let request = Request::builder()
        .uri(uri)
        .header(header::HOST, "127.0.0.1:3024")
        .body(Body::empty())
        .expect("request");
    let response = app.clone().oneshot(request).await.expect("response");
    let status = response.status();
    let headers = response.headers().clone();
    let body = to_bytes(response.into_body(), 16 * 1024 * 1024)
        .await
        .expect("body")
        .to_vec();
    (status, headers, body)
}

fn header<'a>(headers: &'a axum::http::HeaderMap, name: &str) -> &'a str {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default()
}

/// Every `src="..."` of a `<script>` element, in document order.
fn script_sources(html: &str) -> Vec<String> {
    let mut sources = Vec::new();
    let mut rest = html;
    while let Some(start) = rest.find("<script") {
        let tag_end = rest[start..]
            .find('>')
            .map(|i| start + i)
            .unwrap_or(rest.len());
        let tag = &rest[start..tag_end];
        if let Some(src) = tag.find("src=\"") {
            let value = &tag[src + 5..];
            if let Some(end) = value.find('"') {
                sources.push(value[..end].to_string());
            }
        }
        rest = &rest[tag_end..];
    }
    sources
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_real_loctree_report_is_served_whole() {
    let Some(fixture) = Fixture::generate() else {
        return;
    };
    let app = router();
    let written = fs::read_to_string(fixture.report_dir.join("report.html")).expect("report");
    assert!(
        written.contains("<meta http-equiv=\"Content-Security-Policy\""),
        "the fixture must be a Loctree report carrying its file:// policy"
    );

    // The bare path is a pointer to the document, never a second copy of it.
    let (status, headers, _) = call(&app, "/structure/report").await;
    assert_eq!(status, StatusCode::PERMANENT_REDIRECT);
    assert_eq!(header(&headers, "location"), "/structure/report/");

    let (status, headers, body) = call(&app, "/structure/report/").await;
    assert_eq!(status, StatusCode::OK);
    let html = String::from_utf8(body).expect("utf8 report");
    assert!(
        html.contains("<title>Loctree Report</title>"),
        "not a Loctree report"
    );
    assert!(
        !html.contains("<meta http-equiv=\"Content-Security-Policy\""),
        "the file:// policy must be removed"
    );
    let csp = header(&headers, "content-security-policy").to_string();
    assert!(csp.contains("sandbox allow-scripts"), "{csp}");
    assert!(!csp.contains("allow-same-origin"), "{csp}");
    assert!(csp.contains("127.0.0.1:3024/structure/report/"), "{csp}");

    // The storage stand-in runs before anything the report wrote.
    let shim_at = html
        .find("<script data-vibecrafted=\"storage-shim\">")
        .expect("storage stand-in");
    let first_report_script = html
        .find("<script")
        .and_then(|first| {
            if first == shim_at {
                html[shim_at + 1..].find("<script").map(|i| shim_at + 1 + i)
            } else {
                Some(first)
            }
        })
        .expect("report scripts");
    assert!(
        shim_at < first_report_script,
        "stand-in must be the first script"
    );
    assert!(
        html.contains("localStorage"),
        "the fixture report no longer touches localStorage; revisit whether the stand-in is still needed"
    );

    // Every relative script the document asks for answers on the asset route the
    // document URL resolves it to, with a JavaScript content type.
    let sources = script_sources(&html);
    let relative: Vec<&String> = sources
        .iter()
        .filter(|src| !src.contains("://") && !src.starts_with('/'))
        .collect();
    assert!(
        relative.iter().any(|src| src.contains("cytoscape")),
        "expected Loctree's graph library among {sources:?}"
    );
    for src in &relative {
        assert!(
            !src.contains('/'),
            "asset outside the report directory would not be served: {src}"
        );
        let (status, headers, body) = call(&app, &format!("/structure/report/{src}")).await;
        assert_eq!(status, StatusCode::OK, "{src} must be served");
        assert!(
            header(&headers, "content-type").starts_with("text/javascript"),
            "{src}: {}",
            header(&headers, "content-type")
        );
        assert!(header(&headers, "content-security-policy").contains("sandbox"));
        assert_eq!(
            body,
            fs::read(fixture.report_dir.join(src)).expect("asset bytes")
        );
    }

    // Nothing else in the report directory becomes reachable.
    let (status, _, _) = call(&app, "/structure/report/report.html").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    let (status, _, _) = call(&app, "/structure/report/context-atlas").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    let (status, _, _) = call(&app, "/structure/report/context-atlas%2Fmanifest.md").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}
