"""Tests for the runtime caller of ``vc-frame triage-run``.

The transfer primitive itself lives in vc-frame and is tested there. What is at
stake here is the caller's judgement: which runs may be transferred at all, which
drawer the conjunction of a run's signals earns it, and — above all — that nothing
in this path can damage a run that has already finished.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import vibecrafted_core.run_mutation as run_mutation_module
import vibecrafted_core.run_triage as run_triage_module
from vibecrafted_core.report_contract import render_minimal_frontmatter
from vibecrafted_core.run_triage import (
    BUCKET_FAILED,
    BUCKET_FINALIZED,
    BUCKET_NEEDS_ATTENTION,
    MINIMAL_TRANSCRIPT_BYTES,
    OUTCOME_ERROR,
    OUTCOME_FAILED,
    OUTCOME_FINALIZED,
    OUTCOME_NEEDS_ATTENTION,
    OUTCOME_SKIPPED,
    VERDICT_FAILED,
    VERDICT_FINALIZED,
    VERDICT_INFRA_FAILURE,
    VERDICT_NEEDS_ATTENTION,
    KernelAxes,
    TriageOutcome,
    TriagePlan,
    bucket_for_exit_code,
    classify_provider_error,
    classify_run,
    load_vc_frame_transfer_proof,
    plan_triage,
    read_kernel_axes,
    read_run_signals,
    reconcile_untriaged_runs,
    triage_finished_run,
    triage_outcome_is_complete,
)
from vibecrafted_core.runtime_transcript import write_runtime_transcript_manifest
from vibecrafted_core.settlement import (
    Settlement,
    SettlementVerdict,
    persist_settlement_to_meta,
)

MODERN_HELP = (
    "Usage: vc-frame triage-run [OPTIONS]\n"
    "  --bucket <BUCKET>\n"
    "  --settlement-revision <SETTLEMENT_REVISION>\n"
    "  --transfer-lock-fd <TRANSFER_LOCK_FD>\n"
)


class FakeProc:
    def __init__(self, returncode: int = 0, stderr: str = "", stdout: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


class Runner:
    """Records invocations; answers the `--help` probe as a modern binary would."""

    def __init__(
        self,
        result: Any = None,
        supports: bool = True,
        supports_bucket: bool = True,
        supports_settlement_revision: bool = True,
        supports_inherited_lock: bool = True,
    ) -> None:
        self.calls: list[list[str]] = []
        self.result = result if result is not None else FakeProc(0)
        self.supports = supports
        self.supports_bucket = supports_bucket
        self.supports_settlement_revision = supports_settlement_revision
        self.supports_inherited_lock = supports_inherited_lock

    def __call__(self, argv: Sequence[str]) -> Any:
        argv = list(argv)
        self.calls.append(argv)
        if argv[1:] == ["triage-run", "--help"]:
            if not self.supports:
                return FakeProc(2)
            help_lines = ["Usage: vc-frame triage-run [OPTIONS]"]
            if self.supports_bucket:
                help_lines.append("  --bucket <BUCKET>")
            if self.supports_settlement_revision:
                help_lines.append("  --settlement-revision <SETTLEMENT_REVISION>")
            if self.supports_inherited_lock:
                help_lines.append("  --transfer-lock-fd <TRANSFER_LOCK_FD>")
            return FakeProc(0, stdout="\n".join(help_lines) + "\n")
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    @property
    def transfer_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[1:2] == ["triage-run"] and "--help" not in c]

    def bucket_flag(self) -> str | None:
        call = self.transfer_calls[0]
        return call[call.index("--bucket") + 1] if "--bucket" in call else None


LIVE_ENV = {
    "VC_FRAME_SESSION_NAME": "vibecrafted-dev",
    "VC_FRAME_PANE_ID": "terminal_3",
    "PATH": "/usr/bin",
    "VIBECRAFTED_VC_FRAME_BIN": "",
}


def make_env(**overrides: str) -> dict[str, str]:
    env = dict(LIVE_ENV)
    env.update(overrides)
    return env


def fake_bin(tmp_path: Path) -> str:
    """An on-disk vc-frame stand-in — `_resolve_binary` requires the path to exist."""
    binary = tmp_path / "vc-frame"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    return str(binary)


def live_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    return make_env(VIBECRAFTED_VC_FRAME_BIN=fake_bin(tmp_path), **overrides)


def _tiny_transcript(tmp_path: Path) -> Path:
    """A transcript holding nothing but the launcher banner — the W0-A shape."""
    path = tmp_path / "banner-only.transcript.log"
    path.write_text("x" * (MINIMAL_TRANSCRIPT_BYTES - 1), encoding="utf-8")
    return path


def write_meta(tmp_path: Path, **overrides: Any) -> Path:
    """A clean finalized run: exit 0, `completed`, report on disk, real transcript.

    Every deviation a test wants is an override, so each test names exactly the
    one signal it is bending.
    """
    report = tmp_path / "agent.md"
    report.write_text(
        render_minimal_frontmatter(
            run_id="r1", agent="codex", skill="scaffold", status="completed"
        )
        + "# report\n",
        encoding="utf-8",
    )
    transcript = tmp_path / "agent.transcript.log"
    transcript.write_text("x" * (MINIMAL_TRANSCRIPT_BYTES * 4), encoding="utf-8")

    payload: dict[str, Any] = {
        "status": "completed",
        "run_id": "run-0007",
        "exit_code": 0,
        "root": "/repo",
        "launcher": "/tmp/launch-run-0007.sh",
        "liveness": "terminal",
        "report": str(report),
        "transcript": str(transcript),
    }
    payload.update(overrides)
    meta = tmp_path / "agent.meta.json"
    meta.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return meta


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _control_plane_meta(control_plane: Path) -> Path:
    report = control_plane / "artifacts" / "run-proof.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        render_minimal_frontmatter(
            run_id="run-proof",
            agent="codex",
            skill="implement",
            status="completed",
        )
        + "# proof\n",
        encoding="utf-8",
    )
    transcript = control_plane / "artifacts" / "run-proof.log"
    transcript.write_text("x" * (MINIMAL_TRANSCRIPT_BYTES * 4), encoding="utf-8")
    meta = control_plane / "runtime_runs" / "run-proof" / "meta.json"
    _write_json(
        meta,
        {
            "status": "completed",
            "run_id": "run-proof",
            "exit_code": 0,
            "origin_session": "vibecrafted",
            "origin_tab": "run-proof",
            "origin_pane_id": "terminal_3",
            "root": "/repo",
            "command": ["codex", "exec", "ship"],
            "report": str(report),
            "transcript": str(transcript),
        },
    )
    return meta


def _materialize_v4_transfer(
    control_plane: Path,
    payload: dict[str, Any],
    *,
    bucket: str = "Finalized",
) -> None:
    run_id = payload["run_id"]
    bucket_session = {
        "Finalized": BUCKET_FINALIZED,
        "Failed": BUCKET_FAILED,
        "NeedsAttention": BUCKET_NEEDS_ATTENTION,
    }[bucket]
    origin_instance = "1" * 32
    viewer_instance = "2" * 32
    viewer_token = "a" * 32
    scrollback = b"durable terminal capture\n"
    digest = hashlib.sha256(scrollback).hexdigest()
    command, cwd, pane_id, runtime_transcript = (
        run_triage_module._normalized_transfer_request(payload)
    )
    origin_session = payload["origin_session"]
    origin_tab = payload["origin_tab"]
    capture = {
        "capture_source": "terminal_scrollback",
        "source_identity": (
            f"session={origin_session};tab_id=7;"
            f"tab_instance_id={origin_instance};pane_id=terminal_3"
        ),
        "bytes": len(scrollback),
        "sha256": digest,
        "origin_tab_identity": {
            "session": origin_session,
            "name": origin_tab,
            "id": 7,
            "session_incarnation": "origin-incarnation",
            "tab_instance_id": origin_instance,
        },
    }
    receipt = {
        "version": 4,
        "run": run_id,
        "bucket": bucket,
        "exit_code": payload["exit_code"],
        "origin_session": origin_session,
        "origin_tab": origin_tab,
        "command": list(command),
        "cwd": cwd,
        "pane_id": pane_id,
        "runtime_transcript": runtime_transcript,
        "settlement_revision": payload.get("settlement_revision", 0),
        "superseded_viewers": [],
        "capture": capture,
        "capture_committed": True,
        "metadata_committed": True,
        "viewer_confirmed": True,
        "viewer_tab_identity": {
            "session": bucket_session,
            "name": f"{run_id} [vc:{viewer_token}]",
            "id": 4,
            "session_incarnation": "viewer-incarnation",
            "tab_instance_id": viewer_instance,
        },
        "viewer_creation_pending": False,
        "viewer_token": viewer_token,
        "origin_tab_state": "closed",
        "fault": None,
        "updated_at": 1_700_000_000,
    }
    finished = control_plane / "finished_runs" / run_id
    finished.mkdir(parents=True, exist_ok=True)
    (finished / "scrollback.txt").write_bytes(scrollback)
    _write_json(finished / "transfer.json", receipt)
    _write_json(
        finished / "capture.manifest.json",
        {
            "version": 1,
            "run_id": run_id,
            "session": origin_session,
            "origin_tab": origin_tab,
            "pane_id": pane_id,
            "runtime_transcript": runtime_transcript,
            "staging_file": ".terminal-scrollback.staging",
            "evidence": capture,
        },
    )
    _write_json(
        finished / "meta.json",
        {
            "run": run_id,
            "exit_code": payload["exit_code"],
            "bucket": bucket,
            "origin_session": origin_session,
            "origin_tab": origin_tab,
            "command": list(command),
            "cwd": cwd,
            "captured_at": 1_700_000_000,
            "capture_source": "terminal_scrollback",
            "capture_source_identity": capture["source_identity"],
            "capture_bytes": len(scrollback),
            "capture_sha256": digest,
        },
    )


# --------------------------------------------------------------------------
# The classifier. Single signals lie, so the verdict is a conjunction — and the
# whole point of this matrix is that only two rows are allowed to be confident.
# --------------------------------------------------------------------------

BIG = MINIMAL_TRANSCRIPT_BYTES * 10
TINY = MINIMAL_TRANSCRIPT_BYTES - 1


def verdict(
    exit_code: Any = 0,
    state: Any = "completed",
    report_exists: bool | None = True,
    report_bytes: int | None = 512,
    transcript_bytes: int | None = BIG,
) -> str:
    return classify_run(
        exit_code, state, report_exists, report_bytes, transcript_bytes
    ).verdict


# --- The two confident verdicts ------------------------------------------


@pytest.mark.parametrize(
    "state", ["completed", "report_validated", "closed", "converged"]
)
def test_finalized_needs_all_three_signals(state: str) -> None:
    """Exit 0 AND a delivery state AND a report actually on disk."""
    assert verdict(exit_code=0, state=state) == VERDICT_FINALIZED


@pytest.mark.parametrize("state", ["failed", "stopped", "report_missing"])
@pytest.mark.parametrize("code", [1, 2, 137, 143])
def test_failed_is_a_run_that_died_before_working(state: str, code: int) -> None:
    """Non-zero exit AND death state AND no report AND nothing in the transcript."""
    assert (
        verdict(
            exit_code=code,
            state=state,
            report_exists=False,
            report_bytes=0,
            transcript_bytes=TINY,
        )
        == VERDICT_FAILED
    )


def test_w0a_specimen_is_failed() -> None:
    """Today's live specimen: worker killed by the session limit.

    Exit non-zero, control-plane state `report_missing`, no report, and a
    transcript holding nothing but the launcher banner. This is the shape the
    third bucket exists for — it must not land in "Needs attention" and drown
    the contradictions that actually need a human.
    """
    signals = classify_run(
        exit_code=1,
        run_state="report_missing",
        report_exists=False,
        report_bytes=0,
        transcript_bytes=180,
    )
    assert signals.verdict == VERDICT_FAILED
    assert signals.bucket == BUCKET_FAILED
    assert signals.bucket_flag == "failed"
    assert "180b" in signals.reason


# --- C3: provider overload is infra_failure, not worker failed -----------

_ORDINARY_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "worker.py", line 12, in <module>\n'
    "    raise RuntimeError('tool exploded')\n"
    "RuntimeError: tool exploded\n"
)


@pytest.mark.parametrize(
    "text,token",
    [
        ("API 529 Overloaded", "529"),
        ("HTTP 429 Too Many Requests", "429"),
        ("provider overloaded, retry later", "overloaded"),
        ("codex usage limit reached", "usage_limit"),
        ("hit a usage-limit until 2026-08-20", "usage_limit"),
        ("openai rate limit exceeded", "rate_limit"),
    ],
)
def test_classify_provider_error_matches_overload_markers(
    text: str, token: str
) -> None:
    reason = classify_provider_error(text)
    assert reason is not None
    assert reason.startswith("provider_error:")
    assert token in reason


def test_classify_provider_error_ignores_ordinary_traceback() -> None:
    assert classify_provider_error(_ORDINARY_TRACEBACK) is None
    assert classify_provider_error("File worker.py, line 429, in run") is None


def test_synthetic_529_transcript_is_infra_failure_not_worker_failed() -> None:
    """Postmortem 2026-08-19 §C3 specimen: API 529 must not land in failed."""
    classification = classify_run(
        exit_code=1,
        run_state="failed",
        report_exists=False,
        report_bytes=0,
        transcript_bytes=TINY,
        transcript_text="API 529 Overloaded\n",
    )
    assert classification.verdict == VERDICT_INFRA_FAILURE
    assert classification.verdict != VERDICT_FAILED
    assert classification.bucket == BUCKET_NEEDS_ATTENTION
    assert classification.bucket_flag == "needs-attention"
    assert classification.reason == "provider_error:529"


def test_ordinary_traceback_stays_failed_when_the_run_died_idle() -> None:
    classification = classify_run(
        exit_code=1,
        run_state="failed",
        report_exists=False,
        report_bytes=0,
        transcript_bytes=TINY,
        transcript_text=_ORDINARY_TRACEBACK,
    )
    assert classification.verdict == VERDICT_FAILED
    assert "transcript" in classification.reason


def test_ordinary_traceback_stays_needs_attention_after_real_work() -> None:
    classification = classify_run(
        exit_code=1,
        run_state="failed",
        report_exists=False,
        report_bytes=0,
        transcript_bytes=BIG,
        transcript_text=_ORDINARY_TRACEBACK,
    )
    assert classification.verdict == VERDICT_NEEDS_ATTENTION


def test_provider_overlay_does_not_unseal_a_delivered_run() -> None:
    classification = classify_run(
        exit_code=0,
        run_state="completed",
        report_exists=True,
        report_bytes=512,
        transcript_bytes=BIG,
        kernel_axes=KernelAxes(
            execution_state="exited",
            proof_state="passed",
            delivery_state="sealed",
        ),
        transcript_text="API 529 Overloaded\n",
    )
    assert classification.verdict == VERDICT_FINALIZED


def test_kernel_execution_failed_plus_529_is_infra_failure() -> None:
    classification = classify_run(
        exit_code=1,
        run_state="failed",
        report_exists=False,
        report_bytes=0,
        transcript_bytes=TINY,
        kernel_axes=KernelAxes(
            execution_state="failed",
            proof_state="undeclared",
            delivery_state="unverified",
        ),
        transcript_text="API 529 Overloaded\n",
        cost_usd=0.0412,
    )
    assert classification.verdict == VERDICT_INFRA_FAILURE
    assert classification.cost_usd == 0.0412


def test_infra_failure_preserves_existing_meta_cost_usd(tmp_path: Path) -> None:
    """Parents must still aggregate cost on dead provider runs — copy, don't invent."""
    transcript = tmp_path / "dead.transcript.log"
    transcript.write_text(
        "launcher banner\nAPI 529 Overloaded\n",
        encoding="utf-8",
    )
    signals = read_run_signals(
        {
            "exit_code": 1,
            "status": "failed",
            "report": str(tmp_path / "missing.md"),
            "transcript": str(transcript),
            "cost_usd": 0.01925,
        }
    )
    assert signals.cost_usd == 0.01925
    classified = signals.classify()
    assert classified.verdict == VERDICT_INFRA_FAILURE
    assert classified.cost_usd == 0.01925
    assert classified.reason == "provider_error:529"


