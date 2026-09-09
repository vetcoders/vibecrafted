"""Typed dataclass model for ``vibecrafted.dispatch.v1``: plan, cut, verify, and baton state."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import InitVar, dataclass, field
from typing import Any

from vibecrafted_core.delivery.model import ExecutionEnvelope

SCHEMA_VERSION = "vibecrafted.dispatch.v1"
MATCHER_TYPES = {"contains", "equals", "matches", "not_contains", "exit_code"}
READ_MUTATIONS = {"forbid", "allow-report-only", "allow"}
TIMEOUT_POLICIES = {"repair", "fail", "continue"}
CRITICAL_FAIL_POLICIES = {"break", "continue"}
STATE_PENDING = "[ ]"
STATE_WORKER_DONE = "[~]"
STATE_UNKNOWN = "[?]"
STATE_FAILED = "[!]"
STATE_VERIFIED = "[x]"
SCHEDULER_STATES = {
    "queued",
    "launching",
    "active",
    "reported",
    "verified",
    "integrating",
    "settled",
    "failed",
    "stopped",
}


@dataclass(frozen=True)
class Meta:
    """Dispatch identity and location: repo, name, baseline, and artifact paths."""

    name: str
    repo: str
    description: str = ""
    baseline: dict[str, Any] = field(default_factory=dict)
    reports_dir: str = ""
    tracker: str = ""


@dataclass(frozen=True)
class Policy:
    """Run-wide behavior: repair rounds, failure/timeout handling, concurrency, commit rules."""

    repair_rounds: int = 0
    on_critical_fail: str = "break"
    on_timeout: str = "fail"
    concurrency: int = 1
    await_config: dict[str, Any] = field(default_factory=dict)
    verify_executor: str = "supervisor"
    allow_concurrency: bool = False
    require_commit: bool = False
    allow_idempotent_existing: bool = True


@dataclass(frozen=True)
class Common:
    """Shared prompt text prepended to every cut's rendered prompt."""

    text: str = ""


@dataclass(frozen=True)
class Phase:
    """A named grouping cuts can belong to and recovery can target."""

    title: str
    detail: str = ""


@dataclass(frozen=True)
class Matcher:
    """One expectation checked against a verifier command's output/exit code."""

    kind: str
    expected: str | int

    def check(self, output: str, *, exit_code: int | None = None) -> bool:
        """Evaluate this matcher's ``kind`` against captured output/exit code."""
        if self.kind == "contains":
            return str(self.expected) in output
        if self.kind == "equals":
            return output.strip() == str(self.expected)
        if self.kind == "matches":
            return re.search(str(self.expected), output) is not None
        if self.kind == "not_contains":
            return str(self.expected) not in output
        if self.kind == "exit_code":
            return exit_code == self.expected
        raise ValueError(f"unsupported matcher kind {self.kind!r}")


def matchers_from_expect(expect: Mapping[str, str | int]) -> tuple[Matcher, ...]:
    """Convert an ``expect`` mapping (kind -> expected value) into ``Matcher`` tuples."""
    matchers: list[Matcher] = []
    for kind, expected in expect.items():
        if kind not in MATCHER_TYPES:
            raise ValueError(f"unsupported matcher kind {kind!r}")
        if kind == "exit_code":
            expected = int(expected)
        matchers.append(Matcher(kind=kind, expected=expected))
    return tuple(matchers)


@dataclass(frozen=True)
class Verify:
    """One verifier command plus the matchers its output/exit code must satisfy."""

    run: str
    matchers: tuple[Matcher, ...] = ()
    # Dispatch YAML and ad-hoc callers declare matchers as an `expect`
    # mapping (kind -> expected); it folds into `matchers` at init.
    expect: InitVar[Mapping[str, str | int] | None] = None

    def __post_init__(self, expect: Mapping[str, str | int] | None) -> None:
        """Fold a legacy ``expect`` mapping into ``matchers`` at construction time."""
        if expect:
            object.__setattr__(
                self, "matchers", self.matchers + matchers_from_expect(expect)
            )


@dataclass(frozen=True)
class Recovery:
    """Declared retry target: jump to a cut/phase on a named condition, bounded by max_loops."""

    on: str = ""
    goto: str = ""
    max_loops: int | None = None


@dataclass(frozen=True)
class Cut:
    """One unit of dispatched work: a prompt/brief, target workflow/agent, and its verifiers."""

    id: str
    phase: str
    agent: str
    workflow: str
    resolved_workflow: str
    critical: bool = False
    mode: str = "write"
    model: str = ""
    model_source: str = "provider_default"
    source_text: str | None = None
    source_digest: str = ""
    prompt: str = ""
    brief: str = ""
    extra: str = ""
    mutation: str = ""
    observational: bool = False
    verify: tuple[Verify, ...] = ()
    recovery: Recovery | None = None
    depends_on: tuple[str, ...] = ()
    integrator: bool = False
    # Runtime-resolved fields are never accepted from dispatch TOML. The
    # supervisor stamps them after it has created and validated the linked
    # checkout for this specific run.
    runtime_root: str = ""
    runtime_branch: str = ""
    baseline_sha: str = ""
    target_path: str = ""
    artifact_path: str = ""
    scheduler_slot: int = 0


