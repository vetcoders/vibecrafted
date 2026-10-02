from __future__ import annotations

import importlib.util
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
    assert workshop.launch_argv("claude", "resume") == [
        "vibecrafted",
        "resume",
        "claude",
    ]
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
    assert workshop.launch_argv("kimi", "resume") == [
        "vibecrafted",
        "resume",
        "kimi",
    ]


def test_default_provider_stays_codex_with_kimi_on_the_row() -> None:
    workshop = _load()

    assert workshop.AGENTS[2] == "codex"
    picker = workshop.Workshop(SimpleNamespace(), mode="launcher")

    assert picker.agent == workshop.AGENTS.index("codex")


def test_provider_choices_render_every_catalog_provider(monkeypatch):
    workshop, form = _wizard(monkeypatch)
    form.draw()
    labels = [text[2:] for text, kind, _, _ in form.items if kind == "provider"]
    assert sorted(labels) == sorted(workshop.AGENTS)
    assert any(text.startswith("● codex") for text, *_ in form.items)


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

    assert tuple(native) == ("init", "resume", "partner", "operator")
    assert native["partner"]["available"] == native["init"]["available"]
    assert native["operator"]["available"] == native["init"]["available"]
    assert native["resume"]["available"] is True
    assert worktree["resume"] == {
        "available": False,
        "reason": "resume is supported only in local-native runtime",
    }


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
    resume = workshop.launch_argv("codex", "resume", workspace=tmp_path)

    assert partner[-4:] == ["--root", str(tmp_path), "--prompt", "/vc-partner"]
    assert operator[-4:] == ["--root", str(tmp_path), "--prompt", "/vc-operator"]
    assert resume[-2:] == ["--root", str(tmp_path)]


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
    launched = workshop.Workshop(_WizardWindow(), mode="launcher")
    launched.path = str(tmp_path)
    launched.agent = workshop.AGENTS.index("codex")
    launched.launch_mode = workshop.LAUNCH_MODES.index("partner")
    launched.runtime = workshop.RUNTIME_POLICIES.index("local-native")
    launched.permissions = workshop.PERMISSION_POLICIES.index("bypass")
    launched.continuity = workshop.CONTINUITY_MODES.index("fresh")
    calls: list[object] = []
    admission_path = tmp_path / "admission.json"
    admission_path.write_text(
        __import__("json").dumps(
            {"run_id": "init-controlled-test", "effective_worker_root": str(tmp_path)}
        )
    )

    def admit(agent, prompt, runtime, permissions, root, **kwargs):
        argv = workshop.launch_argv(
            agent,
            "init",
            runtime,
            permissions,
            workspace=root,
            model=kwargs.get("model", ""),
            prompt=prompt,
        )
        return [*argv, "--admission-file", str(admission_path)]

    monkeypatch.setattr(workshop, "interactive_workspace_command", admit)
    monkeypatch.setattr(
        workshop, "lookup_runtime_run_meta", lambda _: {"status": "active"}
    )
    monkeypatch.setattr(workshop, "finish_meta", lambda *_: None)
    monkeypatch.setattr(
        workshop,
        "runtime_policy_capabilities",
        lambda _agent: {
            name: {
                "available": name == "local-native",
                "reason": "disabled by controlled capability",
            }
            for name in workshop.RUNTIME_POLICIES
        },
    )
    monkeypatch.setattr(
        workshop,
        "continuity_policy_capabilities",
        lambda *_args, **_kwargs: {
            name: {"available": name == "fresh", "reason": "controlled continuity"}
            for name in workshop.CONTINUITY_MODES
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
    monkeypatch.setattr(workshop, "current_frame_session", lambda: current)
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


def test_require_live_destination_refuses_missing_and_wrong_context() -> None:
    workshop = _load()
    live = ["loctree", "vibecrafted"]
    workshop.require_live_destination("vibecrafted", live)
    with pytest.raises(ValueError, match="No live Frame session"):
        workshop.require_live_destination("codescribe", live)
    with pytest.raises(ValueError, match="No live Frame session"):
        workshop.require_live_destination("vibecrafted", [])
    with pytest.raises(ValueError, match="could not resolve"):
        workshop.require_live_destination("  ", live)


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

    assert launched.mode == "home"
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
    assert command[command.index("--root") + 1] == str(tmp_path)
    assert command[command.index("--prompt") + 1] == "/vc-partner"
    assert ["vc-frame", "attach", "vibecrafted"] in calls


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

    assert launched.mode == "home"
    assert launched.error == ""
    assert len([c for c in calls if "new-tab" in c]) == 1
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

    assert launched.mode == "home"
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
    assert "No live Frame session" in launched.error
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
    assert launched.error == message
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
    with pytest.raises(ValueError, match="H2b2"):
        workshop.launch_argv("claude", "resume", "local-worktrees", "auto")


def test_runtime_help_is_user_facing_without_false_recommendation() -> None:
    workshop = _load()
    help_text = " ".join(
        line for detail in workshop.RUNTIME_HELP.values() for line in detail
    )

    assert "This checkout, shared with you." in help_text
    assert "A separate working copy for this Agent." in help_text
    assert "Not available yet." in help_text
    assert "canonical worktree" not in help_text
    assert "admission" not in help_text
    assert "child-usage" not in help_text
    assert "Operator Agent" not in help_text
    assert "H2b3" not in help_text
    assert "--operator" not in help_text
    # No lane is recommended, and no gated or disabled lane reads as available.
    assert "recommended" not in help_text.casefold()
    assert "live usage can be verified" in " ".join(
        workshop.RUNTIME_HELP["local-worktrees"]
    )
    assert workshop.RUNTIME_HELP["local-vm"][0] == "Not available yet."
    assert workshop.RUNTIME_HELP["cloud-soon"][0] == "Not available yet."


def test_workspace_path_is_full_resolved_and_must_exist(tmp_path: Path) -> None:
    workshop = _load()
    child = tmp_path / "project"
    child.mkdir()

    assert workshop.normalized_workspace("project", base=tmp_path) == child.resolve()
    with pytest.raises(ValueError, match="does not exist"):
        workshop.normalized_workspace("missing", base=tmp_path)


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

        def get_wch(self) -> int:
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
def test_wizard_advanced_choices_are_scrollable_and_mouse_selectable(
    width, monkeypatch
):
    _workshop, form = _wizard(monkeypatch, size=(24, width))
    form.advanced = True
    for kind in (
        "path",
        "mode",
        "permissions",
        "continuity",
        "continuity_parent",
        "parents",
        "advanced",
    ):
        _focus(form, kind)
        form.draw()
        assert any(
            target[-1] == "item" and target[3] == form.focus
            for target in form.mouse_targets
        )
        assert all(
            0 <= row < 24 and col + len(text) <= width
            for row, col, text, _ in form.window.writes
        )


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
        == "Needs live usage metering; only claude today"
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


def test_provider_change_clears_only_provider_dependents(monkeypatch):
    workshop, form = _wizard(monkeypatch)
    form.agent = workshop.AGENTS.index("claude")
    form.continuity_parent = "claude-parent"
    form.model_pin = "claude-model"
    form.prompt = "Keep my prompt"
    form.parent_sessions = [object()]
    _focus(form, "provider", workshop.AGENTS.index("codex"))
    _click_focus(workshop, form, monkeypatch)
    assert workshop.AGENTS[form.agent] == "codex"
    assert form.parent_sessions == [] and form.continuity_parent == ""
    assert form.model_pin == "" and form.prompt == "Keep my prompt"
    assert "Prompt kept" in form.notice


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


def test_project_field_accepts_spaces_without_cycling_provider(monkeypatch):
    workshop, form = _wizard(monkeypatch)
    form.advanced = True
    form.path = "/tmp/My"
    _focus(form, "path")
    form.handle_launcher_key(ord(" "))
    assert form.path == "/tmp/My "
    assert workshop.AGENTS[form.agent] == "codex"


@pytest.mark.parametrize("provider", ["claude", "codex", "kimi", "copilot"])
@pytest.mark.parametrize("activation", ["enter", "click"])
def test_provider_click_then_review_launch_keeps_project(
    tmp_path, monkeypatch, provider, activation
):
    workshop = _load()
    project = tmp_path / "My Project"
    project.mkdir()
    form, calls = _prepare_launch(
        workshop,
        project,
        monkeypatch,
        destination="project-workspace",
        live=["project-workspace"],
        current="project-workspace",
    )
    form.launch_mode = 0
    for name in (
        "_normalize_runtime_choice",
        "_normalize_permission_choice",
        "_normalize_mode_choice",
        "_normalize_continuity_choice",
    ):
        monkeypatch.setattr(workshop.Workshop, name, lambda _self: None)
    _focus(form, "provider", workshop.AGENTS.index(provider))
    _click_focus(workshop, form, monkeypatch)
    form.handle_launcher_key(workshop.curses.KEY_F6)
    _focus(form, "launch")
    if activation == "enter":
        form.handle_launcher_key(10)
    else:
        _click_focus(workshop, form, monkeypatch)
    form.launch()  # repeat activation is inert
    assert form.error == "" and len([c for c in calls if "new-tab" in c]) == 1
    argv = calls[0]
    assert argv[argv.index("--session") + 1] == "project-workspace"
    assert argv[argv.index("--cwd") + 1] == str(project)
    assert argv[argv.index("--root") + 1] == str(project)
    assert argv[argv.index("--") + 3] == provider


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
    assert "Launch" not in form_text  # launch exists only on Review
    assert "┌" not in form_text
    assert "canonical worktree" not in form_text
    assert "• agy" not in form_text


def test_selected_provider_and_focus_have_distinct_visible_markers(monkeypatch):
    workshop, form = _wizard(monkeypatch)
    _focus(form, "provider", 0)
    form.draw()
    assert any(
        text == "● codex" and attr & workshop.curses.A_BOLD
        for _, _, text, attr in form.window.writes
    )
    assert any(
        text == "›" and attr & workshop.curses.A_BOLD
        for _, _, text, attr in form.window.writes
    )
    assert not any(
        attr & workshop.curses.A_UNDERLINE for _, _, _, attr in form.window.writes
    )


def test_prompt_newlines_unicode_and_cursor_edits_cannot_launch(monkeypatch):
    workshop, form = _wizard(monkeypatch)
    launches = []
    monkeypatch.setattr(form, "launch", lambda: launches.append(True))
    form._goto_step(4)
    _focus(form, "prompt")
    for char in "zażółć":
        form.handle_launcher_key(char)
    form.handle_launcher_key(10)
    for char in "hello":
        form.handle_launcher_key(ord(char))
    form.handle_launcher_key(workshop.curses.KEY_LEFT)
    form.handle_launcher_key(workshop.curses.KEY_BACKSPACE)
    assert form.prompt == "zażółć\nhelo" and launches == []
    for step in range(6):
        form.handle_launcher_key(workshop.curses.KEY_F1 + step)
        assert form.prompt == "zażółć\nhelo"
    for step in range(4, -1, -1):
        form.handle_launcher_key(27)
        assert form.step == step and form.prompt == "zażółć\nhelo"
    form.handle_launcher_key(27)
    assert form.confirm_exit
    form.handle_launcher_key(27)
    assert not form.confirm_exit and form.prompt


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
    assert workshop.public_reason(message) == (
        "Needs live usage metering; only claude today"
    )


def test_every_advanced_selection_accepts_keyboard_and_click(monkeypatch):
    workshop, form = _wizard(monkeypatch)
    form.advanced = True
    for kind, index, field in (
        ("mode", 2, "launch_mode"),
        ("permissions", 1, "permissions"),
        ("continuity", 1, "continuity"),
    ):
        _focus(form, kind, index)
        _click_focus(workshop, form, monkeypatch)
        assert getattr(form, field) == index
        _focus(form, kind, 0)
        form.handle_launcher_key(10)
        assert getattr(form, field) == 0


def test_advanced_toggle_and_project_text_keep_draft(monkeypatch):
    workshop, form = _wizard(monkeypatch)
    form.prompt = "draft"
    _focus(form, "advanced")
    _click_focus(workshop, form, monkeypatch)
    assert form.advanced
    _focus(form, "path")
    previous = form.path
    form.handle_launcher_key(ord("a"))
    assert form.path == previous + "a" and form.advanced
    form.handle_launcher_key(27)
    assert not form.advanced and form.prompt == "draft"


def test_small_wizard_all_steps_and_resize_keep_controls_reachable(monkeypatch):
    _workshop, form = _wizard(monkeypatch, size=(12, 40))
    for size in ((12, 40), (8, 28), (24, 80), (14, 50)):
        form.window.size = size
        for step in range(6):
            form._goto_step(step)
            _focus(form, "launch" if step == 5 else "next")
            form.draw()
            assert any(
                target[-1] == "item" and target[3] == form.focus
                for target in form.mouse_targets
            )
            assert all(
                0 <= row < size[0] and col + len(text) <= size[1]
                for row, col, text, _ in form.window.writes
            )


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


class _WizardWindow:
    def __init__(self, size=(24, 80)):
        self.size = size
        self.writes = []

    def getmaxyx(self):
        return self.size

    def addstr(self, row, col, text, attr=0):
        self.writes.append((row, col, text, attr))

    def erase(self):
        self.writes.clear()

    def refresh(self):
        pass


def _wizard(monkeypatch, size=(24, 80)):
    workshop = _load()
    monkeypatch.setattr(
        workshop,
        "runtime_policy_capabilities",
        lambda agent: {
            name: {
                "available": name in ("local-native", "local-worktrees"),
                "reason": "no canonical VM entrypoint",
            }
            for name in workshop.RUNTIME_POLICIES
        },
    )
    monkeypatch.setattr(
        workshop,
        "resolve_provider_policy",
        lambda *a: SimpleNamespace(supported=True, reason=""),
    )
    monkeypatch.setattr(
        workshop,
        "continuity_policy_capabilities",
        lambda *a, **k: {
            name: {"available": True, "reason": ""}
            for name in workshop.CONTINUITY_MODES
        },
    )
    form = workshop.Workshop(_WizardWindow(size), mode="launcher")
    form.runtime = 0
    return workshop, form


def _focus(form, kind, index=None):
    form.items = form._launcher_items(max(1, form.window.size[1] - 3))
    form.focus = next(
        i
        for i, item in enumerate(form.items)
        if item[1] == kind and (index is None or item[2] == index)
    )
    form._show_focus()


def _click_focus(workshop, form, monkeypatch):
    form.draw()
    row, start, _, _, _ = next(
        target
        for target in form.mouse_targets
        if target[-1] == "item" and target[3] == form.focus
    )
    monkeypatch.setattr(
        workshop.curses,
        "getmouse",
        lambda: (0, start, row, 0, workshop.curses.BUTTON1_CLICKED),
    )
    form.handle_mouse()


def test_bracketed_paste_is_text_even_with_wizard_hotkeys(monkeypatch):
    _workshop, form = _wizard(monkeypatch)
    form._goto_step(4)
    _focus(form, "prompt")
    source = iter("[200~a\nLaunch\r\nzażółć\x1b[201~")
    form.window.get_wch = lambda: next(source)
    form.window.timeout = lambda _: None
    form._escape_or_paste()
    assert form.prompt == "a\nLaunch\nzażółć"
    assert form.step == 4 and not form.launched


def test_failed_pane_launch_keeps_draft_and_retry_is_single(tmp_path, monkeypatch):
    workshop = _load()
    form, _calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination="here",
        live=["here"],
        current="here",
    )
    form.prompt = "safe task\nsecond line"
    form.model_pin = "provider-model"
    form._goto_step(5)
    attempts = []

    def transport(command, **kwargs):
        attempts.append(command)
        return SimpleNamespace(
            returncode=2 if len(attempts) == 1 else 0,
            stdout="",
            stderr="controlled start failure",
        )

    monkeypatch.setattr(workshop.subprocess, "run", transport)
    form.launch()
    assert form.error == "controlled start failure" and not form.launched
    assert (
        form.prompt == "safe task\nsecond line" and form.model_pin == "provider-model"
    )
    form.launch()
    form.launch()
    assert len([c for c in attempts if "new-tab" in c]) == 2 and form.launched
    assert attempts[1][attempts[1].index("--model") + 1] == "provider-model"
    assert attempts[1][attempts[1].index("--prompt") + 1].endswith(form.prompt)


def test_runtime_failure_returns_form_from_owner_status(tmp_path, monkeypatch):
    workshop = _load()
    form, calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination="here",
        live=["here"],
        current="here",
    )
    monkeypatch.setattr(
        workshop, "lookup_runtime_run_meta", lambda _: {"status": "prepared"}
    )
    form.prompt = "draft"
    form.launch()
    assert (
        form.mode == "launcher" and form.launched and form.launch_status == "prepared"
    )
    form.launch()
    assert len([c for c in calls if "new-tab" in c]) == 1
    monkeypatch.setattr(
        workshop,
        "lookup_runtime_run_meta",
        lambda _: {"status": "failed", "error": "provider refused model"},
    )
    form._refresh_launch_state()
    assert form.mode == "launcher" and not form.launched and form.prompt == "draft"
    assert form.error == "provider refused model"


