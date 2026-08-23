"""Canonical, provider-neutral linked-checkout geometry for dispatch workers."""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..runtime_paths import vibecrafted_home


class WorktreeContractError(RuntimeError):
    """Raised when a worker checkout cannot satisfy isolation ownership."""


@dataclass(frozen=True)
class WorktreeGeometry:
    """Resolved checkout and storage planes for one cut."""

    org: str
    repo: str
    day: str
    cut_id: str
    worktree_path: str
    branch: str
    baseline_sha: str
    target_path: str
    artifact_path: str
    integrator_exclusive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def repo_identity(repo: str | Path) -> tuple[str, str]:
    """Resolve a stable ``(org, repo)`` from origin, with local fallbacks."""
    root = Path(repo).expanduser().resolve()
    remote = _git(root, "remote", "get-url", "origin")
    tail = remote.strip().removesuffix(".git")
    if ":" in tail and "://" not in tail:
        tail = tail.split(":", 1)[1]
    elif "://" in tail:
        tail = tail.split("://", 1)[1]
        tail = tail.split("/", 1)[1] if "/" in tail else ""
    parts = [part for part in tail.strip("/").split("/") if part]
    org = parts[-2] if len(parts) >= 2 else "local"
    name = parts[-1] if parts else root.name
    return _safe_component(org, "local"), _safe_component(name, "repo")


def canonical_artifact_root(repo: str | Path, *, day: str | None = None) -> Path:
    """Return the durable, agent-agnostic artifact root for this repository."""
    org, name = repo_identity(repo)
    stamp = day or datetime.now(UTC).strftime("%Y_%m%d")
    return vibecrafted_home() / "artifacts" / org / name / stamp


