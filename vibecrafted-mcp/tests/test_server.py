"""Tests for vibecrafted-mcp v0.1.

Synthesis stubs and helpers are tested directly; the FastMCP server
roundtrip is exercised via the in-memory client when ``fastmcp`` is
installed in the test environment, and skipped otherwise.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import pytest
import tomllib
import vibecrafted_mcp
from vibecrafted_mcp import server, synthesis

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _server_observation_contract_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep MCP unit tests isolated while exercising the server-client seam."""

    def observe(run_id: str) -> dict[str, Any]:
        run = server._control_plane.lookup_run(run_id)
        terminal = server._run_terminal(run)
        return {
            "schema": "vibecrafted.run-observation.v1",
            "run_id": run_id,
            "control_plane": str(server._control_plane.control_plane_home()),
            "found": run is not None,
            "terminal": terminal,
            "worker_alive": False if terminal else None,
            "process_truth": "terminal" if terminal else "unknown",
            "evidence_disagreement": False,
            "run": run,
        }

    def await_run(run_id: str, **kwargs: Any) -> dict[str, Any]:
        observation = observe(run_id)
        return {
            **observation,
            "schema": "vibecrafted.run-await-verdict.v1",
            "outcome": "terminal" if observation["terminal"] else "idle_stall",
            "reason": "terminal" if observation["terminal"] else "idle_stall",
            "completed": observation["terminal"],
            "timed_out": not observation["terminal"],
            "idle_timeout_seconds": kwargs["idle_timeout_seconds"],
            "hard_cap_seconds": kwargs["hard_cap_seconds"],
        }

    monkeypatch.setattr(server._server_observation, "observe_run", observe)
    monkeypatch.setattr(server._server_observation, "await_run", await_run)


def test_version_matches_repository_contract() -> None:
    expected = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pyproject = tomllib.loads(
        (REPO_ROOT / "vibecrafted-mcp" / "pyproject.toml").read_text(encoding="utf-8")
    )
    packaged = (
        (REPO_ROOT / "vibecrafted-mcp" / "vibecrafted_mcp" / "VERSION")
        .read_text(encoding="utf-8")
        .strip()
    )

    assert pyproject["project"]["version"] == expected
    assert packaged == expected
    assert vibecrafted_mcp.__version__ == expected
    assert server.build_server().version == expected


# ---------------------------------------------------------------------------
# Synthesis stubs (pure logic — no fastmcp dependency required)
# ---------------------------------------------------------------------------


def test_live_failure_score_clean_repo_is_low() -> None:
    state: dict[str, Any] = {
        "status": {"staged": 0, "unstaged": 0, "untracked": 0},
        "behind": 0,
        "ahead": 0,
        "stashes": 0,
    }
    doctor = {"ok": 5, "warnings": 0, "failures": 0, "healthy": True}
    payload = synthesis.live_failure_score(state, doctor)
    assert payload["score"] == 0
    assert payload["band"] == "low"
    assert payload["reasons"] == []


def test_live_failure_score_dirty_tree_with_failures_is_high() -> None:
    state = {
        "status": {"staged": 4, "unstaged": 6, "untracked": 3},
        "behind": 5,
        "ahead": 12,
        "stashes": 2,
    }
    doctor = {"ok": 0, "warnings": 4, "failures": 2}
    payload = synthesis.live_failure_score(state, doctor)
    assert payload["score"] >= 60
    assert payload["band"] == "high"
    assert any("doctor" in reason for reason in payload["reasons"])
    assert any(
        "upstream" in reason or "ahead" in reason for reason in payload["reasons"]
    )


def test_live_failure_score_is_bounded() -> None:
    state = {
        "status": {"staged": 50, "unstaged": 50, "untracked": 50},
        "behind": 100,
        "ahead": 100,
        "stashes": 100,
    }
    doctor = {"failures": 50, "warnings": 50}
    payload = synthesis.live_failure_score(state, doctor)
    assert 0 <= payload["score"] <= 100


def test_unmade_decisions_flags_staged_changes() -> None:
    state = {
        "status": {"staged": 2, "unstaged": 0},
        "ahead": 0,
        "behind": 0,
        "stashes": 0,
    }
    pending = synthesis.unmade_decisions(state)
    assert any("commit" in note for note in pending)


