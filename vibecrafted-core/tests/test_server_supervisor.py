from __future__ import annotations

import fcntl
import json
import os
import plistlib
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from vibecrafted_core import server_supervisor as supervisor


def _executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path.resolve()


def _config(tmp_path: Path, launcher: Path) -> supervisor.SupervisorConfig:
    operator_home = tmp_path / "operator"
    home = operator_home / ".vibecrafted"
    runtime_home = operator_home / ".local" / "share" / "vibecrafted"
    return supervisor.SupervisorConfig(
        paths=supervisor.SupervisorPaths.create(
            home=home.resolve(),
            runtime_home=runtime_home.resolve(),
            operator_home=operator_home.resolve(),
        ),
        launcher=launcher.resolve(),
        host="127.0.0.1",
        port=3024,
        interval=0.05,
        maximum_backoff=0.2,
        command_timeout=2,
    )


def _managed_probe(
    config: supervisor.SupervisorConfig,
    *,
    pid: int,
    service_managed: bool = True,
) -> supervisor.SupervisorProbe:
    identity = supervisor._installed_service_identity(config.paths)
    assert identity is not None
    return supervisor.SupervisorProbe(
        True,
        True,
        pid,
        service_managed,
        "supervisor",
        str(identity.executable),
        identity.executable_sha256,
        identity.runtime_sha256,
        identity.build_version,
        identity.launcher_sha256,
    )


def test_supervisor_version_mismatch_names_both_public_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    monkeypatch.setattr(supervisor, "PACKAGE_VERSION", "3.6.0+gactual")

    with pytest.raises(
        supervisor.SupervisorError,
        match=(
            "supervisor package version '3.6.0\\+gactual' differs from the "
            "installed LaunchAgent '3.6.0\\+gexpected'"
        ),
    ):
        supervisor._supervisor_identity(
            supervisor_binary,
            launcher=launcher,
            expected_version="3.6.0+gexpected",
        )


def test_trusted_system_owner_is_read_only_and_explicit() -> None:
    regular_executable = stat.S_IFREG | 0o755
    group_writable_executable = stat.S_IFREG | 0o775
    world_writable_executable = stat.S_IFREG | 0o757

    assert supervisor._file_owner_is_trusted(
        os.getuid(),
        world_writable_executable,
        allow_root_owned=False,
    )
    assert supervisor._file_owner_is_trusted(
        0,
        regular_executable,
        allow_root_owned=True,
    )
    if os.getuid() != 0:
        assert not supervisor._file_owner_is_trusted(
            0,
            regular_executable,
            allow_root_owned=False,
        )
        assert not supervisor._file_owner_is_trusted(
            0,
            group_writable_executable,
            allow_root_owned=True,
        )
        assert not supervisor._file_owner_is_trusted(
            0,
            world_writable_executable,
            allow_root_owned=True,
        )


def _launchctl_job_snapshot(
    config: supervisor.SupervisorConfig,
    *,
    plist: Path | None = None,
    program: Path | None = None,
    supervisor_path: Path | None = None,
    home: Path | None = None,
    runtime_home: Path | None = None,
    operator_home: Path | None = None,
) -> str:
    identity = supervisor._installed_service_identity(config.paths)
    assert identity is not None
    loaded_program = program or identity.executable
    environment_program = supervisor_path or identity.executable
    return f"""gui/{os.getuid()}/{supervisor.LAUNCH_AGENT_LABEL} = {{
    path = {plist or config.paths.launch_agent_file}
    type = LaunchAgent
    state = running

    program = {loaded_program}
    inherited environment = {{
        HOME => /ignored/inherited/home
    }}
    environment = {{
        VIBECRAFTED_SERVER_SUPERVISOR_PATH => {environment_program}
        VIBECRAFTED_HOME => {home or config.paths.home}
        VIBECRAFTED_RUNTIME_HOME => {runtime_home or config.paths.runtime_home}
        HOME => {operator_home or config.paths.operator_home}
        XPC_SERVICE_NAME => {supervisor.LAUNCH_AGENT_LABEL}
    }}
}}
"""


def test_launchctl_start_diagnostics_allowlists_only_process_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = """gui/501/io.vetcoders.vibecrafted.server = {
    state = waiting
    runs = 1
    last exit code = 78
    environment = {
        SECRET_TOKEN => do-not-leak
    }
}
"""
    monkeypatch.setattr(
        supervisor,
        "_launchctl",
        lambda args: subprocess.CompletedProcess(args, 0, payload, ""),
    )

    detail, process_observed = supervisor._launchctl_start_diagnostics()

    assert detail == ("launchctl(state=waiting,pid=-,runs=1,last-exit-code=78)")
    assert process_observed
    assert "SECRET_TOKEN" not in detail
    assert "do-not-leak" not in detail


def test_bounded_service_stderr_reports_only_new_redacted_tail(
    tmp_path: Path,
) -> None:
    stderr_log = tmp_path / "server" / "supervisor.stderr.log"
    stderr_log.parent.mkdir(parents=True)
    stderr_log.write_text("old diagnostic\n", encoding="utf-8")
    cursor = supervisor._service_stderr_cursor(stderr_log)
    with stderr_log.open("a", encoding="utf-8") as stream:
        stream.write(
            "vc-server-supervisor: runtime rejected "
            "SECRET_TOKEN => do-not-leak Bearer bearer-secret\n"
        )

    detail = supervisor._bounded_service_stderr(stderr_log, cursor)

    assert detail is not None
    assert detail.startswith("vc-server-supervisor: runtime rejected")
    assert "SECRET_TOKEN=<redacted>" in detail
    assert "Bearer <redacted>" in detail
    assert "do-not-leak" not in detail
    assert "bearer-secret" not in detail
    assert "old diagnostic" not in detail
    assert len(detail) <= 512


