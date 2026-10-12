"""Standalone ``dispatch-doctor`` CLI: validate a dispatch TOML file, report structured errors."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vibecrafted_core.control_plane import control_plane_home
from vibecrafted_core.repository_claims import (
    ClaimContractError,
    RepositoryClaimRegistry,
)
from vibecrafted_core.runtime_paths import vibecrafted_home
from vibecrafted_core.runtime_receipt import ArtifactIdentityError
from vibecrafted_core.server_config import ServerConfigError, load_server_config

from .model import Dispatch
from .schema import doctor_dispatch
from .worktrees import canonical_artifact_root, repo_identity

PROVIDER_POOLS: dict[str, str] = {
    "agy": "google",
    "claude": "anthropic",
    "codex": "openai",
    "copilot": "github",
    "cursor": "anysphere",
    "grok": "xai",
    "junie": "jetbrains",
    "kimi": "moonshot",
}


@dataclass(frozen=True)
class DoctorError:
    """One structured validation error: the offending path and a message."""

    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        """Render this error as a JSON-safe mapping."""
        return {"path": self.path, "message": self.message}


@dataclass(frozen=True)
class DoctorWarning:
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


@dataclass(frozen=True)
class DoctorReport:
    """Outcome of validating one dispatch file: pass/fail plus structured errors."""

    ok: bool
    errors: tuple[DoctorError, ...]
    warnings: tuple[DoctorWarning, ...] = ()
    dispatch: Dispatch | None = None
    quota_override_receipt: dict[str, Any] | None = None
    quota_observations: tuple[dict[str, Any], ...] = ()
    source_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Render this report as a JSON-safe mapping (omits the parsed ``dispatch``)."""
        result: dict[str, Any] = {
            "ok": self.ok,
            "errors": [error.to_dict() for error in self.errors],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }
        if self.quota_observations:
            result["quota_observations"] = list(self.quota_observations)
        if self.quota_override_receipt is not None:
            result["quota_override_receipt"] = self.quota_override_receipt
        return result


def quota_server_url() -> str:
    """Use the existing server config owner; explicit process origin wins."""
    return os.environ.get("VIBECRAFTED_SERVER_URL") or load_server_config().public_url


def _quota_observations(
    server_url: str, agent: str, model: str
) -> list[dict[str, Any]]:
    parsed = urllib.parse.urlsplit(server_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid quota server origin")
    query = urllib.parse.urlencode({"agent": agent, "model": model})
    req = urllib.request.Request(
        f"{server_url.rstrip('/')}/api/usage/quota?{query}",
        headers={"Accept": "application/json"},
    )
    # Fixed endpoint on the configured control server; never a provider model call.
    # nosemgrep: dynamic-urllib-use-detected
    with urllib.request.urlopen(req, timeout=2.0) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("oversized quota response")

    def reject_constant(value: str) -> None:
        raise ValueError(f"malformed nonfinite quota number: {value}")

    payload = json.loads(raw, parse_constant=reject_constant)
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "vibecrafted.quota-dashboard.v1"
    ):
        raise ValueError("malformed quota response schema")
    items = payload.get("agents")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("malformed quota observations")
    return [item for item in items if item.get("id") == agent]


def _valid_observation(info: dict[str, Any], model: str) -> str:
    """Validate transport evidence, never reinterpret provider usage as quota."""
    fraction = info.get("fraction")
    if fraction is not None:
        try:
            valid_fraction = (
                not isinstance(fraction, bool)
                and isinstance(fraction, (int, float))
                and math.isfinite(fraction)
                and fraction >= 0
            )
        except OverflowError:
            valid_fraction = False
        if not valid_fraction:
            return "malformed fraction"
    if not isinstance(info.get("status"), str) or info.get("status") not in {
        "ok",
        "warn",
        "blocked",
        "unknown",
        "absent",
    }:
        return "malformed status"
    if info.get("status") in {"unknown", "absent"}:
        return str(info.get("detail") or "absent or unknown observation")
    if any(
        not isinstance(info.get(k), str) or not info[k].strip()
        for k in ("source", "pool")
    ):
        return "malformed observation scope/source"
    if any(
        info.get(k) is not None and not isinstance(info[k], str)
        for k in ("model", "account")
    ):
        return "malformed model/account scope"
    if (
        not info.get("source")
        or not info.get("pool")
        or info.get("authoritative") is not True
    ):
        return "unmeasured / non-authoritative observation"
    if info.get("applies") is not True or (
        info.get("model") and info["model"] != model
    ):
        return "observation does not cover the effective model/account/pool"
    try:
        stamp = datetime.fromisoformat(str(info["observed_at"]))
        if stamp.tzinfo is None:
            return "malformed observation time"
        age = (datetime.now(UTC) - stamp).total_seconds()
    except (KeyError, TypeError, ValueError):
        return "malformed observation time"
    if info.get("stale") or age > 300 or age < -30:
        return "stale or future observation"
    return ""


