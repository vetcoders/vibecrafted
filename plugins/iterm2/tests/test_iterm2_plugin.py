"""Tests for the vibecrafted iTerm2 / locterm AutoLaunch plugin.

The plugin runs inside iTerm2's vendored Python sandbox at runtime, so
this test file mocks the ``iterm2`` package surface and exercises the
plugin's pure-Python logic: events.jsonl parsing, status bar rendering,
trigger payload shape, installer symlink contract, and the GPL boundary
smoke (no locterm imports loaded by importing the plugin).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

# ---------- Fixtures ---------------------------------------------------------


@pytest.fixture
def fake_iterm2(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Inject a minimal ``iterm2`` stub into ``sys.modules`` before import."""

    stub = types.ModuleType("iterm2")

    class _StatusBarComponent:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def async_register(self, *args: Any, **kwargs: Any) -> None:
            self.registered = (args, kwargs)

    class _PartialProfile:
        @classmethod
        async def async_query(cls, _conn: Any) -> list[Any]:
            return []

    def _rpc(fn: Any) -> Any:  # decorator surface
        return fn

    def _status_bar_rpc(fn: Any) -> Any:
        fn.async_redraw = lambda *a, **k: None
        return fn

    stub.StatusBarComponent = _StatusBarComponent
    stub.PartialProfile = _PartialProfile
    stub.RPC = _rpc
    stub.StatusBarRPC = _status_bar_rpc
    stub.async_get_app = lambda _conn: None
    stub.run_until_complete = lambda _coro: None
    stub.async_main = lambda _coro: asyncio.sleep(0)

    monkeypatch.setitem(sys.modules, "iterm2", stub)
    # Force-reload any plugin module that may have cached the missing import.
    for mod in [
        "vibecrafted_iterm2.vc_launcher",
        "vibecrafted_iterm2.vc_status_bar",
        "vibecrafted_iterm2.vc_triggers",
    ]:
        sys.modules.pop(mod, None)
    return stub


# ---------- GPL boundary smoke -----------------------------------------------


def test_importing_plugin_does_not_load_any_locterm_module() -> None:
    """The whole point of separate-process IPC: no locterm import path.

    We grep ``sys.modules`` after importing the plugin and assert that
    nothing with ``locterm`` in its name slipped in.
    """
    import importlib

    importlib.import_module("vibecrafted_iterm2")
    importlib.import_module("vibecrafted_iterm2.install_autolaunch")

    leaked = [name for name in sys.modules if "locterm" in name.lower()]
    assert leaked == [], f"GPL boundary violation: {leaked}"


# ---------- events.jsonl parser ---------------------------------------------


def test_parse_event_line_decodes_spawn_update(fake_iterm2: Any) -> None:
    from vibecrafted_iterm2 import vc_launcher

    line = json.dumps(
        {
            "ts": "2026-05-24T09:39:19+00:00",
            "run_id": "just-093919-96488",
            "kind": "spawn-update",
            "payload": {
                "agent": "codex",
                "skill": "implement",
                "state": "running",
                "session_id": "sess-1",
                "exit_code": None,
                "launcher_pid": 12345,
                "transcript": "/tmp/t.log",
            },
        }
    )
    event = vc_launcher._parse_event_line(line)
    assert event is not None
    assert vc_launcher._is_spawn_update(event)
    assert event["payload"]["agent"] == "codex"


def test_parse_event_line_ignores_blank_and_garbage(fake_iterm2: Any) -> None:
    from vibecrafted_iterm2 import vc_launcher

    assert vc_launcher._parse_event_line("") is None
    assert vc_launcher._parse_event_line("   \n") is None
    assert vc_launcher._parse_event_line("{not json") is None


def test_format_completion_label_includes_exit_code(fake_iterm2: Any) -> None:
    from vibecrafted_iterm2 import vc_launcher

    payload = {
        "agent": "claude",
        "skill": "implement",
        "state": "failed",
        "exit_code": 2,
    }
    label = vc_launcher._format_completion_label(payload)
    assert label == "claude/implement failed (exit 2)"


def test_state_classifiers(fake_iterm2: Any) -> None:
    from vibecrafted_iterm2 import vc_launcher

    assert vc_launcher._is_active_state("running")
    assert vc_launcher._is_active_state("launching")
    assert not vc_launcher._is_active_state("completed")
    assert vc_launcher._is_final_state("completed")
    assert vc_launcher._is_final_state("failed")
    assert not vc_launcher._is_final_state("running")


def test_vibecrafted_home_honours_env_override(
    fake_iterm2: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from vibecrafted_iterm2 import vc_launcher

    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path))
    assert vc_launcher.vibecrafted_home() == tmp_path
    assert vc_launcher.events_jsonl_path() == tmp_path / "control_plane/events.jsonl"


