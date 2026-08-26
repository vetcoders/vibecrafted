from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

import pytest
from vibecrafted_core import workflow
from vibecrafted_core.dispatch import supervisor as supervisor_module
from vibecrafted_core.dispatch.model import (
    STATE_FAILED,
    STATE_PENDING,
    STATE_UNKNOWN,
    STATE_VERIFIED,
    Dispatch,
)
from vibecrafted_core.dispatch.schema import parse_dispatch
from vibecrafted_core.dispatch.supervisor import (
    CellRun,
    DispatchSupervisor,
    run_dispatch,
    workflow_cell_launcher,
)

FAST_AWAIT = "await = { poll_s = 0.02, timeout_min = 1.0 }"


@dataclass
class FakeCell:
    """Echo-script work cell: bash side effects + a literal report body."""

    bash: str = ""
    report: str = "worker done"
    write_report: bool = True


@dataclass
class FakeCells:
    """Launcher double that spawns real bash processes instead of LLM cells,
    so the supervisor's process-handle await path is exercised for real."""

    reports_dir: Path
    cells: dict[tuple[str, str], FakeCell] = field(default_factory=dict)
    launches: list[tuple[str, str]] = field(default_factory=list)
    prompts: dict[tuple[str, str], str] = field(default_factory=dict)

    def __call__(self, cut, prompt: str, kind: str) -> CellRun:
        self.launches.append((cut.id, kind))
        self.prompts[(cut.id, kind)] = prompt
        cell = self.cells.get((cut.id, kind), FakeCell())
        report_path = self.reports_dir / f"{cut.id}_{kind}_report.md"
        script = cell.bash or "true"
        if cell.write_report:
            script += (
                f"\nprintf '%s\\n' {shlex.quote(cell.report)}"
                f" > {shlex.quote(str(report_path))}"
            )
        proc = subprocess.Popen(["bash", "-c", script])
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=True,
            run_id=f"fake-{cut.id}-{kind}",
            pid=proc.pid,
            report_path=str(report_path),
            proc=proc,
        )


def build_dispatch(
    tmp_path: Path, cuts_toml: str, *, repo: Path | None = None, policy: str = ""
) -> tuple[Dispatch, Path, Path]:
    repo_dir = repo if repo is not None else tmp_path / "repo"
    repo_dir.mkdir(exist_ok=True)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(exist_ok=True)
    artifacts_dir = tmp_path / "artifacts"
    policy_text = policy or "repair_rounds = 0"
    if "await" not in policy_text:
        policy_text += f"\n{FAST_AWAIT}"
    text = f"""
schema = "vibecrafted.dispatch.v1"

[meta]
name = "fake-dispatch"
repo = "{repo_dir}"
reports_dir = "{reports_dir}"

[policy]
{policy_text}

{cuts_toml}
"""
    return parse_dispatch(text, base_dir=tmp_path), reports_dir, artifacts_dir


