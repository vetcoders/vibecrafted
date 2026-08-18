"""Mandatory report frontmatter + claim triangulation."""

from __future__ import annotations

from pathlib import Path

from vibecrafted_core.artifacts import validate_artifacts
from vibecrafted_core.report_contract import (
    ACCEPT_DOU_VERB,
    DEFERRED_CUT_MARK,
    SHIP_LIFECYCLE_LEAD_KEYS,
    ensure_frontmatter_on_text,
    is_ship_or_lifecycle_skill,
    materialize_launcher_report_template,
    parse_dou_index_value,
    parse_report_text,
    render_minimal_frontmatter,
    stamp_launcher_report_identity,
    validate_frontmatter_fields,
    validate_report_file,
)
from vibecrafted_core.run_triage import classify_run


def test_parse_and_validate_minimal_frontmatter(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text(
        render_minimal_frontmatter(
            run_id="run-1",
            agent="codex",
            skill="scaffold",
            status="completed",
            extra={"project": "vibecrafted"},
        )
        + "# Body\n\nok\n",
        encoding="utf-8",
    )
    fm = validate_report_file(report)
    assert fm.ok
    assert fm.claim_status == "completed"
    assert fm.fields["agent"] == "codex"
    assert fm.fields["finalized"] == "false"
    assert fm.finalized is False


def test_missing_frontmatter_fails_artifact_validation(tmp_path: Path) -> None:
    report = tmp_path / "bare.md"
    report.write_text("# no frontmatter\n\nbody\n", encoding="utf-8")
    meta = tmp_path / "meta.json"
    meta.write_text(
        '{{"report": {}, "transcript": {}}}'.format(
            repr(str(report)), repr(str(tmp_path / "t.log"))
        ),
        encoding="utf-8",
    )
    (tmp_path / "t.log").write_text("x\n", encoding="utf-8")
    result = validate_artifacts(meta_path=meta, report_path=report)
    assert not result.ok
    assert any(e.startswith("report_frontmatter_") for e in result.errors)
    assert result.report_valid is False


def test_ensure_frontmatter_prepends_block() -> None:
    text = ensure_frontmatter_on_text(
        "# Hello\n",
        run_id="r1",
        agent="claude",
        skill="workflow",
        status="completed",
    )
    fields, body, has_fm = parse_report_text(text)
    assert has_fm
    assert fields["run_id"] == "r1"
    assert fields["finalized"] == "false"
    assert "Hello" in body


def test_existing_positive_attestation_is_preserved() -> None:
    text = ensure_frontmatter_on_text(
        "---\nrun_id: r1\nagent: codex\nskill: workflow\nstatus: completed\n"
        "finalized: true\nclaim: lifecycle settled\n---\nbody\n",
    )
    fields, _, _ = parse_report_text(text)
    assert fields["finalized"] == "true"
    assert fields["claim"] == "lifecycle settled"


def test_launcher_claim_digest_overrides_worker_copy(tmp_path: Path) -> None:
    digest = "9e0d59e1dc48bc42"
    report = tmp_path / "report.md"

    assert materialize_launcher_report_template(
        report,
        run_id="bound-run",
        agent="codex",
        skill="polarize",
        claim_digest=digest,
    )
    assert f"claim_digest: {digest}" in report.read_text(encoding="utf-8")

    report.write_text(
        "---\nrun_id: copied\nsession_id: copied\nagent: codex\n"
        "skill: polarize\nstatus: completed\nfinalized: true\n"
        "claim: cut completed\nclaim_digest: deadbeefdeadbeef\n---\nbody\n",
        encoding="utf-8",
    )
    assert stamp_launcher_report_identity(
        report,
        run_id="bound-run",
        session_id="provider-session",
        agent="codex",
        skill="polarize",
        status="completed",
        claim_digest=digest,
    )
    fields, _, _ = parse_report_text(report.read_text(encoding="utf-8"))
    assert fields["run_id"] == "bound-run"
    assert fields["session_id"] == "provider-session"
    assert fields["claim_digest"] == digest


def test_classify_exit_0_claim_failed_is_attention() -> None:
    verdict = classify_run(
        0,
        "completed",
        True,
        500,
        1000,
        report_claim_status="failed",
        report_frontmatter_ok=True,
    )
    assert verdict.verdict == "needs_attention"
    assert "claim_failed" in verdict.reason


def test_classify_exit_0_missing_frontmatter_is_attention() -> None:
    verdict = classify_run(
        0,
        "completed",
        True,
        500,
        1000,
        report_claim_status="completed",
        report_frontmatter_ok=False,
    )
    assert verdict.verdict == "needs_attention"
    assert "frontmatter" in verdict.reason


def test_classify_exit_0_completed_claim_finalizes() -> None:
    verdict = classify_run(
        0,
        "completed",
        True,
        500,
        1000,
        report_claim_status="completed",
        report_frontmatter_ok=True,
    )
    assert verdict.verdict == "finalized"


def test_accept_dou_verb_is_the_only_defer_surface() -> None:
    assert ACCEPT_DOU_VERB == "accept-dou"
    assert DEFERRED_CUT_MARK == "[ ]"
    assert SHIP_LIFECYCLE_LEAD_KEYS[:3] == ("dou_index", "cuts_done", "cuts_total")
    assert is_ship_or_lifecycle_skill("vc-ship")
    assert is_ship_or_lifecycle_skill("lifecycle")
    assert not is_ship_or_lifecycle_skill("scaffold")
    assert parse_dou_index_value("3/9") == (None, 3, 9)
    assert parse_dou_index_value("0") == (0, None, None)


def test_ship_report_without_dou_index_fails(tmp_path: Path) -> None:
    report = tmp_path / "ship.md"
    report.write_text(
        render_minimal_frontmatter(
            run_id="life-ship-1",
            agent="grok",
            skill="ship",
            status="completed",
        )
        + "# 11/11 stages\n\nok\n",
        encoding="utf-8",
    )
    fm = validate_report_file(report)
    assert not fm.ok
    assert "report_frontmatter_missing_key:dou_index" in fm.errors
    assert "report_frontmatter_missing_key:cuts_done" in fm.errors
    assert "report_frontmatter_missing_key:cuts_total" in fm.errors


def test_ship_report_leads_with_dou_index_and_cut_table() -> None:
    text = render_minimal_frontmatter(
        run_id="life-ship-1",
        agent="grok",
        skill="vc-ship",
        status="completed",
        extra={
            "dou_index": "6",
            "cuts_done": "3",
            "cuts_total": "9",
        },
    )
    # Delivery fields sit before status so "11/11 stages" cannot bury 3/9.
    lead = text.split("status:", 1)[0]
    assert "dou_index: 6" in lead
    assert "cuts_done: 3" in lead
    assert "cuts_total: 9" in lead

    fm = validate_frontmatter_fields(
        {
            "run_id": "life-ship-1",
            "agent": "grok",
            "skill": "vc-ship",
            "status": "completed",
            "dou_index": "6",
            "cuts_done": "3",
            "cuts_total": "9",
        },
        body="# 11/11 stages\n\nthree cuts landed; the rest stayed "
        + DEFERRED_CUT_MARK
        + "\n",
        has_fm=True,
    )
    assert fm.ok
    assert fm.dou_index == "6"
    assert "report_frontmatter_cuts_incomplete" in fm.warnings
    assert "report_frontmatter_stages_hide_cuts" in fm.warnings
    assert "report_frontmatter_defer_without_accept_dou" in fm.warnings


def test_ship_dou_index_ratio_fills_cut_table() -> None:
    fm = validate_frontmatter_fields(
        {
            "run_id": "life-ship-1",
            "agent": "grok",
            "skill": "ship",
            "status": "partial",
            "dou_index": "3/9",
        },
        body="in flight\n",
        has_fm=True,
    )
    assert fm.ok
    assert fm.dou_index == "3/9"
    assert "report_frontmatter_missing_key:cuts_done" not in fm.errors


def test_defer_named_via_accept_dou_is_not_a_silent_defer() -> None:
    fm = validate_frontmatter_fields(
        {
            "run_id": "life-ship-1",
            "agent": "grok",
            "skill": "ship",
            "status": "completed",
            "dou_index": "3/9",
        },
        body=f"remaining cuts stay {DEFERRED_CUT_MARK} until {ACCEPT_DOU_VERB}\n",
        has_fm=True,
    )
    assert fm.ok
    assert "report_frontmatter_defer_without_accept_dou" not in fm.warnings


def test_scaffold_report_does_not_require_dou_index() -> None:
    fm = validate_frontmatter_fields(
        {
            "run_id": "run-1",
            "agent": "codex",
            "skill": "scaffold",
            "status": "completed",
        },
        body="no cuts here\n",
        has_fm=True,
    )
    assert fm.ok
    assert not any(
        e.startswith("report_frontmatter_missing_key:dou") for e in fm.errors
    )
