"""VCI-0010: interactive init/operator/partner/resume/fork task-file payload.

Drives public interactive-command → admission → interactive-launch with a
closed provider interceptor. PATH-prefix stubs are not enough: host suffix
directories from ``agent_tool_search_path`` are removed, HOME is isolated,
and Popen is fail-closed unless argv0 lives in the trap directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from vibecrafted_core import spawn, workflow
from vibecrafted_core.init_resume import init_resume_block
from vibecrafted_core.package_resources import skills_path
from vibecrafted_core.spawn import (
    canonical_skill_file,
    interactive_policy_command,
    interactive_workspace_command,
    legacy_slash_interactive_prompt,
    slash_skill_name,
)

FROZEN_INIT_OPERATOR = "/vc-init\n\n/vc-operator"
FROZEN_SHA256 = "b435bbe947e7d2f4c193c6e789b951ffe89d6f9b005835fac6386de0ba1b4ab6"
NATIVE_SESSION = "11111111-1111-4111-8111-111111111111"
HOST_LEAK_DIRS = (
    Path("/opt/homebrew/bin"),
    Path("/opt/homebrew/sbin"),
    Path("/usr/local/bin"),
    Path.home() / ".local/bin",
    Path.home() / ".kimi-code/bin",
)
SECRET_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_API_KEY_CODEX",
    "XAI_API_KEY",
    "GOOGLE_API_KEY",
    "CURSOR_API_KEY",
)
_REAL_POPEN = subprocess.Popen


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "agents@vetcoders.io")
    _git(path, "config", "user.name", "runtime-test")
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "seed")
    return path


def _row(root: Path, **overrides: Any) -> dict[str, Any]:
    row = {
        "run_id": "work-260818-195620-61748",
        "agent": "claude",
        "skill": "workflow",
        "reason": "needs attention",
        "state": "timed_out",
        "root": str(root),
        "report_path": "/tmp/report.md",
        "revalidatable": True,
        "checkout_exists": True,
        "native_resume_candidate": False,
        "trust_receipt_present": False,
        "settled_at": "2026-09-14T00:00:00Z",
    }
    row.update(overrides)
    return row


def _stub_settlements(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]
) -> None:
    import vibecrafted_core.settlements_query as sq

    monkeypatch.setattr(sq, "list_settlements", lambda **_k: {"runs": rows})


def _write_trap(directory: Path, name: str, capture: Path) -> Path:
    path = directory / name
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        f"NAME = {name!r}\n"
        "if '--help' in sys.argv or '-h' in sys.argv:\n"
        "  print('Usage: --resume --fork-session --prompt-file --session-id --version')\n"
        "  raise SystemExit(0)\n"
        "if '--version' in sys.argv or '-V' in sys.argv:\n"
        "  print(f'{NAME} 0.0.0-test')\n"
        "  raise SystemExit(0)\n"
        "capture = pathlib.Path(os.environ['VCI0010_CAPTURE'])\n"
        "payload = {\n"
        "  'argv0': sys.argv[0], 'argv': sys.argv[1:], 'pid': os.getpid(),\n"
        "  'home': os.environ.get('HOME', ''),\n"
        "}\n"
        "capture.write_text(json.dumps(payload), encoding='utf-8')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _isolate_world(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, providers: tuple[str, ...]
) -> dict[str, Path]:
    home = tmp_path / "home"
    vc_home = home / ".vibecrafted"
    trap = tmp_path / "trap-bin"
    repo = _repo(tmp_path / "repo")
    capture = tmp_path / "provider.json"
    home.mkdir()
    vc_home.mkdir()
    trap.mkdir()
    for name in providers:
        _write_trap(trap, name, capture)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(vc_home))
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join((str(trap), "/usr/bin", "/bin")))
    monkeypatch.setenv("VCI0010_CAPTURE", str(capture))
    for key in SECRET_KEYS:
        monkeypatch.setenv(key, "test-secret-not-real")
    for key in (
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "CURSOR_CONFIG_DIR",
        "GROK_HOME",
    ):
        monkeypatch.delenv(key, raising=False)

    def closed_search(_environment=None) -> str:
        return str(trap)

    monkeypatch.setattr(spawn, "agent_tool_search_path", closed_search)
    monkeypatch.setattr(
        "vibecrafted_core.runtime_paths.agent_tool_search_path", closed_search
    )
    monkeypatch.setattr(
        "vibecrafted_core.continuity.capabilities.agent_tool_search_path",
        closed_search,
    )
    monkeypatch.setattr(
        spawn,
        "resolve_provider_usage_capability",
        lambda provider, executable=None: spawn.ProviderUsageCapability(
            provider, False, source="vci0010-trap"
        ),
    )
    real_popen = _REAL_POPEN
    trap_root = trap.resolve()
    provider_names = {
        "codex",
        "claude",
        "grok",
        "agy",
        "junie",
        "kimi",
        "cursor-agent",
        "cursor",
    }

    def guarded_popen(command, *args, **kwargs):
        argv0 = Path(str(command[0]))
        if argv0.name in provider_names:
            resolved = argv0.resolve()
            try:
                if not os.path.samefile(resolved.parent, trap_root):
                    raise AssertionError(f"host provider leak: {resolved}")
            except OSError as exc:
                raise AssertionError(f"host provider leak: {resolved}") from exc
            for leak in HOST_LEAK_DIRS:
                if leak in resolved.parents or resolved.parent == leak:
                    raise AssertionError(f"host search-dir leak: {resolved}")
        return real_popen(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
    return {
        "home": home,
        "vc_home": vc_home,
        "trap": trap,
        "repo": repo,
        "capture": capture,
    }


def _permissions(provider: str) -> str:
    return "bypass" if provider in {"codex", "kimi"} else "read-only"


def _run_public_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    provider: str,
    skill: str,
    extra: str,
    native_session: str = "",
    continuity: str = "fresh",
    parent_session_id: str = "",
    settlements: list[dict[str, Any]] | None = None,
    allow_nonzero: bool = False,
) -> dict[str, Any]:
    world = _isolate_world(monkeypatch, tmp_path, (provider, "cursor-agent"))
    _stub_settlements(monkeypatch, settlements or [])
    command = interactive_workspace_command(
        provider,
        extra,
        "local-native",
        _permissions(provider),
        world["repo"],
        token_budget="unmetered",
        skill=skill,
        native_session=native_session,
        continuity=continuity,
        parent_session_id=parent_session_id,
    )
    assert command[command.index("-m") + 1] == "vibecrafted_core.spawn"
    launch_args = command[command.index("interactive-launch") :]
    rc = spawn.main(launch_args)
    if not allow_nonzero:
        assert rc == 0, launch_args
    admission = json.loads(
        Path(command[command.index("--admission-file") + 1]).read_bytes()
    )
    prompt_path = (
        world["vc_home"]
        / "control_plane/runtime_runs"
        / admission["run_id"]
        / "prompt.md"
    )
    body = prompt_path.read_text(encoding="utf-8")
    observed = {}
    if world["capture"].is_file():
        observed = json.loads(world["capture"].read_text(encoding="utf-8"))
    return {
        "admission": admission,
        "prompt": body,
        "prompt_path": prompt_path,
        "plan_source": Path(admission["source_snapshot"]).read_text(encoding="utf-8"),
        "command": command,
        "observed": observed,
        "root": str(world["repo"].resolve()),
        "trap": world["trap"],
        "rc": rc,
    }


def test_before_fixture_is_the_slash_only_defect() -> None:
    body = legacy_slash_interactive_prompt("init", "/vc-operator")
    assert body == FROZEN_INIT_OPERATOR
    assert len(body.encode("utf-8")) == 22
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == FROZEN_SHA256


def test_canonical_skill_files_come_from_this_runtime_not_hardcoded_checkout() -> None:
    init_path = canonical_skill_file("init")
    operator_path = canonical_skill_file("operator")
    partner_path = canonical_skill_file("partner")
    assert init_path == skills_path() / "vc-init" / "SKILL.md"
    assert operator_path == skills_path() / "vc-operator" / "SKILL.md"
    assert partner_path == skills_path() / "vc-partner" / "SKILL.md"
    assert init_path is not None and init_path.is_file()
    assert canonical_skill_file("resume") is None
    assert canonical_skill_file("fork") is None
    assert slash_skill_name("/vc-init") == "init"


def test_init_empty_extra_is_orientation_not_a_mission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _run_public_path(
        monkeypatch, tmp_path, provider="codex", skill="init", extra=""
    )
    body = result["prompt"]
    legacy = legacy_slash_interactive_prompt("init", result["plan_source"])
    assert body != legacy
    assert body.strip() not in {"/vc-init", "/vc-init\n\n/vc-init"}
    assert str(canonical_skill_file("init")) in body
    assert f"Repository root: {result['root']}" in body
    assert "Launcher: init" in body
    assert "Do not invent a task" in body
    assert "No operator-supplied implementation task" in body
    assert "implement the next feature" not in body.lower()
    argv = result["observed"]["argv"]
    assert any(str(result["prompt_path"]) in arg for arg in argv)
    assert Path(result["observed"]["argv0"]).parent == result["trap"]


def test_init_operator_extra_preserves_content_and_resolves_both_skills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _run_public_path(
        monkeypatch,
        tmp_path,
        provider="codex",
        skill="init",
        extra="/vc-operator",
    )
    body = result["prompt"]
    assert body != FROZEN_INIT_OPERATOR
    assert result["plan_source"] == "/vc-operator"
    assert str(canonical_skill_file("init")) in body
    assert str(canonical_skill_file("operator")) in body
    assert "Operator-supplied extra" in body
    assert "/vc-operator" in body
    assert "No operator-supplied implementation task" not in body


def test_operator_and_partner_resolve_their_skill_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operator = _run_public_path(
        monkeypatch, tmp_path, provider="claude", skill="operator", extra=""
    )
    assert "Launcher: operator" in operator["prompt"]
    assert str(canonical_skill_file("operator")) in operator["prompt"]
    partner_world = tmp_path / "partner"
    partner_world.mkdir()
    partner = _run_public_path(
        monkeypatch, partner_world, provider="claude", skill="partner", extra=""
    )
    assert "Launcher: partner" in partner["prompt"]
    assert str(canonical_skill_file("partner")) in partner["prompt"]
    assert "Do not invent a mission" in partner["prompt"]


def test_bare_resume_keeps_native_identity_and_names_resume_as_verb(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _run_public_path(
        monkeypatch,
        tmp_path,
        provider="claude",
        skill="resume",
        extra="",
        native_session=NATIVE_SESSION,
    )
    body = result["prompt"]
    assert result["admission"]["agent_session_id"] == NATIVE_SESSION
    assert result["admission"]["skill"] == "resume"
    assert "launcher verb" in body
    assert "vc-resume/SKILL.md" in body
    assert str(skills_path() / "vc-resume" / "SKILL.md") not in body
    argv = result["observed"]["argv"]
    assert "--resume" in argv
    assert NATIVE_SESSION in argv


def test_explicit_native_session_resume_is_unchanged_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _run_public_path(
        monkeypatch,
        tmp_path,
        provider="codex",
        skill="resume",
        extra="continue the parked diagnosis",
        native_session=NATIVE_SESSION,
    )
    assert result["admission"]["agent_session_id"] == NATIVE_SESSION
    assert (
        result["admission"]["session_selection"]["agent_session_id"] == NATIVE_SESSION
    )
    assert "continue the parked diagnosis" in result["prompt"]
    argv = result["observed"]["argv"]
    assert argv[:2] == ["resume", NATIVE_SESSION]


def test_bare_fork_does_not_invent_a_fork_skill_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _run_public_path(
        monkeypatch,
        tmp_path,
        provider="grok",
        skill="fork",
        extra="",
        continuity="bare-fork",
        parent_session_id=NATIVE_SESSION,
        allow_nonzero=True,
    )
    body = result["prompt"]
    assert result["admission"]["skill"] == "fork"
    assert "Launcher: fork" in body
    assert "launcher verb" in body
    assert canonical_skill_file("fork") is None
    assert str(skills_path() / "vc-fork" / "SKILL.md") not in body
    argv = result["observed"]["argv"]
    assert "--fork-session" in argv
    assert NATIVE_SESSION in argv
    # Grok native-fork still requires a confirmed child identity after spawn;
    # that gate is independent of this task-file repair.
    assert result["rc"] in {0, 1}


def test_preserved_task_content_is_not_replaced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    extra = "Keep the existing continuity caps; do not copy 48000."
    result = _run_public_path(
        monkeypatch, tmp_path, provider="codex", skill="init", extra=extra
    )
    assert extra in result["prompt"]
    assert result["plan_source"] == extra
    assert "Do not invent one" not in result["prompt"]


def test_relevant_settlement_appears_foreign_root_does_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world = _isolate_world(monkeypatch, tmp_path, ("codex",))
    mine = _row(world["repo"])
    foreign = _row(tmp_path / "elsewhere", run_id="work-foreign-000000-00000")
    _stub_settlements(monkeypatch, [mine, foreign])
    command = interactive_workspace_command(
        "codex",
        "",
        "local-native",
        "bypass",
        world["repo"],
        token_budget="unmetered",
        skill="init",
    )
    rc = spawn.main(command[command.index("interactive-launch") :])
    assert rc == 0
    admission = json.loads(
        Path(command[command.index("--admission-file") + 1]).read_bytes()
    )
    body = (
        world["vc_home"]
        / "control_plane/runtime_runs"
        / admission["run_id"]
        / "prompt.md"
    ).read_text(encoding="utf-8")
    assert mine["run_id"] in body
    assert f"vibecrafted resume claude --run-id {mine['run_id']}" in body
    assert foreign["run_id"] not in body
    assert body.count("needs attention") >= 1


def test_clean_ledger_stays_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _run_public_path(
        monkeypatch, tmp_path, provider="codex", skill="init", extra=""
    )
    assert "Resume payload" not in result["prompt"]
    assert "settled `n`" not in result["prompt"]
    assert (
        'vibecrafted message --run-id "$VIBECRAFTED_RUN_ID" --receive'
        in result["prompt"]
    )
    assert init_resume_block(result["root"]) == ""


def test_corrupt_ledger_is_an_honest_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world = _isolate_world(monkeypatch, tmp_path, ("codex",))
    import vibecrafted_core.settlements_query as sq

    def explode(**_kwargs: Any) -> dict[str, Any]:
        raise OSError("ledger is gone")

    monkeypatch.setattr(sq, "list_settlements", explode)
    command = interactive_workspace_command(
        "codex",
        "",
        "local-native",
        "bypass",
        world["repo"],
        token_budget="unmetered",
        skill="init",
    )
    rc = spawn.main(command[command.index("interactive-launch") :])
    assert rc == 0
    admission = json.loads(
        Path(command[command.index("--admission-file") + 1]).read_bytes()
    )
    body = (
        world["vc_home"]
        / "control_plane/runtime_runs"
        / admission["run_id"]
        / "prompt.md"
    ).read_text(encoding="utf-8")
    assert "UNKNOWN" in body
    assert "ledger is gone" in body
    assert "settled `n`" not in body


def test_kimi_delivery_contract_is_argv_drop_not_borrowed_from_codex(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _run_public_path(
        monkeypatch, tmp_path, provider="kimi", skill="init", extra="/vc-operator"
    )
    pointer = f"Read and follow the private task file: {result['prompt_path']}"
    argv = interactive_policy_command("kimi", pointer, "local-native", "bypass")
    assert argv[0] == "kimi"
    assert pointer not in argv
    assert str(result["prompt_path"]) not in argv
    assert str(result["prompt_path"]) not in result["observed"]["argv"]
    assert "Launcher: init" in result["prompt"]
    assert str(canonical_skill_file("init")) in result["prompt"]
    # Independently blocked: interactive kimi never receives prompt.md on argv.


def test_headless_workflow_keeps_runtime_prompt_assembler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_settlements(monkeypatch, [])
    spec = workflow.WorkflowLaunchSpec(
        agent="codex",
        mode="workflow",
        skill="workflow",
        prompt="ship the slice",
        file="",
        runtime="headless",
        root=str(tmp_path),
    )
    prompt = workflow._runtime_prompt(spec)
    assert prompt.startswith("You are running under Vibecrafted core runtime.")
    assert "Step 0 — orient before you touch (the vc-init pass)." in prompt
    assert 'vibecrafted message --run-id "$VIBECRAFTED_RUN_ID" --receive' in prompt
    assert "Operator prompt:\nship the slice" in prompt
    assert "You are in an interactive Vibecrafted session." not in prompt
    assert inspect_native_resume_stays_on_launch_workflow()


def inspect_native_resume_stays_on_launch_workflow() -> bool:
    import inspect

    source = inspect.getsource(workflow.native_resume_run)
    assert "compose_interactive_task_prompt" not in source
    assert "launch_workflow" in source
    return True
