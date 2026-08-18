"""Runtime proof for `scripts/lib/donor-snapshot.sh`.

These tests drive the real shell functions against real scratch git repositories
— no text assertions, no mocks. What they must prove is the thing the 2026-08-11
incident got wrong: a hand-rolled `git worktree add --detach` into a temp dir
left a ghost registration in the donor when the temp dir disappeared first, and
`git worktree list` lied about it afterwards.

The contract under test:

1. a snapshot of a DIRTY donor is clean and sits exactly at the donor HEAD, so
   the release's dirty-donor gate passes honestly and the receipt still binds
   the SHA it claims;
2. the donor's own dirty files, index and stash list are never touched;
3. the reaper removes the worktree through git and prunes, so `worktree list`
   is back to one entry — on the success path AND from a trap on failure;
4. residue from an interrupted earlier run (path deleted behind git's back) is
   reclaimed instead of blocking the next build.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY = REPO_ROOT / "scripts/lib/donor-snapshot.sh"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _make_donor(root: Path) -> Path:
    """A donor repository in the state the Living Tree actually keeps them in."""

    root.mkdir(parents=True)
    _git("init", "--quiet", "--initial-branch", "main", cwd=root)
    _git("config", "user.email", "agents@vetcoders.io", cwd=root)
    _git("config", "user.name", "donor", cwd=root)
    (root / "committed.txt").write_text("committed\n", encoding="utf-8")
    _git("add", "committed.txt", cwd=root)
    _git("commit", "--quiet", "-m", "seed", cwd=root)

    # Dirty, exactly the way a donor under active work is dirty.
    (root / "committed.txt").write_text("edited by another agent\n", encoding="utf-8")
    (root / "scratch.txt").write_text("untracked\n", encoding="utf-8")
    return root


def _run_driver(script: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    driver = tmp_path / "driver.sh"
    driver.write_text(
        f'#!/usr/bin/env bash\nset -euo pipefail\n. "{LIBRARY}"\n{script}\n',
        encoding="utf-8",
    )
    return subprocess.run(
        ["bash", str(driver)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _worktree_count(donor: Path) -> int:
    listing = _git("worktree", "list", cwd=donor)
    return len([line for line in listing.splitlines() if line.strip()])


def test_snapshot_of_a_dirty_donor_is_clean_and_binds_the_donor_head(
    tmp_path: Path,
) -> None:
    donor = _make_donor(tmp_path / "donor")
    snapshot = tmp_path / "work/donor-snapshots/donor"
    head = _git("rev-parse", "HEAD", cwd=donor)

    assert _git("status", "--porcelain", "--untracked-files=normal", cwd=donor), (
        "the fixture must be dirty or it proves nothing"
    )

    result = _run_driver(
        f'donor_snapshot_create "{donor}" "{snapshot}"\n'
        'printf "%s\\n" "$DONOR_SNAPSHOT_HEAD"\n',
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == head

    assert snapshot.is_dir()
    assert _git("rev-parse", "HEAD", cwd=snapshot) == head
    assert _git("status", "--porcelain", "--untracked-files=all", cwd=snapshot) == "", (
        "the snapshot must be clean so the dirty-donor gate passes honestly"
    )
    # The dirty donor content stayed exactly where it was.
    assert (donor / "scratch.txt").exists()
    assert (donor / "committed.txt").read_text(encoding="utf-8") == (
        "edited by another agent\n"
    )
    # ... and the snapshot carries the COMMITTED bytes, not the edited ones.
    assert (snapshot / "committed.txt").read_text(encoding="utf-8") == "committed\n"
    assert not (snapshot / "scratch.txt").exists()


def test_reaper_returns_the_donor_to_a_single_worktree(tmp_path: Path) -> None:
    donor = _make_donor(tmp_path / "donor")
    snapshot = tmp_path / "work/donor-snapshots/donor"

    result = _run_driver(
        f'donor_snapshot_create "{donor}" "{snapshot}" >/dev/null\n'
        f'test "$(git -C "{donor}" worktree list | wc -l)" -eq 2\n'
        "donor_snapshot_reap\n"
        "donor_snapshot_reap  # idempotent\n",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert _worktree_count(donor) == 1
    assert not snapshot.exists()
    assert _git("stash", "list", cwd=donor) == ""


def test_trap_reaps_when_the_build_dies_after_snapshotting(tmp_path: Path) -> None:
    """The failure path is the one the 2026-08-11 ghost came from."""

    donor = _make_donor(tmp_path / "donor")
    snapshot = tmp_path / "work/donor-snapshots/donor"

    result = _run_driver(
        "trap 'donor_snapshot_reap || true' EXIT INT TERM HUP\n"
        f'donor_snapshot_create "{donor}" "{snapshot}" >/dev/null\n'
        'printf "boom\\n" >&2\n'
        "exit 1\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert _worktree_count(donor) == 1, (
        "a failed build must not leave a worktree registration behind"
    )
    assert not snapshot.exists()


def test_residue_from_an_interrupted_run_is_reclaimed(tmp_path: Path) -> None:
    """Exactly the ghost: the snapshot path vanished behind git's back."""

    donor = _make_donor(tmp_path / "donor")
    snapshot = tmp_path / "work/donor-snapshots/donor"

    first = _run_driver(
        f'donor_snapshot_create "{donor}" "{snapshot}" >/dev/null', tmp_path
    )
    assert first.returncode == 0, first.stderr

    # The temp dir disappears; git still believes the worktree exists.
    subprocess.run(["rm", "-rf", str(snapshot)], check=True)
    assert _worktree_count(donor) == 2, "fixture must reproduce the ghost"

    second = _run_driver(
        f'donor_snapshot_create "{donor}" "{snapshot}" >/dev/null\n'
        "donor_snapshot_reap\n",
        tmp_path,
    )
    assert second.returncode == 0, second.stderr
    assert _worktree_count(donor) == 1
    assert not snapshot.exists()


