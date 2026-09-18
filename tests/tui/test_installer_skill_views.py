import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from scripts import vetcoders_install as installer


def test_canonical_store_is_the_package_owned_generation_path(
    tmp_path: Path, monkeypatch
) -> None:
    crafted_home = tmp_path / "state"
    tools_home = tmp_path / "tools"
    generation = tools_home / "vibecrafted-generation-test"
    package_skills = generation / "vibecrafted-core" / "vibecrafted_core" / "skills"
    package_skills.mkdir(parents=True)
    tools_home.mkdir(parents=True, exist_ok=True)
    (tools_home / "vibecrafted-current").symlink_to(generation)
    monkeypatch.setattr(installer, "vibecrafted_home", lambda: crafted_home)
    monkeypatch.setattr(installer, "vibecrafted_tools_home", lambda: tools_home)

    store = installer._canonical_store_path(crafted_home)

    assert store == (
        tools_home
        / "vibecrafted-current"
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "skills"
    )
    assert store.resolve() == package_skills


def test_install_state_lives_outside_the_immutable_generation(
    tmp_path: Path, monkeypatch
) -> None:
    crafted_home = tmp_path / "state"
    store = tmp_path / "generation/vibecrafted-core/vibecrafted_core/skills"
    store.mkdir(parents=True)
    monkeypatch.setattr(installer, "vibecrafted_home", lambda: crafted_home)

    state_file = installer._install_state_file(store)
    installer.InstallState(framework_version="3.7.1").save(state_file.parent)

    assert state_file == crafted_home / installer.STATE_FILE
    assert not (store / installer.STATE_FILE).exists()
    assert installer._load_install_state(store).framework_version == "3.7.1"


def test_default_skill_view_has_one_cross_agent_owner() -> None:
    assert installer.SYMLINK_TARGETS == ["agents"]


def test_standard_views_cover_runtimes_that_read_their_own_dirs() -> None:
    # Claude Code and Codex CLIs never look at ~/.agents/skills; dropping their
    # views from the default install blanks the /vc-* deck (regression: 3.6.0).
    assert installer.STANDARD_VIEW_RUNTIMES == ["agents", "claude", "codex"]


def test_prune_shadowed_skill_views_removes_managed_runtime_links(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    store = tmp_path / "store"
    skill = store / "vc-scaffold"
    skill.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    canonical = home / ".agents" / "skills" / "vc-scaffold"
    canonical.parent.mkdir(parents=True)
    canonical.symlink_to(skill)

    codex = home / ".codex" / "skills" / "vc-scaffold"
    codex.parent.mkdir(parents=True)
    codex.symlink_to(skill)
    claude = home / ".claude" / "skills" / "vc-scaffold"
    claude.parent.mkdir(parents=True)
    claude.symlink_to("/missing/vibecrafted/tools/current/skills/vc-scaffold")

    removed = installer.prune_shadowed_skill_views(store, ["vc-scaffold"], ["agents"])

    assert removed == [claude, codex]
    assert canonical.is_symlink()
    assert not claude.is_symlink()
    assert not codex.is_symlink()


def test_prune_shadowed_skill_views_preserves_explicit_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    store = tmp_path / "store"
    skill = store / "vc-scaffold"
    skill.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    canonical = home / ".agents" / "skills" / "vc-scaffold"
    canonical.parent.mkdir(parents=True)
    canonical.symlink_to(skill)
    codex = home / ".codex" / "skills" / "vc-scaffold"
    codex.parent.mkdir(parents=True)
    codex.symlink_to(skill)

    removed = installer.prune_shadowed_skill_views(
        store, ["vc-scaffold"], ["agents", "codex"]
    )

    assert removed == []
    assert codex.is_symlink()


# ---------------------------------------------------------------------------
# Real-directory skill shadows in per-runtime views (junie regression, 2026-09)
# ---------------------------------------------------------------------------

SKILL_MD = """---
name: vc-x
description: a bundled skill
---

body
"""


def _pin_home(monkeypatch, home: Path, crafted_home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv(
        "VIBECRAFTED_RUNTIME_HOME", str(home / ".local" / "share" / "vibecrafted")
    )
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / ".local" / "bin"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))


def _store_skill(store: Path, name: str, body: str) -> Path:
    skill = store / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(SKILL_MD + body, encoding="utf-8")
    return skill


def _write_provenance(store: Path, skills: dict[str, dict[str, list[str]]]) -> Path:
    """Ship a release manifest inside the store, as the generator would."""
    manifest = store / installer.SKILL_PROVENANCE_FILE
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "schema": installer.SKILL_PROVENANCE_SCHEMA,
                "generated_at": "2026-09-18",
                "skills": skills,
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _prove(
    store: Path,
    name: str,
    *released: Path,
    extra: Mapping[str, Sequence[bytes]] | None = None,
) -> Path:
    """Record every `released` directory as a Vibecrafted release of `name`.

    Each directory contributes its SKILL.md sha256 and, for every file in it,
    the git blob id of those exact bytes at that exact relative path — which is
    what the installer checks. `extra` adds bodies we shipped at a path but no
    longer do, the way history outlives the current store.
    """
    digests: set[str] = set()
    files: dict[str, set[str]] = {}
    for skill in released:
        digests.add(hashlib.sha256((skill / "SKILL.md").read_bytes()).hexdigest())
        for rel, path in installer._skill_copy_entries(skill):
            if path.is_file() and not path.is_symlink():
                files.setdefault(rel, set()).add(
                    installer._git_blob_id(path.read_bytes())
                )
    for rel, bodies in (extra or {}).items():
        files.setdefault(rel, set()).update(
            installer._git_blob_id(body) for body in bodies
        )
    return _write_provenance(
        store,
        {
            name: {
                "sha256": sorted(digests),
                "files": {rel: sorted(files[rel]) for rel in sorted(files)},
            }
        },
    )