# --- Every contradiction routes to a human -------------------------------


def test_exit_zero_without_report_is_a_contradiction() -> None:
    """The 2026-05-14 record: top-level `completed`/exit 0, nothing delivered."""
    assert (
        verdict(exit_code=0, report_exists=False, report_bytes=0)
        == VERDICT_NEEDS_ATTENTION
    )


def test_nonzero_exit_with_a_report_is_a_contradiction() -> None:
    """The mirror: the run says it died, the artifacts say it delivered."""
    assert verdict(exit_code=1, state="failed") == VERDICT_NEEDS_ATTENTION


def test_exit_zero_with_a_death_state_is_a_contradiction() -> None:
    assert verdict(exit_code=0, state="failed") == VERDICT_NEEDS_ATTENTION


def test_nonzero_exit_with_a_delivery_state_is_a_contradiction() -> None:
    assert (
        verdict(exit_code=1, state="completed", report_exists=False, report_bytes=0)
        == VERDICT_NEEDS_ATTENTION
    )


def test_empty_report_is_never_a_delivery() -> None:
    """A zero-byte report is control_plane's `report_invalid`, not a report."""
    assert verdict(exit_code=0, report_bytes=0) == VERDICT_NEEDS_ATTENTION


def test_death_after_real_work_is_not_a_clean_failure() -> None:
    """It died — but it did something first, and that something is worth a look."""
    assert (
        verdict(
            exit_code=1,
            state="failed",
            report_exists=False,
            report_bytes=0,
            transcript_bytes=BIG,
        )
        == VERDICT_NEEDS_ATTENTION
    )


@pytest.mark.parametrize(
    "state",
    [
        "report_invalid",
        "contract_failed",
        "ghost",
        "timed_out",
        "quota_exhausted",
        "recovery_required",
        "blocked",
        "stalled",
        "gc",
    ],
)
@pytest.mark.parametrize("code", [0, 1])
def test_ambiguous_states_never_reach_a_confident_drawer(state: str, code: int) -> None:
    """These states *are* the contradiction — no other signal can rescue them."""
    assert verdict(exit_code=code, state=state) == VERDICT_NEEDS_ATTENTION


# --- Unreadable signals fail closed, never to a drawer -------------------


@pytest.mark.parametrize("code", [None, "", "abc", [], {}])
def test_unreadable_exit_code_fails_closed(code: Any) -> None:
    assert verdict(exit_code=code) == VERDICT_NEEDS_ATTENTION


@pytest.mark.parametrize("state", [None, "", "   ", "some_state_from_the_future"])
def test_unreadable_or_unknown_state_fails_closed(state: Any) -> None:
    """An unrecognised state is a signal we cannot read, not a benign one."""
    assert verdict(state=state) == VERDICT_NEEDS_ATTENTION


def test_unstattable_report_fails_closed() -> None:
    assert verdict(report_exists=None, report_bytes=None) == VERDICT_NEEDS_ATTENTION


def test_report_of_unknown_size_fails_closed() -> None:
    assert verdict(report_exists=True, report_bytes=None) == VERDICT_NEEDS_ATTENTION


def test_unreadable_transcript_fails_closed() -> None:
    """Without the transcript we cannot tell a death from a death-after-work."""
    assert (
        verdict(
            exit_code=1,
            state="failed",
            report_exists=False,
            report_bytes=0,
            transcript_bytes=None,
        )
        == VERDICT_NEEDS_ATTENTION
    )


def test_state_matching_is_case_and_whitespace_tolerant() -> None:
    assert verdict(state="  Completed  ") == VERDICT_FINALIZED


def test_every_verdict_carries_a_reason() -> None:
    """The receipt has to be able to say *why*, for all four verdicts."""
    for classification in (
        classify_run(0, "completed", True, 10, BIG),
        classify_run(1, "failed", False, 0, TINY),
        classify_run(0, "ghost", True, 10, BIG),
        classify_run(
            1,
            "failed",
            False,
            0,
            TINY,
            transcript_text="API 529 Overloaded",
        ),
    ):
        assert classification.reason
        assert classification.bucket
        assert classification.bucket_flag


# --------------------------------------------------------------------------
# Reading the signals off a run's artifacts
# --------------------------------------------------------------------------


def test_signals_are_read_from_the_artifacts_on_disk(tmp_path: Path) -> None:
    report = tmp_path / "r.md"
    report.write_text(
        render_minimal_frontmatter(
            run_id="r1", agent="codex", skill="scaffold", status="completed"
        )
        + "body\n",
        encoding="utf-8",
    )
    transcript = tmp_path / "t.log"
    transcript.write_text("xyz", encoding="utf-8")

    signals = read_run_signals(
        {
            "exit_code": 0,
            "status": "completed",
            "report": str(report),
            "transcript": str(transcript),
        }
    )

    assert signals.report_exists is True
    assert signals.report_bytes is not None and signals.report_bytes >= 4
    assert signals.transcript_bytes == 3
    assert signals.report_frontmatter_ok is True
    assert signals.classify().verdict == VERDICT_FINALIZED


def test_declared_but_absent_report_is_absent_not_unreadable(tmp_path: Path) -> None:
    signals = read_run_signals(
        {"exit_code": 1, "status": "failed", "report": str(tmp_path / "gone.md")}
    )
    assert signals.report_exists is False
    assert signals.report_bytes == 0


def test_undeclared_transcript_is_unknown(tmp_path: Path) -> None:
    """No transcript path means we do not know where to look — not zero work."""
    signals = read_run_signals({"exit_code": 1, "status": "failed"})
    assert signals.transcript_bytes is None
    assert signals.classify().verdict == VERDICT_NEEDS_ATTENTION


def test_symlinked_transcript_is_measured_through_the_link(tmp_path: Path) -> None:
    """`spawn.finalize_artifacts` leaves a compat symlink at the announced path,
    so the announced transcript is routinely a link to the real one. Measuring
    the link instead of its target reads ~60 bytes and calls a full run dead."""
    real = tmp_path / "real.log"
    real.write_text("x" * BIG, encoding="utf-8")
    link = tmp_path / "announced.log"
    link.symlink_to(real)

    signals = read_run_signals(
        {"exit_code": 1, "status": "failed", "transcript": str(link)}
    )

    assert signals.transcript_bytes == BIG


