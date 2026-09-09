//! Launch declaration → canonical launcher argv → launch receipt.
//!
//! VOC does not own sessions, worktrees, agent environments or worker
//! lifecycles. It assembles one declaration (kind, agent, model, environment,
//! presentation, permissions, sandbox, repository, prompt) and hands it to the
//! shared `vibecrafted <skill> <agent>` launcher with `--json`. The launcher
//! validates the declaration before any control-plane write, decides the
//! transport (headless subprocess or a `new-tab` action inside the workspace's
//! vc-frame worker host) and answers with a `vibecrafted.launch_receipt.v1`
//! payload. That receipt — not a PID — is the only proof of acceptance VOC
//! shows.
//!
//! The prompt never enters argv: it rides `--prompt-stdin`, and the command
//! preview shown to the operator states only its size.

use anyhow::Context;
use serde::Deserialize;
use std::collections::BTreeMap;
use std::ffi::OsString;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::str::FromStr;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

/// Schema the canonical launcher stamps on its stdout receipt
/// (`workflow.LAUNCH_RECEIPT_SCHEMA`). A receipt carrying anything else is a
/// different contract, not a confirmation of this one.
pub const LAUNCH_RECEIPT_SCHEMA: &str = "vibecrafted.launch_receipt.v1";

/// Schema of the receipt's execution-controls block
/// (`ExecutionControls.receipt`).
pub const EXECUTION_CONTROLS_SCHEMA: &str = "vibecrafted.execution_controls.v1";

/// How long VOC waits for the canonical launcher to answer a launch. The
/// launcher detaches the worker itself and returns as soon as the declaration
/// is admitted or refused, so a healthy answer is prompt. Passing this bound
/// makes the outcome *unknown* — never "no worker started".
pub const LAUNCH_ANSWER_DEADLINE: Duration = Duration::from_secs(120);

/// Same bound for the read-only catalog probe, which starts nothing.
pub const CATALOG_ANSWER_DEADLINE: Duration = Duration::from_secs(30);

/// How long the stream readers may keep flushing after the child exits.
/// They are never joined: the launcher hands the detached worker its own copy
/// of these pipes, so a reader can legitimately outlive the launcher and
/// joining it would wedge the console.
const READER_FLUSH_GRACE: Duration = Duration::from_millis(250);

const CHILD_POLL_INTERVAL: Duration = Duration::from_millis(10);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LaunchKind {
    Workflow,
    Research,
    Review,
    Marbles,
    Skill(&'static crate::skills_catalog::SkillEntry),
}

impl LaunchKind {
    pub fn all() -> Vec<Self> {
        let mut kinds = vec![Self::Workflow, Self::Research, Self::Review, Self::Marbles];
        kinds.extend(
            crate::skills_catalog::CATALOG
                .iter()
                .filter(|entry| {
                    !matches!(
                        entry.slug,
                        "vc-workflow" | "vc-research" | "vc-review" | "vc-marbles"
                    )
                })
                .map(Self::Skill),
        );
        kinds
    }

    pub fn label(self) -> &'static str {
        match self {
            LaunchKind::Workflow => "workflow",
            LaunchKind::Research => "research",
            LaunchKind::Review => "review",
            LaunchKind::Marbles => "marbles",
            LaunchKind::Skill(entry) => entry.command_token(),
        }
    }

    pub fn human_title(self) -> &'static str {
        match self {
            LaunchKind::Workflow => "Workflow",
            LaunchKind::Research => "Research swarm",
            LaunchKind::Review => "Review",
            LaunchKind::Marbles => "Marbles loop",
            LaunchKind::Skill(entry) => entry.display,
        }
    }

    pub fn human_description(self) -> &'static str {
        match self {
            LaunchKind::Workflow => {
                "Best default. Examine the surface, plan the cut, then implement."
            }
            LaunchKind::Research => "Send a research pass first when the shape is still unclear.",
            LaunchKind::Review => {
                "Audit an existing surface for risk, regressions, and weak claims."
            }
            LaunchKind::Marbles => {
                "Run convergence loops when the code works but still lies or drifts."
            }
            LaunchKind::Skill(entry) => entry.one_line,
        }
    }

    /// Whether the launcher accepts prompt text for this kind at all.
    pub fn accepts_prompt(self) -> bool {
        !matches!(
            self,
            LaunchKind::Skill(entry)
                if matches!(entry.accepts, crate::skills_catalog::SkillPayloadKind::None)
        )
    }

    /// Kinds the launcher runs under a supervised runtime: the public
    /// `--permissions` / `--sandbox` controls are refused there before launch.
    pub fn supervised(self) -> bool {
        matches!(self, LaunchKind::Research | LaunchKind::Marbles)
            || matches!(self, LaunchKind::Skill(entry) if entry.slug == "vc-polarize")
    }
}

/// How the worker is presented. `Headless` runs without a visible terminal;
/// `Terminal` asks the launcher for its interactive view — a tab inside the
/// workspace's vc-frame worker host, opened by the launcher itself. VOC never
/// starts a frame of its own.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Presentation {
    #[default]
    Headless,
    Terminal,
}

impl Presentation {
    pub fn all() -> [Self; 2] {
        [Self::Headless, Self::Terminal]
    }

    /// The `--runtime` word the launcher accepts.
    pub fn label(self) -> &'static str {
        match self {
            Presentation::Headless => "headless",
            Presentation::Terminal => "terminal",
        }
    }

    pub fn human(self) -> &'static str {
        match self {
            Presentation::Headless => "headless — no visible terminal",
            Presentation::Terminal => {
                "interactive view — vc-frame tab in the workspace worker host"
            }
        }
    }

    pub fn cycle(self) -> Self {
        match self {
            Presentation::Headless => Presentation::Terminal,
            Presentation::Terminal => Presentation::Headless,
        }
    }

    /// Transport the launcher receipt must name for this presentation.
    pub fn expected_transport(self) -> &'static str {
        match self {
            Presentation::Headless => "headless",
            Presentation::Terminal => "vc-frame",
        }
    }
}

impl FromStr for Presentation {
    type Err = anyhow::Error;

    fn from_str(raw: &str) -> Result<Self, Self::Err> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "headless" => Ok(Self::Headless),
            // `visible` is the legacy public spelling of the interactive view.
            "terminal" | "visible" | "interactive" => Ok(Self::Terminal),
            other => Err(anyhow::anyhow!(
                "unsupported runtime: {other} (expected headless|terminal)"
            )),
        }
    }
}

/// Where the worker executes. Ids follow the launcher's canonical
/// runtime-policy names; availability comes from the launcher catalog.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Environment {
    #[default]
    LivingTree,
    FleetWorktrees,
    FleetVmLocal,
    FleetVmCloud,
}

impl Environment {
    pub fn all() -> [Self; 4] {
        [
            Self::LivingTree,
            Self::FleetWorktrees,
            Self::FleetVmLocal,
            Self::FleetVmCloud,
        ]
    }

    /// Canonical runtime-policy id (`spawn.RUNTIME_POLICIES`).
    pub fn policy_id(self) -> &'static str {
        match self {
            Environment::LivingTree => "local-native",
            Environment::FleetWorktrees => "local-worktrees",
            Environment::FleetVmLocal => "local-vm",
            Environment::FleetVmCloud => "cloud-soon",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Environment::LivingTree => "Living Tree",
            Environment::FleetWorktrees => "Fleet Worktrees",
            Environment::FleetVmLocal => "Fleet VM local",
            Environment::FleetVmCloud => "Fleet VM cloud",
        }
    }

    pub fn cycle(self) -> Self {
        match self {
            Environment::LivingTree => Environment::FleetWorktrees,
            Environment::FleetWorktrees => Environment::FleetVmLocal,
            Environment::FleetVmLocal => Environment::FleetVmCloud,
            Environment::FleetVmCloud => Environment::LivingTree,
        }
    }
}

