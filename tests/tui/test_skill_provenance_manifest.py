"""Contract for the historical `SKILL.md` provenance manifest.

The manifest is the installer's only sound offline proof that a real-directory
`vc-*` copy in a runtime skill dir came from Vibecrafted: the June-2026 copies
recovered from `~/.junie/skills` carry no generator marker at all, yet every
byte of them is still in this repository's history.

Three properties are load-bearing and therefore tested here:

* **Freshness.** Every `SKILL.md` and every file in the store must already be
  in the committed manifest, by content, so editing or adding a file to a skill
  forces a regeneration in the same cut. `--check` is the gate, wired into the
  Makefile `check` target.
* **Monotonicity.** Regeneration is additive and idempotent, because a shallow
  or partial clone sees only a fraction of the history and must not shrink the
  proof set for everyone else.
* **Scope.** A `vc-*` directory is a skill directory only where a `SKILL.md`
  has actually lived. `runtime/vc-marbles/` never held one, so its files are
  not `vc-marbles`' files and must not widen what the installer will delete.
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
        assert set(entry) == {"sha256", "files"}, name
        hashes, files = entry["sha256"], entry["files"]
        assert list(hashes) == sorted(hashes), name
        assert len(hashes) == len(set(hashes)), name
        assert all(len(h) == 64 and h == h.lower() for h in hashes), name
        assert list(files) == sorted(files), name
        assert "SKILL.md" in files, name
        for rel, blobs in files.items():
            assert rel and not rel.startswith("/"), (name, rel)
            assert ".." not in rel.split("/"), (name, rel)
            assert blobs, (name, rel)
            assert list(blobs) == sorted(blobs), (name, rel)
            assert len(blobs) == len(set(blobs)), (name, rel)
            assert all(len(b) == 40 and b == b.lower() for b in blobs), (name, rel)


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


def test_manifest_covers_every_file_in_the_store_by_content() -> None:
    """Same gate for file bytes: a reference file whose blob id the manifest
    does not know makes every stale copy of that skill classify `unknown`."""
    manifest = installer.load_skill_provenance(STORE)
    roots = {
        ("/".join(p.relative_to(STORE).as_posix().split("/")[:-2]), p.parent.name)
        for p in STORE.glob("**/SKILL.md")
    }
    stale: list[str] = []
    for path in sorted(STORE.glob("**/*")):
        if path.is_dir() and not path.is_symlink():
            continue
        split = gen._owning_skill(path.relative_to(STORE).as_posix(), roots)
        if split is None or gen._ignored_path(split[1]):
            continue
        name, rel = split
        if path.is_symlink() or not path.is_file():
            continue
        known = manifest.get(name, installer.SkillProvenance()).files.get(
            rel, frozenset()
        )
        if installer._git_blob_id(path.read_bytes()) not in known:
            stale.append(f"{name}: {rel}")

    assert not stale, (
        "file bytes missing from "
        f"{MANIFEST.relative_to(REPO_ROOT)}; run "
        "`scripts/gen_skill_provenance.py`:\n  " + "\n  ".join(stale)
    )


def test_the_check_gate_runs_in_the_makefile_check_target() -> None:
    """The docs call `--check` a CI gate; `make check` is what CI runs."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("\ncheck:", 1)[1].split("\n\n", 1)[0]

    assert "scripts/gen_skill_provenance.py --check" in target, target


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


def test_only_a_real_skill_root_owns_a_path() -> None:
    """`runtime/vc-marbles/` never held a SKILL.md, so its files are not that
    skill's files — admitting them would widen what the installer may delete to
    paths that were never part of the skill at all."""
    owners = {("skills", "vc-init"), ("skills/pl", "vc-init"), ("", "vc-marbles")}

    assert gen._owning_skill("skills/vc-init/scripts/await.sh", owners) == (
        "vc-init",
        "scripts/await.sh",
    )
    assert gen._owning_skill("skills/pl/vc-init/SKILL.md", owners) == (
        "vc-init",
        "SKILL.md",
    )
    # The FIRST vc-* directory owns the tree; a nested one is part of its files.
    assert gen._owning_skill("skills/vc-init/refs/vc-old/note.md", owners) == (
        "vc-init",
        "refs/vc-old/note.md",
    )
    # A root nobody has a SKILL.md under owns nothing.
    assert gen._owning_skill("runtime/vc-marbles/orchestrator/help.md", owners) is None
    assert gen._owning_skill("skills/vc-marbles/FLOW.md", owners) is None
    assert gen._owning_skill("vc-marbles/agents/openai.yaml", owners) == (
        "vc-marbles",
        "agents/openai.yaml",
    )
    # A vc-* file is not a skill directory.
    assert gen._owning_skill("bin/vc-review", owners) is None
    assert gen._owning_skill("assets/vc-terminal.svg", owners) is None
    assert gen._owning_skill("docs/adr/0001-vc-justdo.md", owners) is None