def test_control_plane_state_key_wins_over_launcher_status() -> None:
    """`state` is the control-plane spelling; when present it is the fresher truth."""
    signals = read_run_signals(
        {"exit_code": 0, "status": "completed", "state": "contract_failed"}
    )
    assert signals.run_state == "contract_failed"
    assert signals.classify().verdict == VERDICT_NEEDS_ATTENTION


# --------------------------------------------------------------------------
# Exit code → bucket. The degraded path only; it must hold for the whole range.
# --------------------------------------------------------------------------


def test_only_exit_zero_is_success() -> None:
    assert bucket_for_exit_code(0) == BUCKET_FINALIZED
    for code in (1, 2, 7, 127, 255):
        assert bucket_for_exit_code(code) == BUCKET_NEEDS_ATTENTION


def test_timeouts_and_kills_need_attention() -> None:
    # SIGKILL / SIGTERM as the shell reports them. The spec calls these out
    # explicitly; they are non-zero, so they need no special case — this test
    # exists to keep it that way.
    assert bucket_for_exit_code(137) == BUCKET_NEEDS_ATTENTION  # 128 + SIGKILL
    assert bucket_for_exit_code(143) == BUCKET_NEEDS_ATTENTION  # 128 + SIGTERM
    assert bucket_for_exit_code(124) == BUCKET_NEEDS_ATTENTION  # coreutils timeout


@pytest.mark.parametrize("code", [None, "", "abc", [], {}])
def test_unreadable_exit_code_needs_attention(code: Any) -> None:
    """A run whose outcome we cannot read is exactly a run needing attention."""
    assert bucket_for_exit_code(code) == BUCKET_NEEDS_ATTENTION


def test_string_exit_codes_parse() -> None:
    assert bucket_for_exit_code("0") == BUCKET_FINALIZED
    assert bucket_for_exit_code("1") == BUCKET_NEEDS_ATTENTION


# --------------------------------------------------------------------------
# Which runs may be transferred at all
# --------------------------------------------------------------------------


def test_headless_run_is_skipped_not_failed() -> None:
    """CI / detached runs have no pane env. Nothing to triage is not an error."""
    plan = plan_triage({"run_id": "r1", "exit_code": 0}, {"PATH": "/usr/bin"})
    assert plan.should_run is False
    assert plan.skip_reason == "no_session"


def test_meta_stamped_origin_session_enables_dispatcher_triage() -> None:
    """Python dispatcher finishes outside the pane; origin lives on meta."""
    plan = plan_triage(
        {
            "run_id": "scaf-1",
            "exit_code": 0,
            "origin_session": "vibecrafted workers",
            "origin_tab": "scaf-1",
            "status": "completed",
            "report": "/tmp/report.md",
        },
        {"PATH": "/usr/bin"},
    )
    assert plan.should_run is True
    assert plan.origin_session == "vibecrafted workers"
    assert plan.origin_tab == "scaf-1"
    assert plan.pane_id == ""


def test_operator_session_meta_alias_is_accepted() -> None:
    plan = plan_triage(
        {
            "run_id": "work-1",
            "exit_code": 0,
            "operator_session": "vibecrafted",
        },
        {"PATH": "/usr/bin"},
    )
    assert plan.should_run is True
    assert plan.origin_session == "vibecrafted"
    assert plan.origin_tab == "work-1"


def test_pane_env_without_session_name_is_skipped() -> None:
    plan = plan_triage(
        {"run_id": "r1", "exit_code": 0},
        {"VC_FRAME_PANE_ID": "terminal_1"},
    )
    assert plan.should_run is False
    assert plan.skip_reason == "no_session"


def test_legacy_zellij_env_still_identifies_a_session() -> None:
    """vc-frame dual-emits ZELLIJ_* during the rename transition."""
    plan = plan_triage(
        {"run_id": "r1", "exit_code": 0},
        {"ZELLIJ_PANE_ID": "terminal_1", "ZELLIJ_SESSION_NAME": "sess"},
    )
    assert plan.should_run is True
    assert plan.origin_session == "sess"


def test_dispatcher_inherited_pane_env_is_not_trusted() -> None:
    """2026-07-25: dispatched runs stamped the operator's pane ("1"); the dump
    aimed at it found nothing and the tab never reached its bucket. Without an
    env claim of sitting in the run's own tab, the ambient pane id is dropped
    — capture then falls back to the tab-name path on the vc-frame side."""
    plan = plan_triage(
        {"run_id": "r1", "exit_code": 0},
        make_env(),  # VC_FRAME_PANE_ID present, but no VC_FRAME_TAB_NAME claim
    )
    assert plan.should_run is True
    assert plan.pane_id == ""


def test_in_tab_finish_keeps_its_own_pane() -> None:
    """The classic path: the process sits in the run's tab, so its pane is real."""
    plan = plan_triage(
        {"run_id": "r1", "exit_code": 0},
        make_env(VC_FRAME_TAB_NAME="r1"),
    )
    assert plan.should_run is True
    assert plan.pane_id == "terminal_3"


def test_meta_stamped_pane_still_wins_over_env() -> None:
    plan = plan_triage(
        {"run_id": "r1", "exit_code": 0, "origin_pane_id": "terminal_9"},
        make_env(VC_FRAME_TAB_NAME="r1"),
    )
    assert plan.pane_id == "terminal_9"


def test_foreign_env_tab_is_never_transferred() -> None:
    """A dispatcher can leak the operator's VC_FRAME_TAB_NAME. With no tab in
    the meta, transferring would capture and close the operator's tab."""
    plan = plan_triage(
        {"run_id": "r1", "exit_code": 0},
        make_env(VC_FRAME_TAB_NAME="operator-tab"),
    )
    assert plan.should_run is False
    assert plan.skip_reason == "foreign_tab"


def test_meta_stamped_tab_survives_a_foreign_env_claim() -> None:
    """The durable meta stamp outranks whatever tab the ambient env names."""
    plan = plan_triage(
        {"run_id": "r1", "exit_code": 0, "origin_tab": "r1"},
        make_env(VC_FRAME_TAB_NAME="operator-tab"),
    )
    assert plan.should_run is True
    assert plan.origin_tab == "r1"


def test_marbles_shared_tab_is_never_closed() -> None:
    """Closing a shared tab would destroy the sibling marbles' scrollback."""
    plan = plan_triage(
        {"run_id": "r1", "exit_code": 0},
        make_env(
            VIBECRAFTED_MARBLES_TAB_NAME="marbles-wave-3",
            VC_FRAME_TAB_NAME="marbles-wave-3",
        ),
    )
    assert plan.should_run is False
    assert plan.skip_reason == "shared_tab"


def test_run_owning_its_tab_is_transferred_even_under_marbles() -> None:
    """A marbles run in its OWN tab is safe — only the shared tab is refused."""
    plan = plan_triage(
        {"run_id": "r1", "exit_code": 0},
        make_env(
            VIBECRAFTED_MARBLES_TAB_NAME="marbles-wave-3",
            VC_FRAME_TAB_NAME="r1",
        ),
    )
    assert plan.should_run is True
    assert plan.origin_tab == "r1"


def test_missing_run_id_is_skipped() -> None:
    plan = plan_triage({"exit_code": 0}, make_env())
    assert plan.should_run is False
    assert plan.skip_reason == "no_run_id"


def test_run_id_falls_back_to_spawn_env() -> None:
    plan = plan_triage({"exit_code": 0}, make_env(SPAWN_RUN_ID="run-from-env"))
    assert plan.should_run is True
    assert plan.run_id == "run-from-env"


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
def test_operator_can_switch_triage_off(value: str) -> None:
    plan = plan_triage(
        {"run_id": "r1", "exit_code": 0},
        make_env(VIBECRAFTED_TRIAGE_RUN=value),
    )
    assert plan.should_run is False
    assert plan.skip_reason == "disabled"


@pytest.mark.parametrize(
    ("meta", "env"),
    [
        ({"run_id": "r1", "runtime": "headless"}, make_env()),
        ({"run_id": "r1"}, make_env(VIBECRAFTED_RUNTIME="headless")),
    ],
)
def test_headless_runtime_never_enters_terminal_triage(
    meta: dict[str, object], env: dict[str, str]
) -> None:
    plan = plan_triage(meta, env)
    assert plan.should_run is False
    assert plan.skip_reason == "headless"
    assert triage_outcome_is_complete(
        TriageOutcome(outcome=OUTCOME_SKIPPED, reason="headless")
    )


def test_tab_defaults_to_run_id() -> None:
    """The runtime names run tabs by run id (lib/vc_frame.sh)."""
    plan = plan_triage({"run_id": "run-0007", "exit_code": 0}, make_env())
    assert plan.origin_tab == "run-0007"


@pytest.mark.parametrize(
    ("settlement_verdict", "settlement_tui", "expected_verdict", "expected_bucket"),
    [
        (VERDICT_FINALIZED, "f", VERDICT_FINALIZED, BUCKET_FINALIZED),
        (VERDICT_FAILED, "x", VERDICT_FAILED, BUCKET_FAILED),
        (
            VERDICT_NEEDS_ATTENTION,
            "n",
            VERDICT_NEEDS_ATTENTION,
            BUCKET_NEEDS_ATTENTION,
        ),
        ("invalid", "x", VERDICT_FAILED, BUCKET_FAILED),
    ],
)
def test_canonical_settlement_owns_the_plan_destination(
    settlement_verdict: str,
    settlement_tui: str,
    expected_verdict: str,
    expected_bucket: str,
) -> None:
    plan = plan_triage(
        {
            "run_id": "settled-run",
            "exit_code": 0,
            "status": "completed",
            "settlement_revision": 17,
            "settlement_verdict": settlement_verdict,
            "settlement_tui": settlement_tui,
        },
        make_env(),
    )

    assert plan.should_run is True
    assert plan.settlement_revision == 17
    assert plan.settlement_verdict == expected_verdict
    assert plan.settlement_tui == settlement_tui
    assert plan.verdict == expected_verdict
    assert plan.bucket == expected_bucket
    argv = plan.argv("vc-frame")
    assert argv[argv.index("--settlement-revision") + 1] == "17"


def test_partial_settlement_never_falls_back_to_heuristics() -> None:
    plan = plan_triage(
        {
            "run_id": "settled-run",
            "exit_code": 0,
            "status": "completed",
            "settlement_revision": 17,
        },
        make_env(),
    )

    assert plan.should_run is False
    assert plan.skip_reason == "invalid_settlement"


@pytest.mark.parametrize(
    "settlement_fields",
    [
        {
            "settlement": {
                "revision": 17,
                "verdict": VERDICT_FINALIZED,
                "tui": "f",
            }
        },
        {
            "settlement_revision": 17,
            "settlement_verdict": VERDICT_FINALIZED,
            "settlement_tui": "f",
            "settlement": {
                "revision": 16,
                "verdict": VERDICT_FAILED,
                "tui": "x",
            },
        },
        {
            "settlement_revision": 1,
            "settlement_verdict": VERDICT_FINALIZED,
            "settlement_tui": "f",
            "settlement": {
                "revision": True,
                "verdict": VERDICT_FINALIZED,
                "tui": "f",
            },
        },
    ],
)
def test_nested_settlement_cannot_bypass_exact_top_level_identity(
    settlement_fields: dict[str, Any],
) -> None:
    plan = plan_triage(
        {
            "run_id": "settled-run",
            "exit_code": 0,
            "status": "completed",
            **settlement_fields,
        },
        make_env(),
    )

    assert plan.should_run is False
    assert plan.skip_reason == "invalid_settlement"