def test_unmade_decisions_flags_behind_upstream() -> None:
    state = {
        "status": {"staged": 0, "unstaged": 0},
        "ahead": 0,
        "behind": 3,
        "stashes": 0,
    }
    pending = synthesis.unmade_decisions(state)
    assert any("upstream" in note for note in pending)


def test_unverified_claims_lists_required_senses() -> None:
    claims = synthesis.unverified_claims()
    assert any("loctree" in claim for claim in claims)
    assert any("aicx" in claim for claim in claims)


# ---------------------------------------------------------------------------
# Helpers (env override + event filtering)
# ---------------------------------------------------------------------------


def test_override_vibecrafted_home_restores_previous(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", "/sentinel")
    with server._override_vibecrafted_home(str(tmp_path)):
        assert os.environ["VIBECRAFTED_HOME"] == str(tmp_path)
    assert os.environ["VIBECRAFTED_HOME"] == "/sentinel"


def test_override_vibecrafted_home_clears_when_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("VIBECRAFTED_HOME", raising=False)
    with server._override_vibecrafted_home(str(tmp_path)):
        assert os.environ["VIBECRAFTED_HOME"] == str(tmp_path)
    assert "VIBECRAFTED_HOME" not in os.environ


def test_filter_events_by_run_respects_limit() -> None:
    events = [
        {"run_id": "a", "ts": "1"},
        {"run_id": "b", "ts": "2"},
        {"run_id": "a", "ts": "3"},
        {"run_id": "a", "ts": "4"},
    ]
    filtered = server._filter_events_by_run(events, "a", limit=2)
    assert [event["ts"] for event in filtered] == ["1", "3"]


def test_read_run_event_tail_returns_empty_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path))
    assert server._read_run_event_tail("nope", home=str(tmp_path)) == []


