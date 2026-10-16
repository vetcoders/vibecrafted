"""Project launcher delivery, using Frame/provider doubles instead of paid calls."""

from pathlib import Path
from types import SimpleNamespace

from test_agent_workshop import _load, _prepare_launch
from test_frame_workday_project_launcher_entry import pane_runtime  # noqa: F401


def _three(tmp_path, monkeypatch):
    workshop = _load()
    picker, calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination="selected",
        live=["selected", "neighbor"],
        current="selected",
    )
    monkeypatch.setattr(
        workshop,
        "continuity_policy_capabilities",
        lambda *args, **kwargs: {
            name: {"available": name == "fresh", "reason": ""}
            for name in workshop.CONTINUITY_MODES
        },
    )
    picker.model, picker.effort = "exact-codex-model", "low"
    for provider, effort in (("claude", "medium"), ("grok", "high")):
        picker.add_agent()
        picker._select_agent(workshop.AGENTS.index(provider))
        picker.runtime = workshop.RUNTIME_POLICIES.index("local-native")
        picker.continuity = workshop.CONTINUITY_MODES.index("fresh")
        picker.model, picker.effort = f"exact-{provider}-model", effort
    return workshop, picker, calls


def test_launch_three_agents_to_selected_workspace(tmp_path, monkeypatch):
    _workshop, picker, calls = _three(tmp_path, monkeypatch)
    picker.launch()
    panes = [c for c in calls if isinstance(c, list) and "new-tab" in c]
    assert len(panes) == 3
    assert all(c[c.index("--session") + 1] == "selected" for c in panes)
    assert all(c[c.index("--root") + 1] == str(tmp_path) for c in panes)
    assert [c[c.index("--model") + 1] for c in panes] == [
        "exact-codex-model",
        "exact-claude-model",
        "exact-grok-model",
    ]
    assert [c[c.index("--effort") + 1] for c in panes] == ["low", "medium", "high"]
    assert [r["status"] for r in picker.launch_results] == ["opened"] * 3
    picker.launch()
    assert len(calls) == 3


def test_model_effort_persist_per_project_provider(tmp_path, monkeypatch):
    workshop = _load()
    monkeypatch.setenv("VIBECRAFTED_WORKSPACE_ROOT", str(tmp_path))
    picker = workshop.Workshop(SimpleNamespace(), mode="launcher")
    picker.model = "exact-codex-model"
    picker.effort = "high"
    picker.purpose = 2
    picker.save_choices()
    picker._select_agent(workshop.AGENTS.index("claude"))
    assert picker.model != "exact-codex-model"
    picker.model = "exact-claude-model"
    picker.effort = "medium"
    picker.save_choices()
    restored = workshop.Workshop(SimpleNamespace(), mode="launcher")
    assert restored.model == "exact-claude-model"
    restored._select_agent(workshop.AGENTS.index("codex"))
    assert (restored.model, restored.effort, restored.purpose) == (
        "exact-codex-model",
        "high",
        2,
    )
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("VIBECRAFTED_WORKSPACE_ROOT", str(other))
    assert (
        workshop.Workshop(SimpleNamespace(), mode="launcher").model
        != "exact-codex-model"
    )
    # Storage alone is not delivery: the admission must carry the selected
    # effort to the interactive runner, without using a global config edit.
    launched, calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination="selected",
        live=["selected"],
        current="selected",
    )
    launched.model = restored.model
    launched.effort = restored.effort
    launched.launch()
    assert launched.launch_results[0]["status"] == "opened", launched.error
    assert calls
    argv = calls[0]
    assert argv[argv.index("--model") + 1] == "exact-codex-model"
    assert argv[argv.index("--effort") + 1] == "high"


