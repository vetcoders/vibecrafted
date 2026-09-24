"""Durable, run-addressed provider-message control plane.

This is an outbox owned by the existing control-plane root, not another JSONL
bus. A provider queue receipt is only evidence of provider acceptance: it is
never promoted to an agent acknowledgement or execution.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .control_plane import (
    RunNotResolved,
    _read_json,
    _write_json_durable,
    control_plane_home,
    resolve_run,
)
from .run_mutation import run_mutation_locks
from .runtime_paths import selected_runtime_environment
from .spawn import _resolve_agent_command

MESSAGE_SCHEMA = "vibecrafted.provider-message.v1"
MAX_MESSAGE_BYTES = 64 * 1024
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PLACEHOLDER_IDS = frozenset({"pending", "none", "null", "unknown"})
_UNRESOLVED_OR_FAILED = frozenset(
    {"recorded", "retryable_failure", "permanent_failure"}
)
_INBOX_PROVIDERS = frozenset({"claude", "agy", "grok", "junie", "kimi", "cursor"})


class MessageControlError(ValueError):
    """A request cannot safely be bound to one tracked provider session."""


def _now() -> str:
    from .clock import utc_now

    return utc_now().isoformat()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _outbox_root() -> Path:
    root = control_plane_home() / "messages"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkdir's mode is affected by umask and an existing directory can predate
    # this feature. Message payloads are private control-plane data.
    # Private directory: owner needs traversal; group and others get no access.
    # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
    os.chmod(root, 0o700)
    return root


def _message_path(message_id: str) -> Path:
    if not _SAFE_ID.fullmatch(message_id):
        raise MessageControlError("invalid_message_id")
    return _outbox_root() / f"{message_id}.json"


def _idempotency_path(key: str) -> Path:
    directory = _outbox_root() / "idempotency"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Private directory: owner needs traversal; group and others get no access.
    # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
    os.chmod(directory, 0o700)
    return directory / f"{_digest(key)}.json"


def _run_identity(run_id: str) -> tuple[str, str, str]:
    """Read the provider, native session and runtime session from one run."""

    try:
        resolved = resolve_run(run_id)
    except (RunNotResolved, OSError, ValueError) as exc:
        raise MessageControlError(f"run_not_resolved:{run_id}") from exc
    if resolved.meta is None:
        raise MessageControlError("run_meta_missing")
    meta = _read_json(resolved.meta)
    agent = str(meta.get("agent") or "").strip().lower()
    session = str(meta.get("agent_session_id") or "").strip()
    runtime_session = str(
        meta.get("runtime_session_id") or meta.get("vibecrafted_session_id") or ""
    ).strip()
    if not agent:
        raise MessageControlError("provider_missing")
    if session.lower() in _PLACEHOLDER_IDS:
        session = ""
    if runtime_session and session == runtime_session:
        raise MessageControlError("provider_session_is_runtime_session")
    return agent, session, runtime_session


def resolve_message_run(*, run_id: str = "", session: str = "") -> str:
    """Bind a public selector to exactly one recorded run.

    A provider session can span several resumed runs. Never guess which
    executor owns it; callers can use the exact run id in that case.
    """
    target = str(run_id or "").strip()
    token = str(session or "").strip()
    if not target and not token:
        raise MessageControlError("run_id_or_session_required")
    if target and not _SAFE_ID.fullmatch(target):
        raise MessageControlError("invalid_run_id")
    if token and not _SAFE_ID.fullmatch(token):
        raise MessageControlError("invalid_session_id")
    if token.lower() in _PLACEHOLDER_IDS:
        raise MessageControlError("invalid_session_id")
    if target:
        _, native, runtime = _run_identity(target)
        resolved = resolve_run(target)
        meta = _read_json(resolved.meta) if resolved.meta else {}
        identities = {
            native,
            runtime,
            str(meta.get("session_id") or "").strip(),
            str(meta.get("vibecrafted_session_id") or "").strip(),
        }
        if token and token not in identities:
            raise MessageControlError("session_run_mismatch")
        return target
    root = control_plane_home() / "runtime_runs"
    matches: set[str] = set()
    if root.is_dir():
        for path in root.glob("*/meta.json"):
            if not _SAFE_ID.fullmatch(path.parent.name):
                continue
            meta = _read_json(path)
            identities = {
                str(meta.get(key) or "").strip()
                for key in (
                    "agent_session_id",
                    "runtime_session_id",
                    "vibecrafted_session_id",
                    "session_id",
                )
            }
            if token in identities:
                matches.add(path.parent.name)
    if not matches:
        raise MessageControlError("session_not_resolved")
    if len(matches) != 1:
        raise MessageControlError("session_ambiguous_use_run_id")
    return matches.pop()


def pending_messages(*, run_id: str = "", session: str = "") -> list[dict[str, Any]]:
    """Read unacknowledged inbox messages without consuming them."""
    target = resolve_message_run(run_id=run_id, session=session)
    result: list[dict[str, Any]] = []
    for path in _outbox_root().glob("msg-*.json"):
        record = _read_json(path)
        if record.get("run_id") != target:
            continue
        if record.get("delivery_state") != "inbox_pending":
            continue
        result.append(record)
    return sorted(
        result, key=lambda row: (str(row.get("created_at")), str(row.get("message_id")))
    )


def acknowledge_message(
    message_id: str, *, run_id: str = "", session: str = ""
) -> dict[str, Any]:
    """Record explicit recipient acknowledgement, without claiming execution."""
    target = resolve_message_run(run_id=run_id, session=session)
    with run_mutation_locks(control_plane_home(), run_id=target):
        record = inspect_message(message_id)
        if record is None:
            raise MessageControlError("message_not_found")
        if record.get("run_id") != target:
            raise MessageControlError("message_target_mismatch")
        if record.get("delivery_state") not in {"inbox_pending", "agent_acknowledged"}:
            raise MessageControlError("message_not_in_inbox")
        if record.get("delivery_state") == "agent_acknowledged":
            return record
        updated = {
            **record,
            "delivery_state": "agent_acknowledged",
            "agent_ack_state": "claimed_by_recipient",
            "updated_at": _now(),
            "acknowledged_at": _now(),
        }
        _write_json_durable(_message_path(message_id), updated)
        return updated


def _provider_argv(provider: str, session: str, text: str) -> list[str]:
    if provider == "codex":
        # Queue is Codex's native steering primitive. Do not use exec resume:
        # that starts another writer and is not a message delivery operation.
        return ["codex", "queue", "--thread", session, "--message", text]
    raise MessageControlError(f"provider_steering_unsupported:{provider or 'unknown'}")


def _record_failure(
    record: dict[str, Any], *, state: str, reason: str
) -> dict[str, Any]:
    updated = dict(record)
    updated.update(
        {
            "delivery_state": state,
            "updated_at": _now(),
            "failure": {"reason": reason, "at": _now()},
        }
    )
    _write_json_durable(_message_path(str(updated["message_id"])), updated)
    return updated


def _queue_attempt_failure(
    record: dict[str, Any],
    attempt: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    """Persist a typed queue failure without exception or argv payload."""

    updated = dict(record)
    updated["attempts"] = [
        *list(record.get("attempts") or []),
        {**attempt, "finished_at": _now(), "outcome": reason},
    ]
    return _record_failure(updated, state="retryable_failure", reason=reason)


def inspect_message(message_id: str) -> dict[str, Any] | None:
    """Return one durable message receipt without inferring semantic ACK."""

    candidate = str(message_id or "").strip()
    if not candidate:
        raise MessageControlError("message_id_required")
    payload = _read_json(_message_path(candidate))
    return payload if payload else None


def send_message(
    *,
    run_id: str = "",
    session: str = "",
    text: str,
    idempotency_key: str = "",
    retry: bool = False,
    env: dict[str, str] | None = None,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    """Persist intent, then submit one native provider queue operation.

    A crash after ``recorded`` deliberately leaves the state unresolved.
    Reuse of a key is keyed by body digest; a different target run is a
    conflict, not a replay. ``retry`` resubmits only unresolved or failed
    receipts. ``provider_accepted`` is never submitted again. Timeouts are
    ambiguous: the typed reason does not claim exactly-once delivery.
    """

    target = resolve_message_run(run_id=run_id, session=session)
    body = str(text or "")
    if not body.strip():
        raise MessageControlError("message_empty")
    if len(body.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise MessageControlError("message_too_large")
    key = str(idempotency_key or "").strip() or f"message:{uuid.uuid4()}"

    with run_mutation_locks(control_plane_home(), run_id=target, idempotency_key=key):
        idempotency = _read_json(_idempotency_path(key))
        prior_id = str(idempotency.get("message_id") or "")
        if prior_id:
            prior = inspect_message(prior_id)
            if prior is None:
                raise MessageControlError("idempotency_receipt_missing")
            if str(prior.get("text_digest") or "") != _digest(body):
                raise MessageControlError("idempotency_key_payload_mismatch")
            if str(prior.get("run_id") or "") != target:
                raise MessageControlError("idempotency_key_run_mismatch")
            state = str(prior.get("delivery_state") or "")
            if (
                not retry
                or state == "provider_accepted"
                or state not in _UNRESOLVED_OR_FAILED
            ):
                return {**prior, "idempotent_replay": True}
            record = prior
        else:
            provider, native_session, runtime_session = _run_identity(target)
            if provider not in _INBOX_PROVIDERS and provider != "codex":
                raise MessageControlError(f"provider_steering_unsupported:{provider}")
            inbox = provider in _INBOX_PROVIDERS or not native_session
            message_id = f"msg-{uuid.uuid4()}"
            record = {
                "schema": MESSAGE_SCHEMA,
                "message_id": message_id,
                "idempotency_key": key,
                "run_id": target,
                "provider": provider,
                "provider_session_id": native_session,
                "runtime_session_id": runtime_session,
                "text": body,
                "text_digest": _digest(body),
                "text_preview": f"sha256:{_digest(body)[:12]}; bytes={len(body.encode('utf-8'))}",
                "delivery_state": "inbox_pending" if inbox else "recorded",
                "agent_ack_state": "unobserved",
                "created_at": _now(),
                "updated_at": _now(),
                "attempts": [],
            }
            _write_json_durable(_message_path(message_id), record)
            _write_json_durable(
                _idempotency_path(key),
                {
                    "schema": MESSAGE_SCHEMA,
                    "message_id": message_id,
                    "created_at": _now(),
                },
            )

        provider = str(record.get("provider") or "")
        session = str(record.get("provider_session_id") or "")
        if record.get("delivery_state") in {"inbox_pending", "agent_acknowledged"}:
            return record
        try:
            argv = _provider_argv(provider, session, body)
        except MessageControlError as exc:
            return _record_failure(record, state="permanent_failure", reason=str(exc))

        command_env = selected_runtime_environment(dict(env or os.environ))
        try:
            command = _resolve_agent_command(provider, argv, command_env)
        except (OSError, ValueError) as exc:
            return _record_failure(
                record,
                state="retryable_failure",
                reason=f"provider_command_unavailable:{type(exc).__name__}",
            )
        attempt = {"started_at": _now(), "argv": command[:3]}
        try:
            completed = runner(
                command,
                env=command_env,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # TimeoutExpired stringifies the full argv, including --message.
            return _queue_attempt_failure(
                record, attempt, reason="provider_queue_timeout"
            )
        except OSError:
            return _queue_attempt_failure(
                record, attempt, reason="provider_queue_unavailable"
            )

        stdout = str(completed.stdout or "")
        stderr = str(completed.stderr or "")
        receipt = {
            **attempt,
            "finished_at": _now(),
            "exit_code": int(completed.returncode),
            "stdout_digest": _digest(stdout),
            "stderr_digest": _digest(stderr),
        }
        updated = dict(record)
        updated["attempts"] = [*list(record.get("attempts") or []), receipt]
        updated["updated_at"] = _now()
        if completed.returncode == 0:
            updated["delivery_state"] = "provider_accepted"
            updated["provider_receipt"] = receipt
            # Explicitly retain the absence of semantic evidence.
            updated["agent_ack_state"] = "unobserved"
        else:
            updated["delivery_state"] = "retryable_failure"
            updated["failure"] = {
                "reason": "provider_queue_nonzero",
                "at": _now(),
                "provider_receipt": receipt,
            }
        _write_json_durable(_message_path(str(updated["message_id"])), updated)
        return updated
