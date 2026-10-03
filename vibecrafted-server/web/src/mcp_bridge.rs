//! Supervised stdio child that speaks MCP to `vibecrafted-mcp`.
//!
//! The HTTP registry keeps the pilot tools. Everything else under `vc_*`
//! is listed from this child and `tools/call` is forwarded unchanged.
//! The child is a new process group. Drop sends SIGKILL to that group.
//! SIGTERM and SIGINT of this process do the same from a `sigaction`
//! handler, then re-raise so the server dies with a real signal status.
//! SIGKILL of this process cannot run that handler. On Linux the watchdog
//! is armed with `PR_SET_PDEATHSIG` of SIGTERM (blocked until Python
//! installs its handler) and then SIGKILLs the group. Elsewhere it polls
//! `getppid` and does the same. A closed stdio pipe does not kill
//! grandchildren.

use std::collections::{HashMap, HashSet};
use std::ffi::{OsStr, OsString};
use std::io::ErrorKind;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicI64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, Once};
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::{Mutex as AsyncMutex, Notify, oneshot};
use tokio::time::timeout;

const MAX_FRAME: usize = 8 * 1024 * 1024;
const MAX_PAGES: usize = 16;
const PROTOCOL: &str = "2025-03-26";

static TERMINATE_PGID: AtomicI32 = AtomicI32::new(0);

type PendingSender = oneshot::Sender<Result<Value, String>>;
type PendingMap = HashMap<i64, PendingSender>;
type Pending = Arc<Mutex<PendingMap>>;

#[derive(Clone, Debug)]
pub struct BridgeConfig {
    pub call_timeout: Duration,
    pub initial_backoff: Duration,
    pub max_backoff: Duration,
    pub inherit_stderr: bool,
}

impl BridgeConfig {
    pub fn from_process() -> Self {
        let initial = env_millis("VC_MCP_BRIDGE_BACKOFF_MS", 200);
        let mut max = env_millis("VC_MCP_BRIDGE_BACKOFF_MAX_MS", 5_000);
        if max < initial {
            max = initial;
        }
        Self {
            call_timeout: env_millis("VC_MCP_BRIDGE_CALL_TIMEOUT_MS", 330_000),
            initial_backoff: initial,
            max_backoff: max,
            inherit_stderr: true,
        }
    }
}

#[derive(Clone, Debug)]
pub struct BridgeCommand {
    pub program: OsString,
    pub args: Vec<OsString>,
    pub current_dir: Option<PathBuf>,
    pub env: Vec<(OsString, OsString)>,
}

impl BridgeCommand {
    pub fn new(
        program: impl AsRef<OsStr>,
        args: impl IntoIterator<Item = impl AsRef<OsStr>>,
    ) -> Self {
        Self {
            program: program.as_ref().to_os_string(),
            args: args
                .into_iter()
                .map(|arg| arg.as_ref().to_os_string())
                .collect(),
            current_dir: None,
            env: Vec::new(),
        }
    }
}

/// Inputs for [`resolve_bridge_command`]. Production reads the process
/// environment; tests pass a fixture so resolution does not depend on PATH.
#[derive(Clone, Debug)]
pub struct BridgeEnv {
    pub command: Option<String>,
    pub args_json: Option<String>,
    pub path: String,
    pub pythonpath: Option<String>,
    pub current_dir: PathBuf,
    pub executable: Option<PathBuf>,
}

