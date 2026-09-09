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
    Admission, Confirmation, Environment, LaunchOutcome, PermissionPolicy, Presentation,
    SandboxChoice,
};

mod support;
use support::{agent_index, fixture_app};

/// Generous enough that a healthy stand-in deck always answers inside it.
const PATIENT: Duration = Duration::from_secs(30);
/// Short enough that a deliberately silent deck is still running when it ends.
const IMPATIENT: Duration = Duration::from_millis(400);

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
