from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from vibecrafted_core.vc_frame_staging import materialize_vc_frame_config

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "vibecrafted_core"
    / "config"
    / "vc-frame"
    / "vc-agent-workshop.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vc_agent_workshop", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_agent_workshop_script_is_shipped_and_executable() -> None:
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111


def test_materialized_runtime_keeps_agent_workshop_executable(tmp_path: Path) -> None:
    destination = tmp_path / "vc-frame"
    materialize_vc_frame_config(
        SCRIPT.parent,
        destination,
        pane_shell="bash",
        clipboard_command=None,
    )

    installed = destination / SCRIPT.name
    assert installed.is_file()
    assert installed.stat().st_mode & 0o111


def test_launcher_commands_keep_interactive_agent_in_this_panel() -> None:
    workshop = _load()

    assert workshop.launch_argv("codex", "init") == [
        "vibecrafted",
        "init",
        "codex",
        "--runtime",
        "plain",
        "--policy-runtime",
        "local-native",
        "--permissions",
        "bypass",
        "--operator",
        "none",
        "--continuity",
        "fresh",
    ]
    # Resume re-opens one exact conversation in this tab; it never becomes a
    # bare AICX "new session" and never opens a second tab.
    session = "11111111-2222-4333-8444-555555555555"
    assert workshop.launch_argv("claude", "resume", session=session) == [
        "vibecrafted",
        "resume",
        "claude",
        "--runtime",
        "plain",
        "--permissions",
        "bypass",
        "--session",
        session,
    ]
    with pytest.raises(ValueError, match="concrete session"):
        workshop.launch_argv("claude", "resume")
    with pytest.raises(ValueError, match="interactive mode"):
        workshop.launch_argv("codex", "workflow")


def test_kimi_launch_builds_the_same_command_shape() -> None:
    workshop = _load()

    assert workshop.launch_argv("kimi", "init") == [
        "vibecrafted",
        "init",
        "kimi",
        "--runtime",
        "plain",
        "--policy-runtime",
        "local-native",
        "--permissions",
        "bypass",
        "--operator",
        "none",
        "--continuity",
        "fresh",
    ]
    # kimi's interactive CLI cannot re-open one exact session: say so for
    # this provider instead of composing a resume that cannot be honored.
    with pytest.raises(ValueError, match="kimi has no interactive resume"):
        workshop.launch_argv("kimi", "resume", session="abc-session")


def test_default_provider_stays_codex_with_kimi_on_the_row() -> None:
    workshop = _load()

    assert workshop.AGENTS[2] == "codex"
    picker = workshop.Workshop(SimpleNamespace(), mode="launcher")

    assert picker.agent == workshop.AGENTS.index("codex")


def test_provider_row_renders_every_catalog_provider_including_kimi_and_copilot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    drawn: list[str] = []

    class FakeWindow:
        def getmaxyx(self) -> tuple[int, int]:
            return (24, 80)

        def addstr(self, _row: int, _col: int, text: str, _attr: int = 0) -> None:
            drawn.append(text)

        def erase(self) -> None:
            pass

        def refresh(self) -> None:
            pass

    monkeypatch.setattr(workshop, "_provider_available", lambda _agent: True)
    monkeypatch.setattr(
        workshop,
        "runtime_policy_capabilities",
        lambda _agent: {
            name: {"available": True, "reason": ""}
            for name in workshop.RUNTIME_POLICIES
        },
    )
    form = workshop.Workshop(FakeWindow(), mode="launcher")
    form.draw_launcher()

    tokens = [text.strip() for text in drawn if text.strip() in workshop.AGENTS]
    assert sorted(tokens) == sorted(workshop.AGENTS)
    assert "kimi" in tokens
    assert "copilot" in tokens


def test_choice_markers_are_unboxed_and_selected_once() -> None:
    workshop = _load()

    tokens = workshop._choice_tokens(
        ("init", "resume", "operator"),
        selected=1,
        available=(True, True, False),
    )

    assert tokens == ("init", "resume", "operator")
    assert all(not token.startswith(("•", "×")) for token in tokens)
    assert all("[" not in token and "«" not in token for token in tokens)


def test_bind_terminal_paper_uses_default_colors_not_ansi_black() -> None:
    workshop = _load()
    calls: list[object] = []

    class FakeCurses:
        error = Exception

        def use_default_colors(self) -> None:
            calls.append("use_default")

        def init_pair(self, pair: int, fg: int, bg: int) -> None:
            calls.append(("pair", pair, fg, bg))

        def color_pair(self, pair: int) -> int:
            return 256 * pair

        COLOR_CYAN = 6

    class FakeWindow:
        def bkgd(self, ch: str, attr: int) -> None:
            calls.append(("bkgd", ch, attr))

        def bkgdset(self, ch: str, attr: int) -> None:
            calls.append(("bkgdset", ch, attr))

    original = workshop.curses
    workshop.curses = FakeCurses()
    try:
        attr = workshop.bind_terminal_paper(FakeWindow())
    finally:
        workshop.curses = original

    assert attr == 256
    assert "use_default" in calls
    assert ("pair", 1, -1, -1) in calls
    assert ("bkgd", " ", 256) in calls
    assert ("bkgdset", " ", 256) in calls
    assert ("pair", 2, 6, -1) in calls
    assert workshop._ACCENT == 512


def test_safe_addstr_preserves_the_selected_color_pair() -> None:
    workshop = _load()
    writes = []
    window = SimpleNamespace(
        getmaxyx=lambda: (24, 80),
        addstr=lambda row, col, text, attr: writes.append(attr),
    )
    workshop._PAPER = 256
    workshop._safe_addstr(window, 0, 0, "codex", 512 | workshop.curses.A_BOLD)
    assert writes[0] & workshop.curses.A_COLOR == 512


def test_interactive_mode_matrix_is_complete_and_fails_closed() -> None:
    workshop = _load()

    native = workshop.mode_capabilities("codex", "local-native", "bypass")
    worktree = workshop.mode_capabilities("codex", "local-worktrees", "bypass")
    container = workshop.mode_capabilities("codex", "local-vm", "bypass")
    agy_container = workshop.mode_capabilities("agy", "local-vm", "bypass")
    kimi_native = workshop.mode_capabilities("kimi", "local-native", "bypass")

    assert tuple(native) == ("init", "resume", "partner", "operator")
    assert native["partner"]["available"] == native["init"]["available"]
    assert native["operator"]["available"] == native["init"]["available"]
    # Every environment re-opens its own recorded conversation.
    assert native["resume"]["available"] is True
    assert worktree["resume"] == {"available": True, "reason": ""}
    assert container["resume"] == {"available": True, "reason": ""}
    assert container["init"]["available"] is True
    # A real per-provider gap is reported for that cell, not globally.
    assert agy_container["init"]["available"] is False
    assert (
        "not installed in the local container recipe" in agy_container["init"]["reason"]
    )
    assert kimi_native["resume"]["available"] is False
    assert "kimi has no interactive resume" in kimi_native["resume"]["reason"]


def test_partner_operator_and_path_are_preserved_in_launch_argv(
    tmp_path: Path,
) -> None:
    workshop = _load()

    partner = workshop.launch_argv(
        "codex", "partner", workspace=tmp_path, continuity="fresh"
    )
    operator = workshop.launch_argv(
        "codex", "operator", workspace=tmp_path, continuity="fresh"
    )
    resume = workshop.launch_argv(
        "codex",
        "resume",
        workspace=tmp_path,
        session="019a0000-0000-7000-8000-000000000001",
    )
    worktree_resume = workshop.launch_argv(
        "codex",
        "resume",
        "local-worktrees",
        "auto",
        workspace=tmp_path,
        run_id="init-20261010-000000-abcd",
        session="019a0000-0000-7000-8000-000000000001",
        model="gpt-6.1-sol",
        effort="high",
    )

    assert partner[-4:] == ["--root", str(tmp_path), "--prompt", "/vc-partner"]
    assert operator[-4:] == ["--root", str(tmp_path), "--prompt", "/vc-operator"]
    assert resume[-4:] == [
        "--root",
        str(tmp_path),
        "--session",
        "019a0000-0000-7000-8000-000000000001",
    ]
    # A recorded run owns its checkout: no --root, its environment travels,
    # and the selected permissions, model and effort are preserved.
    assert worktree_resume == [
        "vibecrafted",
        "resume",
        "codex",
        "--runtime",
        "plain",
        "--permissions",
        "auto",
        "--run-id",
        "init-20261010-000000-abcd",
        "--policy-runtime",
        "local-worktrees",
        "--model",
        "gpt-6.1-sol",
        "--effort",
        "high",
    ]


def _git_checkout(path: Path, origin: str | None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if origin is not None:
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", "origin", origin], check=True
        )
    return path.resolve()


def test_parent_picker_refuses_a_basename_guess_without_origin(
    tmp_path: Path,
) -> None:
    workshop = _load()
    root = _git_checkout(tmp_path / "shared-name", origin=None)

    class ForbiddenChain(workshop.SessionChain):
        def list_sessions(self, **kwargs: object) -> SimpleNamespace:
            raise AssertionError(f"catalog queried without identity: {kwargs}")

    choices, reason = workshop.parent_session_choices(
        "claude", root, chain=ForbiddenChain()
    )

    assert choices == []
    assert "no canonical owner/repo for shared-name" in reason


def test_parent_picker_uses_canonical_session_catalog(tmp_path: Path) -> None:
    workshop = _load()
    tmp_path = _git_checkout(
        tmp_path / "workshop-parent",
        origin="https://github.com/Fixture/workshop-parent.git",
    )
    older = workshop.SessionRecord(
        session_id="older-session",
        agent="claude",
        repo_path=str(tmp_path),
        updated_at="2026-09-01T10:00:00Z",
    )
    newer = workshop.SessionRecord(
        session_id="newer-session",
        agent="claude",
        repo_path=str(tmp_path),
        updated_at="2026-09-02T10:00:00Z",
    )

    class FakeChain(workshop.SessionChain):
        def list_sessions(self, **kwargs: object) -> SimpleNamespace:
            assert kwargs["project"] == "Fixture/workshop-parent"
            assert kwargs["root"] == tmp_path
            assert kwargs["agent"] == "claude"
            return SimpleNamespace(
                sessions=[older, newer],
                warnings=[],
            )

    choices, error = workshop.parent_session_choices(
        "claude", tmp_path, chain=FakeChain()
    )

    assert error == ""
    assert [choice.session_id for choice in choices] == [
        "newer-session",
        "older-session",
    ]

    picker = workshop.Workshop(SimpleNamespace(), mode="launcher")
    picker.agent = workshop.AGENTS.index("claude")
    picker.parent_sessions = choices
    picker._cycle_parent(1)
    assert picker.continuity_parent == "newer-session"
    picker._cycle_parent(1)
    assert picker.continuity_parent == "older-session"