def _canonical_view(home: Path, store: Path, name: str) -> Path:
    canonical = home / ".agents" / "skills" / name
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.symlink_to(store / name)
    return canonical


def _junie_copy(home: Path, name: str, skill_md: str) -> Path:
    shadow = home / ".junie" / "skills" / name
    shadow.mkdir(parents=True, exist_ok=True)
    (shadow / "SKILL.md").write_text(skill_md, encoding="utf-8")
    return shadow


def _quarantine_dirs(crafted_home: Path) -> list[Path]:
    root = crafted_home / "backups" / "installer"
    if not root.is_dir():
        return []
    return sorted(
        entry
        for entry in root.iterdir()
        if entry.name.startswith(installer.SHADOW_QUARANTINE_PREFIX)
    )


def test_stale_managed_copy_is_quarantined_and_removed(
    tmp_path: Path, monkeypatch
) -> None:
    """June-2026 installer left real vc-* dirs in ~/.junie/skills; they drifted
    from the store and neither install, update nor doctor ever noticed."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    canonical = _canonical_view(home, store, "vc-x")
    shadow = _junie_copy(home, "vc-x", SKILL_MD + "june 2026 body\n")
    _prove(store, "vc-x", shadow)

    detected = installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    assert [(d.runtime, d.skill, d.classification) for d in detected] == [
        ("junie", "vc-x", "managed_stale")
    ]

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], shadows=detected
    )

    assert [d.path for d in reconciled] == [shadow]
    assert kept == []
    assert not shadow.exists()
    quarantined = _quarantine_dirs(crafted_home)
    assert len(quarantined) == 1
    backed_up = quarantined[0] / "junie" / "vc-x" / "SKILL.md"
    assert backed_up.read_text(encoding="utf-8").endswith("june 2026 body\n")
    assert canonical.is_symlink()
    assert canonical.resolve() == (store / "vc-x").resolve()


def test_identical_managed_copy_is_quarantined_and_removed(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    canonical = _canonical_view(home, store, "vc-x")
    (store / "vc-x" / "references").mkdir()
    (store / "vc-x" / "references" / "notes.md").write_text("ref\n", encoding="utf-8")
    shadow = _junie_copy(home, "vc-x", SKILL_MD + "canonical body\n")
    (shadow / "references").mkdir()
    (shadow / "references" / "notes.md").write_text("ref\n", encoding="utf-8")
    # Editor litter must not make an identical copy look drifted.
    (shadow / ".DS_Store").write_bytes(b"\x00")
    _prove(store, "vc-x", shadow)

    detected = installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    assert [d.classification for d in detected] == ["managed_identical"]

    # One nested byte of drift withdraws the claim outright: those bytes are
    # not a version of that file we ever shipped, so the copy is not ours.
    (shadow / "references" / "notes.md").write_text("drifted\n", encoding="utf-8")
    assert [
        d.classification for d in installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    ] == ["unknown"]

    # Drift we DID ship is a different matter. Once those bytes are a release,
    # the copy is stale rather than foreign — the classification changes, the
    # removal policy does not.
    _prove(store, "vc-x", shadow, extra={"references/notes.md": [b"ref\n"]})
    assert [
        d.classification for d in installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    ] == ["managed_stale"]
    (shadow / "references" / "notes.md").write_text("ref\n", encoding="utf-8")
    _prove(store, "vc-x", shadow)

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], shadows=detected
    )

    assert [d.path for d in reconciled] == [shadow]
    assert kept == []
    assert not shadow.exists()
    assert _quarantine_dirs(crafted_home)
    assert canonical.is_symlink()


def test_copy_without_provenance_is_reported_but_never_removed(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    canonical = _canonical_view(home, store, "vc-x")
    shadow = _junie_copy(home, "vc-x", "# my own vc-x skill\n")

    detected = installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    assert [d.classification for d in detected] == ["unknown"]
    assert not detected[0].is_managed

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], shadows=detected
    )

    assert reconciled == []
    assert [d.path for d in kept] == [shadow]
    assert (shadow / "SKILL.md").read_text(encoding="utf-8") == "# my own vc-x skill\n"
    assert _quarantine_dirs(crafted_home) == []
    assert str(shadow) in capsys.readouterr().out
    assert canonical.is_symlink()


def test_missing_runtime_skills_dir_yields_no_findings(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    canonical = _canonical_view(home, store, "vc-x")

    assert installer.collect_shadowed_skill_dirs(store, ["vc-x"]) == []
    assert installer.reconcile_shadowed_skill_dirs(store, ["vc-x"]) == (
        [],
        [],
    )
    assert canonical.is_symlink()


def test_canonical_agents_view_is_never_a_shadow_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    # A real directory in the canonical view must stay out of scope entirely.
    canonical = home / ".agents" / "skills" / "vc-x"
    canonical.mkdir(parents=True)
    (canonical / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")

    assert "agents" not in installer.shadow_candidate_runtimes()
    assert (
        installer.collect_shadowed_skill_dirs(store, ["vc-x"], ["agents", "junie"])
        == []
    )
    assert canonical.is_dir()


def test_a_runtime_with_a_managed_view_is_reconciled_like_any_other(
    tmp_path: Path, monkeypatch
) -> None:
    """`claude` and `codex` carry a managed view, so they used to be skipped
    here on the grounds that the symlink writer would rmtree the directory
    anyway — which quarantined the proven copies and silently deleted the
    unproven ones. The writer no longer removes anything; this owns both."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    _canonical_view(home, store, "vc-x")
    claude = home / ".claude" / "skills" / "vc-x"
    claude.mkdir(parents=True)
    (claude / "SKILL.md").write_text(SKILL_MD + "june 2026 body\n", encoding="utf-8")
    _prove(store, "vc-x", claude)

    detected = installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    assert [(d.runtime, d.classification) for d in detected] == [
        ("claude", "managed_stale")
    ]

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(store, ["vc-x"])

    assert [d.path for d in reconciled] == [claude]
    assert kept == []
    assert not claude.exists()
    assert (
        _quarantine_dirs(crafted_home)[0] / "claude" / "vc-x" / "SKILL.md"
    ).is_file()


