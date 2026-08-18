"""The tracked tree must stay symlink-free, and a Windows clone must still run.

BORN FROM (roadmap 4.2.0, cut W1-a): five alias symlinks lived in the tracked
tree until `4d1f1d11` (#47) removed them — `runtime`, `skills`,
`docs/install.sh`, `vibecrafted-core/vibecrafted_core/config/vc-frame` and
`.../runtime/shell/vetcoders.zsh`. Nothing guarded the removal. A Windows clone
(`core.symlinks=false`, the default without Developer Mode) materializes a
tracked symlink as a *text file holding the target path*, and bsdtar refuses
some of them outright, so a single re-added alias makes a fresh clone dead on
arrival while every macOS gate stays green.

The doctrine these tests enforce (roadmap 4.2.0 D1): the repository tree is
symlink-free; projections such as the installed generation's top-level
`runtime/` are *produced by copy*, never linked.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

GIT_SYMLINK_MODE = "120000"

# The five paths that were symlinks before #47. Each must now be a regular
# file, a real directory, or absent — never a symlink again.
HISTORICAL_ALIAS_PATHS = (
    "docs/install.sh",
    "runtime",
    "skills",
    "vibecrafted-core/vibecrafted_core/config/vc-frame",
    "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.zsh",
)


def _git(*args: str, cwd: Path = REPO_ROOT) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _tracked_symlinks() -> list[str]:
    entries = []
    for line in _git("ls-files", "-s").splitlines():
        if not line:
            continue
        mode, _, remainder = line.partition(" ")
        if mode == GIT_SYMLINK_MODE:
            entries.append(remainder.split("\t", 1)[-1])
    return entries


def test_tracked_tree_carries_no_symlinks() -> None:
    offenders = _tracked_symlinks()
    assert offenders == [], (
        "the tracked tree must stay symlink-free (roadmap 4.2.0 D1); "
        f"mode {GIT_SYMLINK_MODE} entries found: {offenders}. "
        "Produce the projection by copy in the installer/packers instead."
    )


@pytest.mark.parametrize("relative", HISTORICAL_ALIAS_PATHS)
def test_historical_alias_paths_are_real_or_absent(relative: str) -> None:
    path = REPO_ROOT / relative
    if not path.exists() and not path.is_symlink():
        return
    assert not path.is_symlink(), (
        f"{relative} is a symlink again; it was removed in 4d1f1d11 (#47) "
        "because Windows clones and bsdtar cannot carry it"
    )
    assert path.is_file() or path.is_dir()


def test_windows_clone_without_symlink_support_keeps_entrypoints_runnable(
    tmp_path: Path,
) -> None:
    """`core.symlinks=false` is the Windows default; the shims must survive it.

    A clone made this way is exactly what a Windows user gets: any tracked
    symlink would arrive as a text file with the target path inside, and
    executing it would fail. Both entrypoints below were symlinks before #47.
    """

    clone = tmp_path / "windows-clone"
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-hardlinks",
            "--depth",
            "1",
            "--single-branch",
            "--no-tags",
            "-c",
            "core.symlinks=false",
            f"file://{REPO_ROOT}",
            str(clone),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    materialized = [
        entry
        for entry in clone.rglob("*")
        if entry.is_symlink() and ".git" not in entry.relative_to(clone).parts
    ]
    assert materialized == [], f"clone still carries symlinks: {materialized}"

    install_shim = clone / "docs/install.sh"
    assert install_shim.is_file() and not install_shim.is_symlink()
    help_run = subprocess.run(
        ["bash", str(install_shim), "--help"],
        cwd=clone,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert help_run.returncode == 0, help_run.stderr[-2000:]
    assert "Usage: install.sh" in help_run.stdout

    zsh = shutil.which("zsh")
    if zsh is None:  # pragma: no cover - zsh is present on macOS and CI
        pytest.skip("zsh is not available on this host")
    shim = clone / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.zsh"
    assert shim.is_file() and not shim.is_symlink()
    source_run = subprocess.run(
        [zsh, "-c", f"source {shim}"],
        cwd=clone,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert source_run.returncode == 0, source_run.stderr[-2000:]