def diagnose_quotas(
    dispatch: Dispatch,
    *,
    ignore_warnings: bool = False,
    server_url: str | None = None,
    home: Path | None = None,
    observation_sink: list[dict[str, Any]] | None = None,
) -> tuple[tuple[DoctorError, ...], tuple[DoctorWarning, ...], dict[str, Any] | None]:
    """Consume server-owned scoped quota observations for the entire admitted plan.

    parse_dispatch already resolves model overrides, frontmatter and config with
    select_plan_model, the same owner used by workflow_cell_launcher. There is
    deliberately no local provider parser/fallback in the doctor.
    """
    errors: list[DoctorError] = []
    warnings: list[DoctorWarning] = []
    observations: list[dict[str, Any]] = []
    overrides: list[dict[str, Any]] = []
    selections: dict[tuple[str, str], list[str]] = {}
    for cut in dispatch.cuts:
        selections.setdefault((cut.agent, cut.model), []).append(cut.id)
    try:
        origin = server_url if server_url is not None else quota_server_url()
    except ServerConfigError as exc:
        origin = ""
        warnings.append(DoctorWarning("quota", f"quota unknown: server config: {exc}"))
    for (agent, model), cuts in selections.items():
        path = f"quota.{PROVIDER_POOLS.get(agent, agent)}"
        try:
            items = _quota_observations(origin, agent, model)
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            ValueError,
            TypeError,
        ) as exc:
            warnings.append(
                DoctorWarning(
                    path,
                    f"quota unknown for '{agent}' / '{model or 'provider default'}': server unavailable or malformed ({type(exc).__name__})",
                )
            )
            items = []
        if not items:
            warnings.append(
                DoctorWarning(
                    path, f"quota unknown for '{agent}': no shared observation"
                )
            )
        for info in items:
            evidence = {
                "agent": agent,
                "effective_model": model,
                "cuts": cuts,
                "observation": info,
            }
            observations.append(evidence)
            if observation_sink is not None:
                observation_sink.append(evidence)
            problem = _valid_observation(info, model)
            if problem:
                warnings.append(
                    DoctorWarning(path, f"quota unknown for '{agent}': {problem}")
                )
                continue
            pool = str(info["pool"])
            path = f"quota.{pool}"
            if info["status"] == "blocked":
                message = f"quota confirmed exhausted for agent '{agent}' (pool '{pool}', model '{model or 'provider default'}')"
                if ignore_warnings:
                    warnings.append(
                        DoctorWarning(
                            path,
                            message + " [overridden via --ignore-usage-quota-warnings]",
                        )
                    )
                    overrides.append(
                        {**evidence, "pool": pool, "reason": "confirmed_exhausted"}
                    )
                else:
                    errors.append(DoctorError(path, message))
            elif info["status"] == "warn":
                warnings.append(
                    DoctorWarning(
                        path,
                        f"quota utilization high for '{agent}': {info.get('detail') or 'low capacity'}",
                    )
                )
    # An explicit bypass always gets an exact decision receipt, even when all
    # quota observations were unknown. Admission publishes it before launch.
    receipt = (
        {
            "schema": "vibecrafted.quota-override.v1",
            "overridden_at": datetime.now(UTC).isoformat(),
            "flag": "--ignore-usage-quota-warnings",
            "server_url": origin,
            "selections": [
                {"agent": a, "model": m, "cuts": ids}
                for (a, m), ids in selections.items()
            ],
            "observations": observations,
            "diagnosis": [w.to_dict() for w in warnings],
            "overrides": overrides,
        }
        if ignore_warnings
        else None
    )
    return tuple(errors), tuple(warnings), receipt


def diagnose_text(text: str, *, base_dir: str | Path | None = None) -> DoctorReport:
    """Validate dispatch TOML text and convert raw error strings to structured errors."""
    result = doctor_dispatch(text, base_dir=base_dir)
    errors = tuple(_structured_error(error) for error in result.errors)
    warnings = tuple(_structured_warning(warning) for warning in result.warnings)
    return DoctorReport(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        dispatch=result.dispatch,
    )


