"""The source lane binds donors before the canonical Runtime Pack builder runs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/build-source-runtime-pack.py"


def donor(path: Path, cargo_file: str, version: str) -> str:
    (path / cargo_file).parent.mkdir(parents=True)
    if cargo_file == "Cargo.toml":
        body = f'[workspace.package]\nversion = "{version}"\n'
    else:
        body = f'[package]\nversion = "{version}"\n'
    (path / cargo_file).write_text(body)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", cargo_file], check=True)
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
        check=True,
    )
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


@pytest.mark.parametrize("wrong_version", [False, True])
def test_source_check_binds_exact_commits_without_touching_donors(
    tmp_path: Path, wrong_version: bool
) -> None:
    frame = tmp_path / "vc-frame"
    terminal = tmp_path / "vc-terminal"
    frame_sha = donor(frame, "Cargo.toml", "4.3.1")
    terminal_sha = donor(terminal, "alacritty/Cargo.toml", "0.18.0-dev")
    (frame / "uncommitted.txt").write_text("Founder's work\n")
    manifest = tmp_path / "pins.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "vibecrafted.source-components.v1",
                "components": {
                    "vc-frame": {
                        "repository": "unused",
                        "version": "4.3.2" if wrong_version else "4.3.1",
                        "revision": frame_sha,
                    },
                    "vc-terminal": {
                        "repository": "unused",
                        "version": "0.18.0-dev",
                        "revision": terminal_sha,
                    },
                },
            }
        )
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--manifest", str(manifest)],
        env={
            **os.environ,
            "VIBECRAFTED_FRAME_REPO": str(frame),
            "VIBECRAFTED_TERMINAL_REPO": str(terminal),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) != wrong_version, result.stderr
    assert (frame / "uncommitted.txt").read_text() == "Founder's work\n"
    assert (
        subprocess.check_output(
            ["git", "-C", str(frame), "rev-parse", "HEAD"], text=True
        ).strip()
        == frame_sha
    )
    assert (
        subprocess.check_output(
            ["git", "-C", str(terminal), "rev-parse", "HEAD"], text=True
        ).strip()
        == terminal_sha
    )


def test_source_build_passes_pins_to_existing_pack_builder(tmp_path: Path) -> None:
    frame = tmp_path / "vc-frame"
    terminal = tmp_path / "vc-terminal"
    frame_sha = donor(frame, "Cargo.toml", "4.3.1")
    terminal_sha = donor(terminal, "alacritty/Cargo.toml", "0.18.0-dev")
    manifest = tmp_path / "pins.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "vibecrafted.source-components.v1",
                "components": {
                    "vc-frame": {
                        "repository": "unused",
                        "version": "4.3.1",
                        "revision": frame_sha,
                    },
                    "vc-terminal": {
                        "repository": "unused",
                        "version": "0.18.0-dev",
                        "revision": terminal_sha,
                    },
                },
            }
        )
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "capture"
    fake_make = bin_dir / "make"
    fake_make.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" "$VIBECRAFTED_FRAME_REVISION" '
        '"$VIBECRAFTED_TERMINAL_REVISION" "$VIBECRAFTED_FRAME_REPO" '
        '"$VIBECRAFTED_TERMINAL_REPO" > "$CAPTURE"\n'
    )
    fake_make.chmod(0o755)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(manifest)],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CAPTURE": str(capture),
            "VIBECRAFTED_FRAME_REPO": str(frame),
            "VIBECRAFTED_TERMINAL_REPO": str(terminal),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert capture.read_text().splitlines() == [
        "--no-print-directory runtime-pack RELEASE_FLAGS=--snapshot-donors",
        frame_sha,
        terminal_sha,
        str(frame),
        str(terminal),
    ]


def test_source_check_fetches_missing_donor_objects_into_temporary_repositories(
    tmp_path: Path,
) -> None:
    frame = tmp_path / "upstream-frame"
    terminal = tmp_path / "upstream-terminal"
    frame_sha = donor(frame, "Cargo.toml", "4.3.1")
    terminal_sha = donor(terminal, "alacritty/Cargo.toml", "0.18.0-dev")
    manifest = tmp_path / "pins.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "vibecrafted.source-components.v1",
                "components": {
                    "vc-frame": {
                        "repository": frame.as_uri(),
                        "version": "4.3.1",
                        "revision": frame_sha,
                    },
                    "vc-terminal": {
                        "repository": terminal.as_uri(),
                        "version": "0.18.0-dev",
                        "revision": terminal_sha,
                    },
                },
            }
        )
    )
    environment = {**os.environ}
    environment.pop("VIBECRAFTED_FRAME_REPO", None)
    environment.pop("VIBECRAFTED_TERMINAL_REPO", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--manifest", str(manifest)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "vibecrafted-source-donors-" in result.stdout
    assert (
        subprocess.check_output(
            ["git", "-C", str(frame), "rev-parse", "HEAD"], text=True
        ).strip()
        == frame_sha
    )
