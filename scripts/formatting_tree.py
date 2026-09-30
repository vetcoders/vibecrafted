#!/usr/bin/env python3
"""Render the repo's formatting tree as a deliberate map of Vibecrafted.

A stray formatter pass once rewrote ~380 files and accidentally revealed
a map of this repo: files nobody looks at day to day suddenly surfaced in
`git status` (Founder intent s01-037: "właśnie tak! i chcę to wykorzystać.
Repo vibecrafted"). This script makes that accident repeatable and
harmless: it enumerates the tracked files each formatter lane of the
repo's own gates would touch — the same case arms as
scripts/hooks/pre-push — and renders them as a browsable tree with line
counts, so the surfaced map is available without re-running the accident.

Lanes mirror scripts/hooks/pre-push exactly:

- prettier:   *.md *.yaml *.yml
- ruff:       *.py
- shellcheck: *.sh *.bash *.zsh

Tracked files outside every lane are reported as their own surface: the
accident could never have revealed them, and that blind spot is map data
too. This is a map, not a gate — it always exits 0.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Lane order is display order. Suffixes are disjoint, so a file joins
# exactly one lane; keep the arms in sync with scripts/hooks/pre-push.
LANES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("prettier", (".md", ".yaml", ".yml")),
    ("ruff", (".py",)),
    ("shellcheck", (".sh", ".bash", ".zsh")),
)

OUTSIDE = "outside"
LANE_MARKS = {"prettier": "P", "ruff": "R", "shellcheck": "S", OUTSIDE: "·"}


@dataclass
class TreeFile:
    path: str
    lane: str
    lines: int | None


@dataclass
class TreeDir:
    name: str
    dirs: dict[str, TreeDir] = field(default_factory=dict)
    files: list[TreeFile] = field(default_factory=list)
    files_count: int = 0
    lines: int = 0
    lane_counts: dict[str, int] = field(default_factory=dict)

    def add(self, parts: list[str], entry: TreeFile) -> None:
        self.files_count += 1
        if entry.lines:
            self.lines += entry.lines
        self.lane_counts[entry.lane] = self.lane_counts.get(entry.lane, 0) + 1
        if parts:
            child = self.dirs.setdefault(parts[0], TreeDir(parts[0]))
            child.add(parts[1:], entry)
        else:
            self.files.append(entry)


def lane_for(path: str) -> str:
    for lane, suffixes in LANES:
        if path.endswith(suffixes):
            return lane
    return OUTSIDE


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _line_count(path: Path) -> int | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def collect(repo: Path) -> list[TreeFile]:
    names = _git(repo, "ls-files", "-z").split("\0")
    entries = []
    for name in names:
        if not name:
            continue
        entries.append(
            TreeFile(path=name, lane=lane_for(name), lines=_line_count(repo / name))
        )
    return entries


def build_tree(entries: list[TreeFile]) -> TreeDir:
    root = TreeDir("")
    for entry in entries:
        root.add(entry.path.split("/")[:-1], entry)
    return root


def lane_totals(entries: list[TreeFile]) -> dict[str, int]:
    totals: dict[str, int] = {lane: 0 for lane, _ in LANES}
    totals[OUTSIDE] = 0
    for entry in entries:
        totals[entry.lane] += 1
    return totals


def top_files(entries: list[TreeFile], limit: int) -> list[TreeFile]:
    counted = [e for e in entries if e.lines]
    return sorted(counted, key=lambda e: (-(e.lines or 0), e.path))[:limit]


def area_rows(root: TreeDir) -> list[dict[str, object]]:
    rows = []
    for name, child in root.dirs.items():
        rows.append(
            {
                "area": f"{name}/",
                "files": child.files_count,
                "lines": child.lines,
                "lanes": child.lane_counts,
            }
        )
    if root.files:
        rows.append(
            {
                "area": "(root)",
                "files": len(root.files),
                "lines": sum(f.lines or 0 for f in root.files),
                "lanes": lane_totals(root.files),
            }
        )
    rows.sort(key=lambda row: (-int(row["lines"]), str(row["area"])))
    return rows


def _lane_badges(lane_counts: dict[str, int]) -> str:
    parts = [
        f"{LANE_MARKS[lane]}:{lane_counts[lane]}"
        for lane, _ in LANES
        if lane_counts.get(lane)
    ]
    if lane_counts.get(OUTSIDE):
        parts.append(f"{LANE_MARKS[OUTSIDE]}:{lane_counts[OUTSIDE]}")
    return " ".join(parts)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def render_tree_lines(
    node: TreeDir, prefix: str, depth: int, max_depth: int | None
) -> list[str]:
    lines: list[str] = []
    if max_depth is not None and depth >= max_depth:
        if node.files_count:
            lines.append(f"{prefix}…")
        return lines
    rows: list[tuple[str, TreeDir | None]] = [
        (
            (
                f"{child.name}/  ({_count(child.files_count, 'file')}, "
                f"{_count(child.lines, 'line')}, "
                f"{_lane_badges(child.lane_counts)})"
            ),
            child,
        )
        for child in sorted(node.dirs.values(), key=lambda d: d.name)
    ]
    for entry in sorted(node.files, key=lambda f: f.path):
        mark = LANE_MARKS[entry.lane]
        size = _count(entry.lines, "line") if entry.lines is not None else "binary"
        rows.append((f"[{mark}] {entry.path.rsplit('/', 1)[-1]}  ({size})", None))
    for index, (header, child) in enumerate(rows):
        last = index == len(rows) - 1
        lines.append(f"{prefix}{'└── ' if last else '├── '}{header}")
        if child is not None:
            extension = "    " if last else "│   "
            lines.extend(
                render_tree_lines(child, prefix + extension, depth + 1, max_depth)
            )
    return lines


def render_text(
    root: TreeDir, entries: list[TreeFile], args: argparse.Namespace
) -> str:
    totals = lane_totals(entries)
    total_lines = sum(e.lines or 0 for e in entries)
    lane_summary = " · ".join(
        f"{lane} {totals[lane]}" for lane, _ in LANES if totals[lane]
    )
    out = [
        "# Vibecrafted formatting tree",
        "",
        (
            "The accidental formatter pass made deliberate: every tracked file a "
            "repo gate would reformat, mapped without touching the tree."
        ),
        "",
        (
            f"{len(entries)} tracked files · {total_lines} lines · lanes: "
            f"{lane_summary} · outside lanes {totals[OUTSIDE]}"
        ),
        (
            f"marks: P prettier · R ruff · S shellcheck · {LANE_MARKS[OUTSIDE]} "
            "outside the formatting tree"
        ),
        "",
        "## Areas",
        "",
        "| area | files | lines | lanes |",
        "| --- | ---: | ---: | --- |",
    ]
    for row in area_rows(root):
        out.append(
            f"| {row['area']} | {row['files']} | {row['lines']} | "
            f"{_lane_badges(row['lanes'])} |"
        )
    if args.top:
        out.extend(["", f"## Largest files (top {args.top})", ""])
        for entry in top_files(entries, args.top):
            out.append(
                f"- [{LANE_MARKS[entry.lane]}] {entry.path} — {entry.lines} lines"
            )
    if not args.areas_only:
        out.extend(["", "## Tree", "", "```", "."])
        out.extend(render_tree_lines(root, "", 0, args.max_depth))
        out.append("```")
    return "\n".join(out) + "\n"


def _dir_payload(node: TreeDir, max_depth: int | None, depth: int = 0) -> dict:
    payload: dict[str, object] = {
        "files": node.files_count,
        "lines": node.lines,
        "lanes": node.lane_counts,
    }
    if max_depth is None or depth < max_depth:
        payload["dirs"] = {
            name: _dir_payload(child, max_depth, depth + 1)
            for name, child in sorted(node.dirs.items())
        }
        payload["entries"] = [
            {"path": f.path, "lane": f.lane, "lines": f.lines}
            for f in sorted(node.files, key=lambda f: f.path)
        ]
    return payload


def render_json(
    root: TreeDir, entries: list[TreeFile], args: argparse.Namespace
) -> str:
    payload = {
        "repo": str(args.repo),
        "total_files": len(entries),
        "total_lines": sum(e.lines or 0 for e in entries),
        "lanes": lane_totals(entries),
        "lane_marks": LANE_MARKS,
        "areas": area_rows(root),
        "top": [
            {"path": e.path, "lane": e.lane, "lines": e.lines}
            for e in top_files(entries, args.top)
        ]
        if args.top
        else [],
        "tree": None if args.areas_only else _dir_payload(root, args.max_depth),
    }
    return json.dumps(payload, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render the repo's formatting tree as a deliberate map."
    )
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--max-depth",
        type=int,
        default=None,
        help="collapse directories deeper than N levels",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=0,
        metavar="N",
        help="list the N largest files by line count",
    )
    parser.add_argument(
        "--areas-only",
        action="store_true",
        help="print only the area rollup, skip the tree",
    )
    args = parser.parse_args(argv)
    entries = collect(args.repo)
    root = build_tree(entries)
    if args.json:
        sys.stdout.write(render_json(root, entries, args))
    else:
        sys.stdout.write(render_text(root, entries, args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
