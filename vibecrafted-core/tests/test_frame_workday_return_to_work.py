"""Return-to-work delivery through disposable catalog, Frame IPC and providers.

No live Founder seat is queried. Frame is a stateful executable double; the
picker, WES, subprocess navigation and recorded resume owner are real.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest
from test_frame_workday_project_launcher_entry import CONFIG, Terminal, _load
from vibecrafted_core import workspace_catalog as catalog
from vibecrafted_core.control_plane import control_plane_home


@pytest.fixture
def return_runtime(tmp_path, monkeypatch):
    root = tmp_path / "first" / "same-name"
    root.mkdir(parents=True)
    foreign = tmp_path / "second" / "same-name"
    foreign.mkdir(parents=True)
    record = catalog.create_workspace(
        root=root, display_label="Yesterday's project", notes="Continue the saved draft"
    )
    catalog.create_workspace(root=foreign, display_label="Other same-name project")
    instance = catalog.materialize_instance(workspace_id=record.workspace_id, root=root)
    sockets = tmp_path / "sockets"
    catalog.record_runtime_session_attachment(
        workspace_id=record.workspace_id,
        workspace_instance_id=instance.workspace_instance_id,
        vibecrafted_session_id=instance.vibecrafted_session_id,
        runtime="vc-frame",
        runtime_session_id="custom-work-seat",
        state="live",
        socket_dir=str(sockets),
    )
    state = tmp_path / "frame.json"
    state.write_text(
        json.dumps(
            {
                "panes": [
                    {
                        "id": 41,
                        "is_plugin": False,
                        "exited": False,
                        "command": "codex resume exact-conversation",
                        "title": "Hand named conversation",
                    },
                    {
                        "id": 42,
                        "is_plugin": False,
                        "exited": False,
                        "command": "zsh -l",
                        "title": "Research shell",
                    },
                ]
            }
        )
    )
    log = tmp_path / "actions.jsonl"
    binary = tmp_path / "bin"
    binary.mkdir()
    frame = binary / "vc-frame"
    frame.write_text(
        f"#!{sys.executable}\n"
        + f"""
import json,os,pathlib,subprocess,sys
state=pathlib.Path({str(state)!r})
args=sys.argv[1:]
with pathlib.Path({str(log)!r}).open('a') as f:
    f.write(json.dumps(dict(argv=args,socket=os.environ.get('VC_FRAME_SOCKET_DIR'))) + '\\n')
if args[0]=='list-sessions':
    print('custom-work-seat')
elif 'list-panes' in args:
    print(state.read_text())
elif 'new-tab' in args:
    command=args[args.index('--')+1:]
    subprocess.run(command,check=True,cwd=args[args.index('--cwd')+1])