@dataclass(frozen=True)
class VerifierEvidence:
    """Recorded outcome of running one verifier command: exit code, excerpt, timing."""

    command: str
    ok: bool
    exit_code: int | None
    evidence: str = ""
    elapsed_ms: int | None = None
    timestamp: str = ""
    matcher_result: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Render this evidence as a JSON-safe mapping."""
        return {
            "command": self.command,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "evidence": self.evidence,
            "elapsed_ms": self.elapsed_ms,
            "timestamp": self.timestamp,
            "matcher_result": self.matcher_result,
        }


@dataclass(frozen=True)
class Verdict:
    """The supervisor's final judgment on one cut attempt: state, commit, verifier evidence."""

    cut_id: str
    phase: str
    state: str
    commit: str = ""
    report: str = ""
    verifiers: tuple[VerifierEvidence, ...] = ()
    failures: tuple[str, ...] = ()
    repair_attempts: int = 0

    @property
    def ok(self) -> bool:
        """True only when the cut reached the supervisor-verified state."""
        return self.state == STATE_VERIFIED

    def to_dict(self) -> dict[str, Any]:
        """Render this verdict as a JSON-safe mapping."""
        return {
            "cut_id": self.cut_id,
            "phase": self.phase,
            "state": self.state,
            "commit": self.commit,
            "report": self.report,
            "verifiers": [verifier.to_dict() for verifier in self.verifiers],
            "failures": list(self.failures),
            "repair_attempts": self.repair_attempts,
        }


@dataclass(frozen=True)
class CutState:
    """A verdict's summary shape, kept without verifier evidence for tracker rendering."""

    cut_id: str
    phase: str
    state: str
    commit: str = ""
    report: str = ""

    @classmethod
    def from_verdict(cls, verdict: Verdict) -> CutState:
        """Project a full ``Verdict`` down to its tracker-relevant summary fields."""
        return cls(
            cut_id=verdict.cut_id,
            phase=verdict.phase,
            state=verdict.state,
            commit=verdict.commit,
            report=verdict.report,
        )

    def to_dict(self) -> dict[str, Any]:
        """Render this cut state as a JSON-safe mapping."""
        return {
            "cut_id": self.cut_id,
            "phase": self.phase,
            "state": self.state,
            "commit": self.commit,
            "report": self.report,
        }


@dataclass(frozen=True)
class Baton:
    """Immutable, append-only record of every cut's outcome across one dispatch run."""

    last: Verdict | None = None
    states: tuple[CutState, ...] = ()
    total: int = 0

    @classmethod
    def empty(cls, *, total: int) -> Baton:
        """Build an empty baton for a dispatch with ``total`` planned cuts."""
        return cls(last=None, states=(), total=total)

    @property
    def verified(self) -> int:
        """Count of cuts that reached the supervisor-verified state so far."""
        return sum(1 for state in self.states if state.state == STATE_VERIFIED)

    @property
    def ratio(self) -> float:
        """Fraction of planned cuts verified so far; 0.0 when ``total`` is non-positive."""
        if self.total <= 0:
            return 0.0
        return self.verified / self.total

    def append(self, verdict: Verdict) -> Baton:
        """Return a new baton with ``verdict`` appended (the baton is immutable)."""
        return Baton(
            last=verdict,
            states=(*self.states, CutState.from_verdict(verdict)),
            total=self.total,
        )

    def to_dict(self) -> dict[str, Any]:
        """Render this baton, including the derived dou_index, as a JSON-safe mapping."""
        return {
            "last": self.last.to_dict() if self.last is not None else None,
            "states": [state.to_dict() for state in self.states],
            "dou_index": {
                "verified": self.verified,
                "total": self.total,
                "ratio": self.ratio,
            },
        }

    def to_json(self) -> str:
        """Render this baton as an indented JSON string (the form handed to prompts)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class Dispatch:
    """The fully parsed and validated dispatch plan: meta, policy, phases, and cuts."""

    schema: str
    meta: Meta
    policy: Policy
    common: Common
    phases: tuple[Phase, ...]
    cuts: tuple[Cut, ...]
    workflow_map: dict[str, str] = field(default_factory=dict)
    # Optional typed execution envelope (spec §7.1). Absent = legacy dispatch;
    # present = the supervisor MUST qualify it against the live checkout
    # before any spawn.
    envelope: ExecutionEnvelope | None = None
    # Opaque delivery-proof contract payload (spec §11): dispatch transports
    # it for the worker and never interprets proof semantics.
    proof: Mapping[str, Any] | None = None

    def empty_baton(self) -> Baton:
        """Build an empty baton sized to this dispatch's cut count."""
        return Baton.empty(total=len(self.cuts))
