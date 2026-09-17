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

MANAGED_SKILL_MD = """---
name: vc-x
loctree_value: "primary repo map"
---

<!-- fleet-imperative: v3 -->
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
    (skill / "SKILL.md").write_text(MANAGED_SKILL_MD + body, encoding="utf-8")
    return skill


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
    shadow = _junie_copy(home, "vc-x", MANAGED_SKILL_MD + "june 2026 body\n")

    detected = installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    assert [(d.runtime, d.skill, d.classification) for d in detected] == [
        ("junie", "vc-x", "managed_stale")
    ]

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], ["agents", "claude", "codex"], shadows=detected
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
    shadow = _junie_copy(home, "vc-x", MANAGED_SKILL_MD + "canonical body\n")
    (shadow / "references").mkdir()
    (shadow / "references" / "notes.md").write_text("ref\n", encoding="utf-8")
    # Editor litter must not make an identical copy look drifted.
    (shadow / ".DS_Store").write_bytes(b"\x00")

    detected = installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    assert [d.classification for d in detected] == ["managed_identical"]

    # A single nested byte of drift flips the classification, not the removal
    # policy: provenance still holds, so the copy is still reconciled.
    (shadow / "references" / "notes.md").write_text("drifted\n", encoding="utf-8")
    assert [
        d.classification for d in installer.collect_shadowed_skill_dirs(store, ["vc-x"])
    ] == ["managed_stale"]
    (shadow / "references" / "notes.md").write_text("ref\n", encoding="utf-8")

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], ["agents"], shadows=detected
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
        store, ["vc-x"], ["agents"], shadows=detected
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
    assert installer.reconcile_shadowed_skill_dirs(store, ["vc-x"], ["agents"]) == (
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
    (canonical / "SKILL.md").write_text(MANAGED_SKILL_MD, encoding="utf-8")

    assert "agents" not in installer.shadow_candidate_runtimes()
    assert (
        installer.collect_shadowed_skill_dirs(store, ["vc-x"], ["agents", "junie"])
        == []
    )
    assert canonical.is_dir()


def test_active_view_runtime_is_left_to_the_symlink_writer(
    tmp_path: Path, monkeypatch
) -> None:
    """create_skill_view_symlink already rmtree's a dir before linking; the
    reconciler must not double-handle runtimes that are active view targets."""
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    _canonical_view(home, store, "vc-x")
    shadow = _junie_copy(home, "vc-x", MANAGED_SKILL_MD + "june 2026 body\n")

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], ["agents", "junie"]
    )

    assert (reconciled, kept) == ([], [])
    assert shadow.is_dir()


def test_reconcile_keeps_copy_when_canonical_view_is_not_linked(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = tmp_path / "crafted"
    store = tmp_path / "store"
    _pin_home(monkeypatch, home, crafted_home)
    _store_skill(store, "vc-x", "canonical body\n")
    shadow = _junie_copy(home, "vc-x", MANAGED_SKILL_MD + "june 2026 body\n")

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], ["agents"]
    )

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
    shadow = _junie_copy(home, "vc-x", MANAGED_SKILL_MD + "june 2026 body\n")

    reconciled, kept = installer.reconcile_shadowed_skill_dirs(
        store, ["vc-x"], ["agents"], dry_run=True
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
    shadow = _junie_copy(home, "vc-x", MANAGED_SKILL_MD + "june 2026 body\n")

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
    # Runtimes that section 4 already owns must not be double-reported.
    assert "shadow-dir:claude/vc-x" not in indexed
    assert any(
        "reconcile stale runtime skill copies" in action
        for action in installer._doctor_action_items(findings)
    )
