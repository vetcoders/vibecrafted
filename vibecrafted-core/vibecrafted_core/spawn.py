"""Legacy launcher supervisor: spawns agent CLIs, extracts usage, finalizes artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import inspect
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from shutil import which
from typing import Any

from .agent_dispatch import extract_session_id, sandbox_supported
from .clock import utc_now_iso
from .control_plane import control_plane_home, ensure_session_id, normalize_run_root
from .events import append_event
from .report_contract import (
    CLAIM_DIGEST_ENV,
    materialize_launcher_report_template,
    stamp_launcher_report_identity,
)
from .runtime_paths import agent_tool_search_path
from .runtime_transcript import write_runtime_transcript_manifest
from .settlement import BareMarkdownError, require_bound_markdown
from .telemetry import estimate_cost_usd

EventCallback = Callable[[dict[str, Any]], None]

POLICY_PROVIDERS = ("codex", "claude", "agy", "grok", "junie")
RUNTIME_POLICIES = ("local-native", "local-worktrees", "local-vm", "cloud-soon")
PERMISSION_POLICIES = ("bypass", "auto", "accept-edits", "read-only")
POLICY_MODES = ("interactive", "headless")
QUOTA_PRESET_TOKENS = 250_000
QUOTA_MAX_TOKENS = 10_000_000
QUOTA_EXHAUSTED_EXIT_CODE = 75
OPERATOR_POLICIES = ("none", "auto", "claude")
CONTINUITY_MODES = ("full-lineage", "fresh", "bare-fork")
_CONTINUITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_INHERITED_CONTINUITY_ENV = (
    "AICX_CONTEXT_FILE",
    "AICX_CONTINUITY_FILE",
    "CLAUDE_CODE_SESSION_ID",
    "CODEX_SESSION_ID",
    "VIBECRAFTED_OPERATOR_SESSION_ID",
    "VIBECRAFTED_PARENT_RUN_ID",
    "VIBECRAFTED_PARENT_SESSION_ID",
    "VIBECRAFTED_RESUME_CONTEXT",
    "VIBECRAFTED_RESUME_META",
    "VIBECRAFTED_LOOP_STATE_FILE",
    "VIBECRAFTED_LOOP_NR",
    "SPAWN_LOOP_NR",
)
USER_OBSERVED_WARNING = (
    "User-observed only: no Operator Agent is supervising this Agent Workspace."
)


@dataclass(frozen=True)
class ProviderPolicy:
    """Canonical provider/runtime/permission decision shared by CLI and UI."""

    provider: str
    runtime: str
    permissions: str
    mode: str
    supported: bool
    flags: tuple[str, ...] = ()
    behavior: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "runtime": self.runtime,
            "permissions": self.permissions,
            "mode": self.mode,
            "supported": self.supported,
            "status": "SUPPORTED" if self.supported else "UNSUPPORTED",
            "flags": list(self.flags),
            "behavior": self.behavior,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class QuotaPolicy:
    """Validated User-selected policy for one interactive provider session."""

    kind: str
    token_budget: int | None
    selection: str
    warning: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "token_budget": self.token_budget,
            "selection": self.selection,
            "warning": self.warning,
        }


@dataclass(frozen=True)
class OperatorAgentPolicy:
    """Typed decision for a distinct Operator Agent supervising one child Agent."""

    selection: str
    provider: str | None
    permissions: str | None
    supported: bool
    warning: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "selection": self.selection,
            "provider": self.provider,
            "permissions": self.permissions,
            "supported": self.supported,
            "status": "SUPPORTED" if self.supported else "UNSUPPORTED",
            "warning": self.warning,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ContinuityPolicy:
    """One fail-closed continuity decision resolved before runtime truth."""

    mode: str
    lineage_id: str
    parent_provider_session_id: str = ""
    supported: bool = True
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "lineage_id": self.lineage_id,
            "parent_provider_session_id": self.parent_provider_session_id,
            "supported": self.supported,
            "status": "SUPPORTED" if self.supported else "UNSUPPORTED",
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ContinuityMaterial:
    """Private bounded transport material; receipts project hashes, not bodies."""

    policy: ContinuityPolicy
    prompt: str
    context_path: str = ""
    loop_state_path: str = ""
    context_sha256: str = ""
    loop_sha256: str = ""

    def receipt(self) -> dict[str, Any]:
        return {
            **self.policy.as_dict(),
            "context_sha256": self.context_sha256,
            "loop_sha256": self.loop_sha256,
            "materialized": bool(self.context_sha256 and self.loop_sha256)
            if self.policy.mode == "full-lineage"
            else True,
        }


@dataclass(frozen=True)
class ProviderUsageCapability:
    """Provider-specific proof that live usage can be attributed to one child."""

    provider: str
    supported: bool
    source: str = ""
    provider_version: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "supported": self.supported,
            "status": "SUPPORTED" if self.supported else "UNSUPPORTED",
            "source": self.source,
            "provider_version": self.provider_version,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class InteractiveWorkspaceLaunch:
    """Durable parent/effective-root truth for one interactive Agent launch."""

    run_id: str
    provider: str
    runtime: str
    permissions: str
    parent_root: str
    effective_root: str
    workspace_id: str
    vibecrafted_session_id: str
    meta_path: Path
    receipt: dict[str, Any]
    quota_policy: QuotaPolicy
    usage_capability: ProviderUsageCapability
    provider_session_id: str
    worktree_manager: Any | None = field(default=None, repr=False, compare=False)
    worktree_geometry: Any | None = field(default=None, repr=False, compare=False)


_PERMISSION_CONTRACT: dict[str, dict[str, tuple[tuple[str, ...], str] | None]] = {
    "codex": {
        "bypass": (
            ("--dangerously-bypass-approvals-and-sandbox",),
            "all actions bypass approval and sandbox",
        ),
        "auto": (
            ("--ask-for-approval", "on-request", "--sandbox", "workspace-write"),
            "provider requests approval when needed",
        ),
        "accept-edits": None,
        "read-only": (
            ("--ask-for-approval", "never", "--sandbox", "read-only"),
            "writes and escalations fail closed",
        ),
    },
    "claude": {
        "bypass": (
            ("--permission-mode", "bypassPermissions"),
            "all actions bypass permission prompts",
        ),
        "auto": (
            ("--permission-mode", "auto"),
            "provider selects when to request permission",
        ),
        "accept-edits": (
            ("--permission-mode", "acceptEdits"),
            "edits pass; other actions require permission and fail closed without an operator",
        ),
        "read-only": (
            ("--permission-mode", "plan"),
            "plan mode prevents edits and execution",
        ),
    },
    "agy": {
        "bypass": (
            ("--dangerously-skip-permissions",),
            "all actions bypass permission prompts",
        ),
        "auto": ((), "provider default permission prompts remain active"),
        "accept-edits": (
            ("--mode", "accept-edits"),
            "edits pass; other actions require permission and fail closed without an operator",
        ),
        "read-only": (("--mode", "plan"), "plan mode prevents edits and execution"),
    },
    "grok": {
        "bypass": (
            ("--permission-mode", "bypassPermissions"),
            "all actions bypass permission prompts",
        ),
        "auto": (
            ("--permission-mode", "auto"),
            "provider selects when to request permission",
        ),
        "accept-edits": (
            ("--permission-mode", "acceptEdits"),
            "edits pass; other actions require permission and fail closed without an operator",
        ),
        "read-only": (
            ("--permission-mode", "plan"),
            "plan mode prevents edits and execution",
        ),
    },
    "junie": {
        "bypass": (("--brave",), "interactive brave mode bypasses confirmations"),
        "auto": ((), "provider default permission prompts remain active"),
        "accept-edits": None,
        "read-only": (
            ("--plan",),
            "interactive plan mode prevents edits and execution",
        ),
    },
}


def resolve_quota_policy(
    selection: str | int | None,
    *,
    runtime: str,
    mode: str = "interactive",
) -> QuotaPolicy:
    """Validate one bounded or explicitly User-observed unlimited policy."""
    raw = "safe" if selection is None else str(selection).strip().lower()
    if not raw or raw == "safe":
        return QuotaPolicy("bounded", QUOTA_PRESET_TOKENS, "safe")
    if raw == "unlimited":
        if mode != "interactive" or runtime != "local-native":
            raise ValueError(
                "unlimited quota is restricted to directly User-observed local-native sessions"
            )
        return QuotaPolicy(
            "unlimited",
            None,
            "unlimited",
            "User selected unlimited usage; Vibecrafted will measure but will not terminate on token usage",
        )
    try:
        budget = int(raw, 10)
    except ValueError as exc:
        raise ValueError(
            "token budget must be safe, unlimited, or a positive integer"
        ) from exc
    if budget <= 0:
        raise ValueError("token budget must be a positive integer")
    if budget > QUOTA_MAX_TOKENS:
        raise ValueError(f"token budget must not exceed {QUOTA_MAX_TOKENS}")
    return QuotaPolicy("bounded", budget, raw)


def _validated_continuity_id(value: str, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or not _CONTINUITY_ID.fullmatch(normalized):
        raise ValueError(f"{label} must be one explicit, well-formed identifier")
    return normalized


def _ambient_parent_lineage(env: dict[str, str]) -> str:
    for name in (
        "VIBECRAFTED_RUN_ID",
        "CODEX_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "VIBECRAFTED_OPERATOR_SESSION_ID",
    ):
        candidate = str(env.get(name) or "").strip()
        if candidate and _CONTINUITY_ID.fullmatch(candidate):
            return candidate
    return ""


def resolve_continuity_policy(
    selection: str | None,
    *,
    provider: str,
    parent_session_id: str = "",
    parent_lineage_id: str = "",
    env: dict[str, str] | None = None,
) -> ContinuityPolicy:
    """Resolve one continuity mode without inferring a native session target."""
    mode = str(selection or "fresh").strip().lower()
    if mode not in CONTINUITY_MODES:
        raise ValueError(
            f"unknown continuity mode {mode!r}; choose {', '.join(CONTINUITY_MODES)}"
        )
    ambient = dict(os.environ if env is None else env)
    if mode == "fresh":
        if parent_session_id or parent_lineage_id:
            raise ValueError(
                "fresh continuity rejects parent session and lineage input"
            )
        return ContinuityPolicy(mode="fresh", lineage_id=f"fresh:{uuid.uuid4()}")
    if mode == "full-lineage":
        if parent_session_id:
            raise ValueError("full-lineage never accepts a native parent session")
        lineage = parent_lineage_id or _ambient_parent_lineage(ambient)
        return ContinuityPolicy(
            mode=mode,
            lineage_id=_validated_continuity_id(lineage, label="parent lineage id"),
        )

    parent = _validated_continuity_id(
        parent_session_id, label="bare-fork parent provider-session id"
    )
    if parent_lineage_id:
        raise ValueError("bare-fork accepts only an explicit provider-session parent")
    current_ids = {
        str(ambient.get(name) or "").strip()
        for name in (
            "CODEX_SESSION_ID",
            "CLAUDE_CODE_SESSION_ID",
            "VIBECRAFTED_OPERATOR_SESSION_ID",
            "VIBECRAFTED_PROVIDER_SESSION_ID",
        )
    }
    if parent in current_ids:
        raise ValueError("bare-fork parent is the current provider session")
    from .continuity.capabilities import (
        PROBE_CONFIRMED,
        SUPPORTED,
        capability_for,
        probe,
    )

    capability = capability_for(provider)
    if capability.native_fork != SUPPORTED:
        raise ValueError(
            f"bare-fork unsupported for {provider}: {capability.fork_runtime_restrictions}"
        )
    evidence = probe(provider, refresh=True)
    if evidence.state != PROBE_CONFIRMED:
        raise ValueError(
            f"bare-fork capability probe did not confirm {provider}: {evidence.detail}"
        )
    return ContinuityPolicy(
        mode=mode,
        lineage_id=parent,
        parent_provider_session_id=parent,
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _materialize_continuity(
    policy: ContinuityPolicy,
    *,
    provider: str,
    root: Path,
    run_id: str,
    prompt: str,
) -> ContinuityMaterial:
    if policy.mode != "full-lineage":
        return ContinuityMaterial(policy=policy, prompt=prompt)
    from .aicx_session_chain import (
        CliSessionChain,
        assemble_resume_continuity_pack,
        pack_contains_recover_instruction,
    )

    loop_path = Path(
        os.environ.get("VIBECRAFTED_LOOP_STATE_FILE", "").strip()
        or root / ".vibecrafted" / "operator-loop.local.md"
    ).expanduser()
    try:
        loop_text = loop_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise ValueError(
            f"full-lineage requires readable current LOOP state: {exc}"
        ) from exc
    lines = loop_text.splitlines()
    fields: dict[str, str] = {}
    prompt_start = 0
    if lines[:1] == ["---"]:
        for index, line in enumerate(lines[1:], start=1):
            if line == "---":
                prompt_start = index + 1
                break
            if ":" in line:
                key, raw = line.split(":", 1)
                fields[key.strip()] = raw.strip().strip('"')
    loop_prompt = "\n".join(lines[prompt_start:]).strip()
    if fields.get("active") != "true" or not loop_prompt:
        raise ValueError(
            "full-lineage requires an active LOOP with a non-empty durable goal"
        )
    aicx_bin = which("aicx", path=agent_tool_search_path())
    if not aicx_bin:
        raise ValueError("full-lineage requires the aicx executable")
    run_dir = control_plane_home() / "runtime_runs" / run_id
    context_path = run_dir / "continuity-pack.md"
    meta_path = run_dir / "continuity-pack.meta.json"
    pack = assemble_resume_continuity_pack(
        agent=provider,
        root=root,
        hours=48,
        context_file=context_path,
        meta_file=meta_path,
        chain=CliSessionChain(aicx_bin),
    )
    required_sections = (
        "## Session catalog",
        "## Continuity",
        "## Operator instruction",
    )
    if (
        pack.mode != "new_session"
        or pack.empty_kind != "none"
        or pack.session_count < 1
        or pack.degradations
        or not all(section in pack.body for section in required_sections)
        or pack_contains_recover_instruction(pack.body)
    ):
        context_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        raise ValueError(
            "full-lineage continuity pack is empty, stale, degraded, or not new-session safe"
        )
    loop_copy = run_dir / "current-loop.md"
    loop_copy.write_text(loop_text, encoding="utf-8")
    bounded_prompt = (
        f"{prompt}\n\n"
        "Continuity mode: full-lineage. Start a new provider session; never attach.\n"
        f"Read bounded AICX continuity: {context_path}\n"
        f"Read durable current goal/LOOP: {loop_copy}\n"
        f"Parent lineage evidence: {policy.lineage_id}\n"
    )
    return ContinuityMaterial(
        policy=policy,
        prompt=bounded_prompt,
        context_path=str(context_path),
        loop_state_path=str(loop_copy),
        context_sha256=_sha256_file(context_path),
        loop_sha256=_sha256_file(loop_copy),
    )


def _fresh_child_environment(
    env: dict[str, str], policy: ContinuityPolicy
) -> dict[str, str]:
    child = dict(env)
    if policy.mode == "fresh":
        for name in _INHERITED_CONTINUITY_ENV:
            child.pop(name, None)
        for name in tuple(child):
            if name.startswith(("VIBECRAFTED_RESUME_", "AICX_CONTINUITY_")):
                child.pop(name, None)
    return child


def continuity_policy_capabilities(
    provider: str,
    *,
    root: str | os.PathLike[str],
    explicit_parent: str = "",
    env: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Project exact selector availability without materializing or spawning."""
    ambient = dict(os.environ if env is None else env)
    root_path = Path(root).expanduser().resolve()
    parent_lineage = explicit_parent or _ambient_parent_lineage(ambient)
    loop_path = Path(
        ambient.get("VIBECRAFTED_LOOP_STATE_FILE", "").strip()
        or root_path / ".vibecrafted" / "operator-loop.local.md"
    ).expanduser()
    full_reason = ""
    if not parent_lineage:
        full_reason = "no explicit/current parent lineage id"
    elif not loop_path.is_file():
        full_reason = f"current LOOP state missing: {loop_path}"
    elif which("aicx", path=agent_tool_search_path(ambient)) is None:
        full_reason = "aicx executable not found"
    else:
        try:
            loop_text = loop_path.read_text(encoding="utf-8")
        except OSError as exc:
            full_reason = f"current LOOP unreadable: {exc}"
        else:
            if (
                "active: true" not in loop_text
                or not loop_text.split("---")[-1].strip()
            ):
                full_reason = "current LOOP is inactive or has no durable goal"
    bare_reason = ""
    if not explicit_parent:
        bare_reason = "expert-only: provide an explicit parent provider-session id"
    else:
        try:
            resolve_continuity_policy(
                "bare-fork",
                provider=provider,
                parent_session_id=explicit_parent,
                env=ambient,
            )
        except ValueError as exc:
            bare_reason = str(exc)
    return {
        "full-lineage": {
            "available": not full_reason,
            "recommended": True,
            "reason": full_reason,
        },
        "fresh": {
            "available": True,
            "recommended": False,
            "reason": "no inherited memory is supplied",
        },
        "bare-fork": {
            "available": not bare_reason,
            "recommended": False,
            "reason": bare_reason,
        },
    }


