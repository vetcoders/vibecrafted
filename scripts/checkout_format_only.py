#!/usr/bin/env python3
"""Preview or restore explicitly selected Prettier-only edits to HEAD."""

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

SUFFIXES = {
    ".md",
    ".yaml",
    ".yml",
    ".json",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".html",
}


def run(*args: str, data: bytes | None = None) -> bytes:
    env = dict(os.environ, GIT_LITERAL_PATHSPECS="1")
    result = subprocess.run(args, input=data, capture_output=True, env=env, check=False)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def formatted(path: str, content: bytes) -> bytes:
    output = run(
        "npx",
        "--no-install",
        "--offline",
        "prettier",
        "--no-config",
        "--no-editorconfig",
        "--stdin-filepath",
        path,
        data=content,
    )
    if Path(path).suffix == ".json":
        # Prettier preserves an object's multiline layout. Ignore only whitespace
        # outside JSON strings after parser validation; retain order and values.
        return re.sub(
            rb'("(?:[^"\\]|\\.)*")|\s+', lambda match: match[1] or b"", output
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="restore selected format-only files in index and worktree",
    )
    parser.add_argument(
        "files", nargs="*", help="explicit paths relative to repository root (no globs)"
    )
    parser.add_argument(
        "--from-env",
        action="store_true",
        help="read the Make target selection from FORMAT_ONLY_FILES",
    )
    args = parser.parse_args()
    try:
        if args.from_env:
            if args.files:
                raise ValueError("choose either --from-env or literal arguments")
            args.files = shlex.split(os.environ.get("FORMAT_ONLY_FILES", ""))
        if not args.files:
            raise ValueError("select at least one file; nothing restored")
        root = Path(run("git", "rev-parse", "--show-toplevel").decode().strip())
        os.chdir(root)
        head = run("git", "rev-parse", "HEAD").decode().strip()
        snapshots = {}
        for name in dict.fromkeys(args.files):
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or path.as_posix() != name:
                raise ValueError(f"{name}: use a literal repository-relative file path")
            if (
                path.is_symlink()
                or (root / path).resolve() != root.resolve() / path
                or not path.is_file()
                or path.suffix not in SUFFIXES
            ):
                raise ValueError(f"{name}: requires a regular Prettier-supported file")
            tree_entry = run("git", "ls-tree", head, "--", name)
            index_entry = run("git", "ls-files", "--stage", "--", name)
            if (
                not tree_entry.startswith(b"100644 blob ")
                or not index_entry.startswith(b"100644 ")
                or b" 0\t" not in index_entry
                or path.stat().st_mode & 0o111
            ):
                raise ValueError(
                    f"{name}: requires an existing non-executable file without mode changes or conflicts"
                )
            original = run("git", "show", f"{head}:{name}")
            staged = run("git", "show", f":{name}")
            current = path.read_bytes()
            if staged not in (original, current):
                raise ValueError(
                    f"{name}: separately staged changes; preserve them before retrying"
                )
            if current != original and formatted(name, current) != formatted(
                name, original
            ):
                raise ValueError(f"{name}: contains content changes; nothing restored")
            snapshots[name] = (current, staged, index_entry, path.stat().st_mode)
        # Validate the whole selection before writing; refuse drift during formatting.
        if run("git", "rev-parse", "HEAD").decode().strip() != head:
            raise ValueError("HEAD changed during preview; retry")
        for name, (current, staged, index_entry, mode) in snapshots.items():
            if (
                Path(name).is_symlink()
                or Path(name).stat().st_mode != mode
                or Path(name).read_bytes() != current
                or run("git", "show", f":{name}") != staged
                or run("git", "ls-files", "--stage", "--", name) != index_entry
            ):
                raise ValueError(f"{name}: changed during preview; retry")
        if args.apply:
            run(
                "git",
                "restore",
                f"--source={head}",
                "--staged",
                "--worktree",
                "--",
                *snapshots,
            )
        for name in snapshots:
            print(
                f"{'Restored' if args.apply else 'Would restore'} format-only: {name}"
            )
        if not args.apply:
            print("Preview only. Repeat with --apply (make: APPLY=1) to restore.")
        return 0
    except (ValueError, OSError) as exc:
        print(f"checkout-format-only: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