# --------------------------------------------------------------------------
# The rendered invocation
# --------------------------------------------------------------------------


def test_argv_carries_the_full_identity() -> None:
    plan = plan_triage(
        {
            "run_id": "run-0007",
            "exit_code": 3,
            "root": "/repo",
            "launcher": "/tmp/l.sh",
            "origin_pane_id": "terminal_3",
        },
        make_env(),
    )
    argv = plan.argv("/usr/bin/vc-frame")

    assert argv[:2] == ["/usr/bin/vc-frame", "triage-run"]
    assert argv[argv.index("--run") + 1] == "run-0007"
    assert argv[argv.index("--exit-code") + 1] == "3"
    assert argv[argv.index("--origin-session") + 1] == "vibecrafted-dev"
    assert argv[argv.index("--origin-tab") + 1] == "run-0007"
    assert argv[argv.index("--pane-id") + 1] == "terminal_3"
    assert argv[argv.index("--cwd") + 1] == "/repo"
    # The rerun pane gets the launcher — the run, reproducible.
    assert argv[-2:] == ["--", "/tmp/l.sh"]


def test_argv_carries_only_validated_runtime_transcript_before_command(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "run-0007.transcript.log"
    transcript.write_text("durable runtime transcript\n", encoding="utf-8")
    manifest = write_runtime_transcript_manifest(transcript, run_id="run-0007")
    assert manifest is not None

    plan = plan_triage(
        {
            "run_id": "run-0007",
            "exit_code": 0,
            "root": str(tmp_path),
            "launcher": "/tmp/l.sh",
            "origin_pane_id": "terminal_3",
            "transcript": str(transcript),
        },
        make_env(),
    )
    argv = plan.argv("/usr/bin/vc-frame")

    assert plan.runtime_transcript == str(transcript.resolve())
    flag_index = argv.index("--runtime-transcript")
    assert argv[flag_index + 1] == str(transcript.resolve())
    assert flag_index < argv.index("--")


def test_argv_omits_runtime_transcript_without_valid_manifest(tmp_path: Path) -> None:
    transcript = tmp_path / "run-0007.transcript.log"
    transcript.write_text("unbound runtime transcript\n", encoding="utf-8")

    plan = plan_triage(
        {
            "run_id": "run-0007",
            "exit_code": 0,
            "root": str(tmp_path),
            "launcher": "/tmp/l.sh",
            "origin_pane_id": "terminal_3",
            "transcript": str(transcript),
        },
        make_env(),
    )

    assert plan.should_run is True
    assert plan.runtime_transcript == ""
    assert "--runtime-transcript" not in plan.argv("/usr/bin/vc-frame")


def test_argv_renders_negative_exit_code_without_clap_ambiguity() -> None:
    plan = TriagePlan(should_run=True, run_id="run-unknown", exit_code=-1)

    assert plan.argv("vc-frame") == [
        "vc-frame",
        "triage-run",
        "--run",
        "run-unknown",
        "--exit-code=-1",
    ]


def test_argv_carries_the_verdict_as_a_bucket_flag(tmp_path: Path) -> None:
    """W2-B-4a's contract: the drawer is ours to choose, in kebab spelling."""
    meta = write_meta(tmp_path, exit_code=0)
    runner = Runner()

    triage_finished_run(meta, live_env(tmp_path), runner)

    assert runner.bucket_flag() == "finalized"


def test_command_is_passed_after_the_separator() -> None:
    """clap `last(true)`: everything after `--` is the preserved command line."""
    plan = plan_triage(
        {"run_id": "r", "exit_code": 0, "command": ["claude", "-p", "do it"]},
        make_env(),
    )
    argv = plan.argv("vc-frame")
    assert argv[argv.index("--") + 1 :] == ["claude", "-p", "do it"]


# --------------------------------------------------------------------------
# Fail-open. Nothing here may ever damage an already-finished run.
# --------------------------------------------------------------------------


def test_stale_binary_without_the_subcommand_is_a_skip(tmp_path: Path) -> None:
    """An install predating vc-frame 71146085 must not read as a failure."""
    meta = write_meta(tmp_path)
    runner = Runner(supports=False)

    outcome = triage_finished_run(meta, live_env(tmp_path), runner)

    assert outcome.outcome == OUTCOME_SKIPPED
    assert outcome.reason == "unsupported_binary"
    # Crucially: we never attempted the transfer against a binary that can't do it.
    assert runner.transfer_calls == []


def test_binary_predating_the_bucket_flag_degrades_gracefully(tmp_path: Path) -> None:
    """A vc-frame with `triage-run` but no `--bucket` must still work.

    Passing a flag it cannot parse would fail the whole call, so we omit it and
    let vc-frame bucket by exit code — and the receipt says so, both in where the
    run actually went and in the fact that the verdict did not choose it.
    """
    meta = write_meta(
        tmp_path,
        exit_code=137,
        status="report_missing",
        report=str(tmp_path / "never-written.md"),
        transcript=str(_tiny_transcript(tmp_path)),
    )
    runner = Runner(supports_bucket=False)

    outcome = triage_finished_run(meta, live_env(tmp_path), runner)

    assert runner.bucket_flag() is None
    assert outcome.verdict_degraded == "exit_code_only"
    # The verdict was `failed`, but a stale vc-frame has no such drawer, so the
    # receipt must name the drawer the run really lands in.
    assert outcome.verdict == VERDICT_FAILED
    assert outcome.outcome == OUTCOME_NEEDS_ATTENTION
    assert outcome.bucket == BUCKET_NEEDS_ATTENTION

    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["triage_verdict"] == VERDICT_FAILED
    assert payload["triage_bucket"] == BUCKET_NEEDS_ATTENTION
    assert payload["triage_verdict_degraded"] == "exit_code_only"


@pytest.mark.parametrize(
    ("supports_bucket", "supports_settlement_revision"),
    [(False, True), (True, False)],
)
def test_canonical_settlement_fails_closed_on_unsupported_binary_contract(
    tmp_path: Path,
    supports_bucket: bool,
    supports_settlement_revision: bool,
) -> None:
    meta = write_meta(
        tmp_path,
        settlement_revision=9,
        settlement_verdict=VERDICT_FINALIZED,
        settlement_tui="f",
    )
    runner = Runner(
        supports_bucket=supports_bucket,
        supports_settlement_revision=supports_settlement_revision,
    )

    outcome = triage_finished_run(meta, live_env(tmp_path), runner)

    assert outcome.outcome == OUTCOME_ERROR
    assert outcome.reason == "unsupported_settlement_contract"
    assert runner.transfer_calls == []
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["triage_pending"] is True
    assert payload["triage_last_error"]["reason"] == "unsupported_settlement_contract"


def test_missing_binary_is_a_skip(tmp_path: Path) -> None:
    meta = write_meta(tmp_path)
    runner = Runner()

    outcome = triage_finished_run(
        meta, make_env(PATH="/nonexistent", VIBECRAFTED_VC_FRAME_BIN=""), runner
    )

    assert outcome.outcome == OUTCOME_SKIPPED
    assert outcome.reason == "no_binary"


def test_nonzero_triage_exit_is_recorded_not_raised(tmp_path: Path) -> None:
    meta = write_meta(tmp_path)
    runner = Runner(result=FakeProc(2, stderr="bucket session vanished"))

    outcome = triage_finished_run(meta, live_env(tmp_path), runner)

    # `error` and not `failed`: a broken transfer says nothing about the run.
    assert outcome.outcome == OUTCOME_ERROR
    assert "bucket session vanished" in outcome.reason
    # The verdict survives the broken transfer, so the operator still sees it.
    assert outcome.verdict == VERDICT_FINALIZED


def test_exploding_runner_is_contained(tmp_path: Path) -> None:
    """Even an OSError from the subprocess layer must not escape."""
    meta = write_meta(tmp_path)
    runner = Runner(result=OSError("no fork for you"))

    outcome = triage_finished_run(meta, live_env(tmp_path), runner)

    assert outcome.outcome == OUTCOME_ERROR
    assert "no fork for you" in outcome.reason


def test_unreadable_meta_does_not_raise(tmp_path: Path) -> None:
    meta = tmp_path / "broken.meta.json"
    meta.write_text("{not json", encoding="utf-8")

    outcome = triage_finished_run(meta, make_env(), Runner())

    assert outcome.outcome == OUTCOME_SKIPPED
    assert outcome.reason.startswith("no_meta")


def test_absent_meta_does_not_raise(tmp_path: Path) -> None:
    outcome = triage_finished_run(tmp_path / "gone.json", make_env(), Runner())
    assert outcome.outcome == OUTCOME_SKIPPED


# --------------------------------------------------------------------------
# Receipts
# --------------------------------------------------------------------------


def test_success_receipt_names_the_bucket(tmp_path: Path) -> None:
    meta = write_meta(tmp_path, exit_code=0)

    outcome = triage_finished_run(meta, live_env(tmp_path), Runner())

    assert outcome.outcome == OUTCOME_FINALIZED
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["triage"] == OUTCOME_FINALIZED
    assert payload["triage_bucket"] == BUCKET_FINALIZED
    assert payload["triage_pending"] is False


def test_receipt_records_the_verdict_and_its_evidence(tmp_path: Path) -> None:
    """The operator must be able to audit *why* a run went where it went."""
    meta = write_meta(tmp_path, exit_code=0)

    triage_finished_run(meta, live_env(tmp_path), Runner())

    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["triage_verdict"] == VERDICT_FINALIZED
    assert payload["triage_verdict_reason"] == "exit_0_report_delivered"
    assert payload["triage_verdict_degraded"] == ""


def test_killed_run_receipt_says_failed(tmp_path: Path) -> None:
    """Exit 137, no report, banner-only transcript — the W0-A shape, end to end."""
    meta = write_meta(
        tmp_path,
        exit_code=137,
        status="report_missing",
        report=str(tmp_path / "never-written.md"),
        transcript=str(_tiny_transcript(tmp_path)),
    )

    outcome = triage_finished_run(meta, live_env(tmp_path), Runner())

    assert outcome.outcome == OUTCOME_FAILED
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["triage_bucket"] == BUCKET_FAILED
    assert payload["triage_verdict"] == VERDICT_FAILED


def test_contradicting_run_receipt_says_needs_attention(tmp_path: Path) -> None:
    """Exit 137 with a full report on disk — the signals disagree, so: a human."""
    meta = write_meta(tmp_path, exit_code=137, status="failed")

    outcome = triage_finished_run(meta, live_env(tmp_path), Runner())

    assert outcome.outcome == OUTCOME_NEEDS_ATTENTION
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["triage_bucket"] == BUCKET_NEEDS_ATTENTION
    assert payload["triage_verdict_reason"] == "exit_137_with_report"


def test_skip_receipt_is_written_for_headless_runs(tmp_path: Path) -> None:
    """The spec's `triage_skipped: no_session` — a note, not an error."""
    meta = write_meta(tmp_path)

    triage_finished_run(meta, {"PATH": "/usr/bin"}, Runner())

    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["triage"] == OUTCOME_SKIPPED
    assert payload["triage_reason"] == "no_session"


def test_intent_is_recorded_before_the_transfer(tmp_path: Path) -> None:
    """Our own success closes this tab and can kill us mid-flight.

    So the receipt must be on disk *before* the transfer runs, marked pending.
    Asserted by reading meta.json from inside the runner.
    """
    meta = write_meta(tmp_path, exit_code=0)
    seen: dict[str, Any] = {}

    class PeekingRunner(Runner):
        def __call__(self, argv: Sequence[str]) -> Any:
            if list(argv)[1:2] == ["triage-run"] and "--help" not in argv:
                seen.update(json.loads(meta.read_text(encoding="utf-8")))
            return super().__call__(argv)

    triage_finished_run(meta, live_env(tmp_path), PeekingRunner())

    assert seen["triage"] == OUTCOME_FINALIZED
    assert seen["triage_pending"] is True
    assert seen["triage_bucket"] == BUCKET_FINALIZED


def test_artifact_meta_commits_pending_before_vc_frame_probe(
    tmp_path: Path,
) -> None:
    """Artifact metadata uses its own owner root, not sibling control_plane/."""

    home = tmp_path / ".vibecrafted"
    control_plane = home / "control_plane"
    control_plane.mkdir(parents=True)
    artifact_dir = home / "artifacts" / "vetcoders" / "demo" / "reports"
    artifact_dir.mkdir(parents=True)
    meta = write_meta(
        artifact_dir,
        origin_session="demo workers",
        origin_tab="run-0007",
    )
    seen_at_probe: dict[str, Any] = {}

    class PeekingRunner(Runner):
        def __call__(self, argv: Sequence[str]) -> Any:
            if list(argv)[1:] == ["triage-run", "--help"]:
                seen_at_probe.update(json.loads(meta.read_text(encoding="utf-8")))
            return super().__call__(argv)

    runner = PeekingRunner()
    outcome = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_HOME=str(home)),
        runner,
    )

    assert seen_at_probe["triage_pending"] is True
    assert seen_at_probe["triage"] == OUTCOME_FINALIZED
    assert len(runner.transfer_calls) == 1
    # The fake transfer has no durable vc-frame proof, so the post-call result
    # fails closed. The important invariant is that the owner-root receipt and
    # pre-probe barrier both existed before any external call.
    assert outcome.outcome == OUTCOME_ERROR
    assert outcome.reason.startswith("transfer_proof_invalid:")
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["triage"] == OUTCOME_FINALIZED
    assert payload["triage_pending"] is True
    assert payload["triage_last_error"]["reason"].startswith("transfer_proof_invalid:")


