"""Headless worker env is an allowlist, and run meta names the dispatcher."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from vibecrafted_core import workflow
from vibecrafted_core.env_allowlist import (
    filter_headless_worker_env,
    provider_workspace_environment,
)
from vibecrafted_core.spawn import (
    Supervisor,
    _fresh_child_environment,
    resolve_continuity_policy,
    write_meta,
)

_LEAK_SOCKET = "/tmp/dispatcher-bus.sock"
_LEAK_TOKEN = "dispatcher-bus-token"
_RUN_ID = "run-allow-1"
_SESSION_ID = "sess-dispatcher-1"


def test_workspace_trust_is_process_local_and_kimi_only():
    source = {"PATH": "/usr/bin:/bin", "VIBECRAFTED_AGENT": "kimi"}
    assert filter_headless_worker_env(source)["KIMI_CODE_TRUST_WORKSPACE"] == "1"
    assert "KIMI_CODE_TRUST_WORKSPACE" not in source
    for provider in ("codex", "claude", "grok", "junie", ""):
        child = provider_workspace_environment(source, provider)
        assert "KIMI_CODE_TRUST_WORKSPACE" not in child
        headless = filter_headless_worker_env({**source, "VIBECRAFTED_AGENT": provider})
        assert "KIMI_CODE_TRUST_WORKSPACE" not in headless


def _dispatcher_env() -> dict[str, str]:
    """A fake dispatcher environment: contract vars plus secrets that must die."""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/Users/dispatcher",
        "LANG": "en_US.UTF-8",
        "LC_CTYPE": "en_US.UTF-8",
        "TERM": "xterm-256color",
        "VIBECRAFTED_RUN_ID": _RUN_ID,
        "VIBECRAFTED_AGENT": "claude",
        "VIBECRAFTED_SESSION_ID": _SESSION_ID,
        "OPENAI_API_KEY": "sk-codex-test",
        "CODEX_HOME": "/Users/dispatcher/.codex",
        "CLAUDE_CODE_MESSAGING_SOCKET": _LEAK_SOCKET,
        "CLAUDE_CODE_MESSAGING_TOKEN": _LEAK_TOKEN,
        "CLAUDE_CODE_SESSION_ID": "parent-claude-session",
        "PORKBUN_API_KEY": "not-a-worker-secret",
        "AICX_HTTP_AUTH_TOKEN": "not-a-worker-token",
    }


def test_allowlist_blocks_dispatcher_bus_and_passes_contract() -> None:
    source = _dispatcher_env()
    child = filter_headless_worker_env(source)

    assert "CLAUDE_CODE_MESSAGING_SOCKET" not in child
    assert "CLAUDE_CODE_MESSAGING_TOKEN" not in child
    assert "CLAUDE_CODE_SESSION_ID" not in child
    assert "PORKBUN_API_KEY" not in child
    assert "AICX_HTTP_AUTH_TOKEN" not in child
    assert child["VIBECRAFTED_RUN_ID"] == source["VIBECRAFTED_RUN_ID"]
    assert child["PATH"] == source["PATH"]
    assert child["HOME"] == source["HOME"]
    assert child["LANG"] == source["LANG"]
    assert child["LC_CTYPE"] == source["LC_CTYPE"]
    assert child["OPENAI_API_KEY"] == source["OPENAI_API_KEY"]
    assert child["CODEX_HOME"] == source["CODEX_HOME"]
    assert child["VIBECRAFTED_AGENT"] == "claude"
    assert child["VIBECRAFTED_SESSION_ID"] == _SESSION_ID


def test_allowlist_passes_copilot_byok_provider_vars() -> None:
    """COPILOT_PROVIDER_* (copilot's BYOK provider, e.g. kimi-k3:cloud via a
    local Ollama endpoint) must survive the headless allowlist gate, same as
    every other provider's explicit auth/config vars."""
    source = _dispatcher_env()
    source.update(
        {
            "COPILOT_MODEL": "kimi-k3:cloud",
            "COPILOT_PROVIDER_BASE_URL": "http://localhost:11434/v1",
            "COPILOT_PROVIDER_TYPE": "openai",
            "COPILOT_PROVIDER_API_KEY": "",
            "COPILOT_PROVIDER_API_KEY_COMMAND": "op read secret",
            "COPILOT_PROVIDER_BEARER_TOKEN": "bearer-secret",
            "COPILOT_PROVIDER_WIRE_API": "responses",
            "COPILOT_PROVIDER_TRANSPORT": "http",
            "COPILOT_PROVIDER_HEADERS": "Authorization: Bearer xyz",
            "COPILOT_PROVIDER_MODEL_ID": "kimi-k3-id",
            "COPILOT_PROVIDER_WIRE_MODEL": "kimi-k3-wire",
            "COPILOT_PROVIDER_MAX_PROMPT_TOKENS": "128000",
            "COPILOT_PROVIDER_MAX_OUTPUT_TOKENS": "8192",
        }
    )

    child = filter_headless_worker_env(source)

    assert child["COPILOT_MODEL"] == "kimi-k3:cloud"
    assert child["COPILOT_PROVIDER_BASE_URL"] == "http://localhost:11434/v1"
    assert child["COPILOT_PROVIDER_TYPE"] == "openai"
    assert child["COPILOT_PROVIDER_API_KEY"] == ""
    assert child["COPILOT_PROVIDER_API_KEY_COMMAND"] == "op read secret"
    assert child["COPILOT_PROVIDER_BEARER_TOKEN"] == "bearer-secret"
    assert child["COPILOT_PROVIDER_WIRE_API"] == "responses"
    assert child["COPILOT_PROVIDER_TRANSPORT"] == "http"
    assert child["COPILOT_PROVIDER_HEADERS"] == "Authorization: Bearer xyz"
    assert child["COPILOT_PROVIDER_MODEL_ID"] == "kimi-k3-id"
    assert child["COPILOT_PROVIDER_WIRE_MODEL"] == "kimi-k3-wire"
    assert child["COPILOT_PROVIDER_MAX_PROMPT_TOKENS"] == "128000"
    assert child["COPILOT_PROVIDER_MAX_OUTPUT_TOKENS"] == "8192"