def test_read_run_event_tail_filters_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path))
    stream = tmp_path / "control_plane" / "events.jsonl"
    stream.parent.mkdir(parents=True, exist_ok=True)
    stream.write_text(
        "\n".join(
            json.dumps(item)
            for item in [
                {"run_id": "alpha", "ts": "1", "kind": "state"},
                {"run_id": "beta", "ts": "2", "kind": "state"},
                {"run_id": "alpha", "ts": "3", "kind": "state"},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    events = server._read_run_event_tail("alpha", home=str(tmp_path))
    assert [event["ts"] for event in events] == ["3", "1"]


def test_doctor_payload_unavailable_when_installer_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise ModuleNotFoundError("vetcoders_install")

    monkeypatch.setattr(server._doctor, "doctor_run", _boom)
    payload = server._doctor_payload(slim=True)
    assert payload["unavailable"] is True
    assert payload["healthy"] is True
    assert payload["findings"] == []


def test_doctor_payload_reraises_runtime_doctor_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("permission denied")

    monkeypatch.setattr(server._doctor, "doctor_run", _boom)
    with pytest.raises(OSError, match="permission denied"):
        server._doctor_payload(slim=True)


# ---------------------------------------------------------------------------
# FastMCP roundtrip (skipped when fastmcp is not installed)
# ---------------------------------------------------------------------------


fastmcp = pytest.importorskip("fastmcp")


def _run(coro: Any) -> Any:
    return (
        asyncio.get_event_loop().run_until_complete(coro)
        if False
        else asyncio.run(coro)
    )


def test_build_server_registers_tools_and_resources() -> None:
    mcp = server.build_server()

    async def _inspect() -> tuple[set[str], set[str]]:
        tools = await mcp.get_tools()
        resources = await mcp.get_resources()
        templates = await mcp.get_resource_templates()
        tool_names = set(tools)
        resource_uris = {str(uri) for uri in resources}
        resource_uris |= {str(uri) for uri in templates}
        return tool_names, resource_uris

    tool_names, resource_uris = _run(_inspect())
    assert {
        "vc_repo_full",
        "vc_doctor",
        "vc_board_status",
        "vc_launch",
        "vc_run_launch",
        "vc_run_status",
        "vc_await_run",
        "vc_run_observe",
        "vc_run_stop",
        "vc_run_retry",
        "vc_run_blocked",
        "vc_loct_capabilities",
        "vc_init",
        "vc_lifecycle_runs",
        "vc_lifecycle_status",
        "vc_lifecycle_approve",
        "vc_lifecycle_interrupt",
        "vc_lifecycle_force_audit",
        "vc_lifecycle_accept_dou",
        "vc_lifecycle_fallback",
    } <= tool_names
    assert any("vibecrafted://board/runs" in uri for uri in resource_uris)
    assert any("vibecrafted://lifecycle/schema" in uri for uri in resource_uris)
    assert any("vibecrafted://control-plane/events" in uri for uri in resource_uris)
    assert any("vibecrafted://runs/{run_id}/transcript" in uri for uri in resource_uris)
    assert any("vibecrafted://runs/{run_id}/events" in uri for uri in resource_uris)
    assert any("vibecrafted://runs/{run_id}/status" in uri for uri in resource_uris)
    assert any("vibecrafted://runs/{run_id}/report" in uri for uri in resource_uris)
    assert any("vibecrafted://capabilities/foundations" in uri for uri in resource_uris)


def _write_observe_fixture(
    tmp_path: Path, run_id: str = "impl-061414-42"
) -> dict[str, str]:
    home = tmp_path / ".vibecrafted"
    transcript = tmp_path / "transcript.log"
    report = tmp_path / "report.md"
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    transcript.write_text("abcdefghij", encoding="utf-8")
    report.write_text("# Report\nready\n", encoding="utf-8")
    event_stream = home / "control_plane" / "events.jsonl"
    event_stream.parent.mkdir(parents=True, exist_ok=True)
    event_stream.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": now,
                        "run_id": "",
                        "kind": "stream.segment",
                        "message": "event stream generation 0",
                        "payload": {
                            "schema": "vibecrafted.event-stream-segment.v1",
                            "epoch": "mcp-observe-fixture",
                            "generation": 0,
                        },
                    }
                ),
                json.dumps(
                    {
                        "ts": now,
                        "run_id": run_id,
                        "kind": "launch",
                        "message": "launched",
                        "payload": {
                            "state": "active",
                            "agent": "codex",
                            "skill": "implement",
                            "mode": "implement",
                            "root": str(tmp_path),
                            "session_id": "session-observe",
                            "launcher_pid": os.getpid(),
                            "heartbeat_at": now,
                            "report": str(report),
                            "transcript": str(transcript),
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "home": str(home),
        "run_id": run_id,
        "transcript": str(transcript),
        "report": str(report),
    }


def test_observe_falls_back_to_runtime_runs_when_snapshot_lags(
    tmp_path: Path,
) -> None:
    """A just-launched run lives in ``runtime_runs/`` before the snapshot sync
    merges it. observe must read it there (Niezmiennik 3) — not a silent miss."""
    home = tmp_path / ".vibecrafted"
    run_id = "marb-launching-1"
    run_dir = home / "control_plane" / "runtime_runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "transcript.log").write_text("launching-bytes", encoding="utf-8")

    payload = server._observe_run_once(run_id, home=str(home))

    assert payload["found"] is True
    assert payload["state"] == "launching"
    assert payload["transcript"]["bytes"] > 0
    assert payload["transcript"]["text"].startswith("launching")


def test_status_resource_resolves_launching_run_from_runtime_runs(
    tmp_path: Path,
) -> None:
    """The status resource must also see a still-launching run (runtime_runs/),
    not just observe — same contract, same eye (Niezmiennik 3)."""
    home = tmp_path / ".vibecrafted"
    run_id = "marb-status-launching-1"
    run_dir = home / "control_plane" / "runtime_runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "transcript.log").write_text("launching", encoding="utf-8")

    with server._override_vibecrafted_home(str(home)):
        payload = server._run_status_resource_payload(run_id)

    assert payload["found"] is True
    assert payload["state"] == "launching"


def test_observe_reports_missing_when_run_not_on_disk_yet(tmp_path: Path) -> None:
    """A run id with nothing on disk (RunNotResolved) resolves to a benign
    ``missing`` — loud-by-absence, never a crash."""
    home = tmp_path / ".vibecrafted"
    (home / "control_plane").mkdir(parents=True)

    payload = server._observe_run_once("ghost-run", home=str(home))

    assert payload["found"] is False
    assert payload["state"] == "missing"
    assert payload["transcript"]["bytes"] == 0


def test_mcp_observe_wait_argument_never_creates_a_private_poll_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".vibecrafted"
    (home / "control_plane").mkdir(parents=True)
    calls = 0
    original = server._server_observation.observe_run

    def counted(run_id: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return original(run_id)

    monkeypatch.setattr(server._server_observation, "observe_run", counted)

    payload = server._observe_run(
        "ghost-run",
        home=str(home),
        wait_seconds=30,
    )

    assert calls == 1
    assert payload["wait_semantics"] == "one_shot; use vc_await_run for blocking"


def test_vc_loct_capabilities_routes_to_core(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _caps(timeout: float = 5.0) -> dict[str, Any]:
        captured["timeout"] = timeout
        return {
            "schema": "vibecrafted.capabilities.v1",
            "healthy": True,
            "summary": {"ok": 1, "product_missing": 0, "product_broken": 0},
            "tools": [{"tool": "loct", "status": "ok"}],
        }

    monkeypatch.setattr(server._capabilities, "foundation_capabilities", _caps)

    from fastmcp import Client

    mcp = server.build_server()

    async def _call() -> Any:
        async with Client(mcp) as client:
            return await client.call_tool("vc_loct_capabilities", {"timeout": 2.0})

    payload = _run(_call()).data
    assert payload["schema"] == "vibecrafted.capabilities.v1"
    assert captured["timeout"] == 2.0


def test_vc_launch_delegates_to_core_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, Any] = {}

    def _normalize(payload: dict[str, Any], source_dir: str) -> Any:
        calls["payload"] = payload
        calls["source_dir"] = source_dir
        return server._workflow.WorkflowLaunchSpec(
            agent=payload["agent"],
            mode=payload.get("mode") or payload["skill"],
            skill=payload["skill"],
            prompt=payload["prompt"],
            file=payload["file"],
            runtime=payload["runtime"],
            root=payload["root"],
        )

    def _launch(
        spec: Any, source_dir: str, *, env: dict[str, str] | None = None
    ) -> Any:
        calls["launch_spec"] = spec
        calls["launch_source_dir"] = source_dir
        calls["env_home"] = (env or {}).get("VIBECRAFTED_HOME")
        return {"accepted": True, "spec": spec.to_payload()}

    monkeypatch.setattr(server._workflow, "normalize_launch_spec", _normalize)
    monkeypatch.setattr(server._workflow, "launch_workflow", _launch)

    from fastmcp import Client

    mcp = server.build_server()

    async def _call() -> Any:
        async with Client(mcp) as client:
            return await client.call_tool(
                "vc_launch",
                {
                    "skill": "workflow",
                    "agent": "codex",
                    "prompt": "go",
                    "root": str(tmp_path),
                    "source_dir": str(tmp_path / "source"),
                    "home": str(tmp_path / "home"),
                },
            )

    result = _run(_call())
    assert result.data["accepted"] is True
    assert calls["payload"]["skill"] == "workflow"
    assert calls["payload"]["agent"] == "codex"
    assert calls["source_dir"] == str(tmp_path / "source")
    assert calls["launch_source_dir"] == str(tmp_path / "source")


def test_vc_run_launch_alias_delegates_to_core_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, Any] = {}

    def _normalize(payload: dict[str, Any], source_dir: str) -> Any:
        calls["payload"] = payload
        calls["source_dir"] = source_dir
        return server._workflow.WorkflowLaunchSpec(
            agent=payload["agent"],
            mode=payload.get("mode") or payload["skill"],
            skill=payload["skill"],
            prompt=payload["prompt"],
            file=payload["file"],
            runtime=payload["runtime"],
            root=payload["root"],
        )

    def _launch(
        spec: Any, source_dir: str, *, env: dict[str, str] | None = None
    ) -> Any:
        calls["launch_spec"] = spec
        calls["launch_source_dir"] = source_dir
        calls["env_home"] = (env or {}).get("VIBECRAFTED_HOME")
        return {"accepted": True, "spec": spec.to_payload()}

    monkeypatch.setattr(server._workflow, "normalize_launch_spec", _normalize)
    monkeypatch.setattr(server._workflow, "launch_workflow", _launch)

    from fastmcp import Client

    mcp = server.build_server()

    async def _call() -> Any:
        async with Client(mcp) as client:
            return await client.call_tool(
                "vc_run_launch",
                {
                    "skill": "workflow",
                    "agent": "codex",
                    "prompt": "go",
                    "root": str(tmp_path),
                    "source_dir": str(tmp_path / "source"),
                    "home": str(tmp_path / "home"),
                },
            )

    result = _run(_call())
    assert result.data["accepted"] is True
    assert calls["payload"]["skill"] == "workflow"
    assert calls["payload"]["agent"] == "codex"
    assert calls["source_dir"] == str(tmp_path / "source")
    assert calls["launch_source_dir"] == str(tmp_path / "source")
    assert calls["env_home"] == str(tmp_path / "home")


def test_vc_repo_full_returns_ground_truth(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)

    from fastmcp import Client

    mcp = server.build_server()

    async def _call() -> Any:
        async with Client(mcp) as client:
            return await client.call_tool("vc_repo_full", {"project": str(repo)})

    result = _run(_call())
    payload = result.data
    assert payload["branch"] == "main"
    assert payload["repo"] == "repo"
    assert payload["recent_commits"]


def test_vc_init_slim_stays_within_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)

    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))

    from fastmcp import Client

    mcp = server.build_server()

    async def _call() -> Any:
        async with Client(mcp) as client:
            return await client.call_tool(
                "vc_init", {"project": str(repo), "slim": True}
            )

    result = _run(_call())
    payload = result.data
    assert "ground_truth" in payload
    assert "doctor" in payload
    assert "synthesis" in payload
    slim_meta = payload.get("_slim") or {}
    assert slim_meta.get("within_budget") is True
    assert slim_meta.get("bytes", 0) <= server.SLIM_BUDGET_BYTES


