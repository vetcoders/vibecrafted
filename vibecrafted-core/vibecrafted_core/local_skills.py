"""Private immutable skill versions. Agent projection is owned by the installer.

Preparation never changes the selected version or any agent view. A publisher
must hold the runtime install lease, recheck the base revision, validate the
candidate, and commit the selector together with its receipted projections.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import types
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

_NAME = re.compile(r"(?:vc|vetcoders)-[A-Za-z0-9_-]+\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_OWNER = "vibecrafted.local-skills.v1"
_VERSION = "vibecrafted.local-skill-version.v1"
_SELECTION = "vibecrafted.local-skill-selection.v1"


class LocalSkillError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class LocalSkillProjection(NamedTuple):
    name: str
    directory: Path
    version_id: str
    content_sha256: str
    tree_sha256: str


def _paths():
    # The installer loads this file without importing vibecrafted_core. Keep the
    # shared path grammar, and this module, usable by the host interpreter.
    path = Path(__file__).parent / "runtime_paths.py"
    return _load_module(path, "_vc_local_skill_paths")


def _name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name) or len(name) > 128:
        raise LocalSkillError("invalid", "Invalid skill name")
    return name


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _real_dir(path: Path) -> bool:
    try:
        info = path.lstat()
        junction = getattr(path, "is_junction", lambda: False)()
        reparse = getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
        )
        return stat.S_ISDIR(info.st_mode) and not junction and not reparse
    except FileNotFoundError:
        return False


def _read_regular(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = path.lstat()
        reparse = getattr(before, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
        )
        if not stat.S_ISREG(before.st_mode) or reparse:
            raise LocalSkillError("unsafe", "Expected a regular file")
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise LocalSkillError("unsafe", "File changed while opening")
            data = handle.read()
            after = os.fstat(handle.fileno())
            if (opened.st_size, opened.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                raise LocalSkillError("unsafe", "File changed while reading")
            current = path.lstat()
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                raise LocalSkillError("unsafe", "File changed while reading")
            return data
    except FileNotFoundError as exc:
        raise LocalSkillError("not_found", "Skill data is missing") from exc


def _load_module(path: Path, name: str):
    # importlib's source loader can create __pycache__ in a signed generation.
    # Execute the selected package's regular file directly without bytecode.
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(_read_regular(path), str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _json(path: Path) -> dict:
    try:
        result = json.loads(_read_regular(path))
    except LocalSkillError as exc:
        if exc.code == "not_found":
            raise LocalSkillError("unsafe", "Local skill metadata is missing") from exc
        raise
    except (ValueError, UnicodeDecodeError) as exc:
        raise LocalSkillError("unsafe", "Invalid local skill metadata") from exc
    if not isinstance(result, dict):
        raise LocalSkillError("unsafe", "Invalid local skill metadata")
    return result


def _home() -> Path:
    return _paths().vibecrafted_home().expanduser().absolute()


def _store() -> Path:
    return _home() / "local-skills"


def _check_store() -> bool:
    home = _home()
    if (home.exists() or home.is_symlink()) and not _real_dir(home):
        raise LocalSkillError("unsafe", "Local state home is unsafe")
    store = _store()
    try:
        store.lstat()
    except FileNotFoundError:
        return False
    if not _real_dir(store) or _json(store / "owner.json") != {"schema": _OWNER}:
        raise LocalSkillError("unsafe", "Local skill store is not recognized")
    return True


def _init_store() -> None:
    home = _home()
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not _real_dir(home):
        raise LocalSkillError("unsafe", "Local state home is unsafe")
    store = _store()
    try:
        store.mkdir(mode=0o700)
    except FileExistsError:
        if not _check_store():
            raise LocalSkillError("unsafe", "Local skill store is not recognized")
        return
    # A crash before this marker is sealed leaves an unrecognized directory;
    # later calls refuse it rather than adopting or deleting it.
    marker = store / "owner.json"
    with marker.open("x", encoding="utf-8") as handle:
        json.dump({"schema": _OWNER}, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    _sync_dir(store)
    _sync_dir(home)


def _skill_root(name: str, *, create: bool = False) -> Path:
    name = _name(name)
    if create:
        _init_store()
    elif not _check_store():
        return _store() / name
    root = _store() / name
    if create:
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            pass
        else:
            with (root / "skill.json").open("x", encoding="utf-8") as handle:
                json.dump({"schema": _OWNER, "name": name}, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            _sync_dir(root)
            _sync_dir(_store())
    if root.exists() or root.is_symlink():
        if not _real_dir(root):
            raise LocalSkillError("unsafe", "Local skill directory is unsafe")
        if _json(root / "skill.json") != {"schema": _OWNER, "name": name}:
            raise LocalSkillError("unsafe", "Local skill directory is not recognized")
    return root


def local_skill_selector_path(name: str) -> Path:
    return _skill_root(name) / "selected.json"


def _snapshot_tree(root: Path) -> str:
    """Hash the closed copy, refusing pointers, devices and unrecognized nodes."""
    if not _real_dir(root):
        raise LocalSkillError("unsafe", "Version tree is unsafe")
    digest = hashlib.sha256()

    def fail_walk(error: OSError) -> None:
        raise error

    for base, dirs, files in os.walk(root, followlinks=False, onerror=fail_walk):
        dirs.sort()
        files.sort()
        for entry in dirs + files:
            path = Path(base) / entry
            rel = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode) and _real_dir(path):
                item = [rel, "directory", stat.S_IMODE(info.st_mode)]
            elif stat.S_ISREG(info.st_mode):
                item = [
                    rel,
                    "file",
                    stat.S_IMODE(info.st_mode),
                    _digest(_read_regular(path)),
                ]
            else:
                raise LocalSkillError(
                    "unsafe", "Skill resources contain a pointer or special file"
                )
            digest.update(
                json.dumps(item, separators=(",", ":"), ensure_ascii=True).encode()
            )
            digest.update(b"\n")
    return digest.hexdigest()


def _copy_tree(source: Path, destination: Path) -> None:
    if not _real_dir(source):
        raise LocalSkillError("unsafe", "Bundled resources are not a real directory")
    destination.mkdir(mode=0o700)
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        info = entry.lstat()
        target = destination / entry.name
        if stat.S_ISDIR(info.st_mode) and _real_dir(entry):
            _copy_tree(entry, target)
        elif stat.S_ISREG(info.st_mode):
            content = _read_regular(entry)
            with target.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            # Owner-only permissions; retain the executable bits needed by scripts.
            target.chmod(0o600 | (stat.S_IMODE(info.st_mode) & 0o100))
        else:
            raise LocalSkillError(
                "unsafe", "Bundled resources contain a pointer or special file"
            )
    _sync_dir(destination)


def _check_pending() -> None:
    receipt = _paths().vibecrafted_runtime_home() / "install-receipt.json"
    if not receipt.exists() and not receipt.is_symlink():
        return
    data = _json(receipt)
    if data.get("schema") != "vibecrafted.runtime-install.v1":
        raise LocalSkillError("unsafe", "Runtime receipt is invalid")
    if "config_transaction" in data or any(
        data.get(key)
        for key in (
            "install_pending",
            "config_pending",
            "config_conflicts",
            "uninstall_pending",
            "rescue_pending",
        )
    ):
        raise LocalSkillError("unsafe", "Runtime publication is pending")


def _receipt_present(runtime: Path) -> bool:
    try:
        (runtime / "install-receipt.json").lstat()
    except FileNotFoundError:
        return False
    return True


@contextmanager
def _read_lease(lease_held: bool):
    # Only the installer transaction may pass True, while it owns the exclusive
    # canonical lease. Pending receipt checks still happen in the reader.
    if lease_held:
        yield
        return
    runtime = _paths().vibecrafted_runtime_home()
    lock_path = runtime / "tools" / ".vibecrafted-install.lock"
    for parent in (runtime, lock_path.parent):
        if (parent.exists() or parent.is_symlink()) and not _real_dir(parent):
            raise LocalSkillError("unsafe", "Runtime publication path is unsafe")
    try:
        before = lock_path.lstat()
    except FileNotFoundError:
        # Older installs can have a valid receipt without a lock, while a
        # standalone package can have a store without an install receipt.
        # A receipted installation with a store requires its canonical lock.
        # Recheck the three facts around the read to catch publication starting
        # or a standalone store appearing while bundled bytes are being read.
        receipt_before = _receipt_present(runtime)
        _check_pending()
        store_before = _check_store()
        if receipt_before and store_before:
            raise LocalSkillError("unsafe", "Runtime publication lease is unavailable")
        yield
        try:
            lock_path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise LocalSkillError("unsafe", "Runtime publication began during read")
        _check_pending()
        if (
            _check_store() != store_before
            or _receipt_present(runtime) != receipt_before
        ):
            raise LocalSkillError("unsafe", "Runtime state changed during read")
        return
    if not stat.S_ISREG(before.st_mode):
        raise LocalSkillError("unsafe", "Runtime publication lease is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags)
    try:
        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise LocalSkillError("unsafe", "Runtime publication lease changed")
        path = Path(__file__).parent / "portable_lock.py"
        lock = _load_module(path, "_vc_local_skill_lock")
        try:
            lock.flock(fd, lock.LOCK_SH | lock.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise LocalSkillError(
                "unsafe", "Runtime publication is in progress"
            ) from exc
        try:
            yield
        finally:
            lock.flock(fd, lock.LOCK_UN)
    finally:
        os.close(fd)


def _bundled(name: str) -> Path:
    path = Path(__file__).parent / "skills" / _name(name) / "SKILL.md"
    if not _real_dir(path.parent):
        raise LocalSkillError("not_found", "Bundled skill not found")
    _read_regular(path)
    return path


def _proof(name: str, version_id: str) -> LocalSkillProjection:
    name = _name(name)
    if not isinstance(version_id, str) or not _ID.fullmatch(version_id):
        raise LocalSkillError("unsafe", "Invalid version identifier")
    root = _skill_root(name)
    versions = root / "versions"
    version = versions / version_id
    if not all(_real_dir(path) for path in (root, versions, version, version / "tree")):
        raise LocalSkillError("unsafe", "Local version path is unsafe")
    metadata = _json(version / "version.json")
    if (
        metadata.get("schema") != _VERSION
        or metadata.get("name") != name
        or metadata.get("version_id") != version_id
    ):
        raise LocalSkillError("unsafe", "Local version identity is invalid")
    if not all(
        isinstance(metadata.get(key), str) and _HASH.fullmatch(metadata[key])
        for key in (
            "base_revision",
            "source_tree_sha256",
            "content_sha256",
            "tree_sha256",
        )
    ):
        raise LocalSkillError("unsafe", "Local version provenance is invalid")
    tree_hash = _snapshot_tree(version / "tree")
    skill = version / "tree" / "skills" / name / "SKILL.md"
    content_hash = _digest(_read_regular(skill))
    if (
        metadata.get("tree_sha256") != tree_hash
        or metadata.get("content_sha256") != content_hash
    ):
        raise LocalSkillError("unsafe", "Local version content has changed")
    return LocalSkillProjection(
        name, skill.parent.absolute(), version_id, content_hash, tree_hash
    )


def validate_candidate(proof: LocalSkillProjection) -> LocalSkillProjection:
    current = _proof(proof.name, proof.version_id)
    if current != proof:
        raise LocalSkillError("unsafe", "Local version proof changed")
    return current


def selected_bytes(proof: LocalSkillProjection) -> bytes:
    proof = validate_candidate(proof)
    value = {
        "schema": _SELECTION,
        "name": proof.name,
        "version_id": proof.version_id,
        "content_sha256": proof.content_sha256,
        "tree_sha256": proof.tree_sha256,
    }
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _selected_local_skill_after_install_recovery(
    name: str,
) -> LocalSkillProjection | None:
    """Read a selection after the installer proves its exclusive recovery state.

    This private entrypoint does not inspect publication state. Only the
    installer recovery owner may call it after verifying the canonical lease,
    the exact pending receipt, and the already-published runtime selectors.
    Ordinary readers and saves must use ``validated_local_skill``.
    """
    _name(name)
    if not _check_store():
        return None
    root = _skill_root(name)
    if not root.exists() and not root.is_symlink():
        return None
    selection = root / "selected.json"
    if not selection.exists() and not selection.is_symlink():
        return None
    value = _json(selection)
    if value.get("schema") != _SELECTION or value.get("name") != name:
        raise LocalSkillError("unsafe", "Local skill selection is invalid")
    proof = _proof(name, value.get("version_id"))
    if (
        value.get("content_sha256") != proof.content_sha256
        or value.get("tree_sha256") != proof.tree_sha256
    ):
        raise LocalSkillError(
            "unsafe", "Local skill selection does not match its version"
        )
    return proof


def _selected_local_skill(name: str) -> LocalSkillProjection | None:
    _check_pending()
    return _selected_local_skill_after_install_recovery(name)


def validated_local_skill(
    name: str, *, lease_held: bool = False
) -> LocalSkillProjection | None:
    with _read_lease(lease_held):
        return _selected_local_skill(name)


def resolve_execution_skill(name: str) -> Path:
    proof = validated_local_skill(name)
    return proof.directory / "SKILL.md" if proof else _bundled(name)


def read_skill(name: str, *, lease_held: bool = False) -> dict:
    with _read_lease(lease_held):
        proof = _selected_local_skill(name)
        path = proof.directory / "SKILL.md" if proof else _bundled(name)
        raw = _read_regular(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LocalSkillError("unsafe", "Skill is not UTF-8") from exc
    return {
        "ok": True,
        "name": name,
        "text": text,
        "revision": _digest(raw),
        "origin": "local" if proof else "bundled",
        "activation": "unknown",
    }


def prepare_skill(
    name: str, text: str, revision: str, *, lease_held: bool = False
) -> LocalSkillProjection:
    _name(name)
    if (
        not isinstance(text, str)
        or not isinstance(revision, str)
        or not _HASH.fullmatch(revision)
    ):
        raise LocalSkillError("invalid", "Invalid skill text or revision")
    current = read_skill(name, lease_held=lease_held)
    if current["revision"] != revision:
        raise LocalSkillError("conflict", "Skill changed since it was opened")
    selected = validated_local_skill(name, lease_held=lease_held)
    root = _skill_root(name, create=True)
    versions = root / "versions"
    versions.mkdir(mode=0o700, exist_ok=True)
    if not _real_dir(versions):
        raise LocalSkillError("unsafe", "Local versions directory is unsafe")
    version_id = uuid.uuid4().hex
    stage = Path(tempfile.mkdtemp(prefix=".prepare-", dir=versions))
    try:
        tree = stage / "tree"
        tree.mkdir(mode=0o700)
        if selected:
            source = selected.directory.parent.parent
            _copy_tree(source / "skills", tree / "skills")
            _copy_tree(source / "runtime", tree / "runtime")
        else:
            package = Path(__file__).parent
            _copy_tree(package / "skills", tree / "skills")
            _copy_tree(package / "runtime", tree / "runtime")
        before = _snapshot_tree(tree)
        skill = tree / "skills" / name / "SKILL.md"
        if _digest(_read_regular(skill)) != revision:
            raise LocalSkillError(
                "conflict", "Skill changed while preparing the version"
            )
        with skill.open("wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        proof = LocalSkillProjection(
            name,
            (versions / version_id / "tree" / "skills" / name).absolute(),
            version_id,
            _digest(text.encode("utf-8")),
            _snapshot_tree(tree),
        )
        metadata = {
            "schema": _VERSION,
            "name": name,
            "version_id": version_id,
            "base_revision": revision,
            "source_tree_sha256": before,
            "content_sha256": proof.content_sha256,
            "tree_sha256": proof.tree_sha256,
        }
        with (stage / "version.json").open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(metadata, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _sync_dir(skill.parent)
        _sync_dir(tree)
        _sync_dir(stage)
        # Do not replace an existing entry, even if a UUID collision is implausible.
        destination = versions / version_id
        if destination.exists() or destination.is_symlink():
            raise LocalSkillError("unsafe", "Version identifier collision")
        os.rename(stage, destination)
        _sync_dir(versions)
        return validate_candidate(proof)
    except BaseException:
        # Only the private fresh staging directory belongs to this operation.
        if stage.exists() and _real_dir(stage):
            shutil.rmtree(stage)
        raise


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise LocalSkillError("invalid", "Request must be an object")
        op = request.get("op")
        if op == "read":
            result = read_skill(request.get("name"))
        elif op == "save":
            proof = prepare_skill(
                request.get("name"), request.get("text"), request.get("revision")
            )
            result = {
                "ok": True,
                "name": proof.name,
                "revision": proof.content_sha256,
                "version_id": proof.version_id,
                "tree_sha256": proof.tree_sha256,
                "origin": "prepared",
                "activation": "not_published",
            }
        else:
            raise LocalSkillError("invalid", "Unknown operation")
    except LocalSkillError as exc:
        result = {"ok": False, "code": exc.code, "message": str(exc)}
    except (OSError, ValueError, TypeError):
        result = {"ok": False, "code": "io", "message": "Local skill operation failed"}
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
