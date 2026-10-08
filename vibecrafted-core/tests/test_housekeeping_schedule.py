from __future__ import annotations

import json
import plistlib
import subprocess
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


def test_render_plist_omits_execute_without_auto_execute(tmp_path: Path) -> None:
    script = _fake_script(tmp_path)
    paths = _paths(tmp_path, script)
    config = HousekeepingConfig(enabled=True, auto_execute=False, retention_days=9)

    rendered = render_housekeeping_launch_agent_plist(config, paths)
    payload = plistlib.loads(rendered)

    assert payload["Label"] == schedule.HOUSEKEEPING_LAUNCH_AGENT_LABEL
    assert payload["RunAtLoad"] is False
    assert payload["StartInterval"] == 24 * 3600
    arguments = payload["ProgramArguments"]
    assert str(script) in arguments
    assert "--retention-days" in arguments
    assert arguments[arguments.index("--retention-days") + 1] == "9"
    assert "--execute" not in arguments
    assert "--confirm" not in arguments
    assert FAKE_CONFIRM_TOKEN not in arguments


def test_render_plist_includes_confirm_token_only_with_auto_execute(
    tmp_path: Path,
) -> None:
    script = _fake_script(tmp_path)
    paths = _paths(tmp_path, script)
    config = HousekeepingConfig(enabled=True, auto_execute=True, interval_hours=6)

    rendered = render_housekeeping_launch_agent_plist(config, paths)
    payload = plistlib.loads(rendered)

    arguments = payload["ProgramArguments"]
    assert payload["StartInterval"] == 6 * 3600
    assert "--execute" in arguments
    assert "--confirm" in arguments
    assert arguments[arguments.index("--confirm") + 1] == FAKE_CONFIRM_TOKEN


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
    assert paths.launch_agent_file.is_file()


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


def test_activate_and_deactivate_swallow_launchctl_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _fake_script(tmp_path)
    paths = _paths(tmp_path, script)

    def _boom(args: object) -> subprocess.CompletedProcess[str]:
        raise schedule.SupervisorError("no launchctl in this sandbox", 78)

    monkeypatch.setattr(schedule, "_launchctl", _boom)

    # Neither call should raise, even though the fake launchctl always fails.
    schedule._activate(paths)
    schedule._deactivate(paths)


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