# ---------- Runtime event handling ------------------------------------------


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


def test_runtime_active_count_tracks_lifecycle(fake_iterm2: Any) -> None:
    from vibecrafted_iterm2 import vc_launcher

    runtime = vc_launcher.VcPluginRuntime()
    launching = {
        "kind": "spawn-update",
        "run_id": "just-1",
        "payload": {
            "agent": "codex",
            "skill": "implement",
            "state": "launching",
        },
    }
    completed = {
        "kind": "spawn-update",
        "run_id": "just-1",
        "payload": {
            "agent": "codex",
            "skill": "implement",
            "state": "completed",
            "exit_code": 0,
        },
    }
    _run(runtime.handle_event(launching))
    assert len(runtime.state.active_runs) == 1
    _run(runtime.handle_event(completed))
    assert runtime.state.active_runs == {}
    assert "codex/implement" in runtime.state.last_completion


def test_runtime_ignores_non_spawn_update_kinds(fake_iterm2: Any) -> None:
    from vibecrafted_iterm2 import vc_launcher

    runtime = vc_launcher.VcPluginRuntime()
    _run(runtime.handle_event({"kind": "state", "payload": {"state": "running"}}))
    assert runtime.state.active_runs == {}


def test_main_loop_does_not_mutate_default_profile(
    fake_iterm2: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from vibecrafted_iterm2 import vc_launcher

    async def _noop_register(_connection: Any, _state: Any) -> None:
        return None

    async def _noop_tail(_path: Path, _on_event: Any) -> None:
        return None

    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path))
    monkeypatch.setattr(vc_launcher, "register_status_bar", _noop_register)
    monkeypatch.setattr(vc_launcher, "_tail_events", _noop_tail)

    _run(vc_launcher._main_loop(object()))

    assert not hasattr(vc_launcher, "apply_triggers_to_default_profile")


