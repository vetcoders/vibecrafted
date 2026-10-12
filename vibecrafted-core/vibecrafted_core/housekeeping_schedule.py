"""Supervisor-owned housekeeping scheduling.

[housekeeping].enabled and auto_execute remain independent Founder settings.
Install registers an exact script digest; it creates no daemon or LaunchAgent.
Legacy migration requires an explicit prior ownership digest and preserves config.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import plistlib
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .server_config import (
    HousekeepingConfig,
    ServerConfigError,
    config_path,
    load_housekeeping_config,
)
from .server_supervisor import (
    _atomic_private_write,
    _fsync_directory,
    _launch_domain,
    _launchctl,
    _read_owned_text,
    _sha256_file,
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


def render_housekeeping_launch_agent_plist(*args: Any, **kwargs: Any) -> bytes:
    """Retired: scheduling belongs to vc-server-supervisor, not another plist."""
    raise HousekeepingScheduleError(
        "separate housekeeping LaunchAgent retired; use supervisor scheduling"
    )


def _owner_file(paths: HousekeepingSchedulePaths) -> Path:
    return paths.vibecrafted_home / "server" / "housekeeping.owner.json"


def install_housekeeping_schedule(
    config: HousekeepingConfig,
    paths: HousekeepingSchedulePaths,
    *,
    python_executable: Path | None = None,
    activate: bool = True,
) -> bool:
    """Register the script for the supervisor; preserve both config switches."""
    if not config.enabled:
        raise HousekeepingScheduleError("[housekeeping].enabled is false")
    script = _validate_owned_regular_file(paths.script_path)
    payload = (
        json.dumps(
            {
                "schema": "vibecrafted.supervisor-housekeeping.v1",
                "owner": "vc-server-supervisor",
                "script": str(script),
                "sha256": _sha256_file(script),
            },
            sort_keys=True,
        ).encode()
        + b"\n"
    )
    return _atomic_private_write(_owner_file(paths), payload)


def uninstall_housekeeping_schedule(
    paths: HousekeepingSchedulePaths, *, deactivate: bool = True
) -> bool:
    """Remove only our script registration. Never rewrite Founder config."""
    path = _owner_file(paths)
    if not path.exists() and not path.is_symlink():
        return False
    _validate_owned_regular_file(path, allow_symlink=False)
    owner = json.loads(path.read_text())
    if (
        owner.get("schema") != "vibecrafted.supervisor-housekeeping.v1"
        or owner.get("owner") != "vc-server-supervisor"
    ):
        raise HousekeepingScheduleError("foreign housekeeping registration retained")
    path.unlink()
    _fsync_directory(path.parent)
    return True


def housekeeping_schedule_installed(paths: HousekeepingSchedulePaths) -> bool:
    return _owner_file(paths).is_file() and not _owner_file(paths).is_symlink()


def retire_legacy_schedule(
    paths: HousekeepingSchedulePaths, *, expected_sha256: str, deactivate: bool = False
) -> bool:
    """Explicit migration with an exact prior installer digest; names are insufficient."""
    path = paths.launch_agent_file
    if not path.exists() and not path.is_symlink():
        return False
    _validate_owned_regular_file(path, allow_symlink=False)
    if not expected_sha256 or _sha256_file(path) != expected_sha256:
        raise HousekeepingScheduleError(
            "legacy schedule ownership digest mismatch; retained"
        )
    payload = plistlib.loads(path.read_bytes())
    if payload.get("Label") != HOUSEKEEPING_LAUNCH_AGENT_LABEL:
        raise HousekeepingScheduleError("foreign schedule retained")
    if deactivate:
        result = _launchctl(
            ["bootout", f"{_launch_domain()}/{HOUSEKEEPING_LAUNCH_AGENT_LABEL}"]
        )
        if result.returncode != 0:
            raise HousekeepingScheduleError(
                "legacy schedule bootout not confirmed; retained"
            )
    if _sha256_file(path) != expected_sha256:
        raise HousekeepingScheduleError(
            "legacy schedule changed during migration; retained"
        )
    path.unlink()
    _fsync_directory(path.parent)
    return True


class SupervisorHousekeeping:
    """In-process schedule. Config is re-read; errors never become permission."""

    def __init__(
        self,
        supervisor_config: Any,
        *,
        clock: Any = time.monotonic,
        background: bool = False,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.supervisor_config = supervisor_config
        self.clock = clock
        self.next_run: float | None = None
        self.last_config: HousekeepingConfig | None = None
        self.error: str | None = None
        self.background = background
        self.stop_event = stop_event or threading.Event()
        self.thread: threading.Thread | None = None
        self._owner_offset = 0

    def tick(self) -> bool:
        if self.background:
            if self.stop_event.is_set() or (self.thread and self.thread.is_alive()):
                return False
            self.thread = threading.Thread(
                target=self._tick_sync,
                name="vc-server-supervisor-housekeeping",
                daemon=True,
            )
            self.thread.start()
            return True
        return self._tick_sync()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=45)
            if self.thread.is_alive():
                raise HousekeepingScheduleError(
                    "housekeeping did not drain its bounded observation"
                )

    def _tick_sync(self) -> bool:
        try:
            config = load_housekeeping_config(
                self.supervisor_config.config_file,
                operator_home=self.supervisor_config.paths.operator_home,
            )
        except ServerConfigError as exc:
            self.error = f"housekeeping config deferred: {type(exc).__name__}: {exc}"
            return False
        now = self.clock()
        if config != self.last_config:
            self.last_config = config
            self.next_run = now + config.interval_hours * 3600
        if not config.enabled or self.next_run is None:
            return False
        home = self.supervisor_config.paths.home
        descriptor = home / "server" / "housekeeping.owner.json"
        try:
            raw = _read_owned_text(descriptor)
            if raw is None:
                raise HousekeepingScheduleError(
                    "no verified housekeeping script registration"
                )
            owner = json.loads(raw)
            if (
                owner.get("schema") != "vibecrafted.supervisor-housekeeping.v1"
                or owner.get("owner") != "vc-server-supervisor"
            ):
                raise HousekeepingScheduleError(
                    "invalid housekeeping script registration"
                )
            script = _validate_owned_regular_file(Path(owner["script"]))
            if _sha256_file(script) != owner.get("sha256"):
                raise HousekeepingScheduleError(
                    "housekeeping script changed; re-register before execution"
                )
            self.error = None
            if config.auto_execute:
                self._retire_accepted_caches()
            if now < self.next_run:
                return False
            self.next_run = now + config.interval_hours * 3600
            spec = importlib.util.spec_from_file_location(
                "_vc_supervisor_housekeeping", script
            )
            if spec is None or spec.loader is None:
                raise HousekeepingScheduleError("cannot load housekeeping script")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            plan = module.build_plan(
                home, {"transient", "build-cache"}, config.retention_days
            )
            module.write_receipt(home, "plans", plan)
            if config.auto_execute:
                module.write_receipt(
                    home, "executions", module.execute_plan(home, plan)
                )
            self.error = None
        except (Exception, SystemExit) as exc:
            import logging

            logging.getLogger(__name__).exception(
                "vc-server-supervisor: housekeeping execution deferred"
            )
            self.error = f"housekeeping deferred: {type(exc).__name__}: {exc}"
        _atomic_private_write(
            home / "server" / "housekeeping.status.json",
            json.dumps(
                {
                    "owner": "vc-server-supervisor",
                    "enabled": config.enabled,
                    "auto_execute": config.auto_execute,
                    "error": self.error,
                },
                sort_keys=True,
            ).encode()
            + b"\n",
        )
        return self.error is None

    def _retire_accepted_caches(self) -> None:
        """Policy B: a bounded pass after acceptance, no warm-cache warehouse."""
        from .cache_lifecycle import CacheDeferred, accepted_owner, clean_cache

        home = self.supervisor_config.paths.home
        owners = home / "store" / "housekeeping" / "owners"
        if not owners.is_dir() or owners.is_symlink():
            return
        outcomes = []
        entries = sorted(owners.glob("*.json"))
        if not entries:
            return
        offset = self._owner_offset % len(entries)
        batch = (entries[offset:] + entries[:offset])[:256]
        self._owner_offset = (offset + len(batch)) % len(entries)
        for receipt in batch:
            if self.stop_event.is_set():
                break
            try:
                raw = _read_owned_text(receipt)
                if raw is None:
                    continue
                owner = json.loads(raw)
                path = Path(owner["path"])
                if not path.exists() and not path.is_symlink():
                    continue
                accepted_owner(home, path)
                from .cache_lifecycle import observe_unused

                def observe(candidate: Path) -> None:
                    if self.stop_event.is_set():
                        raise CacheDeferred("supervisor-stopping")
                    observe_unused(candidate)

                result = clean_cache(home, path, observe=observe)
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                subprocess.TimeoutExpired,
                CacheDeferred,
            ) as exc:
                result = f"deferred:{type(exc).__name__}:{exc}"
            outcomes.append({"receipt": str(receipt), "result": result})
        if outcomes:
            _atomic_private_write(
                home / "server" / "cache-retirement.status.json",
                json.dumps(
                    {"owner": "vc-server-supervisor", "outcomes": outcomes}
                ).encode()
                + b"\n",
            )


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
                    "owner": "vc-server-supervisor",
                    "registration": str(_owner_file(paths)),
                    "legacy_launch_agent_file": str(paths.launch_agent_file),
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
                    "owner": "vc-server-supervisor",
                    "registration": str(_owner_file(paths)),
                    "legacy_launch_agent_file": str(paths.launch_agent_file),
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
            {"removed": removed, "registration": str(_owner_file(paths))},
            indent=2,
        )
    )
    return 0