impl BridgeEnv {
    pub fn from_process() -> Self {
        Self {
            command: std::env::var("VC_MCP_BRIDGE_COMMAND").ok(),
            args_json: std::env::var("VC_MCP_BRIDGE_ARGS").ok(),
            path: std::env::var("PATH").unwrap_or_default(),
            pythonpath: std::env::var("PYTHONPATH").ok(),
            current_dir: std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
            executable: std::env::current_exe().ok(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BridgeError {
    Restarting { retry_after_ms: u64 },
    Timeout,
    Exited { detail: String },
    Protocol { detail: String },
    Spawn { detail: String },
    Remote { code: i32, message: String },
}

impl BridgeError {
    pub fn rpc_code(&self) -> i32 {
        match self {
            Self::Restarting { .. } => -32001,
            Self::Timeout => -32002,
            Self::Exited { .. } | Self::Protocol { .. } | Self::Spawn { .. } => -32003,
            Self::Remote { code, .. } => *code,
        }
    }

    pub fn rpc_message(&self) -> String {
        match self {
            Self::Restarting { retry_after_ms } => {
                format!("mcp bridge restarting (retry after {retry_after_ms}ms)")
            }
            Self::Timeout => "mcp bridge timeout".to_string(),
            Self::Exited { detail } => format!("mcp bridge child exited: {detail}"),
            Self::Protocol { detail } => format!("mcp bridge protocol: {detail}"),
            Self::Spawn { detail } => format!("mcp bridge spawn: {detail}"),
            Self::Remote { message, .. } => message.clone(),
        }
    }

    fn fatal(&self) -> bool {
        matches!(
            self,
            Self::Timeout | Self::Exited { .. } | Self::Protocol { .. }
        )
    }
}

impl std::fmt::Display for BridgeError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.rpc_message())
    }
}

impl std::error::Error for BridgeError {}

#[derive(Clone)]
struct Session {
    generation: u64,
    alive: Arc<AtomicBool>,
    stdin: Arc<AsyncMutex<ChildStdin>>,
    pending: Pending,
    next_id: Arc<AtomicI64>,
}

struct ChildSlot {
    generation: u64,
    pid: i32,
    session: Session,
    reader: tokio::task::JoinHandle<()>,
    stderr_task: Option<tokio::task::JoinHandle<()>>,
    child: Child,
    tools: Option<Vec<Value>>,
}

impl Drop for ChildSlot {
    fn drop(&mut self) {
        self.session.alive.store(false, Ordering::Release);
        if self.pid > 0 {
            signal_group(self.pid, 9);
            if TERMINATE_PGID.load(Ordering::Relaxed) == self.pid {
                TERMINATE_PGID.store(0, Ordering::Relaxed);
            }
            reap_leader(self.pid);
        }
        self.reader.abort();
        if let Some(task) = self.stderr_task.take() {
            task.abort();
        }
        let _ = self.child.start_kill();
    }
}

struct BridgeState {
    child: Option<ChildSlot>,
    starting: bool,
    failures: u32,
    backoff_until: Option<Instant>,
}

struct Inner {
    state: Mutex<BridgeState>,
    events: Arc<Mutex<Vec<String>>>,
    notify: Notify,
    command: Option<BridgeCommand>,
    config: BridgeConfig,
    broken: Option<String>,
    track_signal: bool,
}

/// Lazy stdio supervisor. Construction does not spawn.
pub struct StdioBridge {
    inner: Inner,
}

impl StdioBridge {
    pub fn start(command: BridgeCommand, config: BridgeConfig) -> Arc<Self> {
        Arc::new(Self {
            inner: Inner {
                state: Mutex::new(BridgeState {
                    child: None,
                    starting: false,
                    failures: 0,
                    backoff_until: None,
                }),
                events: Arc::new(Mutex::new(Vec::new())),
                notify: Notify::new(),
                command: Some(command),
                config,
                broken: None,
                track_signal: false,
            },
        })
    }

    /// Production resolver. Still lazy: the child starts on the first
    /// `tools/list` or non-pilot `tools/call`.
    pub fn production() -> Arc<Self> {
        install_terminate_handler();
        let env = BridgeEnv::from_process();
        match resolve_bridge_command(&env) {
            Ok(command) => Arc::new(Self {
                inner: Inner {
                    state: Mutex::new(BridgeState {
                        child: None,
                        starting: false,
                        failures: 0,
                        backoff_until: None,
                    }),
                    events: Arc::new(Mutex::new(Vec::new())),
                    notify: Notify::new(),
                    command: Some(command),
                    config: BridgeConfig::from_process(),
                    broken: None,
                    track_signal: true,
                },
            }),
            Err(detail) => Arc::new(Self {
                inner: Inner {
                    state: Mutex::new(BridgeState {
                        child: None,
                        starting: false,
                        failures: 0,
                        backoff_until: None,
                    }),
                    events: Arc::new(Mutex::new(Vec::new())),
                    notify: Notify::new(),
                    command: None,
                    config: BridgeConfig::from_process(),
                    broken: Some(detail),
                    track_signal: true,
                },
            }),
        }
    }

    pub fn child_pid(&self) -> Option<u32> {
        let state = lock(&self.inner.state);
        let pid = state.child.as_ref().map(|child| child.pid).unwrap_or(0);
        u32::try_from(pid).ok().filter(|pid| *pid > 0)
    }

    pub fn recent_events(&self) -> Vec<String> {
        lock(&self.inner.events).clone()
    }

    pub async fn list_tools(&self) -> Result<Vec<Value>, BridgeError> {
        let session = self.ensure_session().await?;
        if let Some(cached) = self.cached_tools(session.generation) {
            return Ok(cached);
        }
        let tools = match self.collect_tools(&session).await {
            Ok(tools) => tools,
            Err(error) => {
                self.fail_session(&session, &error).await;
                return Err(error);
            }
        };
        self.store_tools(session.generation, &tools);
        Ok(tools)
    }