def resolve_provider_usage_capability(
    provider: str,
    *,
    executable: str | None = None,
) -> ProviderUsageCapability:
    """Probe the exact installed executable for an attributable live source."""
    resolved = executable or which(provider, path=agent_tool_search_path())
    if not resolved:
        return ProviderUsageCapability(
            provider, False, reason=f"{provider} executable not found"
        )
    resolved_path = str(Path(resolved).expanduser().resolve())
    try:
        stat = Path(resolved_path).stat()
    except OSError as exc:
        return ProviderUsageCapability(
            provider, False, reason=f"cannot inspect {provider} executable: {exc}"
        )
    return _probe_provider_usage_capability(
        provider, resolved_path, stat.st_mtime_ns, stat.st_size
    )


@lru_cache(maxsize=32)
def _probe_provider_usage_capability(
    provider: str,
    executable: str,
    _mtime_ns: int,
    _size: int,
) -> ProviderUsageCapability:
    if provider != "claude":
        return ProviderUsageCapability(
            provider,
            False,
            reason=(
                f"{provider} exposes no verified live, child-attributable, monotonic "
                "usage side channel compatible with inherited interactive TTY"
            ),
        )
    try:
        version_result = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        help_result = subprocess.run(
            [executable, "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ProviderUsageCapability(
            provider, False, reason=f"Claude capability probe failed: {exc}"
        )
    version = (version_result.stdout or version_result.stderr).splitlines()
    version_text = version[0].strip() if version else ""
    help_text = f"{help_result.stdout}\n{help_result.stderr}"
    if version_result.returncode != 0 or not version_text:
        return ProviderUsageCapability(
            provider,
            False,
            reason="Claude version probe did not return exact version truth",
        )
    if help_result.returncode != 0 or "--session-id <uuid>" not in help_text:
        return ProviderUsageCapability(
            provider,
            False,
            provider_version=version_text,
            reason="installed Claude does not expose --session-id <uuid>",
        )
    return ProviderUsageCapability(
        provider,
        True,
        source="claude-transcript-jsonl-v1",
        provider_version=version_text.split()[0],
    )


def resolve_provider_policy(
    provider: str,
    runtime: str,
    permissions: str,
    mode: str,
) -> ProviderPolicy:
    """Resolve one policy cell without approximating unsupported semantics."""
    if provider not in POLICY_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    if runtime not in RUNTIME_POLICIES:
        raise ValueError(f"unsupported runtime policy: {runtime}")
    if permissions not in PERMISSION_POLICIES:
        raise ValueError(f"unsupported permission policy: {permissions}")
    if mode not in POLICY_MODES:
        raise ValueError(f"unsupported policy mode: {mode}")
    if runtime == "cloud-soon":
        return ProviderPolicy(
            provider,
            runtime,
            permissions,
            mode,
            False,
            reason="cloud runtime is coming soon",
        )
    if runtime == "local-vm":
        return ProviderPolicy(
            provider,
            runtime,
            permissions,
            mode,
            False,
            reason="Docker/Colima may be present, but canonical init has no VM entrypoint",
        )
    if runtime == "local-worktrees" and mode != "interactive":
        return ProviderPolicy(
            provider,
            runtime,
            permissions,
            mode,
            False,
            reason="local worktrees are available only for interactive Agent Workspaces",
        )
    cell = _PERMISSION_CONTRACT[provider][permissions]
    if cell is None:
        return ProviderPolicy(
            provider,
            runtime,
            permissions,
            mode,
            False,
            reason=f"{provider} exposes no native {permissions} policy",
        )
    if (
        provider == "junie"
        and mode == "headless"
        and permissions in {"bypass", "read-only"}
    ):
        return ProviderPolicy(
            provider,
            runtime,
            permissions,
            mode,
            False,
            reason=f"junie {permissions} is interactive-only",
        )
    flags, behavior = cell
    return ProviderPolicy(provider, runtime, permissions, mode, True, flags, behavior)


def runtime_policy_capabilities(provider: str) -> dict[str, dict[str, Any]]:
    """Report host substrate separately from canonical-launcher availability."""
    provider_executable = which(provider, path=agent_tool_search_path())
    provider_found = provider_executable is not None
    usage = resolve_provider_usage_capability(provider, executable=provider_executable)
    git_found = which("git") is not None
    try:
        from .dispatch.supervisor import run_dispatch

        dispatch_manages_worktrees = (
            "manage_worktrees" in inspect.signature(run_dispatch).parameters
        )
    except (ImportError, ValueError):
        dispatch_manages_worktrees = False
    worktree_substrate = git_found and dispatch_manages_worktrees
    vm_found = which("docker") is not None or which("colima") is not None
    return {
        "local-native": {
            "available": provider_found and usage.supported,
            "usage_capability": usage.as_dict(),
            "reason": (
                ""
                if provider_found and usage.supported
                else (
                    f"{provider} executable not found"
                    if not provider_found
                    else usage.reason
                )
            ),
        },
        "local-worktrees": {
            "available": provider_found and worktree_substrate and usage.supported,
            "substrate": worktree_substrate,
            "usage_capability": usage.as_dict(),
            "reason": ""
            if provider_found and worktree_substrate and usage.supported
            else (
                f"{provider} executable not found"
                if not provider_found
                else (
                    "git/dispatch manage_worktrees unavailable"
                    if not worktree_substrate
                    else usage.reason
                )
            ),
        },
        "local-vm": {
            "available": False,
            "substrate": vm_found,
            "reason": "no canonical VM entrypoint"
            if vm_found
            else "Docker/Colima is not detected",
        },
        "cloud-soon": {"available": False, "reason": "coming soon"},
    }


def interactive_policy_command(
    provider: str,
    prompt: str,
    runtime: str,
    permissions: str,
    *,
    provider_session_id: str | None = None,
    continuity_policy: ContinuityPolicy | None = None,
) -> list[str]:
    """Build one interactive argv from the canonical policy decision."""
    decision = resolve_provider_policy(provider, runtime, permissions, "interactive")
    if not decision.supported:
        raise ValueError(decision.reason)
    flags = list(decision.flags)
    continuity = continuity_policy or ContinuityPolicy("fresh", "fresh:implicit")
    if provider == "claude":
        session_flags = (
            ["--session-id", provider_session_id] if provider_session_id else []
        )
        if continuity.mode == "bare-fork":
            session_flags = [
                "--resume",
                continuity.parent_provider_session_id,
                "--fork-session",
                *session_flags,
            ]
        return ["claude", "--verbose", *flags, *session_flags, prompt]
    if provider == "codex":
        return ["codex", *flags, prompt]
    if provider == "agy":
        return ["agy", *flags, "--add-dir", ".", "--prompt-interactive", prompt]
    if provider == "junie":
        return [
            "junie",
            *flags,
            f"--prompt={prompt}",
            "--project=.",
            "--skip-update-check",
            "--use-local-cache",
        ]
    session_flags: list[str] = []
    if continuity.mode == "bare-fork":
        session_flags = [
            "--resume",
            continuity.parent_provider_session_id,
            "--fork-session",
        ]
        if provider_session_id:
            session_flags.extend(["--session-id", provider_session_id])
    return [
        "grok",
        "--cwd",
        ".",
        *flags,
        *session_flags,
        "--no-alt-screen",
        prompt,
    ]


def interactive_workspace_command(
    provider: str,
    prompt: str,
    runtime: str,
    permissions: str,
    root: str | os.PathLike[str],
    token_budget: str | int | None = None,
    operator: str = "none",
    continuity: str = "fresh",
    parent_session_id: str = "",
    parent_lineage_id: str = "",
) -> list[str]:
    """Build the portable wrapper argv used by the exact ``init`` route."""
    decision = resolve_provider_policy(provider, runtime, permissions, "interactive")
    if not decision.supported:
        raise ValueError(decision.reason)
    quota = resolve_quota_policy(token_budget, runtime=runtime)
    capability = resolve_provider_usage_capability(provider)
    if not capability.supported:
        raise ValueError(capability.reason)
    operator_policy = resolve_operator_agent_policy(operator, runtime=runtime)
    if not operator_policy.supported:
        raise ValueError(operator_policy.reason)
    continuity_policy = resolve_continuity_policy(
        continuity,
        provider=provider,
        parent_session_id=parent_session_id,
        parent_lineage_id=parent_lineage_id,
    )
    command = [
        sys.executable,
        "-m",
        "vibecrafted_core.spawn",
        "interactive-launch",
        provider,
        "--runtime",
        runtime,
        "--permissions",
        permissions,
        "--token-budget",
        quota.selection,
        "--operator",
        operator_policy.selection,
        "--continuity",
        continuity_policy.mode,
        "--root",
        str(Path(root).expanduser().resolve()),
        "--prompt",
        prompt,
    ]
    if continuity_policy.parent_provider_session_id:
        command[command.index("--root") : command.index("--root")] = [
            "--parent-session",
            continuity_policy.parent_provider_session_id,
        ]
    elif continuity_policy.mode == "full-lineage":
        command[command.index("--root") : command.index("--root")] = [
            "--continuity-parent",
            continuity_policy.lineage_id,
        ]
    import_root = os.environ.get("VIBECRAFTED_INTERACTIVE_IMPORT_ROOT", "").strip()
    if import_root:
        pythonpath = import_root
        if os.environ.get("PYTHONPATH"):
            pythonpath = f"{pythonpath}{os.pathsep}{os.environ['PYTHONPATH']}"
        return ["env", f"PYTHONPATH={pythonpath}", *command]
    return command


def resolve_operator_agent_policy(
    selection: str | None,
    *,
    runtime: str,
) -> OperatorAgentPolicy:
    """Resolve the only supported supervision shape without silent fallback."""
    normalized = (selection or "none").strip().lower()
    if normalized not in OPERATOR_POLICIES:
        return OperatorAgentPolicy(
            selection=normalized,
            provider=None,
            permissions=None,
            supported=False,
            reason=(
                f"unknown Operator Agent policy {normalized!r}; choose "
                f"{', '.join(OPERATOR_POLICIES)}"
            ),
        )
    if normalized == "none":
        return OperatorAgentPolicy(
            selection="none",
            provider=None,
            permissions=None,
            supported=True,
            warning=USER_OBSERVED_WARNING,
        )
    if runtime not in {"local-native", "local-worktrees"}:
        return OperatorAgentPolicy(
            selection=normalized,
            provider=None,
            permissions=None,
            supported=False,
            reason=(
                f"Operator Agent supervision is unavailable for runtime {runtime}; "
                "use local-native or local-worktrees"
            ),
        )
    return OperatorAgentPolicy(
        selection=normalized,
        provider="claude",
        permissions="accept-edits",
        supported=True,
    )


def prepare_interactive_workspace_launch(
    *,
    provider: str,
    runtime: str,
    permissions: str,
    selected_root: str | os.PathLike[str],
    prompt: str,
    run_id: str | None = None,
    executable: str | None = None,
    worker_pid: int | None = None,
    publish: bool = True,
    quota_policy: QuotaPolicy | None = None,
    usage_capability: ProviderUsageCapability | None = None,
    provider_session_id: str | None = None,
    continuity_material: ContinuityMaterial | None = None,
) -> InteractiveWorkspaceLaunch:
    """Resolve identity/root and publish truth only after launch preparation succeeds."""
    decision = resolve_provider_policy(provider, runtime, permissions, "interactive")
    if not decision.supported:
        raise ValueError(decision.reason)
    parent = Path(selected_root).expanduser().resolve()
    if not parent.is_dir():
        raise ValueError(f"selected workspace does not exist: {parent}")
    resolved_executable = executable or which(provider, path=agent_tool_search_path())
    if not resolved_executable:
        raise ValueError(f"{provider} executable not found")
    quota = quota_policy or resolve_quota_policy(None, runtime=runtime)
    capability = usage_capability or resolve_provider_usage_capability(
        provider, executable=resolved_executable
    )
    if not capability.supported:
        raise ValueError(capability.reason)
    effective_provider_session_id = provider_session_id or str(uuid.uuid4())
    try:
        uuid.UUID(effective_provider_session_id)
    except ValueError as exc:
        raise ValueError("provider session id must be a valid UUID") from exc

    from .dispatch.worktrees import (
        WorktreeContractError,
        WorktreeGeometry,
        WorktreeManager,
    )
    from .workflow import reserve_run_id
    from .workspace_catalog import resolve_run_workspace_identity

    effective_run_id = run_id or reserve_run_id("init")
    geometry: WorktreeGeometry | None = None
    effective = parent
    manager: WorktreeManager | None = None
    if runtime == "local-worktrees":
        manager = WorktreeManager(parent)
        baseline = _git_output(parent, "rev-parse", "HEAD")
        if not baseline:
            raise ValueError(f"selected workspace is not a git repository: {parent}")
        geometry = manager.prepare_agent_launch(provider, effective_run_id, baseline)
        effective = Path(geometry.worktree_path).resolve()

    try:
        identity = resolve_run_workspace_identity(
            root=parent, env={}, create_if_missing=True
        )
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
        run_dir = control_plane_home() / "runtime_runs" / effective_run_id
        prompt_path = run_dir / "prompt.md"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        meta_path = run_dir / "meta.json"
        owner_pid = int(worker_pid or os.getpid())
        receipt: dict[str, Any] = {
            "created_at": now_iso,
            "updated_at": now_iso,
            "started_at": now_iso,
            "status": "active" if publish else "prepared",
            "run_id": effective_run_id,
            "agent": provider,
            "skill": "init",
            "mode": "interactive",
            "runtime_policy": runtime,
            "permission_policy": permissions,
            "quota_policy": quota.as_dict(),
            "quota_warning": quota.warning,
            "usage_capability": capability.as_dict(),
            "usage_measurement": {
                "source": capability.source,
                "attribution": "provider_session_id+cwd+provider_version+message_id",
                "monotonic": True,
            },
            "measured_usage": {
                "input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "messages": 0,
            },
            "provider_session_id": effective_provider_session_id,
            "continuity": (
                continuity_material.receipt()
                if continuity_material is not None
                else ContinuityPolicy("fresh", f"fresh:{uuid.uuid4()}").as_dict()
            ),
            "root": str(effective),
            "parent_root": str(parent),
            "effective_worktree_path": str(effective) if geometry else "",
            "input": str(prompt_path),
            "owner_pid": owner_pid,
            "launcher_pid": owner_pid,
            "liveness": "active" if publish else "prepared",
            "executable": str(Path(resolved_executable).expanduser()),
            **identity.to_meta_fields(),
        }
        if publish:
            # Compatibility for direct preparation callers. The real interactive
            # owner replaces this with the provider child PID after Popen succeeds.
            receipt["worker_pid"] = owner_pid
        if geometry is not None:
            receipt.update(
                branch=geometry.branch,
                baseline_sha=geometry.baseline_sha,
                artifact_path=geometry.artifact_path,
            )
        if publish:
            _write_meta(meta_path, receipt)
            append_event(
                "lifecycle:active",
                effective_run_id,
                "interactive Agent Workspace is live",
                {**receipt, "meta": str(meta_path), "identity_required": True},
            )
    except Exception:
        if manager is not None and geometry is not None:
            try:
                manager.cleanup(geometry, settled=True)
            except (OSError, WorktreeContractError) as cleanup_exc:
                import logging

                logging.getLogger(__name__).warning(
                    "failed to remove unlaunched interactive worktree %s: %s",
                    geometry.worktree_path,
                    cleanup_exc,
                )
        raise
    return InteractiveWorkspaceLaunch(
        run_id=effective_run_id,
        provider=provider,
        runtime=runtime,
        permissions=permissions,
        parent_root=str(parent),
        effective_root=str(effective),
        workspace_id=identity.workspace_id,
        vibecrafted_session_id=identity.vibecrafted_session_id,
        meta_path=meta_path,
        receipt=receipt,
        quota_policy=quota,
        usage_capability=capability,
        provider_session_id=effective_provider_session_id,
        worktree_manager=manager,
        worktree_geometry=geometry,
    )


def _git_output(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, check=False, capture_output=True, text=True
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


class _ClaudeTranscriptUsage:
    """Incremental, exact-session reader for Claude's provider-owned JSONL."""

    _FIELDS = (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
    )

    def __init__(
        self,
        *,
        provider_session_id: str,
        effective_root: str,
        provider_version: str,
        env: dict[str, str],
    ) -> None:
        configured = env.get("CLAUDE_CONFIG_DIR", "").strip()
        base = (
            Path(configured).expanduser()
            if configured
            else Path(env.get("HOME", str(Path.home()))).expanduser() / ".claude"
        )
        self.projects_root = (base / "projects").resolve()
        self.provider_session_id = provider_session_id
        self.effective_root = str(Path(effective_root).resolve())
        self.provider_version = provider_version.split()[0]
        self.path: Path | None = None
        self.identity: tuple[int, int] | None = None
        self.offset = 0
        self.seen_message_ids: set[str] = set()
        self.totals = {field: 0 for field in self._FIELDS}
        self._reject_existing_source()

    def _matching_paths(self) -> list[Path]:
        if not self.projects_root.is_dir():
            return []
        return list(self.projects_root.rglob(f"{self.provider_session_id}.jsonl"))

    def _reject_existing_source(self) -> None:
        if self._matching_paths():
            raise ValueError(
                "provider session usage source already exists; refusing stale or reused session identity"
            )

    def _bind_path(self) -> bool:
        matches = self._matching_paths()
        if not matches:
            return False
        if len(matches) != 1:
            raise RuntimeError("multiple provider usage sources claim one session id")
        candidate = matches[0]
        if candidate.is_symlink():
            raise RuntimeError("provider usage source must not be a symlink")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(self.projects_root):
            raise RuntimeError("provider usage source escaped provider projects root")
        stat = resolved.stat()
        if not resolved.is_file():
            raise RuntimeError("provider usage source is not a regular file")
        self.path = resolved
        self.identity = (stat.st_dev, stat.st_ino)
        return True

    def poll(self) -> dict[str, int]:
        if self.path is None and not self._bind_path():
            return self.as_dict()
        assert self.path is not None
        stat = self.path.stat()
        if self.identity != (stat.st_dev, stat.st_ino):
            raise RuntimeError("provider usage source identity changed during the run")
        if stat.st_size < self.offset:
            raise RuntimeError("provider usage source was truncated during the run")
        with self.path.open("r", encoding="utf-8") as handle:
            handle.seek(self.offset)
            while True:
                line_start = handle.tell()
                line = handle.readline()
                if not line:
                    break
                if not line.endswith("\n"):
                    handle.seek(line_start)
                    break
                self._consume_line(line)
            self.offset = handle.tell()
        return self.as_dict()

    def _consume_line(self, line: str) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("provider usage source contains invalid JSONL") from exc
        if not isinstance(event, dict):
            raise TypeError("provider usage event must be a JSON object")
        message = event.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
            return
        if event.get("sessionId") != self.provider_session_id:
            raise RuntimeError("provider usage event belongs to a foreign session")
        event_cwd = event.get("cwd")
        if (
            not isinstance(event_cwd, str)
            or str(Path(event_cwd).resolve()) != self.effective_root
        ):
            raise RuntimeError("provider usage event belongs to a foreign workspace")
        if event.get("version") != self.provider_version:
            raise RuntimeError(
                "provider usage event version differs from probed executable"
            )
        message_id = message.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise RuntimeError("provider usage event has no attributable message id")
        if message_id in self.seen_message_ids:
            return
        usage = message["usage"]
        values: dict[str, int] = {}
        for field_name in self._FIELDS:
            value = usage.get(field_name, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RuntimeError(f"provider usage field {field_name} is invalid")
            values[field_name] = value
        self.seen_message_ids.add(message_id)
        for field_name, value in values.items():
            self.totals[field_name] += value

    def as_dict(self) -> dict[str, int]:
        return {
            **self.totals,
            "total_tokens": sum(self.totals.values()),
            "messages": len(self.seen_message_ids),
        }


def launch_interactive_workspace(
    provider: str,
    prompt: str,
    runtime: str,
    permissions: str,
    root: str | os.PathLike[str],
    token_budget: str | int | None = None,
    operator: str = "none",
    continuity: str = "fresh",
    parent_session_id: str = "",
    parent_lineage_id: str = "",
) -> int:
    """Own one provider child while preserving the inherited interactive TTY."""
    operator_policy = resolve_operator_agent_policy(operator, runtime=runtime)
    if not operator_policy.supported:
        raise ValueError(operator_policy.reason)
    from .workflow import reserve_run_id

    run_id = reserve_run_id("init")
    try:
        continuity_policy = resolve_continuity_policy(
            continuity,
            provider=provider,
            parent_session_id=parent_session_id,
            parent_lineage_id=parent_lineage_id,
        )
        continuity_material = _materialize_continuity(
            continuity_policy,
            provider=provider,
            root=Path(root).expanduser().resolve(),
            run_id=run_id,
            prompt=prompt,
        )
    except ValueError as exc:
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
        failed = {
            "created_at": now_iso,
            "updated_at": now_iso,
            "completed_at": now_iso,
            "status": "failed",
            "liveness": "terminal",
            "terminal_reason": "continuity_validation_failed",
            "reason": str(exc),
            "run_id": run_id,
            "agent": provider,
            "skill": "init",
            "mode": "interactive",
            "root": str(Path(root).expanduser().resolve()),
            "continuity": {
                "mode": str(continuity or "fresh"),
                "lineage_id": str(parent_lineage_id or parent_session_id),
                "supported": False,
                "status": "UNSUPPORTED",
                "reason": str(exc),
            },
        }
        meta_path = control_plane_home() / "runtime_runs" / run_id / "meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        _write_meta(meta_path, failed)
        append_event(
            "lifecycle:failed",
            run_id,
            "continuity validation failed before provider spawn",
            {**failed, "meta": str(meta_path)},
        )
        raise
    if operator_policy.provider is not None:
        return _launch_supervised_interactive_workspace(
            provider=provider,
            prompt=continuity_material.prompt,
            runtime=runtime,
            permissions=permissions,
            root=root,
            token_budget=token_budget,
            operator_policy=operator_policy,
            run_id=run_id,
            continuity_material=continuity_material,
        )
    quota = resolve_quota_policy(token_budget, runtime=runtime)
    child_env = _fresh_child_environment(os.environ.copy(), continuity_policy)
    provider_session_id = str(uuid.uuid4())
    command = interactive_policy_command(
        provider,
        continuity_material.prompt,
        runtime,
        permissions,
        provider_session_id=provider_session_id,
        continuity_policy=continuity_policy,
    )
    resolved = _resolve_agent_command(provider, command, child_env)
    capability = resolve_provider_usage_capability(provider, executable=resolved[0])
    if not capability.supported:
        raise ValueError(capability.reason)
    launch = prepare_interactive_workspace_launch(
        provider=provider,
        runtime=runtime,
        permissions=permissions,
        selected_root=root,
        prompt=continuity_material.prompt,
        run_id=run_id,
        executable=resolved[0],
        publish=False,
        quota_policy=quota,
        usage_capability=capability,
        provider_session_id=provider_session_id,
        continuity_material=continuity_material,
    )
    try:
        usage_reader = _ClaudeTranscriptUsage(
            provider_session_id=provider_session_id,
            effective_root=launch.effective_root,
            provider_version=capability.provider_version,
            env=child_env,
        )
    except Exception:
        _cleanup_unspawned_interactive_launch(launch)
        raise
    child_env.update(
        {
            "VIBECRAFTED_RUN_ID": launch.run_id,
            "VIBECRAFTED_SESSION_ID": launch.vibecrafted_session_id,
            "VIBECRAFTED_WORKSPACE_ID": launch.workspace_id,
            "VIBECRAFTED_WORKSPACE_INSTANCE_ID": str(
                launch.receipt["workspace_instance_id"]
            ),
            "VIBECRAFTED_BUILD_ID": str(launch.receipt["build_id"]["rendered"]),
            "VIBECRAFTED_PARENT_ROOT": launch.parent_root,
            "VIBECRAFTED_EFFECTIVE_ROOT": launch.effective_root,
            "VIBECRAFTED_AGENT_ROLE": "agent",
            "VIBECRAFTED_PROMPT_ROLE": prompt.splitlines()[0] if prompt else "",
            "VIBECRAFTED_CONTINUITY_MODE": continuity_policy.mode,
            "VIBECRAFTED_CONTINUITY_LINEAGE_ID": continuity_policy.lineage_id,
        }
    )
    try:
        # Omitting stdin/stdout/stderr is the contract: the provider inherits the
        # wrapper's exact descriptors and controlling terminal. No PTY broker,
        # pipe, or terminal-text parser sits between the User and provider.
        child = subprocess.Popen(
            resolved,
            cwd=launch.effective_root,
            env=child_env,
        )
    except (OSError, ValueError) as exc:
        cleanup = _cleanup_unspawned_interactive_launch(launch)
        _terminalize_interactive_launch(
            launch,
            launch.receipt,
            status="failed",
            exit_code=2,
            terminal_reason="child_spawn_failed",
            error=str(exc),
            extra={"prepared_worktree_cleanup": cleanup},
        )
        raise

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    receipt = {
        **launch.receipt,
        "updated_at": now_iso,
        "spawned_at": now_iso,
        "status": "active",
        "liveness": "active",
        "owner_pid": os.getpid(),
        "launcher_pid": os.getpid(),
        "worker_pid": child.pid,
        "role": "agent",
        "prompt_role": prompt.splitlines()[0] if prompt else "",
        "operator_policy": operator_policy.as_dict(),
        "supervision": {
            "mode": "user_observed",
            "state": "not_configured",
            "warning": operator_policy.warning,
        },
    }
    received_signal: list[int] = []
    previous_handlers: dict[int, Any] = {}

    def _forward_owner_signal(signum: int, _frame: Any) -> None:
        if not received_signal:
            received_signal.append(signum)
        if child.poll() is None:
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass

    if threading.current_thread() is threading.main_thread():
        for signum in (
            signal.SIGINT,
            signal.SIGTERM,
            getattr(signal, "SIGHUP", signal.SIGTERM),
        ):
            if signum in previous_handlers:
                continue
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, _forward_owner_signal)

    # Publish the mandatory roles immediately after successful child creation.
    # Stronger process fingerprints are a subsequent best-effort enrichment.
    _write_meta(launch.meta_path, receipt)
    append_event(
        "lifecycle:active",
        launch.run_id,
        "interactive Agent Workspace provider child is live",
        {**receipt, "meta": str(launch.meta_path), "identity_required": True},
    )
    try:
        from .process_control import process_identity_receipt

        owner_identity = process_identity_receipt(os.getpid(), run_id=launch.run_id)
        worker_identity = process_identity_receipt(child.pid, run_id=launch.run_id)
        if owner_identity is not None:
            receipt["owner_identity"] = owner_identity
        if worker_identity is not None:
            receipt["worker_identity"] = worker_identity
        _write_meta(launch.meta_path, receipt)
    except (OSError, RuntimeError, ValueError):
        # PID + role truth remains mandatory; stronger identity is best-effort
        # because a deterministic fast-exit provider may already be terminal.
        pass
    quota_exhausted = False
    provider_returncode: int
    try:
        while True:
            current_returncode = child.poll()
            measured_usage = usage_reader.poll()
            if measured_usage != receipt["measured_usage"]:
                receipt["measured_usage"] = measured_usage
                receipt["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
                _write_meta(launch.meta_path, receipt)
            if (
                current_returncode is None
                and not received_signal
                and quota.token_budget is not None
                and measured_usage["total_tokens"] >= quota.token_budget
            ):
                quota_exhausted = True
                child.terminate()
                try:
                    provider_returncode = child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    provider_returncode = child.wait()
                break
            if current_returncode is not None:
                provider_returncode = current_returncode
                break
            time.sleep(0.05)
    except Exception as exc:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        _terminalize_interactive_launch(
            launch,
            receipt,
            status="failed",
            exit_code=1,
            terminal_reason="wrapper_exception",
            error=str(exc),
        )
        raise
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    shell_status = (
        128 + abs(provider_returncode)
        if provider_returncode < 0
        else provider_returncode
    )
    if quota_exhausted:
        status = "quota_exhausted"
        terminal_reason = "quota_exhausted"
        shell_status = QUOTA_EXHAUSTED_EXIT_CODE
    elif received_signal:
        owner_signal = received_signal[0]
        status = "cancelled"
        terminal_reason = f"owner_signal:{signal.Signals(owner_signal).name}"
        if shell_status == 0:
            shell_status = 128 + owner_signal
    elif provider_returncode < 0:
        status = "cancelled"
        terminal_reason = (
            f"provider_signal:{signal.Signals(abs(provider_returncode)).name}"
        )
    elif provider_returncode == 0:
        status = "completed"
        terminal_reason = "provider_exit_zero"
    else:
        status = "failed"
        terminal_reason = "provider_exit_nonzero"
    _terminalize_interactive_launch(
        launch,
        receipt,
        status=status,
        exit_code=shell_status,
        terminal_reason=terminal_reason,
        exit_signal=abs(provider_returncode) if provider_returncode < 0 else None,
    )
    return shell_status


def _launch_supervised_interactive_workspace(
    *,
    provider: str,
    prompt: str,
    runtime: str,
    permissions: str,
    root: str | os.PathLike[str],
    token_budget: str | int | None,
    operator_policy: OperatorAgentPolicy,
    run_id: str,
    continuity_material: ContinuityMaterial,
) -> int:
    """Own one child and one distinct supervisor on the existing lifecycle throne."""
    assert operator_policy.provider is not None
    quota = resolve_quota_policy(token_budget, runtime=runtime)
    continuity_policy = continuity_material.policy
    base_env = _fresh_child_environment(os.environ.copy(), continuity_policy)
    child_session_id = str(uuid.uuid4())
    child_command = interactive_policy_command(
        provider,
        prompt,
        runtime,
        permissions,
        provider_session_id=child_session_id,
        continuity_policy=continuity_policy,
    )
    child_resolved = _resolve_agent_command(provider, child_command, base_env)
    child_capability = resolve_provider_usage_capability(
        provider, executable=child_resolved[0]
    )
    if not child_capability.supported:
        raise ValueError(child_capability.reason)
    launch = prepare_interactive_workspace_launch(
        provider=provider,
        runtime=runtime,
        permissions=permissions,
        selected_root=root,
        prompt=prompt,
        run_id=run_id,
        executable=child_resolved[0],
        publish=False,
        quota_policy=quota,
        usage_capability=child_capability,
        provider_session_id=child_session_id,
        continuity_material=continuity_material,
    )
    try:
        usage_reader = _ClaudeTranscriptUsage(
            provider_session_id=child_session_id,
            effective_root=launch.effective_root,
            provider_version=child_capability.provider_version,
            env=base_env,
        )
        from .workflow import reserve_run_id

        operator_run_id = reserve_run_id("oper")
        operator_session_id = str(uuid.uuid4())
        relation_id = str(uuid.uuid4())
        operator_run_dir = control_plane_home() / "runtime_runs" / operator_run_id
        operator_run_dir.mkdir(parents=True, exist_ok=True)
        operator_meta_path = operator_run_dir / "meta.json"
        operator_prompt_path = operator_run_dir / "prompt.md"
        protocol_path = operator_run_dir / "operator-protocol.jsonl"
        operator_prompt = _operator_supervision_prompt(
            child_run_id=launch.run_id,
            child_meta_path=launch.meta_path,
            relation_id=relation_id,
            protocol_path=protocol_path,
        )
        operator_prompt_path.write_text(operator_prompt, encoding="utf-8")
    except Exception:
        _cleanup_unspawned_interactive_launch(launch)
        raise

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    relation = {
        "relation_id": relation_id,
        "operator_run_id": operator_run_id,
        "child_run_id": launch.run_id,
        "state": "reserved",
        "protocol": "operator-protocol-jsonl-v1",
    }
    child_receipt = {
        **launch.receipt,
        "updated_at": now_iso,
        "status": "reserved",
        "liveness": "reserved",
        "role": "agent",
        "prompt_role": prompt.splitlines()[0] if prompt else "",
        "operator_policy": operator_policy.as_dict(),
        "supervision": dict(relation),
    }
    operator_receipt = {
        **launch.receipt,
        "created_at": now_iso,
        "updated_at": now_iso,
        "started_at": now_iso,
        "status": "reserved",
        "liveness": "reserved",
        "run_id": operator_run_id,
        "agent": operator_policy.provider,
        "skill": "operator",
        "permission_policy": operator_policy.permissions,
        "role": "operator",
        "prompt_role": "/vc-operator",
        "input": str(operator_prompt_path),
        "provider_session_id": operator_session_id,
        "operator_policy": operator_policy.as_dict(),
        "supervision": dict(relation),
        "measured_usage": {
            "input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "messages": 0,
        },
    }
    operator_command = interactive_policy_command(
        operator_policy.provider,
        operator_prompt,
        runtime,
        operator_policy.permissions or "accept-edits",
        provider_session_id=operator_session_id,
        continuity_policy=ContinuityPolicy(
            mode="fresh", lineage_id=continuity_policy.lineage_id
        ),
    )
    operator_resolved = _resolve_agent_command(
        operator_policy.provider, operator_command, base_env
    )
    operator_capability = resolve_provider_usage_capability(
        operator_policy.provider, executable=operator_resolved[0]
    )
    if not operator_capability.supported:
        _cleanup_unspawned_interactive_launch(launch)
        raise ValueError(operator_capability.reason)
    operator_receipt["usage_capability"] = operator_capability.as_dict()
    # The pair is durably reserved and bidirectionally bound before either
    # provider can become ACTIVE. There is no second relationship database.
    try:
        _write_meta(launch.meta_path, child_receipt)
        _write_meta(operator_meta_path, operator_receipt)
    except Exception:
        launch.meta_path.unlink(missing_ok=True)
        operator_meta_path.unlink(missing_ok=True)
        _cleanup_unspawned_interactive_launch(launch)
        raise
    append_event(
        "lifecycle:reserved",
        launch.run_id,
        "Agent reserved with Operator Agent relation",
        {**child_receipt, "meta": str(launch.meta_path)},
    )
    append_event(
        "lifecycle:reserved",
        operator_run_id,
        "Operator Agent reserved with child relation",
        {**operator_receipt, "meta": str(operator_meta_path)},
    )

    operator_env = {
        **base_env,
        "VIBECRAFTED_RUN_ID": operator_run_id,
        "VIBECRAFTED_SESSION_ID": launch.vibecrafted_session_id,
        "VIBECRAFTED_WORKSPACE_ID": launch.workspace_id,
        "VIBECRAFTED_WORKSPACE_INSTANCE_ID": str(
            launch.receipt["workspace_instance_id"]
        ),
        "VIBECRAFTED_PARENT_ROOT": launch.parent_root,
        "VIBECRAFTED_EFFECTIVE_ROOT": launch.effective_root,
        "VIBECRAFTED_AGENT_ROLE": "operator",
        "VIBECRAFTED_PROMPT_ROLE": "/vc-operator",
        "VIBECRAFTED_SUPERVISION_RELATION_ID": relation_id,
        "VIBECRAFTED_SUPERVISION_PEER_RUN_ID": launch.run_id,
        "VIBECRAFTED_SUPERVISED_CHILD_META": str(launch.meta_path),
        "VIBECRAFTED_OPERATOR_PROTOCOL": str(protocol_path),
        "VIBECRAFTED_CONTINUITY_MODE": continuity_policy.mode,
        "VIBECRAFTED_CONTINUITY_LINEAGE_ID": continuity_policy.lineage_id,
    }
    try:
        operator_log = (operator_run_dir / "provider.log").open("ab")
        operator_child = subprocess.Popen(
            operator_resolved,
            cwd=launch.effective_root,
            env=operator_env,
            stdin=subprocess.DEVNULL,
            stdout=operator_log,
            stderr=subprocess.STDOUT,
        )
    except (OSError, ValueError) as exc:
        if "operator_log" in locals():
            operator_log.close()
        cleanup = _cleanup_unspawned_interactive_launch(launch)
        _terminalize_interactive_launch(
            launch,
            child_receipt,
            status="failed",
            exit_code=2,
            terminal_reason="operator_spawn_failed",
            error=str(exc),
            extra={"prepared_worktree_cleanup": cleanup},
        )
        _terminalize_related_receipt(
            operator_run_id,
            operator_meta_path,
            operator_receipt,
            status="failed",
            exit_code=2,
            terminal_reason="operator_spawn_failed",
            error=str(exc),
        )
        raise

    child_env = {
        **base_env,
        "VIBECRAFTED_RUN_ID": launch.run_id,
        "VIBECRAFTED_SESSION_ID": launch.vibecrafted_session_id,
        "VIBECRAFTED_WORKSPACE_ID": launch.workspace_id,
        "VIBECRAFTED_WORKSPACE_INSTANCE_ID": str(
            launch.receipt["workspace_instance_id"]
        ),
        "VIBECRAFTED_BUILD_ID": str(launch.receipt["build_id"]["rendered"]),
        "VIBECRAFTED_PARENT_ROOT": launch.parent_root,
        "VIBECRAFTED_EFFECTIVE_ROOT": launch.effective_root,
        "VIBECRAFTED_AGENT_ROLE": "agent",
        "VIBECRAFTED_PROMPT_ROLE": prompt.splitlines()[0] if prompt else "",
        "VIBECRAFTED_SUPERVISION_RELATION_ID": relation_id,
        "VIBECRAFTED_SUPERVISION_PEER_RUN_ID": operator_run_id,
        "VIBECRAFTED_CONTINUITY_MODE": continuity_policy.mode,
        "VIBECRAFTED_CONTINUITY_LINEAGE_ID": continuity_policy.lineage_id,
    }
    try:
        # The User-facing child keeps the exact inherited descriptors and TTY.
        child = subprocess.Popen(
            child_resolved,
            cwd=launch.effective_root,
            env=child_env,
        )
    except (OSError, ValueError) as exc:
        operator_code = _stop_owned_process(operator_child)
        operator_log.close()
        cleanup = _cleanup_unspawned_interactive_launch(launch)
        _terminalize_interactive_launch(
            launch,
            child_receipt,
            status="failed",
            exit_code=2,
            terminal_reason="child_spawn_failed",
            error=str(exc),
            extra={"prepared_worktree_cleanup": cleanup},
        )
        _terminalize_related_receipt(
            operator_run_id,
            operator_meta_path,
            operator_receipt,
            status="failed",
            exit_code=_shell_status(operator_code),
            terminal_reason="child_spawn_failed",
            error=str(exc),
        )
        raise

    if operator_child.poll() is not None:
        child_code = _stop_owned_process(child)
        operator_code = operator_child.returncode or 0
        operator_log.close()
        _terminalize_interactive_launch(
            launch,
            child_receipt,
            status="failed",
            exit_code=1,
            terminal_reason="supervision_lost_before_active",
            extra={"provider_exit_code": _shell_status(child_code)},
        )
        _terminalize_related_receipt(
            operator_run_id,
            operator_meta_path,
            operator_receipt,
            status="failed",
            exit_code=_shell_status(operator_code),
            terminal_reason="supervision_lost_before_active",
        )
        return 1

    active_at = dt.datetime.now(dt.timezone.utc).isoformat()
    active_relation = {**relation, "state": "active"}
    child_receipt.update(
        updated_at=active_at,
        spawned_at=active_at,
        status="active",
        liveness="active",
        owner_pid=os.getpid(),
        launcher_pid=os.getpid(),
        worker_pid=child.pid,
        supervision=active_relation,
    )
    operator_receipt.update(
        updated_at=active_at,
        spawned_at=active_at,
        status="active",
        liveness="active",
        owner_pid=os.getpid(),
        launcher_pid=os.getpid(),
        worker_pid=operator_child.pid,
        supervision=active_relation,
    )
    try:
        _write_meta(launch.meta_path, child_receipt)
        _write_meta(operator_meta_path, operator_receipt)
        append_event(
            "lifecycle:active",
            launch.run_id,
            "supervised interactive Agent Workspace child is live",
            {
                **child_receipt,
                "meta": str(launch.meta_path),
                "identity_required": True,
            },
        )
        append_event(
            "lifecycle:active",
            operator_run_id,
            "Operator Agent is supervising exact child",
            {
                **operator_receipt,
                "meta": str(operator_meta_path),
                "identity_required": True,
            },
        )
    except Exception as exc:
        child_code = _stop_owned_process(child)
        operator_code = _stop_owned_process(operator_child)
        operator_log.close()
        _terminalize_interactive_launch(
            launch,
            child_receipt,
            status="failed",
            exit_code=1,
            terminal_reason="active_publish_failed",
            error=str(exc),
            extra={"provider_exit_code": _shell_status(child_code)},
        )
        _terminalize_related_receipt(
            operator_run_id,
            operator_meta_path,
            operator_receipt,
            status="failed",
            exit_code=_shell_status(operator_code),
            terminal_reason="active_publish_failed",
            error=str(exc),
        )
        raise

    received_signal: list[int] = []
    previous_handlers: dict[int, Any] = {}

    def _forward_owner_signal(signum: int, _frame: Any) -> None:
        if not received_signal:
            received_signal.append(signum)
        for owned_process in (child, operator_child):
            if owned_process.poll() is None:
                try:
                    owned_process.send_signal(signum)
                except ProcessLookupError:
                    pass

    if threading.current_thread() is threading.main_thread():
        for signum in (
            signal.SIGINT,
            signal.SIGTERM,
            getattr(signal, "SIGHUP", signal.SIGTERM),
        ):
            if signum in previous_handlers:
                continue
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, _forward_owner_signal)

    quota_exhausted = False
    supervision_lost = False
    stop_actor_run_id = ""
    protocol_offset = 0
    provider_returncode = 0
    try:
        while True:
            current_returncode = child.poll()
            measured_usage = usage_reader.poll()
            if measured_usage != child_receipt["measured_usage"]:
                child_receipt["measured_usage"] = measured_usage
                child_receipt["updated_at"] = dt.datetime.now(
                    dt.timezone.utc
                ).isoformat()
                _write_meta(launch.meta_path, child_receipt)
            events, protocol_offset = _poll_operator_protocol(
                protocol_path, protocol_offset
            )
            for event in events:
                _validate_operator_protocol_event(
                    event,
                    relation_id=relation_id,
                    operator_run_id=operator_run_id,
                    child_receipt=child_receipt,
                )
                if event["kind"] == "observation":
                    operator_receipt["supervision"] = {
                        **active_relation,
                        "observation": {
                            "child_run_id": launch.run_id,
                            "child_status": child_receipt["status"],
                            "child_worker_pid": child.pid,
                            "measured_usage": child_receipt["measured_usage"],
                            "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        },
                    }
                    operator_receipt["updated_at"] = dt.datetime.now(
                        dt.timezone.utc
                    ).isoformat()
                    _write_meta(operator_meta_path, operator_receipt)
                elif current_returncode is None:
                    stop_actor_run_id = operator_run_id
                    append_event(
                        "operator:stop-requested",
                        launch.run_id,
                        "Operator Agent requested bounded child stop",
                        {
                            "run_id": launch.run_id,
                            "actor_run_id": operator_run_id,
                            "relation_id": relation_id,
                            "reason": event["reason"],
                        },
                    )
                    provider_returncode = _stop_owned_process(child)
                    break
            if stop_actor_run_id:
                break
            if current_returncode is not None:
                provider_returncode = current_returncode
                break
            operator_returncode = operator_child.poll()
            if operator_returncode is not None:
                provider_returncode = _stop_owned_process(child)
                supervision_lost = not received_signal
                break
            if (
                not received_signal
                and quota.token_budget is not None
                and measured_usage["total_tokens"] >= quota.token_budget
            ):
                quota_exhausted = True
                provider_returncode = _stop_owned_process(child)
                break
            time.sleep(0.05)
    except Exception as exc:
        child_code = _stop_owned_process(child)
        operator_code = _stop_owned_process(operator_child)
        _terminalize_interactive_launch(
            launch,
            child_receipt,
            status="failed",
            exit_code=1,
            terminal_reason="wrapper_exception",
            error=str(exc),
            extra={"provider_exit_code": _shell_status(child_code)},
        )
        _terminalize_related_receipt(
            operator_run_id,
            operator_meta_path,
            operator_receipt,
            status="failed",
            exit_code=_shell_status(operator_code),
            terminal_reason="wrapper_exception",
            error=str(exc),
        )
        raise
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    shell_status = _shell_status(provider_returncode)
    if quota_exhausted:
        status = "quota_exhausted"
        terminal_reason = "quota_exhausted"
        shell_status = QUOTA_EXHAUSTED_EXIT_CODE
    elif received_signal:
        owner_signal = received_signal[0]
        status = "cancelled"
        terminal_reason = f"owner_signal:{signal.Signals(owner_signal).name}"
        shell_status = 128 + owner_signal
    elif stop_actor_run_id:
        status = "cancelled"
        terminal_reason = "operator_policy_stop"
        shell_status = 128 + signal.SIGTERM
    elif supervision_lost:
        status = "failed"
        terminal_reason = "supervision_lost"
        shell_status = 1
    elif provider_returncode < 0:
        status = "cancelled"
        terminal_reason = (
            f"provider_signal:{signal.Signals(abs(provider_returncode)).name}"
        )
    elif provider_returncode == 0:
        status = "completed"
        terminal_reason = "provider_exit_zero"
    else:
        status = "failed"
        terminal_reason = "provider_exit_nonzero"
    terminal_extra = {
        "supervision": {**child_receipt["supervision"], "state": "terminal"}
    }
    if stop_actor_run_id:
        terminal_extra["stop_actor_run_id"] = stop_actor_run_id
    child_terminal = _terminalize_interactive_launch(
        launch,
        child_receipt,
        status=status,
        exit_code=shell_status,
        terminal_reason=terminal_reason,
        exit_signal=abs(provider_returncode) if provider_returncode < 0 else None,
        extra=terminal_extra,
    )
    terminal_observation_confirmed = False
    settlement_error = ""
    if not supervision_lost and not received_signal and operator_child.poll() is None:
        deadline = time.monotonic() + 1.0
        try:
            while time.monotonic() < deadline:
                events, protocol_offset = _poll_operator_protocol(
                    protocol_path, protocol_offset
                )
                for event in events:
                    _validate_operator_protocol_event(
                        event,
                        relation_id=relation_id,
                        operator_run_id=operator_run_id,
                        child_receipt=child_terminal,
                    )
                    if event["kind"] == "observation":
                        terminal_observation_confirmed = True
                        operator_receipt["supervision"] = {
                            **active_relation,
                            "observation": {
                                "child_run_id": launch.run_id,
                                "child_status": child_terminal["status"],
                                "child_worker_pid": child.pid,
                                "measured_usage": child_terminal["measured_usage"],
                                "observed_at": dt.datetime.now(
                                    dt.timezone.utc
                                ).isoformat(),
                            },
                        }
                        _write_meta(operator_meta_path, operator_receipt)
                if terminal_observation_confirmed or operator_child.poll() is not None:
                    break
                time.sleep(0.05)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            settlement_error = str(exc)
    operator_code = _stop_owned_process(operator_child)
    operator_log.close()
    settled_worktree_cleanup = _cleanup_settled_interactive_launch(launch)
    child_terminal["settled_worktree_cleanup"] = settled_worktree_cleanup
    child_terminal["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    _write_meta(launch.meta_path, child_terminal)
    if settled_worktree_cleanup != "not-applicable":
        append_event(
            "lifecycle:worktree-cleanup",
            launch.run_id,
            f"settled interactive worktree cleanup: {settled_worktree_cleanup}",
            {
                "run_id": launch.run_id,
                "result": settled_worktree_cleanup,
                "meta": str(launch.meta_path),
            },
        )
    operator_failed = supervision_lost or bool(settlement_error)
    _terminalize_related_receipt(
        operator_run_id,
        operator_meta_path,
        operator_receipt,
        status="failed" if operator_failed else "completed",
        exit_code=_shell_status(operator_code) if operator_failed else 0,
        terminal_reason=(
            "supervision_lost"
            if supervision_lost
            else "operator_protocol_failed"
            if settlement_error
            else "child_settled"
        ),
        error=settlement_error,
        extra={
            "supervision": {
                **operator_receipt["supervision"],
                "state": "terminal",
                "terminal_observation_confirmed": terminal_observation_confirmed,
            }
        },
    )
    return shell_status


def _operator_supervision_prompt(
    *, child_run_id: str, child_meta_path: Path, relation_id: str, protocol_path: Path
) -> str:
    return (
        "/vc-operator\n"
        "Supervise exactly one child Agent through structured lifecycle truth.\n"
        f"child_run_id: {child_run_id}\n"
        f"child_meta: {child_meta_path}\n"
        f"relation_id: {relation_id}\n"
        f"protocol_jsonl: {protocol_path}\n"
        "Observe child status and measured_usage from child_meta. Write only typed "
        "observation or bounded stop action JSON objects to protocol_jsonl. Remain "
        "live until the child reaches terminal state. Never signal or reap the child.\n"
    )


def _poll_operator_protocol(
    path: Path, offset: int
) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], offset
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Operator Agent protocol must be a regular non-symlink file")
    if path.stat().st_size < offset:
        raise RuntimeError("Operator Agent protocol was truncated")
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        handle.seek(offset)
        while True:
            line_start = handle.tell()
            line = handle.readline()
            if not line:
                break
            if not line.endswith("\n"):
                handle.seek(line_start)
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "Operator Agent protocol contains invalid JSONL"
                ) from exc
            if not isinstance(event, dict):
                raise TypeError("Operator Agent protocol event must be an object")
            events.append(event)
        return events, handle.tell()


def _validate_operator_protocol_event(
    event: dict[str, Any],
    *,
    relation_id: str,
    operator_run_id: str,
    child_receipt: dict[str, Any],
) -> None:
    if event.get("kind") not in {"observation", "action"}:
        raise RuntimeError("Operator Agent protocol kind is unsupported")
    if event.get("actor_run_id") != operator_run_id:
        raise RuntimeError("Operator Agent protocol actor identity mismatch")
    if event.get("child_run_id") != child_receipt["run_id"]:
        raise RuntimeError("Operator Agent protocol child identity mismatch")
    if event.get("relation_id") != relation_id:
        raise RuntimeError("Operator Agent protocol relation identity mismatch")
    if event["kind"] == "observation":
        if event.get("child_status") != child_receipt["status"]:
            raise RuntimeError("Operator Agent observation is not current child truth")
        if event.get("child_worker_pid") != child_receipt["worker_pid"]:
            raise RuntimeError(
                "Operator Agent observation names a foreign child process"
            )
        if event.get("measured_usage") != child_receipt["measured_usage"]:
            raise RuntimeError(
                "Operator Agent observation usage is not exact child truth"
            )
        return
    if event.get("action") not in {"stop", "cancel"}:
        raise RuntimeError("Operator Agent action is outside the bounded policy")
    if event.get("reason") not in {"operator_policy_stop", "operator_policy_cancel"}:
        raise RuntimeError("Operator Agent action reason is outside the bounded policy")


def _stop_owned_process(process: subprocess.Popen[Any]) -> int:
    """Stop and reap one process from the existing wrapper owner only."""
    current = process.poll()
    if current is not None:
        return current
    process.terminate()
    try:
        return process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait()


def _shell_status(returncode: int) -> int:
    return 128 + abs(returncode) if returncode < 0 else returncode


def _terminalize_related_receipt(
    run_id: str,
    meta_path: Path,
    receipt: dict[str, Any],
    *,
    status: str,
    exit_code: int,
    terminal_reason: str,
    error: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    completed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    terminal = {
        **receipt,
        "updated_at": completed_at,
        "completed_at": completed_at,
        "status": status,
        "liveness": "terminal",
        "exit_code": int(exit_code),
        "terminal_reason": terminal_reason,
        **(extra or {}),
    }
    if error:
        terminal["error"] = error
    _write_meta(meta_path, terminal)
    append_event(
        f"lifecycle:{status}",
        run_id,
        f"Operator Agent terminal: {terminal_reason}",
        {**terminal, "meta": str(meta_path), "identity_required": True},
    )
    return terminal


def _cleanup_unspawned_interactive_launch(launch: InteractiveWorkspaceLaunch) -> str:
    """Remove only the clean worktree prepared for a child that never existed."""
    if launch.worktree_manager is None or launch.worktree_geometry is None:
        return "not-applicable"
    try:
        return str(
            launch.worktree_manager.cleanup(launch.worktree_geometry, settled=True)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return f"preserved:{exc}"


def _cleanup_settled_interactive_launch(launch: InteractiveWorkspaceLaunch) -> str:
    """Delegate clean terminal worktree cleanup to the canonical manager."""
    if launch.worktree_manager is None or launch.worktree_geometry is None:
        return "not-applicable"
    try:
        return str(
            launch.worktree_manager.cleanup(launch.worktree_geometry, settled=True)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return f"preserved:{exc}"


def _terminalize_interactive_launch(
    launch: InteractiveWorkspaceLaunch,
    receipt: dict[str, Any],
    *,
    status: str,
    exit_code: int,
    terminal_reason: str,
    exit_signal: int | None = None,
    error: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically terminalize the same interactive receipt and event identity."""
    completed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    terminal = {
        **receipt,
        "updated_at": completed_at,
        "completed_at": completed_at,
        "status": status,
        "liveness": "terminal",
        "exit_code": int(exit_code),
        "terminal_reason": terminal_reason,
        **(extra or {}),
    }
    if exit_signal is not None:
        terminal["exit_signal"] = signal.Signals(exit_signal).name
    if error:
        terminal["error"] = error
    _write_meta(launch.meta_path, terminal)
    append_event(
        f"lifecycle:{status}",
        launch.run_id,
        f"interactive Agent Workspace terminal: {terminal_reason}",
        {**terminal, "meta": str(launch.meta_path), "identity_required": True},
    )
    return terminal


ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SESSION_PATTERNS = (
    re.compile(
        r"(?:^|\[[0-9]{2}:[0-9]{2}:[0-9]{2}\]\s+)session:\s*([A-Za-z0-9][A-Za-z0-9._:-]*)",
        re.MULTILINE,
    ),
    re.compile(
        r"\b(?:thread|conversation|session)[_-]?id['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9][A-Za-z0-9._:-]*)",
        re.IGNORECASE,
    ),
)
TOKEN_PATTERN = re.compile(
    r"tokens:\s*([0-9]+)\s+in(?:\s*\(([0-9]+)\s+cached\))?\s*/\s*([0-9]+)\s+out",
    re.IGNORECASE,
)
# Authoritative per-run totals emitted by the run-closure footer
# (supervisor_async writes these for EVERY agent). Preferred over the
# per-event `tokens: N in / N out` lines, which only some provider
# formatters render and which would otherwise sum partial streaming usage.
FOOTER_TOKEN_PATTERNS = {
    "input": re.compile(r"^\s*tokens_input:\s*([0-9]+)", re.IGNORECASE | re.MULTILINE),
    "cached_input": re.compile(
        r"^\s*tokens_cached_input:\s*([0-9]+)", re.IGNORECASE | re.MULTILINE
    ),
    "cache_write": re.compile(
        r"^\s*tokens_cache_write:\s*([0-9]+)", re.IGNORECASE | re.MULTILINE
    ),
    "output": re.compile(
        r"^\s*tokens_output:\s*([0-9]+)", re.IGNORECASE | re.MULTILINE
    ),
}
JSON_TOKEN_PATTERNS = {
    "input": re.compile(r'"(?:input_tokens|inputTokens|prompt_tokens)"\s*:\s*([0-9]+)'),
    "cached_input": re.compile(
        r'"(?:cached_input_tokens|cached_prompt_tokens|cache_read_input_tokens|cacheReadInputTokens|cacheInputTokens)"\s*:\s*([0-9]+)'
    ),
    "cache_write": re.compile(
        r'"(?:cache_creation_input_tokens|cacheCreateTokens)"\s*:\s*([0-9]+)'
    ),
    "output": re.compile(
        r'"(?:output_tokens|outputTokens|completion_tokens)"\s*:\s*([0-9]+)'
    ),
}
COST_PATTERNS = (
    re.compile(
        r"cost(?:_usd)?['\"]?\s*[:=]\s*\$?([0-9]+(?:\.[0-9]+)?)",
        re.IGNORECASE,
    ),
    re.compile(r"\$([0-9]+\.[0-9]+)\s*(?:usd)?", re.IGNORECASE),
)
MODEL_ENV_VARS = (
    "VIBECRAFTED_PARENT_MODEL",
    "CLAUDE_MODEL",
    "CODEX_MODEL",
    "GEMINI_MODEL",
    "GROK_MODEL",
)
MODEL_PLACEHOLDERS = {"", "none", "null", "unknown", "pending"}


@dataclass
class SpawnHandle:
    """Live/completed handle to one spawned agent process and its artifact paths."""

    run_id: str
    agent: str
    skill: str
    mode: str
    root: Path
    process: Any
    pgid: int | None
    started_at: str
    command: list[str]
    meta_path: Path | None = None
    transcript_path: Path | None = None
    exit_code: int | None = None
    completed_at: str = ""
    session_id: str = ""
    _done: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    @property
    def pid(self) -> int:
        """Underlying child process id."""
        return self.process.pid

    def wait(self, timeout: float | None = None) -> int:
        """Block until the spawned process finishes; raise TimeoutError if it doesn't."""
        if not self._done.wait(timeout):
            raise TimeoutError(f"spawn {self.run_id} still running")
        return int(self.exit_code if self.exit_code is not None else 1)


class _SandboxProcess:
    """Process-like stand-in used when a run executes inside the sandbox adapter."""

    def __init__(self) -> None:
        """Adopt the current process's own pid as the stand-in "child" pid."""
        self.pid = os.getpid()

    def wait(self) -> int:
        """Sandbox execution is synchronous by the time this is called; always exit 0."""
        return 0


def _set_child_pgid() -> None:
    """Put the current (child) process into its own process group; best-effort."""
    try:
        os.setpgid(0, 0)
    except OSError:
        pass


def _default_command(agent: str, prompt: str) -> list[str]:
    """Build the argv for launching *agent* with *prompt* passed inline (ARG_MAX risk).

    Raises ValueError for the deprecated gemini CLI and any unsupported agent.
    """
    if agent == "gemini":
        raise ValueError(
            "gemini CLI is deprecated. Google Antigravity CLI (agy) is the replacement. "
            "Use 'vibecrafted workflow agy --prompt ...' (or agy in other launchers). "
            "No execution path may launch the gemini binary."
        )
    policy = resolve_provider_policy(
        agent, "local-native", "auto" if agent == "junie" else "bypass", "headless"
    )
    if not policy.supported:
        raise ValueError(policy.reason)
    flags = list(policy.flags)
    if agent == "claude":
        return [
            "claude",
            "--print",
            "--verbose",
            *flags,
            prompt,
        ]
    if agent == "codex":
        return ["codex", "exec", *flags, prompt]
    if agent == "agy":
        # agy >= 1.1: --print takes the prompt as its value (Go flags) and
        # print mode does not read stdin; flags must precede it.
        return [
            "agy",
            *flags,
            "--add-dir",
            ".",
            "--print-timeout",
            "30m",
            "--print",
            prompt,
        ]
    if agent == "junie":
        return [
            "junie",
            *flags,
            "--task",
            prompt,
            "--project",
            ".",
            "--skip-update-check",
        ]
    if agent == "grok":
        return [
            "grok",
            "--cwd",
            ".",
            *flags,
            "--no-alt-screen",
            "--single",
            prompt,
        ]
    raise ValueError(f"unsupported agent: {agent}")


def _stdin_command(agent: str) -> list[str]:
    """Build an agent command that receives the full prompt on stdin.

    The command argv must carry flags and paths only; large prompt bodies belong
    on stdin so they do not leak through ps(1) or hit ARG_MAX.
    """

    if agent == "gemini":
        raise ValueError(
            "gemini CLI is deprecated. Google Antigravity CLI (agy) is the replacement. "
            "Use 'vibecrafted workflow agy --prompt ...' (or agy in other launchers). "
            "No execution path may launch the gemini binary."
        )
    policy = resolve_provider_policy(
        agent, "local-native", "auto" if agent == "junie" else "bypass", "headless"
    )
    if not policy.supported:
        raise ValueError(policy.reason)
    flags = list(policy.flags)
    if agent == "claude":
        return [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            *flags,
        ]
    if agent == "codex":
        return [
            "codex",
            "exec",
            "--json",
            *flags,
            "-",
        ]
    if agent == "agy":
        # agy >= 1.1 print mode reads no stdin and --print requires a value;
        # a shell shim folds stdin into the flag. The prompt lands on the
        # inner argv (ARG_MAX-bound) because agy has no file/stdin lane.
        return [
            "bash",
            "-c",
            (
                f"agy {shlex.join(flags)} --add-dir . "
                '--print-timeout 30m --print "$(cat)"'
            ),
        ]
    if agent == "junie":
        return [
            "junie",
            "--project",
            ".",
            "--skip-update-check",
            "--input-format",
            "text",
            "--output-format",
            "json-stream",
        ]
    if agent == "grok":
        return [
            "grok",
            "--cwd",
            ".",
            *flags,
            "--no-alt-screen",
            "--output-format",
            "streaming-json",
            "--prompt-file",
            "/dev/stdin",
        ]
    raise ValueError(f"unsupported agent: {agent}")


def _resolve_agent_command(
    agent: str,
    command: Sequence[str],
    environment: dict[str, str] | None = None,
) -> list[str]:
    """Pin a provider argv to the executable found on the canonical tool PATH.

    Commands owned by another runtime (for example ``python -m`` supervisors or
    test fixtures) pass through unchanged.  The agy stdin adapter is the one
    provider command embedded in ``bash -c`` and is pinned inside that script.
    """

    resolved = list(command)
    if not resolved:
        raise ValueError("agent command must not be empty")
    direct_provider = resolved[0] == agent
    shell_provider = (
        len(resolved) >= 3
        and Path(resolved[0]).name == "bash"
        and resolved[1] == "-c"
        and re.match(rf"^{re.escape(agent)}(?=\s)", resolved[2]) is not None
    )
    if not direct_provider and not shell_provider:
        return resolved
    search_path = agent_tool_search_path(environment)
    executable = which(agent, path=search_path)
    if executable is None:
        raise FileNotFoundError(
            f"provider executable '{agent}' not found on canonical agent tool PATH"
        )
    if direct_provider:
        resolved[0] = executable
    else:
        resolved[2] = re.sub(
            rf"^{re.escape(agent)}(?=\s)",
            shlex.quote(executable),
            resolved[2],
            count=1,
        )
    return resolved


def _parse_launcher_assignment(path: Path, key: str) -> str:
    """Extract the shell-quoted value assigned to *key* (e.g. ``meta=...``) in a launcher script."""
    if not path.is_file():
        return ""
    prefix = f"{key}="
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith(prefix):
            continue
        raw = line.split("=", 1)[1].strip()
        try:
            parts = shlex.split(raw)
        except ValueError:
            return raw.strip("'\"")
        return parts[0] if parts else ""
    return ""


def _read_meta(path: Path | None) -> dict[str, Any]:
    """Read a launcher meta.json; return {} on missing path, missing file, or bad JSON."""
    if path is None or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_meta(path: Path, payload: dict[str, Any]) -> None:
    """Write *payload* as pretty JSON, published atomically via tmp + rename.

    meta.json is read concurrently by the launcher, the startup watcher, the
    control-plane sync and dashboards; an in-place write truncates first, so
    a concurrent reader could observe an empty file. os.replace guarantees a
    reader sees the previous document or the new one, never a torn one.
    """
    tmp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp_path, path)


def _read_text(path: Path) -> str:
    """Best-effort UTF-8 read of *path*; returns "" on any OSError."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _write_text(path: Path, text: str) -> None:
    """Write *text* to *path* as UTF-8, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _clean_text(text: str) -> str:
    """Strip ANSI escape sequences from terminal-captured transcript text."""
    return ANSI_PATTERN.sub("", text)


def _extract_session(text: str) -> str:
    """Find the last session/thread/conversation id mentioned in transcript text."""
    clean = _clean_text(text)
    for pattern in SESSION_PATTERNS:
        matches = pattern.findall(clean)
        if matches:
            return str(matches[-1])
    return ""


def _tokens_total(
    input_tokens: int, cached_input_tokens: int, output_tokens: int
) -> int:
    """Sum usage without double-counting provider-specific cache shapes.

    Claude/Codex: ``input`` already includes cache hits (cached ≤ input).
    Junie-style: ``input`` is non-cached only and ``cached`` is additive
    (cached can exceed input). Detect by comparing magnitudes.
    """
    inp = max(0, int(input_tokens or 0))
    cached = max(0, int(cached_input_tokens or 0))
    out = max(0, int(output_tokens or 0))
    if cached and cached > inp:
        return inp + cached + out
    return inp + out


def _extract_tokens(text: str) -> dict[str, int | None]:
    """Parse token usage from combined transcript/report text.

    Prefers the authoritative run-closure footer, then JSON usage fields,
    then per-event ``tokens: N in / N out`` lines, in that priority order.
    """
    clean = _clean_text(text)
    found = TOKEN_PATTERN.findall(clean)
    json_tokens = {
        key: sum(int(match) for match in pattern.findall(clean))
        for key, pattern in JSON_TOKEN_PATTERNS.items()
    }
    # Prefer the authoritative run-closure footer totals when present: they are
    # written for every agent and carry the final per-run usage, so they work
    # uniformly across providers and never sum partial streaming deltas.
    footer_in = FOOTER_TOKEN_PATTERNS["input"].findall(clean)
    footer_out = FOOTER_TOKEN_PATTERNS["output"].findall(clean)
    if footer_in or footer_out:
        footer_cached = FOOTER_TOKEN_PATTERNS["cached_input"].findall(clean)
        footer_cache_write = FOOTER_TOKEN_PATTERNS["cache_write"].findall(clean)
        input_tokens = int(footer_in[-1]) if footer_in else 0
        cached_tokens = int(footer_cached[-1]) if footer_cached else 0
        cache_write_tokens = int(footer_cache_write[-1]) if footer_cache_write else None
        output_tokens = int(footer_out[-1]) if footer_out else 0
        total_tokens = _tokens_total(input_tokens, cached_tokens, output_tokens)
        if total_tokens or (not found and not any(json_tokens.values())):
            return {
                "input": input_tokens,
                "cached_input": cached_tokens,
                "cache_write": cache_write_tokens,
                "output": output_tokens,
                "total": total_tokens,
            }
    if any(json_tokens.values()):
        return {
            "input": json_tokens["input"],
            "cached_input": json_tokens["cached_input"],
            "cache_write": json_tokens["cache_write"]
            if json_tokens["cache_write"]
            else None,
            "output": json_tokens["output"],
            "total": _tokens_total(
                json_tokens["input"],
                json_tokens["cached_input"],
                json_tokens["output"],
            ),
        }
    if not found:
        return {
            "input": 0,
            "cached_input": 0,
            "cache_write": None,
            "output": 0,
            "total": 0,
        }
    input_tokens = cached_tokens = output_tokens = 0
    for raw_in, raw_cached, raw_out in found:
        input_tokens += int(raw_in)
        cached_tokens += int(raw_cached or 0)
        output_tokens += int(raw_out)
    return {
        "input": input_tokens,
        "cached_input": cached_tokens,
        "cache_write": None,
        "output": output_tokens,
        "total": _tokens_total(input_tokens, cached_tokens, output_tokens),
    }


def _extract_cost(text: str) -> float | None:
    """Parse a USD cost from combined transcript/report text, preferring the footer."""
    clean = _clean_text(text)
    footer = re.findall(
        r"^\s*cost_usd:\s*\$?([0-9]+(?:\.[0-9]+)?)\s*$",
        clean,
        re.IGNORECASE | re.MULTILINE,
    )
    if footer:
        return round(float(footer[-1]), 6)
    totals = re.findall(
        r'"(?:total_cost_usd|totalCostUsd|total_cost)"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        clean,
        re.IGNORECASE,
    )
    if totals:
        return round(float(totals[-1]), 6)
    item_costs = re.findall(r'"cost"\s*:\s*([0-9]+(?:\.[0-9]+)?)', clean, re.IGNORECASE)
    if item_costs:
        return round(sum(float(value) for value in item_costs), 6)
    for pattern in COST_PATTERNS:
        matches = pattern.findall(clean)
        if not matches:
            continue
        try:
            return round(float(matches[-1]), 6)
        except ValueError:
            pass
    return None


def _clean_model(value: object) -> str:
    """Normalize a candidate model value; return "" for known placeholder strings."""
    raw = str(value or "").strip()
    return "" if raw.lower() in MODEL_PLACEHOLDERS else raw


def _fallback_model(agent: object) -> str:
    """Synthesize a `<agent>-cli-default` model label when no real model is known."""
    agent_name = str(agent or "agent").strip() or "agent"
    if agent_name == "agy":
        return "gemini-cli-default"
    return f"{agent_name}-cli-default"


def _extract_model_from_text(text: str) -> str:
    """Find a model identifier in transcript/report text via footer, JSON, or usage-map fields."""
    clean = _clean_text(text)
    for match in reversed(re.findall(r"^model:\s*(.+?)\s*$", clean, re.MULTILINE)):
        model = _clean_model(match)
        if model:
            return model
    json_models = re.findall(
        r'"(?:model|model_id|modelId|model_name|modelName)"\s*:\s*"([^"]+)"',
        clean,
    )
    for match in json_models:
        model = _clean_model(match)
        if model:
            return model
    model_usage_maps = re.findall(r'"modelUsage"\s*:\s*\{\s*"([^"]+)"', clean)
    if model_usage_maps:
        return _clean_model(model_usage_maps[-1])
    return ""


def _resolve_model(payload: dict[str, Any], combined_text: str) -> str:
    """Resolve the effective model: payload, then env vars, then text, then fallback."""
    model = _clean_model(payload.get("model"))
    if model:
        return model
    for env_name in MODEL_ENV_VARS:
        model = _clean_model(os.environ.get(env_name))
        if model:
            return model
    model = _extract_model_from_text(combined_text)
    if model:
        return model
    return _fallback_model(payload.get("agent"))


def _parse_dt(value: object) -> dt.datetime | None:
    """Parse an ISO-ish timestamp string to a UTC-aware datetime, or None if unparsable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _resolve_duration(
    payload: dict[str, Any], completed_at_iso: str
) -> float | int | None:
    """Resolve run duration: an existing valid value wins, else derive from timestamps."""
    current = payload.get("duration_s")
    if isinstance(current, (int, float)):
        return current
    if (
        isinstance(current, str)
        and current.strip()
        and current.lower()
        not in {
            "none",
            "null",
        }
    ):
        try:
            return round(float(current), 3)
        except ValueError:
            pass
    completed_dt = _parse_dt(completed_at_iso)
    started_dt = _parse_dt(payload.get("created_at") or payload.get("updated_at"))
    if completed_dt is None or started_dt is None:
        return None
    return round((completed_dt - started_dt).total_seconds(), 3)


def write_meta(
    meta_path: str | os.PathLike[str],
    status: str,
    agent: str,
    mode: str,
    root: str | os.PathLike[str],
    input_ref: str,
    report: str,
    transcript: str,
    launcher: str,
    model: str = "",
    model_requested: str = "",
    prompt_id: str = "",
    run_id: str = "",
    loop_nr: str | int = 0,
    skill_code: str = "",
    framework_version: str = "",
) -> Path:
    """Write initial launcher meta.json."""
    meta = Path(meta_path)
    meta.parent.mkdir(parents=True, exist_ok=True)

    loop_nr_value: str | int
    try:
        loop_nr_value = int(loop_nr)
    except (ValueError, TypeError):
        loop_nr_value = loop_nr

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "created_at": now_iso,
        "updated_at": now_iso,
        "status": status,
        "agent": agent,
        "mode": mode,
        "root": str(root),
        "input": input_ref,
        "report": report,
        "transcript": transcript,
        "launcher": launcher,
        "prompt_id": prompt_id,
        "run_id": run_id,
        "loop_nr": loop_nr_value,
        "skill_code": skill_code,
        "framework_version": framework_version,
        "exit_code": None,
        "launcher_pid": None,
        "liveness": "pid_pending",
        "model": model,
    }
    if str(model_requested or "").strip():
        payload["model_requested"] = str(model_requested).strip()

    # Cut A: durable workspace identity on every new run meta (best-effort).
    try:
        from .workspace_catalog import resolve_run_workspace_identity

        identity = resolve_run_workspace_identity(root=root, create_if_missing=True)
        payload.update(identity.to_meta_fields())
    except Exception as exc:  # noqa: BLE001 — meta write must not fail closed on catalog
        import logging

        logging.getLogger(__name__).debug(
            "workspace identity stamp skipped: %s", exc, exc_info=False
        )

    _write_meta(meta, payload)
    if run_id:
        append_event(
            "lifecycle:active",
            run_id,
            "legacy shell launcher metadata is live",
            {
                "state": "active",
                "agent": agent,
                "skill": skill_code,
                "mode": mode,
                "root": normalize_run_root(str(root), Path.cwd()),
                "report": report,
                "transcript": transcript,
                "launcher": launcher,
                "model": model,
                **(
                    {"model_requested": str(model_requested).strip()}
                    if str(model_requested or "").strip()
                    else {}
                ),
                "prompt_id": prompt_id,
                "started_at": now_iso,
                "liveness": "active",
                "identity_required": True,
                "meta": str(meta),
                "runtime": "shell",
            },
        )
    return meta


def finish_meta(
    meta_path: str | os.PathLike[str],
    status: str,
    exit_code: int | str = 0,
) -> Path | None:
    """Mark a launcher meta.json terminal and persist completion telemetry."""
    meta = Path(meta_path)
    if not meta.is_file():
        return None

    try:
        payload = json.loads(_read_text(meta))
    except json.JSONDecodeError:
        return None
    launcher_claim_digest = str(
        payload.get("claim_digest") or os.environ.get(CLAIM_DIGEST_ENV, "")
    ).strip()
    if launcher_claim_digest:
        payload["claim_digest"] = launcher_claim_digest

    completed_at = dt.datetime.now(dt.timezone.utc)
    started_dt = _parse_dt(payload.get("created_at") or payload.get("updated_at"))
    duration_s = (
        round((completed_at - started_dt).total_seconds(), 3)
        if started_dt is not None
        else None
    )

    payload["updated_at"] = completed_at.isoformat()
    payload["completed_at"] = completed_at.isoformat()
    payload["duration_s"] = duration_s
    payload["status"] = status
    payload["exit_code"] = int(exit_code)
    payload["liveness"] = "terminal"

    transcript_raw = str(payload.get("transcript") or "")
    transcript_text = (
        _read_text(Path(transcript_raw))[: 64 * 1024] if transcript_raw else ""
    )
    session_id = _extract_session(transcript_text)
    if session_id:
        payload["session_id"] = session_id

    _write_meta(meta, payload)
    run_id = str(payload.get("run_id") or "").strip()
    if run_id:
        append_event(
            f"lifecycle:{status}",
            run_id,
            f"legacy shell launcher finished with status {status}",
            {
                "state": status,
                "exit_code": int(exit_code),
                "completed_at": completed_at.isoformat(),
                "liveness": "terminal",
                "root": str(payload.get("root") or ""),
                "agent": str(payload.get("agent") or ""),
                "skill": str(payload.get("skill_code") or payload.get("skill") or ""),
                "identity_required": True,
                "runtime": "shell",
            },
        )
    return meta


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a markdown document into its ``---`` frontmatter dict and body."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = index
            break
    if end is None:
        return {}, text
    data: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return data, body


def _render_frontmatter(data: dict[str, object]) -> str:
    """Render *data* as ``---``-delimited YAML-ish frontmatter in a fixed key order."""
    order = [
        "run_id",
        "prompt_id",
        "agent",
        "skill",
        "project",
        "model",
        "model_requested",
        "status",
        "claim_status",
        "claim_kind",
        "date",
        "session_id",
        "artifact_stem",
        "artifact_kind",
        "repo_path",
        "tokens_input",
        "tokens_cached_input",
        "tokens_cache_write",
        "tokens_output",
        "tokens_total",
        "cost_usd",
        "cost_source",
    ]
    lines = ["---"]
    emitted = set()
    for key in order:
        if key in data:
            value = data.get(key)
            lines.append(f"{key}: {value if value not in (None, '') else 'unknown'}")
            emitted.add(key)
    for key in sorted(k for k in data if k not in emitted):
        value = data.get(key)
        lines.append(f"{key}: {value if value not in (None, '') else 'unknown'}")
    lines.extend(["---", ""])
    return "\n".join(lines)


def _slug_component(value: object, fallback: str) -> str:
    """Sanitize *value* into a filename-safe slug, falling back to *fallback* if empty."""
    raw = str(value or fallback)
    raw = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")
    return raw or fallback


def _same_file(left: Path, right: Path) -> bool:
    """True when both paths resolve to the same inode, falling back to path equality."""
    try:
        return left.samefile(right)
    except OSError:
        return left == right


def _infer_artifact_store(meta: Path) -> dict[str, object] | None:
    """Infer the org/repo/day artifact-store location from a meta.json's directory shape.

    Returns None unless meta lives at ``.../<org>/<repo>/<YYYY_MMDD>/reports/``.
    """
    reports_dir = meta.parent
    if reports_dir.name != "reports":
        return None
    day_dir = reports_dir.parent
    if not re.fullmatch(r"[0-9]{4}_[0-9]{4}", day_dir.name):
        return None
    repo_dir = day_dir.parent
    org_dir = repo_dir.parent
    if not org_dir.name or not repo_dir.name:
        return None
    yyyy, mmdd = day_dir.name.split("_", 1)
    return {
        "reports_dir": reports_dir,
        "day": f"{yyyy}-{mmdd[:2]}-{mmdd[2:]}",
        "org": org_dir.name,
        "repo": repo_dir.name,
    }


def _unique_stem(
    reports_dir: Path, stem: str, sources: list[Path], disambiguator: str
) -> str:
    """Find a collision-free artifact stem, treating *sources* themselves as non-blocking."""
    candidates = [stem]
    if disambiguator:
        candidates.append(f"{stem}-{_slug_component(disambiguator, 'run')}")
    for index in range(2, 100):
        candidates.append(f"{stem}-{index}")

    suffixes = [".md", ".transcript.log", ".meta.json"]
    for candidate in candidates:
        blocked = False
        for suffix in suffixes:
            target = reports_dir / f"{candidate}{suffix}"
            if not target.exists():
                continue
            if any(
                source and source.exists() and _same_file(target, source)
                for source in sources
            ):
                continue
            blocked = True
            break
        if not blocked:
            return candidate
    return candidates[-1]


def _move_artifact(source: Path, target: Path) -> Path:
    """Rename *source* to *target*; no-op (returns source) if already identical or missing."""
    if not str(source) or not source.is_file() or _same_file(source, target):
        return source
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    return target


def _leave_compat_link(announced: Path, final: Path) -> None:
    """Symlink the originally-announced artifact path to its relocated *final* path.

    Best-effort: never overwrites an existing path and swallows OSError.
    """
    if not str(announced) or not final.is_file():
        return
    if announced == final or _same_file(announced, final):
        return
    if announced.is_symlink() or announced.exists():
        return
    try:
        announced.parent.mkdir(parents=True, exist_ok=True)
        link_target: str | Path = (
            final.name if announced.parent == final.parent else final
        )
        announced.symlink_to(link_target)
    except OSError:
        pass


def _footer(marker: str, payload: dict[str, object]) -> str:
    """Render the `<!-- vibecrafted-artifact-footer:MARKER -->` run-closure YAML block."""
    lines = [
        "",
        f"<!-- vibecrafted-artifact-footer:{marker} -->",
        "---",
        "run_closure:",
        f"  run_id: {payload.get('run_id', 'unknown')}",
        f"  session_id: {payload.get('session_id') or 'unknown'}",
        f"  tokens_input: {payload.get('tokens_input', 0)}",
        f"  tokens_cached_input: {payload.get('tokens_cached_input', 0)}",
    ]
    if payload.get("tokens_cache_write") is not None:
        lines.append(f"  tokens_cache_write: {payload.get('tokens_cache_write')}")
    lines.extend(
        [
            f"  tokens_output: {payload.get('tokens_output', 0)}",
            f"  tokens_total: {payload.get('tokens_total', 0)}",
            f"  cost_usd: {payload.get('cost_usd') if payload.get('cost_usd') is not None else 'unknown'}",
        ]
    )
    if payload.get("cost_source"):
        lines.append(f"  cost_source: {payload.get('cost_source')}")
    if payload.get("model_requested"):
        lines.append(f"  model_requested: {payload.get('model_requested')}")
    lines.extend(
        [
            f"  status: {payload.get('status', 'unknown')}",
            f"  completed_at: {payload.get('completed_at', 'unknown')}",
            f'  resume_hint: "{payload.get("resume_hint", "")}"',
            "---",
            "",
        ]
    )
    return "\n".join(lines)


def _normalize_markdown_artifact(
    path: Path, payload: dict[str, object], *, fallback_body: str = ""
) -> None:
    """Stamp/refresh frontmatter and append the run-closure footer on a markdown artifact."""
    text = _read_text(path)
    if not text and fallback_body:
        text = fallback_body
    if not text:
        return
    fm, body = _parse_frontmatter(text)
    frontmatter: dict[str, object] = dict(fm)
    skill_value = payload.get("skill_code") or payload.get("skill") or "unknown"
    status_value = payload.get("status", "unknown")
    frontmatter_update = {
        "run_id": payload.get("run_id", "unknown"),
        "prompt_id": payload.get("prompt_id", "unknown"),
        "agent": payload.get("agent", "unknown"),
        "skill": skill_value,
        "model": payload.get("model", "unknown"),
        "status": status_value,
        # claim_status mirrors status for board triangulation; agent may have
        # set a more specific claim already in frontmatter — only fill if empty.
        "claim_status": frontmatter.get("claim_status") or status_value,
        "claim_kind": frontmatter.get("claim_kind") or skill_value,
        "date": payload.get("date", "unknown"),
        "session_id": payload.get("session_id") or "unknown",
        "artifact_stem": payload.get("artifact_stem", "unknown"),
        "artifact_kind": payload.get("artifact_kind", "unknown"),
        "repo_path": payload.get("root", "unknown"),
        "tokens_input": payload.get("tokens_input", 0),
        "tokens_cached_input": payload.get("tokens_cached_input", 0),
        "tokens_output": payload.get("tokens_output", 0),
        "tokens_total": payload.get("tokens_total", 0),
        "cost_usd": payload.get("cost_usd")
        if payload.get("cost_usd") is not None
        else "unknown",
    }
    if payload.get("model_requested"):
        frontmatter_update["model_requested"] = payload.get("model_requested")
    else:
        frontmatter.pop("model_requested", None)
    if payload.get("tokens_cache_write") is not None:
        frontmatter_update["tokens_cache_write"] = payload.get("tokens_cache_write")
    else:
        frontmatter.pop("tokens_cache_write", None)
    if payload.get("cost_source"):
        frontmatter_update["cost_source"] = payload.get("cost_source")
    else:
        frontmatter.pop("cost_source", None)
    frontmatter.update(frontmatter_update)
    marker = str(payload.get("run_id") or "unknown")
    new_text = _render_frontmatter(frontmatter) + body.rstrip() + "\n"
    if f"vibecrafted-artifact-footer:{marker}" not in new_text:
        new_text += _footer(marker, payload)
    _write_text(path, new_text)


def finalize_artifacts(
    meta_path: str | os.PathLike[str],
    report_path: str | os.PathLike[str] | None = None,
    transcript_path: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Finalize a launcher run's report/transcript/meta artifact contract."""
    meta = Path(meta_path)
    if not meta.is_file():
        return None

    try:
        payload = json.loads(_read_text(meta))
    except json.JSONDecodeError:
        return None
    launcher_claim_digest = str(
        payload.get("claim_digest") or os.environ.get(CLAIM_DIGEST_ENV, "")
    ).strip()
    if launcher_claim_digest:
        payload["claim_digest"] = launcher_claim_digest

    report = Path(str(report_path or payload.get("report", "")))
    transcript = Path(str(transcript_path or payload.get("transcript", "")))
    announced_report = report
    announced_transcript = transcript
    transcript_text = _read_text(transcript) if str(transcript) else ""
    report_text = _read_text(report) if str(report) else ""
    combined_text = f"{transcript_text}\n{report_text}"

    session_id = payload.get("session_id") or _extract_session(combined_text)
    tokens = _extract_tokens(combined_text)
    tokens_input = int(tokens["input"] or 0)
    tokens_cached_input = int(tokens["cached_input"] or 0)
    tokens_cache_write = tokens["cache_write"]
    tokens_output = int(tokens["output"] or 0)
    tokens_total = int(tokens["total"] or 0)
    cost = _extract_cost(combined_text)
    completed_at = (
        payload.get("completed_at") or dt.datetime.now(dt.timezone.utc).isoformat()
    )
    artifact_time = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    root = payload.get("root") or os.getcwd()
    resume_hint = (
        f"Use `cd {root} && vc-resume --session {session_id}` to continue work with this Agent."
        if session_id
        else f"Use `cd {root} && vc-resume --session <session_id>` to continue work with this Agent."
    )

    payload["session_id"] = session_id or payload.get("session_id") or ""
    payload["model"] = _resolve_model(payload, combined_text)
    cost_source = "provider_reported" if cost is not None else None
    if cost is None:
        cost, cost_source = estimate_cost_usd(
            payload["model"],
            tokens_input=tokens_input,
            tokens_cached_input=tokens_cached_input,
            tokens_output=tokens_output,
        )
    payload["duration_s"] = _resolve_duration(payload, str(completed_at))
    payload["tokens_input"] = tokens_input
    payload["tokens_cached_input"] = tokens_cached_input
    if tokens_cache_write is not None:
        payload["tokens_cache_write"] = tokens_cache_write
    else:
        payload.pop("tokens_cache_write", None)
    payload["tokens_output"] = tokens_output
    payload["tokens_total"] = tokens_total
    token_usage: dict[str, int] = {
        "input": tokens_input,
        "cached_input": tokens_cached_input,
        "output": tokens_output,
        "total": tokens_total,
    }
    if tokens_cache_write is not None:
        token_usage["cache_write"] = int(tokens_cache_write)
    payload["token_usage"] = token_usage
    payload["cost_usd"] = cost
    if cost_source:
        payload["cost_source"] = cost_source
    payload["resume_hint"] = resume_hint
    payload["artifact_contract"] = "vibecrafted.agent-artifact.v1"
    payload["date"] = payload.get("date") or artifact_time

    store = _infer_artifact_store(meta)
    if store:
        reports_dir = Path(str(store["reports_dir"]))
        session_for_name = (
            session_id
            or payload.get("session_id")
            or payload.get("run_id")
            or "unknown-session"
        )
        stem = (
            f"{store['day']}_"
            f"{_slug_component(store['org'], 'org')}_"
            f"{_slug_component(store['repo'], 'repo')}_"
            f"{_slug_component(session_for_name, 'session')}-report"
        )
        stem = _unique_stem(
            reports_dir,
            stem,
            [report, transcript, meta],
            str(payload.get("run_id") or ""),
        )
        final_report = reports_dir / f"{stem}.md"
        final_transcript = reports_dir / f"{stem}.transcript.log"
        final_meta = reports_dir / f"{stem}.meta.json"
        # Contract rule 6: refuse bare Untitled*.md and unbound report paths.
        require_bound_markdown(
            final_report,
            run_id=str(payload.get("run_id") or ""),
            claim_digest=str(payload.get("claim_digest") or ""),
        )

        report = _move_artifact(report, final_report)
        transcript = _move_artifact(transcript, final_transcript)
        _leave_compat_link(announced_report, report)
        _leave_compat_link(announced_transcript, transcript)
        # The worker writes its final handoff next to the announced transcript as
        # `<transcript>.last-message.md` (codex --output-last-message, claude/gemini
        # salvage). Relocate it alongside the transcript so consumers that derive it
        # from meta["transcript"] (resume, aicx, the spawn smokes) still find it;
        # otherwise it is orphaned at the pre-finalize path.
        announced_last_message = announced_transcript.with_suffix(".last-message.md")
        final_last_message = transcript.with_suffix(".last-message.md")
        if announced_last_message.is_file() and not _same_file(
            announced_last_message, final_last_message
        ):
            final_last_message = _move_artifact(
                announced_last_message, final_last_message
            )
            _leave_compat_link(announced_last_message, final_last_message)
        payload["report"] = str(report)
        payload["transcript"] = str(transcript)
        payload["meta"] = str(final_meta)
        payload["artifact_stem"] = stem
        payload["artifact_kind"] = "report"

    payload["artifact_footer"] = {
        "run_id": payload.get("run_id", "unknown"),
        "session_id": payload.get("session_id") or "",
        "tokens_input": tokens_input,
        "tokens_cached_input": tokens_cached_input,
        "tokens_output": tokens_output,
        "tokens_total": tokens_total,
        "cost_usd": cost,
        "resume_hint": resume_hint,
    }
    if payload.get("model_requested"):
        payload["artifact_footer"]["model_requested"] = payload.get("model_requested")
    if tokens_cache_write is not None:
        payload["artifact_footer"]["tokens_cache_write"] = tokens_cache_write
    if payload.get("cost_source"):
        payload["artifact_footer"]["cost_source"] = payload.get("cost_source")
    payload.setdefault("completed_at", completed_at)
    payload["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()

    target_meta = Path(str(payload.get("meta") or meta))
    target_meta.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if not _same_file(meta, target_meta) and meta.exists():
        meta.unlink()
        _leave_compat_link(meta, target_meta)
    meta = target_meta

    footer_payload = {
        **payload,
        "tokens_input": tokens_input,
        "tokens_cached_input": tokens_cached_input,
        "tokens_output": tokens_output,
        "tokens_total": tokens_total,
        "cost_usd": cost,
    }
    if tokens_cache_write is not None:
        footer_payload["tokens_cache_write"] = tokens_cache_write
    else:
        footer_payload.pop("tokens_cache_write", None)

    if str(transcript):
        _normalize_markdown_artifact(transcript, footer_payload)
        write_runtime_transcript_manifest(
            transcript,
            run_id=str(payload.get("run_id") or ""),
        )
    if (
        str(report)
        and report.exists()
        and report.suffix.lower() in {".md", ".markdown"}
    ):
        stamp_launcher_report_identity(
            report,
            run_id=str(payload.get("run_id") or ""),
            session_id=str(payload.get("session_id") or ""),
            agent=str(payload.get("agent") or ""),
            skill=str(payload.get("skill_code") or payload.get("skill") or ""),
            status=str(payload.get("status") or ""),
            model=str(payload.get("model") or ""),
            claim_digest=launcher_claim_digest,
        )
        _normalize_markdown_artifact(report, footer_payload)
    return meta


def _ensure_failed_report_artifact(
    handle: SpawnHandle, exit_code: int, completed_at: str
) -> None:
    """Manufacture a minimal failed-run report if the worker never wrote one, then finalize."""
    if handle.meta_path is None or not handle.meta_path.is_file():
        return
    payload = _read_meta(handle.meta_path)
    report_value = str(payload.get("report") or "")
    if not report_value:
        return

    report = Path(report_value)
    transcript = handle.transcript_path
    if transcript is None and payload.get("transcript"):
        transcript = Path(str(payload["transcript"]))

    payload["status"] = "failed"
    payload["exit_code"] = exit_code
    payload["completed_at"] = completed_at
    if transcript is not None:
        payload["transcript"] = str(transcript)
    payload["report"] = str(report)
    _write_meta(handle.meta_path, payload)

    if not report.exists():
        report.parent.mkdir(parents=True, exist_ok=True)
        try:
            require_bound_markdown(
                report,
                run_id=str(payload.get("run_id") or handle.run_id or ""),
            )
        except BareMarkdownError:
            # Fall back to a bound name rather than writing Untitled*.md.
            report = report.with_name(
                f"{payload.get('run_id') or handle.run_id or 'run'}-failed-report.md"
            )
            payload["report"] = str(report)
            _write_meta(handle.meta_path, payload)
        transcript_ref = str(transcript or payload.get("transcript") or "")
        report.write_text(
            "\n".join(
                [
                    "---",
                    f"run_id: {payload.get('run_id') or handle.run_id}",
                    "status: failed",
                    f"exit_code: {exit_code}",
                    f"completed_at: {completed_at}",
                    f"transcript: {transcript_ref}",
                    "---",
                    "",
                    "# Agent run failed",
                    "",
                    "The supervised agent process exited before writing its final report.",
                    "",
                    f"Transcript: {transcript_ref or '-'}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    finalize_artifacts(handle.meta_path, report, transcript)


def _maybe_extract_session_id(handle: SpawnHandle) -> str:
    """Return the agent session id, reading meta first, else scraping the transcript."""
    meta = _read_meta(handle.meta_path)
    if meta.get("session_id"):
        return str(meta["session_id"])

    transcript = handle.transcript_path
    if transcript is None and meta.get("transcript"):
        transcript = Path(str(meta["transcript"]))
    if transcript is None or not transcript.is_file():
        return ""

    text = transcript.read_text(encoding="utf-8", errors="replace")
    session_id = extract_session_id(handle.agent, text) or ""
    if session_id and handle.meta_path is not None and handle.meta_path.is_file():
        meta["session_id"] = session_id
        _write_meta(handle.meta_path, meta)
    return session_id


class Supervisor:
    """Small UNIX process supervisor for Vibecrafted agent launchers."""

    def spawn(
        self,
        agent: str,
        prompt: str,
        *,
        skill: str,
        mode: str,
        root: str | os.PathLike[str],
        on_event: EventCallback | None = None,
        command: Sequence[str] | None = None,
        env: dict[str, str] | None = None,
        run_id: str | None = None,
        meta_path: str | os.PathLike[str] | None = None,
        transcript_path: str | os.PathLike[str] | None = None,
        sandbox: bool = False,
        sandbox_policy: str | os.PathLike[str] | None = None,
        sandbox_config: dict[str, Any] | None = None,
    ) -> SpawnHandle:
        """Launch *agent* (subprocess or sandbox), returning a live SpawnHandle.

        Starts a background watcher thread that fills in exit_code/session_id
        and emits spawn-* lifecycle events as the child completes.
        """
        root_path = Path(normalize_run_root(os.fspath(root)))
        command_list = (
            list(command) if command is not None else _default_command(agent, prompt)
        )
        effective_run_id = (
            run_id or os.environ.get("VIBECRAFTED_RUN_ID") or f"{skill}-manual"
        )

        launcher = Path(command_list[-1]).expanduser() if command_list else Path()
        inferred_meta = Path(meta_path).expanduser() if meta_path is not None else None
        inferred_transcript = (
            Path(transcript_path).expanduser() if transcript_path is not None else None
        )
        if inferred_meta is None and launcher.suffix == ".sh":
            parsed = _parse_launcher_assignment(launcher, "meta")
            inferred_meta = Path(parsed).expanduser() if parsed else None
        if inferred_transcript is None and launcher.suffix == ".sh":
            parsed = _parse_launcher_assignment(launcher, "transcript")
            inferred_transcript = Path(parsed).expanduser() if parsed else None

        child_env = os.environ.copy()
        if env:
            child_env.update(env)
        session_id = ensure_session_id(child_env.get("VIBECRAFTED_SESSION_ID"))
        child_env.setdefault("VIBECRAFTED_RUN_ID", effective_run_id)
        child_env["VIBECRAFTED_SESSION_ID"] = session_id

        if sandbox:
            if not sandbox_supported(agent):
                raise ValueError(f"agent does not support sandbox dispatch: {agent}")
            sandbox_process = _SandboxProcess()
            handle = SpawnHandle(
                run_id=effective_run_id,
                agent=agent,
                skill=skill,
                mode=mode,
                root=root_path,
                process=sandbox_process,
                pgid=None,
                started_at=utc_now_iso(),
                command=command_list,
                meta_path=inferred_meta,
                transcript_path=inferred_transcript,
                session_id=session_id,
            )
            self._emit(
                "spawn-started",
                handle,
                "supervisor spawned sandbox child",
                {"pid": sandbox_process.pid, "pgid": None, "command": command_list},
                on_event,
            )
            thread = threading.Thread(
                target=self._run_sandbox,
                args=(
                    handle,
                    child_env,
                    sandbox_policy,
                    sandbox_config or {},
                    on_event,
                ),
                daemon=True,
            )
            handle._thread = thread
            thread.start()
            return handle

        # start_new_session puts the child in its own process group (same intent
        # as setpgid) without the PLW1509 preexec_fn hazard in threaded hosts.
        process = subprocess.Popen(
            command_list,
            cwd=str(root_path),
            env=child_env,
            text=True,
            start_new_session=hasattr(os, "setpgid"),
        )
        try:
            pgid = os.getpgid(process.pid)
        except OSError:
            pgid = None

        handle = SpawnHandle(
            run_id=effective_run_id,
            agent=agent,
            skill=skill,
            mode=mode,
            root=root_path,
            process=process,
            pgid=pgid,
            started_at=utc_now_iso(),
            command=command_list,
            meta_path=inferred_meta,
            transcript_path=inferred_transcript,
            session_id=session_id,
        )
        self._emit(
            "spawn-started",
            handle,
            "supervisor spawned child",
            {"pid": process.pid, "pgid": pgid, "command": command_list},
            on_event,
        )
        thread = threading.Thread(
            target=self._wait_owner, args=(handle, on_event), daemon=True
        )
        handle._thread = thread
        thread.start()
        return handle

    def _run_sandbox(
        self,
        handle: SpawnHandle,
        env: dict[str, str],
        sandbox_policy: str | os.PathLike[str] | None,
        sandbox_config: dict[str, Any],
        on_event: EventCallback | None,
    ) -> None:
        """Background-thread target: run the sandbox adapter and emit its terminal event."""
        try:
            from .sandbox import SandboxAdapter, SandboxPolicy

            policy = SandboxPolicy.load(sandbox_policy, root=handle.root)
            adapter = SandboxAdapter(
                policy=policy,
                server_url=sandbox_config.get("server_url"),
                api_key_path=sandbox_config.get("api_key_path"),
            )
            result = adapter.execute_sync(
                handle.command,
                env=env,
                cwd=handle.root,
                timeout=sandbox_config.get("timeout"),
                run_id=handle.run_id,
                agent=handle.agent,
                skill=handle.skill,
                mode=handle.mode,
                on_event=on_event,
            )
            handle.exit_code = result.exit_code
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - defensive event path
            handle.exit_code = 1
            self._emit(
                "spawn-failed",
                handle,
                f"sandbox execution failed: {exc}",
                {"pid": handle.pid, "pgid": handle.pgid, "exit_code": 1},
                on_event,
            )
            handle._done.set()
            return

        handle.completed_at = utc_now_iso()
        extracted_session_id = _maybe_extract_session_id(handle)
        if extracted_session_id:
            handle.session_id = extracted_session_id
        kind = "spawn-completed" if handle.exit_code == 0 else "spawn-failed"
        self._emit(
            kind,
            handle,
            f"sandbox child exited with {handle.exit_code}",
            {
                "pid": handle.pid,
                "pgid": handle.pgid,
                "exit_code": handle.exit_code,
                "session_id": handle.session_id,
                "meta": str(handle.meta_path or ""),
                "transcript": str(handle.transcript_path or ""),
                "substrate": "microsandbox",
            },
            on_event,
        )
        handle._done.set()

    def _wait_owner(self, handle: SpawnHandle, on_event: EventCallback | None) -> None:
        """Background-thread target: block on subprocess exit, finalize state and events."""
        exit_code = handle.process.wait()
        handle.exit_code = exit_code
        handle.completed_at = utc_now_iso()
        extracted_session_id = _maybe_extract_session_id(handle)
        if extracted_session_id:
            handle.session_id = extracted_session_id
        if exit_code != 0:
            _ensure_failed_report_artifact(handle, exit_code, handle.completed_at)
        kind = "spawn-completed" if exit_code == 0 else "spawn-failed"
        self._emit(
            kind,
            handle,
            f"supervisor child exited with {exit_code}",
            {
                "pid": handle.pid,
                "pgid": handle.pgid,
                "exit_code": exit_code,
                "session_id": handle.session_id,
                "meta": str(handle.meta_path or ""),
                "transcript": str(handle.transcript_path or ""),
            },
            on_event,
        )
        if handle.transcript_path and not handle.session_id:
            self._emit(
                "session_id_extraction_failed",
                handle,
                "could not extract agent session_id from transcript",
                {"agent": handle.agent, "transcript": str(handle.transcript_path)},
                on_event,
            )
        handle._done.set()

    def _emit(
        self,
        kind: str,
        handle: SpawnHandle,
        message: str,
        payload: dict[str, Any],
        on_event: EventCallback | None,
    ) -> None:
        """Append a durable lifecycle event and forward it to the caller's callback."""
        event = append_event(
            kind,
            handle.run_id,
            message,
            {
                "agent": handle.agent,
                "skill": handle.skill,
                "mode": handle.mode,
                "root": str(handle.root),
                "session_id": handle.session_id,
                "identity_required": True,
                **payload,
            },
        )
        if on_event is not None:
            on_event(event)


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse CLI: write-meta/finalize-artifacts/prepare-report/finish-meta."""
    parser = argparse.ArgumentParser(description="Vibecrafted launcher helpers.")
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser(
        "write-meta",
        help="Write initial launcher meta.json.",
    )
    write.add_argument("meta")
    write.add_argument("status")
    write.add_argument("agent")
    write.add_argument("mode")
    write.add_argument("root")
    write.add_argument("input")
    write.add_argument("report")
    write.add_argument("transcript")
    write.add_argument("launcher")
    write.add_argument("--model", default="")
    write.add_argument("--model-requested", default="")
    write.add_argument("--prompt-id", default="")
    write.add_argument("--run-id", default="")
    write.add_argument("--loop-nr", default="0")
    write.add_argument("--skill-code", default="")
    write.add_argument("--framework-version", default="")

    finalize = sub.add_parser(
        "finalize-artifacts",
        help="Finalize launcher meta/report/transcript artifacts.",
    )
    finalize.add_argument("meta")
    finalize.add_argument("report", nargs="?")
    finalize.add_argument("transcript", nargs="?")
    prepare = sub.add_parser(
        "prepare-report",
        help="Materialize the launcher-owned report identity template.",
    )
    prepare.add_argument("report")
    prepare.add_argument("run_id")
    prepare.add_argument("agent")
    prepare.add_argument("skill")
    finish = sub.add_parser(
        "finish-meta",
        help="Mark launcher meta terminal and persist completion telemetry.",
    )
    finish.add_argument("meta")
    finish.add_argument("status")
    finish.add_argument("exit_code", nargs="?", default="0")
    policy = sub.add_parser(
        "policy-command", help="Resolve the canonical interactive provider policy."
    )
    policy.add_argument("provider", choices=POLICY_PROVIDERS)
    policy.add_argument("--runtime", choices=RUNTIME_POLICIES, default="local-native")
    policy.add_argument("--permissions", choices=PERMISSION_POLICIES, default="bypass")
    interactive_command = sub.add_parser(
        "interactive-command", help="Build the canonical interactive workspace wrapper."
    )
    interactive_command.add_argument("provider", choices=POLICY_PROVIDERS)
    interactive_command.add_argument(
        "--runtime", choices=RUNTIME_POLICIES, default="local-native"
    )
    interactive_command.add_argument(
        "--permissions", choices=PERMISSION_POLICIES, default="bypass"
    )
    interactive_command.add_argument("--token-budget", default="safe")
    interactive_command.add_argument(
        "--operator", choices=OPERATOR_POLICIES, default="none"
    )
    interactive_command.add_argument(
        "--continuity", choices=CONTINUITY_MODES, default="fresh"
    )
    interactive_command.add_argument("--parent-session", default="")
    interactive_command.add_argument("--continuity-parent", default="")
    interactive_command.add_argument("--root", required=True)
    interactive_launch = sub.add_parser(
        "interactive-launch", help="Prepare and exec an interactive Agent Workspace."
    )
    interactive_launch.add_argument("provider", choices=POLICY_PROVIDERS)
    interactive_launch.add_argument(
        "--runtime", choices=RUNTIME_POLICIES, default="local-native"
    )
    interactive_launch.add_argument(
        "--permissions", choices=PERMISSION_POLICIES, default="bypass"
    )
    interactive_launch.add_argument("--root", required=True)
    interactive_launch.add_argument("--prompt", required=True)
    interactive_launch.add_argument("--token-budget", default="safe")
    interactive_launch.add_argument(
        "--operator", choices=OPERATOR_POLICIES, default="none"
    )
    interactive_launch.add_argument(
        "--continuity", choices=CONTINUITY_MODES, default="fresh"
    )
    interactive_launch.add_argument("--parent-session", default="")
    interactive_launch.add_argument("--continuity-parent", default="")
    sub.add_parser(
        "policy-matrix", help="Print the complete provider policy matrix as JSON."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point dispatching to the launcher helper subcommands."""
    args = _build_parser().parse_args(argv)
    if args.command == "write-meta":
        write_meta(
            args.meta,
            args.status,
            args.agent,
            args.mode,
            args.root,
            args.input,
            args.report,
            args.transcript,
            args.launcher,
            model=args.model,
            model_requested=args.model_requested,
            prompt_id=args.prompt_id,
            run_id=args.run_id,
            loop_nr=args.loop_nr,
            skill_code=args.skill_code,
            framework_version=args.framework_version,
        )
        return 0
    if args.command == "finish-meta":
        finish_meta(args.meta, args.status, args.exit_code)
        return 0
    if args.command == "finalize-artifacts":
        final_meta = finalize_artifacts(args.meta, args.report, args.transcript)
        if final_meta is None:
            return 1
        print(final_meta.resolve(strict=True))
        return 0
    if args.command == "prepare-report":
        materialize_launcher_report_template(
            args.report,
            run_id=args.run_id,
            agent=args.agent,
            skill=args.skill,
            claim_digest=os.environ.get(CLAIM_DIGEST_ENV, ""),
        )
        return 0
    if args.command == "policy-command":
        try:
            command = interactive_policy_command(
                args.provider, sys.stdin.read(), args.runtime, args.permissions
            )
        except ValueError as exc:
            print(f"UNSUPPORTED: {exc}", file=sys.stderr)
            return 2
        print(shlex.join(command))
        return 0
    if args.command == "interactive-command":
        prompt = sys.stdin.read()
        try:
            command = interactive_workspace_command(
                args.provider,
                prompt,
                args.runtime,
                args.permissions,
                args.root,
                args.token_budget,
                args.operator,
                args.continuity,
                args.parent_session,
                args.continuity_parent,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(shlex.join(command))
        return 0
    if args.command == "interactive-launch":
        try:
            return launch_interactive_workspace(
                args.provider,
                args.prompt,
                args.runtime,
                args.permissions,
                args.root,
                args.token_budget,
                args.operator,
                args.continuity,
                args.parent_session,
                args.continuity_parent,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if args.command == "policy-matrix":
        print(
            json.dumps(
                [
                    resolve_provider_policy(p, r, q, m).as_dict()
                    for p in POLICY_PROVIDERS
                    for r in RUNTIME_POLICIES
                    for q in PERMISSION_POLICIES
                    for m in POLICY_MODES
                ],
                indent=2,
            )
        )
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover - CLI entry point.
    raise SystemExit(main())