impl FromStr for Environment {
    type Err = anyhow::Error;

    fn from_str(raw: &str) -> Result<Self, Self::Err> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "living-tree" | "local-native" => Ok(Self::LivingTree),
            "fleet-worktrees" | "local-worktrees" | "worktree" | "worktrees" => {
                Ok(Self::FleetWorktrees)
            }
            "fleet-vm-local" | "local-vm" => Ok(Self::FleetVmLocal),
            "fleet-vm-cloud" | "cloud-soon" | "cloud" => Ok(Self::FleetVmCloud),
            other => Err(anyhow::anyhow!(
                "unsupported environment: {other} (expected living-tree|fleet-worktrees|fleet-vm-local|fleet-vm-cloud)"
            )),
        }
    }
}

/// Public `--permissions` words; `Default` omits the flag and lets the
/// launcher apply the provider default it reports in the receipt.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum PermissionPolicy {
    #[default]
    Default,
    Bypass,
    Auto,
    AcceptEdits,
    ReadOnly,
}

impl PermissionPolicy {
    pub fn all() -> [Self; 5] {
        [
            Self::Default,
            Self::Bypass,
            Self::Auto,
            Self::AcceptEdits,
            Self::ReadOnly,
        ]
    }

    pub fn word(self) -> Option<&'static str> {
        match self {
            PermissionPolicy::Default => None,
            PermissionPolicy::Bypass => Some("bypass"),
            PermissionPolicy::Auto => Some("auto"),
            PermissionPolicy::AcceptEdits => Some("accept-edits"),
            PermissionPolicy::ReadOnly => Some("read-only"),
        }
    }

    pub fn label(self) -> &'static str {
        self.word().unwrap_or("provider default")
    }

    pub fn cycle(self) -> Self {
        match self {
            PermissionPolicy::Default => PermissionPolicy::Bypass,
            PermissionPolicy::Bypass => PermissionPolicy::Auto,
            PermissionPolicy::Auto => PermissionPolicy::AcceptEdits,
            PermissionPolicy::AcceptEdits => PermissionPolicy::ReadOnly,
            PermissionPolicy::ReadOnly => PermissionPolicy::Default,
        }
    }
}

/// Public `--sandbox` words; `Default` omits the flag (provider default).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SandboxChoice {
    #[default]
    Default,
    On,
    Off,
}

impl SandboxChoice {
    pub fn all() -> [Self; 3] {
        [Self::Default, Self::On, Self::Off]
    }

    pub fn word(self) -> Option<&'static str> {
        match self {
            SandboxChoice::Default => None,
            SandboxChoice::On => Some("true"),
            SandboxChoice::Off => Some("false"),
        }
    }

    pub fn label(self) -> &'static str {
        self.word().unwrap_or("provider default")
    }

    pub fn cycle(self) -> Self {
        match self {
            SandboxChoice::Default => SandboxChoice::On,
            SandboxChoice::On => SandboxChoice::Off,
            SandboxChoice::Off => SandboxChoice::Default,
        }
    }
}

/// One complete launch declaration. Every field reaches the launcher argv or
/// stdin; nothing is dropped or replaced by a default without the operator
/// seeing it (see `build_launch_command`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LaunchRequest {
    pub kind: LaunchKind,
    pub agent: String,
    pub prompt: String,
    pub presentation: Presentation,
    pub environment: Environment,
    pub permissions: PermissionPolicy,
    pub sandbox: SandboxChoice,
    pub model: String,
    pub repo: PathBuf,
    pub count: Option<u32>,
    pub depth: Option<u32>,
    pub env: BTreeMap<String, OsString>,
}

impl LaunchRequest {
    /// Human summary of what was declared, for trails and receipts.
    pub fn summary(&self) -> String {
        let mut parts = vec![
            format!("{} {}", self.kind.label(), self.agent),
            self.environment.label().to_string(),
            self.presentation.label().to_string(),
        ];
        if !self.model.trim().is_empty() {
            parts.push(format!("model {}", self.model.trim()));
        }
        if let Some(word) = self.permissions.word() {
            parts.push(format!("permissions {word}"));
        }
        if let Some(word) = self.sandbox.word() {
            parts.push(format!("sandbox {word}"));
        }
        parts.join(" · ")
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LaunchCommand {
    pub program: PathBuf,
    pub args: Vec<OsString>,
    pub env: BTreeMap<String, OsString>,
    /// Private input written to the child's stdin (the prompt). Never part of
    /// argv, never rendered by `command_line`.
    pub stdin: Option<String>,
}

impl LaunchCommand {
    /// Public argv only — safe to show, log, or copy.
    pub fn command_line(&self) -> String {
        let mut parts = vec![self.program.to_string_lossy().into_owned()];
        parts.extend(
            self.args
                .iter()
                .map(|value| value.to_string_lossy().into_owned()),
        );
        parts.join(" ")
    }

    /// `command_line` plus an honest note about the private stdin payload.
    pub fn preview(&self) -> String {
        match self.stdin.as_deref() {
            Some(prompt) if !prompt.trim().is_empty() => format!(
                "{}  <stdin: prompt, {} chars>",
                self.command_line(),
                prompt.chars().count()
            ),
            _ => self.command_line(),
        }
    }

    /// Interactive spawn used by deep controls (attach / resume / mux
    /// health) that hand the terminal to a child process.
    pub fn spawn_interactive_with_stderr(&self) -> anyhow::Result<std::process::Child> {
        let mut command = Command::new(&self.program);
        command.args(&self.args);
        command.envs(&self.env);
        command.stdin(Stdio::inherit());
        command.stdout(Stdio::inherit());
        command.stderr(Stdio::piped());
        command.spawn().context("failed to spawn launch command")
    }

    /// Run the launcher under a bounded wait, feeding the private prompt
    /// through stdin and capturing its receipt. The launcher detaches the
    /// worker itself, so this returns as soon as the launch is admitted or
    /// refused — or, at the deadline, undecided.
    pub fn run_capturing(&self, deadline: Duration) -> anyhow::Result<LauncherRun> {
        let mut command = Command::new(&self.program);
        command.args(&self.args);
        command.envs(&self.env);
        run_bounded(
            command,
            self.stdin.clone(),
            deadline,
            &format!("launcher {}", self.program.to_string_lossy()),
        )
    }
}

/// What a bounded launcher invocation produced.
#[derive(Debug)]
pub enum LauncherRun {
    /// The launcher exited on its own; the streams below are what it wrote.
    Completed(Output),
    /// The deadline passed with the launcher still running. Nothing was
    /// killed and nothing is retried: a worker may already exist, so the
    /// outcome is unknown rather than failed.
    Undecided {
        waited: Duration,
        stdout: Vec<u8>,
        stderr: Vec<u8>,
    },
}

/// Drain one child stream into a shared buffer on its own thread, marking
/// itself done in `finished`. Incremental by design: the caller can snapshot
/// whatever arrived even when the reader never reaches EOF.
fn pump<R: Read + Send + 'static>(
    source: Option<R>,
    finished: &Arc<AtomicU8>,
) -> Arc<Mutex<Vec<u8>>> {
    let buffer = Arc::new(Mutex::new(Vec::new()));
    let Some(mut source) = source else {
        finished.fetch_add(1, Ordering::SeqCst);
        return buffer;
    };
    let sink = Arc::clone(&buffer);
    let finished = Arc::clone(finished);
    thread::spawn(move || {
        let mut chunk = [0u8; 8192];
        loop {
            match source.read(&mut chunk) {
                Ok(0) | Err(_) => break,
                Ok(read) => {
                    if let Ok(mut guard) = sink.lock() {
                        guard.extend_from_slice(&chunk[..read]);
                    }
                }
            }
        }
        finished.fetch_add(1, Ordering::SeqCst);
    });
    buffer
}

fn snapshot(buffer: &Arc<Mutex<Vec<u8>>>) -> Vec<u8> {
    buffer.lock().map(|guard| guard.clone()).unwrap_or_default()
}

/// Spawn `command` with both output streams drained concurrently and the
/// private stdin payload written on its own thread, then wait at most
/// `deadline` for it to exit.
///
/// Every direction moves at once on purpose. A launcher that writes a long
/// diagnostic to stderr before reading a single byte of a large prompt would
/// otherwise fill its pipe while VOC is still blocked writing stdin — a
/// two-sided wedge that no timeout on `wait` can unstick.
pub(crate) fn run_bounded(
    mut command: Command,
    stdin_payload: Option<String>,
    deadline: Duration,
    what: &str,
) -> anyhow::Result<LauncherRun> {
    command.stdin(if stdin_payload.is_some() {
        Stdio::piped()
    } else {
        Stdio::null()
    });
    command.stdout(Stdio::piped());
    command.stderr(Stdio::piped());
    let mut child = command
        .spawn()
        .with_context(|| format!("failed to start {what}"))?;

    let finished = Arc::new(AtomicU8::new(0));
    let stdout = pump(child.stdout.take(), &finished);
    let stderr = pump(child.stderr.take(), &finished);

    if let Some(payload) = stdin_payload {
        // The prompt is written on its own thread so a full pipe stalls only
        // the writer. A launcher that refuses before reading stdin closes the
        // pipe early; that is a refusal, not a transport failure.
        if let Some(mut sink) = child.stdin.take() {
            thread::spawn(move || {
                let _ = sink.write_all(payload.as_bytes());
                let _ = sink.flush();
            });
        }
    }

    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let grace = Instant::now();
                while finished.load(Ordering::SeqCst) < 2 && grace.elapsed() < READER_FLUSH_GRACE {
                    thread::sleep(CHILD_POLL_INTERVAL);
                }
                return Ok(LauncherRun::Completed(Output {
                    status,
                    stdout: snapshot(&stdout),
                    stderr: snapshot(&stderr),
                }));
            }
            Ok(None) => {}
            Err(error) => {
                return Err(anyhow::Error::new(error).context(format!("failed to wait for {what}")));
            }
        }
        let waited = started.elapsed();
        if waited >= deadline {
            // Nothing is killed here. The launcher may already have admitted
            // a run and detached a worker; ending it to tidy up a client-side
            // wait would destroy the work the operator asked for. The child is
            // reaped in the background so it cannot become a zombie.
            thread::spawn(move || {
                let _ = child.wait();
            });
            return Ok(LauncherRun::Undecided {
                waited,
                stdout: snapshot(&stdout),
                stderr: snapshot(&stderr),
            });
        }
        thread::sleep(CHILD_POLL_INTERVAL);
    }
}