def test_interactive_fresh_child_keeps_dispatcher_bus() -> None:
    """Interactive continuity scrub is not the headless allowlist."""
    policy = resolve_continuity_policy("fresh", provider="claude", env={})
    child = _fresh_child_environment(
        {
            "PATH": "/tools",
            "HOME": "/user",
            "CLAUDE_CODE_MESSAGING_SOCKET": _LEAK_SOCKET,
            "CLAUDE_CODE_MESSAGING_TOKEN": _LEAK_TOKEN,
        },
        policy,
    )
    assert child["CLAUDE_CODE_MESSAGING_SOCKET"] == _LEAK_SOCKET
    assert child["CLAUDE_CODE_MESSAGING_TOKEN"] == _LEAK_TOKEN
    assert child["PATH"] == "/tools"


def test_headless_supervisor_spawn_hides_bus_from_child(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", _LEAK_SOCKET)
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", _LEAK_TOKEN)
    monkeypatch.setenv("PORKBUN_API_KEY", "not-a-worker-secret")
    monkeypatch.setenv("VIBECRAFTED_RUN_ID", _RUN_ID)
    monkeypatch.setenv("VIBECRAFTED_SESSION_ID", _SESSION_ID)
    monkeypatch.setenv("VIBECRAFTED_AGENT", "claude")
    monkeypatch.setenv("LANG", "en_US.UTF-8")

    captured = tmp_path / "child-env.json"
    script = (
        "import json, os, pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text("
        "json.dumps(dict(os.environ)), encoding='utf-8')\n"
    )
    handle = Supervisor().spawn(
        "command",
        "",
        skill="implement",
        mode="headless",
        root=tmp_path,
        command=[sys.executable, "-c", script, str(captured)],
        run_id=_RUN_ID,
    )
    assert handle.wait(timeout=30) == 0
    seen = json.loads(captured.read_text(encoding="utf-8"))

    assert "CLAUDE_CODE_MESSAGING_SOCKET" not in seen
    assert "CLAUDE_CODE_MESSAGING_TOKEN" not in seen
    assert "PORKBUN_API_KEY" not in seen
    assert seen["VIBECRAFTED_RUN_ID"] == _RUN_ID
    assert seen["PATH"] == os.environ["PATH"]
    assert seen["HOME"] == os.environ["HOME"]
    assert seen["LANG"] == "en_US.UTF-8"
    assert seen["VIBECRAFTED_SESSION_ID"] == _SESSION_ID


def test_write_meta_stamps_dispatcher_identity(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VIBECRAFTED_AGENT", "claude")
    monkeypatch.setenv("VIBECRAFTED_SESSION_ID", _SESSION_ID)
    meta_path = tmp_path / "runtime_runs" / _RUN_ID / "meta.json"
    write_meta(
        meta_path,
        status="running",
        agent="codex",
        mode="headless",
        root=tmp_path,
        input_ref="prompt.md",
        report=str(tmp_path / "report.md"),
        transcript=str(tmp_path / "transcript.log"),
        launcher="python",
        run_id=_RUN_ID,
        skill_code="impl",
    )
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    dispatcher = payload["dispatcher"]
    assert dispatcher["agent"] == "claude"
    assert dispatcher["session_id"] == _SESSION_ID
    assert dispatcher["pid"] == os.getpid()
    # The worker agent stays on the run; dispatcher is who launched it.
    assert payload["agent"] == "codex"


def test_junie_version_controls_survive_exact_allowlist() -> None:
    source = {
        "JUNIE_VERSION": "3659.6",
        "JUNIE_SKIP_UPDATE_CHECK": "true",
        "JUNIE_PRIVATE_TOKEN": "must-not-pass",
        "JUNIE_VERSION_EXTRA": "must-not-pass",
    }
    child = filter_headless_worker_env(source)
    assert child == {
        "JUNIE_VERSION": "3659.6",
        "JUNIE_SKIP_UPDATE_CHECK": "true",
    }
    assert source["JUNIE_PRIVATE_TOKEN"] == "must-not-pass"
    assert (
        filter_headless_worker_env(
            {"JUNIE_VERSION": None, "JUNIE_SKIP_UPDATE_CHECK": True}
        )
        == {}
    )


@pytest.mark.parametrize("agent", ["junie", "codex"])
def test_launcher_meta_records_only_junie_runtime_requests(
    monkeypatch, tmp_path: Path, agent: str
) -> None:
    monkeypatch.setenv("JUNIE_VERSION", "3659.6")
    monkeypatch.setenv("JUNIE_SKIP_UPDATE_CHECK", "true")
    monkeypatch.setenv("JUNIE_PRIVATE_TOKEN", "must-not-be-recorded")
    meta_path = tmp_path / "meta.json"
    write_meta(
        meta_path,
        "running",
        agent,
        "headless",
        tmp_path,
        "prompt.md",
        str(tmp_path / "report.md"),
        str(tmp_path / "transcript.log"),
        "python",
    )
    payload = json.loads(meta_path.read_text())
    if agent == "junie":
        assert payload["provider_launch_environment"] == {
            "JUNIE_VERSION": "3659.6",
            "JUNIE_SKIP_UPDATE_CHECK": "true",
        }
    else:
        assert "provider_launch_environment" not in payload
    assert "must-not-be-recorded" not in meta_path.read_text()
    assert "provider_version" not in payload


@pytest.mark.parametrize("version,skip", [("3659.6", "true"), ("", "false")])
def test_junie_public_launch_preserves_version_controls_through_dispatch(
    monkeypatch, tmp_path: Path, version: str, skip: str
) -> None:
    """Run the real dispatcher and Supervisor with an inert private provider."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    config = home / ".config" / "vibecrafted" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('[runtime.picking]\npermissions = "bypass"\n')
    # An explicit launch environment must win over the dispatcher's old pin.
    monkeypatch.setenv("JUNIE_VERSION", "3822.1")
    monkeypatch.setenv("JUNIE_SKIP_UPDATE_CHECK", "ambient-old-value")
    monkeypatch.setenv("JUNIE_PRIVATE_TOKEN", "must-not-pass")
    captured = tmp_path / "provider.json"
    tools = tmp_path / "bin"
    tools.mkdir()
    provider = tools / "junie"
    provider.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "sys.stdin.read()\n"
        "keys = ('JUNIE_VERSION', 'JUNIE_SKIP_UPDATE_CHECK', 'JUNIE_PRIVATE_TOKEN')\n"
        "pathlib.Path(os.environ['VIBECRAFTED_TEST_CAPTURE']).write_text(\n"
        "    json.dumps({key: os.environ.get(key) for key in keys}))\n"
        "pathlib.Path(os.environ['VIBECRAFTED_REPORT_PATH']).write_text('Fixture completed\\n')\n"
    )
    provider.chmod(0o700)
    processes = []
    real_popen = workflow.subprocess.Popen

    def track_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        if kwargs.get("start_new_session"):
            processes.append(process)
        return process

    monkeypatch.setattr(workflow.subprocess, "Popen", track_popen)
    spec = workflow.normalize_launch_spec(
        {"agent": "junie", "skill": "workflow", "prompt": "private fixture"},
        tmp_path,
    )
    payload = workflow.launch_workflow(
        spec,
        tmp_path,
        env={
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "JUNIE_VERSION": version,
            "JUNIE_SKIP_UPDATE_CHECK": skip,
            "VIBECRAFTED_TEST_CAPTURE": str(captured),
        },
        launch_meta={"provider_launch_environment": {"JUNIE_VERSION": "forged-parent"}},
    )
    assert payload["accepted"] is True, payload
    assert processes
    for process in processes:
        assert process.wait(timeout=30) == 0
    seen = json.loads(captured.read_text())
    assert seen == {
        "JUNIE_VERSION": version,
        "JUNIE_SKIP_UPDATE_CHECK": skip,
        "JUNIE_PRIVATE_TOKEN": None,
    }
    expected = {"JUNIE_VERSION": version, "JUNIE_SKIP_UPDATE_CHECK": skip}
    assert payload["provider_launch_environment"] == expected
    receipt = workflow.machine_launch_receipt(payload)
    assert receipt["provider_launch_environment"] == expected
    meta = json.loads(Path(payload["meta"]).read_text())
    assert meta["provider_launch_environment"] == expected
    controls = receipt["execution_controls"]
    assert controls["permissions_requested"] == "bypass"
    assert controls["permissions_effective"] == "auto"
    assert controls["permissions_source"] == "config:global"
    assert controls["permissions_reason"] == "junie-headless-no-bypass"
    assert config.read_text() == '[runtime.picking]\npermissions = "bypass"\n'