def test_pending_barrier_failure_records_error_without_any_vc_frame_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed pending replace leaves durable error truth and invokes nothing."""

    meta = write_meta(tmp_path, exit_code=0)
    runner = Runner()
    real_replace = run_mutation_module.os.replace

    def reject_pending(source: str, destination: str | Path) -> None:
        try:
            candidate = json.loads(Path(source).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            candidate = {}
        if candidate.get("triage_pending") is True:
            raise OSError("forced pending receipt failure")
        real_replace(source, destination)

    monkeypatch.setattr(run_mutation_module.os, "replace", reject_pending)

    outcome = triage_finished_run(meta, live_env(tmp_path), runner)

    assert outcome.outcome == OUTCOME_ERROR
    assert outcome.reason == "intent_persist_failed"
    assert runner.calls == []
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["triage"] == OUTCOME_ERROR
    assert payload["triage_reason"] == "intent_persist_failed"
    assert payload["triage_pending"] is False


def test_receipt_never_disturbs_terminal_state(tmp_path: Path) -> None:
    """The run's own truth is not ours to edit."""
    meta = write_meta(tmp_path, exit_code=7, status="failed", duration_s=12.5)

    triage_finished_run(meta, live_env(tmp_path), Runner())

    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 7
    assert payload["duration_s"] == 12.5
    assert payload["liveness"] == "terminal"


def test_concurrent_writer_is_not_clobbered(tmp_path: Path) -> None:
    """meta.json is re-read before the receipt lands, so a control-plane sync
    (or any other writer) that touched it mid-transfer keeps its change."""
    meta = write_meta(tmp_path, exit_code=0)

    class ConcurrentRunner(Runner):
        def __call__(self, argv: Sequence[str]) -> Any:
            if list(argv)[1:2] == ["triage-run"] and "--help" not in argv:
                payload = json.loads(meta.read_text(encoding="utf-8"))
                payload["session_id"] = "written-by-someone-else"
                meta.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            return super().__call__(argv)

    triage_finished_run(meta, live_env(tmp_path), ConcurrentRunner())

    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["session_id"] == "written-by-someone-else"
    assert payload["triage"] == OUTCOME_FINALIZED


def test_success_links_exact_v4_transfer_proof_atomically(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)

    class ProofRunner(Runner):
        def __call__(self, argv: Sequence[str]) -> Any:
            if list(argv)[1:2] == ["triage-run"] and "--help" not in argv:
                current = json.loads(meta.read_text(encoding="utf-8"))
                current["session_id"] = "concurrent-terminal-writer"
                _write_json(meta, current)
                _materialize_v4_transfer(cp, current)
            return super().__call__(argv)

    outcome = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        ProofRunner(),
    )

    assert outcome.outcome == OUTCOME_FINALIZED
    payload = json.loads(meta.read_text(encoding="utf-8"))
    proof = load_vc_frame_transfer_proof(cp, payload)
    assert payload["session_id"] == "concurrent-terminal-writer"
    assert payload["triage_transfer_receipt"] == str(proof.receipt_path)
    assert payload["triage_transfer"] == proof.projection()
    assert payload["triage_pending"] is False


@pytest.mark.parametrize(
    ("receipt_field", "tampered_value"),
    [
        ("settlement_revision", 6),
        ("superseded_viewers", [{"viewer_token": "stale"}]),
        ("command", ["claude", "unexpected"]),
        ("cwd", "/somewhere-else"),
        ("pane_id", "terminal_99"),
        ("runtime_transcript", "/tmp/unbound-transcript.log"),
    ],
)
def test_transfer_proof_binds_settlement_and_normalized_runtime_request(
    tmp_path: Path,
    receipt_field: str,
    tampered_value: Any,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "settlement_revision": 7,
            "settlement_verdict": VERDICT_FINALIZED,
            "settlement_tui": "f",
        }
    )
    _write_json(meta, payload)
    _materialize_v4_transfer(cp, payload)
    receipt_path = cp / "finished_runs" / "run-proof" / "transfer.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt[receipt_field] = tampered_value
    _write_json(receipt_path, receipt)

    with pytest.raises(run_triage_module.TransferProofError):
        load_vc_frame_transfer_proof(cp, payload)


def test_runtime_settlement_revision_flows_through_argv_receipt_and_projection(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "settlement_revision": 23,
            "settlement_verdict": VERDICT_FAILED,
            "settlement_tui": "x",
            "settlement": {
                "revision": 23,
                "verdict": VERDICT_FAILED,
                "tui": "x",
            },
        }
    )
    _write_json(meta, payload)

    class ProofRunner(Runner):
        def __call__(self, argv: Sequence[str]) -> Any:
            rendered = list(argv)
            if rendered[1:2] == ["triage-run"] and "--help" not in rendered:
                assert rendered.count("--settlement-revision") == 1
                revision_index = rendered.index("--settlement-revision")
                assert rendered[revision_index + 1] == "23"
                assert rendered[rendered.index("--bucket") + 1] == "failed"
                _materialize_v4_transfer(
                    cp,
                    json.loads(meta.read_text(encoding="utf-8")),
                    bucket="Failed",
                )
            return super().__call__(rendered)

    runner = ProofRunner()
    outcome = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )

    assert outcome.outcome == OUTCOME_FAILED
    assert len(runner.transfer_calls) == 1
    receipt = json.loads(
        (cp / "finished_runs" / "run-proof" / "transfer.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["settlement_revision"] == 23
    current = json.loads(meta.read_text(encoding="utf-8"))
    assert current["triage_settlement_revision"] == 23
    assert current["triage_transfer"]["settlement"] == {
        "revision": 23,
        "verdict": VERDICT_FAILED,
        "tui": "x",
    }


def test_runtime_settlement_revision_mismatch_never_commits_projection(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "settlement_revision": 23,
            "settlement_verdict": VERDICT_FINALIZED,
            "settlement_tui": "f",
        }
    )
    _write_json(meta, payload)

    class StaleProofRunner(Runner):
        def __call__(self, argv: Sequence[str]) -> Any:
            rendered = list(argv)
            if rendered[1:2] == ["triage-run"] and "--help" not in rendered:
                assert rendered[rendered.index("--settlement-revision") + 1] == "23"
                current = json.loads(meta.read_text(encoding="utf-8"))
                _materialize_v4_transfer(cp, current)
                receipt_path = cp / "finished_runs" / "run-proof" / "transfer.json"
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                receipt["settlement_revision"] = 22
                _write_json(receipt_path, receipt)
            return super().__call__(rendered)

    runner = StaleProofRunner()
    outcome = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )

    assert outcome.outcome == OUTCOME_ERROR
    assert outcome.reason.startswith("transfer_proof_invalid:")
    assert len(runner.transfer_calls) == 1
    current = json.loads(meta.read_text(encoding="utf-8"))
    assert current["triage_pending"] is True
    assert "triage_transfer" not in current
    assert "triage_transfer_receipt" not in current


def test_transfer_proof_requires_explicit_empty_superseded_viewers(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    _materialize_v4_transfer(cp, payload)
    receipt_path = cp / "finished_runs" / "run-proof" / "transfer.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt.pop("superseded_viewers")
    _write_json(receipt_path, receipt)

    with pytest.raises(run_triage_module.TransferProofError):
        load_vc_frame_transfer_proof(cp, payload)


def test_transfer_proof_rejects_boolean_settlement_revision(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "settlement_revision": 1,
            "settlement_verdict": VERDICT_FINALIZED,
            "settlement_tui": "f",
        }
    )
    _write_json(meta, payload)
    _materialize_v4_transfer(cp, payload)
    receipt_path = cp / "finished_runs" / "run-proof" / "transfer.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["settlement_revision"] = True
    _write_json(receipt_path, receipt)

    with pytest.raises(run_triage_module.TransferProofError):
        load_vc_frame_transfer_proof(cp, payload)


def test_transfer_proof_rejects_bucket_that_disagrees_with_settlement(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "settlement_revision": 3,
            "settlement_verdict": VERDICT_FAILED,
            "settlement_tui": "x",
        }
    )
    _write_json(meta, payload)
    _materialize_v4_transfer(cp, payload, bucket="Finalized")

    with pytest.raises(run_triage_module.TransferProofError):
        load_vc_frame_transfer_proof(cp, payload)


def test_durable_proof_requires_current_exact_triage_settlement(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "await_outcome": "completed",
            "settlement_revision": 7,
            "settlement_verdict": VERDICT_FINALIZED,
            "settlement_tui": "f",
            "triage": OUTCOME_FINALIZED,
            "triage_pending": False,
            "triage_reason": "canonical_settlement_revision_7",
            "triage_bucket": BUCKET_FINALIZED,
            "triage_verdict": VERDICT_FINALIZED,
            "triage_verdict_reason": "canonical_settlement_revision_7",
            "triage_verdict_degraded": "",
        }
    )
    _write_json(meta, payload)
    _materialize_v4_transfer(cp, payload)

    runner = Runner()
    adopted = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )
    assert adopted.outcome == OUTCOME_FINALIZED
    assert runner.calls == []
    assert run_triage_module.load_durable_transfer_proof(cp, meta)

    stale = json.loads(meta.read_text(encoding="utf-8"))
    stale["triage_settlement_revision"] = 6
    _write_json(meta, stale)
    with pytest.raises(run_triage_module.TransferProofError):
        run_triage_module.load_durable_transfer_proof(cp, meta)