/// Assemble the canonical launcher argv for one declaration.
///
/// Shape: `<skill> <agent> [--prompt-stdin] [--count n --depth n]
/// --runtime <presentation> --repo <path> [--worktree true]
/// [--permissions w] [--sandbox w] [--model m] --json`.
/// Environments without a skill-launcher surface (VM local/cloud) add no
/// flag here; `App::launch_plan` refuses them before this is spawned.
pub fn build_launch_command(deck: impl AsRef<Path>, request: &LaunchRequest) -> LaunchCommand {
    let mut args: Vec<OsString> = vec![request.kind.label().into()];
    if !request.agent.trim().is_empty() {
        args.push(request.agent.trim().into());
    }
    let prompt = request.prompt.trim();
    let stdin = if request.kind.accepts_prompt() && !prompt.is_empty() {
        args.push("--prompt-stdin".into());
        Some(request.prompt.clone())
    } else {
        None
    };
    if matches!(request.kind, LaunchKind::Marbles) {
        args.push("--count".into());
        args.push(request.count.unwrap_or(3).to_string().into());
        args.push("--depth".into());
        args.push(request.depth.unwrap_or(3).to_string().into());
    }
    args.push("--runtime".into());
    args.push(request.presentation.label().into());
    args.push("--repo".into());
    args.push(request.repo.as_os_str().to_os_string());
    if request.environment == Environment::FleetWorktrees {
        args.push("--worktree".into());
        args.push("true".into());
    }
    if let Some(word) = request.permissions.word() {
        args.push("--permissions".into());
        args.push(word.into());
    }
    if let Some(word) = request.sandbox.word() {
        args.push("--sandbox".into());
        args.push(word.into());
    }
    if !request.model.trim().is_empty() {
        args.push("--model".into());
        args.push(request.model.trim().into());
    }
    args.push("--json".into());
    LaunchCommand {
        program: deck.as_ref().to_path_buf(),
        args,
        env: request.env.clone(),
        stdin,
    }
}

/// Execution-controls block of the launch receipt
/// (`vibecrafted.execution_controls.v1`).
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
pub struct ExecutionControlsReceipt {
    #[serde(default)]
    pub schema: String,
    #[serde(default)]
    pub provider: String,
    #[serde(default)]
    pub permissions_requested: String,
    #[serde(default)]
    pub permissions_effective: String,
    #[serde(default)]
    pub sandbox_requested: String,
    #[serde(default)]
    pub sandbox_effective: String,
    #[serde(default)]
    pub provider_flags: Vec<String>,
    #[serde(default)]
    pub behavior: String,
}

/// The launcher's stdout receipt (`vibecrafted.launch_receipt.v1` merged with
/// the launch result). Unknown fields are ignored; absent fields read empty.
#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
pub struct LaunchReceipt {
    #[serde(default)]
    pub schema: String,
    #[serde(default)]
    pub run_id: String,
    #[serde(default)]
    pub agent: String,
    #[serde(default)]
    pub skill: String,
    #[serde(default)]
    pub root: String,
    #[serde(default)]
    pub accepted: bool,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub error: String,
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub transport: String,
    #[serde(default)]
    pub operator_session: String,
    #[serde(default)]
    pub worktree: bool,
    #[serde(default)]
    pub worktree_path: String,
    #[serde(default)]
    pub worktree_branch: String,
    #[serde(default)]
    pub worktree_baseline_sha: String,
    #[serde(default)]
    pub parent_root: String,
    #[serde(default)]
    pub model_requested: String,
    #[serde(default)]
    pub model_override_supported: Option<bool>,
    #[serde(default)]
    pub model_override_skipped: Option<bool>,
    #[serde(default)]
    pub model_override_skip_reason: String,
    #[serde(default)]
    pub execution_controls: Option<ExecutionControlsReceipt>,
    #[serde(default)]
    pub report: String,
    #[serde(default)]
    pub transcript: String,
    #[serde(default)]
    pub meta: String,
}

impl LaunchReceipt {
    pub fn parse(stdout: &[u8]) -> anyhow::Result<Self> {
        let text = String::from_utf8_lossy(stdout);
        let trimmed = text.trim();
        if trimmed.is_empty() {
            anyhow::bail!("launcher printed no receipt");
        }
        // The receipt is the last JSON object on stdout; tolerate leading
        // human lines from wrappers by scanning for the first `{`.
        let start = trimmed.find('{').unwrap_or(0);
        serde_json::from_str(&trimmed[start..]).context("launch receipt is not valid JSON")
    }