def test_attach_failure_cannot_duplicate_admitted_tab(tmp_path, monkeypatch):
    workshop = _load()
    form, _ = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination="there",
        live=["there"],
        current="here",
    )
    calls = []

    def transport(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=2 if "attach" in command else 0,
            stdout="",
            stderr="attach failed",
        )

    monkeypatch.setattr(workshop.subprocess, "run", transport)
    form.launch()
    form.launch()
    assert len(calls) == 2 and form.launched and form.error == "attach failed"


def test_disabled_runtime_reason_wraps_and_has_no_mouse_target(monkeypatch):
    _workshop, form = _wizard(monkeypatch, size=(12, 32))
    form._goto_step(3)
    form.draw()
    assert not any(kind == "runtime" for _, kind, _, _ in form.items)
    reason = " ".join(text for text, kind, _, _ in form.items if not kind)
    assert "no canonical VM entrypoint" in reason
    assert all(len(text) <= 29 for text, kind, _, _ in form.items if not kind)


def test_prompt_and_model_use_canonical_command_transport():
    workshop = _load()
    argv = workshop.launch_argv(
        "codex", "partner", model="provider-model", prompt="first\nsecond"
    )
    assert argv[argv.index("--model") + 1] == "provider-model"
    assert argv[argv.index("--prompt") + 1] == "/vc-partner\n\nfirst\nsecond"
    with pytest.raises(ValueError, match="Resume selects"):
        workshop.launch_argv("codex", "resume", prompt="draft")


