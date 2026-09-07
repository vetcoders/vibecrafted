from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from vibecrafted_core import workflow
from vibecrafted_core.dispatch import supervisor as supervisor_module
from vibecrafted_core.dispatch.doctor import diagnose_runtime
from vibecrafted_core.dispatch.receipts import (
    DispatchReceiptStore,
    IntegratorLease,
    ReceiptContractError,
)
from vibecrafted_core.dispatch.schema import (
    DispatchSchemaError,
    doctor_dispatch,
    parse_dispatch,
)
from vibecrafted_core.dispatch.supervisor import (
    CellRun,
    cleanup_settled_run,
    run_dispatch,
)
from vibecrafted_core.dispatch.worktrees import (
    WorktreeContractError,
    WorktreeManager,
    _same_filesystem_location,
    canonical_artifact_root,
)
from vibecrafted_core.report_contract import reserve_launcher_report_template
from vibecrafted_core.workflow import _canonical_report_path


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _repo(path: Path, *, rust: bool = False) -> str:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "agents@vetcoders.io")
    _git(path, "config", "user.name", "runtime-test")
    (path / ".gitignore").write_text("target/\n", encoding="utf-8")
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    if rust:
        (path / "src").mkdir()
        (path / "Cargo.toml").write_text(
            '[package]\nname = "same-binary"\nversion = "0.1.0"\nedition = "2021"\n',
            encoding="utf-8",
        )
        (path / "src" / "lib.rs").write_text(
            '#[test]\nfn isolate() { panic!("seed {}", env!("CARGO_MANIFEST_DIR")); }\n',
            encoding="utf-8",
        )
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "seed")
    return _git(path, "rev-parse", "HEAD")


def _dispatch(repo: Path, cuts: str, *, concurrency: int = 1):
    return parse_dispatch(
        f'''schema = "vibecrafted.dispatch.v1"
[meta]
name = "runtime-test"
repo = "{repo}"
[policy]
concurrency = {concurrency}
allow_concurrency = {str(concurrency > 1).lower()}
await = {{ poll_s = 0.01, timeout_min = 1.0 }}
{cuts}
'''
    )


def _cut(
    cut_id: str, *, depends_on: tuple[str, ...] = (), integrator: bool = False
) -> str:
    dependency = (
        f"depends_on = {list(depends_on)!r}\n".replace("'", '"') if depends_on else ""
    )
    return f'''[[cuts]]
id = "{cut_id}"
agent = "codex"
workflow = "implement"
integrator = {str(integrator).lower()}
{dependency}prompt = "run {cut_id}"
  [[cuts.verify]]
  run = "echo ok"
  expect = {{ contains = "ok" }}
'''