def init_git_repo(path: Path) -> None:
    path.mkdir(exist_ok=True)
    for args in (
        ["init", "-q"],
        ["config", "user.email", "agents@vetcoders.io"],
        ["config", "user.name", "fake"],
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def test_workflow_cell_launcher_uses_canonical_report_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    dispatch, _reports_dir, _artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "c1"
agent = "codex"
workflow = "implement"
prompt = "canonical dispatch report prompt"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
    )
    captured: dict[str, object] = {}

    class FakeProc:
        def __init__(self, command: Any) -> None:
            self.args = command
            self.pid = 4242
            self.returncode = 0
            self.stdout = "/repo"
            self.stderr = ""

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def communicate(self, *args: object, **kwargs: object) -> tuple[str, str]:
            return ("/repo", "")

        def poll(self) -> int:
            return 0

        def wait(self) -> int:
            return 0

        def kill(self) -> None:
            pass

    def fake_popen(command: list[str], **kwargs: object) -> FakeProc:
        if "env" in kwargs:
            # The tracked launcher owns the child env. Identity capture may
            # subsequently invoke `ps` through the same monkeypatched
            # subprocess module; do not let that probe replace the command
            # this test is asserting.
            captured["command"] = command
            captured["env"] = kwargs["env"]
        return FakeProc(command)

    monkeypatch.setattr(workflow.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        workflow,
        "_resolve_agent_command",
        lambda _agent, command, _env: list(command),
    )
    # Hermetic: an ambient vc-frame on the host would turn the launcher's
    # liveness probe (added in 3d794af) into a real `list-sessions` subprocess
    # routed through fake_popen, which carries no env kwarg. Keep the probe
    # stubbed so this canonical-report test is independent of host vc-frame state.
    monkeypatch.setattr(workflow, "_vc_frame_session_active", lambda _vc, _s: False)

    run = workflow_cell_launcher(dispatch, source_dir=tmp_path)(
        dispatch.cuts[0],
        "canonical dispatch report prompt",
        "initial",
    )

    env = captured["env"]
    command = captured["command"]
    assert isinstance(env, dict)
    assert isinstance(command, list)
    assert run.accepted is True
    assert run.report_path == env["VIBECRAFTED_REPORT_PATH"]
    assert run.meta_path == env["VIBECRAFTED_META_PATH"]
    assert command[command.index("--report") + 1] == run.report_path
    assert "/artifacts/local/repo/" in run.report_path
    assert "/reports/implement/" in run.report_path
    assert "canonical-dispatch-report" in Path(run.report_path).name
    assert "/control_plane/runtime_runs/" not in run.report_path
    assert "/control_plane/runtime_runs/" in env["VIBECRAFTED_TRANSCRIPT_PATH"]
    assert "/control_plane/runtime_runs/" in env["VIBECRAFTED_META_PATH"]


def test_workflow_cell_launcher_carries_cut_model_pin_into_spec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dispatch, _reports_dir, _artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "c1"
agent = "codex"
workflow = "implement"
model = "test-codex-model"
prompt = "pinned cut"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }

[[cuts]]
id = "c2"
agent = "claude"
workflow = "implement"
prompt = "unpinned cut"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
    )
    captured: dict[str, object] = {}

    def fake_launch_workflow(spec, _base_dir, *, env=None):
        captured[spec.agent] = spec.model
        assert env is not None
        captured[f"{spec.agent}_idempotency"] = env.get(
            workflow.LAUNCH_IDEMPOTENCY_KEY_ENV
        )
        return {"accepted": True, "run_id": "r", "pid": 1, "report": ""}

    monkeypatch.setattr(supervisor_module, "launch_workflow", fake_launch_workflow)

    launch = workflow_cell_launcher(
        dispatch, source_dir=tmp_path, dispatch_run_id="dispatch-stable-1"
    )
    launch(dispatch.cuts[0], "pinned cut", "initial")
    launch(dispatch.cuts[1], "unpinned cut", "initial")

    # The pinned cut forwards its model into the launch spec; the unpinned
    # cut forwards an empty pin (account default is a deliberate non-decision).
    assert captured["codex"] == "test-codex-model"
    assert captured["claude"] == ""
    assert captured["codex_idempotency"] == (
        "dispatch:dispatch-stable-1:cut:c1:attempt:initial"
    )
    assert captured["claude_idempotency"] == (
        "dispatch:dispatch-stable-1:cut:c2:attempt:initial"
    )


def test_passing_cuts_flip_to_verified_and_emit_artifacts(tmp_path: Path) -> None:
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "c1"
agent = "claude"
workflow = "implement"
prompt = "do thing one"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }

[[cuts]]
id = "c2"
agent = "codex"
workflow = "implement"
prompt = "do thing two"
  [[cuts.verify]]
  run = "echo also ok"
  expect = { contains = "ok", exit_code = 0 }
