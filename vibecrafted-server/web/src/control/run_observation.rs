//! Server-owned, in-memory await fan-in over the Python-owned durable plane.
//!
//! This module owns subscriptions and polling tasks only.  Durable mutation is
//! delegated to the installed Python writer before each read; `control-core`
//! remains a typed read model and this registry is intentionally ephemeral.

use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use axum::Json;
use axum::extract::{Path, Query};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use chrono::Utc;
use control_core::{ControlPlane, RunStatus, StateClass, is_safe_run_id};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tokio::process::Command;
use tokio::sync::{Mutex, watch};
use tokio::time::{Instant, sleep, sleep_until};

const DEFAULT_POLL_SECONDS: f64 = 5.0;
const DEFAULT_IDLE_TIMEOUT_SECONDS: f64 = 300.0;
const DEFAULT_EMPTY_GRACE_SECONDS: f64 = 2.0;
const DEFAULT_WRITER_TIMEOUT_SECONDS: f64 = 30.0;

#[derive(Debug, Clone, Hash, PartialEq, Eq)]
struct MonitorKey {
    control_plane: String,
    run_id: String,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct RunObservationV1 {
    schema: &'static str,
    run_id: String,
    control_plane: String,
    generated_at: String,
    found: bool,
    terminal: bool,
    worker_alive: Option<bool>,
    process_truth: String,
    evidence_disagreement: bool,
    disagreement_reasons: Vec<String>,
    run: Option<RunStatus>,
    monitor_witness: Value,
    aicx_witness: Value,
    writer_revalidation: String,
}

impl RunObservationV1 {
    fn from_run(
        plane: &ControlPlane,
        run_id: &str,
        run: Option<RunStatus>,
        writer_revalidation: String,
    ) -> Self {
        let terminal = run.as_ref().is_some_and(RunStatus::is_terminal);
        let persisted_process_truth = run
            .as_ref()
            .map(|item| item.process_truth.as_str())
            .unwrap_or("");
        let worker_alive = if terminal {
            Some(false)
        } else if persisted_process_truth == "live" {
            Some(true)
        } else {
            run.as_ref().and_then(|item| item.worker_alive)
        };
        let process_truth = run
            .as_ref()
            .map(|item| {
                if terminal {
                    "terminal"
                } else if matches!(item.process_truth.as_str(), "live" | "ghost" | "unknown") {
                    item.process_truth.as_str()
                } else if worker_alive == Some(true) {
                    "live"
                } else if item.liveness == "pid_gone" {
                    "ghost"
                } else {
                    "unknown"
                }
            })
            .unwrap_or("unknown")
            .to_string();
        let mut disagreement_reasons = Vec::new();
        if let Some(item) = run.as_ref() {
            if item.state_class() == StateClass::Unknown {
                disagreement_reasons.push("unknown_control_plane_state".to_string());
            }
            if terminal && worker_alive == Some(true) {
                disagreement_reasons.push("terminal_state_with_live_worker".to_string());
            }
            if !terminal
                && item.liveness == "pid_alive"
                && worker_alive != Some(true)
                && item.process_truth != "live"
            {
                disagreement_reasons.push("persisted_pid_alive_without_current_proof".to_string());
            }
        }
        if writer_revalidation != "ok" && writer_revalidation != "disabled_for_test" {
            disagreement_reasons.push("canonical_writer_revalidation_unavailable".to_string());
        }
        Self {
            schema: "vibecrafted.run-observation.v1",
            run_id: run_id.to_string(),
            control_plane: plane.control_plane_home().display().to_string(),
            generated_at: Utc::now().to_rfc3339(),
            found: run.is_some(),
            terminal,
            worker_alive,
            process_truth,
            evidence_disagreement: !disagreement_reasons.is_empty(),
            disagreement_reasons,
            run,
            monitor_witness: json!({
                "source": "vc-monitor",
                "status": "unavailable",
                "reason": "run_truth_not_projected_by_this_server_generation"
            }),
            aicx_witness: json!({
                "source": "aicx",
                "status": "unavailable",
                "reason": "continuity_witness_not_available_for_run"
            }),
            writer_revalidation,
        }
    }