    pub async fn call_tool(&self, name: &str, arguments: Value) -> Result<Value, BridgeError> {
        let session = self.ensure_session().await?;
        match self
            .rpc(
                &session,
                "tools/call",
                json!({"name": name, "arguments": arguments}),
            )
            .await
        {
            Ok(result) => Ok(result),
            Err(error) => {
                self.fail_session(&session, &error).await;
                Err(error)
            }
        }
    }

    async fn ensure_session(&self) -> Result<Session, BridgeError> {
        if let Some(detail) = &self.inner.broken {
            return Err(BridgeError::Spawn {
                detail: detail.clone(),
            });
        }
        loop {
            let waiter = {
                let mut state = lock(&self.inner.state);
                if let Some(child) = &state.child
                    && child.session.alive.load(Ordering::Acquire)
                {
                    return Ok(child.session.clone());
                }
                if state.child.is_some() {
                    let dead = state.child.take();
                    let delay = arm_backoff(&mut state, &self.inner.config);
                    drop(state);
                    drop(dead);
                    self.log(format!("child exited; backoff {}ms", delay.as_millis()));
                    return Err(BridgeError::Restarting {
                        retry_after_ms: u64::try_from(delay.as_millis()).unwrap_or(u64::MAX),
                    });
                }
                if let Some(until) = state.backoff_until {
                    let now = Instant::now();
                    if now < until {
                        let retry_after_ms =
                            u64::try_from(until.saturating_duration_since(now).as_millis())
                                .unwrap_or(u64::MAX);
                        self.log(format!(
                            "reject call: restarting retry_after={retry_after_ms}ms"
                        ));
                        return Err(BridgeError::Restarting { retry_after_ms });
                    }
                }
                if state.starting {
                    Some(self.inner.notify.notified())
                } else {
                    state.starting = true;
                    None
                }
            };
            if let Some(waiter) = waiter {
                waiter.await;
                continue;
            }
            let spawned = self.spawn_and_handshake().await;
            let mut state = lock(&self.inner.state);
            state.starting = false;
            match spawned {
                Ok(slot) => {
                    let session = slot.session.clone();
                    state.child = Some(slot);
                    state.failures = 0;
                    state.backoff_until = None;
                    drop(state);
                    self.inner.notify.notify_waiters();
                    self.log(format!("handshake ok generation={}", session.generation));
                    return Ok(session);
                }
                Err(error) => {
                    let delay = arm_backoff(&mut state, &self.inner.config);
                    drop(state);
                    self.inner.notify.notify_waiters();
                    self.log(format!(
                        "start failed ({error}); backoff {}ms",
                        delay.as_millis()
                    ));
                    return Err(error);
                }
            }
        }
    }

    async fn spawn_and_handshake(&self) -> Result<ChildSlot, BridgeError> {
        let command = self
            .inner
            .command
            .as_ref()
            .ok_or_else(|| BridgeError::Spawn {
                detail: "mcp bridge has no command".to_string(),
            })?;
        let mut slot = spawn_child(command, &self.inner.config, &self.inner.events).await?;
        if self.inner.track_signal {
            TERMINATE_PGID.store(slot.pid, Ordering::Relaxed);
        }
        self.log(format!(
            "spawn pid={} generation={}",
            slot.pid, slot.generation
        ));
        let session = slot.session.clone();
        if let Err(error) = handshake(self, &session).await {
            // Dropping the slot kills the group. Do not publish it.
            drop(slot);
            return Err(error);
        }
        slot.generation = session.generation;
        Ok(slot)
    }

    async fn collect_tools(&self, session: &Session) -> Result<Vec<Value>, BridgeError> {
        let mut tools = Vec::new();
        let mut cursor: Option<String> = None;
        for _ in 0..MAX_PAGES {
            let params = match &cursor {
                Some(value) => json!({"cursor": value}),
                None => json!({}),
            };
            let result = self.rpc(session, "tools/list", params).await?;
            if let Some(page) = result.get("tools").and_then(Value::as_array) {
                tools.extend(page.iter().cloned());
            }
            cursor = result
                .get("nextCursor")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .map(str::to_string);
            if cursor.is_none() {
                return Ok(tools);
            }
        }
        Err(BridgeError::Protocol {
            detail: "tools/list cursor did not terminate".to_string(),
        })
    }

