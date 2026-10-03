"""Headless worker env is an allowlist, and run meta names the dispatcher."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from vibecrafted_core.env_allowlist import filter_headless_worker_env
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
