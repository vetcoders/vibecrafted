"""Build the canonical Runtime Pack from this checkout and pinned donors."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

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


def repository_environment() -> dict[str, str]:
    """Keep caller settings while isolating Git from a foreign hook's repository."""
    return {
        key: value
        for key, value in os.environ.items()
        if key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}
    }


def git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(
        ["git", "--no-replace-objects", "-C", str(cwd), *args],
        text=True,
        stderr=subprocess.DEVNULL,
        env=repository_environment(),
    ).strip()


def load_pins(path: Path) -> dict[str, dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("schema") != "vibecrafted.source-components.v1"
    ):
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
    subprocess.run(
        ["git", "init", "--quiet", str(destination)],
        env=repository_environment(),
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "fetch",
            "--quiet",
            "--depth",
            "1",
            "--",
            pin["repository"],
            pin["revision"],
        ],
        env=repository_environment(),
        check=True,
    )
    if not has_commit(destination, pin["revision"]):
        raise ValueError(f"fetch did not supply pinned {name} commit {pin['revision']}")
    return destination


def verify_version(
    name: str, pin: dict[str, str], repo: Path, *, exported: bool = False
) -> None:
    _, _, manifest_path, table = COMPONENTS[name]
    blob = (
        (repo / manifest_path).read_bytes()
        if exported
        else subprocess.check_output(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(repo),
                "show",
                f"{pin['revision']}:{manifest_path}",
            ],
            env=repository_environment(),
        )
    )
    manifest = tomllib.loads(blob.decode("utf-8"))
    expected_name = "vc-frame" if name == "vc-frame" else "alacritty"
    package = manifest.get("package")
    if not isinstance(package, dict) or package.get("name") != expected_name:
        raise ValueError(
            f"{name} source manifest must identify package {expected_name}"
        )
    version = manifest[table]["package" if table == "workspace" else "version"]
    if table == "workspace":
        version = version["version"]
    if version != pin["version"]:
        raise ValueError(
            f"{name} version at pinned commit is {version}, manifest says {pin['version']}"
        )


def materialize_sources(
    pins: dict[str, dict[str, str]], repositories: dict[str, Path], destination: Path
) -> None:
    """Export verified Git objects, never a donor's index or working files.

    Both native platform assemblers consume this receipt. Its archive digest is
    of the exact Git-generated tar extracted here, not an unrelated hosted zip.
    The receipt describes source selection, not a compiled or released pack.
    """
    destination.mkdir(parents=True)
    records = {}
    for name, repo in repositories.items():
        pin = pins[name]
        archive = destination / f"{name}.tar"
        source = destination / name
        source.mkdir()
        # Read canonical objects through an empty bare repository: archive in
        # the donor would also consume its uncommitted info/attributes. Neither
        # donor config/templates nor caller/global/system attributes may shape
        # a receipt that names this immutable commit. Committed attributes still
        # apply, including export-ignore and export-subst.
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
            and key not in {"TAR_OPTIONS", "TAR_READER_OPTIONS"}
        }
        environment.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_SYSTEM=os.devnull,
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_ATTR_NOSYSTEM="1",
            GIT_NO_REPLACE_OBJECTS="1",
        )
        objects = Path(git("rev-parse", "--git-path", "objects", cwd=repo))
        if not objects.is_absolute():
            objects = repo / objects
        with tempfile.TemporaryDirectory(prefix="vibecrafted-export-") as temp:
            isolated = Path(temp)
            subprocess.run(
                ["git", "init", "--bare", "--template=", "--quiet", str(isolated)],
                env=environment,
                check=True,
            )
            # Git treats a CR as part of the path, so bypass Windows text-mode
            # newline translation for this LF-delimited Git protocol file.
            (isolated / "objects/info/alternates").write_bytes(
                (objects.resolve().as_posix() + "\n").encode("utf-8")
            )
            subprocess.run(
                [
                    "git",
                    "--no-replace-objects",
                    "-C",
                    str(isolated),
                    "-c",
                    f"core.attributesFile={os.devnull}",
                    "archive",
                    "--format=tar",
                    f"--output={archive}",
                    pin["revision"],
                ],
                env=environment,
                check=True,
            )
        # Git produces paths rooted at the repository, so no strip-components
        # or Windows-only nested-archive fallback is needed.
        subprocess.run(
            ["tar", "-xf", str(archive), "-C", str(source)],
            env=environment,
            check=True,
        )
        verify_version(name, pin, source, exported=True)
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        records[name] = {**pin, "source_archive_sha256": digest}
    (destination / "selection.json").write_text(
        json.dumps(
            {"schema": "vibecrafted.source-selection.v1", "components": records},
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="verify pins and donor availability without building",
    )
    mode.add_argument(
        "--materialize",
        type=Path,
        help="export verified donors and selection.json to a new build directory",
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
        if args.materialize:
            materialize_sources(pins, repositories, args.materialize.resolve())
            return 0
        environment = repository_environment()
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
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"source Runtime Pack build failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
