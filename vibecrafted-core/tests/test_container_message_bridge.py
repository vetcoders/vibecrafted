from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from vibecrafted_core import message_control
from vibecrafted_core.container_message_bridge import (
    _MAX_REQUEST,
    _SCAN,
    BRIDGE_ENV,
    ContainerMessageRelay,
    guest_request,
)


def _create_run(home: Path, run_id: str) -> None:
    meta = home / "control_plane" / "runtime_runs" / run_id / "meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "agent": "claude",
                "runtime_session_id": "runtime-" + run_id,
            }
        )
    )


def _fake_docker(path: Path) -> None:
    path.write_text(
        f"#!{sys.executable}\n"
        "import os,sys\n"
        "a=sys.argv[1:]\n"
        "assert a.pop(0)=='exec'\n"
        "if a[0]=='-i': a.pop(0)\n"
        "a.pop(0)\n"
        "os.execvp(a[0],a)\n"
    )
    path.chmod(0o755)


def test_guest_cli_receive_and_ack_reach_only_host_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_home = tmp_path / "host"
    guest_home = tmp_path / "guest"
    staging = tmp_path / "container" / "run-1"
    guest_home.mkdir()
    monkeypatch.setenv("VIBECRAFTED_HOME", str(host_home))
    _create_run(host_home, "run-1")
    _create_run(host_home, "run-2")
    first = message_control.send_message(run_id="run-1", text="synthetic first")
    other = message_control.send_message(run_id="run-2", text="synthetic other")
    docker = tmp_path / "fake-docker"
    _fake_docker(docker)
    relay = ContainerMessageRelay(
        cli=str(docker),
        container_id="container",
        run_id="run-1",
        env=os.environ,
        staging_root=str(staging),
    )
    relay.start()
    env = {
        **os.environ,
        "VIBECRAFTED_HOME": str(guest_home),
        BRIDGE_ENV: relay.guest_path,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }

    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "vibecrafted_core.cli", "message", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    try:
        received = cli("--run-id", "run-1", "--receive")
        assert received.returncode == 0, received.stderr
        assert [row["message_id"] for row in json.loads(received.stdout)] == [
            first["message_id"]
        ]
        refused = cli("--run-id", "run-2", "--receive")
        assert refused.returncode == 2 and "bridge_run_mismatch" in refused.stderr
        missing = cli("--receive")
        assert missing.returncode == 2 and "bridge_run_mismatch" in missing.stderr
        wrong_ack = cli("--run-id", "run-1", "--ack", other["message_id"])
        assert (
            wrong_ack.returncode == 2 and "message_target_mismatch" in wrong_ack.stderr
        )
        acknowledged = cli("--run-id", "run-1", "--ack", first["message_id"])
        assert acknowledged.returncode == 0, acknowledged.stderr
        assert (
            message_control.inspect_message(first["message_id"])["delivery_state"]
            == "agent_acknowledged"
        )
        assert (
            message_control.receive_messages(run_id="run-2")[0]["message_id"]
            == other["message_id"]
        )
        inspected = cli("--run-id", "run-1", "--inspect", first["message_id"])
        assert inspected.returncode == 0
        assert json.loads(inspected.stdout)["message_id"] == first["message_id"]
        assert (
            cli("--run-id", "run-1", "--inspect", other["message_id"]).returncode == 2
        )
        assert cli("--inspect", first["message_id"]).returncode == 2
        assert not (guest_home / "control_plane" / "messages").exists()
        assert not relay.failure
    finally:
        relay.close()
        relay.close()
        assert not Path(relay.guest_path).exists()


def test_guest_timeout_does_not_fall_back_to_local_inbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = tmp_path / "channel"
    channel.mkdir()
    monkeypatch.setenv(BRIDGE_ENV, str(channel))
    with pytest.raises(message_control.MessageControlError, match="bridge_timeout"):
        guest_request(
            action="receive", run_id="run-1", session="", message_id="", timeout=0.01
        )
    assert list(channel.iterdir()) == []


