#![cfg(unix)]

use serde_json::json;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};
use tempfile::{Builder, TempDir};
use voc::goto_work::{GotoWorkError, goto_work};
use voc::home::{HomeScope, project_home};
use voc::state::ControlPlaneState;

const WORKSPACE_ALPHA: &str = "0198f84e-1000-7abc-8def-1234567890ab";
const WORKSPACE_BETA: &str = "0198f84e-1001-7abc-8def-1234567890ab";
const INSTANCE_ALPHA: &str = "0198f84e-3000-7abc-8def-1234567890ab";
const INSTANCE_BETA: &str = "0198f84e-3001-7abc-8def-1234567890ab";
const SESSION_ALPHA: &str = "0198f84e-2000-7abc-8def-1234567890ab";
const SESSION_MISSING: &str = "0198f84e-2002-7abc-8def-1234567890ab";

struct Fixture {
    _home: TempDir,
    _socket_roots: Vec<TempDir>,
    _listeners: Vec<UnixListener>,
    control_plane: PathBuf,
    fake_frame: PathBuf,
}

#[test]
fn two_workspaces_three_runs_route_exactly_and_refuse_wrong_destinations() {
    let fixture = fixture();
    let state = ControlPlaneState::load(&fixture.control_plane).expect("load control-core view");
    assert_eq!(state.runs.len(), 3, "fixture exposes all three runs");
    let rows = project_home(&state, HomeScope::Global, Path::new("/work/alpha"));
    let alpha = rows
        .iter()
        .find(|row| row.run_id == "run-alpha")
        .expect("alpha row");
    assert_eq!(alpha.agent, "codex");
    assert_eq!(alpha.workspace, "Alpha Place");
    assert_eq!(alpha.frame_session, "frame-alpha");

    let command_deck = Path::new("/opt/vibecrafted/bin/vibecrafted");
    let receipt = goto_work(
        &fixture.control_plane,
        "run-alpha",
        &fixture.fake_frame,
        command_deck,
    )
    .expect("exact route opens and confirms one tab");
    assert_eq!(receipt.workspace_id, WORKSPACE_ALPHA);
    assert_eq!(receipt.frame_session, "frame-alpha");
    assert_eq!(receipt.tab_id, 41);

    let capture_path = fixture.fake_frame.with_extension("capture");
    let capture = fs::read_to_string(&capture_path).expect("fake argv capture");
    assert!(
        capture.contains("ARG=--session\nARG=frame-alpha\n"),
        "exact Frame target must be an argv boundary:\n{capture}"
    );
    assert!(
        capture.contains(&format!("SOCKET={}\n", receipt.socket_dir)),
        "exact socket root must cross in the environment:\n{capture}"
    );
    assert!(capture.contains("ARG=/opt/vibecrafted/bin/vibecrafted\n"));
    assert!(capture.contains("ARG=resume\nARG=codex\nARG=--session\nARG=provider-alpha\n"));
    assert_eq!(
        capture.matches("ARG=list-tabs\nARG=--json\n").count(),
        2,
        "preflight and post-mutation registries must both be read"
    );

    let calls_before_refusals = capture.matches("CALL\n").count();
    let wrong = goto_work(
        &fixture.control_plane,
        "run-wrong",
        &fixture.fake_frame,
        command_deck,
    )
    .expect_err("wrong Frame target must refuse");
    assert!(matches!(wrong, GotoWorkError::Refused(_)));
    assert!(wrong.to_string().contains("routing drift"));

    let missing = goto_work(
        &fixture.control_plane,
        "run-missing",
        &fixture.fake_frame,
        command_deck,
    )
    .expect_err("missing live Frame must not spawn");
    assert!(matches!(missing, GotoWorkError::Refused(_)));
    assert!(missing.to_string().contains("vc-start --repo /work/beta"));
    assert!(missing.to_string().contains("goto-work did not spawn"));

    let duplicate_socket = socket_root("alpha-duplicate");
    let _duplicate_listener = bind_frame(&duplicate_socket, "frame-alpha-shadow");
    write_session(
        &fixture.control_plane,
        "alpha-duplicate.json",
        SESSION_ALPHA,
        WORKSPACE_ALPHA,
        INSTANCE_ALPHA,
        "frame-alpha-shadow",
        duplicate_socket.path(),
    );
    let ambiguous = goto_work(
        &fixture.control_plane,
        "run-alpha",
        &fixture.fake_frame,
        command_deck,
    )
    .expect_err("different live Frames for one logical session must refuse");
    assert!(matches!(ambiguous, GotoWorkError::Refused(_)));
    assert!(ambiguous.to_string().contains("ambiguous destination"));

    let capture_after_refusals = fs::read_to_string(&capture_path).expect("capture after refusal");
    assert_eq!(
        capture_after_refusals.matches("CALL\n").count(),
        calls_before_refusals,
        "ambiguous and missing routes must not invoke vc-frame"
    );
}