    fn fingerprint(&self) -> String {
        serde_json::to_string(&(
            self.found,
            self.terminal,
            self.worker_alive,
            &self.process_truth,
            &self.disagreement_reasons,
            &self.run,
        ))
        .unwrap_or_default()
    }
}

struct MonitorEntry {
    sender: watch::Sender<RunObservationV1>,
    subscribers: AtomicUsize,
}

#[derive(Clone)]
struct WriterConfig {
    executable: PathBuf,
    timeout: Duration,
}

impl WriterConfig {
    fn production() -> Option<Self> {
        let executable =
            std::env::var_os("VC_RUN_OBSERVATION_WRITER").unwrap_or_else(|| "vibecrafted".into());
        if executable == "off" {
            return None;
        }
        Some(Self {
            executable: executable.into(),
            timeout: env_seconds(
                "VC_RUN_OBSERVATION_WRITER_TIMEOUT_SECONDS",
                DEFAULT_WRITER_TIMEOUT_SECONDS,
            ),
        })
    }
}

#[derive(Clone)]
struct WriterCancellation {
    entry: Arc<MonitorEntry>,
    empty_grace: Duration,
}

impl WriterCancellation {
    async fn cancelled(&self) {
        loop {
            if self.entry.subscribers.load(Ordering::Acquire) == 0 {
                sleep(self.empty_grace).await;
                if self.entry.subscribers.load(Ordering::Acquire) == 0 {
                    return;
                }
            } else {
                sleep(Duration::from_millis(10)).await;
            }
        }
    }
}

struct WriterOutcome {
    status: String,
    allow_read: bool,
}

struct HubState {
    entries: Mutex<HashMap<MonitorKey, Arc<MonitorEntry>>>,
    monitors_started: AtomicU64,
    underlying_reads: AtomicU64,
    poll_interval: Duration,
    empty_grace: Duration,
    writer: Option<WriterConfig>,
}

impl HubState {
    fn production() -> Arc<Self> {
        Arc::new(Self {
            entries: Mutex::new(HashMap::new()),
            monitors_started: AtomicU64::new(0),
            underlying_reads: AtomicU64::new(0),
            poll_interval: env_seconds("VC_RUN_AWAIT_POLL_SECONDS", DEFAULT_POLL_SECONDS),
            empty_grace: env_seconds(
                "VC_RUN_AWAIT_EMPTY_GRACE_SECONDS",
                DEFAULT_EMPTY_GRACE_SECONDS,
            ),
            writer: WriterConfig::production(),
        })
    }

    async fn subscribe(self: &Arc<Self>, plane: ControlPlane, run_id: String) -> AwaitSubscription {
        let key = MonitorKey {
            control_plane: plane.control_plane_home().display().to_string(),
            run_id,
        };
        let mut entries = self.entries.lock().await;
        if let Some(entry) = entries.get(&key) {
            entry.subscribers.fetch_add(1, Ordering::AcqRel);
            return AwaitSubscription {
                receiver: entry.sender.subscribe(),
                entry: Arc::clone(entry),
            };
        }
        let pending = RunObservationV1::from_run(
            &plane,
            &key.run_id,
            None,
            "pending_shared_revalidation".to_string(),
        );
        let (sender, receiver) = watch::channel(pending);
        let entry = Arc::new(MonitorEntry {
            sender,
            subscribers: AtomicUsize::new(1),
        });
        entries.insert(key.clone(), Arc::clone(&entry));
        self.monitors_started.fetch_add(1, Ordering::AcqRel);
        let hub = Arc::clone(self);
        let task_entry = Arc::clone(&entry);
        tokio::spawn(async move {
            hub.run_monitor(key, plane, task_entry).await;
        });
        AwaitSubscription { receiver, entry }
    }

