//! Bounded transport to the selected generation's local-skill owner.
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const MAX_REQUEST: usize = 1024 * 1024;
const MAX_RESPONSE: usize = 2 * 1024 * 1024;
const TIMEOUT: Duration = Duration::from_secs(30);
const BOOTSTRAP: &str = include_str!("skills_bridge.py");

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BridgeError {
    Invalid,
    Conflict,
    Unavailable,
    UnknownOutcome,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ReadSkill {
    pub name: String,
    pub text: String,
    pub revision: String,
    pub origin: String,
    pub writable: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PublishedSkill {
    pub name: String,
    pub revision: String,
    pub origin: String,
    pub activation: String,
    pub views: BTreeMap<String, String>,
}

#[derive(Serialize)]
struct Request<'a> {
    op: &'a str,
    name: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    text: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    revision: Option<&'a str>,
}

fn sha256(text: &str) -> String {
    format!("{:x}", Sha256::digest(text.as_bytes()))
}
fn valid_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn response(bytes: &[u8], success: bool) -> Result<serde_json::Value, BridgeError> {
    let value: serde_json::Value =
        serde_json::from_slice(bytes).map_err(|_| BridgeError::Unavailable)?;
    match (
        success,
        value.get("ok").and_then(serde_json::Value::as_bool),
    ) {
        (true, Some(true)) => Ok(value),
        (_, Some(false)) => match value.get("code").and_then(serde_json::Value::as_str) {
            Some("conflict") => Err(BridgeError::Conflict),
            Some("invalid" | "not_found") => Err(BridgeError::Invalid),
            _ => Err(BridgeError::Unavailable),
        },
        _ => Err(BridgeError::Unavailable),
    }
}

fn python_names(windows: bool) -> &'static [&'static str] {
    if windows {
        &["python.exe", "python3.exe"]
    } else {
        &["python3"]
    }
}

fn within_generation(root: &Path, resolved: &Path) -> bool {
    resolved.starts_with(root)
}

fn selected_python(root: &Path, windows: bool) -> Result<PathBuf, BridgeError> {
    let root = std::fs::canonicalize(root).map_err(|_| BridgeError::Unavailable)?;
    let names = python_names(windows);
    for name in names {
        let candidate = root.join("bin").join(name);
        if !candidate.is_file() {
            continue;
        }
        let resolved = std::fs::canonicalize(&candidate).map_err(|_| BridgeError::Unavailable)?;
        if !within_generation(&root, &resolved) {
            return Err(BridgeError::Unavailable);
        }
        #[cfg(unix)]
        if !windows {
            use std::os::unix::fs::PermissionsExt;
            if std::fs::metadata(&resolved)
                .map_err(|_| BridgeError::Unavailable)?
                .permissions()
                .mode()
                & 0o111
                == 0
            {
                return Err(BridgeError::Unavailable);
            }
        }
        return Ok(candidate);
    }
    Err(BridgeError::Unavailable)
}

fn invoke(request: &Request<'_>) -> Result<serde_json::Value, BridgeError> {
    let body = serde_json::to_vec(request).map_err(|_| BridgeError::Invalid)?;
    if body.len() > MAX_REQUEST {
        return Err(BridgeError::Invalid);
    }
    let root = std::env::var_os("VIBECRAFTED_RUNTIME_ROOT")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .filter(|v| v.is_absolute())
        .ok_or(BridgeError::Unavailable)?;
    let root = std::fs::canonicalize(root).map_err(|_| BridgeError::Unavailable)?;
    let python = selected_python(&root, cfg!(windows))?;
    let mut child = Command::new(python)
        .args(["-I", "-B", "-c", BOOTSTRAP])
        .env("VIBECRAFTED_RUNTIME_ROOT", &root)
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| BridgeError::Unavailable)?;
    let mut stdin = child.stdin.take().ok_or(BridgeError::Unavailable)?;
    let stdout = child.stdout.take().ok_or(BridgeError::Unavailable)?;
    let (writer_tx, writer_rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let _ = writer_tx.send(stdin.write_all(&body));
    });
    let (reader_tx, reader_rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let mut bytes = Vec::new();
        let result = stdout
            .take((MAX_RESPONSE + 1) as u64)
            .read_to_end(&mut bytes)
            .map(|_| bytes);
        let _ = reader_tx.send(result);
    });
    let deadline = Instant::now() + TIMEOUT;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(10)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(if request.op == "save" {
                    BridgeError::UnknownOutcome
                } else {
                    BridgeError::Unavailable
                });
            }
        }
    };
    let write_ok = writer_rx
        .recv_timeout(deadline.saturating_duration_since(Instant::now()))
        .ok()
        .is_some_and(|result| result.is_ok());
    let bytes = reader_rx
        .recv_timeout(deadline.saturating_duration_since(Instant::now()))
        .ok()
        .and_then(Result::ok)
        .ok_or_else(|| {
            if request.op == "save" {
                BridgeError::UnknownOutcome
            } else {
                BridgeError::Unavailable
            }
        })?;
    if !write_ok || bytes.len() > MAX_RESPONSE {
        return Err(if request.op == "save" {
            BridgeError::UnknownOutcome
        } else {
            BridgeError::Unavailable
        });
    }
    match response(&bytes, status.success()) {
        Err(BridgeError::Unavailable) if request.op == "save" => Err(BridgeError::UnknownOutcome),
        result => result,
    }
}