    async fn rpc(
        &self,
        session: &Session,
        method: &str,
        params: Value,
    ) -> Result<Value, BridgeError> {
        if !session.alive.load(Ordering::Acquire) {
            return Err(BridgeError::Exited {
                detail: "child is not running".to_string(),
            });
        }
        let id = session.next_id.fetch_add(1, Ordering::Relaxed);
        let (sender, receiver) = oneshot::channel();
        {
            let mut pending = lock(&session.pending);
            pending.insert(id, sender);
        }
        let message = json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        });
        if let Err(error) = write_json(&session.stdin, &message).await {
            lock(&session.pending).remove(&id);
            session.alive.store(false, Ordering::Release);
            return Err(BridgeError::Exited {
                detail: error.to_string(),
            });
        }
        let wait = timeout(self.inner.config.call_timeout, receiver).await;
        let message = match wait {
            Ok(Ok(Ok(message))) => message,
            Ok(Ok(Err(detail))) => {
                return Err(BridgeError::Exited { detail });
            }
            Ok(Err(_)) => {
                return Err(BridgeError::Exited {
                    detail: "response channel dropped".to_string(),
                });
            }
            Err(_) => {
                lock(&session.pending).remove(&id);
                session.alive.store(false, Ordering::Release);
                return Err(BridgeError::Timeout);
            }
        };
        into_result(message)
    }

    async fn fail_session(&self, session: &Session, error: &BridgeError) {
        if !error.fatal() {
            return;
        }
        let removed = {
            let mut state = lock(&self.inner.state);
            let current = state.child.as_ref().map(|child| child.generation);
            if current != Some(session.generation) {
                return;
            }
            let removed = state.child.take();
            let delay = arm_backoff(&mut state, &self.inner.config);
            drop(state);
            self.log(format!(
                "child generation {} failed ({error}); backoff {}ms",
                session.generation,
                delay.as_millis()
            ));
            removed
        };
        drop(removed);
    }

    fn cached_tools(&self, generation: u64) -> Option<Vec<Value>> {
        let state = lock(&self.inner.state);
        state.child.as_ref().and_then(|child| {
            if child.generation == generation {
                child.tools.clone()
            } else {
                None
            }
        })
    }

    fn store_tools(&self, generation: u64, tools: &[Value]) {
        let mut state = lock(&self.inner.state);
        if let Some(child) = state.child.as_mut()
            && child.generation == generation
            && child.session.alive.load(Ordering::Acquire)
        {
            child.tools = Some(tools.to_vec());
        }
    }

    fn log(&self, event: String) {
        eprintln!("mcp-bridge: {event}");
        let mut events = lock(&self.inner.events);
        events.push(event);
        if events.len() > 64 {
            let extra = events.len() - 64;
            events.drain(0..extra);
        }
    }
}

impl Drop for StdioBridge {
    fn drop(&mut self) {
        self.inner.state.lock().map_or_else(
            |poison| poison.into_inner().child.take(),
            |mut state| state.child.take(),
        );
        if self.inner.track_signal {
            TERMINATE_PGID.store(0, Ordering::Relaxed);
        }
    }
}

async fn handshake(bridge: &StdioBridge, session: &Session) -> Result<(), BridgeError> {
    let result = bridge
        .rpc(
            session,
            "initialize",
            json!({
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {
                    "name": "vc-server-mcp-bridge",
                    "version": env!("VC_SERVER_VERSION"),
                },
            }),
        )
        .await?;
    if !result.is_object() {
        return Err(BridgeError::Protocol {
            detail: "initialize result was not an object".to_string(),
        });
    }
    write_json(
        &session.stdin,
        &json!({"jsonrpc": "2.0", "method": "notifications/initialized"}),
    )
    .await
    .map_err(|error| BridgeError::Exited {
        detail: error.to_string(),
    })?;
    Ok(())
}

