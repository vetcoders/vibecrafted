from __future__ import annotations

import json

from vibecrafted_core import run_board


def _run(run_id: str, *, started_at: str, root: str = "/repo") -> dict[str, str]:
    return {
        "run_id": run_id,
        "state": "active",
        "started_at": started_at,
        "root": root,
    }


def test_lifecycle_activity_ignores_today_and_display_limit(monkeypatch) -> None:
    old_active = _run("old-active", started_at="2025-01-01T00:00:00+00:00")
    recent = [
        _run(f"recent-{index}", started_at=f"2026-08-25T12:{index:02d}:00+00:00")
        for index in range(13)
    ]
    monkeypatch.setattr(
        run_board,
        "sync_state",
        lambda: {
            "active_runs": [old_active],
            "stalled_runs": [],
            "recent_runs": recent,
        },
    )

    activity = run_board.collect_lifecycle_activity()

    assert [lane["run_id"] for lane in activity["lanes"]] == ["old-active"]
    assert activity["summary"] == {"lanes": 1, "worktrees": 0}


def test_lifecycle_activity_includes_stalled_and_worktree_lanes(
    monkeypatch, tmp_path
) -> None:
    custom_home = tmp_path / "custom-vibecrafted-home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(custom_home))
    worktree = _run(
        "worktree-active",
        started_at="2026-08-24T01:00:00+00:00",
        root=str(custom_home / "worktrees/vetcoders/vibecrafted/2026_0825/cut"),
    )
    stalled = {
        **_run("stalled", started_at="2026-08-23T01:00:00+00:00"),
        "state": "stalled",
    }
    monkeypatch.setattr(
        run_board,
        "sync_state",
        lambda: {
            "active_runs": [worktree],
            "stalled_runs": [stalled, worktree],
            "recent_runs": [],
        },
    )

    activity = run_board.collect_lifecycle_activity()

    assert {lane["run_id"] for lane in activity["lanes"]} == {
        "worktree-active",
        "stalled",
    }
    assert activity["summary"] == {"lanes": 2, "worktrees": 1}


def test_status_activity_json_is_the_unfiltered_machine_contract(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        run_board,
        "sync_state",
        lambda: {
            "active_runs": [],
            "stalled_runs": [],
            "recent_runs": [_run("recent", started_at="2026-08-25T01:00:00+00:00")],
        },
    )

    assert run_board.status_main(["--activity", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "vibecrafted.lifecycle-activity.v1"
    assert payload["summary"] == {"lanes": 0, "worktrees": 0}
    assert payload["lanes"] == []
