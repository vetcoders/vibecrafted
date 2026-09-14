from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAFFOLDER = REPO_ROOT / "tools" / "vc-skill-new.sh"
TEMPLATE_SKILL = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "skills"
    / "_template"
    / "SKILL.md"
)
# Pre-VCI-0002a template: Purpose jumped straight to When To Use.
BASELINE_TEMPLATE_SHA = "2dcdab37f463df9042df21b2821de6048005f770"
BASELINE_TEMPLATE_REL = "vibecrafted-core/vibecrafted_core/skills/_template/SKILL.md"


def test_vc_skills_preserve_init_and_loctree_orientation_contract() -> None:
    skill_files = sorted(
        (REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "skills").glob(
            "vc-*/SKILL.md"
        )
    )
    assert skill_files, "No vc-* skill files discovered"

    missing: list[str] = []
    for skill_file in skill_files:
        if skill_file.parent.name == "vc-init":
            continue
        text = skill_file.read_text(encoding="utf-8")
        has_gate = (
            "## Canonical Orientation Gate" in text
            or "## Canonical Structural Gate" in text
        )
        required = [
            ("canonical gate", has_gate),
            ("vc-init procedure", "`vc-init`" in text),
            ("Loctree skill", "`Loctree:loctree`" in text),
            ("Code-Derived Application Map", "Code-Derived Application Map" in text),
        ]
        for label, ok in required:
            if not ok:
                missing.append(f"{skill_file.relative_to(REPO_ROOT)} missing {label}")

    assert not missing, "\n".join(missing)


def test_loctree_skills_match_literal_and_structural_runtime_truth() -> None:
    paths = [
        REPO_ROOT / "vibecrafted-core/vibecrafted_core/skills/vc-loctree/SKILL.md",
        REPO_ROOT / "vibecrafted-core/vibecrafted_core/skills/pl/vc-loctree/SKILL.md",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "`loct find Identifier`" in text
        assert "`loct find --discover Terms`" in text
        assert "`loct find Identifier --where-symbol`" in text
        assert "38/38" in text
        assert "22/22" in text
        assert (
            "zero-consumer/dead result is a candidate" in text
            or "Zero konsumentów/dead to kandydat" in text
        )
        assert "objective structural truth" not in text
        assert "Every action must trace" not in text


def test_vc_operator_uses_one_repository_local_operator_journal() -> None:
    operator = REPO_ROOT / "vibecrafted-core/vibecrafted_core/skills/vc-operator"
    pl_operator = REPO_ROOT / "vibecrafted-core/vibecrafted_core/skills/pl/vc-operator"
    canonical_docs = sorted(operator.rglob("*.md"))
    mirror_docs = sorted(pl_operator.rglob("*.md"))
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in canonical_docs)
    all_operator_docs = (
        corpus
        + "\n"
        + "\n".join(path.read_text(encoding="utf-8") for path in mirror_docs)
    )

    assert "<repo-root>/.vibecrafted/JOURNAL.md" in corpus
    assert "journal.md" not in all_operator_docs
    assert "Only the Operator writes the journal" in corpus
    assert "surface a falsifiable finding to the active Operator" in corpus
    assert "dispatches the cut into a dedicated worktree" in corpus
    assert "beyond the current ITP or TD" in corpus
    assert "routine negative-work claims" in corpus
    assert "What's NOT done (deliberately)" not in corpus
    assert re.search(r"\bOpera\b", all_operator_docs) is None


