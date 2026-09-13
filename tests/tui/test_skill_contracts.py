from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


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
    # b9ef0329 rewrote compile-embargo.md from a Founder-authorized
    # "Phase-Aware Recovery Contract" (policy-aware hook policy, worker/
    # structural-admission/verified-delivery table) into "W2 Integration
    # Responsibility": a phase contract where workers checkpoint, the W2
    # integrator records W2_STRUCTURALLY_CLOSED against the exact SHA and
    # restores every gate. The same five obligations are pinned in their
    # current wording, plus the two that replaced "policy-aware": hook adapters
    # never widen a bypass, and a failed gate is repaired, never weakened.
    # Markers are compared whitespace-normalized so re-wrapping is not drift.
    variants = [
        (
            skills / "vc-scaffold",
            (
                (
                    "A local checkpoint may use `git commit --no-verify` when hooks "
                    "would run gates."
                ),
                "It does not authorize a push, publication or release.",
                (
                    "restores and runs the full applicable gates, including security "
                    "and secret checks skipped by checkpoint hooks"
                ),
                "A checkpoint preserves work; it does not certify correctness or security.",
                (
                    "the integrator must complete every required gate and real product "
                    "acceptance on the exact delivered generation."
                ),
                "records `W2_STRUCTURALLY_CLOSED` against the exact assembled SHA",
                "A malformed marker is an error, never permission to widen a bypass.",
                (
                    "A failed gate after closure calls for implementation repair, not "
                    "weaker assertions"
                ),
            ),
        ),
        (
            skills / "pl/vc-scaffold",
            (
                (
                    "Checkpoint może użyć `git commit --no-verify`, gdy hooki "
                    "uruchamiałyby bramki."
                ),
                "nie uprawnia do pushu, publikacji ani wydania.",
                (
                    "przywraca i uruchamia pełne właściwe bramki, w tym kontrolę "
                    "bezpieczeństwa i sekretów pominiętą przez checkpointy"
                ),
                "Checkpoint zachowuje pracę, nie potwierdza jakości ani bezpieczeństwa",
                (
                    "Przed wydaniem integrator rozlicza wszystkie wymagane bramki i "
                    "rzeczywiste scenariusze produktu dla dokładnej dostarczanej generacji."
                ),
                "zapisuje `W2_STRUCTURALLY_CLOSED` dla dokładnego złożonego SHA",
                "Błędny marker nie rozszerza uprawnień.",
                (
                    "Nieudana bramka po closure wymaga naprawy implementacji, nie "
                    "osłabienia asercji"
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
        normalized_embargo = " ".join(embargo.split())
        assert "policy-aware" not in embargo
        for marker in embargo_contract:
            assert marker in normalized_embargo, (
                f"{scaffold.relative_to(REPO_ROOT)} missing embargo contract: {marker!r}"
            )
