#!/usr/bin/env python3
"""Generate the release manifest for `vc-*` skills: what we have actually shipped.

WHY A MANIFEST AND NOT A MARKER
-------------------------------
A real directory sitting at `~/.<runtime>/skills/vc-<name>` is an artifact of a
pre-3.x installer that materialized copies instead of views. The installer may
only remove such a copy when it can *prove* the bytes came from Vibecrafted, and
the `vc-` name alone never proves that.

Content markers prove provenance only for skills generated after those tokens
existed. Measured against the real June-2026 copies recovered from
`~/.junie/skills`, *none* carried any marker — yet every byte of them is still
in this repository's history. So the soundest offline proof is what we have
actually released:

* the sha256 of every `SKILL.md` ever committed for the skill, and
* for every relative path under a real skill directory, the git blob id of
  every version of that file we ever committed.

A path is not evidence on its own — a name says nothing about content, and an
operator's own `scripts/await.sh` must not inherit ours. So the manifest carries
bytes: the blob ids come straight out of `git log --raw`, and the installer
recomputes one locally as `sha1(b"blob <len>\\0" + data)` with no git, no
repository and no network in reach.

WHAT COUNTS AS A SKILL DIRECTORY
--------------------------------
Only a directory that has ever held a `SKILL.md`, under a root where one has
ever lived. Both halves matter. `runtime/vc-marbles/` never held a `SKILL.md`,
so `runtime/vc-marbles/orchestrator/commands/help.md` is not a file of the
`vc-marbles` skill and must not widen its allow-list; `skills/vc-marbles/` did,
so the identically-named path under *that* root is.

One case needs saying out loud. We have shipped a `SKILL.md`-adjacent file as a
*symlink* — `skills/vc-agents/shell/vetcoders.zsh -> vetcoders.sh`. An installer
that copies a tree dereferences it, so the copy holds the target's bytes under
the link's name. That is still our byte, so for a path we shipped as a symlink
the manifest also records the blob ids of what it pointed at, resolved inside
the same skill. Without this the largest real copy on the affected host
(`vc-agents`, 54 files) is unprovable for one file out of 54.

Properties the installer depends on:

* **Additive.** Existing entries are never dropped. Different clones see
  different refs (a shallow CI clone sees almost none), so a regeneration in a
  partial clone must not shrink the proof set.
* **Idempotent.** Running twice on the same repository produces the same bytes.
* **Sorted.** Stable diffs; the manifest is reviewed like source.

Usage:
    scripts/gen_skill_provenance.py [--repo <dir>] [--manifest <path>] [--check]

`--check` writes nothing and exits 1 when the committed manifest is not exactly
what a regeneration would render, `generated_at` aside. It is wired into the
Makefile `check` target, so an unrecorded file fails Portable Checks.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SCHEMA = "vibecrafted.skill-provenance.v3"
STORE_RELATIVE = Path("vibecrafted-core/vibecrafted_core/skills")
MANIFEST_NAME = "SKILL_PROVENANCE.json"
SKILL_FILE = "SKILL.md"
NULL_OID = "0" * 40
SYMLINK_MODE = "120000"


@dataclass
class SkillRecord:
    """Everything Vibecrafted has ever released for one skill."""

    sha256: set[str] = field(default_factory=set)
    files: dict[str, set[str]] = field(default_factory=dict)

    def add_file(self, rel: str, *oids: str) -> None:
        """Record blob ids for one relative path."""
        self.files.setdefault(rel, set()).update(
            oid for oid in oids if oid != NULL_OID and len(oid) == 40
        )


def _git(repo: Path, *args: str) -> bytes:
    """Run a read-only git command in `repo` and return raw stdout."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    ).stdout


