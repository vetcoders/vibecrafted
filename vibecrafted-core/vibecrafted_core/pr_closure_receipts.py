"""Local PR candidate verification and SHA-bound command-output receipts.

These receipts record local observations. Log digests detect later corruption;
they do not authenticate the command, the machine, or a remote source.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .dispatch.verify import sanitize_env

_PROFILE_SCHEMA = "vibecrafted.pr-closure.v1"
_RECEIPT_SCHEMA = "vibecrafted.pr-closure-receipt.v1"
_PROVENANCE = "local-command-output; digests do not authenticate source"
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_POSIX_PROCESS_GROUPS = os.name == "posix" and callable(getattr(os, "killpg", None))


class ReceiptError(ValueError):
    """Required local receipt evidence is invalid, unavailable or stale."""


def _require_process_groups() -> None:
    """Refuse unsupported executors before starting commands or observing Git."""
    if not _POSIX_PROCESS_GROUPS:
        raise ReceiptError(
            "local receipt execution currently requires POSIX process-group termination"
        )


def _digest(value: Any) -> str:
    """Hash the entire profile using a deterministic JSON representation."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _matches(actual: dict, expected: dict) -> bool:
    """Compare selected fields without Python's bool/int equality coercion."""
    return all(
        key in actual and _digest(actual[key]) == _digest(value)
        for key, value in expected.items()
    )


def _profile_checks(profile: dict) -> list[dict]:
    """Refuse incomplete profiles before executing any supplied command."""
    if profile.get("schema") != _PROFILE_SCHEMA:
        raise ReceiptError("unsupported PR closure profile schema")
    if not isinstance(profile.get("repository"), str) or not re.fullmatch(
        r"[^/\s]+/[^/\s]+", profile["repository"]
    ):
        raise ReceiptError("profile repository must be owner/repo")
    branches = profile.get("target_branches")
    if (
        not isinstance(branches, list)
        or not branches
        or not all(isinstance(branch, str) and branch for branch in branches)
    ):
        raise ReceiptError("target_branches must be a nonempty string list")
    if not isinstance(profile.get("require_approval"), bool):
        raise ReceiptError("require_approval must be boolean")
    remote = profile.get("remote_checks")
    if not isinstance(remote, list) or any(
        not isinstance(check, dict)
        or not isinstance(check.get("name"), str)
        or not check["name"]
        or type(check.get("app_id")) is not int
        or check["app_id"] <= 0
        for check in remote
    ):
        raise ReceiptError("remote_checks require names and positive app_id values")
    checks = profile.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ReceiptError("at least one local check is required")
    names: set[str] = set()
    test_gates = 0
    for check in checks:
        if not isinstance(check, dict):
            raise ReceiptError("check must be an object")
        name, argv = check.get("name"), check.get("argv")
        if not isinstance(name, str) or not name or name in names:
            raise ReceiptError("check names must be nonempty and unique")
        names.add(name)
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(arg, str) and "\0" not in arg for arg in argv)
            or not argv[0]
        ):
            raise ReceiptError(f"{name}: argv must be a nonempty string array")
        if (
            type(check.get("timeout_seconds")) is not int
            or check["timeout_seconds"] <= 0
        ):
            raise ReceiptError(f"{name}: positive integer timeout_seconds required")
        if "junit" in check:
            if check["junit"] is not True or "test_counts_regex" in check:
                raise ReceiptError(
                    f"{name}: junit=true is an alternative to test_counts_regex"
                )
            if not any("{junit}" in argument for argument in argv):
                raise ReceiptError(
                    f"{name}: JUnit argv requires a literal {{junit}} placeholder"
                )
            test_gates += 1
        if "test_counts_regex" in check:
            pattern = check["test_counts_regex"]
            if not isinstance(pattern, str) or not pattern:
                raise ReceiptError(f"{name}: test_counts_regex must be nonempty")
            try:
                compiled = re.compile(pattern, re.MULTILINE)
            except re.error as exc:
                raise ReceiptError(f"{name}: invalid test_counts_regex") from exc
            if not {"passed", "failed", "skipped"} <= compiled.groupindex.keys():
                raise ReceiptError(
                    f"{name}: named passed, failed, skipped groups required"
                )
            test_gates += 1
    if not test_gates:
        raise ReceiptError("at least one test_counts_regex or JUnit check is required")
    _digest(profile)
    return checks


