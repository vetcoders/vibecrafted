#!/usr/bin/env python3
"""Generate the historical `SKILL.md` provenance manifest for `vc-*` skills.

WHY A MANIFEST AND NOT A MARKER
-------------------------------
A real directory sitting at `~/.<runtime>/skills/vc-<name>` is an artifact of a
pre-3.x installer that materialized copies instead of views. The installer may
only remove such a copy when it can *prove* the bytes came from Vibecrafted, and
the `vc-` name alone never proves that.

Content markers (`MANAGED_SKILL_MARKERS` in `scripts/vetcoders_install.py`) prove
provenance only for skills generated after those tokens existed. Measured against
the real June-2026 copies recovered from `~/.junie/skills`, *none* carried any
marker — yet every one of their `SKILL.md` files is still present, byte for byte,
as a blob in this repository's history. So the soundest offline proof is the set
of hashes Vibecrafted has ever shipped for a skill.

This script walks every `vc-*/SKILL.md` blob reachable from any ref, hashes the
blob contents with sha256, and merges the result into the shipped manifest.

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
from datetime import date
from pathlib import Path

SCHEMA = "vibecrafted.skill-provenance.v1"
STORE_RELATIVE = Path("vibecrafted-core/vibecrafted_core/skills")
MANIFEST_NAME = "SKILL_PROVENANCE.json"
SKILL_FILE = "SKILL.md"
NULL_OID = "0" * 40


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


def collect_worktree_hashes(store: Path) -> dict[str, set[str]]:
    """Map skill name → sha256 of the CURRENT `SKILL.md` under the store dir.

    The working tree is not history yet: an edit committed in the same change as
    a regeneration must already be provable, or the very release that ships the
    manifest would fail its own freshness gate.
    """
    hashes: dict[str, set[str]] = {}
    if not store.is_dir():
        return hashes
    for skill_md in sorted(store.glob(f"**/{SKILL_FILE}")):
        name = _skill_name(skill_md.relative_to(store).as_posix())
        if name is None:
            continue
        hashes.setdefault(name, set()).add(
            hashlib.sha256(skill_md.read_bytes()).hexdigest()
        )
    return hashes


def load_manifest(path: Path) -> dict[str, set[str]]:
    """Read the existing manifest's hash sets, tolerating absence or corruption."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, dict):
        return {}
    merged: dict[str, set[str]] = {}
    for name, hashes in skills.items():
        if isinstance(name, str) and isinstance(hashes, list):
            merged[name] = {h for h in hashes if isinstance(h, str)}
    return merged


def render_manifest(skills: dict[str, set[str]], generated_at: str) -> str:
    """Serialize the manifest with sorted keys and a trailing newline."""
    payload = {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "skills": {name: sorted(skills[name]) for name in sorted(skills)},
    }
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def build(repo: Path, manifest_path: Path, store: Path) -> dict[str, set[str]]:
    """Union of the existing manifest, git history, and the current store."""
    merged = load_manifest(manifest_path)
    oids = collect_history_oids(repo)
    digests = hash_blobs(repo, {oid for group in oids.values() for oid in group})
    for name, group in oids.items():
        bucket = merged.setdefault(name, set())
        for oid in group:
            digest = digests.get(oid)
            if digest:
                bucket.add(digest)
    for name, group in collect_worktree_hashes(store).items():
        merged.setdefault(name, set()).update(group)
    return merged


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
        # Only the hash sets gate freshness; `generated_at` is provenance of the
        # run, not of the bytes, and must never fail a check on its own.
        missing = {
            name: sorted(hashes - existing.get(name, set()))
            for name, hashes in merged.items()
            if hashes - existing.get(name, set())
        }
        if missing:
            print(f"{manifest_path} is out of date; missing hashes:", file=sys.stderr)
            for name, hashes in sorted(missing.items()):
                print(f"  {name}: {', '.join(hashes)}", file=sys.stderr)
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
    total = sum(len(hashes) for hashes in merged.values())
    print(f"{manifest_path}: {len(merged)} skills, {total} hashes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