    pub fn refusal_reason(&self) -> String {
        for candidate in [&self.message, &self.error, &self.reason, &self.status] {
            if !candidate.trim().is_empty() {
                return candidate.trim().to_string();
            }
        }
        "launcher refused the declaration without a reason".to_string()
    }
}

/// What the operator declared, kept next to the receipt so the confirmation
/// can prove the launcher honored every choice.
///
/// `permissions_*`/`sandbox_effective`/`provider_flags` carry what the
/// launcher's own catalog promised for this cell. The receipt is judged
/// against that promise, so a silent downgrade between catalog and launch is
/// visible instead of being read back as agreement.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LaunchExpectation {
    pub summary: String,
    pub agent: String,
    pub skill: String,
    pub presentation: Presentation,
    pub environment: Environment,
    pub model: String,
    pub repo: PathBuf,
    pub permissions: Option<String>,
    pub sandbox: Option<String>,
    /// Whether the launcher reports execution controls for this kind at all.
    /// Supervised runtimes refuse the public controls before launch and emit
    /// no controls block, so their absence there is the contract, not a gap.
    pub expects_execution_controls: bool,
    pub promised_permissions_effective: Option<String>,
    pub promised_sandbox_effective: Option<String>,
    pub promised_provider_flags: Option<Vec<String>>,
}

impl LaunchExpectation {
    /// Build the expectation from the declaration plus the catalog cell the
    /// launcher published for it. `promised` is `None` only where no catalog
    /// is available (unit tests); the console refuses launches whose cell the
    /// catalog does not report.
    pub fn new(request: &LaunchRequest, promised: Option<&crate::catalog::ControlCell>) -> Self {
        Self {
            summary: request.summary(),
            agent: request.agent.trim().to_string(),
            skill: request.kind.label().to_string(),
            presentation: request.presentation,
            environment: request.environment,
            model: request.model.trim().to_string(),
            repo: request.repo.clone(),
            permissions: request.permissions.word().map(ToOwned::to_owned),
            sandbox: request.sandbox.word().map(ToOwned::to_owned),
            expects_execution_controls: !request.kind.supervised(),
            promised_permissions_effective: promised
                .map(|cell| cell.permissions_effective.trim().to_string()),
            promised_sandbox_effective: promised
                .map(|cell| cell.sandbox_effective.trim().to_string()),
            promised_provider_flags: promised.map(|cell| cell.provider_flags.clone()),
        }
    }
}

/// Whether the launcher took the run at all.
///
/// Deliberately separate from whether it honored the declaration: a run can
/// exist and still not be the run that was declared, and a wait can end with
/// no answer while a worker is already running.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Admission {
    /// The launcher accepted the declaration and named the run.
    Admitted,
    /// The launcher answered and refused before any worker existed.
    Refused,
    /// VOC never handed the declaration to the launcher, so nothing started.
    Failed,
    /// The launcher ran but VOC holds no readable admission: no receipt, an
    /// acceptance without a run id, or a wait that ended first. A worker may
    /// exist. VOC does not guess and never retries on its own.
    Unknown,
}

/// How much of the declaration the receipt actually confirms.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Confirmation {
    /// Every declared field is named by the receipt and agrees with it.
    Confirmed,
    /// Nothing contradicts the declaration, but the receipt is silent about
    /// at least one declared field.
    Unverified,
    /// The receipt contradicts at least one declared field.
    Mismatched,
}

/// The declaration checked field by field against the receipt. Silence and
/// contradiction are different answers and are never merged.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DeclarationAudit {
    pub mismatched: Vec<String>,
    pub unverified: Vec<String>,
}

impl DeclarationAudit {
    pub fn confirmation(&self) -> Confirmation {
        if !self.mismatched.is_empty() {
            Confirmation::Mismatched
        } else if !self.unverified.is_empty() {
            Confirmation::Unverified
        } else {
            Confirmation::Confirmed
        }
    }

    fn compare(&mut self, field: &str, declared: &str, reported: &str) {
        let reported = reported.trim();
        if reported.is_empty() {
            self.unverified.push(format!(
                "{field}: declared {declared}; the receipt is silent"
            ));
        } else if reported != declared {
            self.mismatched.push(format!(
                "{field}: declared {declared} but the launcher recorded {reported}"
            ));
        }
    }
}

/// Result of one launcher invocation as VOC presents it.
#[derive(Debug, Clone, PartialEq)]
pub struct LaunchOutcome {
    pub preview: String,
    pub expectation: LaunchExpectation,
    pub exit_code: Option<i32>,
    pub receipt: Option<LaunchReceipt>,
    pub stderr: String,
    /// Set when VOC could not start the launcher, or it exited without a
    /// parseable receipt.
    pub transport_error: Option<String>,
    /// Set when the bounded wait ended with the launcher still running.
    pub undecided_after: Option<Duration>,
}

impl LaunchOutcome {
    pub fn from_run(
        preview: String,
        expectation: LaunchExpectation,
        run: anyhow::Result<LauncherRun>,
    ) -> Self {
        match run {
            Ok(LauncherRun::Completed(output)) => {
                let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
                let (receipt, transport_error) = match LaunchReceipt::parse(&output.stdout) {
                    Ok(receipt) => (Some(receipt), None),
                    Err(error) => (None, Some(format!("{error:#}"))),
                };
                Self {
                    preview,
                    expectation,
                    exit_code: output.status.code(),
                    receipt,
                    stderr,
                    transport_error,
                    undecided_after: None,
                }
            }
            Ok(LauncherRun::Undecided {
                waited,
                stdout,
                stderr,
            }) => Self {
                preview,
                expectation,
                exit_code: None,
                // Partial stdout can already carry the receipt: the launcher
                // may admit a run and keep running. Failing to parse one here
                // is the absence of an answer, not a transport failure.
                receipt: LaunchReceipt::parse(&stdout).ok(),
                stderr: String::from_utf8_lossy(&stderr).into_owned(),
                transport_error: None,
                undecided_after: Some(waited),
            },
            Err(error) => Self {
                preview,
                expectation,
                exit_code: None,
                receipt: None,
                stderr: String::new(),
                transport_error: Some(format!("{error:#}")),
                undecided_after: None,
            },
        }
    }

    /// Whether a run exists, as a fact of its own.
    pub fn admission(&self) -> Admission {
        match self.receipt.as_ref() {
            Some(receipt) if receipt.accepted && !receipt.run_id.trim().is_empty() => {
                Admission::Admitted
            }
            // Acceptance without a name still means a worker may exist.
            Some(receipt) if receipt.accepted => Admission::Unknown,
            Some(_) => Admission::Refused,
            // The launcher was started but said nothing VOC can read. It may
            // have mutated the control plane before dying or before the wait
            // ended, so the outcome is unknown, not empty.
            None if self.transport_error.is_some() && self.exit_code.is_none() => Admission::Failed,
            None => Admission::Unknown,
        }
    }

    /// Whether the bounded wait ended before the launcher answered.
    pub fn timed_out(&self) -> Option<Duration> {
        self.undecided_after
    }

    pub fn run_id(&self) -> Option<&str> {
        self.receipt
            .as_ref()
            .map(|receipt| receipt.run_id.trim())
            .filter(|value| !value.is_empty())
    }

