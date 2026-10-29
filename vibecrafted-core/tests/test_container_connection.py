from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from vibecrafted_core import container_connection as connection
from vibecrafted_core import dev_container
from vibecrafted_core.message_control import MessageControlError


def _meta(root: Path) -> dict:
    return {
        "run_id": "run-1",
        "status": "active",
        "execution_environment": "local-container",
        "parent_root": str(root),
        "owner_pid": 11,
        "worker_pid": 12,
        "owner_identity": {"run_id": "run-1", "pid": 11},
        "worker_identity": {"run_id": "run-1", "pid": 12},
        "container": {
            "engine_context": "chosen-context",
            "compose_project": "vc-project",
            "container_id": "a" * 64,
            "host_root": str(root),
            "workdir": "/workspace",
            "image_id": "sha256:original",
            "recipe_digest": "original-recipe",
        },
    }


def test_recovery_requires_both_original_host_incarnations(tmp_path, monkeypatch):
    meta = _meta(tmp_path)
    monkeypatch.setattr(connection, "resolve_message_run", lambda **_: "run-1")
    monkeypatch.setattr(
        connection,
        "resolve_run",
        lambda _: SimpleNamespace(meta=tmp_path / "meta.json"),
    )
    monkeypatch.setattr(connection, "read_run_meta", lambda *a, **k: meta)
    calls = []

    def validate(receipt, **kwargs):
        calls.append(kwargs)
        return kwargs["expected_pid"] == 11, "process_identity_mismatch", None

    monkeypatch.setattr(connection, "validate_process_identity", validate)
    with pytest.raises(MessageControlError, match="worker:process_identity_mismatch"):
        connection._live_meta("run-1")
    assert [row["expected_pid"] for row in calls] == [11, 12]
    assert all(row["expected_run_id"] == "run-1" for row in calls)


@pytest.mark.parametrize("drift", ["id", "mount", "image", "recipe", "stopped"])
def test_recovery_refuses_changed_container_identity(tmp_path, monkeypatch, drift):
    meta = _meta(tmp_path)
    found = dev_container.ContainerRecord(
        container_id="a" * 64,
        name="c",
        state="running",
        image="image",
        project="vc-project",
        workspace_source=str(tmp_path),
        image_id="sha256:original",
        recipe="original-recipe",
    )
    values = vars(found).copy()
    key, value = {
        "id": ("container_id", "b" * 64),
        "mount": ("workspace_source", str(tmp_path / "other")),
        "image": ("image_id", "sha256:other"),
        "recipe": ("recipe", "other"),
        "stopped": ("state", "exited"),
    }[drift]
    values[key] = value
    monkeypatch.setattr(dev_container, "docker_cli", lambda _: "docker")
    monkeypatch.setattr(
        dev_container,
        "find_project_container",
        lambda *a: dev_container.ContainerRecord(**values),
    )
    env = {"DOCKER_CONTEXT": "wrong", "DOCKER_HOST": "tcp://wrong"}
    with pytest.raises(MessageControlError, match="container_mismatch"):
        connection._target(meta, env)
    assert env["DOCKER_CONTEXT"] == "chosen-context" and "DOCKER_HOST" not in env


def test_recovery_closes_connection_when_original_provider_changes(
    tmp_path, monkeypatch
):
    meta = _meta(tmp_path)
    target = dev_container.ContainerTarget(
        cli="docker",
        project="vc-project",
        container_id="a" * 64,
        container_name="c",
        image="image",
        recipe_digest="original-recipe",
        host_root=str(tmp_path),
    )
    monkeypatch.setattr(connection, "_live_meta", lambda _: copy.deepcopy(meta))
    monkeypatch.setattr(connection, "_target", lambda *_: target)
    events = []

    class Relay:
        failure = ""

        def __init__(self, **kwargs):
            pass

        def _exec(self, *args):
            events.append("provider")
            identity = (
                ["123", "start:old", "boot:original"]
                if events.count("provider") == 1
                else ["123", "start:reused", "boot:original"]
            )
            return subprocess.CompletedProcess(
                [], 0, json.dumps(identity).encode(), b""
            )

        def start(self):
            events.append("roundtrip")

        def close(self):
            events.append("closed")

    monkeypatch.setattr(connection, "ContainerMessageRelay", Relay)
    with pytest.raises(MessageControlError, match="provider_changed"):
        connection.connect_container(
            "run-1", ready=lambda status: events.append(status["status"])
        )
    assert events == ["provider", "roundtrip", "connected", "provider", "closed"]


def test_connect_flag_does_not_accept_guest_or_message_mutations(
    tmp_path, monkeypatch, capsys
):
    from vibecrafted_core.cli import main

    monkeypatch.setattr(dev_container, "RUN_STAGING_ROOT", str(tmp_path / "absent"))
    assert (
        main(["message", "--run-id", "run-1", "--connect-container", "--receive"]) == 2
    )
    assert "connect_container_requires_host_run_id_only" in capsys.readouterr().err