def test_board_runs_resource_returns_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))

    from fastmcp import Client

    mcp = server.build_server()

    async def _call() -> Any:
        async with Client(mcp) as client:
            return await client.read_resource("vibecrafted://board/runs")

    result = _run(_call())
    assert result
    payload = json.loads(result[0].text)
    assert "active_runs" in payload
    assert "recent_runs" in payload


def test_vc_run_status_and_await_use_control_plane_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    reports = home / "artifacts" / "Vetcoders" / "vibecrafted" / "2026_0519" / "reports"
    reports.mkdir(parents=True)
    (reports / "impl.meta.json").write_text(
        json.dumps(
            {
                "run_id": "impl-050505-42",
                "status": "completed",
                "agent": "codex",
                "mode": "implement",
                "root": str(tmp_path),
                "updated_at": "2026-05-19T00:00:00+00:00",
                "skill_code": "impl",
                "exit_code": 0,
                "liveness": "terminal",
                "launcher_pid": 1_073_741_824,
                "completed_at": "2026-05-19T00:00:01+00:00",
                "session_id": "session-xyz",
            }
        ),
        encoding="utf-8",
    )

    from fastmcp import Client

    mcp = server.build_server()

    async def _call_status() -> Any:
        async with Client(mcp) as client:
            return await client.call_tool(
                "vc_run_status",
                {"run_id": "impl-050505-42", "home": str(home)},
            )

    async def _call_await() -> Any:
        async with Client(mcp) as client:
            return await client.call_tool(
                "vc_await_run",
                {
                    "run_id": "impl-050505-42",
                    "home": str(home),
                    "timeout_seconds": 0,
                    "interval_seconds": 0.1,
                },
            )

    status_payload = _run(_call_status()).data
    await_payload = _run(_call_await()).data

    assert status_payload["found"] is True
    assert status_payload["run"]["session_id"] == "session-xyz"
    assert status_payload["run"]["launcher_pid"] == 1_073_741_824
    assert await_payload["completed"] is True
    assert await_payload["run"]["exit_code"] == 0


