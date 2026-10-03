"""Explicit claim behavior for legacy launcher doubles, never a global bypass."""

from __future__ import annotations

import subprocess

import pytest
from vibecrafted_core.dispatch.claims import claim_git_state, submit_claim
from vibecrafted_core.dispatch.receipts import ReceiptContractError
from vibecrafted_core.dispatch.supervisor import DispatchSupervisor


@pytest.fixture
def worker_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    """Finish opted-in worker doubles with the real canonical claim writer.

    The doubles still launch and await real processes. This fixture adds the
    newly required worker action after their report is registered, without
    manufacturing a receipt or bypassing Git/report validation. Claim-contract
    tests do not opt in, so a missing claim remains observable there.
    """
    execute = DispatchSupervisor._execute_cell

    def execute_and_claim(self, cut, prompt, kind):
        outcome, failure = execute(self, cut, prompt, kind)
        if outcome is not None and failure is None and not outcome.timed_out:
            try:
                head, _dirty = claim_git_state(self._cut_root(cut))
                submit_claim(
                    {
                        "run_id": self.run_id,
                        "cut_id": cut.id,
                        "commit_sha": head,
                        "report_path": outcome.report_path,
                        "measurements": ["launcher double completed its report"],
                    },
                    store=self._receipt_store,
                )
            except ReceiptContractError:
                # Negative substrate doubles may intentionally leave a dirty
                # checkpoint. Rejection stays real and cannot settle [x].
                pass
        return outcome, failure

    monkeypatch.setattr(DispatchSupervisor, "_execute_cell", execute_and_claim)


@pytest.fixture
def verified_history():
    """Seed resume history through actual claim, matcher, and settlement paths."""

    def settle(supervisor, cut, report, *, provider_run_id="historical-worker"):
        root = supervisor._cut_root(cut)
        head, _dirty = claim_git_state(root)
        branch = subprocess.run(
            ["git", "-C", root, "branch", "--show-current"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        report.write_text("Historical worker measured its declared contract.\n")
        supervisor._receipt_store.update(
            cut.id,
            "reported",
            report_path=str(report),
            worktree_path=root,
            branch=branch,
            provider_run_id=provider_run_id,
            attempt="initial",
        )
        submit_claim(
            {
                "run_id": supervisor.run_id,
                "cut_id": cut.id,
                "commit_sha": head,
                "report_path": str(report),
                "measurements": ["historical fixture completed its report"],
            },
            store=supervisor._receipt_store,
        )
        verdict = supervisor._verify(cut)
        assert verdict.ok, verdict.failures
        assert supervisor._record_cut_verdict(cut, verdict).ok

    return settle