""",
    )
    launcher = FakeCells(reports_dir=reports_dir)

    result = run_dispatch(dispatch, launcher=launcher, artifacts_dir=artifacts_dir)

    assert result.states == {"c1": STATE_VERIFIED, "c2": STATE_VERIFIED}
    assert result.line_broken is False
    assert launcher.launches == [("c1", "initial"), ("c2", "initial")]

    tracker = (artifacts_dir / "tracker.md").read_text(encoding="utf-8")
    assert tracker.count("[x]") == 2
    journal = (artifacts_dir / "journal.md").read_text(encoding="utf-8")
    assert "dispatch start" in journal and "dispatch end" in journal
    handoff = (artifacts_dir / "handoff.md").read_text(encoding="utf-8")
    assert "dou_index: 2/2" in handoff
    payload = json.loads(
        (artifacts_dir / "dispatch-result.json").read_text(encoding="utf-8")
    )
    assert payload["schema"] == "vibecrafted.dispatch-result.v1"
    assert payload["baton"]["dou_index"]["verified"] == 2
    assert [entry["id"] for entry in payload["cuts"]] == ["c1", "c2"]
    assert payload["cuts"][0]["state"] == STATE_VERIFIED
    assert payload["cuts"][1]["state"] == STATE_VERIFIED


def test_baton_flows_from_verified_cut_into_next_prompt(tmp_path: Path) -> None:
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "c1"
agent = "claude"
workflow = "implement"
prompt = "first"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }

[[cuts]]
id = "c2"
agent = "claude"
workflow = "implement"
prompt = "second consumes {baton}"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
    )
    launcher = FakeCells(reports_dir=reports_dir)

    run_dispatch(dispatch, launcher=launcher, artifacts_dir=artifacts_dir)

    second_prompt = launcher.prompts[("c2", "initial")]
    assert '"cut_id": "c1"' in second_prompt
    assert '"state": "[x]"' in second_prompt


def test_failed_verify_launches_repair_then_verifies(tmp_path: Path) -> None:
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "c1"
agent = "claude"
workflow = "implement"
prompt = "create the marker"
  [[cuts.verify]]
  run = "cat marker.txt"
  expect = { contains = "fixed" }
""",
        policy="repair_rounds = 1",
    )
    repo_dir = Path(dispatch.meta.repo)
    launcher = FakeCells(reports_dir=reports_dir)
    launcher.cells[("c1", "initial")] = FakeCell(bash="true")
    launcher.cells[("c1", "repair1")] = FakeCell(
        bash=f"echo fixed > {shlex.quote(str(repo_dir / 'marker.txt'))}"
    )

    result = run_dispatch(dispatch, launcher=launcher, artifacts_dir=artifacts_dir)

    assert result.states == {"c1": STATE_VERIFIED}
    assert launcher.launches == [("c1", "initial"), ("c1", "repair1")]
    assert result.baton.last is not None
    assert result.baton.last.repair_attempts == 1
    repair_prompt = launcher.prompts[("c1", "repair1")]
    assert "REPAIR ROUND" in repair_prompt
    assert "marker.txt" in repair_prompt


def test_critical_failure_breaks_line_and_skips_downstream(tmp_path: Path) -> None:
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "c1"
critical = true
agent = "claude"
workflow = "implement"
prompt = "doomed"
  [[cuts.verify]]
  run = "echo broken"
  expect = { contains = "never-there" }

[[cuts]]
id = "c2"
agent = "codex"
workflow = "implement"
prompt = "never runs"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
    )
    launcher = FakeCells(reports_dir=reports_dir)

    result = run_dispatch(dispatch, launcher=launcher, artifacts_dir=artifacts_dir)

    assert result.states == {"c1": STATE_FAILED, "c2": STATE_PENDING}
    assert result.line_broken is True
    assert launcher.launches == [("c1", "initial")]
    assert [(entry["id"], entry["state"]) for entry in result.cuts] == [
        ("c1", STATE_FAILED),
        ("c2", STATE_PENDING),
    ]
    tracker = (artifacts_dir / "tracker.md").read_text(encoding="utf-8")
    assert "skipped: line broken upstream" in tracker
    journal = (artifacts_dir / "journal.md").read_text(encoding="utf-8")
    assert "breaking the dispatch line" in journal


