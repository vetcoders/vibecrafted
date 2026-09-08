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
    WorktreeGeometry,
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


def test_public_concurrent_resumes_preserve_live_siblings_then_retry_killed_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a killed dirty child is recovered while its live siblings survive.

    This deliberately exercises the public dispatcher with three real provider
    processes.  The provider itself is disposable, but launch receipts,
    process identity, worktree reuse, and concurrent scheduler recovery are
    the production implementations.  Two initial children are held behind an
    owned barrier, so their original process identities must survive two
    concurrent resume callers.  The first recovered child is then killed;
    only that cut may advance to ``resume-2``.
    """
    home = tmp_path / ".vibecrafted"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    barriers = tmp_path / "barriers"
    barriers.mkdir()
    launches = tmp_path / "launches"
    launches.mkdir()
    provider = fake_bin / "codex"
    provider.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "root=$VIBECRAFTED_DISPATCH_WORKTREE\n"
        "cut=$VIBECRAFTED_DISPATCH_CUT_ID\n"
        "key=$VIBECRAFTED_LAUNCH_IDEMPOTENCY_KEY\n"
        "attempt=${key##*:attempt:}\n"
        'printf \'pid=%s run_id=%s key=%s\\n\' "$$" "$VIBECRAFTED_RUN_ID" "$key" > '
        '"$VIBECRAFTED_TEST_LAUNCH_DIR/$cut-$attempt-$VIBECRAFTED_RUN_ID.launch"\n'
        'printf \'%s\' "$key" > "$root/provider-key.txt"\n'
        "if echo \"$key\" | grep -q ':attempt:initial$'; then\n"
        '  printf dirty > "$root/owned-progress.txt"\n'
        '  if [ "$cut" = "killed" ]; then sleep 30; fi\n'
        '  while [ ! -f "$VIBECRAFTED_TEST_BARRIER_DIR/release-initial" ]; do sleep 0.02; done\n'
        '  if [ "$cut" != "killed" ]; then printf recovered > "$VIBECRAFTED_REPORT_PATH"; exit 0; fi\n'
        "  exit 9\n"
        "fi\n"
        'if [ "$cut" = "killed" ] && [ "$attempt" = "resume-1" ]; then\n'
        '  while [ ! -f "$VIBECRAFTED_TEST_BARRIER_DIR/release-resume-1-killed" ]; do sleep 0.02; done\n'
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
    monkeypatch.setenv("VIBECRAFTED_TEST_BARRIER_DIR", str(barriers))
    monkeypatch.setenv("VIBECRAFTED_TEST_LAUNCH_DIR", str(launches))
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
    owned_pgids: set[int] = set()
    try:
        deadline = time.monotonic() + 10
        initial_meta: dict[str, dict[str, object]] = {}
        while time.monotonic() < deadline:
            for meta_path in (home / "control_plane" / "runtime_runs").glob(
                "*/meta.json"
            ):
                candidate = json.loads(meta_path.read_text(encoding="utf-8"))
                cut = str(candidate.get("dispatch_cut_id") or "")
                if (
                    cut in {"killed", "retained", "retry"}
                    and candidate.get("dispatch_attempt") == "initial"
                    and isinstance(candidate.get("worker_pid"), int)
                    and Path(
                        str(candidate.get("root") or ""), "owned-progress.txt"
                    ).is_file()
                ):
                    initial_meta[cut] = candidate
            if len(initial_meta) == 3:
                break
            time.sleep(0.02)
        assert set(initial_meta) == {"killed", "retained", "retry"}
        for meta in initial_meta.values():
            pgid = meta.get("worker_pgid")
            assert isinstance(pgid, int)
            owned_pgids.add(pgid)

        killed_pgid = initial_meta["killed"]["worker_pgid"]
        assert isinstance(killed_pgid, int)
        os.killpg(killed_pgid, signal.SIGKILL)

        store = DispatchReceiptStore(run_id, dispatch.cuts, create=False)
        first = {cut: store.cut(cut) for cut in ("killed", "retained", "retry")}
        siblings = {cut: initial_meta[cut] for cut in ("retained", "retry")}
        for original in siblings.values():
            pid = original["worker_pid"]
            assert isinstance(pid, int)
            os.kill(pid, 0)

        def resume_once() -> dict[str, str]:
            return run_dispatch(
                dispatch,
                artifacts_dir=tmp_path / "artifacts",
                run_id=run_id,
                manage_worktrees=True,
                resume=True,
            ).states

        pool = ThreadPoolExecutor(max_workers=2)
        raced = [pool.submit(resume_once) for _ in range(2)]
        try:
            deadline = time.monotonic() + 10
            recovered: dict[str, object] | None = None
            while time.monotonic() < deadline:
                for meta_path in (home / "control_plane" / "runtime_runs").glob(
                    "*/meta.json"
                ):
                    candidate = json.loads(meta_path.read_text(encoding="utf-8"))
                    if (
                        candidate.get("dispatch_cut_id") == "killed"
                        and candidate.get("dispatch_attempt") == "resume-1"
                        and isinstance(candidate.get("worker_pid"), int)
                    ):
                        recovered = candidate
                        break
                if recovered is not None:
                    break
                time.sleep(0.02)
            assert recovered is not None, (
                "racing resumes did not admit the killed child"
            )
            recovered_pgid = recovered.get("worker_pgid")
            assert isinstance(recovered_pgid, int)
            owned_pgids.add(recovered_pgid)

            deadline = time.monotonic() + 10
            after_race: dict[str, dict[str, object]] = {}
            while time.monotonic() < deadline:
                after_race = {
                    cut: store.cut(cut) for cut in ("killed", "retained", "retry")
                }
                if after_race["killed"].get("attempt") == "resume-1":
                    break
                time.sleep(0.02)
            assert after_race["killed"]["attempt"] == "resume-1"
            assert (
                after_race["killed"]["provider_run_id"]
                != first["killed"]["provider_run_id"]
            )
            for cut, original in siblings.items():
                current = supervisor_module.lookup_run(
                    str(first[cut]["provider_run_id"])
                )
                pid = original["worker_pid"]
                assert isinstance(pid, int)
                os.kill(pid, 0)
                assert current.get("run_id") == original["run_id"]
                assert current.get("worker_identity") == original["worker_identity"]
                assert after_race[cut]["attempt"] == "initial"
                assert (
                    after_race[cut]["provider_run_id"] == first[cut]["provider_run_id"]
                )

            launch_files = list(launches.glob("*.launch"))
            assert (
                len([path for path in launch_files if path.name.startswith("killed-")])
                == 2
            )
            assert (
                len(
                    [path for path in launch_files if path.name.startswith("retained-")]
                )
                == 1
            )
            assert (
                len([path for path in launch_files if path.name.startswith("retry-")])
                == 1
            )

            os.killpg(recovered_pgid, signal.SIGKILL)
            (barriers / "release-resume-1-killed").touch()
            (barriers / "release-initial").touch()
            assert all(future.result(timeout=15) for future in raced)
        finally:
            # Never let an assertion strand this test's real provider children.
            (barriers / "release-resume-1-killed").touch()
            (barriers / "release-initial").touch()
            pool.shutdown(wait=True)

        initial.join(timeout=15)
        assert not initial.is_alive()
        assert len(initial_results) == 1

        final = resume_once()
        assert final == {"killed": "[x]", "retained": "[x]", "retry": "[x]"}
        killed = store.cut("killed")
        assert killed["attempt"] == "resume-2"
        assert killed["provider_run_id"] != after_race["killed"]["provider_run_id"]
        assert len(list(launches.glob("killed-*.launch"))) == 3
        assert len(list(launches.glob("retained-*.launch"))) == 1
        assert len(list(launches.glob("retry-*.launch"))) == 1
    finally:
        (barriers / "release-initial").touch()
        (barriers / "release-resume-1-killed").touch()
        for pgid in owned_pgids:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        initial.join(timeout=15)


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


def test_cross_day_legacy_resume_reuses_original_checkout_and_leaves_settled_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce the installed W0-c resume shape across a calendar boundary.

    The only launcher used here is a test double.  The old receipt, legacy
    launch-idempotency record, real linked checkouts, receipt restoration, and
    current-day supervisor are production code.  A successful resume must use
    the 2026_0907 checkout, retain dirty W0-c progress, and never relaunch its
    already-settled W0-a/W0-b siblings.
    """
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    repo = tmp_path / "repo"
    baseline = _repo(repo)
    dispatch = _dispatch(
        repo, _cut("W0-a") + _cut("W0-b") + _cut("W0-c"), concurrency=3
    )
    run_id = "life-ship-260907-234034-86089-implement-fleet"
    previous_day = WorktreeManager(repo, day="2026_0907")
    geometries = {
        cut_id: previous_day.prepare(cut_id, baseline)
        for cut_id in ("W0-a", "W0-b", "W0-c")
    }
    dirty_root = Path(geometries["W0-c"].worktree_path)
    (dirty_root / "owned-progress.txt").write_text("keep this work\n", encoding="utf-8")

    store = DispatchReceiptStore(run_id, dispatch.cuts, concurrency=3)
    for cut_id in ("W0-a", "W0-b"):
        geometry = geometries[cut_id]
        store.update(
            cut_id,
            "settled",
            acceptance="verified",
            delivered_commit_sha=baseline,
            worktree_path=geometry.worktree_path,
            target_path=geometry.target_path,
            artifact_path=geometry.artifact_path,
            branch=geometry.branch,
            baseline_sha=baseline,
        )

    provider_run_id = "impl-260907-234041-50924"
    key = f"dispatch:{run_id}:cut:W0-c:attempt:initial"
    historical = workflow.WorkflowLaunchSpec(
        agent="codex",
        mode="implement",
        skill="implement",
        prompt="",
        file="",
        runtime="headless",
        root=str(dirty_root),
    )
    workflow._write_launch_idempotency_record(
        key,
        {
            "run_id": provider_run_id,
            "agent": "codex",
            "skill": "implement",
            "root": str(dirty_root),
            "state": "dispatched",
            "accepted": True,
            "spec_digest": workflow._launch_spec_digest(historical),
            "receipt": {
                "accepted": True,
                "run_id": provider_run_id,
                "agent": "codex",
                "skill": "implement",
                "root": str(dirty_root),
                "idempotency_key": key,
                "spec": historical.to_payload(),
            },
        },
    )
    canonical = {
        "run_id": provider_run_id,
        "root": str(dirty_root),
        "cut_id": "W0-c",
        "branch": geometries["W0-c"].branch,
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
            "run_id": provider_run_id,
        },
        "state": "report_missing",
    }
    monkeypatch.setattr(
        workflow,
        "lookup_run",
        lambda observed: canonical if observed == provider_run_id else None,
    )
    monkeypatch.setattr(
        supervisor_module,
        "lookup_run",
        lambda observed: canonical if observed == provider_run_id else None,
    )
    geometry = geometries["W0-c"]
    store.update(
        "W0-c",
        "failed",
        provider_run_id=provider_run_id,
        worktree_path=geometry.worktree_path,
        target_path=geometry.target_path,
        artifact_path=geometry.artifact_path,
        branch=geometry.branch,
        baseline_sha=baseline,
    )

    launches: list[tuple[str, str, str]] = []

    def launcher(cut, _prompt: str, kind: str) -> CellRun:
        launches.append((cut.id, kind, str(cut.runtime_root)))
        report = tmp_path / f"{cut.id}-{kind}.md"
        proc = subprocess.Popen(
            ["sh", "-c", f"printf recovered > {shlex.quote(str(report))}"]
        )
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=True,
            run_id=f"new-{cut.id}",
            pid=proc.pid,
            report_path=str(report),
            proc=proc,
        )

    result = run_dispatch(
        dispatch,
        launcher=launcher,
        artifacts_dir=tmp_path / "artifacts",
        run_id=run_id,
        manage_worktrees=True,
        resume=True,
    )

    assert result.states == {"W0-a": "[x]", "W0-b": "[x]", "W0-c": "[x]"}
    assert launches == [("W0-c", "resume-1", str(dirty_root))]
    assert (dirty_root / "owned-progress.txt").read_text(
        encoding="utf-8"
    ) == "keep this work\n"
    assert not (WorktreeManager(repo).worktree_root / "W0-c").exists()


def test_cross_day_resume_refuses_legacy_receipt_with_nonancestor_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    baseline = _repo(repo)
    manager = WorktreeManager(repo, day="2026_0907")
    geometry = manager.prepare("W0-c", baseline)
    foreign = tmp_path / "foreign"
    _repo(foreign)
    (foreign / "foreign-only").write_text("foreign\n", encoding="utf-8")
    _git(foreign, "add", "foreign-only")
    _git(foreign, "commit", "-qm", "foreign baseline")
    foreign_baseline = _git(foreign, "rev-parse", "HEAD")
    forged = WorktreeGeometry(
        **{**geometry.to_dict(), "baseline_sha": foreign_baseline}
    )

    with pytest.raises(WorktreeContractError, match="baseline mismatch"):
        WorktreeManager(repo).recover_active(forged)


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
