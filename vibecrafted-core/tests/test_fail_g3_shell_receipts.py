"""Falsifiers for G3 shell receipts: runtime_runs meta, PID sync, early fail."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMON_SH = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "scripts"
    / "common.sh"
)
AWAIT_SH = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "scripts"
    / "await.sh"
)


def _bash(
    script: str, *, home: Path, check: bool = True
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return subprocess.run(
        ["bash", "-lc", script],
        check=check,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_fail_g3_write_meta_publishes_runtime_runs_and_syncs_pid(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    reports = (
        home / ".vibecrafted" / "artifacts" / "org" / "repo" / "2026_0908" / "reports"
    )
    reports.mkdir(parents=True)
    advertised = reports / "run.meta.json"
    script = f'''
set -euo pipefail
source "{COMMON_SH}"
export SPAWN_RUN_ID=impl-g3-receipt-001
export SPAWN_AGENT=grok
spawn_write_meta "{advertised}" launching grok implement / plan.md report.md t.log l.sh
spawn_update_meta_pid "{advertised}" $$
printf '%s\\n' "$(spawn_find_meta_for_run_id "{reports}" "impl-g3-receipt-001")"
'''
    result = _bash(script, home=home)
    canonical = (
        home
        / ".vibecrafted"
        / "control_plane"
        / "runtime_runs"
        / "impl-g3-receipt-001"
        / "meta.json"
    )
    assert canonical.is_file(), result.stdout + result.stderr
    assert advertised.is_file()
    payload = json.loads(canonical.read_text(encoding="utf-8"))
    assert payload["run_id"] == "impl-g3-receipt-001"
    assert isinstance(payload["launcher_pid"], int)
    assert payload["launcher_pid"] > 0
    assert payload["liveness"] == "pid_alive"
    found = result.stdout.strip().splitlines()[-1]
    assert found == str(canonical)
    advertised_payload = json.loads(advertised.read_text(encoding="utf-8"))
    assert advertised_payload["launcher_pid"] == payload["launcher_pid"]


def test_fail_g3_early_failure_settles_runtime_runs_meta(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    script = f'''
set -euo pipefail
source "{COMMON_SH}"
export SPAWN_RUN_ID=impl-g3-early-fail
export SPAWN_AGENT=grok
export SPAWN_SKILL_NAME=implement
spawn_die "probe refused"
'''
    result = _bash(script, home=home, check=False)
    assert result.returncode == 1
    canonical = (
        home
        / ".vibecrafted"
        / "control_plane"
        / "runtime_runs"
        / "impl-g3-early-fail"
        / "meta.json"
    )
    assert canonical.is_file(), result.stderr
    payload = json.loads(canonical.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 1


def test_fail_g3_print_launch_advertises_runtime_runs_not_control_json(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    script = f'''
set -euo pipefail
source "{COMMON_SH}"
export SPAWN_RUN_ID=impl-g3-print
export SPAWN_PLAN=/tmp/plan.md
export SPAWN_REPORT=/tmp/report.md
export SPAWN_TRANSCRIPT=/tmp/t.log
export SPAWN_META=/tmp/legacy.meta.json
spawn_print_launch grok implement headless 1
'''
    result = _bash(script, home=home)
    out = result.stdout
    assert "runtime_runs/impl-g3-print/meta.json" in out
    assert "control_plane/runs/impl-g3-print.json" not in out


def test_fail_g3_await_resolves_runtime_runs_meta(tmp_path: Path) -> None:
    home = tmp_path / "home"
    run_id = "impl-g3-await-001"
    canonical = (
        home / ".vibecrafted" / "control_plane" / "runtime_runs" / run_id / "meta.json"
    )
    canonical.parent.mkdir(parents=True)
    canonical.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "completed",
                "agent": "grok",
                "exit_code": 0,
                "updated_at": "2026-09-08T00:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    result = subprocess.run(
        ["bash", str(AWAIT_SH), "grok", "--run-id", run_id, "--describe"],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert str(canonical) in result.stdout
    assert f"control_plane/runs/{run_id}.json" not in result.stdout