    /// Every declared field checked against the receipt.
    ///
    /// A field the receipt does not carry is *unverified*; a field it carries
    /// with another value is *mismatched*. Neither is ever reported as a
    /// confirmed launch.
    pub fn audit(&self) -> DeclarationAudit {
        let mut audit = DeclarationAudit::default();
        let Some(receipt) = self.receipt.as_ref() else {
            audit.unverified.push(
                "no receipt: the launcher confirmed nothing about this declaration".to_string(),
            );
            return audit;
        };

        // The receipt has to be this contract before any field in it counts.
        if receipt.schema.trim().is_empty() {
            audit
                .unverified
                .push("receipt schema: the launcher stamped none".to_string());
        } else if receipt.schema.trim() != LAUNCH_RECEIPT_SCHEMA {
            audit.mismatched.push(format!(
                "receipt schema: expected {LAUNCH_RECEIPT_SCHEMA}, got {}",
                receipt.schema.trim()
            ));
        }

        if !receipt.accepted {
            // A refusal has no declaration to honor; only the envelope is checked.
            return audit;
        }

        if receipt.run_id.trim().is_empty() {
            audit
                .mismatched
                .push("run: the launcher reported acceptance without naming a run".to_string());
        }

        match self.exit_code {
            Some(0) => {}
            Some(code) => audit.mismatched.push(format!(
                "launcher exit: reported acceptance and then exited {code}"
            )),
            None => audit.unverified.push(
                "launcher exit: not observed — the launcher had not exited when the wait ended"
                    .to_string(),
            ),
        }

        audit.compare("agent", &self.expectation.agent, &receipt.agent);
        audit.compare("skill", &self.expectation.skill, &receipt.skill);

        // With a worktree the receipt's `root` is the checkout; `parent_root`
        // names the repository it was cut from.
        let (repo_field, reported_repo) = if receipt.worktree {
            ("repository (parent of the worktree)", &receipt.parent_root)
        } else {
            ("repository", &receipt.root)
        };
        let reported_repo = reported_repo.trim();
        if reported_repo.is_empty() {
            audit.unverified.push(format!(
                "{repo_field}: declared {}; the receipt is silent",
                self.expectation.repo.display()
            ));
        } else if canonical(Path::new(reported_repo)) != canonical(&self.expectation.repo) {
            audit.mismatched.push(format!(
                "{repo_field}: declared {} but the launcher used {reported_repo}",
                self.expectation.repo.display()
            ));
        }

        let expected_transport = self.expectation.presentation.expected_transport();
        if receipt.transport.trim().is_empty() {
            audit.unverified.push(format!(
                "presentation: declared {} ({expected_transport}); the receipt names no transport",
                self.expectation.presentation.label()
            ));
        } else if receipt.transport.trim() != expected_transport {
            audit.mismatched.push(format!(
                "presentation: declared {} ({expected_transport}) but the launcher used transport {}",
                self.expectation.presentation.label(),
                receipt.transport.trim()
            ));
        }

        match self.expectation.environment {
            Environment::FleetWorktrees => {
                if !receipt.worktree {
                    audit.mismatched.push(
                        "environment: declared Fleet Worktrees but the receipt names no worktree"
                            .to_string(),
                    );
                } else if receipt.worktree_path.trim().is_empty() {
                    audit.unverified.push(
                        "environment: the receipt marks a worktree but names no path".to_string(),
                    );
                }
            }
            Environment::LivingTree => {
                if receipt.worktree {
                    audit.mismatched.push(
                        "environment: declared Living Tree but the launcher cut a worktree"
                            .to_string(),
                    );
                }
            }
            // VM environments carry no skill-launcher entrypoint and are
            // refused before a process exists.
            Environment::FleetVmLocal | Environment::FleetVmCloud => {}
        }

        self.audit_model(receipt, &mut audit);
        self.audit_controls(receipt, &mut audit);
        audit
    }

    fn audit_model(&self, receipt: &LaunchReceipt, audit: &mut DeclarationAudit) {
        let declared = self.expectation.model.trim();
        let reported = receipt.model_requested.trim();
        if declared.is_empty() {
            if !reported.is_empty() {
                audit.mismatched.push(format!(
                    "model: declared the provider default but the launcher pinned {reported}"
                ));
            }
            return;
        }
        audit.compare("model", declared, reported);
        match (
            receipt.model_override_supported,
            receipt.model_override_skipped,
        ) {
            (_, Some(true)) | (Some(false), _) => audit.mismatched.push(format!(
                "model: {declared} was not pinned ({})",
                if receipt.model_override_skip_reason.trim().is_empty() {
                    "launcher skipped the override"
                } else {
                    receipt.model_override_skip_reason.trim()
                }
            )),
            (Some(true), _) => {}
            (None, _) => audit.unverified.push(format!(
                "model: the receipt does not say whether {declared} was pinned"
            )),
        }
    }

    fn audit_controls(&self, receipt: &LaunchReceipt, audit: &mut DeclarationAudit) {
        let declared_permissions = self.expectation.permissions.as_deref().unwrap_or("");
        let declared_sandbox = self.expectation.sandbox.as_deref().unwrap_or("");
        let Some(controls) = receipt.execution_controls.as_ref() else {
            if self.expectation.expects_execution_controls {
                audit.unverified.push(
                    "execution controls: the receipt carries no execution-controls block"
                        .to_string(),
                );
            }
            return;
        };
        if controls.schema.trim().is_empty() {
            audit
                .unverified
                .push("execution controls schema: the launcher stamped none".to_string());
        } else if controls.schema.trim() != EXECUTION_CONTROLS_SCHEMA {
            audit.mismatched.push(format!(
                "execution controls schema: expected {EXECUTION_CONTROLS_SCHEMA}, got {}",
                controls.schema.trim()
            ));
        }
        audit.compare(
            "execution controls provider",
            &self.expectation.agent,
            &controls.provider,
        );

        // Requested values are echoed verbatim, so silence here is itself a
        // contradiction of an explicit request.
        for (field, declared, reported) in [
            (
                "permissions requested",
                declared_permissions,
                controls.permissions_requested.trim(),
            ),
            (
                "sandbox requested",
                declared_sandbox,
                controls.sandbox_requested.trim(),
            ),
        ] {
            if reported != declared {
                audit.mismatched.push(format!(
                    "{field}: declared {} but the launcher recorded {}",
                    if declared.is_empty() {
                        "provider default"
                    } else {
                        declared
                    },
                    if reported.is_empty() {
                        "provider default"
                    } else {
                        reported
                    }
                ));
            }
        }

        // Effective values are judged against what the catalog promised for
        // this exact cell, so a downgrade between catalog and launch shows up.
        for (field, promised, reported) in [
            (
                "permissions effective",
                self.expectation.promised_permissions_effective.as_deref(),
                controls.permissions_effective.trim(),
            ),
            (
                "sandbox effective",
                self.expectation.promised_sandbox_effective.as_deref(),
                controls.sandbox_effective.trim(),
            ),
        ] {
            match promised {
                None => audit.unverified.push(format!(
                    "{field}: the launcher catalog promised nothing to check {} against",
                    if reported.is_empty() {
                        "silence"
                    } else {
                        reported
                    }
                )),
                Some(promised) if reported.is_empty() => audit.unverified.push(format!(
                    "{field}: the catalog promises {promised}; the receipt is silent"
                )),
                Some(promised) if reported != promised => audit.mismatched.push(format!(
                    "{field}: the catalog promises {promised} but the launcher applied {reported}"
                )),
                Some(_) => {}
            }
        }

        match self.expectation.promised_provider_flags.as_deref() {
            None => audit.unverified.push(
                "provider flags: the launcher catalog promised none to check against".to_string(),
            ),
            Some(promised) if controls.provider_flags != promised => {
                audit.mismatched.push(format!(
                    "provider flags: the catalog promises [{}] but the launcher applied [{}]",
                    promised.join(" "),
                    controls.provider_flags.join(" ")
                ));
            }
            Some(_) => {}
        }
    }

