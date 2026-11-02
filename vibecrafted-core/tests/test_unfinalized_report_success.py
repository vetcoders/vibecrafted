"""An unfinalized report must not become a success verdict."""

from __future__ import annotations

from pathlib import Path

from vibecrafted_core import control_plane, wrappers
from vibecrafted_core.cli import _run_succeeded


def _write_report(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _unfinalized(path: Path) -> str:
    _write_report(
        path,
        """---
run_id: work-261029-114509-02831
agent: agy
skill: workflow
status: pending-unset
claim_status: pending
finalized: false
claim: ""
---
""",
    )
    return str(path)


def _attested(path: Path) -> str:
    _write_report(
        path,
        """---
run_id: work-attested
agent: agy
skill: workflow
status: completed
claim_status: completed
finalized: true
claim: shipped the bounded cut
---
The worker finished the cut.
""",
    )
    return str(path)


def _legacy(path: Path) -> str:
    _write_report(
        path,
        """---
run_id: work-legacy
agent: agy
skill: workflow
status: completed
---
Predates the finalized key.
""",
    )
    return str(path)


def _run(report: str, *, state: str = "report_validated") -> dict[str, object]:
    return {
        "run_id": "work-261029-114509-02831",
        "state": state,
        "artifact_ok": True,
        "artifact_errors": [],
        "exit_code": 0,
        "latest_report": report,
        "liveness": "terminal",
    }


def test_unfinalized_report_is_not_success(tmp_path: Path) -> None:
    report = _unfinalized(tmp_path / "report.md")
    run = _run(report)

    assert control_plane._report_denies_success(run) == "report finalized is false"
    assert control_plane._has_success_evidence(run) is False
    assert control_plane._operator_state(run) == "blocked"
    assert _run_succeeded(run) is False

    projected = control_plane._artifact_projection(dict(run))
    assert projected.get("exit_code") in (None, 0)
    assert projected["operator_state"] == "blocked"
    assert "exit_code" not in projected or projected["exit_code"] == 0
    # Projection must not invent a success exit when the record had none.
    fresh = dict(run)
    fresh.pop("exit_code")
    projected_fresh = control_plane._artifact_projection(fresh)
    assert projected_fresh.get("exit_code") is None
    assert projected_fresh["operator_state"] == "blocked"

    code = wrappers._completion_exit_code(
        {"completed": True, "worker_alive": False, "run": fresh}
    )
    assert code == 3


def test_attested_report_is_success(tmp_path: Path) -> None:
    report = _attested(tmp_path / "report.md")
    run = _run(report)
    run.pop("exit_code")

    assert control_plane._report_denies_success(run) == ""
    assert control_plane._has_success_evidence(run) is True
    assert control_plane._operator_state(run) == "completed"
    assert _run_succeeded(run) is True
    projected = control_plane._artifact_projection(dict(run))
    assert projected["exit_code"] == 0
    assert projected["operator_state"] == "completed"
    assert (
        wrappers._completion_exit_code(
            {"completed": True, "worker_alive": False, "run": projected}
        )
        == 0
    )


def test_legacy_report_without_finalized_key_keeps_process_evidence(
    tmp_path: Path,
) -> None:
    report = _legacy(tmp_path / "report.md")
    run = _run(report, state="completed")

    assert control_plane._report_denies_success(run) == ""
    assert control_plane._has_success_evidence(run) is True
    assert control_plane._operator_state(run) == "completed"


def test_finalized_report_with_empty_claim_is_not_success(tmp_path: Path) -> None:
    path = tmp_path / "report.md"
    _write_report(
        path,
        """---
run_id: work-empty-claim
agent: agy
skill: workflow
status: completed
claim_status: completed
finalized: true
claim: ""
---
""",
    )
    run = _run(str(path), state="completed")

    assert control_plane._report_denies_success(run) == "report claim is empty"
    assert _run_succeeded(run) is False
    assert control_plane._has_success_evidence(run) is False


def test_missing_report_file_leaves_process_evidence(tmp_path: Path) -> None:
    """A path that is not on disk is absence, so process evidence still stands."""
    run = _run(str(tmp_path / "absent-report.md"), state="completed")

    assert control_plane._report_denies_success(run) == ""
    assert control_plane._has_success_evidence(run) is True
    assert control_plane._operator_state(run) == "completed"


def test_launcher_template_is_not_success(tmp_path: Path) -> None:
    """The canonical launcher shell is present and unfinalized.

    ``validate_frontmatter_fields`` marks ``launcher_template: true`` with
    ``report_missing``. That sentinel is not file absence, so an exit-0
    completed record must not project as operator success.
    """
    from vibecrafted_core.report_contract import (
        materialize_launcher_report_template,
        validate_report_file,
    )

    report = tmp_path / "launcher-shell.md"
    assert materialize_launcher_report_template(
        report,
        run_id="work-261029-114509-02831",
        agent="agy",
        skill="workflow",
    )
    verdict = validate_report_file(report, require_frontmatter=False)
    assert verdict.has_frontmatter is True
    assert "report_missing" in verdict.errors
    assert verdict.finalized is False

    run = _run(str(report), state="completed")
    assert control_plane._report_denies_success(run) == "report finalized is false"
    assert control_plane._has_success_evidence(run) is False
    assert _run_succeeded(run) is False

    projected = control_plane._artifact_projection(dict(run))
    assert projected["operator_state"] == "blocked"
    assert projected["state"] == "completed"
    assert (
        wrappers._completion_exit_code(
            {"completed": True, "worker_alive": False, "run": projected}
        )
        == 3
    )
