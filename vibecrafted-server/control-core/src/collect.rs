//! On-demand, one-shot local usage collection owned by vc-server.
//!
//! Explicit requests (`?collect=1` on the usage endpoints) run the canonical
//! Python telemetry engines exactly once per provider — no daemons, no
//! permanent per-provider children — and report every outcome honestly. The
//! collectors write `quota.json`/`scan-cache.json` under
//! `<home>/telemetry/<agent>/`; this module only drives them and transports
//! their receipt, it never invents quota state.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde::{Deserialize, Serialize};

use crate::usage::{explicit_python, runtime_python, source_core_dir};

pub const COLLECTION_SCHEMA: &str = "vibecrafted.usage-collection.v1";

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct CollectionOutcome {
    pub agent: String,
    pub status: String,
    pub detail: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct CollectionReport {
    pub schema: String,
    pub generated_at: String,
    pub ok: bool,
    #[serde(default)]
    pub outcomes: Vec<CollectionOutcome>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl CollectionReport {
    fn unavailable(error: String) -> Self {
        Self {
            schema: COLLECTION_SCHEMA.to_string(),
            generated_at: chrono::Utc::now().to_rfc3339(),
            ok: false,
            outcomes: Vec::new(),
            error: Some(error),
        }
    }
}

/// Run the one-shot collection driver against `home` and return its receipt.
///
/// Unavailable Python, a missing driver, a non-zero exit, or an unparseable
/// receipt all produce an honest `ok: false` report — never a panic and never
/// fabricated per-agent state.
#[must_use]
pub fn collect_local_usage(home: &Path) -> CollectionReport {
    let Some(mut command) = collector_command(home) else {
        return CollectionReport::unavailable(
            "collection driver or Python runtime is not present in this installation".to_string(),
        );
    };
    let output = match command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
    {
        Ok(output) => output,
        Err(error) => {
            return CollectionReport::unavailable(format!(
                "collection driver failed to start: {error}"
            ));
        }
    };
    let stdout = String::from_utf8_lossy(&output.stdout);
    let trimmed = stdout.trim();
    match serde_json::from_str::<CollectionReport>(trimmed) {
        Ok(mut report) => {
            report.schema = COLLECTION_SCHEMA.to_string();
            if !output.status.success() && report.ok {
                report.ok = false;
                report.error = Some(format!("collection driver exited {}", output.status));
            }
            report
        }
        Err(error) => {
            let stderr = String::from_utf8_lossy(&output.stderr);
            let tail = stderr
                .lines()
                .last()
                .unwrap_or("")
                .chars()
                .take(200)
                .collect::<String>();
            CollectionReport::unavailable(format!(
                "collection driver returned an unreadable receipt: {error}; {tail}"
            ))
        }
    }
}

fn collector_command(home: &Path) -> Option<Command> {
    let core = source_core_dir()?;
    let script: PathBuf = core.join("vibecrafted_core/runtime/telemetry/collect_all.py");
    if !script.is_file() {
        return None;
    }
    let executable = explicit_python("VIBECRAFTED_PYTHON")
        .or_else(runtime_python)
        .unwrap_or_else(|| PathBuf::from("python3"));
    let mut command = Command::new(executable);
    command
        .arg(script)
        .arg("--home")
        .arg(home)
        // The driver owns collection; an inherited PYTHONPATH could bind it to
        // a foreign vibecrafted_core. The source checkout is the authority.
        .env("PYTHONPATH", &core)
        .env("VIBECRAFTED_HOME", home);
    Some(command)
}