def test_read_cut_mutation_is_refuted_despite_green_verify(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    init_git_repo(repo_dir)
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "audit"
agent = "gemini"
workflow = "review"
mode = "read"
mutation = "forbid"
prompt = "look but do not touch"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
        repo=repo_dir,
    )
    launcher = FakeCells(reports_dir=reports_dir)
    launcher.cells[("audit", "initial")] = FakeCell(
        bash=f"echo dirty > {shlex.quote(str(repo_dir / 'mutated.txt'))}"
    )

    result = run_dispatch(dispatch, launcher=launcher, artifacts_dir=artifacts_dir)

    assert result.states == {"audit": STATE_FAILED}
    assert result.baton.last is not None
    assert any("mutated" in f or "mutation" in f for f in result.baton.last.failures)
    journal = (artifacts_dir / "journal.md").read_text(encoding="utf-8")
    assert "READ cut mutated the repository" in journal


def test_substrate_failure_in_report_refutes_cut(tmp_path: Path) -> None:
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "c1"
agent = "claude"
workflow = "implement"
prompt = "poisoned tree"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
    )
    launcher = FakeCells(reports_dir=reports_dir)
    launcher.cells[("c1", "initial")] = FakeCell(
        report="SUBSTRATE_FAILURE: tree poisoned by concurrent reset"
    )

    result = run_dispatch(dispatch, launcher=launcher, artifacts_dir=artifacts_dir)

    assert result.states == {"c1": STATE_FAILED}
    assert result.baton.last is not None
    assert any("SUBSTRATE_FAILURE" in f for f in result.baton.last.failures)


def test_launcher_exception_still_emits_result_and_handoff(tmp_path: Path) -> None:
    dispatch, _reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "boom"
critical = true
agent = "codex"
workflow = "implement"
prompt = "launcher crashes"
  [[cuts.verify]]
  run = "echo should-not-run"
  expect = { contains = "should-not-run" }

[[cuts]]
id = "downstream"
agent = "codex"
workflow = "implement"
prompt = "skipped after critical crash"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
    )

    def crashing_launcher(_cut, _prompt: str, _kind: str) -> CellRun:
        raise RuntimeError("launcher exploded before accepting the cell")

    result = run_dispatch(
        dispatch, launcher=crashing_launcher, artifacts_dir=artifacts_dir
    )

    assert result.line_broken is True
    assert result.states == {"boom": STATE_FAILED, "downstream": STATE_PENDING}
    payload = json.loads(
        (artifacts_dir / "dispatch-result.json").read_text(encoding="utf-8")
    )
    assert [(entry["id"], entry["state"]) for entry in payload["cuts"]] == [
        ("boom", STATE_FAILED),
        ("downstream", STATE_PENDING),
    ]
    handoff = (artifacts_dir / "handoff.md").read_text(encoding="utf-8")
    assert "launcher exploded before accepting the cell" in handoff
    journal = (artifacts_dir / "journal.md").read_text(encoding="utf-8")
    assert "dispatch end" in journal


def test_timeout_continue_marks_unknown_and_journals(tmp_path: Path) -> None:
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "slow"
agent = "claude"
workflow = "implement"
prompt = "sleeps forever"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
        policy=(
            "repair_rounds = 0\n"
            'on_timeout = "continue"\n'
            "await = { poll_s = 0.02, timeout_min = 0.005 }"
        ),
    )
    launcher = FakeCells(reports_dir=reports_dir)
    launcher.cells[("slow", "initial")] = FakeCell(bash="sleep 10", write_report=False)

    result = run_dispatch(dispatch, launcher=launcher, artifacts_dir=artifacts_dir)

    assert result.states == {"slow": STATE_UNKNOWN}
    journal = (artifacts_dir / "journal.md").read_text(encoding="utf-8")
    assert "timed out" in journal
    assert "process terminated" in journal


