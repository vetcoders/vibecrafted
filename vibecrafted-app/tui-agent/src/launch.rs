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
use std::collections::{BTreeMap, VecDeque};
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

/// The family every launch receipt belongs to, whichever version the launcher
/// stamps. Recognizing the envelope is the parser's job; judging which version
/// arrived is the audit's, so the parser reads only the family and leaves the
/// verdict on the version to the audit that reports it.
const LAUNCH_RECEIPT_SCHEMA_FAMILY: &str = "vibecrafted.launch_receipt.";

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

/// How much of each stream VOC keeps from the front: enough to hold a
/// launcher's opening diagnostics whole.
pub const DIAGNOSTIC_HEAD_CAP: usize = 256 * 1024;

/// How much VOC keeps from the end. The receipt is the last object the
/// launcher prints, so the tail is what can still prove a run exists after a
/// noisy launcher has outrun the head.
pub const DIAGNOSTIC_TAIL_CAP: usize = 64 * 1024;

/// How much of one top-level value VOC is willing to hold while deciding
/// whether it is the receipt. A launch receipt is a small object; anything
/// larger is a diagnostic, and it is stepped over structurally rather than
/// held. This is what keeps a single value — however long — from costing
/// memory in proportion to its size.
pub const RECEIPT_FRAME_CAP: usize = 128 * 1024;

/// How deeply VOC will follow a value's structure while still being able to
/// prove where it ends. The reader remembers which opener each closer has to
/// match, and that memory is bounded: a launcher must not be able to choose
/// how much of it to spend. The bound is `serde_json`'s own recursion limit,
/// so nothing is refused here that could have been read as a receipt anyway.
pub const RECEIPT_DEPTH_CAP: usize = 128;

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
///
/// Every variant carries `answer` beside its streams. The streams are what
/// VOC will show — bounded, and elided in the middle when a launcher outruns
/// that budget. The answer is what the reader proved while the stream was
/// still whole. Keeping them apart is what stops a display budget from
/// deciding whether a run was admitted.
#[derive(Debug)]
pub enum LauncherRun {
    /// The launcher exited on its own; the streams below are what it wrote.
    Completed {
        output: Output,
        answer: StdoutAnswer,
    },
    /// The deadline passed with the launcher still running. Nothing was
    /// killed and nothing is retried: a worker may already exist, so the
    /// outcome is unknown rather than failed.
    Undecided {
        waited: Duration,
        stdout: Vec<u8>,
        stderr: Vec<u8>,
        answer: StdoutAnswer,
    },
    /// The launcher was spawned, but VOC then lost the ability to observe
    /// it: waiting on the child failed. The spawn already succeeded, so a
    /// worker may exist — this is an unknown outcome, never a failed start.
    Unobservable {
        error: String,
        stdout: Vec<u8>,
        stderr: Vec<u8>,
        answer: StdoutAnswer,
    },
}

/// Where the reader stands in the launcher's stdout, byte by byte.
///
/// The states are exhaustive over a stream: either nothing is open, or a
/// line of chatter is being passed over, or a top-level value is being read
/// — or the reader no longer knows which of those is true, which is a
/// position in the output like any other and the only honest name for it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
enum Scan {
    /// Outside every value. The next byte that is not blank either opens one
    /// or begins a line the launcher wrote as prose.
    #[default]
    Between,
    /// Inside a line that did not begin a value. Every byte up to the next
    /// newline is text, whatever it looks like.
    Chatter,
    /// Inside a top-level value, with every opener still waiting for the
    /// closer that matches it.
    Frame,
    /// The stream stopped proving where its values end, and the reader has
    /// no place in it any more. Not an error state: a stream that says
    /// something VOC cannot follow is still a stream, and what it printed
    /// before it stopped making sense is still what it printed.
    Lost,
}

/// Reads the launcher's answer out of stdout *as it arrives*, while the
/// stream is still whole.
///
/// This is the one place that knows what is nested inside what, and it knows
/// it for the only reason that constitutes knowledge here: it saw every byte
/// the launcher wrote, in order, before anything was dropped to fit a display
/// budget. A value is top-level because the reader stood outside every other
/// value when it began reading that one — not because of where it sat on a
/// line, what followed it, or what survived into the text an operator sees.
///
/// The distinction matters because the display text has a hole in it. Once
/// the middle of a long stream is elided, a frame opened before the gap and
/// a frame opened after it are indistinguishable in what remains; a nested
/// object at the tail reads exactly like a top-level one. Deciding here, at
/// the point of arrival, is what removes that ambiguity instead of resolving
/// it by assumption in either direction — neither inventing an admission
/// from a fragment, nor discarding an answer that legitimately came last.
///
/// Nothing about this state can be reached by anything the launcher prints.
/// The gap notice is written by the display projection and never returns
/// here, so a launcher echoing that notice is a launcher printing text.
///
/// Cost is fixed and the work is linear: each byte is examined once, chatter
/// and oversized values are counted rather than kept, at most
/// `RECEIPT_FRAME_CAP` bytes of one candidate value are ever held, and the
/// openers still waiting to be matched are capped at `RECEIPT_DEPTH_CAP`.
/// None of those bounds is a number the launcher gets to choose.
#[derive(Default)]
struct ReceiptScanner {
    scan: Scan,
    /// The closer each open value is still waiting for, innermost last.
    /// A count would say `{]` ended something; only the openers themselves
    /// know that it did not. Bounded by `RECEIPT_DEPTH_CAP`.
    open: Vec<u8>,
    in_string: bool,
    escaped: bool,
    /// The value being read, while it is still small enough to be a receipt.
    frame: Vec<u8>,
    /// The value outgrew what VOC will hold. Its boundary is still counted —
    /// losing the bytes must not mean losing the structure.
    oversized: bool,
    /// The last complete top-level value that stated a verdict under this
    /// contract. Later output can add an answer; it cannot retract one.
    receipt: Option<LaunchReceipt>,
    /// Complete top-level values that were valid JSON, whatever they said.
    readable: usize,
    /// Why some value could not be read, kept for the operator when no
    /// answer was found at all. The first reason is the informative one.
    breakage: Option<String>,
    /// Whether the launcher wrote anything but whitespace.
    spoke: bool,
    /// Why the reader stopped being able to prove where values end. Set
    /// exactly when `scan` is `Scan::Lost`, and never cleared: the stream
    /// does not resume at a boundary VOC chose for it.
    loss: Option<String>,
}

/// The closer that ends a value opened by `opener`.
fn closing_delimiter(opener: u8) -> u8 {
    if opener == b'{' { b'}' } else { b']' }
}