def test_filesystem_location_accepts_case_alias_on_case_insensitive_volume(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "VetCoders"
    canonical.mkdir()
    alias = tmp_path / "vetcoders"
    if not alias.exists():
        pytest.skip("test volume is case-sensitive")

    assert canonical.resolve() != alias.resolve()
    assert _same_filesystem_location(canonical, alias)


def test_rust_worktrees_never_share_mutable_cargo_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    baseline = _repo(repo, rust=True)
    manager = WorktreeManager(repo, day="2026_0811")
    first = manager.prepare("rust-a", baseline)
    second = manager.prepare("rust-b", baseline)

    roots = [Path(first.worktree_path), Path(second.worktree_path)]
    labels = ["alpha", "beta"]
    outputs: list[str] = []
    for root, label in zip(roots, labels, strict=True):
        (root / "src" / "lib.rs").write_text(
            f'#[test]\nfn isolate() {{ panic!("{label} {{}}", env!("CARGO_MANIFEST_DIR")); }}\n',
            encoding="utf-8",
        )
        _git(root, "add", "src/lib.rs")
        _git(root, "commit", "-q", "-m", f"{label} cut")
        proc = subprocess.run(
            ["cargo", "test", "isolate", "--", "--nocapture"],
            cwd=root,
            env={**os.environ, "CARGO_TARGET_DIR": str(root / "target")},
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode != 0
        outputs.append(proc.stdout + proc.stderr)

    assert str(roots[0]) in outputs[0] and "alpha" in outputs[0]
    assert str(roots[1]) not in outputs[0] and "beta" not in outputs[0]
    assert str(roots[1]) in outputs[1] and "beta" in outputs[1]
    assert str(roots[0]) not in outputs[1] and "alpha" not in outputs[1]
    assert Path(first.target_path).resolve() != Path(second.target_path).resolve()


def test_simultaneous_same_provider_reports_are_run_id_addressed_and_atomic(
    tmp_path: Path,
) -> None:
    report_dir = tmp_path / "reports" / "implement"

    def reserve(run_id: str) -> Path:
        path = _canonical_report_path(
            canonical_report_dir=report_dir,
            artifact_ts="2026-08-11",
            agent="codex",
            artifact_slug="same-provider",
            run_id=run_id,
        )
        reserve_launcher_report_template(
            path, run_id=run_id, agent="codex", skill="implement"
        )
        return path

    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(reserve, ("run-a", "run-b")))
    assert paths[0] != paths[1]
    assert all(path.is_file() for path in paths)
    with pytest.raises(FileExistsError):
        reserve("run-a")


def test_diamond_dag_overlaps_siblings_and_waits_for_join(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _repo(repo)
    dispatch = _dispatch(
        repo,
        _cut("a", integrator=True)
        + _cut("b", depends_on=("a",))
        + _cut("c", depends_on=("a",))
        + _cut("d", depends_on=("b", "c"), integrator=True),
        concurrency=2,
    )
    artifact_root = canonical_artifact_root(repo)
    reports = artifact_root / "reports" / "diamond"
    reports.mkdir(parents=True)
    launched: dict[str, float] = {}
    lock = threading.Lock()

    def launcher(cut, _prompt: str, kind: str) -> CellRun:
        with lock:
            launched[cut.id] = time.time()
        report = reports / f"{cut.id}.md"
        delay = 0.04 if cut.id in {"a", "d"} else 0.2
        script = f"sleep {delay}; printf done > {shlex.quote(str(report))}"
        proc = subprocess.Popen(["bash", "-c", script])
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=True,
            run_id=f"run-{cut.id}",
            pid=proc.pid,
            report_path=str(report),
            proc=proc,
        )

    result = run_dispatch(
        dispatch,
        launcher=launcher,
        artifacts_dir=artifact_root / "plans" / "dispatch" / "diamond",
        run_id="diamond",
        manage_worktrees=True,
    )

    assert all(state == "[x]" for state in result.states.values())
    assert abs(launched["b"] - launched["c"]) < 0.1
    assert launched["d"] >= max(
        (reports / "b.md").stat().st_mtime, (reports / "c.md").stat().st_mtime
    )
    receipts = DispatchReceiptStore("diamond", dispatch.cuts, concurrency=2)
    assert receipts.cut("b")["scheduler_slot"] != receipts.cut("c")["scheduler_slot"]
    assert receipts.cut("d")["integrator_exclusivity"] is True
    assert receipts.cut("b")["worktree_path"] != receipts.cut("c")["worktree_path"]
    assert receipts.cut("b")["target_path"].endswith("/b/target")
    assert receipts.cut("c")["target_path"].endswith("/c/target")


def test_public_dispatch_recovers_killed_worker_with_monotonic_resume_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise recovery through launch_workflow, its real receipt, and lookup_run.

    The fake provider is only the agent executable. The dispatch/supervisor and
    core runtime are unmocked: its first child leaves dirty work and fails, and
    each later public resume must mint one new attempt identity without losing
    that worktree or accepting the reserved report template as delivery.
    """
    home = tmp_path / ".vibecrafted"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    provider = fake_bin / "codex"
    provider.write_text(
        "#!/bin/sh\n"
        "root=$VIBECRAFTED_DISPATCH_WORKTREE\n"
        'if [ ! -f "$root/.interrupted" ]; then\n'
        '  touch "$root/.interrupted"\n'
        '  printf progress > "$root/owned-progress.txt"\n'
        "  exit 9\n"
        "fi\n"
        "printf 'recovered delivery' > \"$VIBECRAFTED_REPORT_PATH\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    provider.chmod(0o755)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_GUARD", "0")
    monkeypatch.setenv("VIBECRAFTED_REAPER", "0")
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(fake_bin))
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    repo = tmp_path / "repo"
    _repo(repo)
    dispatch = _dispatch(repo, _cut("recover"))
    run_id = "recover-public"

    failed = run_dispatch(
        dispatch,
        artifacts_dir=tmp_path / "artifacts",
        run_id=run_id,
        manage_worktrees=True,
    )
    assert failed.states["recover"] == "[!]"
    store = DispatchReceiptStore(run_id, dispatch.cuts, create=False)
    first = store.cut("recover")
    assert first["attempt"] == "initial"
    assert first["idempotency_key"].endswith(":attempt:initial")
    assert Path(first["report_path"]).read_text(encoding="utf-8").strip()
    assert Path(first["worktree_path"], "owned-progress.txt").read_text() == "progress"
    canonical = supervisor_module.lookup_run(first["provider_run_id"])
    assert isinstance(canonical, dict)
    assert canonical["worker_alive"] is False

    recovered = run_dispatch(
        dispatch,
        artifacts_dir=tmp_path / "artifacts",
        run_id=run_id,
        manage_worktrees=True,
        resume=True,
    )
    assert recovered.states["recover"] == "[x]"
    second = store.cut("recover")
    assert second["attempt"] == "resume-1"
    assert second["resume_attempt_sequence"] == 1
    assert second["idempotency_key"].endswith(":attempt:resume-1")
    assert second["provider_run_id"] != first["provider_run_id"]


def test_public_concurrent_resumes_replay_three_real_children_then_retry_failed_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A killed dirty child is recovered once even when two schedulers resume.

    This deliberately exercises the public dispatcher with three real provider
    processes.  The provider itself is disposable, but launch receipts,
    process identity, worktree reuse, and concurrent scheduler recovery are
    the production implementations.  ``retry`` fails its first resumed child
    so a later resume must advance to ``resume-2`` rather than replaying a
    terminal identity.
    """
    home = tmp_path / ".vibecrafted"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    provider = fake_bin / "codex"
    provider.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "root=$VIBECRAFTED_DISPATCH_WORKTREE\n"
        "cut=$VIBECRAFTED_DISPATCH_CUT_ID\n"
        "key=$VIBECRAFTED_LAUNCH_IDEMPOTENCY_KEY\n"
        'printf \'%s\' "$key" > "$root/provider-key.txt"\n'
        "if echo \"$key\" | grep -q ':attempt:initial$'; then\n"
        '  printf dirty > "$root/owned-progress.txt"\n'
        '  if [ "$cut" = "killed" ]; then sleep 30; fi\n'
        "  exit 9\n"
        "fi\n"
        'if [ "$cut" = "retry" ] && echo "$key" | grep -q \':attempt:resume-1$\'; then\n'
        '  printf second-dirty > "$root/owned-progress.txt"\n'
        "  exit 9\n"
        "fi\n"
        'printf recovered > "$VIBECRAFTED_REPORT_PATH"\n',
        encoding="utf-8",
    )
    provider.chmod(0o755)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_GUARD", "0")
    monkeypatch.setenv("VIBECRAFTED_REAPER", "0")
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(fake_bin))
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    repo = tmp_path / "repo"
    _repo(repo)
    dispatch = _dispatch(
        repo, _cut("killed") + _cut("retained") + _cut("retry"), concurrency=3
    )
    run_id = "concurrent-public-recovery"
    initial_results: list[dict[str, str]] = []

    def launch_initial() -> None:
        initial_results.append(
            run_dispatch(
                dispatch,
                artifacts_dir=tmp_path / "artifacts",
                run_id=run_id,
                manage_worktrees=True,
            ).states
        )

    initial = threading.Thread(target=launch_initial)
    initial.start()
    deadline = time.monotonic() + 10
    killed_meta: dict[str, object] | None = None
    while time.monotonic() < deadline:
        for meta_path in (home / "control_plane" / "runtime_runs").glob("*/meta.json"):
            candidate = json.loads(meta_path.read_text(encoding="utf-8"))
            if (
                candidate.get("dispatch_cut_id") == "killed"
                and isinstance(candidate.get("worker_pid"), int)
                and Path(
                    str(candidate.get("root") or ""), "owned-progress.txt"
                ).is_file()
            ):
                killed_meta = candidate
                break
        if killed_meta is not None:
            break
        time.sleep(0.02)
    assert killed_meta is not None, "the real killed provider was never admitted"
    worker_pid = killed_meta.get("worker_pid")
    worker_pgid = killed_meta.get("worker_pgid")
    assert isinstance(worker_pid, int) and isinstance(worker_pgid, int)
    os.killpg(worker_pgid, signal.SIGKILL)
    initial.join(timeout=15)
    assert not initial.is_alive()
    assert len(initial_results) == 1

    store = DispatchReceiptStore(run_id, dispatch.cuts, create=False)
    first = {cut: store.cut(cut) for cut in ("killed", "retained", "retry")}
    assert all(item["attempt"] == "initial" for item in first.values())
    assert all(
        Path(item["worktree_path"], "owned-progress.txt").is_file()
        for item in first.values()
    )
    assert all(
        supervisor_module.lookup_run(str(item["provider_run_id"])).get("worker_alive")
        is False
        for item in first.values()
    )

    def resume_once() -> dict[str, str]:
        result = run_dispatch(
            dispatch,
            artifacts_dir=tmp_path / "artifacts",
            run_id=run_id,
            manage_worktrees=True,
            resume=True,
        )
        return result.states

    with ThreadPoolExecutor(max_workers=2) as pool:
        raced = list(pool.map(lambda _ignored: resume_once(), range(2)))
    assert all(set(result) == {"killed", "retained", "retry"} for result in raced)
    after_race = {cut: store.cut(cut) for cut in ("killed", "retained", "retry")}
    assert all(item["attempt"] == "resume-1" for item in after_race.values())
    # Both callers share these exact recovery identities; no second child was minted.
    for cut, item in after_race.items():
        record = workflow._read_launch_idempotency_record(str(item["idempotency_key"]))
        assert record and record["run_id"] == item["provider_run_id"], cut
        assert item["provider_run_id"] != first[cut]["provider_run_id"]
    assert after_race["retry"]["state"] == "failed"

    final = resume_once()
    assert final == {"killed": "[x]", "retained": "[x]", "retry": "[x]"}
    retry = store.cut("retry")
    assert retry["attempt"] == "resume-2"
    assert retry["provider_run_id"] != after_race["retry"]["provider_run_id"]