def _snapshot(profile: dict, snapshot: dict) -> None:
    """Validate identity fields; the caller owns fresh upstream observation."""
    if snapshot.get("repository") != profile["repository"]:
        raise ReceiptError("snapshot repository differs from profile")
    if type(snapshot.get("number")) is not int or snapshot["number"] <= 0:
        raise ReceiptError("snapshot PR number must be positive")
    for key in ("headRefOid", "baseRefOid"):
        if not isinstance(snapshot.get(key), str) or not _SHA.fullmatch(snapshot[key]):
            raise ReceiptError(f"snapshot {key} must be a full SHA")
    if snapshot.get("baseRefName") not in profile["target_branches"]:
        raise ReceiptError("snapshot targets an unapproved branch")
    if not isinstance(snapshot.get("headRefName"), str) or not snapshot["headRefName"]:
        raise ReceiptError("snapshot headRefName is required")
    if not isinstance(snapshot.get("review_digest"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", snapshot["review_digest"]
    ):
        raise ReceiptError("snapshot review_digest must be SHA-256")


def _git(repo: Path, *args: str) -> str:
    """Read Git with replacement objects and ambient Git overrides disabled."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=sanitize_env(
            extra={"GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0"}
        ),
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode:
        raise ReceiptError(f"Git observation failed: {' '.join(args)}")
    return result.stdout.decode("utf-8", errors="replace").strip()


def _state(repo: Path) -> dict:
    """Capture both tracked changes and every untracked path."""
    return {
        "head_sha": _git(repo, "rev-parse", "HEAD"),
        "clean": not _git(
            repo,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ),
    }


def _candidate(repo: Path, snapshot: dict) -> dict:
    """Require an exact clean checkout containing both observed PR tips."""
    if Path(_git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
        raise ReceiptError("repo must name the checkout root")
    state = _state(repo)
    if not state["clean"]:
        raise ReceiptError("candidate checkout is dirty, including untracked files")
    for key in ("headRefOid", "baseRefOid"):
        _git(repo, "merge-base", "--is-ancestor", snapshot[key], state["head_sha"])
    return state


def _outside_repo(path: Path, repo: Path) -> Path:
    """Resolve symlinks before enforcing the external evidence boundary."""
    resolved = path.expanduser().resolve()
    if resolved.is_relative_to(repo):
        raise ReceiptError("receipt and logs must be outside the checkout")
    return resolved


def _log_path(receipt: Path, index: int) -> Path:
    return receipt.parent / f"{receipt.name}.check-{index:03d}.log"


def _junit_path(receipt: Path, index: int) -> Path:
    return receipt.parent / f"{receipt.name}.check-{index:03d}.junit.xml"


def _gate_home(log: Path) -> Path:
    return log.parent / f"{log.name}.home"


class _JUnitTreeBuilder(ET.TreeBuilder):
    """JUnit evidence does not need DTD declarations or entity expansion."""

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise ReceiptError("JUnit DTD and entity declarations are forbidden")


def _junit_counts(raw: bytes) -> dict:
    """Count actual test cases once, never aggregate testsuite counters."""
    try:
        root = ET.fromstring(raw, parser=ET.XMLParser(target=_JUnitTreeBuilder()))
    except ET.ParseError as exc:
        raise ReceiptError("JUnit report is malformed XML") from exc
    if root.tag not in {"testsuites", "testsuite"}:
        raise ReceiptError("JUnit root must be testsuite or testsuites")
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    identities: set[tuple[str, ...]] = set()

    def auxiliary(node: ET.Element) -> None:
        if node.tag == "properties":
            if any(child.tag != "property" or len(child) for child in node):
                raise ReceiptError("unexpected nested JUnit property element")
        elif len(node):
            raise ReceiptError("unexpected nested JUnit output or status element")

    def visit(node: ET.Element, suites: tuple[str, ...]) -> None:
        for key in ("tests", "failures", "errors", "skipped", "disabled"):
            if key in node.attrib and not re.fullmatch(r"[0-9]+", node.attrib[key]):
                raise ReceiptError(
                    "JUnit count attributes must be nonnegative integers"
                )
        if node.tag in {"testsuites", "testsuite"}:
            suites = (*suites, node.get("name", ""))
            for child in node:
                if child.tag in {"testsuite", "testsuites", "testcase"}:
                    if child.tag == "testcase" and node.tag != "testsuite":
                        raise ReceiptError("JUnit testcase must belong to a testsuite")
                    visit(child, suites)
                elif child.tag not in {"properties", "system-out", "system-err"}:
                    raise ReceiptError("unexpected element in JUnit suite")
                else:
                    auxiliary(child)
            return
        name = node.get("name")
        if not name:
            raise ReceiptError("JUnit testcase name is required")
        identity = (*suites, node.get("file", ""), node.get("classname", ""), name)
        if identity in identities:
            raise ReceiptError("duplicate JUnit testcase identity")
        identities.add(identity)
        statuses = {child.tag for child in node}
        if statuses - {
            "failure",
            "error",
            "skipped",
            "properties",
            "system-out",
            "system-err",
        }:
            raise ReceiptError("unexpected or nested JUnit testcase element")
        for child in node:
            auxiliary(child)
        failed = bool(statuses & {"failure", "error"})
        skipped = "skipped" in statuses
        if failed and skipped:
            raise ReceiptError("JUnit testcase cannot be both failed and skipped")
        counts["failed" if failed else "skipped" if skipped else "passed"] += 1

    visit(root, ())
    if not identities:
        raise ReceiptError("JUnit report contains no testcases")
    for suite in root.iter():
        if suite.tag not in {"testsuites", "testsuite"}:
            continue
        cases = list(suite.iter("testcase"))
        expected = {
            "tests": len(cases),
            "failures": sum(
                any(child.tag == "failure" for child in case) for case in cases
            ),
            "errors": sum(
                any(child.tag == "error" for child in case) for case in cases
            ),
            "skipped": sum(
                any(child.tag == "skipped" for child in case) for case in cases
            ),
            "disabled": 0,
        }
        if any(
            key in suite.attrib and int(suite.attrib[key]) != value
            for key, value in expected.items()
        ):
            raise ReceiptError(
                "JUnit aggregate attributes disagree with testcase evidence"
            )
    return counts


def _junit_bytes(path: Path) -> bytes:
    """Read the actual report, refusing symlink substitution or missing evidence."""
    if path.is_symlink():
        raise ReceiptError("JUnit report must not be a symlink")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ReceiptError("JUnit report is missing or unreadable") from exc


def _counts(check: dict, raw: bytes) -> dict | None:
    """Sum all declared test summaries; missing or malformed counts fail closed."""
    pattern = check.get("test_counts_regex")
    if pattern is None:
        return None
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    matches = list(
        re.finditer(pattern, raw.decode("utf-8", errors="replace"), re.MULTILINE)
    )
    if not matches:
        raise ReceiptError("test count summary missing")
    for match in matches:
        for key in counts:
            value = match.group(key)
            if value is None or not re.fullmatch(r"[0-9]+", value):
                raise ReceiptError("test counts must be nonnegative integers")
            counts[key] += int(value)
    return counts


def _run_check(check: dict, repo: Path, log: Path, junit: Path | None = None) -> dict:
    """Run argv directly, retaining the complete combined byte stream."""
    started = time.monotonic()
    timed_out, error, exit_code = False, "", None
    runtime_home = _gate_home(log)
    runtime_home.mkdir(mode=0o700)
    runtime_home.chmod(0o700)
    argv = (
        [argument.replace("{junit}", str(junit)) for argument in check["argv"]]
        if junit
        else check["argv"]
    )
    with log.open("xb") as stream:
        os.chmod(log, 0o600)
        try:
            process = subprocess.Popen(
                argv,
                cwd=repo,
                env=sanitize_env(extra={"VIBECRAFTED_HOME": str(runtime_home)}),
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                exit_code = process.wait(timeout=check["timeout_seconds"])
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                exit_code = process.wait()
        except OSError as exc:
            error = str(exc)
        stream.flush()
        os.fsync(stream.fileno())
    raw = log.read_bytes()
    counts = None
    junit_sha256 = None
    try:
        if junit:
            report = _junit_bytes(junit)
            junit_sha256 = hashlib.sha256(report).hexdigest()
            counts = _junit_counts(report)
        else:
            counts = _counts(check, raw)
    except ValueError as exc:
        error = str(exc)
    passed = not error and not timed_out and exit_code == 0
    if counts is not None:
        passed = (
            passed
            and counts["passed"] > 0
            and counts["failed"] == counts["skipped"] == 0
        )
    return {
        "name": check["name"],
        "argv": check["argv"],
        "executed_argv": argv,
        "vibecrafted_home": str(runtime_home),
        "junit": check.get("junit", False),
        "junit_path": str(junit) if junit else None,
        "junit_sha256": junit_sha256,
        "timeout_seconds": check["timeout_seconds"],
        "test_counts_regex": check.get("test_counts_regex"),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "error": error,
        "test_counts": counts,
        "status": "pass" if passed else "failed",
        "log_path": str(log),
        "log_sha256": hashlib.sha256(raw).hexdigest(),
        "elapsed_seconds": time.monotonic() - started,
    }


def _write_receipt(path: Path, payload: dict) -> None:
    """Publish a complete receipt atomically without replacing an existing path."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                payload,
                stream,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_candidate(profile: dict, repo: Path, snapshot: dict, output: Path) -> dict:
    """Execute local gates and write their receipt; failed gates retain their evidence.

    Invalid prerequisites raise ValueError. The caller must compare fresh upstream
    snapshots before and after this call; this module makes no network observations.
    """
    _require_process_groups()
    checks = _profile_checks(profile)
    _snapshot(profile, snapshot)
    repo = repo.expanduser().resolve()
    before = _candidate(repo, snapshot)
    output = output.expanduser()
    original_logs = [_log_path(output, index) for index in range(len(checks))]
    original_logs.extend(
        _gate_home(_log_path(output, index)) for index in range(len(checks))
    )
    original_logs.extend(
        _junit_path(output, index)
        for index, check in enumerate(checks)
        if check.get("junit")
    )
    if any(os.path.lexists(path) for path in [output, *original_logs]):
        raise ReceiptError("refusing to overwrite existing receipt or logs")
    output = _outside_repo(output, repo)
    logs = [
        _outside_repo(_log_path(output, index), repo) for index in range(len(checks))
    ]
    reports = [
        _outside_repo(_junit_path(output, index), repo) if check.get("junit") else None
        for index, check in enumerate(checks)
    ]
    homes = [_outside_repo(_gate_home(log), repo) for log in logs]
    if any(
        os.path.lexists(path)
        for path in [output, *logs, *homes, *(report for report in reports if report)]
    ):
        raise ReceiptError("refusing to overwrite existing receipt or logs")
    output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for check, log, report in zip(checks, logs, reports, strict=True):
        result = _run_check(check, repo, log, report)
        try:
            after = _state(repo)
        except ValueError:
            after = {"head_sha": "", "clean": False}
        result["checkout_after"] = after
        if after != before:
            result["status"] = "failed"
        results.append(result)
    try:
        after = _state(repo)
    except ValueError:
        after = {"head_sha": "", "clean": False}
    receipt = {
        "schema": _RECEIPT_SCHEMA,
        "provenance": _PROVENANCE,
        "created_at": datetime.now(UTC).isoformat(),
        "repository": snapshot["repository"],
        "number": snapshot["number"],
        "head_ref_name": snapshot["headRefName"],
        "base_ref_name": snapshot["baseRefName"],
        "observed_head_sha": snapshot["headRefOid"],
        "tested_head_sha": before["head_sha"],
        "base_sha": snapshot["baseRefOid"],
        "review_digest": snapshot["review_digest"],
        "profile_sha256": _digest(profile),
        "checkout_before": before,
        "checkout_after": after,
        "checks": results,
        "status": "pass"
        if after == before and all(result["status"] == "pass" for result in results)
        else "failed",
    }
    _write_receipt(output, receipt)
    return receipt


def validate_receipt(profile: dict, repo: Path, snapshot: dict, receipt: Path) -> dict:
    """Revalidate local provenance, identities, logs and counts without rerunning gates.

    A normal publication may move the observed upstream head to the exact tested
    head. The original observed_head_sha remains immutable in the receipt; base,
    reviews, profile and every other identity must still agree.
    """
    _require_process_groups()
    checks = _profile_checks(profile)
    _snapshot(profile, snapshot)
    repo = repo.expanduser().resolve()
    if receipt.is_symlink():
        raise ReceiptError("receipt must not be a symlink")
    receipt = _outside_repo(receipt, repo)
    try:
        data = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReceiptError("receipt is unreadable or invalid JSON") from exc
    if not isinstance(data, dict):
        raise ReceiptError("receipt must be a JSON object")
    original_head = data.get("observed_head_sha")
    if not isinstance(original_head, str) or not _SHA.fullmatch(original_head):
        raise ReceiptError("receipt observed head must be a full SHA")
    tested_head = data.get("tested_head_sha")
    if not isinstance(tested_head, str) or not _SHA.fullmatch(tested_head):
        raise ReceiptError("receipt tested head must be a full SHA")
    if snapshot["headRefOid"] not in {original_head, tested_head}:
        raise ReceiptError("upstream head is neither the observed nor the tested head")
    state = _candidate(repo, snapshot)
    _git(repo, "merge-base", "--is-ancestor", original_head, state["head_sha"])
    expected = {
        "schema": _RECEIPT_SCHEMA,
        "provenance": _PROVENANCE,
        "status": "pass",
        "repository": snapshot["repository"],
        "number": snapshot["number"],
        "head_ref_name": snapshot["headRefName"],
        "base_ref_name": snapshot["baseRefName"],
        "observed_head_sha": original_head,
        "tested_head_sha": state["head_sha"],
        "base_sha": snapshot["baseRefOid"],
        "review_digest": snapshot["review_digest"],
        "profile_sha256": _digest(profile),
        "checkout_before": state,
        "checkout_after": state,
    }
    if not isinstance(data, dict) or not _matches(data, expected):
        raise ReceiptError("receipt identity, candidate, profile or status mismatch")
    results = data.get("checks")
    if not isinstance(results, list) or len(results) != len(checks):
        raise ReceiptError("receipt check set differs from profile")
    for index, (check, result) in enumerate(zip(checks, results, strict=True)):
        log = _log_path(receipt, index)
        if log.is_symlink():
            raise ReceiptError("receipt log must not be a symlink")
        log = _outside_repo(log, repo)
        try:
            raw = log.read_bytes()
        except OSError as exc:
            raise ReceiptError("receipt log is unreadable") from exc
        junit = (
            _outside_repo(_junit_path(receipt, index), repo)
            if check.get("junit")
            else None
        )
        junit_sha256 = None
        if junit:
            original_report = _junit_path(receipt, index)
            report = _junit_bytes(original_report)
            junit_sha256 = hashlib.sha256(report).hexdigest()
            counts = _junit_counts(report)
        else:
            counts = _counts(check, raw)
        expected_result = {
            "name": check["name"],
            "argv": check["argv"],
            "executed_argv": [
                argument.replace("{junit}", str(junit)) for argument in check["argv"]
            ]
            if junit
            else check["argv"],
            "vibecrafted_home": str(_outside_repo(_gate_home(log), repo)),
            "junit": check.get("junit", False),
            "junit_path": str(junit) if junit else None,
            "junit_sha256": junit_sha256,
            "timeout_seconds": check["timeout_seconds"],
            "test_counts_regex": check.get("test_counts_regex"),
            "exit_code": 0,
            "timed_out": False,
            "error": "",
            "status": "pass",
            "test_counts": counts,
            "log_path": str(log),
            "log_sha256": hashlib.sha256(raw).hexdigest(),
            "checkout_after": state,
        }
        if not isinstance(result, dict) or not _matches(result, expected_result):
            raise ReceiptError("receipt check identity, result or log mismatch")
        if counts is not None and not (
            counts["passed"] > 0 and counts["failed"] == counts["skipped"] == 0
        ):
            raise ReceiptError("test counts do not prove a nonempty clean pass")
    if _state(repo) != state:
        raise ReceiptError("candidate changed while validating receipt")
    return data
