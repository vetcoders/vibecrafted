"""Snapshot open agent sessions and dirty worktrees for machine relocation.

``vibecrafted relocate snapshot`` captures every recently active provider
session (cursor / claude / codex transcripts), active Codescribe leases, and
dirty-or-unpushed worktree state into a single self-contained tarball under
``<vibecrafted-home>/snapshots/``.

``vibecrafted relocate restore <tarball>`` on the target machine drops the
transcripts back into the provider stores, restores leases, runs
``aicx catalog rebuild`` so the sessions register in the frame, and prints a
vc-frame-first resume plan (``vibecrafted resume <agent> --session <id>``
with the native provider command as fallback).

The tarball embeds a copy of the standalone restore entry point so a bare
target machine only needs ``tar`` and ``python3``.

What the snapshot promises
--------------------------

* **Dirty work travels whole.** Staged and unstaged changes ship as two separate
  patches — a single ``git diff HEAD`` collapses them and loses which work was
  staged — and untracked files ship as files, because no form of ``git diff``
  carries them. Every skipped file is named in the manifest with a reason.
* **Bytes, not text.** Patches are captured and written as raw bytes. Git calls
  a file binary only when it finds a NUL in the first 8k, so a perfectly ordinary
  high-byte file is "text" to git and undecodable to Python.
* **A file's mode travels with it.** Untracked work is re-created by the restore,
  not extracted, so the execute bit only survives because the manifest states it.
* **An incomplete snapshot says so.** Anything not carried is listed in the
  manifest, in ``RESTORE.md`` and on the CLI, which exits ``2`` rather than
  claiming a full capture it did not make.
* **The snapshot never writes to the source.**

What the restore promises
-------------------------

* **A manifest is input, not a trusted artifact.** Transcript paths, archive
  members and untracked paths are all refused unless they provably land inside
  their destination root — dangling symlinks and symlinked intermediate
  directories included. A refused archive is refused before the first write.
* **Nothing at the destination is overwritten.** Foreign content under a name
  the snapshot also carries is left alone and reported as unresolved; only
  byte-identical content is a silent, successful skip.
* **The recorded digest is checked.** Untracked bytes that no longer hash to
  what the manifest recorded are refused, not restored.
* **A failed restore exits nonzero**, per worktree, saying what did not land.

Known gap
---------

Paths are restored where the source machine had them. A worktree that lives
under a different root on the target is reported as *absent here* with its
artefacts left addressable — cross-machine path mapping is not implemented.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

DAY_S = 86400

SNAPSHOT_SCHEMA = "vc-relocate.snapshot.v2"
# v1 snapshots carry a single collapsed ``git diff HEAD`` patch and no untracked
# inventory; restore still reads them, it just cannot promise what it never got.
SUPPORTED_SCHEMAS = {"vc-relocate.snapshot.v1", SNAPSHOT_SCHEMA}

# Untracked work above this size is recorded as skipped rather than copied, so a
# stray build artifact cannot quietly turn a session snapshot into a disk image.
UNTRACKED_MAX_BYTES = 64 * 1024 * 1024

PROVIDER_STORES = {
    "cursor": ".cursor/projects",
    "claude": ".claude/projects",
    "codex": ".codex/sessions",
}

# vc-frame lane: `vibecrafted resume <agent> --session <uuid>` (tracked run).
# cursor lands in that lane with the fleet adapter cut; until then native.
VC_RESUME_AGENTS = {"claude", "codex", "grok", "junie", "agy"}

NATIVE_RESUME = {
    "cursor": "cursor-agent --resume {sid}",
    "claude": "claude --resume {sid}",
    "codex": "codex resume {sid}",
    "grok": "grok --resume {sid}  # UNVERIFIED — confirm resume flag",
}


def code_repos() -> list[Path]:
    """Repos whose worktrees get snapshotted. ``VC_RELOCATE_REPOS`` (os.pathsep-
    separated) overrides the Founder-machine defaults so the mechanism travels
    to Linux/cloud boxes; ``~/.vibecrafted`` is always appended by the caller."""
    override = os.environ.get("VC_RELOCATE_REPOS", "").strip()
    if override:
        return [Path(p) for p in override.split(os.pathsep) if p.strip()]
    # No baked-in machine defaults: a shipped payload must not name any
    # operator's checkout (payload-hygiene refuses host literals). Without
    # VC_RELOCATE_REPOS only ~/.vibecrafted (appended by the caller) travels.
    return []


def _sh_bytes(args: list[str], cwd: Path | None = None) -> bytes:
    return subprocess.run(args, cwd=cwd, capture_output=True, check=False).stdout


def _sh(args: list[str], cwd: Path | None = None) -> str:
    """Text view of a command, tolerant of undecodable bytes.

    Patches must never travel through here: ``git diff`` emits the *file bytes*
    for everything git considers text, and git only calls a file binary when it
    finds a NUL in the first 8k. A file of 0xaa bytes is text to git and
    undecodable to Python, so a text-mode capture raises on real dirty work.
    """
    return _sh_bytes(args, cwd).decode("utf-8", "replace")


def _git(wt: str, *args: str) -> list[str]:
    """A git invocation pinned away from the operator's own diff config.

    ``diff.mnemonicPrefix`` (``c/`` ``w/``), ``diff.noprefix`` and rename
    detection all travel into the patch bytes and decide whether that patch
    still applies on the target machine. A snapshot must not inherit them.
    """
    return [
        "git",
        "-c",
        "diff.mnemonicPrefix=false",
        "-c",
        "diff.noprefix=false",
        "-c",
        "core.quotePath=false",
        "-C",
        wt,
        *args,
    ]


def worktree_slug(path: str) -> str:
    """Stable artifact name for one worktree.

    The sanitized tail alone collides for any two checkouts sharing their last
    80 characters — sibling fleet worktrees do — and a collision silently
    overwrites one worktree's patch with another's.
    """
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", path).strip("_")[-71:]
    digest = hashlib.sha256(path.encode("utf-8", "surrogateescape")).hexdigest()[:8]
    return f"{sanitized}-{digest}"


def legacy_worktree_slug(path: str) -> str:
    """Slug shape written by ``vc-relocate.snapshot.v1`` snapshots."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", path).strip("_")[-80:]


