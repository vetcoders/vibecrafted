"""Regressions for the 2026-09-17 dispatcher contract incident.

Field evidence (run ``disp-260917-223204-56520`` and the pensieve fleet):
- D1: repair/resume relaunch of a ``worktree`` cut fell back to Living Tree
  admission and died on the "--base differs from HEAD" guard.
- D2: resume rejected an intact delivered worktree because the worker had
  switched to a per-agent branch name (``active recovery branch mismatch``).
- D3: a failed ``critical = false`` cut stopped its dependents, breaking the
  plan's fail-open design.
- D4: supervisor-verify ran the host's bare ``python3`` (3.9, no tomllib) and
  killed a delivered cut without recording which interpreter it used.
- D5: agents invent dispatch subcommands (``preflight``/``launch``) that the
  CLI silently treated as file paths.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from vibecrafted_core.dispatch import supervisor as supervisor_module
from vibecrafted_core.dispatch.cli import main as dispatch_cli_main
from vibecrafted_core.dispatch.model import (
    STATE_FAILED,
    STATE_VERIFIED,
    Verdict,
)
from vibecrafted_core.dispatch.schema import (
    DispatchSchemaError,
    doctor_dispatch,
    parse_dispatch,
)
from vibecrafted_core.dispatch.supervisor import (
    CellRun,
    DispatchSupervisor,
    run_dispatch,
    workflow_cell_launcher,
)
from vibecrafted_core.dispatch.worktrees import (
    WorktreeContractError,
    WorktreeManager,
)

FAST_AWAIT = "await = { poll_s = 0.02, timeout_min = 1.0 }"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _seed_repo(path: Path) -> str:
    path.mkdir(exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "agents@vetcoders.io")
    _git(path, "config", "user.name", "contract-fix-test")
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "seed")
    return _git(path, "rev-parse", "HEAD")


def _commit(repo: Path, name: str) -> str:
    (repo / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _dispatch_text(repo: Path, cuts: str, *, policy: str = "") -> str:
    repo.mkdir(exist_ok=True)
    policy_text = policy or "repair_rounds = 0"
    if "await" not in policy_text:
        policy_text += f"\n{FAST_AWAIT}"
    return f"""
schema = "vibecrafted.dispatch.v1"

[meta]
name = "contract-fix"
repo = "{repo}"

[policy]
{policy_text}

{cuts}
"""


def _cut_toml(
    cut_id: str,
    *,
    verify_run: str = "echo ok",
    expect: str = "ok",
    depends_on: tuple[str, ...] = (),
    critical: bool = False,
) -> str:
    dependency = (
        "depends_on = [" + ", ".join(f'"{dep}"' for dep in depends_on) + "]\n"
        if depends_on
        else ""
    )
    return f"""[[cuts]]
id = "{cut_id}"
agent = "codex"
workflow = "implement"
critical = {str(critical).lower()}
{dependency}prompt = "run {cut_id}"
  [[cuts.verify]]
  run = "{verify_run}"
  expect = {{ contains = "{expect}" }}
