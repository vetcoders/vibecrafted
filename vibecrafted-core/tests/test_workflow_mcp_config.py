"""Strict MCP admission and native continuation at the canonical workflow boundary."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from vibecrafted_core import cli, workflow


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
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
    return root


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    path = tmp_path / "private profile.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "private-mock": {
                        "type": "http",
                        "url": "https://mock.example.invalid/mcp",
                        "headers": {"Authorization": "Bearer PRIVATE-MCP-SECRET"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repository: Path):
    home = tmp_path / "operator-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("VIBECRAFTED_GUARD", "0")
    monkeypatch.setenv("VIBECRAFTED_LAUNCH_IDEMPOTENCY", "0")
    monkeypatch.setattr(workflow, "_sweep_stale_runs", lambda: None)
    monkeypatch.setattr(workflow, "_artifact_org_repo", lambda _: None)
    monkeypatch.setattr(
        workflow, "_resolve_agent_command", lambda _agent, command, _env: list(command)
    )
    events = []
    monkeypatch.setattr(workflow, "append_event", lambda **event: events.append(event))
    monkeypatch.setattr(workflow, "process_identity_receipt", lambda *a, **k: None)
    launches = []
    real_popen = subprocess.Popen

    def launch(command, **kwargs):
        if "vibecrafted_core.dispatcher" not in command:
            return real_popen(command, **kwargs)
        launches.append({"command": list(command), "environment": dict(kwargs["env"])})
        return SimpleNamespace(pid=424242)

    monkeypatch.setattr(workflow.subprocess, "Popen", launch)
    return SimpleNamespace(root=repository, home=home, launches=launches, events=events)


def _spec(context, profile: Path | None = None, **changes):
    return workflow.normalize_launch_spec(
        {
            "agent": "claude",
            "skill": "workflow",
            "prompt": "bounded task",
            "root": str(context.root),
            "runtime": "headless",
            "mcp_config": str(profile) if profile else "",
            **changes,
        },
        context.root,
    )


def test_cli_accepts_explicit_mcp_profile_and_documents_strict_scope(
    runtime, profile, monkeypatch, capsys
):
    seen = []

    def launch(spec, _source):
        seen.append(spec)
        return {"accepted": True, "run_id": "work-private", "status": "launching"}

    monkeypatch.setattr(cli, "launch_workflow", launch)
    assert (
        cli.main(
            [
                "workflow",
                "claude",
                "--repo",
                str(runtime.root),
                "--prompt",
                "task",
                "--mcp-config",
                str(profile),
                "--runtime",
                "headless",
                "--json",
            ]
        )
        == 0
    )
    assert seen[0].mcp_config == str(profile.resolve())
    capsys.readouterr()
    assert cli.main(["workflow", "--help"]) == 0
    help_text = capsys.readouterr().out
    assert "--mcp-config <PATH.json>" in help_text
    assert "frozen on native resume/fork/retry" in help_text


@pytest.mark.parametrize("contents", [None, "{PRIVATE-MCP-SECRET", "[]", "{}"])
def test_invalid_mcp_profile_refuses_before_launch_mutations(
    runtime, tmp_path, monkeypatch, capsys, contents
):
    bad = tmp_path / "invalid.json"
    if contents is not None:
        bad.write_text(contents, encoding="utf-8")

    def forbidden(*args, **kwargs):
        pytest.fail("invalid MCP input crossed the launch mutation boundary")

    monkeypatch.setattr(workflow, "_sweep_stale_runs", forbidden)
    monkeypatch.setattr(workflow, "_prepare_launch_worktree", forbidden)
    monkeypatch.setattr(cli, "launch_workflow", forbidden)
    assert (
        cli.main(
            [
                "workflow",
                "claude",
                "--repo",
                str(runtime.root),
                "--prompt",
                "task",
                "--worktree",
                "true",
                "--mcp-config",
                str(bad),
                "--json",
            ]
        )
        == 2
    )
    assert "PRIVATE-MCP-SECRET" not in capsys.readouterr().err
    direct = workflow.WorkflowLaunchSpec(
        agent="claude",
        mode="workflow",
        skill="workflow",
        prompt="task",
        file="",
        root=str(runtime.root),
        runtime="headless",
        worktree=True,
        mcp_config=str(bad),
    )
    with pytest.raises(ValueError):
        workflow.launch_workflow(direct, runtime.root)
    assert runtime.launches == []


@pytest.mark.parametrize("skill", ["research", "marbles"])
def test_supervised_runtime_refuses_unenforceable_mcp_scope(runtime, profile, skill):
    with pytest.raises(ValueError, match="direct agent workflow"):
        _spec(runtime, profile, skill=skill)
    assert runtime.launches == []


@pytest.mark.parametrize("agent", ["claude", "codex", "agy"])
def test_native_handoff_is_private_and_durable_without_changing_home(
    runtime, profile, agent
):
    admitted = _spec(runtime, profile, agent=agent, model="exact-provider-model")
    original_command = workflow.build_launch_command(admitted, runtime.root)
    original_digest = hashlib.sha256(profile.read_bytes()).hexdigest()
    result = workflow.launch_workflow(admitted, runtime.root)
    assert result["accepted"], result
    child = runtime.launches[-1]
    assert child["environment"]["HOME"] == str(runtime.home)
    assert os.environ["HOME"] == str(runtime.home)
    assert "exact-provider-model" in result["worker_command"]
    receipt = result["mcp_isolation"]
    assert receipt["strict"] is True
    assert receipt["sha256"] == original_digest
    assert receipt["server_names"] == ["private-mock"]
    frozen = Path(receipt["config_path"])
    assert frozen != profile
    assert frozen.read_bytes() == profile.read_bytes()
    if agent == "claude":
        command = result["worker_command"]
        index = command.index("--mcp-config")
        assert command[index + 1 : index + 3] == [str(frozen), "--strict-mcp-config"]
    elif agent == "codex":
        assert child["environment"]["CODEX_HOME"] != os.environ["CODEX_HOME"]
        assert result["worker_command"][0] == original_command[0]
    else:
        command = result["worker_command"]
        index = command.index("--gemini_dir")
        assert Path(command[index + 1]).is_dir()
        assert child["environment"]["CODEX_HOME"] == os.environ["CODEX_HOME"]
    meta = json.loads(Path(result["meta"]).read_text(encoding="utf-8"))
    assert meta["mcp_isolation"] == receipt
    assert meta["mcp_config"] == str(frozen)
    assert result["spec"]["mcp_config"] == str(frozen)
    assert workflow.machine_launch_receipt(result)["mcp_isolation"] == receipt
    public = json.dumps({"result": result, "events": runtime.events})
    assert "PRIVATE-MCP-SECRET" not in public
    assert "Authorization" not in public


def test_no_option_keeps_native_command_and_environment(runtime, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("default launch must not prepare an isolated profile")

    monkeypatch.setattr(workflow, "prepare_isolated_mcp", forbidden)
    admitted = _spec(runtime)
    expected = workflow.build_launch_command(admitted, runtime.root)
    result = workflow.launch_workflow(admitted, runtime.root)
    assert result["accepted"], result
    assert result["worker_command"] == expected
    assert runtime.launches[-1]["environment"]["HOME"] == str(runtime.home)
    assert runtime.launches[-1]["environment"]["CODEX_HOME"] == os.environ["CODEX_HOME"]
    assert "mcp_isolation" not in result
    assert "mcp_config" not in admitted.to_payload()


@pytest.mark.parametrize("already_isolated", [False, True])
def test_visible_handoff_exports_isolated_provider_home_not_daemon_home(
    runtime, profile, monkeypatch, already_isolated
):
    if already_isolated:
        seed = workflow.launch_workflow(
            _spec(runtime, profile, agent="codex"), runtime.root
        )
        assert seed["accepted"], seed
        monkeypatch.setenv(
            "CODEX_HOME", runtime.launches[-1]["environment"]["CODEX_HOME"]
        )
        profile = Path(seed["mcp_isolation"]["config_path"])
    frame = runtime.root / "vc-frame"
    monkeypatch.setattr(
        workflow.shutil,
        "which",
        lambda name: str(frame) if name == "vc-frame" else None,
    )
    monkeypatch.setattr(
        workflow, "_effective_operator_session", lambda **_: "worker-host"
    )
    monkeypatch.setattr(
        workflow,
        "_vc_frame_run_host_action",
        lambda *a, **k: workflow._HostActionResult(True, 424242, "", "", False),
    )
    admitted = _spec(runtime, profile, agent="codex", runtime="visible")
    result = workflow.launch_workflow(admitted, runtime.root)
    assert result["accepted"], result
    assert result["transport"] == "vc-frame"
    script = Path(result["command_script"]).read_text(encoding="utf-8")
    home_export = next(
        line for line in script.splitlines() if line.startswith("export CODEX_HOME=")
    )
    isolated_home = Path(shlex.split(home_export)[1].split("=", 1)[1])
    if already_isolated:
        assert isolated_home == Path(os.environ["CODEX_HOME"])
    else:
        assert isolated_home != Path(os.environ["CODEX_HOME"])
    assert isolated_home.is_relative_to(Path(result["mcp_isolation"]["profile_dir"]))
    assert "export HOME=" not in script
    assert "PRIVATE-MCP-SECRET" not in script


@pytest.fixture
def isolated_parent(runtime, profile, monkeypatch):
    launched = workflow.launch_workflow(_spec(runtime, profile), runtime.root)
    assert launched["accepted"], launched
    meta = json.loads(Path(launched["meta"]).read_text(encoding="utf-8"))
    parent = {
        **launched,
        **meta,
        "state": "failed",
        "agent": "claude",
        "skill": "workflow",
        "prompt": "bounded task",
        "file": "",
        "agent_session_id": "native-private",
    }
    monkeypatch.setattr(workflow, "lookup_run", lambda _: dict(parent))
    monkeypatch.setattr(workflow, "_native_resume_meta", lambda *_: dict(parent))
    monkeypatch.setattr(workflow, "_read_identity_run_facts", lambda _: dict(parent))
    monkeypatch.setattr(
        workflow, "find_run_for_identity_token", lambda *a, **k: dict(parent)
    )
    monkeypatch.setattr(workflow, "classify_resume_identity", lambda _: "run_id")
    monkeypatch.setattr(workflow, "_worker_process_alive", lambda _: False)
    controls = workflow.launch_execution_controls(_spec(runtime))
    monkeypatch.setattr(
        workflow,
        "_verified_native_resume_command",
        lambda *a, **k: (
            ["claude", "--resume", "native-private", "-p"],
            "confirmed",
            "fixture",
            controls,
        ),
    )
    monkeypatch.setattr(
        workflow,
        "resolve_session_selection",
        lambda *a, **k: {
            "accepted": True,
            "agent_session_id": "native-private",
            "source_run_id": parent["run_id"],
        },
    )
    return parent


@pytest.mark.parametrize("transition", ["resume", "fork", "retry", "operator-new"])
def test_continuations_keep_frozen_scope_after_mutable_source_changes(
    runtime, profile, isolated_parent, transition
):
    parent = isolated_parent
    receipt = dict(parent["mcp_isolation"])
    profile.write_text(
        '{"mcpServers":{"real-course":{"type":"http",'
        '"url":"https://real.example.invalid/mcp"}}}',
        encoding="utf-8",
    )
    if transition == "resume":
        result = workflow.manual_resume_session(
            "claude",
            "native-private",
            runtime.root,
            prompt="continue",
            root=runtime.root,
        )
    elif transition == "fork":
        result = workflow.manual_fork_session(
            "claude",
            "native-private",
            runtime.root,
            prompt="fork task",
            root=runtime.root,
        )
    elif transition == "retry":
        retry = workflow.retry_run(parent["run_id"], runtime.root)
        assert retry["accepted"], retry
        result = retry["launch"]
    else:
        parent["agent_session_id"] = ""
        parent["native_identity_status"] = "pending"
        result = workflow.operator_continue_run(parent["run_id"], runtime.root)
    assert result["accepted"], result
    assert result["mcp_isolation"]["config_path"] == receipt["config_path"]
    assert result["mcp_isolation"]["sha256"] == receipt["sha256"]
    assert result["mcp_isolation"]["server_names"] == ["private-mock"]
    assert "--strict-mcp-config" in result["worker_command"]
    assert runtime.launches[-1]["environment"]["HOME"] == str(runtime.home)
    assert isolated_parent["mcp_isolation"] == receipt


@pytest.mark.parametrize("damage", ["missing", "changed", "receipt", "replacement"])
def test_resume_refuses_missing_or_replaced_frozen_mcp_scope(
    runtime, profile, isolated_parent, damage
):
    frozen = Path(isolated_parent["mcp_isolation"]["config_path"])
    requested = ""
    if damage == "missing":
        frozen.unlink()
    elif damage == "changed":
        frozen.write_text('{"mcpServers":{}}', encoding="utf-8")
    elif damage == "receipt":
        isolated_parent.pop("mcp_isolation")
    else:
        profile.write_text(
            '{"mcpServers":{"replacement":{"type":"http",'
            '"url":"https://other.example.invalid/mcp"}}}',
            encoding="utf-8",
        )
        requested = str(profile)
    count = len(runtime.launches)
    result = workflow.manual_resume_session(
        "claude",
        "native-private",
        runtime.root,
        prompt="continue",
        root=runtime.root,
        mcp_config=requested,
    )
    assert not result["accepted"]
    assert result["reason"] == "launch_spec_invalid"
    assert len(runtime.launches) == count
    assert "PRIVATE-MCP-SECRET" not in json.dumps(result)


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("binding_kind", ["parent_run_id", "resume_of", "selection"])
def test_resume_uses_exact_parent_instead_of_ambiguous_native_history(
    runtime, isolated_parent, monkeypatch, binding_kind, strict
):
    parent = isolated_parent
    if not strict:
        parent.pop("mcp_isolation")
        parent.pop("mcp_config")
        parent.pop("spec", None)
    if binding_kind == "selection":
        binding = {"session_selection": {"source_run_id": parent["run_id"]}}
    else:
        binding = {binding_kind: parent["run_id"]}

    def no_discovery(*args, **kwargs):
        pytest.fail("an exact parent must not be replaced by native-history discovery")

    monkeypatch.setattr(workflow, "find_run_for_identity_token", no_discovery)
    result = workflow.manual_resume_session(
        "claude",
        "native-private",
        runtime.root,
        prompt="continue this exact run",
        root=runtime.root,
        launch_meta=binding,
    )
    assert result["accepted"], result
    if strict:
        assert result["mcp_isolation"]["config_path"] == parent["mcp_config"]
    else:
        assert "mcp_isolation" not in result


@pytest.mark.parametrize("transition", ["resume", "fork"])
def test_native_only_continuation_refuses_ambiguous_attempt_owners(
    runtime, isolated_parent, monkeypatch, transition
):
    def ambiguous(_native, *, require_unique_root, require_unique_run):
        assert require_unique_root and require_unique_run
        raise ValueError("ambiguous native policy owner; select an exact --run-id")

    monkeypatch.setattr(workflow, "find_run_for_identity_token", ambiguous)
    monkeypatch.setattr(
        workflow,
        "resolve_session_selection",
        lambda *a, **k: {
            "accepted": True,
            "agent_session_id": "native-private",
            "source_run_id": "",
        },
    )
    before = len(runtime.launches)
    if transition == "resume":
        result = workflow.manual_resume_session(
            "claude",
            "native-private",
            runtime.root,
            prompt="continue",
            root=runtime.root,
        )
    else:
        result = workflow.manual_fork_session(
            "claude",
            "native-private",
            runtime.root,
            prompt="fork",
            root=runtime.root,
        )
    assert not result["accepted"]
    assert "--run-id" in result["detail"]
    assert len(runtime.launches) == before


def test_resume_preserves_damaged_caller_mcp_binding_without_discovered_owner(
    runtime, profile, isolated_parent, monkeypatch
):
    monkeypatch.setattr(workflow, "find_run_for_identity_token", lambda *a, **k: None)
    before = len(runtime.launches)
    result = workflow.manual_resume_session(
        "claude",
        "native-private",
        runtime.root,
        prompt="continue",
        root=runtime.root,
        launch_meta={"mcp_config": str(profile)},
    )
    assert not result["accepted"]
    assert "frozen receipt" in result["detail"]
    assert len(runtime.launches) == before


@pytest.mark.parametrize("cached_receipt", [False, True])
def test_exact_parent_with_missing_receipt_never_uses_another_attempts_profile(
    runtime, isolated_parent, monkeypatch, cached_receipt
):
    parent = isolated_parent
    receipt = parent.pop("mcp_isolation")
    binding = {"parent_run_id": parent["run_id"]}
    if cached_receipt:
        binding["mcp_isolation"] = receipt

    def no_discovery(*args, **kwargs):
        pytest.fail("damaged exact-parent metadata must not fall back to another run")

    monkeypatch.setattr(workflow, "find_run_for_identity_token", no_discovery)
    before = len(runtime.launches)
    result = workflow.manual_resume_session(
        "claude",
        "native-private",
        runtime.root,
        prompt="continue",
        root=runtime.root,
        launch_meta=binding,
    )
    assert not result["accepted"]
    assert "frozen receipt" in result["detail"]
    assert len(runtime.launches) == before


@pytest.mark.parametrize("agent", ["codex", "agy"])
def test_retry_recovers_provider_from_canonical_meta_before_mcp_admission(
    runtime, profile, monkeypatch, agent
):
    first = workflow.launch_workflow(_spec(runtime, profile, agent=agent), runtime.root)
    assert first["accepted"], first
    meta = json.loads(Path(first["meta"]).read_text(encoding="utf-8"))
    canonical = {
        **meta,
        "agent": agent,
        "skill": "workflow",
        "root": str(runtime.root),
        "state": "failed",
        "prompt": "retry bounded task",
        "file": "",
    }
    projection = {**canonical, "agent": ""}
    projection.pop("mcp_isolation")
    projection.pop("mcp_config")
    monkeypatch.setattr(workflow, "lookup_run", lambda _: dict(projection))
    monkeypatch.setattr(workflow, "_native_resume_meta", lambda *_: dict(canonical))
    result = workflow.retry_run(first["run_id"], runtime.root)
    assert result["accepted"], result
    assert result["launch"]["agent"] == agent
    assert (
        result["launch"]["mcp_isolation"]["config_path"]
        == first["mcp_isolation"]["config_path"]
    )