class WorktreeManager:
    """Create, validate, reuse, and remove canonical per-cut worktrees."""

    def __init__(self, main_repo: str | Path, *, day: str | None = None) -> None:
        self.main_repo = Path(main_repo).expanduser().resolve()
        self.org, self.repo = repo_identity(self.main_repo)
        self.day = day or datetime.now(UTC).strftime("%Y_%m%d")
        self.worktree_root = (
            vibecrafted_home() / "worktrees" / self.org / self.repo / self.day
        )
        self.artifact_root = canonical_artifact_root(self.main_repo, day=self.day)

    def geometry(
        self, cut_id: str, baseline_sha: str, *, integrator: bool
    ) -> WorktreeGeometry:
        safe_cut = _safe_component(cut_id, "cut")
        if safe_cut != cut_id:
            raise WorktreeContractError(
                f"cut id {cut_id!r} is not path-safe; use letters, numbers, '.', '_' or '-'"
            )
        root = self.main_repo if integrator else self.worktree_root / safe_cut
        branch = (
            _git(self.main_repo, "branch", "--show-current")
            if integrator
            else f"cut/{safe_cut}"
        )
        return WorktreeGeometry(
            org=self.org,
            repo=self.repo,
            day=self.day,
            cut_id=cut_id,
            worktree_path=str(root),
            branch=branch,
            baseline_sha=baseline_sha,
            target_path=str(root / "target"),
            artifact_path=str(self.artifact_root),
            integrator_exclusive=integrator,
        )

    def prepare(
        self,
        cut_id: str,
        baseline_sha: str,
        *,
        integrator: bool = False,
        allow_reuse: bool = False,
    ) -> WorktreeGeometry:
        """Resolve and materialize a cut root, refusing ambiguous reuse."""
        geometry = self.geometry(cut_id, baseline_sha, integrator=integrator)
        root = Path(geometry.worktree_path)
        if integrator:
            self._validate_integrator(geometry)
            return geometry

        self.worktree_root.mkdir(parents=True, exist_ok=True)
        if root.exists():
            if not allow_reuse:
                raise WorktreeContractError(
                    f"refusing existing worktree {root}; resume with its owning run id or clean it explicitly"
                )
            self._validate_target(root)
            self._validate_reuse(geometry)
        else:
            branch_exists = bool(
                _git(
                    self.main_repo,
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    f"refs/heads/{geometry.branch}",
                )
            )
            command = ["git", "worktree", "add", "--quiet"]
            if branch_exists:
                command.extend([str(root), geometry.branch])
            else:
                command.extend(["-b", geometry.branch, str(root), baseline_sha])
            _run(self.main_repo, command, "create linked checkout")
            self._validate_reuse(geometry)
        self._validate_target(root)
        return geometry

    def validate(self, geometry: WorktreeGeometry) -> None:
        """Revalidate a receipt's geometry before launch or resume."""
        if geometry.integrator_exclusive:
            self._validate_integrator(geometry)
        else:
            self._validate_reuse(geometry)
            self._validate_target(Path(geometry.worktree_path))

    def recover_active(self, geometry: WorktreeGeometry) -> None:
        """Validate an already-live legacy checkout without relocating or dirt checks."""
        root = Path(geometry.worktree_path)
        if not root.is_dir():
            raise WorktreeContractError(f"active recovery worktree is missing: {root}")
        observed_root = _git(root, "rev-parse", "--show-toplevel")
        observed_branch = _git(root, "branch", "--show-current")
        if not observed_root or Path(observed_root).resolve() != root.resolve():
            raise WorktreeContractError(
                f"active recovery root is not a registered worktree: {root}"
            )
        if observed_branch != geometry.branch:
            raise WorktreeContractError(
                f"active recovery branch mismatch: expected {geometry.branch}, observed {observed_branch or '<detached>'}"
            )
        target = Path(geometry.target_path)
        if target != root / "target":
            raise WorktreeContractError(
                "old shared fleet target is forbidden for concurrent plans; unset CARGO_TARGET_DIR — Vibecrafted assigns $PWD/target per worker"
            )
        self._validate_target(root)

    def cleanup(self, geometry: WorktreeGeometry, *, settled: bool) -> str:
        """Remove only a settled worker checkout; durable evidence and branch remain."""
        if geometry.integrator_exclusive:
            return "not-applicable"
        if not settled:
            raise WorktreeContractError("refusing cleanup: cut is not settled")
        root = Path(geometry.worktree_path)
        if not root.exists():
            return "already-removed"
        self._validate_reuse(geometry)
        dirty = _git(root, "status", "--porcelain")
        if dirty:
            raise WorktreeContractError(
                f"refusing cleanup of dirty worktree {root}: {dirty}"
            )
        _run(
            self.main_repo,
            ["git", "worktree", "remove", str(root)],
            "remove settled worktree",
        )
        return "removed"

    def _validate_integrator(self, geometry: WorktreeGeometry) -> None:
        root = Path(geometry.worktree_path).resolve()
        if root != self.main_repo:
            raise WorktreeContractError("integrator must run in the main checkout")
        observed = _git(root, "rev-parse", "HEAD")
        if geometry.baseline_sha and observed != geometry.baseline_sha:
            raise WorktreeContractError(
                f"integrator baseline drift: expected {geometry.baseline_sha}, observed {observed or '<none>'}"
            )
        dirty = _git(root, "status", "--porcelain")
        if dirty:
            raise WorktreeContractError(
                f"integrator requires a clean main checkout: {dirty}"
            )

    def _validate_reuse(self, geometry: WorktreeGeometry) -> None:
        root = Path(geometry.worktree_path)
        if not root.is_dir():
            raise WorktreeContractError(f"worker worktree is missing: {root}")
        observed_root = _git(root, "rev-parse", "--show-toplevel")
        observed_branch = _git(root, "branch", "--show-current")
        if Path(observed_root).resolve() != root.resolve():
            raise WorktreeContractError(
                f"worker root is not the registered worktree: {root}"
            )
        if observed_branch != geometry.branch:
            raise WorktreeContractError(
                f"worker branch mismatch at {root}: expected {geometry.branch}, observed {observed_branch or '<detached>'}"
            )
        dirty = _git(root, "status", "--porcelain")
        if dirty:
            raise WorktreeContractError(
                f"refusing dirty reused worktree {root}: {dirty}"
            )

    def _validate_target(self, root: Path) -> None:
        target = root / "target"
        if target.is_symlink():
            raise WorktreeContractError(
                f"worker target must be a real local directory, not a symlink: {target}"
            )
        target.mkdir(parents=True, exist_ok=True)
        resolved = target.resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise WorktreeContractError(
                f"CARGO_TARGET_DIR escapes worker root: {resolved}; unset CARGO_TARGET_DIR and use $PWD/target"
            ) from exc
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", "target"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if ignored.returncode != 0:
            exclude = Path(
                _git(root, "rev-parse", "--git-path", "info/exclude")
            ).expanduser()
            if not exclude.is_absolute():
                exclude = root / exclude
            exclude.parent.mkdir(parents=True, exist_ok=True)
            existing = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
            if "/target/" not in existing.splitlines():
                with exclude.open("a", encoding="utf-8") as handle:
                    if existing and not existing.endswith("\n"):
                        handle.write("\n")
                    handle.write("/target/\n")


def _safe_component(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return cleaned or fallback


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _run(repo: Path, command: list[str], action: str) -> None:
    proc = subprocess.run(
        command, cwd=repo, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        raise WorktreeContractError(f"failed to {action}: {detail}")
