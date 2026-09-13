#!/usr/bin/env python3
"""Shell quality gate: shellcheck (or syntax-only fallback) over tracked shell files.

Runs shellcheck on bash/sh scripts and falls back to `zsh -n` / `<shell> -n`
syntax checks for zsh files (shellcheck cannot parse zsh) or when shellcheck
itself is unavailable on the host.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Dynamic `source` paths are the only repo-wide exclusion: shellcheck runs without
# -x and cannot follow them. Every other finding is fixed or carries a reasoned
# inline directive.
SHELLCHECK_EXCLUDES = ("SC1090", "SC1091")
SHELL_SUFFIXES = {".sh", ".bash", ".zsh"}
SHELL_NAMES = ("zsh", "bash", "sh")


def read_shebang(path: Path) -> str:
    """Return the file's shebang line verbatim, or "" if absent/unreadable."""
    try:
        with path.open("rb") as handle:
            first_line = handle.readline().decode("utf-8", errors="ignore").strip()
    except OSError:
        return ""
    return first_line if first_line.startswith("#!") else ""


def shell_for_path(path: Path) -> str | None:
    """Infer the shell dialect ("zsh"/"bash"/"sh") from shebang, else suffix."""
    shebang = read_shebang(path)
    if shebang:
        shebang_parts = shebang[2:].strip().split()
        if shebang_parts:
            interpreter = Path(shebang_parts[0]).name
            if interpreter == "env" and len(shebang_parts) > 1:
                interpreter = Path(shebang_parts[1]).name
            for shell_name in SHELL_NAMES:
                if interpreter == shell_name:
                    return shell_name

    suffix = path.suffix.lower()
    if suffix == ".zsh":
        return "zsh"
    if suffix in {".sh", ".bash"}:
        return "bash"
    return None


def is_shell_path(path: Path) -> bool:
    """True if `path` has a shell suffix or a recognized shell shebang."""
    return path.suffix.lower() in SHELL_SUFFIXES or shell_for_path(path) is not None


def tracked_shell_files(repo_root: Path = REPO_ROOT) -> list[Path]:
    """List every git-tracked shell file under `repo_root`, sorted."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    )
    files: list[Path] = []
    for raw_path in result.stdout.splitlines():
        if not raw_path:
            continue
        candidate = repo_root / raw_path
        if candidate.is_file() and is_shell_path(candidate):
            files.append(candidate.resolve())
    return sorted(files)


def resolve_shell_files(
    raw_paths: list[str], repo_root: Path = REPO_ROOT
) -> list[Path]:
    """Resolve explicit CLI paths to shell files, or fall back to tracked files.

    Non-shell, non-existent, or duplicate paths are silently dropped.
    """
    if not raw_paths:
        return tracked_shell_files(repo_root)

    files: list[Path] = []
    seen: set[Path] = set()
    for raw_path in raw_paths:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        else:
            candidate = candidate.resolve()

        if not candidate.is_file() or not is_shell_path(candidate) or candidate in seen:
            continue

        files.append(candidate)
        seen.add(candidate)
    return files


def build_shellcheck_command(files: list[Path]) -> list[str]:
    """Build the shellcheck invocation with the repo's excluded rule codes."""
    return [
        "shellcheck",
        "-e",
        ",".join(SHELLCHECK_EXCLUDES),
        *(str(path) for path in files),
    ]


def syntax_check_command(path: Path) -> list[str]:
    """Build the ``<shell> -n`` syntax-check command for `path`'s dialect."""
    shell_name = shell_for_path(path)
    if shell_name == "zsh":
        shell_binary = shutil.which("zsh") or "zsh"
    elif shell_name == "sh":
        shell_binary = shutil.which("sh") or "sh"
    else:
        shell_binary = shutil.which("bash") or "bash"
    return [shell_binary, "-n", str(path)]


def run_shellcheck(files: list[Path]) -> int:
    """Run shellcheck on non-zsh files and the syntax-only fallback on zsh files.

    ShellCheck only parses sh/bash/dash/ksh. Zsh sources get syntax-only
    (`zsh -n`) so operator fragments like config/shell/atuin-up.zsh stay
    gateable without false SC1071 failures.
    """
    zsh_files = [path for path in files if shell_for_path(path) == "zsh"]
    other_files = [path for path in files if path not in zsh_files]
    rc = 0
    if other_files:
        print(f"Running shellcheck on {len(other_files)} shell files...")
        rc = max(
            rc,
            subprocess.run(
                build_shellcheck_command(other_files), check=False
            ).returncode,
        )
    if zsh_files:
        print(f"Running zsh -n on {len(zsh_files)} zsh files...")
        rc = max(rc, run_syntax_fallback(zsh_files))
    return rc


def run_syntax_fallback(files: list[Path]) -> int:
    """Run per-file ``<shell> -n`` syntax checks, skipping shells missing on host.

    Prints failures and a skip summary; returns 1 on any real failure, 0
    otherwise (skips alone do not fail the gate).
    """
    failed = False
    skipped: list[Path] = []

    for path in files:
        command = syntax_check_command(path)
        if shutil.which(command[0]) is None:
            # A host without this shell (ubuntu-latest ships no zsh) cannot
            # syntax-check the file at all; an honest skip beats a crash, and
            # CI hosts are expected to install the shell for real coverage.
            skipped.append(path)
            continue
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode == 0:
            continue
        failed = True
        print(f"[fail] {path.relative_to(REPO_ROOT)}")
        if result.stdout:
            print(result.stdout.rstrip())
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)

    if skipped:
        names = ", ".join(str(p.relative_to(REPO_ROOT)) for p in skipped[:5])
        more = f" (+{len(skipped) - 5} more)" if len(skipped) > 5 else ""
        print(
            f"[skip] {len(skipped)} file(s) unverifiable — interpreter missing"
            f" on this host: {names}{more}",
            file=sys.stderr,
        )

    if failed:
        return 1

    checked = len(files) - len(skipped)
    print(f"Syntax-only shell checks passed for {checked} shell files.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args: optional explicit paths and --require-shellcheck."""
    parser = argparse.ArgumentParser(
        description="Run the 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. shell quality gate across tracked files."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional shell files to lint. Defaults to tracked repo shell files.",
    )
    parser.add_argument(
        "--require-shellcheck",
        action="store_true",
        help="Fail instead of falling back when shellcheck is unavailable.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: run shellcheck (or the syntax-only fallback) and exit."""
    args = parse_args(argv)
    files = resolve_shell_files(args.paths)
    if not files:
        print("No shell files to check.")
        return 0

    if shutil.which("shellcheck"):
        return run_shellcheck(files)

    print("shellcheck not found; running syntax-only shell checks with local shells.")

    if args.require_shellcheck or os.environ.get("CI"):
        print(
            "shellcheck is required for this quality gate but is not installed.",
            file=sys.stderr,
        )
        return 127

    return run_syntax_fallback(files)


if __name__ == "__main__":
    raise SystemExit(main())
