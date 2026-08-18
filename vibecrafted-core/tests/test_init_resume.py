"""Resume must ride along with init — silently when there is nothing to resume."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from vibecrafted_core import init_resume, workflow


def _row(**overrides: Any) -> dict[str, Any]:
    """One enriched ``n`` settlement row with sane, resumable defaults."""
    row = {
        "run_id": "work-260818-000000-11111",
        "agent": "claude",
        "skill": "workflow",
        "reason": "worker died",
        "state": "timed_out",
        "root": "/tmp/repo",
        "report_path": "/tmp/report.md",
        "revalidatable": True,
        "checkout_exists": True,
        "native_resume_candidate": False,
        "trust_receipt_present": False,
    }
    row.update(overrides)
    return row


def _stub_listing(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> None:
    """Make ``list_settlements`` return exactly ``rows`` for bucket ``n``."""
    import vibecrafted_core.settlements_query as sq

    def fake_list(**kwargs: Any) -> dict[str, Any]:
        assert kwargs.get("bucket") == "n"
        return {"runs": rows}

    monkeypatch.setattr(sq, "list_settlements", fake_list)


def test_clean_checkout_adds_nothing_to_init(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Silence is the contract: a block that always prints becomes noise."""
    _stub_listing(monkeypatch, [])
    assert init_resume.init_resume_block(tmp_path) == ""


def test_runs_from_another_checkout_are_never_claimed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A run recorded against a different root is not this repository's business."""
    _stub_listing(monkeypatch, [_row(root=str(tmp_path / "elsewhere"))])
    payload = init_resume.resume_payload(tmp_path)
    assert payload["matched"] == 0
    assert init_resume.render_init_resume_block(payload) == ""


def test_unreadable_ledger_reports_unknown_instead_of_clean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Init survives a broken ledger, but never implies the checkout is clean."""
    import vibecrafted_core.settlements_query as sq

    def explode(**_kwargs: Any) -> dict[str, Any]:
        raise OSError("ledger is gone")

    monkeypatch.setattr(sq, "list_settlements", explode)
    payload = init_resume.resume_payload(tmp_path)
    assert payload["available"] is False
    block = init_resume.render_init_resume_block(payload)
    assert "UNKNOWN" in block
    assert "ledger is gone" in block


def test_operator_resumable_run_carries_its_exact_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The payload names the run and the one line that continues it."""
    run_id = "work-260818-195620-61748"
    _stub_listing(monkeypatch, [_row(root=str(tmp_path), run_id=run_id)])
    block = init_resume.init_resume_block(tmp_path)
    assert run_id in block
    assert f"vibecrafted claude resume --run-id {run_id}" in block


def test_guardian_owned_run_is_reported_but_never_hand_resumed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Guardian holds a one-attempt budget; a hand resume would burn it."""
    run_id = "work-260818-195620-61748"
    _stub_listing(
        monkeypatch,
        [
            _row(
                root=str(tmp_path),
                run_id=run_id,
                native_resume_candidate=True,
                trust_receipt_present=True,
            )
        ],
    )
    payload = init_resume.resume_payload(tmp_path)
    assert payload["counts"][init_resume.GUARDIAN_AUTO] == 1
    block = init_resume.render_init_resume_block(payload)
    assert run_id in block
    assert "do not resume by hand" in block
    assert f"resume --run-id {run_id}" not in block


def test_run_without_a_recorded_agent_gets_no_guessed_command() -> None:
    """A missing agent yields no command rather than an invented one."""
    assert init_resume.resume_command(_row(agent="")) == ""


def test_every_pipeline_prompt_carries_the_resume_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Wiring proof: the block reaches the worker contract of every launch."""
    run_id = "work-260818-195620-61748"
    _stub_listing(monkeypatch, [_row(root=str(tmp_path), run_id=run_id)])
    spec = workflow.WorkflowLaunchSpec(
        agent="claude",
        mode="",
        skill="workflow",
        prompt="do the thing",
        file="",
        runtime="headless",
        root=str(tmp_path),
    )
    prompt = workflow._runtime_prompt(spec)
    assert "Step 0 — orient before you touch (the vc-init pass)." in prompt
    assert "Resume payload (computed by this init pass" in prompt
    assert run_id in prompt
    assert prompt.index("Step 0") < prompt.index("Resume payload")
    assert prompt.index("Resume payload") < prompt.index("Operator prompt:")


def test_clean_pipeline_prompt_stays_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No unfinished work means no extra prose in the worker contract."""
    _stub_listing(monkeypatch, [])
    spec = workflow.WorkflowLaunchSpec(
        agent="claude",
        mode="",
        skill="workflow",
        prompt="do the thing",
        file="",
        runtime="headless",
        root=str(tmp_path),
    )
    assert "Resume payload" not in workflow._runtime_prompt(spec)


def test_newest_runs_are_shown_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A long history must not bury today's unfinished work under July's."""
    _stub_listing(
        monkeypatch,
        [
            _row(
                root=str(tmp_path),
                run_id="audi-260721-000000-00001",
                settled_at="2026-07-21T00:00:00Z",
            ),
            _row(
                root=str(tmp_path),
                run_id="work-260818-000000-00002",
                settled_at="2026-08-18T00:00:00Z",
            ),
        ],
    )
    payload = init_resume.resume_payload(tmp_path)
    order = [
        entry["run_id"] for entry in payload["classes"][init_resume.OPERATOR_RESUME]
    ]
    assert order == ["work-260818-000000-00002", "audi-260721-000000-00001"]


def test_unstamped_runs_never_outrank_stamped_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing settlement stamp is not evidence of recency."""
    _stub_listing(
        monkeypatch,
        [
            _row(root=str(tmp_path), run_id="zzzz-no-stamp", settled_at=""),
            _row(
                root=str(tmp_path),
                run_id="aaaa-stamped",
                settled_at="2026-07-01T00:00:00Z",
            ),
        ],
    )
    payload = init_resume.resume_payload(tmp_path)
    order = [
        entry["run_id"] for entry in payload["classes"][init_resume.OPERATOR_RESUME]
    ]
    assert order == ["aaaa-stamped", "zzzz-no-stamp"]


def test_long_history_is_truncated_with_an_honest_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Truncation is stated, never silent — a hidden cap reads as 'that is all'."""
    _stub_listing(
        monkeypatch,
        [
            _row(
                root=str(tmp_path),
                run_id=f"work-2608{index:02d}-000000-00000",
                settled_at=f"2026-08-{index:02d}T00:00:00Z",
            )
            for index in range(1, 10)
        ],
    )
    block = init_resume.init_resume_block(tmp_path, limit=3)
    assert "This checkout has 9 run(s) settled `n`" in block
    assert "… and 6 older run(s) in this class, newest shown first." in block