def _raw_entries(
    repo: Path, pathspecs: list[str]
) -> list[tuple[str, str, str, str, str]]:
    """`(src mode, dst mode, src oid, dst oid, path)` for every change to `pathspecs`.

    Both sides of each change are taken. A blob introduced on a branch whose tip
    has since been deleted can survive only as the *pre-image* of a later
    commit, and such a blob is exactly the kind of stale copy still sitting in an
    operator's runtime dir.

    `-z` is what keeps the paths honest: without it git quotes anything outside
    ASCII, and the localized `pl/` mirror is full of them.

    Known limitation: without `-m`, `git log --raw` emits no entries for merge
    commits, so a blob introduced only by an evil merge, never touched by a
    later commit and absent from the current working tree store, is not
    recorded; such a copy classifies unproven and is kept, never removed
    (fail-safe).
    """
    if not pathspecs:
        return []
    out = _git(
        repo,
        "log",
        "--all",
        "--raw",
        "--no-abbrev",
        "--no-renames",
        "-z",
        "--format=",
        "--",
        *pathspecs,
    ).decode("utf-8", "surrogateescape")
    chunks = out.split("\0")
    entries: list[tuple[str, str, str, str, str]] = []
    index = 0
    while index < len(chunks):
        meta = chunks[index]
        if not meta.startswith(":"):
            index += 1
            continue
        if index + 1 >= len(chunks):
            break
        path = chunks[index + 1]
        index += 2
        fields = meta.lstrip(":").split()
        if len(fields) < 5 or not path:
            continue
        entries.append((fields[0], fields[1], fields[2], fields[3], path))
    return entries


def _skill_name(path: str) -> str | None:
    """Skill name owning `path`, or None when the path is not a `vc-*` SKILL.md.

    The localized `pl/` mirror of a skill ships the same skill under the same
    name, so `skills/pl/vc-init/SKILL.md` and `skills/vc-init/SKILL.md` both
    count as `vc-init`.
    """
    parts = path.split("/")
    if len(parts) < 2 or parts[-1] != SKILL_FILE:
        return None
    owner = parts[-2]
    return owner if owner.startswith("vc-") else None


def _ignored_path(rel: str) -> bool:
    """True for editor/interpreter litter that is never part of a release.

    Mirrors `_skill_fingerprint_ignored` in `scripts/vetcoders_install.py`: the
    installer skips these when auditing a copy, so recording them here would
    only put unreachable entries in the manifest.
    """
    parts = rel.split("/")
    return (
        "__pycache__" in parts or parts[-1] == ".DS_Store" or parts[-1].endswith(".pyc")
    )


def collect_skill_roots(repo: Path) -> dict[str, set[str]]:
    """Map skill name → every directory that has ever held its `SKILL.md`.

    This is what makes a `vc-*` directory a skill directory. Anything else that
    merely carries the name — `runtime/vc-marbles/`, `bin/vc-review` — is not
    one, and its files are not that skill's files.
    """
    roots: dict[str, set[str]] = {}
    for *_modes, path in _raw_entries(
        repo, [f"*/vc-*/{SKILL_FILE}", f"vc-*/{SKILL_FILE}"]
    ):
        name = _skill_name(path)
        if name is None:
            continue
        roots.setdefault(name, set()).add("/".join(path.split("/")[:-2]))
    return roots


def collect_history_oids(repo: Path) -> dict[str, set[str]]:
    """Map skill name → git blob oids of every `SKILL.md` ever committed for it."""
    oids: dict[str, set[str]] = {}
    for _smode, _dmode, src, dst, path in _raw_entries(
        repo, [f"*/vc-*/{SKILL_FILE}", f"vc-*/{SKILL_FILE}"]
    ):
        name = _skill_name(path)
        if name is None:
            continue
        for oid in (src, dst):
            if oid != NULL_OID and len(oid) == 40:
                oids.setdefault(name, set()).add(oid)
    return oids


def hash_blobs(repo: Path, oids: set[str]) -> dict[str, str]:
    """Map each git blob oid to the sha256 of its contents."""
    return {
        oid: hashlib.sha256(body).hexdigest()
        for oid, body in _read_blobs(repo, oids).items()
    }


def _read_blobs(repo: Path, oids: set[str]) -> dict[str, bytes]:
    """Read the contents of `oids`.

    One `git cat-file --batch` process reads them all; spawning a process per
    blob would turn a thousand-blob history into a minute of fork overhead.
    """
    if not oids:
        return {}
    proc = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        input=("\n".join(sorted(oids)) + "\n").encode(),
        check=True,
        stdout=subprocess.PIPE,
    )
    bodies: dict[str, bytes] = {}
    buf = proc.stdout
    pos = 0
    while pos < len(buf):
        eol = buf.find(b"\n", pos)
        if eol < 0:
            break
        header = buf[pos:eol].decode("utf-8", errors="replace").split()
        pos = eol + 1
        if len(header) != 3 or header[1] != "blob":
            # "<oid> missing" — a promisor/partial clone can lack the object.
            continue
        oid, size = header[0], int(header[2])
        bodies[oid] = buf[pos : pos + size]
        pos += size + 1  # trailing newline after the payload
    return bodies


