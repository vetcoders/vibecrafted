//! Owner-backed tool surfaces served by `vc-server`: the Loctree HTML report
//! and the AICX corpus.
//!
//! Both read private local data that the console itself never needed, so each
//! carries its own boundary instead of inheriting the console's:
//!
//! * **AICX** (`/api/aicx/search`, `/api/aicx/reference`) is served only to a
//!   verified local peer. The server has no authentication layer and its bind
//!   is operator-owned (`[server]` in `~/.config/vibecrafted/config.toml`:
//!   loopback by default, a tailnet interface by choice, no reverse proxy). The
//!   real signal available is the accepted socket's peer address, so that is
//!   the gate: loopback, or the same interface the listener is bound to (a
//!   connection from this host to its own tailnet address). A request whose
//!   peer is unknown fails closed. Search hits never carry raw filesystem
//!   paths; each one gets a server-owned reference route that is authorised
//!   against the AICX extracts root at read time.
//! * **Loctree report** (`/structure/report/`, `/structure/report/{asset}`) is
//!   a generated, script-bearing document. It is served on the runtime origin
//!   but inside a Content-Security-Policy `sandbox`, so it runs with an opaque
//!   origin: no cookies, no `fetch` to `/api/*`, no form posts, no frames. Its
//!   own scripts and the sibling assets Loctree writes next to it are allowed
//!   explicitly, so the interactive graph keeps working. Nothing in this module
//!   ever gives generated HTML control-plane authority.
//!
//!   The document is served at the directory-style URL `/structure/report/`
//!   (`/structure/report` redirects there) because Loctree writes it for the
//!   `file://` case: its `<script src="loctree-cytoscape.min.js">` references
//!   are relative to the document, so the document URL has to end in the same
//!   segment that serves the assets. Two adaptations happen at serve time,
//!   neither of which widens the sandbox: the report's own `<meta>` CSP is
//!   removed (see [`api::strip_meta_csp`]) and an in-memory `localStorage` /
//!   `sessionStorage` stand-in is installed for the opaque origin (see
//!   [`api::inject_storage_shim`]), where the real Web Storage getters throw
//!   and the report's top-level scripts would die before wiring their tabs.

#[cfg(feature = "ssr")]
pub mod api {
    use std::net::SocketAddr;
    use std::path::{Path, PathBuf};
    use std::time::Duration;

    use axum::Json;
    use axum::extract::{ConnectInfo, Query, Request, State};
    use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
    use axum::response::{IntoResponse, Response};
    use control_core::ControlPlane;
    use leptos::config::LeptosOptions;
    use serde_json::{Value, json};

    /// Query size the AICX CLI is asked to search. Long free text is a paste,
    /// not a query.
    const MAX_QUERY_CHARS: usize = 512;
    const AICX_RESULT_LIMIT: &str = "12";
    const DEFAULT_AICX_TIMEOUT_SECONDS: f64 = 10.0;
    /// Bound on one reference document. Extracts are Markdown conversations;
    /// the App shows them in a read-only reference tab, not as a download.
    const MAX_REFERENCE_BYTES: u64 = 256 * 1024;
    /// Bound on one report asset (the largest Loctree ships is ~360 KiB).
    const MAX_ASSET_BYTES: u64 = 8 * 1024 * 1024;
    const MATCH_SNIPPET_CHARS: usize = 360;
    const MAX_MATCHES_PER_ITEM: usize = 3;

    // ------------------------------------------------------------------
    // Access boundary
    // ------------------------------------------------------------------

