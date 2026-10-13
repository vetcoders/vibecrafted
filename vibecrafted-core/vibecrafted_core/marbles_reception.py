"""Strict READ-phase evidence and the single sequential Marbles ledger reducer.

Prose is never coerced into findings. This reducer accepts only an explicitly
attested receiver document bound to the candidate and the current Git tree.
It does not dispatch, write code, select providers or accept operator debt.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = "vibecrafted.marbles-reception.v1"
WEIGHTS = {"high": 3, "medium": 2, "low": 1}
SETS = ("attacked", "resolved", "still_open", "discovered", "regressions")


def readonly_command(
    root: str, command: list[str], provider: str = ""
) -> tuple[list[str], dict[str, str]]:
    """Confine every receiver provider to read-only source and Git metadata.

    Codex's restrictive inner tool sandbox cannot start under the outer guard,
    and native read-only also refuses needed external verification outputs.
    Use one scoped external boundary; Codex explicitly bypasses the inner
    sandbox only while this source/Git write-deny profile is active.
    Unsupported hosts refuse reception rather than fall back to prompt-only READ.
    """
    path = Path(root).resolve()
    if provider == "codex" and not (
        "--dangerously-bypass-approvals-and-sandbox" in command
        or 'sandbox_mode="danger-full-access"' in command
    ):
        raise ValueError("receiver_codex_inner_sandbox_not_disabled")
    sandbox = (
        "/usr/bin/sandbox-exec"
        if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file()
        else None
    )
    if not sandbox:
        raise ValueError(
            "receiver_source_guard_unavailable:requires_macos_sandbox_exec"
        )
    result = subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    common = str(Path(result.stdout.strip()).resolve())
    profile = (
        "(version 1) (allow default) (deny file-write* "
        + " ".join(
            f"(subpath {json.dumps(value, ensure_ascii=False)})"
            for value in (str(path), common)
        )
        + ")"
    )
    return [sandbox, "-p", profile, *command], {
        "kind": "macos_source_write_deny",
        "provider": provider,
        "native_tool_sandbox": "bypassed_under_external_guard"
        if provider == "codex"
        else "provider_default",
        "root": str(path),
        "git_common_dir": common,
        "boundary": "assigned source and shared Git metadata; provider stores/artifacts outside remain writable",
    }


def tree_receipt(root: str) -> dict[str, str]:
    """Bind reception to branch, HEAD and content, including untracked files.

    Ignored runtime artifacts are outside source truth. Dirty source is hashed
    so an informed receiver cannot silently modify the candidate it evaluates.
    """
    path = Path(root).resolve()

    def git(*args: str) -> bytes:
        result = subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, timeout=15, check=True
        )
        return result.stdout

    head = git("rev-parse", "HEAD").decode().strip()
    branch = git("symbolic-ref", "--quiet", "--short", "HEAD").decode().strip()
    paths = git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split(
        b"\0"
    )
    digest = hashlib.sha256()
    for raw in sorted(set(paths) - {b""}):
        item = path / raw.decode("utf-8", errors="surrogateescape")
        digest.update(raw + b"\0")
        if item.is_symlink():
            digest.update(b"link:" + str(item.readlink()).encode())
        elif item.is_file():
            digest.update(str(item.stat().st_mode).encode() + b":" + item.read_bytes())
        else:
            digest.update(b"missing")
    return {
        "root": str(path),
        "branch": branch,
        "head": head,
        "source_hash": digest.hexdigest(),
    }


def read_evidence(
    report: Path, *, candidate_run_id: str, tree: dict[str, str]
) -> dict[str, Any]:
    """Validate one explicit schema block, refusing incomplete/contradictory sets."""
    blocks = re.findall(
        r"^```marbles-reception\s*\n(.*?)^```\s*$",
        report.read_text(),
        re.MULTILINE | re.DOTALL,
    )
    if len(blocks) != 1:
        raise ValueError("reception_block_missing_or_ambiguous")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate_key:{key}")
            value[key] = item
        return value

    data = json.loads(blocks[0], object_pairs_hook=unique_object)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("unsupported_reception_schema")
    for key, expected in {
        "candidate_run_id": candidate_run_id,
        "candidate_sha": tree["head"],
        "root": tree["root"],
    }.items():
        if data.get(key) != expected:
            raise ValueError(f"reception_identity_mismatch:{key}")
    if type(data.get("scope_verified")) is not bool:
        raise ValueError("scope_verification_missing")
    for key in ("scope_evidence", "blocker"):
        if not isinstance(data.get(key), str) or (
            key == "scope_evidence" and not data[key].strip()
        ):
            raise ValueError(f"invalid_reception_field:{key}")
    if data.get("gate") not in {"pass", "fail", "unknown"}:
        raise ValueError("invalid_gate")
    checks = data.get("checks")
    if not isinstance(checks, list) or (data["gate"] != "unknown" and not checks):
        raise ValueError("verification_checks_missing")
    for check in checks:
        if not isinstance(check, dict) or type(check.get("exit_code")) is not int:
            raise ValueError("invalid_verification_check")
        for key in ("command", "evidence"):
            if not isinstance(check.get(key), str) or not check[key].strip():
                raise ValueError(f"invalid_verification_check:{key}")
    if data["gate"] == "pass" and any(c["exit_code"] != 0 for c in checks):
        raise ValueError("gate_contradicts_checks")
    if data["gate"] == "fail" and not any(c["exit_code"] != 0 for c in checks):
        raise ValueError("gate_contradicts_checks")
    identities: dict[str, tuple[str, str]] = {}
    for name in SETS:
        items = data.get(name)
        if not isinstance(items, list):
            raise TypeError(f"finding_set_missing:{name}")
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise TypeError(f"invalid_finding:{name}")
            for key in ("id", "cluster", "evidence"):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    raise ValueError(f"invalid_finding:{key}")
            if item.get("severity") not in WEIGHTS or item["id"] in seen:
                raise ValueError("invalid_or_duplicate_finding")
            identity = (item["severity"], item["cluster"])
            if item["id"] in identities and identities[item["id"]] != identity:
                raise ValueError("conflicting_finding_identity")
            identities[item["id"]] = identity
            seen.add(item["id"])
    resolved = {item["id"] for item in data["resolved"]}
    opened = {
        item["id"]
        for name in ("still_open", "discovered", "regressions")
        for item in data[name]
    }
    if resolved & opened:
        raise ValueError("resolved_finding_still_open")
    if not resolved <= {item["id"] for item in data["attacked"]}:
        raise ValueError("resolution_not_attacked")
    if not {item["id"] for item in data["attacked"]} <= resolved | opened:
        raise ValueError("attacked_finding_without_disposition")
    return data


def receive(
    previous: dict[str, Any] | None,
    evidence: dict[str, Any],
    *,
    report: Path,
    no_source_progress: bool,
) -> dict[str, Any]:
    """Merge verified receiver sets, clamp optimism and route bounded continuation."""
    prior = {item["id"]: item for item in (previous or {}).get("open_findings", [])}
    sets = {name: {item["id"]: item for item in evidence[name]} for name in SETS}
    for values in sets.values():
        for key, value in values.items():
            if key in prior and (value["severity"], value["cluster"]) != (
                prior[key]["severity"],
                prior[key]["cluster"],
            ):
                raise ValueError("finding_identity_changed")
    current = {
        key: value for key, value in prior.items() if key not in sets["resolved"]
    }
    for name in ("still_open", "discovered", "regressions"):
        current.update(sets[name])

    def weight(items: dict[str, Any]) -> int:
        return sum(WEIGHTS[item["severity"]] for item in items.values())

    closed = {key: prior[key] for key in prior.keys() & sets["resolved"].keys()}
    instant = {
        key: value for key, value in sets["resolved"].items() if key not in prior
    }
    opened = {
        key: value
        for name in ("discovered", "regressions")
        for key, value in sets[name].items()
        if key not in prior
    }
    delta_raw = len(prior) - len(current)
    delta_weighted = weight(prior) - weight(current)
    stepper = (weight(closed) + weight(instant) - weight(opened)) / max(
        weight(sets["attacked"]), 1
    )
    rate = delta_weighted / max(weight(prior), 1)
    if previous is None:
        delta_raw = delta_weighted = 0
        stepper = rate = 0.0
    if evidence["gate"] != "pass":
        stepper, rate = min(stepper, 0), min(rate, 0)
    clusters: dict[str, int] = {}
    for item in current.values():
        clusters[item["cluster"]] = (
            clusters.get(item["cluster"], 0) + WEIGHTS[item["severity"]]
        )
    dominant = min(clusters, key=lambda key: (-clusters[key], key)) if clusters else ""
    old = previous or {}
    gate_failures = (
        int(old.get("gate_failures", 0)) + 1
        if evidence["gate"] == "fail"
        and dominant == old.get("dominant_cluster", dominant)
        else 1
        if evidence["gate"] == "fail"
        else 0
    )
    negative_rounds = (
        int(old.get("negative_rounds", 0)) + 1 if delta_weighted < 0 else 0
    )
    neutral_rounds = int(old.get("neutral_rounds", 0)) + 1 if stepper <= 0 else 0
    no_progress_rounds = (
        int(old.get("no_progress_rounds", 0)) + 1
        if no_source_progress and delta_weighted <= 0
        else 0
    )
    reason = "open_findings_remain"
    decision, verdict = "continue", "not_converged"
    if (
        evidence["blocker"]
        or not evidence["scope_verified"]
        or evidence["gate"] == "unknown"
    ):
        decision, verdict = "escalate", "blocked"
        reason = evidence["blocker"] or "verification_incomplete"
    elif gate_failures >= 2 or negative_rounds >= 2 or no_progress_rounds >= 2:
        decision, verdict, reason = (
            "escalate",
            "blocked",
            "repeated_failure_or_no_progress",
        )
    elif not current and evidence["gate"] == "pass":
        decision, verdict, reason = "stop", "converged", "verified_ledger_empty"
    elif neutral_rounds >= 2 or (previous and dominant != old.get("dominant_cluster")):
        decision, reason = "shift_surface", "dominant_risk_or_neutral_trajectory"
    return {
        "schema": SCHEMA,
        "open_findings": list(current.values()),
        "accepted_report": str(report),
        "candidate_run_id": evidence["candidate_run_id"],
        "candidate_sha": evidence["candidate_sha"],
        "gate": evidence["gate"],
        "checks": evidence["checks"],
        "scope_evidence": evidence["scope_evidence"],
        "closed": sorted(closed),
        "opened": sorted(opened),
        "instant_fixes": sorted(instant),
        "delta_raw": delta_raw,
        "delta_weighted": delta_weighted,
        "stepper": stepper,
        "convergence_rate": rate,
        "dominant_cluster": dominant,
        "gate_failures": gate_failures,
        "negative_rounds": negative_rounds,
        "neutral_rounds": neutral_rounds,
        "no_progress_rounds": no_progress_rounds,
        "decision": decision,
        "verdict": verdict,
        "reason": reason,
    }