def test_reconcile_keeps_copy_when_canonical_view_is_not_linked(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    shadow = _junie_copy(home, "vc-x", SKILL_MD + "june 2026 body\n")
    _prove(store, "vc-x", shadow)

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(store, ["vc-x"])

    assert reconciled == []
    assert [d.path for d in kept] == [shadow]
    assert shadow.is_dir()


def test_reconcile_dry_run_touches_nothing(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    _canonical_view(home, store, "vc-x")
    shadow = _junie_copy(home, "vc-x", SKILL_MD + "june 2026 body\n")
    _prove(store, "vc-x", shadow)

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], dry_run=True
    )

    assert [d.path for d in reconciled] == [shadow]
    assert kept == []
    assert shadow.is_dir()
    assert _quarantine_dirs(crafted_home) == []


def test_doctor_reports_stale_real_directory_shadow(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store = crafted_home / "skills"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    _canonical_view(home, store, "vc-x")
    shadow = _junie_copy(home, "vc-x", SKILL_MD + "june 2026 body\n")
    _prove(store, "vc-x", shadow)

    state = installer.InstallState(
        framework_version="4.1.0", skills=["vc-x"], runtimes=["agents"]
    )
    state.save(store)
    monkeypatch.setattr(installer, "FOUNDATIONS", [])

    findings = installer.run_doctor(store, state)
    indexed = {finding.component: finding for finding in findings}

    finding = indexed["shadow-dir:junie/vc-x"]
    assert finding.level == "warn"
    assert str(shadow) in finding.message
    assert "managed_stale" in finding.message
    # A runtime with no copy in it produces no shadow-dir finding at all — the
    # audit reports what is there, not every runtime it looked at.
    assert "shadow-dir:claude/vc-x" not in indexed
    assert "`vibecrafted update --force`" in finding.message
    # A plain `vibecrafted update` returns at "up to date" once the installed
    # version matches the channel, so the reconciliation would never run.
    actions = installer._doctor_action_items(findings)
    assert any(
        "reconcile stale runtime skill copies" in action
        and "vibecrafted update --force" in action
        for action in actions
    ), actions


# ---------------------------------------------------------------------------
# Release provenance manifest: a shipped SKILL.md AND only shipped file paths
# ---------------------------------------------------------------------------


def test_historical_release_hash_proves_a_stale_copy(
    tmp_path: Path, monkeypatch
) -> None:
    """The real June-2026 ~/.junie/skills copies carry no marker token at all,
    so the proof is the release manifest: a SKILL.md we shipped, and not one
    file path we did not."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "2026-09 body\n")
    canonical = _canonical_view(home, store, "vc-x")
    june = SKILL_MD + "june 2026 body\n"
    shadow = _junie_copy(home, "vc-x", june)
    (shadow / "scripts").mkdir()
    (shadow / "scripts" / "await.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    # A file the current store no longer carries is still ours when those exact
    # bytes are in history — that is the point of recording blob ids per path
    # rather than comparing against the current tree.
    assert not (store / "vc-x" / "scripts").exists()
    _prove(store, "vc-x", shadow)

    detected = installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    assert [(d.classification, d.is_managed) for d in detected] == [
        ("managed_stale", True)
    ]
    assert "byte for byte a version we shipped" in detected[0].detail

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], shadows=detected
    )

    assert [d.path for d in reconciled] == [shadow]
    assert kept == []
    assert not shadow.exists()
    quarantined = _quarantine_dirs(crafted_home)
    assert len(quarantined) == 1
    assert (quarantined[0] / "junie" / "vc-x" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == june
    assert canonical.is_symlink()


def test_hand_edited_skill_md_absent_from_the_manifest_stays_unknown(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "2026-09 body\n")
    _canonical_view(home, store, "vc-x")
    released = _junie_copy(home, "vc-x", SKILL_MD + "june 2026 body\n")
    _prove(store, "vc-x", released)
    shadow = _junie_copy(home, "vc-x", SKILL_MD + "june 2026 body\noperator edit\n")

    detected = installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    assert [d.classification for d in detected] == ["unknown"]
    assert "not a Vibecrafted release" in detected[0].detail

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], shadows=detected
    )

    assert reconciled == []
    assert [d.path for d in kept] == [shadow]
    assert shadow.is_dir()


def test_a_file_we_never_shipped_withdraws_the_whole_claim(
    tmp_path: Path, monkeypatch
) -> None:
    """A shipped SKILL.md is not a licence to delete the directory around it:
    an operator's own note parked next to it would go with the copy."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "2026-09 body\n")
    _canonical_view(home, store, "vc-x")
    shadow = _junie_copy(home, "vc-x", SKILL_MD + "june 2026 body\n")
    _prove(store, "vc-x", shadow)
    (shadow / "references").mkdir()
    (shadow / "references" / "my-notes.md").write_text("mine\n", encoding="utf-8")
    # Litter is still litter; it must not be the foreign file that saves a copy.
    (shadow / ".DS_Store").write_bytes(b"\x00")

    detected = installer.collect_shadowed_skill_dirs(store, ["vc-x"])

    assert [d.classification for d in detected] == ["unknown"]
    assert "'references/my-notes.md' was never shipped" in detected[0].detail

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], shadows=detected
    )

    assert reconciled == []
    assert [d.path for d in kept] == [shadow]
    assert (shadow / "references" / "my-notes.md").is_file()