def _prepare_launch(
    workshop: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    destination: str,
    live: list[str],
    listing_error: str = "",
    current: str = "",
) -> tuple[object, list[object]]:
    launched = workshop.Workshop(SimpleNamespace(), mode="launcher")
    launched.path = str(tmp_path)
    launched.agent = workshop.AGENTS.index("codex")
    launched.launch_mode = workshop.LAUNCH_MODES.index("partner")
    launched.runtime = workshop.RUNTIME_POLICIES.index("local-native")
    launched.permissions = workshop.PERMISSION_POLICIES.index("bypass")
    launched.continuity = workshop.CONTINUITY_MODES.index("fresh")
    calls: list[object] = []
    monkeypatch.setattr(launched, "draw", lambda: None)
    monkeypatch.setattr(
        workshop, "catalog_owns_destination", lambda *_args: True, raising=False
    )
    monkeypatch.setattr(
        workshop,
        "runtime_policy_capabilities",
        lambda _agent: {
            "local-native": {"available": True, "reason": ""},
        },
    )
    monkeypatch.setattr(
        workshop,
        "continuity_policy_capabilities",
        lambda *_args, **_kwargs: {
            "fresh": {"available": True, "reason": ""},
        },
    )
    monkeypatch.setattr(
        workshop,
        "destination_session_for_workspace",
        lambda *_args, **_kwargs: destination,
    )
    monkeypatch.setattr(
        workshop,
        "list_live_frame_sessions",
        lambda: (list(live), listing_error),
    )
    monkeypatch.setattr(workshop, "current_frame_session", lambda **_kwargs: current)
    monkeypatch.setattr(workshop.shutil, "which", lambda _name: "/bin/vibecrafted")
    monkeypatch.setattr(
        workshop.subprocess,
        "run",
        lambda command, **_kwargs: (
            calls.append(command) or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(
        workshop.os,
        "execvpe",
        lambda *_args, **_kwargs: calls.append("exec"),
    )
    return launched, calls


def test_launch_pane_argv_requires_session_and_opens_a_new_tab(
    tmp_path: Path,
) -> None:
    workshop = _load()
    command = ["vibecrafted", "init", "codex", "--runtime", "plain"]
    argv = workshop.launch_pane_argv(
        "codex · init · vibecrafted", tmp_path, command, session="vibecrafted"
    )

    assert argv[:6] == [
        "vc-frame",
        "--session",
        "vibecrafted",
        "action",
        "new-tab",
        "--name",
    ]
    assert "--near-current-pane" not in argv
    assert "new-pane" not in argv
    assert "--floating" not in argv
    assert argv[argv.index("--cwd") + 1] == str(tmp_path)
    assert argv[argv.index("--") + 1 :] == command
    with pytest.raises(ValueError, match="destination Frame session is missing"):
        workshop.launch_pane_argv("t", tmp_path, command, session="  ")


def test_destination_session_uses_catalog_place_session_not_current_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    loctree = tmp_path / "loctree"
    vibe = tmp_path / "vibecrafted"
    loctree.mkdir()
    vibe.mkdir()
    monkeypatch.setattr(
        workshop,
        "resolve_operator_place_session",
        lambda *, root, env=None: Path(root).name,
    )
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "loctree")

    assert workshop.destination_session_for_workspace(vibe) == "vibecrafted"
    assert workshop.current_frame_session() == "loctree"
    assert workshop.destination_session_for_workspace(loctree) == "loctree"


def test_session_names_from_listing_skip_exited_sessions() -> None:
    workshop = _load()
    listing = (
        "loctree [Created 2h ago]\n"
        "vibecrafted [Created 1h ago] (current)\n"
        "old EXITED\n"
        "codescribe [EXITED]\n"
    )
    assert workshop.session_names_from_listing(listing) == [
        "loctree",
        "vibecrafted",
    ]


@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("binding", ["live", "dead", "wrong-instance", "missing"])
def test_custom_current_seat_requires_exact_project_wes_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, foreign: bool, binding: str
) -> None:
    from vibecrafted_core.workspace_catalog import (
        new_uuid7,
        record_runtime_session_attachment,
        resolve_run_workspace_identity,
    )

    project = tmp_path / "selected"
    source = tmp_path / "another-project"
    project.mkdir()
    source.mkdir()
    identity = resolve_run_workspace_identity(root=source if foreign else project)
    if binding != "missing":
        record_runtime_session_attachment(
            workspace_id=identity.workspace_id,
            vibecrafted_session_id=identity.vibecrafted_session_id,
            workspace_instance_id=identity.workspace_instance_id,
            runtime="vc-frame",
            runtime_session_id="workday-440",
            state="dead" if binding == "dead" else "live",
        )
    for key, value in identity.to_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "workday-440")
    if binding == "wrong-instance":
        monkeypatch.setenv("VIBECRAFTED_WORKSPACE_INSTANCE_ID", new_uuid7())
    workshop = _load()
    owned = not foreign and binding == "live"
    assert workshop.destination_session_for_workspace(project) == (
        "workday-440" if owned else "selected"
    )
    assert workshop.catalog_owns_destination(project.resolve(), "workday-440") is (
        owned
    )


def test_custom_current_seat_launch_preserves_old_seat_and_retry_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibecrafted_core.workspace_catalog import (
        operator_session_name,
        record_runtime_session_attachment,
        resolve_run_workspace_identity,
    )

    identity = resolve_run_workspace_identity(root=tmp_path)
    record_runtime_session_attachment(
        workspace_id=identity.workspace_id,
        vibecrafted_session_id=identity.vibecrafted_session_id,
        workspace_instance_id=identity.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="workday-440",
        state="live",
    )
    for key, value in identity.to_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "workday-440")
    workshop = _load()
    destination = workshop.destination_session_for_workspace
    ownership = workshop.catalog_owns_destination
    old_destination = operator_session_name(identity.workspace_id)
    launched, calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination=old_destination,
        live=["workday-440", old_destination],
        current="workday-440",
    )
    monkeypatch.setattr(workshop, "destination_session_for_workspace", destination)
    monkeypatch.setattr(workshop, "catalog_owns_destination", ownership)
    launched.launch()
    launched.launch()

    assert launched.error == ""
    assert len(calls) == 1
    assert calls[0][:5] == ["vc-frame", "--session", "workday-440", "action", "new-tab"]


def test_creator_error_retains_actionable_stdout_before_progress_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop, launched, calls, _generation = _prepare_project_creation(
        tmp_path, monkeypatch
    )

    def refused(command: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(
            returncode=4,
            stdout="Frame client rejected the session contract.\nOpen a current-generation session from the Sessions rail.",
            stderr="vc-start: checking your workspace and available sessions...\n",
        )

    monkeypatch.setattr(workshop.subprocess, "run", refused)
    launched.launch()
    assert "Frame client rejected the session contract" in launched.error
    assert "Open a current-generation session" in launched.error
    assert "checking your workspace" in launched.error
    assert launched.error.index("Frame client") < launched.error.index("checking")
    assert len(calls) == 1


def test_launch_error_details_wrap_scroll_and_return_without_retry() -> None:
    workshop = _load()
    drawn: list[str] = []

    class Window:
        def getmaxyx(self) -> tuple[int, int]:
            return (8, 38)

        def addstr(self, _row: int, _col: int, text: str, _attr: int = 0) -> None:
            drawn.append(text)

    form = workshop.Workshop(Window(), mode="launcher")
    form.error = (
        "Project could not be opened: Frame rejected this session.\n"
        + "Long diagnostic context " * 20
        + "\nUse the Sessions rail to open a current-generation session."
    )
    form.handle_launcher_key(ord("e"))
    form.draw_launcher()
    assert "Launch error" in drawn
    assert "Frame rejected" in " ".join(drawn)
    for _ in range(40):
        form.handle_launcher_key(workshop.curses.KEY_DOWN)
    drawn.clear()
    form.draw_launcher()
    assert any("current-generation session" in line for line in drawn)
    form.handle_launcher_key(27)
    assert not form.error_details
    assert form.mode == "launcher"
    assert form.launch_results == []


def test_successful_launch_opens_destination_tab_and_keeps_workshop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    launched, calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination="vibecrafted",
        live=["loctree", "vibecrafted"],
        current="loctree",
    )

    launched.launch()

    assert launched.mode == "launcher"
    assert launched.error == ""
    assert "exec" not in calls
    pane = calls[0]
    assert isinstance(pane, list)
    assert pane[:6] == [
        "vc-frame",
        "--session",
        "vibecrafted",
        "action",
        "new-tab",
        "--name",
    ]
    assert "--near-current-pane" not in pane
    assert "new-pane" not in pane
    assert "--floating" not in pane
    title = f"codex · partner · {tmp_path.name}"
    assert pane[pane.index("--name") + 1] == title
    command = pane[pane.index("--") + 1 :]
    assert command[:3] == ["vibecrafted", "init", "codex"]
    assert command[-4:] == ["--root", str(tmp_path), "--prompt", "/vc-partner"]
    assert calls[1] == ["vc-frame", "attach", "vibecrafted"]


def test_same_project_launch_still_opens_a_new_tab_without_attach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    launched, calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination="loctree",
        live=["loctree"],
        current="loctree",
    )

    launched.launch()

    assert launched.mode == "launcher"
    assert launched.error == ""
    assert len(calls) == 1
    pane = calls[0]
    assert isinstance(pane, list)
    assert pane[:5] == ["vc-frame", "--session", "loctree", "action", "new-tab"]
    assert "attach" not in pane


def test_existing_target_session_is_used_when_already_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    launched, calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination="vibecrafted",
        live=["codescribe", "vibecrafted", "loctree"],
        current="codescribe",
    )

    launched.launch()

    assert launched.mode == "launcher"
    pane = calls[0]
    assert isinstance(pane, list)
    assert pane[2] == "vibecrafted"


def test_launch_refuses_missing_destination_without_current_session_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    launched, calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination="vibecrafted",
        live=["loctree"],
        current="loctree",
    )

    launched.launch()

    assert launched.mode == "launcher"
    assert "selected Runtime Pack" in launched.error
    assert calls == []


def _prepare_project_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    recovery: bool = False,
) -> tuple[ModuleType, object, list[object], Path]:
    from vibecrafted_core.workspace_catalog import (
        record_runtime_session_attachment,
        resolve_run_workspace_identity,
    )

    project = tmp_path / "project"
    project.mkdir()
    identity = resolve_run_workspace_identity(root=project)
    generation = tmp_path / "selected-generation"
    (generation / "bin").mkdir(parents=True)
    (generation / "VERSION").write_text("0.0.0+g00000000\n")
    for name in ("python3", "vc-start", "vc-frame"):
        binary = generation / "bin" / name
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_ROOT", str(generation))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", "/foreign-generation/bin")
    monkeypatch.setenv("VIBECRAFTED_PYTHON", "/foreign-generation/python3")
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "source-host")
    workshop = _load()
    launched, calls = _prepare_launch(
        workshop,
        project,
        monkeypatch,
        destination="target",
        live=["source-host"],
        current="source-host",
    )
    monkeypatch.setattr(
        workshop,
        "resolve_run_workspace_identity",
        lambda **_kwargs: identity,
        raising=False,
    )
    progress_draws: list[str] = []
    monkeypatch.setattr(
        launched, "draw", lambda: progress_draws.append(launched.notice)
    )
    target = "target-recovery" if recovery else "target"
    inventory = iter([(["source-host"], ""), (["source-host"], ""), ([target], "")])
    monkeypatch.setattr(
        workshop, "list_live_frame_sessions", lambda **_kwargs: next(inventory)
    )
    monkeypatch.setattr(workshop.time, "sleep", lambda _seconds: None)

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(command)
        if command[0] == str(generation / "bin" / "vc-start"):
            assert progress_draws == ["Opening project…"]
            assert launched.notice == "Opening project…"
            assert launched.error == ""
            assert command == [command[0], "resume", "--repo", str(project)]
            assert kwargs["cwd"] == str(project)
            child = kwargs["env"]
            assert isinstance(child, dict)
            assert child["VIBECRAFTED_SESSION_ID"] == identity.vibecrafted_session_id
            assert child["VIBECRAFTED_WORKSPACE_ROOT"] == str(project)
            assert child["VIBECRAFTED_RUNTIME_BIN"] == str(generation / "bin")
            assert child["VIBECRAFTED_PYTHON"] == str(generation / "bin" / "python3")
            assert str(child["PATH"]).split(os.pathsep)[0] == str(generation / "bin")
            assert kwargs["timeout"] <= 60
            if recovery:
                record_runtime_session_attachment(
                    workspace_id=identity.workspace_id,
                    vibecrafted_session_id=identity.vibecrafted_session_id,
                    workspace_instance_id=identity.workspace_instance_id,
                    runtime="vc-frame",
                    runtime_session_id="target",
                    state="dead",
                )
            record_runtime_session_attachment(
                workspace_id=identity.workspace_id,
                vibecrafted_session_id=identity.vibecrafted_session_id,
                workspace_instance_id=identity.workspace_instance_id,
                runtime="vc-frame",
                runtime_session_id=target,
                state="live",
                replaces_runtime_session_id="target" if recovery else None,
            )
            # Text is not routing authority, even if it names another live session.
            return SimpleNamespace(returncode=0, stdout="source-host", stderr="")
        assert command[0] == str(generation / "bin" / "vc-frame")
        assert kwargs["env"]["VIBECRAFTED_RUNTIME_ROOT"] == str(generation)
        assert kwargs["env"]["VIBECRAFTED_HOME"] == os.environ["VIBECRAFTED_HOME"]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(workshop.subprocess, "run", run)
    return workshop, launched, calls, generation