def test_broken_announced_report_recovers_by_mtime(tmp_path: Path) -> None:
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "c1"
agent = "claude"
workflow = "implement"
prompt = "writes report elsewhere"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
    )
    stray_report = reports_dir / "stray_actual_report.md"

    def broken_path_launcher(cut, prompt: str, kind: str) -> CellRun:
        proc = subprocess.Popen(
            [
                "bash",
                "-c",
                f"printf 'real report\\n' > {shlex.quote(str(stray_report))}",
            ]
        )
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=True,
            run_id="fake-broken",
            pid=proc.pid,
            report_path=str(tmp_path / "announced" / "never_written.md"),
            proc=proc,
        )

    result = run_dispatch(
        dispatch, launcher=broken_path_launcher, artifacts_dir=artifacts_dir
    )

    assert result.states == {"c1": STATE_VERIFIED}
    assert result.baton.last is not None
    assert result.baton.last.report == str(stray_report)
    journal = (artifacts_dir / "journal.md").read_text(encoding="utf-8")
    assert "recovered by mtime" in journal


def test_supervisor_default_artifacts_follow_tracker_path(tmp_path: Path) -> None:
    plans_dir = tmp_path / "plans"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    text = f"""
schema = "vibecrafted.dispatch.v1"

[meta]
name = "tracker-located"
repo = "{repo_dir}"
reports_dir = "{reports_dir}"
tracker = "{plans_dir / "tracker.md"}"

[policy]
repair_rounds = 0
{FAST_AWAIT}

[[cuts]]
id = "c1"
agent = "claude"
workflow = "implement"
prompt = "noop"
  [[cuts.verify]]
  run = "echo ok"
  expect = {{ contains = "ok" }}
"""
    dispatch = parse_dispatch(text, base_dir=tmp_path)
    launcher = FakeCells(reports_dir=reports_dir)
    supervisor = DispatchSupervisor(dispatch, launcher=launcher)

    result = supervisor.run()

    assert result.states == {"c1": STATE_VERIFIED}
    assert (plans_dir / "tracker.md").is_file()
    assert (plans_dir / "journal.md").is_file()
    assert (plans_dir / "dispatch-result.json").is_file()


def test_failed_runtime_meta_halts_noncritical_line_before_verify(
    tmp_path: Path,
) -> None:
    dispatch, _reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "quota-failed"
agent = "codex"
workflow = "implement"
prompt = "must not false-green"
  [[cuts.verify]]
  run = "printf verifier-ran > verifier.txt"
  expect = { exit_code = 0 }

[[cuts]]
id = "downstream"
agent = "codex"
workflow = "implement"
prompt = "must remain pending"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
    )
    meta_path = tmp_path / "failed-meta.json"
    meta_path.write_text(
        json.dumps({"status": "failed", "exit_code": 1}), encoding="utf-8"
    )

    def failed_launcher(cut, _prompt: str, kind: str) -> CellRun:
        proc = subprocess.Popen(["bash", "-c", "true"])
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=True,
            run_id="failed-runtime",
            pid=proc.pid,
            report_path=str(tmp_path / "missing-report.md"),
            meta_path=str(meta_path),
            proc=proc,
        )

    result = run_dispatch(
        dispatch, launcher=failed_launcher, artifacts_dir=artifacts_dir
    )

    assert result.line_broken is True
    assert result.states == {
        "quota-failed": STATE_FAILED,
        "downstream": STATE_PENDING,
    }
    assert not (Path(dispatch.meta.repo) / "verifier.txt").exists()
    journal = (artifacts_dir / "journal.md").read_text(encoding="utf-8")
    assert "runtime contract failed" in journal