#[test]
fn mutation_without_canonical_tab_registration_is_unconfirmed() {
    let fixture = fixture();
    fs::write(fixture.fake_frame.with_extension("omit-confirm"), b"").expect("omit-confirm marker");

    let error = goto_work(
        &fixture.control_plane,
        "run-alpha",
        &fixture.fake_frame,
        Path::new("/opt/vibecrafted/bin/vibecrafted"),
    )
    .expect_err("a tab absent from canonical registration cannot be success");

    assert!(matches!(error, GotoWorkError::Unconfirmed(_)));
    assert!(
        error
            .to_string()
            .contains("canonical registry has no tab id 41")
    );
    assert!(error.to_string().contains("do not retry blindly"));
}

#[test]
fn nonzero_new_tab_result_is_unconfirmed_because_mutation_may_have_happened() {
    let fixture = fixture();
    fs::write(fixture.fake_frame.with_extension("fail-new-tab"), b"").expect("fail-new-tab marker");

    let error = goto_work(
        &fixture.control_plane,
        "run-alpha",
        &fixture.fake_frame,
        Path::new("/opt/vibecrafted/bin/vibecrafted"),
    )
    .expect_err("post-dispatch failure cannot prove absence of a new tab");

    assert!(matches!(error, GotoWorkError::Unconfirmed(_)));
    assert!(error.to_string().contains("new-tab failed after dispatch"));
    assert!(error.to_string().contains("do not retry blindly"));
}

fn fixture() -> Fixture {
    let home = Builder::new()
        .prefix("voc-goto-control-")
        .tempdir()
        .expect("fixture home");
    let control_plane = home.path().join("control_plane");
    fs::create_dir_all(control_plane.join("runtime_runs")).expect("runtime root");
    fs::create_dir_all(control_plane.join("workspaces/sessions")).expect("workspace registry");

    fs::write(
        control_plane.join("workspaces/catalog.json"),
        serde_json::to_vec(&json!({
            "schema": "vibecrafted.workspace-catalog.v1",
            "updated_at": "2026-09-22T12:00:00Z",
            "selected_workspace_id": WORKSPACE_ALPHA,
            "workspaces": {
                (WORKSPACE_ALPHA): workspace(WORKSPACE_ALPHA, "Alpha Place", "/work/alpha"),
                (WORKSPACE_BETA): workspace(WORKSPACE_BETA, "Beta Place", "/work/beta")
            }
        }))
        .expect("catalog json"),
    )
    .expect("catalog");

    let socket_alpha = socket_root("alpha");
    let socket_beta = socket_root("beta-missing");
    let listeners = vec![bind_frame(&socket_alpha, "frame-alpha")];
    write_session(
        &control_plane,
        "alpha.json",
        SESSION_ALPHA,
        WORKSPACE_ALPHA,
        INSTANCE_ALPHA,
        "frame-alpha",
        socket_alpha.path(),
    );
    write_session(
        &control_plane,
        "missing.json",
        SESSION_MISSING,
        WORKSPACE_BETA,
        INSTANCE_BETA,
        "frame-missing",
        socket_beta.path(),
    );

    write_run(
        &control_plane,
        "run-alpha",
        "codex",
        "provider-alpha",
        "/work/alpha",
        WORKSPACE_ALPHA,
        INSTANCE_ALPHA,
        "Alpha Place",
        SESSION_ALPHA,
        "frame-alpha",
    );
    write_run(
        &control_plane,
        "run-wrong",
        "claude",
        "provider-wrong",
        "/work/alpha",
        WORKSPACE_ALPHA,
        INSTANCE_ALPHA,
        "Alpha Place",
        SESSION_ALPHA,
        "frame-wrong",
    );
    write_run(
        &control_plane,
        "run-missing",
        "kimi",
        "provider-missing",
        "/work/beta",
        WORKSPACE_BETA,
        INSTANCE_BETA,
        "Beta Place",
        SESSION_MISSING,
        "frame-missing",
    );

    let fake_frame = home.path().join("fake-vc-frame");
    fs::write(&fake_frame, fake_frame_script()).expect("fake vc-frame");
    let mut permissions = fs::metadata(&fake_frame)
        .expect("fake metadata")
        .permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(&fake_frame, permissions).expect("fake executable");

    Fixture {
        _home: home,
        _socket_roots: vec![socket_alpha, socket_beta],
        _listeners: listeners,
        control_plane,
        fake_frame,
    }
}

