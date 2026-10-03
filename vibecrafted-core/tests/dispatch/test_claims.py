"""Real writer admission and independently executed dispatch settlement."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from vibecrafted_core.dispatch.claims import claim_git_state, submit_claim
from vibecrafted_core.dispatch.model import (
    STATE_FAILED,
    STATE_UNKNOWN,
    STATE_VERIFIED,
    STATE_WORKER_DONE,
    Common,
    Cut,
    Dispatch,
    Meta,
    Policy,
    Verify,
)
from vibecrafted_core.dispatch.receipts import ReceiptContractError
from vibecrafted_core.dispatch.supervisor import CellRun, DispatchSupervisor


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def writer_fixture(tmp_path: Path, command: str = "echo green", *, embargo=False):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "claim fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / "source.txt").write_text("checkpoint\n")
    git(root, "add", "source.txt")
    git(root, "commit", "-qm", "fixture checkpoint")
    cut = Cut(
        id="w1",
        phase="build",
        agent="codex",
        workflow="implement",
        resolved_workflow="implement",
        runtime_root=str(root),
        compile_embargo=embargo,
        verify=(Verify(run=command, expect={"contains": "green"}),),
    )
    dispatch = Dispatch(
        schema="vibecrafted.dispatch.v1",
        meta=Meta(name="claims", repo=str(root)),
        policy=Policy(await_config={"poll_s": 0.01, "timeout_min": 1.0}),
        common=Common(),
        phases=(),
        cuts=(cut,),
    )
    supervisor = DispatchSupervisor(
        dispatch,
        launcher=lambda *args: None,
        artifacts_dir=tmp_path / "artifacts",
        run_id="dispatch-claim-test",
        manage_worktrees=False,
    )
    report = tmp_path / "report.md"
    report.write_text(
        "Measured the actual checkpoint.\n- [x] Worker sentence cannot settle.\n"
    )
    supervisor._receipt_store.update(
        "w1",
        "active",
        worktree_path=str(root),
        report_path=str(report),
        provider_run_id="worker-1",
        attempt="initial",
    )
    claim = {
        "run_id": supervisor.run_id,
        "cut_id": cut.id,
        "commit_sha": git(root, "rev-parse", "HEAD"),
        "report_path": str(report),
        "measurements": ["measured source.txt at checkpoint"],
    }
    supervisor._write_tracker()
    return supervisor, cut, claim, root


def test_claim_doorbell_does_not_settle_then_writer_executes_all_matchers(tmp_path):
    marker = tmp_path / "verifier-ran"
    supervisor, cut, claim, root = writer_fixture(
        tmp_path, f"pwd; touch '{marker}'; echo green"
    )
    before = supervisor.tracker_path.read_bytes()
    response = submit_claim(claim)
    assert response["marker"] == STATE_WORKER_DONE
    assert response["verification"] == "unverified"
    assert not marker.exists()
    assert supervisor.tracker_path.read_bytes() == before
    entry = supervisor._receipt_store.cut(cut.id)
    assert entry["claim_marker"] == STATE_WORKER_DONE
    assert entry["acceptance"] == "unverified"
    assert entry["state"] == "active"
    assert not entry.get("verification_rule")

    verdict = supervisor._verify(cut)
    assert marker.exists()
    assert verdict.state == STATE_VERIFIED
    assert str(root) in verdict.verifiers[0].evidence
    assert supervisor._record_cut_verdict(cut, verdict).ok
    supervisor._set_state(cut.id, verdict.state, supervisor._verdict_note(verdict))
    assert "| [x] |" in supervisor.tracker_path.read_text()
    proof = supervisor._receipt_store.cut(cut.id)["verification_rule"]
    assert proof["passed"] and proof["commit_sha"] == claim["commit_sha"]


@pytest.mark.parametrize("allow_red_baseline", [False, True])
def test_red_matcher_is_refuted_and_journal_has_verifier_cwd(
    tmp_path, allow_red_baseline
):
    supervisor, cut, claim, root = writer_fixture(tmp_path, "echo red")
    supervisor.dispatch = replace(
        supervisor.dispatch,
        meta=replace(
            supervisor.dispatch.meta,
            baseline={"allow_red_baseline": allow_red_baseline},
        ),
    )
    submit_claim(claim)
    verdict = supervisor._verify(cut)
    assert verdict.state == STATE_FAILED
    assert len(verdict.verifiers) == 1
    assert verdict.verifiers[0].matcher_result == "fail"
    supervisor._record_cut_verdict(cut, verdict)
    supervisor._set_state(cut.id, verdict.state, supervisor._verdict_note(verdict))
    assert "| [!] |" in supervisor.tracker_path.read_text()
    journal = supervisor.journal_path.read_text()
    assert f"verifier failure: cwd={root}" in journal
    assert not supervisor._receipt_store.cut(cut.id)["verification_rule"]["passed"]


def test_missing_claim_is_unknown_and_never_executes_verifiers(tmp_path):
    marker = tmp_path / "must-not-run"
    supervisor, cut, _claim, _root = writer_fixture(
        tmp_path, f"touch '{marker}'; echo green"
    )
    verdict = supervisor._verify(cut)
    assert verdict.state == STATE_UNKNOWN
    assert not verdict.verifiers and not marker.exists()
    journal = supervisor.journal_path.read_text()
    assert "claim not received, verifiers were not run" in journal
    assert (
        "vibecrafted dispatch" in journal and "--resume dispatch-claim-test" in journal
    )
    assert "verifier failure" not in journal and "acceptance gate" not in journal
    assert "| [~] |" not in supervisor.tracker_path.read_text()


def test_missing_claim_blocks_line_with_explicit_unrun_verification(tmp_path):
    supervisor, cut, _claim, _root = writer_fixture(tmp_path)
    second = replace(cut, id="w2", depends_on=("w1",))
    supervisor.dispatch = replace(
        supervisor.dispatch, cuts=(replace(cut, critical=True), second)
    )

    # Build a fresh writer for the two-cut DAG with a real worker process that
    # writes its report and intentionally never rings the doorbell.
    def launcher(active, prompt, kind):
        report = tmp_path / f"{active.id}.md"
        report.write_text("Worker says done; tracker [x] is only prose.\n")
        proc = subprocess.Popen(["bash", "-c", "true"])
        return CellRun(
            cut_id=active.id,
            kind=kind,
            accepted=True,
            proc=proc,
            pid=proc.pid,
            report_path=str(report),
            run_id="worker-missing",
        )

    writer = DispatchSupervisor(
        supervisor.dispatch,
        launcher=launcher,
        artifacts_dir=tmp_path / "dag-artifacts",
        run_id="dispatch-missing-claim",
        manage_worktrees=False,
    )
    result = writer.run()
    assert result.states["w1"] == STATE_UNKNOWN
    assert result.states["w2"] != STATE_VERIFIED
    assert (
        "claim not received, verifiers were not run" in writer.tracker_path.read_text()
    )
    assert "verifier failure" not in writer.journal_path.read_text()


@pytest.mark.parametrize("checkpoint", [False, True])
def test_worker_cannot_admit_a_stale_sha_or_dirty_tracked_tree(tmp_path, checkpoint):
    supervisor, cut, claim, root = writer_fixture(tmp_path)
    if checkpoint:
        claim["checkpoint"] = {
            "owned_scope": ["source.txt"],
            "skipped_controls": ["echo green"],
        }
    (root / "source.txt").write_text("uncommitted\n")
    with pytest.raises(ReceiptContractError, match="clean runtime HEAD"):
        submit_claim(claim)
    git(root, "add", "source.txt")
    git(root, "commit", "-qm", "advance")
    with pytest.raises(ReceiptContractError, match="clean runtime HEAD"):
        submit_claim(claim)
    assert not supervisor._receipt_store.cut(cut.id).get("claim")


def test_sha_drift_after_admission_cannot_settle(tmp_path):
    marker = tmp_path / "must-not-run"
    supervisor, cut, claim, root = writer_fixture(
        tmp_path, f"touch '{marker}'; echo green"
    )
    submit_claim(claim)
    (root / "source.txt").write_text("advance\n")
    git(root, "add", "source.txt")
    git(root, "commit", "-qm", "advance")
    assert supervisor._verify(cut).state == STATE_UNKNOWN
    assert not marker.exists()


@pytest.mark.parametrize("boundary", ["admission", "verification", "settlement"])
def test_untracked_implementation_cannot_settle_claimed_sha(tmp_path, boundary):
    supervisor, cut, claim, root = writer_fixture(tmp_path)
    if boundary != "admission":
        submit_claim(claim)
    if boundary == "settlement":
        verdict = supervisor._verify(cut)
        assert verdict.ok
    (root / "untracked-implementation.txt").write_text("green\n")
    if boundary == "admission":
        with pytest.raises(ReceiptContractError, match="clean runtime HEAD"):
            submit_claim(claim)
    elif boundary == "verification":
        assert supervisor._verify(cut).state == STATE_UNKNOWN
    else:
        assert supervisor._record_cut_verdict(cut, verdict).state == STATE_UNKNOWN
    assert supervisor._receipt_store.cut(cut.id)["state"] != "settled"
    assert "| [x] |" not in supervisor.tracker_path.read_text()


def test_ignored_generated_outputs_remain_verifiable(tmp_path):
    supervisor, cut, claim, root = writer_fixture(tmp_path)
    (root / ".gitignore").write_text("generated-output/\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-qm", "ignore generated test output")
    claim["commit_sha"] = git(root, "rev-parse", "HEAD")
    (root / "generated-output").mkdir()
    (root / "generated-output" / "result.txt").write_text("generated\n")
    submit_claim(claim)
    assert supervisor._record_cut_verdict(cut, supervisor._verify(cut)).ok


@pytest.mark.parametrize(
    "command",
    [
        "echo mutation >> source.txt; echo green",
        "echo green > untracked-implementation.txt; cat untracked-implementation.txt",
    ],
)
def test_runtime_mutation_by_verifier_cannot_settle(tmp_path, command):
    supervisor, cut, claim, _root = writer_fixture(tmp_path, command)
    submit_claim(claim)
    assert supervisor._verify(cut).state == STATE_UNKNOWN
    assert "runtime changed during verification" in supervisor.journal_path.read_text()


def test_new_claim_cannot_reuse_previous_green_measurements(tmp_path):
    supervisor, cut, claim, _root = writer_fixture(tmp_path)
    submit_claim(claim)
    old_green = supervisor._verify(cut)
    assert old_green.ok
    submit_claim(claim)
    assert supervisor._record_cut_verdict(cut, old_green).state == STATE_UNKNOWN
    assert supervisor._receipt_store.cut(cut.id)["acceptance"] == "unverified"


def test_worker_cannot_declare_compile_embargo_through_post(tmp_path):
    supervisor, cut, claim, _root = writer_fixture(tmp_path)
    claim["checkpoint"] = {
        "owned_scope": ["source.txt"],
        "skipped_controls": ["echo green"],
    }
    with pytest.raises(ReceiptContractError, match="plan-owned compile embargo"):
        submit_claim(claim)
    assert not supervisor._receipt_store.cut(cut.id).get("claim")


def test_late_claim_during_verification_invalidates_its_measurements(
    tmp_path, monkeypatch
):
    from vibecrafted_core.dispatch import supervisor as module

    writer, cut, claim, _root = writer_fixture(tmp_path)
    submit_claim(claim)
    execute = module.run_verifies

    def run_and_ring_again(*args, **kwargs):
        measured = execute(*args, **kwargs)
        assert measured.ok
        submit_claim(claim)
        return measured

    monkeypatch.setattr(module, "run_verifies", run_and_ring_again)
    assert writer._verify(cut).state == STATE_UNKNOWN
    assert not writer._receipt_store.cut(cut.id)["verification_rule"]
    assert "claim changed during verification" in writer.journal_path.read_text()


def test_embargo_checkpoint_stores_all_controls_without_running_any_gate(tmp_path):
    marker = tmp_path / "embargo-no-gates"
    supervisor, cut, claim, _root = writer_fixture(
        tmp_path, f"touch '{marker}'; echo green", embargo=True
    )
    claim["checkpoint"] = {
        "owned_scope": ["source.txt"],
        "skipped_controls": [
            "compiler",
            "pre-commit security scan",
            "secret detection",
        ],
    }
    assert submit_claim(claim)["marker"] == STATE_WORKER_DONE
    verdict = supervisor._verify(cut)
    assert verdict.state == STATE_WORKER_DONE
    assert not verdict.verifiers and not marker.exists()
    supervisor._record_cut_verdict(cut, verdict)
    entry = supervisor._receipt_store.cut(cut.id)
    assert (
        entry["claim"]["checkpoint"]["skipped_controls"]
        == claim["checkpoint"]["skipped_controls"]
    )
    assert entry["acceptance"] == "unverified"
    assert entry["state"] == "reported"
    assert not supervisor._settled_verdict(cut, entry)


@pytest.mark.parametrize(
    "extra", [{"state": "[x]"}, {"finalized": True}, {"verified": True}]
)
def test_claim_payload_cannot_choose_settlement(tmp_path, extra):
    supervisor, cut, claim, _root = writer_fixture(tmp_path)
    with pytest.raises(ReceiptContractError, match="unsupported claim fields"):
        submit_claim({**claim, **extra})
    assert not supervisor._receipt_store.cut(cut.id).get("claim")


def test_worker_cannot_close_embargo_in_checkpoint_post(tmp_path):
    _supervisor, _cut, claim, _root = writer_fixture(tmp_path)
    claim["checkpoint"] = {
        "owned_scope": ["source.txt"],
        "skipped_controls": ["security"],
        "W2_STRUCTURALLY_CLOSED": claim["commit_sha"],
    }
    with pytest.raises(ReceiptContractError, match="workers cannot close embargo"):
        submit_claim(claim)


def test_structural_cut_cannot_post_normal_claim(tmp_path):
    _supervisor, _cut, claim, _root = writer_fixture(tmp_path, embargo=True)
    with pytest.raises(
        ReceiptContractError, match="requires an unverified embargo checkpoint"
    ):
        submit_claim(claim)


@pytest.mark.parametrize(
    "bad", [{"run_id": "../outside"}, {"cut_id": "unknown"}, {"measurements": []}]
)
def test_invalid_or_unknown_claim_fails_closed(tmp_path, bad):
    supervisor, cut, claim, _root = writer_fixture(tmp_path)
    with pytest.raises(ReceiptContractError):
        submit_claim({**claim, **bad})
    assert not supervisor._receipt_store.cut(cut.id).get("claim")


def test_verifier_env_is_writer_owned_and_inline_toolchain_setting_works(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DEVELOPER_DIR", "worker-only-setting")
    supervisor, cut, claim, _root = writer_fixture(
        tmp_path,
        'test -z "$DEVELOPER_DIR" && DEVELOPER_DIR=inline bash -c \'test "$DEVELOPER_DIR" = inline\' && command -v rg && echo green',
    )
    submit_claim(claim)
    assert supervisor._verify(cut).ok


def test_writer_cli_records_claim_as_separate_process(tmp_path):
    supervisor, cut, claim, _root = writer_fixture(tmp_path)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    module_root = str(Path(__file__).resolve().parents[2])
    code = "import runpy,sys; sys.path.insert(0,sys.argv[1]); runpy.run_module('vibecrafted_core.dispatch.claims',run_name='__main__')"
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, module_root],
        input=json.dumps(claim),
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verification"] == "unverified"
    assert supervisor._receipt_store.cut(cut.id)["claim_writer_pid"] != os.getpid()
    assert claim_git_state(supervisor.repo)[0] == claim["commit_sha"]


@pytest.mark.parametrize("security_passes", [True, False])
def test_integrator_closes_embargo_only_after_all_deferred_security_matchers(
    tmp_path, monkeypatch, security_passes
):
    security_marker = tmp_path / "security-ran"
    command = f"touch '{security_marker}'; echo {'green' if security_passes else 'red'}"
    initial, worker, _claim, root = writer_fixture(tmp_path, command, embargo=True)
    integrator = replace(
        worker,
        id="w2",
        integrator=True,
        compile_embargo=False,
        closes_embargo=("w1",),
        depends_on=("w1",),
        verify=(Verify(run="echo green-integrator", expect={"contains": "green"}),),
    )
    dispatch = replace(initial.dispatch, cuts=(worker, integrator))
    launches = []

    def launcher(cut, prompt, kind):
        launches.append(cut.id)
        if cut.id == "w2":
            assert not security_marker.exists(), (
                "worker embargo must defer even security hooks"
            )
        report = tmp_path / f"{cut.id}-report.md"
        report.write_text("structural proof\n")
        proc = subprocess.Popen(["bash", "-c", "true"])
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            run_id=f"worker-{cut.id}",
            accepted=True,
            pid=proc.pid,
            proc=proc,
            report_path=str(report),
        )

    writer = DispatchSupervisor(
        dispatch,
        launcher=launcher,
        artifacts_dir=tmp_path / "closure-artifacts",
        run_id="dispatch-closure",
        manage_worktrees=False,
    )
    execute = writer._execute_cell

    def execute_with_claim(cut, prompt, kind):
        outcome, failure = execute(cut, prompt, kind)
        assert failure is None
        payload = {
            "run_id": writer.run_id,
            "cut_id": cut.id,
            "commit_sha": git(root, "rev-parse", "HEAD"),
            "report_path": outcome.report_path,
            "measurements": ["structural scope inspected"],
        }
        if cut.compile_embargo:
            payload["checkpoint"] = {
                "owned_scope": ["source.txt"],
                "skipped_controls": [command],
            }
        submit_claim(payload)
        return outcome, failure

    monkeypatch.setattr(writer, "_execute_cell", execute_with_claim)
    result = writer.run()
    assert launches == ["w1", "w2"]
    assert security_marker.exists()
    assert "W2_STRUCTURALLY_CLOSED" in writer.journal_path.read_text()
    if security_passes:
        assert result.states == {"w1": STATE_VERIFIED, "w2": STATE_VERIFIED}
        assert result.baton.verified == 2
        checkpoint = writer._receipt_store.cut("w1")
        assert checkpoint["embargo_closed_by"] == "w2"
        assert checkpoint["verification_rule"]["commit_sha"] == git(
            root, "rev-parse", "HEAD"
        )
        assert len(checkpoint["gates"]) == 2
    else:
        assert result.states == {"w1": STATE_WORKER_DONE, "w2": STATE_FAILED}
        assert result.baton.verified == 0
        assert writer._receipt_store.cut("w1")["acceptance"] == "unverified"
        assert f"verifier failure: cwd={root}" in writer.journal_path.read_text()


def test_skipped_security_hook_without_declared_verifier_cannot_close(tmp_path):
    initial, worker, claim, root = writer_fixture(tmp_path, embargo=True)
    claim["checkpoint"] = {
        "owned_scope": ["source.txt"],
        "skipped_controls": ["undeclared secret check"],
    }
    submit_claim(claim)
    initial._record_cut_verdict(worker, initial._verify(worker))
    integrator = replace(
        worker,
        id="w2",
        compile_embargo=False,
        integrator=True,
        closes_embargo=("w1",),
        depends_on=("w1",),
    )
    # A new canonical ledger carries both cuts for closure; copy only the
    # admitted structural claim by issuing another real POST to that ledger.
    dispatch = replace(initial.dispatch, cuts=(worker, integrator))
    writer = DispatchSupervisor(
        dispatch,
        launcher=lambda *args: None,
        artifacts_dir=tmp_path / "uncovered-artifacts",
        run_id="dispatch-uncovered",
        manage_worktrees=False,
    )
    report = Path(claim["report_path"])
    for cut in dispatch.cuts:
        writer._receipt_store.update(
            cut.id, "active", report_path=str(report), worktree_path=str(root)
        )
    submit_claim({**claim, "run_id": writer.run_id})
    writer._record_cut_verdict(worker, writer._verify(worker))
    del claim["checkpoint"]
    submit_claim({**claim, "run_id": writer.run_id, "cut_id": "w2"})
    verdict = writer._verify(integrator)
    assert verdict.state == STATE_UNKNOWN
    assert not verdict.verifiers
    assert "skipped controls lack declared verifiers" in writer.journal_path.read_text()
    assert not writer._receipt_store.cut("w2").get("structural_closure")