"""
    )
    frame.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary}:/usr/bin:/bin")
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "lobby")
    monkeypatch.setenv("VC_FRAME_SOCKET_DIR", str(sockets))
    monkeypatch.chdir(foreign)
    start = _load("vc-start-here")
    monkeypatch.setattr(start, "probe_readiness", lambda: ("ready", "fixture"))
    ui = start.StartHere(SimpleNamespace())
    return SimpleNamespace(
        root=root,
        foreign=foreign,
        record=record,
        instance=instance,
        start=start,
        ui=ui,
        state=state,
        log=log,
        binary=binary,
        sockets=sockets,
        tmp=tmp_path,
    )


def _actions(runtime):
    return [json.loads(line) for line in runtime.log.read_text().splitlines()]


def test_reopen_project_preserves_conversation_and_shell(return_runtime):
    r = return_runtime
    shell = subprocess.Popen(
        ["/bin/bash", "--noprofile", "--norc"],
        cwd=r.root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    conversation = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "import sys; identity='exact-conversation'; [print(identity+':'+line.strip(),flush=True) for line in sys.stdin]",
        ],
        cwd=r.root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    processes = [conversation, shell]
    shell.stdin.write("WORKDAY_MARKER=kept; printf 'ready\\n'\n")
    shell.stdin.flush()
    assert shell.stdout.readline().strip() == "ready"
    payload = json.loads(r.state.read_text())
    payload["panes"][0]["pid"] = conversation.pid
    payload["panes"][1]["pid"] = shell.pid
    r.state.write_text(json.dumps(payload))
    try:
        before = catalog.read_catalog().to_payload()
        r.ui.open_recent_projects()
        assert any(r.record.canonical_root in a.detail for a in r.ui.return_actions)
        r.ui.open_return_project(r.record.workspace_id)
        assert not r.ui.error, r.ui.error
        assert len([a for a in r.ui.return_actions if a.key.startswith("pane:")]) == 2
        for key in ("workspace", "pane:41", "pane:42"):
            r.ui.activate_return(key)
        r.ui.open_return_project(r.record.workspace_id)
        commands = [a["argv"] for a in _actions(r)]
        assert [
            "--session",
            "lobby",
            "action",
            "switch-session",
            "custom-work-seat",
            "--pane-id",
            "terminal_41",
        ] in commands
        assert [
            "--session",
            "lobby",
            "action",
            "switch-session",
            "custom-work-seat",
            "--pane-id",
            "terminal_42",
        ] in commands
        assert not any("new-tab" in c or "init" in c or "resume" in c for c in commands)
        assert all(p.poll() is None for p in processes)
        shell.stdin.write('printf \'%s|%s\\n\' "$WORKDAY_MARKER" "$PWD"\n')
        shell.stdin.flush()
        assert shell.stdout.readline().strip() == "kept|" + str(r.root)
        conversation.stdin.write("continue\n")
        conversation.stdin.flush()
        assert conversation.stdout.readline().strip() == "exact-conversation:continue"
        assert json.loads(r.state.read_text()) == payload
        assert catalog.read_catalog().to_payload() == before
        assert all(a["socket"] == str(r.sockets) for a in _actions(r))
    finally:
        for p in processes:
            p.terminate()
            p.wait(timeout=5)


def _record_closed(r, provider, run_id, root, session, **extra):
    meta = {
        "run_id": run_id,
        "agent": provider,
        "root": str(root),
        "parent_root": str(root),
        "mode": "interactive",
        "requires_pty": True,
        "status": "completed",
        "agent_session_id": session,
        "native_identity_status": "verified",
        "completed_at": "2026-10-09T18:00:00Z",
        "runtime_class": "living-tree",
    }
    meta.update(extra)
    path = control_plane_home() / "runtime_runs" / run_id / "meta.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta))
    return meta


def test_resume_uses_exact_provider_session(return_runtime, monkeypatch):
    r = return_runtime
    r.state.write_text(json.dumps({"panes": []}))
    session = "019a0000-0000-7000-8000-00000000c0de"
    for args in (
        ("init", "-q"),
        ("config", "user.name", "Fixture"),
        ("config", "user.email", "fixture@example.invalid"),
    ):
        subprocess.run(["git", "-C", str(r.root), *args], check=True)
    (r.root / "seed").write_text("saved work")
    subprocess.run(["git", "-C", str(r.root), "add", "seed"], check=True)
    subprocess.run(["git", "-C", str(r.root), "commit", "-qm", "fixture"], check=True)
    parent = _record_closed(r, "codex", "init-return-exact", r.root, session)
    _record_closed(r, "codex", "init-foreign", r.foreign, "foreign-session")
    _record_closed(r, "claude", "init-other-provider", r.foreign, session)
    _record_closed(
        r,
        "codex",
        "init-unproven",
        r.root,
        "requested-only",
        native_identity_status="pending",
    )
    capture = r.tmp / "provider.json"
    provider = r.binary / "codex"
    provider.write_text(
        f"#!{sys.executable}\nimport json,os,pathlib,sys\n"
        f"pathlib.Path({str(capture)!r}).write_text(json.dumps(dict(argv=sys.argv[1:],root=os.getcwd())))\n"
    )
    provider.chmod(0o755)
    # The canonical admitted resume owner runs for real; only historical lookup
    # and provider transport are fixture-owned, with no subscription turn.
    deck = r.binary / "vibecrafted"
    deck.write_text(
        f"#!{sys.executable}\n"
        + f"""
