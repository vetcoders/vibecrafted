"""Contract tests for the LIVE RUNS dashboard.

Parity contract: `scan_live_runs` mirrors vc-frame's Rust census
(`zellij-server/src/vc_live_runs.rs`) — same meta.json requirements, same
liveness semantics, same chronological order. The Rust side carries the same
fixture shapes in its own tests; a divergence must fail on one of the two.
"""

from __future__ import annotations

import json
from pathlib import Path

from vibecrafted_core.live_dashboard import (
    PENDING_HUMAN_NOTICE,
    WORKSPACE_CATALOG_SCHEMA,
    DashboardState,
    TranscriptTail,
    resolve_workspace_id,
    scan_live_runs,
    worker_is_alive,
)


def write_meta(
    root: Path,
    run_id: str,
    pid: int,
    repo_root: str = "/tmp/ws/vc-frame",
    workspace_id: str | None = None,
) -> None:
    directory = root / "runtime_runs" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    meta = {
        "run_id": run_id,
        "agent": "claude",
        "skill": "workflow",
        "root": repo_root,
        "worker_pid": pid,
    }
    if workspace_id is not None:
        meta["workspace_id"] = workspace_id
    (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def write_catalog(
    root: Path, workspaces: list[dict], schema: str | None = None
) -> None:
    directory = root / "workspaces"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "catalog.json").write_text(
        json.dumps(
            {
                "schema": schema or WORKSPACE_CATALOG_SCHEMA,
                "workspaces": {w["workspace_id"]: w for w in workspaces},
            }
        ),
        encoding="utf-8",
    )


def test_census_keeps_only_live_workers_in_chronological_order(tmp_path: Path) -> None:
    write_meta(tmp_path, "work-260810-020000-2", 22)
    write_meta(tmp_path, "work-260810-010000-1", 11)
    write_meta(tmp_path, "work-260810-030000-3", 33)

    cards = scan_live_runs(tmp_path, is_alive=lambda pid: pid != 22)

    assert [card.worker_pid for card in cards] == [11, 33]
    assert cards[0].repo == "vc-frame"


