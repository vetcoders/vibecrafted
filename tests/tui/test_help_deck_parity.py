"""Advertised compact help, deck routes, and owned aliases must agree.

No live provider or worker is started. Source deck and a packaged-install
fixture both have to reach the intended parser for every compact verb.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from vibecrafted_core import cli
from vibecrafted_core.help_surface import (
    CORE_SURFACE_COMMANDS,
    advertised_compact_verbs,
)
from vibecrafted_core.repository_claims import RESULT_SCHEMA

from scripts import vetcoders_install

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DECK = REPO_ROOT / "scripts" / "vibecrafted"
PACKAGE_DECK = (
    REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "deck" / "vibecrafted"
)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
UNKNOWN = "not in the command deck"
CORE_HELP_MARKERS = {
    "claims": ("acquire", "heartbeat"),
    "message": ("Codex `queue --thread`", "does not invent Claude"),
    "relocate": ("snapshot", "restore"),
    "resume-session": ("--agent-session-id", "always headless"),
    "settlements": ("summary", "inspect"),
}
INVENTED_ALIASES = (
    "vc-message",
    "vc-relocate",
    "vc-claims",
    "vc-resume-session",
    "vc-settlements",
)


def _strip(text: str) -> str:
    return ANSI.sub("", text).replace("\r\n", "\n")


def _display_text(text: str) -> str:
    return " ".join(_strip(text).split())


def _isolated_env(home: Path, extra_bin: Path | None = None) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "PYTHONPATH",
            "PYTHONHOME",
            "VIBECRAFTED_ROOT",
            "VIBECRAFTED_RUNTIME_ROOT",
            "VIBECRAFTED_RUNTIME_BIN",
            "VIBECRAFTED_PYTHON",
            "VIBECRAFTED_PREFER_REPO_SPAWN",
            "VIBECRAFTED_AGENT",
            "VIBECRAFTED_AGENT_SESSION_ID",
            "VIBECRAFTED_RUN_ID",
            "CODEX_SESSION_ID",
            "CODEX_THREAD_ID",
        }
    }
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    if extra_bin is not None:
        env["PATH"] = f"{extra_bin}:{env.get('PATH', '')}"
    return env


def _run(
    argv: list[str],
    *,
    home: Path,
    deck: Path = SCRIPTS_DECK,
    extra_bin: Path | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(deck), *argv],
        cwd=cwd or REPO_ROOT,
        env=_isolated_env(home, extra_bin),
        capture_output=True,
        text=True,
        check=False,
    )


def _packaged_deck(tmp_path: Path) -> Path:
    from vibecrafted_core.runtime_paths import version_is_stamped

    generation = tmp_path / "generation"
    deck = generation / "bin" / "vibecrafted"
    deck.parent.mkdir(parents=True)
    shutil.copy2(PACKAGE_DECK, deck)
    deck.chmod(0o755)
    shutil.copytree(
        REPO_ROOT / "vibecrafted-core" / "vibecrafted_core",
        generation / "vibecrafted-core" / "vibecrafted_core",
    )
    version = "4.3.0+g1234567"
    assert version_is_stamped(version)
    (generation / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (generation / "runtime-manifest.json").write_text("{}\n", encoding="utf-8")
    python = generation / "bin" / "python3"
    python.write_text(f'#!/bin/sh\nexec {sys.executable!r} "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    return deck


def test_deck_twins_stay_byte_identical() -> None:
    assert SCRIPTS_DECK.read_bytes() == PACKAGE_DECK.read_bytes()


def test_core_surface_is_advertised_and_python_owned() -> None:
    advertised = advertised_compact_verbs()
    assert set(CORE_SURFACE_COMMANDS) <= set(advertised)
    owned = cli.python_owned_commands()
    assert set(CORE_SURFACE_COMMANDS) <= set(owned)
    deck = SCRIPTS_DECK.read_text(encoding="utf-8")
    assert "cmd_core_cli() {" in deck
    assert "claims|message|relocate|resume-session) cmd_core_cli" in deck
    assert "cmd_settlements() {" in deck
    assert 'cmd_core_cli settlements "$@"' in deck


def _help_argv(verb: str) -> list[str]:
    # ``uninstall --help`` is forwarded to the installer; probe the help
    # topic instead so the test cannot start a teardown.
    if verb == "help":
        return ["--help"]
    if verb == "uninstall":
        return ["help", "uninstall"]
    return [verb, "--help"]


@pytest.mark.parametrize("verb", advertised_compact_verbs())
def test_source_deck_advertised_help_reaches_parser(tmp_path: Path, verb: str) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = _run(_help_argv(verb), home=home)
    combined = _display_text(result.stdout + result.stderr)
    assert UNKNOWN not in combined
    assert result.returncode == 0, combined
    for marker in CORE_HELP_MARKERS.get(verb, ()):
        assert marker in combined


@pytest.mark.parametrize("verb", CORE_SURFACE_COMMANDS)
def test_packaged_deck_core_surface_help_reaches_parser(
    tmp_path: Path, verb: str
) -> None:
    deck = _packaged_deck(tmp_path)
    home = tmp_path / "run-home"
    home.mkdir()
    result = _run([verb, "--help"], home=home, deck=deck, cwd=tmp_path)
    combined = _display_text(result.stdout + result.stderr)
    assert UNKNOWN not in combined
    assert result.returncode == 0, combined
    for marker in CORE_HELP_MARKERS[verb]:
        assert marker in combined


def test_unknown_command_stays_a_deck_refusal(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = _run(["definitely-not-a-public-command"], home=home)
    assert result.returncode == 1
    assert UNKNOWN in _strip(result.stdout + result.stderr)


def test_message_missing_run_does_not_spawn_a_provider(tmp_path: Path) -> None:
    home = tmp_path / "home"
    extra_bin = tmp_path / "bin"
    home.mkdir()
    extra_bin.mkdir()
    spawn_log = tmp_path / "spawn.log"
    for name in ("codex", "claude", "agy", "junie", "grok", "cursor"):
        script = extra_bin / name
        script.write_text(
            f"#!/bin/sh\nprintf '%s\\n' '{name}' \"$@\" >> '{spawn_log}'\nexit 0\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
    body = tmp_path / "note.txt"
    body.write_text("steering body", encoding="utf-8")
    result = _run(
        ["message", "--run-id", "missing-run", "--file", str(body)],
        home=home,
        extra_bin=extra_bin,
    )
    assert result.returncode != 0
    assert UNKNOWN not in _strip(result.stdout + result.stderr)
    assert "run_not_resolved" in result.stderr or "run_not_resolved" in result.stdout
    assert not spawn_log.exists()


def test_resume_session_without_session_does_not_spawn(tmp_path: Path) -> None:
    home = tmp_path / "home"
    extra_bin = tmp_path / "bin"
    home.mkdir()
    extra_bin.mkdir()
    spawn_log = tmp_path / "spawn.log"
    script = extra_bin / "codex"
    script.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" >> '{spawn_log}'\nexit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    result = _run(
        ["resume-session", "codex", "--prompt", "continue"],
        home=home,
        extra_bin=extra_bin,
    )
    assert result.returncode != 0
    assert UNKNOWN not in _strip(result.stdout + result.stderr)
    assert not spawn_log.exists()


def test_claims_list_json_schema_is_stable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = _run(["claims", "--json", "list"], home=home)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema"] == RESULT_SCHEMA
    assert set(payload) == {
        "schema",
        "ok",
        "action",
        "claims",
        "conflicts",
        "stale_claims",
    }
    assert payload["ok"] is True
    assert payload["action"] == "list"
    assert payload["claims"] == []
    assert payload["conflicts"] == []
    assert payload["stale_claims"] == []


def test_owned_alias_inventory_keeps_vc_fork_and_refuses_invented_twins() -> None:
    assert "vc-fork" in vetcoders_install.LAUNCHER_WRAPPERS
    assert vetcoders_install._RUNTIME_WRAPPER_VERBS["vc-fork"] == "fork"
    assert "vc-fork" in cli.SHELL_WRAPPER_VERBS
    for name in INVENTED_ALIASES:
        assert name not in vetcoders_install.LAUNCHER_WRAPPERS
        assert name not in vetcoders_install._RUNTIME_WRAPPER_VERBS
        assert name not in cli.SHELL_WRAPPER_VERBS
        assert name not in vetcoders_install.PYTHON_ENTRYPOINT_LAUNCHERS