def test_an_edited_file_at_a_shipped_path_withdraws_the_claim(
    tmp_path: Path, monkeypatch
) -> None:
    """The path is one we ship and the SKILL.md is one we released, so a
    path-only proof claimed this copy. The operator's edit to `scripts/await.sh`
    would have gone into the quarantine and out of their reach."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "2026-09 body\n")
    _canonical_view(home, store, "vc-x")
    shadow = _junie_copy(home, "vc-x", SKILL_MD + "june 2026 body\n")
    (shadow / "scripts").mkdir()
    await_sh = shadow / "scripts" / "await.sh"
    await_sh.write_text("#!/bin/sh\necho ours\n", encoding="utf-8")
    _prove(store, "vc-x", shadow)
    assert [
        d.classification for d in installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    ] == ["managed_stale"]

    await_sh.write_text("#!/bin/sh\necho my own tweak\n", encoding="utf-8")

    detected = installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    assert [d.classification for d in detected] == ["unknown"]
    assert "'scripts/await.sh' differs from every version" in detected[0].detail

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], shadows=detected
    )

    assert reconciled == []
    assert [d.path for d in kept] == [shadow]
    assert await_sh.read_text(encoding="utf-8").endswith("my own tweak\n")


def test_a_symlink_inside_a_copy_withdraws_the_claim(
    tmp_path: Path, monkeypatch
) -> None:
    """A materialized copy is files. A link inside one points somewhere we
    cannot vouch for, and quarantining it would copy whatever it aims at."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "2026-09 body\n")
    _canonical_view(home, store, "vc-x")
    shadow = _junie_copy(home, "vc-x", SKILL_MD + "june 2026 body\n")
    _prove(store, "vc-x", shadow)
    (shadow / "notes.md").symlink_to(tmp_path / "elsewhere.md")

    detected = installer.collect_shadowed_skill_dirs(store, ["vc-x"])

    assert [d.classification for d in detected] == ["unknown"]
    assert "'notes.md' is a symlink" in detected[0].detail


def test_manifest_that_is_absent_corrupt_or_v1_proves_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """A machine without the manifest, or with a truncated one, must still
    install — with the manifest proof gone, not with a guess in its place. An
    older shape counts as gone too: v1 recorded hashes alone and v2 added bare
    paths, and neither can say whether a file's bytes are ours."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    _canonical_view(home, store, "vc-x")
    shadow = _junie_copy(home, "vc-x", SKILL_MD + "june 2026 body\n")
    june_digest = hashlib.sha256((shadow / "SKILL.md").read_bytes()).hexdigest()

    assert installer.load_skill_provenance(store) == {}
    assert [
        d.classification for d in installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    ] == ["unknown"]

    # The identical-tree proof needs no manifest at all.
    (shadow / "SKILL.md").write_text(
        (store / "vc-x" / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert [
        d.classification for d in installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    ] == ["managed_identical"]
    (shadow / "SKILL.md").write_text(SKILL_MD + "june 2026 body\n", encoding="utf-8")

    manifest = store / installer.SKILL_PROVENANCE_FILE
    for payload in (
        '{"schema": "vibecrafted.skill',
        json.dumps({"schema": "something.else.v1", "skills": {"vc-x": ["deadbeef"]}}),
        json.dumps(
            {
                "schema": "vibecrafted.skill-provenance.v1",
                "skills": {"vc-x": [june_digest]},
            }
        ),
        json.dumps(
            {
                "schema": "vibecrafted.skill-provenance.v2",
                "skills": {"vc-x": {"sha256": [june_digest], "paths": ["SKILL.md"]}},
            }
        ),
    ):
        manifest.write_text(payload, encoding="utf-8")
        assert installer.load_skill_provenance(store) == {}, payload[:40]
        assert [
            d.classification
            for d in installer.collect_shadowed_skill_dirs(store, ["vc-x"])
        ] == ["unknown"], payload[:40]
    assert shadow.is_dir()


# ---------------------------------------------------------------------------
# The view writer never removes a real directory
# ---------------------------------------------------------------------------


def _install_pass(store: Path, names: list[str], runtimes: list[str]) -> None:
    """Exactly what an install does over the runtime skill dirs, in order."""
    installer.link_and_reconcile_skill_views(runtimes, store, store, names)


def test_a_first_install_links_and_reconciles_in_one_pass(
    tmp_path: Path, monkeypatch
) -> None:
    """Reconciliation refuses to remove a copy before ~/.agents/skills/<skill>
    points at the store, so it has to run after that view is written and before
    the rest. Get it wrong and a first install keeps every copy — and the next
    plain `vibecrafted update` stops at "up to date" without ever retrying."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    shadow = _junie_copy(home, "vc-x", SKILL_MD + "june 2026 body\n")
    _prove(store, "vc-x", shadow)
    # No canonical view exists yet: this is a first-ever install.
    assert not (home / ".agents").exists()

    _install_pass(store, ["vc-x"], ["agents", "junie"])

    # The real directory is gone and the view has taken its place — same path.
    assert shadow.is_symlink()
    quarantined = _quarantine_dirs(crafted_home)
    assert (
        (quarantined[0] / "junie" / "vc-x" / "SKILL.md")
        .read_text(encoding="utf-8")
        .endswith("june 2026 body\n")
    )
    for runtime in ("agents", "junie"):
        view = home / f".{runtime}" / "skills" / "vc-x"
        assert view.is_symlink(), runtime
        assert view.resolve() == (store / "vc-x").resolve(), runtime


