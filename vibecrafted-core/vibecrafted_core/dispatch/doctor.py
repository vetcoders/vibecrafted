"""Standalone ``dispatch-doctor`` CLI: validate a dispatch TOML file, report structured errors."""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, replace
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

STALE_SNAPSHOT_SECONDS = 300.0
HIGH_UTILIZATION_FRACTION = 0.85


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

    def to_dict(self) -> dict[str, Any]:
        """Render this report as a JSON-safe mapping (omits the parsed ``dispatch``)."""
        result: dict[str, Any] = {
            "ok": self.ok,
            "errors": [error.to_dict() for error in self.errors],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }
        if self.quota_override_receipt is not None:
            result["quota_override_receipt"] = self.quota_override_receipt
        return result


def _parse_timestamp(val: Any) -> float | None:
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        parsed = float(val)
        return parsed if math.isfinite(parsed) else None
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        try:
            parsed = float(val)
            return parsed if math.isfinite(parsed) else None
        except ValueError:
            pass
        try:
            iso = val.replace("Z", "+00:00")
            return datetime.fromisoformat(iso).timestamp()
        except (ValueError, TypeError):
            return None
    return None


def _safe_fraction(value: Any) -> float | None:
    """Parse a quota fraction from an untrusted JSON value; None when malformed."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = float(text)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _field_fraction(raw: Any, label: str, malformed_fields: list[str]) -> float | None:
    """Parse one present fraction field; record the label when malformed."""
    parsed = _safe_fraction(raw)
    if parsed is None:
        malformed_fields.append(label)
    return parsed


def _max_bar_ratio(info: dict[str, Any]) -> float | None:
    bars = info.get("bars")
    if not isinstance(bars, list):
        return None
    ratios = [
        ratio
        for ratio in (
            _safe_fraction(bar.get("ratio")) for bar in bars if isinstance(bar, dict)
        )
        if ratio is not None
    ]
    return max(ratios) if ratios else None


def default_server_url() -> tuple[str | None, str | None]:
    """Resolve the operator-configured vc-server origin for quota preflight.

    Returns ``(url, note)``: ``url`` is the configured public URL (or the
    default loopback origin), ``note`` is a human-readable reason when the
    configuration could not be read and only local snapshots will be used.
    """
    try:
        config = load_server_config()
    except ServerConfigError as exc:
        return (
            None,
            f"server config unreadable; quota preflight uses local snapshots only: {exc}",
        )
    url = config.public_url.strip()
    return (url or None), None


def _resolve_candidate_files(agent: str, home: Path | None = None) -> list[Path]:
    base_home = home if home is not None else vibecrafted_home()
    out = [
        base_home / "telemetry" / agent / "quota.json",
        base_home / "telemetry" / agent / "scan-cache.json",
    ]
    user_home = Path.home()
    if agent == "agy":
        out.append(user_home / ".gemini" / "agy-monitor" / "runtime" / "quota.json")
        out.append(user_home / ".gemini" / "agy-monitor" / "runtime" / "snapshot.json")
    elif agent == "kimi":
        out.append(user_home / ".kimi-code" / "runtime" / "quota.json")
        out.append(user_home / ".kimi-code" / "runtime" / "snapshot.json")
    return out


def _needed_quota_scopes(dispatch: Dispatch) -> dict[str, dict[str, Any]]:
    """Effective per-agent quota demand: the shared pool plus resolved models.

    ``cut.model``/``cut.model_source`` are the canonical effective selection
    resolved from CLI override, plan frontmatter, configured defaults, or the
    provider default (see ``workflow.select_plan_model`` via the dispatch
    schema), so the quota gate scopes itself to what would actually launch.
    """
    needed: dict[str, dict[str, Any]] = {}
    for cut in dispatch.cuts:
        agent = cut.agent.strip().lower() if cut.agent else ""
        if not agent:
            continue
        entry = needed.setdefault(
            agent, {"pool": PROVIDER_POOLS.get(agent, agent), "models": {}}
        )
        if cut.model:
            entry["models"][cut.model] = cut.model_source
    return needed


def _scope_suffix(entry: dict[str, Any]) -> str:
    models = entry["models"]
    if not models:
        model_note = "model provider-default"
    else:
        model_note = "model " + ", ".join(
            f"{model} (via {source})" for model, source in sorted(models.items())
        )
    return f"pool '{entry['pool']}'; {model_note}"


def _fetch_server_quota_cards(server_url: str) -> dict[str, dict[str, Any]] | None:
    """Fetch the server's projected quota cards; None when unreachable/invalid.

    The server response is the interpreted truth (status/stale/scope computed
    by vc-server); the doctor consumes it instead of re-deriving quota state.
    """
    parsed_url = urllib.parse.urlsplit(server_url)
    if parsed_url.scheme not in ("http", "https"):
        return None
    try:
        url = f"{server_url.rstrip('/')}/api/usage/quota"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        # Server probe constructs an HTTP URL to the fixed internal quota endpoint.
        # nosemgrep: dynamic-urllib-use-detected
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            if resp.status != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    agents = payload.get("agents") if isinstance(payload, dict) else None
    if not isinstance(agents, list):
        return None
    cards: dict[str, dict[str, Any]] = {}
    for item in agents:
        if not isinstance(item, dict):
            continue
        key = item.get("agent") or item.get("id")
        if isinstance(key, str) and key.strip():
            cards[key.strip().lower()] = item
    return cards


def _quota_observation_note(
    source: str,
    age_s: float | None,
    account: str | None,
    card_model: str | None,
) -> str:
    parts = [f"source {source}"]
    if age_s is not None:
        parts.append(f"observed {int(age_s)}s ago")
    else:
        parts.append("observation time unknown")
    if account:
        parts.append(f"account {account}")
    if card_model:
        parts.append(f"observed model {card_model}")
    return "; ".join(parts)


def diagnose_quotas(
    dispatch: Dispatch,
    *,
    ignore_warnings: bool = False,
    server_url: str | None = None,
    home: Path | None = None,
) -> tuple[tuple[DoctorError, ...], tuple[DoctorWarning, ...], dict[str, Any] | None]:
    needed = _needed_quota_scopes(dispatch)

    if not needed:
        return (), (), None

    server_agents: dict[str, dict[str, Any]] = {}
    server_attempted = bool(server_url)
    if server_url:
        fetched = _fetch_server_quota_cards(server_url)
        if fetched is not None:
            server_agents = fetched

    errors: list[DoctorError] = []
    warnings: list[DoctorWarning] = []
    overrides: list[dict[str, Any]] = []

    now = time.time()

    for agent, need in needed.items():
        pool = need["pool"]
        scope = _scope_suffix(need)
        info = server_agents.get(agent)
        if info is not None:
            status = str(info.get("status") or "unknown").strip().lower()
            stale = bool(info.get("stale", False))
            age_s = _safe_fraction(info.get("age_s"))
            source = str(info.get("source") or "vc-server")
            detail = str(info.get("detail") or "").strip()
            card_pool = str(info.get("pool") or pool).strip().lower() or pool
            account = (
                info.get("account") if isinstance(info.get("account"), str) else None
            )
            card_model = (
                info.get("model") if isinstance(info.get("model"), str) else None
            )
            fraction = _max_bar_ratio(info)
            note = _quota_observation_note(source, age_s, account, card_model)

            if status == "blocked":
                if stale:
                    warnings.append(
                        DoctorWarning(
                            f"quota.{card_pool}",
                            f"quota snapshot for agent '{agent}' is stale"
                            + (f" ({int(age_s)}s old)" if age_s is not None else "")
                            + " and reported exhaustion; treating as unconfirmed warning"
                            + f" ({scope}; {note})",
                        )
                    )
                elif ignore_warnings:
                    warnings.append(
                        DoctorWarning(
                            f"quota.{card_pool}",
                            f"quota confirmed exhausted for agent '{agent}' "
                            f"[overridden via --ignore-usage-quota-warnings] ({scope}; {note})",
                        )
                    )
                    overrides.append(
                        {
                            "agent": agent,
                            "pool": card_pool,
                            "fraction": fraction,
                            "age_s": age_s,
                            "model": card_model,
                            "account": account,
                            "planned_models": dict(need["models"]),
                            "source": source,
                            "reason": "confirmed_exhausted",
                        }
                    )
                else:
                    errors.append(
                        DoctorError(
                            f"quota.{card_pool}",
                            f"quota confirmed exhausted for agent '{agent}' ({scope}; {note})",
                        )
                    )
            elif status == "warn":
                warnings.append(
                    DoctorWarning(
                        f"quota.{card_pool}",
                        f"quota warning for agent '{agent}': "
                        f"{detail or 'high utilization'} ({scope}; {note})",
                    )
                )
            elif status in {"unknown", "absent"}:
                warnings.append(
                    DoctorWarning(
                        f"quota.{card_pool}",
                        f"quota for agent '{agent}' is unknown (server status: {status}; "
                        f"{scope}; {note})",
                    )
                )
            continue

        server_gap = ""
        if server_attempted:
            server_gap = (
                "; server unreachable, local snapshots only"
                if not server_agents
                else "; no server card, local snapshots only"
            )

        candidates = _resolve_candidate_files(agent, home=home)
        found_file = False
        parsed_data: Any = None
        target_path: Path | None = None
        malformed_note = ""

        for path in candidates:
            if path.is_file():
                found_file = True
                target_path = path
                try:
                    parsed_data = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(parsed_data, dict):
                        malformed_note = "top-level JSON is not an object"
                        parsed_data = None
                except (OSError, json.JSONDecodeError) as exc:
                    malformed_note = str(exc)
                    parsed_data = None
                break

        if not found_file:
            warnings.append(
                DoctorWarning(
                    f"quota.{pool}",
                    f"quota for agent '{agent}' is unknown: no local observation "
                    f"({scope}; looked in {', '.join(str(p) for p in candidates)}"
                    f"{server_gap})",
                )
            )
            continue

        if parsed_data is None:
            warnings.append(
                DoctorWarning(
                    f"quota.{pool}",
                    f"quota snapshot for agent '{agent}' at {target_path} is malformed: "
                    f"{malformed_note} ({scope})",
                )
            )
            continue

        payload = parsed_data
        ts_val = (
            payload.get("timestamp")
            or payload.get("generated_at")
            or payload.get("updated_at")
        )
        parsed_ts = _parse_timestamp(ts_val)
        if parsed_ts is not None:
            age_s = max(0.0, now - parsed_ts)
        elif target_path is not None:
            try:
                age_s = max(0.0, now - target_path.stat().st_mtime)
            except OSError:
                age_s = 0.0
        else:
            age_s = 0.0

        stale = age_s > STALE_SNAPSHOT_SECONDS

        if agent == "cursor":
            warnings.append(
                DoctorWarning(
                    f"quota.{pool}",
                    f"quota for agent '{agent}' is unmeasured (cloud-only) ({scope})",
                )
            )
            continue

        fraction: float | None = None
        malformed_fields: list[str] = []
        is_exhausted = False
        is_unmeasured = False

        if agent == "kimi":
            limits = (
                payload.get("limits") if isinstance(payload.get("limits"), dict) else {}
            )
            f5 = (
                limits.get("five_hour", {}).get("fraction")
                if isinstance(limits.get("five_hour"), dict)
                else None
            )
            fm = (
                limits.get("monthly", {}).get("fraction")
                if isinstance(limits.get("monthly"), dict)
                else None
            )
            fracs = [
                parsed
                for raw, label in (
                    (f5, "limits.five_hour.fraction"),
                    (fm, "limits.monthly.fraction"),
                )
                if raw is not None
                for parsed in [_field_fraction(raw, label, malformed_fields)]
                if parsed is not None
            ]
            fraction = max(fracs) if fracs else None
            if fraction is None and payload.get("fraction") is not None:
                fraction = _field_fraction(
                    payload.get("fraction"), "fraction", malformed_fields
                )
            status_str = str(payload.get("status") or "")
            is_exhausted = (fraction is not None and fraction >= 1.0) or status_str in {
                "blocked",
                "exhausted",
                "RESOURCE_EXHAUSTED",
            }
        elif agent == "agy":
            quota_obj = (
                payload.get("quota") if isinstance(payload.get("quota"), dict) else {}
            )
            if quota_obj.get("fraction") is not None:
                fraction = _field_fraction(
                    quota_obj.get("fraction"), "quota.fraction", malformed_fields
                )
            elif payload.get("fraction") is not None:
                fraction = _field_fraction(
                    payload.get("fraction"), "fraction", malformed_fields
                )
            elif payload.get("utilization") is not None:
                fraction = _field_fraction(
                    payload.get("utilization"), "utilization", malformed_fields
                )
            status_str = str(quota_obj.get("status") or payload.get("status") or "")
            is_exhausted = (fraction is not None and fraction >= 1.0) or status_str in {
                "blocked",
                "exhausted",
                "RESOURCE_EXHAUSTED",
            }
        else:
            quota_obj = (
                payload.get("quota") if isinstance(payload.get("quota"), dict) else {}
            )
            status_str = str(quota_obj.get("status") or payload.get("status") or "")
            if quota_obj.get("fraction") is not None:
                fraction = _field_fraction(
                    quota_obj.get("fraction"), "quota.fraction", malformed_fields
                )
            elif payload.get("fraction") is not None:
                fraction = _field_fraction(
                    payload.get("fraction"), "fraction", malformed_fields
                )

            if status_str in {"unavailable", "empty"} or fraction is None:
                is_unmeasured = True
            else:
                is_exhausted = (fraction >= 1.0) or status_str in {
                    "blocked",
                    "exhausted",
                    "RESOURCE_EXHAUSTED",
                }

        if is_exhausted:
            if stale:
                warnings.append(
                    DoctorWarning(
                        f"quota.{pool}",
                        f"quota snapshot for agent '{agent}' is stale ({int(age_s)}s old) "
                        f"and reported exhaustion; treating as unconfirmed warning ({scope})",
                    )
                )
            elif ignore_warnings:
                warnings.append(
                    DoctorWarning(
                        f"quota.{pool}",
                        f"quota confirmed exhausted for agent '{agent}' "
                        f"[overridden via --ignore-usage-quota-warnings] ({scope})",
                    )
                )
                overrides.append(
                    {
                        "agent": agent,
                        "pool": pool,
                        # None when exhaustion came from an explicit status with
                        # no measured fraction; the receipt carries what was
                        # actually observed, never an invented number.
                        "fraction": fraction,
                        "age_s": age_s,
                        "model": None,
                        "account": None,
                        "planned_models": dict(need["models"]),
                        "source": str(target_path) if target_path else "local",
                        "reason": "confirmed_exhausted",
                    }
                )
            else:
                errors.append(
                    DoctorError(
                        f"quota.{pool}",
                        f"quota confirmed exhausted for agent '{agent}' ({scope})",
                    )
                )
        elif malformed_fields:
            warnings.append(
                DoctorWarning(
                    f"quota.{pool}",
                    f"quota snapshot for agent '{agent}' at {target_path} has malformed "
                    f"numeric field(s) {', '.join(malformed_fields)}; treating quota as "
                    f"unknown ({scope})",
                )
            )
        elif is_unmeasured:
            warnings.append(
                DoctorWarning(
                    f"quota.{pool}",
                    f"quota for agent '{agent}' is unmeasured ({scope})",
                )
            )
        elif fraction is not None and fraction >= HIGH_UTILIZATION_FRACTION:
            warnings.append(
                DoctorWarning(
                    f"quota.{pool}",
                    f"quota utilization high for agent '{agent}' ({int(fraction * 100)}%) "
                    f"({scope})",
                )
            )

    receipt = (
        {
            "overridden_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "flag": "--ignore-usage-quota-warnings",
            "overrides": overrides,
        }
        if overrides
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
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
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
    if check_quotas or ignore_usage_quota_warnings:
        quota_errors, quota_warnings, quota_receipt = diagnose_quotas(
            report.dispatch,
            ignore_warnings=ignore_usage_quota_warnings,
            server_url=server_url,
            home=home,
        )

    all_errors = (*report.errors, *runtime_errors, *quota_errors)
    all_warnings = (*report.warnings, *quota_warnings)
    return DoctorReport(
        ok=not all_errors,
        errors=all_errors,
        warnings=all_warnings,
        dispatch=report.dispatch,
        quota_override_receipt=quota_receipt,
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

    server_url: str | None = None
    server_note: str | None = None
    if args.check_quotas or args.ignore_usage_quota_warnings:
        server_url, server_note = default_server_url()
    report = diagnose_file(
        args.dispatch_file,
        run_id=args.run_id,
        check_quotas=args.check_quotas or args.ignore_usage_quota_warnings,
        ignore_usage_quota_warnings=args.ignore_usage_quota_warnings,
        server_url=server_url,
    )
    if server_note:
        report = replace(
            report,
            warnings=(*report.warnings, DoctorWarning("quota.server", server_note)),
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
