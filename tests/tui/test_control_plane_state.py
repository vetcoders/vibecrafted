from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from vibecrafted_core import control_plane


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def test_sync_state_normalizes_agent_meta_and_lock(monkeypatch, tmp_path: Path) -> None:
    crafted_home = tmp_path / ".vibecrafted"
    meta_path = (
        crafted_home
        / "artifacts"
        / "2026_0416"
        / "codex"
        / "workflow"
        / "run-123.meta.json"
    )
    lock_path = crafted_home / "locks" / "workflow" / "run-123.lock"
    meta_path.parent.mkdir(parents=True)
    lock_path.parent.mkdir(parents=True)

    meta_path.write_text(
        json.dumps(
            {
                "run_id": "run-123",
                "agent": "codex",
                "skill_code": "wflw",
                "mode": "implement",
                "status": "running",
                "root": str(tmp_path / "project"),
                "updated_at": _now_iso(),
                "started_at": _now_iso(),
                "report": str(tmp_path / "report.md"),
                "transcript": str(tmp_path / "transcript.log"),
            }
        ),
        encoding="utf-8",
    )
    lock_path.write_text(
        "\n".join(
            [
                "run_id=run-123",
                "agent=codex",
                "skill=wflw",
                "mode=implement",
                f"root={tmp_path / 'project'}",
                f"started={_now_iso()}",
                "status=running",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))

    first = control_plane.sync_state()
    second = control_plane.sync_state()

    assert [run["run_id"] for run in first["active_runs"]] == ["run-123"]
    assert first["active_runs"][0]["skill"] == "workflow"
    assert first["active_runs"][0]["lock_present"] is True
    assert first["active_runs"][0]["operator_session"] == "project-run-123"
    assert (crafted_home / "control_plane" / "runs" / "run-123.json").is_file()
    assert len(first["events"]) == 1
    assert len(second["events"]) == 1


def test_sync_state_reads_marbles_progress(monkeypatch, tmp_path: Path) -> None:
    crafted_home = tmp_path / ".vibecrafted"
    state_path = crafted_home / "marbles" / "run-456" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "run_id": "run-456",
                "agent": "claude",
                "status": "running",
                "mode": "steered",
                "root": str(tmp_path / "workspace"),
                "started_at": _now_iso(),
                "updated_at": _now_iso(),
                "current_loop": 2,
                "total_loops": 4,
                "loops": [
                    {
                        "report": str(tmp_path / "round-2.md"),
                        "transcript": str(tmp_path / "round-2.log"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))

    payload = control_plane.sync_state()

    assert payload["active_runs"][0]["run_id"] == "run-456"
    assert payload["active_runs"][0]["skill"] == "marbles"
    assert payload["active_runs"][0]["current_loop"] == 2
    assert payload["active_runs"][0]["total_loops"] == 4
    assert payload["active_runs"][0]["latest_report"].endswith("round-2.md")