def test_a_selection_without_agents_still_reconciles(
    tmp_path: Path, monkeypatch
) -> None:
    """`--tool claude` or an advanced selection can leave `agents` out, and then
    ~/.agents/skills/<skill> never exists. Requiring it made the precondition
    unmeetable by construction, stranding every proven copy on the host."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    shadow = _junie_copy(home, "vc-x", SKILL_MD + "june 2026 body\n")
    _prove(store, "vc-x", shadow)

    _install_pass(store, ["vc-x"], ["junie"])

    assert not (home / ".agents").exists(), "agents was not part of the selection"
    assert shadow.is_symlink()
    assert shadow.resolve() == (store / "vc-x").resolve()
    quarantined = _quarantine_dirs(crafted_home)
    assert (
        (quarantined[0] / "junie" / "vc-x" / "SKILL.md")
        .read_text(encoding="utf-8")
        .endswith("june 2026 body\n")
    )


def test_a_copy_in_a_runtime_nobody_links_is_kept(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The other half of the same rule: with no canonical view and no view being
    written for that runtime, removing the copy would take the skill away."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    shadow = _junie_copy(home, "vc-x", SKILL_MD + "june 2026 body\n")
    _prove(store, "vc-x", shadow)

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], view_runtimes=["claude"]
    )

    assert reconciled == []
    assert [d.path for d in kept] == [shadow]
    assert shadow.is_dir()
    assert "no view is being written for junie" in capsys.readouterr().out


def test_the_canonical_view_is_written_before_shadows_are_reconciled(
    tmp_path: Path, monkeypatch
) -> None:
    """The guard against someone reordering the three steps back."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    shadow = _junie_copy(home, "vc-x", SKILL_MD + "june 2026 body\n")
    _prove(store, "vc-x", shadow)
    seen: list[str] = []
    real_reconcile = installer.reconcile_shadowed_skill_dirs

    def spy(*args, **kwargs):
        canonical = home / ".agents" / "skills" / "vc-x"
        seen.append(
            "canonical-linked" if canonical.is_symlink() else "canonical-absent"
        )
        return real_reconcile(*args, **kwargs)

    monkeypatch.setattr(installer, "reconcile_shadowed_skill_dirs", spy)

    installer.link_and_reconcile_skill_views(
        ["agents", "junie"], store, store, ["vc-x"]
    )

    assert seen == ["canonical-linked"]


def test_an_unproven_copy_in_an_active_runtime_survives_an_install_pass(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The operator's own skill under a vc-* name lives in ~/.claude/skills,
    which the installer writes views into. It used to be rmtree'd there without
    a quarantine copy, on the very run that was supposed to be careful."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    mine = home / ".claude" / "skills" / "vc-x"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("# my own vc-x skill\n", encoding="utf-8")

    _install_pass(store, ["vc-x"], ["agents", "claude"])

    assert not mine.is_symlink()
    assert (mine / "SKILL.md").read_text(encoding="utf-8") == "# my own vc-x skill\n"
    assert _quarantine_dirs(crafted_home) == []
    assert "Keeping real directory" in capsys.readouterr().out
    # The canonical view is still written.
    assert (home / ".agents" / "skills" / "vc-x").is_symlink()


def test_a_real_file_under_a_skill_name_survives_an_install_pass(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A file gets less protection than a directory, not more: detection only
    considers directories, and it skips a runtime whose skills dir is reached
    through a symlink. `~/.grok/skills -> ~/notes` with a note named
    `vc-research` in it was a note the writer unlinked."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-research", "canonical body\n")
    notes = tmp_path / "notes"
    notes.mkdir()
    note = notes / "vc-research"
    note.write_text("my own notes on research\n", encoding="utf-8")
    grok = home / ".grok"
    grok.mkdir(parents=True)
    (grok / "skills").symlink_to(notes)

    _install_pass(store, ["vc-research"], ["agents", "grok"])

    assert note.is_file()
    assert not note.is_symlink()
    assert note.read_text(encoding="utf-8") == "my own notes on research\n"
    assert "Keeping real file" in capsys.readouterr().out
    assert (home / ".agents" / "skills" / "vc-research").is_symlink()


def test_a_proven_copy_in_an_active_runtime_is_quarantined_then_linked(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    _canonical_view(home, store, "vc-x")
    claude = home / ".claude" / "skills" / "vc-x"
    claude.mkdir(parents=True)
    (claude / "SKILL.md").write_text(SKILL_MD + "june 2026 body\n", encoding="utf-8")
    _prove(store, "vc-x", claude)

    _install_pass(store, ["vc-x"], ["agents", "claude"])

    assert claude.is_symlink()
    assert claude.resolve() == (store / "vc-x").resolve()
    quarantined = _quarantine_dirs(crafted_home)
    assert (
        (quarantined[0] / "claude" / "vc-x" / "SKILL.md")
        .read_text(encoding="utf-8")
        .endswith("june 2026 body\n")
    )


def test_a_stale_junction_view_is_replaced_without_rmtree(
    tmp_path: Path, monkeypatch
) -> None:
    """A junction is `is_dir()` and not `is_symlink()`, so a stale one landed in
    the writer's `shutil.rmtree` branch — which raises on a junction on Windows
    and takes the install down with it."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    # An empty real directory stands in for a junction: a reparse point is what
    # `rmdir` removes and `unlink` refuses, which is the distinction under test.
    stale = home / ".claude" / "skills" / "vc-x"
    stale.mkdir(parents=True)

    real_is_junction = getattr(Path, "is_junction", None)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda self: self == stale or bool(real_is_junction and real_is_junction(self)),
        raising=False,
    )

    def never(*args, **kwargs):
        raise AssertionError("rmtree must never touch a view pointer")

    monkeypatch.setattr(installer.shutil, "rmtree", never)

    assert not stale.is_symlink()
    assert installer._is_owned_pointer(stale)

    installer.create_skill_view_symlink(store / "vc-x", stale)

    assert stale.is_symlink()
    assert stale.resolve() == (store / "vc-x").resolve()
    assert (store / "vc-x" / "SKILL.md").is_file()


def test_a_dangling_junction_is_removed_instead_of_crashing_the_writer(
    tmp_path: Path, monkeypatch
) -> None:
    """`exists()` follows a pointer, so one aimed at something gone reads as
    absent — and `symlink_to` then raises FileExistsError on the entry that is
    still very much there. A junction outliving its generation is the case."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    stale = home / ".claude" / "skills" / "vc-x"
    stale.parent.mkdir(parents=True)
    stale.mkdir()

    real_is_junction = getattr(Path, "is_junction", None)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda self: self == stale or bool(real_is_junction and real_is_junction(self)),
        raising=False,
    )
    # A junction whose target is gone: a pointer that `exists()` denies.
    monkeypatch.setattr(Path, "exists", lambda self, **_: self != stale)

    assert not stale.exists()
    assert installer._is_owned_pointer(stale)

    installer.create_skill_view_symlink(store / "vc-x", stale)

    assert stale.is_symlink()
    assert stale.resolve() == (store / "vc-x").resolve()


def test_a_real_file_under_a_skill_name_is_reported_not_ignored(
    tmp_path: Path, monkeypatch
) -> None:
    """Detection only ever looked at directories, so the operator's own note at
    `~/.grok/skills/vc-research` was invisible to doctor while the view writer
    stood ready to remove it."""
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store = crafted_home / "skills"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    _canonical_view(home, store, "vc-x")
    note = home / ".junie" / "skills" / "vc-x"
    note.parent.mkdir(parents=True)
    note.write_text("my own note\n", encoding="utf-8")

    detected = installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    assert [(d.runtime, d.classification) for d in detected] == [("junie", "unknown")]
    assert "regular file, kept" in detected[0].detail
    assert not detected[0].is_managed

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], shadows=detected
    )

    assert reconciled == []
    assert [d.path for d in kept] == [note]
    assert note.read_text(encoding="utf-8") == "my own note\n"

    state = installer.InstallState(
        framework_version="4.3.1", skills=["vc-x"], runtimes=["agents"]
    )
    state.save(store)
    monkeypatch.setattr(installer, "FOUNDATIONS", [])

    findings = installer.run_doctor(store, state)
    finding = {f.component: f for f in findings}["shadow-dir:junie/vc-x"]

    assert finding.level == "warn"
    assert "regular file, kept" in finding.message
    assert "move it aside yourself" in finding.message or "mv " in finding.message