def safe_relative(rel: str) -> PurePosixPath | None:
    """A manifest-supplied relative path, or ``None`` when it cannot be trusted.

    Rejects absolute paths, drive letters, and any ``.``/``..`` component before
    it can be joined onto a destination root.
    """
    if not rel or rel.startswith(("/", "\\")) or ":" in rel[:2]:
        return None
    parts = PurePosixPath(rel.replace("\\", "/")).parts
    if not parts:
        return None
    if any(part in {"", ".", ".."} or part.startswith("/") for part in parts):
        return None
    return PurePosixPath(*parts)


def contained_path(root: Path, rel: str) -> Path | None:
    """``root/rel`` only when it provably lands inside ``root``.

    The lexical check is not enough: a symlinked directory anywhere along the
    way is what turns a clean-looking relative path into a write outside the
    store, so the deepest *present* ancestor is re-resolved before answering.

    "Present" has to mean ``lexists``, not ``exists``. ``exists()`` follows the
    link, so a **dangling** symlink — ``store/link -> /outside/nothing.yet`` —
    reads as an absence, the walk steps straight over it, containment is
    measured for the parent directory and the caller's ``write_bytes`` then
    creates ``/outside/nothing.yet``. The same hole swallows an intermediate
    chain (``store/chain -> /outside/nodir`` plus ``chain/inner.txt``). Stopping
    *on* the link instead lets ``realpath`` resolve it out of the root, which is
    what makes the refusal happen.
    """
    safe = safe_relative(rel)
    if safe is None:
        return None
    root_real = Path(os.path.realpath(root))
    candidate = root_real / safe
    probe = candidate
    while probe != root_real and not os.path.lexists(probe):
        probe = probe.parent
    probe_real = Path(os.path.realpath(probe))
    if probe_real != root_real and root_real not in probe_real.parents:
        return None
    return candidate


def _apply_recorded_mode(path: Path, mode: object) -> None:
    """Put back the file mode the snapshot recorded, minus anything privileged.

    ``write_bytes`` creates at the umask default, so a dirty ``run.sh`` used to
    come back unexecutable — the file survived the move and the thing it was
    *for* did not. Only the nine rwx bits travel: setuid, setgid and sticky are
    masked off, because a manifest is untrusted input and must not be able to
    hand out privilege on the target machine.
    """
    if not isinstance(mode, int) or isinstance(mode, bool):
        return
    try:
        os.chmod(path, mode & 0o777)
    except OSError:
        pass


