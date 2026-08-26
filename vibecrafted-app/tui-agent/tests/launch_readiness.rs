//! Integration tests for `wait_for_interactive_launch`.
//!
//! Drives the readiness loop through a fake `vc_frame`-shaped shell script so
//! the operator-visible behavior (success / "session exited before probe" /
//! "session never appeared" / probe error preservation) is exercised end to
//! end instead of just verified at command-shape level. Closes vc-review
//! P2-03.

#![cfg(unix)]

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;
fn get_message(e: &voc::LaunchRunError) -> String {
    if let voc::LaunchRunError::Exec { message, .. } = e {
        message.clone()
    } else {
        panic!()
    }
}
fn get_probe_error(e: &voc::LaunchRunError) -> Option<String> {
    if let voc::LaunchRunError::Exec { probe_error, .. } = e {
        probe_error.clone()
    } else {
        panic!()
    }
}
fn get_probe_error_at_deadline(e: &voc::LaunchRunError) -> Option<String> {
    if let voc::LaunchRunError::Exec {
        probe_error_at_deadline,
        ..
    } = e
    {
        probe_error_at_deadline.clone()
    } else {
        panic!()
    }
}

use std::time::{Duration, Instant};

use tempfile::TempDir;
use voc::launch::LaunchCommand;
use voc::{READINESS_DEADLINE, wait_for_interactive_launch};

static ENV_LOCK: std::sync::OnceLock<std::sync::Mutex<()>> = std::sync::OnceLock::new();
fn env_guard() -> std::sync::MutexGuard<'static, ()> {
    ENV_LOCK
        .get_or_init(|| std::sync::Mutex::new(()))
        .lock()
        .unwrap_or_else(|err| err.into_inner())
}

#[derive(Clone, Copy)]
enum ChildBehavior {
    QuickSuccess,
    VisibleUntilReleased,
    AwaitDeadline,
}

#[derive(Clone, Copy)]
enum ProbeBehavior {
    Ok,
    Error,
}

struct FakeVcFrame {
    _tmp: TempDir,
    program: PathBuf,
    probe_observed_fifo: PathBuf,
    exit_fifo: PathBuf,
}

fn shell_quote(path: &Path) -> String {
    format!("'{}'", path.to_string_lossy().replace('\'', "'\"'\"'"))
}

fn create_fifo(path: &Path) {
    let output = Command::new("mkfifo")
        .arg(path)
        .output()
        .expect("run mkfifo for fake vc_frame handshake");
    assert!(
        output.status.success(),
        "mkfifo failed for {}: {}",
        path.display(),
        String::from_utf8_lossy(&output.stderr)
    );
}

fn fake_vc_frame(child_behavior: ChildBehavior, probe_behavior: ProbeBehavior) -> FakeVcFrame {
    let tmp = tempfile::tempdir().expect("tempdir");
    let program = tmp.path().join("vc_frame.sh");
    let visible_file = tmp.path().join("visible.txt");
    let probe_observed_fifo = tmp.path().join("probe-observed.fifo");
    let exit_fifo = tmp.path().join("exit.fifo");
    create_fifo(&probe_observed_fifo);
    create_fifo(&exit_fifo);

    let child_case = match child_behavior {
        ChildBehavior::QuickSuccess => "exit 0".to_string(),
        ChildBehavior::VisibleUntilReleased => format!(
            "printf '%s\\n' \"$NAME\" > {}; IFS= read -r _release < {}",
            shell_quote(&visible_file),
            shell_quote(&exit_fifo)
        ),
        ChildBehavior::AwaitDeadline => {
            format!("IFS= read -r _release < {}", shell_quote(&exit_fifo))
        }
    };
    let probe_case = match probe_behavior {
        ProbeBehavior::Ok => "exit 0",
        ProbeBehavior::Error => "echo 'probe config not found' >&2; exit 2",
    };
    let script = format!(
        r#"#!/bin/sh
VISIBLE_FILE={visible_file}
PROBE_OBSERVED_FIFO={probe_observed_fifo}
case "${{1:-}}" in
  list-sessions)
    if [ -f "$VISIBLE_FILE" ]; then
      cat "$VISIBLE_FILE"
      cat "$VISIBLE_FILE" > "$PROBE_OBSERVED_FIFO"
    fi
    {probe_case}
    ;;
  --session)
    NAME="$2"
    {child_case}
    ;;
  *) exit 0 ;;
esac
"#,
        visible_file = shell_quote(&visible_file),
        probe_observed_fifo = shell_quote(&probe_observed_fifo),
    );
    fs::write(&program, script).expect("write fake vc_frame");
    let mut perms = fs::metadata(&program).expect("metadata").permissions();
    perms.set_mode(0o755);
    fs::set_permissions(&program, perms).expect("chmod +x");
    FakeVcFrame {
        _tmp: tmp,
        program,
        probe_observed_fifo,
        exit_fifo,
    }
}