def test_plistlib_renderer_preserves_metacharacters_without_xml_injection(
    tmp_path: Path,
) -> None:
    special = tmp_path / 'owned & <path> "quoted"'
    launcher = _executable(special / "vibecrafted")
    supervisor_binary = _executable(special / "vc-server-supervisor")
    config = _config(special, launcher)

    rendered = supervisor.render_launch_agent_plist(
        config,
        supervisor_binary=supervisor_binary,
    )
    payload = plistlib.loads(rendered)

    assert payload["Label"] == supervisor.LAUNCH_AGENT_LABEL
    assert payload["ProgramArguments"][0] == str(supervisor_binary)
    arguments = payload["ProgramArguments"]
    assert arguments[arguments.index("--supervisor-bin") + 1] == str(supervisor_binary)
    assert arguments[arguments.index("--launcher") + 1] == str(launcher)
    assert arguments[arguments.index("--home") + 1] == str(config.paths.home)
    assert arguments[
        arguments.index("--expected-supervisor-sha256") + 1
    ] == supervisor._sha256_file(supervisor_binary)
    assert (
        arguments[arguments.index("--expected-build-version") + 1]
        == supervisor.PACKAGE_VERSION
    )
    assert arguments[
        arguments.index("--expected-launcher-sha256") + 1
    ] == supervisor._sha256_file(launcher)
    assert arguments[
        arguments.index("--expected-runtime-sha256") + 1
    ] == supervisor._sha256_file(Path(supervisor.__file__).resolve())
    assert "--interval" not in arguments
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    assert set(payload["EnvironmentVariables"]) == {
        "HOME",
        "PATH",
        "VIBECRAFTED_HOME",
        "VIBECRAFTED_RUNTIME_HOME",
        "VC_SERVER_PUBLIC_URL",
        "VIBECRAFTED_SERVER_CONFIG",
        "VIBECRAFTED_SERVER_SERVICE",
        "VIBECRAFTED_SERVER_SUPERVISOR_PATH",
        "VIBECRAFTED_SERVER_SUPERVISOR_SHA256",
        "VIBECRAFTED_SERVER_SUPERVISOR_RUNTIME_SHA256",
        "VIBECRAFTED_SERVER_SUPERVISOR_VERSION",
        "VIBECRAFTED_SERVER_LAUNCHER_SHA256",
        "VIBECRAFTED_TRIAGE_RUN",
    }
    assert b"&amp;" in rendered
    assert b"<path>" not in rendered


