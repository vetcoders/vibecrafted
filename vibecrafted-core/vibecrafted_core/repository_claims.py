"""Atomic repository-mutation claims for Living Tree coordination.

The filesystem below ``control_plane/repository_claims`` is the authority.
Server, MCP, Slack, and UI surfaces may project these records, but a local
claim never depends on any of them being available.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from vibecrafted_core.control_plane import control_plane_home
from vibecrafted_core.delivery.store import atomic_write_json
from vibecrafted_core.events import append_event

CLAIM_SCHEMA = "vibecrafted.repository-mutation-claim.v1"
REGISTRY_SCHEMA = "vibecrafted.repository-mutation-registry.v1"
RESULT_SCHEMA = "vibecrafted.repository-mutation-claim-result.v1"
EVENT_SCHEMA = "vibecrafted.repository-mutation-event.v1"
DEFAULT_DEAD_OWNER_GRACE_SECONDS = 30.0


class ClaimContractError(RuntimeError):
    """Raised when a claim request or persisted registry is invalid."""


class ClaimConflictError(ClaimContractError):
    """Raised when an atomic acquire overlaps one or more active claims."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        conflicts = result.get("conflicts") or []
        detail = "; ".join(_conflict_text(item) for item in conflicts)
        super().__init__(f"repository mutation claim refused: {detail or 'conflict'}")


