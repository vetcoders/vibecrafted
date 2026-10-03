"""W1-04 admission verifier: VC identity and mandate on the prompt path.

Proof is the delivered launch input at the provider process boundary
(prompt.md pointed at by argv, plus the captured child argv/env), not a
string-builder unit test alone.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from vibecrafted_core import spawn
from vibecrafted_core.init_resume import resume_payload
from vibecrafted_core.package_resources import skills_path
from vibecrafted_core.spawn import (
    UNKNOWN_NATIVE_IDENTITY,
    disjoint_session_identities,
    interactive_workspace_command,
)

NATIVE_SESSION = "11111111-1111-4111-8111-111111111111"
PARENT_SESSION = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SECRET = "test-secret-not-real-w1-04"
SECRET_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_API_KEY_CODEX",
    "XAI_API_KEY",
    "GOOGLE_API_KEY",
    "CURSOR_API_KEY",
)
HOST_LEAK_DIRS = (
    Path("/opt/homebrew/bin"),
    Path("/opt/homebrew/sbin"),
    Path("/usr/local/bin"),
    Path.home() / ".local/bin",
    Path.home() / ".kimi-code/bin",
)
SHARED_CONTRACT_MARKERS = (
    "VC identity (delivered to this agent):",
    "VC role:",
    "Mandate:",
    "Provider:",
    "Model:",
    "Task/run:",
    "Workspace:",
    "Panel:",
    "Parent session ID:",
    "Native child session ID:",
    "team/launcher role does not replace provider or model",
    "unfinished-work / settlement context is not a native",
    "It does not grant extra permissions",
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
        "capture = pathlib.Path(os.environ['W104_CAPTURE'])\n"
        "prompt_files = []\n"
        "for arg in sys.argv:\n"
        "  for part in str(arg).split():\n"
        "    if part.endswith('prompt.md'):\n"
        "      prompt_files.append(part)\n"
        "delivered = {}\n"
        "for item in prompt_files:\n"
        "  p = pathlib.Path(item)\n"
        "  delivered[str(p)] = p.read_text(encoding='utf-8') if p.is_file() else ''\n"
        "secret_keys = "
        f"{list(SECRET_KEYS)!r}\n"
        "payload = {\n"
        "  'argv0': sys.argv[0], 'argv': sys.argv[1:], 'pid': os.getpid(),\n"
        "  'home': os.environ.get('HOME', ''),\n"
        "  'delivered_prompts': delivered,\n"
        "  'env': {k: os.environ.get(k, '') for k in secret_keys + [\n"
        "    'CLAUDE_CODE_SESSION_ID', 'CODEX_SESSION_ID', 'GROK_SESSION_ID',\n"
        "    'VIBECRAFTED_PARENT_SESSION_ID', 'VIBECRAFTED_AGENT_SESSION_ID',\n"
        "  ]},\n"
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
    monkeypatch.setenv("W104_CAPTURE", str(capture))
    for key in SECRET_KEYS:
        monkeypatch.setenv(key, SECRET)
    for key in (
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "CURSOR_CONFIG_DIR",
        "GROK_HOME",
        "VC_FRAME_PANE_ID",
        "ZELLIJ_PANE_ID",
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
            provider, False, source="w1-04-trap"
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


def _launch(
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
    model: str = "grok-4.6",
    allow_nonzero: bool = False,
) -> dict[str, Any]:
    world = _isolate_world(monkeypatch, tmp_path, (provider, "cursor-agent"))
    stamped: list[dict[str, Any]] = []
    for row in settlements or []:
        item = dict(row)
        item["root"] = str(world["repo"].resolve())
        stamped.append(item)
    _stub_settlements(monkeypatch, stamped)
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
        model=model,
    )
    rc = spawn.main(command[command.index("interactive-launch") :])
    if not allow_nonzero:
        assert rc == 0, command
    admission = json.loads(
        Path(command[command.index("--admission-file") + 1]).read_bytes()
    )
    run_dir = world["vc_home"] / "control_plane/runtime_runs" / admission["run_id"]
    prompt_path = run_dir / "prompt.md"
    meta_path = run_dir / "meta.json"
    body = prompt_path.read_text(encoding="utf-8")
    observed: dict[str, Any] = {}
    if world["capture"].is_file():
        observed = json.loads(world["capture"].read_text(encoding="utf-8"))
    meta = (
        json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    )
    return {
        "admission": admission,
        "prompt": body,
        "prompt_path": prompt_path,
        "meta": meta,
        "observed": observed,
        "root": str(world["repo"].resolve()),
        "trap": world["trap"],
        "rc": rc,
        "run_dir": run_dir,
    }


def _assert_no_secrets(*blobs: object) -> None:
    for blob in blobs:
        text = blob if isinstance(blob, str) else json.dumps(blob)
        assert SECRET not in text


def _assert_shared_contract(body: str, *, provider: str, skill: str) -> None:
    for marker in SHARED_CONTRACT_MARKERS:
        assert marker in body, marker
    assert f"VC role: {skill}" in body
    assert f"Provider: {provider}" in body
    assert "team/launcher role does not replace provider or model" in body


def _delivered_prompt(result: dict[str, Any]) -> str:
    """Prompt the child process actually received, not a local builder copy."""
    observed = result["observed"]
    delivered = observed.get("delivered_prompts") or {}
    if delivered:
        values = list(delivered.values())
        assert values[0] == result["prompt"]
        return values[0]
    argv = observed.get("argv") or []
    assert any(str(result["prompt_path"]) in str(arg) for arg in argv), argv
    return result["prompt"]


def test_disjoint_helper_refuses_parent_copy() -> None:
    parent, native = disjoint_session_identities(
        parent_session_id=PARENT_SESSION,
        native_child_session_id=PARENT_SESSION,
    )
    assert parent == PARENT_SESSION
    assert native == ""
    parent, native = disjoint_session_identities(
        parent_session_id=PARENT_SESSION,
        native_child_session_id="",
    )
    assert native == ""
    assert _honest_unknown(native) == UNKNOWN_NATIVE_IDENTITY


def _honest_unknown(value: str) -> str:
    return value.strip() or UNKNOWN_NATIVE_IDENTITY


@pytest.mark.parametrize("skill", ["init", "partner", "operator", "resume"])
def test_four_entrypoints_share_identity_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, skill: str
) -> None:
    native = NATIVE_SESSION if skill == "resume" else ""
    result = _launch(
        monkeypatch,
        tmp_path / skill,
        provider="claude",
        skill=skill,
        extra="keep this mandate text",
        native_session=native,
        model="grok-4.6",
    )
    body = _delivered_prompt(result)
    _assert_shared_contract(body, provider="claude", skill=skill)
    assert "keep this mandate text" in body
    assert f"Task/run: {result['admission']['run_id']}" in body
    assert "Model: grok-4.6" in body
    if skill == "resume":
        assert f"Native child session ID: {NATIVE_SESSION}" in body
        assert "--resume" in result["observed"]["argv"]
        assert NATIVE_SESSION in result["observed"]["argv"]
    else:
        assert f"Native child session ID: {UNKNOWN_NATIVE_IDENTITY}" in body
        assert "--resume" not in result["observed"]["argv"]
        assert result["admission"]["native_identity_status"] == "unknown"
        assert result["meta"].get("agent_session_id") in {"", None}
    _assert_no_secrets(
        body,
        result["admission"],
        result["meta"],
        result["observed"].get("argv"),
        result["observed"].get("delivered_prompts"),
    )


def test_two_children_keep_parent_and_native_disjoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", PARENT_SESSION)
    children = []
    for name in ("child-a", "child-b"):
        children.append(
            _launch(
                monkeypatch,
                tmp_path / name,
                provider="claude",
                skill="init",
                extra=f"work as {name}",
            )
        )
    ids = []
    for child in children:
        body = _delivered_prompt(child)
        _assert_shared_contract(body, provider="claude", skill="init")
        assert f"Native child session ID: {UNKNOWN_NATIVE_IDENTITY}" in body
        assert (
            PARENT_SESSION
            not in body.split("Native child session ID:")[1].splitlines()[0]
        )
        assert child["admission"]["agent_session_id"] != PARENT_SESSION
        assert child["meta"].get("agent_session_id") != PARENT_SESSION
        assert child["meta"].get("native_child_session_id") in {"", None}
        assert child["observed"]["env"].get("CLAUDE_CODE_SESSION_ID") == ""
        argv = child["observed"]["argv"]
        if "--session-id" in argv:
            requested = argv[argv.index("--session-id") + 1]
            assert requested != PARENT_SESSION
            ids.append(requested)
        assert PARENT_SESSION not in argv
        _assert_no_secrets(
            body,
            child["admission"],
            child["meta"],
            child["observed"].get("argv"),
            child["observed"].get("delivered_prompts"),
        )
    assert children[0]["admission"]["run_id"] != children[1]["admission"]["run_id"]
    if len(ids) == 2:
        assert ids[0] != ids[1]


def test_resume_context_is_not_native_conversation_resume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _launch(
        monkeypatch,
        tmp_path,
        provider="claude",
        skill="init",
        extra="",
        settlements=[_row(tmp_path)],
    )
    body = _delivered_prompt(result)
    _assert_shared_contract(body, provider="claude", skill="init")
    assert "work-260818-195620-61748" in body
    assert "Resume payload (computed by this init pass" in body
    assert "not a native provider-conversation resume" in body
    assert "--resume" not in result["observed"]["argv"]
    payload = resume_payload(result["root"])
    assert payload["native_conversation_resume"] is False
    assert result["admission"]["native_identity_status"] == "unknown"


def test_skill_sources_and_workspace_are_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _launch(
        monkeypatch,
        tmp_path,
        provider="codex",
        skill="init",
        extra="/vc-operator",
    )
    body = _delivered_prompt(result)
    assert str(skills_path() / "vc-init" / "SKILL.md") in body
    assert str(skills_path() / "vc-operator" / "SKILL.md") in body
    assert "Sources:" in body
    assert result["admission"]["source_snapshot"] in body
    workspace = str(result["admission"].get("workspace_id") or "")
    if workspace:
        assert f"Workspace: {workspace}" in body
    else:
        assert f"Workspace: {UNKNOWN_NATIVE_IDENTITY}" in body
    assert f"Panel: {UNKNOWN_NATIVE_IDENTITY}" in body
    _assert_no_secrets(
        body,
        result["admission"],
        result["meta"],
        result["observed"].get("argv"),
        result["observed"].get("delivered_prompts"),
    )