def test_missing_required_bridge_never_reads_guest_inbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = {
        **os.environ,
        "VIBECRAFTED_HOME": str(tmp_path / "guest"),
        "VIBECRAFTED_MESSAGE_BRIDGE_REQUIRED": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    env.pop(BRIDGE_ENV, None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "vibecrafted_core.cli",
            "message",
            "--run-id",
            "run-1",
            "--receive",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 2
    assert "bridge_unavailable" in result.stderr
    assert not (tmp_path / "guest" / "control_plane" / "messages").exists()


def test_scan_refuses_symlinks_and_bounds_untrusted_request_bytes(
    tmp_path: Path,
) -> None:
    channel = tmp_path / "channel"
    channel.mkdir()
    secret = tmp_path / "secret"
    secret.write_text("must never enter request payload")
    (channel / ("a" * 32 + ".request")).symlink_to(secret)
    oversized = channel / ("b" * 32 + ".request")
    oversized.write_text("x" * (_MAX_REQUEST + 200))
    result = subprocess.run(
        [sys.executable, "-c", _SCAN, str(channel)],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    rows = json.loads(result.stdout)
    assert rows == [["b" * 32, "x" * (_MAX_REQUEST + 1)]]
    assert secret.read_text() == "must never enter request payload"
    assert (channel / ("a" * 32 + ".request")).is_symlink()


def test_relay_failure_is_visible_and_stop_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = tmp_path / "host"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(host))
    _create_run(host, "run-1")
    docker = tmp_path / "fake-docker"
    _fake_docker(docker)
    relay = ContainerMessageRelay(
        cli=str(docker),
        container_id="c",
        run_id="run-1",
        env=os.environ,
        staging_root=str(tmp_path / "stage"),
    )
    real_exec = relay._exec

    def failing_exec(script: str, *args: str, data: bytes | None = None):
        if script == _SCAN:
            return subprocess.CompletedProcess([], 1, b"", b"lost")
        return real_exec(script, *args, data=data)

    monkeypatch.setattr(relay, "_exec", failing_exec)
    with pytest.raises(
        message_control.MessageControlError, match="bridge_roundtrip_failed"
    ):
        relay.start()
    deadline = time.monotonic() + 3
    while not relay.failure and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "container request scan failed" in relay.failure
    relay.close()
    relay.close()
    assert not Path(relay.guest_path).exists()
    with pytest.raises(message_control.MessageControlError, match="already_started"):
        relay.start()


def _guest_connection(root: Path, run_id: str, *, age: float = 0) -> Path:
    from vibecrafted_core.container_message_transport import _CONNECTION_SCHEMA

    channel = root / run_id / ("message-" + os.urandom(24).hex())
    channel.mkdir(parents=True)
    (channel / "connection.json").write_text(
        json.dumps(
            {
                "schema": _CONNECTION_SCHEMA,
                "run_id": run_id,
                "container_id": "c",
                "channel": str(channel),
                "updated_at": time.time() - age,
            }
        )
    )
    return channel


def test_legacy_cli_discovers_host_connection_without_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, guest, staging = (tmp_path / name for name in ("host", "guest", "stage"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(host))
    _create_run(host, "run-legacy")
    message = message_control.send_message(
        run_id="run-legacy", text="host-only payload"
    )
    docker = tmp_path / "docker"
    _fake_docker(docker)
    relay = ContainerMessageRelay(
        cli=str(docker),
        container_id="c",
        run_id="run-legacy",
        env=os.environ,
        staging_root=str(staging / "run-legacy"),
    )
    (staging / "run-legacy").mkdir(parents=True, exist_ok=True)
    (staging / "run-legacy" / "provider.identity").write_text("existing-provider")
    relay.start()
    env = {
        **os.environ,
        "VIBECRAFTED_HOME": str(guest),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    env.pop(BRIDGE_ENV, None)
    env.pop("VIBECRAFTED_MESSAGE_BRIDGE_REQUIRED", None)

    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from vibecrafted_core import dev_container; dev_container.RUN_STAGING_ROOT=sys.argv.pop(1); from vibecrafted_core.cli import main; raise SystemExit(main())",
                str(staging),
                "message",
                *args,
            ],
            env=env,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )

    try:
        received = cli("--run-id", "run-legacy", "--receive")
        assert received.returncode == 0, received.stderr
        assert json.loads(received.stdout)[0]["message_id"] == message["message_id"]
        inspected = cli("--run-id", "run-legacy", "--inspect", message["message_id"])
        assert (
            inspected.returncode == 0
            and json.loads(inspected.stdout)["text"] == "host-only payload"
        )
        refused = cli("--run-id", "foreign-run", "--receive")
        assert (
            refused.returncode == 2 and "run_not_resolved:foreign-run" in refused.stderr
        )
        acknowledged = cli("--run-id", "run-legacy", "--ack", message["message_id"])
        assert acknowledged.returncode == 0, acknowledged.stderr
        assert (
            message_control.inspect_message(message["message_id"])["delivery_state"]
            == "agent_acknowledged"
        )
        assert not (guest / "control_plane" / "messages").exists()
    finally:
        relay.close()
    disconnected = cli("--run-id", "run-legacy", "--receive")
    assert disconnected.returncode == 2 and "bridge_unavailable" in disconnected.stderr
    assert disconnected.stdout == ""


@pytest.mark.parametrize("change", ["stale", "foreign", "symlink", "ambiguous"])
def test_legacy_discovery_refuses_stale_foreign_and_ambiguous_connections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    from vibecrafted_core import dev_container
    from vibecrafted_core.container_message_bridge import guest_channel

    monkeypatch.delenv(BRIDGE_ENV, raising=False)
    monkeypatch.setattr(dev_container, "RUN_STAGING_ROOT", str(tmp_path))
    channel = _guest_connection(tmp_path, "run-1", age=120 if change == "stale" else 0)
    descriptor = channel / "connection.json"
    if change == "foreign":
        data = json.loads(descriptor.read_text())
        data["run_id"] = "run-2"
        descriptor.write_text(json.dumps(data))
    elif change == "symlink":
        backup = tmp_path / "descriptor"
        descriptor.rename(backup)
        descriptor.symlink_to(backup)
    elif change == "ambiguous":
        _guest_connection(tmp_path, "run-1")
    with pytest.raises(message_control.MessageControlError, match="bridge_"):
        guest_channel("run-1")


def test_second_live_connection_does_not_announce_connected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "host"))
    _create_run(tmp_path / "host", "run-1")
    docker = tmp_path / "docker"
    _fake_docker(docker)
    kwargs = {
        "cli": str(docker),
        "container_id": "c",
        "run_id": "run-1",
        "env": os.environ,
        "staging_root": str(tmp_path / "stage" / "run-1"),
    }
    first = ContainerMessageRelay(**kwargs)
    second = ContainerMessageRelay(**kwargs)
    first.start()
    try:
        with pytest.raises(
            message_control.MessageControlError, match="bridge_roundtrip_failed"
        ):
            second.start()
        assert not Path(second.guest_path).exists()
        assert not first.failure
    finally:
        first.close()
        second.close()