def test_require_commit_blocks_green_verifier_on_unchanged_head(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    init_git_repo(repo_dir)
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "noop"
agent = "codex"
workflow = "implement"
prompt = "must commit"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
        repo=repo_dir,
        policy="repair_rounds = 0\nrequire_commit = true",
    )

    result = run_dispatch(
        dispatch,
        launcher=FakeCells(reports_dir=reports_dir),
        artifacts_dir=artifacts_dir,
    )

    assert result.line_broken is True
    assert result.states == {"noop": STATE_FAILED}
    assert result.baton.last is not None
    assert any("no new commit" in failure for failure in result.baton.last.failures)


def test_process_exit_one_with_report_cannot_false_green(tmp_path: Path) -> None:
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "exit-one"
agent = "codex"
workflow = "implement"
prompt = "must fail closed"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
    )
    report_path = reports_dir / "exit-one.md"
    report_path.write_text("worker report\n", encoding="utf-8")

    def exit_one_launcher(cut, _prompt: str, kind: str) -> CellRun:
        proc = subprocess.Popen(["bash", "-c", "exit 1"])
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=True,
            run_id="exit-one",
            pid=proc.pid,
            report_path=str(report_path),
            proc=proc,
        )

    result = run_dispatch(
        dispatch, launcher=exit_one_launcher, artifacts_dir=artifacts_dir
    )

    assert result.line_broken is True
    assert result.states == {"exit-one": STATE_FAILED}
    assert result.baton.last is not None
    assert any("exit_code=1" in failure for failure in result.baton.last.failures)


def test_production_envelope_fails_closed_when_meta_is_missing(tmp_path: Path) -> None:
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "missing-meta"
agent = "codex"
workflow = "implement"
prompt = "must fail closed"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
    )
    report_path = reports_dir / "present-report.md"
    report_path.write_text("worker report\n", encoding="utf-8")

    def missing_meta_launcher(cut, _prompt: str, kind: str) -> CellRun:
        proc = subprocess.Popen(["bash", "-c", "true"])
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=True,
            run_id="missing-meta",
            pid=proc.pid,
            report_path=str(report_path),
            meta_path=str(tmp_path / "missing-meta.json"),
            proc=proc,
        )

    result = run_dispatch(
        dispatch, launcher=missing_meta_launcher, artifacts_dir=artifacts_dir
    )

    assert result.line_broken is True
    assert result.states == {"missing-meta": STATE_FAILED}
    assert result.baton.last is not None
    assert any("meta missing or unreadable" in f for f in result.baton.last.failures)


def test_noncritical_launch_refusal_halts_downstream(tmp_path: Path) -> None:
    dispatch, _reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "refused"
agent = "codex"
workflow = "implement"
prompt = "refused"
  [[cuts.verify]]
  run = "echo should-not-run"
  expect = { contains = "should-not-run" }

[[cuts]]
id = "downstream"
agent = "codex"
workflow = "implement"
prompt = "must stay pending"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
    )
    launches: list[str] = []

    def refusing_launcher(cut, _prompt: str, kind: str) -> CellRun:
        launches.append(cut.id)
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=False,
            error="quota unavailable",
        )

    result = run_dispatch(
        dispatch, launcher=refusing_launcher, artifacts_dir=artifacts_dir
    )

    assert result.line_broken is True
    assert result.states == {"refused": STATE_FAILED, "downstream": STATE_PENDING}
    assert launches == ["refused"]