@pytest.mark.parametrize("recovery", [False, True])
def test_launch_opens_missing_project_before_agent_and_admits_wes_live_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recovery: bool
) -> None:
    _workshop, launched, calls, generation = _prepare_project_creation(
        tmp_path, monkeypatch, recovery=recovery
    )

    launched.launch()

    assert launched.mode == "launcher"
    assert launched.error == ""
    assert launched.notice == ""
    assert calls[0][0] == str(generation / "bin" / "vc-start")
    destination = "target-recovery" if recovery else "target"
    assert calls[1][:5] == [
        str(generation / "bin" / "vc-frame"),
        "--session",
        destination,
        "action",
        "new-tab",
    ]
    assert calls[2] == [str(generation / "bin" / "vc-frame"), "attach", destination]


@pytest.mark.parametrize(
    "failure", ["creator", "timeout", "missing-binary", "missing-frame"]
)
def test_missing_project_creation_failure_never_launches_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    workshop, launched, calls, generation = _prepare_project_creation(
        tmp_path, monkeypatch
    )
    if failure in {"missing-binary", "missing-frame"}:
        name = "vc-frame" if failure == "missing-frame" else "vc-start"
        (generation / "bin" / name).unlink()
    else:

        def failed(command: list[str], **_kwargs: object) -> SimpleNamespace:
            calls.append(command)
            if failure == "timeout":
                raise subprocess.TimeoutExpired(command, 45)
            return SimpleNamespace(returncode=4, stdout="", stderr="host is ambiguous")

        monkeypatch.setattr(workshop.subprocess, "run", failed)

    launched.launch()

    assert launched.mode == "launcher"
    assert "Project could not be opened" in launched.error
    assert not any("new-tab" in command for command in calls)
    if failure == "creator":
        assert "host is ambiguous" in launched.error
    if failure == "timeout":
        assert "timed out" in launched.error
    if failure in {"missing-binary", "missing-frame"}:
        assert calls == []
    assert launched.notice == ""
    assert workshop.public_reason(launched.error) == launched.error


@pytest.mark.parametrize("wrong_receipt", [False, True])
def test_project_creator_success_requires_owned_live_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wrong_receipt: bool
) -> None:
    workshop, launched, calls, _generation = _prepare_project_creation(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(
        workshop, "list_live_frame_sessions", lambda **_kwargs: (["source-host"], "")
    )
    monkeypatch.setattr(workshop, "PROJECT_LIVE_TIMEOUT", 0, raising=False)
    if wrong_receipt:
        monkeypatch.setattr(
            workshop,
            "read_workspace_session",
            lambda _session: SimpleNamespace(workspace_id="foreign-project"),
            raising=False,
        )

    launched.launch()

    assert launched.mode == "launcher"
    assert "Project could not be opened" in launched.error
    assert len(calls) == 1
    assert "new-tab" not in calls[0]


def test_live_inventory_probes_pinned_frame_with_same_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    env = {
        "VIBECRAFTED_RUNTIME_BIN": "/selected-generation/bin",
        "VIBECRAFTED_HOME": "/isolated-runtime-home",
        "PATH": "/foreign-generation/bin",
    }
    calls: list[object] = []

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(command)
        assert kwargs["env"] == env
        return SimpleNamespace(returncode=0, stdout="target\n", stderr="")

    monkeypatch.setattr(workshop.subprocess, "run", run)
    names, error = workshop.list_live_frame_sessions(env=env)

    assert calls == [
        ["/selected-generation/bin/vc-frame", "list-sessions", "--no-formatting"]
    ]
    assert names == ["target"]
    assert error == ""


def test_fresh_project_basename_collision_is_not_an_owned_live_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    original_ownership = getattr(
        workshop, "catalog_owns_destination", lambda *_args: False
    )
    launched, calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination=tmp_path.name,
        live=[tmp_path.name],
        current="source-host",
    )
    monkeypatch.setattr(workshop, "catalog_owns_destination", original_ownership)

    launched.launch()

    assert launched.mode == "launcher"
    assert "workspace catalog does not bind it" in launched.error
    assert calls == []


def test_registered_live_project_preserves_its_catalog_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibecrafted_core.workspace_catalog import (
        create_workspace,
        operator_session_name,
    )

    record = create_workspace(root=tmp_path, display_label="chosen-project")
    destination = operator_session_name(
        record.workspace_id, display_label=record.display_label
    )
    workshop = _load()
    original_ownership = getattr(
        workshop, "catalog_owns_destination", lambda *_args: False
    )
    launched, calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination=destination,
        live=[destination],
        current=destination,
    )
    monkeypatch.setattr(workshop, "catalog_owns_destination", original_ownership)

    launched.launch()

    assert launched.mode == "launcher"
    assert launched.error == ""
    assert len(calls) == 1
    assert calls[0][:5] == ["vc-frame", "--session", destination, "action", "new-tab"]


def test_empty_destination_refuses_before_project_or_agent_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    launched, calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination=" ",
        live=["foreign"],
        current="foreign",
    )

    launched.launch()

    assert launched.mode == "launcher"
    assert "could not resolve a Frame session" in launched.error
    assert calls == []


def test_missing_project_refuses_storage_root_before_creator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibecrafted_core.workspace_catalog import resolve_run_workspace_identity

    workshop, launched, calls, _generation = _prepare_project_creation(
        tmp_path, monkeypatch
    )
    launched.path = os.environ["VIBECRAFTED_HOME"]
    monkeypatch.setattr(
        workshop, "resolve_run_workspace_identity", resolve_run_workspace_identity
    )

    launched.launch()

    assert launched.mode == "launcher"
    assert "Project could not be opened" in launched.error
    assert calls == []


@pytest.mark.parametrize("denied_root", ["runtime-home", "projects-lobby"])
def test_catalog_owned_live_storage_root_refuses_all_launch_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, denied_root: str
) -> None:
    import json

    from vibecrafted_core.workspace_catalog import (
        catalog_path,
        create_workspace,
        operator_session_name,
    )

    runtime_home = Path(os.environ["VIBECRAFTED_HOME"]).resolve()
    blocked = (
        runtime_home if denied_root == "runtime-home" else runtime_home / "projects"
    )
    blocked.mkdir(parents=True, exist_ok=True)
    record = create_workspace(root=tmp_path, display_label=blocked.name)
    # Model a durable record admitted before storage-root denial existed.
    path = catalog_path()
    payload = json.loads(path.read_text())
    payload["workspaces"][record.workspace_id]["canonical_root"] = str(blocked)
    path.write_text(json.dumps(payload))
    destination = operator_session_name(
        record.workspace_id, display_label=record.display_label
    )
    workshop = _load()
    original_ownership = workshop.catalog_owns_destination
    original_destination = workshop.destination_session_for_workspace
    assert original_ownership(blocked, destination)
    launched, calls = _prepare_launch(
        workshop,
        blocked,
        monkeypatch,
        destination=destination,
        live=[destination],
        current=destination,
    )
    monkeypatch.setattr(workshop, "catalog_owns_destination", original_ownership)
    monkeypatch.setattr(
        workshop, "destination_session_for_workspace", original_destination
    )

    launched.launch()

    assert launched.mode == "launcher"
    assert "Project could not be opened" in launched.error
    reason = (
        "VIBECRAFTED_HOME cannot be a workspace root"
        if denied_root == "runtime-home"
        else "projects lobby has no project"
    )
    assert reason in launched.error
    assert calls == []


def test_launch_refuses_when_session_listing_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    launched, calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination="vibecrafted",
        live=[],
        listing_error="Frame sessions are unavailable",
        current="loctree",
    )

    launched.launch()

    assert launched.mode == "launcher"
    assert launched.error == "Frame sessions are unavailable"
    assert calls == []


def test_launch_refuses_unadmittable_worktree_before_invoking_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    launched = workshop.Workshop(SimpleNamespace(), mode="launcher")
    launched.path = str(tmp_path)
    launched.agent = workshop.AGENTS.index("codex")
    launched.runtime = workshop.RUNTIME_POLICIES.index("local-worktrees")
    message = (
        "codex exposes no verified live, child-attributable, monotonic usage "
        "side channel compatible with inherited interactive TTY"
    )
    monkeypatch.setattr(
        workshop,
        "runtime_policy_capabilities",
        lambda _agent: {
            "local-worktrees": {"available": False, "reason": message},
        },
    )
    monkeypatch.setattr(
        workshop.os,
        "execvpe",
        lambda *_args: pytest.fail("unadmittable worktree must not execute"),
    )
    monkeypatch.setattr(
        workshop.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "unadmittable worktree must not open a pane"
        ),
    )

    launched.launch()

    # The gate still refuses before any pane exists; the User reads the plain
    # sentence instead of the internal admission wording.
    assert launched.error == workshop.public_reason(message)
    assert launched.error == "Needs live usage metering"
    assert launched.mode == "launcher"


def test_launcher_projects_explicit_continuity_selection() -> None:
    workshop = _load()

    assert workshop.launch_argv(
        "claude",
        "init",
        continuity="bare-fork",
        continuity_parent="11111111-1111-4111-8111-111111111111",
    )[-4:] == [
        "--continuity",
        "bare-fork",
        "--parent-session",
        "11111111-1111-4111-8111-111111111111",
    ]
    with pytest.raises(ValueError, match="explicit parent"):
        workshop.launch_argv("claude", "init", continuity="bare-fork")
    with pytest.raises(ValueError, match="unsupported continuity"):
        workshop.launch_argv("claude", "init", continuity="latest")


def test_launcher_exposes_exact_disabled_continuity_reasons(tmp_path: Path) -> None:
    workshop = _load()

    capabilities = workshop.continuity_policy_capabilities(
        "claude", root=tmp_path, explicit_parent="", env={"PATH": ""}
    )
    assert capabilities["fresh"]["available"] is True
    assert capabilities["fresh"]["reason"] == "no inherited memory is supplied"
    assert capabilities["full-lineage"]["available"] is False
    assert capabilities["full-lineage"]["reason"] == (
        "no explicit/current parent lineage id"
    )
    assert capabilities["bare-fork"]["available"] is False
    assert "expert-only" in capabilities["bare-fork"]["reason"]