def collect_history_files(
    repo: Path, roots: dict[str, set[str]]
) -> dict[str, dict[str, set[str]]]:
    """Map skill name → relative path → blob ids ever committed at that path.

    Only paths under one of that skill's own historical roots count, and a path
    we shipped as a symlink also gets the blob ids of what it pointed at: an
    installer that copies a tree dereferences the link, so the copy holds the
    target's bytes under the link's name, and those bytes are still ours.
    """
    owners = {(root, name) for name, group in roots.items() for root in group}
    pathspecs = sorted(
        {
            f"{root}/vc-*/*" if root else "vc-*/*"
            for group in roots.values()
            for root in group
        }
    )
    files: dict[str, dict[str, set[str]]] = {}
    links: dict[tuple[str, str], set[str]] = {}
    for smode, dmode, src, dst, path in _raw_entries(repo, pathspecs):
        split = _owning_skill(path, owners)
        if split is None:
            continue
        name, rel = split
        if _ignored_path(rel):
            continue
        record = files.setdefault(name, {}).setdefault(rel, set())
        record.update(oid for oid in (src, dst) if oid != NULL_OID and len(oid) == 40)
        for mode, oid in ((smode, src), (dmode, dst)):
            if mode == SYMLINK_MODE and oid != NULL_OID:
                links.setdefault((name, rel), set()).add(oid)

    targets = _read_blobs(repo, {oid for group in links.values() for oid in group})
    for (name, rel), oids in links.items():
        for oid in oids:
            body = targets.get(oid)
            if body is None:
                continue
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(rel), body.decode("utf-8", "replace"))
            )
            if resolved.startswith("..") or resolved not in files.get(name, {}):
                continue
            files[name][rel] |= files[name][resolved]
    return files


def _owning_skill(path: str, owners: set[tuple[str, str]]) -> tuple[str, str] | None:
    """Split `path` into `(skill name, path inside the skill dir)`, or None.

    The first `vc-*` component decides, and only if the directory above it is a
    root where that skill has actually lived. A nested `vc-*` directory inside a
    skill belongs to the outer skill's tree; a `vc-*` file is not a directory at
    all.
    """
    parts = path.split("/")
    for index, part in enumerate(parts[:-1]):
        if not part.startswith("vc-"):
            continue
        if ("/".join(parts[:index]), part) in owners:
            return part, "/".join(parts[index + 1 :])
        return None
    return None


def collect_worktree(store: Path) -> dict[str, SkillRecord]:
    """Read the CURRENT store: SKILL.md hashes plus every file's blob id.

    The working tree is not history yet: an edit committed in the same change as
    a regeneration must already be provable, or the very release that ships the
    manifest would fail its own freshness gate.
    """
    records: dict[str, SkillRecord] = {}
    roots: set[tuple[str, str]] = set()
    if not store.is_dir():
        return records
    for skill_md in sorted(store.glob(f"**/{SKILL_FILE}")):
        rel = skill_md.relative_to(store).as_posix()
        name = _skill_name(rel)
        if name is None:
            continue
        records.setdefault(name, SkillRecord()).sha256.add(
            hashlib.sha256(skill_md.read_bytes()).hexdigest()
        )
        roots.add(("/".join(rel.split("/")[:-2]), name))
    for path in sorted(store.glob("**/*")):
        if path.is_dir() and not path.is_symlink():
            continue
        split = _owning_skill(path.relative_to(store).as_posix(), roots)
        if split is None:
            continue
        name, rel = split
        if _ignored_path(rel) or path.is_symlink() or not path.is_file():
            continue
        records[name].add_file(rel, _git_blob_id(path.read_bytes()))
    return records


def _git_blob_id(data: bytes) -> str:
    """The git blob id of `data`, computed without git."""
    # A git blob id IS sha1 over that header — an identifier in git's format,
    # not a signature of ours. See the twin in scripts/vetcoders_install.py.
    return hashlib.sha1(  # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1
        b"blob %d\0" % len(data) + data, usedforsecurity=False
    ).hexdigest()