impl ReceiptScanner {
    fn push(&mut self, bytes: &[u8]) {
        for &byte in bytes {
            match self.scan {
                Scan::Between => {
                    if byte.is_ascii_whitespace() {
                        continue;
                    }
                    self.spoke = true;
                    if byte == b'{' || byte == b'[' {
                        self.scan = Scan::Frame;
                        self.open.clear();
                        self.open.push(closing_delimiter(byte));
                        self.in_string = false;
                        self.escaped = false;
                        self.oversized = false;
                        self.frame.clear();
                        self.frame.push(byte);
                    } else {
                        self.scan = Scan::Chatter;
                    }
                }
                Scan::Chatter => {
                    self.spoke = true;
                    if byte == b'\n' {
                        self.scan = Scan::Between;
                    }
                }
                Scan::Frame => self.step_through_frame(byte),
                // Past this point every byte is at a depth nobody knows.
                // Reading them would only produce a verdict about bytes.
                Scan::Lost => return,
            }
        }
    }

    /// One byte inside a top-level value: keep it if the value can still be
    /// a receipt, and count what it does to the structure either way.
    fn step_through_frame(&mut self, byte: u8) {
        if self.oversized {
            // Nothing is kept, but the boundary is still counted: a value
            // too large to be an answer must not take the reader's place in
            // the stream with it.
        } else if self.frame.len() >= RECEIPT_FRAME_CAP {
            self.oversized = true;
            self.frame = Vec::new();
        } else {
            self.frame.push(byte);
        }

        if self.escaped {
            self.escaped = false;
            return;
        }
        if self.in_string {
            match byte {
                b'\\' => self.escaped = true,
                b'"' => self.in_string = false,
                _ => {}
            }
            return;
        }
        match byte {
            b'"' => self.in_string = true,
            b'{' | b'[' => {
                // Following this opener would mean remembering it, and how
                // many there are is the launcher's choice, not VOC's. The
                // bound is where the reader stops following rather than
                // where it starts guessing.
                if self.open.len() >= RECEIPT_DEPTH_CAP {
                    self.lose_the_thread("a value nested deeper than VOC follows");
                    return;
                }
                self.open.push(closing_delimiter(byte));
            }
            b'}' | b']' => match self.open.pop() {
                Some(expected) if expected == byte => {
                    if self.open.is_empty() {
                        self.close_frame();
                    }
                }
                // The delimiters disagree, so this byte ended nothing. What
                // it takes with it is the reader's place in the stream: from
                // here, no object can be shown to be top-level, and one that
                // merely looks it is the whole defect this guards against.
                _ => self.lose_the_thread("a value closed by a delimiter that never opened it"),
            },
            _ => {}
        }
    }

    /// Structure stopped proving where values end.
    ///
    /// The reader admits nothing further: an object printed after this is at
    /// a depth no one knows, and being the last thing printed does not make
    /// it top-level. An answer already proved stands — losing sight of the
    /// stream is not the launcher retracting what it said. There is no
    /// resynchronisation, because a newline is legal inside a value and
    /// anything else VOC picked would be a boundary it invented.
    fn lose_the_thread(&mut self, why: &str) {
        self.scan = Scan::Lost;
        self.frame = Vec::new();
        self.open = Vec::new();
        self.oversized = false;
        self.in_string = false;
        self.escaped = false;
        self.loss = Some(why.to_string());
    }

    /// A top-level value just ended. Whether it is the launcher's answer is
    /// now a question about its contents alone.
    fn close_frame(&mut self) {
        self.scan = Scan::Between;
        if !self.oversized {
            self.judge_frame();
        }
        self.frame = Vec::new();
        self.oversized = false;
    }

    fn judge_frame(&mut self) {
        match serde_json::from_slice::<LaunchEnvelope>(&self.frame) {
            Ok(envelope) => {
                self.readable += 1;
                if envelope.names_a_launch() {
                    match serde_json::from_slice::<LaunchReceipt>(&self.frame) {
                        Ok(receipt) => self.receipt = Some(receipt),
                        Err(error) => {
                            self.breakage.get_or_insert_with(|| error.to_string());
                        }
                    }
                }
            }
            // Balanced delimiters are not a promise of valid JSON. What the
            // reader proved is where the value ended, and that is enough to
            // carry on reading past it.
            Err(error) => {
                self.breakage.get_or_insert_with(|| error.to_string());
            }
        }
    }

    /// The launcher's answer, or why the stream does not contain one.
    fn answer(&self) -> StdoutAnswer {
        if let Some(receipt) = &self.receipt {
            return Ok(receipt.clone());
        }
        if !self.spoke {
            return Err("launcher printed no receipt".to_string());
        }
        // Nothing printed after the loss can be shown to be an answer, so
        // the operator gets the reason the reading stopped instead of a
        // verdict assembled out of bytes at an unknown depth.
        if let Some(why) = &self.loss {
            return Err(format!(
                "launch receipt cannot be read from this stream: {why}"
            ));
        }
        let unread = self.breakage.clone().or_else(|| match self.scan {
            Scan::Frame => {
                Some("the launcher's output ended inside a value it never closed".to_string())
            }
            _ => None,
        });
        match unread {
            // Nothing on stdout ever read as a value, so what the operator
            // needs to see is the launcher's own breakage.
            Some(why) if self.readable == 0 => {
                Err(format!("launch receipt is not valid JSON: {why}"))
            }
            _ => Err("launcher printed diagnostics but no receipt".to_string()),
        }
    }
}

/// The launcher's answer as proved by the reader that saw every byte of its
/// stdout, or the reason no answer could be proved from it.
///
/// Carried alongside the display text rather than derived from it: the text
/// is a bounded projection with a hole in the middle, and a verdict read back
/// out of a projection is a verdict about the projection.
pub type StdoutAnswer = Result<LaunchReceipt, String>;

/// One stream's retained bytes: a head, a tail, and an honest count of what
/// fell between them.
///
/// Draining and retaining are separate duties. The reader below never stops
/// draining — a launcher that may already have admitted a run must not be
/// stopped by a full pipe — but what VOC *keeps* costs a fixed amount of
/// memory however long that launcher talks. The head holds the opening
/// diagnostics; the tail holds the receipt, which the launcher prints last.
#[derive(Default)]
struct BoundedCapture {
    head: Vec<u8>,
    tail: VecDeque<u8>,
    dropped: u64,
    /// What the stream meant, kept apart from what will be shown of it.
    /// Retaining bytes and reading structure are answers to different
    /// questions, and only one of them can be answered later.
    answer: ReceiptScanner,
}