"""


@dataclass
class ScriptCells:
    """Launcher double spawning real bash cells so the await path is real."""

    reports_dir: Path
    launches: list[tuple[str, str]] = field(default_factory=list)

    def __call__(self, cut, prompt: str, kind: str) -> CellRun:
        self.launches.append((cut.id, kind))
        report = self.reports_dir / f"{cut.id}_{kind}.md"
        script = f"printf 'worker done\\n' > {shlex.quote(str(report))}"
        proc = subprocess.Popen(["bash", "-c", script])
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=True,
            run_id=f"fake-{cut.id}-{kind}",
            pid=proc.pid,
            report_path=str(report),
            proc=proc,
        )


# --------------------------------------------------------------------- D1


def test_repair_relaunch_of_worktree_cut_keeps_local_worktrees_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worktree cut whose worker already committed relaunches without the
    Living Tree "--base differs from HEAD" guard killing the cell (D1)."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    baseline = _seed_repo(repo)
    manager = WorktreeManager(repo, day="2026_0918")
    geometry = manager.prepare("W1-04", baseline)
    worker_root = Path(geometry.worktree_path)
    # The worker delivered a commit: worktree HEAD is now AHEAD of the pinned
    # baseline — exactly the state every repair/resume relaunch starts from.
    delivered = _commit(worker_root, "delivered")
    assert delivered != baseline

    dispatch = parse_dispatch(
        _dispatch_text(repo, _cut_toml("W1-04", verify_run="echo ok"))
    )
    runtime_cut = replace(
        dispatch.cuts[0],
        runtime_root=str(worker_root),
        runtime_branch=geometry.branch,
        runtime_class="local-worktrees",
        baseline_sha=baseline,
    )
    captured: dict[str, object] = {}

    def fake_launch_workflow(spec, base_dir, *, env=None, launch_meta=None):
        captured["spec"] = spec
        return {
            "accepted": True,
            "run_id": "run-repair",
            "pid": 4242,
            "report": str(tmp_path / "report.md"),
            "meta": str(tmp_path / "meta.json"),
        }

    monkeypatch.setattr(supervisor_module, "launch_workflow", fake_launch_workflow)
    launcher = workflow_cell_launcher(dispatch, dispatch_run_id="disp-test")

    cell = launcher(runtime_cut, "repair prompt", "repair1")

    assert cell.accepted
    spec = captured["spec"]
    assert spec.runtime_class == "local-worktrees"
    # The dispatcher owns the prepared checkout; launch_workflow must not cut
    # a second nested worktree for the same cell.
    assert spec.worktree is False
    assert spec.baseline_sha == baseline
    assert Path(spec.root).resolve() == worker_root.resolve()


def test_launcher_falls_back_to_transient_branch_for_legacy_cuts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cuts restored from legacy receipts (no runtime_class stamp) still ride
    the worktree admission when their geometry says so (D1 fallback)."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    baseline = _seed_repo(repo)
    manager = WorktreeManager(repo, day="2026_0918")
    geometry = manager.prepare("W1-05", baseline)
    worker_root = Path(geometry.worktree_path)
    _commit(worker_root, "delivered")

    dispatch = parse_dispatch(_dispatch_text(repo, _cut_toml("W1-05")))
    legacy_cut = replace(
        dispatch.cuts[0],
        runtime_root=str(worker_root),
        runtime_branch=geometry.branch,
        baseline_sha=baseline,
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        supervisor_module,
        "launch_workflow",
        lambda spec, base_dir, *, env=None, launch_meta=None: (
            captured.__setitem__("spec", spec)
            or {"accepted": True, "run_id": "r", "pid": 1, "report": "", "meta": ""}
        ),
    )
    launcher = workflow_cell_launcher(dispatch, dispatch_run_id="disp-legacy")

    assert launcher(legacy_cut, "prompt", "repair1").accepted
    assert captured["spec"].runtime_class == "local-worktrees"


# --------------------------------------------------------------------- D2


def test_recover_active_adopts_foreign_branch_at_delivered_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker that switched to a per-agent branch is adopted back onto the
    contract branch ``cut/<id>`` at the same commit; the foreign ref and the
    committed work survive untouched (D2)."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    baseline = _seed_repo(repo)
    manager = WorktreeManager(repo, day="2026_0918")
    geometry = manager.prepare("W2-01", baseline)
    worker_root = Path(geometry.worktree_path)
    delivered = _commit(worker_root, "delivered")
    # Briefs teach per-agent branch naming; workers follow them.
    _git(worker_root, "checkout", "-q", "-b", "grok/workflow/distill-lane")

    manager.recover_active(geometry, delivered_sha=delivered)

    assert _git(worker_root, "branch", "--show-current") == geometry.branch
    assert _git(worker_root, "rev-parse", "HEAD") == delivered
    assert _git(worker_root, "rev-parse", geometry.branch) == delivered
    # Non-destructive: the worker's own branch name remains an extra ref.
    assert _git(worker_root, "rev-parse", "grok/workflow/distill-lane") == delivered


def test_recover_active_refuses_adoption_when_head_lacks_delivered_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    baseline = _seed_repo(repo)
    manager = WorktreeManager(repo, day="2026_0918")
    geometry = manager.prepare("W2-02", baseline)
    worker_root = Path(geometry.worktree_path)
    _commit(worker_root, "unrelated-progress")
    _git(worker_root, "checkout", "-q", "-b", "codex/workflow/other")
    # A commit only on the main checkout: visible object, not in the
    # worktree's ancestry — the receipt's delivery lives elsewhere.
    foreign_delivery = _commit(repo, "mainline")

    with pytest.raises(WorktreeContractError, match="refusing adoption"):
        manager.recover_active(geometry, delivered_sha=foreign_delivery)

    assert _git(worker_root, "branch", "--show-current") == "codex/workflow/other"


# --------------------------------------------------------------------- D3


def test_failed_noncritical_dependency_does_not_stop_dependents(
    tmp_path: Path,
) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    dispatch = parse_dispatch(
        _dispatch_text(
            tmp_path / "norepo",
            _cut_toml("a", verify_run="echo bad", expect="ok")
            + _cut_toml("b", depends_on=("a",)),
        )
    )
    cells = ScriptCells(reports)

    result = run_dispatch(
        dispatch, launcher=cells, artifacts_dir=tmp_path / "artifacts"
    )

    assert result.states["a"] == STATE_FAILED
    assert result.states["b"] == STATE_VERIFIED
    assert not result.line_broken
    assert ("b", "initial") in cells.launches
    journal = (tmp_path / "artifacts" / "journal.md").read_text(encoding="utf-8")
    assert "launching despite failed non-critical dependencies: a" in journal


def test_noncritical_dep_fail_stop_policy_restores_the_fence(
    tmp_path: Path,
) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    dispatch = parse_dispatch(
        _dispatch_text(
            tmp_path / "norepo",
            _cut_toml("a", verify_run="echo bad", expect="ok")
            + _cut_toml("b", depends_on=("a",)),
            policy=f'repair_rounds = 0\non_noncritical_dep_fail = "stop"\n{FAST_AWAIT}',
        )
    )
    cells = ScriptCells(reports)

    result = run_dispatch(
        dispatch, launcher=cells, artifacts_dir=tmp_path / "artifacts"
    )

    assert result.states["a"] == STATE_FAILED
    assert result.states["b"] == STATE_FAILED
    assert ("b", "initial") not in cells.launches


def test_failed_critical_dependency_still_stops_dependents(
    tmp_path: Path,
) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    dispatch = parse_dispatch(
        _dispatch_text(
            tmp_path / "norepo",
            _cut_toml("a", verify_run="echo bad", expect="ok", critical=True)
            + _cut_toml("b", depends_on=("a",)),
            policy=f'repair_rounds = 0\non_critical_fail = "continue"\n{FAST_AWAIT}',
        )
    )
    cells = ScriptCells(reports)

    result = run_dispatch(
        dispatch, launcher=cells, artifacts_dir=tmp_path / "artifacts"
    )

    assert result.states["a"] == STATE_FAILED
    assert result.states["b"] == STATE_FAILED
    assert ("b", "initial") not in cells.launches


def test_unsupported_noncritical_dep_fail_policy_is_a_schema_error(
    tmp_path: Path,
) -> None:
    with pytest.raises(DispatchSchemaError, match="on_noncritical_dep_fail"):
        parse_dispatch(
            _dispatch_text(
                tmp_path / "norepo",
                _cut_toml("a"),
                policy=f'on_noncritical_dep_fail = "shrug"\n{FAST_AWAIT}',
            )
        )


def test_baseline_falls_back_to_plan_head_when_every_dep_failed_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-open dependents build from the plan baseline instead of raising
    "dependencies supplied no delivered commit SHA" (D3 geometry leg)."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    head = _seed_repo(repo)
    dispatch = parse_dispatch(
        _dispatch_text(repo, _cut_toml("a") + _cut_toml("b", depends_on=("a",)))
    )
    supervisor = DispatchSupervisor(
        dispatch,
        artifacts_dir=tmp_path / "artifacts",
        manage_worktrees=True,
        run_id="disp-fallback-test",
    )
    failed = Verdict(cut_id="a", phase="", state=STATE_FAILED)

    selection = supervisor._baseline_for(dispatch.cuts[1], {"a": failed})

    assert selection.selected == head


# --------------------------------------------------------------------- D4


def test_failed_verifier_journals_interpreter_resolution(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    dispatch = parse_dispatch(
        _dispatch_text(
            tmp_path / "norepo",
            _cut_toml("a", verify_run="vc-missing-interp-xyz --self-test", expect="ok"),
        )
    )
    run_dispatch(
        dispatch, launcher=ScriptCells(reports), artifacts_dir=tmp_path / "artifacts"
    )

    journal = (tmp_path / "artifacts" / "journal.md").read_text(encoding="utf-8")
    assert "verifier interpreter: vc-missing-interp-xyz ->" in journal
    assert "not found on verifier PATH" in journal


def test_doctor_warns_on_bare_python3_verifier(tmp_path: Path) -> None:
    bare = doctor_dispatch(
        _dispatch_text(
            tmp_path / "norepo",
            _cut_toml("a", verify_run="python3 scripts/check.py", expect="ok"),
        )
    )
    assert bare.ok
    assert any("bare 'python3'" in warning for warning in bare.warnings)

    pinned = doctor_dispatch(
        _dispatch_text(
            tmp_path / "norepo",
            _cut_toml("a", verify_run="python3.13 scripts/check.py", expect="ok"),
        )
    )
    assert not any("bare 'python3'" in warning for warning in pinned.warnings)


# --------------------------------------------------------------------- D5


def test_cli_refuses_hallucinated_subcommands_with_pilot(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert dispatch_cli_main(["preflight", "plan.dispatch.toml"]) == 2
    err = capsys.readouterr().err
    assert "unknown dispatch subcommand 'preflight'" in err
    assert "plan.dispatch.toml --doctor" in err
    assert "canonical dispatch invocations" in err

    assert dispatch_cli_main(["launch", "plan.dispatch.toml"]) == 2
    err = capsys.readouterr().err
    assert "did you mean: vibecrafted dispatch plan.dispatch.toml" in err


def test_cli_still_accepts_a_plan_file_named_like_a_verb(tmp_path: Path) -> None:
    plan = tmp_path / "doctor"
    plan.write_text("not really toml", encoding="utf-8")
    # An existing file wins over the verb heuristic: the doctor path reports
    # its schema errors (exit 1), never the unknown-subcommand refusal (2).
    assert dispatch_cli_main([str(plan), "--doctor"]) == 1