    /// One trail line: what happened to the run and whether it is the run
    /// that was declared.
    pub fn trail_line(&self) -> String {
        let audit = self.audit();
        match self.admission() {
            Admission::Admitted => {
                let receipt = self.receipt.as_ref().expect("admitted implies a receipt");
                let mut line = format!(
                    "run {} · {} · repo {}",
                    receipt.run_id.trim(),
                    self.expectation.summary,
                    if receipt.worktree_path.trim().is_empty() {
                        receipt.root.trim()
                    } else {
                        receipt.worktree_path.trim()
                    }
                );
                if !receipt.transport.trim().is_empty() {
                    line.push_str(&format!(" · {}", receipt.transport.trim()));
                }
                match audit.confirmation() {
                    Confirmation::Confirmed => {}
                    Confirmation::Unverified => line.push_str(&format!(
                        " · UNVERIFIED ({} field{} unconfirmed)",
                        audit.unverified.len(),
                        if audit.unverified.len() == 1 { "" } else { "s" }
                    )),
                    Confirmation::Mismatched => line.push_str(" · DECLARATION NOT HONORED"),
                }
                if let Some(waited) = self.undecided_after {
                    line.push_str(&format!(
                        " · launcher still running after {}s",
                        waited.as_secs()
                    ));
                }
                line
            }
            Admission::Refused => format!(
                "refused · {} · {}",
                self.expectation.summary,
                self.receipt
                    .as_ref()
                    .map(LaunchReceipt::refusal_reason)
                    .unwrap_or_default()
            ),
            Admission::Failed => format!(
                "failed before the launcher started · {} · {}",
                self.expectation.summary,
                self.transport_error
                    .as_deref()
                    .unwrap_or("VOC could not start the launcher")
            ),
            Admission::Unknown => format!(
                "UNKNOWN · {} · {} — a worker may already be running; check Live Runs before relaunching",
                self.expectation.summary,
                self.unknown_reason()
            ),
        }
    }

    /// Why the outcome is unknown, in the operator's terms.
    fn unknown_reason(&self) -> String {
        if let Some(waited) = self.undecided_after {
            return format!(
                "the launcher had not answered after {}s and was left running",
                waited.as_secs()
            );
        }
        if let Some(receipt) = self.receipt.as_ref()
            && receipt.accepted
        {
            return "the launcher reported acceptance without naming a run".to_string();
        }
        match (&self.transport_error, self.exit_code) {
            (Some(error), Some(code)) => {
                format!("launcher exited {code} without a receipt: {error}")
            }
            (Some(error), None) => error.clone(),
            (None, Some(code)) => format!("launcher exited {code} without a receipt"),
            (None, None) => "the launcher produced no receipt".to_string(),
        }
    }

    /// Full confirmation / failure detail for the operator overlay.
    pub fn detail_lines(&self) -> Vec<String> {
        let mut lines = vec![format!("declared: {}", self.expectation.summary)];
        lines.push(format!("command: {}", self.preview));
        if matches!(self.admission(), Admission::Unknown) {
            lines.push(String::new());
            lines.push(format!("OUTCOME UNKNOWN: {}", self.unknown_reason()));
            lines.push(
                "A worker may already be running. VOC did not stop the launcher and will not"
                    .to_string(),
            );
            lines.push("relaunch on its own — check Live Runs, then decide.".to_string());
        }
        match &self.receipt {
            Some(receipt) if receipt.accepted => {
                lines.push(String::new());
                lines.push(format!(
                    "run id: {}  status: {}",
                    receipt.run_id,
                    if receipt.status.is_empty() {
                        "launching"
                    } else {
                        receipt.status.as_str()
                    }
                ));
                lines.push(format!(
                    "agent: {}  skill: {}",
                    receipt.agent, receipt.skill
                ));
                lines.push(format!("repo: {}", receipt.root));
                if receipt.worktree {
                    lines.push(format!(
                        "worktree: {} (branch {}, baseline {}, parent {})",
                        receipt.worktree_path,
                        receipt.worktree_branch,
                        short_sha(&receipt.worktree_baseline_sha),
                        receipt.parent_root
                    ));
                }
                lines.push(format!(
                    "presentation: {} (transport {})",
                    self.expectation.presentation.label(),
                    if receipt.transport.is_empty() {
                        "unreported"
                    } else {
                        receipt.transport.as_str()
                    }
                ));
                if !receipt.operator_session.is_empty() {
                    lines.push(format!(
                        "worker host session: {}  (Monitor → Enter attaches)",
                        receipt.operator_session
                    ));
                }
                if let Some(controls) = receipt.execution_controls.as_ref() {
                    lines.push(format!(
                        "permissions: {} (requested {})",
                        controls.permissions_effective,
                        if controls.permissions_requested.is_empty() {
                            "provider default"
                        } else {
                            controls.permissions_requested.as_str()
                        }
                    ));
                    lines.push(format!(
                        "sandbox: {} (requested {})",
                        controls.sandbox_effective,
                        if controls.sandbox_requested.is_empty() {
                            "provider default"
                        } else {
                            controls.sandbox_requested.as_str()
                        }
                    ));
                }
                if !receipt.model_requested.is_empty() {
                    lines.push(format!(
                        "model: {} ({})",
                        receipt.model_requested,
                        match receipt.model_override_supported {
                            Some(true) => "pinned through the provider flag",
                            Some(false) => "NOT pinned: provider has no model flag",
                            None => "pin status unreported",
                        }
                    ));
                }
                lines.push("continuity: fresh (skill launchers start a new session)".to_string());
                if !receipt.report.is_empty() {
                    lines.push(format!("report: {}", receipt.report));
                }
                if !receipt.transcript.is_empty() {
                    lines.push(format!("transcript: {}", receipt.transcript));
                }
                let audit = self.audit();
                if !audit.mismatched.is_empty() {
                    lines.push(String::new());
                    lines.push("DECLARATION NOT HONORED:".to_string());
                    lines.extend(audit.mismatched.iter().map(|line| format!("  {line}")));
                }
                if !audit.unverified.is_empty() {
                    lines.push(String::new());
                    lines.push("NOT CONFIRMED BY THE RECEIPT:".to_string());
                    lines.extend(audit.unverified.iter().map(|line| format!("  {line}")));
                }
            }
            Some(receipt) => {
                lines.push(String::new());
                lines.push(format!(
                    "refused before worker start: {}",
                    receipt.refusal_reason()
                ));
                if !receipt.run_id.is_empty() {
                    lines.push(format!("run id reserved: {}", receipt.run_id));
                }
                if let Some(code) = self.exit_code {
                    lines.push(format!("launcher exit code: {code}"));
                }
                let audit = self.audit();
                if !audit.mismatched.is_empty() {
                    lines.push(String::new());
                    lines.push("RECEIPT ENVELOPE NOT HONORED:".to_string());
                    lines.extend(audit.mismatched.iter().map(|line| format!("  {line}")));
                }
            }
            None => {
                lines.push(String::new());
                lines.push(format!(
                    "launcher produced no receipt: {}",
                    self.transport_error
                        .as_deref()
                        .unwrap_or("nothing parseable on stdout")
                ));
                if let Some(code) = self.exit_code {
                    lines.push(format!("launcher exit code: {code}"));
                }
            }
        }
        if !self.stderr.trim().is_empty() {
            lines.push(String::new());
            lines.push("stderr:".to_string());
            lines.extend(self.stderr.lines().map(ToOwned::to_owned));
        }
        lines
    }
}

fn short_sha(sha: &str) -> &str {
    if sha.len() > 12 { &sha[..12] } else { sha }
}

