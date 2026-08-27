"""Execution-envelope gate: fail-closed qualification BEFORE any spawn.

Spec: docs/runtime/DELIVERY_PROOF_KERNEL_v1.md §7.1 (envelope), §11
(vc-dispatch ownership), §15 rows T08/T09, §5.3 (stale-checkout incident).
The supervisor must block a cut before the worker process exists when the
declared envelope contradicts the live checkout; absent envelope keeps the
legacy path unchanged; proof contract blocks travel opaquely.
"""

from __future__ import annotations

import hashlib
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from vibecrafted_core.delivery.model import ExecutionEnvelope
from vibecrafted_core.dispatch.model import STATE_FAILED, STATE_VERIFIED, Dispatch
from vibecrafted_core.dispatch.schema import DispatchSchemaError, parse_dispatch
from vibecrafted_core.dispatch.supervisor import CellRun, DispatchSupervisor
from vibecrafted_core.repository_claims import RepositoryClaimRegistry

FAST_AWAIT = "await = { poll_s = 0.02, timeout_min = 1.0 }"
ORIGIN_URL = "git@github.com:vetcoders/fixture.git"
ORIGIN_IDENTITY = "vetcoders/fixture"


@dataclass
class SpyLauncher:
    """Sentinel launcher: a blocked cut must never reach it."""

    launches: list[tuple[str, str]] = field(default_factory=list)

    def __call__(self, cut, prompt: str, kind: str) -> CellRun:
        self.launches.append((cut.id, kind))
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=False,
            error="spy launcher: spawn must not happen for a blocked cut",
        )


