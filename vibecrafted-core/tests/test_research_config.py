from pathlib import Path

import pytest
from vibecrafted_core.research_config import (
    _scalar,
    _yaml_lanes,
    resolve_research_runtime_config,
)


@pytest.fixture()
def config_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "vc"))
    yaml = tmp_path / "vc" / "config" / "research.yaml"
    toml = tmp_path / "xdg" / "vibecrafted" / "config.toml"
    yaml.parent.mkdir(parents=True)
    toml.parent.mkdir(parents=True)
    return yaml, toml


@pytest.mark.parametrize(
    "content",
    [
        "lanes:\n  - codex\n  - agy\n  - junie\n",
        "agents: [codex, agy, junie]\n",
        "lanes: [codex, agy, junie]\n",
        "agents:\n  - codex\n  - agy\n  - junie\n",
    ],
)
def test_research_string_lanes_and_flow_list(config_paths, capsys, content):
    yaml, _ = config_paths
    yaml.write_text(content)
    selection = resolve_research_runtime_config()
    assert selection.agents == ("codex", "agy", "junie")
    assert selection.source == str(yaml)
    assert not selection.ignored
    assert "Deprecated research config" in capsys.readouterr().err
    assert selection.warnings


def test_scalar_flow_list():
    assert _scalar("[codex, \"agy\", 'junie']") == ["codex", "agy", "junie"]
    assert _scalar("[]") == []


def test_uninterpretable_rows_are_never_silent(config_paths, capsys):
    yaml, _ = config_paths
    yaml.write_text(
        "lanes:\n  - codex\n  - 42\n  -\n  - model: lost\n  - gemini\n  broken line\n"
    )
    selection = resolve_research_runtime_config()
    assert selection.agents == ("codex",)
    assert len(selection.ignored) == 5
    stderr = capsys.readouterr().err
    for item in selection.ignored:
        assert f"Ignored research config element: {item}" in stderr


def test_canonical_toml_wins_and_deprecates_existing_yaml(config_paths, capsys):
    yaml, toml = config_paths
    yaml.write_text("agents: [claude, gemini]\n")
    toml.write_text('[runtime.picking.research]\ndefault_agents = ["codex", "agy"]\n')
    selection = resolve_research_runtime_config()
    assert selection.agents == ("codex", "agy")
    assert selection.source == str(toml)
    assert selection.ignored == ("gemini",)
    assert (
        "Canonical config.toml overrides research.yaml agents"
        in capsys.readouterr().err
    )


def test_yaml_wins_over_legacy_install_but_env_and_explicit_win_over_toml(
    config_paths, monkeypatch
):
    yaml, toml = config_paths
    Path("install.toml").write_text(
        '[runtime.picking.research]\ndefault_agents = ["claude"]\n'
    )
    yaml.write_text("agents: [agy]\n")
    assert resolve_research_runtime_config().agents == ("agy",)
    toml.write_text('[runtime.picking.research]\ndefault_agents = ["codex"]\n')
    monkeypatch.setenv("VIBECRAFTED_RESEARCH_AGENTS", "grok")
    assert resolve_research_runtime_config().agents == ("grok",)
    assert resolve_research_runtime_config(override_agents=["junie"]).agents == (
        "junie",
    )


@pytest.mark.parametrize("row", [42, None, {}, {"model": "lost"}])
def test_invalid_lane_shapes_report_ignored(row):
    agents, _, ignored = _yaml_lanes({"lanes": [row, "codex"]})
    assert agents == ("codex",)
    assert ignored


def test_empty_canonical_roster_never_falls_back_to_yaml(config_paths, capsys):
    yaml, toml = config_paths
    yaml.write_text("agents: [claude]\n")
    toml.write_text("[runtime.picking.research]\ndefault_agents = []\n")
    selection = resolve_research_runtime_config()
    assert selection.agents == ()
    assert selection.source == str(toml)
    assert "Canonical config.toml overrides" in capsys.readouterr().err


def test_invalid_yaml_roster_never_launches_builtin_lanes(config_paths, capsys):
    yaml, _ = config_paths
    yaml.write_text("lanes:\n  - 42\n  - model: lost\n")
    selection = resolve_research_runtime_config()
    assert selection.agents == ()
    assert selection.source == str(yaml)
    assert selection.ignored
    assert "Ignored research config element" in capsys.readouterr().err


def test_unknown_key_cannot_overwrite_ignored_collection(config_paths, capsys):
    yaml, _ = config_paths
    yaml.write_text("_ignored: invalid\nagents: [codex]\n")
    selection = resolve_research_runtime_config()
    assert selection.agents == ("codex",)
    assert selection.ignored == ("line 1: _ignored: invalid",)
    assert "Ignored research config element" in capsys.readouterr().err


def test_research_roster_matches_the_dispatchable_fleet() -> None:
    """Every dispatchable provider is a valid lane (kimi drift, 2026-09-30).

    Three rosters drifted apart once already (deck _has_agent, cli.AGENTS,
    SUPPORTED_RESEARCH_AGENTS); kimi was silently dropped from lanes the same
    day the Founder called it fable-class. The swarm coordinator itself is the
    only legitimate difference.
    """
    from vibecrafted_core.cli import AGENTS
    from vibecrafted_core.research_config import SUPPORTED_RESEARCH_AGENTS

    assert set(SUPPORTED_RESEARCH_AGENTS) == AGENTS - {"swarm"}