import json,sys,subprocess
from pathlib import Path
sys.path.insert(0,{str(CONFIG.parents[2])!r})
from vibecrafted_core import spawn,workflow
parent={parent!r}
workflow.lookup_run=lambda run: parent if run==parent['run_id'] else None
workflow._native_resume_meta=lambda *a: {{}}
workflow._worker_process_alive=lambda *a: False
spawn.resolve_provider_usage_capability=lambda *a,**k: spawn.ProviderUsageCapability('codex',False,reason='fixture')
args=sys.argv[1:]
assert args[0:2]==['resume','codex']
command=spawn.interactive_workspace_command('codex','','local-native','bypass','',token_budget='unmetered',skill='resume',resume_run_id=args[args.index('--run-id')+1])
admission=json.loads(Path(command[-1]).read_text())
raise SystemExit(spawn.launch_interactive_workspace('codex','','local-native','bypass',parent['root'],token_budget='unmetered',admission=admission))
"""
    )
    deck.chmod(0o755)
    r.ui.open_return_project(r.record.workspace_id)
    assert not r.ui.error, r.ui.error
    choices = [a for a in r.ui.return_actions if a.key.startswith("resume:")]
    assert [a.key for a in choices] == ["resume:init-return-exact"]
    assert session in choices[0].title
    assert "2026-10-09" in choices[0].detail
    r.ui.activate_return(choices[0].key)
    assert not capture.exists()  # explicit recovery confirmation first
    r.ui.activate_return(choices[0].key)
    assert r.ui.error.startswith("Opened Resume codex"), r.ui.error
    actual = json.loads(capture.read_text())
    assert session in actual["argv"] and "resume" in actual["argv"]
    assert (
        "foreign-session" not in actual["argv"]
        and "requested-only" not in actual["argv"]
    )
    assert actual["root"] == str(r.root.resolve())


def test_missing_project_is_explained_without_spawn(return_runtime):
    r = return_runtime
    r.root.rmdir()
    before = catalog.read_catalog().to_payload()
    r.ui.open_return_project(r.record.workspace_id)
    assert "missing" in r.ui.error.lower()
    assert "Open project" in r.ui.error
    assert not r.log.exists()
    assert catalog.read_catalog().to_payload() == before
    assert not r.root.exists()


def test_return_picker_product_path(return_runtime):
    r = return_runtime
    terminal = Terminal(
        shlex.join([sys.executable, str(CONFIG / "vc-start-here.py")]),
        dict(os.environ, TERM="xterm-256color"),
        r.foreign,
    )
    try:
        terminal.expect("Recent projects")
        terminal.send(b"6")
        terminal.expect("RECENT PROJECTS")
        terminal.expect("Yesterday's project")
        terminal.send(b"j")
        terminal.expect("Continue the saved")
        terminal.expect("draft")  # wrapping emits a terminal cursor move between words
        terminal.send(b"\n")  # choose the exact first project, not same basename
        terminal.expect("TURN TO WORK")
        terminal.expect("Open Hand named conversation")
        terminal.send(b"j\n")
        terminal.expect("Opened Open Hand named conversation")
        terminal.send(b"j\n")
        terminal.expect("Research shell")
        terminal.send(b"qq")
        assert terminal.finish() == 0
        commands = [a["argv"] for a in _actions(r)]
        assert any("terminal_41" in c for c in commands)
        assert any("terminal_42" in c for c in commands)
        assert not any("new-tab" in c for c in commands)
    finally:
        terminal.close()


def test_closed_shell_requires_explicit_recovery(return_runtime):
    r = return_runtime
    panes = json.loads(r.state.read_text())
    panes["panes"][1]["exited"] = True
    r.state.write_text(json.dumps(panes))
    r.ui.open_return_project(r.record.workspace_id)
    assert any(a.key == "recover-shell" for a in r.ui.return_actions)
    r.ui.activate_return("recover-shell")
    assert "Enter again" in r.ui.error
    assert not any("new-tab" in a["argv"] for a in _actions(r))
    # A shell reappearing while the choice is pending cancels recovery.
    panes["panes"][1]["exited"] = False
    r.state.write_text(json.dumps(panes))
    r.ui.activate_return("recover-shell")
    assert "no longer" in r.ui.error
    assert not any("new-tab" in a["argv"] for a in _actions(r))


def test_no_duplicate_resume_beside_live_conversation(return_runtime):
    r = return_runtime
    _record_closed(r, "codex", "init-older", r.root, "older-proven-session")
    r.ui.open_return_project(r.record.workspace_id)
    assert any(a.key == "pane:41" for a in r.ui.return_actions)
    assert not any(a.key.startswith("resume:") for a in r.ui.return_actions)


def test_catalog_attachment_ambiguity_is_not_a_guess(return_runtime):
    r = return_runtime
    catalog.record_runtime_session_attachment(
        workspace_id=r.record.workspace_id,
        workspace_instance_id=r.instance.workspace_instance_id,
        vibecrafted_session_id=r.instance.vibecrafted_session_id,
        runtime="vc-frame",
        runtime_session_id="other-open-seat",
        state="live",
    )
    r.ui.open_return_project(r.record.workspace_id)
    assert "Multiple workspaces" in r.ui.error
    assert not r.log.exists()


def test_unknown_pane_state_still_prevents_duplicate_resume(return_runtime):
    r = return_runtime
    payload = json.loads(r.state.read_text())
    for pane in payload["panes"]:
        pane.pop("exited")
    payload["panes"][1]["command"] = "bash -lc 'printf ready; exec zsh -l'"
    r.state.write_text(json.dumps(payload))
    _record_closed(r, "codex", "init-older-unknown", r.root, "proven-closed")
    r.ui.open_return_project(r.record.workspace_id)
    assert [a.key for a in r.ui.return_actions] == ["workspace", "pane:41", "pane:42"]
    assert "unknown" in r.ui.return_actions[1].detail


def test_drifted_recovery_identity_needs_review(return_runtime):
    r = return_runtime
    r.state.write_text(json.dumps({"panes": []}))
    parent = _record_closed(r, "codex", "init-return-drift", r.root, "original-session")
    r.ui.open_return_project(r.record.workspace_id)
    r.ui.activate_return("resume:init-return-drift")
    assert "Enter again" in r.ui.error
    parent["agent_session_id"] = "different-session"
    meta = control_plane_home() / "runtime_runs/init-return-drift/meta.json"
    meta.write_text(json.dumps(parent))
    r.ui.activate_return("resume:init-return-drift")
    assert "changed" in r.ui.error
    assert not any("new-tab" in a["argv"] for a in _actions(r))


def test_saved_context_and_no_context_are_honest(return_runtime):
    r = return_runtime
    r.ui.open_recent_projects()
    details = {a.key: a.detail for a in r.ui.return_actions}
    assert (
        "Last saved context: Continue the saved draft"
        in details["project:" + r.record.workspace_id]
    )
    assert any("No reliable last context" in detail for detail in details.values())


def test_closed_shell_recovery_preserves_conversation(return_runtime):
    r = return_runtime
    payload = json.loads(r.state.read_text())
    payload["panes"][1]["exited"] = True
    r.state.write_text(json.dumps(payload))
    capture = r.tmp / "shell.json"
    shell = r.binary / "zsh"
    shell.write_text(
        f"#!{sys.executable}\nimport json,os,pathlib\n"
        f"pathlib.Path({str(capture)!r}).write_text(json.dumps(dict(root=os.getcwd())))\n"
    )
    shell.chmod(0o755)
    r.ui.open_return_project(r.record.workspace_id)
    r.ui.activate_return("recover-shell")
    assert not capture.exists()
    r.ui.activate_return("recover-shell")
    assert json.loads(capture.read_text())["root"] == str(r.root)
    assert json.loads(r.state.read_text()) == payload
    creations = [a["argv"] for a in _actions(r) if "new-tab" in a["argv"]]
    assert len(creations) == 1
    assert creations[0][:2] == ["--session", "custom-work-seat"]
    assert not any("resume" in a["argv"] or "init" in a["argv"] for a in _actions(r))


def test_unclassified_terminal_is_not_permission_to_resume(return_runtime):
    r = return_runtime
    payload = json.loads(r.state.read_text())
    payload["panes"][0]["command"] = ""
    r.state.write_text(json.dumps(payload))
    _record_closed(r, "codex", "init-unclassified", r.root, "proven-closed")
    r.ui.open_return_project(r.record.workspace_id)
    assert any(a.key == "pane:41" for a in r.ui.return_actions)
    assert not any(a.key.startswith("resume:") for a in r.ui.return_actions)
    assert "identity is unavailable" in r.ui.return_actions[0].detail