def test_exact_proof_wins_when_transfer_process_reports_lock_contention(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)

    class ContendedRunner(Runner):
        def __call__(self, argv: Sequence[str]) -> Any:
            if list(argv)[1:2] == ["triage-run"] and "--help" not in argv:
                _materialize_v4_transfer(
                    cp,
                    json.loads(meta.read_text(encoding="utf-8")),
                )
                self.calls.append(list(argv))
                return FakeProc(1, stderr="another triage process owns transfer lock")
            return super().__call__(argv)

    runner = ContendedRunner()
    outcome = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )

    assert outcome.outcome == OUTCOME_FINALIZED
    assert len(runner.transfer_calls) == 1
    recovered = json.loads(meta.read_text(encoding="utf-8"))
    assert recovered["triage_pending"] is False
    assert recovered["triage_transfer"]["version"] == 4


def test_pending_v4_proof_is_adopted_without_second_transfer(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "triage": OUTCOME_FINALIZED,
            "triage_pending": True,
            "triage_reason": "exit_0_report_delivered",
            "triage_bucket": BUCKET_FINALIZED,
            "triage_verdict": VERDICT_FINALIZED,
            "triage_verdict_reason": "exit_0_report_delivered",
            "triage_verdict_degraded": "",
        }
    )
    _write_json(meta, payload)
    _materialize_v4_transfer(cp, payload)
    runner = Runner()

    outcome = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )

    assert outcome.outcome == OUTCOME_FINALIZED
    assert runner.calls == []
    recovered = json.loads(meta.read_text(encoding="utf-8"))
    assert recovered["triage_pending"] is False
    assert recovered["triage_transfer"]["version"] == 4
    assert recovered["triage_transfer_receipt"]


def test_parent_exception_does_not_unlock_living_inheriting_child(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    ready = tmp_path / "inherited-lock-ready"
    child_code = """
import os
import pathlib
import sys
import time

descriptor = int(sys.argv[1])
ready = pathlib.Path(sys.argv[2])
os.fstat(descriptor)
ready.write_text("inherited", encoding="utf-8")
time.sleep(60)
"""
    child: subprocess.Popen[str] | None = None
    try:
        with (
            pytest.raises(RuntimeError, match="parent interrupted"),
            run_triage_module._hold_vc_frame_transfer_lock(cp, payload) as fd,
        ):
            child = subprocess.Popen(
                [sys.executable, "-c", child_code, str(fd), str(ready)],
                pass_fds=(fd,),
                start_new_session=True,
                text=True,
            )
            deadline = time.monotonic() + 5
            while not ready.is_file() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.is_file(), "child never confirmed inherited lock fd"
            raise RuntimeError("parent interrupted")

        assert child is not None
        assert child.poll() is None
        assert run_triage_module._vc_frame_transfer_lock_is_held(cp, payload) is True
    finally:
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)

    assert run_triage_module._vc_frame_transfer_lock_is_held(cp, payload) is False


def test_killed_dispatcher_child_lock_prevents_second_transfer(
    tmp_path: Path,
) -> None:
    """A vc-frame child survives its killed caller and remains authoritative."""

    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "triage": OUTCOME_FINALIZED,
            "triage_pending": True,
            "triage_reason": "exit_0_report_delivered",
            "triage_bucket": BUCKET_FINALIZED,
            "triage_verdict": VERDICT_FINALIZED,
            "triage_verdict_reason": "exit_0_report_delivered",
            "triage_verdict_degraded": "",
            "triage_attempt_started_at_ns": (
                time.time_ns() - run_triage_module._TRANSFER_CHILD_START_GRACE_NS - 1
            ),
        }
    )
    _write_json(meta, payload)
    lock = cp / "finished_runs" / "run-proof" / "transfer.lock"
    ready = tmp_path / "vc-frame-child-locked"
    child_code = """
import fcntl
import os
import pathlib
import sys
import time

lock = pathlib.Path(sys.argv[1])
ready = pathlib.Path(sys.argv[2])
lock.parent.mkdir(parents=True, exist_ok=True)
descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(descriptor, fcntl.LOCK_EX)
ready.write_text("locked", encoding="utf-8")
time.sleep(60)
"""
    parent_code = """
import subprocess
import sys
import time

child = subprocess.Popen(
    [sys.executable, "-c", sys.argv[1], sys.argv[2], sys.argv[3]],
    start_new_session=True,
)
print(child.pid, flush=True)
time.sleep(60)
"""
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            parent_code,
            child_code,
            str(lock),
            str(ready),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pid = 0
    try:
        assert parent.stdout is not None
        child_pid = int(parent.stdout.readline().strip())
        deadline = time.monotonic() + 5
        while not ready.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.is_file(), "vc-frame stand-in never acquired transfer.lock"

        parent.kill()
        parent.wait(timeout=5)
        assert parent.returncode == -signal.SIGKILL
        os.kill(child_pid, 0)

        runner = Runner()
        outcome = triage_finished_run(
            meta,
            live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
            runner,
        )

        assert outcome.outcome == OUTCOME_ERROR
        assert outcome.reason == "transfer_in_progress"
        assert runner.calls == []
        recovered = json.loads(meta.read_text(encoding="utf-8"))
        assert recovered["triage_pending"] is True
        assert recovered["triage_last_error"]["reason"] == "transfer_in_progress"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if child_pid:
            try:
                os.killpg(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if parent.stdout is not None:
            parent.stdout.close()
        if parent.stderr is not None:
            parent.stderr.close()


def test_recent_pending_intent_waits_for_child_before_retry(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "triage": OUTCOME_FINALIZED,
            "triage_pending": True,
            "triage_reason": "exit_0_report_delivered",
            "triage_bucket": BUCKET_FINALIZED,
            "triage_verdict": VERDICT_FINALIZED,
            "triage_verdict_reason": "exit_0_report_delivered",
            "triage_verdict_degraded": "",
            "triage_attempt_started_at_ns": time.time_ns(),
        }
    )
    _write_json(meta, payload)
    runner = Runner()

    outcome = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )

    assert outcome.outcome == OUTCOME_ERROR
    assert outcome.reason == "transfer_child_start_grace"
    assert runner.calls == []


def test_pending_legacy_bucket_proof_is_adopted_without_second_transfer(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "status": "report_missing",
            "exit_code": 137,
            "report": str(tmp_path / "missing-report.md"),
            "transcript": str(_tiny_transcript(tmp_path)),
            "triage": OUTCOME_NEEDS_ATTENTION,
            "triage_pending": True,
            "triage_reason": "exit_137_no_report_transcript_511b",
            "triage_bucket": BUCKET_NEEDS_ATTENTION,
            "triage_verdict": VERDICT_FAILED,
            "triage_verdict_reason": "exit_137_no_report_transcript_511b",
            "triage_verdict_degraded": "exit_code_only",
        }
    )
    _write_json(meta, payload)
    _materialize_v4_transfer(cp, payload, bucket="NeedsAttention")
    runner = Runner()

    outcome = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )

    assert outcome.outcome == OUTCOME_NEEDS_ATTENTION
    assert outcome.verdict == VERDICT_FAILED
    assert outcome.verdict_degraded == "exit_code_only"
    assert runner.calls == []
    recovered = json.loads(meta.read_text(encoding="utf-8"))
    assert recovered["triage_pending"] is False
    assert recovered["triage_transfer"]["bucket_session"] == BUCKET_NEEDS_ATTENTION


def test_pending_proof_projection_failure_records_error_without_unbound_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "triage": OUTCOME_FINALIZED,
            "triage_pending": True,
            "triage_reason": "exit_0_report_delivered",
            "triage_bucket": BUCKET_FINALIZED,
            "triage_verdict": VERDICT_FINALIZED,
            "triage_verdict_reason": "exit_0_report_delivered",
            "triage_verdict_degraded": "",
        }
    )
    _write_json(meta, payload)
    _materialize_v4_transfer(cp, payload)
    real_record = run_triage_module._record_receipt

    def fail_only_projection(*args: Any, **kwargs: Any) -> bool:
        if kwargs.get("proof") is not None:
            return False
        return real_record(*args, **kwargs)

    monkeypatch.setattr(run_triage_module, "_record_receipt", fail_only_projection)
    runner = Runner()

    outcome = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )

    assert outcome.outcome == OUTCOME_ERROR
    assert outcome.reason == "transfer_projection_persist_failed"
    assert outcome.bucket == BUCKET_FINALIZED
    recovered = json.loads(meta.read_text(encoding="utf-8"))
    assert recovered["triage_pending"] is False
    assert recovered["triage"] == OUTCOME_ERROR

    monkeypatch.setattr(run_triage_module, "_record_receipt", real_record)
    retry = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )

    assert retry.outcome == OUTCOME_FINALIZED
    assert runner.calls == []
    recovered = json.loads(meta.read_text(encoding="utf-8"))
    assert recovered["triage_pending"] is False
    assert recovered["triage_transfer"]["version"] == 4


def test_completed_receipt_is_idempotent_without_external_calls(tmp_path: Path) -> None:
    meta = write_meta(
        tmp_path,
        triage=OUTCOME_FINALIZED,
        triage_pending=False,
        triage_reason="exit_0_report_delivered",
        triage_bucket=BUCKET_FINALIZED,
        triage_verdict=VERDICT_FINALIZED,
        triage_verdict_reason="exit_0_report_delivered",
        triage_verdict_degraded="",
    )
    runner = Runner()

    outcome = triage_finished_run(meta, live_env(tmp_path), runner)

    assert outcome.outcome == OUTCOME_FINALIZED
    assert outcome.bucket == BUCKET_FINALIZED
    assert runner.calls == []


def test_canonical_completed_receipt_without_proof_is_not_returned_complete(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "triage": OUTCOME_FINALIZED,
            "triage_pending": False,
            "triage_reason": "exit_0_report_delivered",
            "triage_bucket": BUCKET_FINALIZED,
            "triage_verdict": VERDICT_FINALIZED,
            "triage_verdict_reason": "exit_0_report_delivered",
            "triage_verdict_degraded": "",
        }
    )
    _write_json(meta, payload)
    runner = Runner()

    outcome = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )

    assert outcome.outcome == OUTCOME_ERROR
    assert outcome.reason.startswith("transfer_proof_invalid:")
    assert len(runner.transfer_calls) == 1
    current = json.loads(meta.read_text(encoding="utf-8"))
    assert current["triage_pending"] is True
    assert "triage_transfer" not in current


