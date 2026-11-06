"""Strict task MCP scopes must not disappear at the bare interactive boundary."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from vibecrafted_core import spawn, workflow
from vibecrafted_core.continuity import capabilities

_NATIVE_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def strict_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "runtime"
    repo = tmp_path / "repo"
    repo.mkdir()
    parent_dir = home / "control_plane" / "runtime_runs" / "strict-parent"
    parent_dir.mkdir(parents=True)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setattr(
        spawn,
        "resolve_provider_usage_capability",
        lambda *_a, **_k: SimpleNamespace(supported=False, reason="unmetered fixture"),
    )
    monkeypatch.setattr(
        capabilities,
        "probe",
        lambda *_a, **_k: SimpleNamespace(
            state=capabilities.PROBE_CONFIRMED, detail="fixture"
        ),
    )

    def seed(provider: str):
        record = {
            "run_id": "strict-parent",
            "agent": provider,
            "agent_session_id": _NATIVE_ID,
            "root": str(repo),
            "state": "completed",
            "liveness": "terminal",
            "exit_code": 0,
            "mcp_config": str(parent_dir / "mcp-isolation" / "config.json"),
            "mcp_isolation": {
                "schema": "vibecrafted.mcp-isolation.v1",
                "agent": provider,
            },
        }
        (parent_dir / "meta.json").write_text(json.dumps(record), encoding="utf-8")
        monkeypatch.setattr(
            workflow,
            "lookup_run",
            lambda run_id: dict(record) if run_id == record["run_id"] else None,
        )
        return record

    return home, repo, parent_dir, seed


@pytest.mark.parametrize("provider", ["claude", "codex", "agy"])
@pytest.mark.parametrize("selector", ["native", "run-id"])
def test_bare_resume_cannot_restore_global_mcp_scope(strict_parent, provider, selector):
    home, repo, parent_dir, seed = strict_parent
    seed(provider)
    identity = (
        {"native_session": _NATIVE_ID}
        if selector == "native"
        else {"resume_run_id": "strict-parent"}
    )

    with pytest.raises(
        ValueError, match="strict MCP continuations require --prompt or --file"
    ):
        spawn.interactive_workspace_command(
            provider,
            "/vc-resume",
            "local-native",
            "read-only",
            repo,
            token_budget="unmetered",
            skill="resume",
            **identity,
        )

    assert sorted((home / "control_plane" / "runtime_runs").iterdir()) == [parent_dir]
    assert not (home / "worktrees").exists()


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_bare_fork_cannot_restore_global_mcp_scope(strict_parent, provider):
    home, repo, parent_dir, seed = strict_parent
    seed(provider)

    with pytest.raises(
        ValueError, match="strict MCP continuations require --prompt or --file"
    ):
        spawn.interactive_workspace_command(
            provider,
            "/vc-fork",
            "local-native",
            "read-only",
            repo,
            token_budget="unmetered",
            skill="fork",
            continuity="bare-fork",
            parent_session_id=_NATIVE_ID,
        )

    assert sorted((home / "control_plane" / "runtime_runs").iterdir()) == [parent_dir]


@pytest.mark.parametrize(
    "clue", [{"mcp_config": "missing-private-profile.json"}, {"mcp_isolation": None}]
)
def test_direct_interactive_executor_refuses_damaged_strict_admission(
    tmp_path, monkeypatch, clue
):
    home = tmp_path / "runtime"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))

    with pytest.raises(
        ValueError, match="strict MCP continuations require --prompt or --file"
    ):
        spawn.launch_interactive_workspace(
            "claude",
            "/vc-resume",
            "local-native",
            "read-only",
            tmp_path,
            token_budget="unmetered",
            admission={"skill": "resume", **clue},
        )

    assert not (home / "control_plane" / "runtime_runs").exists()
