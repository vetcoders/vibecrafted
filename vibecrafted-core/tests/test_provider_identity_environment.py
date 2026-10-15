"""New managed providers cannot inherit their parent's native identity."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from vibecrafted_core import env_allowlist, spawn, workflow

IDENTITY_KEYS = (
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "CODEX_TOOL_CALL_ID",
    "CLAUDE_CODE_SESSION_ID",
    "GROK_SESSION_ID",
)
RETAINED = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/fixture-home",
    "CODEX_HOME": "/fixture-codex",
    "CLAUDE_CONFIG_DIR": "/fixture-claude",
    "OPENAI_API_KEY": "fixture-openai",
    "ANTHROPIC_API_KEY": "fixture-anthropic",
    "HTTPS_PROXY": "https://proxy.invalid",
    "CODEX_CI": "fixture-harness",
    "CLAUDE_CODE_MESSAGING_SOCKET": "/fixture-bus",
    "CLAUDE_CODE_MESSAGING_TOKEN": "fixture-bus-token",
    "FOUNDER_SHELL_SETTING": "fixture-setting",
}


def _polluted():
    return {**RETAINED, **dict.fromkeys(IDENTITY_KEYS, "fixture-parent")}


@pytest.mark.parametrize("mode", ["fresh", "bare-fork", "full-lineage"])
def test_new_provider_birth_scrubs_parent_identity_in_every_continuity_mode(mode):
    source = _polluted()
    child = spawn._fresh_child_environment(
        source, spawn.ContinuityPolicy(mode=mode, lineage_id="fixture-lineage")
    )
    assert not set(IDENTITY_KEYS).intersection(child)
    assert all(child[key] == value for key, value in RETAINED.items())
    assert source == _polluted()


def _probe_command(capture):
    # Only fixture key presence and equality booleans leave the child. A new
    # Claude session then exposes its OWN identity to its ordinary tool child.
    program = (
        "import os,json,sys,subprocess; from pathlib import Path; "
        f"keys={IDENTITY_KEYS!r}; retained={RETAINED!r}; "
        "birth={key:(key in os.environ) for key in keys}; "
        "os.environ['CLAUDE_CODE_SESSION_ID']='fixture-own-claude'; "
        "tool=subprocess.check_output([sys.executable,'-c',"
        "\"import os; print(os.environ.get('CODEX_THREAD_ID') or "
        "os.environ.get('CLAUDE_CODE_SESSION_ID') or '')\"],text=True).strip(); "
        f"Path({str(capture)!r}).write_text(json.dumps({{"
        "'birth':birth,'retained':{k:os.environ.get(k)==v for k,v in retained.items()},"
        "'own_tool_identity':tool=='fixture-own-claude',"
        "'admitted_argv':sys.argv[1:]==['--resume','fixture-admitted-session'],"
        "'run':os.environ.get('VIBECRAFTED_RUN_ID')=='fixture-child-run'}))"
    )
    return [sys.executable, "-c", program, "--resume", "fixture-admitted-session"]


def test_generated_provider_script_scrubs_old_frame_environment(tmp_path):
    capture = tmp_path / "receipt.json"
    script = workflow._write_command_script(
        tmp_path / "provider.sh",
        _probe_command(capture),
        {"VIBECRAFTED_AGENT": "claude", "VIBECRAFTED_RUN_ID": "fixture-child-run"},
        visible=True,
    )
    result = subprocess.run(
        [str(script)], env=_polluted(), capture_output=True, check=False, timeout=10
    )
    assert result.returncode == 0
    receipt = json.loads(capture.read_text())
    assert not any(receipt["birth"].values())
    assert all(receipt["retained"].values())
    assert receipt["own_tool_identity"]
    assert receipt["admitted_argv"]
    assert receipt["run"]


def test_ordinary_command_script_keeps_its_current_provider_identity(tmp_path):
    capture = tmp_path / "ordinary.json"
    script = workflow._write_command_script(
        tmp_path / "ordinary.sh", _probe_command(capture)
    )
    result = subprocess.run(
        [str(script)], env=_polluted(), capture_output=True, check=False, timeout=10
    )
    assert result.returncode == 0
    assert all(json.loads(capture.read_text())["birth"].values())


def test_shared_provider_identity_policy_is_exact_and_nonmutating():
    assert env_allowlist.INHERITED_PROVIDER_IDENTITY_KEYS == IDENTITY_KEYS
    source = {**_polluted(), "VIBECRAFTED_AGENT_SESSION_ID": "fixture-admitted"}
    child = env_allowlist.managed_provider_environment(source)
    assert not set(IDENTITY_KEYS).intersection(child)
    assert child["VIBECRAFTED_AGENT_SESSION_ID"] == "fixture-admitted"
    assert all(child[key] == value for key, value in RETAINED.items())
    assert set(IDENTITY_KEYS).issubset(source)
