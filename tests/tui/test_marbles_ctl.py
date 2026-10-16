from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MARBLES_CTL = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "scripts"
    / "marbles_ctl.sh"
)


def _write_state(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_marbles_delete_archives_run_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    session_dir = crafted_home / "marbles" / "marb-424242"
    state_file = session_dir / "state.json"

    home.mkdir()
    _write_state(
        state_file,
        {
            "run_id": "marb-424242",
            "status": "initialized",
            "agent": "codex",
            "root": "/tmp/worktree",
        },
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)

    subprocess.run(
        ["bash", str(MARBLES_CTL), "delete", "marb-424242"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    archive_day = datetime.now(UTC).strftime("%Y-%m-%d")
    archived_dir = crafted_home / "marbles" / "_archived" / archive_day / "marb-424242"
    archived_state = json.loads(
        (archived_dir / "state.json").read_text(encoding="utf-8")
    )

    assert not session_dir.exists()
    assert archived_dir.is_dir()
    assert archived_state["status"] == "deleted"
    assert archived_state["previous_status"] == "initialized"
    assert archived_state["archived_from"] == str(session_dir)
    assert "archived_at" in archived_state
    assert "deleted_at" in archived_state


def test_marbles_delete_refuses_live_session(tmp_path: Path) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    session_dir = crafted_home / "marbles" / "marb-live"
    state_file = session_dir / "state.json"

    home.mkdir()
    _write_state(
        state_file,
        {
            "run_id": "marb-live",
            "status": "running",
            "watcher_pid": os.getpid(),
            "agent": "codex",
            "root": "/tmp/worktree",
        },
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)

    result = subprocess.run(
        ["bash", str(MARBLES_CTL), "delete", "marb-live"],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    archive_day = datetime.now(UTC).strftime("%Y-%m-%d")
    archived_dir = crafted_home / "marbles" / "_archived" / archive_day / "marb-live"

    assert result.returncode != 0
    assert session_dir.is_dir()
    assert not archived_dir.exists()
    assert "appears live; stop it first" in result.stderr


def _gc_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    (crafted_home / "marbles").mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    return env


def test_marbles_gc_rejects_missing_stale_minutes_value(tmp_path: Path) -> None:
    """Missing value must surface as a clean usage error, not a `set -u` abort."""
    result = subprocess.run(
        ["bash", str(MARBLES_CTL), "gc", "--stale-minutes"],
        check=False,
        cwd=REPO_ROOT,
        env=_gc_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "--stale-minutes requires a positive integer value" in result.stderr
    # Must not be the bash `set -u` trap.
    assert "unbound variable" not in result.stderr


def test_marbles_gc_rejects_non_integer_stale_minutes(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(MARBLES_CTL), "gc", "--stale-minutes", "abc"],
        check=False,
        cwd=REPO_ROOT,
        env=_gc_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "--stale-minutes must be a positive integer" in result.stderr
    assert "abc" in result.stderr


def test_marbles_gc_rejects_non_positive_stale_minutes(tmp_path: Path) -> None:
    for bad_value in ("0", "-5"):
        result = subprocess.run(
            ["bash", str(MARBLES_CTL), "gc", "--stale-minutes", bad_value],
            check=False,
            cwd=REPO_ROOT,
            env=_gc_env(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, f"value {bad_value!r} should be rejected"
        assert "--stale-minutes must be a positive integer" in result.stderr


def test_marbles_gc_accepts_valid_stale_minutes_dry_run(tmp_path: Path) -> None:
    """Valid --stale-minutes + --dry-run must still run cleanly on an empty store."""
    result = subprocess.run(
        ["bash", str(MARBLES_CTL), "gc", "--stale-minutes", "30", "--dry-run"],
        check=False,
        cwd=REPO_ROOT,
        env=_gc_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_marbles_gc_auto_archive_moves_stale_state_dir(tmp_path: Path) -> None:
    env = _gc_env(tmp_path)
    crafted_home = Path(env["VIBECRAFTED_HOME"])
    session_dir = crafted_home / "marbles" / "marb-stale"
    state_file = session_dir / "state.json"
    _write_state(
        state_file,
        {
            "run_id": "marb-stale",
            "status": "running",
            "agent": "codex",
            "root": "/tmp/worktree",
            "current_loop": 1,
            "total_loops": 3,
            "updated_at": "2000-01-01T00:00:00+00:00",
        },
    )

    result = subprocess.run(
        [
            "bash",
            str(MARBLES_CTL),
            "gc",
            "--auto-archive",
            "--stale-minutes",
            "1",
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    archive_day = datetime.now(UTC).strftime("%Y-%m-%d")
    archived_dir = crafted_home / "marbles" / "_archived" / archive_day / "marb-stale"
    archived_state = json.loads(
        (archived_dir / "state.json").read_text(encoding="utf-8")
    )

    assert result.returncode == 0, result.stderr
    assert not session_dir.exists()
    assert archived_dir.is_dir()
    assert archived_state["status"] == "ghost"
    assert archived_state["previous_status"] == "running"
    assert archived_state["archived_from"] == str(session_dir)