def test_partial_launch_failure_does_not_duplicate_agents(tmp_path, monkeypatch):
    workshop, picker, calls = _three(tmp_path, monkeypatch)
    failures = [True]

    def run(command, **kwargs):
        calls.append(command)
        if "new-tab" in command and "claude" in command and failures:
            failures.pop()
            return SimpleNamespace(
                returncode=1, stdout="", stderr="second start failed"
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(workshop.subprocess, "run", run)
    picker.launch()
    assert [r["status"] for r in picker.launch_results] == [
        "opened",
        "failed",
        "opened",
    ]
    assert "second start failed" in picker.launch_results[1]["reason"]
    visible = []
    picker.window = SimpleNamespace(
        getmaxyx=lambda: (24, 120),
        addstr=lambda row, col, text, attr=0: visible.append(text),
    )
    monkeypatch.setattr(workshop, "_provider_available", lambda provider: True)
    picker.draw_launcher()
    assert any(
        "2 claude: failed" in line and "second start failed" in line for line in visible
    )
    assert any("1 codex: opened" in line for line in visible)
    assert any("3 grok: opened" in line for line in visible)
    picker.launch()
    assert len(calls) == 4
    assert [r["status"] for r in picker.launch_results] == ["opened"] * 3


def test_fourth_agent_is_refused(tmp_path, monkeypatch):
    _, picker, _ = _three(tmp_path, monkeypatch)
    picker.add_agent()
    assert len(picker.agent_slots) == 3
    assert "3" in picker.error


def test_distinct_choices_for_same_provider_survive_reopen(tmp_path, monkeypatch):
    workshop = _load()
    monkeypatch.setenv("VIBECRAFTED_WORKSPACE_ROOT", str(tmp_path))
    picker = workshop.Workshop(SimpleNamespace(), mode="launcher")
    picker.model = "first-model"
    picker.add_agent()
    picker.model = "second-model"
    picker.save_choices()
    reopened = workshop.Workshop(SimpleNamespace(), mode="launcher")
    assert len(reopened.agent_slots) == 2
    assert reopened.model == "first-model"
    reopened.select_slot(1)
    assert reopened.model == "second-model"


def test_no_effort_for_provider_without_control(tmp_path, monkeypatch):
    workshop = _load()
    monkeypatch.setenv("VIBECRAFTED_WORKSPACE_ROOT", str(tmp_path))
    picker = workshop.Workshop(SimpleNamespace(), mode="launcher")
    picker._select_agent(workshop.AGENTS.index("kimi"))
    picker.edit_controls = True
    picker.row = 8
    picker.handle_launcher_key(ord("h"))
    assert picker.effort == ""
    assert "unavailable" in picker.error
    # A stale or injected choice must also fail at launch, before any tab opens.
    launched, calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination="selected",
        live=["selected"],
        current="selected",
    )
    launched.agent = workshop.AGENTS.index("kimi")
    launched.effort = "high"
    launched.launch()
    assert launched.launch_results[0]["status"] == "failed"
    assert "unavailable" in launched.error
    assert calls == []


def test_materialized_launcher_keyboard_batch(pane_runtime, tmp_path):  # noqa: F811
    import json
    import sys

    from test_frame_workday_project_launcher_entry import Terminal, _pane_command
    from vibecrafted_core.workspace_catalog import (
        create_workspace,
        operator_session_name,
    )

    config, env, project, _ = pane_runtime
    record = create_workspace(root=project, display_label="selected-project")
    destination = operator_session_name(
        record.workspace_id, display_label=record.display_label
    )
    frame = Path(env["VIBECRAFTED_PYTHON"]).parent / "vc-frame"
    capture = tmp_path / "batch.jsonl"
    frame.write_text(
        f"#!{sys.executable}\nimport json,sys,pathlib\n"
        "if 'list-sessions' in sys.argv:\n"
        f" print({destination!r})\n"
        "elif 'new-tab' in sys.argv:\n"
        f" with pathlib.Path({str(capture)!r}).open('a') as out: out.write(json.dumps(sys.argv[1:])+'\\n')\n"
    )
    terminal = Terminal(_pane_command(config, "vc-agent-workshop.py"), env, tmp_path)
    try:
        terminal.expect("Choose a provider, then Launch.")
        terminal.send(b"c")
        terminal.expect("Model")
        terminal.send(b"exact-codex-model\thigh\tc")
        terminal.send(b"++")
        terminal.expect("3 codex")
        terminal.send(b"\n")
        terminal.expect("3 codex: opened")
        lines = [json.loads(line) for line in capture.read_text().splitlines()]
        assert len(lines) == 3
        assert lines[0][lines[0].index("--model") + 1] == "exact-codex-model"
        assert lines[0][lines[0].index("--effort") + 1] == "high"
        assert all(c[c.index("--session") + 1] == destination for c in lines)
        assert all(c[c.index("--root") + 1] == str(project) for c in lines)
        terminal.send(b"\n")
        terminal.expect("All selected agent tabs are already open")
        assert len(capture.read_text().splitlines()) == 3
        terminal.send(b"\x1b")
        assert terminal.finish() == 0
    finally:
        terminal.close()


def test_configured_effort_remains_runtime_default(tmp_path, monkeypatch):
    config = tmp_path / "xdg" / "vibecrafted"
    config.mkdir(parents=True)
    (config / "config.toml").write_text('[agents.codex]\neffort = "high"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config.parent))
    workshop = _load()
    picker, calls = _prepare_launch(
        workshop,
        tmp_path,
        monkeypatch,
        destination="selected",
        live=["selected"],
        current="selected",
    )
    assert picker.effort == ""
    picker.launch()
    assert picker.launch_results[0]["status"] == "opened"
    assert len(calls) == 1
