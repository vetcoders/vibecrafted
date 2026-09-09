//! The VOC launch contract, end to end.
//!
//! VOC assembles a declaration and hands it to the canonical launcher; the
//! launcher's receipt — not a PID, not a spawned process — decides whether a
//! run exists, and whether it is the run that was declared. These tests drive
//! a stand-in deck that reads the real argv and the real stdin, so the
//! transport is exercised rather than asserted about.
//!
//! This file replaces `launch_readiness.rs`, which froze VOC's own readiness
//! probe over its own `vc-frame` session. Both are gone: the launcher owns
//! session creation and start confirmation.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::time::Duration;

use tempfile::{TempDir, tempdir};
use voc::catalog::{CatalogState, LauncherCatalog};
use voc::launch::{
    Admission, Confirmation, Environment, LaunchOutcome, LaunchReceipt, LauncherRun,
    PermissionPolicy, Presentation, SandboxChoice,
};

mod support;
use support::{agent_index, fixture_app};

/// Generous enough that a healthy stand-in deck always answers inside it.
const PATIENT: Duration = Duration::from_secs(30);
/// Short enough that a deliberately silent deck is still running when it ends.
const IMPATIENT: Duration = Duration::from_millis(400);
/// Long enough for a talkative deck to have said its piece, short enough that
/// it is still running — and still unanswered — when the wait ends.
const PATIENT_ENOUGH_TO_SPEAK: Duration = Duration::from_secs(3);

fn install(dir: &Path, script: String) -> PathBuf {
    let deck = dir.join("deck.sh");
    fs::write(&deck, script).unwrap();
    fs::set_permissions(&deck, fs::Permissions::from_mode(0o755)).unwrap();
    deck
}

