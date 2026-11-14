"""Real tiny donor repositories for source-selection and inventory contracts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def repository_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}
    }


def donor(path: Path, cargo_file: str, version: str) -> str:
    (path / cargo_file).parent.mkdir(parents=True)
    if cargo_file == "Cargo.toml":
        body = f'[package]\nname = "vc-frame"\n[workspace.package]\nversion = "{version}"\n'
    else:
        body = f'[package]\nname = "alacritty"\nversion = "{version}"\n'
    (path / cargo_file).write_text(body)
    subprocess.run(
        ["git", "init", "-q", str(path)], env=repository_environment(), check=True
    )
    subprocess.run(
        ["git", "-C", str(path), "add", cargo_file],
        env=repository_environment(),
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        env=repository_environment(),
        check=True,
    )
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        text=True,
        env=repository_environment(),
    ).strip()


def source_fixture(root: Path) -> tuple[Path, dict, dict[str, Path]]:
    checkout = root / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "build-source-runtime-pack.py",
        "build-linux-arm64-runtime-pack.sh",
        "build-windows-x64-runtime-pack.ps1",
        "build-linux-runtime-pack.sh",
    ):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    repos = {"vc-frame": root / "local-frame", "vc-terminal": root / "local-terminal"}
    records = {}
    for name, repo in repos.items():
        cargo = "Cargo.toml" if name == "vc-frame" else "alacritty/Cargo.toml"
        version = "4.3.1" if name == "vc-frame" else "0.18.0-dev"
        revision = donor(repo, cargo, version)
        records[name] = {
            "repository": repo.as_uri(),
            "version": version,
            "revision": revision,
        }
    pins = {"schema": "vibecrafted.source-components.v1", "components": records}
    (checkout / "config").mkdir()
    write_pins(checkout, pins)
    return checkout, pins, repos


def write_pins(checkout: Path, pins: dict) -> None:
    (checkout / "config/source-components.json").write_text(json.dumps(pins))


def prepare(
    checkout: Path,
    destination: Path,
    builder: str,
    repos: dict[str, Path] | None = None,
) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    for name in (
        "VIBECRAFTED_FRAME_REPO",
        "VIBECRAFTED_TERMINAL_REPO",
        "VIBECRAFTED_FRAME_REVISION",
        "VIBECRAFTED_TERMINAL_REVISION",
    ):
        environment.pop(name, None)
    if repos is not None:
        environment.update(
            VIBECRAFTED_FRAME_REPO=str(repos["vc-frame"]),
            VIBECRAFTED_TERMINAL_REPO=str(repos["vc-terminal"]),
        )
    if builder == "linux":
        command = [
            "bash",
            str(checkout / "scripts/build-linux-runtime-pack.sh"),
            "--prepare-sources",
            str(destination),
        ]
    elif builder == "windows":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            pytest.skip(
                "PowerShell required for actual Windows builder source-selection CLI"
            )
        command = [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(checkout / "scripts/build-windows-x64-runtime-pack.ps1"),
            "-PrepareSources",
            str(destination),
        ]
    else:
        command = [
            sys.executable,
            str(checkout / "scripts/build-source-runtime-pack.py"),
            "--materialize",
            str(destination),
        ]
    return subprocess.run(
        command, env=environment, capture_output=True, text=True, check=False
    )


def materialize_fixture(root: Path, *, changed_pin: bool) -> Path:
    checkout, pins, repos = source_fixture(root)
    if changed_pin:
        for name, repo in repos.items():
            (repo / "new-source.txt").write_text("next committed donor input\n")
            subprocess.run(
                ["git", "-C", str(repo), "add", "."],
                env=repository_environment(),
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "commit",
                    "-qm",
                    "next",
                ],
                env=repository_environment(),
                check=True,
            )
            pins["components"][name]["revision"] = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                env=repository_environment(),
                text=True,
            ).strip()
        write_pins(checkout, pins)
    result = prepare(checkout, root / "selected", "helper", repos)
    assert result.returncode == 0, result.stderr
    return root / "selected/selection.json"
