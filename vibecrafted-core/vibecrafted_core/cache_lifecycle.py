"""Task-owned reconstructible caches; leases never expire by PID or age.

Registration records ownership, not acceptance. The control plane's exact
trust settlement grants acceptance separately. Source and durable evidence
never enter this lifecycle. All uncertainty retains payload.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

from . import portable_lock as lock
from .server_supervisor import _atomic_json, _read_owned_text
from .settlement import TrustReceiptV1

LEASE_NAME = ".vc-build-lease"
OWNER_SCHEMA = "vibecrafted.task-cache.v1"
SOURCE_ANCHORS = frozenset(
    {
        ".git",
        ".noclean",
        "Cargo.toml",
        "pyproject.toml",
        "package.json",
        "src",
        "store",
        "sessions",
        "reports",
        "artifacts",
        "releases",
        "secrets",
    }
)
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class CacheDeferred(RuntimeError):
    """A failed proof is a deferral, never deletion authority."""


def identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise CacheDeferred(f"unsafe-cache-directory:{path}")
    return info.st_dev, info.st_ino


def directory_chain(path: Path, boundary: Path) -> list[Path]:
    path, boundary = path.absolute(), boundary.absolute()
    if path == boundary or boundary not in path.parents:
        raise CacheDeferred("outside-cache-boundary")
    chain = [path]
    while chain[-1] != boundary:
        chain.append(chain[-1].parent)
    for directory in chain:
        identity(directory)
    return chain


@contextlib.contextmanager
def lease(path: Path, *, exclusive: bool, create: bool = False) -> Iterator[int]:
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    if create:
        flags |= os.O_CREAT
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        named = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise CacheDeferred("unsafe-build-lease")
        lock.flock(fd, (lock.LOCK_EX if exclusive else lock.LOCK_SH) | lock.LOCK_NB)
        named = path.lstat()
        if (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino):
            raise CacheDeferred("build-lease-identity-changed")
        yield fd
    finally:
        os.close(fd)


@contextlib.contextmanager
def build_lease(root: Path) -> Iterator[int]:
    """Hold one regular shared lease for a complete build chain, including gaps."""
    before = identity(root)
    with lease(root / LEASE_NAME, exclusive=False, create=True) as fd:
        if identity(root) != before:
            raise CacheDeferred("cache-identity-changed")
        yield fd
        if identity(root) != before:
            raise CacheDeferred("cache-identity-changed-during-build")


def owner_path(home: Path, path: Path) -> Path:
    key = hashlib.sha256(os.fsencode(path.absolute())).hexdigest()
    return home / "store" / "housekeeping" / "owners" / f"{key}.json"


def _document(path: Path) -> dict:
    raw = _read_owned_text(path)
    if raw is None:
        raise CacheDeferred(f"missing-or-unsafe-receipt:{path}")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise CacheDeferred("invalid-receipt")
    return result


def _run(home: Path, run_id: str) -> dict:
    if not _RUN_ID.fullmatch(run_id):
        raise CacheDeferred("invalid-run-id")
    path = home / "control_plane" / "runs" / f"{run_id}.json"
    directory_chain(path.parent, home)
    run = _document(path)
    if run.get("run_id") != run_id:
        raise CacheDeferred("run-identity-mismatch")
    return run


def register_cache(
    home: Path,
    path: Path,
    run_id: str,
    *,
    created_identity: tuple[int, int],
    dispatch_creation: dict | None = None,
) -> Path:
    """Build owner registers an exact output inode under its actual task root."""
    path, home = path.absolute(), home.absolute()
    directory_chain(path, home)
    if identity(path) != created_identity:
        raise CacheDeferred("cache-creation-identity-changed")
    run = _run(home, run_id)
    root = Path(str(run.get("root") or ""))
    if not root.is_absolute() or root not in path.parents:
        raise CacheDeferred("cache-outside-task-root")
    if path.parts[len(home.parts)] not in {"worktrees", "tmp"}:
        raise CacheDeferred("durable-or-unknown-scope")
    validate_payload(path)
    device, inode = identity(path)
    receipt = owner_path(home, path)
    registration = (
        home / "store/housekeeping/registrations" / f"{uuid.uuid4().hex}.json"
    )
    payload = {
        "schema": OWNER_SCHEMA,
        "path": str(path),
        "run_id": run_id,
        "root": str(root),
        "device": device,
        "inode": inode,
        "reconstructible": True,
        "registration": str(registration),
    }
    if dispatch_creation is not None:
        payload["dispatch_creation"] = dispatch_creation
    # The current registry may advance for a new inode at a reused cache path;
    # the original ownership receipt remains durable and is never overwritten.
    _immutable_json(registration, payload, boundary=home)
    _receipt_parent(home, receipt.parent)
    _atomic_json(receipt, payload)
    return receipt


def _receipt_parent(home: Path, parent: Path) -> None:
    relative = parent.relative_to(home)
    identity(home)
    current = home
    for part in relative.parts:
        current = current / part
        try:
            current.mkdir()
        except FileExistsError:
            pass
        identity(current)


def _immutable_json(path: Path, payload: dict, *, boundary: Path) -> None:
    """Exclusive durable creation; a previous proof can never be replaced."""
    _receipt_parent(boundary, path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _record_leftover(home: Path, stage: Path) -> None:
    leftovers = home / "store/housekeeping/leftovers.txt"
    _receipt_parent(home, leftovers.parent)
    fd = os.open(
        leftovers, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600
    )
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
        ):
            raise CacheDeferred("unsafe-leftover-receipt")
        os.write(fd, os.fsencode(str(stage) + "\n"))
    finally:
        os.close(fd)


def _publish_directory_noreplace(parent_fd: int, source: str, destination: str) -> None:
    """Publish a held private object; never replace an existing public path."""
    name, flags = (
        ("renameatx_np", 0x00000004)
        if sys.platform == "darwin"
        else ("renameat2", 1)
        if sys.platform.startswith("linux")
        else ("", 0)
    )
    library = ctypes.CDLL(None, use_errno=True)
    function = getattr(library, name, None) if name else None
    if function is None:
        raise CacheDeferred("exclusive-directory-publication-unavailable")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    if (
        function(
            parent_fd, os.fsencode(source), parent_fd, os.fsencode(destination), flags
        )
        != 0
    ):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


@contextlib.contextmanager
def dispatch_target_creation(
    home: Path, root: Path, *, target_name: str = "target"
) -> Iterator[tuple[int, int] | None]:
    """Private random staging + held descriptor + exclusive atomic publication.

    The public target is never mkdir'd and then observed as proof. Platforms
    lacking an exclusive rename retain an ordinary unclaimed target instead.
    """
    parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    stage: Path | None = None
    held = None
    try:
        before = identity(root)
        if (os.fstat(parent_fd).st_dev, os.fstat(parent_fd).st_ino) != before:
            raise CacheDeferred("checkout-creation-parent-changed")
        stage = root / f".vc-target-{uuid.uuid4().hex}"
        os.mkdir(stage.name, 0o700, dir_fd=parent_fd)
        held = os.open(
            stage.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
        )
        info = os.fstat(held)
        created_identity = info.st_dev, info.st_ino
        if os.listdir(held) or identity(stage) != created_identity:
            raise CacheDeferred("private-creation-stage-uncertain")
        try:
            _publish_directory_noreplace(parent_fd, stage.name, target_name)
        except OSError as exc:
            if exc.errno not in {
                errno.EEXIST,
                errno.ENOTSUP,
                errno.EINVAL,
                errno.ENOSYS,
            }:
                raise
            yield None
            return
        except CacheDeferred:
            yield None
            return
        if identity(root) != before or identity(root / target_name) != created_identity:
            raise CacheDeferred("published-cache-identity-changed")
        yield created_identity
        if identity(root / target_name) != created_identity:
            raise CacheDeferred("published-cache-identity-changed")
    finally:
        try:
            if stage is not None and (stage.exists() or stage.is_symlink()):
                try:
                    if held is None or identity(stage) != (
                        os.fstat(held).st_dev,
                        os.fstat(held).st_ino,
                    ):
                        raise CacheDeferred("private-stage-identity-changed")
                    os.rmdir(stage.name, dir_fd=parent_fd)  # Empty owned object only.
                except (OSError, CacheDeferred):
                    _record_leftover(home, stage)
        finally:
            if held is not None:
                os.close(held)
            os.close(parent_fd)


def record_dispatch_cache_creation(
    home: Path, geometry, *, created_identity: tuple[int, int]
) -> str:
    """Record the held, exclusively published object in a new linked checkout."""
    path, root = Path(geometry.target_path), Path(geometry.worktree_path)
    home = home.absolute()
    directory_chain(path, home / "worktrees")
    if geometry.integrator_exclusive or path != root / "target":
        raise CacheDeferred("not-new-worker-cache")
    if identity(path) != created_identity:
        raise CacheDeferred("cache-creation-identity-changed")
    common = _checkout_identity(root, geometry.branch, geometry.baseline_sha)
    receipt = home / "store/housekeeping/creations" / f"{uuid.uuid4().hex}.json"
    _immutable_json(
        receipt,
        {
            "schema": "vibecrafted.dispatch-cache-creation.v1",
            "path": str(path),
            "root": str(root),
            "cut_id": geometry.cut_id,
            "branch": geometry.branch,
            "baseline_sha": geometry.baseline_sha,
            "device": created_identity[0],
            "inode": created_identity[1],
            "root_identity": list(identity(root)),
            "git_common_dir": common,
        },
        boundary=home,
    )
    return str(receipt)


def _checkout_identity(root: Path, branch: str, baseline: str) -> str:
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode:
            raise CacheDeferred("checkout-observation-uncertain")
        return result.stdout.strip()

    if Path(git("rev-parse", "--show-toplevel")) != root:
        raise CacheDeferred("checkout-root-mismatch")
    if git("branch", "--show-current") != branch:
        raise CacheDeferred("checkout-branch-mismatch")
    head = git("rev-parse", "HEAD")
    if git("merge-base", baseline, head) != baseline:
        raise CacheDeferred("checkout-baseline-mismatch")
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    registered = git("worktree", "list", "--porcelain").splitlines()
    if f"worktree {root}" not in registered or not (root / ".git").is_file():
        raise CacheDeferred("checkout-not-managed-linked-worktree")
    return common


def retain_cache_for_launch(home: Path, path: Path) -> str:
    """A later attempt invalidates acceptance; it never transfers ownership."""
    receipt = owner_path(home, path)
    if not receipt.exists():
        return "unregistered"
    with build_lease(path):
        owner = _document(receipt)
        if owner.get("path") != str(path.absolute()) or (
            owner.get("device"),
            owner.get("inode"),
        ) != identity(path):
            raise CacheDeferred("prior-cache-identity-unproven")
        owner["retained"] = "later-launch-or-reuse"
        _atomic_json(receipt, owner)
    return "retained-later-launch-or-reuse"


def reserve_dispatch_cache_attempt(home: Path, geometry, kind: str) -> str:
    """One initial attempt may consume a creation proof, even if launch fails."""
    try:
        proof = Path(geometry.cache_creation_receipt)
        if (
            kind != "initial"
            or geometry.integrator_exclusive
            or proof.parent != home / "store/housekeeping/creations"
            or not re.fullmatch(r"[0-9a-f]{32}\.json", proof.name)
        ):
            return ""
        directory_chain(proof.parent, home)
        _document(proof)
        token = uuid.uuid4().hex
        _immutable_json(
            home / "store/housekeeping/attempts" / proof.name,
            {"creation": str(proof), "token": token},
            boundary=home,
        )
        return token
    except (OSError, ValueError, CacheDeferred):
        return ""


def bind_dispatch_cache(
    home: Path,
    geometry,
    run_id: str,
    *,
    attempt_token: str = "",
    dispatch_run_id: str = "",
) -> str:
    """Bind one creation event to one accepted exact run; never infer reuse."""
    try:
        if (
            not run_id
            or not _RUN_ID.fullmatch(dispatch_run_id)
            or geometry.integrator_exclusive
        ):
            raise CacheDeferred("missing-accepted-worker-run")
        proof = Path(geometry.cache_creation_receipt)
        if proof.parent != home / "store/housekeeping/creations" or not re.fullmatch(
            r"[0-9a-f]{32}\.json", proof.name
        ):
            raise CacheDeferred("creation-proof-missing")
        directory_chain(proof.parent, home)
        creation = _document(proof)
        attempt = _document(home / "store/housekeeping/attempts" / proof.name)
        if (
            not attempt_token
            or attempt.get("token") != attempt_token
            or attempt.get("creation") != str(proof)
        ):
            raise CacheDeferred("initial-launch-attempt-unproven")
        path, root = Path(geometry.target_path), Path(geometry.worktree_path)
        with build_lease(path):
            if (
                creation.get("schema") != "vibecrafted.dispatch-cache-creation.v1"
                or creation.get("path") != str(path)
                or creation.get("root") != str(root)
                or creation.get("cut_id") != geometry.cut_id
                or creation.get("branch") != geometry.branch
                or creation.get("baseline_sha") != geometry.baseline_sha
                or creation.get("root_identity") != list(identity(root))
                or (creation.get("device"), creation.get("inode")) != identity(path)
                or creation.get("git_common_dir")
                != _checkout_identity(root, geometry.branch, geometry.baseline_sha)
            ):
                raise CacheDeferred("creation-proof-identity-mismatch")
            if _run(home, run_id).get("root") != str(root):
                raise CacheDeferred("accepted-run-root-mismatch")
            if owner_path(home, path).exists():
                raise CacheDeferred("cache-already-has-owner")
            # Burn before registration: a crash or lost proof retains payload,
            # and a repair can never recycle the initial creation event.
            _immutable_json(
                home / "store/housekeeping/bindings" / proof.name,
                {"creation": str(proof), "run_id": run_id},
                boundary=home,
            )
            register_cache(
                home,
                path,
                run_id,
                created_identity=identity(path),
                dispatch_creation={
                    "receipt": str(proof),
                    "run_id": dispatch_run_id,
                    "cut_id": geometry.cut_id,
                },
            )
        return "bound"
    except (OSError, ValueError, CacheDeferred, subprocess.TimeoutExpired) as exc:
        return f"retained-{exc}"


def accepted_owner(
    home: Path, path: Path, *, identity_path: Path | None = None
) -> dict:
    receipt = owner_path(home, path)
    try:
        directory_chain(receipt.parent, home)
    except FileNotFoundError as exc:
        raise CacheDeferred("cache-ownership-receipt-missing") from exc
    owner = _document(receipt)
    if owner.get("retained"):
        raise CacheDeferred(str(owner["retained"]))
    registration = Path(str(owner.get("registration") or ""))
    expected_parent = home / "store/housekeeping/registrations"
    if registration.parent != expected_parent or not re.fullmatch(
        r"[0-9a-f]{32}\.json", registration.name
    ):
        raise CacheDeferred("ownership-registration-missing")
    directory_chain(registration.parent, home)
    original = _document(registration)
    if any(
        owner.get(key) != original.get(key)
        for key in (
            "schema",
            "path",
            "run_id",
            "root",
            "device",
            "inode",
            "reconstructible",
            "registration",
            "dispatch_creation",
        )
    ):
        raise CacheDeferred("ownership-registration-mismatch")
    if (
        owner.get("schema") != OWNER_SCHEMA
        or owner.get("path") != str(path.absolute())
        or owner.get("reconstructible") is not True
        or (owner.get("device"), owner.get("inode")) != identity(identity_path or path)
    ):
        raise CacheDeferred("cache-ownership-or-identity-unproven")
    dispatch_creation = owner.get("dispatch_creation")
    if dispatch_creation is not None:
        if not isinstance(dispatch_creation, dict) or owner.get("settled_dispatch") != {
            "run_id": dispatch_creation.get("run_id"),
            "cut_id": dispatch_creation.get("cut_id"),
        }:
            raise CacheDeferred("dispatch-not-admitted-settled")
        dispatch_id = str(dispatch_creation.get("run_id") or "")
        if not _RUN_ID.fullmatch(dispatch_id):
            raise CacheDeferred("invalid-owning-dispatch")
        dispatch_path = (
            home / "control_plane/dispatches" / dispatch_id / "receipts.json"
        )
        directory_chain(dispatch_path.parent, home)
        dispatch = _document(dispatch_path)
        cuts = dispatch.get("cuts")
        if not isinstance(cuts, dict):
            raise CacheDeferred("dispatch-cuts-unavailable")
        cut = cuts.get(dispatch_creation.get("cut_id"), {})
        if (
            dispatch.get("schema") != "vibecrafted.dispatch-receipts.v1"
            or dispatch.get("run_id") != dispatch_id
            or not isinstance(cut, dict)
            or cut.get("state") != "settled"
            or cut.get("acceptance") != "verified"
            or cut.get("provider_run_id") != owner.get("run_id")
            or cut.get("worktree_path") != owner.get("root")
            or cut.get("target_path") != str(path)
        ):
            raise CacheDeferred("dispatch-current-settlement-mismatch")
    run = _run(home, str(owner.get("run_id") or ""))
    if (
        run.get("worker_alive") is not False
        or run.get("settlement_source") != "trust"
        or run.get("settlement_tui") != "f"
        or run.get("settlement_verdict") != "finalized"
        or run.get("root") != owner.get("root")
    ):
        raise CacheDeferred("task-not-settled-and-accepted")
    trust = TrustReceiptV1.from_payload(run.get("trust_receipt", {}))
    if (
        trust.run_id != owner["run_id"]
        or trust.repo_root != owner["root"]
        or trust.settlement_tui != "f"
        or trust.settlement_verdict != "finalized"
        or trust.settlement_revision != run.get("settlement_revision")
        or not trust.claim_digest
        or trust.trust_verdict not in {"pass", "pass-with-gaps"}
        or run.get("settlement_waived") is True
    ):
        raise CacheDeferred("acceptance-receipt-mismatch")
    return owner


def mark_cache_eligible(
    home: Path, path: Path, *, run_id: str, dispatch_run_id: str, cut_id: str
) -> str:
    """Settlement marks prior registration only; it never creates ownership."""
    receipt = owner_path(home, path)
    try:
        owner = _document(receipt)
        if (
            owner.get("schema") != OWNER_SCHEMA
            or owner.get("retained")
            or owner.get("run_id") != run_id
            or owner.get("path") != str(path.absolute())
            or (owner.get("device"), owner.get("inode")) != identity(path)
        ):
            return "retained-unproven-owner"
        owner["settled_dispatch"] = {"run_id": dispatch_run_id, "cut_id": cut_id}
        _atomic_json(receipt, owner)
        return "eligible-pending-final-acceptance"
    except (OSError, ValueError, CacheDeferred):
        return "retained-unregistered-cache"


def validate_payload(path: Path) -> None:
    """Bounded no-symlink source/data veto. Names never grant cache ownership."""
    visited = 0
    for base, dirs, files in os.walk(path, followlinks=False):
        identity(Path(base))
        for name in [*dirs, *files]:
            visited += 1
            if visited > 100_000:
                raise CacheDeferred("payload-observation-limit")
            info = (Path(base) / name).lstat()
            if (
                name in SOURCE_ANCHORS
                or stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.getuid()
                or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
            ):
                raise CacheDeferred(f"protected-or-uncertain-payload:{name}")


def observe_unused(path: Path) -> None:
    """Real process/open-file observations; errors and ambiguous output veto."""
    ps = subprocess.run(
        ["/bin/ps", "-ax", "-o", "pid=,comm="],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if ps.returncode != 0 or ps.stderr or not ps.stdout.strip():
        raise CacheDeferred("process-observation-uncertain")
    if str(path) in ps.stdout:
        raise CacheDeferred("live-process-in-cache")
    lsof = "/usr/sbin/lsof" if sys.platform == "darwin" else "lsof"
    opened = subprocess.run(
        [lsof, "-n", "-P", "-Fpn", "+D", str(path)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if opened.stderr or opened.returncode not in {0, 1}:
        raise CacheDeferred("open-file-observation-uncertain")
    if opened.stdout.strip():
        raise CacheDeferred("live-open-file")


def capacity(paths: Sequence[Path]) -> dict:
    """Measure each actual filesystem independently, including missing roots."""
    volumes: dict[int, dict] = {}
    errors = []
    for requested in paths:
        existing = requested.absolute()
        try:
            while not existing.exists():
                existing = existing.parent
            device = existing.stat().st_dev
            usage = os.statvfs(existing)
            record = volumes.setdefault(
                device,
                {
                    "device": device,
                    "free_bytes": usage.f_bavail * usage.f_frsize,
                    "roots": [],
                    "missing_roots": [],
                },
            )
            record["roots"].append(str(requested))
            if existing != requested.absolute():
                record["missing_roots"].append(str(requested))
        except OSError as exc:
            errors.append({"path": str(requested), "error": type(exc).__name__})
    return {"volumes": list(volumes.values()), "capacity_errors": errors}


def _remove_tree(fd: int, *, metadata_only: bool = False, budget: list[int]) -> None:
    """Delete via nofollow dirfds. A retry only visits bounded host metadata."""
    budget[0] -= 1
    if budget[0] < 0:
        raise CacheDeferred("removal-directory-limit")
    for name in os.listdir(fd):
        if name in SOURCE_ANCHORS:
            raise CacheDeferred(f"protected-entry:{name}")
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            try:
                _remove_tree(child, metadata_only=metadata_only, budget=budget)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=fd)
        elif (
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and (not metadata_only or name == ".DS_Store")
        ):
            if name == LEASE_NAME:
                continue
            os.unlink(name, dir_fd=fd)
        else:
            raise CacheDeferred(f"retained-entry:{name}")


def clean_cache(
    home: Path, path: Path, *, observe: Callable[[Path], None] = observe_unused
) -> str:
    """Exclusive NB locks survive rename, postrename observation and deletion."""
    path, home = path.absolute(), home.absolute()
    stage: Path | None = None
    before = identity(path)
    relative = path.relative_to(home)
    if not relative.parts or relative.parts[0] not in {"worktrees", "tmp"}:
        raise CacheDeferred("durable-or-unknown-scope")
    chain = directory_chain(path, home / relative.parts[0])
    if capacity([home, path])["capacity_errors"]:
        raise CacheDeferred("capacity-observation-uncertain")
    held: list[tuple[Path, int]] = []

    def recheck_leases(*, staged: bool = False) -> None:
        for named, fd in held:
            check_path = (
                stage / LEASE_NAME if staged and named.parent == path else named
            )
            info, current = os.fstat(fd), check_path.lstat()
            if (
                not stat.S_ISREG(current.st_mode)
                or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise CacheDeferred("build-lease-identity-changed")

    with contextlib.ExitStack() as stack:
        for directory in reversed(chain):
            if (directory / ".noclean").exists() or (
                directory / ".noclean"
            ).is_symlink():
                raise CacheDeferred("containing-noclean-boundary")
            lease_path = directory / LEASE_NAME
            if directory == path or lease_path.exists() or lease_path.is_symlink():
                held.append(
                    (lease_path, stack.enter_context(lease(lease_path, exclusive=True)))
                )
        accepted_owner(home, path)
        validate_payload(path)
        observe(path)
        recheck_leases()
        if identity(path) != before:
            raise CacheDeferred("cache-identity-changed-before-staging")
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, parent_fd)
        root_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, root_fd)
        if (os.fstat(root_fd).st_dev, os.fstat(root_fd).st_ino) != before:
            raise CacheDeferred("cache-identity-changed")
        stage = path.with_name(f".vc-clean-{uuid.uuid4().hex}")
        _publish_directory_noreplace(parent_fd, path.name, stage.name)
        try:
            if identity(stage) != before or path.exists() or path.is_symlink():
                raise CacheDeferred("cache-identity-changed-after-staging")
            validate_payload(stage)
            observe(stage)
            recheck_leases(staged=True)
            if path.exists() or path.is_symlink():
                raise CacheDeferred("cache-recreated-during-staging")
            accepted_owner(home, path, identity_path=stage)
            try:
                _remove_tree(root_fd, budget=[100_000])
            except OSError:
                _remove_tree(root_fd, metadata_only=True, budget=[128])
            os.unlink(LEASE_NAME, dir_fd=root_fd)
            os.rmdir(stage.name, dir_fd=parent_fd)
        except BaseException:
            try:
                _record_leftover(home, stage)
            finally:
                if stage.exists():
                    try:
                        _publish_directory_noreplace(parent_fd, stage.name, path.name)
                    except (OSError, CacheDeferred):
                        pass  # No replacement, payload stays durably staged.
            raise
    return "deleted"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vc-build-lease")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--lease-root", type=Path, action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a complete build chain command is required after --")
    if args.run_id and args.home is None:
        parser.error("--run-id registration requires --home")
    try:
        with contextlib.ExitStack() as stack:
            created_identity = None
            args.root = args.root.absolute()
            args.root.parent.mkdir(parents=True, exist_ok=True)
            if args.run_id:
                args.home = args.home.absolute()
                created_identity = stack.enter_context(
                    dispatch_target_creation(
                        args.home, args.root.parent, target_name=args.root.name
                    )
                )
            args.root.mkdir(exist_ok=True)
            fd = stack.enter_context(build_lease(args.root))
            descriptors = [fd]
            for other in args.lease_root:
                other.mkdir(parents=True, exist_ok=True)
                descriptors.append(stack.enter_context(build_lease(other)))
            if args.run_id:
                if created_identity is not None:
                    register_cache(
                        args.home,
                        args.root,
                        args.run_id,
                        created_identity=created_identity,
                    )
                else:
                    owner = _document(owner_path(args.home, args.root))
                    if owner.get("run_id") != args.run_id or (
                        owner.get("device"),
                        owner.get("inode"),
                    ) != identity(args.root):
                        raise CacheDeferred("pre-existing-cache-ownership-unproven")
            environment = os.environ.copy()
            environment["VIBECRAFTED_BUILD_LEASE_FD"] = str(fd)
            return subprocess.run(
                command, check=False, pass_fds=tuple(descriptors), env=environment
            ).returncode
    except (OSError, ValueError, CacheDeferred) as exc:
        print(f"vc-build-lease: deferred: {exc}", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