def _same_file_bytes(a: Path, b: Path) -> bool:
    """Byte equality for two regular files, cheap size check first.

    Neither side may be a symlink: a link at the destination name is foreign
    content, not a copy of ours, and following it would let the answer be about
    a file somewhere else entirely.
    """
    if a.is_symlink() or b.is_symlink() or not (a.is_file() and b.is_file()):
        return False
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def safe_extract(tar_path: Path, dest: Path) -> tuple[bool, list[str]]:
    """Extract a snapshot tarball, refusing the whole archive on any escape.

    The previous call site justified ``shutil.unpack_archive`` with "tarball is
    self-produced by snapshot" — but the tarball is a CLI argument, so that
    describes the happy path, not the input. Every member is validated *before*
    the first byte is written, so a refusal leaves nothing behind.
    """
    rejected: list[str] = []
    accepted: list[tarfile.TarInfo] = []
    with tarfile.open(tar_path) as tar:
        for member in tar.getmembers():
            if not (member.isfile() or member.isdir()):
                rejected.append(f"{member.name}: unsupported member type")
                continue
            if safe_relative(member.name) is None:
                rejected.append(f"{member.name}: escapes the extraction root")
                continue
            accepted.append(member)
        if rejected:
            return False, rejected
        dest.mkdir(parents=True, exist_ok=True)
        for member in accepted:
            target = contained_path(dest, member.name)
            if target is None:
                return False, [f"{member.name}: escapes the extraction root"]
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            handle = tar.extractfile(member)
            if handle is None:
                return False, [f"{member.name}: unreadable member"]
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as out:
                shutil.copyfileobj(handle, out)
    return True, rejected


def _untracked_files(wt: str) -> list[str]:
    raw = _sh_bytes(_git(wt, "ls-files", "--others", "--exclude-standard", "-z"))
    return [
        chunk.decode("utf-8", "surrogateescape")
        for chunk in raw.split(b"\x00")
        if chunk
    ]


def capture_untracked(wt: str, dest_root: Path) -> list[dict]:
    """Copy untracked work into the snapshot and inventory what was refused.

    ``git diff`` in any form never carries untracked files, so this is the only
    transport for them. Every skip is recorded with a reason: a snapshot that
    silently drops work is worse than one that admits the gap.
    """
    inventory: list[dict] = []
    src_root = Path(wt)
    for rel in _untracked_files(wt):
        entry: dict = {"path": rel, "captured": False}
        src = contained_path(src_root, rel)
        if src is None:
            entry["reason"] = "path escapes the worktree"
        elif src.is_symlink() or not src.is_file():
            entry["reason"] = "not a regular file"
        else:
            try:
                st = src.stat()
            except OSError as exc:
                entry["reason"] = f"stat failed: {exc.strerror}"
                inventory.append(entry)
                continue
            size = st.st_size
            if size > UNTRACKED_MAX_BYTES:
                entry["reason"] = f"larger than {UNTRACKED_MAX_BYTES} bytes"
                entry["size"] = size
            else:
                dst = contained_path(dest_root, rel)
                if dst is None:
                    entry["reason"] = "path escapes the snapshot root"
                else:
                    try:
                        data = src.read_bytes()
                    except OSError as exc:
                        entry["reason"] = f"read failed: {exc.strerror}"
                        inventory.append(entry)
                        continue
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(data)
                    entry["captured"] = True
                    entry["size"] = len(data)
                    entry["sha256"] = hashlib.sha256(data).hexdigest()
                    # The tarball cannot carry this: untracked files are written
                    # with ``write_bytes`` here and re-created with ``open(.., "wb")``
                    # on extraction, so both ends default to the umask and the
                    # execute bit is lost unless the manifest states it.
                    entry["mode"] = stat.S_IMODE(st.st_mode) & 0o777
        inventory.append(entry)
    return inventory


