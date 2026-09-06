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


def test_choice_markers_are_unboxed_and_selected_once() -> None:
    workshop = _load()

    tokens = workshop._choice_tokens(
        ("init", "resume", "operator"),
        selected=1,
        available=(True, True, False),
    )

    assert tokens == ("init", "• resume", "× operator")
    assert sum(token.startswith("• ") for token in tokens) == 1
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

    class FakeWindow:
        def bkgd(self, ch: str, attr: int) -> None:
            calls.append(("bkgd", ch, attr))

        def bkgdset(self, ch: str, attr: int) -> None:
            calls.append(("bkgdset", ch, attr))

    original = workshop.curses
    workshop.curses = FakeCurses()  # type: ignore[misc]
    try:
        attr = workshop.bind_terminal_paper(FakeWindow())  # type: ignore[arg-type]
    finally:
        workshop.curses = original

    assert attr == 256
    assert "use_default" in calls
    assert ("pair", 1, -1, -1) in calls
    assert ("bkgd", " ", 256) in calls
    assert ("bkgdset", " ", 256) in calls


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


def test_parent_picker_uses_canonical_session_catalog(tmp_path: Path) -> None:
    workshop = _load()
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
            assert kwargs["project"] == f"/{tmp_path.name}"
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


def test_successful_launch_renames_and_replaces_the_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    launched = workshop.Workshop(SimpleNamespace(), mode="launcher")
    launched.path = str(tmp_path)
    launched.agent = workshop.AGENTS.index("codex")
    launched.launch_mode = workshop.LAUNCH_MODES.index("partner")
    launched.runtime = workshop.RUNTIME_POLICIES.index("local-native")
    launched.permissions = workshop.PERMISSION_POLICIES.index("bypass")
    launched.continuity = workshop.CONTINUITY_MODES.index("fresh")
    calls: list[object] = []

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
    monkeypatch.setattr(workshop.shutil, "which", lambda _name: "/bin/vibecrafted")
    monkeypatch.setattr(
        workshop.subprocess,
        "run",
        lambda command, **_kwargs: (
            calls.append(("rename", command))
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(workshop.curses, "endwin", lambda: calls.append("endwin"))
    monkeypatch.setattr(workshop.os, "chdir", lambda path: calls.append(("cwd", path)))
    monkeypatch.setattr(
        workshop.os,
        "execvpe",
        lambda executable, argv, env: calls.append(("exec", executable, argv, env)),
    )

    launched.launch()

    assert calls[0] == (
        "rename",
        [
            "vc-frame",
            "action",
            "rename-pane",
            f"codex · partner · {tmp_path.name}",
        ],
    )
    assert calls[1:3] == ["endwin", ("cwd", tmp_path)]
    exec_call = calls[3]
    assert isinstance(exec_call, tuple)
    assert exec_call[0:2] == ("exec", "/bin/vibecrafted")
    assert exec_call[2][-4:] == [
        "--root",
        str(tmp_path),
        "--prompt",
        "/vc-partner",
    ]


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


def test_runtime_help_preserves_product_truth_and_recommended_default() -> None:
    workshop = _load()
    help_text = " ".join(
        line for detail in workshop.RUNTIME_HELP.values() for line in detail
    )

    assert "no isolation" in help_text
    assert "full disk scope per provider permissions" in help_text
    assert "Shared checkout, no worktrees" in help_text
    assert "Safe recommended local default" in help_text
    assert "one canonical worktree per Agent launch" in help_text
    assert "Maximum local concurrency" in help_text
    assert "unattended pipelines require an Operator Agent" in help_text
    assert "--operator auto or claude" in help_text
    assert "Coming in H2b3" in help_text
    assert "selected-workspace container launch and live proof" in help_text
    assert "Coming soon; disabled" in help_text


def test_workspace_path_is_full_resolved_and_must_exist(tmp_path: Path) -> None:
    workshop = _load()
    child = tmp_path / "project"
    child.mkdir()

    assert workshop.normalized_workspace("project", base=tmp_path) == child.resolve()
    with pytest.raises(ValueError, match="does not exist"):
        workshop.normalized_workspace("missing", base=tmp_path)


def test_dashboard_projects_only_human_agent_faces_from_agents_tab() -> None:
    workshop = _load()
    payload = [
        {
            "tab_name": "Agents",
            "title": "Sessions",
            "is_plugin": True,
        },
        {"tab_name": "Agents", "pane_title": "Agent Workspaces"},
        {"tab_name": "Agents", "pane_title": "codex · resume · vibecrafted"},
        {"tab_name": "Agents", "pane_title": "claude · init · vibecrafted"},
        {"tab_name": "Shell", "pane_title": "Shell"},
        {"tab_name": "Agents", "pane_title": "codex · resume · vibecrafted"},
    ]

    assert workshop.agent_faces_from_payload(payload) == [
        "codex · resume · vibecrafted",
        "claude · init · vibecrafted",
    ]


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