fn build_command(program: &Path, session: &str) -> LaunchCommand {
    LaunchCommand {
        program: program.to_path_buf(),
        args: vec![
            "--session".into(),
            session.into(),
            "--layout-string".into(),
            "noop".into(),
        ],
        env: Default::default(),
    }
}

fn run_visible_then_exit_case(session: &str) {
    let fake = fake_vc_frame(ChildBehavior::VisibleUntilReleased, ProbeBehavior::Ok);
    let command = build_command(&fake.program, session);
    let child = command
        .spawn_interactive_with_stderr()
        .expect("spawn fake vc_frame");
    let observed_fifo = fake.probe_observed_fifo.clone();
    let exit_fifo = fake.exit_fifo.clone();
    let expected_session = session.to_string();
    let handshake = std::thread::spawn(move || {
        let mut observed = String::new();
        File::open(&observed_fifo)
            .expect("open readiness acknowledgement")
            .read_to_string(&mut observed)
            .expect("read readiness acknowledgement");
        OpenOptions::new()
            .write(true)
            .open(&exit_fifo)
            .expect("open child exit signal")
            .write_all(b"exit\n")
            .expect("release visible fake child");
        assert_eq!(observed.trim(), expected_session);
    });

    let output = wait_for_interactive_launch(&command, child)
        .expect("visible session should converge to success");
    assert!(output.status.success(), "fake child should exit zero");
    handshake.join().expect("visible launch handshake");
}

#[test]
fn quick_child_exit_before_visibility_reports_session_exited() {
    let fake = fake_vc_frame(ChildBehavior::QuickSuccess, ProbeBehavior::Ok);
    let session = "vc-op-fake-quickexit";
    let command = build_command(&fake.program, session);
    let child = command
        .spawn_interactive_with_stderr()
        .expect("spawn fake vc_frame");
    let result = wait_for_interactive_launch(&command, child);
    let error = result.expect_err("quick-exit should fail readiness check");
    assert!(
        get_message(&error).contains("exited before the readiness probe saw it"),
        "unexpected message: {}",
        get_message(&error)
    );
    assert!(
        get_message(&error).contains(session),
        "session name must appear in the error: {}",
        get_message(&error)
    );
}

#[test]
fn visible_session_then_child_exits_returns_success() {
    run_visible_then_exit_case("vc-op-fake-visible");
}

#[test]
fn visible_before_exit_is_isolated_under_repetition_and_concurrency() {
    const WORKERS: usize = 8;
    const ITERATIONS_PER_WORKER: usize = 8;

    let workers: Vec<_> = (0..WORKERS)
        .map(|worker| {
            std::thread::spawn(move || {
                for iteration in 0..ITERATIONS_PER_WORKER {
                    run_visible_then_exit_case(&format!("vc-op-fake-visible-{worker}-{iteration}"));
                }
            })
        })
        .collect();
    for worker in workers {
        worker.join().expect("concurrent visible launch worker");
    }
}

#[test]
fn deadline_kills_child_when_session_never_visible() {
    let fake = fake_vc_frame(ChildBehavior::AwaitDeadline, ProbeBehavior::Ok);
    let session = "vc-op-fake-hang";
    let command = build_command(&fake.program, session);
    let child = command
        .spawn_interactive_with_stderr()
        .expect("spawn fake vc_frame");
    let started = Instant::now();
    let result = wait_for_interactive_launch(&command, child);
    let elapsed = started.elapsed();
    let error = result.expect_err("hanging child past deadline must be a failure");
    assert!(
        get_message(&error).contains("did not appear within"),
        "unexpected message: {}",
        get_message(&error)
    );
    assert!(
        get_message(&error).contains(session),
        "session name must appear in the error: {}",
        get_message(&error)
    );
    // Killing the directly blocked shell must release us soon after the
    // product deadline; there is no timed grandchild left to mask failure.
    assert!(
        elapsed < READINESS_DEADLINE + Duration::from_secs(5),
        "deadline test should reap the blocked fixture promptly: {elapsed:?}"
    );
}

#[test]
fn probe_failure_surfaces_in_launch_error() {
    let fake = fake_vc_frame(ChildBehavior::AwaitDeadline, ProbeBehavior::Error);
    let session = "vc-op-fake-probe-err";
    let command = build_command(&fake.program, session);
    let child = command
        .spawn_interactive_with_stderr()
        .expect("spawn fake vc_frame");
    let result = wait_for_interactive_launch(&command, child);
    let error = result.expect_err("probe error + hang must produce a failure");
    let probe_error = get_probe_error(&error)
        .clone()
        .expect("probe error must be preserved when probe exits non-zero with stderr");
    assert!(
        probe_error.contains("probe config not found"),
        "probe stderr should be surfaced verbatim: {probe_error}"
    );
    let deadline_probe = get_probe_error_at_deadline(&error)
        .clone()
        .expect("deadline kill must preserve the last probe diagnostic");
    assert!(
        deadline_probe.contains(&format!(
            "killed after {}ms",
            READINESS_DEADLINE.as_millis()
        )) && deadline_probe.contains("last probe error:")
            && deadline_probe.contains("probe config not found"),
        "deadline diagnostic should include kill timing and last probe error: {deadline_probe}"
    );
    // Detail lines render the probe diagnostic in the operator overlay.
    let detail = error.detail_lines("vc_frame ...".to_string());
    assert!(
        detail
            .iter()
            .any(|line| line.starts_with("readiness probe:")
                && line.contains("probe config not found")),
        "probe error must show in the overlay detail block: {detail:?}"
    );
    assert!(
        detail
            .iter()
            .any(|line| line.starts_with("readiness timeout probe:")
                && line.contains("probe config not found")),
        "deadline probe error must show in the overlay detail block: {detail:?}"
    );
}