def test_launcher_refuses_unsupported_policy_instead_of_approximating() -> None:
    workshop = _load()

    with pytest.raises(ValueError, match="no native accept-edits"):
        workshop.launch_argv("codex", "init", "local-native", "accept-edits")
    with pytest.raises(ValueError, match="coming soon"):
        workshop.launch_argv("claude", "init", "cloud-soon", "auto")
    with pytest.raises(ValueError, match="concrete session"):
        workshop.launch_argv("claude", "resume", "local-worktrees", "auto")
    with pytest.raises(ValueError, match="not installed in the local container"):
        workshop.launch_argv("grok", "init", "local-vm", "bypass")


def test_runtime_help_is_user_facing_without_false_recommendation() -> None:
    workshop = _load()
    help_text = " ".join(
        line for detail in workshop.RUNTIME_HELP.values() for line in detail
    )

    assert "This checkout, shared with you." in help_text
    assert "branch-backed working copy" in help_text
    assert "Not available yet." in help_text
    assert "canonical worktree" not in help_text
    assert "admission" not in help_text
    assert "child-usage" not in help_text
    assert "Operator Agent" not in help_text
    assert "H2b3" not in help_text
    assert "--operator" not in help_text
    # No lane is recommended, and no gated or disabled lane reads as available.
    assert "recommended" not in help_text.casefold()
    # Metering is no longer an environment gate: no lane promises it.
    assert "live usage can be verified" not in help_text
    # The local-vm key is honestly a container, never sold as a VM.
    assert workshop.RUNTIME_LABELS["local-vm"] == "local-container"
    assert "container (not a VM)" in workshop.RUNTIME_HELP["local-vm"][0]
    assert workshop.RUNTIME_HELP["cloud-soon"][0] == "Not available yet."


def test_workspace_path_is_full_resolved_and_must_exist(tmp_path: Path) -> None:
    workshop = _load()
    child = tmp_path / "project"
    child.mkdir()

    assert workshop.normalized_workspace("project", base=tmp_path) == child.resolve()
    with pytest.raises(ValueError, match="does not exist"):
        workshop.normalized_workspace("missing", base=tmp_path)


@pytest.mark.parametrize("title", ["Launchpad", "Start here"])
def test_product_entry_pane_is_not_an_agent_face(title: str) -> None:
    workshop = _load()
    assert (
        workshop._agent_face({"pane_title": title, "command": "codex", "exited": False})
        is None
    )


def test_dashboard_projects_only_active_agent_faces_from_agents_tab() -> None:
    workshop = _load()
    payload = [
        {
            "tab_name": "Agents",
            "title": "Sessions",
            "is_plugin": True,
        },
        {"tab_name": "Agents", "pane_title": "Agent Workspaces", "exited": False},
        {
            "tab_name": "Agents",
            "pane_title": "codex · resume · vibecrafted",
            "exited": False,
        },
        {
            "tab_name": "Agents",
            "pane_title": "claude · init · vibecrafted",
            "state": "active",
        },
        {"tab_name": "Shell", "pane_title": "Shell", "exited": False},
        {
            "tab_name": "Agents",
            "pane_title": "codex · resume · vibecrafted",
            "state": "running",
        },
    ]

    assert workshop.agent_faces_from_payload(payload) == [
        "codex · resume · vibecrafted",
        "claude · init · vibecrafted",
        "codex · resume · vibecrafted",
    ]


def test_dashboard_uses_explicit_frame_exited_schema_without_claiming_provider_liveness() -> (
    None
):
    workshop = _load()
    presence = workshop.agent_presence_from_payload(
        [
            {
                "id": 41,
                "tab_name": "Agents",
                "title": "codex · partner · codescribe",
                "exited": True,
                "exit_status": 1,
            },
            {
                "id": 42,
                "tab_name": "Agents",
                "title": "claude · init · vibecrafted",
                "exited": False,
                "exit_status": None,
            },
            {
                "id": 43,
                "tab_name": "Agents",
                "title": "ordinary shell",
                "exited": False,
                "exit_status": None,
            },
            {"id": 44, "tab_name": "Agents", "title": "codex · init · vibecrafted"},
        ]
    )

    assert presence.active == ("claude · init · vibecrafted",)
    assert presence.unknown == ("codex · init · vibecrafted",)


def test_dashboard_terminal_exit_evidence_overrides_conflicting_open_flag() -> None:
    workshop = _load()
    presence = workshop.agent_presence_from_payload(
        [
            {
                "tab_name": "Agents",
                "title": "codex · partner · vibecrafted",
                "exited": False,
                "exit_status": 1,
            }
        ]
    )

    assert presence.active == ()
    assert presence.unknown == ()


def test_dashboard_displays_open_pane_count_without_provider_liveness_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    writes: list[str] = []

    class FakeWindow:
        def getmaxyx(self) -> tuple[int, int]:
            return (30, 100)

        def addstr(self, _row: int, _col: int, text: str, _attr: int = 0) -> None:
            writes.append(text)

    monkeypatch.setattr(
        workshop,
        "current_agent_presence",
        lambda: workshop.AgentPresence(("codex · init · vibecrafted",), ()),
    )
    dashboard = workshop.Workshop(FakeWindow(), mode="home")
    dashboard.draw_home()

    assert "Agents in this session (1)" in writes
    assert "  codex · init · vibecrafted" in writes
    # Pane state only: nothing on the dashboard claims provider health.
    rendered = "\n".join(writes).casefold()
    for claim in ("healthy", "running", "online", "responding"):
        assert claim not in rendered


class _StopDashboard(Exception):
    """Ends the scripted home loop once the input script is exhausted."""


_IDLE_TICK = (-1, 0.5)  # window.timeout(500): no key within half a second