def test_launch_agent_carries_the_installing_path_and_active_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """launchd hands a job only its plist PATH. A frozen system-only PATH hid
    Homebrew/cargo/npm bins from the supervisor and every process it spawned, so
    `#!/usr/bin/env node` agent CLIs exited 127."""

    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    generation = config.paths.runtime_home / "releases" / "9.9.9"
    (generation / "bin").mkdir(parents=True)
    config.paths.runtime_home.mkdir(parents=True, exist_ok=True)
    (config.paths.runtime_home / "active.json").write_text(
        json.dumps(
            {
                "schema": "vibecrafted.active-runtime.v1",
                "version": "9.9.9",
                "runtime_root": str(generation),
                "app_root": "/Applications/Vibecrafted.app",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin:/bin")

    payload = plistlib.loads(
        supervisor.render_launch_agent_plist(
            config,
            supervisor_binary=supervisor_binary,
        )
    )

    assert payload["EnvironmentVariables"]["PATH"] == (
        f"{generation / 'bin'}:/opt/homebrew/bin:/usr/bin:/bin"
    )
    assert payload["EnvironmentVariables"]["VIBECRAFTED_RUNTIME_ROOT"] == str(
        generation
    )
    assert supervisor._child_environment(config.paths)[
        "VIBECRAFTED_RUNTIME_ROOT"
    ] == str(generation)

    # No active-runtime receipt: the installing PATH still survives whole.
    (config.paths.runtime_home / "active.json").unlink()
    payload = plistlib.loads(
        supervisor.render_launch_agent_plist(
            config,
            supervisor_binary=supervisor_binary,
        )
    )

    assert payload["EnvironmentVariables"]["PATH"] == "/opt/homebrew/bin:/usr/bin:/bin"
    assert "VIBECRAFTED_RUNTIME_ROOT" not in payload["EnvironmentVariables"]
    assert "VIBECRAFTED_RUNTIME_ROOT" not in supervisor._child_environment(config.paths)


def test_launch_agent_path_drops_empty_and_relative_segments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plist PATH is frozen for the life of the LaunchAgent. An empty
    segment (leading/trailing ':' or '::') and relative entries ('.', 'bin')
    are implicit current-directory lookups — they must not survive into a
    long-lived launchd job, nor into supervised children."""

    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    monkeypatch.setenv("PATH", ":/opt/homebrew/bin::.:bin:/usr/bin:/opt/homebrew/bin:")

    payload = plistlib.loads(
        supervisor.render_launch_agent_plist(
            config, supervisor_binary=supervisor_binary
        )
    )
    assert payload["EnvironmentVariables"]["PATH"] == "/opt/homebrew/bin:/usr/bin"

    child = supervisor._child_environment(config.paths)["PATH"].split(os.pathsep)
    assert "" not in child
    assert "." not in child
    assert "bin" not in child
    assert child.count("/opt/homebrew/bin") == 1


def test_child_environment_keeps_the_inherited_path_behind_canonical_bins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    config = _config(tmp_path, launcher)
    monkeypatch.setenv("PATH", f"/opt/homebrew/bin:{tmp_path}/.cargo/bin:/usr/bin")

    path = supervisor._child_environment(config.paths)["PATH"].split(os.pathsep)

    assert path[0] == f"{config.paths.operator_home}/.local/bin"
    assert f"{tmp_path}/.cargo/bin" in path
    assert path.index("/opt/homebrew/bin") < path.index(f"{tmp_path}/.cargo/bin")
    assert len(path) == len(set(path))


def test_launch_agent_propagates_terminal_triage_kill_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    monkeypatch.setenv("VIBECRAFTED_TRIAGE_RUN", "0")

    payload = plistlib.loads(
        supervisor.render_launch_agent_plist(
            config,
            supervisor_binary=supervisor_binary,
        )
    )

    assert payload["EnvironmentVariables"]["VIBECRAFTED_TRIAGE_RUN"] == "0"


def test_service_install_is_idempotent_and_refuses_symlink_destination(
    tmp_path: Path,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)

    assert supervisor.install_service(
        config,
        supervisor_binary=supervisor_binary,
    )
    first = config.paths.launch_agent_file.read_bytes()
    assert not supervisor.install_service(
        config,
        supervisor_binary=supervisor_binary,
    )
    assert config.paths.launch_agent_file.read_bytes() == first
    assert config.paths.launch_agent_file.stat().st_mode & 0o777 == 0o600

    config.paths.launch_agent_file.unlink()
    decoy = tmp_path / "decoy.plist"
    decoy.write_text("untouched", encoding="utf-8")
    config.paths.launch_agent_file.symlink_to(decoy)
    with pytest.raises(supervisor.SupervisorError, match="refusing to replace"):
        supervisor.install_service(
            config,
            supervisor_binary=supervisor_binary,
        )
    assert decoy.read_text(encoding="utf-8") == "untouched"


def test_foreground_supervisor_lock_and_receipt_are_truthful(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle_log = tmp_path / "lifecycle.log"
    launcher = _executable(
        tmp_path / "bin" / "vibecrafted",
        f"""#!/bin/sh
printf '%s\n' "$2" >> {str(lifecycle_log)!r}
if [ "$2" = "status" ]; then
    printf '%s\n' 'Server: RUNNING' 'Guardian: RUNNING'
fi
exit 0
""",
    )
    config = _config(tmp_path, launcher)
    monkeypatch.setattr(
        supervisor,
        "_managed_pair_snapshot",
        lambda _paths: {
            "server_pid": os.getpid(),
            "guardian_pid": os.getppid(),
        },
    )
    stop_event = threading.Event()
    result: list[int] = []
    worker = threading.Thread(
        target=lambda: result.append(
            supervisor.run_supervisor(config, stop_event=stop_event)
        ),
        daemon=True,
    )
    worker.start()

    deadline = time.monotonic() + 5
    probe = supervisor.probe_supervisor(config.paths)
    while not probe.verified and time.monotonic() < deadline:
        time.sleep(0.02)
        probe = supervisor.probe_supervisor(config.paths)
    assert probe.live and probe.verified and probe.pid == os.getpid()

    receipt = json.loads(config.paths.receipt_file.read_text(encoding="utf-8"))
    while not receipt["last_success_at"] and time.monotonic() < deadline:
        time.sleep(0.02)
        receipt = json.loads(config.paths.receipt_file.read_text(encoding="utf-8"))
    assert receipt["schema"] == supervisor.SUPERVISOR_SCHEMA
    assert receipt["endpoint"]["url"] == "http://127.0.0.1:3024"
    assert receipt["last_success_at"]
    assert receipt["consecutive_failures"] == 0

    stop_event.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert result == [0]
    assert not supervisor.probe_supervisor(config.paths).live
    stopped = json.loads(config.paths.receipt_file.read_text(encoding="utf-8"))
    assert stopped["state"] == "stopped"
    assert lifecycle_log.read_text(encoding="utf-8").splitlines()[-1] == "stop"


def test_run_child_does_not_wait_for_daemonized_descendant_capture(
    tmp_path: Path,
) -> None:
    launcher = _executable(
        tmp_path / "bin" / "launcher",
        """#!/bin/sh
(sleep 5) &
printf 'launcher complete\n'
exit 0
""",
    )

    started = time.monotonic()
    return_code, detail = supervisor._run_child(
        [str(launcher)],
        env=dict(os.environ),
        timeout=2,
        stop_event=threading.Event(),
    )

    assert time.monotonic() - started < 2
    assert return_code == 0
    assert detail == "launcher complete"


def test_zero_exit_without_verified_pid_pair_is_degraded(
    tmp_path: Path,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    config = _config(tmp_path, launcher)
    stop_event = threading.Event()
    result: list[int] = []
    worker = threading.Thread(
        target=lambda: result.append(
            supervisor.run_supervisor(config, stop_event=stop_event)
        ),
        daemon=True,
    )
    worker.start()

    deadline = time.monotonic() + 5
    receipt: dict[str, object] = {}
    while time.monotonic() < deadline:
        try:
            receipt = json.loads(config.paths.receipt_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.02)
            continue
        if receipt.get("state") == "backoff":
            break
        time.sleep(0.02)

    assert receipt["state"] == "backoff"
    assert receipt["last_exit_code"] == 0
    assert receipt["last_success_at"] is None
    assert receipt["managed_pair"] == {
        "server_pid": None,
        "guardian_pid": None,
    }
    assert "without a verified live server and guardian PID pair" in str(
        receipt["last_error"]
    )
    stop_event.set()
    worker.join(timeout=5)
    assert result == [0]


def test_zero_exit_with_foreign_live_minimal_identities_is_degraded(
    tmp_path: Path,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    config = _config(tmp_path, launcher)
    config.paths.server_dir.mkdir(parents=True)
    strangers = [
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _role in ("server", "guardian")
    ]
    stop_event = threading.Event()
    result: list[int] = []
    worker: threading.Thread | None = None
    try:
        for role, process in zip(("server", "guardian"), strangers, strict=True):
            (config.paths.server_dir / f"{role}.pid").write_text(
                f"{process.pid}\n",
                encoding="utf-8",
            )
            (config.paths.server_dir / f"{role}.identity.json").write_text(
                json.dumps(
                    {
                        "schema": "vibecrafted.managed-process.v1",
                        "role": role,
                        "pid": process.pid,
                    }
                ),
                encoding="utf-8",
            )

        snapshot = supervisor._managed_pair_snapshot(config.paths)
        assert snapshot == {
            "server_pid": strangers[0].pid,
            "guardian_pid": strangers[1].pid,
        }
        assert supervisor._managed_pair_healthy(snapshot)

        worker = threading.Thread(
            target=lambda: result.append(
                supervisor.run_supervisor(config, stop_event=stop_event)
            ),
            daemon=True,
        )
        worker.start()

        deadline = time.monotonic() + 5
        receipt: dict[str, object] = {}
        while time.monotonic() < deadline:
            try:
                receipt = json.loads(
                    config.paths.receipt_file.read_text(encoding="utf-8")
                )
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.02)
                continue
            if receipt.get("state") == "backoff":
                break
            time.sleep(0.02)

        assert receipt["state"] == "backoff"
        assert receipt["last_exit_code"] == 0
        assert receipt["last_success_at"] is None
        assert receipt["managed_pair"] == snapshot
        assert "without canonical managed-pair status proof" in str(
            receipt["last_error"]
        )
    finally:
        stop_event.set()
        if worker is not None:
            worker.join(timeout=5)
        for process in strangers:
            if process.poll() is None:
                process.terminate()
        for process in strangers:
            process.wait(timeout=5)

    assert worker is not None and not worker.is_alive()
    assert result == [0]


def test_start_service_does_not_kill_a_freshly_bootstrapped_supervisor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")

    calls: list[list[str]] = []
    loaded = False

    def fake_launchctl(args: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal loaded
        calls.append(list(args))
        if args[0] == "bootstrap":
            loaded = True
        return subprocess.CompletedProcess(args, 0, "", "")

    probes = iter(
        [
            supervisor.SupervisorProbe(False, False, None, None),
            supervisor.SupervisorProbe(False, False, None, None),
        ]
    )
    monkeypatch.setattr(supervisor, "_launchctl", fake_launchctl)
    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: loaded)
    monkeypatch.setattr(supervisor, "probe_supervisor", lambda _paths: next(probes))
    monkeypatch.setattr(
        supervisor,
        "_wait_for_managed_supervisor",
        lambda _config, *, identity, previous_pid=None: _managed_probe(
            config,
            pid=1234,
        ),
    )

    supervisor.start_service(config)
    assert calls == [
        ["enable", supervisor._launch_target()],
        [
            "bootstrap",
            supervisor._launch_domain(),
            str(config.paths.launch_agent_file),
        ],
        ["kickstart", supervisor._launch_target()],
    ]

    calls.clear()
    monkeypatch.setattr(
        supervisor,
        "probe_supervisor",
        lambda _paths: _managed_probe(config, pid=1234),
    )
    supervisor.start_service(config)
    assert calls == []


def test_start_service_refuses_bootstrap_when_launchctl_enable_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: False)
    monkeypatch.setattr(
        supervisor,
        "probe_supervisor",
        lambda _paths: supervisor.SupervisorProbe(False, False, None, None),
    )
    calls: list[list[str]] = []

    def fake_launchctl(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 5, "", "retained gate rejected")

    monkeypatch.setattr(supervisor, "_launchctl", fake_launchctl)

    with pytest.raises(
        supervisor.SupervisorError,
        match="launchctl enable failed: retained gate rejected",
    ) as failure:
        supervisor.start_service(config)

    assert failure.value.exit_code == 5
    assert calls == [["enable", supervisor._launch_target()]]


def test_start_service_kickstarts_a_loaded_job_without_current_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: True)
    monkeypatch.setattr(
        supervisor,
        "probe_supervisor",
        lambda _paths: supervisor.SupervisorProbe(False, False, None, None),
    )
    calls: list[list[str]] = []

    def fake_launchctl(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(supervisor, "_launchctl", fake_launchctl)
    monkeypatch.setattr(
        supervisor,
        "_wait_for_managed_supervisor",
        lambda _config, *, identity, previous_pid=None: _managed_probe(
            config,
            pid=1234,
        ),
    )

    supervisor.start_service(config)

    assert calls == [["kickstart", "-k", supervisor._launch_target()]]


def test_service_status_distinguishes_all_runtime_dimensions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: True)
    monkeypatch.setattr(
        supervisor,
        "probe_supervisor",
        lambda _paths: _managed_probe(config, pid=9876),
    )
    monkeypatch.setattr(
        supervisor,
        "_pair_healthy",
        lambda _launcher, _env, **_kwargs: True,
    )
    monkeypatch.setattr(
        supervisor,
        "_managed_pair_snapshot",
        lambda _paths: {"server_pid": 123, "guardian_pid": 456},
    )

    status = supervisor.service_status(config)

    assert status == supervisor.ServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        pair_healthy=True,
        supervisor_pid=9876,
        supervisor_service_managed=True,
        build_current=True,
    )


@pytest.mark.parametrize(
    "failure",
    [
        OSError("status executable unavailable"),
        subprocess.TimeoutExpired(["vibecrafted", "server", "status"], 15),
    ],
)
def test_pair_health_probe_failures_are_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")

    def fail_probe(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise failure

    monkeypatch.setattr(supervisor.subprocess, "run", fail_probe)

    assert not supervisor._pair_healthy(launcher, {})


def test_truncated_launch_agent_plist_degrades_service_and_runtime_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    config = _config(tmp_path, launcher)
    config.paths.launch_agent_file.parent.mkdir(parents=True)
    config.paths.launch_agent_file.write_bytes(
        b'<?xml version="1.0"?><plist version="1.0"><dict><key>Label</key>'
    )
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: True)

    status = supervisor.service_status(config)

    assert status.installed
    assert status.loaded
    assert not status.build_current
    assert not status.pair_healthy
    assert supervisor._runtime_status(config.paths) == 1
    assert "Supervision: BROKEN" in capsys.readouterr().out


def test_runtime_status_ignores_loaded_job_for_different_runtime_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    config = _config(tmp_path, launcher)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: True)
    monkeypatch.setattr(
        supervisor,
        "_launchctl_job_owns_paths",
        lambda _paths: False,
    )

    assert supervisor._runtime_status(config.paths) == 0
    assert "Supervision: UNSUPERVISED" in capsys.readouterr().out


def test_runtime_status_reports_loaded_job_with_missing_plist_as_broken(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    config = _config(tmp_path, launcher)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    monkeypatch.setattr(
        supervisor,
        "_launchctl_job_owns_paths",
        lambda _paths: True,
    )

    assert supervisor._runtime_status(config.paths) == 1
    assert "Supervision: BROKEN" in capsys.readouterr().out


def test_launcher_fingerprint_is_enforced_by_run_and_service_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    identity = supervisor._installed_service_identity(config.paths)
    assert identity is not None

    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: True)
    monkeypatch.setattr(
        supervisor,
        "probe_supervisor",
        lambda _paths: _managed_probe(config, pid=9876),
    )
    monkeypatch.setattr(
        supervisor,
        "_pair_healthy",
        lambda _launcher, _env, **_kwargs: True,
    )
    monkeypatch.setattr(
        supervisor,
        "_managed_pair_snapshot",
        lambda _paths: {"server_pid": 123, "guardian_pid": 456},
    )
    assert supervisor.service_status(config).build_current
    assert supervisor._runtime_status(config.paths) == 0
    assert "Supervision: LAUNCHD" in capsys.readouterr().out

    launcher.write_text("#!/bin/sh\n# changed launcher\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)

    status = supervisor.service_status(config)
    assert not status.build_current
    assert not status.pair_healthy
    assert supervisor._runtime_status(config.paths) == 1
    assert "Supervision: BROKEN" in capsys.readouterr().out
    with pytest.raises(supervisor.SupervisorError, match="launcher hash differs"):
        supervisor._supervisor_identity(
            supervisor_binary,
            launcher=launcher,
            expected_sha256=identity.executable_sha256,
            expected_runtime_sha256=identity.runtime_sha256,
            expected_version=identity.build_version,
            expected_launcher_sha256=identity.launcher_sha256,
        )


def test_legacy_supervisor_probe_remains_stoppable_but_not_build_current(
    tmp_path: Path,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    identity = supervisor._installed_service_identity(config.paths)
    assert identity is not None

    with supervisor._SupervisorLease(
        config.paths,
        service_managed=True,
        identity=identity,
    ):
        payload = json.loads(config.paths.lock_file.read_text(encoding="utf-8"))
        payload.pop("launcher_sha256")
        config.paths.lock_file.write_text(json.dumps(payload), encoding="utf-8")

        probe = supervisor.probe_supervisor(config.paths)
        assert supervisor._probe_is_supervisor(probe)
        assert probe.service_managed is True
        assert not supervisor._probe_matches_identity(
            probe,
            identity,
            service_managed=True,
        )


def test_start_service_rejects_foreground_marked_final_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: True)
    foreground = _managed_probe(config, pid=4321, service_managed=False)
    monkeypatch.setattr(
        supervisor,
        "probe_supervisor",
        lambda _paths: supervisor.SupervisorProbe(False, False, None, None),
    )
    monkeypatch.setattr(
        supervisor,
        "_launchctl",
        lambda args: subprocess.CompletedProcess(args, 0, "", ""),
    )
    monkeypatch.setattr(
        supervisor,
        "_wait_for_managed_supervisor",
        lambda _config, *, identity, previous_pid=None: foreground,
    )

    with pytest.raises(
        supervisor.SupervisorError,
        match="acquired the coordination lock but the installed identity was rejected",
    ) as failure:
        supervisor.start_service(config)

    assert failure.value.exit_code == supervisor.EX_TEMPFAIL


def test_start_service_reports_process_exit_before_lock_without_secret_dump(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: True)
    missing = supervisor.SupervisorProbe(False, False, None, None)
    monkeypatch.setattr(supervisor, "probe_supervisor", lambda _paths: missing)
    monkeypatch.setattr(
        supervisor,
        "_wait_for_managed_supervisor",
        lambda _config, *, identity, previous_pid=None: missing,
    )
    config.paths.stderr_log.write_text("", encoding="utf-8")

    def fake_launchctl(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[0] == "kickstart":
            with config.paths.stderr_log.open("a", encoding="utf-8") as stream:
                stream.write(
                    "vc-server-supervisor: runtime hash rejected "
                    "SECRET_TOKEN=do-not-leak\n"
                )
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(
            args,
            0,
            """service = {
    state = waiting
    runs = 1
    last exit code = 78
    environment = {
        SECRET_TOKEN => do-not-leak
    }
}
""",
            "",
        )

    monkeypatch.setattr(supervisor, "_launchctl", fake_launchctl)

    with pytest.raises(
        supervisor.SupervisorError,
        match="process started but exited or stalled before acquiring",
    ) as failure:
        supervisor.start_service(config)

    message = str(failure.value)
    assert "launchctl(state=waiting,pid=-,runs=1,last-exit-code=78)" in message
    assert "vc-server-supervisor: runtime hash rejected" in message
    assert "SECRET_TOKEN=<redacted>" in message
    assert "do-not-leak" not in message
    assert failure.value.exit_code == supervisor.EX_TEMPFAIL


def test_start_service_reports_job_that_never_started(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: True)
    missing = supervisor.SupervisorProbe(False, False, None, None)
    monkeypatch.setattr(supervisor, "probe_supervisor", lambda _paths: missing)
    monkeypatch.setattr(
        supervisor,
        "_wait_for_managed_supervisor",
        lambda _config, *, identity, previous_pid=None: missing,
    )

    def fake_launchctl(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[0] == "kickstart":
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(
            args,
            0,
            "service = {\n    state = waiting\n    runs = 0\n}\n",
            "",
        )

    monkeypatch.setattr(supervisor, "_launchctl", fake_launchctl)

    with pytest.raises(
        supervisor.SupervisorError,
        match="job did not start and no supervisor acquired",
    ) as failure:
        supervisor.start_service(config)

    assert "launchctl(state=waiting,pid=-,runs=0,last-exit-code=-)" in str(
        failure.value
    )
    assert failure.value.exit_code == supervisor.EX_TEMPFAIL


def test_install_reconciles_loaded_service_to_new_binary_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(
        tmp_path / "bin" / "vc-server-supervisor",
        "#!/bin/sh\n# build one\nexit 0\n",
    )
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    old_identity = supervisor._installed_service_identity(config.paths)
    assert old_identity is not None
    old_probe = _managed_probe(config, pid=1111)

    supervisor_binary.write_text(
        "#!/bin/sh\n# build two\nexit 0\n",
        encoding="utf-8",
    )
    supervisor_binary.chmod(0o755)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: True)
    monkeypatch.setattr(supervisor, "probe_supervisor", lambda _paths: old_probe)
    restarted: list[tuple[int | None, supervisor.SupervisorIdentity]] = []

    def fake_restart(
        target: supervisor.SupervisorConfig,
        *,
        previous_pid: int | None = None,
    ) -> supervisor.SupervisorProbe:
        identity = supervisor._installed_service_identity(target.paths)
        assert identity is not None
        restarted.append((previous_pid, identity))
        return _managed_probe(target, pid=2222)

    monkeypatch.setattr(supervisor, "restart_service", fake_restart)

    changed, did_restart = supervisor.install_and_reconcile_service(
        config,
        supervisor_binary=supervisor_binary,
    )

    new_identity = supervisor._installed_service_identity(config.paths)
    assert changed and did_restart
    assert restarted == [(1111, new_identity)]
    assert new_identity is not None
    assert new_identity.executable == supervisor_binary
    assert new_identity.executable_sha256 == supervisor._sha256_file(supervisor_binary)
    assert new_identity.executable_sha256 != old_identity.executable_sha256
    assert new_identity.runtime_sha256 == supervisor._sha256_file(
        Path(supervisor.__file__).resolve()
    )
    assert new_identity.build_version == supervisor.PACKAGE_VERSION


def test_install_bootstraps_fresh_service_with_installed_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    started: list[supervisor.SupervisorIdentity] = []

    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: False)
    monkeypatch.setattr(
        supervisor,
        "probe_supervisor",
        lambda _paths: supervisor.SupervisorProbe(False, False, None, None),
    )

    def fake_start(target: supervisor.SupervisorConfig) -> None:
        identity = supervisor._installed_service_identity(target.paths)
        assert identity is not None
        started.append(identity)

    monkeypatch.setattr(supervisor, "start_service", fake_start)

    changed, restarted = supervisor.install_and_reconcile_service(
        config,
        supervisor_binary=supervisor_binary,
    )

    assert changed
    assert not restarted
    assert len(started) == 1
    assert started[0].executable == supervisor_binary
    assert started[0].launcher_sha256 == supervisor._sha256_file(launcher)


def test_hermetic_service_upgrade_restarts_into_new_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(
        tmp_path / "bin" / "vc-server-supervisor",
        (
            "#!/bin/sh\n"
            f"exec {str(Path(sys.executable).resolve())!r} "
            '-m vibecrafted_core.server_supervisor "$@"\n'
        ),
    )
    config = _config(tmp_path, launcher)
    # Keep this service test independent from both the operator's installed
    # tools stamp and a concurrently advancing checkout HEAD.  The spawned
    # supervisor resolves its version under the fixture's isolated runtime
    # home, so seed that same explicit build identity before rendering the
    # LaunchAgent contract in the parent process.
    hermetic_version = "3.7.1+g00000000"
    staged_version = (
        config.paths.runtime_home / "tools" / "vibecrafted-current" / "VERSION"
    )
    staged_version.parent.mkdir(parents=True, exist_ok=True)
    staged_version.write_text(f"{hermetic_version}\n", encoding="utf-8")
    monkeypatch.setattr(supervisor, "PACKAGE_VERSION", hermetic_version)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    loaded = False
    service_process: subprocess.Popen[str] | None = None

    def stop_process() -> None:
        nonlocal service_process
        if service_process is None or service_process.poll() is not None:
            return
        service_process.terminate()
        service_process.wait(timeout=10)

    def start_process() -> None:
        nonlocal service_process
        payload = plistlib.loads(config.paths.launch_agent_file.read_bytes())
        environment = os.environ.copy()
        environment.update(payload["EnvironmentVariables"])
        environment["PYTHONPATH"] = str(Path(supervisor.__file__).resolve().parents[1])
        service_process = subprocess.Popen(
            payload["ProgramArguments"],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    def fake_launchctl(args: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal loaded, service_process
        action = args[0]
        if action == "bootstrap":
            loaded = True
            start_process()
        elif action == "bootout":
            loaded = False
            stop_process()
        elif action == "kickstart":
            if "-k" in args:
                stop_process()
            if service_process is None or service_process.poll() is not None:
                start_process()
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: loaded)
    monkeypatch.setattr(supervisor, "_launchctl", fake_launchctl)
    monkeypatch.setattr(
        supervisor,
        "_launchctl_job_owns_paths",
        lambda _paths: True,
    )
    try:
        supervisor.start_service(config)
        first = supervisor.probe_supervisor(config.paths)
        first_identity = supervisor._installed_service_identity(config.paths)
        assert first_identity is not None
        assert supervisor._probe_matches_identity(
            first,
            first_identity,
            service_managed=True,
        )

        supervisor_binary.write_text(
            (
                "#!/bin/sh\n"
                "# upgraded wrapper\n"
                f"exec {str(Path(sys.executable).resolve())!r} "
                '-m vibecrafted_core.server_supervisor "$@"\n'
            ),
            encoding="utf-8",
        )
        supervisor_binary.chmod(0o755)

        changed, restarted = supervisor.install_and_reconcile_service(
            config,
            supervisor_binary=supervisor_binary,
        )
        second = supervisor.probe_supervisor(config.paths)
        second_identity = supervisor._installed_service_identity(config.paths)

        assert changed and restarted
        assert first.pid is not None and second.pid is not None
        assert second.pid != first.pid
        assert not supervisor._process_alive(first.pid)
        assert second_identity is not None
        assert second_identity.executable_sha256 != (first_identity.executable_sha256)
        assert second_identity.runtime_sha256 == supervisor._sha256_file(
            Path(supervisor.__file__).resolve()
        )
        assert supervisor._probe_matches_identity(
            second,
            second_identity,
            service_managed=True,
        )
    finally:
        loaded = False
        stop_process()


def test_default_config_uses_runtime_environment_without_argparse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_home = tmp_path / "operator"
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(tmp_path / "runtime"))
    config_file = operator_home / ".config" / "vibecrafted" / "config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        "[server]\n"
        'bind_host = "100.82.232.70"\n'
        "port = 3025\n"
        'public_url = "http://100.82.232.70:3025"\n',
        encoding="utf-8",
    )

    config = supervisor.default_config(launcher=launcher)

    assert config.launcher == launcher
    assert config.paths.operator_home == operator_home.resolve()
    assert config.paths.home == (tmp_path / "state").resolve()
    assert config.paths.runtime_home == (tmp_path / "runtime").resolve()
    assert config.host == "100.82.232.70"
    assert config.port == 3025
    assert config.public_url == "http://100.82.232.70:3025"
    assert config.config_file == config_file


def test_config_command_reports_operator_owned_file_without_launcher(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operator_home = tmp_path / "operator"
    config_file = operator_home / ".config" / "vibecrafted" / "config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        "[server]\n"
        'bind_host = "100.82.232.70"\n'
        "port = 3025\n"
        'public_url = "http://100.82.232.70:3025"\n',
        encoding="utf-8",
    )

    result = supervisor.main(
        ["config", "--operator-home", str(operator_home), "--json"]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {
        "bind_host": "100.82.232.70",
        "config_path": str(config_file),
        "port": 3025,
        "public_url": "http://100.82.232.70:3025",
        "source": "file",
    }


def test_config_command_reports_explicit_default_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operator_home = tmp_path / "operator"

    result = supervisor.main(
        ["config", "--operator-home", str(operator_home), "--json"]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {
        "bind_host": "127.0.0.1",
        "config_path": str(operator_home / ".config" / "vibecrafted" / "config.toml"),
        "port": 3024,
        "public_url": "http://127.0.0.1:3024",
        "source": "default",
    }


def test_config_command_does_not_treat_unrelated_toml_as_server_authority(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operator_home = tmp_path / "operator"
    config_file = operator_home / ".config" / "vibecrafted" / "config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        '[runtime.picking.research]\ndefault_agents = ["claude", "codex", "agy"]\n',
        encoding="utf-8",
    )

    result = supervisor.main(
        ["config", "--operator-home", str(operator_home), "--json"]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["source"] == "default"


def test_linux_service_command_fails_closed_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    monkeypatch.setattr(supervisor.sys, "platform", "linux")

    result = supervisor.main(
        [
            "service",
            "status",
            "--launcher",
            str(launcher),
            "--home",
            str((tmp_path / "home").resolve()),
            "--runtime-home",
            str((tmp_path / "runtime").resolve()),
            "--operator-home",
            str((tmp_path / "operator").resolve()),
        ]
    )

    assert result == supervisor.EX_CONFIG
    assert "macOS launchd-only" in capsys.readouterr().err
    assert not (tmp_path / "operator" / "Library" / "LaunchAgents").exists()


def test_service_logs_reports_canonical_owner_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    home = (tmp_path / "crafted-home").resolve()
    runtime_home = (tmp_path / "runtime").resolve()
    operator_home = (tmp_path / "operator").resolve()
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")

    result = supervisor.main(
        [
            "service",
            "logs",
            "--json",
            "--launcher",
            str(launcher),
            "--home",
            str(home),
            "--runtime-home",
            str(runtime_home),
            "--operator-home",
            str(operator_home),
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {
        "directory": str(home / "server"),
        "stdout": str(home / "server" / "supervisor.stdout.log"),
        "stderr": str(home / "server" / "supervisor.stderr.log"),
    }
    assert not (operator_home / "Library" / "LaunchAgents").exists()


def test_child_environment_is_a_minimal_nonsecret_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    config = _config(tmp_path, launcher)
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-cross")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-cross")
    monkeypatch.setenv("VIBECRAFTED_STOP_TERM_WAIT_TICKS", "9")
    monkeypatch.setenv("VIBECRAFTED_TRIAGE_RUN", "0")

    environment = supervisor._child_environment(config.paths)

    assert "GITHUB_TOKEN" not in environment
    assert "OPENAI_API_KEY" not in environment
    assert environment["VIBECRAFTED_STOP_TERM_WAIT_TICKS"] == "9"
    assert environment["VIBECRAFTED_TRIAGE_RUN"] == "0"
    assert environment["VIBECRAFTED_HOME"] == str(config.paths.home)
    assert environment["VIBECRAFTED_RUNTIME_HOME"] == str(config.paths.runtime_home)
    assert environment["VIBECRAFTED_SERVER_SUPERVISOR_CHILD"] == "1"


def test_manual_stop_guard_refuses_loaded_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    monkeypatch.setattr(
        supervisor,
        "_launchctl",
        lambda args: subprocess.CompletedProcess(
            args,
            0,
            _launchctl_job_snapshot(config),
            "",
        ),
    )
    monkeypatch.setattr(
        supervisor,
        "probe_supervisor",
        lambda _paths: supervisor.SupervisorProbe(False, False, None, None),
    )

    with pytest.raises(
        supervisor.SupervisorError,
        match="vibecrafted server service stop",
    ) as failure:
        supervisor.manual_stop_guard(config.paths)
    assert failure.value.exit_code == supervisor.EX_TEMPFAIL


def test_launchd_ownership_matches_the_loaded_job_runtime_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    monkeypatch.setattr(
        supervisor,
        "_launchctl",
        lambda args: subprocess.CompletedProcess(
            args,
            0,
            _launchctl_job_snapshot(config),
            "",
        ),
    )

    assert supervisor._launchd_owns_pair(config.paths)


@pytest.mark.parametrize(
    "mismatch",
    ["plist", "program", "supervisor_path", "home", "runtime_home", "operator_home"],
)
def test_launchd_ownership_rejects_a_fixed_label_loaded_for_other_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    foreign = (tmp_path / "foreign" / mismatch).resolve()
    output = _launchctl_job_snapshot(
        config,
        plist=foreign if mismatch == "plist" else None,
        program=foreign if mismatch == "program" else None,
        supervisor_path=foreign if mismatch == "supervisor_path" else None,
        home=foreign if mismatch == "home" else None,
        runtime_home=foreign if mismatch == "runtime_home" else None,
        operator_home=foreign if mismatch == "operator_home" else None,
    )
    monkeypatch.setattr(
        supervisor,
        "_launchctl",
        lambda args: subprocess.CompletedProcess(args, 0, output, ""),
    )
    monkeypatch.setattr(
        supervisor,
        "probe_supervisor",
        lambda _paths: supervisor.SupervisorProbe(False, False, None, None),
    )

    assert not supervisor._launchd_owns_pair(config.paths)
    supervisor.manual_stop_guard(config.paths)


def test_manual_stop_holds_common_lease_against_concurrent_supervisor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "stopped"
    launcher = _executable(
        tmp_path / "bin" / "vibecrafted",
        f"""#!/bin/sh
sleep "${{VIBECRAFTED_TEST_SERVER_STOP_DELAY:-0}}"
printf stopped > {str(marker)!r}
""",
    )
    config = _config(tmp_path, launcher)
    monkeypatch.setenv("VIBECRAFTED_TEST_SERVER_STOP_DELAY", "0.4")
    result: list[str] = []

    def run_manual_stop() -> None:
        supervisor.manual_stop(config)
        result.append("stopped")

    worker = threading.Thread(
        target=run_manual_stop,
        daemon=True,
    )
    worker.start()

    deadline = time.monotonic() + 5
    probe = supervisor.probe_supervisor(config.paths)
    while probe.role != "manual-stop" and time.monotonic() < deadline:
        time.sleep(0.02)
        probe = supervisor.probe_supervisor(config.paths)
    assert probe.live and probe.verified and probe.role == "manual-stop"

    with pytest.raises(
        supervisor.SupervisorError,
        match="coordination lease is already active",
    ) as failure:
        supervisor.run_supervisor(config, stop_event=threading.Event())
    assert failure.value.exit_code == supervisor.EX_TEMPFAIL

    worker.join(timeout=5)
    assert result == ["stopped"]
    assert marker.read_text(encoding="utf-8") == "stopped"
    assert not supervisor.probe_supervisor(config.paths).live


def test_manual_stop_repairs_launchd_reactivation_during_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    loaded = False
    launchctl_calls: list[list[str]] = []

    def fake_launchctl(args: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal loaded
        if args[0] == "print":
            return subprocess.CompletedProcess(
                args,
                0 if loaded else 113,
                _launchctl_job_snapshot(config) if loaded else "",
                "",
            )
        launchctl_calls.append(list(args))
        if args[0] == "bootout":
            loaded = False
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_run(
        argv: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal loaded
        loaded = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(supervisor, "_launchctl", fake_launchctl)
    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)

    with pytest.raises(
        supervisor.SupervisorError,
        match="reactivated during manual-stop cleanup",
    ) as failure:
        supervisor.manual_stop(config)

    assert failure.value.exit_code == supervisor.EX_TEMPFAIL
    assert launchctl_calls == [["bootout", supervisor._launch_target()]]
    assert not loaded
    assert not supervisor.probe_supervisor(config.paths).live


def test_service_stop_holds_common_lease_during_pair_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    loaded = True

    def fake_launchctl(args: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal loaded
        if args[0] == "bootout":
            loaded = False
        return subprocess.CompletedProcess(args, 0, "", "")

    cleanup_roles: list[str | None] = []

    def fake_run(
        argv: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        probe = supervisor.probe_supervisor(config.paths)
        cleanup_roles.append(probe.role)
        assert probe.live and probe.verified
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: loaded)
    monkeypatch.setattr(supervisor, "_launchctl", fake_launchctl)
    monkeypatch.setattr(
        supervisor,
        "_launchctl_job_owns_paths",
        lambda _paths: True,
    )
    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)

    supervisor.stop_service(config)

    assert cleanup_roles == ["manual-stop"]
    assert not supervisor.probe_supervisor(config.paths).live


def test_service_stop_refuses_foreign_launchd_job_without_bootout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    launchctl_calls: list[list[str]] = []

    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: True)
    monkeypatch.setattr(
        supervisor,
        "_launchctl_job_owns_paths",
        lambda _paths: False,
    )
    monkeypatch.setattr(
        supervisor,
        "_launchctl",
        lambda args: (
            launchctl_calls.append(list(args))
            or subprocess.CompletedProcess(args, 0, "", "")
        ),
    )

    with pytest.raises(
        supervisor.SupervisorError,
        match="foreign runtime paths",
    ) as failure:
        supervisor.stop_service(config)

    assert failure.value.exit_code == supervisor.EX_TEMPFAIL
    assert launchctl_calls == []


def test_service_stop_rejects_launchd_reactivation_during_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    supervisor_binary = _executable(tmp_path / "bin" / "vc-server-supervisor")
    config = _config(tmp_path, launcher)
    supervisor.install_service(config, supervisor_binary=supervisor_binary)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    loaded = True

    def fake_launchctl(args: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal loaded
        if args[0] == "bootout":
            loaded = False
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_run(
        argv: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal loaded
        loaded = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(supervisor, "_launchctl_loaded", lambda: loaded)
    monkeypatch.setattr(supervisor, "_launchctl", fake_launchctl)
    monkeypatch.setattr(
        supervisor,
        "_launchctl_job_owns_paths",
        lambda _paths: True,
    )
    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)

    with pytest.raises(supervisor.SupervisorError, match="became active during"):
        supervisor.stop_service(config)
    assert not supervisor.probe_supervisor(config.paths).live


def test_service_mutation_lease_refuses_concurrent_runtime_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    config = _config(tmp_path, launcher)
    tools_home = tmp_path / "tools"
    tools_home.mkdir()
    lock_path = tools_home / supervisor._TOOLS_INSTALL_LOCK_NAME
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools_home))
    monkeypatch.delenv(supervisor._TOOLS_INSTALL_LEASE_ENV, raising=False)

    try:
        with (
            pytest.raises(
                supervisor.SupervisorError,
                match="runtime install is active",
            ) as failure,
            supervisor._ToolsInstallMutationLease(config.paths),
        ):
            pass
        assert failure.value.exit_code == supervisor.EX_TEMPFAIL
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_service_mutation_lease_accepts_verified_inherited_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    config = _config(tmp_path, launcher)
    tools_home = tmp_path / "tools"
    tools_home.mkdir()
    lock_path = tools_home / supervisor._TOOLS_INSTALL_LOCK_NAME
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools_home))
    monkeypatch.setenv(supervisor._TOOLS_INSTALL_LEASE_ENV, str(descriptor))

    try:
        with supervisor._ToolsInstallMutationLease(config.paths) as lease:
            assert lease.inherited
            assert lease.descriptor == descriptor
        os.fstat(descriptor)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_stopping_receipt_failure_does_not_skip_pair_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    config = _config(tmp_path, launcher)
    stop_event = threading.Event()
    stop_event.set()
    child_calls: list[list[str]] = []
    original_atomic_json = supervisor._atomic_json

    def flaky_atomic_json(path: Path, payload: dict[str, object]) -> None:
        if payload.get("state") == "stopping":
            raise OSError("receipt unavailable")
        original_atomic_json(path, payload)

    def fake_run_child(
        argv: list[str],
        **_kwargs: object,
    ) -> tuple[int, str]:
        child_calls.append(argv)
        return 0, ""

    monkeypatch.setattr(supervisor, "_atomic_json", flaky_atomic_json)
    monkeypatch.setattr(supervisor, "_run_child", fake_run_child)

    assert supervisor.run_supervisor(config, stop_event=stop_event) == 0
    assert child_calls == [[str(launcher), "server", "stop"]]
    receipt = json.loads(config.paths.receipt_file.read_text(encoding="utf-8"))
    assert receipt["state"] == "stopped"


def test_invalid_held_kernel_lock_remains_fail_closed(tmp_path: Path) -> None:
    launcher = _executable(tmp_path / "bin" / "vibecrafted")
    config = _config(tmp_path, launcher)

    with supervisor._SupervisorLease(config.paths, service_managed=False):
        config.paths.lock_file.write_text("{invalid", encoding="utf-8")
        probe = supervisor.probe_supervisor(config.paths)
        assert probe.live
        assert not probe.verified
        assert probe.pid is None
        with pytest.raises(
            supervisor.SupervisorError,
            match="active foreground supervisor",
        ) as failure:
            supervisor.manual_stop_guard(config.paths)
        assert failure.value.exit_code == supervisor.EX_TEMPFAIL
