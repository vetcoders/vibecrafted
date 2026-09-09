//! The VOC launch contract, end to end.
//!
//! VOC assembles a declaration and hands it to the canonical launcher; the
//! launcher's receipt — not a PID, not a spawned process — decides whether a
//! run exists. These tests drive a stand-in deck that reads the real argv and
//! the real stdin, so the transport is exercised rather than asserted about.
//!
//! This file replaces `launch_readiness.rs`, which froze VOC's own readiness
//! probe over its own `vc-frame` session. Both are gone: the launcher owns
//! session creation and start confirmation.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

use tempfile::{TempDir, tempdir};
use voc::launch::{
    Environment, LaunchExpectation, LaunchOutcome, PermissionPolicy, Presentation, SandboxChoice,
};

mod support;
use support::{agent_index, fixture_app};

/// A stand-in for the canonical deck. It records the argv and the stdin it was
/// given, then answers with `body` on stdout.
fn fake_deck(dir: &Path, body: &str) -> PathBuf {
    let deck = dir.join("deck.sh");
    fs::write(
        &deck,
        format!(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$(dirname \"$0\")/argv.txt\"\ncat > \"$(dirname \"$0\")/stdin.txt\"\ncat <<'RECEIPT'\n{body}\nRECEIPT\n"
        ),
    )
    .unwrap();
    fs::set_permissions(&deck, fs::Permissions::from_mode(0o755)).unwrap();
    deck
}

fn accepted_receipt() -> &'static str {
    r#"{"schema":"vibecrafted.launch_receipt.v1","accepted":true,"run_id":"work-260909-120000-11111","transport":"headless","worktree":false,"worktree_path":"","repo":"/tmp/repo","agent":"claude","model":"claude-opus-5"}"#
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
    let deck = fake_deck(dir.path(), accepted_receipt());
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

    command.run_capturing().unwrap();

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
fn an_accepted_receipt_is_the_proof_and_names_the_run() {
    let (dir, repo, roots) = workspace();
    let deck = fake_deck(dir.path(), accepted_receipt());
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");
    app.launch_model = "claude-opus-5".to_string();

    let command = app.launch_plan().unwrap();
    let expectation = LaunchExpectation {
        summary: "workflow claude".to_string(),
        presentation: app.launch_presentation,
        environment: app.launch_environment,
        model: app.launch_model.clone(),
        repo: repo.clone(),
    };
    let outcome =
        LaunchOutcome::from_output(command.preview(), expectation, command.run_capturing());

    assert!(outcome.accepted());
    assert_eq!(outcome.run_id(), Some("work-260909-120000-11111"));
    assert!(
        outcome.declaration_mismatches().is_empty(),
        "an honest launch has nothing to reconcile: {:?}",
        outcome.declaration_mismatches()
    );

    app.record_launch_outcome(outcome);
    let confirmation = app.confirmation_lines().join("\n");
    assert!(
        confirmation.contains("work-260909-120000-11111"),
        "the operator must see the launcher's run id: {confirmation}"
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
    let expectation = LaunchExpectation::from(&voc::launch::LaunchRequest {
        kind: app.launch_kind,
        agent: app.selected_agent().to_string(),
        prompt: app.launch_prompt.clone(),
        presentation: app.launch_presentation,
        environment: app.launch_environment,
        permissions: app.launch_permissions,
        sandbox: app.launch_sandbox,
        model: app.launch_model.clone(),
        repo: repo.clone(),
        count: None,
        depth: None,
        env: Default::default(),
    });
    let outcome =
        LaunchOutcome::from_output(command.preview(), expectation, command.run_capturing());

    assert!(
        !outcome.accepted(),
        "a process that ran and refused is not a started run"
    );
    let detail = outcome.detail_lines().join("\n");
    assert!(
        detail.contains("cannot be enforced"),
        "the launcher's refusal reason must reach the operator: {detail}"
    );
}

#[test]
fn a_receiptless_exit_is_not_reported_as_success() {
    let (dir, repo, roots) = workspace();
    let deck = fake_deck(dir.path(), "");
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");

    let command = app.launch_plan().unwrap();
    let expectation = LaunchExpectation {
        summary: "workflow claude".to_string(),
        presentation: app.launch_presentation,
        environment: app.launch_environment,
        model: String::new(),
        repo: repo.clone(),
    };
    let outcome =
        LaunchOutcome::from_output(command.preview(), expectation, command.run_capturing());

    assert!(!outcome.accepted());
    assert!(
        outcome.transport_error.is_some(),
        "a zero exit without a receipt must be surfaced, not treated as a start"
    );
}

#[test]
fn a_worktree_declaration_the_receipt_does_not_confirm_is_named_a_mismatch() {
    let (dir, repo, roots) = workspace();
    let deck = fake_deck(dir.path(), accepted_receipt());
    let mut app = fixture_app(&repo, &deck, &roots);
    app.launch_agent = agent_index("claude");
    app.launch_environment = Environment::FleetWorktrees;

    let command = app.launch_plan().unwrap();
    let expectation = LaunchExpectation {
        summary: "workflow claude".to_string(),
        presentation: app.launch_presentation,
        environment: Environment::FleetWorktrees,
        model: String::new(),
        repo: repo.clone(),
    };
    let outcome =
        LaunchOutcome::from_output(command.preview(), expectation, command.run_capturing());

    assert!(outcome.accepted());
    let mismatches = outcome.declaration_mismatches();
    assert!(
        mismatches
            .iter()
            .any(|line| line.contains("Fleet Worktrees") && line.contains("no worktree")),
        "an accepted run that ignored the declared environment must be named: {mismatches:?}"
    );
}

#[test]
fn unsupported_and_unavailable_choices_are_refused_before_any_process_exists() {
    let (dir, repo, roots) = workspace();
    let deck = fake_deck(dir.path(), accepted_receipt());
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

    // No process was ever created for any of the three refusals.
    assert!(
        !dir.path().join("argv.txt").exists(),
        "a refused declaration must not reach the launcher at all"
    );
}

#[test]
fn a_home_directory_repository_is_refused_as_a_work_context() {
    let (dir, _repo, roots) = workspace();
    let deck = fake_deck(dir.path(), accepted_receipt());
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
