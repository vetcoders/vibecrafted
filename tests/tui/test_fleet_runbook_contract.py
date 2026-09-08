from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from vibecrafted_core.dispatch import cli as dispatch_cli
from vibecrafted_core.dispatch.schema import load_dispatch

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = REPO_ROOT / "docs" / "operator" / "FLEET_DISPATCH_RUNBOOK.md"
FLEET_TEMPLATE = (
    REPO_ROOT / "examples" / "dispatch" / "ship-acceptance-fleet.dispatch.toml"
)
LAUNCHER = REPO_ROOT / "scripts" / "vibecrafted"


def test_runbook_fleet_template_is_a_three_cut_two_provider_worktree_plan() -> None:
    """The executable template, not prose, supplies the fleet topology."""
    dispatch = load_dispatch(FLEET_TEMPLATE)

    assert dispatch.schema == "vibecrafted.dispatch.v1"
    assert [phase.title for phase in dispatch.phases] == ["Acceptance"]
    assert dispatch.policy.concurrency == 3
    assert dispatch.policy.allow_concurrency is True
    assert dispatch.policy.require_commit is True
    assert [cut.id for cut in dispatch.cuts] == ["W0-a", "W0-b", "W0-c"]
    assert [cut.agent for cut in dispatch.cuts] == ["codex", "claude", "codex"]
    assert all(cut.integrator is False for cut in dispatch.cuts)


def test_runbook_commands_are_backed_by_public_dispatch_and_ship_parsers() -> None:
    """Keep documented recovery on real parsers, not a prose-only promise."""
    text = RUNBOOK.read_text(encoding="utf-8")
    assert 'vibecrafted dispatch "$PLAN" --doctor --json' in text
    assert 'vibecrafted dispatch "$PLAN" --dry-run --json' in text
    assert 'vibecrafted dispatch "$PLAN" --resume <dispatch-run-id>' in text
    assert 'vibecrafted dispatch "$PLAN" --cleanup-settled <dispatch-run-id>' in text
    assert "vibecrafted ship interrupt <life-ship-run-id> --json" in text

    parser = dispatch_cli._build_parser()
    assert parser.parse_args(["fleet.dispatch.toml", "--doctor", "--json"]).doctor
    assert parser.parse_args(["fleet.dispatch.toml", "--dry-run", "--json"]).dry_run
    parsed = parser.parse_args(["fleet.dispatch.toml", "--resume", "dispatch-123"])
    assert parsed.dispatch_file == "fleet.dispatch.toml"
    assert parsed.resume == "dispatch-123"
    assert (
        parser.parse_args(
            ["fleet.dispatch.toml", "--cleanup-settled", "dispatch-123"]
        ).cleanup_settled
        == "dispatch-123"
    )

    result = subprocess.run(
        ["bash", str(LAUNCHER), "ship", "interrupt", "--help"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "usage: vc-ship <control> interrupt" in result.stdout
    assert "run_id" in result.stdout


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["start", "--help"], "Start the operator vc-frame session"),
        (["status", "--help"], "Show today's runs"),
        (["await", "--help"], "Join the shared vc-server monitor"),
        (["observe", "--help"], "Check an agent report or transcript"),
        (["resume", "--help"], "Resume a stopped control-plane run"),
        (["dispatch", "--help"], "vibecrafted.dispatch.v1 TOML plan"),
    ],
)
def test_runbook_observability_commands_have_public_help(
    argv: list[str], expected: str
) -> None:
    """The document names only deck commands whose public help resolves."""
    result = subprocess.run(
        ["bash", str(LAUNCHER), *argv],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert expected in result.stdout
