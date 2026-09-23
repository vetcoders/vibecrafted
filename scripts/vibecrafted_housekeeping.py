#!/usr/bin/env python3
"""Receipt-driven cleanup planner for the Vibecrafted state home.

The default mode is read-only. Destructive execution requires both --execute
and an exact confirmation token. Durable receipts live below store/, which is
never a cleanup candidate.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

CONFIRM_TOKEN = "DELETE-REGENERABLE-VIBECRAFTED-STATE"
TRANSIENT_ROOTS = ("tmp", "logs", "install-transactions", "recovery")
BUILD_CACHE_NAMES = frozenset(
    {"target", "node_modules", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
)
NEVER_DESCEND = frozenset({".git", "store", "secrets"})


@dataclass(frozen=True)
class Candidate:
    category: str
    path: str
    bytes: int
    newest_mtime: float
    age_days: float
    eligible: bool
    reason: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_home(raw: str | None) -> Path:
    value = (
        raw or os.environ.get("VIBECRAFTED_HOME") or str(Path.home() / ".vibecrafted")
    )
    root = Path(value).expanduser().resolve()
    forbidden = {Path("/").resolve(), Path.home().resolve()}
    if root in forbidden:
        raise ValueError(f"refusing unsafe state home: {root}")
    if root.exists() and not root.is_dir():
        raise ValueError(f"state home is not a directory: {root}")
    return root


def contained(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def tree_stats(path: Path) -> tuple[int, float]:
    if path.is_symlink():
        stat = path.lstat()
        return 0, stat.st_mtime
    if path.is_file():
        stat = path.stat()
        return stat.st_size, stat.st_mtime
    total = 0
    newest = path.stat().st_mtime
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = [name for name in dirnames if not (current / name).is_symlink()]
        try:
            newest = max(newest, current.stat().st_mtime)
        except OSError:
            pass
        for name in filenames:
            child = current / name
            try:
                stat = child.lstat()
            except OSError:
                continue
            newest = max(newest, stat.st_mtime)
            if not child.is_symlink():
                total += stat.st_size
    return total, newest


def live_cwds() -> tuple[Path, ...]:
    try:
        result = subprocess.run(
            ["/usr/sbin/lsof", "-n", "-F", "n", "-d", "cwd"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        if line.startswith("n/"):
            try:
                path = Path(line[1:]).resolve()
            except OSError:
                continue
            if path not in {Path("/"), Path.home().resolve()}:
                paths.append(path)
    return tuple(dict.fromkeys(paths))


def overlaps_live_path(candidate: Path, cwds: Iterable[Path]) -> bool:
    resolved = candidate.resolve()
    for cwd in cwds:
        if resolved == cwd or resolved in cwd.parents or cwd in resolved.parents:
            return True
    return False


def make_candidate(
    *,
    category: str,
    path: Path,
    now: float,
    retention_days: int,
    cwds: tuple[Path, ...],
) -> Candidate:
    size, newest = tree_stats(path)
    age = max(0.0, (now - newest) / 86_400)
    if path.is_symlink():
        eligible, reason = False, "symlink-refused"
    elif age < retention_days:
        eligible, reason = False, f"newer-than-{retention_days}d"
    elif overlaps_live_path(path, cwds):
        eligible, reason = False, "overlaps-live-process-cwd"
    else:
        eligible, reason = True, "old-regenerable-state"
    return Candidate(category, str(path), size, newest, round(age, 3), eligible, reason)


def transient_candidates(
    root: Path, *, now: float, retention_days: int, cwds: tuple[Path, ...]
) -> list[Candidate]:
    found: list[Candidate] = []
    for name in TRANSIENT_ROOTS:
        parent = root / name
        if not parent.is_dir() or parent.is_symlink():
            continue
        for child in sorted(parent.iterdir()):
            found.append(
                make_candidate(
                    category="transient",
                    path=child,
                    now=now,
                    retention_days=retention_days,
                    cwds=cwds,
                )
            )
    return found


def build_cache_candidates(
    root: Path, *, now: float, retention_days: int, cwds: tuple[Path, ...]
) -> list[Candidate]:
    worktrees = root / "worktrees"
    if not worktrees.is_dir() or worktrees.is_symlink():
        return []
    found: list[Candidate] = []
    for dirpath, dirnames, _filenames in os.walk(worktrees, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = [
            name
            for name in dirnames
            if name not in NEVER_DESCEND and not (current / name).is_symlink()
        ]
        selected = [name for name in dirnames if name in BUILD_CACHE_NAMES]
        for name in selected:
            path = current / name
            found.append(
                make_candidate(
                    category="build-cache",
                    path=path,
                    now=now,
                    retention_days=retention_days,
                    cwds=cwds,
                )
            )
        dirnames[:] = [name for name in dirnames if name not in BUILD_CACHE_NAMES]
    return found


def classify_top_level(root: Path) -> dict[str, list[str]]:
    core = Path(__file__).resolve().parents[1] / "vibecrafted-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from vibecrafted_core.runtime_paths import classify_vibecrafted_home_child

    classes: dict[str, list[str]] = {
        "runtime-state": [],
        "founder-data": [],
        "unknown": [],
    }
    if not root.exists():
        return classes
    for child in sorted(root.iterdir()):
        classes[classify_vibecrafted_home_child(child)].append(child.name)
    return classes


def build_plan(
    root: Path, categories: set[str], retention_days: int
) -> dict[str, object]:
    now = utc_now()
    timestamp = now.timestamp()
    cwds = live_cwds()
    candidates: list[Candidate] = []
    if "transient" in categories:
        candidates.extend(
            transient_candidates(
                root, now=timestamp, retention_days=retention_days, cwds=cwds
            )
        )
    if "build-cache" in categories:
        candidates.extend(
            build_cache_candidates(
                root, now=timestamp, retention_days=retention_days, cwds=cwds
            )
        )
    candidates.sort(key=lambda item: (-item.bytes, item.path))
    eligible = [item for item in candidates if item.eligible]
    return {
        "schema": "vibecrafted.housekeeping.plan.v1",
        "created_at": now.isoformat(),
        "home": str(root),
        "mode": "plan",
        "retention_days": retention_days,
        "categories": sorted(categories),
        "live_cwds_observed": len(cwds),
        "top_level": classify_top_level(root),
        "summary": {
            "candidates": len(candidates),
            "eligible": len(eligible),
            "eligible_bytes": sum(item.bytes for item in eligible),
            "protected": len(candidates) - len(eligible),
        },
        "items": [asdict(item) for item in candidates],
    }


def receipt_path(root: Path, kind: str) -> Path:
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    directory = root / "store" / "housekeeping" / kind
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{stamp}-{os.getpid()}.json"


def write_receipt(root: Path, kind: str, payload: dict[str, object]) -> Path:
    path = receipt_path(root, kind)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def delete_candidate(root: Path, raw: str) -> tuple[bool, str]:
    path = Path(raw)
    if not contained(root, path):
        return False, "escaped-home"
    if path.is_symlink():
        return False, "symlink-refused"
    if not path.exists():
        return True, "already-absent"
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True, "deleted"


def execute_plan(root: Path, plan: dict[str, object]) -> dict[str, object]:
    outcomes: list[dict[str, object]] = []
    for item in plan["items"]:  # type: ignore[index]
        if not item["eligible"]:
            continue
        ok, result = delete_candidate(root, str(item["path"]))
        outcomes.append(
            {"path": item["path"], "bytes": item["bytes"], "ok": ok, "result": result}
        )
    return {
        "schema": "vibecrafted.housekeeping.execution.v1",
        "created_at": utc_now().isoformat(),
        "home": str(root),
        "plan_created_at": plan["created_at"],
        "outcomes": outcomes,
        "deleted_bytes": sum(int(item["bytes"]) for item in outcomes if item["ok"]),
        "failures": sum(1 for item in outcomes if not item["ok"]),
    }


def launch_background(argv: Sequence[str], root: Path) -> int:
    forwarded = [arg for arg in argv if arg != "--background"]
    log_dir = root / "store" / "housekeeping" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"{stamp}.log"
    with log_path.open("ab", buffering=0) as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), *forwarded],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({"pid": process.pid, "log": str(log_path), "home": str(root)}))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="vibecrafted-housekeeping",
        description="Plan or execute receipt-driven cleanup below VIBECRAFTED_HOME.",
    )
    result.add_argument(
        "--home", help="state home (default: VIBECRAFTED_HOME or ~/.vibecrafted)"
    )
    result.add_argument(
        "--category",
        action="append",
        choices=("all", "transient", "build-cache"),
        default=[],
        help="repeatable; default: all",
    )
    result.add_argument("--retention-days", type=int, default=7)
    result.add_argument("--execute", action="store_true", help="delete eligible paths")
    result.add_argument("--confirm", help=f"required with --execute: {CONFIRM_TOKEN}")
    result.add_argument(
        "--background", action="store_true", help="detach and write output below store/"
    )
    result.add_argument(
        "--json", action="store_true", help="print the complete receipt"
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(argv if argv is not None else sys.argv[1:])
    args = parser().parse_args(raw_argv)
    if args.retention_days < 0:
        raise SystemExit("--retention-days must be non-negative")
    root = resolve_home(args.home)
    root.mkdir(parents=True, exist_ok=True)
    if args.background:
        return launch_background(raw_argv, root)
    if args.execute and args.confirm != CONFIRM_TOKEN:
        raise SystemExit(f"--execute requires --confirm {CONFIRM_TOKEN}")
    selected = set(args.category or ["all"])
    categories = {"transient", "build-cache"} if "all" in selected else selected
    plan = build_plan(root, categories, args.retention_days)
    plan_path = write_receipt(root, "plans", plan)
    payload: dict[str, object] = {"plan_receipt": str(plan_path), **plan}
    if args.execute:
        execution = execute_plan(root, plan)
        execution_path = write_receipt(root, "executions", execution)
        payload["execution_receipt"] = str(execution_path)
        payload["execution"] = execution
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        summary = plan["summary"]
        print(
            f"housekeeping: {summary['eligible']} eligible / {summary['candidates']} scanned; "
            f"{summary['eligible_bytes']} bytes; receipt={plan_path}"
        )
        if args.execute:
            print(f"execution receipt={payload['execution_receipt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
