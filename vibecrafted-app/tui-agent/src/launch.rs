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
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::str::FromStr;

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

    /// Run the launcher to completion, feeding the private prompt through
    /// stdin and capturing its receipt. The launcher detaches the worker
    /// itself, so this returns as soon as the launch is accepted or refused.
    pub fn run_capturing(&self) -> anyhow::Result<Output> {
        let mut command = Command::new(&self.program);
        command.args(&self.args);
        command.envs(&self.env);
        command.stdin(if self.stdin.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        });
        command.stdout(Stdio::piped());
        command.stderr(Stdio::piped());
        let mut child = command.spawn().with_context(|| {
            format!(
                "failed to start launcher {}",
                self.program.to_string_lossy()
            )
        })?;
        if let Some(prompt) = self.stdin.as_deref()
            && let Some(mut stdin) = child.stdin.take()
        {
            // A launcher that refuses before reading stdin closes the pipe
            // early; that is a refusal, not a transport failure.
            let _ = stdin.write_all(prompt.as_bytes());
            let _ = stdin.flush();
            drop(stdin);
        }
        child
            .wait_with_output()
            .context("failed to collect launcher output")
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
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LaunchExpectation {
    pub summary: String,
    pub presentation: Presentation,
    pub environment: Environment,
    pub model: String,
    pub repo: PathBuf,
}