def test_tail_events_uses_wide_stdio_limit(
    fake_iterm2: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from vibecrafted_iterm2 import vc_launcher

    captured: dict[str, Any] = {}

    class _FakeStdout:
        async def readline(self) -> bytes:
            return b""

    class _FakeProc:
        stdout = _FakeStdout()
        returncode = 0

        def terminate(self) -> None:  # not reached
            raise AssertionError("process should not need termination")

    async def _fake_create_subprocess_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    async def _on_event(_event: dict[str, Any]) -> None:
        return None

    _run(vc_launcher._tail_events(tmp_path / "events.jsonl", _on_event))

    assert captured["kwargs"]["limit"] == vc_launcher.STDIO_LIMIT_BYTES


# ---------- Status bar render ------------------------------------------------


def test_status_bar_render_with_no_runs(fake_iterm2: Any) -> None:
    from vibecrafted_iterm2.vc_status_bar import VcStatusBarState

    state = VcStatusBarState()
    assert state.render() == "vc: 0"


def test_status_bar_render_with_completion_tail(fake_iterm2: Any) -> None:
    from vibecrafted_iterm2.vc_status_bar import VcStatusBarState

    state = VcStatusBarState(
        active_runs={"a": {}, "b": {}},
        last_completion="codex/implement completed",
    )
    rendered = state.render()
    assert rendered.startswith("vc: 2")
    assert "codex/implement completed" in rendered


def test_open_transcript_no_file_returns_false(
    fake_iterm2: Any, tmp_path: Path
) -> None:
    from vibecrafted_iterm2.vc_status_bar import (
        VcStatusBarState,
        _open_transcript,
    )

    state = VcStatusBarState(
        last_completion_payload={"transcript": str(tmp_path / "missing.log")}
    )
    assert _open_transcript(state) is False


def test_open_transcript_invokes_open(
    fake_iterm2: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibecrafted_iterm2 import vc_status_bar

    transcript = tmp_path / "t.log"
    transcript.write_text("hello", encoding="utf-8")
    calls: list[list[str]] = []

    class _FakeProc:
        def __init__(self, args: list[str], **_: Any) -> None:
            calls.append(args)

    monkeypatch.setattr(subprocess, "Popen", _FakeProc)
    state = vc_status_bar.VcStatusBarState(
        last_completion_payload={"transcript": str(transcript)}
    )
    assert vc_status_bar._open_transcript(state) is True
    assert calls == [["open", str(transcript)]]


# ---------- Triggers ---------------------------------------------------------


def test_trigger_payload_shape(fake_iterm2: Any) -> None:
    from vibecrafted_iterm2.vc_triggers import (
        VIBECRAFTED_TRIGGERS,
        triggers_as_iterm2_payload,
    )

    payload = triggers_as_iterm2_payload(VIBECRAFTED_TRIGGERS)
    assert len(payload) == len(VIBECRAFTED_TRIGGERS)
    for row in payload:
        assert row["name"].startswith("vibecrafted:")
        assert {"regex", "action", "parameter", "partial", "enabled"} <= row.keys()


def test_trigger_set_excludes_user_owned_rows(fake_iterm2: Any) -> None:
    """Re-applying triggers must preserve operator-authored rows verbatim."""
    from vibecrafted_iterm2.vc_triggers import (
        VIBECRAFTED_TRIGGERS,
        triggers_as_iterm2_payload,
    )

    operator_row = {
        "name": "operator: custom highlight",
        "regex": "TODO",
        "action": "HighlightLineTrigger",
        "parameter": "0xff0000ff",
        "partial": True,
        "enabled": True,
    }
    existing = [operator_row]
    preserved = [
        row
        for row in existing
        if not str(row.get("name") or "").startswith("vibecrafted:")
    ]
    new_payload = preserved + triggers_as_iterm2_payload(VIBECRAFTED_TRIGGERS)
    assert new_payload[0] == operator_row
    assert all(row["name"].startswith("vibecrafted:") for row in new_payload[1:])


# ---------- Installer --------------------------------------------------------


def test_installer_symlinks_into_autolaunch(
    fake_iterm2: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibecrafted_iterm2 import install_autolaunch

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    target = install_autolaunch.install(home=fake_home)
    assert target.is_symlink()
    assert target.name == "vc_launcher.py"
    assert target.resolve() == install_autolaunch.launcher_source().resolve()
    uninstaller = target.parent / install_autolaunch.UNINSTALL_SCRIPT
    assert uninstaller.exists()
    assert os.access(uninstaller, os.X_OK)


def test_installer_writes_dynamic_profile_with_managed_triggers(
    fake_iterm2: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibecrafted_iterm2 import install_autolaunch
    from vibecrafted_iterm2.vc_triggers import VIBECRAFTED_TRIGGERS

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    install_autolaunch.install(home=fake_home)

    target = (
        fake_home
        / "Library/Application Support/iTerm2/DynamicProfiles/vibecrafted.json"
    )
    assert target.exists()
    assert target.parent.parts[-3:] == (
        "Application Support",
        "iTerm2",
        "DynamicProfiles",
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    trigger_rows = [
        row
        for profile in payload["Profiles"]
        for row in profile.get("Triggers", [])
        if str(row.get("name") or "").startswith("vibecrafted:")
    ]
    assert len(trigger_rows) == len(VIBECRAFTED_TRIGGERS)


def test_installer_refuses_to_clobber_without_force(
    fake_iterm2: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibecrafted_iterm2 import install_autolaunch

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    install_autolaunch.install(home=fake_home)
    with pytest.raises(FileExistsError):
        install_autolaunch.install(home=fake_home)
    # --force replaces it cleanly.
    target = install_autolaunch.install(home=fake_home, force=True)
    assert target.is_symlink()


def test_installer_uninstall_cleans_up(
    fake_iterm2: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibecrafted_iterm2 import install_autolaunch

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    install_autolaunch.install(home=fake_home)
    profile_target = (
        fake_home
        / "Library/Application Support/iTerm2/DynamicProfiles/vibecrafted.json"
    )
    assert profile_target.exists()
    assert install_autolaunch.uninstall(home=fake_home) is True
    assert not profile_target.exists()
    assert install_autolaunch.uninstall(home=fake_home) is False


def test_find_iterm2_app_returns_none_when_no_candidate_exists(
    fake_iterm2: Any, tmp_path: Path
) -> None:
    from vibecrafted_iterm2 import install_autolaunch

    bogus = (str(tmp_path / "iTerm.app"), str(tmp_path / "locterm.app"))
    assert install_autolaunch.find_iterm2_app(candidates=bogus) is None


# ---------- Public surface ---------------------------------------------------


def test_subpackage_exposes_constants(fake_iterm2: Any) -> None:
    import vibecrafted_iterm2 as plugin

    assert plugin.EVENTS_JSONL_RELPATH == "control_plane/events.jsonl"
    assert plugin.SPAWN_UPDATE_KIND == "spawn-update"
    assert plugin.STATUS_BAR_COMPONENT_ID.startswith("io.vetcoders.")


def test_subpackage_exports_public_constants(fake_iterm2: Any) -> None:
    import vibecrafted_iterm2 as plugin

    assert "STATUS_BAR_COMPONENT_ID" in plugin.__all__
