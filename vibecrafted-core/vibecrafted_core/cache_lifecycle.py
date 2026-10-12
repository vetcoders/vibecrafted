"""Task-owned reconstructible caches; leases never expire by PID or age.

Registration records ownership, not acceptance. The control plane's exact
trust settlement grants acceptance separately. Source and durable evidence
never enter this lifecycle. All uncertainty retains payload.
"""

from __future__ import annotations

import argparse
import contextlib
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
    home: Path, path: Path, run_id: str, *, created_identity: tuple[int, int]
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
    # The current registry may advance for a new inode at a reused cache path;
    # the original ownership receipt remains durable and is never overwritten.
    _atomic_json(registration, payload)
    _atomic_json(receipt, payload)
    return receipt


def accepted_owner(
    home: Path, path: Path, *, identity_path: Path | None = None
) -> dict:
    receipt = owner_path(home, path)
    try:
        directory_chain(receipt.parent, home)
    except FileNotFoundError as exc:
        raise CacheDeferred("cache-ownership-receipt-missing") from exc
    owner = _document(receipt)
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
        os.rename(path.name, stage.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
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
            leftovers = home / "store" / "housekeeping" / "leftovers.txt"
            leftovers.parent.mkdir(parents=True, exist_ok=True)
            with leftovers.open("a", encoding="utf-8") as handle:
                handle.write(str(stage) + "\n")
            if not path.exists() and not path.is_symlink() and stage.exists():
                os.rename(
                    stage.name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd
                )
            else:
                pass  # Payload remains staged; never recursively retried automatically.
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
    created = False
    try:
        args.root.mkdir(parents=True)
        created = True
    except FileExistsError:
        pass
    created_identity = identity(args.root)
    try:
        with contextlib.ExitStack() as stack:
            fd = stack.enter_context(build_lease(args.root))
            descriptors = [fd]
            for other in args.lease_root:
                other.mkdir(parents=True, exist_ok=True)
                descriptors.append(stack.enter_context(build_lease(other)))
            if args.run_id:
                if args.home is None:
                    parser.error("--run-id registration requires --home")
                if created:
                    register_cache(
                        args.home,
                        args.root,
                        args.run_id,
                        created_identity=created_identity,
                    )
                else:
                    owner = _document(owner_path(args.home, args.root))
                    if (
                        owner.get("run_id") != args.run_id
                        or (owner.get("device"), owner.get("inode")) != created_identity
                    ):
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