def test_concurrent_resume_claims_share_one_attempt_then_advance_for_new_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _repo(repo)
    dispatch = _dispatch(repo, _cut("recover"))
    store = DispatchReceiptStore("resume-claim", dispatch.cuts)
    with ThreadPoolExecutor(max_workers=3) as pool:
        attempts = list(
            pool.map(
                lambda _ignored: store.claim_resume_attempt(
                    "recover", parent_run_id="failed-original"
                ),
                range(3),
            )
        )
    assert attempts == ["resume-1"] * 3
    assert (
        store.claim_resume_attempt("recover", parent_run_id="failed-resume-1")
        == "resume-2"
    )


def test_legacy_dispatch_identity_recovers_only_from_bound_historical_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The original W0-c shape had no dispatch_* metadata in its run record.

    Recovery derives identity from the exact launch-idempotency receipt, then
    requires the older canonical projection to agree on its worktree, branch,
    baseline, cut and terminated worker identity.  This is intentionally not a
    compatibility allowance for arbitrary legacy metadata.
    """
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    baseline = _repo(repo)
    dispatch = _dispatch(repo, _cut("W0-c"))
    manager = WorktreeManager(repo, day="2026_0907")
    geometry = manager.prepare("W0-c", baseline)
    prompt = tmp_path / "historical-prompt.md"
    prompt.write_text("preserved W0-c prompt\n", encoding="utf-8")
    run_id = "impl-260907-234041-50924"
    dispatch_run_id = "life-ship-260907-234034-86089-implement-fleet"
    key = f"dispatch:{dispatch_run_id}:cut:W0-c:attempt:initial"
    historical = workflow.WorkflowLaunchSpec(
        agent="codex",
        mode="implement",
        skill="implement",
        prompt="",
        file=str(prompt),
        runtime="headless",
        root=geometry.worktree_path,
        model="gpt-5.6-terra",
    )
    # This fixture is deliberately the old persisted format: launch receipt
    # has a spec digest and idempotency key, while canonical run metadata has
    # cut_id/branch/baseline but none of the later dispatch_* projection keys.
    workflow._write_launch_idempotency_record(
        key,
        {
            "run_id": run_id,
            "agent": "codex",
            "skill": "implement",
            "root": geometry.worktree_path,
            "state": "dispatched",
            "accepted": True,
            "spec_digest": workflow._launch_spec_digest(historical),
            "receipt": {
                "accepted": True,
                "run_id": run_id,
                "agent": "codex",
                "skill": "implement",
                "root": geometry.worktree_path,
                "idempotency_key": key,
                "spec": historical.to_payload(),
            },
        },
    )
    canonical = {
        "run_id": run_id,
        "root": geometry.worktree_path,
        "cut_id": "W0-c",
        "branch": geometry.branch,
        "baseline_sha": baseline,
        "agent": "codex",
        "skill": "implement",
        "worker_alive": False,
        "worker_pid": 66836,
        "worker_identity": {
            "pid": 66836,
            "pgid": 66836,
            "start_token": "start:83602922338560",
            "command_sha256": "4aefeb389b1fd968b989295da222f517d3243e83eb43fba74581e6e20cecb141",
            "run_id": run_id,
        },
        "state": "report_missing",
    }
    monkeypatch.setattr(
        workflow,
        "lookup_run",
        lambda observed: canonical if observed == run_id else None,
    )
    monkeypatch.setattr(
        supervisor_module,
        "lookup_run",
        lambda observed: canonical if observed == run_id else None,
    )
    recovered, reason = workflow.recover_legacy_dispatch_identity(
        workflow.WorkflowLaunchSpec(
            agent="codex",
            mode="implement",
            skill="implement",
            prompt="",
            file="",
            runtime="headless",
            root=geometry.worktree_path,
        ),
        env={workflow.LAUNCH_IDEMPOTENCY_KEY_ENV: key},
        provider_run_id=run_id,
        cut_id="W0-c",
        branch=geometry.branch,
        baseline_sha=baseline,
    )
    assert reason == ""
    assert recovered == {
        "provider_run_id": run_id,
        "idempotency_key": key,
        "spec_digest": workflow._launch_spec_digest(historical),
    }

    supervisor = supervisor_module.DispatchSupervisor(
        dispatch,
        artifacts_dir=tmp_path / "artifacts",
        run_id=dispatch_run_id,
        manage_worktrees=True,
        resume=False,
    )
    legacy_receipt = {"provider_run_id": run_id, "attempt": "initial"}
    runtime_cut = dispatch.cuts[0]
    runtime_cut = runtime_cut.__class__(
        **{
            **runtime_cut.__dict__,
            "runtime_root": geometry.worktree_path,
            "runtime_branch": geometry.branch,
            "baseline_sha": baseline,
        }
    )
    assert supervisor._authenticated_terminal_progress(runtime_cut, legacy_receipt)

    canonical["baseline_sha"] = "forged-baseline"
    denied, denied_reason = workflow.recover_legacy_dispatch_identity(
        historical,
        env={workflow.LAUNCH_IDEMPOTENCY_KEY_ENV: key},
        provider_run_id=run_id,
        cut_id="W0-c",
        branch=geometry.branch,
        baseline_sha=baseline,
    )
    assert denied is None
    assert "conflicts" in denied_reason


def test_unknown_resume_run_id_refuses_before_any_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _repo(repo)
    dispatch = _dispatch(repo, _cut("never-launch"))
    launches = 0

    def launcher(*_args):
        nonlocal launches
        launches += 1
        raise AssertionError("missing receipt must refuse before launch")

    with pytest.raises(ReceiptContractError, match="ledger not found"):
        run_dispatch(
            dispatch,
            launcher=launcher,
            artifacts_dir=tmp_path / "artifacts",
            run_id="unknown-run",
            resume=True,
        )
    assert launches == 0


def test_second_integrator_for_same_repo_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    first = IntegratorLease("org", "repo", "run-a", "join-a")
    second = IntegratorLease("org", "repo", "run-b", "join-b")
    first.acquire()
    try:
        with pytest.raises(
            ReceiptContractError, match="integrator exclusivity refused"
        ):
            second.acquire()
    finally:
        first.release()


def test_target_symlink_is_refused_by_launch_and_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    baseline = _repo(repo)
    manager = WorktreeManager(repo, day="2026_0811")
    geometry = manager.geometry("symlinked", baseline, integrator=False)
    root = Path(geometry.worktree_path)
    root.parent.mkdir(parents=True)
    _git(repo, "worktree", "add", "--quiet", "-b", geometry.branch, str(root), baseline)
    shared = repo / "target"
    shared.mkdir()
    (root / "target").symlink_to(shared, target_is_directory=True)

    with pytest.raises(WorktreeContractError, match="not a symlink"):
        manager.prepare("symlinked", baseline, allow_reuse=True)

    dispatch = _dispatch(repo, _cut("symlinked"))
    store = DispatchReceiptStore("symlink-doctor", dispatch.cuts)
    store.update(
        "symlinked",
        "active",
        worktree_path=str(root),
        target_path=str(root / "target"),
        artifact_path=str(manager.artifact_root),
        branch=geometry.branch,
        baseline_sha=baseline,
    )
    errors = diagnose_runtime(dispatch, run_id="symlink-doctor")
    assert any("target symlink is forbidden" in error.message for error in errors)


def test_cleanup_removes_only_settled_checkout_and_retains_durable_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    baseline = _repo(repo)
    dispatch = _dispatch(repo, _cut("done"))
    manager = WorktreeManager(repo, day="2026_0811")
    geometry = manager.prepare("done", baseline)
    report = manager.artifact_root / "reports" / "done.md"
    report.parent.mkdir(parents=True)
    report.write_text("durable evidence\n", encoding="utf-8")
    store = DispatchReceiptStore("cleanup-run", dispatch.cuts)
    geometry_receipt = geometry.to_dict()
    geometry_receipt.pop("cut_id")
    store.update(
        "done",
        "settled",
        **geometry_receipt,
        report_path=str(report),
        delivered_commit_sha=baseline,
    )

    outcome = cleanup_settled_run(dispatch, "cleanup-run")

    assert outcome == {"done": "removed"}
    assert not Path(geometry.worktree_path).exists()
    assert not Path(geometry.target_path).exists()
    assert report.is_file()
    assert store.path.is_file()
    assert store.cut("done")["cleanup_status"] == "removed"


def test_schema_rejects_dependency_cycle() -> None:
    with pytest.raises(DispatchSchemaError, match="dependency cycle"):
        _dispatch(
            Path("/tmp/repo"),
            _cut("a", depends_on=("b",)) + _cut("b", depends_on=("a",)),
            concurrency=2,
        )


def test_doctor_rejects_provider_roots_and_ambient_shared_cargo_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _repo(repo)
    text = f'''schema = "vibecrafted.dispatch.v1"
[meta]
repo = "{repo}"
reports_dir = "{repo}/.claude/reports"
[policy]
concurrency = 2
allow_concurrency = true
{_cut("a")}
'''
    monkeypatch.setenv("CARGO_TARGET_DIR", str(repo / "target"))
    result = doctor_dispatch(text)
    assert result.ok is False
    assert any("provider-specific" in error for error in result.errors)
    assert any("unset CARGO_TARGET_DIR" in error for error in result.errors)


def test_runtime_doctor_detects_duplicate_reports_integrators_and_serial_degradation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _repo(repo)
    dispatch = _dispatch(
        repo,
        _cut("join-a", integrator=True) + _cut("join-b", integrator=True),
        concurrency=2,
    )
    store = DispatchReceiptStore("doctor-run", dispatch.cuts, concurrency=2)
    report = canonical_artifact_root(repo) / "reports" / "duplicate.md"
    for cut_id in ("join-a", "join-b"):
        store.update(
            cut_id,
            "integrating",
            report_path=str(report),
            artifact_path=str(canonical_artifact_root(repo)),
            integrator_exclusivity=True,
        )
    payload = store.read()
    payload["scheduler_mode"] = "serial-only"
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    errors = diagnose_runtime(dispatch, run_id="doctor-run")
    messages = "\n".join(error.message for error in errors)
    assert "serial-only supervisor" in messages
    assert "duplicate report path" in messages
    assert "multiple active integrators" in messages