impl BoundedCapture {
    fn push(&mut self, bytes: &[u8]) {
        // Structure is read first, from the original stream. Everything
        // below decides what an operator will be shown; this line decides
        // what VOC knows, and it cannot be done after the fact.
        self.answer.push(bytes);
        let mut rest = bytes;
        if self.head.len() < DIAGNOSTIC_HEAD_CAP {
            let take = (DIAGNOSTIC_HEAD_CAP - self.head.len()).min(rest.len());
            self.head.extend_from_slice(&rest[..take]);
            rest = &rest[take..];
        }
        if rest.is_empty() {
            return;
        }
        // A single write larger than the tail replaces it outright, so no
        // amount of output is ever held line by line or frame by frame.
        if rest.len() >= DIAGNOSTIC_TAIL_CAP {
            self.dropped += (self.tail.len() + rest.len() - DIAGNOSTIC_TAIL_CAP) as u64;
            self.tail.clear();
            self.tail.extend(&rest[rest.len() - DIAGNOSTIC_TAIL_CAP..]);
            return;
        }
        self.tail.extend(rest);
        while self.tail.len() > DIAGNOSTIC_TAIL_CAP {
            self.tail.pop_front();
            self.dropped += 1;
        }
    }

    /// The launcher's answer, as read from the whole stream rather than from
    /// the bounded text below.
    fn answer(&self) -> StdoutAnswer {
        self.answer.answer()
    }

    /// The retained bytes as one stream. Nothing is elided silently: the gap
    /// is named in place, in VOC's own words and counts — never in bytes
    /// carried over from the launcher.
    fn snapshot(&self) -> Vec<u8> {
        let mut out = Vec::with_capacity(self.head.len() + self.tail.len() + 96);
        out.extend_from_slice(&self.head);
        if self.dropped > 0 {
            out.extend_from_slice(
                format!(
                    "\n… [VOC kept the first {DIAGNOSTIC_HEAD_CAP} and last {DIAGNOSTIC_TAIL_CAP} bytes; {} elided] …\n",
                    self.dropped
                )
                .as_bytes(),
            );
        }
        out.extend(self.tail.iter().copied());
        out
    }
}

/// Drain one child stream on its own thread, retaining a bounded window of it
/// and marking itself done in `finished`. Incremental by design: the caller
/// can snapshot whatever arrived even when the reader never reaches EOF.
fn pump<R: Read + Send + 'static>(
    source: Option<R>,
    finished: &Arc<AtomicU8>,
) -> Arc<Mutex<BoundedCapture>> {
    let buffer = Arc::new(Mutex::new(BoundedCapture::default()));
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
                        guard.push(&chunk[..read]);
                    }
                }
            }
        }
        finished.fetch_add(1, Ordering::SeqCst);
    });
    buffer
}

/// The signal that ended a child, when one did. `ExitStatus::code()` is
/// `None` for a signalled child, and that silence must never be mistaken for
/// a launcher that never ran.
#[cfg(unix)]
fn termination_signal(status: &std::process::ExitStatus) -> Option<i32> {
    use std::os::unix::process::ExitStatusExt;
    status.signal()
}

#[cfg(not(unix))]
fn termination_signal(_status: &std::process::ExitStatus) -> Option<i32> {
    None
}

fn snapshot(buffer: &Arc<Mutex<BoundedCapture>>) -> Vec<u8> {
    buffer
        .lock()
        .map(|guard| guard.snapshot())
        .unwrap_or_default()
}

/// The stdout capture's two projections, taken together: the bounded text an
/// operator reads, and the answer the reader proved from the whole stream.
///
/// Both come from one lock so they describe the same moment. A reader lost to
/// a panicking thread is a gap in VOC's sight, never a launcher that answered
/// nothing.
fn stdout_view(buffer: &Arc<Mutex<BoundedCapture>>) -> (Vec<u8>, StdoutAnswer) {
    match buffer.lock() {
        Ok(guard) => (guard.snapshot(), guard.answer()),
        Err(_) => (
            Vec::new(),
            Err("VOC lost the reader holding the launcher's stdout".to_string()),
        ),
    }
}