fn decode_read(value: serde_json::Value, name: &str) -> Result<ReadSkill, BridgeError> {
    let data: ReadSkill = serde_json::from_value(value).map_err(|_| BridgeError::Unavailable)?;
    if data.name != name
        || !matches!(data.origin.as_str(), "bundled" | "local")
        || !valid_hash(&data.revision)
        || sha256(&data.text) != data.revision
    {
        return Err(BridgeError::Unavailable);
    }
    Ok(data)
}

fn decode_publish(
    value: serde_json::Value,
    name: &str,
    text: &str,
) -> Result<PublishedSkill, BridgeError> {
    let data: PublishedSkill =
        serde_json::from_value(value).map_err(|_| BridgeError::UnknownOutcome)?;
    let valid_views = (3..=32).contains(&data.views.len())
        && ["agents", "claude", "codex"]
            .iter()
            .all(|name| data.views.contains_key(*name))
        && data.views.iter().all(|(name, status)| {
            !name.is_empty()
                && name.len() <= 32
                && name
                    .bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_' || c == b'-')
                && !status.is_empty()
                && status.len() <= 32
                && status
                    .bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_')
        });
    let all_ready = data
        .views
        .values()
        .all(|status| matches!(status.as_str(), "published" | "not_configured"));
    if data.name != name
        || data.origin != "local"
        || data.revision != sha256(text)
        || !valid_views
        || !matches!(data.activation.as_str(), "published" | "partial")
        || (data.activation == "published") != all_ready
    {
        return Err(BridgeError::UnknownOutcome);
    }
    Ok(data)
}

pub fn read(name: &str) -> Result<ReadSkill, BridgeError> {
    let value = invoke(&Request {
        op: "read",
        name,
        text: None,
        revision: None,
    })?;
    decode_read(value, name)
}

pub fn publish(name: &str, text: &str, revision: &str) -> Result<PublishedSkill, BridgeError> {
    if !valid_hash(revision) {
        return Err(BridgeError::Invalid);
    }
    let value = invoke(&Request {
        op: "save",
        name,
        text: Some(text),
        revision: Some(revision),
    })?;
    decode_publish(value, name, text)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn response_rejects_unverified_success_and_preserves_conflict() {
        assert!(matches!(
            response(br#"{"ok":false,"code":"conflict"}"#, false),
            Err(BridgeError::Conflict)
        ));
        assert!(response(br#"{"ok":true}"#, false).is_err());
        assert!(response(b"not json", true).is_err());
        assert!(response(br#"{"ok":false,"code":"unsafe"}"#, false).is_err());
    }
    #[test]
    fn read_and_publication_receipts_are_bound_to_contents_and_views() {
        let read = serde_json::json!({"name": "vc-alpha", "text": "hello", "revision": sha256("hello"), "origin": "bundled", "writable": true});
        assert!(decode_read(read.clone(), "vc-alpha").is_ok());
        assert!(decode_read(read.clone(), "vc-beta").is_err());
        let mut damaged = read;
        damaged["text"] = serde_json::json!("changed");
        assert!(decode_read(damaged, "vc-alpha").is_err());
        let receipt = serde_json::json!({"name": "vc-alpha", "revision": sha256("new"), "origin": "local", "activation": "partial", "views": {"agents": "published", "claude": "conflict", "codex": "missing_root"}});
        assert!(decode_publish(receipt.clone(), "vc-alpha", "new").is_ok());
        assert!(decode_publish(receipt.clone(), "vc-alpha", "different").is_err());
        let mut false_success = receipt.clone();
        false_success["activation"] = serde_json::json!("published");
        assert!(decode_publish(false_success, "vc-alpha", "new").is_err());
        let mut future = receipt;
        future["views"]["new_runtime"] = serde_json::json!("unknown_state");
        assert!(decode_publish(future.clone(), "vc-alpha", "new").is_ok());
        future["activation"] = serde_json::json!("published");
        assert!(decode_publish(future, "vc-alpha", "new").is_err());
        let published = serde_json::json!({"name": "vc-alpha", "revision": sha256("new"), "origin": "local", "activation": "published", "views": {"agents": "published", "claude": "published", "codex": "published", "gemini": "not_configured"}});
        assert!(decode_publish(published, "vc-alpha", "new").is_ok());
    }
    #[test]
    fn selected_interpreter_names_and_confinement() {
        assert_eq!(python_names(true), ["python.exe", "python3.exe"]);
        assert_eq!(python_names(false), ["python3"]);
        let root = Path::new("generation");
        assert!(within_generation(root, Path::new("generation/bin/python3")));
        assert!(!within_generation(root, Path::new("foreign/python3")));
        assert!(!within_generation(
            root,
            Path::new("generation-other/bin/python3")
        ));
    }
}