/// A stand-in for the canonical deck. It records the argv and the stdin it was
/// given, then answers with `body` on stdout.
fn fake_deck(dir: &Path, body: &str) -> PathBuf {
    install(
        dir,
        format!(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$(dirname \"$0\")/argv.txt\"\ncat > \"$(dirname \"$0\")/stdin.txt\"\ncat <<'RECEIPT'\n{body}\nRECEIPT\n"
        ),
    )
}

/// A deck that floods stderr *before* reading a byte of stdin — the shape that
/// deadlocks any client which writes the whole prompt before draining output.
fn stderr_before_stdin_deck(dir: &Path, body: &str, stderr_bytes: usize) -> PathBuf {
    install(
        dir,
        format!(
            "#!/bin/sh\nawk 'BEGIN{{for(i=0;i<{stderr_bytes};i++)printf \"d\"}}' >&2\ncat > \"$(dirname \"$0\")/stdin.txt\"\ncat <<'RECEIPT'\n{body}\nRECEIPT\n"
        ),
    )
}

/// A deck that never answers: it holds stdin open and prints nothing.
fn silent_deck(dir: &Path) -> PathBuf {
    install(dir, "#!/bin/sh\nsleep 30\n".to_string())
}

/// A deck that admits the run and then keeps running.
fn admit_then_hang_deck(dir: &Path, body: &str) -> PathBuf {
    install(
        dir,
        format!("#!/bin/sh\ncat <<'RECEIPT'\n{body}\nRECEIPT\nsleep 30\n"),
    )
}

/// A deck that mutates the admission side of the control plane and is then
/// terminated by a signal before it can print a receipt. `ExitStatus::code()`
/// is `None` for a signalled child, which must never be read as "the launcher
/// never ran": the artifact below proves work already happened.
fn admit_then_signal_deck(dir: &Path) -> PathBuf {
    install(
        dir,
        "#!/bin/sh\n: > \"$(dirname \"$0\")/admission-side-artifact\"\nkill -TERM $$\nsleep 5\n"
            .to_string(),
    )
}

/// A deck that floods stdout with JSON-shaped noise far beyond the retention
/// cap and only then prints its receipt, plus stderr noise past the cap. The
/// receipt is the last object on stdout; no volume of preceding output may
/// hide it.
fn noisy_then_receipt_deck(
    dir: &Path,
    body: &str,
    noise_lines: usize,
    stderr_bytes: usize,
) -> PathBuf {
    install(
        dir,
        format!(
            "#!/bin/sh\nawk 'BEGIN{{for(i=0;i<{noise_lines};i++)printf \"{{\\\"log\\\":\\\"noise\\\",\\\"i\\\":%d}}\\n\", i}}'\nawk 'BEGIN{{for(i=0;i<{stderr_bytes};i++)printf \"d\"}}' >&2\ncat <<'RECEIPT'\n{body}\nRECEIPT\n"
        ),
    )
}

/// A deck that never answers, floods stderr far past anything VOC should
/// retain, and is still alive and still holding its pipes when the client
/// deadline passes. The burst is finite so this fixture does not starve the
/// tests running beside it; unbounded draining to EOF is proved directly in
/// the transport's own unit tests.
fn noisy_then_silent_deck(dir: &Path, noise_lines: usize) -> PathBuf {
    install(
        dir,
        format!(
            "#!/bin/sh\nawk 'BEGIN{{for(i=0;i<{noise_lines};i++)printf \"noise %d with padding to move real bytes\\n\", i}}' >&2\nsleep 30\n"
        ),
    )
}

/// The receipt a healthy launcher returns for the fixture console's default
/// declaration: claude, workflow, Living Tree, headless, provider defaults.
/// Every field the audit checks is present and agrees.
fn confirmed_receipt(repo: &Path) -> String {
    format!(
        r#"{{"schema":"vibecrafted.launch_receipt.v1","accepted":true,"run_id":"work-260909-120000-11111","status":"launching","agent":"claude","skill":"workflow","root":"{}","transport":"headless","worktree":false,"execution_controls":{{"schema":"vibecrafted.execution_controls.v1","provider":"claude","permissions_requested":"","permissions_effective":"bypass","sandbox_requested":"","sandbox_effective":"provider-default","provider_flags":["--permission-mode","bypassPermissions"]}}}}"#,
        repo.display()
    )
}

fn workspace() -> (TempDir, PathBuf, PathBuf) {
    let dir = tempdir().unwrap();
    let repo = dir.path().join("repo");
    fs::create_dir_all(repo.join(".git")).unwrap();
    let roots = dir.path().join("roots");
    fs::create_dir_all(&roots).unwrap();
    (dir, repo, roots)
}

#[test]
fn every_declared_choice_reaches_the_launcher_and_the_prompt_never_reaches_argv() {
    let (dir, repo, roots) = workspace();
    let deck = fake_deck(dir.path(), &confirmed_receipt(&repo));
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");
    app.launch_model = "claude-opus-5".to_string();
    app.launch_environment = Environment::FleetWorktrees;
    app.launch_presentation = Presentation::Headless;
    app.launch_permissions = PermissionPolicy::ReadOnly;
    app.launch_sandbox = SandboxChoice::On;
    app.launch_prompt = "Zbadaj kontrakt — ściśle tajny prompt 🔒".to_string();

    let command = app
        .launch_plan()
        .unwrap_or_else(|refusals| panic!("declaration must be accepted: {refusals:?}"));

    let argv = command
        .args
        .iter()
        .map(|value| value.to_string_lossy().into_owned())
        .collect::<Vec<_>>();

    // Every user choice has executive force: it is in the argv the launcher sees.
    assert_eq!(argv[0], "workflow");
    assert_eq!(argv[1], "claude");
    assert!(argv.contains(&"--prompt-stdin".to_string()));
    assert_eq!(
        argv.iter()
            .position(|a| a == "--repo")
            .map(|i| &argv[i + 1]),
        Some(&repo.to_string_lossy().into_owned())
    );
    assert_eq!(
        argv.iter()
            .position(|a| a == "--worktree")
            .map(|i| &argv[i + 1]),
        Some(&"true".to_string())
    );
    assert_eq!(
        argv.iter()
            .position(|a| a == "--permissions")
            .map(|i| &argv[i + 1]),
        Some(&"read-only".to_string())
    );
    assert_eq!(
        argv.iter()
            .position(|a| a == "--sandbox")
            .map(|i| &argv[i + 1]),
        Some(&"true".to_string())
    );
    assert_eq!(
        argv.iter()
            .position(|a| a == "--model")
            .map(|i| &argv[i + 1]),
        Some(&"claude-opus-5".to_string())
    );
    assert_eq!(argv.last().unwrap(), "--json");

    // The prompt is private: not in argv, not in the preview, only on stdin.
    assert!(
        !argv.iter().any(|value| value.contains("ściśle tajny")),
        "prompt content must never reach public argv: {argv:?}"
    );
    let preview = app.launch_preview();
    assert!(
        !preview.contains("ściśle tajny"),
        "preview must not reveal the prompt: {preview}"
    );
    assert!(
        preview.contains("<stdin: prompt,") && preview.contains("chars>"),
        "preview must still be honest about the private payload: {preview}"
    );

    command.run_capturing(PATIENT).unwrap();

    let seen_argv = fs::read_to_string(dir.path().join("argv.txt")).unwrap();
    assert!(
        !seen_argv.contains("ściśle tajny"),
        "the launcher process must not observe the prompt in argv: {seen_argv}"
    );
    let seen_stdin = fs::read_to_string(dir.path().join("stdin.txt")).unwrap();
    assert_eq!(
        seen_stdin, "Zbadaj kontrakt — ściśle tajny prompt 🔒",
        "the exact multibyte prompt must arrive intact on stdin"
    );
}

#[test]
fn a_receipt_that_confirms_every_declared_field_is_the_only_confirmed_launch() {
    let (dir, repo, roots) = workspace();
    let deck = fake_deck(dir.path(), &confirmed_receipt(&repo));
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");

    let command = app.launch_plan().unwrap();
    let outcome = LaunchOutcome::from_run(
        command.preview(),
        app.launch_expectation(),
        command.run_capturing(PATIENT),
    );

    assert_eq!(outcome.admission(), Admission::Admitted);
    assert_eq!(outcome.run_id(), Some("work-260909-120000-11111"));
    let audit = outcome.audit();
    assert_eq!(
        audit.confirmation(),
        Confirmation::Confirmed,
        "nothing should be left unconfirmed: mismatched={:?} unverified={:?}",
        audit.mismatched,
        audit.unverified
    );

    app.record_launch_outcome(outcome);
    let confirmation = app.confirmation_lines().join("\n");
    assert!(
        confirmation.contains("work-260909-120000-11111"),
        "the operator must see the launcher's run id: {confirmation}"
    );
}

#[test]
fn a_receipt_silent_about_declared_fields_is_unverified_not_accepted() {
    let (dir, repo, roots) = workspace();
    // The launcher names the run and nothing else: no agent, no skill, no
    // repository, no transport, no execution controls.
    let deck = fake_deck(
        dir.path(),
        r#"{"schema":"vibecrafted.launch_receipt.v1","accepted":true,"run_id":"work-1","status":"launching"}"#,
    );
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");

    let command = app.launch_plan().unwrap();
    let outcome = LaunchOutcome::from_run(
        command.preview(),
        app.launch_expectation(),
        command.run_capturing(PATIENT),
    );

    // The run exists — that fact survives on its own.
    assert_eq!(outcome.admission(), Admission::Admitted);
    let audit = outcome.audit();
    assert_eq!(audit.confirmation(), Confirmation::Unverified);
    assert!(
        audit.mismatched.is_empty(),
        "silence is not contradiction: {:?}",
        audit.mismatched
    );
    for field in [
        "agent",
        "skill",
        "repository",
        "presentation",
        "execution controls",
    ] {
        assert!(
            audit.unverified.iter().any(|line| line.starts_with(field)),
            "the receipt's silence about {field} must be named: {:?}",
            audit.unverified
        );
    }
    let detail = outcome.detail_lines().join("\n");
    assert!(
        detail.contains("NOT CONFIRMED BY THE RECEIPT:"),
        "the operator must not read this as a confirmed launch: {detail}"
    );
}

#[test]
fn a_receipt_contradicting_the_declaration_is_a_mismatch_not_a_success() {
    let (dir, repo, roots) = workspace();
    // A run was named, but for another agent, another skill, another
    // repository, another transport, and with weaker controls than the
    // catalog promised.
    let deck = fake_deck(
        dir.path(),
        r#"{"schema":"vibecrafted.launch_receipt.v1","accepted":true,"run_id":"work-2","agent":"codex","skill":"review","root":"/somewhere/else","transport":"vc-frame","worktree":true,"worktree_path":"/wt/x","parent_root":"/somewhere/else","execution_controls":{"schema":"vibecrafted.execution_controls.v1","provider":"codex","permissions_requested":"bypass","permissions_effective":"bypass","sandbox_requested":"","sandbox_effective":"disabled","provider_flags":["--yolo"]}}"#,
    );
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");

    let command = app.launch_plan().unwrap();
    let outcome = LaunchOutcome::from_run(
        command.preview(),
        app.launch_expectation(),
        command.run_capturing(PATIENT),
    );

    assert_eq!(outcome.admission(), Admission::Admitted);
    let audit = outcome.audit();
    assert_eq!(audit.confirmation(), Confirmation::Mismatched);
    for field in [
        "agent",
        "skill",
        "repository",
        "presentation",
        "environment",
        "execution controls provider",
        "permissions requested",
        "sandbox effective",
        "provider flags",
    ] {
        assert!(
            audit.mismatched.iter().any(|line| line.starts_with(field)),
            "a contradicted {field} must be named: {:?}",
            audit.mismatched
        );
    }
    assert!(
        outcome.trail_line().contains("DECLARATION NOT HONORED"),
        "the trail must not read as an honored launch: {}",
        outcome.trail_line()
    );
}

#[test]
fn a_foreign_receipt_schema_is_never_read_as_confirmation() {
    let (dir, repo, roots) = workspace();
    let deck = fake_deck(
        dir.path(),
        r#"{"schema":"vibecrafted.launch_receipt.v2","accepted":true,"run_id":"work-3"}"#,
    );
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");

    let command = app.launch_plan().unwrap();
    let outcome = LaunchOutcome::from_run(
        command.preview(),
        app.launch_expectation(),
        command.run_capturing(PATIENT),
    );

    let audit = outcome.audit();
    assert_eq!(audit.confirmation(), Confirmation::Mismatched);
    assert!(
        audit
            .mismatched
            .iter()
            .any(|line| line.starts_with("receipt schema")),
        "a receipt from another contract must be named: {:?}",
        audit.mismatched
    );
}

#[test]
fn a_refused_receipt_is_a_failure_even_though_a_process_ran() {
    let (dir, repo, roots) = workspace();
    let deck = fake_deck(
        dir.path(),
        r#"{"schema":"vibecrafted.launch_receipt.v1","accepted":false,"run_id":"","reason":"sandbox=true cannot be enforced for agy"}"#,
    );
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");

    let command = app.launch_plan().unwrap();
    let outcome = LaunchOutcome::from_run(
        command.preview(),
        app.launch_expectation(),
        command.run_capturing(PATIENT),
    );

    assert_eq!(
        outcome.admission(),
        Admission::Refused,
        "a process that ran and refused is not a started run"
    );
    let detail = outcome.detail_lines().join("\n");
    assert!(
        detail.contains("cannot be enforced"),
        "the launcher's refusal reason must reach the operator: {detail}"
    );
}

#[test]
fn a_receiptless_exit_leaves_the_outcome_unknown_not_successful() {
    let (dir, repo, roots) = workspace();
    let deck = fake_deck(dir.path(), "");
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");

    let command = app.launch_plan().unwrap();
    let outcome = LaunchOutcome::from_run(
        command.preview(),
        app.launch_expectation(),
        command.run_capturing(PATIENT),
    );

    // The launcher ran. It may have mutated the control plane before dying,
    // so "nothing started" is a claim VOC has no right to make.
    assert_eq!(outcome.admission(), Admission::Unknown);
    assert!(
        outcome.transport_error.is_some(),
        "a zero exit without a receipt must be surfaced, not treated as a start"
    );
    let trail = outcome.trail_line();
    assert!(
        trail.starts_with("UNKNOWN ·") && trail.contains("a worker may already be running"),
        "an unreadable answer must not be reported as no run: {trail}"
    );
}

#[test]
fn a_large_prompt_survives_a_launcher_that_writes_stderr_before_reading_stdin() {
    let (dir, repo, roots) = workspace();
    // 256 KiB of stderr ahead of the first read, against a prompt far larger
    // than any pipe buffer. Writing stdin to completion before draining the
    // output streams wedges both sides here.
    let deck = stderr_before_stdin_deck(dir.path(), &confirmed_receipt(&repo), 256 * 1024);
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");
    let prompt = "Ω".repeat(400_000);
    app.launch_prompt = prompt.clone();

    let command = app.launch_plan().unwrap();
    let outcome = LaunchOutcome::from_run(
        command.preview(),
        app.launch_expectation(),
        command.run_capturing(PATIENT),
    );

    assert_eq!(
        outcome.admission(),
        Admission::Admitted,
        "concurrent I/O must let both directions finish: {}",
        outcome.trail_line()
    );
    assert_eq!(
        fs::read_to_string(dir.path().join("stdin.txt")).unwrap(),
        prompt,
        "the original prompt bytes must arrive intact, however large"
    );
    assert_eq!(
        outcome.stderr.len(),
        256 * 1024,
        "the launcher's diagnostics must be captured, not dropped"
    );
}

#[test]
fn a_launcher_that_never_answers_bounds_the_wait_and_preserves_uncertainty() {
    let (dir, repo, roots) = workspace();
    let deck = silent_deck(dir.path());
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");

    let command = app.launch_plan().unwrap();
    let outcome = LaunchOutcome::from_run(
        command.preview(),
        app.launch_expectation(),
        command.run_capturing(IMPATIENT),
    );

    assert!(
        outcome.timed_out().is_some(),
        "the wait must be bounded, not open-ended"
    );
    assert_eq!(
        outcome.admission(),
        Admission::Unknown,
        "a wait that ended first says nothing about whether a worker started"
    );
    let detail = outcome.detail_lines().join("\n");
    assert!(
        detail.contains("OUTCOME UNKNOWN") && detail.contains("will not"),
        "the operator must be told VOC neither stopped nor retried anything: {detail}"
    );
}

#[test]
fn a_launcher_that_admits_and_keeps_running_still_reports_its_run() {
    let (dir, repo, roots) = workspace();
    let deck = admit_then_hang_deck(dir.path(), &confirmed_receipt(&repo));
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");

    let command = app.launch_plan().unwrap();
    let outcome = LaunchOutcome::from_run(
        command.preview(),
        app.launch_expectation(),
        command.run_capturing(IMPATIENT),
    );

    // The admission is real even though the wait ended first, and the run is
    // named rather than lost — but the exit was never observed, so the launch
    // is not reported as fully confirmed.
    assert_eq!(outcome.admission(), Admission::Admitted);
    assert_eq!(outcome.run_id(), Some("work-260909-120000-11111"));
    assert!(outcome.timed_out().is_some());
    let audit = outcome.audit();
    assert_eq!(audit.confirmation(), Confirmation::Unverified);
    assert!(
        audit
            .unverified
            .iter()
            .any(|line| line.starts_with("launcher exit")),
        "an unobserved exit must be named as unverified: {:?}",
        audit.unverified
    );
}

#[test]
fn a_launcher_killed_before_its_receipt_is_unknown_not_a_failed_start() {
    let (dir, repo, roots) = workspace();
    let deck = admit_then_signal_deck(dir.path());
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");

    let command = app.launch_plan().unwrap();
    let outcome = LaunchOutcome::from_run(
        command.preview(),
        app.launch_expectation(),
        command.run_capturing(PATIENT),
    );

    assert!(
        dir.path().join("admission-side-artifact").exists(),
        "the fixture must prove the launcher ran and reached the admission side"
    );
    // A signalled child carries no exit code. That is a fact about how it
    // ended, never evidence that VOC failed to hand over the declaration.
    assert_eq!(outcome.exit_code, None);
    assert_eq!(
        outcome.admission(),
        Admission::Unknown,
        "a launcher killed mid-flight may already have admitted a run: {}",
        outcome.trail_line()
    );
    let trail = outcome.trail_line();
    assert!(
        !trail.contains("failed before the launcher started"),
        "a launcher that demonstrably ran must never be reported as never started: {trail}"
    );
    assert!(
        trail.contains("a worker may already be running"),
        "the operator must be told to check Live Runs before relaunching: {trail}"
    );
    let detail = outcome.detail_lines().join("\n");
    assert!(
        detail.contains("signal"),
        "the kill itself must reach the operator, not just the missing receipt: {detail}"
    );
}

#[test]
fn a_receipt_behind_output_beyond_the_retention_cap_is_still_read() {
    let (dir, repo, roots) = workspace();
    // ~1.2 MB of JSON-shaped stdout ahead of the receipt, and 400 KiB of
    // stderr: both far past anything VOC should retain, with the real
    // receipt last on stdout where the contract puts it.
    let deck = noisy_then_receipt_deck(dir.path(), &confirmed_receipt(&repo), 40_000, 400 * 1024);
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");

    let command = app.launch_plan().unwrap();
    let outcome = LaunchOutcome::from_run(
        command.preview(),
        app.launch_expectation(),
        command.run_capturing(PATIENT),
    );

    assert_eq!(
        outcome.admission(),
        Admission::Admitted,
        "a valid trailing receipt must survive any volume of preceding output: {}",
        outcome.trail_line()
    );
    assert_eq!(outcome.run_id(), Some("work-260909-120000-11111"));
    assert!(
        outcome.stderr.len() < 400 * 1024,
        "retained diagnostics must be bounded, not the launcher's whole stream: {} bytes",
        outcome.stderr.len()
    );
}

#[test]
fn a_noisy_launcher_that_never_answers_bounds_what_voc_retains() {
    let (dir, repo, roots) = workspace();
    // ~1.6 MB of stderr, five times over anything VOC keeps, from a deck
    // that is still running when the wait ends.
    let deck = noisy_then_silent_deck(dir.path(), 40_000);
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");

    let command = app.launch_plan().unwrap();
    let outcome = LaunchOutcome::from_run(
        command.preview(),
        app.launch_expectation(),
        command.run_capturing(PATIENT_ENOUGH_TO_SPEAK),
    );

    assert!(
        outcome.timed_out().is_some(),
        "the wait must still be bounded: {}",
        outcome.trail_line()
    );
    assert_eq!(
        outcome.admission(),
        Admission::Unknown,
        "noise is not an answer, and nothing was killed to tidy up the wait"
    );
    assert!(
        !outcome.stderr.is_empty(),
        "the fixture must actually produce output for this to mean anything"
    );
    assert!(
        outcome.stderr.len() <= 512 * 1024,
        "a launcher that never stops talking must not grow what VOC retains without bound: {} bytes",
        outcome.stderr.len()
    );
}

#[test]
fn a_wait_lost_after_the_spawn_is_unknown_not_an_absent_launcher() {
    let (dir, repo, roots) = workspace();
    let deck = fake_deck(dir.path(), &confirmed_receipt(&repo));
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");
    let command = app.launch_plan().unwrap();

    // The spawn succeeded and VOC then lost the ability to wait on the
    // child. That arrives at the transport boundary as its own outcome:
    // provoking a real `waitpid` failure would mean reaping the child from
    // underneath the transport, which is a race, not a test.
    let blind = LaunchOutcome::from_run(
        command.preview(),
        app.launch_expectation(),
        Ok(LauncherRun::Unobservable {
            error: "failed to wait for launcher: No child processes (os error 10)".to_string(),
            stdout: Vec::new(),
            stderr: Vec::new(),
            answer: Err("launcher printed no receipt".to_string()),
        }),
    );

    assert_eq!(
        blind.admission(),
        Admission::Unknown,
        "losing sight of a launcher says nothing about whether it started: {}",
        blind.trail_line()
    );
    assert!(
        blind.launcher_started,
        "the spawn succeeded and that fact must survive the lost wait"
    );
    let trail = blind.trail_line();
    assert!(
        !trail.contains("failed before the launcher started"),
        "a blind wait must never be reported as an absent launcher: {trail}"
    );
    assert!(
        trail.contains("a worker may already be running"),
        "the operator must be sent to Live Runs rather than told nothing ran: {trail}"
    );

    // And whatever the launcher did manage to say still counts: an admission
    // already on stdout is not lost because the wait was.
    let spoke_first = LaunchOutcome::from_run(
        command.preview(),
        app.launch_expectation(),
        Ok(LauncherRun::Unobservable {
            error: "failed to wait for launcher: No child processes (os error 10)".to_string(),
            stdout: confirmed_receipt(&repo).into_bytes(),
            stderr: Vec::new(),
            // What the launcher managed to say was read while it was
            // saying it, which is why losing the wait does not lose it.
            answer: LaunchReceipt::parse(confirmed_receipt(&repo).as_bytes())
                .map_err(|why| why.to_string()),
        }),
    );
    assert_eq!(spoke_first.admission(), Admission::Admitted);
    assert_eq!(spoke_first.run_id(), Some("work-260909-120000-11111"));
}

#[test]
fn unsupported_and_unavailable_choices_are_refused_before_any_process_exists() {
    let (dir, repo, roots) = workspace();
    let deck = fake_deck(dir.path(), &confirmed_receipt(&repo));
    let mut app = fixture_app(&repo, &deck, &roots);

    // An agent the launcher reports as unavailable, with the launcher's reason.
    app.launch_agent = agent_index("grok");
    let refusals = app.launch_plan().unwrap_err();
    assert!(
        refusals.iter().any(|line| line.contains("grok")),
        "an unavailable agent must be refused with the launcher's reason: {refusals:?}"
    );

    // A model pin on an agent that exposes no model flag.
    app.launch_agent = agent_index("junie");
    app.launch_model = "gpt-6".to_string();
    let refusals = app.launch_plan().unwrap_err();
    assert!(
        refusals
            .iter()
            .any(|line| line.contains("model") && line.contains("junie")),
        "a model pin an agent cannot carry must be refused: {refusals:?}"
    );

    // An environment the launcher has no entrypoint for.
    app.launch_agent = agent_index("claude");
    app.launch_model.clear();
    app.launch_environment = Environment::FleetVmLocal;
    let refusals = app.launch_plan().unwrap_err();
    assert!(
        refusals
            .iter()
            .any(|line| line.contains("Fleet VM local") && line.contains("no canonical VM")),
        "an unavailable environment must not silently fall back: {refusals:?}"
    );

    // A control combination the catalog reports as unsupported.
    app.launch_environment = Environment::LivingTree;
    app.launch_agent = agent_index("agy");
    app.launch_sandbox = SandboxChoice::Off;
    let refusals = app.launch_plan().unwrap_err();
    assert!(
        refusals
            .iter()
            .any(|line| line.contains("cannot be enforced")),
        "an unsupported control cell must be refused with the launcher's reason: {refusals:?}"
    );

    // No process was ever created for any of the refusals.
    assert!(
        !dir.path().join("argv.txt").exists(),
        "a refused declaration must not reach the launcher at all"
    );
}

#[test]
fn a_control_combination_the_catalog_does_not_describe_is_refused() {
    let (dir, repo, roots) = workspace();
    let deck = fake_deck(dir.path(), &confirmed_receipt(&repo));
    let mut app = fixture_app(&repo, &deck, &roots);
    // A catalog that names the agent and the environment but describes no
    // control cells at all. Silence about a combination is not permission to
    // launch it.
    app.catalog = CatalogState::Ready(
        LauncherCatalog::parse(
            br#"{"schema":"vibecrafted.workflow_capabilities.v1","agents":["claude"],
                 "providers":{"claude":{"binary":"claude","available":true,
                   "model_override":{"supported":true,"flag":"--model"},"controls":[]}},
                 "environments":{"local-native":{"label":"Living Tree","available":true,
                   "skill_launcher_supported":true}}}"#,
        )
        .unwrap(),
    );
    app.launch_agent = 0;

    let refusals = app.launch_plan().unwrap_err();
    assert!(
        refusals
            .iter()
            .any(|line| line.contains("does not report permissions")),
        "an unreported control cell must block the launch: {refusals:?}"
    );
    assert!(
        !dir.path().join("argv.txt").exists(),
        "an undescribed combination must never reach the launcher"
    );
}

