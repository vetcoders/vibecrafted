"""Automatic scheduling for `scripts/vibecrafted_housekeeping.py`, the
receipt-driven disk-space reclaim planner/executor for `VIBECRAFTED_HOME`.

The planner+executor already exists and is safe on its own (retention-days
aging, live-process protection, an exact `--confirm` token gate on deletion,
and receipts under `store/housekeeping/`); what was missing was any automatic
trigger for it. This module renders and installs a macOS LaunchAgent that
runs it on a recurring interval.

Two separate, Founder-owned opt-ins gate everything here:

- `[housekeeping].enabled` must be `true` before `install_housekeeping_schedule`
  will install anything at all.
- `[housekeeping].auto_execute` must *also* be `true` before the installed
  schedule ever appends `--execute --confirm <token>` to the scheduled run.
  Left at its default (`false`), an installed schedule only ever writes a
  read-only plan receipt -- it never deletes anything on its own.

Deleting data is a Founder-button action; this module only ever presses that
button when the Founder has explicitly told it to, in their own config file.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import plistlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .server_config import HousekeepingConfig, config_path, load_housekeeping_config
from .server_supervisor import (
    SupervisorError,
    _atomic_private_write,
    _ensure_owned_directory,
    _fsync_directory,
    _launch_domain,
    _launchctl,
    _validate_owned_regular_file,
)

HOUSEKEEPING_LAUNCH_AGENT_LABEL = "io.vetcoders.vibecrafted.housekeeping"


class HousekeepingScheduleError(RuntimeError):
    """Raised for any housekeeping-schedule render/install/uninstall failure."""


@dataclass(frozen=True)
class HousekeepingSchedulePaths:
    """Canonical filesystem locations the schedule reads from and writes to."""

    vibecrafted_home: Path
    operator_home: Path
    script_path: Path
    launch_agent_file: Path
    stdout_log: Path
    stderr_log: Path

    @classmethod
    def create(
        cls,
        *,
        vibecrafted_home: Path,
        operator_home: Path,
        script_path: Path,
    ) -> HousekeepingSchedulePaths:
        """Canonicalize the home directories and script path and derive the
        fixed LaunchAgent/log paths beneath them."""

        canonical_home = vibecrafted_home.expanduser().resolve()
        canonical_operator_home = operator_home.expanduser().resolve()
        log_dir = canonical_home / "store" / "housekeeping" / "logs"
        return cls(
            vibecrafted_home=canonical_home,
            operator_home=canonical_operator_home,
            script_path=script_path.expanduser(),
            launch_agent_file=(
                canonical_operator_home
                / "Library"
                / "LaunchAgents"
                / f"{HOUSEKEEPING_LAUNCH_AGENT_LABEL}.plist"
            ),
            stdout_log=log_dir / "schedule.stdout.log",
            stderr_log=log_dir / "schedule.stderr.log",
        )


def default_paths(
    *,
    script_path: Path,
    vibecrafted_home: Path | None = None,
    operator_home: Path | None = None,
) -> HousekeepingSchedulePaths:
    """Resolve `HousekeepingSchedulePaths` from explicit overrides or the
    same `VIBECRAFTED_HOME`/`HOME` environment the rest of the runtime uses."""

    home = vibecrafted_home or Path(
        os.environ.get("VIBECRAFTED_HOME", str(Path.home() / ".vibecrafted"))
    )
    owner = operator_home or Path(os.environ.get("HOME", str(Path.home())))
    return HousekeepingSchedulePaths.create(
        vibecrafted_home=home, operator_home=owner, script_path=script_path
    )


def _housekeeping_confirm_token(script_path: Path) -> str:
    """Dynamically import the standalone housekeeping script to read its
    `CONFIRM_TOKEN`, so the destructive-execute confirmation string has a
    single source of truth instead of a duplicated literal."""

    try:
        validated = _validate_owned_regular_file(script_path)
    except SupervisorError as exc:
        raise HousekeepingScheduleError(str(exc)) from exc
    spec = importlib.util.spec_from_file_location(
        "_vibecrafted_housekeeping_script", validated
    )
    if spec is None or spec.loader is None:
        raise HousekeepingScheduleError(f"cannot load housekeeping script: {validated}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise HousekeepingScheduleError(
            f"cannot import housekeeping script {validated}: {exc}"
        ) from exc
    token = getattr(module, "CONFIRM_TOKEN", None)
    if not isinstance(token, str) or not token:
        raise HousekeepingScheduleError(
            f"housekeeping script has no CONFIRM_TOKEN: {validated}"
        )
    return token


def render_housekeeping_launch_agent_plist(
    config: HousekeepingConfig,
    paths: HousekeepingSchedulePaths,
    *,
    python_executable: Path | None = None,
) -> bytes:
    """Pure-ish function (only ensures the plist/log directories exist):
    build the launchd plist bytes for the housekeeping schedule.

    `RunAtLoad` is always False -- the schedule only ever fires on its
    `StartInterval`, never at install or login time. `--execute --confirm
    <token>` is appended to `ProgramArguments` only when `config.auto_execute`
    is True; otherwise the scheduled run only ever plans."""

    interpreter = python_executable or Path(sys.executable)
    for directory in (paths.launch_agent_file.parent, paths.stdout_log.parent):
        _ensure_owned_directory(directory)
    arguments: list[str] = [
        str(interpreter),
        str(paths.script_path),
        "--home",
        str(paths.vibecrafted_home),
        "--retention-days",
        str(config.retention_days),
        "--json",
    ]
    if config.auto_execute:
        token = _housekeeping_confirm_token(paths.script_path)
        arguments += ["--execute", "--confirm", token]
    payload: dict[str, Any] = {
        "Label": HOUSEKEEPING_LAUNCH_AGENT_LABEL,
        "AssociatedBundleIdentifiers": ["io.vetcoders.vibecrafted"],
        "ProgramArguments": arguments,
        "StartInterval": max(1, config.interval_hours) * 3600,
        "RunAtLoad": False,
        "ProcessType": "Background",
        "StandardOutPath": str(paths.stdout_log),
        "StandardErrorPath": str(paths.stderr_log),
        "EnvironmentVariables": {
            "HOME": str(paths.operator_home),
            "VIBECRAFTED_HOME": str(paths.vibecrafted_home),
        },
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def install_housekeeping_schedule(
    config: HousekeepingConfig,
    paths: HousekeepingSchedulePaths,
    *,
    python_executable: Path | None = None,
    activate: bool = True,
) -> bool:
    """Require `config.enabled` (the Founder's own opt-in) and atomically
    write the rendered plist; returns whether the content actually changed.
    Best-effort `launchctl bootstrap` activation unless `activate=False`."""

    if not config.enabled:
        raise HousekeepingScheduleError(
            "[housekeeping].enabled is false; set it to true in "
            f"{config_path()} before installing the automatic schedule"
        )
    rendered = render_housekeeping_launch_agent_plist(
        config, paths, python_executable=python_executable
    )
    try:
        changed = _atomic_private_write(paths.launch_agent_file, rendered)
    except SupervisorError as exc:
        raise HousekeepingScheduleError(str(exc)) from exc
    if activate:
        _activate(paths)
    return changed


def uninstall_housekeeping_schedule(
    paths: HousekeepingSchedulePaths,
    *,
    deactivate: bool = True,
) -> bool:
    """Best-effort `launchctl bootout` then delete the LaunchAgent plist.
    Returns whether a plist was actually removed."""

    if deactivate:
        _deactivate(paths)
    path = paths.launch_agent_file
    if not path.exists() and not path.is_symlink():
        return False
    try:
        _validate_owned_regular_file(path, allow_symlink=False)
    except SupervisorError as exc:
        raise HousekeepingScheduleError(str(exc)) from exc
    path.unlink()
    _fsync_directory(path.parent)
    return True


def housekeeping_schedule_installed(paths: HousekeepingSchedulePaths) -> bool:
    """True when a (non-symlink, regular-file) LaunchAgent plist is present."""

    return (
        paths.launch_agent_file.is_file() and not paths.launch_agent_file.is_symlink()
    )


def _launch_target() -> str:
    """Fully-qualified launchd service target for the housekeeping schedule."""

    return f"{_launch_domain()}/{HOUSEKEEPING_LAUNCH_AGENT_LABEL}"


def _activate(paths: HousekeepingSchedulePaths) -> None:
    """Best-effort `launchctl bootstrap`; failures are swallowed since the
    plist alone is still correct and macOS will pick up the schedule after
    the Founder's next login regardless."""

    try:
        _launchctl(["bootstrap", _launch_domain(), str(paths.launch_agent_file)])
    except SupervisorError:
        pass


def _deactivate(paths: HousekeepingSchedulePaths) -> None:
    """Best-effort `launchctl bootout`; failures are swallowed (e.g. not
    currently loaded)."""

    try:
        _launchctl(["bootout", _launch_target()])
    except SupervisorError:
        pass


def schedule_cli_main(argv: Sequence[str] | None = None) -> int:
    """`install|uninstall|status` CLI entrypoint, invoked by
    `vibecrafted housekeeping schedule ...` via the main deck script."""

    args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="vibecrafted-housekeeping-schedule")
    parser.add_argument("action", choices=("install", "uninstall", "status"))
    parser.add_argument(
        "--script-path",
        required=True,
        help="Path to scripts/vibecrafted_housekeeping.py",
    )
    parser.add_argument("--home", help="Override VIBECRAFTED_HOME")
    namespace = parser.parse_args(args)

    script_path = Path(namespace.script_path)
    home = Path(namespace.home).expanduser() if namespace.home else None
    paths = default_paths(script_path=script_path, vibecrafted_home=home)
    config = load_housekeeping_config()

    if namespace.action == "status":
        print(
            json.dumps(
                {
                    "enabled": config.enabled,
                    "auto_execute": config.auto_execute,
                    "retention_days": config.retention_days,
                    "interval_hours": config.interval_hours,
                    "config_path": str(config_path()),
                    "installed": housekeeping_schedule_installed(paths),
                    "launch_agent_file": str(paths.launch_agent_file),
                },
                indent=2,
            )
        )
        return 0

    if namespace.action == "install":
        try:
            changed = install_housekeeping_schedule(config, paths)
        except HousekeepingScheduleError as exc:
            print(f"vibecrafted: {exc}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "installed": True,
                    "changed": changed,
                    "launch_agent_file": str(paths.launch_agent_file),
                    "auto_execute": config.auto_execute,
                    "interval_hours": config.interval_hours,
                },
                indent=2,
            )
        )
        return 0

    # uninstall
    removed = uninstall_housekeeping_schedule(paths)
    print(
        json.dumps(
            {"removed": removed, "launch_agent_file": str(paths.launch_agent_file)},
            indent=2,
        )
    )
    return 0
