//! Local-only access boundary for the Skills catalog and editor.

use std::net::{IpAddr, SocketAddr};

use axum::extract::{ConnectInfo, Request, State};
use axum::http::{HeaderMap, Method, StatusCode, Uri, header, uri::Authority};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use leptos::config::LeptosOptions;

fn is_skills_path(path: &str) -> bool {
    fn is_target(path: &str) -> bool {
        path == "/skills" || path.starts_with("/skills/") || path == "/api/skills/file"
    }
    if is_target(path) {
        return true;
    }
    // Be conservative if another router layer decodes percent-encoded paths.
    let bytes = path.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' && index + 2 < bytes.len() {
            if let (Some(hi), Some(lo)) = (
                (bytes[index + 1] as char).to_digit(16),
                (bytes[index + 2] as char).to_digit(16),
            ) {
                decoded.push(((hi << 4) | lo) as u8);
                index += 3;
                continue;
            }
        }
        decoded.push(bytes[index]);
        index += 1;
    }
    std::str::from_utf8(&decoded).is_ok_and(is_target)
}

fn one_header<'a>(headers: &'a HeaderMap, name: &str) -> Option<&'a str> {
    let mut values = headers.get_all(name).iter();
    let value = values.next()?.to_str().ok()?;
    if values.next().is_some() {
        return None;
    }
    Some(value)
}

fn trusted_host(value: &str, bind: SocketAddr) -> Option<Authority> {
    if value.contains('@') || value.ends_with(':') {
        return None;
    }
    let authority: Authority = value.parse().ok()?;
    if (authority.port().is_some() && authority.port_u16().is_none())
        || authority.port_u16().unwrap_or(80) != bind.port()
    {
        return None;
    }
    let host = authority.host();
    let host_ip = host
        .trim_start_matches('[')
        .trim_end_matches(']')
        .parse::<IpAddr>()
        .ok()
        .map(|ip| ip.to_canonical());
    let bind_ip = bind.ip().to_canonical();
    let local_name = host.eq_ignore_ascii_case("localhost");
    let local_ip = host_ip.is_some_and(|ip| ip.is_loopback());
    let bound_ip = !bind_ip.is_unspecified() && host_ip == Some(bind_ip);
    if local_name || local_ip || bound_ip {
        Some(authority)
    } else {
        None
    }
}

fn same_origin(headers: &HeaderMap, host: &Authority) -> bool {
    let Some(origin) = one_header(headers, "origin") else {
        return false;
    };
    let Ok(uri) = origin.parse::<Uri>() else {
        return false;
    };
    let Some(authority) = uri.authority() else {
        return false;
    };
    // Origin is a serialized origin, not an arbitrary URL or user-info authority.
    if uri.scheme_str() != Some("http")
        || authority.as_str().contains('@')
        || !origin.eq_ignore_ascii_case(&format!("http://{authority}"))
        || !authority.host().eq_ignore_ascii_case(host.host())
        || authority.port_u16().unwrap_or(80) != host.port_u16().unwrap_or(80)
    {
        return false;
    }
    match one_header(headers, "sec-fetch-site") {
        Some(value) => value == "same-origin",
        None => !headers.contains_key("sec-fetch-site"),
    }
}

fn permits(
    peer: Option<SocketAddr>,
    bind: SocketAddr,
    method: &Method,
    headers: &HeaderMap,
) -> bool {
    if crate::tools::api::local_peer_access(peer, bind).is_err() {
        return false;
    }
    let Some(host) = one_header(headers, "host").and_then(|value| trusted_host(value, bind)) else {
        return false;
    };
    if *method == Method::GET || *method == Method::HEAD {
        return true;
    }
    *method == Method::POST && same_origin(headers, &host)
}

