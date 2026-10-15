"""Private JSONL and inert child seams for continuation usage ownership."""

import json

import pytest
from vibecrafted_core.spawn import _ClaudeTranscriptUsage

PARENT = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"


def event(root, sid=CHILD, *, uid="new", version="2.1.300", tokens=3):
    return {
        "type": "assistant",
        "uuid": uid,
        "sessionId": sid,
        "cwd": str(root),
        "version": version,
        "message": {
            "id": "msg-" + uid,
            "content": [{"type": "text", "text": "fixture"}],
            "usage": {"input_tokens": tokens, "output_tokens": 2},
        },
    }


def source(config, sid, events):
    path = config / "projects" / "fixture" / (sid + ".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


def reader(root, config, **kw):
    return _ClaudeTranscriptUsage(
        provider_session_id=CHILD,
        effective_root=str(root),
        provider_version="2.1.300",
        env={"CLAUDE_CONFIG_DIR": str(config)},
        **kw,
    )


def test_resume_counts_only_appended_current_usage(tmp_path):
    old = event(tmp_path, uid="old", version="2.1.296", tokens=100000)
    path = source(tmp_path / "config", CHILD, [old])
    baseline = path.read_bytes()
    usage = reader(tmp_path, tmp_path / "config", resume=True)
    assert usage.poll()["total_tokens"] == 0
    with path.open("a") as handle:
        handle.write(json.dumps(event(tmp_path)) + "\n")
    assert usage.poll()["total_tokens"] == 5
    assert path.read_bytes().startswith(baseline)


@pytest.mark.parametrize("mutation", ["replace", "truncate", "rewrite", "symlink"])
def test_resume_refuses_history_mutation(tmp_path, mutation):
    config = tmp_path / "config"
    path = source(config, CHILD, [event(tmp_path, uid="old")])
    usage = reader(tmp_path, config, resume=True)
    if mutation == "replace":
        path.rename(path.with_suffix(".bak"))
        source(config, CHILD, [event(tmp_path, uid="old")])
    elif mutation == "truncate":
        path.write_bytes(b"")
    elif mutation == "rewrite":
        path.write_bytes(path.read_bytes().replace(b"fixture", b"changed"))
    elif mutation == "symlink":
        backup = path.with_suffix(".bak")
        path.rename(backup)
        path.symlink_to(backup)
    with pytest.raises((RuntimeError, ValueError)):
        usage.poll()


def test_resume_refuses_missing_or_partial_source(tmp_path):
    with pytest.raises(ValueError, match="resume.*source"):
        reader(tmp_path, tmp_path / "config", resume=True)
    path = source(tmp_path / "config", CHILD, [event(tmp_path)])
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    with pytest.raises(ValueError, match="incomplete"):
        reader(tmp_path, tmp_path / "config", resume=True)


@pytest.mark.parametrize("rewrite_sid", [False, True])
def test_fork_excludes_verified_parent_history_before_validating_child_version(
    tmp_path, rewrite_sid
):
    config = tmp_path / "config"
    old = event(tmp_path, PARENT, uid="old", version="2.1.296", tokens=100000)
    source(config, PARENT, [old])
    usage = reader(tmp_path, config, fork_parent_session_id=PARENT)
    inherited = dict(old, sessionId=CHILD if rewrite_sid else PARENT, slug="child-slug")
    # Claude may recompute historical usage; immutable UUID/message contents establish inheritance.
    inherited["message"] = dict(old["message"], usage={"input_tokens": 999999})
    source(config, CHILD, [inherited, event(tmp_path)])
    assert usage.poll()["total_tokens"] == 5
    assert usage.confirmed_session_id == CHILD


def test_fork_refuses_modified_inherited_payload_and_new_wrong_version(tmp_path):
    config = tmp_path / "config"
    old = event(tmp_path, PARENT, uid="old", version="2.1.296")
    source(config, PARENT, [old])
    usage = reader(tmp_path, config, fork_parent_session_id=PARENT)
    inherited = dict(
        old, sessionId=CHILD, message=dict(old["message"], content=["tampered"])
    )
    source(config, CHILD, [inherited])
    with pytest.raises(RuntimeError, match="inherited"):
        usage.poll()
    source(config, CHILD, [])
    usage = reader(tmp_path, config, resume=True)
    with (config / "projects/fixture" / f"{CHILD}.jsonl").open("a") as handle:
        handle.write(json.dumps(event(tmp_path, version="2.1.296")) + "\n")
    with pytest.raises(RuntimeError, match="version differs"):
        usage.poll()


def test_fresh_session_collision_still_refuses(tmp_path):
    source(tmp_path / "config", CHILD, [event(tmp_path)])
    with pytest.raises(ValueError, match="already exists"):
        reader(tmp_path, tmp_path / "config")


def test_inert_fork_refuses_missing_parent_before_provider_start(tmp_path, monkeypatch):
    import os
    import sys

    from vibecrafted_core.spawn import launch_interactive_workspace

    repo = tmp_path / "repo"
    repo.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    started = tmp_path / "provider-started"
    fake = fakebin / "claude"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import pathlib,sys\n"
        "if '--help' in sys.argv:\n"
        " print('--resume --fork-session --print --session-id <uuid>'); sys.exit(0)\n"
        "if '--version' in sys.argv:\n"
        " print('2.1.300 (Claude Code)'); sys.exit(0)\n"
        f"pathlib.Path({str(started)!r}).write_text('started')\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "vc"))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(fakebin))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("PATH", str(fakebin) + os.pathsep + os.environ["PATH"])

    with pytest.raises(ValueError, match="fork parent usage source is missing"):
        launch_interactive_workspace(
            "claude",
            "fixture",
            "local-native",
            "read-only",
            repo,
            "unmetered",
            continuity="bare-fork",
            parent_session_id=PARENT,
            admission={},
        )
    assert not started.exists()


@pytest.mark.parametrize("mode", ["resume", "fork"])
@pytest.mark.parametrize("budget", ["unmetered", "10", "4"])
@pytest.mark.parametrize("operator", ["none", "auto"])
def test_inert_continuation_launch_preserves_sid_history_repo_and_own_usage(
    tmp_path, monkeypatch, mode, budget, operator
):
    import os
    import subprocess

    from vibecrafted_core.spawn import launch_interactive_workspace

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "baseline",
        ],
        check=True,
    )
    config = tmp_path / "config"
    home = tmp_path / "vc"
    sid = CHILD if mode == "resume" else PARENT
    parent = source(
        config, sid, [event(repo, sid, uid="old", version="2.1.296", tokens=100000)]
    )
    baseline = parent.read_bytes()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    fake = fakebin / "claude"
    fake.write_text("""#!/usr/bin/env python3
import json,os,pathlib,sys
if '--help' in sys.argv:
 print('--resume --fork-session --print --session-id <uuid>'); sys.exit(0)
if '--version' in sys.argv:
 print('2.1.300 (Claude Code)'); sys.exit(0)
args=sys.argv
if os.environ.get('VIBECRAFTED_AGENT_ROLE') == 'operator':
 import time
 child_meta=pathlib.Path(os.environ['VIBECRAFTED_SUPERVISED_CHILD_META'])
 protocol=pathlib.Path(os.environ['VIBECRAFTED_OPERATOR_PROTOCOL'])
 while True:
  child=json.loads(child_meta.read_text())
  if child.get('liveness')=='terminal':
   with protocol.open('a') as f:
    f.write(json.dumps(dict(kind='observation',actor_run_id=os.environ['VIBECRAFTED_RUN_ID'],
        child_run_id=child['run_id'],relation_id=child['supervision']['relation_id'],
        child_status=child['status'],child_worker_pid=child['worker_pid'],measured_usage=child['measured_usage']))+'\\n')
   sys.exit(0)
  time.sleep(.01)
sid=args[args.index('--resume')+1]
root=pathlib.Path(os.environ['CLAUDE_CONFIG_DIR'])/'projects'/'fixture'
parent=root/(sid+'.jsonl')
if '--fork-session' in args:
 sid=args[args.index('--session-id')+1]
 events=[dict(json.loads(line),sessionId=sid) for line in parent.read_text().splitlines()]
 target=root/(sid+'.jsonl')
 target.write_text(''.join(json.dumps(e)+'\\n' for e in events))
else: target=parent
with target.open('a') as f:
 f.write(json.dumps(dict(type='assistant',uuid='new',sessionId=sid,cwd=os.getcwd(),version='2.1.300',
                        message=dict(id='msg-new',usage=dict(input_tokens=3,output_tokens=2))))+'\\n')
pathlib.Path(os.environ['SMOKE_CAPTURE']).write_text(json.dumps(dict(sid=sid,argv=args,cwd=os.getcwd())))
if os.environ.get('BLOCK_AFTER_USAGE') == '1':
 import time
 while True: time.sleep(.01)
""")
    fake.chmod(0o755)
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(fakebin))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("SMOKE_CAPTURE", str(capture))
    monkeypatch.setenv("BLOCK_AFTER_USAGE", "1" if budget == "4" else "0")
    monkeypatch.setenv("PATH", str(fakebin) + os.pathsep + os.environ["PATH"])
    admission = {"skill": mode, "run_id": "rsme-private-fixture"}
    if mode == "resume":
        admission["agent_session_id"] = CHILD
    code = launch_interactive_workspace(
        "claude",
        "fixture",
        "local-native",
        "read-only",
        repo,
        budget,
        continuity="bare-fork" if mode == "fork" else "fresh",
        parent_session_id=PARENT if mode == "fork" else "",
        admission=admission,
        operator=operator,
    )
    assert code == (75 if budget == "4" else 0)
    receipt = json.loads(
        (home / "control_plane/runtime_runs/rsme-private-fixture/meta.json").read_text()
    )
    observed = json.loads(capture.read_text())
    assert observed["cwd"] == str(repo)
    assert receipt["root"] == str(repo)
    assert receipt["agent_session_id"] == observed["sid"]
    assert receipt["native_child_session_id"] == observed["sid"]
    assert receipt["measured_usage"]["total_tokens"] == 5
    assert receipt["status"] == ("quota_exhausted" if budget == "4" else "completed")
    if mode == "fork":
        assert observed["sid"] != PARENT
        assert parent.read_bytes() == baseline
        assert receipt["native_identity_status"] == "confirmed"
    else:
        assert observed["sid"] == CHILD
        assert parent.read_bytes().startswith(baseline)
        assert "--session-id" not in observed["argv"]


