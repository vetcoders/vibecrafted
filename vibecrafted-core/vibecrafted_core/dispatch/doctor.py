"""Standalone ``dispatch-doctor`` CLI: validate a dispatch TOML file, report structured errors."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vibecrafted_core.control_plane import control_plane_home
from vibecrafted_core.runtime_paths import vibecrafted_home

from .model import Dispatch
from .schema import doctor_dispatch
from .worktrees import canonical_artifact_root, repo_identity


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

    def to_dict(self) -> dict[str, Any]:
        """Render this report as a JSON-safe mapping (omits the parsed ``dispatch``)."""
        return {
            "ok": self.ok,
            "errors": [error.to_dict() for error in self.errors],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


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


def diagnose_file(path: str | Path, *, run_id: str = "") -> DoctorReport:
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
    return DoctorReport(
        ok=not report.errors and not runtime_errors,
        errors=(*report.errors, *runtime_errors),
        warnings=report.warnings,
        dispatch=report.dispatch,
    )


def diagnose_runtime(
    dispatch: Dispatch, *, run_id: str = ""
) -> tuple[DoctorError, ...]:
    """Falsify resolved worktree/report/control-plane receipts for a plan."""
    errors: list[DoctorError] = []
    expected_artifacts = canonical_artifact_root(dispatch.meta.repo).resolve()
    org, repo = repo_identity(dispatch.meta.repo)
    expected_worktrees = (
        vibecrafted_home() / "worktrees" / org / repo / expected_artifacts.name
    ).resolve()
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
            if worktree and str(worktree) != "." and not integrator:
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
                artifacts
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
                if not _is_within(Path(report_path), expected_artifacts):
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
    args = parser.parse_args(argv)

    report = diagnose_file(args.dispatch_file, run_id=args.run_id)
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