#[test]
fn a_home_directory_repository_is_refused_as_a_work_context() {
    let (dir, _repo, roots) = workspace();
    let deck = fake_deck(dir.path(), "{}");
    let home = dirs_home();
    let mut app = fixture_app(&home, &deck, &roots);
    app.launch_agent = agent_index("claude");

    let refusals = app.launch_plan().unwrap_err();
    assert!(
        refusals.iter().any(|line| line.contains("home directory")),
        "the operator's home is not a work context: {refusals:?}"
    );
}

fn dirs_home() -> PathBuf {
    PathBuf::from(std::env::var_os("HOME").expect("HOME must be set for this test"))
}

/// Opt-in exact-source cross-language gate; no fixture substitutes for core output.
#[test]
#[ignore = "requires VC_TEST_REAL_DECK and a prepared source Python"]
fn real_selected_generation_catalog_reaches_voc() {
    let deck = std::path::PathBuf::from(std::env::var_os("VC_TEST_REAL_DECK").unwrap());
    let catalog = LauncherCatalog::load(&deck, &std::collections::BTreeMap::new()).unwrap();
    assert_eq!(
        catalog.agents,
        vec!["agy", "claude", "codex", "cursor", "grok", "junie"]
    );
    let codex = catalog.provider("codex").unwrap();
    assert!(codex.model_override.supported);
    assert!(codex.control_cell(None, None).unwrap().supported);
    assert!(
        !codex
            .control_cell(Some("accept-edits"), None)
            .unwrap()
            .supported
    );
    assert!(catalog.environment_availability("local-native").is_ok());
    assert!(catalog.environment_availability("cloud-soon").is_err());
}