def test_implicit_bridge_selection_requires_this_staged_run(tmp_path, monkeypatch):
    from vibecrafted_core import dev_container
    from vibecrafted_core.container_message_bridge import guest_bridge_selected

    monkeypatch.delenv(BRIDGE_ENV, raising=False)
    monkeypatch.delenv("VIBECRAFTED_MESSAGE_BRIDGE_REQUIRED", raising=False)
    monkeypatch.setattr(dev_container, "RUN_STAGING_ROOT", str(tmp_path))
    stage = tmp_path / "run-owned"
    stage.mkdir()
    (stage / "provider.identity").write_text("existing-provider")
    assert guest_bridge_selected("run-owned")
    assert not guest_bridge_selected("run-unrelated")
    assert not guest_bridge_selected("")


def test_roundtrip_uses_discovery_and_preserves_old_undiscoverable_channel(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "host"))
    _create_run(tmp_path / "host", "run-1")
    stage = tmp_path / "stage" / "run-1"
    old = stage / ("message-" + "a" * 48)
    old.mkdir(parents=True)
    (old / "unrelated").write_text("preserve")
    docker = tmp_path / "docker"
    _fake_docker(docker)
    relay = ContainerMessageRelay(
        cli=str(docker),
        container_id="c",
        run_id="run-1",
        env=os.environ,
        staging_root=str(stage),
    )
    try:
        relay.start()
        assert not relay.failure
    finally:
        relay.close()
    assert (old / "unrelated").read_text() == "preserve"
