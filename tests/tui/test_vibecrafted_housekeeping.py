from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "vibecrafted_housekeeping.py"
SPEC = importlib.util.spec_from_file_location("vibecrafted_housekeeping", SCRIPT)
assert SPEC and SPEC.loader
housekeeping = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = housekeeping
SPEC.loader.exec_module(housekeeping)


def age(path: Path, days: int) -> None:
    stamp = time.time() - days * 86_400
    os.utime(path, (stamp, stamp))


def test_plan_preserves_store_unknown_and_recent_state(tmp_path: Path) -> None:
    home = tmp_path / ".vibecrafted"
    old = home / "tmp" / "old"
    recent = home / "logs" / "recent.log"
    store = home / "store" / "keep.txt"
    unknown = home / "mystery" / "keep.txt"
    for path in (old / "payload", recent, store, unknown):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    age(old / "payload", 10)
    age(old, 10)

    plan = housekeeping.build_plan(home, {"transient"}, retention_days=7)

    items = {Path(item["path"]).name: item for item in plan["items"]}
    assert items["old"]["eligible"] is True
    assert items["recent.log"]["eligible"] is False
    assert plan["top_level"]["founder-data"] == ["store"]
    assert plan["top_level"]["unknown"] == ["mystery"]


def test_execute_requires_exact_confirmation(tmp_path: Path) -> None:
    try:
        housekeeping.main(["--home", str(tmp_path / ".vibecrafted"), "--execute"])
    except SystemExit as exc:
        assert housekeeping.CONFIRM_TOKEN in str(exc)
    else:
        raise AssertionError("unsafe execution was accepted")


def test_execute_deletes_only_eligible_and_writes_receipts(tmp_path: Path) -> None:
    home = tmp_path / ".vibecrafted"
    old = home / "tmp" / "old"
    keep = home / "store" / "keep.txt"
    old.mkdir(parents=True)
    (old / "payload").write_bytes(b"payload")
    keep.parent.mkdir(parents=True)
    keep.write_text("durable", encoding="utf-8")
    age(old / "payload", 10)
    age(old, 10)

    assert (
        housekeeping.main(
            [
                "--home",
                str(home),
                "--retention-days",
                "7",
                "--execute",
                "--confirm",
                housekeeping.CONFIRM_TOKEN,
            ]
        )
        == 0
    )

    assert not old.exists()
    assert keep.read_text(encoding="utf-8") == "durable"
    plans = list((home / "store" / "housekeeping" / "plans").glob("*.json"))
    executions = list((home / "store" / "housekeeping" / "executions").glob("*.json"))
    assert len(plans) == len(executions) == 1
    assert json.loads(executions[0].read_text())["failures"] == 0


def test_build_cache_under_live_cwd_is_protected(tmp_path: Path) -> None:
    home = tmp_path / ".vibecrafted"
    worktree = home / "worktrees" / "repo"
    target = worktree / "target"
    target.mkdir(parents=True)
    (target / "artifact").write_text("x", encoding="utf-8")
    age(target / "artifact", 10)
    age(target, 10)

    item = housekeeping.make_candidate(
        category="build-cache",
        path=target,
        now=time.time(),
        retention_days=7,
        cwds=(worktree,),
    )

    assert item.eligible is False
    assert item.reason == "overlaps-live-process-cwd"