def diagnose_file(
    path: str | Path,
    *,
    run_id: str = "",
    check_quotas: bool = False,
    ignore_usage_quota_warnings: bool = False,
    server_url: str | None = None,
    home: Path | None = None,
) -> DoctorReport:
    """Validate a dispatch TOML file on disk; an unreadable file is reported as an error."""
    source = Path(path).expanduser()
    try:
        text = source.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return DoctorReport(
            ok=False,
            errors=(DoctorError(path=str(source), message=f"unreadable file: {exc}"),),
            warnings=(),
            dispatch=None,
        )
    report = diagnose_text(text, base_dir=source.parent)
    if report.dispatch is None:
        return report
    runtime_errors = diagnose_runtime(report.dispatch, run_id=run_id) if run_id else ()

    quota_errors: tuple[DoctorError, ...] = ()
    quota_warnings: tuple[DoctorWarning, ...] = ()
    quota_receipt: dict[str, Any] | None = None
    observations: list[dict[str, Any]] = []
    if check_quotas or ignore_usage_quota_warnings:
        quota_errors, quota_warnings, quota_receipt = diagnose_quotas(
            report.dispatch,
            ignore_warnings=ignore_usage_quota_warnings,
            server_url=server_url,
            home=home,
            observation_sink=observations,
        )

    all_errors = (*report.errors, *runtime_errors, *quota_errors)
    all_warnings = (*report.warnings, *quota_warnings)
    return DoctorReport(
        ok=not all_errors,
        errors=all_errors,
        warnings=all_warnings,
        dispatch=report.dispatch,
        quota_override_receipt=quota_receipt,
        quota_observations=tuple(observations),
        source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def diagnose_runtime(
    dispatch: Dispatch, *, run_id: str = ""
) -> tuple[DoctorError, ...]:
    """Falsify resolved worktree/report/control-plane receipts for a plan."""
    errors: list[DoctorError] = []
    expected_artifacts: Path | None
    try:
        expected_artifacts = canonical_artifact_root(dispatch.meta.repo).resolve()
        org, repo = repo_identity(dispatch.meta.repo)
    except ArtifactIdentityError as exc:
        errors.append(DoctorError(str(dispatch.meta.repo), str(exc)))
        expected_artifacts = None
        org, repo = "", ""
    expected_worktrees = (
        (
            vibecrafted_home() / "worktrees" / org / repo / expected_artifacts.name
        ).resolve()
        if expected_artifacts is not None
        else None
    )
    dispatches = control_plane_home() / "dispatches"
    receipt_paths = (
        sorted(dispatches.glob("*/receipts.json")) if dispatches.is_dir() else []
    )
    requested_receipt = dispatches / run_id / "receipts.json" if run_id else None
    if requested_receipt is not None and requested_receipt not in receipt_paths:
        errors.append(
            DoctorError(
                str(requested_receipt),
                f"runtime receipt ledger not found for run {run_id}",
            )
        )
    report_owners: dict[str, list[str]] = {}
    active_integrators: list[str] = []
    active_states = {"launching", "active", "reported", "verified", "integrating"}
    for receipt_path in receipt_paths:
        try:
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(DoctorError(str(receipt_path), f"unreadable receipt: {exc}"))
            continue
        payload_run_id = str(payload.get("run_id") or "")
        payload_repo = str(payload.get("repo_root") or "")
        is_requested = bool(run_id and payload_run_id == run_id)
        if payload_repo:
            try:
                same_repo = (
                    Path(payload_repo).resolve()
                    == Path(dispatch.meta.repo).expanduser().resolve()
                )
            except OSError:
                same_repo = False
            if not same_repo and not is_requested:
                continue
        elif not is_requested:
            continue
        if (
            payload.get("configured_concurrency", 1) > 1
            and payload.get("scheduler_mode") != "dag-concurrent"
        ):
            errors.append(
                DoctorError(
                    str(receipt_path),
                    "concurrent plan ran on a serial-only supervisor without a degradation receipt",
                )
            )
        cuts = payload.get("cuts")
        if not isinstance(cuts, dict):
            continue
        for cut_id, raw in cuts.items():
            if not isinstance(raw, dict):
                continue
            state = str(raw.get("state") or "")
            owner = f"{payload.get('run_id') or '?'}:{cut_id}"
            worktree = Path(str(raw.get("worktree_path") or "")).expanduser()
            target = Path(str(raw.get("target_path") or "")).expanduser()
            artifacts = Path(str(raw.get("artifact_path") or "")).expanduser()
            report_path = str(raw.get("report_path") or "")
            integrator = bool(raw.get("integrator_exclusivity"))
            if (
                expected_worktrees is not None
                and worktree
                and str(worktree) != "."
                and not integrator
            ):
                if not _is_within(worktree, expected_worktrees):
                    errors.append(
                        DoctorError(
                            f"cuts.{cut_id}.worktree_path",
                            f"outside canonical worktree plane: {worktree}",
                        )
                    )
                if target.is_symlink():
                    errors.append(
                        DoctorError(
                            f"cuts.{cut_id}.target_path",
                            f"target symlink is forbidden: {target}",
                        )
                    )
                if not _is_within(target, worktree) or target.name != "target":
                    errors.append(
                        DoctorError(
                            f"cuts.{cut_id}.target_path",
                            f"CARGO_TARGET_DIR must be {worktree / 'target'}",
                        )
                    )
            if (
                expected_artifacts is not None
                and artifacts
                and str(artifacts) != "."
                and artifacts.resolve() != expected_artifacts
            ):
                errors.append(
                    DoctorError(
                        f"cuts.{cut_id}.artifact_path",
                        f"outside canonical artifact plane: {artifacts}",
                    )
                )
            if report_path:
                report_owners.setdefault(
                    str(Path(report_path).expanduser().resolve()), []
                ).append(owner)
                if expected_artifacts is not None and not _is_within(
                    Path(report_path), expected_artifacts
                ):
                    errors.append(
                        DoctorError(
                            f"cuts.{cut_id}.report_path",
                            f"outside canonical artifact plane: {report_path}",
                        )
                    )
            if integrator and state in active_states:
                active_integrators.append(owner)
            if state in active_states and raw.get("cleanup_status") == "removed":
                errors.append(
                    DoctorError(
                        f"cuts.{cut_id}.cleanup_status", "active cut was cleaned"
                    )
                )
    for report_path, owners in report_owners.items():
        if len(owners) > 1:
            errors.append(
                DoctorError(
                    "receipts.report_path",
                    f"duplicate report path {report_path}: {', '.join(owners)}",
                )
            )
    if len(active_integrators) > 1:
        errors.append(
            DoctorError(
                "receipts.integrators",
                f"multiple active integrators for {org}/{repo}: {', '.join(active_integrators)}",
            )
        )
    try:
        claim_health = RepositoryClaimRegistry().health(repo=dispatch.meta.repo)
    except ClaimContractError as exc:
        errors.append(
            DoctorError("repository_claims", f"claim registry unhealthy: {exc}")
        )
    else:
        for claim in claim_health["stale_claims"]:
            errors.append(
                DoctorError(
                    f"repository_claims.{claim.get('claim_id')}",
                    "stale mutation claim: "
                    f"owner {claim.get('run_id') or '?'}/{claim.get('session_id') or '?'} "
                    f"pid {claim.get('pid') or '?'} is {claim.get('owner_liveness')}; "
                    f"paths {', '.join(claim.get('owned_paths') or [])}; "
                    f"reclaimable={claim.get('reclaimable')}",
                )
            )
        for conflict in claim_health["conflicts"]:
            errors.append(
                DoctorError(
                    "repository_claims.conflicts",
                    "stored mutation claims overlap: "
                    f"{conflict.get('left_claim_id')} and {conflict.get('right_claim_id')} "
                    f"at {conflict.get('overlapping_paths')}",
                )
            )
    return tuple(errors)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint: validate a dispatch file, print the report, and return exit code."""
    parser = argparse.ArgumentParser(
        prog="dispatch-doctor",
        description="Validate a vibecrafted.dispatch.v1 TOML file.",
    )
    parser.add_argument("dispatch_file")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--run-id", default="", help="also validate one runtime receipt ledger"
    )
    parser.add_argument(
        "--check-quotas",
        action="store_true",
        default=False,
        help="preflight check agent quota pools",
    )
    parser.add_argument(
        "--ignore-usage-quota-warnings",
        action="store_true",
        default=False,
        help="bypass quota exhaustion errors and record override receipt",
    )
    args = parser.parse_args(argv)

    report = diagnose_file(
        args.dispatch_file,
        run_id=args.run_id,
        check_quotas=args.check_quotas or args.ignore_usage_quota_warnings,
        ignore_usage_quota_warnings=args.ignore_usage_quota_warnings,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        for error in report.errors:
            print(f"{error.path}: {error.message}")
        for warning in report.warnings:
            print(f"warning: {warning.path}: {warning.message}")
        if report.ok:
            print("dispatch-doctor: ok")
    return 0 if report.ok else 1


def _structured_error(error: str) -> DoctorError:
    """Split a "path: message" error string into a structured ``DoctorError``."""
    path, separator, message = error.partition(":")
    if not separator:
        return DoctorError(path="dispatch", message=error)
    return DoctorError(path=path.strip() or "dispatch", message=message.strip())


def _structured_warning(warning: str) -> DoctorWarning:
    path, separator, message = warning.partition(":")
    if not separator:
        return DoctorWarning(path="dispatch", message=warning)
    return DoctorWarning(path=path.strip() or "dispatch", message=message.strip())


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(root.expanduser().resolve())
    except (OSError, ValueError):
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