def test_completed_receipt_adopts_existing_proof_without_external_calls(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "triage": OUTCOME_FINALIZED,
            "triage_pending": False,
            "triage_reason": "exit_0_report_delivered",
            "triage_bucket": BUCKET_FINALIZED,
            "triage_verdict": VERDICT_FINALIZED,
            "triage_verdict_reason": "exit_0_report_delivered",
            "triage_verdict_degraded": "",
        }
    )
    _write_json(meta, payload)
    _materialize_v4_transfer(cp, payload)
    runner = Runner()

    outcome = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )

    assert outcome.outcome == OUTCOME_FINALIZED
    assert runner.calls == []
    recovered = json.loads(meta.read_text(encoding="utf-8"))
    proof = load_vc_frame_transfer_proof(cp, recovered)
    assert recovered["triage_transfer_receipt"] == str(proof.receipt_path)
    assert recovered["triage_transfer"] == proof.projection()


def test_later_settlement_revision_never_accepts_stale_completed_bucket(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "settlement_revision": 1,
            "settlement_verdict": VERDICT_FINALIZED,
            "settlement_tui": "f",
            "triage": OUTCOME_FINALIZED,
            "triage_pending": False,
            "triage_reason": "exit_0_report_delivered",
            "triage_bucket": BUCKET_FINALIZED,
            "triage_verdict": VERDICT_FINALIZED,
            "triage_verdict_reason": "exit_0_report_delivered",
            "triage_verdict_degraded": "",
        }
    )
    _write_json(meta, payload)
    _materialize_v4_transfer(cp, payload)

    first = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        Runner(),
    )
    assert first.outcome == OUTCOME_FINALIZED
    bound = json.loads(meta.read_text(encoding="utf-8"))
    assert bound["triage_settlement_revision"] == 1
    assert bound["triage_transfer"]["settlement"] == {
        "revision": 1,
        "verdict": VERDICT_FINALIZED,
        "tui": "f",
    }

    bound.update(
        {
            "status": "failed",
            "exit_code": 9,
            "report": str(tmp_path / "missing-after-revision.md"),
            "transcript": str(_tiny_transcript(tmp_path)),
            "settlement_revision": 2,
            "settlement_verdict": VERDICT_FAILED,
            "settlement_tui": "x",
        }
    )
    _write_json(meta, bound)
    runner = Runner()

    revised = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )

    assert revised.outcome == OUTCOME_ERROR
    assert revised.outcome != first.outcome
    assert len(runner.transfer_calls) == 1
    current = json.loads(meta.read_text(encoding="utf-8"))
    assert current["triage_pending"] is True
    assert current["triage_settlement_revision"] == 2
    assert current["triage_settlement_verdict"] == VERDICT_FAILED
    assert current["triage_settlement_tui"] == "x"
    assert current["triage_last_error"]["reason"].startswith("transfer_proof_invalid:")


