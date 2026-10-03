//! Subprocess proof for the Runtime Pack inventory probe.
//!
//! `scripts/build-linux-arm64-runtime-pack.sh` records every packed
//! executable by running `<bin> --version` with no terminal attached and
//! aborts the whole build on a non-zero exit. `vc-admin` used to reject the
//! flag (clap exit 2) and `vc-procs` opened its TUI instead (`Device not
//! configured`), which kept the Linux pack red in CI.

use std::process::{Command, Output, Stdio};

fn version(binary: &str) -> Output {
    Command::new(binary)
        .arg("--version")
        .stdin(Stdio::null())
        .output()
        .expect("binary runs")
}

fn assert_version_line(output: &Output, name: &str) {
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        output.status.success(),
        "{name} --version exited {:?}; stderr: {}",
        output.status,
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        stdout.trim(),
        format!("{name} {}", env!("CARGO_PKG_VERSION")),
        "stdout: {stdout}"
    );
}

#[test]
fn vc_admin_answers_version_without_a_terminal() {
    let output = version(env!("CARGO_BIN_EXE_vc-admin"));
    assert_version_line(&output, "vc-admin");
}

#[test]
fn vc_procs_answers_version_without_a_terminal() {
    let output = version(env!("CARGO_BIN_EXE_vc-procs"));
    assert_version_line(&output, "vc-procs");
}