def test_a_skills_root_linked_into_the_store_leaves_the_store_intact(
    tmp_path: Path, monkeypatch
) -> None:
    """`ln -s ~/.vibecrafted/skills ~/.junie/skills` is the manual workaround
    for the junie gap. Every link the writer would create inside it resolves
    back onto the store copy it points at, so writing one means removing the
    store copy first."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    (store / "vc-x" / "references").mkdir()
    (store / "vc-x" / "references" / "notes.md").write_text("ref\n", encoding="utf-8")
    junie = home / ".junie"
    junie.mkdir(parents=True)
    (junie / "skills").symlink_to(store)

    _install_pass(store, ["vc-x"], ["agents", "junie"])

    assert (store / "vc-x" / "SKILL.md").is_file()
    assert (store / "vc-x" / "references" / "notes.md").read_text() == "ref\n"
    assert not (store / "vc-x").is_symlink()
    assert _quarantine_dirs(crafted_home) == []


def test_doctor_gives_a_copy_in_a_recorded_runtime_the_reconciling_action(
    tmp_path: Path, monkeypatch
) -> None:
    """A real dir in ~/.claude/skills was reported only as `symlink:` COPY, and
    the action list then said plain `vibecrafted update` — a no-op on a host
    already at the latest version. Nothing told the operator whether the copy
    could be proven, either."""
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store = crafted_home / "skills"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    _canonical_view(home, store, "vc-x")
    copy = home / ".claude" / "skills" / "vc-x"
    copy.mkdir(parents=True)
    (copy / "SKILL.md").write_text(SKILL_MD + "june 2026 body\n", encoding="utf-8")
    _prove(store, "vc-x", copy)

    state = installer.InstallState(
        framework_version="4.3.1", skills=["vc-x"], runtimes=["agents", "claude"]
    )
    state.save(store)
    monkeypatch.setattr(installer, "FOUNDATIONS", [])

    findings = installer.run_doctor(store, state)
    indexed = {finding.component: finding for finding in findings}

    # Section 4 still owns the view contract...
    assert indexed["symlink:claude/vc-x"].level == "fail"
    assert "is a COPY" in indexed["symlink:claude/vc-x"].message
    assert "--force" in indexed["symlink:claude/vc-x"].message
    # ...and the provenance class is reported alongside it.
    shadow = indexed["shadow-dir:claude/vc-x"]
    assert shadow.level == "warn"
    assert "managed_stale" in shadow.message
    assert "quarantines and removes it" in shadow.message

    actions = installer._doctor_action_items(findings)
    assert any("vibecrafted update --force" in action for action in actions), actions


def test_a_copy_finding_alone_still_asks_for_force(tmp_path: Path, monkeypatch) -> None:
    """Even with the provenance manifest gone, the COPY finding must route to
    the command that reconciles rather than to the one that says "up to date"."""
    findings = [
        installer.DoctorFinding(
            "fail",
            "symlink:claude/vc-x",
            "is a COPY, not a symlink — stale drift risk; "
            "`vibecrafted update --force` reconciles it",
        )
    ]

    actions = installer._doctor_action_items(findings)

    assert any("vibecrafted update --force" in action for action in actions), actions


# ---------------------------------------------------------------------------
# Orphans: a name leaving the bundle is not a licence to delete the directory
# ---------------------------------------------------------------------------


def test_an_unproven_orphan_survives_a_non_interactive_install(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """`vc-canvas` is a skill we retired. It is also a name an operator can put
    their own work under, and orphan pruning used to rmtree it either way —
    with `ask_yn` returning its `default=True` in a non-interactive install, so
    a piped install answered the prompt for them."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    mine = home / ".junie" / "skills" / "vc-canvas"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("# my own canvas skill\n", encoding="utf-8")
    (mine / "notes.md").write_text("mine\n", encoding="utf-8")

    removed = installer.prune_orphaned_skills(
        store, ["junie"], {"vc-x"}, interactive=False
    )

    assert removed == 0
    assert (mine / "SKILL.md").read_text(encoding="utf-8") == "# my own canvas skill\n"
    assert (mine / "notes.md").is_file()
    assert _quarantine_dirs(crafted_home) == []
    out = capsys.readouterr().out
    assert "Keeping junie/vc-canvas" in out
    assert "not a Vibecrafted release" in out


