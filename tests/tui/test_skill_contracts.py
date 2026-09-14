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
TEMPLATE_DIR = TEMPLATE_SKILL.parent
_HEADING_RE = re.compile(r"(?m)^## (.+)$")
_ENDPOINT_PLACEHOLDER_RE = re.compile(r"(?i)\bTODO\b.+\bendpoint\b")
_AMBIENT_RUNTIME_ENV = (
    "PYTHONPATH",
    "VIBECRAFTED_RUNTIME_HOME",
    "VIBECRAFTED_TOOLS_HOME",
    "VIBECRAFTED_RUNTIME_BIN",
    "VIBECRAFTED_ROOT",
    "VIBECRAFTED_RUNTIME_ROOT",
    "VIBECRAFTED_PYTHON",
    "VIBECRAFTED_SOURCE",
)


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


def _markdown_headings(text: str) -> list[tuple[int, str]]:
    return [
        (match.start(), match.group(1).strip()) for match in _HEADING_RE.finditer(text)
    ]


def goal_acceptance_errors(text: str, skill_name: str) -> list[str]:
    """Semantic Goal contract for a generated SKILL.md.

    Codes are stable so the external fail-before/pass-after probe can assert
    `missingGoal` without depending on Git history inside pytest.
    """

    errors: list[str] = []
    headings = _markdown_headings(text)
    names = [name for _, name in headings]
    if "Purpose" not in names:
        errors.append("missingHeading:Purpose")
    if "Goal" not in names:
        errors.append("missingGoal")
    if "Acceptance Criteria" not in names:
        errors.append("missingHeading:Acceptance Criteria")
    if "{{SKILL_NAME}}" in text:
        errors.append("unsubstitutedSkillName")

    if (
        "Purpose" in names
        and "Goal" in names
        and names.index("Goal") != names.index("Purpose") + 1
    ):
        errors.append("goalNotImmediatelyAfterPurpose")

    if "Goal" in names:
        goal_index = next(
            index for index, (_, name) in enumerate(headings) if name == "Goal"
        )
        start = headings[goal_index][0]
        end = (
            headings[goal_index + 1][0] if goal_index + 1 < len(headings) else len(text)
        )
        body = text[start:end]
        content = "\n".join(body.splitlines()[1:]).strip()
        paragraphs = [
            paragraph.strip()
            for paragraph in re.split(r"\n\s*\n", content)
            if paragraph.strip() and paragraph.strip() != "---"
        ]
        if len(paragraphs) != 1:
            errors.append(f"goalNotOneParagraph:{len(paragraphs)}")
        goal_text = paragraphs[0] if paragraphs else content
        if skill_name not in goal_text:
            errors.append("missingGeneratedName")
        if not _ENDPOINT_PLACEHOLDER_RE.search(goal_text):
            errors.append("missingEndpointPlaceholder")

    if (
        "Goal" in names
        and "Acceptance Criteria" in names
        and names.index("Acceptance Criteria") <= names.index("Goal")
    ):
        errors.append("acceptanceCriteriaNotSeparate")
    return errors


def _isolated_scaffold_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    for name in _AMBIENT_RUNTIME_ENV:
        env.pop(name, None)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / "xdg-config")
    env["XDG_DATA_HOME"] = str(home / "xdg-data")
    env["XDG_CACHE_HOME"] = str(home / "xdg-cache")
    env["VIBECRAFTED_HOME"] = str(home / "vibecrafted-home")
    return env


def _mini_fixture_repo(tmp_path: Path, skill_md: str | None = None) -> Path:
    repo = tmp_path / "skill-scaffold-fixture"
    tools = repo / "tools"
    skills_root = repo / "vibecrafted-core" / "vibecrafted_core" / "skills"
    tools.mkdir(parents=True)
    skills_root.mkdir(parents=True)
    shutil.copy(SCAFFOLDER, tools / "vc-skill-new.sh")
    (tools / "vc-skill-new.sh").chmod(0o755)
    shutil.copytree(TEMPLATE_DIR, skills_root / "_template")
    if skill_md is not None:
        (skills_root / "_template" / "SKILL.md").write_text(skill_md, encoding="utf-8")
    return repo


def _scaffold_skill(repo: Path, name: str, home: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["bash", str(repo / "tools" / "vc-skill-new.sh"), name],
        cwd=repo,
        env=_isolated_scaffold_env(home),
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


def test_goal_acceptance_rejects_missing_goal() -> None:
    generated = (
        "## Purpose\n\nOutcome.\n\n## When To Use\n\nNow.\n\n"
        "## Acceptance Criteria\n\n- [ ] done\n"
    )
    errors = goal_acceptance_errors(generated, "vc-goalprobe")
    assert "missingGoal" in errors


def test_scaffold_places_one_goal_after_purpose_with_name_and_endpoint(
    tmp_path: Path,
) -> None:
    repo = _mini_fixture_repo(tmp_path)
    generated = _scaffold_skill(
        repo, "vc-goalprobe", tmp_path / "isolated-home"
    ).read_text(encoding="utf-8")
    errors = goal_acceptance_errors(generated, "vc-goalprobe")
    assert not errors, "\n".join(errors)
