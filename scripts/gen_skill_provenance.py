#!/usr/bin/env python3
"""Generate the historical release manifest for `vc-*` skills.

WHY A MANIFEST AND NOT A MARKER
-------------------------------
A real directory sitting at `~/.<runtime>/skills/vc-<name>` is an artifact of a
pre-3.x installer that materialized copies instead of views. The installer may
only remove such a copy when it can *prove* the bytes came from Vibecrafted, and
the `vc-` name alone never proves that.

Content markers prove provenance only for skills generated after those tokens
existed. Measured against the real June-2026 copies recovered from
`~/.junie/skills`, *none* carried any marker — yet every one of their files is
still present, byte for byte or at least path for path, in this repository's
history. So the soundest offline proof is what we have actually released:

* the sha256 of every `SKILL.md` ever committed for the skill, and
* every relative file path that ever lived under a `vc-<name>/` directory.

The installer claims a copy only when both hold: a `SKILL.md` it recognizes as
one of its own releases, and not one file path it never shipped. A `SKILL.md`
whose hash is absent was edited by its owner, and a foreign file next to a
shipped `SKILL.md` is the operator's own work — either way the directory is
custom content and stays untouched.

Properties the installer depends on:

* **Additive.** Existing entries are never dropped. Different clones see
  different refs (a shallow CI clone sees almost none), so a regeneration in a
  partial clone must not shrink the proof set.
* **Idempotent.** Running twice on the same repository produces the same bytes.
* **Sorted.** Stable diffs; the manifest is reviewed like source.

Usage:
    scripts/gen_skill_provenance.py [--repo <dir>] [--manifest <path>] [--check]

`--check` writes nothing and exits 1 when the manifest would change, which is
the regeneration gate for CI and for `tests/tui/test_installer_skill_views.py`.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SCHEMA = "vibecrafted.skill-provenance.v2"
STORE_RELATIVE = Path("vibecrafted-core/vibecrafted_core/skills")
MANIFEST_NAME = "SKILL_PROVENANCE.json"
SKILL_FILE = "SKILL.md"
NULL_OID = "0" * 40


@dataclass
class SkillRecord:
    """Everything Vibecrafted has ever released for one skill."""

    sha256: set[str] = field(default_factory=set)
    paths: set[str] = field(default_factory=set)


def _git(repo: Path, *args: str) -> str:
    """Run a read-only git command in `repo` and return stdout."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _skill_name(path: str) -> str | None:
    """Skill name owning `path`, or None when the path is not a `vc-*` SKILL.md.

    The localized `pl/` mirror of a skill ships the same skill under the same
    name, so `skills/pl/vc-init/SKILL.md` and `skills/vc-init/SKILL.md` both
    count as `vc-init`. Historical prefixes (`skills/`, `skills/foundations/`,
    `skills/experimental/`, `vibecrafted-core/...`) need no special casing —
    only the two trailing components decide.
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


def _skill_relative_path(path: str) -> tuple[str, str] | None:
    """Split a repo path into `(skill name, path relative to the skill dir)`.

    The *first* `vc-*` component that is not the last one names the skill: a
    `vc-*` directory nested inside a skill belongs to the outer skill's tree,
    and a `vc-*` file (`bin/vc-review`, `assets/vc-terminal.svg`) is not a
    skill directory at all.
    """
    parts = path.split("/")
    for index, part in enumerate(parts[:-1]):
        if part.startswith("vc-"):
            return part, "/".join(parts[index + 1 :])
    return None


def collect_history_oids(repo: Path) -> dict[str, set[str]]:
    """Map skill name → git blob oids of every `SKILL.md` ever committed for it.

    Both sides of each change are taken. A blob introduced on a branch whose tip
    has since been deleted can survive only as the *pre-image* of a later
    commit, and such a blob is exactly the kind of stale copy still sitting in an
    operator's runtime dir.
    """
    raw = _git(
        repo,
        "log",
        "--all",
        "--raw",
        "--no-abbrev",
        "--no-renames",
        "--format=",
        "--",
        f"*/vc-*/{SKILL_FILE}",
        f"vc-*/{SKILL_FILE}",
    )
    oids: dict[str, set[str]] = {}
    for line in raw.splitlines():
        if not line.startswith(":") or "\t" not in line:
            continue
        meta, _, path = line.partition("\t")
        fields = meta.split()
        if len(fields) < 5:
            continue
        name = _skill_name(path)
        if name is None:
            continue
        for oid in (fields[2], fields[3]):
            if oid != NULL_OID and len(oid) == 40:
                oids.setdefault(name, set()).add(oid)
    return oids


def collect_history_paths(repo: Path, names: set[str]) -> dict[str, set[str]]:
    """Map skill name → every relative file path ever committed under it.

    `-z` is what keeps this honest: without it git quotes any path outside
    ASCII, and the localized `pl/` mirror is full of them. Only names in
    `names` are recorded, which is the set of directories that have ever held a
    `SKILL.md` — that, and not the `vc-` prefix, is what makes a directory a
    skill, so `vc-frame/` and friends stay out of the manifest.
    """
    raw = _git(
        repo,
        "log",
        "--all",
        "--name-only",
        "-z",
        "--no-renames",
        "--format=",
        "--",
        "*/vc-*/*",
        "vc-*/*",
    )
    paths: dict[str, set[str]] = {}
    for entry in raw.split("\0"):
        if not entry:
            continue
        split = _skill_relative_path(entry)
        if split is None:
            continue
        name, rel = split
        if name not in names or _ignored_path(rel):
            continue
        paths.setdefault(name, set()).add(rel)
    return paths


def hash_blobs(repo: Path, oids: set[str]) -> dict[str, str]:
    """Map each git blob oid to the sha256 of its contents.

    One `git cat-file --batch` process reads them all; spawning a process per
    blob would turn a thousand-blob history into a minute of fork overhead.
    """
    if not oids:
        return {}
    ordered = sorted(oids)
    proc = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        input=("\n".join(ordered) + "\n").encode(),
        check=True,
        stdout=subprocess.PIPE,
    )
    digests: dict[str, str] = {}
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
        digests[oid] = hashlib.sha256(buf[pos : pos + size]).hexdigest()
        pos += size + 1  # trailing newline after the payload
    return digests


def collect_worktree(store: Path) -> dict[str, SkillRecord]:
    """Read the CURRENT store: SKILL.md hashes plus every file path under a skill.

    The working tree is not history yet: an edit committed in the same change as
    a regeneration must already be provable, or the very release that ships the
    manifest would fail its own freshness gate.
    """
    records: dict[str, SkillRecord] = {}
    if not store.is_dir():
        return records
    for skill_md in sorted(store.glob(f"**/{SKILL_FILE}")):
        name = _skill_name(skill_md.relative_to(store).as_posix())
        if name is None:
            continue
        records.setdefault(name, SkillRecord()).sha256.add(
            hashlib.sha256(skill_md.read_bytes()).hexdigest()
        )
    for path in sorted(store.glob("**/*")):
        if path.is_dir() and not path.is_symlink():
            continue
        split = _skill_relative_path(path.relative_to(store).as_posix())
        if split is None:
            continue
        name, rel = split
        if name not in records or _ignored_path(rel):
            continue
        records[name].paths.add(rel)
    return records


def load_manifest(path: Path) -> dict[str, SkillRecord]:
    """Read the existing manifest, tolerating absence or corruption."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, dict):
        return {}
    merged: dict[str, SkillRecord] = {}
    for name, entry in skills.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        merged[name] = SkillRecord(
            _string_set(entry.get("sha256")), _string_set(entry.get("paths"))
        )
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
                "paths": sorted(skills[name].paths),
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
    worktree = collect_worktree(store)
    for name, group in collect_history_paths(repo, set(oids) | set(worktree)).items():
        merged.setdefault(name, SkillRecord()).paths.update(group)
    for name, record in worktree.items():
        target = merged.setdefault(name, SkillRecord())
        target.sha256.update(record.sha256)
        target.paths.update(record.paths)
    return merged


