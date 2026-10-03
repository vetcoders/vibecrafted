from __future__ import annotations

import json
import os
import re
import shutil
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
SKILLS = REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "skills"


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
        (["start", "--help"], "Create a vc-frame workspace for a repository"),
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


@pytest.mark.parametrize(
    "relative",
    [
        "vc-dispatch/SKILL.md",
        "vc-dispatch/references/toml-plan-preflight.md",
        "vc-dispatch/references/prompt-checklist.md",
    ],
)
def test_dispatch_skills_require_owned_staging(relative: str) -> None:
    """Every prompt-authoring surface must protect foreign concurrent edits."""
    text = " ".join((SKILLS / relative).read_text(encoding="utf-8").split())
    assert "git add -- <owned-path>" in text
    assert "sweeps preserve concurrent work" not in text
    assert "sweeps are commitment" not in text


@pytest.mark.parametrize(
    "relative",
    [
        "vc-dispatch/SKILL.md",
        "vc-dispatch/references/toml-plan-preflight.md",
        "vc-dispatch/references/prompt-checklist.md",
    ],
)
def test_dispatch_skills_preserve_supervisor_worktree_contract(relative: str) -> None:
    """Typed dispatch workers inherit the assigned cut, never invent isolation."""
    text = " ".join((SKILLS / relative).read_text(encoding="utf-8").split())
    assert "cut/<cut-id>" in text
    assert "supervisor" in text.lower()
    assert "local-worktrees" in text
    assert "zero worktree" not in text
    assert "worker then creates its OWN worktree" not in text


def test_scaffold_brief_template_satisfies_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Render the official brief verbatim; the real doctor decides its validity."""
    # Use an existing binary: a documentation test must not install a toolchain.
    candidates = [
        Path(os.environ[key]) / "bin/scaffold-doctor"
        for key in ("VIBECRAFTED_RUNTIME_ROOT", "VIBECRAFTED_ROOT")
        if os.environ.get(key)
    ]
    if os.environ.get("VIBECRAFTED_RUNTIME_BIN"):
        candidates.insert(
            0, Path(os.environ["VIBECRAFTED_RUNTIME_BIN"]) / "scaffold-doctor"
        )
    candidates.extend(
        REPO_ROOT / "vibecrafted-server/target" / mode / "scaffold-doctor"
        for mode in ("debug", "release")
    )
    if found := shutil.which("scaffold-doctor"):
        candidates.append(Path(found))
    doctor = next((path for path in candidates if path.is_file()), None)
    if doctor is None:
        pytest.skip("requires a built scaffold-doctor or VIBECRAFTED_RUNTIME_BIN")
    plan = tmp_path / "artifacts/vetcoders/vibecrafted/2026_1001/plans/template-test"
    (plan / "briefs").mkdir(parents=True)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path))
    source = (SKILLS / "vc-scaffold/references/output-shapes.md").read_text(
        encoding="utf-8"
    )
    brief = source.split("```markdown\n", 1)[1].split("\n```", 1)[0]
    # Fill plan identity and content, preserving every heading from the source.
    values = {
        "plan-id": "template-test",
        "session-id": "test-session",
        "YYYY-MM-DD": "2026-10-01",
        "org": "vetcoders",
        "repo": "vibecrafted",
        "slug": "W1-01",
        "Wn": "W1",
        "title": "Template smoke",
        "claude|codex|gemini|cursor": "codex",
        "agent": "codex",
    }
    brief = re.sub(r"<([^>]+)>", lambda m: values.get(m[1], "test-value"), brief)
    # Insert content below the source heading without replacing any heading.
    lines = brief.splitlines()
    acceptance = next(i for i, line in enumerate(lines) if line.startswith("## 5."))
    lines.insert(acceptance + 1, "\n- [ ] verifier: printf 'template-probe\\n'\n")
    (plan / "briefs/W1-01_template.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    header = (
        "---\nplan_id: template-test\nsession_id: test-session\nrole: {role}\n"
        "agent: codex\ndate: 2026-10-01\nproject: vetcoders/vibecrafted\n---\n"
    )
    (plan / "DRIVER.md").write_text(
        header.format(role="driver") + f"# DRIVER\n\nFull absolute paths: `{plan}`\n\n"
        "Why: one independent cut.\n\nvibecrafted dispatch plan.dispatch.toml --doctor\n\n"
        "Only a delivery-verifier flips [ ]→[x].\n\ndou-index = 0/1\n\n"
        "## Odbiór (matryca wyników)\n\n"
        "| cut | commit | dowód (Operator) | Worker | Operator | Founder |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| W1-01 | — | — | [ ] | [ ] | [ ] |\n\n"
        "Zatwierdzono przez: Worker [ ] Operator [ ] Founder [ ]\n",
        encoding="utf-8",
    )
    (plan / "00_ATLAS.md").write_text(
        header.format(role="wave-atlas")
        + "# Wave atlas\n\n| Cut | Vector | Depends |\n| --- | --- | --- |\n"
        "| W1-01 | stabilize | — |\n\nDependency graph: W1-01 → done\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "1",
        "plan_id": "template-test",
        "org": "vetcoders",
        "repo": "vibecrafted",
        "day": "2026_1001",
        "artifacts": [
            {"id": name, "role": role, "path": path, "editable": True, "required": True}
            for name, role, path in [
                ("driver", "driver", "DRIVER.md"),
                ("atlas", "wave-atlas", "00_ATLAS.md"),
                ("w1-01", "brief", "briefs/W1-01_template.md"),
            ]
        ],
    }
    (plan / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = subprocess.run(
        [str(doctor), "--plan", str(plan), "--json"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["valid"] is True
