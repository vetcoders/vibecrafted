"""Terminal cleanup must preserve another run's liveness lock."""

import subprocess
from pathlib import Path

import pytest

LOCK_LIBRARY = (
    Path(__file__).resolve().parents[2]
    / "vibecrafted-core/vibecrafted_core/runtime/scripts/lib/lock.sh"
)


@pytest.mark.parametrize(
    ("owner", "caller", "retained"),
    [
        ("run-a", "run.a", True),
        ("run.a", "run.a", False),
        ("run.ab", "run.a", True),
        ("run.a", "run-a", True),
    ],
)
def test_run_lock_release_uses_literal_owner(
    tmp_path: Path, owner: str, caller: str, retained: bool
) -> None:
    lock = tmp_path / "run.lock"
    contents = f"run_id={owner}\nstatus=running\n"
    lock.write_text(contents)

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; spawn_release_run_lock "$2" "$3"',
            "run-lock-test",
            str(LOCK_LIBRARY),
            str(lock),
            caller,
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert lock.exists() is retained
    if retained:
        assert lock.read_text() == contents