@pytest.mark.parametrize(
    "needle",
    (
        "worktree add --detach",
        "worktree remove --force",
        "worktree prune",
    ),
)
def test_library_reaps_through_git_not_through_rm(needle: str) -> None:
    assert needle in LIBRARY.read_text(encoding="utf-8")


def test_the_record_is_made_in_the_callers_shell_not_a_subshell(
    tmp_path: Path,
) -> None:
    """REGRESSION 2026-08-18, caught by a real release run, not by a unit test.

    `donor_snapshot_create` used to print the SHA so the caller could write
    `head="$(donor_snapshot_create ...)"`. Command substitution runs the
    function in a subshell: `DONOR_SNAPSHOTS+=(...)` mutated a copy that died
    with the subshell, the parent entered its trap with an empty record list,
    and a deliberately failed release left BOTH donor worktrees registered —
    the exact ghost this library exists to prevent. The driver below mirrors the
    builder's real call shape.
    """

    donor = _make_donor(tmp_path / "donor")
    snapshot = tmp_path / "work/donor-snapshots/donor"

    result = _run_driver(
        "trap 'donor_snapshot_reap || true' EXIT INT TERM HUP\n"
        f'donor_snapshot_create "{donor}" "{snapshot}"\n'
        'head="$DONOR_SNAPSHOT_HEAD"\n'
        'test -n "$head"\n'
        'test "${#DONOR_SNAPSHOTS[@]}" -eq 1\n'
        "exit 1\n",
        tmp_path,
    )
    assert result.returncode == 1, result.stderr
    assert _worktree_count(donor) == 1
    assert not snapshot.exists()


def test_builder_never_captures_the_snapshot_through_command_substitution() -> None:
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )
    assert "$(donor_snapshot_create" not in builder
    assert 'terminal_head="$DONOR_SNAPSHOT_HEAD"' in builder
    assert 'frame_head="$DONOR_SNAPSHOT_HEAD"' in builder
