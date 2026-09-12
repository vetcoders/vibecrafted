//! HTTP-level contract tests for the owner-backed tool surfaces: the sandboxed
//! Loctree report and the local-peer-only AICX corpus.
//!
//! One test, one process: the handlers read process-wide environment
//! (`VIBECRAFTED_HOME`, `AICX_HOME`, `VC_AICX_BIN`), so this binary owns it.

#![cfg(feature = "ssr")]

use std::fs;
use std::net::SocketAddr;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::Router;
use axum::body::{Body, to_bytes};
use axum::extract::ConnectInfo;
use axum::http::{Request, StatusCode, header};
use axum::routing::get;
use leptos::config::{Env, LeptosOptions};
use serde_json::{Value, json};
use tower::ServiceExt;
use vibecrafted_server_web::tools::api::{
    aicx_reference, aicx_search, loctree_report, loctree_report_asset, loctree_report_redirect,
};

struct Fixture {
    home: PathBuf,
    extract: PathBuf,
    large_extract: PathBuf,
    symlinked_extract: PathBuf,
    aicx_mode: PathBuf,
    aicx_argv: PathBuf,
    aicx_payload: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let home = std::env::temp_dir().join(format!(
            "vc-tools-http-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        let repo = home.join("repo");
        let report_dir = repo.join(".loctree");
        fs::create_dir_all(&report_dir).expect("report dir");
        fs::write(
            report_dir.join("report.html"),
            "<!DOCTYPE html>\n<html><head><meta charset=\"UTF-8\"><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'self' file: data: blob:; script-src 'self' 'unsafe-inline'\"><title>Loctree Report</title></head><body><div id=\"graph\"></div><script src=\"loctree-cytoscape.min.js\"></script><script>window.report=1</script></body></html>",
        )
        .expect("report");
        fs::write(
            report_dir.join("loctree-cytoscape.min.js"),
            "window.cytoscape=function(){};",
        )
        .expect("asset");
        fs::write(report_dir.join("secret.txt"), "not an asset").expect("secret");
        std::os::unix::fs::symlink("/etc/hosts", report_dir.join("evil.js")).expect("symlink");

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
                "root": repo.to_string_lossy(),
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

        let aicx_home = home.join("aicx");
        let extracts = aicx_home.join("extracts/claude");
        fs::create_dir_all(&extracts).expect("extracts");
        let extract = extracts.join("0001-conversation.md");
        fs::write(&extract, "# session 0001\n\nnative tabs decision\n").expect("extract");
        let large_extract = extracts.join("0002-conversation.md");
        fs::write(&large_extract, "x".repeat(300 * 1024)).expect("large extract");
        let symlinked_extract = extracts.join("0003-conversation.md");
        std::os::unix::fs::symlink("/etc/hosts", &symlinked_extract).expect("extract symlink");
        fs::write(aicx_home.join("config.toml"), "secret = true\n").expect("aicx config");

        let aicx_mode = home.join("aicx-mode");
        let aicx_argv = home.join("aicx-argv");
        let aicx_payload = home.join("aicx-payload.json");
        let script = home.join("aicx.sh");
        fs::write(
            &script,
            format!(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > '{argv}'\nmode=$(cat '{mode}')\ncase \"$mode\" in\n  ok) exec cat '{payload}' ;;\n  garbage) echo 'not json'; exit 0 ;;\n  fail) echo 'aicx: index unavailable for that project' >&2; exit 2 ;;\n  hang) exec /bin/sleep 30 ;;\nesac\nexit 9\n",
                argv = aicx_argv.display(),
                mode = aicx_mode.display(),
                payload = aicx_payload.display()
            ),
        )
        .expect("aicx script");
        let mut permissions = fs::metadata(&script).expect("script meta").permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&script, permissions).expect("script exec");

        // Safety: this integration binary holds one test, so it is the single
        // owner of process-wide environment for its lifetime.
        unsafe {
            std::env::set_var("VIBECRAFTED_HOME", &home);
            std::env::set_var("HOME", home.join("never-used-home"));
            std::env::set_var("AICX_HOME", &aicx_home);
            std::env::set_var("VC_AICX_BIN", &script);
            std::env::set_var("VC_AICX_TIMEOUT_SECONDS", "0.3");
        }
        Self {
            home,
            extract,
            large_extract,
            symlinked_extract,
            aicx_mode,
            aicx_argv,
            aicx_payload,
        }
    }