def test_vc_scaffold_emits_dispatch_and_preserves_embargo_recovery_contract() -> None:
    skills = REPO_ROOT / "vibecrafted-core/vibecrafted_core/skills"
    variants = [
        (
            skills / "vc-scaffold",
            (
                (
                    "For a local worker checkpoint under a declared embargo, "
                    "`--no-verify` is fully authorized."
                ),
                "No push, publication, or remote `embargo/<plan-id>` ref.",
                "runs Semgrep plus secret/security review",
                "this is neither security-clean nor verified delivery.",
                (
                    "Full language-appropriate deferred and normal gates pass and are "
                    "recorded against the exact admitted SHA."
                ),
            ),
        ),
        (
            skills / "pl/vc-scaffold",
            (
                (
                    "Przy lokalnym checkpoincie workera pod zadeklarowanym embargiem "
                    "`--no-verify` jest w pełni\nautoryzowany."
                ),
                "Bez push, publikacji ani zdalnego refa `embargo/<plan-id>`.",
                "uruchamia Semgrep oraz przegląd sekretów/bezpieczeństwa",
                "to nie jest security-clean ani verified delivery.",
                (
                    "Pełne, odpowiednie dla języka bramki odroczone i normalne "
                    "przechodzą i są zapisane dla dokładnego dopuszczonego SHA."
                ),
            ),
        ),
    ]

    for scaffold, embargo_contract in variants:
        skill = (scaffold / "SKILL.md").read_text(encoding="utf-8")
        flow = (scaffold / "FLOW.md").read_text(encoding="utf-8")
        template = (scaffold / "references/plan-template.md").read_text(
            encoding="utf-8"
        )
        embargo = (scaffold / "references/compile-embargo.md").read_text(
            encoding="utf-8"
        )
        corpus = f"{skill}\n{flow}\n{template}\n{embargo}"

        assert 'schema = "vibecrafted.dispatch.v1"' in corpus
        assert ".dispatch.toml" in skill
        assert "/vc-ship" in flow
        assert "/vc-ship" in template
        assert "--doctor" in template
        assert (
            "Emergency manual fallback" in template
            or "Awaryjny fallback ręczny" in template
        )
        assert "founder_interview_evidence:" in template
        assert "AICX" in skill
        assert "policy-aware" in embargo
        for marker in embargo_contract:
            assert marker in embargo, (
                f"{scaffold.relative_to(REPO_ROOT)} missing embargo contract: {marker!r}"
            )


def _heading_pos(text: str, heading: str) -> int:
    marker = f"## {heading}"
    idx = text.find(marker)
    assert idx >= 0, f"missing heading {heading!r}"
    return idx


def _baseline_template_skill_md() -> str:
    proc = subprocess.run(
        ["git", "show", f"{BASELINE_TEMPLATE_SHA}:{BASELINE_TEMPLATE_REL}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0 and proc.stdout, (
        "baseline template blob missing; need "
        f"{BASELINE_TEMPLATE_SHA}:{BASELINE_TEMPLATE_REL}"
    )
    return proc.stdout


def _mini_fixture_repo(tmp_path: Path, template_skill_md: str) -> Path:
    repo = tmp_path / "skill-scaffold-fixture"
    tools = repo / "tools"
    template_dir = (
        repo / "vibecrafted-core" / "vibecrafted_core" / "skills" / "_template"
    )
    tools.mkdir(parents=True)
    template_dir.mkdir(parents=True)
    shutil.copy(SCAFFOLDER, tools / "vc-skill-new.sh")
    (tools / "vc-skill-new.sh").chmod(0o755)
    (template_dir / "SKILL.md").write_text(template_skill_md, encoding="utf-8")
    return repo


def _scaffold_skill(repo: Path, name: str) -> Path:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        ["bash", str(repo / "tools" / "vc-skill-new.sh"), name],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"scaffolder failed for {name}: {proc.stderr or proc.stdout}"
    )
    generated = (
        repo / "vibecrafted-core" / "vibecrafted_core" / "skills" / name / "SKILL.md"
    )
    assert generated.is_file(), f"scaffolder did not write {generated}"
    return generated


def test_original_template_scaffold_has_no_goal_after_purpose(
    tmp_path: Path,
) -> None:
    original = _baseline_template_skill_md()
    assert "## Goal" not in original
    repo = _mini_fixture_repo(tmp_path, original)
    generated = _scaffold_skill(repo, "vc-goalprobe").read_text(encoding="utf-8")
    purpose = _heading_pos(generated, "Purpose")
    when_to_use = _heading_pos(generated, "When To Use")
    assert purpose < when_to_use
    assert "## Goal" not in generated
    assert "{{SKILL_NAME}}" not in generated
    assert "vc-goalprobe" in generated


def test_live_template_scaffold_places_goal_after_purpose_with_name(
    tmp_path: Path,
) -> None:
    live = TEMPLATE_SKILL.read_text(encoding="utf-8")
    repo = _mini_fixture_repo(tmp_path, live)
    generated = _scaffold_skill(repo, "vc-goalprobe").read_text(encoding="utf-8")
    purpose = _heading_pos(generated, "Purpose")
    goal = _heading_pos(generated, "Goal")
    when_to_use = _heading_pos(generated, "When To Use")
    acceptance = _heading_pos(generated, "Acceptance Criteria")
    assert purpose < goal < when_to_use < acceptance
    goal_body = generated[goal:when_to_use]
    assert "\n## " not in goal_body[len("## Goal") :]
    assert "{{SKILL_NAME}}" not in generated
    assert "vc-goalprobe" in goal_body
    assert "TODO concrete result" in goal_body
    assert "TODO verifiable endpoint" in goal_body
    assert "Founder owns the final wording" in goal_body
    assert "Acceptance Criteria" in generated[acceptance:]