def test_vc_run_observe_returns_bounded_cursor_deltas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _write_observe_fixture(tmp_path)
    monkeypatch.setenv("VIBECRAFTED_HOME", fixture["home"])

    from fastmcp import Client

    mcp = server.build_server()

    async def _call(payload: dict[str, Any]) -> Any:
        async with Client(mcp) as client:
            return await client.call_tool("vc_run_observe", payload)

    first = _run(
        _call(
            {
                "run_id": fixture["run_id"],
                "home": fixture["home"],
                "cursor": {"event_offset": 0, "transcript_offset": 0},
                "max_bytes": 4,
                "max_events": 100,
            }
        )
    ).data

    assert first["found"] is True
    assert first["operator_state"] == "running"
    launch_events = [event for event in first["events"] if event["kind"] == "launch"]
    assert len(launch_events) == 1
    assert all(event["cursor"].startswith("v2:") for event in first["events"])
    assert first["cursor"]["event_cursor"] == first["events"][-1]["cursor"]
    assert "event_offset" not in first["cursor"]
    assert first["transcript"]["offset"] == 0
    assert first["transcript"]["next_offset"] == 4
    assert first["transcript"]["bytes"] == 4
    assert first["transcript"]["text"] == "abcd"
    assert first["transcript"]["truncated"] is True
    assert first["cursor"]["transcript_offset"] == 4
    assert first["terminal"] is False
    assert first["report_ready"] is True

    second = _run(
        _call(
            {
                "run_id": fixture["run_id"],
                "home": fixture["home"],
                "cursor": first["cursor"],
                "max_bytes": 4,
            }
        )
    ).data

    assert second["events"] == []
    assert second["transcript"]["offset"] == 4
    assert second["transcript"]["next_offset"] == 8
    assert second["transcript"]["text"] == "efgh"
    assert second["transcript"]["truncated"] is True

    at_end = _run(
        _call(
            {
                "run_id": fixture["run_id"],
                "home": fixture["home"],
                "cursor": {
                    "event_cursor": first["cursor"]["event_cursor"],
                    "transcript_offset": 10,
                },
                "max_bytes": 4,
            }
        )
    ).data

    assert at_end["events"] == []
    assert at_end["transcript"]["bytes"] == 0
    assert at_end["transcript"]["text"] == ""
    assert at_end["cursor"]["transcript_offset"] == 10


