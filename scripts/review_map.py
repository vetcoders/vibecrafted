#!/usr/bin/env python3
"""Render the working tree as a review map, not a flat diff list.

Groups changed paths into the repo's structural areas, separates
formatting-only edits (identical once whitespace is ignored) from changes
that carry real content, and puts deletions, renames, and semantic diffs
up front so they are never swept into the "just formatting" bag.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

AREA_RULES: tuple[tuple[str, str], ...] = (
    ("docs/public/", "docs-public"),
    ("docs/runtime/", "docs-runtime"),
    ("docs/design/", "docs-design"),
    ("docs/adr/", "docs-adr"),
    ("docs/operator/", "docs-operator"),
    ("docs/installer/", "docs-installer"),
    ("docs/pl/", "docs-pl"),
    ("docs/", "docs-general"),
    ("vibecrafted-core/vibecrafted_core/skills/pl/", "skills-pl"),
    ("vibecrafted-core/vibecrafted_core/skills/", "skills"),
    ("vibecrafted-core/", "core-python"),
    ("vibecrafted-server/", "server-rust"),
    ("vibecrafted-app/", "app-rust"),
    ("vibecrafted-mcp/", "mcp-python"),
    ("vibecrafted-acp/", "acp-python"),
    ("vibecrafted-vm/", "vm"),
    ("scripts/", "scripts"),
    ("tests/", "tests"),
    ("bin/", "launchers"),
    ("config/", "config"),
    (".github/", "ci"),
    ("packaging/", "packaging"),
    ("plugins/", "plugins"),
    ("templates/", "templates"),
    ("examples/", "examples"),
    ("assets/", "assets"),
)

KIND_LABELS = {
    "modified": "M",
    "added": "A",
    "deleted": "D",
    "renamed": "R",
}


@dataclass
class Change:
    path: str
    kind: str
    added: int = 0
    deleted: int = 0
    formatting_only: bool = False
    orig_path: str | None = None


@dataclass
class ReviewMap:
    repo: Path
    base: str
    base_rev: str
    changes: list[Change] = field(default_factory=list)

    @property
    def review_first(self) -> list[Change]:
        return [
            c
            for c in self.changes
            if not c.formatting_only or c.kind in ("deleted", "renamed")
        ]

    @property
    def formatting_only(self) -> list[Change]:
        return [c for c in self.changes if c.formatting_only and c.kind != "renamed"]

    def areas(self) -> dict[str, list[Change]]:
        grouped: dict[str, list[Change]] = {}
        for change in self.changes:
            grouped.setdefault(area_for(change.path), []).append(change)
        return dict(sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])))


def area_for(path: str) -> str:
    for prefix, area in AREA_RULES:
        if path.startswith(prefix):
            return area
    return path.split("/", 1)[0] if "/" in path else "root"


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


def _porcelain(repo: Path) -> dict[str, tuple[str, str | None]]:
    entries = _git(
        repo, "status", "--porcelain=v1", "--untracked-files=all", "-z"
    ).split("\0")
    statuses: dict[str, tuple[str, str | None]] = {}
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        xy, path = entry[:2], entry[3:]
        if "R" in xy or "C" in xy:
            orig = entries[index]
            index += 1
            statuses[path] = (xy, orig)
        else:
            statuses[path] = (xy, None)
    return statuses


def _numstat(repo: Path, base: str) -> dict[str, tuple[int | None, int | None]]:
    out = _git(repo, "diff", base, "--numstat", "--no-renames", "-z")
    stats: dict[str, tuple[int | None, int | None]] = {}
    for record in out.split("\0"):
        if not record:
            continue
        added, deleted, path = record.split("\t", 2)
        stats[path] = (
            int(added) if added != "-" else None,
            int(deleted) if deleted != "-" else None,
        )
    return stats


def _normalized_blob(repo: Path, base: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{base}:{path}"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        text = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return "".join(text.split())


def _normalized_worktree(repo: Path, path: str) -> str | None:
    try:
        text = (repo / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return "".join(text.split())


def _is_formatting_only(
    repo: Path, base: str, path: str, orig_path: str | None
) -> bool:
    old = _normalized_blob(repo, base, orig_path or path)
    new = _normalized_worktree(repo, path)
    if old is None or new is None:
        return False
    return old == new


def build_review_map(repo: Path, base: str = "HEAD") -> ReviewMap:
    repo = repo.resolve()
    base_rev = _git(repo, "rev-parse", "--short", base).strip()
    statuses = _porcelain(repo)
    stats = _numstat(repo, base)
    rename_origins = {orig for _, orig in statuses.values() if orig}

    changes: list[Change] = []
    for path in sorted((set(statuses) | set(stats)) - rename_origins):
        xy, orig = statuses.get(path, ("", None))
        added, deleted = stats.get(path, (0, 0))
        if xy == "??" or "A" in xy:
            kind = "added"
        elif "R" in xy or "C" in xy:
            kind = "renamed"
        elif "D" in xy:
            kind = "deleted"
        else:
            kind = "modified"

        change = Change(
            path=path,
            kind=kind,
            added=added or 0,
            deleted=deleted or 0,
            orig_path=orig,
        )
        if kind == "added" and path not in stats:
            worktree_file = repo / path
            if worktree_file.is_file():
                change.added = len(worktree_file.read_bytes().splitlines())
        if kind in ("modified", "renamed"):
            change.formatting_only = _is_formatting_only(repo, base, path, orig)
        changes.append(change)

    return ReviewMap(repo=repo, base=base, base_rev=base_rev, changes=changes)


def _describe(change: Change) -> str:
    label = KIND_LABELS[change.kind]
    if change.kind == "renamed":
        detail = f"{change.orig_path} → {change.path}"
        if change.formatting_only:
            return f"{label} {detail} (content unchanged)"
        return f"{label} {detail} (+{change.added} -{change.deleted})"
    if change.kind == "deleted":
        return f"{label} {change.path} (deleted)"
    return f"{label} {change.path} (+{change.added} -{change.deleted})"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def render_markdown(review: ReviewMap, max_files: int = 50) -> str:
    lines = [
        f"# Review map — worktree vs {review.base} ({review.base_rev})",
        "",
        (
            f"{_plural(len(review.changes), 'path')} · {len(review.formatting_only)} formatting-only"
            f" · {len(review.review_first)} need real review"
        ),
        "",
    ]
    if not review.changes:
        lines.append(f"Tree is clean against {review.base} — nothing to map.")
        return "\n".join(lines) + "\n"

    lines.append("## Not just formatting — review these first")
    lines.append("")
    if review.review_first:
        for change in review.review_first:
            lines.append(f"- {_describe(change)}")
    else:
        lines.append("- none — every change is whitespace-only")
    lines.append("")

    lines.append("## Map by area")
    for area, changes in review.areas().items():
        formatting = [c for c in changes if c.formatting_only]
        semantic = [c for c in changes if not c.formatting_only]
        lines.append("")
        lines.append(
            f"### {area} — {_plural(len(changes), 'path')}"
            f" ({len(formatting)} formatting-only, {len(semantic)} semantic)"
        )
        lines.append("")
        shown = changes if max_files <= 0 else changes[:max_files]
        for change in shown:
            suffix = " (formatting-only)" if change.formatting_only else ""
            if change.kind in ("modified", "added") and not change.formatting_only:
                suffix = f" (+{change.added} -{change.deleted})"
            lines.append(f"- {KIND_LABELS[change.kind]} {change.path}{suffix}")
        overflow = len(changes) - len(shown)
        if overflow > 0:
            lines.append(f"- … {overflow} more (use --max-files 0 to list all)")
    return "\n".join(lines) + "\n"


def render_json(review: ReviewMap) -> str:
    payload = {
        "base": review.base,
        "base_rev": review.base_rev,
        "total": len(review.changes),
        "formatting_only": len(review.formatting_only),
        "review_first": [_describe(c) for c in review.review_first],
        "areas": {
            area: {
                "total": len(changes),
                "formatting_only": sum(1 for c in changes if c.formatting_only),
                "semantic": sum(1 for c in changes if not c.formatting_only),
                "paths": [c.path for c in changes],
            }
            for area, changes in review.areas().items()
        },
    }
    return json.dumps(payload, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="repository to map (default: current directory)",
    )
    parser.add_argument(
        "--base",
        default="HEAD",
        help="git ref the worktree is compared against (default: HEAD)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit JSON instead of Markdown"
    )
    parser.add_argument(
        "--output", type=Path, help="write the map to a file instead of stdout"
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=50,
        help="cap files listed per area; 0 lists everything (default: 50)",
    )
    args = parser.parse_args(argv)

    try:
        review = build_review_map(args.repo, args.base)
    except RuntimeError as error:
        print(f"review-map: {error}", file=sys.stderr)
        return 1

    rendered = (
        render_json(review) if args.json else render_markdown(review, args.max_files)
    )
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