async fn spawn_child(
    command: &BridgeCommand,
    config: &BridgeConfig,
    events: &Arc<Mutex<Vec<String>>>,
) -> Result<ChildSlot, BridgeError> {
    // The direct child is a watchdog. A SIGTERM/SIGKILL of vc-server does
    // not run `Drop`, so the watchdog notices the parent pid change and
    // kills the process group. It stays silent on stdout; the real server
    // is the grandchild and inherits the MCP pipes.
    let command = watch_parent(command);
    let mut process = Command::new(&command.program);
    process
        .args(&command.args)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(if config.inherit_stderr {
            std::process::Stdio::inherit()
        } else {
            std::process::Stdio::piped()
        })
        .kill_on_drop(true);
    if let Some(dir) = &command.current_dir {
        process.current_dir(dir);
    }
    for (key, value) in &command.env {
        process.env(key, value);
    }
    // `pre_exec` runs in the forked child before exec. `setpgid(0, 0)` puts
    // only this child in a new group so the parent can signal the group
    // without signalling itself.
    #[cfg(unix)]
    // SAFETY: called in the forked child before exec, with no locks held.
    // setpgid, sigprocmask and prctl are async-signal-safe. SIGTERM is
    // blocked here so Linux parent-death cannot arrive before Python
    // installs kill_group. The signal is SIGTERM, not SIGKILL: SIGKILL
    // would prevent the watchdog from killing the rest of the group.
    unsafe {
        process.pre_exec(|| {
            if libc::setpgid(0, 0) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            #[cfg(target_os = "linux")]
            {
                let mut blocked = std::mem::zeroed::<libc::sigset_t>();
                if libc::sigemptyset(&mut blocked) != 0
                    || libc::sigaddset(&mut blocked, libc::SIGTERM) != 0
                    || libc::sigprocmask(libc::SIG_BLOCK, &blocked, std::ptr::null_mut()) != 0
                    || libc::prctl(
                        libc::PR_SET_PDEATHSIG,
                        libc::SIGTERM as libc::c_ulong,
                        0,
                        0,
                        0,
                    ) != 0
                {
                    return Err(std::io::Error::last_os_error());
                }
            }
            Ok(())
        });
    }
    let mut child = process.spawn().map_err(|error| BridgeError::Spawn {
        detail: format!(
            "failed to start {}: {error}",
            command.program.to_string_lossy()
        ),
    })?;
    let pid = child
        .id()
        .and_then(|pid| i32::try_from(pid).ok())
        .unwrap_or(0);
    let stdin = child.stdin.take().ok_or_else(|| BridgeError::Spawn {
        detail: "child stdin was not piped".to_string(),
    })?;
    let stdout = child.stdout.take().ok_or_else(|| BridgeError::Spawn {
        detail: "child stdout was not piped".to_string(),
    })?;
    let stderr_task = child
        .stderr
        .take()
        .map(|stderr| tokio::spawn(drain_stderr(stderr, Arc::clone(events))));
    let pending = Arc::new(Mutex::new(HashMap::new()));
    let stdin = Arc::new(AsyncMutex::new(stdin));
    let alive = Arc::new(AtomicBool::new(true));
    let generation = next_generation();
    let session = Session {
        generation,
        alive: Arc::clone(&alive),
        stdin: Arc::clone(&stdin),
        pending: Arc::clone(&pending),
        next_id: Arc::new(AtomicI64::new(1)),
    };
    let reader = tokio::spawn(read_stdout(
        stdout,
        Arc::clone(&stdin),
        Arc::clone(&pending),
        Arc::clone(&alive),
    ));
    Ok(ChildSlot {
        generation,
        pid,
        session,
        reader,
        stderr_task,
        child,
        tools: None,
    })
}

static GENERATION: AtomicI64 = AtomicI64::new(1);

fn next_generation() -> u64 {
    u64::try_from(GENERATION.fetch_add(1, Ordering::Relaxed)).unwrap_or(1)
}

async fn drain_stderr(stderr: tokio::process::ChildStderr, events: Arc<Mutex<Vec<String>>>) {
    let mut reader = BufReader::new(stderr);
    let mut line = String::new();
    loop {
        line.clear();
        match reader.read_line(&mut line).await {
            Ok(0) | Err(_) => break,
            Ok(_) => {
                let text = line.trim();
                if text.is_empty() {
                    continue;
                }
                let mut events = lock(&events);
                if events.len() < 64 {
                    events.push(format!("stderr: {text}"));
                }
            }
        }
    }
}

async fn read_stdout(
    stdout: ChildStdout,
    stdin: Arc<AsyncMutex<ChildStdin>>,
    pending: Pending,
    alive: Arc<AtomicBool>,
) {
    let mut reader = BufReader::new(stdout);
    loop {
        match read_frame(&mut reader).await {
            Ok(Some(bytes)) => {
                let value = match serde_json::from_slice::<Value>(&bytes) {
                    Ok(value) => value,
                    Err(error) => {
                        fail_pending(&pending, &format!("child wrote a non-JSON frame: {error}"));
                        break;
                    }
                };
                dispatch(&stdin, &pending, value).await;
            }
            Ok(None) => break,
            Err(error) => {
                fail_pending(&pending, &error.to_string());
                break;
            }
        }
    }
    alive.store(false, Ordering::Release);
    fail_pending(&pending, "child closed stdout");
}

async fn dispatch(stdin: &AsyncMutex<ChildStdin>, pending: &Pending, value: Value) {
    let method = value
        .get("method")
        .and_then(Value::as_str)
        .map(str::to_string);
    let id = value.get("id").and_then(Value::as_i64);
    match (method, id) {
        (Some(_), Some(id)) => {
            let error = json!({
                "jsonrpc": "2.0",
                "id": id,
                "error": {"code": -32601, "message": "vc-server bridge does not accept client requests"},
            });
            let _ = write_json(stdin, &error).await;
        }
        (Some(_), None) => {}
        (None, Some(id)) => {
            if let Some(sender) = lock(pending).remove(&id) {
                let _ = sender.send(Ok(value));
            }
        }
        (None, None) => {}
    }
}