def test_mcp_observe_persists_opaque_event_cursor(tmp_path: Path) -> None:
    fixture = _write_observe_fixture(tmp_path, run_id="opaque-cursor-run")

    migrated = server._observe_run_once(
        fixture["run_id"],
        home=fixture["home"],
        cursor={"event_offset": 0, "transcript_offset": 0},
    )

    assert "event_offset" not in migrated["cursor"]
    opaque = migrated["cursor"]["event_cursor"]
    assert isinstance(opaque, str)
    assert opaque.startswith("v2:mcp-observe-fixture:0:")
    assert migrated["events"][0]["cursor"].startswith("v2:mcp-observe-fixture:0:")
    assert int(opaque.rsplit(":", 1)[1]) >= int(
        migrated["events"][0]["cursor"].rsplit(":", 1)[1]
    )

    resumed = server._observe_run_once(
        fixture["run_id"],
        home=fixture["home"],
        cursor={"event_cursor": opaque, "transcript_offset": 0},
    )
    assert resumed["events"] == []
    assert resumed["cursor"]["event_cursor"] == opaque


def test_vc_run_observe_defaults_to_recent_tail_for_long_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _write_observe_fixture(tmp_path)
    monkeypatch.setenv("VIBECRAFTED_HOME", fixture["home"])
    transcript = Path(fixture["transcript"])
    transcript.write_text("old-head" + ("x" * 200) + "recent-tail", encoding="utf-8")

    payload = server._observe_run_once(
        fixture["run_id"], home=fixture["home"], max_bytes=32
    )

    assert payload["transcript"]["bytes"] <= 32
    assert "recent-tail" in payload["transcript"]["text"]
    assert "old-head" not in payload["transcript"]["text"]
    assert payload["transcript"]["truncated"] is True


def test_observe_bounds_complete_serialized_result() -> None:
    payload = {
        "events": [
            {"payload": {"blob": "x" * 20_000}, "cursor": str(index)}
            for index in range(10)
        ],
        "transcript": {
            "text": "old-head" + ("y" * 100_000) + "recent-tail",
            "bytes": 100_019,
            "truncated": False,
            "next_offset": 100_019,
        },
    }

    bounded = server._bound_observe_payload(payload)

    encoded = json.dumps(bounded, ensure_ascii=False).encode("utf-8")
    assert len(encoded) <= server.OBSERVE_RESULT_MAX_BYTES
    assert bounded["transcript"]["truncated"] is True
    assert bounded["transcript"]["text"].endswith("recent-tail")