    /// Decides whether `peer` may read the private local corpus served from a
    /// listener bound at `bind`.
    ///
    /// Loopback always may. When the listener is bound to one concrete
    /// interface, a connection whose source is that same address originates on
    /// this host (a tailnet bind reached locally). An unspecified bind
    /// (`0.0.0.0`) gives no such evidence, so only loopback qualifies. No peer
    /// address at all is a transport we cannot vouch for: refuse.
    pub(crate) fn local_peer_access(
        peer: Option<SocketAddr>,
        bind: SocketAddr,
    ) -> Result<(), &'static str> {
        let Some(peer) = peer else {
            return Err("the AICX corpus is private to this host and the peer address is unknown");
        };
        let ip = peer.ip().to_canonical();
        if ip.is_loopback() {
            return Ok(());
        }
        let bound = bind.ip().to_canonical();
        if !bound.is_unspecified() && !bound.is_loopback() && ip == bound {
            return Ok(());
        }
        Err("the AICX corpus is private to this host; open it from the machine running vc-server")
    }

    fn peer_of(request: &Request) -> Option<SocketAddr> {
        request
            .extensions()
            .get::<ConnectInfo<SocketAddr>>()
            .map(|info| info.0)
    }

    fn json_error(status: StatusCode, error: &str) -> Response {
        (
            status,
            [(header::CACHE_CONTROL, "no-store")],
            Json(json!({ "error": error })),
        )
            .into_response()
    }

    // ------------------------------------------------------------------
    // AICX
    // ------------------------------------------------------------------

    fn home_dir() -> Option<PathBuf> {
        std::env::var_os("HOME")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
    }

    /// `$AICX_HOME` when absolute, else `~/.aicx` — the same resolution the
    /// AICX CLI applies.
    pub(crate) fn aicx_home() -> Option<PathBuf> {
        if let Some(configured) = std::env::var_os("AICX_HOME") {
            let path = PathBuf::from(configured);
            if path.is_absolute() {
                return Some(path);
            }
        }
        home_dir().map(|home| home.join(".aicx"))
    }

    fn aicx_timeout() -> Duration {
        std::env::var("VC_AICX_TIMEOUT_SECONDS")
            .ok()
            .and_then(|raw| raw.trim().parse::<f64>().ok())
            .filter(|seconds| seconds.is_finite() && *seconds > 0.0)
            .map(Duration::from_secs_f64)
            .unwrap_or(Duration::from_secs_f64(DEFAULT_AICX_TIMEOUT_SECONDS))
    }

    fn aicx_binary() -> String {
        std::env::var("VC_AICX_BIN")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| "aicx".to_string())
    }

    fn valid_query(raw: &str) -> Option<&str> {
        let term = raw.trim();
        if term.is_empty()
            || term.chars().count() > MAX_QUERY_CHARS
            || term.starts_with('-')
            || term.chars().any(char::is_control)
        {
            return None;
        }
        Some(term)
    }

    /// `owner/repo` exactly — the only project form the CLI accepts strictly.
    pub(crate) fn valid_project(raw: &str) -> Option<&str> {
        let value = raw.trim();
        if value.is_empty() {
            return None;
        }
        let (owner, repo) = value.split_once('/')?;
        let segment_ok = |segment: &str| {
            !segment.is_empty()
                && segment.len() <= 64
                && segment
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
                && !segment.starts_with('.')
        };
        (segment_ok(owner) && segment_ok(repo)).then_some(value)
    }

    /// Authorises one AICX extract path for the reference route.
    ///
    /// The path must be absolute, name a regular file (never a symlink) whose
    /// canonical location lies under the canonical extracts root, and carry a
    /// text extension. Anything else is not a reference the server owns.
    pub(crate) fn authorize_reference_path(raw: &str, extracts_root: &Path) -> Option<PathBuf> {
        if raw.is_empty() || raw.len() > 4096 || raw.contains('\0') {
            return None;
        }
        let candidate = Path::new(raw);
        if !candidate.is_absolute() {
            return None;
        }
        let extension = candidate.extension()?.to_str()?;
        if !matches!(extension, "md" | "txt" | "json" | "jsonl") {
            return None;
        }
        let metadata = std::fs::symlink_metadata(candidate).ok()?;
        if !metadata.is_file() || metadata.file_type().is_symlink() {
            return None;
        }
        let canonical_root = std::fs::canonicalize(extracts_root).ok()?;
        let canonical = std::fs::canonicalize(candidate).ok()?;
        canonical.starts_with(&canonical_root).then_some(canonical)
    }

    fn reference_route(path: &str) -> String {
        format!("/api/aicx/reference?path={}", percent_encode(path))
    }

    fn percent_encode(value: &str) -> String {
        let mut encoded = String::with_capacity(value.len());
        for byte in value.bytes() {
            match byte {
                b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' | b'/' => {
                    encoded.push(byte as char);
                }
                other => encoded.push_str(&format!("%{other:02X}")),
            }
        }
        encoded
    }

    fn truncate_chars(value: &str, limit: usize) -> String {
        let mut out: String = value.chars().take(limit).collect();
        if value.chars().count() > limit {
            out.push('…');
        }
        out
    }

    /// Reduces the CLI payload to what a reader needs: identity, date, agent,
    /// bounded snippets, and a server-owned reference route. Raw paths,
    /// coverage diagnostics and the AICX home never leave the host.
    pub(crate) fn project_search_items(payload: &Value, extracts_root: &Path) -> Vec<Value> {
        payload
            .get("items")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .map(|item| {
                        let text = |key: &str| {
                            item.get(key)
                                .and_then(Value::as_str)
                                .unwrap_or_default()
                                .to_string()
                        };
                        let matches = item
                            .get("matches")
                            .and_then(Value::as_array)
                            .map(|values| {
                                values
                                    .iter()
                                    .filter_map(Value::as_str)
                                    .take(MAX_MATCHES_PER_ITEM)
                                    .map(|snippet| {
                                        truncate_chars(snippet.trim(), MATCH_SNIPPET_CHARS)
                                    })
                                    .collect::<Vec<_>>()
                            })
                            .unwrap_or_default();
                        let reference = item
                            .get("path")
                            .and_then(Value::as_str)
                            .filter(|path| authorize_reference_path(path, extracts_root).is_some())
                            .map(reference_route);
                        let session_id = {
                            let explicit = text("session_id");
                            if explicit.is_empty() {
                                text("session")
                            } else {
                                explicit
                            }
                        };
                        json!({
                            "session_id": session_id,
                            "agent": text("agent"),
                            "date": text("date"),
                            "project": text("project"),
                            "kind": text("kind"),
                            "matches": matches,
                            "reference": reference,
                        })
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    /// `GET /api/aicx/search?q=<term>[&project=owner/repo]`
    pub async fn aicx_search(
        State(options): State<LeptosOptions>,
        Query(query): Query<std::collections::BTreeMap<String, String>>,
        request: Request,
    ) -> Response {
        if let Err(reason) = local_peer_access(peer_of(&request), options.site_addr) {
            return json_error(StatusCode::FORBIDDEN, reason);
        }
        let Some(term) = query.get("q").and_then(|raw| valid_query(raw)) else {
            return json_error(
                StatusCode::BAD_REQUEST,
                "a non-empty query of at most 512 characters, not starting with '-', is required",
            );
        };
        let project = match query.get("project").map(String::as_str) {
            None => None,
            Some(raw) if raw.trim().is_empty() => None,
            Some(raw) => match valid_project(raw) {
                Some(project) => Some(project.to_string()),
                None => {
                    return json_error(
                        StatusCode::BAD_REQUEST,
                        "project must be an exact owner/repo slug",
                    );
                }
            },
        };
        let Some(aicx_home) = aicx_home() else {
            return json_error(
                StatusCode::SERVICE_UNAVAILABLE,
                "AICX home cannot be resolved",
            );
        };
        let extracts_root = aicx_home.join("extracts");

        let mut command = tokio::process::Command::new(aicx_binary());
        command.args([
            "search",
            "--json",
            "--no-semantic",
            "--limit",
            AICX_RESULT_LIMIT,
        ]);
        if let Some(project) = &project {
            command.args(["-p", project]);
        }
        command.arg(term);
        command.stdin(std::process::Stdio::null());
        command.kill_on_drop(true);
        let output = tokio::time::timeout(aicx_timeout(), command.output()).await;
        match output {
            Ok(Ok(result)) if result.status.success() => {
                match serde_json::from_slice::<Value>(&result.stdout) {
                    Ok(payload) => {
                        let items = project_search_items(&payload, &extracts_root);
                        (
                            [(header::CACHE_CONTROL, "no-store")],
                            Json(json!({
                                "schema": "vibecrafted.aicx-search.v1",
                                "query": term,
                                "project": project,
                                "count": items.len(),
                                "items": items,
                            })),
                        )
                            .into_response()
                    }
                    Err(_) => json_error(
                        StatusCode::BAD_GATEWAY,
                        "AICX returned a response that is not JSON",
                    ),
                }
            }
            Ok(Ok(result)) => {
                let detail = String::from_utf8_lossy(&result.stderr);
                let line = detail
                    .lines()
                    .rev()
                    .find(|line| !line.trim().is_empty())
                    .unwrap_or("AICX search failed");
                json_error(StatusCode::BAD_GATEWAY, &truncate_chars(line.trim(), 240))
            }
            Ok(Err(_)) => json_error(
                StatusCode::SERVICE_UNAVAILABLE,
                "the AICX executable is not available to vc-server",
            ),
            Err(_) => json_error(StatusCode::GATEWAY_TIMEOUT, "AICX search timed out"),
        }
    }

    /// `GET /api/aicx/reference?path=<absolute extract path>`
    ///
    /// A machine document answering a main-frame navigation: the App's console
    /// diverts it into a read-only reference tab, a browser shows plain text.
    pub async fn aicx_reference(
        State(options): State<LeptosOptions>,
        Query(query): Query<std::collections::BTreeMap<String, String>>,
        request: Request,
    ) -> Response {
        if let Err(reason) = local_peer_access(peer_of(&request), options.site_addr) {
            return json_error(StatusCode::FORBIDDEN, reason);
        }
        let Some(raw) = query.get("path") else {
            return json_error(StatusCode::BAD_REQUEST, "path is required");
        };
        let Some(aicx_home) = aicx_home() else {
            return json_error(
                StatusCode::SERVICE_UNAVAILABLE,
                "AICX home cannot be resolved",
            );
        };
        let Some(path) = authorize_reference_path(raw, &aicx_home.join("extracts")) else {
            return json_error(
                StatusCode::NOT_FOUND,
                "no AICX reference document at that path",
            );
        };
        let (body, truncated) = match read_bounded(&path, MAX_REFERENCE_BYTES) {
            Some(read) => read,
            None => {
                return json_error(
                    StatusCode::NOT_FOUND,
                    "the reference document could not be read",
                );
            }
        };
        let mut headers = HeaderMap::new();
        headers.insert(
            header::CONTENT_TYPE,
            HeaderValue::from_static("text/plain; charset=utf-8"),
        );
        headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
        headers.insert(
            header::X_CONTENT_TYPE_OPTIONS,
            HeaderValue::from_static("nosniff"),
        );
        headers.insert(
            header::CONTENT_SECURITY_POLICY,
            HeaderValue::from_static("default-src 'none'; sandbox"),
        );
        if truncated {
            headers.insert("x-vibecrafted-truncated", HeaderValue::from_static("true"));
        }
        (StatusCode::OK, headers, body).into_response()
    }

    fn read_bounded(path: &Path, limit: u64) -> Option<(Vec<u8>, bool)> {
        use std::io::Read;
        let file = std::fs::File::open(path).ok()?;
        let len = file.metadata().ok()?.len();
        let mut bytes = Vec::with_capacity(len.min(limit) as usize);
        file.take(limit).read_to_end(&mut bytes).ok()?;
        Some((bytes, len > limit))
    }

    // ------------------------------------------------------------------
    // Loctree report
    // ------------------------------------------------------------------

    /// The one report the canonical state view knows: `<root>/.loctree/report.html`
    /// for the first active or recent run root that has one. `loct report
    /// --output .loctree/report.html` writes it there together with its assets.
    pub(crate) fn locate_report(plane: &ControlPlane) -> Option<PathBuf> {
        let view = plane.read_state_view();
        view.active_runs
            .iter()
            .chain(view.recent_runs.iter())
            .map(|run| Path::new(&run.root).join(".loctree/report.html"))
            .find(|path| {
                std::fs::symlink_metadata(path)
                    .map(|meta| meta.is_file() && !meta.file_type().is_symlink())
                    .unwrap_or(false)
            })
    }

    /// Removes the report's own `<meta http-equiv="Content-Security-Policy">`.
    ///
    /// Loctree writes that tag for the `file://` case. Under the server's
    /// `sandbox` policy the document has an opaque origin, so its `'self'`
    /// sources would match nothing and the graph script could not load. The
    /// header policy below is the one that applies here.
    pub(crate) fn strip_meta_csp(html: &str) -> String {
        const NEEDLE: &str = "<meta http-equiv=\"Content-Security-Policy\"";
        let mut out = String::with_capacity(html.len());
        let mut rest = html;
        while let Some(start) = rest.find(NEEDLE) {
            out.push_str(&rest[..start]);
            match rest[start..].find('>') {
                Some(end) => rest = &rest[start + end + 1..],
                None => {
                    rest = "";
                }
            }
        }
        out.push_str(rest);
        out
    }

    /// Script installed at the top of `<head>` when the report is served under
    /// the sandbox policy. The document's origin is opaque, so `window.localStorage`
    /// and `window.sessionStorage` throw `SecurityError` on access (Chromium,
    /// WebKit) — and Loctree's inline scripts read them at top level, so the
    /// tab wiring and the theme toggle never get installed. The stand-in is a
    /// per-document in-memory `Storage` look-alike (`getItem`, `setItem`,
    /// `removeItem`, `clear`, `key`, `length`), one independent instance per
    /// storage name: nothing persists across loads, nothing crosses the origin
    /// boundary, and `localStorage` never sees `sessionStorage`. Where the real
    /// storage is usable the script leaves it alone. Behaviour is proven by
    /// `tests/acceptance/storage_shim_probe.mjs` (run from
    /// [`tests::storage_stand_ins_are_independent`] when `node` is on `PATH`).
    const STORAGE_SHIM: &str = concat!(
        "<script data-vibecrafted=\"storage-shim\">",
        "(function(){",
        // One stand-in per call: `store` belongs to that call's closure alone,
        // so `localStorage` and `sessionStorage` never share or clear each other.
        "function standIn(){var store=Object.create(null);var shim={",
        "getItem:function(k){k=String(k);return k in store?store[k]:null;},",
        "setItem:function(k,v){store[String(k)]=String(v);},",
        "removeItem:function(k){delete store[String(k)];},",
        "clear:function(){store=Object.create(null);},",
        "key:function(n){var keys=Object.keys(store);return n<keys.length?keys[n]:null;}};",
        "Object.defineProperty(shim,\"length\",{get:function(){return Object.keys(store).length;}});",
        "return shim;}",
        "var names=[\"localStorage\",\"sessionStorage\"];",
        "for(var i=0;i<names.length;i++){var name=names[i];var usable=false;",
        "try{var real=window[name];if(real){real.getItem(\"vibecrafted-storage-probe\");usable=true;}}catch(e){}",
        "if(usable){continue;}",
        "try{Object.defineProperty(window,name,{value:standIn(),configurable:true,writable:true});}catch(e){}",
        "}})();",
        "</script>"
    );

    /// Position just past the opening `<head>` tag (or `<html>` when the
    /// document has no head), matched case-insensitively on ASCII.
    fn opening_tag_end(html: &str, tag: &str) -> Option<usize> {
        let lower = html.to_ascii_lowercase();
        let mut from = 0;
        while let Some(rel) = lower[from..].find(tag) {
            let start = from + rel;
            let after = start + tag.len();
            let boundary = lower[after..]
                .chars()
                .next()
                .is_some_and(|c| c == '>' || c.is_ascii_whitespace() || c == '/');
            if boundary {
                return lower[after..].find('>').map(|gt| after + gt + 1);
            }
            from = after;
        }
        None
    }

    /// Installs [`STORAGE_SHIM`] as the first script of the document so it
    /// runs before any report script. Idempotent: a document that already
    /// carries the shim is returned unchanged.
    pub(crate) fn inject_storage_shim(html: &str) -> String {
        if html.contains("data-vibecrafted=\"storage-shim\"") {
            return html.to_string();
        }
        let at = opening_tag_end(html, "<head").or_else(|| opening_tag_end(html, "<html"));
        match at {
            Some(at) => format!("{}{}{}", &html[..at], STORAGE_SHIM, &html[at..]),
            None => format!("{STORAGE_SHIM}{html}"),
        }
    }

    /// The document as served: Loctree's `file://` policy removed, the
    /// storage stand-in installed. Everything else is the report as written.
    pub(crate) fn adapt_report(html: &str) -> String {
        inject_storage_shim(&strip_meta_csp(html))
    }

    /// A `Host` header value safe to place in a CSP source expression.
    fn csp_host(headers: &HeaderMap) -> Option<&str> {
        let host = headers.get(header::HOST)?.to_str().ok()?.trim();
        let ok = !host.is_empty()
            && host.len() <= 255
            && host.bytes().all(|byte| {
                byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b':' | b'[' | b']')
            });
        ok.then_some(host)
    }

    /// The policy the report runs under. Scheme-less host sources match the
    /// scheme the document was served with.
    pub(crate) fn report_csp(host: Option<&str>) -> String {
        let assets = host
            .map(|host| format!(" {host}/structure/report/"))
            .unwrap_or_default();
        format!(
            "default-src 'none'; \
             script-src 'unsafe-inline' 'unsafe-eval'{assets}; \
             style-src 'unsafe-inline' https://fonts.googleapis.com; \
             font-src data: https://fonts.gstatic.com; \
             img-src data: blob:; \
             connect-src 'none'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'; \
             sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox"
        )
    }

    fn report_headers(content_type: &'static str, csp: &str) -> HeaderMap {
        let mut headers = HeaderMap::new();
        headers.insert(header::CONTENT_TYPE, HeaderValue::from_static(content_type));
        headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
        headers.insert(
            header::X_CONTENT_TYPE_OPTIONS,
            HeaderValue::from_static("nosniff"),
        );
        headers.insert(
            header::REFERRER_POLICY,
            HeaderValue::from_static("no-referrer"),
        );
        if let Ok(value) = HeaderValue::from_str(csp) {
            headers.insert(header::CONTENT_SECURITY_POLICY, value);
        }
        headers
    }

    fn plain(status: StatusCode, body: &'static str) -> Response {
        (status, [(header::CACHE_CONTROL, "no-store")], body).into_response()
    }

    /// `GET /structure/report` — the document lives at the directory-style URL
    /// so its relative asset references resolve; send the caller there.
    pub async fn loctree_report_redirect() -> Response {
        (
            StatusCode::PERMANENT_REDIRECT,
            [
                (header::LOCATION, "/structure/report/"),
                (header::CACHE_CONTROL, "no-store"),
            ],
        )
            .into_response()
    }

    /// `GET /structure/report/`
    pub async fn loctree_report(headers: HeaderMap) -> Response {
        let plane = ControlPlane::from_env();
        let Some(report) = locate_report(&plane) else {
            return plain(
                StatusCode::NOT_FOUND,
                "No Loctree report is known for the canonical workspace roots. Run `loct report --output .loctree/report.html` in the workspace root.",
            );
        };
        let Ok(html) = std::fs::read_to_string(&report) else {
            return plain(
                StatusCode::NOT_FOUND,
                "The Loctree report could not be read.",
            );
        };
        let csp = report_csp(csp_host(&headers));
        (
            StatusCode::OK,
            report_headers("text/html; charset=utf-8", &csp),
            adapt_report(&html),
        )
            .into_response()
    }

    /// One sibling asset name Loctree writes next to `report.html`.
    pub(crate) fn valid_asset_name(name: &str) -> Option<&'static str> {
        let bytes = name.as_bytes();
        if bytes.is_empty()
            || bytes.len() > 128
            || !bytes[0].is_ascii_alphanumeric()
            || !bytes
                .iter()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
            || name.contains("..")
        {
            return None;
        }
        match name.rsplit_once('.')?.1 {
            "js" => Some("text/javascript; charset=utf-8"),
            "css" => Some("text/css; charset=utf-8"),
            "json" => Some("application/json"),
            "map" => Some("application/json"),
            "svg" => Some("image/svg+xml"),
            "png" => Some("image/png"),
            "woff" => Some("font/woff"),
            "woff2" => Some("font/woff2"),
            _ => None,
        }
    }

    /// `GET /structure/report/{asset}` — only a regular file that sits in the
    /// report's own directory, under the same sandbox policy.
    pub async fn loctree_report_asset(
        axum::extract::Path(asset): axum::extract::Path<String>,
        headers: HeaderMap,
    ) -> Response {
        let Some(content_type) = valid_asset_name(&asset) else {
            return plain(StatusCode::NOT_FOUND, "no such report asset");
        };
        let plane = ControlPlane::from_env();
        let Some(report) = locate_report(&plane) else {
            return plain(StatusCode::NOT_FOUND, "no Loctree report is available");
        };
        let Some(directory) = report.parent() else {
            return plain(StatusCode::NOT_FOUND, "no such report asset");
        };
        let candidate = directory.join(&asset);
        let Ok(meta) = std::fs::symlink_metadata(&candidate) else {
            return plain(StatusCode::NOT_FOUND, "no such report asset");
        };
        if !meta.is_file() || meta.file_type().is_symlink() || meta.len() > MAX_ASSET_BYTES {
            return plain(StatusCode::NOT_FOUND, "no such report asset");
        }
        let Ok(bytes) = std::fs::read(&candidate) else {
            return plain(StatusCode::NOT_FOUND, "the report asset could not be read");
        };
        let csp = report_csp(csp_host(&headers));
        (StatusCode::OK, report_headers(content_type, &csp), bytes).into_response()
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        fn addr(value: &str) -> SocketAddr {
            value.parse().expect("socket address")
        }

        #[test]
        fn local_peer_access_admits_loopback_and_same_interface_only() {
            let loopback_bind = addr("127.0.0.1:3024");
            assert!(local_peer_access(Some(addr("127.0.0.1:50000")), loopback_bind).is_ok());
            assert!(local_peer_access(Some(addr("[::1]:50000")), loopback_bind).is_ok());
            assert!(local_peer_access(None, loopback_bind).is_err());

            let tailnet_bind = addr("100.82.232.70:3025");
            assert!(local_peer_access(Some(addr("100.82.232.70:50000")), tailnet_bind).is_ok());
            assert!(local_peer_access(Some(addr("100.82.232.71:50000")), tailnet_bind).is_err());
            assert!(local_peer_access(Some(addr("127.0.0.1:50000")), tailnet_bind).is_ok());

            let any_bind = addr("0.0.0.0:3025");
            assert!(local_peer_access(Some(addr("192.168.1.20:50000")), any_bind).is_err());
            assert!(local_peer_access(Some(addr("0.0.0.0:50000")), any_bind).is_err());
            assert!(local_peer_access(Some(addr("127.0.0.1:50000")), any_bind).is_ok());
        }

        #[test]
        fn project_slug_is_exact_owner_repo() {
            assert_eq!(
                valid_project("vetcoders/vibecrafted"),
                Some("vetcoders/vibecrafted")
            );
            assert_eq!(valid_project(" Loctree/aicx "), Some("Loctree/aicx"));
            for bad in [
                "",
                "vibecrafted",
                "a/b/c",
                "../x",
                "owner/",
                "/repo",
                "o/.hidden",
                "o/r epo",
            ] {
                assert!(valid_project(bad).is_none(), "{bad:?} accepted");
            }
        }

        #[test]
        fn query_rejects_flags_and_control_characters() {
            assert_eq!(valid_query("  native tabs "), Some("native tabs"));
            assert!(valid_query("").is_none());
            assert!(valid_query("--help").is_none());
            assert!(valid_query("a\u{7}b").is_none());
            assert!(valid_query(&"x".repeat(513)).is_none());
        }

        #[test]
        fn asset_names_are_plain_siblings_with_known_types() {
            assert_eq!(
                valid_asset_name("loctree-cytoscape.min.js"),
                Some("text/javascript; charset=utf-8")
            );
            assert_eq!(
                valid_asset_name("report.css"),
                Some("text/css; charset=utf-8")
            );
            for bad in [
                "",
                "../report.html",
                ".hidden.js",
                "a/b.js",
                "report.html",
                "x.exe",
                "no-extension",
            ] {
                assert!(valid_asset_name(bad).is_none(), "{bad:?} accepted");
            }
        }

        #[test]
        fn meta_csp_is_removed_and_header_policy_sandboxes_with_explicit_assets() {
            let html = "<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'self' file:; script-src 'self'\"><title>R</title></head><body><script src=\"loctree-cytoscape.min.js\"></script></body></html>";
            let stripped = strip_meta_csp(html);
            assert!(!stripped.contains("Content-Security-Policy"));
            assert!(stripped.contains("<meta charset=\"UTF-8\"><title>R</title>"));
            assert!(stripped.contains("loctree-cytoscape.min.js"));

            let csp = report_csp(Some("127.0.0.1:3024"));
            assert!(csp.contains("sandbox allow-scripts"));
            assert!(!csp.contains("allow-same-origin"));
            assert!(csp.contains("connect-src 'none'"));
            assert!(csp.contains("form-action 'none'"));
            assert!(csp.contains(
                "script-src 'unsafe-inline' 'unsafe-eval' 127.0.0.1:3024/structure/report/"
            ));
            assert!(!report_csp(None).contains("/structure/report/"));
        }

        #[test]
        fn storage_shim_is_the_first_script_and_installs_once() {
            let html = "<!DOCTYPE html>\n<html><head><meta charset=\"UTF-8\"><title>R</title></head><body><script>localStorage.getItem('loctree-theme')</script><script src=\"loctree-cytoscape.min.js\"></script></body></html>";
            let adapted = adapt_report(html);
            let shim_at = adapted
                .find("<script data-vibecrafted=\"storage-shim\">")
                .expect("shim present");
            assert_eq!(
                &adapted[..shim_at],
                "<!DOCTYPE html>\n<html><head>",
                "shim goes right after <head>"
            );
            assert!(shim_at < adapted.find("localStorage.getItem").expect("report script"));
            assert_eq!(adapted.matches("storage-shim").count(), 1);
            assert_eq!(adapt_report(&adapted), adapted, "adapting twice is a no-op");
            assert!(adapted.ends_with("</script></body></html>"));
            // The shim itself only ever replaces a storage that throws.
            assert!(STORAGE_SHIM.contains("catch(e){}"));
            assert!(STORAGE_SHIM.contains("if(usable){continue;}"));
            assert!(!STORAGE_SHIM.contains("allow-same-origin"));
            // Each storage gets its own closure, never a loop-shared binding.
            assert!(STORAGE_SHIM.contains("value:standIn()"));
            assert_eq!(STORAGE_SHIM.matches("var store=").count(), 1);
            assert!(
                STORAGE_SHIM.find("var store=").unwrap() < STORAGE_SHIM.find("for(var i").unwrap()
            );

            // Uppercase / attributed head, then no head at all.
            let upper =
                inject_storage_shim("<HTML><HEAD lang=\"en\"><TITLE>x</TITLE></HEAD></HTML>");
            assert!(upper.starts_with("<HTML><HEAD lang=\"en\"><script data-vibecrafted"));
            let headless = inject_storage_shim("<html><body>report</body></html>");
            assert!(headless.starts_with("<html><script data-vibecrafted"));
            let bare = inject_storage_shim("<p>fragment</p>");
            assert!(bare.starts_with("<script data-vibecrafted"));
            assert!(bare.ends_with("<p>fragment</p>"));
            // `<header>` is not `<head>`.
            let header_only = inject_storage_shim("<header>x</header>");
            assert!(header_only.starts_with("<script data-vibecrafted"));
        }

        /// The injected script, evaluated as a browser would under an opaque
        /// origin: `localStorage` and `sessionStorage` must be two independent
        /// stand-ins, and a usable real storage must be left alone. Skips with a
        /// note when `node` is not runnable on this host.
        #[test]
        fn storage_stand_ins_are_independent() {
            let probe = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/acceptance/storage_shim_probe.mjs");
            assert!(probe.is_file(), "missing {}", probe.display());
            let html = adapt_report(
                "<!DOCTYPE html><html><head><title>R</title></head><body></body></html>",
            );
            let dir =
                std::env::temp_dir().join(format!("vc-storage-shim-probe-{}", std::process::id()));
            std::fs::create_dir_all(&dir).expect("probe dir");
            let document = dir.join("report.html");
            std::fs::write(&document, html).expect("probe document");
            let output = std::process::Command::new("node")
                .arg(&probe)
                .arg(&document)
                .output();
            let _ = std::fs::remove_dir_all(&dir);
            let output = match output {
                Ok(output) => output,
                Err(error) => {
                    println!("skipped: `node` is not runnable on this host ({error})");
                    return;
                }
            };
            let stdout = String::from_utf8_lossy(&output.stdout);
            assert!(
                output.status.success(),
                "storage stand-ins are not independent:\n{stdout}\n{}",
                String::from_utf8_lossy(&output.stderr)
            );
            assert!(stdout.contains("\"pass\": true"), "{stdout}");
        }
    }
}