def _drive_home_dashboard(
    workshop: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    script: list[tuple[int, float]],
    *,
    mouse_state: int = 0,
) -> list[float]:
    """Run the real home loop under a fake clock; return pane-probe timestamps.

    Each probe stands for one `vc-frame action list-panes` CLI client, whose
    attach makes the server re-broadcast Tab/Pane/Session updates to every
    plugin in the session.
    """
    clock = [0.0]
    probes: list[float] = []
    steps = iter(script)

    def probe() -> object:
        probes.append(clock[0])
        return workshop.AgentPresence(("codex · init · vibecrafted",), ())

    class ScriptedWindow:
        def getmaxyx(self) -> tuple[int, int]:
            return (30, 100)

        def addstr(self, *_args: object) -> None:
            pass

        def erase(self) -> None:
            pass

        def refresh(self) -> None:
            pass

        def getch(self) -> int:
            try:
                key, elapsed = next(steps)
            except StopIteration:
                raise _StopDashboard from None
            clock[0] += elapsed
            return key

    monkeypatch.setattr(workshop, "current_agent_presence", probe)
    monkeypatch.setattr(workshop, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(workshop.Workshop, "configure", lambda _self: None)
    monkeypatch.setattr(
        workshop.curses, "getmouse", lambda: (0, 0, 0, 0, mouse_state), raising=False
    )
    dashboard = workshop.Workshop(ScriptedWindow(), mode="home")
    with pytest.raises(_StopDashboard):
        dashboard.run()
    return probes


def test_home_dashboard_idle_minute_spawns_at_most_four_pane_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()

    probes = _drive_home_dashboard(workshop, monkeypatch, [_IDLE_TICK] * 120)

    # 121 redraws across 60 s of idle.  The old 2 s poll spawned ~24 clients.
    assert len(probes) <= 4, probes
    assert probes[0] == 0.0, "the first draw must show real pane state"


def test_home_dashboard_probes_immediately_after_user_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    script = [_IDLE_TICK] * 41 + [(workshop.curses.KEY_RIGHT, 0.1)] + [_IDLE_TICK] * 2

    probes = _drive_home_dashboard(workshop, monkeypatch, script)

    # first draw, the 15 s idle cadence, then the draw right after the key
    assert len(probes) == 3, probes
    assert probes[-1] == pytest.approx(20.6)


def test_home_dashboard_pointer_motion_never_probes_but_click_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    hover = [_IDLE_TICK] * 41 + [(workshop.curses.KEY_MOUSE, 0.05)] * 10
    hover_probes = _drive_home_dashboard(
        workshop,
        monkeypatch,
        hover + [_IDLE_TICK] * 2,
        mouse_state=workshop.curses.REPORT_MOUSE_POSITION,
    )
    assert len(hover_probes) == 2, hover_probes

    click_probes = _drive_home_dashboard(
        workshop,
        monkeypatch,
        [_IDLE_TICK] * 41 + [(workshop.curses.KEY_MOUSE, 0.1)] + [_IDLE_TICK],
        mouse_state=workshop.curses.BUTTON1_CLICKED,
    )
    assert len(click_probes) == 3, click_probes
    assert click_probes[-1] == pytest.approx(20.6)


def test_presence_schedule_backs_off_while_unchanged_and_resets_on_change() -> None:
    workshop = _load()
    schedule = workshop.PresenceSchedule(base=15.0, ceiling=60.0, floor=2.0)

    assert schedule.due(0.0)
    schedule.record(0.0, changed=True)
    assert not schedule.due(14.9)
    assert schedule.due(15.0)
    schedule.record(15.0, changed=False)
    assert not schedule.due(44.9)
    assert schedule.due(45.0)
    schedule.record(45.0, changed=False)
    schedule.record(105.0, changed=False)
    assert schedule.interval == 60.0
    schedule.record(165.0, changed=True)
    assert schedule.interval == 15.0
    schedule.request()
    assert not schedule.due(166.9)
    assert schedule.due(167.0)


@pytest.mark.parametrize("width", [58, 92])
def test_launcher_choice_redraw_preserves_final_cells_and_selected_row_styling(
    width: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    writes: list[tuple[int, int, str, int]] = []

    class FakeWindow:
        def getmaxyx(self) -> tuple[int, int]:
            return (24, width)

        def erase(self) -> None:
            pass

        def refresh(self) -> None:
            pass

        def addstr(self, row: int, col: int, text: str, _attr: int = 0) -> None:
            writes.append((row, col, text, _attr))

    capabilities = {
        "local-native": {"available": True, "reason": ""},
        "local-worktrees": {
            "available": False,
            "reason": "codex exposes no verified live child-attributable monotonic usage side channel",
        },
        "local-vm": {"available": False, "reason": "no canonical VM entrypoint"},
        "cloud-soon": {"available": False, "reason": "coming soon"},
    }
    monkeypatch.setattr(
        workshop, "runtime_policy_capabilities", lambda _agent: capabilities
    )
    monkeypatch.setattr(
        workshop,
        "continuity_policy_capabilities",
        lambda *_args, **_kwargs: {
            name: {"available": name == "fresh", "reason": "unavailable"}
            for name in workshop.CONTINUITY_MODES
        },
    )
    monkeypatch.setattr(
        workshop,
        "resolve_provider_policy",
        lambda _agent, _runtime, _permissions, _mode: SimpleNamespace(
            supported=_permissions == "bypass", reason="unsupported"
        ),
    )
    monkeypatch.setattr(
        workshop,
        "mode_capabilities",
        lambda *_args, **_kwargs: {
            name: {"available": name != "resume", "reason": "unavailable"}
            for name in workshop.LAUNCH_MODES
        },
    )

    picker = workshop.Workshop(FakeWindow(), mode="launcher")
    picker.advanced = True
    picker.row = 2  # the Mode row of the inline advanced view
    picker.runtime = 0
    picker.continuity = 1
    picker.draw_launcher()

    assert writes
    assert all(
        0 <= col < width and col + len(text) <= width for _, col, text, _ in writes
    )
    # Inline advanced view, no bordered card: the geometry of draw_launcher.
    left = max(1, (width - min(width - 2, 84)) // 2)
    inner = max(12, width - left - 2)
    cursor = max(1, (24 - 19) // 2) + 8
    assert all(
        col + len(text) <= left + inner
        for row, col, text, _ in writes
        if cursor <= row < cursor + 4
    )
    grid = [[(" ", 0) for _ in range(width)] for _ in range(24)]
    for row, col, text, attr in writes:
        for offset, character in enumerate(text):
            if 0 <= row < len(grid) and 0 <= col + offset < width:
                grid[row][col + offset] = (character, attr)

    expected = (
        "Mode      init resume partner operator",
        "Runtime   local-native local-worktrees local-container cloud-soon",
        "Permits   bypass auto accept-edits read-only",
        "Memory    full-lineage fresh bare-fork",
    )
    for row, text in enumerate(expected, start=cursor):
        exact_visible = text if len(text) <= inner else text[: inner - 1] + "…"
        visible = "".join(
            character for character, _ in grid[row][left : left + len(exact_visible)]
        )
        assert visible == exact_visible

    selected_col = left + len("Mode      ")
    assert grid[cursor][selected_col][1] & workshop.curses.A_BOLD
    assert not grid[cursor][selected_col][1] & workshop.curses.A_REVERSE

    # The worktree gate keeps a visible reason in plain words.
    rendered = "\n".join(text for _, _, text, _ in writes)
    assert "child-attributable" not in rendered
    reasons = [text for _, _, text, _ in writes if text.startswith("Unavailable — ")]
    assert reasons
    assert "local-worktrees: Needs live usage" in reasons[0]
    assert "Not available for this provider" not in rendered
    assert any("Advanced options" in text for _, _, text, _ in writes)


def _host_python_without_core() -> Path | None:
    for candidate in (Path("/opt/homebrew/bin/python3"), Path("/usr/bin/python3")):
        if not candidate.is_file():
            continue
        probe = subprocess.run(
            [str(candidate), "-c", "import vibecrafted_core"],
            capture_output=True,
            env={**os.environ, "PYTHONPATH": "", "PYTHONNOUSERSITE": "1"},
            check=False,
        )
        if probe.returncode != 0:
            return candidate
    return None


def test_workshop_reexecs_generation_python_when_host_lacks_core(
    tmp_path: Path,
) -> None:
    """Agents tab used env python3; generation python3 is the only one with core.

    Runs a materialized copy (no core tree around it): an in-tree script now
    imports its own tree's core and never reaches the re-exec lane."""
    host = _host_python_without_core()
    if host is None:
        pytest.skip("no host python3 that lacks vibecrafted_core")
    materialized = tmp_path / "vc-agent-workshop.py"
    materialized.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    log = tmp_path / "generation.log"
    stub = tmp_path / "generation-python"
    stub.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$0" "$@" > "{log}"\nexit 0\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["VIBECRAFTED_PYTHON"] = str(stub)
    env["PATH"] = "/usr/bin:/bin"
    result = subprocess.run(
        [str(host), str(materialized), "home"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    recorded = log.read_text(encoding="utf-8")
    assert str(stub) in recorded
    assert "home" in recorded


def test_ensure_generation_python_is_noop_when_core_imports() -> None:
    workshop = _load()
    workshop.ensure_generation_python()


def test_workshop_prefers_core_from_its_own_tree(tmp_path: Path) -> None:
    """Source-lane skew guard: an ambient PYTHONPATH pointing at an older
    installed generation must not supply the core for a workshop script that
    lives in a newer tree — the launcher UI and the policy tables must come
    from one tree or they disagree at runtime (the `unsupported provider:
    cursor` crash class). A stub core lacking the required symbols stands in
    for the stale generation: if the script imported it, module load would
    fail before argparse."""
    stale = tmp_path / "stale-generation"
    stub_pkg = stale / "vibecrafted_core"
    stub_pkg.mkdir(parents=True)
    (stub_pkg / "__init__.py").write_text("", encoding="utf-8")
    (stub_pkg / "spawn.py").write_text(
        "# stale generation: no resolve_provider_policy\n", encoding="utf-8"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(stale)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "launcher" in result.stdout


def test_generation_python_candidates_prefer_env_then_uv_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    data = tmp_path / "data"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("VIBECRAFTED_PYTHON", "/tmp/explicit-python")
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.delenv("VIBECRAFTED_ROOT", raising=False)
    candidates = workshop._generation_python_candidates()
    assert candidates[0] == "/tmp/explicit-python"
    assert str(tmp_path / "runtime" / "bin" / "python3") in candidates
    assert str(data / "uv" / "tools" / "vibecrafted" / "bin" / "python3") in candidates
    assert str(data / "uv" / "tools" / "vibecrafted" / "bin" / "python") in candidates


def test_workshop_reexecs_uv_tools_python_when_env_unset(tmp_path: Path) -> None:
    """Source-lane panes have no VIBECRAFTED_PYTHON; uv venv has core.

    Runs a materialized copy (no core tree around it): an in-tree script now
    imports its own tree's core and never reaches the re-exec lane."""
    host = _host_python_without_core()
    if host is None:
        pytest.skip("no host python3 that lacks vibecrafted_core")
    materialized = tmp_path / "vc-agent-workshop.py"
    materialized.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    log = tmp_path / "uv.log"
    uv_bin = tmp_path / "data" / "uv" / "tools" / "vibecrafted" / "bin"
    uv_bin.mkdir(parents=True)
    stub = uv_bin / "python3"
    stub.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$0" "$@" > "{log}"\nexit 0\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("VIBECRAFTED_PYTHON", None)
    env.pop("VIBECRAFTED_RUNTIME_ROOT", None)
    env.pop("VIBECRAFTED_ROOT", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["HOME"] = str(tmp_path)
    env["XDG_DATA_HOME"] = str(tmp_path / "data")
    env["PATH"] = "/usr/bin:/bin"
    result = subprocess.run(
        [str(host), str(materialized), "home"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    recorded = log.read_text(encoding="utf-8")
    assert str(stub) in recorded
    assert "home" in recorded


def test_discovery_includes_live_agents_across_tabs_and_skips_labels() -> None:
    workshop = _load()
    payload = [
        {
            "tab_name": "Claude",
            "title": "Claude",
            "is_plugin": True,
            "exited": False,
        },
        {
            "tab_name": "Claude",
            "pane_title": "claude · partner · vibecrafted",
            "exited": False,
        },
        {
            "tab_name": "Agy",
            "pane_title": "shell",
            "command": "/usr/bin/zsh",
            "exited": False,
        },
        {
            "tab_name": "Codex",
            "pane_title": "",
            "command": "vibecrafted init grok --runtime plain --root /tmp/p",
            "exited": False,
        },
        {
            "tab_name": "Agents",
            "pane_title": "New agent",
            "exited": False,
        },
        {
            "tab_name": "Shell",
            "pane_title": "dead-codex · init · vibecrafted",
            "exited": True,
            "exit_status": 1,
        },
    ]

    presence = workshop.agent_presence_from_payload(payload)
    assert presence.scope == "this session"
    assert presence.status == "ok"
    assert list(presence.active) == [
        "claude · partner · vibecrafted",
        "grok · Codex",
    ]
    assert workshop.agent_faces_from_payload(payload) == list(presence.active)

    untitled = workshop.agent_presence_from_payload(
        [
            {
                "tab_name": "Grok",
                "command": "vibecrafted resume grok --root /tmp/p",
            }
        ]
    )
    assert untitled.status == "ok"
    assert list(untitled.active) == ["grok · Grok"]

    untitled = workshop.agent_presence_from_payload(
        [
            {
                "tab_name": "Grok",
                "command": "vibecrafted resume grok --root /tmp/p",
            }
        ]
    )
    assert untitled.status == "ok"
    assert list(untitled.active) == ["grok · Grok"]


def test_discovery_zero_unavailable_and_stale_headlines() -> None:
    workshop = _load()
    assert workshop.presence_headline("ok", 0) == "Agents in this session (0)"
    assert (
        workshop.presence_headline("unavailable", 0)
        == "Agents in this session — unavailable"
    )
    assert (
        workshop.presence_headline("ok", 3, stale=True)
        == "Agents in this session (3, stale)"
    )
    empty = workshop.agent_presence_from_payload([])
    assert empty.status == "ok"
    assert empty.active == ()


def test_current_presence_unavailable_is_not_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    monkeypatch.setattr(
        workshop.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="no session"
        ),
    )
    presence = workshop.current_agent_presence()
    assert presence.status == "unavailable"
    assert presence.active == ()


def test_open_launcher_is_inline_not_floating() -> None:
    workshop = _load()
    dashboard = workshop.Workshop(SimpleNamespace(), mode="home")
    dashboard.open_launcher()
    assert dashboard.mode == "launcher"
    assert dashboard.advanced is False


def test_launch_pane_argv_is_tiled_and_usable(tmp_path: Path) -> None:
    workshop = _load()
    pane = workshop.launch_pane_argv(
        "codex · init · demo",
        tmp_path,
        ["vibecrafted", "init", "codex", "--runtime", "plain"],
        session="vibecrafted",
    )
    assert "--floating" not in pane
    assert "--near-current-pane" not in pane
    assert "new-pane" not in pane
    assert pane[:5] == ["vc-frame", "--session", "vibecrafted", "action", "new-tab"]
    assert pane[pane.index("--cwd") + 1] == str(tmp_path)
    assert pane[pane.index("--") + 1 :] == [
        "vibecrafted",
        "init",
        "codex",
        "--runtime",
        "plain",
    ]


def test_public_reason_strips_policy_jargon() -> None:
    workshop = _load()
    assert (
        workshop.public_reason(
            "codex exposes no verified live child-attributable monotonic usage side channel"
        )
        == "Needs live usage metering"
    )
    assert (
        workshop.public_reason(
            "Safe recommended local default; one canonical worktree per Agent launch."
        )
        == "Not available for this provider"
    )
    assert workshop.public_reason("coming soon") == "Not available yet"
    assert workshop.public_reason("codex executable not found") == (
        "This provider is not installed"
    )
    # Parent-picker and project reasons never read as a provider problem.
    assert (
        workshop.public_reason(
            "no canonical owner/repo for shared-name; parent sessions need a git origin"
        )
        == "Parent sessions need a git origin"
    )
    assert (
        workshop.public_reason("aicx executable not found; parent sessions unavailable")
        == "Parent sessions are unavailable right now"
    )
    assert (
        workshop.public_reason(
            "workspace does not exist: /Volumes/vc-workspace/some/long/project/path/x"
        )
        == "That project folder does not exist"
    )


def test_face_rows_open_the_tab_of_the_active_agent_they_show(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    unknown = workshop.AgentFace("codex · init · vibecrafted", "Shell", "unknown")
    active = workshop.AgentFace("claude · partner · vibecrafted", "Claude", "active")
    monkeypatch.setattr(
        workshop,
        "current_agent_presence",
        lambda: workshop.AgentPresence(
            (active.label,), (unknown.label,), faces=(unknown, active)
        ),
    )
    opened: list[list[str]] = []
    monkeypatch.setattr(
        workshop.subprocess,
        "run",
        lambda command, **_kwargs: (
            opened.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    dashboard = workshop.Workshop(SimpleNamespace(), mode="home")
    dashboard._refresh_presence()
    dashboard._focus_face(0)

    assert opened == [["vc-frame", "action", "go-to-tab-name", "Claude"]]


def test_provider_click_drops_parent_sessions_of_the_previous_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    monkeypatch.setattr(workshop, "_provider_available", lambda _agent: True)
    for name in (
        "_normalize_runtime_choice",
        "_normalize_permission_choice",
        "_normalize_mode_choice",
        "_normalize_continuity_choice",
    ):
        monkeypatch.setattr(workshop.Workshop, name, lambda _self: None)
    monkeypatch.setattr(
        workshop.curses,
        "getmouse",
        lambda: (0, 4, 3, 0, workshop.curses.BUTTON1_CLICKED),
        raising=False,
    )
    picker = workshop.Workshop(SimpleNamespace(), mode="launcher")
    picker.agent = workshop.AGENTS.index("claude")
    picker.parent_sessions = [
        workshop.SessionRecord(
            session_id="claude-parent",
            agent="claude",
            repo_path="/tmp/project",
            updated_at="2026-09-01T10:00:00Z",
        )
    ]
    picker.parent_index = 0
    picker.row = 1  # provider click must take focus back from the project
    picker.mouse_targets = [(3, 0, 10, workshop.AGENTS.index("codex"), "provider")]

    picker.handle_mouse()

    assert picker.agent == workshop.AGENTS.index("codex")
    assert picker.row == 0
    assert picker.parent_sessions == []
    assert picker.parent_index == -1

    picker.handle_launcher_key(workshop.curses.KEY_RIGHT)
    assert picker.agent == (workshop.AGENTS.index("codex") + 1) % len(workshop.AGENTS)


@pytest.mark.parametrize("event", ["BUTTON1_RELEASED", "BUTTON3_CLICKED"])
def test_launcher_mouse_release_and_other_buttons_do_not_launch(
    monkeypatch: pytest.MonkeyPatch, event: str
) -> None:
    workshop = _load()
    picker = workshop.Workshop(SimpleNamespace(), mode="launcher")
    picker.mouse_targets = [(3, 0, 10, 0, "launch")]
    monkeypatch.setattr(
        workshop.curses,
        "getmouse",
        lambda: (0, 4, 3, 0, getattr(workshop.curses, event)),
    )
    launches = []
    monkeypatch.setattr(picker, "launch", lambda: launches.append(True))

    picker.handle_mouse()

    assert launches == []


def test_project_field_accepts_spaces_without_cycling_provider() -> None:
    workshop = _load()
    picker = workshop.Workshop(SimpleNamespace(), mode="launcher")
    picker.row = 1
    picker.path = "/tmp/My"

    picker.handle_launcher_key(ord(" "))

    assert picker.path == "/tmp/My "


@pytest.mark.parametrize("provider", ["claude", "codex", "kimi", "copilot"])
@pytest.mark.parametrize("activation", ["enter", "click"])
def test_provider_click_then_keyboard_and_launch_keep_exact_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, activation: str
) -> None:
    workshop = _load()
    project = tmp_path / "My Project"
    project.mkdir()
    picker, calls = _prepare_launch(
        workshop,
        project,
        monkeypatch,
        destination="project-workspace",
        live=["project-workspace"],
        current="project-workspace",
    )
    monkeypatch.setattr(workshop, "_provider_available", lambda _agent: True)
    for name in (
        "_normalize_runtime_choice",
        "_normalize_permission_choice",
        "_normalize_mode_choice",
        "_normalize_continuity_choice",
    ):
        monkeypatch.setattr(workshop.Workshop, name, lambda _self: None)
    picker.row = 1
    picker.mouse_targets = [(3, 0, 10, workshop.AGENTS.index(provider), "provider")]
    monkeypatch.setattr(
        workshop.curses,
        "getmouse",
        lambda: (0, 4, 3, 0, workshop.curses.BUTTON1_CLICKED),
    )
    picker.handle_mouse()
    picker.handle_launcher_key(workshop.curses.KEY_RIGHT)
    picker.handle_launcher_key(workshop.curses.KEY_LEFT)
    if activation == "enter":
        picker.handle_launcher_key(10)
    else:
        picker.mouse_targets = [(3, 0, 10, 0, "launch")]
        picker.handle_mouse()

    assert picker.error == ""
    assert len(calls) == 1
    argv = calls[0]
    assert argv[argv.index("--session") + 1] == "project-workspace"
    assert argv[argv.index("--cwd") + 1] == str(project)
    command = argv[argv.index("--") + 1 :]
    assert command[:3] == ["vibecrafted", "init", provider]
    assert command[command.index("--root") + 1] == str(project)


def test_agents_home_has_no_second_voc_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Voc has one global entry beside Composer; the Agents home must not
    offer another one (and no longer has a Voc tab to jump to)."""
    workshop = _load()
    writes: list[str] = []

    class FakeWindow:
        def getmaxyx(self) -> tuple[int, int]:
            return (14, 90)

        def addstr(self, _row: int, _col: int, text: str, _attr: int = 0) -> None:
            writes.append(text)

        def erase(self) -> None:
            pass

        def refresh(self) -> None:
            pass

    monkeypatch.setattr(
        workshop,
        "current_agent_presence",
        lambda: workshop.AgentPresence((), (), status="ok"),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        workshop.subprocess,
        "run",
        lambda argv, **_kwargs: (
            calls.append(argv) or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    home = workshop.Workshop(FakeWindow(), mode="home")
    home.draw_home()
    home_text = "\n".join(writes)
    assert "[ New agent ]" in home_text
    assert "Voc" not in home_text
    assert [kind for *_, kind in home.mouse_targets].count("home") == 1
    assert not hasattr(home, "open_voc")

    home.handle_home_key(ord("v"))
    assert home.mode == "home"
    home.handle_home_key(10)
    assert home.mode == "launcher"
    assert not any("Voc" in argv for argv in calls)


def test_small_home_and_launcher_render_without_nested_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    writes: list[str] = []

    class FakeWindow:
        def __init__(self, size: tuple[int, int]) -> None:
            self.size = size

        def getmaxyx(self) -> tuple[int, int]:
            return self.size

        def addstr(self, _row: int, _col: int, text: str, _attr: int = 0) -> None:
            writes.append(text)

        def erase(self) -> None:
            pass

        def refresh(self) -> None:
            pass

    monkeypatch.setattr(
        workshop,
        "current_agent_presence",
        lambda: workshop.AgentPresence((), (), status="ok"),
    )
    home = workshop.Workshop(FakeWindow((10, 40)), mode="home")
    home.draw_home()
    home_text = "\n".join(writes)
    assert "Agents in this session (0)" in home_text
    assert "┌" not in home_text
    assert "canonical" not in home_text
    assert "unavailable" not in home_text

    writes.clear()
    monkeypatch.setattr(
        workshop,
        "runtime_policy_capabilities",
        lambda _agent: {
            name: {"available": name == "local-native", "reason": ""}
            for name in workshop.RUNTIME_POLICIES
        },
    )
    monkeypatch.setattr(
        workshop,
        "_provider_available",
        lambda _agent: True,
    )
    form = workshop.Workshop(FakeWindow((12, 40)), mode="launcher")
    form.draw_launcher()
    form_text = "\n".join(writes)
    assert "New agent" in form_text
    assert "[ Launch ]" in form_text
    assert "┌" not in form_text
    assert "canonical worktree" not in form_text
    assert "• agy" not in form_text


def test_selected_provider_has_accent_without_a_dot_or_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    monkeypatch.setattr(workshop, "_ACCENT", 2 << 8)
    styled: list[tuple[str, int]] = []

    class FakeWindow:
        def getmaxyx(self) -> tuple[int, int]:
            return (24, 80)

        def addstr(self, _row: int, _col: int, text: str, attr: int = 0) -> None:
            styled.append((text, attr))

        def erase(self) -> None:
            pass

        def refresh(self) -> None:
            pass

    monkeypatch.setattr(workshop, "_provider_available", lambda _agent: True)
    monkeypatch.setattr(
        workshop,
        "runtime_policy_capabilities",
        lambda _agent: {
            name: {"available": True, "reason": ""}
            for name in workshop.RUNTIME_POLICIES
        },
    )
    form = workshop.Workshop(FakeWindow(), mode="launcher")
    form.agent = workshop.AGENTS.index("codex")
    form.draw_launcher()
    selected = [item for item in styled if item[0].strip() == "codex"]
    assert selected
    assert selected[0][1] & workshop.curses.A_BOLD
    assert selected[0][1] & workshop.curses.A_COLOR == 2 << 8
    assert not selected[0][1] & workshop.curses.A_REVERSE
    bullets = [item[0] for item in styled if item[0].startswith("• codex")]
    assert bullets == []


def test_focused_picker_row_uses_a_chevron_and_selection_stays_bold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selected option is bold. Focus is a chevron, not a full-row underline."""
    workshop = _load()
    styled: list[tuple[str, int]] = []

    class FakeWindow:
        def getmaxyx(self) -> tuple[int, int]:
            return (24, 80)

        def addstr(self, _row: int, _col: int, text: str, attr: int = 0) -> None:
            styled.append((text, attr))

        def erase(self) -> None:
            pass

        def refresh(self) -> None:
            pass

    monkeypatch.setattr(workshop, "_provider_available", lambda _agent: True)
    form = workshop.Workshop(FakeWindow(), mode="launcher")
    form.agent = workshop.AGENTS.index("codex")
    form.row = 0
    form.draw_launcher()
    providers = [item for item in styled if item[0].strip() in workshop.AGENTS]
    assert providers
    assert not any(attr & workshop.curses.A_UNDERLINE for _, attr in styled)
    assert not any(attr & workshop.curses.A_REVERSE for _, attr in styled)
    selected = [attr for text, attr in providers if text.strip() == "codex"]
    assert selected[0] & workshop.curses.A_BOLD
    others = [attr for text, attr in providers if text.strip() != "codex"]
    assert others and not any(attr & workshop.curses.A_BOLD for attr in others)
    assert any(text == "›" for text, _attr in styled)

    styled.clear()
    form.row = 1
    form.draw_launcher()
    providers = [item for item in styled if item[0].strip() in workshop.AGENTS]
    assert not any(attr & workshop.curses.A_UNDERLINE for _, attr in providers)
    path_rows = [attr for text, attr in styled if text.startswith("Project  ")]
    assert path_rows and not path_rows[0] & workshop.curses.A_UNDERLINE
    assert any(text == "›" for text, _attr in styled)
    launch = [attr for text, attr in styled if text == "[ Launch ]"]
    assert launch and launch[0] == workshop.curses.A_BOLD


def test_public_reason_vm_is_host_not_provider() -> None:
    workshop = _load()
    assert workshop.public_reason("no canonical VM entrypoint") == (
        "VM runtime is not available on this host"
    )
    assert "for this provider" not in workshop.public_reason(
        "no canonical VM entrypoint for anyone"
    )


def test_public_reason_worktrees_name_the_usage_gap() -> None:
    workshop = _load()
    message = (
        "codex exposes no verified live, child-attributable, monotonic usage "
        "side channel compatible with inherited interactive TTY"
    )
    assert workshop.public_reason(message) == ("Needs live usage metering")


def test_selected_advanced_choice_uses_reverse_not_only_a_dot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    styled: list[tuple[str, int]] = []

    class FakeWindow:
        def getmaxyx(self) -> tuple[int, int]:
            return (24, 80)

        def addstr(self, _row: int, _col: int, text: str, attr: int = 0) -> None:
            styled.append((text, attr))

        def erase(self) -> None:
            pass

        def refresh(self) -> None:
            pass

    monkeypatch.setattr(workshop, "_provider_available", lambda _agent: True)
    monkeypatch.setattr(
        workshop,
        "runtime_policy_capabilities",
        lambda _agent: {
            name: {"available": name == "local-native", "reason": ""}
            for name in workshop.RUNTIME_POLICIES
        },
    )
    monkeypatch.setattr(
        workshop,
        "mode_capabilities",
        lambda *_args, **_kwargs: {
            name: {"available": True, "reason": ""} for name in workshop.LAUNCH_MODES
        },
    )
    monkeypatch.setattr(
        workshop,
        "resolve_provider_policy",
        lambda *_args, **_kwargs: SimpleNamespace(supported=True, reason=""),
    )
    monkeypatch.setattr(
        workshop,
        "continuity_policy_capabilities",
        lambda *_args, **_kwargs: {
            name: {"available": True, "reason": ""}
            for name in workshop.CONTINUITY_MODES
        },
    )
    form = workshop.Workshop(FakeWindow(), mode="launcher")
    form.advanced = True
    form.launch_mode = 0
    form.draw_launcher()
    selected = [item for item in styled if item[0] == "init"]
    assert selected
    assert selected[0][1] & workshop.curses.A_BOLD
    assert not selected[0][1] & workshop.curses.A_REVERSE
    assert not any(item[0].startswith("• ") for item in styled)
    assert not any(item[0].startswith("× ") for item in styled)


def test_advanced_toggle_is_visible_clickable_and_keyed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    writes: list[tuple[int, int, str, int]] = []

    class FakeWindow:
        def getmaxyx(self) -> tuple[int, int]:
            return (24, 80)

        def addstr(self, row: int, col: int, text: str, attr: int = 0) -> None:
            writes.append((row, col, text, attr))

        def erase(self) -> None:
            pass

        def refresh(self) -> None:
            pass

    monkeypatch.setattr(workshop, "_provider_available", lambda _agent: True)
    monkeypatch.setattr(
        workshop,
        "runtime_policy_capabilities",
        lambda _agent: {
            name: {"available": True, "reason": ""}
            for name in workshop.RUNTIME_POLICIES
        },
    )
    form = workshop.Workshop(FakeWindow(), mode="launcher")
    form.notice = "Opening project…"
    form.draw_launcher()
    assert any(text == "Opening project…" for _, _, text, _ in writes)
    assert form.error == ""
    assert any(
        text == "▸ Advanced options · click or press a" for _, _, text, _ in writes
    )
    assert any(kind == "advanced" for *_, kind in form.mouse_targets)
    form.handle_launcher_key(ord("a"))
    assert form.advanced is True
    writes.clear()
    form.draw_launcher()
    assert any(
        text == "▾ Advanced options · click or press a" for _, _, text, _ in writes
    )

    form.row = 1
    form.path = "/tmp/project"
    form.handle_launcher_key(ord("a"))
    assert form.advanced is True
    assert form.path.endswith("a")

    form.path = "/tmp/project"
    form.row = 2
    form.handle_launcher_key(ord("a"))
    assert form.advanced is False
    assert form.path == "/tmp/project"

    monkeypatch.setattr(
        workshop.curses,
        "getmouse",
        lambda: (0, 2, 10, 0, workshop.curses.BUTTON1_CLICKED),
        raising=False,
    )
    form.mouse_targets = [(10, 0, 20, 0, "advanced")]
    form.handle_mouse()
    assert form.advanced is True


def test_small_launcher_does_not_overlap_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    writes: list[tuple[int, str]] = []

    class FakeWindow:
        def __init__(self, size: tuple[int, int]) -> None:
            self.size = size

        def getmaxyx(self) -> tuple[int, int]:
            return self.size

        def addstr(self, row: int, _col: int, text: str, _attr: int = 0) -> None:
            writes.append((row, text))

        def erase(self) -> None:
            pass

        def refresh(self) -> None:
            pass

    monkeypatch.setattr(workshop, "_provider_available", lambda _agent: True)
    monkeypatch.setattr(
        workshop,
        "runtime_policy_capabilities",
        lambda _agent: {
            name: {"available": name == "local-native", "reason": ""}
            for name in workshop.RUNTIME_POLICIES
        },
    )
    form = workshop.Workshop(FakeWindow((14, 50)), mode="launcher")
    form.advanced = True
    form.draw_launcher()

    def first_row(predicate) -> int:
        for row, text in writes:
            if predicate(text):
                return row
        raise AssertionError("missing row")

    title = first_row(lambda text: text == "New agent")
    project = first_row(lambda text: text.startswith("Project"))
    toggle = first_row(lambda text: "Advanced options" in text)
    launch = first_row(lambda text: text.startswith("[ Launch ]"))
    rows = (title, project, toggle, launch)
    assert rows == tuple(sorted(rows))
    assert len(set(rows)) == 4
    assert all(0 <= row < 14 for row in rows)


def test_session_names_from_listing_and_other_sessions_are_on_demand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    listing = (
        "vibecrafted-921310b3-w [Created 2h ago]\n"
        "other-root [Created 1h ago]\n"
        "\n"
        "vibecrafted-921310b3-w [Created 2h ago]\n"
    )
    assert workshop.session_names_from_listing(listing) == [
        "vibecrafted-921310b3-w",
        "other-root",
    ]
    monkeypatch.setenv("ZELLIJ_SESSION_NAME", "vibecrafted-921310b3-w")
    monkeypatch.setattr(
        workshop.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=listing, stderr=""
        ),
    )
    names, error = workshop.list_other_sessions()
    assert error == ""
    assert names == ["other-root"]


# --- Advanced: every visible value is a mouse target from the same render ---


class _GridWindow:
    """Records painted cells so hitboxes can be checked against the screen."""

    def __init__(self, height: int = 26, width: int = 100) -> None:
        self.height = height
        self.width = width
        self.grid: dict[tuple[int, int], str] = {}

    def getmaxyx(self) -> tuple[int, int]:
        return (self.height, self.width)

    def erase(self) -> None:
        self.grid.clear()

    def refresh(self) -> None:
        pass

    def addstr(self, row: int, col: int, text: str, _attr: int = 0) -> None:
        for offset, char in enumerate(text):
            self.grid[(row, col + offset)] = char

    def text(self, row: int, start: int, end: int) -> str:
        return "".join(self.grid.get((row, col), " ") for col in range(start, end))


def _advanced_picker(
    workshop: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    window: _GridWindow,
    *,
    runtime_caps: dict | None = None,
) -> object:
    caps = runtime_caps or {
        name: {"available": True, "reason": ""} for name in workshop.RUNTIME_POLICIES
    }
    monkeypatch.setattr(workshop, "_provider_available", lambda _agent: True)
    monkeypatch.setattr(workshop, "runtime_policy_capabilities", lambda _agent: caps)
    monkeypatch.setattr(
        workshop,
        "resolve_provider_policy",
        lambda *_args: SimpleNamespace(supported=True, reason=""),
    )
    monkeypatch.setattr(
        workshop,
        "mode_capabilities",
        lambda *_args: {
            name: {"available": True, "reason": ""} for name in workshop.LAUNCH_MODES
        },
    )
    monkeypatch.setattr(
        workshop,
        "continuity_policy_capabilities",
        lambda *_args, **_kwargs: {
            name: {"available": True, "reason": ""}
            for name in workshop.CONTINUITY_MODES
        },
    )
    picker = workshop.Workshop(window, mode="launcher")
    picker.advanced = True
    picker.draw()
    return picker


def _press(
    workshop: ModuleType, monkeypatch: pytest.MonkeyPatch, x: int, y: int, state: int
) -> None:
    monkeypatch.setattr(
        workshop.curses, "getmouse", lambda: (0, x, y, 0, state), raising=False
    )


def _choice_targets(picker: object) -> dict[tuple[int, int], tuple[int, int, int]]:
    return {
        index: (row, start, end)
        for row, start, end, index, kind in picker.mouse_targets
        if kind == "choice"
    }


def test_every_advanced_value_is_a_hitbox_over_its_own_rendered_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    window = _GridWindow()
    picker = _advanced_picker(workshop, monkeypatch, window)

    targets = _choice_targets(picker)
    rows = picker._advanced_model()
    expected = {
        (offset, index)
        for offset, row in enumerate(rows)
        for index in range(len(row["values"]))
    }
    assert set(targets) == expected
    for (offset, index), (row, start, end) in targets.items():
        assert window.text(row, start, end) == rows[offset]["labels"][index]
    # The container is named honestly on screen; the config key stays local-vm.
    runtime_row = targets[(1, workshop.RUNTIME_POLICIES.index("local-vm"))]
    assert window.text(*runtime_row) == "local-container"


@pytest.mark.parametrize(
    "offset,attribute,choices",
    [
        (0, "launch_mode", "LAUNCH_MODES"),
        (1, "runtime", "RUNTIME_POLICIES"),
        (2, "permissions", "PERMISSION_POLICIES"),
        (3, "continuity", "CONTINUITY_MODES"),
    ],
)
def test_clicking_each_value_selects_exactly_that_value(
    monkeypatch: pytest.MonkeyPatch, offset: int, attribute: str, choices: str
) -> None:
    workshop = _load()
    window = _GridWindow()
    picker = _advanced_picker(workshop, monkeypatch, window)

    for index in reversed(range(len(getattr(workshop, choices)))):
        picker.draw()
        row, _start, end = _choice_targets(picker)[(offset, index)]
        _press(workshop, monkeypatch, end - 1, row, workshop.curses.BUTTON1_PRESSED)
        picker.handle_mouse()
        _press(workshop, monkeypatch, end - 1, row, workshop.curses.BUTTON1_RELEASED)
        picker.handle_mouse()
        assert getattr(picker, attribute) == index, (attribute, index)
        # Focus follows the click so the keyboard continues on that row.
        assert picker.row == offset + 2
    if attribute == "continuity":
        assert picker.continuity_explicit is True


def test_one_physical_click_is_one_change_and_hover_or_release_do_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    window = _GridWindow()
    picker = _advanced_picker(workshop, monkeypatch, window)
    applied: list[tuple[int, int]] = []
    original = picker._choose_advanced
    monkeypatch.setattr(
        picker,
        "_choose_advanced",
        lambda offset, index: (
            applied.append((offset, index)) or original(offset, index)
        ),
    )
    row, start, _end = _choice_targets(picker)[(1, 1)]

    _press(workshop, monkeypatch, start, row, workshop.curses.BUTTON1_PRESSED)
    picker.handle_mouse()
    # A terminal that still reports CLICKED for the same press: same click.
    _press(workshop, monkeypatch, start, row, workshop.curses.BUTTON1_CLICKED)
    picker.handle_mouse()
    _press(workshop, monkeypatch, start, row, workshop.curses.BUTTON1_RELEASED)
    picker.handle_mouse()
    hover = workshop.curses.REPORT_MOUSE_POSITION | workshop.curses.BUTTON1_PRESSED
    _press(workshop, monkeypatch, start, row, hover)
    picker.handle_mouse()

    assert applied == [(1, 1)]
    # A CLICKED without a preceding press is its own physical click.
    _press(workshop, monkeypatch, start, row, workshop.curses.BUTTON1_CLICKED)
    picker.handle_mouse()
    assert applied == [(1, 1), (1, 1)]


def test_unavailable_value_click_keeps_choice_and_names_the_exact_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    window = _GridWindow()
    reason = (
        "Docker daemon is not running (context colima-ci); start it with "
        "`colima start --profile ci`, then Launch again"
    )
    caps = {
        name: {"available": True, "reason": ""} for name in workshop.RUNTIME_POLICIES
    }
    caps["local-vm"] = {"available": False, "reason": reason}
    picker = _advanced_picker(workshop, monkeypatch, window, runtime_caps=caps)
    before = picker.runtime
    row, start, _end = _choice_targets(picker)[
        (1, workshop.RUNTIME_POLICIES.index("local-vm"))
    ]

    _press(workshop, monkeypatch, start + 2, row, workshop.curses.BUTTON1_PRESSED)
    picker.handle_mouse()

    assert picker.runtime == before
    assert picker.error == f"local-container: {reason}"
    picker.draw()
    unavailable = [
        window.text(r, 0, window.width).strip()
        for r in range(window.height)
        if window.text(r, 0, window.width).strip().startswith("Unavailable")
    ]
    assert (
        unavailable
        and "local-container: Docker daemon is not running" in unavailable[0]
    )


def test_resize_rebuilds_hitboxes_before_hit_testing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workshop = _load()
    window = _GridWindow(width=100)
    picker = _advanced_picker(workshop, monkeypatch, window)
    wide = _choice_targets(picker)[(0, 3)]

    window.width = 64  # the terminal shrinks after the last paint
    probe = _GridWindow(width=64)
    reference = _advanced_picker(workshop, monkeypatch, probe)
    narrow = _choice_targets(reference)[(0, 3)]
    assert narrow != wide

    _press(workshop, monkeypatch, narrow[1], narrow[0], workshop.curses.BUTTON1_PRESSED)
    picker.handle_mouse()

    assert picker.launch_mode == workshop.LAUNCH_MODES.index("operator")


def test_clipped_and_wide_layout_never_exceeds_the_visible_cells() -> None:
    workshop = _load()
    layout = workshop._choice_layout(("ąę", "日本語", "operator"), 10, 20)
    assert layout[0][:3] == (0, 10, 12)
    assert layout[1][:3] == (1, 13, 19)
    # The third token is cut to the ellipsis cell, never past the row end.
    assert layout[-1][2] <= 20
    assert all(end <= 20 for _index, _start, end, _fragment in layout)
    assert workshop._cell_width(workshop._clip("日本語テキスト", 7)) <= 7


def test_parent_row_click_cycles_parent_then_resume_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    window = _GridWindow()
    picker = _advanced_picker(workshop, monkeypatch, window)
    picker.path = str(tmp_path)
    monkeypatch.setattr(
        workshop,
        "parent_session_choices",
        lambda *_args, **_kwargs: (
            [
                SimpleNamespace(session_id="parent-a"),
                SimpleNamespace(session_id="parent-b"),
            ],
            "",
        ),
    )
    picker.draw()
    parent = next(t for t in picker.mouse_targets if t[4] == "parent")
    _press(workshop, monkeypatch, parent[1], parent[0], workshop.curses.BUTTON1_PRESSED)
    picker.handle_mouse()
    assert picker.continuity_parent == "parent-a"

    monkeypatch.setattr(
        workshop,
        "resumable_interactive_runs",
        lambda *_args, **_kwargs: [
            {
                "run_id": "init-worktree-1",
                "session_id": "019a-worktree",
                "environment": "local-worktrees",
                "root": str(tmp_path),
                "branch": "cut/codex-init-worktree-1",
                "updated_at": "2026-10-10T06:00:00Z",
            }
        ],
    )
    picker.launch_mode = workshop.LAUNCH_MODES.index("resume")
    picker.runtime = workshop.RUNTIME_POLICIES.index("local-worktrees")
    picker._reset_resume()
    picker.draw()
    session = next(t for t in picker.mouse_targets if t[4] == "parent")
    assert window.text(session[0], session[1], session[1] + 8) == "Session "
    _press(
        workshop, monkeypatch, session[1], session[0], workshop.curses.BUTTON1_PRESSED
    )
    picker.handle_mouse()
    assert picker.resume_run_id == "init-worktree-1"
    assert picker.session == "019a-worktree"
    picker.draw()
    assert "cut/codex-init-worktree-1" in window.text(session[0], 0, window.width)


def test_resume_launch_needs_a_concrete_session_before_any_tab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    launched, calls = _prepare_launch(
        workshop, tmp_path, monkeypatch, destination="vibecrafted", live=["vibecrafted"]
    )
    launched.launch_mode = workshop.LAUNCH_MODES.index("resume")
    launched.runtime = workshop.RUNTIME_POLICIES.index("local-worktrees")
    monkeypatch.setattr(
        workshop,
        "runtime_policy_capabilities",
        lambda _agent: {"local-worktrees": {"available": True, "reason": ""}},
    )
    monkeypatch.setattr(workshop, "resumable_interactive_runs", lambda *_a, **_k: [])

    launched.launch()

    assert calls == []
    assert launched.error.startswith("Resume needs a concrete session")
    assert "no earlier codex session in local-worktrees" in launched.error


def test_resume_launch_routes_the_recorded_run_into_this_tab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    launched, calls = _prepare_launch(
        workshop, tmp_path, monkeypatch, destination="vibecrafted", live=["vibecrafted"]
    )
    launched.launch_mode = workshop.LAUNCH_MODES.index("resume")
    launched.runtime = workshop.RUNTIME_POLICIES.index("local-vm")
    launched.permissions = workshop.PERMISSION_POLICIES.index("auto")
    monkeypatch.setattr(
        workshop,
        "runtime_policy_capabilities",
        lambda _agent: {"local-vm": {"available": True, "reason": ""}},
    )
    monkeypatch.setattr(
        workshop,
        "resolve_provider_policy",
        lambda *_args: SimpleNamespace(supported=True, reason=""),
    )
    monkeypatch.setattr(
        workshop,
        "resumable_interactive_runs",
        lambda *_a, **_k: [
            {
                "run_id": "init-container-7",
                "session_id": "019a-container",
                "environment": "local-vm",
                "root": str(tmp_path),
                "branch": "",
                "updated_at": "",
            }
        ],
    )
    launched._cycle_resume(1)

    launched.launch()

    assert launched.error == ""
    tab = calls[0]
    command = tab[tab.index("--") + 1 :]
    assert command[:7] == [
        "vibecrafted",
        "resume",
        "codex",
        "--runtime",
        "plain",
        "--permissions",
        "auto",
    ]
    assert command[command.index("--run-id") + 1] == "init-container-7"
    assert command[command.index("--policy-runtime") + 1] == "local-vm"
    assert "--root" not in command
    assert tab[tab.index("--name") + 1].endswith(" · container")


def test_memory_parent_is_never_guessed_from_the_launcher_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    seen: list[dict] = []

    def capture(*_args, **kwargs):
        seen.append(dict(kwargs.get("env") or {}))
        return {
            "full-lineage": {
                "available": False,
                "reason": "no explicit/current parent lineage id",
            },
            "fresh": {"available": True, "reason": ""},
            "bare-fork": {"available": False, "reason": "expert-only"},
        }

    monkeypatch.setenv("VIBECRAFTED_RUN_ID", "ambient-run-of-whoever-opened-this-pane")
    monkeypatch.setenv("CODEX_SESSION_ID", "ambient-codex")
    monkeypatch.setattr(workshop, "continuity_policy_capabilities", capture)
    picker = workshop.Workshop(SimpleNamespace(), mode="launcher")
    picker.path = str(tmp_path)
    picker.continuity = workshop.CONTINUITY_MODES.index("full-lineage")
    picker.continuity_explicit = True

    picker._normalize_continuity_choice()

    # An explicit full-lineage pick is not silently turned into fresh.
    assert picker.continuity == workshop.CONTINUITY_MODES.index("full-lineage")
    caps = picker._continuity_caps()
    assert caps["full-lineage"]["available"] is False
    assert seen and all(
        "VIBECRAFTED_RUN_ID" not in env and "CODEX_SESSION_ID" not in env
        for env in seen
    )
    assert workshop.public_reason(caps["full-lineage"]["reason"]).startswith(
        "Memory full-lineage needs a parent"
    )


def test_slots_keep_their_own_environment_permissions_mode_and_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(workshop, "vibecrafted_home", lambda: tmp_path / "home")
    first = workshop.Workshop(SimpleNamespace(), mode="launcher")
    first.path = first.choices_root = str(tmp_path)
    first.runtime = workshop.RUNTIME_POLICIES.index("local-worktrees")
    first.permissions = workshop.PERMISSION_POLICIES.index("auto")
    first.launch_mode = workshop.LAUNCH_MODES.index("partner")
    first.continuity = workshop.CONTINUITY_MODES.index("fresh")
    first.continuity_explicit = True
    first.add_agent()
    first.agent = workshop.AGENTS.index("claude")
    first.runtime = workshop.RUNTIME_POLICIES.index("local-vm")
    first.permissions = workshop.PERMISSION_POLICIES.index("bypass")
    first.launch_mode = workshop.LAUNCH_MODES.index("init")
    first.save_choices()

    data = json.loads(first._choice_path().read_text())
    assert [slot["runtime"] for slot in data["slots"]] == [
        "local-worktrees",
        "local-vm",
    ]
    assert [slot["permissions"] for slot in data["slots"]] == ["auto", "bypass"]
    assert [slot["launch_mode"] for slot in data["slots"]] == ["partner", "init"]

    monkeypatch.setenv("VIBECRAFTED_WORKSPACE_ROOT", str(tmp_path))
    again = workshop.Workshop(SimpleNamespace(), mode="launcher")
    assert len(again.agent_slots) == 2
    assert again.agent_slots[0]["runtime"] == workshop.RUNTIME_POLICIES.index(
        "local-worktrees"
    )
    assert again.agent_slots[0]["continuity_explicit"] is True
    assert again.agent_slots[1]["runtime"] == workshop.RUNTIME_POLICIES.index(
        "local-vm"
    )
    assert again.runtime == workshop.RUNTIME_POLICIES.index("local-worktrees")


@pytest.mark.parametrize("runtime", ["local-native", "local-worktrees", "local-vm"])
@pytest.mark.parametrize("mode", ["init", "resume", "partner", "operator"])
def test_launch_matrix_three_environments_by_four_modes(
    tmp_path: Path, runtime: str, mode: str
) -> None:
    workshop = _load()
    session = "019a0000-0000-7000-8000-000000000042"
    argv = workshop.launch_argv(
        "codex",
        mode,
        runtime,
        "auto",
        continuity="fresh",
        workspace=tmp_path,
        model="gpt-6.1-sol",
        effort="high",
        session=session if mode == "resume" else "",
        run_id="init-recorded-1"
        if mode == "resume" and runtime != "local-native"
        else "",
    )
    assert argv[:3] == [
        "vibecrafted",
        "init" if mode != "resume" else "resume",
        "codex",
    ]
    assert argv[argv.index("--runtime") + 1] == "plain"
    assert argv[argv.index("--permissions") + 1] == "auto"
    assert argv[argv.index("--model") + 1] == "gpt-6.1-sol"
    assert argv[argv.index("--effort") + 1] == "high"
    if mode == "resume":
        if runtime == "local-native":
            assert argv[argv.index("--session") + 1] == session
            assert argv[argv.index("--root") + 1] == str(tmp_path.resolve())
        else:
            assert argv[argv.index("--run-id") + 1] == "init-recorded-1"
            assert "--root" not in argv
            assert argv[argv.index("--policy-runtime") + 1] == runtime
    else:
        assert argv[argv.index("--policy-runtime") + 1] == runtime
        assert argv[argv.index("--root") + 1] == str(tmp_path.resolve())
        prompt = {"partner": "/vc-partner", "operator": "/vc-operator"}.get(mode)
        if prompt:
            assert argv[argv.index("--prompt") + 1] == prompt