def test_run_resource_templates_resolve_bounded_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _write_observe_fixture(tmp_path)
    monkeypatch.setenv("VIBECRAFTED_HOME", fixture["home"])

    from fastmcp import Client

    mcp = server.build_server()
    run_id = fixture["run_id"]

    async def _read(uri: str) -> Any:
        async with Client(mcp) as client:
            return await client.read_resource(uri)

    status = json.loads(_run(_read(f"vibecrafted://runs/{run_id}/status"))[0].text)
    events = json.loads(_run(_read(f"vibecrafted://runs/{run_id}/events"))[0].text)
    transcript = json.loads(
        _run(_read(f"vibecrafted://runs/{run_id}/transcript"))[0].text
    )
    report = json.loads(_run(_read(f"vibecrafted://runs/{run_id}/report"))[0].text)

    assert status["found"] is True
    assert status["operator_state"] == "running"
    assert any(event["kind"] == "launch" for event in events["events"])
    assert transcript["bytes"] == 10
    assert transcript["truncated"] is False
    assert transcript["text"] == "abcdefghij"
    assert report["found"] is True
    assert report["report"]["text"].startswith("# Report")


def test_vc_run_stop_and_retry_route_to_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _stop(run_id: str, *, reason: str = "") -> dict[str, Any]:
        captured["stop"] = {"run_id": run_id, "reason": reason}
        return {"accepted": True, "run_id": run_id, "reason": reason}

    def _retry(
        run_id: str,
        source_dir: str = ".",
        *,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        captured["retry"] = {
            "run_id": run_id,
            "source_dir": source_dir,
            "home": (env or {}).get("VIBECRAFTED_HOME"),
        }
        return {
            "accepted": True,
            "run_id": run_id,
            "retry_run_id": "wflw-010101-0001",
        }

    def _block(
        run_id: str,
        *,
        reason: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        captured["block"] = {"run_id": run_id, "reason": reason, "note": note}
        return {"accepted": True, "run_id": run_id, "reason": reason, "note": note}

    monkeypatch.setattr(server._workflow, "stop_run", _stop)
    monkeypatch.setattr(server._workflow, "retry_run", _retry)
    monkeypatch.setattr(server._workflow, "block_run", _block)

    home = tmp_path / ".vibecrafted"
    home.mkdir(parents=True)

    from fastmcp import Client

    mcp = server.build_server()

    async def _call_stop() -> Any:
        async with Client(mcp) as client:
            return await client.call_tool(
                "vc_run_stop",
                {
                    "run_id": "wflw-000000-0000",
                    "reason": "manual-stop",
                    "home": str(home),
                },
            )

    async def _call_retry() -> Any:
        async with Client(mcp) as client:
            return await client.call_tool(
                "vc_run_retry",
                {
                    "run_id": "wflw-000000-0000",
                    "source_dir": str(tmp_path / "src"),
                    "home": str(home),
                },
            )

    async def _call_block() -> Any:
        async with Client(mcp) as client:
            return await client.call_tool(
                "vc_run_blocked",
                {
                    "run_id": "wflw-000000-0000",
                    "reason": "needs-intervention",
                    "note": "missing api key",
                    "home": str(home),
                },
            )

    stop_payload = _run(_call_stop()).data
    retry_payload = _run(_call_retry()).data
    block_payload = _run(_call_block()).data

    assert stop_payload["accepted"] is True
    assert stop_payload["run_id"] == "wflw-000000-0000"
    assert retry_payload["accepted"] is True
    assert retry_payload["retry_run_id"] == "wflw-010101-0001"
    assert block_payload["accepted"] is True
    assert captured["stop"]["reason"] == "manual-stop"
    assert captured["retry"]["source_dir"] == str(tmp_path / "src")
    assert captured["retry"]["home"] == str(home)
    assert captured["block"]["reason"] == "needs-intervention"
    assert captured["block"]["note"] == "missing api key"


def _seed_lifecycle_run(home: Path, run_id: str, report: Path) -> Path:
    run_dir = home / "control_plane" / "lifecycle_runs" / run_id
    run_dir.mkdir(parents=True)
    state_path = run_dir / "state.json"
    state = {
        "schema": "vibecrafted.lifecycle.v1",
        "run_id": run_id,
        "workflow": "vc-ship",
        "agent": "codex",
        "root": str(home),
        "status": "launching",
        "parent_run_id": "",
        "operator_actions": [],
        "human_controls": ["approve_transition", "interrupt_workflow"],
        "state_path": str(state_path),
        "report_path": str(run_dir / "report.md"),
        "transcript_path": str(run_dir / "transcript.log"),
        "spec": {
            "workflow_id": "vc-ship",
            "agent": "codex",
            "prompt": "mission",
            "file": "",
            "root": str(home),
            "runtime": "headless",
            "await_stages": False,
            "start_stage": "scaffold",
            "count": None,
            "depth": None,
            "previous_reports": [],
        },
        "manifest": {"id": "vc-ship", "stages": [{"id": "scaffold"}]},
        "baton": {
            "from_stage": "scaffold",
            "next_stage": "implement",
            "next_agent": "codex",
            "reason": "stage_launched_without_await",
            "previous_reports": [str(report)],
        },
        "stages": [
            {
                "id": "scaffold",
                "phase": "read",
                "launch": {"run_id": "scaf-1", "report": str(report)},
            }
        ],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return state_path


def test_lifecycle_tools_status_and_approve_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".vibecrafted"
    report = tmp_path / "scaffold-report.md"
    report.write_text("scaffold ok\n", encoding="utf-8")
    _seed_lifecycle_run(home, "life-ship-000000-00000", report)

    launched: list[Any] = []

    def _fake_run_lifecycle(spec: Any) -> dict[str, Any]:
        launched.append(spec)
        return {
            "run_id": "life-cont-1",
            "workflow": "vc-ship",
            "status": "launching",
            "stages": [],
            "baton": {"next_stage": "review", "previous_reports": []},
        }

    monkeypatch.setattr(server._lifecycle_control, "run_lifecycle", _fake_run_lifecycle)

    from fastmcp import Client

    mcp = server.build_server()

    async def _call(tool: str, args: dict[str, Any]) -> Any:
        async with Client(mcp) as client:
            return await client.call_tool(tool, args)

    listed = _run(
        _call("vc_lifecycle_runs", {"workflow_id": "vc-ship", "home": str(home)})
    ).data
    assert listed["count"] == 1
    assert listed["runs"][0]["run_id"] == "life-ship-000000-00000"

    status = _run(_call("vc_lifecycle_status", {"home": str(home)})).data
    assert status["ok"] is True
    assert status["result"]["schema"] == "vibecrafted.lifecycle.v1"
    assert status["result"]["next_stage"] == "implement"
    assert status["result"]["human_controls"] == [
        "approve_transition",
        "interrupt_workflow",
    ]
    assert status["result"]["previous_reports"] == [str(report)]

    approved = _run(_call("vc_lifecycle_approve", {"home": str(home)})).data
    assert approved["ok"] is True
    assert approved["result"]["run_id"] == "life-cont-1"
    assert len(launched) == 1
    assert launched[0].start_stage == "implement"
    assert launched[0].previous_reports == (str(report),)

    # Unknown verb target: fallback rejects a stage the manifest lacks and
    # human_controls gating still applies (no choose_fallback_stage here).
    fallback = _run(
        _call("vc_lifecycle_fallback", {"stage": "release", "home": str(home)})
    ).data
    assert fallback["ok"] is False
    assert "choose_fallback_stage" in fallback["error"]


def test_lifecycle_schema_resource_returns_packaged_contract() -> None:
    from fastmcp import Client

    mcp = server.build_server()

    async def _call() -> Any:
        async with Client(mcp) as client:
            return await client.read_resource("vibecrafted://lifecycle/schema")

    result = _run(_call())
    payload = json.loads(result[0].text)
    assert payload["$id"] == "vibecrafted.lifecycle.v1"
    assert payload["properties"]["schema"]["const"] == "vibecrafted.lifecycle.v1"
    assert "worker_report_frontmatter" in payload["$defs"]