def test_a_proven_retired_copy_is_quarantined_then_removed(
    tmp_path: Path, monkeypatch
) -> None:
    """The manifest is built from all of history, so a skill that has left the
    bundle is still provable — and worth removing, once it is safely aside."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    retired = home / ".junie" / "skills" / "vc-canvas"
    retired.mkdir(parents=True)
    (retired / "SKILL.md").write_text(SKILL_MD + "retired body\n", encoding="utf-8")
    _prove(store, "vc-canvas", retired)

    removed = installer.prune_orphaned_skills(
        store, ["junie"], {"vc-x"}, interactive=False
    )

    assert removed == 1
    assert not retired.exists()
    quarantined = _quarantine_dirs(crafted_home)
    assert (
        (quarantined[0] / "junie" / "vc-canvas" / "SKILL.md")
        .read_text(encoding="utf-8")
        .endswith("retired body\n")
    )


def test_an_orphan_symlink_is_still_removed_as_a_pointer(
    tmp_path: Path, monkeypatch
) -> None:
    """A view we wrote is ours to remove, and nothing is quarantined for it."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    gone = store / "vc-canvas"
    gone.mkdir()
    (gone / "SKILL.md").write_text("retired\n", encoding="utf-8")
    view = home / ".junie" / "skills" / "vc-canvas"
    view.parent.mkdir(parents=True)
    view.symlink_to(gone)

    removed = installer.prune_orphaned_skills(
        store,
        ["junie"],
        {"vc-x"},
        orphaned_entries=[("junie", view)],
        interactive=False,
    )

    assert removed == 1
    assert not view.is_symlink()
    assert (gone / "SKILL.md").is_file(), "the target is not ours to delete here"
    assert _quarantine_dirs(crafted_home) == []


def test_an_orphan_reached_through_a_symlink_is_refused(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    elsewhere = tmp_path / "notes"
    (elsewhere / "vc-canvas").mkdir(parents=True)
    (elsewhere / "vc-canvas" / "SKILL.md").write_text("mine\n", encoding="utf-8")
    junie = home / ".junie"
    junie.mkdir(parents=True)
    (junie / "skills").symlink_to(elsewhere)

    removed = installer.prune_orphaned_skills(
        store, ["junie"], {"vc-x"}, interactive=False
    )

    assert removed == 0
    assert (elsewhere / "vc-canvas" / "SKILL.md").is_file()
    assert "reached through a symlink" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Symlinked runtime skill dirs must never route a removal into the store
# ---------------------------------------------------------------------------


def test_symlinked_runtime_skills_dir_is_never_a_shadow_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    """`ln -s ~/.vibecrafted/skills ~/.junie/skills` is the obvious manual
    workaround for the junie gap. Comparing the store with itself classifies
    every skill as an identical copy, and rmtree would follow the link and
    delete the canonical store."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    _canonical_view(home, store, "vc-x")
    junie = home / ".junie"
    junie.mkdir(parents=True)
    (junie / "skills").symlink_to(store)

    assert installer.collect_shadowed_skill_dirs(store, ["vc-x"]) == []
    assert (store / "vc-x" / "SKILL.md").is_file()

    # Even if a caller hands the reconciler that path, the store survives.
    forced = installer.ShadowedSkillDir(
        "junie",
        "vc-x",
        junie / "skills" / "vc-x",
        "managed_identical",
        "forced by a caller",
    )
    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], shadows=[forced]
    )

    assert reconciled == []
    assert [d.path for d in kept] == [forced.path]
    assert (store / "vc-x" / "SKILL.md").is_file()


def test_a_windows_junction_counts_as_a_symlinked_ancestor(
    tmp_path: Path, monkeypatch
) -> None:
    """A junction is a symlink that lies: `is_symlink()` is False for it, while
    `shutil.rmtree` still walks through it. On Windows it is what a per-runtime
    skill dir pointing at the store looks like."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    _canonical_view(home, store, "vc-x")
    shadow = _junie_copy(home, "vc-x", SKILL_MD + "june 2026 body\n")
    _prove(store, "vc-x", shadow)
    junction = home / ".junie"

    real_is_junction = getattr(Path, "is_junction", None)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda self: (
            self == junction or bool(real_is_junction and real_is_junction(self))
        ),
        raising=False,
    )

    assert not junction.is_symlink()
    assert installer._is_owned_pointer(junction)
    assert not installer._path_is_symlink_free(shadow)
    assert installer.collect_shadowed_skill_dirs(store, ["vc-x"]) == []

    # And a caller who hands the reconciler that path is refused too.
    forced = installer.ShadowedSkillDir(
        "junie", "vc-x", shadow, "managed_stale", "forced by a caller"
    )
    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], shadows=[forced]
    )

    assert reconciled == []
    assert [d.path for d in kept] == [shadow]
    assert shadow.is_dir()