#[test]
fn pre_launch_verify_passes_on_clean_config() {
    let _guard = env_guard();
    let dir = tempfile::tempdir().unwrap();
    unsafe {
        std::env::set_var("HOME", dir.path());
    }
    let socket_dir = dir.path().join(".rmcp-mux/ipc");
    std::fs::create_dir_all(&socket_dir).unwrap();
    let socket_path = socket_dir.join("control.sock");

    let listener = std::os::unix::net::UnixListener::bind(&socket_path).unwrap();

    std::thread::spawn(move || {
        if let Ok((mut stream, _)) = listener.accept() {
            use std::io::{BufRead, Write};
            let mut reader = std::io::BufReader::new(&stream);
            let mut line = String::new();
            if reader.read_line(&mut line).is_ok() {
                let resp = rmcp_mux::ipc::MuxControlResponse::VerifyResult(
                    rmcp_mux::ipc::command::VerifyResult {
                        ok: true,
                        non_mux_servers: vec![],
                    },
                );
                let payload = serde_json::to_string(&resp).unwrap();
                let _ = stream.write_all(format!("{payload}\n").as_bytes());
            }
        }
    });

    let res = voc::launch::pre_launch_verify(rmcp_mux::ipc::ClientKind::Codex);
    assert!(res.is_ok(), "Verify should pass");
}

#[test]
fn pre_launch_verify_blocks_dispatch_on_drift() {
    let _guard = env_guard();
    let dir = tempfile::tempdir().unwrap();
    unsafe {
        std::env::set_var("HOME", dir.path());
    }
    let socket_dir = dir.path().join(".rmcp-mux/ipc");
    std::fs::create_dir_all(&socket_dir).unwrap();
    let socket_path = socket_dir.join("control.sock");

    let listener = std::os::unix::net::UnixListener::bind(&socket_path).unwrap();

    std::thread::spawn(move || {
        if let Ok((mut stream, _)) = listener.accept() {
            use std::io::{BufRead, Write};
            let mut reader = std::io::BufReader::new(&stream);
            let mut line = String::new();
            if reader.read_line(&mut line).is_ok() {
                let resp = rmcp_mux::ipc::MuxControlResponse::VerifyResult(
                    rmcp_mux::ipc::command::VerifyResult {
                        ok: false,
                        non_mux_servers: vec![rmcp_mux::ipc::command::NonMuxEntry {
                            client: "codex".into(),
                            path: "/tmp/config".into(),
                            line: 12,
                            server_name: "codex".into(),
                        }],
                    },
                );
                let payload = serde_json::to_string(&resp).unwrap();
                let _ = stream.write_all(format!("{payload}\n").as_bytes());
            }
        }
    });

    let res = voc::launch::pre_launch_verify(rmcp_mux::ipc::ClientKind::Codex);
    let err = res.expect_err("Should block dispatch");
    match err {
        voc::launch::VerifyHalt::Drift(servers) => {
            assert_eq!(servers.len(), 1);
            assert_eq!(servers[0].client, "codex");
        }
        _ => panic!("Expected Drift error"),
    }
}

#[test]
fn pre_launch_verify_falls_back_to_polling_when_socket_down() {
    let _guard = env_guard();
    let dir = tempfile::tempdir().unwrap();
    unsafe {
        std::env::set_var("HOME", dir.path());
    }
    // Socket doesn't exist. Should return Ok(()).
    let res = voc::launch::pre_launch_verify(rmcp_mux::ipc::ClientKind::Codex);
    assert!(
        res.is_ok(),
        "Verify should fall back gracefully if socket is down"
    );
}

#[test]
fn client_drift_overlay_carries_non_mux_paths_to_fix_action() {
    let halt = voc::launch::VerifyHalt::Drift(vec![rmcp_mux::ipc::command::NonMuxEntry {
        client: "claude".into(),
        path: "/Users/x/.claude/config.toml".into(),
        line: 42,
        server_name: "claude".into(),
    }]);
    let err = voc::LaunchRunError::ClientDrift(halt);
    let details = err.detail_lines("".into());
    assert!(
        details
            .iter()
            .any(|l| l.contains("Client drift detected. Dispatch halted."))
    );
    assert!(
        details
            .iter()
            .any(|l| l.contains("/Users/x/.claude/config.toml:42"))
    );
    assert!(details.iter().any(|l| l.contains("Press F to auto-fix")));
}