impl From<&LaunchRequest> for LaunchExpectation {
    fn from(request: &LaunchRequest) -> Self {
        Self {
            summary: request.summary(),
            presentation: request.presentation,
            environment: request.environment,
            model: request.model.trim().to_string(),
            repo: request.repo.clone(),
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
    /// Set when the launcher exited without a parseable receipt.
    pub transport_error: Option<String>,
}

impl LaunchOutcome {
    pub fn from_output(
        preview: String,
        expectation: LaunchExpectation,
        output: anyhow::Result<Output>,
    ) -> Self {
        match output {
            Ok(output) => {
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
                }
            }
            Err(error) => Self {
                preview,
                expectation,
                exit_code: None,
                receipt: None,
                stderr: String::new(),
                transport_error: Some(format!("{error:#}")),
            },
        }
    }

    /// The launcher accepted the run and named it.
    pub fn accepted(&self) -> bool {
        self.receipt
            .as_ref()
            .is_some_and(|receipt| receipt.accepted && !receipt.run_id.trim().is_empty())
    }

    pub fn run_id(&self) -> Option<&str> {
        self.receipt
            .as_ref()
            .map(|receipt| receipt.run_id.trim())
            .filter(|value| !value.is_empty())
    }

    /// Declared choices the receipt does not confirm. Empty for an honest
    /// launch; any entry means the run exists but not as declared.
    pub fn declaration_mismatches(&self) -> Vec<String> {
        let mut mismatches = Vec::new();
        let Some(receipt) = self.receipt.as_ref() else {
            return mismatches;
        };
        if !receipt.accepted {
            return mismatches;
        }
        let expected_transport = self.expectation.presentation.expected_transport();
        if !receipt.transport.is_empty() && receipt.transport != expected_transport {
            mismatches.push(format!(
                "presentation: declared {} ({expected_transport}) but the launcher used transport {}",
                self.expectation.presentation.label(),
                receipt.transport
            ));
        }
        if self.expectation.environment == Environment::FleetWorktrees
            && (!receipt.worktree || receipt.worktree_path.trim().is_empty())
        {
            mismatches.push(
                "environment: declared Fleet Worktrees but the receipt names no worktree"
                    .to_string(),
            );
        }
        if !self.expectation.model.is_empty() && receipt.model_override_skipped == Some(true) {
            mismatches.push(format!(
                "model: {} was not pinned ({})",
                self.expectation.model,
                if receipt.model_override_skip_reason.is_empty() {
                    "launcher skipped the override"
                } else {
                    receipt.model_override_skip_reason.as_str()
                }
            ));
        }
        mismatches
    }

    /// One trail line: run id, declaration, effective repo.
    pub fn trail_line(&self) -> String {
        match (&self.receipt, self.accepted()) {
            (Some(receipt), true) => {
                let mut line = format!(
                    "run {} · {} · repo {}",
                    receipt.run_id,
                    self.expectation.summary,
                    if receipt.worktree_path.is_empty() {
                        receipt.root.as_str()
                    } else {
                        receipt.worktree_path.as_str()
                    }
                );
                if !receipt.transport.is_empty() {
                    line.push_str(&format!(" · {}", receipt.transport));
                }
                if !self.declaration_mismatches().is_empty() {
                    line.push_str(" · DECLARATION NOT HONORED");
                }
                line
            }
            (Some(receipt), false) => {
                format!(
                    "refused · {} · {}",
                    self.expectation.summary,
                    receipt.refusal_reason()
                )
            }
            (None, _) => format!(
                "failed · {} · {}",
                self.expectation.summary,
                self.transport_error
                    .as_deref()
                    .unwrap_or("launcher produced no receipt")
            ),
        }
    }

    /// Full confirmation / failure detail for the operator overlay.
    pub fn detail_lines(&self) -> Vec<String> {
        let mut lines = vec![format!("declared: {}", self.expectation.summary)];
        lines.push(format!("command: {}", self.preview));
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
                let mismatches = self.declaration_mismatches();
                if !mismatches.is_empty() {
                    lines.push(String::new());
                    lines.push("DECLARATION NOT HONORED:".to_string());
                    lines.extend(mismatches.into_iter().map(|line| format!("  {line}")));
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
            }
            None => {
                lines.push(String::new());
                lines.push(format!(
                    "launcher failed: {}",
                    self.transport_error
                        .as_deref()
                        .unwrap_or("no receipt on stdout")
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

    #[test]
    fn accepted_receipt_is_the_proof_and_mismatches_are_named() {
        let receipt = br#"{"schema":"vibecrafted.launch_receipt.v1","run_id":"wflw-1","agent":"claude","skill":"workflow","root":"/tmp/repo","accepted":true,"status":"launching","transport":"headless","worktree":true,"worktree_path":"/wt/x","worktree_branch":"cut/claude-wflw-1","worktree_baseline_sha":"0123456789abcdef","parent_root":"/tmp/repo","model_requested":"m","model_override_supported":false,"model_override_skipped":true,"model_override_skip_reason":"unsupported_agent_model_flag","execution_controls":{"permissions_requested":"","permissions_effective":"bypass","sandbox_requested":"","sandbox_effective":"provider-default","provider_flags":[],"behavior":"b"}}"#;
        let mut req = request(LaunchKind::Workflow);
        req.presentation = Presentation::Terminal;
        req.environment = Environment::FleetWorktrees;
        req.model = "m".to_string();
        let outcome = LaunchOutcome::from_output(
            "preview".to_string(),
            LaunchExpectation::from(&req),
            Ok(Output {
                status: std::process::ExitStatus::default(),
                stdout: receipt.to_vec(),
                stderr: Vec::new(),
            }),
        );
        assert!(outcome.accepted());
        assert_eq!(outcome.run_id(), Some("wflw-1"));
        let mismatches = outcome.declaration_mismatches();
        assert_eq!(mismatches.len(), 2, "{mismatches:?}");
        assert!(mismatches[0].contains("transport headless"));
        assert!(mismatches[1].contains("was not pinned"));
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
            line.contains("worktree: /wt/x (branch cut/claude-wflw-1, baseline 0123456789ab")
        }));
    }

    #[test]
    fn refused_receipt_and_missing_receipt_are_failures_not_pids() {
        let refused = br#"{"accepted":false,"run_id":"","status":"rejected","message":"Failed to launch workflow: agy: --sandbox false cannot be enforced"}"#;
        let outcome = LaunchOutcome::from_output(
            "p".to_string(),
            LaunchExpectation::from(&request(LaunchKind::Workflow)),
            Ok(Output {
                status: std::process::ExitStatus::default(),
                stdout: refused.to_vec(),
                stderr: b"error: junk\n".to_vec(),
            }),
        );
        assert!(!outcome.accepted());
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

        let silent = LaunchOutcome::from_output(
            "p".to_string(),
            LaunchExpectation::from(&request(LaunchKind::Workflow)),
            Ok(Output {
                status: std::process::ExitStatus::default(),
                stdout: Vec::new(),
                stderr: b"boom".to_vec(),
            }),
        );
        assert!(!silent.accepted());
        assert!(
            silent
                .transport_error
                .as_deref()
                .unwrap()
                .contains("no receipt")
        );
        assert!(silent.trail_line().starts_with("failed · "));
    }
}
