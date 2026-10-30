"""Reconnect a recorded live container run through its existing host inbox.

This is an explicit foreground recovery owner, not a detached service or a
second run store. It stops when either recorded host/provider incarnation ends.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import dev_container
from .container_message_bridge import ContainerMessageRelay
from .control_plane import resolve_run
from .message_control import MessageControlError, resolve_message_run
from .process_control import validate_process_identity
from .run_mutation import read_run_meta

_PROVIDER = """import json,pathlib,sys
root=pathlib.Path(sys.argv[1]); record=(root/"provider.identity").read_text().split()
if len(record)!=3: raise SystemExit(2)
pid,start,boot=record
proc=pathlib.Path("/proc")/str(int(pid))
if int(pid)<=0: raise SystemExit(2)
if "start:"+proc.joinpath("stat").read_text().rsplit(")",1)[1].split()[19]!=start: raise SystemExit(2)
if "boot:"+pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()!=boot: raise SystemExit(2)
if ("VIBECRAFTED_RUN_ID="+sys.argv[2]).encode() not in proc.joinpath("environ").read_bytes().split(b"\\0"): raise SystemExit(2)
print(json.dumps(record))
"""


def _live_meta(run_id: str) -> dict[str, Any]:
    resolve_message_run(run_id=run_id)
    resolved = resolve_run(run_id)
    meta = read_run_meta(resolved.meta, expected_run_id=run_id)
    if (
        meta.get("status") != "active"
        or meta.get("execution_environment") != "local-container"
    ):
        raise MessageControlError("container_connection_run_not_active")
    for role in ("owner", "worker"):
        pid = meta.get(role + "_pid")
        current, reason, _ = validate_process_identity(
            meta.get(role + "_identity"),
            expected_pid=pid,
            expected_pgid=None,
            expected_run_id=run_id,
        )
        # The validator reaches this reason only after proving the exact
        # incarnation. Interactive owners can inherit another ambient run id.
        if not current and reason != "process_run_id_mismatch":
            raise MessageControlError(f"container_connection_{role}:{reason}")
    return meta


def _target(
    meta: Mapping[str, Any], env: dict[str, str]
) -> dev_container.ContainerTarget:
    record = meta.get("container")
    if not isinstance(record, dict):
        raise MessageControlError("container_connection_receipt_missing")
    context = record.get("engine_context")
    project = record.get("compose_project")
    cid = record.get("container_id")
    host_root = record.get("host_root")
    if (
        not isinstance(context, str)
        or not context
        or not isinstance(project, str)
        or not project
        or not isinstance(cid, str)
        or len(cid) != 64
        or any(c not in "0123456789abcdef" for c in cid)
        or not isinstance(host_root, str)
        or not Path(host_root).is_absolute()
        or record.get("workdir") != dev_container.WORKDIR
        or str(Path(host_root).resolve())
        != str(Path(str(meta.get("parent_root") or meta.get("root") or "")).resolve())
    ):
        raise MessageControlError("container_connection_receipt_invalid")
    env["DOCKER_CONTEXT"] = context
    env.pop("DOCKER_HOST", None)
    cli = dev_container.docker_cli(env)
    if not cli:
        raise MessageControlError("container_connection_engine_unavailable")
    found = dev_container.find_project_container(cli, project, env)
    if (
        found is None
        or found.container_id != cid
        or found.state != "running"
        or str(Path(found.workspace_source).resolve()) != str(Path(host_root).resolve())
        or (record.get("image_id") and record["image_id"] != found.image_id)
        or (record.get("recipe_digest") and record["recipe_digest"] != found.recipe)
    ):
        raise MessageControlError("container_connection_container_mismatch")
    return dev_container.ContainerTarget(
        cli=cli,
        project=project,
        container_id=cid,
        container_name=found.name,
        image=found.image,
        recipe_digest=found.recipe,
        host_root=host_root,
        engine_context=context,
        image_id=found.image_id,
    )


def connect_container(
    run_id: str,
    *,
    ready: Callable[[dict[str, Any]], None],
    env: Mapping[str, str] | None = None,
) -> None:
    """Serve until interrupted or the original recorded owners stop matching."""
    child_env = dict(os.environ if env is None else env)
    meta = _live_meta(run_id)
    target = _target(meta, child_env)
    relay = ContainerMessageRelay(
        cli=target.cli,
        container_id=target.container_id,
        run_id=run_id,
        env=child_env,
        staging_root=dev_container._run_dir(run_id),
    )
    try:
        provider = relay._exec(_PROVIDER, dev_container._run_dir(run_id), run_id)
        if provider.returncode:
            raise MessageControlError("container_connection_provider_unproven")
        identity = json.loads(provider.stdout)

        def prove_current_ownership() -> None:
            current = _live_meta(run_id)
            if any(
                current.get(key) != meta.get(key)
                for key in ("container", "owner_identity", "worker_identity")
            ):
                raise MessageControlError("container_connection_ownership_changed")
            check = relay._exec(_PROVIDER, dev_container._run_dir(run_id), run_id)
            if check.returncode or json.loads(check.stdout) != identity:
                raise MessageControlError("container_connection_provider_changed")

        relay.start()
        # The provider may have ended while the guest handshake was in flight.
        prove_current_ownership()
        ready(
            {
                "run_id": run_id,
                "container_id": target.container_id,
                "status": "connected",
                "transport": "host-owned-run-inbox",
                "provider_restarted": False,
            }
        )
        while True:
            if relay.failure:
                raise MessageControlError(
                    "container_connection_relay_failed:" + relay.failure
                )
            prove_current_ownership()
            time.sleep(1)
    finally:
        relay.close()
