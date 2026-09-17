"""Contract for the historical `SKILL.md` provenance manifest.

The manifest is the installer's only sound offline proof that a real-directory
`vc-*` copy in a runtime skill dir came from Vibecrafted: the June-2026 copies
recovered from `~/.junie/skills` carry none of the generator markers, yet every
one of their `SKILL.md` files is still a blob in this repository's history.

Two properties are load-bearing and therefore tested here:

* **Freshness.** Every `SKILL.md` currently in the store must already be in the
  committed manifest, so editing a skill forces a regeneration in the same cut.
* **Monotonicity.** Regeneration is additive and idempotent, because a shallow
  or partial clone sees only a fraction of the history and must not shrink the
  proof set for everyone else.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import gen_skill_provenance as gen
from scripts import vetcoders_install as installer

REPO_ROOT = Path(__file__).resolve().parents[2]
STORE = REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "skills"
MANIFEST = STORE / installer.SKILL_PROVENANCE_FILE


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def test_manifest_ships_inside_the_store_with_the_expected_shape() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert data["schema"] == installer.SKILL_PROVENANCE_SCHEMA
    assert data["schema"] == gen.SCHEMA
    assert len(data["generated_at"]) == len("2026-09-17")
    skills = data["skills"]
    assert skills, "manifest carries no skills"
    assert list(skills) == sorted(skills), "keys must be sorted for stable diffs"
    for name, hashes in skills.items():
        assert name.startswith("vc-"), name
        assert list(hashes) == sorted(hashes), name
        assert len(hashes) == len(set(hashes)), name
        assert all(len(h) == 64 and h == h.lower() for h in hashes), name


def test_manifest_covers_every_skill_md_currently_in_the_store() -> None:
    """Freshness gate. Regenerate with `scripts/gen_skill_provenance.py`."""
    manifest = installer.load_skill_provenance(STORE)
    stale: list[str] = []
    for skill_md in sorted(STORE.glob("**/SKILL.md")):
        name = gen._skill_name(skill_md.relative_to(STORE).as_posix())
        if name is None:
            continue
        digest = hashlib.sha256(skill_md.read_bytes()).hexdigest()
        if digest not in manifest.get(name, frozenset()):
            stale.append(f"{skill_md.relative_to(REPO_ROOT)} ({digest})")

    assert not stale, (
        "SKILL.md files missing from "
        f"{MANIFEST.relative_to(REPO_ROOT)}; run "
        "`scripts/gen_skill_provenance.py`:\n  " + "\n  ".join(stale)
    )


def test_the_installer_reads_the_shipped_manifest() -> None:
    manifest = installer.load_skill_provenance(STORE)

    assert manifest, "installer could not load the shipped manifest"
    assert "vc-init" in manifest


def test_localized_mirror_counts_under_the_same_skill_name() -> None:
    assert gen._skill_name("skills/pl/vc-init/SKILL.md") == "vc-init"
    assert gen._skill_name("skills/vc-init/SKILL.md") == "vc-init"
    assert gen._skill_name("skills/foundations/vc-init/SKILL.md") == "vc-init"
    assert (
        gen._skill_name("vibecrafted-core/vibecrafted_core/skills/vc-init/SKILL.md")
        == "vc-init"
    )
    assert gen._skill_name("skills/_template/SKILL.md") is None
    assert gen._skill_name("skills/vc-init/README.md") is None


@pytest.fixture
def history_repo(tmp_path: Path) -> Path:
    """A throwaway git repo with two committed revisions of one skill."""
    repo = tmp_path / "repo"
    store = repo / gen.STORE_RELATIVE
    (store / "vc-x").mkdir(parents=True)
    _git(repo.parent, "init", "-q", "--initial-branch=main", str(repo))
    skill_md = store / "vc-x" / "SKILL.md"
    skill_md.write_text("first\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first")
    skill_md.write_text("second\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second")
    skill_md.write_text("worktree only\n", encoding="utf-8")
    return repo


def test_generator_covers_history_and_the_working_tree(history_repo: Path) -> None:
    manifest_path = history_repo / gen.STORE_RELATIVE / gen.MANIFEST_NAME

    assert gen.main(["--repo", str(history_repo)]) == 0

    hashes = installer.load_skill_provenance(manifest_path.parent)["vc-x"]
    expected = {
        hashlib.sha256(body.encode()).hexdigest()
        for body in ("first\n", "second\n", "worktree only\n")
    }
    assert hashes == expected


def test_generator_merge_is_additive_and_idempotent(history_repo: Path) -> None:
    """A clone that cannot see a branch must not drop its hashes: the manifest
    is a union across every clone that ever regenerated it."""
    manifest_path = history_repo / gen.STORE_RELATIVE / gen.MANIFEST_NAME
    foreign = "a" * 64
    manifest_path.write_text(
        json.dumps(
            {
                "schema": gen.SCHEMA,
                "generated_at": "2026-01-01",
                "skills": {"vc-x": [foreign], "vc-gone": [foreign]},
            }
        ),
        encoding="utf-8",
    )

    assert gen.main(["--repo", str(history_repo)]) == 0
    first = manifest_path.read_text(encoding="utf-8")
    assert gen.main(["--repo", str(history_repo)]) == 0

    assert manifest_path.read_text(encoding="utf-8") == first, "not idempotent"
    merged = installer.load_skill_provenance(manifest_path.parent)
    assert foreign in merged["vc-x"], "an existing hash was dropped"
    assert merged["vc-gone"] == frozenset({foreign}), "a retired skill was dropped"
    assert len(merged["vc-x"]) == 4


def test_generator_check_fails_on_an_unrecorded_skill_md(history_repo: Path) -> None:
    manifest_path = history_repo / gen.STORE_RELATIVE / gen.MANIFEST_NAME

    assert gen.main(["--repo", str(history_repo), "--check"]) == 1

    assert gen.main(["--repo", str(history_repo)]) == 0
    assert gen.main(["--repo", str(history_repo), "--check"]) == 0

    (history_repo / gen.STORE_RELATIVE / "vc-x" / "SKILL.md").write_text(
        "edited after regeneration\n", encoding="utf-8"
    )
    assert gen.main(["--repo", str(history_repo), "--check"]) == 1
    # `generated_at` is provenance of the run, not of the bytes.
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["generated_at"] = "1999-12-31"
    payload["skills"]["vc-x"].append(
        hashlib.sha256(b"edited after regeneration\n").hexdigest()
    )
    payload["skills"]["vc-x"].sort()
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    assert gen.main(["--repo", str(history_repo), "--check"]) == 0


def test_generator_depends_on_the_standard_library_only() -> None:
    """The generator also runs in release tooling that has no site-packages."""
    tree = ast.parse(
        (REPO_ROOT / "scripts" / "gen_skill_provenance.py").read_text(encoding="utf-8")
    )
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= (sys.stdlib_module_names | {"__future__"}), imported
