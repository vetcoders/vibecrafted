//! `vc-server`-owned collection of provider usage and quota observations.
//!
//! One owner, no resident children. A collection is a bounded one-shot of the
//! existing Python parsers (`fleet_engine` for six providers, the kimi harvest,
//! the agy snapshot) run by the server on its own schedule or on request, which
//! writes the same `<home>/telemetry/` projections the report reads. Nothing
//! here parses a provider format itself, and no per-provider monitor has to be
//! alive for the numbers to move.
//!
//! Two modes, and only the second may touch the network:
//!
//! * `local` — scan host session stores; reads only files on this machine;
//! * `quota` — ask a provider-owned source whether quota is exhausted (the kimi
//!   probe), strictly on explicit request or task preflight.
//!
//! Providers without a quota source report `unavailable` — never a guess.

use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::ControlPlane;
use crate::usage::{USAGE_PROVIDER_ADAPTERS, telemetry_python};

const COLLECT_TIMEOUT: Duration = Duration::from_secs(90);
const QUOTA_TIMEOUT: Duration = Duration::from_secs(45);
const STAMP_FILE: &str = ".collect-local.json";

static SINGLE_FLIGHT: Mutex<()> = Mutex::new(());

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CollectMode {
    Local,
    Quota,
}

impl CollectMode {
    fn name(self) -> &'static str {
        match self {
            Self::Local => "local",
            Self::Quota => "quota",
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct AgentCollect {
    pub agent: String,
    /// `ok`, `unavailable`, `error`, `timeout` or `skipped`.
    pub status: String,
    pub detail: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct CollectOutcome {
    pub mode: String,
    pub started: bool,
    pub reason: String,
    pub agents: Vec<AgentCollect>,
}

impl CollectOutcome {
    fn skipped(mode: CollectMode, reason: &str) -> Self {
        Self {
            mode: mode.name().to_string(),
            started: false,
            reason: reason.to_string(),
            agents: Vec::new(),
        }
    }
}

const COLLECT_SCRIPT: &str = r#"
import contextlib, importlib.util, io, json, sys
from pathlib import Path

request = json.load(sys.stdin)
import vibecrafted_core

telemetry = Path(vibecrafted_core.__file__).resolve().parent / "runtime" / "telemetry"
sys.path.insert(0, str(telemetry))
import fleet_engine


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, telemetry / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


results = []
quota = request["mode"] == "quota"
for agent in request["agents"]:
    row = {"agent": agent, "status": "ok", "detail": ""}
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink):
            if agent == "kimi":
                module = load("kimi_monitor", "kimi-monitor/kimi_monitor.py")
                config = module.load_config(None)
                code = module.cmd_once(config) if quota else module.cmd_archive(config)
                if code:
                    row["status"], row["detail"] = "error", f"kimi exit {code}"
            elif agent == "agy":
                if quota:
                    module = load("agy_monitor", "agy-monitor/agy_monitor.py")
                    code = module.cmd_once(module.load_config(None))
                    if code:
                        row["status"], row["detail"] = "error", "no active agy session"
                else:
                    row["status"] = "unavailable"
                    row["detail"] = "agy keeps estimated transcripts only; no token ledger"
            elif agent in fleet_engine.FLEET_AGENTS:
                if quota:
                    native = getattr(fleet_engine, "refresh_native_quota", None)
                    found = agent == "codex" and native and native(
                        agent, fleet_engine.default_store(agent), fleet_engine.quota_dir(agent)
                    )
                    if not found:
                        row["status"] = "unavailable"
                        row["detail"] = "no native quota-window observation available"
                else:
                    code = fleet_engine.run(
                        agent,
                        "once",
                        fleet_engine.default_store(agent),
                        fleet_engine.quota_dir(agent),
                    )
                    if code:
                        row["status"], row["detail"] = "error", f"engine exit {code}"
            else:
                row["status"], row["detail"] = "unavailable", "unknown provider"
    except Exception as exc:  # noqa: BLE001 - one provider must not hide the rest
        busy = getattr(fleet_engine, "TelemetryBusy", None)
        if busy and isinstance(exc, busy):
            row["status"], row["detail"] = "skipped", "another collector owns this provider projection"
        else:
            row["status"], row["detail"] = "error", f"{type(exc).__name__}: {exc}"[:200]
    results.append(row)
json.dump(results, sys.stdout)
"#;

impl ControlPlane {
    fn collect_stamp_path(&self) -> PathBuf {
        self.home().join("telemetry").join(STAMP_FILE)
    }

    fn collected_age(&self) -> Option<Duration> {
        let raw = std::fs::read(self.collect_stamp_path()).ok()?;
        let at = serde_json::from_slice::<Value>(&raw)
            .ok()?
            .get("at")?
            .as_u64()?;
        let now = SystemTime::now().duration_since(UNIX_EPOCH).ok()?.as_secs();
        Some(Duration::from_secs(now.saturating_sub(at)))
    }

    /// Refresh the local projections of all supported providers, unless a
    /// collection newer than `min_interval` already exists or one is running.
    #[must_use]
    pub fn collect_local_usage(&self, min_interval: Duration) -> CollectOutcome {
        if self.collected_age().is_some_and(|age| age < min_interval) {
            return CollectOutcome::skipped(CollectMode::Local, "recent collection exists");
        }
        let agents = USAGE_PROVIDER_ADAPTERS
            .iter()
            .map(|agent| (*agent).to_string())
            .collect::<Vec<_>>();
        let outcome = self.run_collect(CollectMode::Local, &agents, COLLECT_TIMEOUT);
        if outcome.started {
            let now = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_or(0, |elapsed| elapsed.as_secs());
            let path = self.collect_stamp_path();
            if let Some(parent) = path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let _ = std::fs::write(path, json!({"at": now}).to_string());
        }
        outcome
    }

    /// Ask provider-owned sources about quota for exactly `agents`. This is the
    /// only call that may reach the network, and only for providers that have
    /// such a source; the caller is an explicit request or a task preflight.
    #[must_use]
    pub fn refresh_quota(&self, agents: &[String]) -> CollectOutcome {
        let wanted = agents
            .iter()
            .filter(|agent| USAGE_PROVIDER_ADAPTERS.contains(&agent.as_str()))
            .cloned()
            .collect::<Vec<_>>();
        if wanted.is_empty() {
            return CollectOutcome::skipped(CollectMode::Quota, "no supported provider requested");
        }
        self.run_collect(CollectMode::Quota, &wanted, QUOTA_TIMEOUT)
    }

    fn run_collect(
        &self,
        mode: CollectMode,
        agents: &[String],
        timeout: Duration,
    ) -> CollectOutcome {
        let Ok(_flight) = SINGLE_FLIGHT.try_lock() else {
            return CollectOutcome::skipped(mode, "a collection is already running");
        };
        let Some(mut command) = telemetry_python() else {
            return CollectOutcome::skipped(mode, "no python interpreter");
        };
        command
            .arg("-c")
            .arg(COLLECT_SCRIPT)
            .env("VIBECRAFTED_HOME", self.home())
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        let Ok(mut child) = command.spawn() else {
            return CollectOutcome::skipped(mode, "collector could not start");
        };
        if let Some(mut stdin) = child.stdin.take() {
            let payload = json!({"mode": mode.name(), "agents": agents}).to_string();
            let _ = stdin.write_all(payload.as_bytes());
        }
        let deadline = Instant::now() + timeout;
        let status = loop {
            match child.try_wait() {
                Ok(Some(status)) => break Some(status),
                Ok(None) if Instant::now() >= deadline => {
                    let _ = child.kill();
                    let _ = child.wait();
                    break None;
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(50)),
                Err(_) => break None,
            }
        };
        let mut stdout = String::new();
        if let Some(mut pipe) = child.stdout.take() {
            let _ = pipe.read_to_string(&mut stdout);
        }
        let parsed = status
            .filter(|status| status.success())
            .and_then(|_| serde_json::from_str::<Vec<AgentCollect>>(&stdout).ok());
        match parsed {
            Some(agents) => CollectOutcome {
                mode: mode.name().to_string(),
                started: true,
                reason: String::new(),
                agents,
            },
            None => CollectOutcome {
                mode: mode.name().to_string(),
                started: true,
                reason: "collector failed or timed out".to_string(),
                agents: agents
                    .iter()
                    .map(|agent| AgentCollect {
                        agent: agent.clone(),
                        status: "timeout".to_string(),
                        detail: "collector did not finish".to_string(),
                    })
                    .collect(),
            },
        }
    }
}