    async fn run_monitor(
        self: Arc<Self>,
        key: MonitorKey,
        plane: ControlPlane,
        entry: Arc<MonitorEntry>,
    ) {
        let mut empty_since: Option<Instant> = None;
        loop {
            let subscribers = entry.subscribers.load(Ordering::Acquire);
            if subscribers == 0 {
                let since = empty_since.get_or_insert_with(Instant::now);
                if since.elapsed() >= self.empty_grace {
                    break;
                }
            } else {
                empty_since = None;
            }
            self.underlying_reads.fetch_add(1, Ordering::AcqRel);
            let observed = observe_once(
                plane.clone(),
                key.run_id.clone(),
                self.writer.clone(),
                Some(WriterCancellation {
                    entry: Arc::clone(&entry),
                    empty_grace: self.empty_grace,
                }),
            )
            .await;
            let terminal = observed.terminal && observed.worker_alive != Some(true);
            let should_close = terminal || !observed.found || observed.evidence_disagreement;
            entry.sender.send_replace(observed);
            if should_close {
                break;
            }
            sleep(self.poll_interval).await;
        }
        let mut entries = self.entries.lock().await;
        if entries
            .get(&key)
            .is_some_and(|current| Arc::ptr_eq(current, &entry))
        {
            entries.remove(&key);
        }
    }
}

struct AwaitSubscription {
    receiver: watch::Receiver<RunObservationV1>,
    entry: Arc<MonitorEntry>,
}

impl Drop for AwaitSubscription {
    fn drop(&mut self) {
        self.entry.subscribers.fetch_sub(1, Ordering::AcqRel);
    }
}

static HUB: OnceLock<Arc<HubState>> = OnceLock::new();

fn hub() -> &'static Arc<HubState> {
    HUB.get_or_init(HubState::production)
}

fn env_seconds(name: &str, default: f64) -> Duration {
    let seconds = std::env::var(name)
        .ok()
        .and_then(|raw| raw.parse::<f64>().ok())
        .filter(|value| value.is_finite() && *value >= 0.0)
        .unwrap_or(default);
    Duration::from_secs_f64(seconds)
}

async fn observe_once(
    plane: ControlPlane,
    run_id: String,
    writer: Option<WriterConfig>,
    cancellation: Option<WriterCancellation>,
) -> RunObservationV1 {
    let writer_outcome = if let Some(config) = writer {
        invoke_python_revalidation(&plane, &run_id, &config, cancellation).await
    } else {
        WriterOutcome {
            status: "disabled_for_test".to_string(),
            allow_read: true,
        }
    };
    if !writer_outcome.allow_read {
        return RunObservationV1::from_run(&plane, &run_id, None, writer_outcome.status);
    }

    let read_plane = plane.clone();
    let read_run_id = run_id.clone();
    let run = tokio::task::spawn_blocking(move || read_plane.lookup_run(&read_run_id)).await;
    match run {
        Ok(run) => RunObservationV1::from_run(&plane, &run_id, run, writer_outcome.status),
        Err(_) => {
            RunObservationV1::from_run(&plane, &run_id, None, "reader_task_failed".to_string())
        }
    }
}

async fn invoke_python_revalidation(
    plane: &ControlPlane,
    run_id: &str,
    config: &WriterConfig,
    cancellation: Option<WriterCancellation>,
) -> WriterOutcome {
    let Some(home) = plane.control_plane_home().parent().map(ToOwned::to_owned) else {
        return WriterOutcome {
            status: "invalid_control_plane_home".to_string(),
            allow_read: false,
        };
    };
    let mut command = Command::new(&config.executable);
    command
        .args(["control-plane-revalidate", "--run-id", run_id, "--json"])
        .env("VIBECRAFTED_HOME", home)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            return WriterOutcome {
                status: format!("writer_unavailable_{}", error.kind()),
                allow_read: false,
            };
        }
    };

    let cancellation_wait = async {
        if let Some(cancellation) = cancellation {
            cancellation.cancelled().await;
        } else {
            std::future::pending::<()>().await;
        }
    };
    tokio::select! {
        status = child.wait() => match status {
            Ok(status) if status.success() => WriterOutcome {
                status: "ok".to_string(),
                allow_read: true,
            },
            Ok(status) => WriterOutcome {
                status: format!("writer_exit_{}", status.code().unwrap_or(-1)),
                allow_read: false,
            },
            Err(error) => {
                terminate_and_reap(&mut child).await;
                WriterOutcome {
                    status: format!("writer_wait_failed_{}", error.kind()),
                    allow_read: false,
                }
            },
        },
        () = sleep(config.timeout) => {
            terminate_and_reap(&mut child).await;
            WriterOutcome {
                status: "writer_timeout".to_string(),
                allow_read: false,
            }
        },
        () = cancellation_wait => {
            terminate_and_reap(&mut child).await;
            WriterOutcome {
                status: "writer_cancelled".to_string(),
                allow_read: false,
            }
        },
    }
}

async fn terminate_and_reap(child: &mut tokio::process::Child) {
    if child.try_wait().ok().flatten().is_none() {
        let _ = child.start_kill();
    }
    let _ = child.wait().await;
}