def _read_lease(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def active_leases(home: Path) -> list[dict]:
    leases = []
    lease_root = home / ".codescribe/agent-bridge/leases"
    for f in sorted(lease_root.glob("*.json")):
        d = _read_lease(f)
        if d is None:
            continue
        if d.get("active"):
            leases.append(d)
    return leases


def _cursor_project_cwd(slug: str) -> str:
    # slug: Users-<account>-vibecrafted -> /Users/<account>/vibecrafted (best effort)
    parts = slug.split("-")
    for i in range(1, len(parts)):
        candidate = Path("/" + "/".join(parts[:i]) + "/" + "-".join(parts[i:]))
        if candidate.is_dir():
            return str(candidate)
    return "/" + "/".join(parts)


def resume_commands(provider: str, sid: str, cwd: str) -> tuple[str, str]:
    if provider in NATIVE_RESUME:
        native = NATIVE_RESUME[provider].format(sid=sid)
    else:
        native = f"# no native resume known for {provider}"
    native_full = (
        f"cd {cwd} && {native}" if provider in {"cursor", "claude"} else native
    )
    if provider in VC_RESUME_AGENTS:
        return f"vibecrafted resume {provider} --session {sid}", native_full
    return (
        f"# vc-frame lane pending cursor fleet adapter — native: {native_full}",
        native_full,
    )


def collect_sessions(now: float, max_age_s: float, home: Path) -> list[dict]:
    sessions: list[dict] = []
    lease_sids = {lease.get("provider_session_id") for lease in active_leases(home)}

    for provider, rel_store in PROVIDER_STORES.items():
        root = home / rel_store
        if not root.is_dir():
            continue
        for f in root.rglob("*.jsonl"):
            try:
                st = f.stat()
            except OSError:
                continue
            age = now - st.st_mtime
            if age > max_age_s:
                continue
            sid = f.stem
            if provider == "codex":
                m = re.search(
                    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                    f.name,
                )
                sid = m.group(1) if m else f.stem
            if provider == "cursor":
                parts = f.relative_to(root).parts
                slug = parts[0] if len(parts) > 2 else ""
                cwd = _cursor_project_cwd(slug) if slug else str(home)
            elif provider == "claude":
                slug = f.parent.name
                cwd = "/" + slug.lstrip("-").replace("-", "/")
                cwd = cwd if Path(cwd).is_dir() else str(home)
            else:
                cwd = str(home)
            reasons = ["active-today" if age < DAY_S else "active-window"]
            if sid in lease_sids:
                reasons.append("codescribe-lease")
            vc_resume, native_resume = resume_commands(provider, sid, cwd)
            sessions.append(
                {
                    "provider": provider,
                    "session_id": sid,
                    "transcript": str(f),
                    "rel_transcript": f"{provider}/{f.relative_to(root)}",
                    "cwd": cwd,
                    "mtime": datetime.fromtimestamp(
                        st.st_mtime, timezone.utc
                    ).isoformat(),
                    "size": st.st_size,
                    "resume": vc_resume,
                    "resume_native": native_resume,
                    "reasons": reasons,
                }
            )
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


def collect_worktrees(repos: Sequence[Path], home: Path) -> list[dict]:
    seen: set[str] = set()
    entries: list[dict] = []
    for repo in [*repos, home / ".vibecrafted"]:
        if not (repo / ".git").exists() and not (repo / "HEAD").exists():
            continue
        for line in _sh(
            _git(str(repo), "worktree", "list", "--porcelain")
        ).splitlines():
            if not line.startswith("worktree "):
                continue
            wt = line.split(" ", 1)[1]
            if wt in seen:
                continue
            seen.add(wt)
            branch = _sh(_git(wt, "rev-parse", "--abbrev-ref", "HEAD")).strip()
            head = _sh(_git(wt, "rev-parse", "HEAD")).strip()
            status = _sh(_git(wt, "status", "--porcelain")).strip()
            upstream = _sh(
                _git(wt, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
            ).strip()
            unpushed = ""
            if upstream:
                unpushed = _sh(_git(wt, "log", "--format=%H %s", "@{u}..HEAD")).strip()
            entries.append(
                {
                    "path": wt,
                    "repo": str(repo),
                    "slug": worktree_slug(wt),
                    "branch": branch,
                    "head": head,
                    "dirty": bool(status),
                    "status": status,
                    "upstream": upstream or None,
                    "unpushed": unpushed.splitlines() if unpushed else [],
                }
            )
    return entries


def snapshot_omissions(worktrees: Sequence[dict]) -> list[str]:
    """Everything the snapshot could not carry, named one line per item.

    A skipped 200MB untracked file and a repo with no HEAD to diff against were
    both recorded deep inside the manifest and nowhere else, so ``relocate
    snapshot`` printed a tarball path and exited 0 — a full-snapshot promise the
    archive does not keep. The caller turns this list into an explicit status.
    """
    omissions: list[str] = []
    for w in worktrees:
        if w.get("patch_unavailable"):
            omissions.append(
                f"{w['path']}: dirty work has no patch ({w['patch_unavailable']})"
            )
        for u in w.get("untracked") or []:
            if not u.get("captured"):
                omissions.append(
                    f"{w['path']}: untracked {u.get('path')!r} not captured "
                    f"— {u.get('reason', 'unknown reason')}"
                )
    return omissions


def snapshot_dir_for(tarball: Path) -> Path:
    """The snapshot directory ``do_snapshot`` built next to its tarball."""
    return tarball.parent / tarball.name.removesuffix(".tar.gz")


def do_snapshot(
    out_root: Path | None, home: Path, repos: Sequence[Path] | None = None
) -> Path:
    from .runtime_paths import vibecrafted_home

    if repos is None:
        repos = code_repos()

    now = datetime.now(timezone.utc).astimezone()
    stamp = now.strftime("%Y-%m-%dT%H%M%S")
    root = out_root or vibecrafted_home() / "snapshots"
    snap_dir = root / f"relocate-{stamp}"
    snap_dir.mkdir(parents=True, exist_ok=False)

    sessions = collect_sessions(now.timestamp(), max_age_s=2 * DAY_S, home=home)
    leases = active_leases(home)
    worktrees = collect_worktrees(repos, home)

    for s in sessions:
        src = Path(s["transcript"])
        dst = snap_dir / "transcripts" / s["rel_transcript"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    lease_dir = snap_dir / "codescribe/leases"
    lease_dir.mkdir(parents=True, exist_ok=True)
    for lease in leases:
        (lease_dir / f"{lease.get('lease_id', 'unknown')}.json").write_text(
            json.dumps(lease, indent=2)
        )

    wt_dir = snap_dir / "worktrees"
    wt_dir.mkdir(exist_ok=True)
    for w in worktrees:
        slug = w.setdefault("slug", worktree_slug(w["path"]))
        if w["dirty"] and w["head"]:
            # Two patches, not one. ``git diff HEAD`` collapses index and
            # worktree into a single hunk set, so restoring it loses which work
            # was staged — and staging is a decision, not a formatting detail.
            for kind, extra in (("staged", ("--cached", "HEAD")), ("unstaged", ())):
                patch = _sh_bytes(
                    _git(
                        w["path"],
                        "diff",
                        "--binary",
                        "--no-ext-diff",
                        "--no-textconv",
                        "--no-renames",
                        *extra,
                    )
                )
                if patch:
                    (wt_dir / f"{slug}.{kind}.patch").write_bytes(patch)
        elif w["dirty"]:
            w["patch_unavailable"] = "no HEAD commit to diff against"
        if w["unpushed"] and w["upstream"]:
            fp = _sh_bytes(
                _git(
                    w["path"],
                    "format-patch",
                    "--binary",
                    "--no-renames",
                    "--stdout",
                    f"{w['upstream']}..HEAD",
                )
            )
            if fp:
                (wt_dir / f"{slug}.unpushed.patch").write_bytes(fp)
        w["untracked"] = capture_untracked(w["path"], wt_dir / slug / "untracked")

    omissions = snapshot_omissions(worktrees)
    manifest = {
        "schema": SNAPSHOT_SCHEMA,
        "created_at": now.astimezone().isoformat(),
        "host": os.uname().nodename,
        "home": str(home),
        "complete": not omissions,
        "omissions": omissions,
        "sessions": sessions,
        "leases": leases,
        "worktrees": worktrees,
    }
    (snap_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    shutil.copy2(Path(__file__).resolve(), snap_dir / "vc-relocate.py")

    lines = [
        "# RESTORE — vc-relocate",
        "",
        f"Snapshot: {stamp} from {os.uname().nodename}",
        "",
    ]
    if omissions:
        lines += [
            f"> **INCOMPLETE — {len(omissions)} item(s) are not in this archive.**",
            "> The list is below and in `manifest.json` under `omissions`.",
            "",
        ]
    lines += [
        "## Quick restore on the new machine",
        "",
        "```bash",
        f"tar xzf relocate-{stamp}.tar.gz",
        f"vibecrafted relocate restore relocate-{stamp}",
        "```",
        "",
        "## Sessions captured",
        "",
    ]
    for s in sessions:
        lines.append(
            f"- [{s['provider']}] `{s['session_id']}` ({', '.join(s['reasons'])})"
        )
        lines.append(f"  `{s['resume']}`")
    lines += ["", "## Dirty worktrees", ""]
    for w in worktrees:
        if not (w["dirty"] or w["unpushed"]):
            continue
        untracked = w.get("untracked", [])
        kept = sum(1 for u in untracked if u.get("captured"))
        lines.append(
            f"- `{w['path']}` [{w['branch']}] dirty={w['dirty']} "
            f"unpushed={len(w['unpushed'])} untracked={kept}/{len(untracked)}"
        )
        for u in untracked:
            if not u.get("captured"):
                lines.append(f"  - NOT captured: `{u['path']}` — {u.get('reason')}")
    if omissions:
        lines += ["", "## NOT captured", ""]
        lines += [f"- {item}" for item in omissions]
    (snap_dir / "RESTORE.md").write_text("\n".join(lines) + "\n")

    tarball = root / f"relocate-{stamp}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(snap_dir, arcname=snap_dir.name)
    return tarball


def _snapshot_slug(w: dict, worktrees_dir: Path) -> str:
    """Slug for one worktree: the one the snapshot recorded, else the v1 shape."""
    recorded = w.get("slug")
    if isinstance(recorded, str) and recorded:
        return recorded
    legacy = legacy_worktree_slug(w["path"])
    if any(worktrees_dir.glob(f"{legacy}.*")):
        return legacy
    return worktree_slug(w["path"])


def _restore_untracked(
    snapshot_root: Path, w: dict, slug: str
) -> tuple[int, list[str], list[str]]:
    """Put untracked work back without ever overwriting what is already there.

    Returns ``(restored, notes, problems)``. A destination file whose bytes
    differ is a *conflict*, not a skip: the snapshot carries work that did not
    land, and the caller has to exit nonzero for it.
    """
    inventory = w.get("untracked") or []
    restored = 0
    notes: list[str] = []
    problems: list[str] = []
    src_root = snapshot_root / "worktrees" / slug / "untracked"
    wt_root = Path(w["path"])
    for entry in inventory:
        rel = entry.get("path", "")
        if not entry.get("captured"):
            problems.append(
                f"{w['path']}: untracked {rel!r} was never captured "
                f"({entry.get('reason', 'unknown reason')})"
            )
            continue
        source = contained_path(src_root, rel)
        dst = contained_path(wt_root, rel)
        if source is None or dst is None:
            problems.append(f"{w['path']}: refused untracked path {rel!r}")
            continue
        if not source.is_file():
            problems.append(f"{w['path']}: untracked {rel!r} missing from snapshot")
            continue
        data = source.read_bytes()
        # The manifest recorded a sha256 at snapshot time and nothing ever read
        # it back, so a truncated or edited tarball restored silently and
        # returned 0. A digest that is written but never checked is decoration.
        expected = entry.get("sha256")
        if isinstance(expected, str) and expected:
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                problems.append(
                    f"{w['path']}: untracked {rel!r} is corrupt — the snapshot "
                    f"bytes hash to {actual[:12]}, the manifest recorded "
                    f"{expected[:12]}"
                )
                continue
        if os.path.lexists(dst):
            if _same_file_bytes(dst, source):
                notes.append(f"untracked {rel}: already present, identical")
            else:
                problems.append(
                    f"{w['path']}: untracked {rel!r} exists on the target with "
                    "different content — left untouched"
                )
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        _apply_recorded_mode(dst, entry.get("mode"))
        restored += 1
    return restored, notes, problems


def _apply_worktree_patches(
    snapshot_root: Path, w: dict, slug: str
) -> tuple[list[str], list[str]]:
    """Apply a worktree's patches in index-then-worktree order.

    ``--3way`` implies ``--index``, which is exactly right for the staged patch
    (the index has to move) and exactly wrong for the unstaged one (the index
    has to stay), so the two are not applied the same way.
    """
    wt_dir = snapshot_root / "worktrees"
    staged = wt_dir / f"{slug}.staged.patch"
    unstaged = wt_dir / f"{slug}.unstaged.patch"
    plan: list[tuple[str, Path, bool]] = [
        ("unpushed", wt_dir / f"{slug}.unpushed.patch", True)
    ]
    if staged.exists() or unstaged.exists():
        plan += [("staged", staged, True), ("unstaged", unstaged, False)]
    else:
        # v1 snapshot: one collapsed `git diff HEAD`, index separation lost.
        plan += [("collapsed", wt_dir / f"{slug}.patch", True)]

    notes: list[str] = []
    problems: list[str] = []
    for kind, path, three_way in plan:
        if not path.exists():
            continue
        args = ["apply", "--binary"]
        if three_way:
            args.append("--3way")
        result = subprocess.run(
            _git(w["path"], *args, str(path)), capture_output=True, check=False
        )
        if result.returncode == 0:
            notes.append(f"{kind}: applied")
            continue
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        problems.append(
            f"{w['path']}: {kind} patch failed (git apply exit {result.returncode})"
            + (f": {detail[-1]}" if detail else "")
        )
        notes.append(f"{kind}: FAILED (exit {result.returncode})")
    return notes, problems


def do_restore(src: Path, target_home: Path, apply_patches: bool) -> int:
    problems: list[str] = []
    if src.is_file():
        tmp = Path(tempfile.mkdtemp(prefix="vc-relocate-"))
        ok, rejected = safe_extract(src, tmp)
        if not ok:
            print(f"error: refusing snapshot {src}", file=sys.stderr)
            for item in rejected:
                print(f"  unsafe archive member — {item}", file=sys.stderr)
            shutil.rmtree(tmp, ignore_errors=True)
            return 1
        dirs = [d for d in tmp.iterdir() if d.is_dir()]
        if not dirs:
            print(f"error: no snapshot directory in {src}", file=sys.stderr)
            return 1
        src = dirs[0]
    try:
        manifest = json.loads((src / "manifest.json").read_text())
    except (OSError, ValueError) as exc:
        print(f"error: unreadable manifest in {src}: {exc}", file=sys.stderr)
        return 1
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") not in SUPPORTED_SCHEMAS
    ):
        print(
            f"error: unsupported snapshot schema {manifest.get('schema')!r}"
            if isinstance(manifest, dict)
            else "error: manifest is not an object",
            file=sys.stderr,
        )
        return 1
    sessions = manifest.get("sessions") or []
    worktrees = manifest.get("worktrees") or []

    real_home = Path.home()
    restored = 0
    for s in sessions:
        sid = s.get("session_id", "<unknown>")
        rel = s.get("rel_transcript") or ""
        provider = s.get("provider")
        if provider not in PROVIDER_STORES:
            problems.append(f"session {sid}: unknown provider {provider!r}")
            continue
        # The manifest is input, not a trusted self-produced artifact: a
        # `rel_transcript` of `claude/../../x` used to write outside the store.
        safe = safe_relative(rel)
        if safe is None or len(safe.parts) < 2 or safe.parts[0] != provider:
            problems.append(f"session {sid}: refused transcript path {rel!r}")
            continue
        store = target_home / PROVIDER_STORES[provider]
        store.mkdir(parents=True, exist_ok=True)
        source = contained_path(src / "transcripts", str(safe))
        dst = contained_path(store, str(PurePosixPath(*safe.parts[1:])))
        if source is None or dst is None:
            problems.append(f"session {sid}: refused transcript path {rel!r}")
            continue
        if not source.is_file():
            problems.append(f"session {sid}: transcript missing from snapshot")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(dst):
            # "Exists" alone was not an answer. Re-running a restore has to be
            # idempotent, but a *different* transcript under the same session id
            # is the snapshot's work failing to land — and that used to print
            # "skip" and return 0, which reads as "your sessions are here".
            if _same_file_bytes(dst, source):
                print(f"skip (identical): {dst}")
                continue
            problems.append(
                f"session {sid}: {dst} already holds different content — left untouched"
            )
            print(f"conflict (differs): {dst}")
            continue
        shutil.copy2(source, dst)
        restored += 1

    lease_src = src / "codescribe/leases"
    if lease_src.is_dir():
        lease_dst = target_home / ".codescribe/agent-bridge/leases"
        lease_dst.mkdir(parents=True, exist_ok=True)
        for f in lease_src.glob("*.json"):
            dst = contained_path(lease_dst, f.name)
            if dst is None:
                problems.append(f"lease {f.name}: refused destination path")
                continue
            if not dst.exists():
                shutil.copy2(f, dst)

    print(f"\nrestored {restored} session transcripts into {target_home}\n")

    aicx = shutil.which("aicx")
    if aicx and restored and target_home == real_home:
        r = subprocess.run(
            [aicx, "catalog", "rebuild"], capture_output=True, text=True, check=False
        )
        cataloged = r.returncode == 0
        print(
            "aicx catalog rebuild: "
            + ("OK — sessions registered in the frame" if cataloged else "FAILED")
        )
        if not cataloged:
            print(r.stderr[-500:])
    elif target_home != real_home:
        print("custom --target-home: skipping aicx catalog rebuild (dry-run mode)")
    elif not aicx:
        print(
            "aicx not on PATH — run `aicx catalog rebuild` to register sessions in the frame"
        )

    print("\n== Resume plan (vc-frame first, native fallback) ==")
    for s in sessions:
        print(
            f"[{s.get('provider', '?'):6}] {s.get('session_id')}  ({s.get('mtime', '')[:16]})"
        )
        print(f"         frame : {s.get('resume', '-')}")
        print(f"         native: {s.get('resume_native', '-')}")

    dirty = [w for w in worktrees if w.get("dirty") or w.get("unpushed")]
    if dirty:
        print("\n== Worktrees with state ==")
    for w in dirty:
        slug = _snapshot_slug(w, src / "worktrees")
        print(f"- {w['path']} [{w.get('branch', '?')}]")
        if w.get("patch_unavailable"):
            problems.append(
                f"{w['path']}: no patch in snapshot ({w['patch_unavailable']})"
            )
            print(f"    patches   : NONE — {w['patch_unavailable']}")
        if not Path(w["path"]).is_dir():
            # Not a failure: on a fresh machine the checkout simply is not there
            # yet. The artefacts stay addressable instead of being called lost.
            print(f"    worktree  : absent here — artefacts under {src / 'worktrees'}")
            continue
        if not apply_patches:
            for name in sorted((src / "worktrees").glob(f"{slug}.*patch")):
                print(f"    patch     : {name} (run with --apply-patches to apply)")
            kept = sum(1 for u in (w.get("untracked") or []) if u.get("captured"))
            if kept:
                print(
                    f"    untracked : {kept} file(s) under "
                    f"{src / 'worktrees' / slug / 'untracked'} "
                    "(run with --apply-patches to restore)"
                )
            continue
        notes, patch_problems = _apply_worktree_patches(src, w, slug)
        problems += patch_problems
        count, untracked_notes, untracked_problems = _restore_untracked(src, w, slug)
        problems += untracked_problems
        for note in notes:
            print(f"    {note}")
        if w.get("untracked"):
            print(
                f"    untracked : {count} restored, {len(untracked_problems)} unresolved"
            )
        for note in untracked_notes:
            print(f"      {note}")

    if problems:
        print("\n== Incomplete restore ==", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        print(
            f"\n{len(problems)} item(s) did not land; the snapshot still holds them.",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vibecrafted relocate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("snapshot", help="capture open sessions + worktree state")
    sp.add_argument("--out", type=Path, default=None)
    rp = sub.add_parser("restore", help="restore a snapshot on the target machine")
    rp.add_argument("src", type=Path)
    rp.add_argument("--target-home", type=Path, default=Path.home())
    rp.add_argument("--apply-patches", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    home = Path.home()
    if args.cmd == "snapshot":
        tarball = do_snapshot(args.out, home)
        print(f"snapshot: {tarball}")
        try:
            manifest = json.loads(
                (snapshot_dir_for(tarball) / "manifest.json").read_text()
            )
        except (OSError, ValueError):
            manifest = {}
        omissions = manifest.get("omissions") or []
        if omissions:
            # Exit 2, not 1: the tarball above is real and worth moving. It just
            # is not everything, and a caller that only checks `rc == 0` must
            # not be told otherwise.
            print(f"status: INCOMPLETE — {len(omissions)} item(s) not captured")
            print("\n== Not in this snapshot ==", file=sys.stderr)
            for item in omissions:
                print(f"  - {item}", file=sys.stderr)
            return 2
        print("status: complete")
        return 0
    return do_restore(args.src, args.target_home, args.apply_patches)