@dataclass
class BashCells:
    """Real-process launcher (bash echo cells) for admitted cuts."""

    reports_dir: Path
    launches: list[tuple[str, str]] = field(default_factory=list)

    def __call__(self, cut, prompt: str, kind: str) -> CellRun:
        self.launches.append((cut.id, kind))
        report_path = self.reports_dir / f"{cut.id}_{kind}_report.md"
        script = f"printf 'worker done\\n' > {shlex.quote(str(report_path))}"
        proc = subprocess.Popen(["bash", "-c", script])
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=True,
            run_id=f"bash-{cut.id}-{kind}",
            pid=proc.pid,
            report_path=str(report_path),
            proc=proc,
        )


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def init_git_repo(path: Path) -> None:
    path.mkdir(exist_ok=True)
    for args in (
        ["init", "-q"],
        ["config", "user.email", "agents@vetcoders.io"],
        ["config", "user.name", "fake"],
        ["remote", "add", "origin", ORIGIN_URL],
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    (path / "owned").mkdir(exist_ok=True)
    (path / "owned" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def sha256_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def envelope_toml(repo_dir: Path, brief: Path, **overrides: str) -> str:
    values = {
        "schema": "vibecrafted.execution-envelope.v1",
        "agent": "claude",
        "repo": ORIGIN_IDENTITY,
        "root": str(repo_dir.resolve()),
        "branch": _git(repo_dir, "branch", "--show-current"),
        "expected_head": _git(repo_dir, "rev-parse", "HEAD"),
        "upstream_ref": "origin/main",
        "dirty_policy": "living-tree-scoped",
        "baseline_status_digest": "sha256:" + hashlib.sha256(b"").hexdigest(),
        "brief_path": str(brief),
        "brief_sha256": sha256_digest(brief),
    }
    values.update(overrides)
    lines = ["[execution]"]
    lines += [f'{key} = "{value}"' for key, value in values.items()]
    lines.append("upstream_relation = { ahead = 0, behind = 0 }")
    lines.append('protected_paths = ["seed.txt"]')
    lines.append('owned_paths = ["owned/module.py", "tests/owned"]')
    return "\n".join(lines)


def dispatch_text(
    repo: Path, reports_dir: Path, envelope: str = "", extra: str = ""
) -> str:
    return f"""
schema = "vibecrafted.dispatch.v1"

[meta]
name = "envelope-gate"
repo = "{repo}"
reports_dir = "{reports_dir}"

[policy]
repair_rounds = 0
{FAST_AWAIT}

{envelope}

{extra}

[[cuts]]
id = "c1"
agent = "claude"
workflow = "implement"
prompt = "envelope gate cut"
  [[cuts.verify]]
  run = "echo ok"
  expect = {{ contains = "ok" }}
"""


@pytest.fixture()
def gate_env(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    brief = tmp_path / "brief.md"
    brief.write_text("# W5-b brief\n", encoding="utf-8")
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    artifacts = tmp_path / "artifacts"
    return {
        "repo": repo,
        "brief": brief,
        "reports": reports_dir,
        "artifacts": artifacts,
    }


def parse(env: dict[str, Path], envelope: str = "", extra: str = "") -> Dispatch:
    return parse_dispatch(
        dispatch_text(env["repo"], env["reports"], envelope, extra),
        base_dir=env["repo"].parent,
    )


# ---------------------------------------------------------------- schema


def test_envelope_block_parses_into_typed_model(gate_env: dict[str, Path]) -> None:
    dispatch = parse(gate_env, envelope_toml(gate_env["repo"], gate_env["brief"]))

    envelope = dispatch.envelope
    assert isinstance(envelope, ExecutionEnvelope)
    assert envelope.agent == "claude"
    assert envelope.repo == ORIGIN_IDENTITY
    assert envelope.dirty_policy == "living-tree-scoped"
    assert envelope.owned_paths == ("owned/module.py", "tests/owned")
    assert envelope.brief_sha256 == sha256_digest(gate_env["brief"])
    # Round-trip: the typed record reproduces its declared payload.
    payload = envelope.to_payload()
    assert ExecutionEnvelope.from_payload(payload) == envelope


def test_absent_envelope_keeps_legacy_path(gate_env: dict[str, Path]) -> None:
    dispatch = parse(gate_env)

    assert dispatch.envelope is None


def test_unknown_envelope_schema_fails_closed(gate_env: dict[str, Path]) -> None:
    envelope = envelope_toml(
        gate_env["repo"],
        gate_env["brief"],
        schema="vibecrafted.execution-envelope.v999",
    )

    with pytest.raises(DispatchSchemaError) as exc:
        parse(gate_env, envelope)

    assert any("execution" in error for error in exc.value.errors)
    assert any("v999" in error for error in exc.value.errors)


def test_malformed_envelope_fails_closed(gate_env: dict[str, Path]) -> None:
    envelope = envelope_toml(gate_env["repo"], gate_env["brief"])
    envelope += '\nweather_over_baltic = "sunny"'

    with pytest.raises(DispatchSchemaError) as exc:
        parse(gate_env, envelope)

    assert any("execution" in error for error in exc.value.errors)


def test_proof_block_transported_opaquely(gate_env: dict[str, Path]) -> None:
    extra = """
[proof]
schema = "vibecrafted.delivery-proof.v1"
id = "dpk-w5b-envelope-gate"
delivery_scope = "checkout"
unknowable_future_field = "must survive untouched"
  [proof.subject]
  producer_id = "vetcoders/vibecrafted"
  expected_exit = 0
"""
    dispatch = parse(
        gate_env, envelope_toml(gate_env["repo"], gate_env["brief"]), extra
    )

    assert dispatch.proof == {
        "schema": "vibecrafted.delivery-proof.v1",
        "id": "dpk-w5b-envelope-gate",
        "delivery_scope": "checkout",
        "unknowable_future_field": "must survive untouched",
        "subject": {"producer_id": "vetcoders/vibecrafted", "expected_exit": 0},
    }


def test_absent_proof_block_stays_none(gate_env: dict[str, Path]) -> None:
    dispatch = parse(gate_env)

    assert dispatch.proof is None


# ------------------------------------------------------------- supervisor


def run_gate(
    env: dict[str, Path], envelope: str, launcher
) -> tuple[DispatchSupervisor, object]:
    dispatch = parse(env, envelope)
    supervisor = DispatchSupervisor(
        dispatch,
        launcher=launcher,
        artifacts_dir=env["artifacts"],
        sleep=lambda _s: None,
    )
    return supervisor, supervisor.run()


def test_matching_envelope_admits_spawn(gate_env: dict[str, Path]) -> None:
    launcher = BashCells(reports_dir=gate_env["reports"])
    supervisor, result = run_gate(
        gate_env, envelope_toml(gate_env["repo"], gate_env["brief"]), launcher
    )

    assert launcher.launches == [("c1", "initial")]
    assert result.states["c1"] == STATE_VERIFIED
    receipt = supervisor._receipt_store.cut("c1")
    assert receipt["mutation_claim_id"]
    assert receipt["mutation_claim_released_at"]


def test_active_path_claim_refuses_dispatch_before_spawn_and_records_receipt(
    gate_env: dict[str, Path],
) -> None:
    registry = RepositoryClaimRegistry(
        root=gate_env["artifacts"] / "shared-claims", emit_events=False
    )
    incumbent = registry.acquire(
        repo=gate_env["repo"],
        owned_paths=("owned",),
        run_id="incumbent-run",
        session_id="incumbent-session",
        agent="codex",
    )
    dispatch = parse(gate_env, envelope_toml(gate_env["repo"], gate_env["brief"]))
    launcher = SpyLauncher()
    supervisor = DispatchSupervisor(
        dispatch,
        launcher=launcher,
        artifacts_dir=gate_env["artifacts"],
        sleep=lambda _s: None,
    )
    supervisor._claim_registry = registry

    result = supervisor.run()
    receipt = supervisor._receipt_store.cut("c1")

    assert launcher.launches == []
    assert result.states["c1"] == STATE_FAILED
    assert receipt["state"] == "failed"
    assert receipt["acceptance"] == "ownership-conflict"
    assert receipt["claim_conflicts"][0]["run_id"] == "incumbent-run"
    assert receipt["claim_conflicts"][0]["session_id"] == "incumbent-session"
    assert receipt["claim_conflicts"][0]["overlapping_paths"] == [
        {"requested": "owned/module.py", "owned": "owned"}
    ]
    assert incumbent["claim"]["claim_id"]


def test_brief_digest_mismatch_blocks_before_spawn(gate_env: dict[str, Path]) -> None:
    # T08: envelope digest computed first, then the brief mutates on disk.
    envelope = envelope_toml(gate_env["repo"], gate_env["brief"])
    declared = sha256_digest(gate_env["brief"])
    gate_env["brief"].write_text("# tampered brief\n", encoding="utf-8")
    observed = sha256_digest(gate_env["brief"])

    launcher = SpyLauncher()
    _supervisor, result = run_gate(gate_env, envelope, launcher)

    assert launcher.launches == []
    assert result.states["c1"] == STATE_FAILED
    note = result.cuts[0]["note"]
    assert "blocked" in note
    assert declared in note
    assert observed in note


@pytest.mark.parametrize(
    ("field_name", "declared"),
    [
        ("agent", "codex"),
        ("repo", "vetcoders/other-repo"),
        ("root", "/nonexistent/other-root"),
        ("branch", "feat/other-branch"),
        ("expected_head", "0" * 40),
    ],
)
def test_live_checkout_mismatch_blocks_before_spawn(
    gate_env: dict[str, Path], field_name: str, declared: str
) -> None:
    # T09: repo identity / root / branch / HEAD (and agent) must match the
    # live checkout or the run is blocked before the process exists.
    envelope = envelope_toml(
        gate_env["repo"], gate_env["brief"], **{field_name: declared}
    )

    launcher = SpyLauncher()
    _supervisor, result = run_gate(gate_env, envelope, launcher)

    assert launcher.launches == []
    assert result.states["c1"] == STATE_FAILED
    note = result.cuts[0]["note"]
    assert "blocked" in note
    assert declared in note  # declared value named in the reason
    assert "observed" in note  # observed value named alongside


def test_dirty_outside_owned_paths_admits_spawn(gate_env: dict[str, Path]) -> None:
    envelope = envelope_toml(gate_env["repo"], gate_env["brief"])
    (gate_env["repo"] / "unrelated-scratch.txt").write_text(
        "living tree\n", encoding="utf-8"
    )

    launcher = BashCells(reports_dir=gate_env["reports"])
    _supervisor, result = run_gate(gate_env, envelope, launcher)

    assert launcher.launches == [("c1", "initial")]
    assert result.states["c1"] == STATE_VERIFIED


def test_dirty_inside_owned_paths_blocks_before_spawn(
    gate_env: dict[str, Path],
) -> None:
    envelope = envelope_toml(gate_env["repo"], gate_env["brief"])
    (gate_env["repo"] / "owned" / "module.py").write_text(
        "VALUE = 2\n", encoding="utf-8"
    )

    launcher = SpyLauncher()
    _supervisor, result = run_gate(gate_env, envelope, launcher)

    assert launcher.launches == []
    assert result.states["c1"] == STATE_FAILED
    note = result.cuts[0]["note"]
    assert "blocked" in note
    assert "owned/module.py" in note


def test_blocked_state_is_recorded_in_tracker_not_swallowed(
    gate_env: dict[str, Path],
) -> None:
    envelope = envelope_toml(
        gate_env["repo"], gate_env["brief"], branch="feat/other-branch"
    )

    launcher = SpyLauncher()
    supervisor, result = run_gate(gate_env, envelope, launcher)

    tracker = supervisor.tracker_path.read_text(encoding="utf-8")
    assert "| c1 |" in tracker
    assert "[!]" in tracker
    assert "blocked" in tracker
    journal = supervisor.journal_path.read_text(encoding="utf-8")
    assert "blocked before spawn" in journal
    # The baton carries the blocked verdict as a first-class state, and the
    # dispatch line is not torn down by an exception.
    assert result.baton.states[0].state == STATE_FAILED
    assert result.line_broken is False
