"""Deterministic subprocess execution for the delivery proof kernel.

The executor deliberately keeps process execution separate from proof semantics.
It records what ran and what happened; later kernel layers decide what that
evidence proves.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from .model import ExecutionEvidence

DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_MAX_OUTPUT_BYTES = 10 * 1024 * 1024
DEFAULT_EXCERPT_BYTES = 4_000

_SAFE_ENV_KEYS = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "PATH",
        "SHELL",
        "TERM",
        "TMPDIR",
        "TZ",
        "USER",
    }
)
_FALLBACK_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
_SECRET_KEY = re.compile(
    r"(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.IGNORECASE
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)"
    r"[A-Z0-9_]*)\s*([:=])\s*([^\s]+)"
)


@dataclass(frozen=True)
class ExecutionResult:
    """Execution evidence plus facts that the v1 evidence schema cannot encode."""

    evidence: ExecutionEvidence
    succeeded: bool
    spawned: bool
    timed_out: bool
    output_limit_exceeded: bool
    failure_reason: str | None


@dataclass(frozen=True)
class PipelineResult:
    """Strict-shell pipeline evidence with every segment exit kept separate."""

    evidences: tuple[ExecutionEvidence, ...]
    controller_evidence: ExecutionEvidence
    segment_exit_codes: tuple[int | None, ...]
    succeeded: bool
    failure_reason: str | None


def run_evidence(
    role: str,
    argv: Sequence[str],
    *,
    cwd: str | os.PathLike[str],
    parent_contract_id: str,
    run_id: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    excerpt_bytes: int = DEFAULT_EXCERPT_BYTES,
    env: Mapping[str, str] | None = None,
    input_paths: Sequence[str | os.PathLike[str]] = (),
    output_paths: Sequence[str | os.PathLike[str]] = (),
    allowed_roots: Sequence[str | os.PathLike[str]] | None = None,
    run_identity_sha256: str | None = None,
    liveness_evidence_sha256: Sequence[str] = (),
) -> ExecutionResult:
    """Run one argv-array process and return a complete evidence record.

    Environment input is filtered to a stable allowlist. Output is spooled to
    files so full-stream digests do not require unbounded memory. A process is
    terminated if it exceeds either the timeout or output byte limit.
    """

    started_wall = _now()
    started_monotonic = time.monotonic()
    canonical_cwd = Path(cwd).expanduser().resolve()
    run_env, redaction_values = _sanitize_env(env)
    repo_before = _repo_snapshot(canonical_cwd, run_env)
    normalized_argv = tuple(str(item) for item in argv)
    roots = tuple(
        Path(item).expanduser().resolve()
        for item in (allowed_roots if allowed_roots is not None else (canonical_cwd,))
    )

    validation_error = _validate_request(
        role=role,
        argv=normalized_argv,
        cwd=canonical_cwd,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        excerpt_bytes=excerpt_bytes,
    )
    try:
        canonical_inputs = _canonical_paths(input_paths, canonical_cwd, roots)
        canonical_outputs = _canonical_paths(output_paths, canonical_cwd, roots)
    except ValueError as exc:
        validation_error = str(exc)
        canonical_inputs = ()
        canonical_outputs = ()

    resolved_executable = (
        _resolve_executable(normalized_argv[0], run_env)
        if normalized_argv and validation_error is None
        else None
    )
    if validation_error is None and resolved_executable is None:
        validation_error = f"executable not found: {normalized_argv[0]}"

    input_digests = _path_digests(canonical_inputs)
    executable_digest = (
        _sha256_path(Path(resolved_executable)) if resolved_executable else None
    )
    if validation_error is not None:
        evidence = _build_evidence(
            role=role,
            argv=normalized_argv,
            cwd=canonical_cwd,
            parent_contract_id=parent_contract_id,
            run_id=run_id,
            environment=run_env,
            resolved_executable=resolved_executable or "",
            executable_sha256=executable_digest,
            started_at=started_wall,
            started_monotonic=started_monotonic,
            timeout_seconds=timeout_seconds,
            exit_code=None,
            stdout_sha256=_sha256_bytes(b""),
            stderr_sha256=_sha256_bytes(b""),
            stdout_excerpt="",
            stderr_excerpt=_redact(validation_error, redaction_values),
            input_digests=input_digests,
            output_digests=_path_digests(canonical_outputs),
            repo_before=repo_before,
            repo_after=_repo_snapshot(canonical_cwd, run_env),
            run_identity_sha256=run_identity_sha256,
            liveness_evidence_sha256=liveness_evidence_sha256,
        )
        return ExecutionResult(
            evidence=evidence,
            succeeded=False,
            spawned=False,
            timed_out=False,
            output_limit_exceeded=False,
            failure_reason=validation_error,
        )

    spawned = False
    timed_out = False
    output_limit_exceeded = False
    exit_code: int | None = None
    failure_reason: str | None = None
    with (
        tempfile.TemporaryFile() as stdout_file,
        tempfile.TemporaryFile() as stderr_file,
    ):
        try:
            process = subprocess.Popen(
                normalized_argv,
                cwd=str(canonical_cwd),
                env=run_env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            spawned = True
            deadline = started_monotonic + timeout_seconds
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    timed_out = True
                    failure_reason = f"timeout after {timeout_seconds:g}s"
                    _terminate_process_group(process)
                    break
                if (
                    _stream_size(stdout_file) + _stream_size(stderr_file)
                    > max_output_bytes
                ):
                    output_limit_exceeded = True
                    failure_reason = f"output limit exceeded ({max_output_bytes} bytes)"
                    _terminate_process_group(process)
                    break
                time.sleep(0.01)
            process.wait()
            if not timed_out and not output_limit_exceeded:
                exit_code = process.returncode
                if exit_code != 0:
                    failure_reason = f"process exited {exit_code}"
        except OSError as exc:
            failure_reason = f"spawn failed ({type(exc).__name__})"

        stdout_sha256, stdout_excerpt = _digest_and_excerpt(stdout_file, excerpt_bytes)
        stderr_sha256, stderr_excerpt = _digest_and_excerpt(stderr_file, excerpt_bytes)

    evidence = _build_evidence(
        role=role,
        argv=normalized_argv,
        cwd=canonical_cwd,
        parent_contract_id=parent_contract_id,
        run_id=run_id,
        environment=run_env,
        resolved_executable=resolved_executable or "",
        executable_sha256=executable_digest,
        started_at=started_wall,
        started_monotonic=started_monotonic,
        timeout_seconds=timeout_seconds,
        exit_code=exit_code,
        stdout_sha256=stdout_sha256,
        stderr_sha256=stderr_sha256,
        stdout_excerpt=_redact(stdout_excerpt, redaction_values),
        stderr_excerpt=_redact(stderr_excerpt, redaction_values),
        input_digests=input_digests,
        output_digests=_path_digests(canonical_outputs),
        repo_before=repo_before,
        repo_after=_repo_snapshot(canonical_cwd, run_env),
        run_identity_sha256=run_identity_sha256,
        liveness_evidence_sha256=liveness_evidence_sha256,
    )
    return ExecutionResult(
        evidence=evidence,
        succeeded=spawned and exit_code == 0,
        spawned=spawned,
        timed_out=timed_out,
        output_limit_exceeded=output_limit_exceeded,
        failure_reason=failure_reason,
    )


def run_pipeline(
    segments: Sequence[Sequence[str]],
    *,
    cwd: str | os.PathLike[str],
    parent_contract_id: str,
    run_id: str,
    role: str = "subject",
    required_segments: Sequence[bool] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    excerpt_bytes: int = DEFAULT_EXCERPT_BYTES,
    env: Mapping[str, str] | None = None,
    input_paths: Sequence[str | os.PathLike[str]] = (),
    output_paths: Sequence[str | os.PathLike[str]] = (),
    allowed_roots: Sequence[str | os.PathLike[str]] | None = None,
) -> PipelineResult:
    """Run an explicitly authorized Bash pipeline and preserve ``PIPESTATUS``.

    Segment argv arrays are shell-quoted individually. Bash writes PIPESTATUS to
    a private side channel before any subsequent command can overwrite it.
    """

    normalized = tuple(tuple(str(item) for item in segment) for segment in segments)
    required = (
        tuple(True for _ in normalized)
        if required_segments is None
        else tuple(required_segments)
    )
    if not normalized:
        raise ValueError("pipeline requires at least one segment")
    if any(not segment for segment in normalized):
        raise ValueError("pipeline segments must not be empty")
    if len(required) != len(normalized):
        raise ValueError("required_segments must match segments")

    canonical_cwd = Path(cwd).expanduser().resolve()
    run_env, redaction_values = _sanitize_env(env)
    resolved_segments = tuple(
        _resolve_executable(segment[0], run_env) for segment in normalized
    )
    with tempfile.TemporaryDirectory(prefix="vibecrafted-pipeline-") as temp_dir:
        evidence_dir = Path(temp_dir)
        status_path = evidence_dir / "pipestatus"
        stdout_paths = tuple(
            evidence_dir / f"segment-{index}.stdout" for index in range(len(normalized))
        )
        stderr_paths = tuple(
            evidence_dir / f"segment-{index}.stderr" for index in range(len(normalized))
        )
        wrapped_segments = tuple(
            "( set +e; "
            f"{{ {shlex.join(segment)}; }} 2> {shlex.quote(str(stderr_path))} "
            f"| tee {shlex.quote(str(stdout_path))}; "
            'segment_status=("${PIPESTATUS[@]}"); '
            'exit "${segment_status[0]}" )'
            for segment, stdout_path, stderr_path in zip(
                normalized, stdout_paths, stderr_paths, strict=True
            )
        )
        pipeline = " | ".join(wrapped_segments)
        shell_program = (
            "set -o pipefail\n"
            f"{pipeline}\n"
            'pipeline_status=("${PIPESTATUS[@]}")\n'
            f"printf '%s\\n' \"${{pipeline_status[*]}}\" > {shlex.quote(str(status_path))}\n"
        )
        controller = run_evidence(
            f"{role}.pipeline_controller",
            ["/bin/bash", "-c", shell_program],
            cwd=canonical_cwd,
            parent_contract_id=parent_contract_id,
            run_id=run_id,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            excerpt_bytes=excerpt_bytes,
            env=env,
            input_paths=input_paths,
            output_paths=output_paths,
            allowed_roots=allowed_roots,
        )
        codes = _read_pipeline_status(status_path, len(normalized))
        stream_evidence = tuple(
            (
                _digest_path_and_excerpt(stdout_path, excerpt_bytes),
                _digest_path_and_excerpt(stderr_path, excerpt_bytes),
            )
            for stdout_path, stderr_path in zip(stdout_paths, stderr_paths, strict=True)
        )

        segment_evidences = tuple(
            replace(
                controller.evidence,
                evidence_id=f"evidence-{uuid.uuid4()}",
                role=f"{role}.segment.{index}",
                argv=segment,
                resolved_executable=resolved or "",
                executable_sha256=(
                    _sha256_path(Path(resolved)) if resolved is not None else None
                ),
                exit_code=codes[index],
                stdout_sha256=stream_evidence[index][0][0],
                stderr_sha256=stream_evidence[index][1][0],
                stdout_excerpt=_redact(stream_evidence[index][0][1], redaction_values),
                stderr_excerpt=_redact(stream_evidence[index][1][1], redaction_values),
            )
            for index, (segment, resolved) in enumerate(
                zip(normalized, resolved_segments, strict=True)
            )
        )
    required_failures = tuple(
        index
        for index, (code, is_required) in enumerate(zip(codes, required, strict=True))
        if is_required and code != 0
    )
    succeeded = (
        controller.spawned
        and not required_failures
        and all(code is not None for code in codes)
    )
    if controller.failure_reason is not None:
        failure_reason = controller.failure_reason
    elif required_failures:
        detail = ", ".join(
            f"segment {index} exited {codes[index]}" for index in required_failures
        )
        failure_reason = f"required pipeline segment failed: {detail}"
    elif any(code is None for code in codes):
        failure_reason = "pipeline exited without complete PIPESTATUS evidence"
    else:
        failure_reason = None
    return PipelineResult(
        evidences=segment_evidences,
        controller_evidence=controller.evidence,
        segment_exit_codes=codes,
        succeeded=succeeded,
        failure_reason=failure_reason,
    )


def _validate_request(
    *,
    role: str,
    argv: tuple[str, ...],
    cwd: Path,
    timeout_seconds: float,
    max_output_bytes: int,
    excerpt_bytes: int,
) -> str | None:
    """Return a validation error message for malformed run_evidence arguments, or None."""
    if not role:
        return "role must not be empty"
    if not argv or not argv[0]:
        return "argv must contain an executable"
    if not cwd.is_dir():
        return f"cwd is not a directory: {cwd}"
    if timeout_seconds <= 0:
        return "timeout_seconds must be positive"
    if max_output_bytes <= 0:
        return "max_output_bytes must be positive"
    if excerpt_bytes <= 0:
        return "excerpt_bytes must be positive"
    return None


def _sanitize_env(
    supplied: Mapping[str, str] | None,
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Filter environment to `_SAFE_ENV_KEYS`, falling back to os.environ when unset.

    Returns the sanitized env mapping plus the set of secret-looking values (by
    key pattern) that must be redacted from captured stdout/stderr excerpts.
    """
    source = os.environ if supplied is None else supplied
    manifest = {
        key: str(source[key]) for key in sorted(_SAFE_ENV_KEYS) if key in source
    }
    manifest.setdefault("PATH", _FALLBACK_PATH)
    redaction_values = tuple(
        value for key, value in source.items() if value and _SECRET_KEY.search(str(key))
    )
    return manifest, redaction_values


