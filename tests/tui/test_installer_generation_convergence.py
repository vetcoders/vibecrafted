"""Convergent install-source: stale identity, bundle service, crash-safe publish.

These tests stage the real distribution payload and the real deck bytes. They
do not fabricate a `vibecrafted` wrapper that prints canned status. Launchd is
isolated (this suite must never mutate the operator label); the service JSON
schema is the real `service status --json` contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import vetcoders_install as installer
from tests.tui.test_staged_tools_sync import (
    REPO_ROOT,
    _mock_runtime_launchd_gate,
    _write_complete_source,
    _write_executable,
    _write_valid_runtime_generation,
)


@pytest.fixture(autouse=True)
def _isolate_fixed_runtime_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        installer,
        "_canonical_operator_home",
        lambda: Path.home().resolve(strict=False),
    )
    monkeypatch.setattr(installer, "_runtime_loaded_service_home", lambda: None)


def _real_deck() -> str:
    return (REPO_ROOT / "scripts" / "vibecrafted").read_text(encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_identity_plist(
    home: Path,
    shared_home: Path,
    *,
    supervisor: Path,
    launcher: Path,
    supervisor_sha: str,
    launcher_sha: str,
) -> Path:
    path = (
        home / "Library" / "LaunchAgents" / f"{installer._RUNTIME_SERVICE_LABEL}.plist"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": installer._RUNTIME_SERVICE_LABEL,
        "ProgramArguments": [
            str(supervisor),
            "run",
            "--launcher",
            str(launcher),
            "--home",
            str(shared_home),
            "--host",
            "127.0.0.1",
            "--port",
            "3024",
        ],
        "EnvironmentVariables": {
            "HOME": str(home),
            "VIBECRAFTED_HOME": str(shared_home),
            "VIBECRAFTED_RUNTIME_HOME": str(home / ".local" / "share" / "vibecrafted"),
            "VIBECRAFTED_SERVER_SUPERVISOR_PATH": str(supervisor),
            "VIBECRAFTED_SERVER_SUPERVISOR_SHA256": supervisor_sha,
            "VIBECRAFTED_SERVER_LAUNCHER_SHA256": launcher_sha,
        },
    }
    path.write_bytes(plistlib.dumps(payload, sort_keys=True))
    path.chmod(0o600)
    return path


def _isolate_darwin_install(
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    shared_home: Path,
    tools: Path,
    plist: Path,
) -> dict[str, bool]:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setenv(
        "VIBECRAFTED_RUNTIME_HOME", str(home / ".local" / "share" / "vibecrafted")
    )
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / ".local" / "bin"))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer, "_runtime_launch_agent_path", lambda: plist)
    monkeypatch.setattr(installer, "_darwin_process_ids", lambda: ())
    monkeypatch.setattr(
        installer, "_assert_runtime_launchd_job_owned", lambda _home: True
    )
    monkeypatch.setattr(
        installer, "_bootout_owned_runtime_launchd_job", lambda _home: True
    )
    return _mock_runtime_launchd_gate(monkeypatch)


def test_stale_launcher_running_pair_is_reconcilable_not_foreign() -> None:
    """pair_healthy=false + Server/Guardian RUNNING is drainable ownership."""
    stale = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=4242,
    )
    assert stale.reclaimable
    assert stale.stale_identity
    assert stale.needs_drain
    assert not stale.healthy


def test_runtime_snapshot_drains_stale_running_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / "vibecrafted"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    payload = {
        "installed": True,
        "loaded": True,
        "supervisor_live": True,
        "supervisor_verified": True,
        "supervisor_service_managed": True,
        "build_current": False,
        "pair_healthy": False,
        "supervisor_pid": 4242,
    }
    monkeypatch.setattr(
        installer, "_runtime_service_launcher", lambda _shared_home: launcher
    )

    def service_command(
        _launcher: Path, _shared_home: Path, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        if arguments == ("service", "status", "--json"):
            return subprocess.CompletedProcess(
                list(arguments), 1, json.dumps(payload) + "\n", ""
            )
        if arguments == ("status",):
            return subprocess.CompletedProcess(
                list(arguments),
                0,
                "Supervision: LAUNCHD (installed=yes, loaded=yes, supervisor PID 4242)\n"
                "Server: RUNNING\n"
                "Guardian: RUNNING\n",
                "",
            )
        raise AssertionError(arguments)

    monkeypatch.setattr(installer, "_run_runtime_service_command", service_command)
    snapshot = installer._runtime_service_snapshot(tmp_path)
    assert snapshot is not None
    assert snapshot[1].reclaimable
    assert snapshot[2] == "running"


def test_launch_agent_identity_detects_uv_overwrite(tmp_path: Path) -> None:
    supervisor = tmp_path / "vc-server-supervisor"
    launcher = tmp_path / "vibecrafted"
    supervisor.write_bytes(b"old-supervisor\n")
    launcher.write_bytes(b"old-launcher\n")
    backup = installer._RuntimeLaunchAgentBackup(
        tmp_path / "label.plist",
        plistlib.dumps(
            {
                "Label": installer._RUNTIME_SERVICE_LABEL,
                "ProgramArguments": [
                    str(supervisor),
                    "run",
                    "--launcher",
                    str(launcher),
                ],
                "EnvironmentVariables": {
                    "VIBECRAFTED_SERVER_SUPERVISOR_PATH": str(supervisor),
                    "VIBECRAFTED_SERVER_SUPERVISOR_SHA256": _sha256(supervisor),
                    "VIBECRAFTED_SERVER_LAUNCHER_SHA256": _sha256(launcher),
                },
            },
            sort_keys=True,
        ),
        0o600,
        ("--host", "127.0.0.1", "--port", "3024"),
    )
    assert installer._launch_agent_identity_matches_published_binaries(tmp_path, backup)
    supervisor.write_bytes(b"new-supervisor-after-uv\n")
    assert not installer._launch_agent_identity_matches_published_binaries(
        tmp_path, backup
    )


def test_bundle_service_without_current_converges_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """make install-source on a bundle-hosted service must not FATAL, twice."""
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    launcher = home / ".local" / "bin" / "vibecrafted"
    supervisor = home / ".local" / "bin" / "vc-server-supervisor"
    current = tools / "vibecrafted-current"
    plist = (
        home / "Library" / "LaunchAgents" / f"{installer._RUNTIME_SERVICE_LABEL}.plist"
    )
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher=_real_deck(),
        service_lock_contract=False,
    )
    _write_executable(launcher, _real_deck())
    _write_executable(supervisor, "#!/bin/sh\nexit 0\n")
    _write_identity_plist(
        home,
        shared_home,
        supervisor=supervisor,
        launcher=launcher,
        supervisor_sha=_sha256(supervisor),
        launcher_sha=_sha256(launcher),
    )
    gate = _isolate_darwin_install(monkeypatch, home, shared_home, tools, plist)

    healthy = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=9191,
    )
    quiescent = installer._RuntimeServiceStatus(
        installed=True,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    mode = "healthy"
    events: list[str] = []

    def snapshot(_shared_home: Path):
        if mode == "healthy":
            return launcher, healthy, "running"
        if mode == "stopped":
            return launcher, quiescent, "stopped"
        return launcher, healthy, "running"

    monkeypatch.setattr(installer, "_runtime_service_snapshot", snapshot)

    def service_command(
        _launcher: Path, _shared_home: Path, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        nonlocal mode
        if arguments == ("service", "stop"):
            events.append("service stop")
            mode = "stopped"
            return subprocess.CompletedProcess(list(arguments), 0, "", "")
        if arguments[:2] == ("service", "install"):
            events.append("service install")
            mode = "healthy"
            supervisor.write_bytes(supervisor.read_bytes() + b"\n# published\n")
            return subprocess.CompletedProcess(list(arguments), 0, "", "")
        raise AssertionError(arguments)

    monkeypatch.setattr(installer, "_run_runtime_service_command", service_command)
    child = (
        "from pathlib import Path; import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import vetcoders_install as v\n"
        "v.refresh_current_tools(Path(sys.argv[2]), Path(sys.argv[3]), mirror=True)\n"
    )
    argv = [
        sys.executable,
        "-c",
        child,
        str(REPO_ROOT / "scripts"),
        str(source),
        str(shared_home),
    ]

    first = installer.run_with_tools_install_lease(
        shared_home, argv, service_policy="ensure"
    )
    assert first == 0
    assert "service stop" in events
    assert "service install" in events
    assert current.is_symlink()
    assert gate["disabled"] is False
    handoff = installer._read_tools_handoff(shared_home)
    assert handoff is not None and handoff["state"] == "complete"

    events.clear()
    mode = "healthy"
    second = installer.run_with_tools_install_lease(
        shared_home, argv, service_policy="ensure"
    )
    assert second == 0
    assert "service stop" in events
    assert "service install" in events
    second_handoff = installer._read_tools_handoff(shared_home)
    assert second_handoff is not None and second_handoff["state"] == "complete"


def test_preserve_policy_reconciles_after_uv_overwrites_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Death-window: uv publishes a new supervisor while the plist is stale."""
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    supervisor = home / ".local" / "bin" / "vc-server-supervisor"
    plist = (
        home / "Library" / "LaunchAgents" / f"{installer._RUNTIME_SERVICE_LABEL}.plist"
    )
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher=_real_deck(),
        service_lock_contract=False,
    )
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(launcher, _real_deck())
    _write_executable(supervisor, "#!/bin/sh\necho old\n")
    _write_identity_plist(
        home,
        shared_home,
        supervisor=supervisor,
        launcher=launcher,
        supervisor_sha=_sha256(supervisor),
        launcher_sha=_sha256(launcher),
    )
    gate = _isolate_darwin_install(monkeypatch, home, shared_home, tools, plist)

    quiescent = installer._RuntimeServiceStatus(
        installed=True,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    healthy = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=7777,
    )
    mode = "stopped"
    events: list[str] = []
    monkeypatch.setattr(
        installer,
        "_runtime_service_snapshot",
        lambda _home: (
            (launcher, healthy, "running")
            if mode == "healthy"
            else (launcher, quiescent, "stopped")
        ),
    )

    def service_command(
        _launcher: Path, _shared_home: Path, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        nonlocal mode
        if arguments[:2] == ("service", "install"):
            events.append("service install")
            mode = "healthy"
            return subprocess.CompletedProcess(list(arguments), 0, "", "")
        raise AssertionError(arguments)

    monkeypatch.setattr(installer, "_run_runtime_service_command", service_command)

    child = (
        "from pathlib import Path; import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import vetcoders_install as v\n"
        "v.refresh_current_tools(Path(sys.argv[2]), Path(sys.argv[3]), mirror=True)\n"
        "Path(sys.argv[4]).write_bytes(b'new-supervisor-after-uv\\n')\n"
    )
    result = installer.run_with_tools_install_lease(
        shared_home,
        [
            sys.executable,
            "-c",
            child,
            str(REPO_ROOT / "scripts"),
            str(source),
            str(shared_home),
            str(supervisor),
        ],
        service_policy="preserve",
    )
    assert result == 0
    assert events == ["service install"]
    assert gate["disabled"] is False
    assert current.resolve() != old_target.resolve()


def test_stale_generation_argv_is_retired_keep_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keep = tmp_path / "tools" / "vibecrafted-generation-new"
    stale_bin = tmp_path / "tools" / "vibecrafted-generation-old" / "bin" / "vc-server"
    keep.mkdir(parents=True)
    stale_bin.parent.mkdir(parents=True)
    stale_bin.write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer, "vibecrafted_tools_home", lambda: tmp_path / "tools")
    monkeypatch.setattr(
        installer,
        "vibecrafted_runtime_home",
        lambda: tmp_path / "unrelated-runtime",
    )
    birth = ("token", os.geteuid(), 8)
    stale = installer._RetiredVcFrameProcess(
        pid=40404, birth=birth, argv=(str(stale_bin),)
    )
    keep_proc = installer._RetiredVcFrameProcess(
        pid=40505,
        birth=birth,
        argv=(str(keep / "bin" / "vc-server"),),
    )
    agent = installer._RetiredVcFrameProcess(
        pid=40606,
        birth=birth,
        argv=(str(stale_bin.parent / "claude"), "--print"),
    )
    monkeypatch.setattr(installer, "_darwin_caller_ancestor_pids", lambda: frozenset())

    def birth_of(pid: int):
        return birth

    def argv_of(pid: int, pointer_size: int = 8):
        mapping = {40404: stale.argv, 40505: keep_proc.argv, 40606: agent.argv}
        return mapping[pid]

    monkeypatch.setattr(installer, "_darwin_process_birth", birth_of)
    monkeypatch.setattr(installer, "_darwin_process_arguments", argv_of)
    terminated: list[int] = []

    def terminate(records, *, timeout_seconds: float = 5.0):
        terminated.extend(record.pid for record in records)

    monkeypatch.setattr(installer, "_terminate_owned_runtime_processes", terminate)
    # After terminate, census is empty.
    calls = {"n": 0}

    def process_ids():
        calls["n"] += 1
        return () if calls["n"] > 1 else (40404, 40505, 40606)

    monkeypatch.setattr(installer, "_darwin_process_ids", process_ids)

    installer._retire_stale_framework_generations(
        tmp_path / "home" / ".vibecrafted",
        keep_generation=keep,
        keep_pids=(40505,),
    )
    assert terminated == [40404]


def test_missing_supervisor_entrypoint_exit_code_is_ex_config() -> None:
    launcher = (REPO_ROOT / "scripts" / "vibecrafted").read_text(encoding="utf-8")
    resolver = launcher.split("_server_supervisor_binary() {", 1)[1].split("\n}", 1)[0]
    assert "command -v vc-server-supervisor" in resolver
    assert "uv tool dir" in resolver
    assert "XDG_BIN_HOME" in resolver
    assert "$HOME/.local/bin/vc-server-supervisor" not in resolver
