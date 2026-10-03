"""Regression gate for the deck ↔ scripts/vibecrafted mirror-parity defect.

2026-09-30 floor cut: a worker landed a new verb in `scripts/vibecrafted` (the
checkout mirror) instead of `vibecrafted-core/vibecrafted_core/deck/vibecrafted`
(the packaged owner — see docs/runtime/UNIFIED_LAUNCH_CONTRACT.md §"One
semantic owner"). `make check` stayed green and the verb never reached the
installed product; the integrator only caught the drift with a manual
``diff -q``. The existing byte-identity assertion
(`vibecrafted-core/tests/test_cursor_parity.py::
test_deck_mirror_is_byte_identical_to_canonical`) only runs under `make test`,
not `make check`. This module pins the cheap `make check` gate
(`scripts/check-deck-mirror.sh` via the `deck-mirror-check` target) that
closes that hole.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD_SCRIPT = REPO_ROOT / "scripts" / "check-deck-mirror.sh"
OWNER = REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "deck" / "vibecrafted"
MIRROR = REPO_ROOT / "scripts" / "vibecrafted"


def _recipe(makefile_text: str, target: str) -> str:
    return makefile_text.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]


def test_check_target_runs_the_deck_mirror_guard() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    check_recipe = _recipe(makefile, "check")
    assert "deck-mirror-check" in check_recipe.split("\n", 1)[0].split(), check_recipe

    guard_recipe = _recipe(makefile, "deck-mirror-check")
    assert "scripts/check-deck-mirror.sh" in guard_recipe, guard_recipe


def test_guard_passes_on_the_current_repo_checkout() -> None:
    """Baseline truth: owner and mirror are identical at HEAD."""
    assert OWNER.read_bytes() == MIRROR.read_bytes()

    result = subprocess.run(
        ["bash", str(GUARD_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "deck mirror OK" in result.stdout


def test_guard_fails_closed_and_names_the_owner_and_repair_command(
    tmp_path: Path,
) -> None:
    """Reproduces the 2026-09-30 defect: a mirror-only edit must fail the gate."""
    drifted_root = tmp_path / "drifted-repo"
    scripts_dir = drifted_root / "scripts"
    deck_dir = drifted_root / "vibecrafted-core" / "vibecrafted_core" / "deck"
    scripts_dir.mkdir(parents=True)
    deck_dir.mkdir(parents=True)

    shutil.copyfile(OWNER, deck_dir / "vibecrafted")
    mirror_copy = scripts_dir / "vibecrafted"
    shutil.copyfile(OWNER, mirror_copy)
    # Simulate the exact defect: a new verb landed in the mirror only.
    mirror_copy.write_text(
        mirror_copy.read_text(encoding="utf-8") + "\n# drifted housekeeping verb\n",
        encoding="utf-8",
    )
    shutil.copyfile(GUARD_SCRIPT, scripts_dir / "check-deck-mirror.sh")

    result = subprocess.run(
        ["bash", str(scripts_dir / "check-deck-mirror.sh")],
        cwd=drifted_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "vibecrafted-core/vibecrafted_core/deck/vibecrafted" in result.stderr, (
        result.stderr
    )
    assert (
        "cp vibecrafted-core/vibecrafted_core/deck/vibecrafted scripts/vibecrafted"
        in result.stderr
    ), result.stderr
