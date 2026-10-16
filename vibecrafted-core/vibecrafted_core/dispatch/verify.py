"""Deterministic verifier execution: run declared commands, match output, build a Verdict."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from .model import (
    STATE_FAILED,
    STATE_UNKNOWN,
    STATE_VERIFIED,
    Cut,
    Verdict,
    VerifierEvidence,
    Verify,
)

MATCHER_PASS = "pass"
MATCHER_FAIL = "fail"
MATCHER_TIMEOUT = "timeout"

DEFAULT_TIMEOUT_S = 600.0

_EXCERPT_HEAD_LINES = 20
_EXCERPT_TAIL_LINES = 20
_EXCERPT_MAX_CHARS = 4000
_FAILURE_EVIDENCE_CHARS = 240

# Agent-session noise (API keys, runtime markers, launcher state) must never
# reach verifier shells — only stable interpreter/tooling keys survive.
_SAFE_ENV_KEYS = (
    "HOME",
    "LANG",
    "LC_ALL",
    "LOGNAME",
    "PATH",
    "SHELL",
    "TERM",
    "TMPDIR",
    "USER",
)
_FALLBACK_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def sanitize_env(
    base: Mapping[str, str] | None = None,
    *,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a minimal verifier-shell env from a safe key allowlist plus explicit extras.

    Drops everything else (API keys, agent-session/runtime markers) so verifier
    shells never inherit launcher secrets or state.
    """
    source = os.environ if base is None else base
    env = {key: source[key] for key in _SAFE_ENV_KEYS if key in source}
    env.setdefault("PATH", _FALLBACK_PATH)
    if extra:
        env.update({str(key): str(value) for key, value in extra.items()})
    return env


def run_verifies(
    target: Cut | Iterable[Verify],
    *,
    repo: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    env: Mapping[str, str] | None = None,
) -> Verdict:
    """Execute every declared verifier of a cut deterministically.

    `target` is either a full `Cut` or a bare iterable of `Verify` (the
    ad-hoc conductor path). `repo` defaults to the current working
    directory. The verifier shell env is always the sanitized base;
    `env` entries are merged on top as extras, never a wholesale
    replacement — agent-session noise must not leak into verifier shells.

    The verdict is the three-valued AND of all matchers across all
    verifiers: any red matcher refutes the cut (`[!]`), otherwise any
    timed-out command leaves it unknown (`[?]`), otherwise the cut is
    supervisor-verified (`[x]`). A cut without verifiers stays `[?]` —
    verified state must be earned by green matchers, never assumed.
    """
    if isinstance(target, Cut):
        cut_id, phase, verifies = target.id, target.phase, target.verify
    else:
        cut_id, phase, verifies = "adhoc", "", tuple(target)

    if not verifies:
        return Verdict(cut_id=cut_id, phase=phase, state=STATE_UNKNOWN)

    cwd = os.getcwd() if repo is None else repo
    run_env = sanitize_env(extra=env)
    evidences: list[VerifierEvidence] = []
    failures: list[str] = []
    for index, verify in enumerate(verifies):
        evidence, verifier_failures = _run_verifier(
            verify,
            index=index,
            cut_id=cut_id,
            cwd=cwd,
            timeout_s=timeout_s,
            env=run_env,
        )
        evidences.append(evidence)
        failures.extend(verifier_failures)

    results = {evidence.matcher_result for evidence in evidences}
    if MATCHER_FAIL in results:
        state = STATE_FAILED
    elif MATCHER_TIMEOUT in results:
        state = STATE_UNKNOWN
    else:
        state = STATE_VERIFIED
    return Verdict(
        cut_id=cut_id,
        phase=phase,
        state=state,
        verifiers=tuple(evidences),
        failures=tuple(failures),
    )


def _run_verifier(
    verify: Verify,
    *,
    index: int,
    cut_id: str,
    cwd: str,
    timeout_s: float,
    env: Mapping[str, str],
) -> tuple[VerifierEvidence, list[str]]:
    """Run one verifier command under bash with a timeout, then evaluate its matchers."""
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["bash", "-c", verify.run],
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        excerpt = _excerpt(_text(exc.stdout) + _text(exc.stderr))
        evidence = VerifierEvidence(
            command=verify.run,
            ok=False,
            exit_code=None,
            evidence=excerpt,
            elapsed_ms=elapsed_ms,
            timestamp=_now(),
            matcher_result=MATCHER_TIMEOUT,
        )
        failure = (
            f"{cut_id} verify[{index}]: timeout after {timeout_s:g}s"
            f" running {verify.run!r}; partial output: "
            f"{_failure_evidence(excerpt)}"
        )
        return evidence, [failure]

    elapsed_ms = int((time.monotonic() - started) * 1000)
    output = proc.stdout + proc.stderr
    excerpt = _excerpt(output)
    failures: list[str] = []
    # Matching text cannot turn a crashed gate green. Negative-path probes
    # may explicitly declare a different expected exit code.
    if proc.returncode != 0 and not any(
        matcher.kind == "exit_code" for matcher in verify.matchers
    ):
        failures.append(
            f"{cut_id} verify[{index}]: expected exit_code=0"
            f" (exit_code={proc.returncode}); evidence: {_failure_evidence(excerpt)}"
        )
    for matcher in verify.matchers:
        if matcher.check(output, exit_code=proc.returncode):
            continue
        failures.append(
            f"{cut_id} verify[{index}]: matcher {matcher.kind}="
            f"{matcher.expected!r} failed (exit_code={proc.returncode});"
            f" evidence: {_failure_evidence(excerpt)}"
        )
    evidence = VerifierEvidence(
        command=verify.run,
        ok=not failures,
        exit_code=proc.returncode,
        evidence=excerpt,
        elapsed_ms=elapsed_ms,
        timestamp=_now(),
        matcher_result=MATCHER_FAIL if failures else MATCHER_PASS,
    )
    return evidence, failures


def _excerpt(output: str) -> str:
    """Trim captured output to a bounded head/tail excerpt for journals and evidence."""
    text = output.strip()
    lines = text.splitlines()
    if len(lines) > _EXCERPT_HEAD_LINES + _EXCERPT_TAIL_LINES:
        omitted = len(lines) - _EXCERPT_HEAD_LINES - _EXCERPT_TAIL_LINES
        text = "\n".join(
            [
                *lines[:_EXCERPT_HEAD_LINES],
                f"... [{omitted} lines omitted] ...",
                *lines[-_EXCERPT_TAIL_LINES:],
            ]
        )
    if len(text) > _EXCERPT_MAX_CHARS:
        half = _EXCERPT_MAX_CHARS // 2
        text = f"{text[:half]}\n... [chars omitted] ...\n{text[-half:]}"
    return text


def _failure_evidence(excerpt: str) -> str:
    """Flatten an excerpt to a single line, truncated for inline failure messages."""
    flat = " ".join(excerpt.split())
    if len(flat) > _FAILURE_EVIDENCE_CHARS:
        flat = flat[:_FAILURE_EVIDENCE_CHARS] + "..."
    return flat or "<no output>"


def _text(value: object) -> str:
    """Coerce a subprocess stdout/stderr capture (str, bytes, or None) to text."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _now() -> str:
    """Return the current UTC timestamp in second-precision ISO 8601."""
    return datetime.now(UTC).isoformat(timespec="seconds")
