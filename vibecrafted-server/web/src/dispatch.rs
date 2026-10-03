//! Claim transport only. The canonical Python writer owns every durable byte.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;

use axum::body::to_bytes;
use axum::extract::{ConnectInfo, Request};
use axum::http::{StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::post;
use axum::{Json, Router};
use serde_json::{Value, json};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::process::Command;

const MAX_CLAIM_BYTES: usize = 64 * 1024;
const WRITER_TIMEOUT: Duration = Duration::from_secs(30);
const GENERATION_BOOTSTRAP: &str = "import sys, runpy; from pathlib import Path; root = Path(sys.argv[1]); sys.path[:0] = [str(root / 'vibecrafted-core'), str(root / 'python-site')]; runpy.run_module('vibecrafted_core.dispatch.claims', run_name='__main__')";

pub fn dispatch_routes() -> Router<leptos::config::LeptosOptions> {
    Router::new().route("/api/dispatch/claim", post(receive_claim))
}

fn error(status: StatusCode, message: &str) -> Response {
    (
        status,
        [(header::CACHE_CONTROL, "no-store")],
        Json(json!({"status": "rejected", "error": message})),
    )
        .into_response()
}

fn generation_python() -> Result<PathBuf, &'static str> {
    // Match the scaffold dispatch door: only the server's selected generation
    // supplies an interpreter. Neither PATH nor a request supplies executable
    // code, a module root, an argv, or a working directory.
    let path = std::env::var_os("VIBECRAFTED_PYTHON")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("VIBECRAFTED_RUNTIME_ROOT")
                .filter(|value| !value.is_empty())
                .map(|root| PathBuf::from(root).join("bin/python3"))
        })
        .ok_or("generation Python is unavailable")?;
    if !path.is_absolute() {
        return Err("generation Python must be absolute");
    }
    let metadata = std::fs::metadata(&path).map_err(|_| "generation Python is unavailable")?;
    if !metadata.is_file() {
        return Err("generation Python is unavailable");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o111 == 0 {
            return Err("generation Python is not executable");
        }
    }
    Ok(path)
}

pub async fn receive_claim(request: Request) -> Response {
    let local_peer = request
        .extensions()
        .get::<ConnectInfo<SocketAddr>>()
        .is_some_and(|info| info.0.ip().to_canonical().is_loopback());
    // This is a headless worker door. Browser-origin requests (including null
    // sandbox origins) are refused even when the browser itself is on loopback.
    if !local_peer
        || request.headers().contains_key(header::ORIGIN)
        || request
            .headers()
            .get("sec-fetch-site")
            .is_some_and(|value| value != "none")
    {
        return error(
            StatusCode::FORBIDDEN,
            "claim transport requires a local headless peer",
        );
    }
    if !request
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.split(';').next().unwrap_or("").trim() == "application/json")
    {
        return error(
            StatusCode::UNSUPPORTED_MEDIA_TYPE,
            "claim body must be JSON",
        );
    }
    let body = match to_bytes(request.into_body(), MAX_CLAIM_BYTES).await {
        Ok(body) => body,
        Err(_) => {
            return error(
                StatusCode::PAYLOAD_TOO_LARGE,
                "claim body exceeds the limit",
            );
        }
    };
    if !serde_json::from_slice::<Value>(&body).is_ok_and(|value| value.is_object()) {
        return error(StatusCode::BAD_REQUEST, "claim body must be a JSON object");
    }
    let python = match generation_python() {
        Ok(python) => python,
        Err(message) => return error(StatusCode::SERVICE_UNAVAILABLE, message),
    };
    let mut command = Command::new(python);
    command.arg("-I");
    if let Some(root) = std::env::var_os("VIBECRAFTED_RUNTIME_ROOT")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
    {
        // Runtime packs install modules beside the interpreter, rather than
        // into its site-packages. -I intentionally ignores the bootstrap's
        // PYTHONPATH; add only these server-owned generation directories.
        if !root.is_absolute() || !root.join("vibecrafted-core").is_dir() {
            return error(
                StatusCode::SERVICE_UNAVAILABLE,
                "canonical writer generation is unavailable",
            );
        }
        command.args(["-c", GENERATION_BOOTSTRAP]).arg(root);
    } else {
        command.args(["-m", "vibecrafted_core.dispatch.claims"]);
    }
    let mut child = match command
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()
    {
        Ok(child) => child,
        Err(_) => {
            return error(
                StatusCode::SERVICE_UNAVAILABLE,
                "canonical writer is unavailable",
            );
        }
    };
    let result = tokio::time::timeout(WRITER_TIMEOUT, async {
        let mut input = child.stdin.take().ok_or(())?;
        input.write_all(&body).await.map_err(|_| ())?;
        drop(input);
        let output = child.stdout.take().ok_or(())?;
        let mut bytes = Vec::new();
        output
            .take((MAX_CLAIM_BYTES + 1) as u64)
            .read_to_end(&mut bytes)
            .await
            .map_err(|_| ())?;
        if bytes.len() > MAX_CLAIM_BYTES {
            return Err(());
        }
        let status = child.wait().await.map_err(|_| ())?;
        Ok((status, bytes))
    })
    .await;
    let (status, bytes) = match result {
        Ok(Ok(result)) => result,
        Err(_) => {
            return error(
                StatusCode::GATEWAY_TIMEOUT,
                "canonical writer timed out; inspect receipts before retrying",
            );
        }
        Ok(Err(())) => return error(StatusCode::BAD_GATEWAY, "canonical writer transport failed"),
    };
    // A receipt is written by Python before this acknowledgement. A successful
    // transport is never verification or settlement, and no writer output may
    // turn this endpoint into a delivery stamp.
    if status.code() == Some(2) {
        return error(
            StatusCode::BAD_REQUEST,
            "canonical writer rejected the claim",
        );
    }
    let result = match serde_json::from_slice::<Value>(&bytes) {
        Ok(result)
            if status.success()
                && result["status"] == "claim_received"
                && result["marker"] == "[~]"
                && result["verification"] == "unverified"
                && result["writer"] == "vibecrafted_core.dispatch.claims" =>
        {
            result
        }
        _ => {
            return error(
                StatusCode::BAD_GATEWAY,
                "canonical writer did not acknowledge an unverified claim",
            );
        }
    };
    (
        StatusCode::ACCEPTED,
        [(header::CACHE_CONTROL, "no-store")],
        Json(result),
    )
        .into_response()
}