def test_a_symlink_we_shipped_also_proves_its_target_bytes(tmp_path: Path) -> None:
    """`skills/vc-agents/shell/vetcoders.zsh` is a symlink to `vetcoders.sh` in
    the real history. An installer that copies a tree dereferences it, so the
    copy holds the target's bytes under the link's name — still our bytes, and
    the largest recovered copy hangs on this one file."""
    repo = tmp_path / "repo"
    store = repo / gen.STORE_RELATIVE
    (store / "vc-x" / "shell").mkdir(parents=True)
    (store / "vc-x" / "SKILL.md").write_text("skill\n", encoding="utf-8")
    (store / "vc-x" / "shell" / "helper.sh").write_text("body\n", encoding="utf-8")
    (store / "vc-x" / "shell" / "helper.zsh").symlink_to("helper.sh")
    _git(repo.parent, "init", "-q", "--initial-branch=main", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ship a helper plus a link to it")

    assert gen.main(["--repo", str(repo)]) == 0

    files = installer.load_skill_provenance(store)["vc-x"].files
    body = installer._git_blob_id(b"body\n")
    assert body in files["shell/helper.zsh"], files["shell/helper.zsh"]
    assert body in files["shell/helper.sh"]


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


@pytest.fixture
def collision_repo(tmp_path: Path) -> Path:
    """A repo where a non-skill `vc-x` tree shares a path with the real one."""
    repo = tmp_path / "repo"
    store = repo / gen.STORE_RELATIVE
    (store / "vc-x" / "scripts").mkdir(parents=True)
    (store / "vc-x" / "SKILL.md").write_text("skill\n", encoding="utf-8")
    (store / "vc-x" / "scripts" / "await.sh").write_text("ours\n", encoding="utf-8")
    runtime = repo / "runtime" / "vc-x" / "scripts"
    runtime.mkdir(parents=True)
    (runtime / "leak.sh").write_text("not a skill file\n", encoding="utf-8")
    _git(repo.parent, "init", "-q", "--initial-branch=main", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "skill plus a runtime tree of the same name")
    return repo


def test_a_runtime_tree_of_the_same_name_is_not_the_skill(
    collision_repo: Path,
) -> None:
    manifest_path = collision_repo / gen.STORE_RELATIVE / gen.MANIFEST_NAME

    assert gen.main(["--repo", str(collision_repo)]) == 0

    files = installer.load_skill_provenance(manifest_path.parent)["vc-x"].files
    assert sorted(files) == ["SKILL.md", "scripts/await.sh"]
    assert "scripts/leak.sh" not in files


def test_generator_covers_history_and_the_working_tree(history_repo: Path) -> None:
    manifest_path = history_repo / gen.STORE_RELATIVE / gen.MANIFEST_NAME

    assert gen.main(["--repo", str(history_repo)]) == 0

    record = installer.load_skill_provenance(manifest_path.parent)["vc-x"]
    expected = {
        hashlib.sha256(body.encode()).hexdigest()
        for body in ("first\n", "second\n", "worktree only\n")
    }
    assert record.sha256 == expected
    assert sorted(record.files) == ["SKILL.md", "references/notes.md"]
    # Every committed version of SKILL.md plus the one only in the worktree.
    assert record.files["SKILL.md"] == {
        installer._git_blob_id(body)
        for body in (b"first\n", b"second\n", b"worktree only\n")
    }
    assert record.files["references/notes.md"] == {installer._git_blob_id(b"ref\n")}


def test_generator_merge_is_additive_and_idempotent(history_repo: Path) -> None:
    """A clone that cannot see a branch must not drop its hashes: the manifest
    is a union across every clone that ever regenerated it."""
    manifest_path = history_repo / gen.STORE_RELATIVE / gen.MANIFEST_NAME
    foreign_sha, foreign_blob = "a" * 64, "b" * 40
    entry = {"sha256": [foreign_sha], "files": {"retired/only.md": [foreign_blob]}}
    manifest_path.write_text(
        json.dumps(
            {
                "schema": gen.SCHEMA,
                "generated_at": "2026-01-01",
                "skills": {
                    "vc-x": json.loads(json.dumps(entry)),
                    "vc-gone": json.loads(json.dumps(entry)),
                },
            }
        ),
        encoding="utf-8",
    )

    assert gen.main(["--repo", str(history_repo)]) == 0
    first = manifest_path.read_text(encoding="utf-8")
    assert gen.main(["--repo", str(history_repo)]) == 0

    assert manifest_path.read_text(encoding="utf-8") == first, "not idempotent"
    merged = installer.load_skill_provenance(manifest_path.parent)
    assert foreign_sha in merged["vc-x"].sha256, "an existing hash was dropped"
    assert merged["vc-x"].files["retired/only.md"] == frozenset({foreign_blob}), (
        "an existing file entry was dropped"
    )
    assert merged["vc-gone"].sha256 == frozenset({foreign_sha}), (
        "a retired skill was dropped"
    )
    assert merged["vc-gone"].files == {"retired/only.md": frozenset({foreign_blob})}
    assert len(merged["vc-x"].sha256) == 4


def test_generator_refuses_to_merge_a_foreign_schema(history_repo: Path) -> None:
    """The merge is additive, so an entry carried over from a document this
    version cannot read would live in the manifest forever without ever being
    explainable. v1 recorded hashes alone and v2 added bare paths; neither can
    say whether a file's bytes are ours, and half a proof is not one."""
    manifest_path = history_repo / gen.STORE_RELATIVE / gen.MANIFEST_NAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    foreign = "b" * 64
    for schema in (
        "vibecrafted.skill-provenance.v1",
        "vibecrafted.skill-provenance.v2",
        "something.else.v1",
        None,
    ):
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
        "skills": {
            "vc-legacy": {"sha256": [foreign], "files": {"SKILL.md": ["c" * 40]}}
        },
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    assert gen.load_manifest(manifest_path)["vc-legacy"].sha256 == {foreign}
    assert gen.main(["--repo", str(history_repo)]) == 0
    assert "vc-legacy" in installer.load_skill_provenance(manifest_path.parent)


def test_check_rejects_an_unsorted_or_duplicated_manifest(
    history_repo: Path,
) -> None:
    """`--check` compares the whole rendered document, not just "is everything
    present": an unsorted, duplicated or foreign entry is drift too, and a
    check that only looked for missing entries would wave it through."""
    manifest_path = history_repo / gen.STORE_RELATIVE / gen.MANIFEST_NAME

    assert gen.main(["--repo", str(history_repo)]) == 0
    assert gen.main(["--repo", str(history_repo), "--check"]) == 0
    good = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Reordering the timestamp alone is still fine.
    stamped = json.loads(json.dumps(good))
    stamped["generated_at"] = "1999-12-31"
    manifest_path.write_text(json.dumps(stamped, indent=2) + "\n", encoding="utf-8")
    assert gen.main(["--repo", str(history_repo), "--check"]) == 0

    for mutate in (
        lambda d: d["skills"]["vc-x"]["sha256"].reverse(),
        lambda d: d["skills"]["vc-x"]["sha256"].append(
            d["skills"]["vc-x"]["sha256"][0]
        ),
        lambda d: d["skills"]["vc-x"]["files"]["SKILL.md"].reverse(),
        lambda d: d["skills"]["vc-x"]["files"]["SKILL.md"].append(
            d["skills"]["vc-x"]["files"]["SKILL.md"][0]
        ),
        lambda d: d["skills"].update({"vc-a-late-key": {"sha256": [], "files": {}}}),
        # A shape this version cannot read is dropped on load and therefore
        # cannot come back out of the renderer.
        lambda d: d["skills"]["vc-x"].update({"files": ["SKILL.md"]}),
        lambda d: d.update({"schema": "vibecrafted.skill-provenance.v2"}),
    ):
        broken = json.loads(json.dumps(good))
        mutate(broken)
        manifest_path.write_text(json.dumps(broken, indent=2) + "\n", encoding="utf-8")
        assert gen.main(["--repo", str(history_repo), "--check"]) == 1, broken

    # What `--check` deliberately does NOT reject: a well-formed entry for
    # something this clone cannot see. That is the additive contract — it is
    # how a retired skill's proof survives a regeneration in a shallow clone —
    # so an extra sorted entry is legitimate, not drift.
    extra = json.loads(json.dumps(good))
    extra["skills"]["vc-zz-retired"] = {"sha256": [], "files": {"SKILL.md": ["e" * 40]}}
    manifest_path.write_text(json.dumps(extra, indent=2) + "\n", encoding="utf-8")
    assert gen.main(["--repo", str(history_repo), "--check"]) == 0


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
    blobs = payload["skills"]["vc-x"]["files"]["SKILL.md"]
    blobs.append(installer._git_blob_id(b"edited after regeneration\n"))
    blobs.sort()
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
