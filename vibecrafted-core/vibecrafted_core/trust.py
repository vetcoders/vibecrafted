"""vc-trust journal and settlement helper.

The agent skill owns falsification. This module only provides durable,
deterministic mechanics: enumerate candidate commits, append one structured
verdict, project that verdict onto the existing settlement axis, and wait for a
run boundary without inventing a second monitor.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import control_plane
from .clock import utc_now_iso
from .run_mutation import run_mutation_locks
from .settlement import (
    Settlement,
    SettlementEventV2,
    SettlementVerdict,
    TrustReceiptV1,
    build_trust_settlement_event,
    emit_settlement_event,
    tui_key_for,
)

TRUST_VERDICTS = ("pass", "pass-with-gaps", "block")
EVIDENCE_GRADES = ("strong", "medium", "weak")
VERDICT_TO_SETTLEMENT = {
    "pass": SettlementVerdict.FINALIZED,
    "pass-with-gaps": SettlementVerdict.NEEDS_ATTENTION,
    "block": SettlementVerdict.FAILED,
}
TRUST_JOURNAL_SCHEMA_V2 = "vibecrafted.trust-journal.v2"
TRUST_OUTBOX_SCHEMA = "vibecrafted.trust-settlement-outbox.v1"
TRUST_RECOVERY_DEFAULT_LIMIT = 256
TRUST_RECOVERY_MAX_LIMIT = 1024
TRUST_OUTBOX_MAX_BYTES = 1024 * 1024
_TRUST_OUTBOX_NAME_RE = re.compile(r"^[0-9a-f]{64}\.json$")


class TrustJournalRetryable(RuntimeError):
    """Durable trust authority is busy, changing, or has an in-flight tail."""


@dataclass(frozen=True)
class TrustRecoverySuccess:
    """One exact prepared receipt completed by a recovery sweep."""

    run_id: str
    receipt_id: str
    settlement_revision: int


@dataclass(frozen=True)
class TrustRecoveryError:
    """One fail-closed pending outbox result."""

    outbox_path: str
    run_id: str
    error_type: str
    message: str
    retryable: bool


@dataclass(frozen=True)
class TrustRecoveryReport:
    """Bounded one-shot recovery result; no background polling is implied."""

    scanned: int
    recovered: tuple[TrustRecoverySuccess, ...]
    errors: tuple[TrustRecoveryError, ...]
    skipped: int
    truncated: bool

    @property
    def ok(self) -> bool:
        """True when every scanned outbox recovered cleanly and nothing was truncated."""
        return not self.errors and not self.truncated


# Subject: [<agent>/<runtime>] <type>(optional scope): <subject>
_SUBJECT_RE = re.compile(
    r"^\[([a-z][a-z0-9_-]*)/[a-z0-9][a-z0-9._-]*\]\s+"
    r"(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert|release)"
    r"(?:\([^)]*\))?:\s+.+",
    re.IGNORECASE,
)
_AUTHORED_BY_RE = re.compile(
    r"^Authored-By:\s*([a-z][a-z0-9_-]*)\s*<agents@vetcoders\.io>\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_VENDOR_FOOTER_RE = re.compile(
    r"(^Co-Authored-By:|noreply@|@anthropic\.com|@openai\.com)",
    re.IGNORECASE | re.MULTILINE,
)
_TRAILER_KEYS = (
    "Authored-By",
    "session_id",
    "time",
    "runtime",
    "Signed-off-by",
    "Vibecrafted-Warn-Signature",
)
_UNEARNED_DONE = re.compile(
    r"\b(done|fixed|complete|shipped|production.?ready|all green)\b",
    re.IGNORECASE,
)
# Path-like tokens claimed in the commit body (message-claimed envelope).
# Matches repo-relative paths with a separator or a dotted filename, optional
# backticks, and optional trailing punctuation from prose.
_CLAIMED_PATH_RE = re.compile(
    r"(?:`(?P<bt>[A-Za-z0-9_./-]{2,200})`"
    r"|(?P<bare>(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
    r"|[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,12}))"
)


def _repo_root(path: Path | None = None) -> Path:
    """Resolve the git repository toplevel containing *path* (default cwd)."""
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(path or Path.cwd()),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip() or "not inside a git repository")
    return Path(proc.stdout.strip()).resolve()


def default_journal_path() -> Path:
    """Resolve the trust journal path: env override, else ~/.vibecrafted/trust/journal.jsonl."""
    override = str(os.environ.get("VIBECRAFTED_TRUST_JOURNAL") or "").strip()
    if override:
        return Path(override).expanduser()
    home = Path(
        os.environ.get("VIBECRAFTED_HOME") or Path.home() / ".vibecrafted"
    ).expanduser()
    return home / "trust" / "journal.jsonl"


def _resolve_commit(repo: Path, sha: str) -> str:
    """Resolve *sha* to a full commit hash; raise ValueError if it is not a commit."""
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"{sha}^{{commit}}"],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(f"not a commit in {repo}: {sha}")
    return proc.stdout.strip()


def _commit_record(repo: Path, sha: str) -> dict[str, str]:
    """Read one commit's sha/author/email/authored_at/subject via `git show`."""
    full_sha = _resolve_commit(repo, sha)
    proc = subprocess.run(
        [
            "git",
            "show",
            "-s",
            "--format=%H%x00%an%x00%ae%x00%aI%x00%s",
            full_sha,
        ],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip() or f"cannot read commit {full_sha}")
    fields = proc.stdout.rstrip("\n").split("\0")
    if len(fields) != 5:
        raise ValueError(f"unexpected git metadata for {full_sha}")
    return dict(
        zip(
            ("sha", "author_name", "author_email", "authored_at", "subject"),
            fields,
            strict=True,
        )
    )


def _commit_message(repo: Path, sha: str) -> str:
    """Return the full commit message body (`git log -1 --format=%B`) for *sha*."""
    full_sha = _resolve_commit(repo, sha)
    proc = subprocess.run(
        ["git", "log", "-1", "--format=%B", full_sha],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip() or f"cannot read message for {full_sha}")
    return proc.stdout