    fn aicx(&self, mode: &str, items: Value) {
        fs::write(&self.aicx_mode, mode).expect("mode");
        fs::write(
            &self.aicx_payload,
            serde_json::to_vec(&json!({
                "coverage": {"scanned_sessions": 3},
                "oracle_status": {"aicx_home": self.home.join("aicx").to_string_lossy()},
                "items": items
            }))
            .expect("payload"),
        )
        .expect("payload file");
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.home);
    }
}

fn router(bind: &str) -> Router {
    let opts = LeptosOptions::builder()
        .output_name("vibecrafted-server-web-test")
        .site_root("target/site-test")
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr(bind.parse::<SocketAddr>().expect("bind"))
        .reload_port(0)
        .build();
    Router::new()
        .route("/structure/report", get(loctree_report_redirect))
        .route("/structure/report/", get(loctree_report))
        .route("/structure/report/{asset}", get(loctree_report_asset))
        .route("/api/aicx/search", get(aicx_search))
        .route("/api/aicx/reference", get(aicx_reference))
        .with_state(opts)
}

async fn call(
    app: &Router,
    uri: &str,
    peer: Option<&str>,
) -> (StatusCode, axum::http::HeaderMap, Vec<u8>) {
    let mut request = Request::builder()
        .uri(uri)
        .header(header::HOST, "127.0.0.1:3024")
        .body(Body::empty())
        .expect("request");
    if let Some(peer) = peer {
        request
            .extensions_mut()
            .insert(ConnectInfo(peer.parse::<SocketAddr>().expect("peer")));
    }
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

fn encode(path: &Path) -> String {
    path.to_string_lossy()
        .bytes()
        .map(|byte| match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'/' => {
                (byte as char).to_string()
            }
            other => format!("%{other:02X}"),
        })
        .collect()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn tool_surfaces_keep_their_boundaries() {
    let fixture = Fixture::new();
    let app = router("127.0.0.1:3024");

    // ---- Loctree report: the document lives at the directory-style URL so its
    // relative `<script src>` resolve onto the asset route; the bare path redirects.
    let (status, headers, _) = call(&app, "/structure/report", Some("192.168.1.9:4000")).await;
    assert_eq!(status, StatusCode::PERMANENT_REDIRECT);
    assert_eq!(header(&headers, "location"), "/structure/report/");
    assert_eq!(header(&headers, "cache-control"), "no-store");

    // Served sandboxed, its meta CSP removed, storage stand-in first, assets explicit.
    let (status, headers, body) = call(&app, "/structure/report/", Some("192.168.1.9:4000")).await;
    assert_eq!(
        status,
        StatusCode::OK,
        "report is a public page on the runtime origin"
    );
    let html = String::from_utf8(body).expect("utf8 report");
    assert!(
        !html.contains("Content-Security-Policy"),
        "meta CSP must not survive: {html}"
    );
    assert!(html.contains("<script src=\"loctree-cytoscape.min.js\">"));
    let shim_at = html
        .find("<script data-vibecrafted=\"storage-shim\">")
        .expect("storage stand-in installed");
    assert!(
        shim_at < html.find("<script src=").expect("report script"),
        "stand-in must run before any report script: {html}"
    );
    assert_eq!(html.matches("storage-shim").count(), 1);
    assert!(header(&headers, "content-type").starts_with("text/html"));
    let csp = header(&headers, "content-security-policy").to_string();
    assert!(csp.contains("sandbox allow-scripts"), "{csp}");
    assert!(!csp.contains("allow-same-origin"), "{csp}");
    assert!(
        csp.contains("connect-src 'none'") && csp.contains("form-action 'none'"),
        "{csp}"
    );
    assert!(csp.contains("127.0.0.1:3024/structure/report/"), "{csp}");
    assert_eq!(header(&headers, "cache-control"), "no-store");
    assert_eq!(header(&headers, "x-content-type-options"), "nosniff");

    let (status, headers, body) =
        call(&app, "/structure/report/loctree-cytoscape.min.js", None).await;
    assert_eq!(status, StatusCode::OK);
    assert!(header(&headers, "content-type").starts_with("text/javascript"));
    assert!(header(&headers, "content-security-policy").contains("sandbox"));
    assert_eq!(body, b"window.cytoscape=function(){};");
    for denied in [
        "/structure/report/evil.js",
        "/structure/report/secret.txt",
        "/structure/report/report.html",
        "/structure/report/..%2Freport.html",
        "/structure/report/.hidden.js",
    ] {
        let (status, _, _) = call(&app, denied, None).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{denied} must not be served");
    }

    // ---- AICX search: local peer only, fail closed without a peer.
    fixture.aicx("ok", json!([]));
    let (status, _, body) = call(&app, "/api/aicx/search?q=native", Some("192.168.1.9:4000")).await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert!(String::from_utf8_lossy(&body).contains("private to this host"));
    let (status, _, _) = call(&app, "/api/aicx/search?q=native", None).await;
    assert_eq!(status, StatusCode::FORBIDDEN, "unknown peer fails closed");
    assert!(
        !fixture.aicx_argv.exists(),
        "a refused request must not spawn aicx"
    );

    // A tailnet bind admits the same interface as a local peer and refuses others.
    let tailnet = router("100.82.232.70:3025");
    let (status, _, _) = call(
        &tailnet,
        "/api/aicx/search?q=native",
        Some("100.82.232.70:5000"),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let (status, _, _) = call(
        &tailnet,
        "/api/aicx/search?q=native",
        Some("100.82.232.71:5000"),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);

    // Validation before any process spawn.
    let _ = fs::remove_file(&fixture.aicx_argv);
    for bad in [
        "/api/aicx/search",
        "/api/aicx/search?q=",
        "/api/aicx/search?q=--help",
        "/api/aicx/search?q=x&project=../etc",
        "/api/aicx/search?q=x&project=vibecrafted",
    ] {
        let (status, _, _) = call(&app, bad, Some("127.0.0.1:5000")).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{bad}");
    }
    assert!(
        !fixture.aicx_argv.exists(),
        "invalid input must not spawn aicx"
    );

    // Success: bounded argv, projected items, references only inside extracts.
    fixture.aicx(
        "ok",
        json!([
            {"agent": "claude", "date": "2026-09-08", "project": "vetcoders/vibecrafted",
             "session_id": "sess-0001", "kind": "conversations",
             "matches": ["native tabs decision", "second match", "third", "fourth is dropped"],
             "path": fixture.extract.to_string_lossy()},
            {"agent": "codex", "date": "2026-09-07", "session": "sess-outside",
             "matches": ["m"], "path": "/etc/hosts"},
            {"agent": "codex", "date": "2026-09-07", "session_id": "sess-symlink",
             "matches": ["m"], "path": fixture.symlinked_extract.to_string_lossy()},
            {"agent": "codex", "date": "2026-09-07", "session_id": "sess-config",
             "matches": ["m"], "path": fixture.home.join("aicx/config.toml").to_string_lossy()}
        ]),
    );
    let (status, headers, body) = call(
        &app,
        "/api/aicx/search?q=native%20tabs&project=vetcoders%2Fvibecrafted",
        Some("[::1]:5000"),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{}", String::from_utf8_lossy(&body));
    assert_eq!(header(&headers, "cache-control"), "no-store");
    let argv = fs::read_to_string(&fixture.aicx_argv).expect("argv");
    assert_eq!(
        argv.lines().collect::<Vec<_>>(),
        [
            "search",
            "--json",
            "--no-semantic",
            "--limit",
            "12",
            "-p",
            "vetcoders/vibecrafted",
            "native tabs"
        ]
    );
    let payload: Value = serde_json::from_slice(&body).expect("json");
    assert_eq!(payload["schema"], "vibecrafted.aicx-search.v1");
    assert_eq!(payload["count"], 4);
    let text = String::from_utf8_lossy(&body);
    assert!(
        !text.contains("oracle_status") && !text.contains("coverage"),
        "diagnostics leaked: {text}"
    );
    assert!(!text.contains("\"path\""), "raw paths leaked: {text}");
    assert!(!text.contains("/etc/hosts"), "{text}");
    let items = payload["items"].as_array().expect("items");
    assert_eq!(items[0]["session_id"], "sess-0001");
    assert_eq!(items[0]["matches"].as_array().map(Vec::len), Some(3));
    let reference = items[0]["reference"].as_str().expect("reference route");
    assert!(
        reference.starts_with("/api/aicx/reference?path="),
        "{reference}"
    );
    assert_eq!(items[1]["session_id"], "sess-outside");
    assert!(
        items[1]["reference"].is_null(),
        "outside path got a reference"
    );
    assert!(
        items[2]["reference"].is_null(),
        "symlinked extract got a reference"
    );
    assert!(
        items[3]["reference"].is_null(),
        "non-extract file got a reference"
    );

    // Failure modes stay typed.
    fixture.aicx("garbage", json!([]));
    let (status, _, _) = call(&app, "/api/aicx/search?q=native", Some("127.0.0.1:5000")).await;
    assert_eq!(status, StatusCode::BAD_GATEWAY);
    fixture.aicx("fail", json!([]));
    let (status, _, body) = call(&app, "/api/aicx/search?q=native", Some("127.0.0.1:5000")).await;
    assert_eq!(status, StatusCode::BAD_GATEWAY);
    assert!(String::from_utf8_lossy(&body).contains("index unavailable"));
    fixture.aicx("hang", json!([]));
    let (status, _, _) = call(&app, "/api/aicx/search?q=native", Some("127.0.0.1:5000")).await;
    assert_eq!(status, StatusCode::GATEWAY_TIMEOUT);
    unsafe {
        std::env::set_var("VC_AICX_BIN", fixture.home.join("missing-aicx"));
    }
    let (status, _, _) = call(&app, "/api/aicx/search?q=native", Some("127.0.0.1:5000")).await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);

    // ---- Reference route: the one authorised document, as a machine document.
    let (status, headers, body) = call(&app, reference, Some("127.0.0.1:5000")).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        header(&headers, "content-type"),
        "text/plain; charset=utf-8"
    );
    assert_eq!(header(&headers, "x-content-type-options"), "nosniff");
    assert_eq!(header(&headers, "cache-control"), "no-store");
    assert_eq!(
        header(&headers, "content-security-policy"),
        "default-src 'none'; sandbox"
    );
    assert!(headers.get("x-vibecrafted-truncated").is_none());
    assert_eq!(body, fs::read(&fixture.extract).expect("extract bytes"));
    let (status, _, _) = call(&app, reference, Some("10.0.0.7:5000")).await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    let (status, headers, body) = call(
        &app,
        &format!(
            "/api/aicx/reference?path={}",
            encode(&fixture.large_extract)
        ),
        Some("127.0.0.1:5000"),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(header(&headers, "x-vibecrafted-truncated"), "true");
    assert_eq!(body.len(), 256 * 1024);
    for denied in [
        "/api/aicx/reference".to_string(),
        "/api/aicx/reference?path=/etc/hosts".to_string(),
        "/api/aicx/reference?path=relative/extract.md".to_string(),
        format!(
            "/api/aicx/reference?path={}",
            encode(&fixture.symlinked_extract)
        ),
        format!(
            "/api/aicx/reference?path={}",
            encode(&fixture.home.join("aicx/config.toml"))
        ),
        format!(
            "/api/aicx/reference?path={}",
            encode(&fixture.home.join("aicx/extracts/../config.toml"))
        ),
    ] {
        let (status, _, _) = call(&app, &denied, Some("127.0.0.1:5000")).await;
        assert!(
            matches!(status, StatusCode::NOT_FOUND | StatusCode::BAD_REQUEST),
            "{denied} answered {status}"
        );
    }
}