/// Spawn `command` with both output streams drained concurrently and the
/// private stdin payload written on its own thread, then wait at most
/// `deadline` for it to exit.
///
/// Returns `Err` for one outcome only: a child that could never be spawned.
/// That is the single fact which proves no worker exists. Everything after a
/// successful spawn — an exit, a signal, a deadline, even a lost wait — comes
/// back as `Ok`, because by then a run may already have been admitted.
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
                let (text, answer) = stdout_view(&stdout);
                return Ok(LauncherRun::Completed {
                    output: Output {
                        status,
                        stdout: text,
                        stderr: snapshot(&stderr),
                    },
                    answer,
                });
            }
            Ok(None) => {}
            // The spawn already succeeded, so this is not a failure to
            // start: VOC has merely gone blind to a launcher that may
            // already have admitted a run.
            Err(error) => {
                let (text, answer) = stdout_view(&stdout);
                return Ok(LauncherRun::Unobservable {
                    error: format!("failed to wait for {what}: {error}"),
                    stdout: text,
                    stderr: snapshot(&stderr),
                    answer,
                });
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
            let (text, answer) = stdout_view(&stdout);
            return Ok(LauncherRun::Undecided {
                waited,
                stdout: text,
                stderr: snapshot(&stderr),
                answer,
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

/// The two facts that decide whether a complete top-level object on the
/// launcher's stdout is the launch receipt: the verdict it states about a
/// launch, and the contract it claims to speak.
///
/// The canonical launcher stamps both on every answer it gives — schema and
/// verdict alike are written unconditionally by
/// `workflow.machine_launch_receipt` — so an object carrying neither, or
/// carrying a verdict under no contract at all, is something else it
/// printed: a progress line, a drain notice, a cache diagnostic. Reading
/// such an object as the receipt turns an admitted run into a refusal the
/// launcher never gave, which is why an unstamped object is not this
/// contract however plainly it says `accepted`.
#[derive(Debug, Deserialize)]
struct LaunchEnvelope {
    #[serde(default)]
    accepted: Option<bool>,
    #[serde(default)]
    schema: Option<String>,
}

impl LaunchEnvelope {
    /// Whether this object states a verdict about a launch of this contract.
    ///
    /// Deliberately generous about *which* version: a receipt stamped with
    /// another one is still a receipt, and refusing it is the audit's job.
    /// Deliberately strict about everything else: the stamp has to be there,
    /// it has to name this family, and the verdict has to be stated.
    fn names_a_launch(&self) -> bool {
        self.accepted.is_some()
            && self
                .schema
                .as_deref()
                .map(str::trim)
                .is_some_and(|schema| schema.starts_with(LAUNCH_RECEIPT_SCHEMA_FAMILY))
    }
}

impl LaunchReceipt {
    /// Read the launcher's answer out of a finished stream of stdout.
    ///
    /// This is the same reader the stream pump runs while a launcher is still
    /// talking, handed every byte at once: `ReceiptScanner` decides what is
    /// top-level by standing outside every value before it begins reading
    /// one, so an object nested in a diagnostic is never a candidate and a
    /// value that never closed never yields one.
    ///
    /// Use it where the whole stream is in hand. Where it is not — a live
    /// launcher whose output must also be bounded for display — read the
    /// answer from the capture instead, which is holding this same reader
    /// open across the gap it will later elide.
    pub fn parse(stdout: &[u8]) -> anyhow::Result<Self> {
        let mut reader = ReceiptScanner::default();
        reader.push(stdout);
        reader.answer().map_err(|why| anyhow::anyhow!(why))
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
    /// Whether VOC actually handed the declaration to a launcher process.
    ///
    /// Kept apart from `exit_code` on purpose. A signalled child reports no
    /// exit code at all, and a lost wait reports nothing either; neither says
    /// anything about whether the launcher started. Only a spawn that never
    /// happened proves no worker exists.
    pub launcher_started: bool,
    /// The signal that ended the launcher, when one did.
    pub termination_signal: Option<i32>,
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
            Ok(LauncherRun::Completed { output, answer }) => {
                let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
                let (receipt, transport_error) = match answer {
                    Ok(receipt) => (Some(receipt), None),
                    Err(why) => (None, Some(why)),
                };
                Self {
                    preview,
                    expectation,
                    exit_code: output.status.code(),
                    launcher_started: true,
                    termination_signal: termination_signal(&output.status),
                    receipt,
                    stderr,
                    transport_error,
                    undecided_after: None,
                }
            }
            Ok(LauncherRun::Undecided {
                waited,
                stdout: _,
                stderr,
                answer,
            }) => Self {
                preview,
                expectation,
                exit_code: None,
                // Partial stdout can already carry the receipt: the launcher
                // may admit a run and keep running. Failing to parse one here
                // is the absence of an answer, not a transport failure.
                launcher_started: true,
                termination_signal: None,
                receipt: answer.ok(),
                stderr: String::from_utf8_lossy(&stderr).into_owned(),
                transport_error: None,
                undecided_after: Some(waited),
            },
            // The spawn succeeded and the streams are what the launcher did
            // say, so a partial receipt still counts. Losing the wait is a
            // gap in VOC's sight, not proof of an absent launcher.
            Ok(LauncherRun::Unobservable {
                error,
                stdout: _,
                stderr,
                answer,
            }) => Self {
                preview,
                expectation,
                exit_code: None,
                launcher_started: true,
                termination_signal: None,
                receipt: answer.ok(),
                stderr: String::from_utf8_lossy(&stderr).into_owned(),
                transport_error: Some(error),
                undecided_after: None,
            },
            Err(error) => Self {
                preview,
                expectation,
                exit_code: None,
                launcher_started: false,
                termination_signal: None,
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
            // Nothing was ever handed over, so nothing can have started.
            // This is the only evidence that proves an empty outcome.
            None if !self.launcher_started => Admission::Failed,
            // The launcher ran but said nothing VOC can read. It may have
            // mutated the control plane before dying, before the wait ended,
            // or before VOC lost sight of it — unknown, not empty. A missing
            // exit code is how a signalled child ends, not how a spawn fails.
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

        match (self.exit_code, self.termination_signal) {
            (Some(0), _) => {}
            (Some(code), _) => audit.mismatched.push(format!(
                "launcher exit: reported acceptance and then exited {code}"
            )),
            // A signalled launcher did exit — it was killed. Reporting that
            // as "not observed" would bury a termination the operator needs.
            (None, Some(signal)) => audit.mismatched.push(format!(
                "launcher exit: reported acceptance and was then terminated by signal {signal}"
            )),
            (None, None) => audit.unverified.push(
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
        if let Some(signal) = self.termination_signal {
            return format!(
                "the launcher was terminated by signal {signal} before it printed a receipt"
            );
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
                } else if let Some(signal) = self.termination_signal {
                    // A signalled launcher has no exit code to show. Saying
                    // nothing here would leave the operator reading silence
                    // where a kill belongs.
                    lines.push(format!("launcher terminated by signal: {signal}"));
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

    /// A finished launcher, presented the way the transport presents one:
    /// the bytes it wrote, and the answer the reader proved from them.
    fn completed(stdout: &[u8], stderr: &[u8]) -> anyhow::Result<LauncherRun> {
        let mut capture = BoundedCapture::default();
        capture.push(stdout);
        Ok(LauncherRun::Completed {
            output: Output {
                status: std::process::ExitStatus::default(),
                stdout: capture.snapshot(),
                stderr: stderr.to_vec(),
            },
            answer: capture.answer(),
        })
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
    fn a_drained_stream_retains_a_bounded_head_and_tail() {
        let finished = Arc::new(AtomicU8::new(0));
        // Far more than VOC keeps, ending in the bytes a receipt would
        // occupy. The reader runs to EOF: draining is not what is bounded.
        let mut source = vec![b'n'; 4 * 1024 * 1024];
        source.extend_from_slice(b"TAIL-MARKER\n");
        let buffer = pump(Some(std::io::Cursor::new(source)), &finished);

        let deadline = Instant::now();
        while finished.load(Ordering::SeqCst) < 1 && deadline.elapsed() < Duration::from_secs(5) {
            thread::sleep(CHILD_POLL_INTERVAL);
        }
        assert_eq!(
            finished.load(Ordering::SeqCst),
            1,
            "the reader must reach EOF"
        );

        let kept = snapshot(&buffer);
        assert!(
            kept.len() <= DIAGNOSTIC_HEAD_CAP + DIAGNOSTIC_TAIL_CAP + 128,
            "a stream that outruns the caps must still cost a fixed amount: {} bytes",
            kept.len()
        );
        assert!(
            kept.ends_with(b"TAIL-MARKER\n"),
            "the end of the stream is where the receipt lives and must survive"
        );
        assert!(
            kept.starts_with(&[b'n'; 64][..]),
            "the launcher's opening diagnostics must be kept"
        );
        assert!(
            String::from_utf8_lossy(&kept).contains("elided"),
            "the gap must be named rather than dropped silently"
        );
    }

    #[test]
    fn a_single_write_larger_than_the_tail_is_never_held_whole() {
        let mut capture = BoundedCapture::default();
        // One write bigger than everything VOC keeps — the shape a frame- or
        // line-buffered reader would hold entirely before trimming.
        let mut burst = vec![b'x'; DIAGNOSTIC_HEAD_CAP + 4 * DIAGNOSTIC_TAIL_CAP];
        burst.extend_from_slice(b"END");
        capture.push(&burst);

        let kept = capture.snapshot();
        assert!(
            kept.len() <= DIAGNOSTIC_HEAD_CAP + DIAGNOSTIC_TAIL_CAP + 128,
            "{} bytes retained from a single oversized write",
            kept.len()
        );
        assert!(kept.ends_with(b"END"), "the end of the write must survive");
        assert_eq!(
            capture.dropped,
            (burst.len() - DIAGNOSTIC_HEAD_CAP - DIAGNOSTIC_TAIL_CAP) as u64,
            "the elided count must be the truth, not an estimate"
        );
    }

    #[test]
    fn a_receipt_is_read_from_the_tail_behind_json_shaped_noise() {
        let mut stdout = Vec::new();
        for index in 0..2000 {
            stdout.extend_from_slice(format!("{{\"log\":\"noise\",\"i\":{index}}}\n").as_bytes());
        }
        stdout.extend_from_slice(br#"{"schema":"vibecrafted.launch_receipt.v1","accepted":true,"run_id":"work-tail","status":"launching","execution_controls":{"schema":"vibecrafted.execution_controls.v1","provider":"claude","provider_flags":[]}}"#);
        stdout.extend_from_slice(b"\nthe launcher keeps talking after the receipt\n");

        let receipt = LaunchReceipt::parse(&stdout).expect("the trailing receipt must be found");
        assert_eq!(receipt.run_id, "work-tail");
        assert!(receipt.accepted);
        // The nested controls block carries a schema of its own and must
        // never be mistaken for the receipt around it.
        assert_eq!(receipt.schema, LAUNCH_RECEIPT_SCHEMA);
    }

    #[test]
    fn a_receipt_survives_a_diagnostic_printed_after_it() {
        // The launcher answers, then keeps talking. What it says afterwards
        // is about the run, not a verdict on it.
        let bytes = b"{\"schema\":\"vibecrafted.launch_receipt.v1\",\"accepted\":true,\"run_id\":\"work-real\",\"status\":\"launching\"}\n{\"status\":\"draining\"}\n";
        let receipt = LaunchReceipt::parse(bytes).expect("the receipt must outlive the noise");
        assert!(receipt.accepted);
        assert_eq!(receipt.run_id, "work-real");
    }

    #[test]
    fn a_diagnostic_nested_in_a_pretty_printed_receipt_is_never_the_receipt() {
        // Pretty-printing puts a nested object at the start of its own line.
        // Nesting is structure, not indentation.
        let bytes = br#"{
  "schema": "vibecrafted.launch_receipt.v1",
  "accepted": true,
  "run_id": "work-real",
  "diagnostics": [
    {"run_id": "diagnostic-only"}
  ]
}"#;
        let receipt = LaunchReceipt::parse(bytes).expect("the enclosing receipt must be read");
        assert!(receipt.accepted);
        assert_eq!(receipt.run_id, "work-real");
    }

    #[test]
    fn a_verdict_nested_inside_the_receipt_is_still_not_the_receipt() {
        // Even an object that states a verdict of its own is not the answer
        // when it is a member of the object that carries the real one.
        let bytes = br#"{
  "schema": "vibecrafted.launch_receipt.v1",
  "accepted": true,
  "run_id": "work-real",
  "diagnostics": [
    {"accepted": false, "run_id": "rejected-sibling"}
  ]
}"#;
        let receipt = LaunchReceipt::parse(bytes).expect("the enclosing receipt must be read");
        assert!(receipt.accepted);
        assert_eq!(receipt.run_id, "work-real");
    }

    #[test]
    fn an_unstamped_verdict_never_replaces_the_stamped_receipt() {
        // The canonical launcher stamps its schema on every answer it gives,
        // so a bare verdict printed afterwards belongs to some other
        // subsystem — a cache, a queue — and says nothing about this launch.
        let bytes = b"{\"schema\":\"vibecrafted.launch_receipt.v1\",\"accepted\":true,\"run_id\":\"work-real\",\"status\":\"launching\"}\n{\"accepted\":false,\"message\":\"cache entry rejected\"}\n";
        let receipt = LaunchReceipt::parse(bytes).expect("the stamped answer must be kept");
        assert!(receipt.accepted);
        assert_eq!(receipt.run_id, "work-real");

        // Alone, the same lines answer nothing. The launcher started a child
        // and VOC cannot read a verdict on it: unknown, never refused, and
        // never an acceptance either.
        let req = request(LaunchKind::Workflow);
        for unstamped in [
            &b"{\"accepted\":false,\"message\":\"cache entry rejected\"}"[..],
            &b"{\"schema\":\"\",\"accepted\":false,\"message\":\"cache entry rejected\"}"[..],
            &b"{\"schema\":\"   \",\"accepted\":true,\"run_id\":\"invented\"}"[..],
        ] {
            assert!(
                LaunchReceipt::parse(unstamped).is_err(),
                "an unstamped verdict is not this contract: {}",
                String::from_utf8_lossy(unstamped)
            );
            let outcome = LaunchOutcome::from_run(
                "p".to_string(),
                LaunchExpectation::new(&req, None),
                completed(unstamped, b""),
            );
            assert_eq!(
                outcome.admission(),
                Admission::Unknown,
                "an unstamped verdict decides nothing: {}",
                String::from_utf8_lossy(unstamped)
            );
            assert_eq!(outcome.run_id(), None);
        }
    }

    #[test]
    fn a_finished_object_inside_an_unfinished_one_is_never_the_answer() {
        // Output snapshotted while the launcher was still printing: neither
        // the object nor the array holding this member ever closed. The
        // member is complete, carries this contract's stamp, and states a
        // verdict — and it is still a fragment of an answer, not one.
        let req = request(LaunchKind::Workflow);
        for verdict in ["false", "true"] {
            let truncated = format!(
                "{{\n  \"diagnostics\": [\n    {{\"schema\":\"vibecrafted.launch_receipt.v1\",\"accepted\":{verdict},\"run_id\":\"nested-only\"}}"
            );
            assert!(
                LaunchReceipt::parse(truncated.as_bytes()).is_err(),
                "a member of an unfinished object is not the answer: {truncated}"
            );
            let outcome = LaunchOutcome::from_run(
                "p".to_string(),
                LaunchExpectation::new(&req, None),
                completed(truncated.as_bytes(), b""),
            );
            assert_eq!(
                outcome.admission(),
                Admission::Unknown,
                "an unfinished frame decides nothing: {truncated}"
            );
            assert_eq!(outcome.run_id(), None);

            // The same fragment printed after a finished answer takes
            // nothing away from it.
            let mut after = Vec::from(
                &b"{\"schema\":\"vibecrafted.launch_receipt.v1\",\"accepted\":true,\"run_id\":\"work-real\",\"status\":\"launching\"}\n"[..],
            );
            after.extend_from_slice(truncated.as_bytes());
            let receipt = LaunchReceipt::parse(&after)
                .expect("the finished answer must survive the fragment");
            assert!(receipt.accepted);
            assert_eq!(receipt.run_id, "work-real");
        }
    }

    #[test]
    fn a_stamped_refusal_keeps_its_reason_and_the_audit_owns_its_version() {
        // A receipt of another version of this family is still a receipt.
        // The parser recognizes the envelope; refusing the version is the
        // audit's job, and the refusal keeps the reason the launcher gave.
        let bytes = br#"{"schema":"vibecrafted.launch_receipt.v2","accepted":false,"run_id":"","status":"rejected","message":"agy: --sandbox false cannot be enforced"}"#;
        let req = request(LaunchKind::Workflow);
        let outcome = LaunchOutcome::from_run(
            "p".to_string(),
            LaunchExpectation::new(&req, None),
            completed(bytes, b""),
        );
        assert_eq!(outcome.admission(), Admission::Refused);
        assert!(
            outcome
                .detail_lines()
                .iter()
                .any(|line| line.contains("--sandbox false cannot be enforced"))
        );
        let audit = outcome.audit();
        assert!(
            audit.mismatched.iter().any(|line| {
                line.starts_with("receipt schema") && line.contains("vibecrafted.launch_receipt.v2")
            }),
            "the version the audit owns must be named: {:?}",
            audit.mismatched
        );
    }

    #[test]
    fn an_answer_survives_a_line_that_begins_with_a_closer() {
        // Trailing text is trailing text, whatever byte it starts with. A
        // parser that read a leading `]` as proof the answer had been nested
        // would throw away the only receipt the launcher gave.
        let bytes = b"{\"schema\":\"vibecrafted.launch_receipt.v1\",\"accepted\":true,\"run_id\":\"work-real\",\"status\":\"launching\"}\n] worker output closed\n";
        let receipt = LaunchReceipt::parse(bytes).expect("trailing text must not hide the answer");
        assert!(receipt.accepted);
        assert_eq!(receipt.run_id, "work-real");
    }

    #[test]
    fn braces_and_quotes_inside_strings_are_text_not_structure() {
        let bytes = br#"{"schema":"vibecrafted.launch_receipt.v1","accepted":true,"run_id":"work-real","status":"launching","message":"printed {\"accepted\": false} and \"}\" as text","noise":[["{","}"],["\\"],[{"accepted":false}]]}"#;
        let receipt = LaunchReceipt::parse(bytes).expect("quoted braces never end a value");
        assert!(receipt.accepted);
        assert_eq!(receipt.run_id, "work-real");
        assert!(
            receipt.message.contains("printed {\"accepted\": false}"),
            "{}",
            receipt.message
        );
    }

    #[test]
    fn an_answer_at_the_end_of_an_elided_stream_is_still_read() {
        // A launcher that outruns the head budget still gets its answer
        // read: the elision decides what an operator is shown, never what
        // VOC knows. The two projections part company here, and the test
        // holds both — the answer entire, the text honestly holed.
        let mut capture = BoundedCapture::default();
        for index in 0..20_000 {
            capture.push(format!("{{\"log\":\"noise\",\"i\":{index}}}\n").as_bytes());
        }
        capture.push(br#"{"schema":"vibecrafted.launch_receipt.v1","accepted":true,"run_id":"work-elided","status":"launching"}"#);
        capture.push(b"\n");
        assert!(capture.dropped > 0, "the fixture must outrun the caps");

        let receipt = capture
            .answer()
            .expect("the answer must survive the elision");
        assert!(receipt.accepted);
        assert_eq!(receipt.run_id, "work-elided");

        // The display text is a bounded projection with a gap named in it.
        // Reading structure back out of it is what this boundary stops
        // doing, so the verdict above was never taken from here.
        let shown = String::from_utf8_lossy(&capture.snapshot()).into_owned();
        assert!(
            shown.contains("elided") && shown.len() < 20_000 * 28,
            "the operator's text stays bounded and says where the gap is"
        );
    }

    #[test]
    fn diagnostics_without_a_verdict_are_unknown_and_never_a_refusal() {
        // A launcher that printed only chatter refused nothing. Reading that
        // as a refusal would invent an answer it never gave.
        let req = request(LaunchKind::Workflow);
        for chatter in [
            &b"{\"status\":\"draining\"}"[..],
            &b"{\"schema\":\"vibecrafted.execution_controls.v1\",\"provider\":\"claude\"}"[..],
            &b"the launcher said nothing machine-readable"[..],
        ] {
            assert!(
                LaunchReceipt::parse(chatter).is_err(),
                "chatter must not parse as a receipt: {}",
                String::from_utf8_lossy(chatter)
            );
            let outcome = LaunchOutcome::from_run(
                "p".to_string(),
                LaunchExpectation::new(&req, None),
                completed(chatter, b""),
            );
            assert_eq!(
                outcome.admission(),
                Admission::Unknown,
                "chatter is not a refusal: {}",
                String::from_utf8_lossy(chatter)
            );
        }
    }

    #[test]
    fn a_stated_refusal_is_still_a_refusal_behind_later_noise() {
        let bytes = b"{\"schema\":\"vibecrafted.launch_receipt.v1\",\"accepted\":false,\"run_id\":\"\",\"status\":\"rejected\",\"message\":\"agy: --sandbox false cannot be enforced\"}\n{\"status\":\"draining\"}\n";
        let req = request(LaunchKind::Workflow);
        let outcome = LaunchOutcome::from_run(
            "p".to_string(),
            LaunchExpectation::new(&req, None),
            completed(bytes, b""),
        );
        assert_eq!(outcome.admission(), Admission::Refused);
        assert!(
            outcome
                .detail_lines()
                .iter()
                .any(|line| line.contains("--sandbox false cannot be enforced"))
        );
    }

    #[test]
    fn a_bounded_wait_leaves_a_silent_launcher_running_and_the_outcome_unknown() {
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "sleep 30"]);
        let run = run_bounded(command, None, Duration::from_millis(200), "silent stub").unwrap();
        let waited = match run {
            LauncherRun::Undecided { waited, .. } => waited,
            LauncherRun::Completed { .. } => {
                panic!("a sleeping launcher must not report completion")
            }
            LauncherRun::Unobservable { error, .. } => {
                panic!("the wait on a healthy sleeping child must not be lost: {error}")
            }
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
                answer: Err("launcher printed no receipt".to_string()),
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

    /// One stamped launch answer, in the shape the canonical launcher prints.
    fn stamped(run_id: &str, accepted: bool) -> String {
        format!(
            r#"{{"schema":"vibecrafted.launch_receipt.v1","accepted":{accepted},"run_id":"{run_id}","status":"launching"}}"#
        )
    }

    /// Complete top-level diagnostic objects, enough of them to outrun the
    /// head cap on their own. `separator` is what the launcher prints between
    /// them, so the same noise can be used inside an array or at top level.
    fn noise_larger_than_the_head(separator: &str) -> String {
        let mut text = String::new();
        let mut index = 0u32;
        while text.len() < DIAGNOSTIC_HEAD_CAP + DIAGNOSTIC_TAIL_CAP * 2 {
            text.push_str(&format!("{{\"log\":\"noise\",\"i\":{index}}}{separator}\n"));
            index += 1;
        }
        text
    }

    #[test]
    fn a_frame_opened_before_the_elision_never_makes_its_members_top_level() {
        // The launcher opened an array and never closed it. Everything after
        // that opener is inside it, including the stamped object printed
        // last — and the reader knows this because it saw the opener, even
        // though the display text no longer contains it.
        for accepted in [true, false] {
            let mut capture = BoundedCapture::default();
            capture.push(b"{\"outer\":[\n");
            capture.push(noise_larger_than_the_head(",").as_bytes());
            capture.push(format!("{}\n", stamped("work-nested", accepted)).as_bytes());
            assert!(capture.dropped > 0, "the fixture must outrun the caps");
            assert!(
                capture.answer().is_err(),
                "a member of a frame that never closed is not an answer (accepted={accepted})"
            );
        }
    }

    #[test]
    fn an_earlier_receipt_survives_a_frame_opened_after_it_and_never_closed() {
        // Losing structural certainty must not retract what was already
        // proved: the stamped receipt read whole, before the opener, stands.
        let mut capture = BoundedCapture::default();
        capture.push(format!("{}\n", stamped("work-early", true)).as_bytes());
        capture.push(b"{\"outer\":[\n");
        capture.push(noise_larger_than_the_head(",").as_bytes());
        capture.push(format!("{}\n", stamped("work-nested", false)).as_bytes());
        assert!(capture.dropped > 0, "the fixture must outrun the caps");

        let receipt = capture
            .answer()
            .expect("the receipt proved before the opener must survive");
        assert_eq!(receipt.run_id, "work-early");
        assert!(receipt.accepted);
    }

    #[test]
    fn a_receipt_after_complete_diagnostics_larger_than_the_head_is_still_the_answer() {
        // The promise the boundary must not break: diagnostics that outrun
        // the head are stepped over, and the answer printed after them is
        // still read.
        let mut capture = BoundedCapture::default();
        capture.push(noise_larger_than_the_head("").as_bytes());
        capture.push(format!("{}\n", stamped("work-late", true)).as_bytes());
        assert!(capture.dropped > 0, "the fixture must outrun the caps");

        let receipt = capture
            .answer()
            .expect("a receipt behind complete noise must still be read");
        assert_eq!(receipt.run_id, "work-late");
        assert!(receipt.accepted);
    }

    #[test]
    fn an_elision_notice_printed_by_the_launcher_closes_nothing() {
        // VOC's own gap notice is display prose. A launcher that prints the
        // same words is printing text, and text never resets what the reader
        // proved about structure.
        let mut capture = BoundedCapture::default();
        capture.push(b"{\"outer\":[\n");
        capture.push(
            format!(
                "\n… [VOC kept the first {DIAGNOSTIC_HEAD_CAP} and last {DIAGNOSTIC_TAIL_CAP} bytes; 999999 elided] …\n"
            )
            .as_bytes(),
        );
        capture.push(format!("{}\n", stamped("work-spoofed", true)).as_bytes());
        assert!(
            capture.answer().is_err(),
            "a notice printed by the child must not make its members top-level"
        );
    }

    #[test]
    fn a_frame_too_large_to_be_a_receipt_is_stepped_over_rather_than_held() {
        // One value larger than anything VOC will hold, with no newline in
        // it. Its boundary is still known, so the answer printed after it is
        // read — and none of it was ever kept.
        let mut capture = BoundedCapture::default();
        capture.push(b"{\"blob\":\"");
        capture.push("x".repeat(RECEIPT_FRAME_CAP * 2).as_bytes());
        capture.push(b"\"}\n");
        capture.push(format!("{}\n", stamped("work-after-blob", true)).as_bytes());

        let receipt = capture
            .answer()
            .expect("an answer after an oversized frame must still be read");
        assert_eq!(receipt.run_id, "work-after-blob");
    }

    #[test]
    fn the_answer_does_not_depend_on_how_the_stream_was_chunked() {
        // Openers, quotes and escapes land wherever the pipe splits them.
        let stream = format!(
            "{{\"note\":\"a brace {{ and a quote \\\" inside a string\"}}\n{}{}\n",
            noise_larger_than_the_head(""),
            stamped("work-chunked", true)
        );
        let mut whole = BoundedCapture::default();
        whole.push(stream.as_bytes());
        let mut split = BoundedCapture::default();
        for chunk in stream.as_bytes().chunks(7) {
            split.push(chunk);
        }
        assert_eq!(
            whole.answer().expect("whole").run_id,
            split.answer().expect("split").run_id
        );
        assert_eq!(whole.answer().expect("whole").run_id, "work-chunked");

        // The same for a stream whose outer frame never closes: chunking
        // must not manufacture a boundary either.
        let unfinished = format!("{{\"outer\":[\n{}\n", stamped("work-nested", true));
        let mut split = BoundedCapture::default();
        for chunk in unfinished.as_bytes().chunks(3) {
            split.push(chunk);
        }
        assert!(split.answer().is_err(), "chunking must not close a frame");
    }

    #[test]
    fn a_frame_closed_by_the_wrong_delimiter_never_hands_the_stream_back() {
        // `{` opened a value and `]` did not close it. That pair proves
        // nothing about where the launcher's value ended, so the stamped
        // object printed after it is a stamped object at an unknown depth —
        // not an answer. Reading it as one would supply, by assumption, the
        // very boundary the stream failed to give.
        let req = request(LaunchKind::Workflow);
        for accepted in [true, false] {
            let mut capture = BoundedCapture::default();
            capture.push(b"{]\n");
            capture.push(stamped("nested-only", accepted).as_bytes());
            assert!(
                capture.answer().is_err(),
                "a mismatched pair must not make the next object top-level (accepted={accepted})"
            );

            let stream = format!("{{]\n{}\n", stamped("nested-only", accepted));
            let outcome = LaunchOutcome::from_run(
                "p".to_string(),
                LaunchExpectation::new(&req, None),
                completed(stream.as_bytes(), b""),
            );
            assert_eq!(
                outcome.admission(),
                Admission::Unknown,
                "broken framing decides nothing, in either direction: {}",
                outcome.trail_line()
            );
            assert_eq!(outcome.run_id(), None);
        }
    }

    #[test]
    fn an_earlier_answer_outlives_the_frame_that_broke_the_reading() {
        // The launcher answered, and then printed something whose structure
        // the reader cannot follow. Losing the thread after an answer takes
        // nothing away from the answer: uncertainty is not a retraction, and
        // the object that arrives during it is not a correction.
        let mut capture = BoundedCapture::default();
        capture.push(format!("{}\n", stamped("work-first", true)).as_bytes());
        capture.push(b"{]\n");
        capture.push(format!("{}\n", stamped("work-second", false)).as_bytes());

        let receipt = capture
            .answer()
            .expect("an answer already proved must survive later uncertainty");
        assert!(receipt.accepted);
        assert_eq!(receipt.run_id, "work-first");

        let stream = format!(
            "{}\n{{]\n{}\n",
            stamped("work-first", true),
            stamped("work-second", false)
        );
        let outcome = LaunchOutcome::from_run(
            "p".to_string(),
            LaunchExpectation::new(&request(LaunchKind::Workflow), None),
            completed(stream.as_bytes(), b""),
        );
        assert_eq!(outcome.admission(), Admission::Admitted);
        assert_eq!(outcome.run_id(), Some("work-first"));
    }

    #[test]
    fn values_of_mixed_kinds_still_close_the_openers_they_match() {
        // Objects and arrays nest through each other freely, and matching is
        // exactly what proves those boundaries. A reader strict enough to
        // reject `{]` must stay permissive enough to read the launcher's
        // ordinary diagnostics, or it buys the fix with everything else.
        let mut capture = BoundedCapture::default();
        capture.push(br#"{"a":[{"b":[[],{}]},[]],"c":{"d":[{"e":{}}]}}"#);
        capture.push(b"\n[[{},[{}]],{},[[[]]]]\n");
        capture.push(format!("{}\n", stamped("work-mixed", true)).as_bytes());

        let receipt = capture
            .answer()
            .expect("matched nesting of either kind must still close");
        assert!(receipt.accepted);
        assert_eq!(receipt.run_id, "work-mixed");
    }

    #[test]
    fn delimiters_inside_strings_are_never_matched_against_anything() {
        // The delimiters that frame a value are the ones outside its strings.
        // A launcher quoting `]` or `}` in a message is quoting a character,
        // and a matcher that weighed it would lose the stream on prose.
        let mut capture = BoundedCapture::default();
        capture.push(br#"{"note":"] } ] [ { \" }] still text","tail":"\\"}"#);
        capture.push(b"\n");
        capture.push(
            br#"{"schema":"vibecrafted.launch_receipt.v1","accepted":true,"run_id":"work-quoted","status":"launching","message":"closed with }] and [ {"}"#,
        );
        capture.push(b"\n");

        let receipt = capture
            .answer()
            .expect("quoted delimiters are text, not structure");
        assert!(receipt.accepted);
        assert_eq!(receipt.run_id, "work-quoted");
        assert!(
            receipt.message.contains("closed with }]"),
            "{}",
            receipt.message
        );
    }

    #[test]
    fn a_broken_pair_is_broken_however_the_pipe_split_it() {
        // The opener and the closer that fails to match it can arrive in
        // different reads. What the stream meant cannot depend on where the
        // pipe happened to cut it.
        let stream = format!("{{]\n{}\n", stamped("nested-only", true));
        for size in [1usize, 2, 3, 7, 64] {
            let mut split = BoundedCapture::default();
            for chunk in stream.as_bytes().chunks(size) {
                split.push(chunk);
            }
            assert!(
                split.answer().is_err(),
                "chunk size {size} must not repair a mismatched pair"
            );
        }
    }

    #[test]
    fn a_value_nested_past_what_voc_can_follow_admits_nothing_after_it() {
        // Under the bound the reader still proves boundaries the ordinary
        // way, and the answer printed after a deep diagnostic is read.
        let mut within = BoundedCapture::default();
        within.push("[".repeat(RECEIPT_DEPTH_CAP - 1).as_bytes());
        within.push("]".repeat(RECEIPT_DEPTH_CAP - 1).as_bytes());
        within.push(format!("\n{}\n", stamped("work-deep-ok", true)).as_bytes());
        let receipt = within
            .answer()
            .expect("nesting within the bound still closes");
        assert_eq!(receipt.run_id, "work-deep-ok");

        // Past it, following the structure would mean keeping a stack whose
        // size the launcher chooses. VOC declines, and says so by admitting
        // nothing further rather than by guessing.
        let mut past = BoundedCapture::default();
        past.push("[".repeat(RECEIPT_DEPTH_CAP + 8).as_bytes());
        past.push("]".repeat(RECEIPT_DEPTH_CAP + 8).as_bytes());
        past.push(format!("\n{}\n", stamped("work-too-deep", true)).as_bytes());
        assert!(
            past.answer().is_err(),
            "a value nested past the bound must not hand the stream back"
        );

        // And an answer proved before the deep value still stands.
        let mut after = BoundedCapture::default();
        after.push(format!("{}\n", stamped("work-before-deep", true)).as_bytes());
        after.push("[".repeat(RECEIPT_DEPTH_CAP + 8).as_bytes());
        after.push(format!("\n{}\n", stamped("work-after-deep", false)).as_bytes());
        let kept = after
            .answer()
            .expect("the earlier answer outlives the unfollowable value");
        assert!(kept.accepted);
        assert_eq!(kept.run_id, "work-before-deep");
    }

    /// Run a real `/bin/sh` launcher stub through the capture boundary and
    /// present it exactly as the console does.
    fn launcher_stub(script: &str) -> LaunchOutcome {
        let mut command = Command::new("/bin/sh");
        command.args(["-c", script]);
        let run = run_bounded(command, None, Duration::from_secs(30), "launcher stub");
        LaunchOutcome::from_run(
            "preview".to_string(),
            LaunchExpectation::new(&request(LaunchKind::Workflow), None),
            run,
        )
    }

    #[test]
    fn a_real_launcher_whose_diagnostics_outrun_the_head_still_proves_its_admission() {
        let outcome = launcher_stub(
            r#"yes '{"log":"noise"}' | head -n 20000; printf '%s\n' '{"schema":"vibecrafted.launch_receipt.v1","accepted":true,"run_id":"work-e2e-late","status":"launching"}'"#,
        );
        assert_eq!(
            outcome.admission(),
            Admission::Admitted,
            "{}",
            outcome.trail_line()
        );
        assert_eq!(outcome.run_id(), Some("work-e2e-late"));
    }

    #[test]
    fn a_real_launcher_that_never_closed_its_frame_leaves_the_admission_unknown() {
        let outcome = launcher_stub(
            r#"printf '%s\n' '{"outer":['; yes '{"log":"noise"},' | head -n 20000; printf '%s\n' '{"schema":"vibecrafted.launch_receipt.v1","accepted":true,"run_id":"work-e2e-nested","status":"launching"}'"#,
        );
        assert_eq!(
            outcome.admission(),
            Admission::Unknown,
            "a member of an unfinished frame must not be read as an admission: {}",
            outcome.trail_line()
        );
        assert_eq!(outcome.run_id(), None);
    }

    #[test]
    fn a_real_launcher_whose_delimiters_never_matched_leaves_the_admission_unknown() {
        // The same defect at the far end of a real pipe: a shell writes a
        // pair that never matched, then writes a stamped acceptance. The
        // acceptance is real text and still not a proved answer.
        let outcome = launcher_stub(
            r#"printf '%s\n' '{]'; printf '%s\n' '{"schema":"vibecrafted.launch_receipt.v1","accepted":true,"run_id":"work-e2e-mismatch","status":"launching"}'"#,
        );
        assert_eq!(
            outcome.admission(),
            Admission::Unknown,
            "an object printed after broken framing must not admit a run: {}",
            outcome.trail_line()
        );
        assert_eq!(outcome.run_id(), None);
    }
}
