from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import pytest
from vibecrafted_core import cli, workflow
from vibecrafted_core import workflow_capabilities as caps


@pytest.fixture()
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Pin every research-config layer to tmp so the operator's real machine
    state (XDG config.toml, ~/.vibecrafted/config/research.yaml, the repo's
    install.toml via cwd) cannot leak into precedence assertions."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "vc-home"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _by_name(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {record["name"]: record for record in payload["workflows"]}


def _write_config_toml(tmp_path: Path, agents: list[str]) -> Path:
    config = tmp_path / "xdg" / "vibecrafted" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    listed = ", ".join(f'"{agent}"' for agent in agents)
    config.write_text(
        f"[runtime.picking.research]\ndefault_agents = [{listed}]\n",
        encoding="utf-8",
    )
    return config


def test_payload_schema_version_and_agent_universe(isolated_config: Path) -> None:
    payload = caps.workflow_capabilities_payload()

    assert payload["schema"] == "vibecrafted.workflow_capabilities.v1"
    assert payload["schema_version"] == 1
    # The declared agent universe IS the live launch gate — one set, no gemini.
    assert payload["agents"] == sorted(cli.AGENTS)
    assert set(payload["agents"]) == workflow.SUPPORTED_AGENTS
    assert "gemini" not in json.dumps(payload)


def test_payload_rows_for_single_agent_and_research(isolated_config: Path) -> None:
    payload = caps.workflow_capabilities_payload()
    rows = _by_name(payload)

    for name in ("implement", "ownership"):
        assert rows[name]["runtime_kind"] == "direct_agent"
        assert rows[name]["execution_target"] == "single_agent"
        assert rows[name]["requested_agent_policy"] == "honored"
        assert rows[name]["default_agent"] == "claude"
    assert rows["implement"]["aliases"] == []
    assert "justdo" in rows
    assert rows["justdo"]["aliases"] == []
    assert rows["justdo"]["name"] == "justdo"

    research = rows["research"]
    assert research["runtime_kind"] == "supervised_research"
    assert research["execution_target"] == "swarm"
    assert research["requested_agent_policy"] == "fail_closed"
    assert research["default_agent"] == "swarm"
    assert research["positional_agent_semantics"] == {
        "single": "synthesizer_override",
        "multiple": "lanes_with_first_as_synthesizer",
        "unsupported_token": "launch_rejected",
        "execution_agent": "swarm",
    }
    assert research["selection_source"] == "builtin-default"
    assert research["effective_agents"] == ["claude", "codex", "agy"]
    assert research["unsupported_configured"] == []
    assert research["synthesizer"]["fallback"] == "last_surviving_lane"

    orders = [record["lifecycle_order"] for record in payload["workflows"]]
    assert orders == sorted(orders)


def test_marbles_rows_expose_swarm_fallback_matching_launch_command(
    isolated_config: Path,
) -> None:
    payload = caps.workflow_capabilities_payload()
    rows = _by_name(payload)

    for name in ("marbles", "polarize"):
        assert rows[name]["runtime_kind"] == "supervised_marbles"
        assert rows[name]["execution_target"] == "single_agent"
        assert rows[name]["requested_agent_policy"] == "honored"
        assert rows[name]["swarm_agent_fallback"] == "codex"

    # Parity with the live launch path: a swarm request on marbles runs codex.
    spec = workflow.WorkflowLaunchSpec(
        agent="swarm",
        mode="marbles",
        skill="marbles",
        prompt="go",
        file="",
        runtime="headless",
        root=str(isolated_config),
        count=1,
        depth=1,
    )
    command = workflow.build_launch_command(spec, isolated_config)
    assert command[command.index("--agent") + 1] == "codex"


def test_config_layer_reports_gemini_as_unsupported_not_dropped(
    isolated_config: Path,
) -> None:
    config = _write_config_toml(isolated_config, ["grok", "codex", "gemini"])

    payload = caps.workflow_capabilities_payload()
    research = _by_name(payload)["research"]

    assert research["selection_source"] == str(config)
    assert research["effective_agents"] == ["grok", "codex"]
    assert research["unsupported_configured"] == ["gemini"]