def _resolve_executable(executable: str, env: Mapping[str, str]) -> str | None:
    """Resolve an executable to an absolute, executable path, or None if not found.

    A path containing ``os.sep`` is checked directly; a bare name is resolved
    via ``shutil.which`` against the (possibly sanitized) PATH in ``env``.
    """
    if os.sep in executable:
        candidate = Path(executable).expanduser().resolve()
        return (
            str(candidate)
            if candidate.is_file() and os.access(candidate, os.X_OK)
            else None
        )
    resolved = shutil.which(executable, path=env.get("PATH", _FALLBACK_PATH))
    return str(Path(resolved).resolve()) if resolved else None


def _canonical_paths(
    paths: Sequence[str | os.PathLike[str]], cwd: Path, roots: tuple[Path, ...]
) -> tuple[Path, ...]:
    """Resolve each path relative to ``cwd`` and require it to fall under an allowed root.

    Raises ValueError on the first path that escapes ``roots`` (containment check).
    """
    canonical: list[Path] = []
    for item in paths:
        raw = Path(item).expanduser()
        path = (cwd / raw if not raw.is_absolute() else raw).resolve()
        if not any(path == root or path.is_relative_to(root) for root in roots):
            raise ValueError(f"path outside allowed roots: {path}")
        canonical.append(path)
    return tuple(canonical)


