from __future__ import annotations

from pathlib import Path

import pytest
from vibecrafted_core import ship

TRACKER = """\
# Tracker — fixture

| Wave | Cut | Brief | state | commit SHA | Gate |
| ---- | --- | ----- | ----- | ---------- | ---- |
| W0 | W0-a | briefs/W0-a.md | [?] | aaa111 | dmg rebuild pending |
| W1 | W1-a | briefs/W1-a.md | [x] | bbb222 | guard green |
| W2 | W2-a | briefs/W2-a.md | [ ] | — | not started |

state legend: `[ ]` pending · `[x]` delivered.

## Recovery log
do not parse this [x] as a cut
"""

HISTORY = """\
# Vibecrafted 4.2.0 roadmap — measured truths, finished seams

Status: planned. Historical notes follow the generated block.

## Implement stage — what landed, 2026-08-18

| Cut  | Stage snapshot | Landed SHA(s) |
| ---- | -------------- | ------------- |
| W0-a | `[!]`          | recon only    |
| W1-a | `[~]`          | bbb222        |

## Thesis

Keep this prose.
"""


def _plan(tmp_path: Path, text: str = TRACKER) -> Path:
    root = tmp_path / "plan"
    root.mkdir()
    (root / "tracker.md").write_text(text, encoding="utf-8")
    return root


def test_parse_tracker_cuts_reads_state_column_only() -> None:
    cuts = ship.parse_tracker_cuts(TRACKER)
    assert [cut.cut_id for cut in cuts] == ["W0-a", "W1-a", "W2-a"]
    assert [cut.state for cut in cuts] == ["[?]", "[x]", "[ ]"]
    assert ship.dou_index(cuts) == (1, 3)


def test_render_does_not_invent_delivered_states(tmp_path: Path) -> None:
    tracker = """\
| Cut | state |
| --- | ----- |
| C1 | [?] |
| C2 | [ ] |
"""
    root = _plan(tmp_path, tracker)
    block = ship.render_roadmap_from_tracker(
        plan_root=root, roadmap_path=None, stdout=True
    )
    assert "| C1 | [?] |" in block
    assert "| C2 | [ ] |" in block
    assert "| C1 | [x] |" not in block
    assert "| C2 | [x] |" not in block
    assert "**dou-index:** 0/2" in block
    assert "not a delivery certificate" in block.lower()


def test_render_writes_generated_block_and_preserves_history(tmp_path: Path) -> None:
    root = _plan(tmp_path)
    roadmap = tmp_path / "docs" / "ROADMAP_4.2.0.md"
    roadmap.parent.mkdir()
    roadmap.write_text(HISTORY, encoding="utf-8")

    ship.render_roadmap_from_tracker(plan_root=root, roadmap_path=roadmap)
    text = roadmap.read_text(encoding="utf-8")

    assert ship.ROADMAP_BEGIN in text
    assert ship.ROADMAP_END in text
    assert "| W0-a | [?]" in text
    assert "| W1-a | [x]" in text
    assert "| W2-a | [ ]" in text
    assert "Source of truth:" in text
    assert "`[!]`" in text
    assert "Keep this prose." in text
    assert text.index(ship.ROADMAP_BEGIN) < text.index("## Thesis")
    assert text.index("## Cut states (from tracker)") < text.index("## Implement stage")


def test_render_is_idempotent_and_does_not_rewrite_history(tmp_path: Path) -> None:
    root = _plan(tmp_path)
    roadmap = tmp_path / "ROADMAP.md"
    roadmap.write_text(HISTORY, encoding="utf-8")
    ship.render_roadmap_from_tracker(plan_root=root, roadmap_path=roadmap)
    first = roadmap.read_text(encoding="utf-8")
    ship.render_roadmap_from_tracker(plan_root=root, roadmap_path=roadmap)
    second = roadmap.read_text(encoding="utf-8")
    assert first == second
    assert first.count(ship.ROADMAP_BEGIN) == 1
    assert first.count("| W0-a | `[!]`") == 1


def test_display_plan_path_contracts_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    plan = home / ".vibecrafted" / "plans" / "roadmap"
    plan.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert ship.display_plan_path(plan) == "~/.vibecrafted/plans/roadmap"
    assert (
        ship.display_plan_path(plan, original="~/.vibecrafted/plans/roadmap")
        == "~/.vibecrafted/plans/roadmap"
    )


def test_missing_tracker_fails_closed(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="tracker not found"):
        ship.render_roadmap_from_tracker(plan_root=empty, stdout=True)


def test_ship_main_routes_roadmap_render(tmp_path: Path, capsys) -> None:
    root = _plan(tmp_path)
    roadmap = tmp_path / "docs" / "ROADMAP_4.2.0.md"
    rc = ship.main(
        [
            "roadmap",
            "--render",
            "--plan",
            str(root),
            "--roadmap",
            str(roadmap),
        ]
    )
    assert rc == 0
    text = roadmap.read_text(encoding="utf-8")
    assert "| W1-a | [x]" in text
    assert "dou-index:** 1/3" in text
    out = capsys.readouterr()
    assert "1/3" in out.out
    assert "complete" not in out.out.lower()


def test_ship_main_stdout_does_not_write(tmp_path: Path, capsys) -> None:
    root = _plan(tmp_path)
    roadmap = tmp_path / "ROADMAP.md"
    rc = ship.main(
        [
            "roadmap",
            "--render",
            "--plan",
            str(root),
            "--roadmap",
            str(roadmap),
            "--stdout",
        ]
    )
    assert rc == 0
    assert not roadmap.exists()
    assert "W0-a" in capsys.readouterr().out