def test_uncommitted_repair_cannot_verify_prior_broken_commit(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    init_git_repo(repo_dir)
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "c1"
agent = "codex"
workflow = "implement"
prompt = "repair and commit"
  [[cuts.verify]]
  run = "grep -q fixed marker.txt"
  expect = { exit_code = 0 }
""",
        repo=repo_dir,
        policy="repair_rounds = 1\nrequire_commit = true",
    )
    marker = repo_dir / "marker.txt"
    launcher = FakeCells(reports_dir=reports_dir)
    launcher.cells[("c1", "initial")] = FakeCell(
        bash=(
            f"printf broken > {shlex.quote(str(marker))}\n"
            f"git -C {shlex.quote(str(repo_dir))} add marker.txt\n"
            f"git -C {shlex.quote(str(repo_dir))} commit -q -m '[c1] broken'"
        )
    )
    launcher.cells[("c1", "repair1")] = FakeCell(
        bash=f"printf fixed > {shlex.quote(str(marker))}"
    )

    result = run_dispatch(dispatch, launcher=launcher, artifacts_dir=artifacts_dir)

    assert result.line_broken is True
    assert result.states == {"c1": STATE_FAILED}
    assert result.baton.last is not None
    assert any("uncommitted changes" in f for f in result.baton.last.failures)


def test_committed_repair_verifies_clean_final_head(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    init_git_repo(repo_dir)
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "c1"
agent = "codex"
workflow = "implement"
prompt = "repair and commit"
  [[cuts.verify]]
  run = "grep -q fixed marker.txt"
  expect = { exit_code = 0 }
""",
        repo=repo_dir,
        policy="repair_rounds = 1\nrequire_commit = true",
    )
    marker = repo_dir / "marker.txt"
    launcher = FakeCells(reports_dir=reports_dir)
    launcher.cells[("c1", "initial")] = FakeCell(bash="true")
    launcher.cells[("c1", "repair1")] = FakeCell(
        bash=(
            f"printf fixed > {shlex.quote(str(marker))}\n"
            f"git -C {shlex.quote(str(repo_dir))} add marker.txt\n"
            f"git -C {shlex.quote(str(repo_dir))} commit -q -m '[c1] fixed'"
        )
    )

    result = run_dispatch(dispatch, launcher=launcher, artifacts_dir=artifacts_dir)

    assert result.line_broken is False
    assert result.states == {"c1": STATE_VERIFIED}
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )


@pytest.mark.parametrize("commit_key", ["commit", "commit_sha", "sha", "head_sha"])
@pytest.mark.parametrize("wrapper", ["", "'", '"', "`"])
def test_idempotent_existing_commit_proof_allows_noop_resume(
    tmp_path: Path, commit_key: str, wrapper: str
) -> None:
    repo_dir = tmp_path / "repo"
    init_git_repo(repo_dir)
    (repo_dir / "delivered.txt").write_text("done\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "delivered.txt"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "fix: delivered [c1]"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    delivered = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "c1"
agent = "codex"
workflow = "implement"
prompt = "verify existing delivery"
  [[cuts.verify]]
  run = "test -s delivered.txt"
  expect = { exit_code = 0 }
""",
        repo=repo_dir,
        policy="repair_rounds = 0\nrequire_commit = true",
    )
    launcher = FakeCells(reports_dir=reports_dir)
    launcher.cells[("c1", "initial")] = FakeCell(
        report=(
            f"---\n{commit_key}: {wrapper}{delivered}{wrapper}\n---\nalready delivered"
        )
    )

    result = run_dispatch(dispatch, launcher=launcher, artifacts_dir=artifacts_dir)

    assert result.line_broken is False
    assert result.states == {"c1": STATE_VERIFIED}
    assert result.baton.last is not None
    assert result.baton.last.commit == delivered


def test_failed_noncritical_verifier_without_commit_is_not_substrate_failure(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    init_git_repo(repo_dir)
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "ordinary-failure"
agent = "codex"
workflow = "implement"
prompt = "fails normally"
  [[cuts.verify]]
  run = "echo nope"
  expect = { contains = "missing" }
""",
        repo=repo_dir,
        policy="repair_rounds = 0\nrequire_commit = true",
    )

    marker = repo_dir / "uncommitted.txt"
    launcher = FakeCells(reports_dir=reports_dir)
    launcher.cells[("ordinary-failure", "initial")] = FakeCell(
        bash=f"printf dirty > {shlex.quote(str(marker))}"
    )

    result = run_dispatch(dispatch, launcher=launcher, artifacts_dir=artifacts_dir)

    assert result.line_broken is False
    assert result.states == {"ordinary-failure": STATE_FAILED}
    assert marker.read_text(encoding="utf-8") == "dirty"