def test_concurrent_triage_observers_invoke_one_transfer(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    runner = Runner()
    start = threading.Barrier(3)
    outcomes: list[Any] = []

    class ProofRunner(Runner):
        def __call__(self, argv: Sequence[str]) -> Any:
            if list(argv)[1:2] == ["triage-run"] and "--help" not in argv:
                _materialize_v4_transfer(
                    cp,
                    json.loads(meta.read_text(encoding="utf-8")),
                )
            return super().__call__(argv)

    runner = ProofRunner()

    def observer() -> None:
        start.wait()
        outcomes.append(
            triage_finished_run(
                meta,
                live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
                runner,
            )
        )

    threads = [threading.Thread(target=observer) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert [outcome.outcome for outcome in outcomes] == [
        OUTCOME_FINALIZED,
        OUTCOME_FINALIZED,
    ]
    assert len(runner.transfer_calls) == 1


def test_bounded_sweep_recovers_pending_and_rejects_unproved_complete(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    pending_meta = _control_plane_meta(cp)
    pending = json.loads(pending_meta.read_text(encoding="utf-8"))
    pending.update(
        {
            "triage": OUTCOME_FINALIZED,
            "triage_pending": True,
            "triage_reason": "exit_0_report_delivered",
            "triage_bucket": BUCKET_FINALIZED,
            "triage_verdict": VERDICT_FINALIZED,
            "triage_verdict_reason": "exit_0_report_delivered",
            "triage_verdict_degraded": "",
        }
    )
    _write_json(pending_meta, pending)
    _materialize_v4_transfer(cp, pending)

    live_meta = cp / "runtime_runs" / "run-live" / "meta.json"
    _write_json(live_meta, {"run_id": "run-live", "status": "running"})
    completed_meta = cp / "runtime_runs" / "run-complete" / "meta.json"
    _write_json(
        completed_meta,
        {
            "run_id": "run-complete",
            "exit_code": 0,
            "triage": OUTCOME_FINALIZED,
            "triage_pending": False,
        },
    )
    runner = Runner()

    report = reconcile_untriaged_runs(
        cp,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
        scan_limit=8,
        attempt_limit=4,
    )

    assert report.scanned == 3
    assert report.attempted == 2
    assert report.ok is False
    assert [item.run_id for item in report.items] == ["run-complete", "run-proof"]
    assert report.items[0].outcome == OUTCOME_ERROR
    assert report.items[1].outcome == OUTCOME_FINALIZED
    assert len(runner.transfer_calls) == 1


def test_sweep_repairs_completed_receipt_with_unlinked_exact_proof(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "triage": OUTCOME_FINALIZED,
            "triage_pending": False,
            "triage_reason": "exit_0_report_delivered",
            "triage_bucket": BUCKET_FINALIZED,
            "triage_verdict": VERDICT_FINALIZED,
            "triage_verdict_reason": "exit_0_report_delivered",
            "triage_verdict_degraded": "",
        }
    )
    _write_json(meta, payload)
    _materialize_v4_transfer(cp, payload)
    runner = Runner()

    report = reconcile_untriaged_runs(
        cp,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )

    assert report.attempted == 1
    assert report.ok is True
    assert runner.calls == []
    recovered = json.loads(meta.read_text(encoding="utf-8"))
    assert recovered["triage_transfer"]["version"] == 4


def test_sweep_rejects_arbitrary_projection_and_repairs_from_exact_proof(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "triage": OUTCOME_FINALIZED,
            "triage_pending": False,
            "triage_reason": "exit_0_report_delivered",
            "triage_bucket": BUCKET_FINALIZED,
            "triage_verdict": VERDICT_FINALIZED,
            "triage_verdict_reason": "exit_0_report_delivered",
            "triage_verdict_degraded": "",
            "triage_transfer_receipt": "looks-linked-but-is-not",
            "triage_transfer": {"schema": "arbitrary.mapping"},
        }
    )
    _write_json(meta, payload)
    _materialize_v4_transfer(cp, payload)
    runner = Runner()

    report = reconcile_untriaged_runs(
        cp,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )

    assert report.attempted == 1
    assert report.ok is True
    assert runner.calls == []
    recovered = json.loads(meta.read_text(encoding="utf-8"))
    proof = load_vc_frame_transfer_proof(cp, recovered)
    assert recovered["triage_transfer_receipt"] == str(proof.receipt_path)
    assert recovered["triage_transfer"] == proof.projection()


def test_sweep_rejects_directory_alias_without_invoking_transfer(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    alias_meta = cp / "runtime_runs" / "alias-directory" / "meta.json"
    _write_json(
        alias_meta,
        json.loads(meta.read_text(encoding="utf-8")),
    )
    meta.unlink()
    runner = Runner()

    report = reconcile_untriaged_runs(
        cp,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )

    assert report.attempted == 0
    assert len(report.errors) == 1
    assert report.errors[0].run_id == "alias-directory"
    assert report.errors[0].reason.startswith("meta_unreadable:")
    assert runner.calls == []


def test_direct_runtime_alias_is_rejected_before_external_call(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    canonical_meta = _control_plane_meta(cp)
    alias_meta = cp / "runtime_runs" / "alias-directory" / "meta.json"
    _write_json(
        alias_meta,
        json.loads(canonical_meta.read_text(encoding="utf-8")),
    )
    runner = Runner()

    outcome = triage_finished_run(
        alias_meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        runner,
    )

    assert outcome.outcome == OUTCOME_ERROR
    assert outcome.reason == "runtime_meta_identity_mismatch"
    assert runner.calls == []


def test_repeated_small_sweeps_rotate_past_stable_raw_prefix(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    runtime_runs = cp / "runtime_runs"
    runtime_runs.mkdir(parents=True)
    _write_json(
        runtime_runs / "aaa-live" / "meta.json",
        {"run_id": "aaa-live", "status": "running"},
    )
    meta = _control_plane_meta(cp)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload.update(
        {
            "triage": OUTCOME_FINALIZED,
            "triage_pending": True,
            "triage_reason": "exit_0_report_delivered",
            "triage_bucket": BUCKET_FINALIZED,
            "triage_verdict": VERDICT_FINALIZED,
            "triage_verdict_reason": "exit_0_report_delivered",
            "triage_verdict_degraded": "",
        }
    )
    _write_json(meta, payload)
    _materialize_v4_transfer(cp, payload)
    runner = Runner()

    first = reconcile_untriaged_runs(cp, runner=runner, scan_limit=1, attempt_limit=1)
    second = reconcile_untriaged_runs(cp, runner=runner, scan_limit=1, attempt_limit=1)

    assert first.scanned == 1
    assert first.attempted == 0
    assert first.truncated is True
    assert second.scanned == 1
    assert second.attempted == 1
    assert second.items[0].run_id == "run-proof"
    assert second.items[0].outcome == OUTCOME_FINALIZED
    assert runner.calls == []


def test_settlement_waits_at_triage_pre_replace_and_preserves_full_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)
    current = json.loads(meta.read_text(encoding="utf-8"))
    current["terminal_writer_marker"] = "keep-me"
    _write_json(meta, current)

    replace_window = threading.Event()
    release_replace = threading.Event()
    settlement_lock_attempted = threading.Event()
    settlement_lock_acquired = threading.Event()
    real_replace = run_mutation_module.os.replace
    real_locks = run_mutation_module.run_mutation_locks

    def blocked_proof_replace(source: str, destination: str | Path) -> None:
        try:
            candidate = json.loads(Path(source).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            candidate = {}
        if "triage_transfer" in candidate and not replace_window.is_set():
            replace_window.set()
            assert release_replace.wait(5), "test did not release triage replace"
        real_replace(source, destination)

    @contextmanager
    def observed_locks(*args: Any, **kwargs: Any):
        settlement_thread = threading.current_thread().name == "settlement-writer"
        if settlement_thread:
            settlement_lock_attempted.set()
        with real_locks(*args, **kwargs):
            if settlement_thread:
                settlement_lock_acquired.set()
            yield

    monkeypatch.setattr(run_mutation_module.os, "replace", blocked_proof_replace)
    monkeypatch.setattr(run_mutation_module, "run_mutation_locks", observed_locks)

    class ProofRunner(Runner):
        def __call__(self, argv: Sequence[str]) -> Any:
            if list(argv)[1:2] == ["triage-run"] and "--help" not in argv:
                _materialize_v4_transfer(
                    cp, json.loads(meta.read_text(encoding="utf-8"))
                )
            return super().__call__(argv)

    outcomes: dict[str, Any] = {}

    def triage_writer() -> None:
        outcomes["triage"] = triage_finished_run(
            meta,
            live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
            ProofRunner(),
        )

    def settlement_writer() -> None:
        outcomes["settlement"] = persist_settlement_to_meta(
            meta,
            Settlement(
                verdict=SettlementVerdict.FINALIZED,
                reason="delivery_sealed",
                settled_at="2026-07-26T09:00:00+00:00",
            ),
            control_plane_root=cp,
            run_id="run-proof",
        )

    triage_thread = threading.Thread(target=triage_writer, name="triage-writer")
    triage_thread.start()
    assert replace_window.wait(5), "triage never reached its proof replace window"

    settlement_thread = threading.Thread(
        target=settlement_writer,
        name="settlement-writer",
    )
    settlement_thread.start()
    assert settlement_lock_attempted.wait(5)
    assert not settlement_lock_acquired.is_set()

    release_replace.set()
    triage_thread.join(timeout=5)
    settlement_thread.join(timeout=5)
    assert not triage_thread.is_alive()
    assert not settlement_thread.is_alive()
    assert outcomes["triage"].outcome == OUTCOME_FINALIZED
    assert outcomes["settlement"] is True

    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["terminal_writer_marker"] == "keep-me"
    assert payload["settlement_verdict"] == VERDICT_FINALIZED
    assert payload["settlement_tui"] == "f"
    assert payload["triage_transfer"]["version"] == 4
    assert payload["triage_transfer_receipt"]
    assert payload["triage_pending"] is False


def test_explicit_control_plane_requires_real_transfer_proof(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)

    outcome = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        Runner(),
    )

    assert outcome.outcome == OUTCOME_ERROR
    assert outcome.reason.startswith("transfer_proof_invalid:")
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["triage"] == OUTCOME_FINALIZED
    assert payload["triage_pending"] is True
    assert payload["triage_last_error"]["reason"].startswith("transfer_proof_invalid:")
    assert "triage_transfer" not in payload
    assert "triage_transfer_receipt" not in payload


def test_proof_link_never_clobbers_changed_terminal_identity(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    meta = _control_plane_meta(cp)

    class ChangedTerminalRunner(Runner):
        def __call__(self, argv: Sequence[str]) -> Any:
            if list(argv)[1:2] == ["triage-run"] and "--help" not in argv:
                current = json.loads(meta.read_text(encoding="utf-8"))
                _materialize_v4_transfer(cp, current)
                current["exit_code"] = 9
                current["terminal_revision"] = "newer-writer"
                _write_json(meta, current)
            return super().__call__(argv)

    outcome = triage_finished_run(
        meta,
        live_env(tmp_path, VIBECRAFTED_CONTROL_PLANE=str(cp)),
        ChangedTerminalRunner(),
    )

    assert outcome.outcome == OUTCOME_ERROR
    assert outcome.reason == "transfer_projection_persist_failed"
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["exit_code"] == 9
    assert payload["terminal_revision"] == "newer-writer"
    assert "triage_transfer" not in payload


# --------------------------------------------------------------------------
# G4 — Delivery-kernel three-axis branch
#
# When a kernel receipt is present, the drawer follows the orthogonal axes
# (execution / proof / delivery). Without a receipt the legacy 5-signal
# conjunction above is the whole story — those tests must stay green.
# --------------------------------------------------------------------------


def _axes(
    *,
    execution: str = "exited",
    proof: str = "passed",
    delivery: str = "unverified",
    corrupt: bool = False,
) -> KernelAxes:
    return KernelAxes(
        execution_state=execution,
        proof_state=proof,
        delivery_state=delivery,
        corrupt=corrupt,
    )


def test_kernel_sealed_is_finalized_even_with_tiny_transcript() -> None:
    """delivery=sealed is authority; legacy transcript size is irrelevant."""
    classification = classify_run(
        exit_code=1,
        run_state="failed",
        report_exists=False,
        report_bytes=0,
        transcript_bytes=TINY,
        kernel_axes=_axes(delivery="sealed", execution="exited", proof="passed"),
    )
    assert classification.verdict == VERDICT_FINALIZED
    assert classification.bucket_flag == "finalized"
    assert "sealed" in classification.reason


def test_kernel_proof_invalid_is_failed() -> None:
    classification = classify_run(
        0,
        "completed",
        True,
        512,
        BIG,
        kernel_axes=_axes(proof="invalid", delivery="unverified"),
    )
    assert classification.verdict == VERDICT_FAILED
    assert classification.bucket_flag == "failed"
    assert (
        "proof_invalid" in classification.reason or "invalid" in classification.reason
    )


def test_kernel_execution_failed_is_failed() -> None:
    classification = classify_run(
        0,
        "completed",
        True,
        512,
        BIG,
        kernel_axes=_axes(
            execution="failed", proof="undeclared", delivery="unverified"
        ),
    )
    assert classification.verdict == VERDICT_FAILED
    assert classification.bucket_flag == "failed"
    assert (
        "execution_failed" in classification.reason or "failed" in classification.reason
    )


def test_kernel_partial_axes_need_attention() -> None:
    """exited + passed + unverified is honest incompleteness, not a drawer lie."""
    classification = classify_run(
        0,
        "completed",
        True,
        512,
        BIG,
        kernel_axes=_axes(execution="exited", proof="passed", delivery="unverified"),
    )
    assert classification.verdict == VERDICT_NEEDS_ATTENTION
    assert classification.bucket_flag == "needs-attention"


def test_kernel_proof_failed_is_failed() -> None:
    classification = classify_run(
        0,
        "completed",
        True,
        512,
        BIG,
        kernel_axes=_axes(proof="failed", delivery="unverified"),
    )
    assert classification.verdict == VERDICT_FAILED


def test_no_kernel_receipt_keeps_legacy_conjunction() -> None:
    """Absence of axes is the pre-G4 path — sealed-or-not never enters."""
    # Same inputs as the legacy finalized matrix row.
    assert (
        classify_run(0, "completed", True, 512, BIG, kernel_axes=None).verdict
        == VERDICT_FINALIZED
    )
    # And the W0-A death still fails without axes.
    assert (
        classify_run(1, "report_missing", False, 0, TINY, kernel_axes=None).verdict
        == VERDICT_FAILED
    )


def test_corrupt_kernel_receipt_fails_closed_never_raises(tmp_path: Path) -> None:
    """Unreadable receipt body is not 'no receipt' — it is unreadable axes."""
    bad = tmp_path / "axes.json"
    bad.write_text("{not-json", encoding="utf-8")

    # Path to a broken JSON file.
    axes = read_kernel_axes({"delivery_axes": str(bad)})
    assert axes is not None
    assert axes.corrupt is True
    assert (
        classify_run(0, "completed", True, 512, BIG, kernel_axes=axes).verdict
        == VERDICT_NEEDS_ATTENTION
    )

    # Inline garbage is the same fail-closed shape.
    axes2 = read_kernel_axes({"delivery_axes": "{still-not-json"})
    assert axes2 is not None and axes2.corrupt is True
    assert (
        classify_run(0, "completed", True, 512, BIG, kernel_axes=axes2).verdict
        == VERDICT_NEEDS_ATTENTION
    )


def test_read_run_signals_picks_up_meta_axes(tmp_path: Path) -> None:
    """Lifecycle/ship write the three axis keys onto the run receipt."""
    report = tmp_path / "r.md"
    report.write_text(
        render_minimal_frontmatter(
            run_id="r1", agent="codex", skill="scaffold", status="completed"
        )
        + "body\n",
        encoding="utf-8",
    )
    # Tiny transcript would block legacy finalized; axes must win.
    transcript = tmp_path / "t.log"
    transcript.write_text("x" * TINY, encoding="utf-8")

    signals = read_run_signals(
        {
            "exit_code": 0,
            "status": "completed",
            "report": str(report),
            "transcript": str(transcript),
            "execution_state": "exited",
            "proof_state": "passed",
            "delivery_state": "sealed",
        }
    )
    assert signals.kernel_axes is not None
    assert signals.kernel_axes.delivery_state == "sealed"
    assert signals.classify().verdict == VERDICT_FINALIZED


def test_read_kernel_axes_absent_when_no_receipt() -> None:
    assert read_kernel_axes({"exit_code": 0, "status": "completed"}) is None


def test_nested_delivery_axes_dict_is_a_receipt() -> None:
    axes = read_kernel_axes(
        {
            "delivery_axes": {
                "execution_state": "exited",
                "proof_state": "passed",
                "delivery_state": "sealed",
            }
        }
    )
    assert axes is not None
    assert axes.delivery_state == "sealed"


def test_modern_caller_always_passes_explicit_bucket_for_every_verdict(
    tmp_path: Path,
) -> None:
    """vc-frame BucketKind::for_exit_code never yields Failed — only --bucket does.

    The modern path must therefore always ship an explicit --bucket flag for
    every confident verdict, including failed.
    """
    cases = [
        # sealed axes → finalized
        {
            "exit_code": 0,
            "status": "completed",
            "execution_state": "exited",
            "proof_state": "passed",
            "delivery_state": "sealed",
            "expected_bucket": "finalized",
        },
        # execution failed axes → failed
        {
            "exit_code": 1,
            "status": "failed",
            "report": str(tmp_path / "gone.md"),
            "transcript": str(_tiny_transcript(tmp_path)),
            "execution_state": "failed",
            "proof_state": "undeclared",
            "delivery_state": "unverified",
            "expected_bucket": "failed",
        },
        # partial axes → needs-attention
        {
            "exit_code": 0,
            "status": "completed",
            "execution_state": "exited",
            "proof_state": "passed",
            "delivery_state": "unverified",
            "expected_bucket": "needs-attention",
        },
    ]
    for case in cases:
        expected = case.pop("expected_bucket")
        meta = write_meta(tmp_path, **case)
        runner = Runner()
        triage_finished_run(meta, live_env(tmp_path), runner)
        assert runner.bucket_flag() == expected, case
        # And the flag is present on the argv, not merely in the receipt.
        transfer = runner.transfer_calls[0]
        assert "--bucket" in transfer
        assert transfer[transfer.index("--bucket") + 1] == expected
        # Reset meta file for next case (write_meta overwrites).
        meta.unlink(missing_ok=True)


def test_plan_argv_includes_bucket_for_failed_verdict(tmp_path: Path) -> None:
    """Direct plan.argv contract: failed is only reachable via explicit flag."""
    meta_payload = {
        "run_id": "run-fail",
        "exit_code": 1,
        "status": "failed",
        "report": str(tmp_path / "gone.md"),
        "transcript": str(_tiny_transcript(tmp_path)),
        "execution_state": "failed",
        "proof_state": "undeclared",
        "delivery_state": "unverified",
    }
    plan = plan_triage(meta_payload, make_env())
    assert plan.verdict == VERDICT_FAILED
    argv = plan.argv("/usr/bin/vc-frame", with_bucket=True)
    assert "--bucket" in argv
    assert argv[argv.index("--bucket") + 1] == "failed"


def test_plan_argv_infra_failure_uses_needs_attention_rail(tmp_path: Path) -> None:
    """Verdict is infra_failure; vc-frame still only has the three rail flags."""
    transcript = tmp_path / "overload.transcript.log"
    transcript.write_text("API 529 Overloaded\n", encoding="utf-8")
    plan = plan_triage(
        {
            "run_id": "run-529",
            "exit_code": 1,
            "status": "failed",
            "report": str(tmp_path / "gone.md"),
            "transcript": str(transcript),
        },
        make_env(),
    )
    assert plan.verdict == VERDICT_INFRA_FAILURE
    assert plan.bucket == BUCKET_NEEDS_ATTENTION
    argv = plan.argv("/usr/bin/vc-frame", with_bucket=True)
    assert argv[argv.index("--bucket") + 1] == "needs-attention"