def load_manifest(path: Path) -> dict[str, SkillRecord]:
    """Read the existing manifest, tolerating absence, corruption or drift.

    The schema is checked here for the same reason the installer checks it: the
    merge is additive, so carrying entries over from a document this version
    does not understand would preserve them forever without ever being able to
    say what they mean. Earlier schemas recorded weaker evidence — v1 hashes
    only, v2 hashes and bare paths — and half a proof is not one, so those are
    regenerated from the repository rather than salvaged.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {}
    skills = data.get("skills")
    if not isinstance(skills, dict):
        return {}
    merged: dict[str, SkillRecord] = {}
    for name, entry in skills.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        record = SkillRecord(_string_set(entry.get("sha256")))
        files = entry.get("files")
        if isinstance(files, dict):
            for rel, oids in files.items():
                if isinstance(rel, str):
                    record.add_file(rel, *_string_set(oids))
        merged[name] = record
    return merged


def _string_set(value: object) -> set[str]:
    """Coerce an untrusted manifest list into a set of strings."""
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def render_manifest(skills: dict[str, SkillRecord], generated_at: str) -> str:
    """Serialize the manifest with sorted keys and a trailing newline."""
    payload = {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "skills": {
            name: {
                "sha256": sorted(skills[name].sha256),
                "files": {
                    rel: sorted(skills[name].files[rel])
                    for rel in sorted(skills[name].files)
                },
            }
            for name in sorted(skills)
        },
    }
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def build(repo: Path, manifest_path: Path, store: Path) -> dict[str, SkillRecord]:
    """Union of the existing manifest, git history, and the current store."""
    merged = load_manifest(manifest_path)
    oids = collect_history_oids(repo)
    digests = hash_blobs(repo, {oid for group in oids.values() for oid in group})
    for name, group in oids.items():
        record = merged.setdefault(name, SkillRecord())
        for oid in group:
            digest = digests.get(oid)
            if digest:
                record.sha256.add(digest)
    for name, group in collect_history_files(repo, collect_skill_roots(repo)).items():
        record = merged.setdefault(name, SkillRecord())
        for rel, blobs in group.items():
            record.add_file(rel, *blobs)
    for name, worktree in collect_worktree(store).items():
        record = merged.setdefault(name, SkillRecord())
        record.sha256.update(worktree.sha256)
        for rel, blobs in worktree.files.items():
            record.add_file(rel, *blobs)
    return merged


def _without_timestamp(text: str) -> str:
    """The manifest's bytes with `generated_at` neutralized.

    `generated_at` is provenance of the run, not of the bytes, and must never
    fail a check on its own.
    """
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if isinstance(data, dict):
        data["generated_at"] = ""
    return json.dumps(data, indent=2, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on success, 1 when `--check` finds drift."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository to read history from (default: this checkout)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=f"manifest path (default: <repo>/{STORE_RELATIVE}/{MANIFEST_NAME})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="write nothing; exit 1 when the manifest is out of date",
    )
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    store = repo / STORE_RELATIVE
    manifest_path = args.manifest or (store / MANIFEST_NAME)

    merged = build(repo, manifest_path, store)
    rendered = render_manifest(merged, date.today().isoformat())
    if args.check:
        # The whole rendered document is the contract, not just "are all the
        # hashes there": an unsorted, duplicated or foreign entry is drift too,
        # and a check that only looked for missing entries would pass it.
        try:
            on_disk = manifest_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"{manifest_path}: {exc}", file=sys.stderr)
            return 1
        if _without_timestamp(on_disk) != _without_timestamp(rendered):
            print(
                f"{manifest_path} is not what a regeneration renders.\n"
                "Regenerate with: scripts/gen_skill_provenance.py",
                file=sys.stderr,
            )
            return 1
        print(f"{manifest_path} is up to date ({len(merged)} skills)")
        return 0

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(rendered, encoding="utf-8")
    hashes = sum(len(record.sha256) for record in merged.values())
    paths = sum(len(record.files) for record in merged.values())
    blobs = sum(len(ids) for record in merged.values() for ids in record.files.values())
    print(
        f"{manifest_path}: {len(merged)} skills, {hashes} SKILL.md hashes, "
        f"{paths} paths, {blobs} blob ids"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
