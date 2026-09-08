"""Falsifiers for G3 dispatch doctor, worktree baseline, research help, ship deck."""

from __future__ import annotations

import os
from pathlib import Path

from vibecrafted_core.cli import main as cli_main
from vibecrafted_core.dispatch.schema import doctor_dispatch
from vibecrafted_core.dispatch.supervisor import (
    prefer_live_descendant_head,
    select_live_descendant_head,
)
from vibecrafted_core.help_surface import render_workflow_help
from vibecrafted_core.research_config import _yaml_lanes

FIXTURES = Path(__file__).resolve().parent / "dispatch" / "fixtures"
MINIMAL = (FIXTURES / "minimal.dispatch.toml").read_text(encoding="utf-8")
_PROVIDER_ERROR = "provider-specific or repo-local runtime roots"


def _with_reports_dir(reports_dir: str) -> str:
    return MINIMAL.replace(
        'reports_dir = "/tmp/vibecrafted-dispatch-fixture/reports"',
        f'reports_dir = "{reports_dir}"',
    )


def test_fail_g3_doctor_accepts_canonical_artifacts_plane() -> None:
    """Doctor must not refuse the artifact root its own error names.

    A substring ``/.vibecrafted/`` matched ``~/.vibecrafted/artifacts/...``
    and ``$VIBECRAFTED_HOME/artifacts/...``, so --doctor rejected the
    canonical write plane.
    """
    isolated = Path(os.environ["VIBECRAFTED_HOME"])
    canonical = isolated / "artifacts" / "org" / "repo" / "2026_0908"
    tilde = "~/.vibecrafted/artifacts/org/repo/2026_0908"

    for reports_dir in (str(canonical), tilde):
        result = doctor_dispatch(_with_reports_dir(reports_dir), base_dir=FIXTURES)
        assert not any(_PROVIDER_ERROR in error for error in result.errors), (
            reports_dir,
            result.errors,
        )


def test_fail_g3_doctor_still_rejects_repo_local_and_provider_roots(
    tmp_path: Path,
) -> None:
    """Repo-local ``.vibecrafted`` and provider homes remain recovery-only."""
    repo_local = tmp_path / "checkout" / ".vibecrafted" / "reports"
    provider = str(Path.home() / ".codex" / "sessions")

    for reports_dir in (str(repo_local), provider):
        result = doctor_dispatch(_with_reports_dir(reports_dir), base_dir=FIXTURES)
        assert any(_PROVIDER_ERROR in error for error in result.errors), (
            reports_dir,
            result.errors,
        )


def test_fail_g3_doctor_rejects_artifacts_typo_and_traversal_without_existing_leaf() -> (
    None
):
    """Admission is a normalized directory boundary, not a prefix or example list.

    Neither ``artifacts-typo`` nor ``artifacts/../../.codex`` is the canonical
    plane. The leaf does not need to exist for the refusal.
    """
    typo = "~/.vibecrafted/artifacts-typo/report.md"
    traversal = "~/.vibecrafted/artifacts/../../.codex/report.md"
    for reports_dir in (typo, traversal):
        result = doctor_dispatch(_with_reports_dir(reports_dir), base_dir=FIXTURES)
        assert any(_PROVIDER_ERROR in error for error in result.errors), (
            reports_dir,
            result.errors,
        )


def test_fail_g3_doctor_rejects_existing_symlink_escape_without_leaf(
    tmp_path: Path,
) -> None:
    """Follow an existing symlink out of artifacts even if the output file is absent."""
    isolated = Path(os.environ["VIBECRAFTED_HOME"])
    artifacts = isolated / "artifacts"
    artifacts.mkdir(parents=True)
    provider = tmp_path / "provider-codex"
    provider.mkdir()
    escape = artifacts / "escape"
    escape.symlink_to(provider)
    reports_dir = str(escape / "report.md")
    assert not Path(reports_dir).exists()

    result = doctor_dispatch(_with_reports_dir(reports_dir), base_dir=FIXTURES)
    assert any(_PROVIDER_ERROR in error for error in result.errors), result.errors


def test_fail_g3_worktree_baseline_prefers_descendant_head() -> None:
    """A later living-tree HEAD that contains the receipt SHA wins.

    Workers were pinned to the integrator commit frozen in the receipt even
    when the checkout had already advanced.
    """
    receipt = "aa" * 20
    later = "bb" * 20
    unrelated = "cc" * 20
    ancestors = {(receipt, later)}

    def is_ancestor(ancestor: str, descendant: str) -> bool:
        return (ancestor, descendant) in ancestors

    assert prefer_live_descendant_head(receipt, later, is_ancestor=is_ancestor) == later
    assert (
        prefer_live_descendant_head(receipt, receipt, is_ancestor=is_ancestor)
        == receipt
    )
    assert (
        prefer_live_descendant_head(receipt, unrelated, is_ancestor=is_ancestor)
        == receipt
    )
    kept = select_live_descendant_head(receipt, unrelated, is_ancestor=is_ancestor)
    assert kept.selected == receipt
    assert kept.planned == receipt
    assert kept.reason == "frozen_baseline_kept_head_not_descendant"
    drifted = select_live_descendant_head(receipt, later, is_ancestor=is_ancestor)
    assert drifted.selected == later
    assert drifted.reason == "live_descendant_head"
    assert "local-069" in drifted.source_requirement


def test_fail_g3_research_help_documents_yaml_n_lanes_not_only_trio() -> None:
    """YAML already accepts four lanes; help must not teach a trio-only product."""
    agents, _models, ignored = _yaml_lanes(
        {
            "lanes": [
                {"agent": "grok"},
                {"agent": "codex"},
                {"agent": "claude"},
                {"agent": "agy"},
            ],
            "lane_count": 4,
        }
    )
    assert agents == ("grok", "codex", "claude", "agy")
    assert ignored == ()

    help_text = render_workflow_help("research")
    assert "research.yaml" in help_text
    assert "lane_count" in help_text
    assert "four" in help_text


def test_fail_g3_vibecrafted_ship_roadmap_reaches_ship_module(
    monkeypatch, tmp_path: Path
) -> None:
    """``vibecrafted ship roadmap --render`` must not die in deck argparse."""
    seen: list[list[str]] = []

    def fake_ship_main(argv: list[str] | None = None) -> int:
        seen.append(list(argv or []))
        return 0

    monkeypatch.setattr("vibecrafted_core.ship.main", fake_ship_main)
    plan = tmp_path / "plan"
    rc = cli_main(["ship", "roadmap", "--render", "--plan", str(plan)])
    assert rc == 0
    assert seen == [["roadmap", "--render", "--plan", str(plan)]]
