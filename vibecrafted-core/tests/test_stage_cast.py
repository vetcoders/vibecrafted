"""C8: stage_agents is a failover list, not a single name."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from vibecrafted_core.lifecycle_runner import (
    LifecycleRunner,
    LifecycleRunSpec,
    _mission_stage_agents,
    _validated_stage_agents,
)
from vibecrafted_core.stage_cast import (
    encode_stage_cast,
    parse_agent_names,
    primary_stage_agent,
    primary_stage_map,
)
from vibecrafted_core.workflows.registry import workflow_manifest


def test_parse_agent_names_single_and_failover() -> None:
    assert parse_agent_names("claude") == ["claude"]
    assert parse_agent_names("claude,grok") == ["claude", "grok"]
    assert parse_agent_names("[claude, grok]") == ["claude", "grok"]
    assert parse_agent_names(["claude", "grok"]) == ["claude", "grok"]
    assert parse_agent_names("claude, grok, claude") == ["claude", "grok"]
    assert parse_agent_names("") == []
    assert parse_agent_names(None) == []


def test_mission_stage_agents_single_name_still_valid() -> None:
    inline = "---\nstage_agents: scaffold=claude, review=codex\n---\nmission"
    assert _mission_stage_agents(inline) == {
        "scaffold": ["claude"],
        "review": ["codex"],
    }

    nested = "---\nstage_agents:\n  marbles: codex\n  audit: claude\n---\nmission\n"
    assert _mission_stage_agents(nested) == {
        "marbles": ["codex"],
        "audit": ["claude"],
    }
    assert _mission_stage_agents("plain mission, no frontmatter") == {}


def test_mission_stage_agents_inline_failover_list() -> None:
    text = "---\nstage_agents: audit=claude,grok\n---\nmission"
    assert _mission_stage_agents(text) == {"audit": ["claude", "grok"]}


def test_mission_stage_agents_mixed_inline_pairs_and_failover() -> None:
    text = (
        "---\n"
        "stage_agents: scaffold=claude, audit=claude,grok, review=codex\n"
        "---\n"
        "mission"
    )
    assert _mission_stage_agents(text) == {
        "scaffold": ["claude"],
        "audit": ["claude", "grok"],
        "review": ["codex"],
    }


def test_mission_stage_agents_nested_yaml_list() -> None:
    text = (
        "---\nstage_agents:\n  audit: [claude, grok]\n  marbles: codex\n---\nmission\n"
    )
    assert _mission_stage_agents(text) == {
        "audit": ["claude", "grok"],
        "marbles": ["codex"],
    }


def test_validated_stage_agents_accepts_legacy_string_map() -> None:
    manifest = workflow_manifest("vc-marbles")
    assert manifest is not None
    assert _validated_stage_agents({"audit": "claude"}, manifest) == {
        "audit": ["claude"]
    }
    assert _validated_stage_agents({"audit": ["claude", "grok"]}, manifest) == {
        "audit": ["claude", "grok"]
    }
    assert _validated_stage_agents({"audit": "claude,grok"}, manifest) == {
        "audit": ["claude", "grok"]
    }


def test_validated_stage_agents_unknown_agent_fail_fast() -> None:
    manifest = workflow_manifest("vc-marbles")
    assert manifest is not None
    with pytest.raises(ValueError, match="unsupported agent 'hal9000'"):
        _validated_stage_agents({"audit": "hal9000"}, manifest)
    with pytest.raises(ValueError, match="unsupported agent 'hal9000'"):
        _validated_stage_agents({"audit": ["claude", "hal9000"]}, manifest)
    with pytest.raises(ValueError, match="unknown stage 'nosuch'"):
        _validated_stage_agents({"nosuch": "claude"}, manifest)


def test_primary_and_encode_keep_schema_safe_persist() -> None:
    cast = {"audit": ["claude", "grok"], "marbles": ["codex"]}
    assert primary_stage_map(cast) == {"audit": "claude", "marbles": "codex"}
    assert primary_stage_agent(cast, "audit") == "claude"
    assert encode_stage_cast(cast) == {
        "audit": "claude,grok",
        "marbles": "codex",
    }
    assert encode_stage_cast({"audit": "claude"}) == {"audit": "claude"}


def test_runner_launches_primary_from_failover_list(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    casting: list[tuple[str, str]] = []

    def fake_launcher(spec, _source_dir):
        casting.append((spec.skill, spec.agent))
        report = tmp_path / f"{spec.skill}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": f"{spec.skill}-run",
            "report": str(report),
            "transcript": str(tmp_path / f"{spec.skill}.log"),
            "meta": str(tmp_path / f"{spec.skill}.json"),
        }

    runner = LifecycleRunner(
        launcher=fake_launcher,
        awaiter=lambda payload: {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
        },
    )
    mission = (
        "---\n"
        "stage_agents:\n"
        "  marbles: codex\n"
        "  audit: [claude, grok]\n"
        "---\n"
        "# Mission: failover list, primary launches\n"
    )
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-marbles",
                agent="junie",
                prompt=mission,
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )

    assert casting == [("marbles", "codex"), ("audit", "claude")]
    assert state["spec"]["stage_agents"] == {
        "marbles": "codex",
        "audit": "claude,grok",
    }