fn fail_pending(pending: &Pending, detail: &str) {
    for (_, sender) in lock(pending).drain() {
        let _ = sender.send(Err(detail.to_string()));
    }
}

async fn read_frame(reader: &mut BufReader<ChildStdout>) -> std::io::Result<Option<Vec<u8>>> {
    loop {
        let mut line = Vec::new();
        let read = reader.read_until(b'\n', &mut line).await?;
        if read == 0 {
            return Ok(None);
        }
        if line.len() > MAX_FRAME {
            return Err(std::io::Error::new(
                ErrorKind::InvalidData,
                "mcp frame exceeds 8MiB",
            ));
        }
        let trimmed = trim_ascii(&line);
        if trimmed.is_empty() {
            continue;
        }
        if starts_with_ignore_ascii(trimmed, b"content-length:") {
            let len = parse_content_length(trimmed)?;
            loop {
                let mut extra = Vec::new();
                let read = reader.read_until(b'\n', &mut extra).await?;
                if read == 0 {
                    return Err(std::io::Error::new(
                        ErrorKind::UnexpectedEof,
                        "truncated mcp headers",
                    ));
                }
                if trim_ascii(&extra).is_empty() {
                    break;
                }
            }
            if len > MAX_FRAME {
                return Err(std::io::Error::new(
                    ErrorKind::InvalidData,
                    "mcp frame exceeds 8MiB",
                ));
            }
            let mut body = vec![0_u8; len];
            reader.read_exact(&mut body).await?;
            return Ok(Some(body));
        }
        if trimmed.first() == Some(&b'{') {
            return Ok(Some(trimmed.to_vec()));
        }
    }
}

fn parse_content_length(header: &[u8]) -> std::io::Result<usize> {
    let text = String::from_utf8_lossy(header);
    let Some((_, value)) = text.split_once(':') else {
        return Err(std::io::Error::new(
            ErrorKind::InvalidData,
            "malformed Content-Length",
        ));
    };
    value
        .trim()
        .parse::<usize>()
        .map_err(|_| std::io::Error::new(ErrorKind::InvalidData, "malformed Content-Length"))
}

fn trim_ascii(bytes: &[u8]) -> &[u8] {
    let start = bytes
        .iter()
        .position(|byte| !matches!(byte, b' ' | b'\t' | b'\r' | b'\n'))
        .unwrap_or(bytes.len());
    let end = bytes
        .iter()
        .rposition(|byte| !matches!(byte, b' ' | b'\t' | b'\r' | b'\n'))
        .map(|index| index + 1)
        .unwrap_or(start);
    &bytes[start..end]
}

fn starts_with_ignore_ascii(bytes: &[u8], prefix: &[u8]) -> bool {
    bytes.len() >= prefix.len()
        && bytes
            .iter()
            .zip(prefix)
            .all(|(byte, expected)| byte.to_ascii_lowercase() == *expected)
}

async fn write_json(stdin: &AsyncMutex<ChildStdin>, value: &Value) -> std::io::Result<()> {
    let mut payload = serde_json::to_vec(value)
        .map_err(|error| std::io::Error::new(ErrorKind::InvalidData, error))?;
    payload.push(b'\n');
    let mut stdin = stdin.lock().await;
    stdin.write_all(&payload).await?;
    stdin.flush().await?;
    Ok(())
}

fn into_result(message: Value) -> Result<Value, BridgeError> {
    if let Some(error) = message.get("error") {
        let code = error
            .get("code")
            .and_then(Value::as_i64)
            .and_then(|code| i32::try_from(code).ok())
            .unwrap_or(-32_000);
        let text = error
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("remote error");
        return Err(BridgeError::Remote {
            code,
            message: text.to_string(),
        });
    }
    Ok(message.get("result").cloned().unwrap_or(Value::Null))
}

fn arm_backoff(state: &mut BridgeState, config: &BridgeConfig) -> Duration {
    state.failures = state.failures.saturating_add(1);
    let shift = state.failures.saturating_sub(1).min(6);
    let factor = 1_u32 << shift;
    let delay = config
        .initial_backoff
        .checked_mul(factor)
        .unwrap_or(config.max_backoff)
        .min(config.max_backoff)
        .max(config.initial_backoff);
    state.backoff_until = Some(Instant::now() + delay);
    delay
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|poison| poison.into_inner())
}