def _commit_files(repo: Path, sha: str) -> list[str]:
    """List paths changed by *sha*, including root commits.

    ``git diff-tree`` without ``--root`` returns no paths for the repository's
    first commit (no parent). Always pass ``--root`` so root commits that
    added real files are not mis-read as empty envelopes.
    """
    full_sha = _resolve_commit(repo, sha)
    proc = subprocess.run(
        [
            "git",
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-only",
            "-r",
            full_sha,
        ],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip() or f"cannot list files for {full_sha}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


_NON_PATH_EXTS = frozenset(
    {
        "com",
        "org",
        "net",
        "io",
        "ai",
        "dev",
        "app",
        "edu",
        "gov",
        "info",
        "co",
        "uk",
        "de",
        "pl",
    }
)


def _normalize_claimed_path(raw: str, *, backtick: bool) -> str | None:
    """Filter/normalize one regex-matched token into a plausible repo path, or None.

    Rejects URLs, emails, version strings, and prose fragments that merely
    look path-ish (e.g. an agent/runtime subject fragment) unless backtick-quoted.
    """
    token = raw.strip().strip("`\"'").rstrip(".,;:)")
    if not token or token in {".", ".."}:
        return None
    # Drop URL-like, emails, and pure version noise.
    if "://" in token or token.startswith("http") or "@" in token:
        return None
    if re.fullmatch(r"v?\d+(\.\d+)+", token):
        return None
    # Ignore bare extensions and single-char noise.
    if token.startswith(".") and "/" not in token:
        return None
    has_ext = bool(re.search(r"\.[A-Za-z0-9]{1,12}$", token))
    slash_count = token.count("/")
    if has_ext and slash_count == 0:
        ext = token.rsplit(".", 1)[-1].lower()
        # Bare "anthropic.com" from email footers is not a repo path.
        if ext in _NON_PATH_EXTS and not backtick:
            return None
    # Reject agent/runtime subject fragments like "codex/workflow" unless the
    # author explicitly backtick-quoted a real path (or it carries an extension
    # / deeper tree path).
    if not backtick and not has_ext and slash_count < 2:
        return None
    if not has_ext and slash_count == 0 and not backtick:
        return None
    # Require either a path separator or a file extension.
    if "/" not in token and "." not in token:
        return None
    return token


def extract_claimed_paths(message: str) -> list[str]:
    """Mechanical paths the message claims to touch (body + path-ish subject).

    The conventional subject prefix ``[<agent>/<runtime>]`` is stripped before
    scanning so it is never mistaken for a repo path.
    """
    body = _message_body_without_trailers(message)
    subject = message.splitlines()[0] if message.splitlines() else ""
    # Drop [<agent>/<runtime>] so "codex/workflow" is not a claimed path.
    subject = re.sub(r"^\[[^\]]+\]\s*", "", subject)
    haystack = f"{subject}\n{body}"
    found: list[str] = []
    seen: set[str] = set()
    for match in _CLAIMED_PATH_RE.finditer(haystack):
        bt = match.group("bt")
        bare = match.group("bare")
        raw = bt or bare or ""
        path = _normalize_claimed_path(raw, backtick=bool(bt))
        if path is None or path in seen:
            continue
        # Skip trailer-looking keys that can look path-ish.
        if path.lower().startswith("authored-by"):
            continue
        seen.add(path)
        found.append(path)
    return found


def _envelope_path_mismatch(
    *,
    claimed: Sequence[str],
    files: Sequence[str],
) -> tuple[list[str], list[str]]:
    """Return (claimed_missing_from_diff, foreign_unclaimed_in_diff).

    Matching is suffix/basename tolerant so a body may say ``trust.py`` while
    the tree carries ``vibecrafted_core/trust.py``.
    """
    file_set = list(files)
    claimed_list = list(claimed)

    def _covers(claimed_path: str, actual: str) -> bool:
        """True when *actual* satisfies *claimed_path* via exact, suffix, or basename match."""
        if claimed_path == actual:
            return True
        # Prefix-tolerant: body says trust.py, tree has vibecrafted_core/trust.py
        if actual.endswith("/" + claimed_path) or claimed_path.endswith("/" + actual):
            return True
        return Path(claimed_path).name == Path(actual).name and (
            "/" not in claimed_path or "/" not in actual
        )

    missing: list[str] = []
    for path in claimed_list:
        if not any(_covers(path, actual) for actual in file_set):
            missing.append(path)

    foreign: list[str] = []
    if claimed_list:
        for actual in file_set:
            if not any(_covers(path, actual) for path in claimed_list):
                foreign.append(actual)
    return missing, foreign


def _message_body_without_trailers(message: str) -> str:
    """Return the commit message body, stripped of the subject line and known trailers."""
    lines = message.splitlines()
    if not lines:
        return ""
    body_lines: list[str] = []
    for line in lines[1:]:
        stripped = line.strip()
        if any(stripped.startswith(f"{key}:") for key in _TRAILER_KEYS):
            continue
        body_lines.append(line)
    return "\n".join(body_lines).strip()


def parse_subject_agent(subject: str) -> str | None:
    """Extract the ``[<agent>/<runtime>]`` agent name from a commit subject line."""
    match = _SUBJECT_RE.match(subject.strip())
    if not match:
        return None
    return match.group(1).lower()


def parse_authored_by_agent(message: str) -> str | None:
    """Extract the agent name from a canonical `Authored-By:` trailer, if present."""
    match = _AUTHORED_BY_RE.search(message)
    if not match:
        return None
    return match.group(1).lower()


def extract_fairness_and_completeness_claims(
    *,
    repo: Path,
    sha: str,
) -> dict[str, Any]:
    """Turn a commit message + envelope into falsifiable claims.

    Agent fairness is first-class: subject agent must match Authored-By, vendor
    footers are banned, and the message body must carry substance before trailers.
    Format legality alone never upgrades a verdict to pass.
    """
    commit = _commit_record(repo, sha)
    message = _commit_message(repo, sha)
    files = _commit_files(repo, sha)
    subject = commit["subject"]
    subject_agent = parse_subject_agent(subject)
    authored_by = parse_authored_by_agent(message)
    body = _message_body_without_trailers(message)
    has_vendor = bool(_VENDOR_FOOTER_RE.search(message))
    claims: list[dict[str, str]] = []
    failures: list[str] = []
    gaps: list[str] = []

    # --- Agent fairness axis ---
    if subject_agent is None:
        failures.append("subject lacks mandatory [<agent>/<runtime>] type prefix")
        claims.append(
            {
                "claim": "subject declares executor as [<agent>/<runtime>] conventional type",
                "grade": "strong",
                "evidence": f"subject failed pattern: {subject!r}",
            }
        )
    else:
        claims.append(
            {
                "claim": f"subject agent is {subject_agent}",
                "grade": "strong",
                "evidence": f"parsed from subject: {subject!r}",
            }
        )

    if authored_by is None:
        failures.append("missing Authored-By: <agent> <agents@vetcoders.io>")
        claims.append(
            {
                "claim": "Authored-By trailer names the executing agent at agents@vetcoders.io",
                "grade": "strong",
                "evidence": "Authored-By trailer missing or non-canonical",
            }
        )
    else:
        claims.append(
            {
                "claim": f"Authored-By is {authored_by} <agents@vetcoders.io>",
                "grade": "strong",
                "evidence": "canonical Authored-By trailer present",
            }
        )

    if subject_agent and authored_by and subject_agent != authored_by:
        failures.append(
            f"agent fairness breach: subject agent {subject_agent!r} "
            f"!= Authored-By {authored_by!r}"
        )
        claims.append(
            {
                "claim": "subject agent matches Authored-By executor (agent fairness)",
                "grade": "strong",
                "evidence": (
                    f"mismatch subject={subject_agent!r} authored_by={authored_by!r}"
                ),
            }
        )
    elif subject_agent and authored_by and subject_agent == authored_by:
        claims.append(
            {
                "claim": "subject agent matches Authored-By executor (agent fairness)",
                "grade": "strong",
                "evidence": f"both name {subject_agent}",
            }
        )

    if has_vendor:
        failures.append("vendor Co-Authored-By / vendor email footer present")
        claims.append(
            {
                "claim": "no vendor Co-Authored-By or vendor emails (agent fairness)",
                "grade": "strong",
                "evidence": "vendor footer pattern matched in message",
            }
        )
    else:
        claims.append(
            {
                "claim": "no vendor Co-Authored-By or vendor emails (agent fairness)",
                "grade": "strong",
                "evidence": "no vendor footer patterns found",
            }
        )

    # --- Completeness axis (message shape is necessary, never sufficient) ---
    if not body:
        failures.append("commit message has no explanatory body before trailers")
        claims.append(
            {
                "claim": "explanatory body exists before trailers",
                "grade": "strong",
                "evidence": "body empty after stripping subject and trailers",
            }
        )
    else:
        claims.append(
            {
                "claim": "explanatory body exists before trailers",
                "grade": "medium",
                "evidence": f"body length={len(body)} chars",
            }
        )
        if _UNEARNED_DONE.search(body) and not re.search(
            r"\b(test|pytest|cargo|verify|gate|evidence|proof)\b", body, re.IGNORECASE
        ):
            gaps.append(
                "unearned done-language without named test/gate/evidence in body"
            )
            claims.append(
                {
                    "claim": "done/fixed language is backed by named verification",
                    "grade": "weak",
                    "evidence": "done-language present without test/gate/evidence tokens",
                }
            )

    if not files:
        gaps.append("commit touches no files (empty envelope)")
        claims.append(
            {
                "claim": "commit envelope lists changed files",
                "grade": "strong",
                "evidence": "git diff-tree --root returned zero paths",
            }
        )
    else:
        claims.append(
            {
                "claim": "commit envelope lists changed files",
                "grade": "strong",
                "evidence": f"{len(files)} path(s): {', '.join(files[:8])}",
            }
        )

    # --- Envelope honesty: message-claimed paths vs diff-tree files ---
    # Foreign/unclaimed files are a first-class agent-fairness axis: a commit
    # that smuggles paths the message does not claim (or claims paths the
    # commit never touched) fails the envelope contract.
    claimed_paths = extract_claimed_paths(message)
    missing_claimed, foreign_files = _envelope_path_mismatch(
        claimed=claimed_paths, files=files
    )
    if claimed_paths:
        claims.append(
            {
                "claim": "message-claimed paths are present in the commit envelope",
                "grade": "strong",
                "evidence": (
                    f"claimed={claimed_paths!r}; missing_from_diff={missing_claimed!r}"
                    if missing_claimed
                    else f"all claimed paths covered by envelope: {claimed_paths!r}"
                ),
            }
        )
        claims.append(
            {
                "claim": (
                    "staged/commit envelope does not carry foreign unclaimed files"
                ),
                "grade": "strong",
                "evidence": (
                    f"foreign_unclaimed={foreign_files!r} vs claimed={claimed_paths!r}"
                    if foreign_files
                    else f"no foreign files beyond claimed set ({len(files)} path(s))"
                ),
            }
        )
        if missing_claimed:
            failures.append(
                "message claims paths absent from the commit envelope: "
                + ", ".join(missing_claimed)
            )
        if foreign_files:
            # Multi-file smuggling is a fairness/completeness gap (or block when
            # combined with other fairness failures). Named evidence is required.
            gaps.append(
                "commit envelope carries foreign unclaimed files: "
                + ", ".join(foreign_files)
            )
    elif files:
        # No path tokens in the message: still emit the axis so inspect always
        # surfaces the envelope contract (cannot prove foreign without claims).
        claims.append(
            {
                "claim": (
                    "staged/commit envelope does not carry foreign unclaimed files"
                ),
                "grade": "medium",
                "evidence": (
                    "message claims no concrete paths; envelope has "
                    f"{len(files)} path(s) — scope honesty not path-checkable"
                ),
            }
        )

    # Align git author email with Authored-By when the author is a named agent
    # mailbox (not the shared agents@ fleet identity).
    git_email = commit.get("author_email", "")
    if (
        authored_by
        and git_email.endswith("@vetcoders.io")
        and "agents@" not in git_email
        and authored_by not in git_email
    ):
        gaps.append(
            f"git author email {git_email!r} does not align with Authored-By {authored_by!r}"
        )

    if failures:
        recommended = "block"
    elif gaps:
        recommended = "pass-with-gaps"
    else:
        # Format + fairness can only ever justify pass-with-gaps unless the
        # operator/agent supplies strong runtime evidence via note.
        recommended = "pass-with-gaps"

    return {
        "schema": "vibecrafted.trust-inspect.v1",
        "sha": commit["sha"],
        "subject": subject,
        "subject_agent": subject_agent,
        "authored_by_agent": authored_by,
        "files": files,
        "failures": failures,
        "gaps": gaps,
        "claims": claims,
        "recommended_verdict": recommended,
        "note": (
            "Format/trailer legality and agent-fairness checks never alone "
            "produce pass; pass requires strong runtime evidence supplied via note."
        ),
    }


def recommend_verdict_from_inspect(inspect: Mapping[str, Any]) -> str:
    """Read the recommended trust verdict out of an inspect() result, defaulting to block."""
    verdict = str(inspect.get("recommended_verdict") or "block")
    if verdict not in TRUST_VERDICTS:
        return "block"
    return verdict


def _parse_journal_bytes(
    raw_journal: bytes,
    path: Path,
    *,
    partial_is_retryable: bool,
) -> list[dict[str, Any]]:
    """Decode raw journal bytes into JSONL records, rejecting a torn/partial tail."""
    if raw_journal and not raw_journal.endswith(b"\n"):
        error = f"trust journal has a partial tail: {path}"
        if partial_is_retryable:
            raise TrustJournalRetryable(error)
        raise ValueError(error)
    try:
        journal_text = raw_journal.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid journal encoding at {path}") from exc
    records: list[dict[str, Any]] = []
    for line_number, raw in enumerate(journal_text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid journal JSON at {path}:{line_number}") from exc
        if not isinstance(payload, dict):
            raise TypeError(f"invalid journal record at {path}:{line_number}")
        records.append(payload)
    return records


def _read_journal(path: Path) -> list[dict[str, Any]]:
    """Read and parse the trust journal under a shared, non-blocking flock.

    Raises TrustJournalRetryable if the file is exclusively locked by a
    concurrent writer rather than blocking indefinitely.
    """
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return []
    locked = False
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(errno.EINVAL, f"trust journal is not a regular file: {path}")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                raise
            raise TrustJournalRetryable(f"trust journal is busy: {path}") from exc

        chunks: list[bytes] = []
        while True:
            try:
                chunk = os.read(descriptor, 64 * 1024)
            except InterruptedError:
                continue
            if not chunk:
                break
            chunks.append(chunk)
        raw_journal = b"".join(chunks)
    finally:
        if locked:
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    return _parse_journal_bytes(
        raw_journal,
        path,
        partial_is_retryable=True,
    )


def _journal_write(descriptor: int, data: bytes) -> int:
    """Indirection for deterministic short-write and error-injection tests."""

    return os.write(descriptor, data)


def _write_all(descriptor: int, data: bytes) -> None:
    """Write the full *data* buffer to *descriptor*, looping over short writes."""
    offset = 0
    while offset < len(data):
        try:
            written = _journal_write(descriptor, data[offset:])
        except InterruptedError:
            continue
        if written <= 0 or written > len(data) - offset:
            raise OSError(
                errno.EIO,
                f"invalid journal write progress: {written}/{len(data) - offset}",
            )
        offset += written


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    """Append one JSON line to *path* under an exclusive flock, fsynced and rollback-safe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(dict(payload), sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    flags = os.O_APPEND | os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    locked = False
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(errno.EINVAL, f"trust journal is not a regular file: {path}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        pre_append_offset = os.lseek(descriptor, 0, os.SEEK_END)
        try:
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
        except BaseException:
            try:
                os.ftruncate(descriptor, pre_append_offset)
                os.fsync(descriptor)
            except OSError as rollback_error:
                raise OSError(
                    errno.EIO,
                    f"trust journal append rollback failed for {path}",
                ) from rollback_error
            raise
    finally:
        if locked:
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    control_plane._fsync_directory_durable(path.parent)


def _validate_owned_directory(path: Path, *, label: str) -> Path:
    """Verify *path* is a canonical, current-user-owned, non-group/world-writable directory.

    Raises PermissionError on any deviation (symlink, foreign owner, lax mode).
    """
    absolute = Path(os.path.abspath(path.expanduser()))
    try:
        canonical = absolute.resolve(strict=True)
    except OSError as exc:
        raise PermissionError(f"{label} directory is unavailable: {absolute}") from exc
    if canonical != absolute:
        raise PermissionError(f"{label} directory is not canonical: {absolute}")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(absolute, flags)
    try:
        opened = os.fstat(descriptor)
        visible = absolute.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(visible.st_mode)
            or not os.path.samestat(opened, visible)
        ):
            raise PermissionError(f"{label} path is not a stable directory: {absolute}")
        if opened.st_uid != os.getuid():
            raise PermissionError(
                f"{label} directory is not owned by the current user: {absolute}"
            )
        if stat.S_IMODE(opened.st_mode) & 0o022:
            raise PermissionError(
                f"{label} directory is group/world writable: {absolute}"
            )
    finally:
        os.close(descriptor)
    return absolute


def _read_descriptor_all(descriptor: int) -> bytes:
    """Rewind *descriptor* to offset 0 and read its entire contents."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        try:
            chunk = os.read(descriptor, 64 * 1024)
        except InterruptedError:
            continue
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _receipt_entries(
    records: Sequence[Mapping[str, Any]],
    receipt_id: str,
) -> list[dict[str, Any]]:
    """Return every journal record whose nested trust_receipt.receipt_id matches."""
    matches: list[dict[str, Any]] = []
    for item in records:
        receipt = item.get("trust_receipt")
        if (
            isinstance(receipt, Mapping)
            and str(receipt.get("receipt_id") or "") == receipt_id
        ):
            matches.append(dict(item))
    return matches


def _recover_prepared_journal_entry(
    path: Path,
    payload: Mapping[str, Any],
    *,
    receipt_id: str,
) -> dict[str, Any]:
    """Repair only this prepared append's torn prefix, then append exactly once."""

    receipt_payload = payload.get("trust_receipt")
    if (
        not isinstance(receipt_payload, Mapping)
        or str(receipt_payload.get("receipt_id") or "") != receipt_id
    ):
        raise ValueError("prepared journal entry receipt mismatch")
    path = Path(os.path.abspath(path.expanduser()))
    path.parent.mkdir(parents=True, exist_ok=True)
    parent = _validate_owned_directory(path.parent, label="trust journal")
    if path.parent != parent:
        raise PermissionError(f"trust journal parent mismatch: {path}")
    encoded = (
        json.dumps(dict(payload), sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    expected_without_newline = encoded[:-1]
    flags = os.O_APPEND | os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    locked = False
    try:
        opened = os.fstat(descriptor)
        visible = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(visible.st_mode)
            or not os.path.samestat(opened, visible)
        ):
            raise OSError(errno.EINVAL, f"trust journal is not a stable file: {path}")
        if opened.st_uid != os.getuid():
            raise PermissionError(
                f"trust journal is not owned by the current user: {path}"
            )
        if opened.st_nlink != 1 or stat.S_IMODE(opened.st_mode) & 0o022:
            raise PermissionError(f"trust journal permissions are unsafe: {path}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        raw_journal = _read_descriptor_all(descriptor)
        complete_raw = raw_journal
        torn_tail = b""
        if raw_journal and not raw_journal.endswith(b"\n"):
            split_at = raw_journal.rfind(b"\n") + 1
            complete_raw = raw_journal[:split_at]
            torn_tail = raw_journal[split_at:]
        records = _parse_journal_bytes(
            complete_raw,
            path,
            partial_is_retryable=False,
        )
        existing = _receipt_entries(records, receipt_id)

        if torn_tail:
            if torn_tail == expected_without_newline:
                if existing:
                    if len(existing) != 1 or existing[0] != dict(payload):
                        raise ValueError(
                            "trust settlement journal receipt missing or mismatched"
                        )
                    os.ftruncate(descriptor, len(complete_raw))
                    os.fsync(descriptor)
                    control_plane._fsync_directory_durable(parent)
                    return existing[0]
                _write_all(descriptor, b"\n")
                os.fsync(descriptor)
                control_plane._fsync_directory_durable(parent)
                verified = _parse_journal_bytes(
                    _read_descriptor_all(descriptor),
                    path,
                    partial_is_retryable=False,
                )
                matches = _receipt_entries(verified, receipt_id)
                if len(matches) != 1 or matches[0] != dict(payload):
                    raise OSError(
                        "trust settlement journal durability verification failed"
                    )
                return matches[0]
            try:
                decoded_tail = torn_tail.decode("utf-8")
                json.loads(decoded_tail)
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            else:
                raise ValueError(
                    f"trust journal has a complete unterminated record: {path}"
                )
            if existing:
                raise ValueError(
                    f"trust journal has a foreign tail after prepared receipt: {path}"
                )
            if not expected_without_newline.startswith(torn_tail):
                raise ValueError(
                    f"trust journal partial tail is not this prepared receipt: {path}"
                )
            os.ftruncate(descriptor, len(complete_raw))
            os.fsync(descriptor)
            control_plane._fsync_directory_durable(parent)
            raw_journal = complete_raw

        if existing:
            if len(existing) != 1 or existing[0] != dict(payload):
                raise ValueError(
                    "trust settlement journal receipt missing or mismatched"
                )
            return existing[0]

        pre_append_offset = len(raw_journal)
        try:
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
        except BaseException:
            try:
                os.ftruncate(descriptor, pre_append_offset)
                os.fsync(descriptor)
            except OSError as rollback_error:
                raise OSError(
                    errno.EIO,
                    f"trust journal recovery append rollback failed for {path}",
                ) from rollback_error
            raise
        control_plane._fsync_directory_durable(parent)
        verified = _parse_journal_bytes(
            _read_descriptor_all(descriptor),
            path,
            partial_is_retryable=False,
        )
        matches = _receipt_entries(verified, receipt_id)
        if len(matches) != 1 or matches[0] != dict(payload):
            raise OSError("trust settlement journal durability verification failed")
        return matches[0]
    finally:
        if locked:
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


# ISO-ish timestamps from control-plane run meta (started_at / created_at).
# Used only when since is not a resolvable commit; never treat failed rev tokens
# (e.g. rootSHA^) as git --since date filters.
_ISO_OR_GIT_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"$"
)


def _git_log_range(repo: Path, since: str) -> list[str]:
    """Build git-log range args for *since*.

    - empty → no lower bound
    - resolvable commit → ``<since>..HEAD``
    - ISO/date run-meta token → ``--since=<date>`` (await-primary boundary)
    - unresolvable rev-ish (e.g. ``<root>^``) → no lower bound (fail open)

    Never pass a failed commit token into ``git log --since=…``: git treats that
    as a date filter and can yield zero candidates for commits that exist.
    """
    token = (since or "").strip()
    if not token:
        return []
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", f"{token}^{{commit}}"],
        cwd=str(repo),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode == 0:
        return [f"{token}..HEAD"]
    if _ISO_OR_GIT_DATE_RE.match(token):
        return [f"--since={token}"]
    # Unresolvable rev (root parent, typo, foreign sha): omit lower bound.
    return []


def enumerate_commits(
    *,
    repo: Path,
    journal: Path,
    author: str = "",
    since: str = "",
    limit: int = 100,
    include_noted: bool = False,
) -> list[dict[str, str]]:
    """List candidate commits (newest-filtered by *since*/*author*) not yet journaled.

    Excludes commits already noted in the journal for this repo unless
    *include_noted* is set.
    """
    command = [
        "git",
        "log",
        f"--max-count={max(limit, 1)}",
        "--format=%H",
        *_git_log_range(repo, since),
    ]
    if author:
        command.append(f"--author={author}")
    proc = subprocess.run(
        command,
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip() or "git log failed")
    try:
        resolved = str(Path(repo).resolve())
    except OSError:
        resolved = str(repo)
    noted = {
        str(item.get("sha") or "")
        for item in _read_journal(journal)
        if str(item.get("repo_root") or "") in {str(repo), resolved}
        or _same_repo_root(str(item.get("repo_root") or ""), resolved)
    }
    commits = []
    for sha in proc.stdout.splitlines():
        record = _commit_record(repo, sha)
        if include_noted or record["sha"] not in noted:
            commits.append(record)
    return commits


def _same_repo_root(stored: str, resolved: str) -> bool:
    """True when a stored repo-root string resolves to the same path as *resolved*."""
    if not stored:
        return False
    try:
        return str(Path(stored).resolve()) == resolved
    except OSError:
        return False


def _claims_from_args(args: argparse.Namespace) -> list[dict[str, str]]:
    """Zip the CLI's parallel --claim/--grade/--evidence lists into claim dicts."""
    claims = list(args.claim or [])
    grades = list(args.grade or [])
    evidence = list(args.evidence or [])
    if not claims:
        raise ValueError("note requires at least one --claim")
    if not (len(claims) == len(grades) == len(evidence)):
        raise ValueError("each --claim requires one matching --grade and --evidence")
    return [
        {"claim": claim, "grade": grade, "evidence": proof}
        for claim, grade, proof in zip(claims, grades, evidence, strict=True)
    ]


def _claims_digest(claims: Sequence[Mapping[str, str]]) -> str:
    """Deterministic sha256 digest of a claims list (canonical JSON encoding)."""
    raw = json.dumps(
        [dict(claim) for claim in claims],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _trust_outbox_dir() -> Path:
    """Control-plane directory holding pending trust-settlement outbox files."""
    return control_plane.control_plane_home() / "trust_settlement_outbox"


def _ensure_trust_outbox_directory() -> Path:
    """Create (mode 0700) and validate ownership of the trust settlement outbox directory."""
    path = Path(os.path.abspath(_trust_outbox_dir().expanduser()))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return _validate_owned_directory(path, label="trust settlement outbox")


def _trust_outbox_path(run_id: str) -> Path:
    """Deterministic outbox file path for *run_id*, keyed by its sha256 digest."""
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    return _trust_outbox_dir() / f"{digest}.json"


def _strict_json_object(encoded: bytes, *, label: str) -> dict[str, Any]:
    """Decode *encoded* as a JSON object, rejecting duplicate keys and non-finite numbers."""

    def reject_duplicates(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        """object_pairs_hook that raises ValueError on any repeated JSON key."""
        payload: dict[str, Any] = {}
        for key, value in pairs:
            if key in payload:
                raise ValueError(f"{label} has duplicate JSON key: {key}")
            payload[key] = value
        return payload

    try:
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"{label} has non-finite JSON number: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"{label} is not a JSON object")
    return payload


def _read_trust_outbox(path: Path) -> dict[str, Any] | None:
    """Read one trust settlement outbox file with full path/ownership/size validation.

    Returns None if the outbox directory or file is absent; raises on any
    security or consistency violation (escaped path, size, ownership, TOCTOU).
    """
    expected_parent = Path(os.path.abspath(_trust_outbox_dir().expanduser()))
    absolute = Path(os.path.abspath(path.expanduser()))
    if absolute.parent != expected_parent:
        raise ValueError(f"trust settlement outbox path escaped authority: {absolute}")
    if not _TRUST_OUTBOX_NAME_RE.fullmatch(absolute.name):
        raise ValueError(f"trust settlement outbox filename invalid: {absolute.name}")
    try:
        expected_parent.lstat()
    except FileNotFoundError:
        return None
    _validate_owned_directory(expected_parent, label="trust settlement outbox")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(absolute, flags)
    except FileNotFoundError:
        return None
    try:
        opened = os.fstat(descriptor)
        visible = absolute.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(visible.st_mode)
            or not os.path.samestat(opened, visible)
        ):
            raise PermissionError(
                f"trust settlement outbox is not a stable file: {absolute}"
            )
        if opened.st_uid != os.getuid() or opened.st_nlink != 1:
            raise PermissionError(
                f"trust settlement outbox ownership is unsafe: {absolute}"
            )
        if stat.S_IMODE(opened.st_mode) & 0o077:
            raise PermissionError(
                f"trust settlement outbox permissions are not private: {absolute}"
            )
        if opened.st_size > TRUST_OUTBOX_MAX_BYTES:
            raise ValueError(
                f"trust settlement outbox exceeds {TRUST_OUTBOX_MAX_BYTES} bytes"
            )
        chunks: list[bytes] = []
        remaining = TRUST_OUTBOX_MAX_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        if len(encoded) > TRUST_OUTBOX_MAX_BYTES:
            raise ValueError(
                f"trust settlement outbox exceeds {TRUST_OUTBOX_MAX_BYTES} bytes"
            )
        opened_after = os.fstat(descriptor)
        visible_after = absolute.stat(follow_symlinks=False)
        if (
            not os.path.samestat(opened, opened_after)
            or not os.path.samestat(opened_after, visible_after)
            or opened.st_size != opened_after.st_size
            or opened.st_mtime_ns != opened_after.st_mtime_ns
        ):
            raise TrustJournalRetryable(
                f"trust settlement outbox changed while reading: {absolute}"
            )
    finally:
        os.close(descriptor)
    payload = _strict_json_object(
        encoded,
        label=f"trust settlement outbox {absolute}",
    )
    run_id = str(payload.get("run_id") or "")
    if not run_id:
        raise ValueError("trust settlement outbox run id missing")
    if absolute.name != _trust_outbox_path(run_id).name:
        raise ValueError("trust settlement outbox filename/run id mismatch")
    return payload


def _nested_settlement(settlement: Settlement) -> dict[str, Any]:
    """Render a Settlement as the nested "settlement" dict stored in run projections."""
    return {
        "verdict": settlement.verdict.value,
        "reason": settlement.reason,
        "settled_at": settlement.settled_at,
        "source": settlement.source,
        "claim_digest": settlement.claim_digest,
        "waived": settlement.waived,
        "tui": settlement.tui_key,
        "await_rc": settlement.await_rc,
        "await_outcome": settlement.await_outcome,
    }


def _trust_projection_fields(
    settlement: Settlement,
    receipt: TrustReceiptV1,
) -> dict[str, Any]:
    """Build the flat field set written onto run meta/snapshot to project a trust settlement."""
    return {
        **settlement.to_payload(),
        "run_id": receipt.run_id,
        "root": receipt.repo_root,
        "repo_root": receipt.repo_root,
        "commit_sha": receipt.commit_sha,
        "settlement": _nested_settlement(settlement),
        "settlement_revision": receipt.settlement_revision,
        "trust_receipt": receipt.to_payload(),
    }


def _validate_projection_identity(
    payload: Mapping[str, Any],
    *,
    label: str,
    repo_root: Path,
    run_id: str,
    commit_sha: str,
) -> None:
    """Raise ValueError if payload's run_id/root/repo_root/commit_sha diverge from expected."""
    projected_run_id = str(payload.get("run_id") or "").strip()
    if projected_run_id and projected_run_id != run_id:
        raise ValueError(f"trust settlement {label} run_id mismatch")
    expected_root = str(repo_root)
    for field_name in ("root", "repo_root"):
        value = str(payload.get(field_name) or "").strip()
        if value:
            try:
                canonical = str(Path(value).resolve())
            except OSError as exc:
                raise ValueError(
                    f"trust settlement {label} {field_name} invalid"
                ) from exc
            if canonical != expected_root:
                raise ValueError(f"trust settlement {label} {field_name} mismatch")
    projected_commit = str(payload.get("commit_sha") or "").strip()
    if projected_commit and projected_commit != commit_sha:
        raise ValueError(f"trust settlement {label} commit_sha mismatch")


def _remove_durable(path: Path) -> None:
    """Unlink *path* and fsync its parent directory so the removal is durable."""
    path.unlink()
    control_plane._fsync_directory_durable(path.parent)


def _journal_entry_for_receipt(
    journal: Path,
    receipt_id: str,
) -> dict[str, Any] | None:
    """Return the last (most recent) journal record for *receipt_id*, or None."""
    latest: dict[str, Any] | None = None
    for item in _read_journal(journal):
        receipt = item.get("trust_receipt")
        if (
            isinstance(receipt, Mapping)
            and str(receipt.get("receipt_id") or "") == receipt_id
        ):
            latest = item
    return latest


def _projection_matches_receipt(
    payload: Mapping[str, Any],
    receipt: TrustReceiptV1,
) -> bool:
    """True when a meta/snapshot projection payload exactly matches *receipt*'s settlement."""
    raw = payload.get("trust_receipt")
    if not isinstance(raw, Mapping):
        return False
    try:
        projected = TrustReceiptV1.from_payload(raw)
    except (TypeError, ValueError):
        return False
    if projected != receipt:
        return False
    expected = {
        "run_id": receipt.run_id,
        "repo_root": receipt.repo_root,
        "commit_sha": receipt.commit_sha,
        "settlement_revision": receipt.settlement_revision,
        "settlement_verdict": receipt.settlement_verdict,
        "settlement_tui": receipt.settlement_tui,
        "settlement_source": "trust",
        "settlement_claim_digest": receipt.claim_digest,
        "settlement_reason": (
            f"trust_{receipt.trust_verdict.replace('-', '_')}:{receipt.commit_sha}"
        ),
        "settlement_waived": False,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return False
    root = str(payload.get("root") or "").strip()
    repo_root = str(payload.get("repo_root") or "").strip()
    if not root or not repo_root:
        return False
    try:
        if str(Path(root).resolve()) != receipt.repo_root:
            return False
        if str(Path(repo_root).resolve()) != receipt.repo_root:
            return False
    except OSError:
        return False
    settled_at = str(payload.get("settlement_at") or "")
    if not settled_at or payload.get("await_rc") is not None:
        return False
    if str(payload.get("await_outcome") or "") or str(
        payload.get("await_settled_at") or ""
    ):
        return False
    nested = payload.get("settlement")
    return isinstance(nested, Mapping) and dict(nested) == {
        "verdict": receipt.settlement_verdict,
        "reason": expected["settlement_reason"],
        "settled_at": settled_at,
        "source": "trust",
        "claim_digest": receipt.claim_digest,
        "waived": False,
        "tui": receipt.settlement_tui,
        "await_rc": None,
        "await_outcome": "",
    }


def _projection_matches_plan(
    payload: Mapping[str, Any],
    *,
    receipt: TrustReceiptV1,
    fields: Mapping[str, Any],
) -> bool:
    """True when a projection matches the receipt AND every explicit plan field."""
    return _projection_matches_receipt(payload, receipt) and all(
        payload.get(key) == value for key, value in fields.items()
    )


def _projection_fields_for_receipt(
    fields: Mapping[str, Any],
    receipt: TrustReceiptV1,
) -> dict[str, Any]:
    """Normalize legacy outbox plans without preserving snapshot authority."""

    normalized = dict(fields)
    normalized.pop("settlement_history", None)
    normalized.setdefault("run_id", receipt.run_id)
    normalized.setdefault("root", receipt.repo_root)
    normalized.setdefault("repo_root", receipt.repo_root)
    normalized.setdefault("commit_sha", receipt.commit_sha)
    return normalized


def _can_complete_projection(
    payload: Mapping[str, Any],
    *,
    receipt: TrustReceiptV1,
    previous_revision: int,
    fields: Mapping[str, Any] | None = None,
) -> bool:
    """True when it is safe to (idempotently) complete this projection write.

    Accepts an exact match to the planned outcome, or a strictly earlier
    settlement revision than *previous_revision* (never a later/foreign one).
    """
    if fields is not None and _projection_matches_plan(
        payload,
        receipt=receipt,
        fields=fields,
    ):
        return True
    if fields is None and _projection_matches_receipt(payload, receipt):
        return True
    prior = payload.get("trust_receipt")
    if prior not in (None, {}):
        if not isinstance(prior, Mapping):
            return False
        try:
            prior_receipt = TrustReceiptV1.from_payload(prior)
        except (TypeError, ValueError):
            return False
        if prior_receipt == receipt:
            if fields is None:
                return True
            return all(
                key not in payload or payload.get(key) == value
                for key, value in fields.items()
            )
        if prior_receipt.settlement_revision > previous_revision:
            return False
    revision = payload.get("settlement_revision")
    return revision in (None, "") or (
        type(revision) is int and revision <= previous_revision
    )


def _publish_trust_event(event: SettlementEventV2) -> dict[str, Any]:
    """Single indirection kept explicit for crash-injection tests."""

    return emit_settlement_event(event)


def _after_trust_transition(_transition: str) -> None:
    """Crash-injection seam after one durable trust transaction transition."""


def _trust_event_exists(event: SettlementEventV2) -> bool:
    """Find an exact prior publish across retained active and archived segments."""

    expected = event.to_payload()
    matches = False
    with control_plane._event_lock(exclusive=False):
        paths = [control_plane.event_stream_path()]
        archive_dir = control_plane._events_archive_dir()
        if archive_dir.is_dir():
            paths.extend(archive_dir.glob("events-*.jsonl"))
        for path in paths:
            try:
                handle = path.open("rb")
            except FileNotFoundError:
                continue
            with handle:
                for raw in handle:
                    if not raw.endswith(b"\n"):
                        continue
                    try:
                        candidate = json.loads(raw)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not isinstance(candidate, Mapping):
                        continue
                    payload = candidate.get("payload")
                    if not isinstance(payload, Mapping):
                        continue
                    if payload.get("event_key") != event.event_key:
                        continue
                    if (
                        candidate.get("kind") != "settlement.changed"
                        or str(candidate.get("run_id") or "") != event.run_id
                        or dict(payload) != expected
                    ):
                        raise ValueError(
                            f"trust settlement event key collision: {event.event_key}"
                        )
                    matches = True
                    os.fsync(handle.fileno())
    return matches


def _recover_trust_outbox(
    *,
    run_id: str,
    outbox_path: Path,
    expected_journal: Path,
) -> dict[str, Any] | None:
    """Finish one prepared trust transaction with the same exact receipt."""

    outbox = _read_trust_outbox(outbox_path)
    if outbox is None:
        return None
    if (
        outbox.get("schema") != TRUST_OUTBOX_SCHEMA
        or str(outbox.get("run_id") or "") != run_id
    ):
        raise ValueError(f"invalid trust settlement outbox: {outbox_path}")
    receipt_payload = outbox.get("trust_receipt")
    event_payload = outbox.get("event")
    entry_payload = outbox.get("journal_entry")
    if not isinstance(receipt_payload, Mapping):
        raise TypeError("trust settlement outbox receipt missing")
    if not isinstance(event_payload, Mapping):
        raise TypeError("trust settlement outbox event missing")
    if not isinstance(entry_payload, Mapping):
        raise TypeError("trust settlement outbox journal entry missing")
    receipt = TrustReceiptV1.from_payload(receipt_payload)
    event = SettlementEventV2.from_payload(event_payload)
    if (
        receipt.run_id != run_id
        or event.trust_receipt != receipt
        or dict(event_payload) != event.to_payload()
    ):
        raise ValueError("trust settlement outbox event receipt mismatch")
    journal = Path(str(outbox.get("journal") or "")).expanduser()
    if not journal.is_absolute():
        raise ValueError("trust settlement outbox journal path invalid")
    expected_journal_path = expected_journal.expanduser().resolve()
    if journal.resolve() != expected_journal_path:
        raise ValueError("trust settlement outbox journal path mismatch")
    journal = expected_journal_path
    previous_revision = outbox.get("previous_revision")
    if type(previous_revision) is not int or previous_revision < 0:
        raise ValueError("trust settlement outbox previous revision invalid")
    if receipt.settlement_revision != previous_revision + 1:
        raise ValueError("trust settlement outbox revision plan mismatch")
    meta_path = Path(str(outbox.get("meta_path") or "")).expanduser()
    snapshot_path = Path(str(outbox.get("snapshot_path") or "")).expanduser()
    if not meta_path.is_absolute() or not snapshot_path.is_absolute():
        raise ValueError("trust settlement outbox projection path invalid")
    resolved = control_plane.resolve_run(run_id)
    if resolved.meta is None:
        raise ValueError("trust settlement recovery run meta missing")
    expected_meta_path = resolved.meta.resolve()
    expected_snapshot_path = (
        control_plane.run_snapshot_dir() / f"{run_id}.json"
    ).resolve()
    if meta_path.resolve() != expected_meta_path:
        raise ValueError("trust settlement outbox meta path mismatch")
    if snapshot_path.resolve() != expected_snapshot_path:
        raise ValueError("trust settlement outbox snapshot path mismatch")
    meta_path = expected_meta_path
    snapshot_path = expected_snapshot_path
    raw_fields = outbox.get("projection_fields")
    if not isinstance(raw_fields, Mapping):
        raise TypeError("trust settlement outbox projection fields missing")
    fields = _projection_fields_for_receipt(raw_fields, receipt)
    terminal = VERDICT_TO_SETTLEMENT.get(receipt.trust_verdict)
    if terminal is None or terminal.value != receipt.settlement_verdict:
        raise ValueError("trust settlement outbox verdict plan mismatch")
    expected_fields = _trust_projection_fields(
        Settlement(
            verdict=terminal,
            reason=event.reason,
            settled_at=event.settled_at,
            source=event.source,
            claim_digest=event.claim_digest,
            waived=event.waived,
        ),
        receipt,
    )
    if fields != expected_fields:
        raise ValueError("trust settlement outbox projection plan mismatch")
    claims = entry_payload.get("claims")
    journal_bindings = {
        "schema": TRUST_JOURNAL_SCHEMA_V2,
        "repo_root": receipt.repo_root,
        "run_id": receipt.run_id,
        "sha": receipt.commit_sha,
        "verdict": receipt.trust_verdict,
        "settlement_tui": receipt.settlement_tui,
        "claim_digest": receipt.claim_digest,
        "trust_receipt": receipt.to_payload(),
        "recorded_at": event.settled_at,
    }
    if any(entry_payload.get(key) != value for key, value in journal_bindings.items()):
        raise ValueError("trust settlement outbox journal plan mismatch")
    if not isinstance(claims, list) or not all(
        isinstance(claim, Mapping) for claim in claims
    ):
        raise TypeError("trust settlement outbox claims invalid")
    if _claims_digest(claims) != receipt.claim_digest:
        raise ValueError("trust settlement outbox claims digest mismatch")
    if (
        fields.get("settlement_at") != event.settled_at
        or fields.get("settlement_reason") != event.reason
        or fields.get("settlement_source") != event.source
        or fields.get("settlement_claim_digest") != event.claim_digest
        or fields.get("settlement_waived") != event.waived
    ):
        raise ValueError("trust settlement outbox event plan mismatch")

    meta = control_plane._read_json(meta_path)
    if not meta:
        raise ValueError("trust settlement recovery meta projection missing")
    snapshot = control_plane._read_json(snapshot_path)
    if not snapshot:
        snapshot = dict(meta)
        snapshot["run_id"] = run_id
    meta_had_embedded_history = "settlement_history" in meta
    snapshot_had_embedded_history = "settlement_history" in snapshot
    meta.pop("settlement_history", None)
    snapshot.pop("settlement_history", None)
    for label, payload in (("meta", meta), ("snapshot", snapshot)):
        _validate_projection_identity(
            payload,
            label=label,
            repo_root=Path(receipt.repo_root),
            run_id=receipt.run_id,
            commit_sha=receipt.commit_sha,
        )
        if not _can_complete_projection(
            payload,
            receipt=receipt,
            previous_revision=previous_revision,
            fields=fields,
        ):
            raise ValueError(f"trust settlement recovery {label} projection diverged")

    _recover_prepared_journal_entry(
        journal,
        entry_payload,
        receipt_id=receipt.receipt_id,
    )
    _after_trust_transition("journal")

    if meta_had_embedded_history or not _projection_matches_plan(
        meta,
        receipt=receipt,
        fields=fields,
    ):
        meta.update(dict(fields))
        control_plane._write_json_durable(meta_path, meta)
        _after_trust_transition("meta")
    if snapshot_had_embedded_history or not _projection_matches_plan(
        snapshot,
        receipt=receipt,
        fields=fields,
    ):
        snapshot.update(dict(fields))
        control_plane._write_json_durable(snapshot_path, snapshot)
        _after_trust_transition("snapshot")

    if not _projection_matches_plan(
        control_plane._read_json(meta_path),
        receipt=receipt,
        fields=fields,
    ) or not _projection_matches_plan(
        control_plane._read_json(snapshot_path),
        receipt=receipt,
        fields=fields,
    ):
        raise OSError("trust settlement projection durability verification failed")
    published_event_key = str(outbox.get("published_event_key") or "")
    if published_event_key and published_event_key != event.event_key:
        raise ValueError("trust settlement outbox published event mismatch")
    if not _trust_event_exists(event):
        _publish_trust_event(event)
    if not _trust_event_exists(event):
        raise OSError("trust settlement event durability verification failed")
    if not published_event_key:
        acknowledged = dict(outbox)
        acknowledged["published_event_key"] = event.event_key
        control_plane._write_json_durable(outbox_path, acknowledged)
        _after_trust_transition("event")
    _remove_durable(outbox_path)
    return dict(entry_payload)


def _trust_recovery_error(
    path: Path,
    *,
    run_id: str,
    error: BaseException,
) -> TrustRecoveryError:
    """Wrap a caught exception as a TrustRecoveryError, marking retryable failures."""
    return TrustRecoveryError(
        outbox_path=str(path),
        run_id=run_id,
        error_type=type(error).__name__,
        message=str(error),
        retryable=isinstance(error, TrustJournalRetryable),
    )


def recover_pending_trust_settlements(
    *,
    limit: int = TRUST_RECOVERY_DEFAULT_LIMIT,
) -> TrustRecoveryReport:
    """Complete a bounded snapshot of durable trust outboxes exactly once."""

    if type(limit) is not int or not 1 <= limit <= TRUST_RECOVERY_MAX_LIMIT:
        raise ValueError(f"trust recovery limit must be 1..{TRUST_RECOVERY_MAX_LIMIT}")
    outbox_dir = Path(os.path.abspath(_trust_outbox_dir().expanduser()))
    try:
        outbox_dir.lstat()
    except FileNotFoundError:
        return TrustRecoveryReport(
            scanned=0,
            recovered=(),
            errors=(),
            skipped=0,
            truncated=False,
        )
    try:
        _validate_owned_directory(outbox_dir, label="trust settlement outbox")
    except (OSError, ValueError) as exc:
        return TrustRecoveryReport(
            scanned=0,
            recovered=(),
            errors=(
                _trust_recovery_error(
                    outbox_dir,
                    run_id="",
                    error=exc,
                ),
            ),
            skipped=0,
            truncated=False,
        )

    names: list[str] = []
    truncated = False
    try:
        with os.scandir(outbox_dir) as directory_entries:
            for directory_entry in directory_entries:
                if len(names) >= limit:
                    truncated = True
                    break
                names.append(directory_entry.name)
    except OSError as exc:
        return TrustRecoveryReport(
            scanned=0,
            recovered=(),
            errors=(
                _trust_recovery_error(
                    outbox_dir,
                    run_id="",
                    error=exc,
                ),
            ),
            skipped=0,
            truncated=False,
        )

    recovered: list[TrustRecoverySuccess] = []
    errors: list[TrustRecoveryError] = []
    skipped = 0
    for name in sorted(names):
        outbox_path = outbox_dir / name
        run_id = ""
        try:
            if not _TRUST_OUTBOX_NAME_RE.fullmatch(name):
                raise ValueError(f"trust settlement outbox filename invalid: {name}")
            outbox_payload = _read_trust_outbox(outbox_path)
            if outbox_payload is None:
                skipped += 1
                continue
            run_id = str(outbox_payload.get("run_id") or "")
            journal = Path(str(outbox_payload.get("journal") or "")).expanduser()
            if not journal.is_absolute():
                raise ValueError("trust settlement outbox journal path invalid")
            with run_mutation_locks(
                control_plane.control_plane_home(),
                run_id=run_id,
            ):
                recovered_entry = _recover_trust_outbox(
                    run_id=run_id,
                    outbox_path=outbox_path,
                    expected_journal=journal,
                )
            if recovered_entry is None:
                skipped += 1
                continue
            receipt_payload = recovered_entry.get("trust_receipt")
            if not isinstance(receipt_payload, Mapping):
                raise TypeError("recovered trust receipt missing")
            receipt = TrustReceiptV1.from_payload(receipt_payload)
            recovered.append(
                TrustRecoverySuccess(
                    run_id=receipt.run_id,
                    receipt_id=receipt.receipt_id,
                    settlement_revision=receipt.settlement_revision,
                )
            )
        except (OSError, TypeError, ValueError) as exc:
            errors.append(
                _trust_recovery_error(
                    outbox_path,
                    run_id=run_id,
                    error=exc,
                )
            )

    return TrustRecoveryReport(
        scanned=len(names),
        recovered=tuple(recovered),
        errors=tuple(errors),
        skipped=skipped,
        truncated=truncated,
    )


def _persist_trust_settlement(
    *,
    repo_root: Path,
    journal: Path,
    run_id: str,
    verdict: str,
    sha: str,
    claims: Sequence[Mapping[str, str]],
    stamp: str,
) -> dict[str, Any]:
    """Durably settle a run's trust verdict: journal + meta/snapshot projection + event.

    Prepares an outbox (crash-safe two-phase commit) before any irreversible
    side effect, and short-circuits to the already-recovered entry when a
    prior attempt for this exact plan already landed.
    """
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError(f"invalid run id: {run_id!r}")
    journal = journal.expanduser()
    outbox_path = _trust_outbox_path(run_id)
    recovered = _recover_trust_outbox(
        run_id=run_id,
        outbox_path=outbox_path,
        expected_journal=journal,
    )
    if recovered is not None:
        expected_digest = _claims_digest(claims)
        recovered_receipt = recovered.get("trust_receipt")
        if (
            str(recovered.get("sha") or "") == sha
            and str(recovered.get("verdict") or "") == verdict
            and isinstance(recovered_receipt, Mapping)
            and str(recovered_receipt.get("claim_digest") or "") == expected_digest
            and recovered.get("claims") == list(claims)
        ):
            return recovered

    resolved = control_plane.resolve_run(run_id)
    if resolved.meta is None:
        raise ValueError(f"run {run_id} has no meta.json to settle")
    # Read both live projections while the shared run mutation lock is held.
    # Do not call the generic synchronizer here: it may publish an automatic
    # settlement event, while this transaction's journal authority is not yet
    # durable. Missing snapshots are materialised from the fresh runtime meta.
    snapshot_path = control_plane.run_snapshot_dir() / f"{run_id}.json"
    meta = control_plane._read_json(resolved.meta)
    snapshot = control_plane._read_json(snapshot_path)
    if not meta:
        raise ValueError(f"run {run_id} has unreadable meta.json")
    if not snapshot:
        snapshot = dict(meta)
        snapshot["run_id"] = run_id
    for label, payload in (("meta", meta), ("snapshot", snapshot)):
        _validate_projection_identity(
            payload,
            label=label,
            repo_root=repo_root,
            run_id=run_id,
            commit_sha=sha,
        )
    revisions = [
        value
        for value in (
            meta.get("settlement_revision"),
            snapshot.get("settlement_revision"),
        )
        if type(value) is int and value > 0
    ]
    previous_revision = max(revisions, default=0)
    terminal = VERDICT_TO_SETTLEMENT[verdict]
    claim_digest = _claims_digest(claims)
    settlement = Settlement(
        verdict=terminal,
        reason=f"trust_{verdict.replace('-', '_')}:{sha}",
        settled_at=stamp,
        source="trust",
        claim_digest=claim_digest,
    )
    receipt = TrustReceiptV1.issue(
        repo_root=str(repo_root),
        run_id=run_id,
        commit_sha=sha,
        trust_verdict=verdict,
        settlement_verdict=terminal.value,
        settlement_tui=tui_key_for(terminal),
        settlement_revision=previous_revision + 1,
        claim_digest=claim_digest,
    )
    fields = _trust_projection_fields(settlement, receipt)
    journal_entry: dict[str, Any] = {
        "schema": TRUST_JOURNAL_SCHEMA_V2,
        "recorded_at": stamp,
        "repo_root": str(repo_root),
        **_commit_record(repo_root, sha),
        "verdict": verdict,
        "settlement_tui": receipt.settlement_tui,
        "run_id": run_id,
        "claims": list(claims),
        "claim_digest": claim_digest,
        "trust_receipt": receipt.to_payload(),
    }
    event = build_trust_settlement_event(
        previous_payload=snapshot,
        settlement=settlement,
        receipt=receipt,
    )

    # The prepared outbox is durable before the first irreversible side effect.
    # Recovery replays this exact plan: journal, projections, then notification.
    outbox = {
        "schema": TRUST_OUTBOX_SCHEMA,
        "run_id": run_id,
        "journal": str(journal.resolve()),
        "meta_path": str(resolved.meta.resolve()),
        "snapshot_path": str(snapshot_path.resolve()),
        "previous_revision": previous_revision,
        "trust_receipt": receipt.to_payload(),
        "journal_entry": journal_entry,
        "projection_fields": fields,
        "event": event.to_payload(),
    }
    outbox_dir = _ensure_trust_outbox_directory()
    if Path(os.path.abspath(outbox_path.parent.expanduser())) != outbox_dir:
        raise PermissionError("trust settlement outbox directory mismatch")
    control_plane._write_json_durable(outbox_path, outbox)
    _after_trust_transition("outbox")
    persisted = _recover_trust_outbox(
        run_id=run_id,
        outbox_path=outbox_path,
        expected_journal=journal,
    )
    if persisted is None:
        raise OSError("trust settlement outbox disappeared before recovery")
    return persisted


def note_verdict(
    *,
    repo: Path,
    journal: Path,
    sha: str,
    verdict: str,
    claims: Sequence[Mapping[str, str]],
    run_id: str = "",
) -> dict[str, Any]:
    """Record one falsification verdict for a commit: journal-only, or full run settlement.

    With *run_id* set, delegates to `_persist_trust_settlement` under the run
    mutation lock; otherwise appends a bare v1 journal entry (no run projection).
    """
    if verdict not in TRUST_VERDICTS:
        raise ValueError(f"unsupported trust verdict: {verdict}")
    # Always persist the git toplevel path so macOS /var vs /private/var and
    # relative roots still match guard/triage lookups.
    resolved_repo = _repo_root(repo)
    commit = _commit_record(resolved_repo, sha)
    stamp = utc_now_iso()
    lock = (
        run_mutation_locks(control_plane.control_plane_home(), run_id=run_id)
        if run_id
        else nullcontext()
    )
    with lock:
        if run_id:
            return _persist_trust_settlement(
                repo_root=resolved_repo,
                journal=journal,
                run_id=run_id,
                verdict=verdict,
                sha=commit["sha"],
                claims=claims,
                stamp=stamp,
            )
        tui = tui_key_for(VERDICT_TO_SETTLEMENT[verdict])
        entry: dict[str, Any] = {
            "schema": "vibecrafted.trust-journal.v1",
            "recorded_at": stamp,
            "repo_root": str(resolved_repo),
            **commit,
            "verdict": verdict,
            "settlement_tui": tui,
            "run_id": run_id,
            "claims": list(claims),
        }
        _append_jsonl(journal, entry)
    return entry


def triage_records(
    records: Sequence[Mapping[str, Any]], *, run_id: str = ""
) -> dict[str, Any]:
    """Tally the latest verdict per (repo_root, sha), counting pass/gaps/block cells."""
    latest: dict[tuple[str, str], Mapping[str, Any]] = {}
    for record in records:
        if run_id and str(record.get("run_id") or "") != run_id:
            continue
        key = (str(record.get("repo_root") or ""), str(record.get("sha") or ""))
        if all(key):
            latest[key] = record
    counts = {"f": 0, "x": 0, "n": 0}
    for record in latest.values():
        cell = str(record.get("settlement_tui") or "")
        if cell in counts:
            counts[cell] += 1
    return {
        "schema": "vibecrafted.trust-triage.v1",
        "run_id": run_id,
        "counts": counts,
        "commits": len(latest),
    }


def _read_run_meta(run_id: str) -> dict[str, Any]:
    """Read a run's meta.json via control_plane, returning {} if missing/unreadable."""
    resolved = control_plane.resolve_run(run_id)
    if resolved.meta is None:
        return {}
    try:
        payload = json.loads(resolved.meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _run_start(payload: Mapping[str, Any]) -> str:
    """Return the first present run-start timestamp field from run meta, or ""."""
    for key in ("started_at", "created_at", "launched_at", "timestamp"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def await_primary(
    *,
    run_id: str,
    repo: Path,
    journal: Path,
    author: str = "",
    since: str = "",
    interval: float = 5.0,
    timeout: float = 0.0,
) -> dict[str, Any]:
    """Block until *run_id* completes, then list not-yet-noted commits since it started.

    Raises TimeoutError if *timeout* elapses first (0 means wait indefinitely).
    """
    started = time.monotonic()
    initial_meta = _read_run_meta(run_id)
    while True:
        remaining = max(timeout - (time.monotonic() - started), 0.0) if timeout else 0.0
        window = min(max(interval, 0.1), remaining) if timeout else max(interval, 0.1)
        result = control_plane.await_run(
            run_id,
            timeout_seconds=window,
            interval_seconds=min(max(interval, 0.1), 1.0),
        )
        if result.get("completed"):
            break
        if timeout and time.monotonic() - started >= timeout:
            raise TimeoutError(f"trust await timed out for run {run_id}")
    candidates = enumerate_commits(
        repo=repo,
        journal=journal,
        author=author,
        since=since or _run_start(initial_meta),
        include_noted=False,
    )
    return {
        "schema": "vibecrafted.trust-await-primary.v1",
        "run_id": run_id,
        "await": {
            "completed": bool(result.get("completed")),
            "reason": str(result.get("reason") or ""),
            "await_rc": result.get("await_rc"),
            "await_outcome": str(result.get("await_outcome") or ""),
        },
        "candidate_commits": candidates,
        "next": (
            "Falsify every candidate claim, then record each commit with "
            "python -m vibecrafted_core.trust note ..."
        ),
    }


def _parser() -> argparse.ArgumentParser:
    """Build the vc-trust CLI: enumerate/inspect/note/triage/await-primary subcommands."""
    parser = argparse.ArgumentParser(
        prog="python -m vibecrafted_core.trust",
        description="Append-only vc-trust journal and settlement helper.",
    )
    parser.add_argument("--journal", type=Path, default=default_journal_path())
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)

    enumerate_parser = commands.add_parser("enumerate")
    enumerate_parser.add_argument("author", nargs="?", default="")
    enumerate_parser.add_argument("--since", default="")
    enumerate_parser.add_argument("--limit", type=int, default=100)
    enumerate_parser.add_argument("--all", action="store_true")

    inspect_parser = commands.add_parser(
        "inspect",
        help=(
            "Extract agent-fairness and completeness claims from a commit "
            "(never auto-notes; never implies pass)."
        ),
    )
    inspect_parser.add_argument("sha")

    note_parser = commands.add_parser("note")
    note_parser.add_argument("sha")
    note_parser.add_argument("verdict", choices=TRUST_VERDICTS)
    note_parser.add_argument("--run-id", default="")
    note_parser.add_argument("--claim", action="append", required=True)
    note_parser.add_argument(
        "--grade", action="append", choices=EVIDENCE_GRADES, required=True
    )
    note_parser.add_argument("--evidence", action="append", required=True)

    triage_parser = commands.add_parser("triage")
    triage_parser.add_argument("--run-id", default="")

    await_parser = commands.add_parser("await-primary")
    await_parser.add_argument("run_id")
    await_parser.add_argument("--author", default="")
    await_parser.add_argument("--since", default="")
    await_parser.add_argument("--interval", type=float, default=5.0)
    await_parser.add_argument("--timeout", type=float, default=0.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point dispatching to the vc-trust journal/settlement subcommands."""
    args = _parser().parse_args(argv)
    try:
        repo = _repo_root(args.repo)
        journal = args.journal.expanduser()
        if args.command == "enumerate":
            result: Any = enumerate_commits(
                repo=repo,
                journal=journal,
                author=args.author,
                since=args.since,
                limit=args.limit,
                include_noted=args.all,
            )
        elif args.command == "inspect":
            result = extract_fairness_and_completeness_claims(repo=repo, sha=args.sha)
        elif args.command == "note":
            result = note_verdict(
                repo=repo,
                journal=journal,
                sha=args.sha,
                verdict=args.verdict,
                claims=_claims_from_args(args),
                run_id=args.run_id,
            )
        elif args.command == "triage":
            result = triage_records(
                _read_journal(journal),
                run_id=args.run_id,
            )
        else:
            result = await_primary(
                run_id=args.run_id,
                repo=repo,
                journal=journal,
                author=args.author,
                since=args.since,
                interval=args.interval,
                timeout=args.timeout,
            )
    except (OSError, TimeoutError, ValueError, control_plane.RunNotResolved) as exc:
        print(f"vc-trust: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