def _repo_snapshot(cwd: Path, env: Mapping[str, str]) -> dict[str, object]:
    """Capture git HEAD and a status digest for ``cwd`` before/after an execution.

    Returns ``is_git: False`` (with a null head) when the directory is not a
    git repo or the git calls fail, rather than raising.
    """

    def git(*args: str) -> subprocess.CompletedProcess[bytes]:
        """Run one git subcommand against ``cwd`` with a 5s timeout, capturing output."""
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            env=dict(env),
            capture_output=True,
            check=False,
            timeout=5,
        )

    try:
        head_result = git("rev-parse", "HEAD")
        status_result = git("status", "--porcelain=v2", "--untracked-files=all")
    except (OSError, subprocess.TimeoutExpired):
        return {"head": None, "status_sha256": _sha256_bytes(b""), "is_git": False}
    if head_result.returncode != 0 or status_result.returncode != 0:
        return {"head": None, "status_sha256": _sha256_bytes(b""), "is_git": False}
    return {
        "head": head_result.stdout.decode("utf-8", errors="replace").strip(),
        "status_sha256": _sha256_bytes(status_result.stdout),
        "is_git": True,
    }


def _path_digests(paths: Sequence[Path]) -> dict[str, str]:
    """Return {path: sha256-or-"missing"} for each path, streamed to avoid full loads."""
    return {
        str(path): _sha256_path(path) if path.is_file() else "missing" for path in paths
    }