/// Only the provider is a fixture. Catalog, declaration, shell, core, durable
/// receipt and VOC's admission audit all execute their real implementations.
#[test]
#[ignore = "requires VC_TEST_REAL_DECK and a prepared source Python"]
fn real_deck_receipt_confirms_voc_declaration() {
    let deck = PathBuf::from(std::env::var_os("VC_TEST_REAL_DECK").unwrap());
    let dir = tempdir().unwrap();
    let repo = dir.path().join("private repo with spaces");
    fs::create_dir(&repo).unwrap();
    for args in [
        vec!["init", "-q"],
        vec![
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "baseline",
        ],
    ] {
        assert!(
            std::process::Command::new("git")
                .arg("-C")
                .arg(&repo)
                .args(args)
                .status()
                .unwrap()
                .success()
        );
    }
    let baseline = std::process::Command::new("git")
        .arg("-C")
        .arg(&repo)
        .args(["rev-parse", "HEAD"])
        .output()
        .unwrap();
    let bin = dir.path().join("bin");
    fs::create_dir(&bin).unwrap();
    let provider = bin.join("codex");
    fs::write(&provider, r#"#!/usr/bin/env python3
import json, sys, uuid
if '--help' in sys.argv:
    print('exec stdin'); sys.exit()
if '--version' in sys.argv:
    print('codex fixture'); sys.exit()
sys.stdin.buffer.read()
print(json.dumps({'type':'thread.started','thread_id':str(uuid.uuid4())}), flush=True)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}), flush=True)
"#).unwrap();
    fs::set_permissions(&provider, fs::Permissions::from_mode(0o755)).unwrap();
    let mut env = std::collections::BTreeMap::new();
    env.insert(
        "PATH".to_string(),
        format!("{}:{}", bin.display(), std::env::var("PATH").unwrap()).into(),
    );
    let home = dir.path().join("vc");
    env.insert(
        "VIBECRAFTED_HOME".to_string(),
        home.clone().into_os_string(),
    );
    let catalog = LauncherCatalog::load(&deck, &env).unwrap();
    let codex_index = catalog.agents.iter().position(|a| a == "codex").unwrap();
    let mut app = fixture_app(&repo, &deck, &dir.path().join("roots"));
    app.catalog = CatalogState::Ready(catalog);
    app.launch_agent = codex_index;
    app.launch_environment = Environment::FleetWorktrees;
    app.launch_permissions = PermissionPolicy::ReadOnly;
    app.launch_sandbox = SandboxChoice::On;
    app.launch_model = "fixture-model".to_string();
    app.launch_prompt = "Private Żółć 🔒\r\nsecond line\r\n\r\n".repeat(2000);
    let mut command = app.launch_plan().unwrap();
    command.env.extend(env);
    assert!(!command.preview().contains("Private Żółć"));
    let result = command.run_capturing(Duration::from_secs(90)).unwrap();
    let raw = match &result {
        LauncherRun::Completed { output, .. } => {
            assert!(
                output.status.success(),
                "{}",
                String::from_utf8_lossy(&output.stderr)
            );
            serde_json::from_slice::<serde_json::Value>(&output.stdout).unwrap()
        }
        other => panic!("real launcher did not settle: {other:?}"),
    };
    let outcome = LaunchOutcome::from_run(command.preview(), app.launch_expectation(), Ok(result));
    assert_eq!(outcome.admission(), Admission::Admitted);
    assert_eq!(
        outcome.audit().confirmation(),
        Confirmation::Confirmed,
        "{:?}",
        outcome.audit()
    );
    assert_eq!(
        fs::read(raw["source_snapshot"].as_str().unwrap()).unwrap(),
        app.launch_prompt.as_bytes()
    );
    assert_eq!(raw["parent_root"].as_str().unwrap(), repo.to_str().unwrap());
    assert_eq!(
        raw["worktree_baseline_sha"].as_str().unwrap(),
        String::from_utf8(baseline.stdout).unwrap().trim()
    );
    assert!(Path::new(raw["root"].as_str().unwrap()).starts_with(home.join("worktrees")));
    let meta_path = Path::new(raw["meta"].as_str().unwrap());
    let deadline = std::time::Instant::now() + Duration::from_secs(90);
    loop {
        let meta: serde_json::Value =
            serde_json::from_slice(&fs::read(meta_path).unwrap()).unwrap();
        if !meta["exit_code"].is_null() {
            assert_eq!(meta["exit_code"], 0);
            break;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "fixture worker did not settle: {meta}"
        );
        std::thread::sleep(Duration::from_millis(100));
    }
}
