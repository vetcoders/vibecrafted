"""Falsifiers for G3 shell receipts: runtime_runs meta, PID sync, early fail."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "runtime" / "scripts"
COMMON_SH = SCRIPTS / "common.sh"
META_SH = SCRIPTS / "lib" / "meta.sh"
AWAIT_SH = SCRIPTS / "await.sh"


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


def _runtime_runs(home: Path) -> Path:
    return home / ".vibecrafted" / "control_plane" / "runtime_runs"


def _write_meta(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _mirror_only_script(src: Path) -> str:
    python = Path(sys.executable).as_posix()
    return f'''
set -euo pipefail
source "{META_SH}"
spawn_python_bin() {{ printf '%s\\n' "{python}"; }}
spawn_abspath() {{ printf '%s\\n' "$1"; }}
spawn_mirror_meta_to_runtime_runs "{src}"
'''


def test_fail_g3_mirror_uses_source_identity_not_spawn_run_id(
    tmp_path: Path,
) -> None:
    """Ambient SPAWN_RUN_ID must not choose the canonical dest.

    Reproducer: source old-run document while SPAWN_RUN_ID=new-run. The
    previous mapping wrote old-run payload into runtime_runs/new-run.
    """
    home = tmp_path / "home"
    home.mkdir()
    src = tmp_path / "old.meta.json"
    _write_meta(src, {"run_id": "old-run", "status": "completed"})
    new_canonical = _runtime_runs(home) / "new-run" / "meta.json"
    _write_meta(new_canonical, {"run_id": "new-run", "status": "running"})
    script = _mirror_only_script(src)
    env_script = f"""
export SPAWN_RUN_ID=new-run
{script}
"""
    result = _bash(env_script, home=home, check=False)
    assert result.returncode == 0, result.stderr
    old_canonical = _runtime_runs(home) / "old-run" / "meta.json"
    assert old_canonical.is_file(), result.stderr
    assert json.loads(old_canonical.read_text(encoding="utf-8"))["run_id"] == "old-run"
    assert json.loads(new_canonical.read_text(encoding="utf-8"))["run_id"] == "new-run"
    assert json.loads(new_canonical.read_text(encoding="utf-8"))["status"] == "running"


def test_fail_g3_mirror_isolates_two_run_identities(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    first = tmp_path / "first.meta.json"
    second = tmp_path / "second.meta.json"
    _write_meta(first, {"run_id": "run-alpha", "status": "completed", "mark": "a"})
    _write_meta(second, {"run_id": "run-beta", "status": "completed", "mark": "b"})
    result_a = _bash(
        "export SPAWN_RUN_ID=run-beta\n" + _mirror_only_script(first),
        home=home,
        check=False,
    )
    result_b = _bash(
        "export SPAWN_RUN_ID=run-alpha\n" + _mirror_only_script(second),
        home=home,
        check=False,
    )
    assert result_a.returncode == 0, result_a.stderr
    assert result_b.returncode == 0, result_b.stderr
    alpha = json.loads(
        (_runtime_runs(home) / "run-alpha" / "meta.json").read_text(encoding="utf-8")
    )
    beta = json.loads(
        (_runtime_runs(home) / "run-beta" / "meta.json").read_text(encoding="utf-8")
    )
    assert alpha == {"run_id": "run-alpha", "status": "completed", "mark": "a"}
    assert beta == {"run_id": "run-beta", "status": "completed", "mark": "b"}


def test_fail_g3_mirror_refuses_missing_and_invalid_identity(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    new_canonical = _runtime_runs(home) / "new-run" / "meta.json"
    _write_meta(new_canonical, {"run_id": "new-run", "status": "running"})
    payloads: list[dict[str, object]] = [
        {"status": "completed"},
        {"run_id": "", "status": "completed"},
        {"run_id": ".", "status": "completed"},
        {"run_id": "two words", "status": "completed"},
        {"run_id": 1, "status": "completed"},
    ]
    for index, payload in enumerate(payloads):
        src = tmp_path / f"bad-{index}.meta.json"
        _write_meta(src, payload)
        result = _bash(
            "export SPAWN_RUN_ID=new-run\n" + _mirror_only_script(src),
            home=home,
            check=False,
        )
        assert result.returncode != 0, payload
        assert json.loads(new_canonical.read_text(encoding="utf-8"))["run_id"] == (
            "new-run"
        )
        assert not (_runtime_runs(home) / "old-run").exists()


def test_fail_g3_mirror_refuses_traversal_run_id(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    control = home / ".vibecrafted" / "control_plane"
    new_canonical = _runtime_runs(home) / "new-run" / "meta.json"
    _write_meta(new_canonical, {"run_id": "new-run", "status": "running"})
    for token in ("../secret", "old-run/../../.codex", "/absolute", r"..\\secret"):
        src = tmp_path / "traverse.meta.json"
        _write_meta(src, {"run_id": token, "status": "completed"})
        result = _bash(
            "export SPAWN_RUN_ID=new-run\n" + _mirror_only_script(src),
            home=home,
            check=False,
        )
        assert result.returncode != 0, token
        assert not (control / "secret").exists()
        assert not (home / ".vibecrafted" / ".codex").exists()
        assert not (control / "runtime_runs" / token).exists()
    assert json.loads(new_canonical.read_text(encoding="utf-8"))["run_id"] == "new-run"


def test_fail_g3_mirror_refuses_path_identity_disagreement(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    mismatched = _runtime_runs(home) / "new-run" / "meta.json"
    _write_meta(mismatched, {"run_id": "old-run", "status": "completed"})
    result = _bash(
        "export SPAWN_RUN_ID=new-run\n" + _mirror_only_script(mismatched),
        home=home,
        check=False,
    )
    assert result.returncode != 0, result.stderr
    assert not (_runtime_runs(home) / "old-run").exists()
    leftover = json.loads(mismatched.read_text(encoding="utf-8"))
    assert leftover["run_id"] == "old-run"


def test_fail_g3_gc_reap_does_not_clobber_other_run(tmp_path: Path) -> None:
    home = tmp_path / "home"
    reports = home / ".vibecrafted" / "artifacts" / "org" / "repo" / "reports"
    reports.mkdir(parents=True)
    old_src = reports / "old.meta.json"
    _write_meta(
        old_src,
        {
            "run_id": "old-run",
            "status": "running",
            "launcher_pid": 999999999,
            "mark": "old",
        },
    )
    new_canonical = _runtime_runs(home) / "new-run" / "meta.json"
    _write_meta(
        new_canonical,
        {
            "run_id": "new-run",
            "status": "running",
            "launcher_pid": os.getpid(),
            "mark": "new",
        },
    )
    script = f'''
set -euo pipefail
source "{COMMON_SH}"
export SPAWN_RUN_ID=new-run
spawn_gc_dead_runs "{reports}"
'''
    result = _bash(script, home=home, check=False)
    assert result.returncode == 0, result.stderr
    old_payload = json.loads(old_src.read_text(encoding="utf-8"))
    assert old_payload["status"] == "ghost"
    old_canonical = _runtime_runs(home) / "old-run" / "meta.json"
    assert old_canonical.is_file()
    assert json.loads(old_canonical.read_text(encoding="utf-8"))["run_id"] == "old-run"
    assert json.loads(old_canonical.read_text(encoding="utf-8"))["status"] == "ghost"
    new_payload = json.loads(new_canonical.read_text(encoding="utf-8"))
    assert new_payload["run_id"] == "new-run"
    assert new_payload["status"] == "running"
    assert new_payload["mark"] == "new"