def test_resume_refuses_duplicate_session_sources_at_admission(tmp_path):
    config = tmp_path / "config"
    source(config, CHILD, [event(tmp_path)])
    source(config / "projects", CHILD, [event(tmp_path)])
    with pytest.raises(RuntimeError, match="multiple provider usage sources"):
        reader(tmp_path, config, resume=True)


def test_resume_excludes_exact_historical_replay_before_version_validation(tmp_path):
    config = tmp_path / "config"
    old = event(tmp_path, uid="old", version="2.1.296", tokens=100000)
    path = source(config, CHILD, [old])
    usage = reader(tmp_path, config, resume=True)
    with path.open("a") as handle:
        handle.write(json.dumps(old) + "\n")
    assert usage.poll()["total_tokens"] == 0
    with path.open("a") as handle:
        handle.write(json.dumps(event(tmp_path)) + "\n")
    assert usage.poll()["total_tokens"] == 5


@pytest.mark.parametrize(
    "mutation",
    ["content", "uuid", "message_id", "session", "version", "usage", "empty_session"],
)
def test_resume_refuses_tampered_historical_replay(tmp_path, mutation):
    config = tmp_path / "config"
    old = event(tmp_path, uid="old", version="2.1.296")
    path = source(config, CHILD, [old])
    usage = reader(tmp_path, config, resume=True)
    replay = json.loads(json.dumps(old))
    if mutation == "content":
        replay["message"]["content"] = ["tampered"]
    elif mutation == "uuid":
        replay["uuid"] = "changed"
        replay["version"] = "2.1.300"
    elif mutation == "message_id":
        replay["message"]["id"] = "changed"
    elif mutation == "session":
        replay["sessionId"] = PARENT
    elif mutation == "empty_session":
        replay["sessionId"] = ""
    elif mutation == "usage":
        replay["message"]["usage"]["input_tokens"] = 999999
    else:
        replay["version"] = "2.1.300"
    with path.open("a") as handle:
        handle.write(json.dumps(replay) + "\n")
    with pytest.raises(RuntimeError, match="inherited|historical|foreign session"):
        usage.poll()


@pytest.mark.parametrize("rewritten", [False, True])
def test_fork_confirmation_distinguishes_parent_copy_from_child_ack(
    tmp_path, rewritten
):
    config = tmp_path / "config"
    old = event(tmp_path, PARENT, uid="old", version="2.1.296")
    source(config, PARENT, [old])
    usage = reader(tmp_path, config, fork_parent_session_id=PARENT)
    source(config, CHILD, [dict(old, sessionId=CHILD if rewritten else PARENT)])
    assert usage.poll()["total_tokens"] == 0
    receipt = {"native_fork": True, "native_identity_status": "pending"}
    assert usage.confirm_fork_receipt(receipt) is rewritten
    assert receipt.get("native_child_session_id", "") == (CHILD if rewritten else "")