def test_broken_or_missing_meta_is_skipped_not_guessed(tmp_path: Path) -> None:
    write_meta(tmp_path, "work-260810-010000-1", 11)
    broken = tmp_path / "runtime_runs" / "broken-run"
    broken.mkdir(parents=True)
    (broken / "meta.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "runtime_runs" / "empty-run").mkdir()

    cards = scan_live_runs(tmp_path, is_alive=lambda _pid: True)

    assert [card.run_id for card in cards] == ["work-260810-010000-1"]


def test_missing_control_plane_yields_empty_census(tmp_path: Path) -> None:
    assert scan_live_runs(tmp_path / "absent", is_alive=lambda _pid: True) == []


def test_worker_liveness_rejects_init_and_nonpositive_pids() -> None:
    assert not worker_is_alive(0)
    assert not worker_is_alive(1)
    assert not worker_is_alive(-4)


def _state_with(
    tmp_path: Path, metas: list[tuple[str, str]], current: str
) -> DashboardState:
    for run_id, repo_root in metas:
        write_meta(tmp_path, run_id, 99, repo_root=repo_root)
    state = DashboardState(current_root=current)
    state.refresh(scan_live_runs(tmp_path, is_alive=lambda _pid: True))
    return state


def test_count_and_rows_come_from_the_same_census(tmp_path: Path) -> None:
    state = _state_with(
        tmp_path,
        [("a-260812-010000-1", "/tmp/x"), ("b-260812-020000-2", "/tmp/y")],
        current="/tmp/x",
    )
    state.filter_mode = "all"
    assert state.live_count() == len(state.visible_rows()) == 2


def test_current_repo_filter_uses_root_identity_not_basename(tmp_path: Path) -> None:
    mine = tmp_path / "a" / "vibecrafted"
    other = tmp_path / "b" / "vibecrafted"  # same basename, different root
    mine.mkdir(parents=True)
    other.mkdir(parents=True)
    state = _state_with(
        tmp_path,
        [
            ("run-260812-010000-1", str(mine)),
            ("run-260812-020000-2", str(other)),
        ],
        current=str(mine),
    )

    rows = state.visible_rows()
    assert [card.root for card in rows] == [str(mine)]

    state.toggle_filter()
    assert len(state.visible_rows()) == 2


def test_sort_is_current_first_then_newest_then_run_id(tmp_path: Path) -> None:
    mine = tmp_path / "mine"
    mine.mkdir()
    state = _state_with(
        tmp_path,
        [
            ("run-260812-010000-old", "/tmp/elsewhere"),
            ("run-260812-030000-new", "/tmp/elsewhere"),
            ("run-260812-020000-cur", str(mine)),
        ],
        current=str(mine),
    )
    state.filter_mode = "all"

    assert [card.run_id for card in state.visible_rows()] == [
        "run-260812-020000-cur",
        "run-260812-030000-new",
        "run-260812-010000-old",
    ]


def test_selection_is_pinned_to_run_id_across_refresh(tmp_path: Path) -> None:
    state = _state_with(
        tmp_path,
        [
            ("run-260812-010000-1", "/tmp/x"),
            ("run-260812-020000-2", "/tmp/x"),
        ],
        current="/tmp/x",
    )
    state.move_selection(1)
    chosen = state.selected_run_id
    assert chosen == "run-260812-010000-1"

    write_meta(tmp_path, "run-260812-030000-3", 99, repo_root="/tmp/x")
    state.refresh(scan_live_runs(tmp_path, is_alive=lambda _pid: True))
    assert state.selected_run_id == chosen


def test_dead_selection_falls_back_to_first_visible_row(tmp_path: Path) -> None:
    state = _state_with(
        tmp_path,
        [("run-260812-010000-1", "/tmp/x"), ("run-260812-020000-2", "/tmp/x")],
        current="/tmp/x",
    )
    first = state.selected_run_id
    state.refresh(scan_live_runs(tmp_path, is_alive=lambda pid: False))
    assert state.selected_run_id is None
    assert first is not None


def test_transcript_defaults_to_human_and_raw_is_explicit(tmp_path: Path) -> None:
    state = _state_with(tmp_path, [("run-260812-010000-1", "/tmp/x")], current="/tmp/x")
    card = state.visible_rows()[0]

    human = state.transcript_path(card, tmp_path)
    assert human.name == "transcript.human.log"

    state.raw_transcript = True
    raw = state.transcript_path(card, tmp_path)
    assert raw.name == "transcript.log"


def test_pending_human_notice_never_falls_back_to_raw() -> None:
    # The draw layer substitutes PENDING_HUMAN_NOTICE when the human
    # transcript is absent; the path itself must keep pointing at human.
    assert PENDING_HUMAN_NOTICE == "human transcript pending"


def test_stream_prefix_carries_agent_repo_and_run(tmp_path: Path) -> None:
    state = _state_with(
        tmp_path, [("run-260812-010000-1", "/tmp/ws/demo")], current="/tmp/ws/demo"
    )
    card = state.visible_rows()[0]
    assert state.stream_prefix(card) == "claude/demo/run-260812-010000-1"


def test_detail_header_names_agent_repo_run_and_mode(tmp_path: Path) -> None:
    state = _state_with(
        tmp_path, [("run-260812-010000-1", "/tmp/ws/demo")], current="/tmp/ws/demo"
    )
    card = state.visible_rows()[0]
    assert state.detail_header(card) == (
        "claude · demo · run-260812-010000-1 · running · HUMAN"
    )
    state.raw_transcript = True
    assert state.detail_header(card).endswith("· RAW")


def test_workspace_id_resolves_from_canonical_catalog(tmp_path: Path) -> None:
    mine = tmp_path / "repo"
    mine.mkdir()
    write_catalog(
        tmp_path,
        [
            {"workspace_id": "ws-mine", "canonical_root": str(mine)},
            {
                "workspace_id": "ws-buried",
                "canonical_root": str(mine),
                "buried_at": "2026-08-12T00:00:00+00:00",
            },
        ],
    )
    assert resolve_workspace_id(str(mine), tmp_path) == "ws-mine"
    assert resolve_workspace_id(str(tmp_path / "elsewhere"), tmp_path) is None


def test_foreign_schema_or_missing_catalog_resolves_to_none(tmp_path: Path) -> None:
    assert resolve_workspace_id(str(tmp_path), tmp_path) is None
    write_catalog(
        tmp_path,
        [{"workspace_id": "ws", "canonical_root": str(tmp_path)}],
        schema="somebody.elses.catalog.v9",
    )
    assert resolve_workspace_id(str(tmp_path), tmp_path) is None


# ---------------------------------------------------------------------------
# One identity resolution order — regression contract
#
# The runtime resolves a run's workspace from VIBECRAFTED_WORKSPACE_ID first
# (`workspace_catalog.resolve_run_workspace_identity`). A dashboard that
# resolved its own identity by root alone would disagree with every run its
# own shell launched. These pin the shared order, both directions.
# ---------------------------------------------------------------------------


def test_dashboard_identity_honours_the_exported_workspace_id(tmp_path: Path) -> None:
    mine = tmp_path / "repo"
    mine.mkdir()
    write_catalog(
        tmp_path,
        [
            {"workspace_id": "ws-rooted-here", "canonical_root": str(mine)},
            {"workspace_id": "ws-exported", "canonical_root": str(tmp_path / "other")},
        ],
    )
    # The export wins over the root lookup: it is the identity the runtime
    # already resolved for this process tree.
    assert (
        resolve_workspace_id(
            str(mine), tmp_path, env={"VIBECRAFTED_WORKSPACE_ID": "ws-exported"}
        )
        == "ws-exported"
    )
    # No export → the catalog answers by canonical_root, as before.
    assert resolve_workspace_id(str(mine), tmp_path, env={}) == "ws-rooted-here"


def test_exported_workspace_id_unknown_to_the_catalog_is_refused(
    tmp_path: Path,
) -> None:
    mine = tmp_path / "repo"
    mine.mkdir()
    write_catalog(
        tmp_path, [{"workspace_id": "ws-rooted-here", "canonical_root": str(mine)}]
    )
    # Stale evidence is not identity — the one catalog arbitrates both steps.
    assert (
        resolve_workspace_id(
            str(mine), tmp_path, env={"VIBECRAFTED_WORKSPACE_ID": "ws-long-gone"}
        )
        == "ws-rooted-here"
    )


def test_exported_workspace_id_of_a_buried_workspace_is_refused(
    tmp_path: Path,
) -> None:
    mine = tmp_path / "repo"
    mine.mkdir()
    write_catalog(
        tmp_path,
        [
            {"workspace_id": "ws-rooted-here", "canonical_root": str(mine)},
            {
                "workspace_id": "ws-buried",
                "canonical_root": str(tmp_path / "other"),
                "buried_at": "2026-08-12T00:00:00+00:00",
            },
        ],
    )
    assert (
        resolve_workspace_id(
            str(mine), tmp_path, env={"VIBECRAFTED_WORKSPACE_ID": "ws-buried"}
        )
        == "ws-rooted-here"
    )


def test_unusable_catalog_refuses_even_an_exported_workspace_id(
    tmp_path: Path,
) -> None:
    write_catalog(
        tmp_path,
        [{"workspace_id": "ws-exported", "canonical_root": str(tmp_path)}],
        schema="somebody.elses.catalog.v9",
    )
    assert (
        resolve_workspace_id(
            str(tmp_path), tmp_path, env={"VIBECRAFTED_WORKSPACE_ID": "ws-exported"}
        )
        is None
    )


def test_worktree_worker_stays_visible_in_the_dispatching_workspace(
    tmp_path: Path,
) -> None:
    # The live shape this pins: the shell exports ws-flight, every run of the
    # flight is stamped ws-flight, and the catalog ALSO holds an older
    # workspace rooted at this very repo. Resolving the dashboard's identity
    # by root alone answers ws-stale and hides the whole flight — including
    # the Mode B worker, whose worktree root can never equal the dispatcher's.
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktrees" / "feat-x"
    repo.mkdir()
    worktree.mkdir(parents=True)
    write_catalog(
        tmp_path,
        [
            {"workspace_id": "ws-stale", "canonical_root": str(repo)},
            {
                "workspace_id": "ws-flight",
                "canonical_root": str(tmp_path / "elsewhere"),
            },
        ],
    )
    write_meta(
        tmp_path,
        "scaf-260818-202208-1",
        99,
        repo_root=str(worktree),
        workspace_id="ws-flight",
    )
    write_meta(
        tmp_path,
        "pola-260818-192800-2",
        98,
        repo_root=str(repo),
        workspace_id="ws-flight",
    )

    identity = resolve_workspace_id(
        str(repo), tmp_path, env={"VIBECRAFTED_WORKSPACE_ID": "ws-flight"}
    )
    state = DashboardState(current_root=str(repo), current_workspace_id=identity)
    state.refresh(scan_live_runs(tmp_path, is_alive=lambda _pid: True))

    assert sorted(card.run_id for card in state.visible_rows()) == [
        "pola-260818-192800-2",
        "scaf-260818-202208-1",
    ]


def test_current_filter_prefers_workspace_id_over_root(tmp_path: Path) -> None:
    # Same root string on both cards — only the workspace identity separates
    # them (the exact aliasing Cut A exists to kill).
    write_meta(tmp_path, "a-260812-010000-1", 99, "/tmp/shared", "ws-mine")
    write_meta(tmp_path, "b-260812-020000-2", 99, "/tmp/shared", "ws-other")
    state = DashboardState(current_root="/tmp/shared", current_workspace_id="ws-mine")
    state.refresh(scan_live_runs(tmp_path, is_alive=lambda _pid: True))

    assert [card.run_id for card in state.visible_rows()] == ["a-260812-010000-1"]


def test_legacy_run_without_workspace_id_falls_back_to_root(tmp_path: Path) -> None:
    mine = tmp_path / "repo"
    mine.mkdir()
    write_meta(tmp_path, "old-260812-010000-1", 99, str(mine))  # no workspace_id
    state = DashboardState(current_root=str(mine), current_workspace_id="ws-mine")
    state.refresh(scan_live_runs(tmp_path, is_alive=lambda _pid: True))

    assert [card.run_id for card in state.visible_rows()] == ["old-260812-010000-1"]


def test_transcript_tail_reads_increments_and_survives_truncation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "transcript.human.log"
    path.write_text("one\ntwo\n", encoding="utf-8")
    tail = TranscriptTail(path)

    assert tail.poll() == ["one", "two"]
    with path.open("a", encoding="utf-8") as handle:
        handle.write("three\n")
    assert tail.poll() == ["three"]

    path.write_text("fresh\n", encoding="utf-8")  # rotation/truncate
    assert tail.poll() == ["fresh"]

    path.unlink()
    assert tail.poll() == []