#[derive(Debug, Deserialize)]
pub(crate) struct AwaitQuery {
    idle_timeout: Option<f64>,
    hard_cap: Option<f64>,
    interval: Option<f64>,
}

#[derive(Debug, Serialize)]
struct AwaitVerdictV1 {
    schema: &'static str,
    outcome: &'static str,
    reason: &'static str,
    run_id: String,
    found: bool,
    completed: bool,
    timed_out: bool,
    idle_timeout_seconds: f64,
    hard_cap_seconds: Option<f64>,
    subscription: Value,
    #[serde(flatten)]
    observation: RunObservationV1,
}

fn verdict(
    outcome: &'static str,
    observation: RunObservationV1,
    idle_timeout_seconds: f64,
    hard_cap_seconds: Option<f64>,
) -> AwaitVerdictV1 {
    AwaitVerdictV1 {
        schema: "vibecrafted.run-await-verdict.v1",
        outcome,
        reason: outcome,
        run_id: observation.run_id.clone(),
        found: observation.found,
        completed: outcome == "terminal",
        timed_out: matches!(outcome, "idle_stall" | "hard_cap"),
        idle_timeout_seconds,
        hard_cap_seconds,
        subscription: json!({
            "ownership": "server_await_subscription",
            "monitor_key": format!("{}::{}", observation.control_plane, observation.run_id)
        }),
        observation,
    }
}

pub(crate) async fn observe(Path(run_id): Path<String>) -> Response {
    if !is_safe_run_id(&run_id) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "invalid run id" })),
        )
            .into_response();
    }
    let observation = observe_once(
        ControlPlane::from_env(),
        run_id,
        WriterConfig::production(),
        None,
    )
    .await;
    let status = if observation.evidence_disagreement {
        StatusCode::SERVICE_UNAVAILABLE
    } else if observation.found {
        StatusCode::OK
    } else {
        StatusCode::NOT_FOUND
    };
    (status, Json(observation)).into_response()
}