fn env_millis(name: &str, default_ms: u64) -> Duration {
    let Ok(raw) = std::env::var(name) else {
        return Duration::from_millis(default_ms);
    };
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Duration::from_millis(default_ms);
    }
    match trimmed.parse::<u64>() {
        Ok(value) if value > 0 => Duration::from_millis(value),
        _ => Duration::from_millis(default_ms),
    }
}

/// Pilots stay in front. Remote names that are already present, or that
/// are not `vc_*`, are dropped. The remote tool object is otherwise kept.
#[must_use]
pub fn merge_vc_tools(pilots: Vec<Value>, remote: Vec<Value>) -> Vec<Value> {
    let mut seen = HashSet::new();
    let mut merged = Vec::with_capacity(pilots.len() + remote.len());
    for tool in pilots {
        let Some(name) = tool_name(&tool) else {
            continue;
        };
        if seen.insert(name.to_string()) {
            merged.push(tool);
        }
    }
    for tool in remote {
        let Some(name) = tool_name(&tool) else {
            continue;
        };
        if !name.starts_with("vc_") || !seen.insert(name.to_string()) {
            continue;
        }
        merged.push(tool);
    }
    merged
}

fn tool_name(tool: &Value) -> Option<&str> {
    tool.get("name")
        .and_then(Value::as_str)
        .filter(|name| !name.is_empty())
}

pub fn resolve_bridge_command(env: &BridgeEnv) -> Result<BridgeCommand, String> {
    if let Some(program) = env
        .command
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        let args = parse_args(env.args_json.as_deref())?;
        return Ok(BridgeCommand {
            program: OsString::from(program),
            args,
            current_dir: None,
            env: Vec::new(),
        });
    }
    if let Some(path) = executable_on_path(&env.path, "vibecrafted-mcp") {
        return Ok(BridgeCommand::new(path, Vec::<OsString>::new()));
    }
    let checkout = find_checkout(&env.current_dir)
        .or_else(|| env.executable.as_deref().and_then(find_checkout));
    let Some(checkout) = checkout else {
        return Err(
            "vibecrafted-mcp is not on PATH and no source checkout with vibecrafted-mcp/pyproject.toml was found"
                .to_string(),
        );
    };
    let project = checkout.join("vibecrafted-mcp");
    if let Some(uv) = executable_on_path(&env.path, "uv") {
        return Ok(BridgeCommand {
            program: uv.into(),
            args: vec![
                OsString::from("run"),
                OsString::from("--project"),
                project.into(),
                OsString::from("vibecrafted-mcp"),
            ],
            current_dir: Some(checkout),
            env: Vec::new(),
        });
    }
    let Some(python) = executable_on_path(&env.path, "python3") else {
        return Err(format!(
            "found {} but neither uv nor python3 is on PATH",
            checkout.display()
        ));
    };
    let mut pythonpath = format!(
        "{}:{}",
        project.display(),
        checkout.join("vibecrafted-core").display()
    );
    if let Some(existing) = env.pythonpath.as_deref().filter(|value| !value.is_empty()) {
        pythonpath.push(':');
        pythonpath.push_str(existing);
    }
    Ok(BridgeCommand {
        program: python.into(),
        args: vec![
            OsString::from("-c"),
            OsString::from("from vibecrafted_mcp.server import main; raise SystemExit(main())"),
        ],
        current_dir: Some(checkout),
        env: vec![(OsString::from("PYTHONPATH"), OsString::from(pythonpath))],
    })
}

fn parse_args(raw: Option<&str>) -> Result<Vec<OsString>, String> {
    let Some(raw) = raw.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(Vec::new());
    };
    let value: Value = serde_json::from_str(raw)
        .map_err(|error| format!("VC_MCP_BRIDGE_ARGS is not a JSON array: {error}"))?;
    let Some(items) = value.as_array() else {
        return Err("VC_MCP_BRIDGE_ARGS must be a JSON array of strings".to_string());
    };
    items
        .iter()
        .map(|item| {
            item.as_str()
                .map(OsString::from)
                .ok_or_else(|| "VC_MCP_BRIDGE_ARGS must be a JSON array of strings".to_string())
        })
        .collect()
}

fn find_checkout(start: &Path) -> Option<PathBuf> {
    let mut current = if start.is_file() {
        start.parent()?.to_path_buf()
    } else {
        start.to_path_buf()
    };
    loop {
        if current.join("vibecrafted-mcp/pyproject.toml").is_file()
            && current.join("vibecrafted-core").is_dir()
        {
            return Some(current);
        }
        if !current.pop() {
            return None;
        }
    }
}

