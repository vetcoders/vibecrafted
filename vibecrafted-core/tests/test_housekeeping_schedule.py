from __future__ import annotations

import json
import plistlib
from pathlib import Path

import pytest
from vibecrafted_core import housekeeping_schedule as schedule
from vibecrafted_core.housekeeping_schedule import (
    HousekeepingScheduleError,
    HousekeepingSchedulePaths,
    default_paths,
    housekeeping_schedule_installed,
    install_housekeeping_schedule,
    render_housekeeping_launch_agent_plist,
    schedule_cli_main,
    uninstall_housekeeping_schedule,
)
from vibecrafted_core.server_config import HousekeepingConfig

FAKE_CONFIRM_TOKEN = "DELETE-REGENERABLE-VIBECRAFTED-STATE"

_FAKE_SCRIPT_BODY = f'''
"""Stand-in for scripts/vibecrafted_housekeeping.py in tests."""

CONFIRM_TOKEN = "{FAKE_CONFIRM_TOKEN}"


def main(argv=None):
    return 0
'''


def _fake_script(tmp_path: Path) -> Path:
    script = tmp_path / "vibecrafted_housekeeping.py"
    script.write_text(_FAKE_SCRIPT_BODY, encoding="utf-8")
    return script


def _paths(tmp_path: Path, script: Path) -> HousekeepingSchedulePaths:
    return HousekeepingSchedulePaths.create(
        vibecrafted_home=tmp_path / "vh",
        operator_home=tmp_path / "home",
        script_path=script,
    )


def test_separate_daemon_plist_is_retired(tmp_path: Path) -> None:
    paths = _paths(tmp_path, _fake_script(tmp_path))
    with pytest.raises(HousekeepingScheduleError, match="retired"):
        render_housekeeping_launch_agent_plist(HousekeepingConfig(enabled=True), paths)
    assert not paths.launch_agent_file.exists()


def test_registration_preserves_config_and_records_supervisor_owner(
    tmp_path: Path,
) -> None:
    script = _fake_script(tmp_path)
    paths = _paths(tmp_path, script)
    config = HousekeepingConfig(enabled=True, auto_execute=False, retention_days=9)
    install_housekeeping_schedule(config, paths, activate=False)
    payload = json.loads(schedule._owner_file(paths).read_text())
    assert payload["owner"] == "vc-server-supervisor"
    assert payload["script"] == str(script)
    assert payload["sha256"] == schedule._sha256_file(script)
    assert config.auto_execute is False
    assert not paths.launch_agent_file.exists()


def test_install_refuses_when_not_enabled(tmp_path: Path) -> None:
    script = _fake_script(tmp_path)
    paths = _paths(tmp_path, script)
    config = HousekeepingConfig(enabled=False)

    with pytest.raises(HousekeepingScheduleError, match="enabled is false"):
        install_housekeeping_schedule(config, paths, activate=False)

    assert not housekeeping_schedule_installed(paths)


def test_install_writes_plist_and_is_idempotent(tmp_path: Path) -> None:
    script = _fake_script(tmp_path)
    paths = _paths(tmp_path, script)
    config = HousekeepingConfig(enabled=True, auto_execute=False)

    first = install_housekeeping_schedule(config, paths, activate=False)
    second = install_housekeeping_schedule(config, paths, activate=False)

    assert first is True
    assert second is False
    assert housekeeping_schedule_installed(paths)
    assert schedule._owner_file(paths).is_file()
    assert not paths.launch_agent_file.exists()


def test_uninstall_removes_the_plist(tmp_path: Path) -> None:
    script = _fake_script(tmp_path)
    paths = _paths(tmp_path, script)
    config = HousekeepingConfig(enabled=True)
    install_housekeeping_schedule(config, paths, activate=False)

    removed_first = uninstall_housekeeping_schedule(paths, deactivate=False)
    removed_second = uninstall_housekeeping_schedule(paths, deactivate=False)

    assert removed_first is True
    assert removed_second is False
    assert not housekeeping_schedule_installed(paths)