/// Mounted around the router so both Leptos SSR and the file API are covered.
pub async fn skills_access_guard(
    State(options): State<LeptosOptions>,
    request: Request,
    next: Next,
) -> Response {
    if !is_skills_path(request.uri().path()) {
        return next.run(request).await;
    }
    let peer = request
        .extensions()
        .get::<ConnectInfo<SocketAddr>>()
        .map(|info| info.0);
    if request.uri().authority().is_some()
        || !permits(peer, options.site_addr, request.method(), request.headers())
    {
        return (
            StatusCode::FORBIDDEN,
            [(header::CACHE_CONTROL, "no-store")],
            "Skills are available only from this machine.",
        )
            .into_response();
    }
    let mut response = next.run(request).await;
    response.headers_mut().insert(
        header::CACHE_CONTROL,
        axum::http::HeaderValue::from_static("no-store"),
    );
    response
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;

    fn addr(value: &str) -> SocketAddr {
        value.parse().unwrap()
    }
    fn headers(host: &str, origin: Option<&str>, fetch_site: Option<&str>) -> HeaderMap {
        let mut headers = HeaderMap::new();
        headers.insert(header::HOST, HeaderValue::from_str(host).unwrap());
        if let Some(origin) = origin {
            headers.insert(header::ORIGIN, HeaderValue::from_str(origin).unwrap());
        }
        if let Some(site) = fetch_site {
            headers.insert("sec-fetch-site", HeaderValue::from_str(site).unwrap());
        }
        headers
    }

    #[test]
    fn skills_access_requires_local_peer_trusted_host_and_same_origin_for_save() {
        let bind = addr("127.0.0.1:3024");
        let local = Some(addr("127.0.0.1:1234"));
        let good = headers(
            "127.0.0.1:3024",
            Some("http://127.0.0.1:3024"),
            Some("same-origin"),
        );
        assert!(permits(local, bind, &Method::GET, &good));
        assert!(permits(local, bind, &Method::POST, &good));
        assert!(permits(
            local,
            bind,
            &Method::POST,
            &headers("localhost:3024", Some("http://localhost:3024"), None)
        ));
        assert!(!permits(None, bind, &Method::GET, &good));
        assert!(!permits(
            Some(addr("192.0.2.3:1234")),
            bind,
            &Method::GET,
            &good
        ));
        assert!(!permits(
            local,
            bind,
            &Method::POST,
            &headers("127.0.0.1:3024", None, None)
        ));
        assert!(!permits(
            local,
            bind,
            &Method::POST,
            &headers("127.0.0.1:3024", Some("null"), None)
        ));
        assert!(!permits(
            local,
            bind,
            &Method::POST,
            &headers(
                "127.0.0.1:3024",
                Some("http://evil.example"),
                Some("cross-site")
            )
        ));
        assert!(!permits(
            local,
            bind,
            &Method::GET,
            &headers("evil.example:3024", None, None)
        ));
        assert!(!permits(
            local,
            bind,
            &Method::POST,
            &headers(
                "evil.example:3024",
                Some("http://evil.example:3024"),
                Some("same-origin")
            )
        ));
        assert!(!permits(
            local,
            bind,
            &Method::POST,
            &headers("localhost:3024", Some("http://localhost:3025"), None)
        ));
        assert!(!permits(
            local,
            bind,
            &Method::POST,
            &headers(
                "localhost:3024",
                Some("http://localhost:3024"),
                Some("cross-site")
            )
        ));
        let mut duplicate = good.clone();
        duplicate.append(
            header::ORIGIN,
            HeaderValue::from_static("http://evil.example"),
        );
        assert!(!permits(local, bind, &Method::POST, &duplicate));
        assert!(is_skills_path("/skills/vc-init"));
        assert!(is_skills_path("/%73kills/vc-init"));
        assert!(is_skills_path("/api/skills%2ffile"));
        assert!(is_skills_path("/api/skills/file"));
        let ipv6_bind = addr("[::1]:3024");
        let ipv6_local = Some(addr("[::1]:1234"));
        assert!(permits(
            ipv6_local,
            ipv6_bind,
            &Method::POST,
            &headers("[::1]:3024", Some("http://[::1]:3024"), None)
        ));
        let tailnet_bind = addr("100.82.232.70:3024");
        assert!(permits(
            Some(addr("100.82.232.70:1234")),
            tailnet_bind,
            &Method::POST,
            &headers(
                "100.82.232.70:3024",
                Some("http://100.82.232.70:3024"),
                None
            )
        ));
        assert!(!permits(
            Some(addr("100.82.232.71:1234")),
            tailnet_bind,
            &Method::POST,
            &headers(
                "100.82.232.70:3024",
                Some("http://100.82.232.70:3024"),
                None
            )
        ));
        assert!(!is_skills_path("/settings"));
    }
    #[tokio::test]
    async fn mounted_guard_enforces_access_before_handler_and_disables_cache() {
        use axum::Router;
        use axum::body::Body;
        use axum::routing::{get, post};
        use tower::ServiceExt;

        let options = LeptosOptions::builder()
            .output_name("skills-access-test")
            .site_addr(addr("127.0.0.1:3024"))
            .build();
        let app: Router = Router::new()
            .route("/skills/{name}", get(|| async { "private" }))
            .route("/api/skills/file", post(|| async { "saved" }))
            .route("/other", get(|| async { "other" }))
            .layer(axum::middleware::from_fn_with_state(
                options,
                skills_access_guard,
            ));
        let request =
            |path: &str, method: Method, peer: Option<SocketAddr>, origin: Option<&str>| {
                let mut builder = Request::builder()
                    .uri(path)
                    .method(method)
                    .header(header::HOST, "127.0.0.1:3024");
                if let Some(peer) = peer {
                    builder = builder.extension(ConnectInfo(peer));
                }
                if let Some(origin) = origin {
                    builder = builder.header(header::ORIGIN, origin);
                }
                builder.body(Body::empty()).unwrap()
            };
        let local = Some(addr("127.0.0.1:1234"));
        let response = app
            .clone()
            .oneshot(request("/skills/vc-init", Method::GET, local, None))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.headers()[header::CACHE_CONTROL], "no-store");
        let response = app
            .clone()
            .oneshot(request("/skills/vc-init", Method::GET, None, None))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
        assert_eq!(response.headers()[header::CACHE_CONTROL], "no-store");
        assert_eq!(
            app.clone()
                .oneshot(request(
                    "/api/skills/file",
                    Method::POST,
                    local,
                    Some("http://evil.example")
                ))
                .await
                .unwrap()
                .status(),
            StatusCode::FORBIDDEN
        );
        assert_eq!(
            app.clone()
                .oneshot(request(
                    "/api/skills/file",
                    Method::POST,
                    local,
                    Some("http://127.0.0.1:3024")
                ))
                .await
                .unwrap()
                .status(),
            StatusCode::OK
        );
        assert_eq!(
            app.oneshot(request("/other", Method::GET, None, None))
                .await
                .unwrap()
                .status(),
            StatusCode::OK
        );
    }
}