def test_empty_report_cannot_false_green(tmp_path: Path) -> None:
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "empty-report"
agent = "codex"
workflow = "implement"
prompt = "must report"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
    )
    launcher = FakeCells(reports_dir=reports_dir)
    launcher.cells[("empty-report", "initial")] = FakeCell(report="")

    result = run_dispatch(dispatch, launcher=launcher, artifacts_dir=artifacts_dir)

    assert result.line_broken is True
    assert result.states == {"empty-report": STATE_FAILED}
    assert result.baton.last is not None
    assert any("report empty" in failure for failure in result.baton.last.failures)


def test_unrelated_new_commit_cannot_satisfy_cut_commit_gate(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    init_git_repo(repo_dir)
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "c1"
agent = "codex"
workflow = "implement"
prompt = "must identify the cut"
  [[cuts.verify]]
  run = "test -s unrelated.txt"
  expect = { exit_code = 0 }
""",
        repo=repo_dir,
        policy="repair_rounds = 0\nrequire_commit = true",
    )
    unrelated = repo_dir / "unrelated.txt"
    launcher = FakeCells(reports_dir=reports_dir)
    launcher.cells[("c1", "initial")] = FakeCell(
        bash=(
            f"printf unrelated > {shlex.quote(str(unrelated))}\n"
            f"git -C {shlex.quote(str(repo_dir))} add unrelated.txt\n"
            f"git -C {shlex.quote(str(repo_dir))} commit -q -m 'chore: unrelated'"
        )
    )

    result = run_dispatch(dispatch, launcher=launcher, artifacts_dir=artifacts_dir)

    assert result.line_broken is True
    assert result.states == {"c1": STATE_FAILED}
    assert result.baton.last is not None
    assert any("does not identify" in failure for failure in result.baton.last.failures)


def test_fleet_worktree_cut_delivery_commit_comes_from_cut_branch(
    tmp_path: Path,
) -> None:
    """Living Tree Rule v3, Mode B: a WRITE cut delivered in its own worktree on
    ``cut/<id>`` never moves the main checkout's HEAD. The supervisor must judge
    and record the CUT BRANCH tip, not the baseline HEAD.

    Field bug (2026-08-10, stt-live-first-v2): the tracker showed the baseline
    sha as w1-b's evidence while the real delivery commit sat on
    ``cut/w1-b-apple-utterance-eater``; with ``require_commit = true`` the same
    blindness would have refused the green cut outright ("no new commit").
    """
    repo_dir = tmp_path / "repo"
    init_git_repo(repo_dir)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    worktree_bash = (
        f"cd {shlex.quote(str(repo_dir))}"
        " && git worktree add -q -b cut/wt-cut .claude/worktrees/wt-cut"
        " && cd .claude/worktrees/wt-cut"
        " && printf 'delivered\\n' > delivered.txt"
        " && git add delivered.txt"
        " && git -c user.email=agents@vetcoders.io -c user.name=fake"
        " commit -qm '[codex/vc-implement] feat: wt-cut delivered'"
    )
    dispatch, reports_dir, artifacts_dir = build_dispatch(
        tmp_path,
        """
[[cuts]]
id = "wt-cut"
agent = "codex"
workflow = "implement"
prompt = "deliver in a fleet worktree"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
""",
        repo=repo_dir,
        policy="repair_rounds = 0\nrequire_commit = true",
    )
    cells = FakeCells(reports_dir=reports_dir)
    cells.cells[("wt-cut", "initial")] = FakeCell(bash=worktree_bash)

    result = run_dispatch(dispatch, launcher=cells, artifacts_dir=artifacts_dir)

    branch_tip = subprocess.run(
        ["git", "rev-parse", "cut/wt-cut"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert branch_tip != baseline
    assert result.states == {"wt-cut": STATE_VERIFIED}
    assert result.cuts[0]["commit"] == branch_tip