def _sha256_path(path: Path) -> str:
    """Return the ``sha256:<hex>`` digest of a file's contents, read in 1MB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _sha256_bytes(payload: bytes) -> str:
    """Return the ``sha256:<hex>`` digest of an in-memory byte string."""
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _stream_size(stream: BinaryIO) -> int:
    """Return the current byte size of an open file-like stream via fstat."""
    return os.fstat(stream.fileno()).st_size


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Send SIGTERM to the process group, escalating to SIGKILL after a 1s grace period."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _digest_and_excerpt(stream: BinaryIO, limit: int) -> tuple[str, str]:
    """Digest a full stream and return a decoded head+tail excerpt bounded by ``limit``.

    When the stream exceeds ``limit`` bytes, the excerpt keeps the first and last
    halves and inserts an "N bytes omitted" marker between them.
    """
    stream.flush()
    size = _stream_size(stream)
    digest = hashlib.sha256()
    stream.seek(0)
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    if size <= limit:
        stream.seek(0)
        excerpt = stream.read()
    else:
        half = limit // 2
        stream.seek(0)
        head = stream.read(half)
        stream.seek(max(0, size - half))
        tail = stream.read(half)
        excerpt = (
            head
            + f"\n... [{size - len(head) - len(tail)} bytes omitted] ...\n".encode()
            + tail
        )
    return f"sha256:{digest.hexdigest()}", excerpt.decode("utf-8", errors="replace")


def _digest_path_and_excerpt(path: Path, limit: int) -> tuple[str, str]:
    """Like _digest_and_excerpt but opens ``path``, treating a missing file as empty."""
    if not path.is_file():
        return _sha256_bytes(b""), ""
    with path.open("rb") as stream:
        return _digest_and_excerpt(stream, limit)


def _redact(text: str, secret_values: Sequence[str]) -> str:
    """Replace known secret values and secret-looking key=value assignments with [REDACTED]."""
    redacted = text
    for value in sorted(set(secret_values), key=len, reverse=True):
        if len(value) >= 4:
            redacted = redacted.replace(value, "[REDACTED]")
    return _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", redacted)


def _read_pipeline_status(path: Path, expected: int) -> tuple[int | None, ...]:
    """Read the whitespace-separated PIPESTATUS integers written by run_pipeline.

    Pads with None for any missing trailing entries; returns all-None on parse failure.
    """
    try:
        values = tuple(int(item) for item in path.read_text(encoding="utf-8").split())
    except (OSError, ValueError):
        values = ()
    return tuple(
        values[index] if index < len(values) else None for index in range(expected)
    )


def _build_evidence(
    *,
    role: str,
    argv: tuple[str, ...],
    cwd: Path,
    parent_contract_id: str,
    run_id: str,
    environment: Mapping[str, str],
    resolved_executable: str,
    executable_sha256: str | None,
    started_at: str,
    started_monotonic: float,
    timeout_seconds: float,
    exit_code: int | None,
    stdout_sha256: str,
    stderr_sha256: str,
    stdout_excerpt: str,
    stderr_excerpt: str,
    input_digests: Mapping[str, str],
    output_digests: Mapping[str, str],
    repo_before: Mapping[str, object],
    repo_after: Mapping[str, object],
    run_identity_sha256: str | None,
    liveness_evidence_sha256: Sequence[str],
) -> ExecutionEvidence:
    """Assemble an immutable ExecutionEvidence record from execution outcome fields."""
    return ExecutionEvidence(
        schema=ExecutionEvidence.SCHEMA,
        evidence_id=f"evidence-{uuid.uuid4()}",
        parent_contract_id=parent_contract_id,
        run_id=run_id,
        role=role,
        argv=argv,
        cwd=str(cwd),
        environment=dict(environment),
        resolved_executable=resolved_executable,
        executable_version=None,
        executable_sha256=executable_sha256,
        started_at=started_at,
        ended_at=_now(),
        elapsed_ms=max(0, int((time.monotonic() - started_monotonic) * 1000)),
        timeout_seconds=timeout_seconds,
        exit_code=exit_code,
        stdout_sha256=stdout_sha256,
        stderr_sha256=stderr_sha256,
        stdout_excerpt=stdout_excerpt,
        stderr_excerpt=stderr_excerpt,
        input_digests=dict(input_digests),
        output_digests=dict(output_digests),
        repo_before=dict(repo_before),
        repo_after=dict(repo_after),
        run_identity_sha256=run_identity_sha256,
        liveness_evidence_sha256=tuple(liveness_evidence_sha256),
    )


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string with millisecond precision."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")