class RepositoryClaimRegistry:
    """One lock-serialized claim registry on the existing control-plane plane."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        grace_seconds: float = DEFAULT_DEAD_OWNER_GRACE_SECONDS,
        now: Callable[[], datetime] | None = None,
        emit_events: bool = True,
    ) -> None:
        if grace_seconds < 0:
            raise ClaimContractError("dead-owner grace must be non-negative")
        self.root = (root or control_plane_home() / "repository_claims").resolve()
        self.registry_path = self.root / "registry.json"
        self.lock_path = self.root / "registry.lock"
        self.grace = timedelta(seconds=grace_seconds)
        self._now = now or (lambda: datetime.now(UTC))
        self._emit_events = emit_events

    def acquire(
        self,
        *,
        repo: str | Path,
        owned_paths: Iterable[str],
        run_id: str,
        session_id: str,
        agent: str,
        worktree: str | Path | None = None,
        branch: str = "",
        pid: int | None = None,
        pgid: int | None = None,
    ) -> dict[str, Any]:
        """Atomically acquire every path or acquire none; never steal a live claim."""
        identity = canonical_repo_identity(repo, worktree=worktree)
        paths = normalize_owned_paths(owned_paths, identity["worktree_root"])
        if not run_id.strip() or not session_id.strip() or not agent.strip():
            raise ClaimContractError("run_id, session_id, and agent are required")
        owner_pid = int(pid if pid is not None else os.getpid())
        if owner_pid <= 0:
            raise ClaimContractError("claim owner pid must be positive")
        owner_pgid = int(
            pgid if pgid is not None else _safe_getpgid(owner_pid) or owner_pid
        )
        timestamp = self._now()
        emitted: list[tuple[str, str, dict[str, Any]]] = []
        with self._locked_registry() as registry:
            reclaimed = self._reclaim_dead_locked(registry, timestamp, emitted)
            conflicts = self._conflicts_locked(
                registry, identity["repo_key"], paths, timestamp
            )
            if conflicts:
                result = _result(
                    False,
                    "acquire",
                    conflicts=conflicts,
                    reclaimed=reclaimed,
                )
                emitted.append(
                    (
                        "repository_mutation.conflict",
                        run_id,
                        {
                            "request": {
                                "repo_key": identity["repo_key"],
                                "repo_identity": identity["repo_identity"],
                                "owned_paths": list(paths),
                                "session_id": session_id,
                                "agent": agent,
                            },
                            "conflicts": conflicts,
                        },
                    )
                )
            else:
                claim_id = str(uuid.uuid4())
                stamp = _iso(timestamp)
                claim = {
                    "schema": CLAIM_SCHEMA,
                    "claim_id": claim_id,
                    **identity,
                    "owned_paths": list(paths),
                    "run_id": run_id,
                    "session_id": session_id,
                    "agent": agent,
                    "branch": branch
                    or _git(identity["worktree_root"], "branch", "--show-current"),
                    "pid": owner_pid,
                    "pgid": owner_pgid,
                    "process_start": _process_start_marker(owner_pid),
                    "acquired_at": stamp,
                    "heartbeat_at": stamp,
                    "updated_at": stamp,
                    "dead_since": None,
                }
                registry["claims"][claim_id] = claim
                result = _result(True, "acquire", claim=claim, reclaimed=reclaimed)
                emitted.append(
                    (
                        "repository_mutation.acquired",
                        run_id,
                        {"claim": claim, "reclaimed": reclaimed},
                    )
                )
        self._emit_all(emitted)
        if not result["ok"]:
            raise ClaimConflictError(result)
        return result

    def heartbeat(
        self, claim_id: str, *, run_id: str, session_id: str
    ) -> dict[str, Any]:
        """Refresh a claim heartbeat after proving the caller's durable identity."""
        with self._locked_registry() as registry:
            claim = self._owned_claim(registry, claim_id, run_id, session_id)
            stamp = _iso(self._now())
            claim["heartbeat_at"] = stamp
            claim["updated_at"] = stamp
            claim["dead_since"] = None
            return _result(True, "heartbeat", claim=claim)

    def adopt_liveness_owner(
        self,
        claim_id: str,
        *,
        run_id: str,
        session_id: str,
        pid: int,
    ) -> dict[str, Any]:
        """Bind liveness to a spawned worker without changing claim identity."""
        if pid <= 0:
            raise ClaimContractError("claim owner pid must be positive")
        with self._locked_registry() as registry:
            claim = self._owned_claim(registry, claim_id, run_id, session_id)
            claim["pid"] = int(pid)
            claim["pgid"] = int(_safe_getpgid(pid) or pid)
            claim["process_start"] = _process_start_marker(pid)
            stamp = _iso(self._now())
            claim["heartbeat_at"] = stamp
            claim["updated_at"] = stamp
            claim["dead_since"] = None
            return _result(True, "adopt-liveness-owner", claim=claim)

    def release(
        self,
        claim_id: str,
        *,
        run_id: str,
        session_id: str,
        force: bool = False,
        reason: str = "",
    ) -> dict[str, Any]:
        """Release immediately and idempotently; force requires audit evidence."""
        emitted: list[tuple[str, str, dict[str, Any]]] = []
        with self._locked_registry() as registry:
            raw = registry["claims"].get(claim_id)
            if raw is None:
                return _result(True, "release", released=False)
            if not isinstance(raw, dict):
                raise ClaimContractError(f"claim {claim_id!r} is malformed")
            if force:
                if not reason.strip():
                    raise ClaimContractError(
                        "force release requires a non-empty reason"
                    )
            else:
                self._owned_claim(registry, claim_id, run_id, session_id)
            claim = dict(registry["claims"].pop(claim_id))
            audit = {
                "claim": claim,
                "forced": force,
                "reason": reason.strip(),
                "released_by_pid": os.getpid(),
                "released_at": _iso(self._now()),
            }
            emitted.append(("repository_mutation.released", run_id, audit))
            result = _result(
                True,
                "force-release" if force else "release",
                claim=claim,
                released=True,
            )
        self._emit_all(emitted)
        return result

    def status(self, claim_id: str) -> dict[str, Any]:
        """Return one claim plus current owner liveness and grace state."""
        with self._locked_registry() as registry:
            self._mark_dead_since_locked(registry, self._now())
            raw = registry["claims"].get(claim_id)
            if not isinstance(raw, dict):
                return _result(False, "status", found=False)
            return _result(True, "status", claim=self._project(raw), found=True)

    def list(self, *, repo: str | Path | None = None) -> dict[str, Any]:
        """List claims, optionally scoped to the canonical repository identity."""
        repo_key = canonical_repo_identity(repo)["repo_key"] if repo else ""
        with self._locked_registry() as registry:
            self._mark_dead_since_locked(registry, self._now())
            claims = [
                self._project(raw)
                for raw in registry["claims"].values()
                if isinstance(raw, dict)
                and (not repo_key or raw.get("repo_key") == repo_key)
            ]
        claims.sort(
            key=lambda item: (str(item.get("repo_identity")), str(item.get("claim_id")))
        )
        conflicts = _internal_conflicts(claims)
        return _result(
            True,
            "list",
            claims=claims,
            conflicts=conflicts,
            stale_claims=[item for item in claims if item["owner_liveness"] != "alive"],
        )

    def health(self, *, repo: str | Path | None = None) -> dict[str, Any]:
        """Doctor projection naming stale owners and impossible stored overlaps."""
        result = self.list(repo=repo)
        result["action"] = "health"
        result["ok"] = not result["stale_claims"] and not result["conflicts"]
        return result

    def _owned_claim(
        self, registry: dict[str, Any], claim_id: str, run_id: str, session_id: str
    ) -> dict[str, Any]:
        raw = registry["claims"].get(claim_id)
        if not isinstance(raw, dict):
            raise ClaimContractError(f"claim {claim_id!r} not found")
        if raw.get("run_id") != run_id or raw.get("session_id") != session_id:
            raise ClaimContractError(
                f"claim {claim_id} belongs to run {raw.get('run_id') or '?'} "
                f"session {raw.get('session_id') or '?'}"
            )
        return raw

    def _conflicts_locked(
        self,
        registry: dict[str, Any],
        repo_key: str,
        requested: tuple[str, ...],
        now: datetime,
    ) -> list[dict[str, Any]]:
        conflicts: list[dict[str, Any]] = []
        for raw in registry["claims"].values():
            if not isinstance(raw, dict) or raw.get("repo_key") != repo_key:
                continue
            overlaps = _overlapping_paths(
                requested, tuple(raw.get("owned_paths") or ())
            )
            if not overlaps:
                continue
            projected = self._project(raw, now=now)
            projected["overlapping_paths"] = overlaps
            conflicts.append(projected)
        return conflicts

    def _mark_dead_since_locked(self, registry: dict[str, Any], now: datetime) -> None:
        for raw in registry["claims"].values():
            if not isinstance(raw, dict):
                continue
            if _owner_liveness(raw) == "alive":
                raw["dead_since"] = None
            elif not raw.get("dead_since"):
                raw["dead_since"] = _iso(now)

    def _reclaim_dead_locked(
        self,
        registry: dict[str, Any],
        now: datetime,
        emitted: list[tuple[str, str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        self._mark_dead_since_locked(registry, now)
        reclaimed: list[dict[str, Any]] = []
        for claim_id, raw in list(registry["claims"].items()):
            if not isinstance(raw, dict) or _owner_liveness(raw) == "alive":
                continue
            dead_since = _parse_time(raw.get("dead_since"))
            if dead_since is None or now - dead_since < self.grace:
                continue
            claim = dict(registry["claims"].pop(claim_id))
            evidence = {
                "claim": claim,
                "owner_liveness": _owner_liveness(claim),
                "dead_since": claim.get("dead_since"),
                "grace_seconds": self.grace.total_seconds(),
                "reclaimed_at": _iso(now),
            }
            reclaimed.append(evidence)
            emitted.append(
                (
                    "repository_mutation.reclaimed",
                    str(claim.get("run_id") or ""),
                    evidence,
                )
            )
        return reclaimed

    def _project(
        self, raw: dict[str, Any], *, now: datetime | None = None
    ) -> dict[str, Any]:
        claim = dict(raw)
        liveness = _owner_liveness(claim)
        claim["owner_liveness"] = liveness
        dead_since = _parse_time(claim.get("dead_since"))
        current = now or self._now()
        claim["reclaimable"] = bool(
            liveness != "alive"
            and dead_since is not None
            and current - dead_since >= self.grace
        )
        return claim

    @contextmanager
    def _locked_registry(self) -> Iterator[dict[str, Any]]:
        self.root.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.lock_path, flags, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            registry = self._read_registry()
            yield registry
            registry["updated_at"] = _iso(self._now())
            atomic_write_json(self.registry_path, registry)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read_registry(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            stamp = _iso(self._now())
            return {
                "schema": REGISTRY_SCHEMA,
                "created_at": stamp,
                "updated_at": stamp,
                "claims": {},
            }
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ClaimContractError(f"claim registry unreadable: {exc}") from exc
        if payload.get("schema") != REGISTRY_SCHEMA or not isinstance(
            payload.get("claims"), dict
        ):
            raise ClaimContractError("claim registry schema mismatch")
        return payload

    def _emit_all(self, events: Iterable[tuple[str, str, dict[str, Any]]]) -> None:
        if not self._emit_events:
            return
        for kind, run_id, payload in events:
            append_event(
                kind,
                run_id,
                kind.replace("repository_mutation.", "repository mutation "),
                {"schema": EVENT_SCHEMA, **payload},
            )


def canonical_repo_identity(
    repo: str | Path, *, worktree: str | Path | None = None
) -> dict[str, str]:
    """Resolve clone-stable display identity plus clone-local common Git root."""
    worktree_root = Path(worktree or repo).expanduser().resolve()
    observed = _git(worktree_root, "rev-parse", "--show-toplevel")
    if not observed:
        raise ClaimContractError(f"not a Git repository: {worktree_root}")
    worktree_root = Path(observed).resolve()
    common_raw = _git(
        worktree_root, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    if not common_raw:
        raise ClaimContractError(f"cannot resolve Git common dir for {worktree_root}")
    common_dir = Path(common_raw).resolve()
    canonical_root = common_dir.parent if common_dir.name == ".git" else worktree_root
    remote = _git(worktree_root, "remote", "get-url", "origin")
    repo_identity = _remote_identity(remote) or f"local/{canonical_root.name}"
    repo_key = hashlib.sha256(str(canonical_root).encode("utf-8")).hexdigest()
    return {
        "repo_key": repo_key,
        "repo_identity": repo_identity,
        "canonical_repo_root": str(canonical_root),
        "worktree_root": str(worktree_root),
    }


def normalize_owned_paths(
    owned_paths: Iterable[str], worktree_root: str | Path
) -> tuple[str, ...]:
    """Normalize explicit paths to minimal repo-relative POSIX anchors."""
    root = Path(worktree_root).resolve()
    normalized: set[str] = set()
    for raw in owned_paths:
        text = str(raw).strip()
        if not text:
            raise ClaimContractError("owned paths cannot be empty")
        candidate = Path(text).expanduser()
        absolute = (
            candidate.resolve()
            if candidate.is_absolute()
            else (root / candidate).resolve()
        )
        try:
            relative = absolute.relative_to(root)
        except ValueError as exc:
            raise ClaimContractError(f"owned path escapes repository: {raw!r}") from exc
        rendered = relative.as_posix().rstrip("/") or "."
        if rendered == ".git" or rendered.startswith(".git/"):
            raise ClaimContractError("Git administrative paths cannot be claimed")
        normalized.add(rendered)
    if not normalized:
        raise ClaimContractError("at least one owned path is required")
    minimal: list[str] = []
    for path in sorted(
        normalized, key=lambda value: (len(PurePosixPath(value).parts), value)
    ):
        if any(_path_overlaps(path, parent) for parent in minimal):
            continue
        minimal.append(path)
    return tuple(sorted(minimal))


def _overlapping_paths(
    left: tuple[str, ...], right: tuple[str, ...]
) -> list[dict[str, str]]:
    return [
        {"requested": requested, "owned": owned}
        for requested in left
        for owned in right
        if _path_overlaps(requested, owned)
    ]


def _path_overlaps(left: str, right: str) -> bool:
    if left == "." or right == ".":
        return True
    left_parts = PurePosixPath(left).parts
    right_parts = PurePosixPath(right).parts
    shorter = min(len(left_parts), len(right_parts))
    return left_parts[:shorter] == right_parts[:shorter]


def _owner_liveness(claim: dict[str, Any]) -> str:
    pid = claim.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return "unknown"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pgid = claim.get("pgid")
        if isinstance(pgid, int) and pgid == pid and _process_group_alive(pgid):
            return "alive"
        return "dead"
    except PermissionError:
        return "alive"
    except OSError:
        return "unknown"
    recorded = str(claim.get("process_start") or "")
    observed = _process_start_marker(pid)
    if recorded and observed and recorded != observed:
        return "pid-reused"
    return "alive"


def _process_group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _process_start_marker(pid: int) -> str:
    try:
        proc = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return " ".join(proc.stdout.split()) if proc.returncode == 0 else ""


def _internal_conflicts(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for index, left in enumerate(claims):
        for right in claims[index + 1 :]:
            if left.get("repo_key") != right.get("repo_key"):
                continue
            overlaps = _overlapping_paths(
                tuple(left.get("owned_paths") or ()),
                tuple(right.get("owned_paths") or ()),
            )
            if overlaps:
                conflicts.append(
                    {
                        "left_claim_id": left.get("claim_id"),
                        "right_claim_id": right.get("claim_id"),
                        "overlapping_paths": overlaps,
                    }
                )
    return conflicts


def _result(ok: bool, action: str, **fields: Any) -> dict[str, Any]:
    return {"schema": RESULT_SCHEMA, "ok": ok, "action": action, **fields}


def _conflict_text(conflict: dict[str, Any]) -> str:
    pairs = ", ".join(
        f"{item.get('requested')} ↔ {item.get('owned')}"
        for item in conflict.get("overlapping_paths") or []
    )
    return (
        f"run {conflict.get('run_id') or '?'} session {conflict.get('session_id') or '?'} "
        f"agent {conflict.get('agent') or '?'} owns {pairs or conflict.get('owned_paths')}"
    )


def _remote_identity(remote: str) -> str:
    tail = remote.strip().removesuffix(".git")
    if "://" in tail:
        tail = tail.split("://", 1)[1]
        tail = tail.split("/", 1)[1] if "/" in tail else ""
    elif ":" in tail.split("/", 1)[0]:
        tail = tail.split(":", 1)[1]
    return tail.strip("/")


def _git(repo: str | Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _safe_getpgid(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except (ProcessLookupError, PermissionError, OSError):
        return None


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC)


def _identity_defaults(args: argparse.Namespace) -> tuple[str, str, str]:
    run_id = args.run_id or os.environ.get("VIBECRAFTED_RUN_ID", "")
    session_id = (
        args.session_id
        or os.environ.get("VIBECRAFTED_SESSION_ID", "")
        or os.environ.get("CODEX_SESSION_ID", "")
        or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    )
    agent = args.agent or os.environ.get("VIBECRAFTED_AGENT", "")
    return run_id, session_id, agent


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibecrafted claims",
        description="Atomic local repository-mutation claims for Living Tree sessions.",
    )
    parser.add_argument(
        "--json", action="store_true", help="stable machine-readable output"
    )
    sub = parser.add_subparsers(dest="action", required=True)
    acquire = sub.add_parser(
        "acquire", help="atomically claim one or more repo-relative paths"
    )
    acquire.add_argument("paths", nargs="+")
    acquire.add_argument("--repo", default=".")
    acquire.add_argument("--run-id", default="")
    acquire.add_argument("--session-id", default="")
    acquire.add_argument("--agent", default="")
    acquire.add_argument("--branch", default="")
    heartbeat = sub.add_parser("heartbeat", help="refresh one owned claim")
    heartbeat.add_argument("claim_id")
    heartbeat.add_argument("--run-id", default="")
    heartbeat.add_argument("--session-id", default="")
    heartbeat.add_argument("--agent", default="")
    status = sub.add_parser("status", help="show one claim and owner liveness")
    status.add_argument("claim_id")
    listing = sub.add_parser("list", help="list active and stale claims")
    listing.add_argument("--repo", default="")
    release = sub.add_parser("release", help="release one owned claim immediately")
    release.add_argument("claim_id")
    release.add_argument("--run-id", default="")
    release.add_argument("--session-id", default="")
    release.add_argument("--agent", default="")
    force = sub.add_parser(
        "force-release", help="operator override with durable audit evidence"
    )
    force.add_argument("claim_id")
    force.add_argument("--reason", required=True)
    force.add_argument("--run-id", default="operator")
    force.add_argument("--session-id", default="operator")
    force.add_argument("--agent", default="operator")
    return parser


def claims_cli_main(argv: Sequence[str] | None = None) -> int:
    """Public CLI entrypoint shared by hooks, humans, and dispatch."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    registry = RepositoryClaimRegistry()
    try:
        if args.action == "acquire":
            run_id, session_id, agent = _identity_defaults(args)
            result = registry.acquire(
                repo=args.repo,
                owned_paths=args.paths,
                run_id=run_id,
                session_id=session_id,
                agent=agent,
                branch=args.branch,
            )
        elif args.action == "heartbeat":
            run_id, session_id, _agent = _identity_defaults(args)
            result = registry.heartbeat(
                args.claim_id, run_id=run_id, session_id=session_id
            )
        elif args.action == "status":
            result = registry.status(args.claim_id)
        elif args.action == "list":
            result = registry.list(repo=args.repo or None)
        elif args.action == "release":
            run_id, session_id, _agent = _identity_defaults(args)
            result = registry.release(
                args.claim_id, run_id=run_id, session_id=session_id
            )
        else:
            result = registry.release(
                args.claim_id,
                run_id=args.run_id,
                session_id=args.session_id,
                force=True,
                reason=args.reason,
            )
    except ClaimConflictError as exc:
        result = exc.result
    except ClaimContractError as exc:
        result = _result(False, args.action, error=str(exc))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        _print_human(result)
    return 0 if result.get("ok") else 1


def _print_human(result: dict[str, Any]) -> None:
    if result.get("error"):
        print(f"claim error: {result['error']}", file=sys.stderr)
        return
    if result.get("conflicts"):
        for conflict in result["conflicts"]:
            print(f"claim conflict: {_conflict_text(conflict)}", file=sys.stderr)
        return
    claim = result.get("claim")
    if isinstance(claim, dict):
        print(
            f"claim {claim.get('claim_id')}: {claim.get('repo_identity')} "
            f"{', '.join(claim.get('owned_paths') or [])}; "
            f"owner {claim.get('run_id')}/{claim.get('session_id')} "
            f"pid {claim.get('pid')} liveness {claim.get('owner_liveness', 'alive')}"
        )
        return
    for item in result.get("claims") or []:
        print(
            f"{item.get('claim_id')} {item.get('repo_identity')} "
            f"{','.join(item.get('owned_paths') or [])} "
            f"{item.get('run_id')}/{item.get('session_id')} "
            f"pid={item.get('pid')} {item.get('owner_liveness')}"
        )
    if result.get("action") == "release" and result.get("released") is False:
        print("claim already released")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(claims_cli_main())


__all__ = [
    "CLAIM_SCHEMA",
    "ClaimConflictError",
    "ClaimContractError",
    "RepositoryClaimRegistry",
    "canonical_repo_identity",
    "claims_cli_main",
    "normalize_owned_paths",
]
