"""Contract for the historical `SKILL.md` provenance manifest.

The manifest is the installer's only sound offline proof that a real-directory
`vc-*` copy in a runtime skill dir came from Vibecrafted: the June-2026 copies
recovered from `~/.junie/skills` carry no generator marker at all, yet every one
of their files is still in this repository's history — the `SKILL.md` byte for
byte as a blob, the rest at least path for path.

Two properties are load-bearing and therefore tested here:

* **Freshness.** Every `SKILL.md` and every file path currently in the store
  must already be in the committed manifest, so editing or adding a file to a
  skill forces a regeneration in the same cut.
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
    for name, entry in skills.items():
        assert name.startswith("vc-"), name
        assert set(entry) == {"sha256", "paths"}, name
        hashes, paths = entry["sha256"], entry["paths"]
        assert list(hashes) == sorted(hashes), name
        assert len(hashes) == len(set(hashes)), name
        assert all(len(h) == 64 and h == h.lower() for h in hashes), name
        assert list(paths) == sorted(paths), name
        assert len(paths) == len(set(paths)), name
        assert "SKILL.md" in paths, name
        assert all(
            path and not path.startswith("/") and ".." not in path.split("/")
            for path in paths
        ), name


def test_manifest_covers_every_skill_md_currently_in_the_store() -> None:
    """Freshness gate. Regenerate with `scripts/gen_skill_provenance.py`."""
    manifest = installer.load_skill_provenance(STORE)
    stale: list[str] = []
    for skill_md in sorted(STORE.glob("**/SKILL.md")):
        name = gen._skill_name(skill_md.relative_to(STORE).as_posix())
        if name is None:
            continue
        digest = hashlib.sha256(skill_md.read_bytes()).hexdigest()
        if digest not in manifest.get(name, installer.SkillProvenance()).sha256:
            stale.append(f"{skill_md.relative_to(REPO_ROOT)} ({digest})")

    assert not stale, (
        "SKILL.md files missing from "
        f"{MANIFEST.relative_to(REPO_ROOT)}; run "
        "`scripts/gen_skill_provenance.py`:\n  " + "\n  ".join(stale)
    )


def test_manifest_covers_every_file_path_currently_in_the_store() -> None:
    """Same gate for paths: a new reference file the manifest does not know
    would make every stale copy of that skill classify `unknown`."""
    manifest = installer.load_skill_provenance(STORE)
    stale: list[str] = []
    for path in sorted(STORE.glob("**/*")):
        if path.is_dir() and not path.is_symlink():
            continue
        split = gen._skill_relative_path(path.relative_to(STORE).as_posix())
        if split is None or gen._ignored_path(split[1]):
            continue
        name, rel = split
        if rel not in manifest.get(name, installer.SkillProvenance()).paths:
            stale.append(f"{name}: {rel}")

    assert not stale, (
        "file paths missing from "
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


def test_skill_relative_path_names_the_directory_not_the_file() -> None:
    assert gen._skill_relative_path("skills/vc-init/scripts/await.sh") == (
        "vc-init",
        "scripts/await.sh",
    )
    assert gen._skill_relative_path("skills/pl/vc-init/SKILL.md") == (
        "vc-init",
        "SKILL.md",
    )
    # The FIRST vc-* directory owns the tree; a nested one is part of its files.
    assert gen._skill_relative_path("skills/vc-init/refs/vc-old/note.md") == (
        "vc-init",
        "refs/vc-old/note.md",
    )
    # A vc-* file is not a skill directory.
    assert gen._skill_relative_path("bin/vc-review") is None
    assert gen._skill_relative_path("assets/vc-terminal.svg") is None
    assert gen._skill_relative_path("docs/adr/0001-vc-justdo.md") is None


def test_litter_is_never_recorded_as_a_shipped_path() -> None:
    assert gen._ignored_path("scripts/__pycache__/x.cpython-313.pyc")
    assert gen._ignored_path("scripts/x.pyc")
    assert gen._ignored_path("references/.DS_Store")
    assert not gen._ignored_path("scripts/await.sh")


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
    (store / "vc-x" / "references").mkdir()
    (store / "vc-x" / "references" / "notes.md").write_text("ref\n", encoding="utf-8")
    return repo


def test_generator_covers_history_and_the_working_tree(history_repo: Path) -> None:
    manifest_path = history_repo / gen.STORE_RELATIVE / gen.MANIFEST_NAME

    assert gen.main(["--repo", str(history_repo)]) == 0

    record = installer.load_skill_provenance(manifest_path.parent)["vc-x"]
    expected = {
        hashlib.sha256(body.encode()).hexdigest()
        for body in ("first\n", "second\n", "worktree only\n")
    }
    assert record.sha256 == expected
    assert record.paths == {"SKILL.md", "references/notes.md"}


def test_generator_merge_is_additive_and_idempotent(history_repo: Path) -> None:
    """A clone that cannot see a branch must not drop its hashes: the manifest
    is a union across every clone that ever regenerated it."""
    manifest_path = history_repo / gen.STORE_RELATIVE / gen.MANIFEST_NAME
    foreign = "a" * 64
    entry = {"sha256": [foreign], "paths": ["retired/only.md"]}
    manifest_path.write_text(
        json.dumps(
            {
                "schema": gen.SCHEMA,
                "generated_at": "2026-01-01",
                "skills": {"vc-x": dict(entry), "vc-gone": dict(entry)},
            }
        ),
        encoding="utf-8",
    )

    assert gen.main(["--repo", str(history_repo)]) == 0
    first = manifest_path.read_text(encoding="utf-8")
    assert gen.main(["--repo", str(history_repo)]) == 0

    assert manifest_path.read_text(encoding="utf-8") == first, "not idempotent"
    merged = installer.load_skill_provenance(manifest_path.parent)
    assert foreign in merged["vc-x"].sha256, "an existing hash was dropped"
    assert "retired/only.md" in merged["vc-x"].paths, "an existing path was dropped"
    assert merged["vc-gone"] == installer.SkillProvenance(
        frozenset({foreign}), frozenset({"retired/only.md"})
    ), "a retired skill was dropped"
    assert len(merged["vc-x"].sha256) == 4


def test_generator_refuses_to_merge_a_foreign_schema(history_repo: Path) -> None:
    """The merge is additive, so an entry carried over from a document this
    version cannot read would live in the manifest forever without ever being
    explainable. v1 recorded hashes and no paths: half a proof is not one."""
    manifest_path = history_repo / gen.STORE_RELATIVE / gen.MANIFEST_NAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    foreign = "b" * 64
    for schema in ("vibecrafted.skill-provenance.v1", "something.else.v1", None):
        payload: dict[str, object] = {
            "skills": {"vc-legacy": {"sha256": [foreign], "paths": ["SKILL.md"]}}
        }
        if schema is not None:
            payload["schema"] = schema
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")

        assert gen.load_manifest(manifest_path) == {}, schema

        assert gen.main(["--repo", str(history_repo)]) == 0
        assert "vc-legacy" not in installer.load_skill_provenance(manifest_path.parent)

    # Our own schema is read back and carried over.
    payload = {
        "schema": gen.SCHEMA,
        "skills": {"vc-legacy": {"sha256": [foreign], "paths": ["SKILL.md"]}},
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    assert gen.load_manifest(manifest_path)["vc-legacy"].sha256 == {foreign}
    assert gen.main(["--repo", str(history_repo)]) == 0
    assert "vc-legacy" in installer.load_skill_provenance(manifest_path.parent)


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
    payload["skills"]["vc-x"]["sha256"].append(
        hashlib.sha256(b"edited after regeneration\n").hexdigest()
    )
    payload["skills"]["vc-x"]["sha256"].sort()
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    assert gen.main(["--repo", str(history_repo), "--check"]) == 0

    # A new file in the store is drift too, not only a new SKILL.md.
    (history_repo / gen.STORE_RELATIVE / "vc-x" / "extra.md").write_text(
        "new\n", encoding="utf-8"
    )
    assert gen.main(["--repo", str(history_repo), "--check"]) == 1
    assert gen.main(["--repo", str(history_repo)]) == 0
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
