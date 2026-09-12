from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
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


def test_resume_awaits_live_receipt_without_duplicate_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _repo(repo)
    dispatch = _dispatch(repo, _cut("resume-me"))
    report = tmp_path / "resume.md"
    proc = subprocess.Popen(
        ["bash", "-c", f"sleep 0.15; printf resumed > {shlex.quote(str(report))}"]
    )
    store = DispatchReceiptStore("resume-run", dispatch.cuts)
    store.update(
        "resume-me",
        "active",
        pid=proc.pid,
        provider_run_id="provider-existing",
        report_path=str(report),
    )
    launches = 0

    def forbidden_launcher(*_args):
        nonlocal launches
        launches += 1
        raise AssertionError("resume duplicated a live launch")

    result = run_dispatch(
        dispatch,
        launcher=forbidden_launcher,
        artifacts_dir=tmp_path / "artifacts",
        run_id="resume-run",
        resume=True,
    )
    assert launches == 0
    assert result.states["resume-me"] == "[x]"
    assert store.cut("resume-me")["state"] == "settled"


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