fn executable_on_path(path_var: &str, name: &str) -> Option<PathBuf> {
    path_var.split(':').find_map(|dir| {
        if dir.is_empty() {
            return None;
        }
        let candidate = Path::new(dir).join(name);
        is_executable(&candidate).then_some(candidate)
    })
}

fn is_executable(path: &Path) -> bool {
    let Ok(meta) = std::fs::metadata(path) else {
        return false;
    };
    if !meta.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        meta.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    {
        true
    }
}

fn watch_parent(command: &BridgeCommand) -> BridgeCommand {
    let Some(python) = executable_on_path(&std::env::var("PATH").unwrap_or_default(), "python3")
    else {
        return command.clone();
    };
    let mut args = Vec::with_capacity(command.args.len() + 3);
    args.push(OsString::from("-c"));
    args.push(OsString::from(PARENT_WATCH));
    args.push(command.program.clone());
    args.extend(command.args.iter().cloned());
    BridgeCommand {
        program: python.into(),
        args,
        current_dir: command.current_dir.clone(),
        env: command.env.clone(),
    }
}

const PARENT_WATCH: &str = r#"
import os, signal, sys, time

def kill_group(_signum, _frame):
    try:
        os.kill(-os.getpid(), signal.SIGKILL)
    except OSError:
        pass
    os._exit(0)

# SIGTERM stays blocked across exec on Linux until this handler exists.
# PR_SET_PDEATHSIG delivers SIGTERM, not SIGKILL, so the handler can run.
signal.signal(signal.SIGTERM, kill_group)
if hasattr(signal, "pthread_sigmask"):
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})

program = sys.argv[1]
args = sys.argv[2:]
pid = os.fork()
if pid == 0:
    try:
        os.execvp(program, [program, *args])
    except OSError:
        os._exit(127)
parent = os.getppid()
while True:
    if os.getppid() != parent:
        kill_group(0, None)
    waited, status = os.waitpid(pid, os.WNOHANG)
    if waited == pid:
        if os.WIFEXITED(status):
            os._exit(os.WEXITSTATUS(status))
        if os.WIFSIGNALED(status):
            os._exit(128 + os.WTERMSIG(status))
        os._exit(1)
    time.sleep(0.2)
"#;

fn install_terminate_handler() {
    static INSTALLED: Once = Once::new();
    INSTALLED.call_once(|| {
        #[cfg(unix)]
        // SAFETY: zeroed sigaction is a valid empty mask and null restorer.
        // The handler only calls kill, sigaction and raise, which are
        // async-signal-safe. Installed once for the process.
        unsafe {
            let mut action = std::mem::zeroed::<libc::sigaction>();
            action.sa_sigaction = on_terminate as *const () as libc::sighandler_t;
            let _ = libc::sigemptyset(std::ptr::addr_of_mut!(action.sa_mask));
            let _ = libc::sigaction(libc::SIGTERM, &action, std::ptr::null_mut());
            let _ = libc::sigaction(libc::SIGINT, &action, std::ptr::null_mut());
        }
    });
}

#[cfg(unix)]
extern "C" fn on_terminate(signal: i32) {
    let pid = TERMINATE_PGID.load(Ordering::Relaxed);
    // SAFETY: kill, sigaction and raise are async-signal-safe. pid > 1
    // refuses kill(-1), which would signal every process we can reach.
    // After the group is signalled, the default disposition is restored
    // and the original signal is raised so wait status stays WIFSIGNALED.
    unsafe {
        if pid > 1 {
            libc::kill(-pid, libc::SIGKILL);
        }
        let mut action = std::mem::zeroed::<libc::sigaction>();
        action.sa_sigaction = libc::SIG_DFL;
        let _ = libc::sigaction(signal, &action, std::ptr::null_mut());
        libc::raise(signal);
    }
}

fn signal_group(pid: i32, sig: i32) {
    #[cfg(unix)]
    // SAFETY: pid > 1 and the child called setpgid(0, 0), so -pid is that
    // group and not the caller's group or every process (kill(-1)).
    unsafe {
        if pid > 1 {
            libc::kill(-pid, sig);
        }
    }
    #[cfg(not(unix))]
    {
        let _ = (pid, sig);
    }
}

fn reap_leader(pid: i32) {
    #[cfg(unix)]
    {
        if pid <= 1 {
            return;
        }
        // SAFETY: waitpid reaps only this direct child. WNOHANG comes from
        // libc, not a guessed constant.
        unsafe {
            let mut status = 0;
            for _ in 0..20 {
                let reaped = libc::waitpid(pid, &mut status, libc::WNOHANG);
                if reaped == pid || reaped < 0 {
                    return;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            let _ = libc::waitpid(pid, &mut status, 0);
        }
    }
    #[cfg(not(unix))]
    {
        let _ = pid;
    }
}