/// Resolve a path for comparison, falling back to the literal when it cannot
/// be resolved. Without this a declared `/tmp/x` and a reported
/// `/private/tmp/x` — the same directory on macOS — read as a mismatch.
fn canonical(path: &Path) -> PathBuf {
    std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum VerifyHalt {
    Drift(Vec<rmcp_mux::ipc::command::NonMuxEntry>),
    Timeout,
}

pub fn pre_launch_verify(client_kind: rmcp_mux::ipc::ClientKind) -> Result<(), VerifyHalt> {
    use std::io::{BufRead, BufReader};
    use std::os::unix::net::UnixStream;
    use std::time::Duration;

    let path = rmcp_mux::ipc::socket_path();
    let stream = match UnixStream::connect(&path) {
        Ok(s) => s,
        Err(_) => return Ok(()),
    };

    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));

    let cmd = rmcp_mux::ipc::MuxControlCommand::Verify { client_kind };
    let Ok(json) = serde_json::to_string(&cmd) else {
        return Ok(());
    };

    let mut writer = &stream;
    if writeln!(writer, "{json}").is_err() {
        return Err(VerifyHalt::Timeout);
    }

    let mut reader = BufReader::new(stream);
    let mut response_line = String::new();
    if reader.read_line(&mut response_line).is_err() || response_line.is_empty() {
        return Err(VerifyHalt::Timeout);
    }

    if let Ok(rmcp_mux::ipc::MuxControlResponse::VerifyResult(res)) =
        serde_json::from_str(&response_line)
        && !res.ok
    {
        return Err(VerifyHalt::Drift(res.non_mux_servers));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(kind: LaunchKind) -> LaunchRequest {
        LaunchRequest {
            kind,
            agent: "codex".to_string(),
            prompt: "Plan and implement.\nSecond line with 'quotes'.".to_string(),
            presentation: Presentation::Headless,
            environment: Environment::LivingTree,
            permissions: PermissionPolicy::Default,
            sandbox: SandboxChoice::Default,
            model: String::new(),
            repo: PathBuf::from("/tmp/repo"),
            count: None,
            depth: None,
            env: BTreeMap::new(),
        }
    }

    fn args(command: &LaunchCommand) -> Vec<String> {
        command
            .args
            .iter()
            .map(|value| value.to_string_lossy().into_owned())
            .collect()
    }

    #[test]
    fn prompt_rides_stdin_and_never_argv() {
        let command = build_launch_command("/usr/bin/vibecrafted", &request(LaunchKind::Workflow));
        let argv = args(&command);
        assert_eq!(
            argv,
            vec![
                "workflow",
                "codex",
                "--prompt-stdin",
                "--runtime",
                "headless",
                "--repo",
                "/tmp/repo",
                "--json"
            ]
        );
        assert_eq!(
            command.stdin.as_deref(),
            Some("Plan and implement.\nSecond line with 'quotes'.")
        );
        assert!(!command.command_line().contains("Plan and implement"));
        assert!(
            command.preview().ends_with("<stdin: prompt, 46 chars>"),
            "preview must state the prompt size without its content: {}",
            command.preview()
        );
    }

    #[test]
    fn empty_prompt_omits_stdin_flag() {
        let mut req = request(LaunchKind::Review);
        req.prompt = "   ".to_string();
        let command = build_launch_command("vibecrafted", &req);
        assert!(!args(&command).iter().any(|arg| arg == "--prompt-stdin"));
        assert!(command.stdin.is_none());
        assert_eq!(command.preview(), command.command_line());
    }

    #[test]
    fn every_declared_choice_reaches_the_launcher_argv() {
        let mut req = request(LaunchKind::Workflow);
        req.agent = "claude".to_string();
        req.presentation = Presentation::Terminal;
        req.environment = Environment::FleetWorktrees;
        req.permissions = PermissionPolicy::ReadOnly;
        req.sandbox = SandboxChoice::On;
        req.model = " claude-fable-5-1 ".to_string();
        let argv = args(&build_launch_command("vibecrafted", &req));
        assert_eq!(
            argv,
            vec![
                "workflow",
                "claude",
                "--prompt-stdin",
                "--runtime",
                "terminal",
                "--repo",
                "/tmp/repo",
                "--worktree",
                "true",
                "--permissions",
                "read-only",
                "--sandbox",
                "true",
                "--model",
                "claude-fable-5-1",
                "--json"
            ]
        );
    }

    #[test]
    fn marbles_carries_loop_controls_and_vm_environments_add_no_flag() {
        let mut req = request(LaunchKind::Marbles);
        req.count = Some(4);
        req.depth = Some(7);
        req.environment = Environment::FleetVmLocal;
        let argv = args(&build_launch_command("vibecrafted", &req));
        assert_eq!(
            &argv[..7],
            &[
                "marbles",
                "codex",
                "--prompt-stdin",
                "--count",
                "4",
                "--depth",
                "7"
            ]
        );
        assert!(!argv.iter().any(|arg| arg == "--worktree"));
        assert!(!argv.iter().any(|arg| arg == "--root"));
    }

    #[test]
    fn skill_without_payload_never_sends_a_prompt() {
        // No shipped skill declares `None` today, so this pins the builder's
        // side of the contract against a synthetic entry rather than pretending
        // the catalog supplies one.
        const SILENT: crate::skills_catalog::SkillEntry = crate::skills_catalog::SkillEntry {
            slug: "vc-silent",
            display: "Silent",
            one_line: "Takes no operator input",
            default_agent: "",
            accepts: crate::skills_catalog::SkillPayloadKind::None,
        };
        let command = build_launch_command("vibecrafted", &request(LaunchKind::Skill(&SILENT)));
        let argv = args(&command);
        assert_eq!(argv[0], "silent");
        assert!(!argv.iter().any(|arg| arg == "--prompt-stdin"));
        assert!(command.stdin.is_none());
    }

    #[test]
    fn optional_payload_skill_carries_a_staged_prompt_and_omits_a_blank_one() {
        let init = crate::skills_catalog::catalog_entry("vc-init").unwrap();
        let mut req = request(LaunchKind::Skill(init));
        let staged = build_launch_command("vibecrafted", &req);
        assert_eq!(args(&staged)[0], "init");
        assert!(args(&staged).iter().any(|arg| arg == "--prompt-stdin"));

        req.prompt = "   ".to_string();
        let blank = build_launch_command("vibecrafted", &req);
        assert!(!args(&blank).iter().any(|arg| arg == "--prompt-stdin"));
        assert!(blank.stdin.is_none());
    }

    #[test]
    fn presentation_and_environment_parse_public_words() {
        assert_eq!(
            "visible".parse::<Presentation>().unwrap(),
            Presentation::Terminal
        );
        assert_eq!(
            "HEADLESS".parse::<Presentation>().unwrap(),
            Presentation::Headless
        );
        assert!("plain".parse::<Presentation>().is_err());
        assert_eq!(
            "local-worktrees".parse::<Environment>().unwrap(),
            Environment::FleetWorktrees
        );
        assert_eq!(
            "cloud".parse::<Environment>().unwrap(),
            Environment::FleetVmCloud
        );
        assert_eq!(Environment::FleetVmCloud.policy_id(), "cloud-soon");
    }

    fn completed(stdout: &[u8], stderr: &[u8]) -> anyhow::Result<LauncherRun> {
        Ok(LauncherRun::Completed(Output {
            status: std::process::ExitStatus::default(),
            stdout: stdout.to_vec(),
            stderr: stderr.to_vec(),
        }))
    }

    #[test]
    fn a_contradicted_declaration_is_admitted_but_never_confirmed() {
        let receipt = br#"{"schema":"vibecrafted.launch_receipt.v1","run_id":"wflw-1","agent":"codex","skill":"workflow","root":"/wt/x","accepted":true,"status":"launching","transport":"headless","worktree":true,"worktree_path":"/wt/x","worktree_branch":"cut/codex-wflw-1","worktree_baseline_sha":"0123456789abcdef","parent_root":"/tmp/repo","model_requested":"m","model_override_supported":false,"model_override_skipped":true,"model_override_skip_reason":"unsupported_agent_model_flag","execution_controls":{"schema":"vibecrafted.execution_controls.v1","provider":"codex","permissions_requested":"","permissions_effective":"bypass","sandbox_requested":"","sandbox_effective":"provider-default","provider_flags":[],"behavior":"b"}}"#;
        let mut req = request(LaunchKind::Workflow);
        req.presentation = Presentation::Terminal;
        req.environment = Environment::FleetWorktrees;
        req.model = "m".to_string();
        let outcome = LaunchOutcome::from_run(
            "preview".to_string(),
            LaunchExpectation::new(&req, None),
            completed(receipt, b""),
        );
        assert_eq!(outcome.admission(), Admission::Admitted);
        assert_eq!(outcome.run_id(), Some("wflw-1"));

        let audit = outcome.audit();
        assert_eq!(audit.confirmation(), Confirmation::Mismatched);
        assert!(
            audit
                .mismatched
                .iter()
                .any(|line| line.contains("transport headless")),
            "{:?}",
            audit.mismatched
        );
        assert!(
            audit
                .mismatched
                .iter()
                .any(|line| line.contains("was not pinned")),
            "{:?}",
            audit.mismatched
        );
        // No catalog cell was supplied, so the effective controls are
        // unverified rather than silently taken as agreement.
        assert!(
            audit
                .unverified
                .iter()
                .any(|line| line.starts_with("permissions effective")),
            "{:?}",
            audit.unverified
        );

        let trail = outcome.trail_line();
        assert!(trail.starts_with("run wflw-1 · workflow codex"));
        assert!(trail.contains("/wt/x"));
        assert!(trail.contains("DECLARATION NOT HONORED"));
        let detail = outcome.detail_lines();
        assert!(
            detail
                .iter()
                .any(|line| line.contains("permissions: bypass (requested provider default)"))
        );
        assert!(detail.iter().any(|line| {
            line.contains("worktree: /wt/x (branch cut/codex-wflw-1, baseline 0123456789ab")
        }));
    }

    #[test]
    fn refused_and_receiptless_answers_are_never_started_runs() {
        let refused = br#"{"schema":"vibecrafted.launch_receipt.v1","accepted":false,"run_id":"","status":"rejected","message":"Failed to launch workflow: agy: --sandbox false cannot be enforced"}"#;
        let req = request(LaunchKind::Workflow);
        let outcome = LaunchOutcome::from_run(
            "p".to_string(),
            LaunchExpectation::new(&req, None),
            completed(refused, b"error: junk\n"),
        );
        assert_eq!(outcome.admission(), Admission::Refused);
        assert!(outcome.trail_line().starts_with("refused · workflow codex"));
        assert!(
            outcome
                .detail_lines()
                .iter()
                .any(|line| line.contains("--sandbox false cannot be enforced"))
        );
        assert!(
            outcome
                .detail_lines()
                .iter()
                .any(|line| line == "error: junk")
        );

        let silent = LaunchOutcome::from_run(
            "p".to_string(),
            LaunchExpectation::new(&req, None),
            completed(b"", b"boom"),
        );
        assert_eq!(silent.admission(), Admission::Unknown);
        assert!(
            silent
                .transport_error
                .as_deref()
                .unwrap()
                .contains("no receipt")
        );
        assert!(silent.trail_line().starts_with("UNKNOWN · "));
    }

    #[test]
    fn an_acceptance_without_a_run_id_is_unknown_not_accepted() {
        let nameless =
            br#"{"schema":"vibecrafted.launch_receipt.v1","accepted":true,"run_id":"   "}"#;
        let req = request(LaunchKind::Workflow);
        let outcome = LaunchOutcome::from_run(
            "p".to_string(),
            LaunchExpectation::new(&req, None),
            completed(nameless, b""),
        );
        assert_eq!(outcome.admission(), Admission::Unknown);
        assert_eq!(outcome.run_id(), None);
        assert!(
            outcome
                .trail_line()
                .contains("acceptance without naming a run")
        );
    }

    #[test]
    fn a_launcher_voc_could_not_start_is_the_only_definite_non_start() {
        let req = request(LaunchKind::Workflow);
        let outcome = LaunchOutcome::from_run(
            "p".to_string(),
            LaunchExpectation::new(&req, None),
            Err(anyhow::anyhow!("failed to start launcher /nope")),
        );
        assert_eq!(outcome.admission(), Admission::Failed);
        assert!(
            outcome
                .trail_line()
                .starts_with("failed before the launcher started · ")
        );
    }

    #[test]
    fn effective_controls_are_judged_against_the_catalog_promise() {
        let downgraded = br#"{"schema":"vibecrafted.launch_receipt.v1","accepted":true,"run_id":"r","agent":"codex","skill":"workflow","root":"/tmp/repo","transport":"headless","execution_controls":{"schema":"vibecrafted.execution_controls.v1","provider":"codex","permissions_requested":"read-only","permissions_effective":"bypass","sandbox_requested":"true","sandbox_effective":"enabled","provider_flags":["--sandbox","read-only"]}}"#;
        let mut req = request(LaunchKind::Workflow);
        req.permissions = PermissionPolicy::ReadOnly;
        req.sandbox = SandboxChoice::On;
        let promised = crate::catalog::ControlCell {
            permissions: "read-only".to_string(),
            sandbox: "true".to_string(),
            supported: true,
            permissions_effective: "read-only".to_string(),
            sandbox_effective: "enabled".to_string(),
            provider_flags: vec!["--sandbox".to_string(), "read-only".to_string()],
            reason: String::new(),
        };
        let outcome = LaunchOutcome::from_run(
            "p".to_string(),
            LaunchExpectation::new(&req, Some(&promised)),
            completed(downgraded, b""),
        );
        let audit = outcome.audit();
        assert_eq!(audit.confirmation(), Confirmation::Mismatched);
        assert!(
            audit.mismatched.iter().any(|line| {
                line.starts_with("permissions effective")
                    && line.contains("promises read-only")
                    && line.contains("applied bypass")
            }),
            "a silent downgrade from the catalog promise must be named: {:?}",
            audit.mismatched
        );
        // The sandbox leg agreed with the promise, so it is not reported.
        assert!(
            !audit
                .mismatched
                .iter()
                .any(|line| line.starts_with("sandbox effective")),
            "{:?}",
            audit.mismatched
        );
    }

    #[test]
    fn a_bounded_wait_leaves_a_silent_launcher_running_and_the_outcome_unknown() {
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "sleep 30"]);
        let run = run_bounded(command, None, Duration::from_millis(200), "silent stub").unwrap();
        let waited = match run {
            LauncherRun::Undecided { waited, .. } => waited,
            LauncherRun::Completed(_) => panic!("a sleeping launcher must not report completion"),
        };
        assert!(waited >= Duration::from_millis(200));

        let req = request(LaunchKind::Workflow);
        let outcome = LaunchOutcome::from_run(
            "p".to_string(),
            LaunchExpectation::new(&req, None),
            Ok(LauncherRun::Undecided {
                waited,
                stdout: Vec::new(),
                stderr: Vec::new(),
            }),
        );
        assert_eq!(outcome.admission(), Admission::Unknown);
        assert_eq!(outcome.timed_out(), Some(waited));
        assert!(
            outcome
                .trail_line()
                .contains("a worker may already be running")
        );
    }
}
