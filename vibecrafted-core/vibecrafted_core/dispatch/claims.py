"""Canonical Python admission for dispatch claim POSTs; never settles a cut."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from vibecrafted_core.control_plane import control_plane_home

from .receipts import DispatchReceiptStore, ReceiptContractError
from .verify import sanitize_env

MAX_CLAIM_BYTES = 65536
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_REQUIRED = {"run_id", "cut_id", "commit_sha", "report_path", "measurements"}


def _strings(value: Any, name: str, *, required: bool = True) -> list[str]:
    if not isinstance(value, list) or (required and not value):
        raise ReceiptContractError(f"{name} must be a non-empty string array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ReceiptContractError(f"{name} contains an empty or non-string value")
    return list(value)


def parse_claim(payload: Any) -> dict[str, Any]:
    """Validate bounded data only; no request-supplied commands or settlement."""
    if not isinstance(payload, dict) or not _REQUIRED.issubset(payload):
        raise ReceiptContractError(
            "claim requires run_id, cut_id, commit_sha, report_path, measurements"
        )
    if payload.keys() - (_REQUIRED | {"checkpoint"}):
        raise ReceiptContractError(
            "unsupported claim fields; settlement belongs to the supervisor"
        )
    if len(json.dumps(payload).encode()) > MAX_CLAIM_BYTES:
        raise ReceiptContractError("claim is too large")
    for name in ("run_id", "cut_id"):
        if not isinstance(payload[name], str) or not _ID.fullmatch(payload[name]):
            raise ReceiptContractError(f"invalid {name}")
    sha = payload["commit_sha"]
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise ReceiptContractError("commit_sha must be a full commit SHA")
    report = payload["report_path"]
    if not isinstance(report, str) or not Path(report).is_absolute():
        raise ReceiptContractError("report_path must be absolute")
    checkpoint = payload.get("checkpoint")
    claim = {key: payload[key] for key in _REQUIRED}
    claim["measurements"] = _strings(
        payload["measurements"], "measurements", required=checkpoint is None
    )
    if checkpoint is not None:
        if not isinstance(checkpoint, dict) or set(checkpoint) != {
            "owned_scope",
            "skipped_controls",
        }:
            raise ReceiptContractError(
                "checkpoint requires owned_scope and skipped_controls; workers cannot close embargo"
            )
        claim["checkpoint"] = {
            key: _strings(checkpoint[key], f"checkpoint.{key}")
            for key in ("owned_scope", "skipped_controls")
        }
    return claim


def claim_git_state(root: str) -> tuple[str, str]:
    """Check HEAD and all non-ignored changes using the writer shell."""

    def git(*args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
            env=sanitize_env(),
            timeout=10,
            check=False,
        )
        if proc.returncode:
            raise ReceiptContractError("claim runtime is not a readable Git checkout")
        return proc.stdout.strip()

    return git("rev-parse", "HEAD"), git(
        "status", "--porcelain", "--untracked-files=all"
    )


def submit_claim(
    payload: Any, *, store: DispatchReceiptStore | None = None
) -> dict[str, Any]:
    """Persist `[~]` in the existing dispatch ledger, leaving tracker untouched."""
    claim = parse_claim(payload)
    if store is None:
        root = control_plane_home() / "dispatches" / claim["run_id"]
        if not (root / "receipts.json").is_file():
            raise ReceiptContractError("dispatch receipt ledger not found")
        # No new run/cut identities are created by admission. record_claim
        # checks the existing entry under its cross-process ledger lock.
        store = DispatchReceiptStore(claim["run_id"], (), root=root, create=False)
    if store.run_id != claim["run_id"]:
        raise ReceiptContractError("dispatch claim identity mismatch")

    def validate(entry: dict[str, Any], ledger: dict[str, Any]) -> None:
        expected_report = str(entry.get("report_path") or "")
        report = Path(claim["report_path"])
        if not expected_report or report.resolve() != Path(expected_report).resolve():
            raise ReceiptContractError(
                "report_path does not match the registered worker report"
            )
        if not report.is_file() or not report.stat().st_size:
            raise ReceiptContractError("worker report is missing or empty")
        root = str(entry.get("worktree_path") or ledger.get("repo_root") or "")
        if not root:
            raise ReceiptContractError("cut runtime root is missing")
        head, dirty = claim_git_state(root)
        if head != claim["commit_sha"] or dirty:
            raise ReceiptContractError("claim SHA must be the clean runtime HEAD")
        if entry.get("compile_embargo") and "checkpoint" not in claim:
            raise ReceiptContractError(
                "structural cut requires an unverified embargo checkpoint"
            )
        if "checkpoint" in claim and not entry.get("compile_embargo"):
            raise ReceiptContractError(
                "checkpoint requires a plan-owned compile embargo"
            )

    store.record_claim(claim["cut_id"], claim, validate=validate)
    return {
        "status": "claim_received",
        "marker": "[~]",
        "run_id": claim["run_id"],
        "cut_id": claim["cut_id"],
        "verification": "unverified",
        "writer": "vibecrafted_core.dispatch.claims",
    }


def main() -> int:
    """Fixed stdin/stdout boundary called by the read-model HTTP server."""
    try:
        raw = sys.stdin.buffer.read(MAX_CLAIM_BYTES + 1)
        if len(raw) > MAX_CLAIM_BYTES:
            raise ReceiptContractError("claim is too large")
        result = submit_claim(json.loads(raw))
    except (
        ReceiptContractError,
        ValueError,
        OSError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