def test_symlinked_runtime_home_resolving_into_the_store_is_skipped(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "vibecrafted" / "skills"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    _canonical_view(home, store, "vc-x")
    home.mkdir(parents=True, exist_ok=True)
    (home / ".junie").symlink_to(store.parent)

    assert installer.collect_shadowed_skill_dirs(store, ["vc-x"]) == []
    assert (store / "vc-x" / "SKILL.md").is_file()


def test_symlinked_shadow_entry_is_left_to_the_view_pruner(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    _canonical_view(home, store, "vc-x")
    junie_skills = home / ".junie" / "skills"
    junie_skills.mkdir(parents=True)
    link = junie_skills / "vc-x"
    link.symlink_to(store / "vc-x")

    assert installer.collect_shadowed_skill_dirs(store, ["vc-x"]) == []
    assert link.is_symlink()
    assert (store / "vc-x" / "SKILL.md").is_file()


# ---------------------------------------------------------------------------
# A linked runtime skills root: skipped by detection, but never in silence
# ---------------------------------------------------------------------------


def test_a_skills_root_linked_into_the_store_is_named_by_doctor(
    tmp_path: Path, monkeypatch
) -> None:
    """`ln -s ~/.vibecrafted/skills ~/.junie/skills` makes detection skip the
    runtime, and the skip used to be indistinguishable from a clean host: no
    finding, and the `shadow-dirs` OK line still listed `~/.junie/skills` among
    the directories it had cleared."""
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store = crafted_home / "skills"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    _canonical_view(home, store, "vc-x")
    junie_skills = home / ".junie" / "skills"
    junie_skills.parent.mkdir(parents=True)
    junie_skills.symlink_to(store)

    problem = installer.runtime_skills_root_problem("junie", store)
    assert problem is not None and "canonical skill store" in problem
    assert installer.collect_shadowed_skill_dirs(store, ["vc-x"]) == []

    state = installer.InstallState(
        framework_version="4.3.1", skills=["vc-x"], runtimes=["agents"]
    )
    state.save(store)
    monkeypatch.setattr(installer, "FOUNDATIONS", [])

    findings = installer.run_doctor(store, state)
    indexed = {finding.component: finding for finding in findings}

    finding = indexed["skill-root:junie"]
    assert finding.level == "warn"
    assert str(junie_skills) in finding.message
    assert "canonical skill store" in finding.message
    assert "nothing there is ever removed" in finding.message
    # The OK line must no longer vouch for a root nobody inspected.
    assert "~/.junie/skills" not in indexed["shadow-dirs"].message

    actions = installer._doctor_action_items(findings)
    assert any("runtime skill root is a link" in action for action in actions), actions


def test_a_skills_root_linked_to_an_unrelated_dir_is_named_by_doctor(
    tmp_path: Path, monkeypatch
) -> None:
    """`~/.junie/skills -> ~/notes` is someone else's tree. Detection skips it
    — correctly, since `rmtree` would follow the pointer — but the operator had
    no way to learn that the directory was never examined."""
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store = crafted_home / "skills"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    _canonical_view(home, store, "vc-x")
    notes = tmp_path / "notes"
    copy = notes / "vc-x"
    copy.mkdir(parents=True)
    (copy / "SKILL.md").write_text(SKILL_MD + "june 2026 body\n", encoding="utf-8")
    _prove(store, "vc-x", copy)
    junie_skills = home / ".junie" / "skills"
    junie_skills.parent.mkdir(parents=True)
    junie_skills.symlink_to(notes)

    problem = installer.runtime_skills_root_problem("junie", store)
    assert problem == f"is reached through a link into {notes}"
    assert installer.collect_shadowed_skill_dirs(store, ["vc-x"]) == []

    state = installer.InstallState(
        framework_version="4.3.1", skills=["vc-x"], runtimes=["agents"]
    )
    state.save(store)
    monkeypatch.setattr(installer, "FOUNDATIONS", [])

    findings = installer.run_doctor(store, state)
    indexed = {finding.component: finding for finding in findings}

    finding = indexed["skill-root:junie"]
    assert finding.level == "warn"
    assert str(notes) in finding.message
    # No provenance verdict is invented for a copy that was never inspected.
    assert "shadow-dir:junie/vc-x" not in indexed
    assert (copy / "SKILL.md").is_file()


def test_the_writer_puts_nothing_inside_a_store_shaped_skills_root(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """With `~/.junie/skills -> <store>`, every view the writer would create is
    a symlink inside the canonical store aimed at its own sibling, and the
    root-rule sync would drop `*_RULE.md` copies in there on every run. The
    store has to stay exactly what we shipped, and the skills are already
    readable through that root — it *is* the store."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    source_skills = tmp_path / "src"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    source_skills.mkdir()
    (source_skills / "VERIFICATION_RULE.md").write_text("rule\n", encoding="utf-8")
    junie_skills = home / ".junie" / "skills"
    junie_skills.parent.mkdir(parents=True)
    junie_skills.symlink_to(store)

    before = sorted(entry.name for entry in store.iterdir())
    installer.link_and_reconcile_skill_views(
        ["agents", "junie"], store, source_skills, ["vc-x"]
    )
    out = capsys.readouterr().out

    assert sorted(entry.name for entry in store.iterdir()) == before
    assert not (store / "VERIFICATION_RULE.md").exists()
    assert [entry for entry in store.rglob("*") if entry.is_symlink()] == []
    assert (store / "vc-x" / "SKILL.md").is_file()
    # The canonical runtime is written as always, rule file included.
    agents_skills = home / ".agents" / "skills"
    assert (agents_skills / "vc-x").is_symlink()
    assert (agents_skills / "VERIFICATION_RULE.md").is_file()
    assert "junie skill root" in out
    assert "no view is written into it" in out
    assert _quarantine_dirs(crafted_home) == []