fn workspace(id: &str, label: &str, root: &str) -> serde_json::Value {
    json!({
        "schema": "vibecrafted.workspace.v1",
        "workspace_id": id,
        "display_label": label,
        "canonical_root": root,
        "status": "active",
        "updated_at": "2026-09-22T12:00:00Z"
    })
}

fn socket_root(label: &str) -> TempDir {
    let root = Builder::new()
        .prefix(&format!("vg-{label}-"))
        .tempdir_in("/tmp")
        .expect("short socket root");
    fs::create_dir(root.path().join("contract_version_2")).expect("contract dir");
    root
}

fn bind_frame(root: &TempDir, name: &str) -> UnixListener {
    UnixListener::bind(root.path().join("contract_version_2").join(name)).expect("frame socket")
}

fn write_session(
    control_plane: &Path,
    filename: &str,
    session_id: &str,
    workspace_id: &str,
    workspace_instance_id: &str,
    frame_session: &str,
    socket_dir: &Path,
) {
    fs::write(
        control_plane.join("workspaces/sessions").join(filename),
        serde_json::to_vec(&json!({
            "schema": "vibecrafted.workspace-session.v1",
            "session_id": session_id,
            "workspace_id": workspace_id,
            "workspace_instance_id": workspace_instance_id,
            "updated_at": "2026-09-22T12:00:00Z",
            "attachments": [{
                "runtime": "vc-frame",
                "runtime_session_id": frame_session,
                "state": "live",
                "socket_dir": socket_dir,
                "updated_at": "2026-09-22T12:00:00Z"
            }]
        }))
        .expect("session json"),
    )
    .expect("session record");
}

#[allow(clippy::too_many_arguments)]
fn write_run(
    control_plane: &Path,
    run_id: &str,
    agent: &str,
    provider_session_id: &str,
    root: &str,
    workspace_id: &str,
    workspace_instance_id: &str,
    workspace_display_label: &str,
    workspace_session_id: &str,
    worker_host_session: &str,
) {
    let run = control_plane.join("runtime_runs").join(run_id);
    fs::create_dir(&run).expect("runtime run");
    fs::write(
        run.join("meta.json"),
        serde_json::to_vec(&json!({
            "run_id": run_id,
            "status": "running",
            "updated_at": chrono::Utc::now().to_rfc3339(),
            "started_at": chrono::Utc::now().to_rfc3339(),
            "agent": agent,
            "skill": "implement",
            "mode": "headless",
            "root": root,
            "provider_session_id": provider_session_id,
            "workspace_id": workspace_id,
            "workspace_instance_id": workspace_instance_id,
            "workspace_display_label": workspace_display_label,
            "vibecrafted_session_id": workspace_session_id,
            "worker_host_session": worker_host_session,
            "worker_host_display": worker_host_session
        }))
        .expect("run json"),
    )
    .expect("run meta");
}

fn fake_frame_script() -> &'static str {
    r#"#!/bin/sh
set -eu
capture="${0%.*}.capture"
state="${0%.*}.tab"
{
  printf 'CALL\n'
  printf 'SOCKET=%s\n' "${VC_FRAME_SOCKET_DIR:-}"
  for arg in "$@"; do printf 'ARG=%s\n' "$arg"; done
} >> "$capture"
mode=''
name=''
want_name=0
for arg in "$@"; do
  if [ "$want_name" -eq 1 ]; then name="$arg"; want_name=0; continue; fi
  case "$arg" in
    --name) want_name=1 ;;
    new-tab) mode='new' ;;
    list-tabs) mode='list' ;;
  esac
done
if [ "$mode" = 'new' ]; then
  printf '%s\n' "$name" > "$state"
  if [ -f "${0%.*}.fail-new-tab" ]; then
    printf 'simulated failure after mutation\n' >&2
    exit 70
  fi
  printf '41\n'
  exit 0
fi
if [ "$mode" = 'list' ]; then
  if [ -f "$state" ] && [ ! -f "${0%.*}.omit-confirm" ]; then
    IFS= read -r name < "$state"
    printf '[{"tab_id":1,"name":"home","position":0,"active":false,"other_focused_clients":[],"session_incarnation":"fixture-incarnation","tab_instance_id":"abcdef0123456789abcdef0123456789"},{"tab_id":41,"name":"%s","position":1,"active":true,"other_focused_clients":[],"session_incarnation":"fixture-incarnation","tab_instance_id":"0123456789abcdef0123456789abcdef"}]\n' "$name"
  else
    printf '[{"tab_id":1,"name":"home","position":0,"active":true,"other_focused_clients":[],"session_incarnation":"fixture-incarnation","tab_instance_id":"abcdef0123456789abcdef0123456789"}]\n'
  fi
  exit 0
fi
printf 'unexpected invocation\n' >&2
exit 64
"#
}