def test_legacy_migration_requires_exact_ownership_digest(tmp_path: Path) -> None:
    paths = _paths(tmp_path, _fake_script(tmp_path))
    paths.launch_agent_file.parent.mkdir(parents=True)
    payload = plistlib.dumps(
        {
            "Label": schedule.HOUSEKEEPING_LAUNCH_AGENT_LABEL,
            "ProgramArguments": ["foreign-command"],
        }
    )
    paths.launch_agent_file.write_bytes(payload)
    ownership = tmp_path / "install-receipt.json"
    digest = schedule._sha256_file(paths.launch_agent_file)
    with pytest.raises(HousekeepingScheduleError, match="ownership receipt missing"):
        schedule.retire_legacy_schedule(
            paths, expected_sha256=digest, ownership_receipt=ownership
        )
    with pytest.raises(HousekeepingScheduleError, match="migration required"):
        install_housekeeping_schedule(
            HousekeepingConfig(enabled=True), paths, activate=False
        )
    ownership.write_text(
        json.dumps(
            {
                "schema": "vibecrafted.runtime-install.v1",
                "owned_files": {str(paths.launch_agent_file): digest},
            }
        )
    )
    with pytest.raises(
        HousekeepingScheduleError, match="receipt.*mismatched|digest mismatch"
    ):
        schedule.retire_legacy_schedule(
            paths, expected_sha256="0" * 64, ownership_receipt=ownership
        )
    assert paths.launch_agent_file.read_bytes() == payload
    digest = schedule._sha256_file(paths.launch_agent_file)
    assert schedule.retire_legacy_schedule(
        paths, expected_sha256=digest, ownership_receipt=ownership
    )


def test_schedule_respects_disabled_and_plan_only(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    script = tmp_path / "script.py"
    marker = tmp_path / "executed"
    script.write_text(f"""
CONFIRM_TOKEN = 'token'
def build_plan(*args): return {{'mode':'plan'}}
def write_receipt(*args): pass
def execute_plan(*args):
 from pathlib import Path
 Path({str(marker)!r}).touch()
 return {{'executed':True}}
""")
    paths = _paths(tmp_path, script)
    cfg = HousekeepingConfig(enabled=True, auto_execute=False, interval_hours=1)
    monkeypatch.setattr(schedule, "load_housekeeping_config", lambda *a, **k: cfg)
    install_housekeeping_schedule(cfg, paths, activate=False)
    now = [0.0]
    supervisor_config = SimpleNamespace(
        config_file=None,
        paths=SimpleNamespace(
            home=paths.vibecrafted_home, operator_home=paths.operator_home
        ),
    )
    scheduler = schedule.SupervisorHousekeeping(supervisor_config, clock=lambda: now[0])
    assert scheduler.tick() is False
    now[0] = 3600
    assert scheduler.tick() is True
    assert not marker.exists()
    cfg = HousekeepingConfig(enabled=False, auto_execute=True, interval_hours=1)
    now[0] = 7200
    assert scheduler.tick() is False
    now[0] = 100_000
    assert scheduler.tick() is False
    assert not marker.exists()
    cfg = HousekeepingConfig(enabled=True, auto_execute=True, interval_hours=1)
    assert scheduler.tick() is False
    now[0] += 3600
    assert scheduler.tick() is True
    assert marker.exists()


def test_default_paths_uses_environment_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "vh-env"))
    monkeypatch.setenv("HOME", str(tmp_path / "home-env"))
    script = _fake_script(tmp_path)

    paths = default_paths(script_path=script)

    assert paths.vibecrafted_home == (tmp_path / "vh-env").resolve()
    assert paths.operator_home == (tmp_path / "home-env").resolve()


def test_schedule_cli_status_reports_defaults_when_config_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _fake_script(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "operator-home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "vh"))

    exit_code = schedule_cli_main(["status", "--script-path", str(script)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["enabled"] is False
    assert payload["auto_execute"] is False
    assert payload["installed"] is False


def test_schedule_cli_install_refuses_without_founder_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _fake_script(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "operator-home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "vh"))

    exit_code = schedule_cli_main(["install", "--script-path", str(script)])

    assert exit_code == 1
    assert "enabled is false" in capsys.readouterr().err