def test_env_layer_overrides_config_and_still_reports_unsupported(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config_toml(isolated_config, ["grok", "codex", "gemini"])
    monkeypatch.setenv("VIBECRAFTED_RESEARCH_AGENTS", "junie gemini claude")

    payload = caps.workflow_capabilities_payload()
    research = _by_name(payload)["research"]

    assert research["selection_source"] == "env:VIBECRAFTED_RESEARCH_AGENTS"
    assert research["effective_agents"] == ["junie", "claude"]
    assert "gemini" in research["unsupported_configured"]


def test_manifest_yaml_layer_overrides_config_toml(isolated_config: Path) -> None:
    _write_config_toml(isolated_config, ["grok"])
    research_yaml = isolated_config / "vc-home" / "config" / "research.yaml"
    research_yaml.parent.mkdir(parents=True, exist_ok=True)
    research_yaml.write_text(
        "lanes:\n"
        "  - agent: codex\n"
        "  - agent: gemini\n"
        "  - agent: agy\n"
        "synthesizer: claude\n",
        encoding="utf-8",
    )

    payload = caps.workflow_capabilities_payload()
    research = _by_name(payload)["research"]

    assert research["selection_source"] == str(research_yaml)
    assert research["effective_agents"] == ["codex", "agy"]
    assert research["unsupported_configured"] == ["gemini"]
    assert research["synthesizer"]["agent"] == "claude"


def test_cli_capabilities_json_and_human_forms(
    isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["capabilities", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "vibecrafted.workflow_capabilities.v1"
    assert _by_name(payload)["research"]["execution_target"] == "swarm"

    assert cli.main(["capabilities"]) == 0
    human = capsys.readouterr().out
    assert "vibecrafted.workflow_capabilities.v1" in human
    assert "supervised_research" in human
    assert "fail_closed" in human


def test_cli_capabilities_is_side_effect_free(
    isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The command must not launch runs, mutate config, or touch the control
    # plane — fingerprint the entire isolated home before/after.
    def fingerprint() -> set[str]:
        return {str(path) for path in isolated_config.rglob("*")}

    before = fingerprint()
    assert cli.main(["capabilities", "--json"]) == 0
    capsys.readouterr()
    assert fingerprint() == before
    assert not (isolated_config / "vc-home" / "control_plane").exists()


def test_positional_fail_closed_matches_declared_policy(isolated_config: Path) -> None:
    # Declared: unsupported positional token -> launch_rejected. Live gate:
    with pytest.raises(ValueError, match="Unsupported research agent: gemini"):
        workflow.normalize_launch_spec(
            {"skill": "research", "agent": ["gemini"], "prompt": "x"},
            isolated_config,
        )


def _fake_popen(monkeypatch: pytest.MonkeyPatch, calls: list[Any]) -> None:
    class DummyProc:
        def __init__(self, command: Any) -> None:
            self.args = command
            self.pid = 4242
            self.returncode = 0
            self.stdout = "/repo"
            self.stderr = ""

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def communicate(self, *args: Any, **kwargs: Any) -> tuple[str, str]:
            return ("/repo", "")

        def poll(self) -> int:
            return 0

        def wait(self) -> int:
            return 0

        def kill(self) -> None:
            pass

    def popen(command: Any, **kwargs: Any) -> DummyProc:
        calls.append(command)
        return DummyProc(command)

    monkeypatch.setattr(workflow.subprocess, "Popen", popen)


def _launch_research(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    agents: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        workflow, "append_event", lambda **kwargs: events.append(kwargs)
    )
    # Scope the no-vc-frame stub to the workflow module: the operator-session
    # probe would otherwise drive the process-global subprocess.run into the
    # faked Popen below.
    monkeypatch.setattr(workflow, "shutil", SimpleNamespace(which=lambda _name: None))
    monkeypatch.setattr(workflow, "git_toplevel", lambda _root: "")
    _fake_popen(monkeypatch, [])
    brief = tmp_path / "brief.md"
    brief.write_text("map the target\n", encoding="utf-8")
    spec = workflow.normalize_launch_spec(
        {
            "skill": "research",
            "agent": agents,
            "file": str(brief),
            "runtime": "headless",
            "root": str(tmp_path),
        },
        tmp_path,
    )
    payload = workflow.launch_workflow(spec, tmp_path)
    assert payload["accepted"] is True
    return payload, events


def test_receipt_parity_research_single_positional(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    declared = _by_name(caps.workflow_capabilities_payload())["research"]

    _payload, events = _launch_research(monkeypatch, isolated_config, ["claude"])
    accepted = next(
        event["payload"]
        for event in events
        if event["payload"].get("state") == "created"
    )

    # One positional agent = synthesizer override; lanes stay the declared
    # effective members from config precedence; execution agent stays swarm.
    assert accepted["agent"] == "swarm"
    assert accepted["research_synthesizer"] == "claude"
    assert accepted["research_agents"] == declared["effective_agents"]
    assert accepted["research_agent_source"] == declared["selection_source"]


def test_receipt_parity_research_multi_positional(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    declared = _by_name(caps.workflow_capabilities_payload())["research"]
    semantics = declared["positional_agent_semantics"]
    assert semantics["multiple"] == "lanes_with_first_as_synthesizer"

    _payload, events = _launch_research(
        monkeypatch, isolated_config, ["claude", "codex"]
    )
    accepted = next(
        event["payload"]
        for event in events
        if event["payload"].get("state") == "created"
    )

    assert accepted["agent"] == "swarm"
    assert accepted["research_agents"] == ["claude", "codex"]
    assert accepted["research_synthesizer"] == accepted["research_agents"][0]
    assert accepted["research_agent_source"] == "positional-override"


# --- Launcher catalog for GUI/TUI clients (VOC reads exactly this) -----------
#
# These rows are the single owner of "which agents exist, which of them can be
# pinned to a model, and which permission/sandbox and environment combinations
# the launcher will accept". A client that keeps its own copy drifts; a client
# that reads these cannot offer a combination the launcher already refuses.


def test_provider_rows_cover_the_declarable_agent_universe(
    isolated_config: Path,
) -> None:
    payload = caps.workflow_capabilities_payload()

    # `swarm` is a research execution target, not a declarable launcher agent.
    assert sorted(payload["providers"]) == sorted(workflow.SUPPORTED_AGENTS - {"swarm"})
    assert "gemini" not in payload["providers"]
    for name, provider in payload["providers"].items():
        assert set(provider) == {
            "binary",
            "executable",
            "available",
            "reason",
            "model_override",
            "permissions_default",
            "controls",
        }, name
        # An unavailable provider must say why; silence would read as a bug.
        if not provider["available"]:
            assert provider["reason"], name


def test_model_pin_support_matches_the_launcher_override_table(
    isolated_config: Path,
) -> None:
    from vibecrafted_core.model_overrides import MODEL_OVERRIDE_FLAGS

    payload = caps.workflow_capabilities_payload()

    for name, provider in payload["providers"].items():
        override = provider["model_override"]
        expected = name in MODEL_OVERRIDE_FLAGS
        assert override["supported"] is expected, name
        if expected:
            assert override["flag"] == MODEL_OVERRIDE_FLAGS[name]
        else:
            # A client must be able to explain the refusal, not just grey a row.
            assert override["reason"], name
            assert not override["flag"]


def test_every_permission_sandbox_cell_is_resolved_and_refusals_carry_reasons(
    isolated_config: Path,
) -> None:
    payload = caps.workflow_capabilities_payload()

    words = ("", "true", "false")
    expected_cells = {
        (permission, sandbox)
        for permission in ("",) + tuple(caps.PERMISSION_POLICIES)
        for sandbox in words
    }
    for name, provider in payload["providers"].items():
        cells = {
            (cell["permissions"], cell["sandbox"]) for cell in provider["controls"]
        }
        assert cells == expected_cells, name
        for cell in provider["controls"]:
            if cell["supported"]:
                # A supported cell states what the provider will actually enforce.
                assert cell["permissions_effective"], (name, cell)
                assert cell["sandbox_effective"], (name, cell)
            else:
                # A refused cell is refused out loud, before any worker exists.
                assert cell["reason"], (name, cell)


def test_environment_rows_cover_every_runtime_policy_with_launcher_flags(
    isolated_config: Path,
) -> None:
    from vibecrafted_core.spawn import RUNTIME_POLICIES

    payload = caps.workflow_capabilities_payload()

    assert sorted(payload["environments"]) == sorted(RUNTIME_POLICIES)
    for policy, environment in payload["environments"].items():
        assert environment["label"], policy
        if not environment["available"]:
            assert environment["reason"], policy
            assert not environment["skill_launcher_supported"], policy
        if not environment["skill_launcher_supported"]:
            assert environment["skill_launcher_flags"] == [], policy

    # The two environments a skill launcher can actually express today.
    assert payload["environments"]["local-native"]["skill_launcher_flags"] == []
    assert payload["environments"]["local-worktrees"]["skill_launcher_flags"] == [
        "--worktree",
        "true",
    ]
    # VM environments have no launcher entrypoint yet and must say so rather
    # than silently falling back to the local tree.
    assert payload["environments"]["local-vm"]["available"] is False
    assert payload["environments"]["cloud-soon"]["available"] is False


def test_catalog_payload_is_json_serialisable_for_gui_clients(
    isolated_config: Path,
) -> None:
    payload = caps.workflow_capabilities_payload()

    reparsed = json.loads(json.dumps(payload))
    assert reparsed["schema"] == "vibecrafted.workflow_capabilities.v1"
    assert reparsed["providers"] == payload["providers"]
    assert reparsed["environments"] == payload["environments"]


def test_human_rendering_lists_providers_and_environments(
    isolated_config: Path,
) -> None:
    lines = caps.render_capabilities_lines(caps.workflow_capabilities_payload())
    text = "\n".join(lines)

    assert "claude" in text
    assert "Fleet Worktrees" in text
    assert "gemini" not in text
