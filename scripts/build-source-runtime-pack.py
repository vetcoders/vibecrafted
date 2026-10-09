"""Build the canonical Runtime Pack from this checkout and pinned donors."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PINS = ROOT / "config/source-components.json"
COMPONENTS = {
    "vc-frame": (
        "VIBECRAFTED_FRAME_REPO",
        "VIBECRAFTED_FRAME_REVISION",
        "Cargo.toml",
        "workspace",
    ),
    "vc-terminal": (
        "VIBECRAFTED_TERMINAL_REPO",
        "VIBECRAFTED_TERMINAL_REVISION",
        "alacritty/Cargo.toml",
        "package",
    ),
}


def git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(cwd), *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


def load_pins(path: Path) -> dict[str, dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "vibecrafted.source-components.v1":
        raise ValueError("unsupported source component manifest")
    pins = data.get("components")
    if not isinstance(pins, dict) or set(pins) != set(COMPONENTS):
        raise ValueError("source component manifest must name Frame and Terminal")
    for name, pin in pins.items():
        if not isinstance(pin, dict) or set(pin) != {
            "repository",
            "version",
            "revision",
        }:
            raise ValueError(f"invalid {name} component record")
        if not isinstance(pin["revision"], str) or not re.fullmatch(
            r"[0-9a-f]{40}", pin["revision"]
        ):
            raise ValueError(f"{name} requires a full lowercase commit SHA")
        if not isinstance(pin["version"], str) or not pin["version"]:
            raise ValueError(f"{name} requires a version")
        if not isinstance(pin["repository"], str) or not pin["repository"]:
            raise ValueError(f"{name} requires a repository")
    return pins


def primary_checkout() -> Path | None:
    try:
        listing = git("worktree", "list", "--porcelain", cwd=ROOT)
    except subprocess.CalledProcessError:
        return None
    first = listing.splitlines()[0] if listing else ""
    return (
        Path(first.removeprefix("worktree ")) if first.startswith("worktree ") else None
    )


def has_commit(repo: Path, revision: str) -> bool:
    try:
        return (
            git("rev-parse", "--verify", f"{revision}^{{commit}}", cwd=repo) == revision
        )
    except (OSError, subprocess.CalledProcessError):
        return False


def select_repository(
    name: str, pin: dict[str, str], scratch: Path, primary: Path | None
) -> Path:
    env_name = COMPONENTS[name][0]
    override = os.environ.get(env_name)
    if override:
        candidate = Path(override).expanduser().resolve()
        if not has_commit(candidate, pin["revision"]):
            raise ValueError(f"{env_name} lacks pinned commit {pin['revision']}")
        return candidate
    candidates = [ROOT.parent / name]
    if primary is not None:
        candidates.append(primary.parent / name)
    for candidate in candidates:
        if has_commit(candidate, pin["revision"]):
            return candidate.resolve()
    destination = scratch / name
    destination.mkdir()
    subprocess.run(["git", "init", "--quiet", str(destination)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "fetch",
            "--quiet",
            "--depth",
            "1",
            pin["repository"],
            pin["revision"],
        ],
        check=True,
    )
    if not has_commit(destination, pin["revision"]):
        raise ValueError(f"fetch did not supply pinned {name} commit {pin['revision']}")
    return destination


def verify_version(name: str, pin: dict[str, str], repo: Path) -> None:
    _, _, manifest_path, table = COMPONENTS[name]
    blob = subprocess.check_output(
        ["git", "-C", str(repo), "show", f"{pin['revision']}:{manifest_path}"]
    )
    version = tomllib.loads(blob.decode("utf-8"))[table][
        "package" if table == "workspace" else "version"
    ]
    if table == "workspace":
        version = version["version"]
    if version != pin["version"]:
        raise ValueError(
            f"{name} version at pinned commit is {version}, manifest says {pin['version']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify pins and donor availability without building",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_PINS)
    args = parser.parse_args()
    pins = load_pins(args.manifest)
    with tempfile.TemporaryDirectory(prefix="vibecrafted-source-donors-") as temp:
        scratch = Path(temp)
        primary = primary_checkout()
        repositories = {
            name: select_repository(name, pins[name], scratch, primary)
            for name in COMPONENTS
        }
        for name, repository in repositories.items():
            verify_version(name, pins[name], repository)
            print(
                f"{name}: {pins[name]['version']} {pins[name]['revision']} ({repository})",
                flush=True,
            )
        if args.check:
            return 0
        environment = os.environ.copy()
        for name, repository in repositories.items():
            repo_var, revision_var, _, _ = COMPONENTS[name]
            environment[repo_var] = str(repository)
            environment[revision_var] = pins[name]["revision"]
        subprocess.run(
            [
                "make",
                "--no-print-directory",
                "runtime-pack",
                "RELEASE_FLAGS=--snapshot-donors",
            ],
            cwd=ROOT,
            env=environment,
            check=True,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"source Runtime Pack build failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