def test_resume_keeps_new_session_draft_without_sending_it(tmp_path, monkeypatch):
    workshop = _load()
    form, calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination="here",
        live=["here"],
        current="here",
    )
    form.launch_mode = workshop.LAUNCH_MODES.index("resume")
    form.prompt = "new conversation draft"
    form.model_pin = "new-model"
    form._goto_step(1)
    form.draw_launcher()
    assert not any(
        kind in {"model", "model_pin", "default_model"} for _, kind, _, _ in form.items
    )
    form._goto_step(4)
    form.draw_launcher()
    assert not any(kind == "prompt" for _, kind, _, _ in form.items)
    form.launch()
    pane = next(command for command in calls if "new-tab" in command)
    assert "--model" not in pane and "--prompt" not in pane
    assert form.prompt == "new conversation draft" and form.model_pin == "new-model"


@pytest.mark.parametrize("runtime", ["local-native", "local-worktrees"])
def test_workshop_real_admission_and_pty_provider_contract(
    tmp_path, monkeypatch, runtime
):
    """Real owner and provider process; only Frame transport is controlled."""
    import json
    import pty
    import select
    import time

    from vibecrafted_core import control_plane

    workshop = _load()
    root = _git_checkout(
        tmp_path / "project", origin="https://github.com/Fixture/project.git"
    )
    (root / "README.md").write_text("fixture\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.org",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=root,
        check=True,
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "provider.json"
    provider = fake_bin / "claude"
    provider.write_text(
        f"#!{sys.executable}\n" + "import json,os,sys,pathlib\n"
        "if '--help' in sys.argv: print('--session-id <uuid>'); sys.exit(0)\n"
        "if '--version' in sys.argv: print('2.1.232 (Claude Code)'); sys.exit(0)\n"
        "pathlib.Path(os.environ['SMOKE_CAPTURE']).write_text(json.dumps({'argv':sys.argv[1:], 'cwd':os.getcwd(), 'run_id':os.environ['VIBECRAFTED_RUN_ID'], 'tty':os.isatty(0)}))\n"
        "print('CONTROLLED_PROVIDER_OK', flush=True)\n"
    )
    provider.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("SMOKE_CAPTURE", str(capture))
    monkeypatch.setenv("VIBECRAFTED_INTERACTIVE_IMPORT_ROOT", str(SCRIPT.parents[3]))
    monkeypatch.setattr(
        workshop, "destination_session_for_workspace", lambda _: "fixture-workspace"
    )
    monkeypatch.setattr(
        workshop, "list_live_frame_sessions", lambda: (["fixture-workspace"], "")
    )
    monkeypatch.setattr(workshop, "current_frame_session", lambda: "fixture-workspace")
    transport_run = subprocess.run
    output = bytearray()
    commands = []

    def transport(command, **kwargs):
        if command[0] != "vc-frame":
            return transport_run(command, **kwargs)
        commands.append(command)
        if "new-tab" not in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        master, slave = pty.openpty()
        child = subprocess.Popen(
            command[command.index("--") + 1 :],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
        )
        os.close(slave)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.05)[0]:
                    try:
                        output.extend(os.read(master, 65536))
                    except OSError:
                        break
                if child.poll() is not None:
                    break
            assert child.wait(timeout=2) == 0, output.decode(errors="replace")
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=3)
            os.close(master)
        return SimpleNamespace(returncode=0, stdout="1", stderr="")

    monkeypatch.setattr(workshop.subprocess, "run", transport)
    form = workshop.Workshop(_WizardWindow(), mode="launcher")
    form.agent = workshop.AGENTS.index("claude")
    form.runtime = workshop.RUNTIME_POLICIES.index(runtime)
    form.permissions = workshop.PERMISSION_POLICIES.index("read-only")
    form.continuity = workshop.CONTINUITY_MODES.index("fresh")
    form.path = str(root)
    form.model_pin = "fixture-model"
    form.prompt = "Do not edit files. Reply CONTROLLED_PROVIDER_OK.\nSecond line."
    form._goto_step(5)
    _focus(form, "launch")
    form.handle_launcher_key(10)
    assert form.error == "", form.error
    recorded = json.loads(capture.read_text())
    assert recorded["tty"] is True
    assert recorded["argv"][recorded["argv"].index("--model") + 1] == "fixture-model"
    delivered_prompt = Path(recorded["argv"][-1].split("private task file: ", 1)[1])
    assert form.prompt in delivered_prompt.read_text()
    assert form.launch_run_id == recorded["run_id"]
    receipt = control_plane.lookup_runtime_run_meta(recorded["run_id"])
    assert receipt["status"] == "completed" and receipt["exit_code"] == 0
    assert receipt["model_requested"] == "fixture-model"
    assert Path(recorded["cwd"]) == Path(receipt["effective_worker_root"])
    assert Path(receipt["parent_root"]) == root
    if runtime == "local-worktrees":
        assert Path(recorded["cwd"]) != root
        assert receipt["worktree"] is True
    else:
        assert Path(recorded["cwd"]) == root
    form.launch()
    assert len([command for command in commands if "new-tab" in command]) == 1


def test_sgr_mouse_fallback_uses_same_hit_targets_not_escape(monkeypatch):
    _workshop, form = _wizard(monkeypatch, size=(12, 40))
    form._goto_step(4)
    form.draw()
    keys = iter("[<0;3;2M")
    form.window.get_wch = lambda: next(keys)
    form.window.timeout = lambda _: None
    form._escape_or_paste()
    assert form.step == 0
    form._goto_step(4)
    form.draw()
    keys = iter("[<0;3;2m")
    form._escape_or_paste()
    assert form.step == 4