def _missing(
    merged: dict[str, SkillRecord], existing: dict[str, SkillRecord]
) -> dict[str, list[str]]:
    """Entries present in `merged` that the committed manifest does not carry."""
    gaps: dict[str, list[str]] = {}
    for name, record in merged.items():
        known = existing.get(name, SkillRecord())
        absent = sorted(record.sha256 - known.sha256) + sorted(
            record.paths - known.paths
        )
        if absent:
            gaps[name] = absent
    return gaps


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
    existing = load_manifest(manifest_path)
    if args.check:
        # Only the proof sets gate freshness; `generated_at` is provenance of
        # the run, not of the bytes, and must never fail a check on its own.
        missing = _missing(merged, existing)
        if missing:
            print(f"{manifest_path} is out of date; missing entries:", file=sys.stderr)
            for name, entries in sorted(missing.items()):
                print(f"  {name}: {', '.join(entries)}", file=sys.stderr)
            print(
                "Regenerate with: scripts/gen_skill_provenance.py",
                file=sys.stderr,
            )
            return 1
        print(f"{manifest_path} is up to date ({len(merged)} skills)")
        return 0

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        render_manifest(merged, date.today().isoformat()), encoding="utf-8"
    )
    hashes = sum(len(record.sha256) for record in merged.values())
    paths = sum(len(record.paths) for record in merged.values())
    print(f"{manifest_path}: {len(merged)} skills, {hashes} hashes, {paths} paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
