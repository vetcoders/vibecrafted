//! Subprocess proof for the public `voc --repo` selector.
//!
//! Runs the real `voc` binary with argument vectors that must be decided
//! before the TUI starts: a conflicting `--repo`/`--root` pair is refused on
//! stderr with a non-zero exit, and an accepted `--repo` still reaches the
//! `--version` short-circuit (so no terminal is ever opened).

use std::process::Command;

fn voc(args: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_voc"))
        .args(args)
        .env_remove("VIBECRAFTED_ROOT")
        .output()
        .expect("voc binary runs")
}

#[test]
fn conflicting_repo_and_root_is_refused_before_anything_starts() {
    let output = voc(&["--repo", "/tmp/left", "--root", "/tmp/right", "--version"]);
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("conflicting --repo /tmp/left and --root /tmp/right; pass one repository"),
        "stderr: {stderr}"
    );
    assert!(String::from_utf8_lossy(&output.stdout).is_empty());
}

#[test]
fn repo_is_accepted_with_legacy_root_semantics() {
    for argv in [
        vec!["--repo", "/tmp/app", "--version"],
        vec!["--repo=/tmp/app", "--root", "/tmp/app", "--version"],
        vec!["--root", "/tmp/app", "--version"],
    ] {
        let output = voc(&argv);
        assert!(output.status.success(), "{argv:?}: {:?}", output);
        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(stdout.starts_with("voc "), "{argv:?}: {stdout}");
    }
}

#[test]
fn help_documents_repo_as_the_standard_selector() {
    let output = voc(&["--help"]);
    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("--repo <path>"), "{stdout}");
    assert!(stdout.contains("Legacy spelling of --repo"), "{stdout}");
}