pub(crate) async fn await_run(
    Path(run_id): Path<String>,
    Query(query): Query<AwaitQuery>,
) -> Response {
    if !is_safe_run_id(&run_id) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "invalid run id" })),
        )
            .into_response();
    }
    let idle = query
        .idle_timeout
        .filter(|value| value.is_finite() && *value >= 0.0)
        .unwrap_or(DEFAULT_IDLE_TIMEOUT_SECONDS);
    let hard_cap = query
        .hard_cap
        .filter(|value| value.is_finite() && *value >= 0.0);
    let _requested_interval = query.interval;
    let plane = ControlPlane::from_env();
    let mut subscription = hub().subscribe(plane, run_id).await;
    let start = Instant::now();
    let mut idle_deadline = start + Duration::from_secs_f64(idle);
    let hard_deadline = hard_cap.map(|cap| start + Duration::from_secs_f64(cap));
    let mut fingerprint = subscription.receiver.borrow().fingerprint();
    loop {
        let hard_sleep = async {
            if let Some(deadline) = hard_deadline {
                sleep_until(deadline).await;
            } else {
                std::future::pending::<()>().await;
            }
        };
        tokio::select! {
            changed = subscription.receiver.changed() => {
                if changed.is_err() {
                    let current = subscription.receiver.borrow().clone();
                    return (StatusCode::SERVICE_UNAVAILABLE, Json(verdict("server_unavailable", current, idle, hard_cap))).into_response();
                }
                let current = subscription.receiver.borrow().clone();
                let next_fingerprint = current.fingerprint();
                if next_fingerprint != fingerprint || current.worker_alive == Some(true) {
                    idle_deadline = Instant::now() + Duration::from_secs_f64(idle);
                    fingerprint = next_fingerprint;
                }
                if current.evidence_disagreement {
                    return (StatusCode::CONFLICT, Json(verdict("evidence_disagreement", current, idle, hard_cap))).into_response();
                }
                if !current.found {
                    return (StatusCode::NOT_FOUND, Json(verdict("not_found", current, idle, hard_cap))).into_response();
                }
                if current.terminal && current.worker_alive != Some(true) {
                    return Json(verdict("terminal", current, idle, hard_cap)).into_response();
                }
            }
            () = sleep_until(idle_deadline) => {
                let current = subscription.receiver.borrow().clone();
                if current.worker_alive != Some(true) {
                    return (StatusCode::REQUEST_TIMEOUT, Json(verdict("idle_stall", current, idle, hard_cap))).into_response();
                }
                idle_deadline = Instant::now() + Duration::from_secs_f64(idle);
            }
            () = hard_sleep => {
                let current = subscription.receiver.borrow().clone();
                return (StatusCode::REQUEST_TIMEOUT, Json(verdict("hard_cap", current, idle, hard_cap))).into_response();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::path::PathBuf;

    fn fixture_home(label: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "vc-await-hub-{label}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        fs::create_dir_all(path.join("control_plane/runtime_runs/run-1")).expect("fixture root");
        path
    }

    fn write_meta(home: &std::path::Path, state: &str, exit_code: Option<i32>) {
        let payload = json!({
            "run_id": "run-1",
            "status": state,
            "state": state,
            "agent": "codex",
            "mode": "implement",
            "root": "/repo",
            "updated_at": Utc::now().to_rfc3339(),
            "liveness": if exit_code.is_some() { "terminal" } else { "heartbeat" },
            "exit_code": exit_code,
        });
        fs::write(
            home.join("control_plane/runtime_runs/run-1/meta.json"),
            serde_json::to_vec(&payload).expect("JSON"),
        )
        .expect("write meta");
    }

    fn test_hub() -> Arc<HubState> {
        Arc::new(HubState {
            entries: Mutex::new(HashMap::new()),
            monitors_started: AtomicU64::new(0),
            underlying_reads: AtomicU64::new(0),
            poll_interval: Duration::from_millis(20),
            empty_grace: Duration::from_millis(40),
            writer: None,
        })
    }

    fn blocking_writer(home: &std::path::Path) -> (WriterConfig, PathBuf) {
        let executable = home.join("blocking-writer.sh");
        let pid_file = home.join("blocking-writer.pid");
        fs::write(
            &executable,
            format!(
                "#!/bin/sh\nprintf '%s' \"$$\" > \"{}\"\nexec /bin/sleep 30\n",
                pid_file.display()
            ),
        )
        .expect("writer script");
        let mut permissions = fs::metadata(&executable)
            .expect("writer metadata")
            .permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&executable, permissions).expect("writer executable");
        (
            WriterConfig {
                executable,
                timeout: Duration::from_secs(5),
            },
            pid_file,
        )
    }

    async fn wait_for_writer_pid(pid_file: &std::path::Path) -> u32 {
        tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                if let Ok(raw) = fs::read_to_string(pid_file)
                    && let Ok(pid) = raw.parse::<u32>()
                {
                    return pid;
                }
                sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("writer child pid")
    }

    fn process_exists(pid: u32) -> bool {
        std::process::Command::new("/bin/kill")
            .args(["-0", &pid.to_string()])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .is_ok_and(|status| status.success())
    }

    async fn assert_process_reaped(pid: u32) {
        tokio::time::timeout(Duration::from_secs(2), async {
            while process_exists(pid) {
                sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("writer child terminated and reaped");
    }

    #[tokio::test]
    async fn concurrent_first_subscribers_create_one_monitor_and_all_settle() {
        let home = fixture_home("fanin");
        write_meta(&home, "running", None);
        let plane = ControlPlane::new(&home);
        let hub = test_hub();
        let barrier = Arc::new(tokio::sync::Barrier::new(20));
        let mut joins = Vec::new();
        for _ in 0..20 {
            let hub = Arc::clone(&hub);
            let plane = plane.clone();
            let barrier = Arc::clone(&barrier);
            joins.push(tokio::spawn(async move {
                barrier.wait().await;
                hub.subscribe(plane, "run-1".to_string()).await
            }));
        }
        let mut subscribers = Vec::new();
        for join in joins {
            subscribers.push(join.await.expect("subscriber"));
        }
        assert_eq!(hub.monitors_started.load(Ordering::Acquire), 1);
        assert_eq!(subscribers[0].entry.subscribers.load(Ordering::Acquire), 20);

        write_meta(&home, "completed", Some(0));
        for subscriber in &mut subscribers {
            tokio::time::timeout(Duration::from_secs(2), async {
                loop {
                    subscriber.receiver.changed().await.expect("monitor open");
                    if subscriber.receiver.borrow().terminal {
                        break;
                    }
                }
            })
            .await
            .expect("terminal fanout");
            assert_eq!(subscriber.receiver.borrow().run_id, "run-1");
        }
        drop(subscribers);
        tokio::time::timeout(Duration::from_secs(2), async {
            while !hub.entries.lock().await.is_empty() {
                sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("registry cleanup");
        assert!(hub.underlying_reads.load(Ordering::Acquire) < 10);
        let _ = fs::remove_dir_all(home);
    }

    #[tokio::test]
    async fn disconnects_do_not_cancel_remaining_subscribers() {
        let home = fixture_home("disconnect");
        write_meta(&home, "running", None);
        let plane = ControlPlane::new(&home);
        let hub = test_hub();
        let first = hub.subscribe(plane.clone(), "run-1".to_string()).await;
        let mut remaining = hub.subscribe(plane, "run-1".to_string()).await;
        drop(first);
        assert_eq!(remaining.entry.subscribers.load(Ordering::Acquire), 1);
        write_meta(&home, "completed", Some(0));
        tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                remaining.receiver.changed().await.expect("monitor open");
                if remaining.receiver.borrow().terminal {
                    break;
                }
            }
        })
        .await
        .expect("remaining subscriber settles");
        let _ = fs::remove_dir_all(home);
    }

    #[tokio::test]
    async fn one_shot_observe_never_grows_registry() {
        let home = fixture_home("observe");
        write_meta(&home, "running", None);
        let plane = ControlPlane::new(&home);
        let hub = test_hub();
        for _ in 0..20 {
            assert!(
                observe_once(plane.clone(), "run-1".to_string(), None, None)
                    .await
                    .found
            );
        }
        assert!(hub.entries.lock().await.is_empty());
        assert_eq!(hub.monitors_started.load(Ordering::Acquire), 0);
        let _ = fs::remove_dir_all(home);
    }

    #[tokio::test]
    async fn terminal_and_unknown_fast_paths_leave_no_registry_entry() {
        for (label, write_fixture, expected_found) in
            [("terminal", true, true), ("unknown", false, false)]
        {
            let home = fixture_home(label);
            if write_fixture {
                write_meta(&home, "completed", Some(0));
            } else {
                fs::remove_dir_all(home.join("control_plane/runtime_runs/run-1"))
                    .expect("remove unknown fixture run");
            }
            let plane = ControlPlane::new(&home);
            let hub = test_hub();
            let mut subscriber = hub.subscribe(plane, "run-1".to_string()).await;
            tokio::time::timeout(Duration::from_secs(2), subscriber.receiver.changed())
                .await
                .expect("fast path response")
                .expect("monitor publishes verdict");
            assert_eq!(subscriber.receiver.borrow().found, expected_found);
            if expected_found {
                assert!(subscriber.receiver.borrow().terminal);
            }
            drop(subscriber);
            tokio::time::timeout(Duration::from_secs(2), async {
                while !hub.entries.lock().await.is_empty() {
                    sleep(Duration::from_millis(10)).await;
                }
            })
            .await
            .expect("fast path registry cleanup");
            assert_eq!(hub.monitors_started.load(Ordering::Acquire), 1);
            assert_eq!(hub.underlying_reads.load(Ordering::Acquire), 1);
            let _ = fs::remove_dir_all(home);
        }
    }

    #[tokio::test]
    async fn last_disconnect_cleans_live_monitor_after_declared_grace() {
        let home = fixture_home("empty-grace");
        write_meta(&home, "running", None);
        let plane = ControlPlane::new(&home);
        let hub = test_hub();
        let mut subscriber = hub.subscribe(plane, "run-1".to_string()).await;
        tokio::time::timeout(Duration::from_secs(2), subscriber.receiver.changed())
            .await
            .expect("initial observation")
            .expect("monitor open");
        drop(subscriber);
        tokio::time::timeout(Duration::from_secs(2), async {
            while !hub.entries.lock().await.is_empty() {
                sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("empty monitor cleanup");
        let reads_after_cleanup = hub.underlying_reads.load(Ordering::Acquire);
        sleep(Duration::from_millis(80)).await;
        assert_eq!(
            hub.underlying_reads.load(Ordering::Acquire),
            reads_after_cleanup
        );
        let _ = fs::remove_dir_all(home);
    }

    #[tokio::test]
    async fn last_subscriber_hard_cap_cancels_and_reaps_production_writer() {
        let home = fixture_home("writer-cancel");
        write_meta(&home, "running", None);
        let (writer, pid_file) = blocking_writer(&home);
        let hub = Arc::new(HubState {
            entries: Mutex::new(HashMap::new()),
            monitors_started: AtomicU64::new(0),
            underlying_reads: AtomicU64::new(0),
            poll_interval: Duration::from_millis(20),
            empty_grace: Duration::from_millis(40),
            writer: Some(writer),
        });
        let subscriber = hub
            .subscribe(ControlPlane::new(&home), "run-1".to_string())
            .await;
        let witness = subscriber.receiver.clone();
        let pid = wait_for_writer_pid(&pid_file).await;

        drop(subscriber);
        tokio::time::timeout(Duration::from_secs(2), async {
            while !hub.entries.lock().await.is_empty() {
                sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("cancelled monitor registry cleanup");

        assert_process_reaped(pid).await;
        assert_eq!(witness.borrow().writer_revalidation, "writer_cancelled");
        assert!(witness.borrow().evidence_disagreement);
        assert!(!witness.borrow().found);
        let reads_after_cleanup = hub.underlying_reads.load(Ordering::Acquire);
        sleep(Duration::from_millis(80)).await;
        assert_eq!(
            hub.underlying_reads.load(Ordering::Acquire),
            reads_after_cleanup
        );
        let _ = fs::remove_dir_all(home);
    }

    #[tokio::test]
    async fn writer_deadline_reaps_child_publishes_disagreement_and_stops_reads() {
        let home = fixture_home("writer-timeout");
        write_meta(&home, "running", None);
        let (mut writer, pid_file) = blocking_writer(&home);
        // Leave enough launch headroom on loaded macOS runners so the PID
        // witness is durable before the deliberately short deadline fires.
        writer.timeout = Duration::from_millis(500);
        let hub = Arc::new(HubState {
            entries: Mutex::new(HashMap::new()),
            monitors_started: AtomicU64::new(0),
            underlying_reads: AtomicU64::new(0),
            poll_interval: Duration::from_millis(20),
            empty_grace: Duration::from_millis(40),
            writer: Some(writer),
        });
        let mut subscriber = hub
            .subscribe(ControlPlane::new(&home), "run-1".to_string())
            .await;
        let pid = wait_for_writer_pid(&pid_file).await;

        tokio::time::timeout(Duration::from_secs(2), subscriber.receiver.changed())
            .await
            .expect("writer timeout verdict")
            .expect("monitor publishes timeout");
        let observation = subscriber.receiver.borrow().clone();
        assert_eq!(observation.writer_revalidation, "writer_timeout");
        assert!(observation.evidence_disagreement);
        assert!(!observation.found);
        assert_process_reaped(pid).await;
        tokio::time::timeout(Duration::from_secs(2), async {
            while !hub.entries.lock().await.is_empty() {
                sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("timed-out monitor registry cleanup");
        assert_eq!(hub.underlying_reads.load(Ordering::Acquire), 1);
        sleep(Duration::from_millis(80)).await;
        assert_eq!(hub.underlying_reads.load(Ordering::Acquire), 1);
        let _ = fs::remove_dir_all(home);
    }

    #[tokio::test]
    async fn canonical_writer_live_truth_preserves_owned_pgid_descendant() {
        let home = fixture_home("pgid-child");
        let payload = json!({
            "run_id": "run-1",
            "status": "running",
            "state": "running",
            "agent": "codex",
            "mode": "implement",
            "root": "/repo",
            "updated_at": Utc::now().to_rfc3339(),
            "liveness": "pid_alive",
            "worker_pid": 999999998,
            "worker_pgid": 999999997,
            "worker_alive": false,
            "process_truth": "live",
            "process_truth_reason": "canonical_pgid_descendant"
        });
        fs::write(
            home.join("control_plane/runtime_runs/run-1/meta.json"),
            serde_json::to_vec(&payload).expect("JSON"),
        )
        .expect("write meta");

        let observed =
            observe_once(ControlPlane::new(&home), "run-1".to_string(), None, None).await;

        assert_eq!(observed.process_truth, "live");
        assert_eq!(observed.worker_alive, Some(true));
        assert!(!observed.evidence_disagreement);
        let _ = fs::remove_dir_all(home);
    }
}
