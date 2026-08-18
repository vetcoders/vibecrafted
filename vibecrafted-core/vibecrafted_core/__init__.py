"""Vibecrafted core package: version resolution plus lazy re-exports of runtime helpers."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import Any

from .artifacts import ArtifactValidation, validate_artifacts
from .capabilities import (
    ToolCapability,
    foundation_capabilities,
    probe_tool,
)
from .control_plane import (
    Event,
    RunStatus,
    await_run,
    control_plane_home,
    event_stream_path,
    lookup_run,
    read_event_tail,
    run_snapshot_dir,
    subscribe_events,
    sync_state,
)
from .doctor import doctor_run, doctor_summary
from .events import append_event
from .git import repo_full, repo_full_summary
from .lifecycle import (
    EventKind,
    RunState,
    is_final_state,
    is_negative_state,
    transition_allowed,
)
from .perception import (
    DEFAULT_MCP_TRANSPORT,
    DEFAULT_WATCH_MODE,
    WatchOutcome,
    ensure_watch,
    loctree_mcp_config_entry,
    mcp_endpoint,
    mcp_servers_config,
    port_for_root,
    watcher_running,
)
from .runtime_paths import (
    read_staged_tools_version,
    read_version_file,
    resolve_env_path,
    version_is_stamped,
    vibecrafted_home,
    xdg_config_home,
)
from .supervisor_async import AsyncRunHandle, AsyncSupervisor


def _version_from_git(package_dir: Path, base: str) -> str | None:
    """Lift a bare semver to ``base+gSHORTSHA`` when the package sits in a git tree.

    Used only after staged install stamp and package VERSION fail to provide
    ``+g`` — never as a substitute for ``make install``.
    """
    import subprocess

    if not base or base == "unknown":
        return None
    root = package_dir
    for _ in range(8):
        if (root / ".git").exists():
            break
        if root.parent == root:
            return None
        root = root.parent
    else:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short=8", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    sha = (proc.stdout or "").strip()
    if not sha or any(c not in "0123456789abcdefABCDEF" for c in sha):
        return None
    bare = base.split("+", 1)[0]
    return f"{bare}+g{sha}"


def _mark_unstamped(version: str) -> str:
    """Never claim release identity without a git stamp."""
    if version_is_stamped(version) or version.endswith("+UNSTAMPED"):
        return version
    if version == "unknown":
        return version
    return f"{version}+UNSTAMPED"


def _resolve_installed_version() -> str:
    """Resolve the operator-facing install identity.

    Priority (docs/INSTALL.md — ``VERSION`` / ``--version`` share ``+g<sha>``):

    1. Stamped package VERSION next to this module (staged tools tree).
    2. Stamped ``make install`` tools/vibecrafted-current — wins over a bare
       living-tree editable (classic Homebrew ``pip install -e`` shadow).
    3. Stamped ``importlib.metadata`` version.
    4. Git short SHA lifted onto a bare package VERSION (dev checkout honesty).
    5. Bare version marked ``+UNSTAMPED`` — never silent ``3.7.0`` alone.
    """
    package_dir = Path(__file__).resolve().parent
    packaged_version = read_version_file(package_dir)
    staged_version = read_staged_tools_version()

    if version_is_stamped(packaged_version):
        return packaged_version
    if version_is_stamped(staged_version):
        return staged_version

    try:
        meta_version = importlib.metadata.version("vibecrafted")
    except importlib.metadata.PackageNotFoundError:
        meta_version = None
    if meta_version and version_is_stamped(meta_version):
        return meta_version

    bare = (
        packaged_version
        if packaged_version != "unknown"
        else (meta_version or "unknown")
    )
    if bare != "unknown":
        git_version = _version_from_git(package_dir, bare)
        if git_version and version_is_stamped(git_version):
            return git_version
        return _mark_unstamped(bare)
    return "unknown"


__version__ = _resolve_installed_version()

_LAZY_EXPORTS = {
    "DELIVERY_EVENT_KINDS": ".events",
    "DeliveryAxes": ".control_plane",
    "DeliveryEventKind": ".events",
    "DeliveryStore": ".delivery",
    "DeliveryStoreError": ".delivery",
    "ProviderCapability": ".continuity",
    "SettlementLedgerAppendResult": ".settlement_ledger",
    "SettlementLedgerCollision": ".settlement_ledger",
    "SettlementLedgerCorrupt": ".settlement_ledger",
    "SettlementLedgerError": ".settlement_ledger",
    "SettlementLedgerOrderError": ".settlement_ledger",
    "append_delivery_event": ".events",
    "capability_registry": ".continuity",
    "probe_provider": ".continuity",
    "read_delivery_axes": ".control_plane",
    "read_settlement_ledger": ".settlement_ledger",
    "settlement_ledger_path": ".settlement_ledger",
    "WorkflowLaunchSpec": ".workflow",
    "await_launch_truth": ".workflow",
    "build_launch_command": ".workflow",
    "launch_workflow": ".workflow",
    "native_resume_run": ".workflow",
    "normalize_launch_spec": ".workflow",
    "retry_run": ".workflow",
    "stop_run": ".workflow",
    "vibecrafted_launcher": ".workflow",
}


def __getattr__(name: str) -> Any:
    """Lazily expose optional workflow helpers without preloading CLI modules."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    # Each branch imports its sibling BY MODULE PATH. The obvious spelling here
    # is `from . import workflow`, and it is what this chain used to say — but
    # `.` is the package barrel, i.e. this very file, so every static importer
    # graph recorded `__init__.py -> __init__.py` and reported a structural
    # self-cycle that has no load-order meaning. The absolute form names the
    # module that actually owns the symbol. Runtime behaviour is unchanged:
    # these imports still happen lazily, only when __getattr__ is reached.
    #
    # The spelling is `import vibecrafted_core.X`, not `import ... as X`: ruff's
    # PLR0402 rewrites an alias that repeats the last component back into
    # `from vibecrafted_core import X`, which is the barrel again and brings the
    # self-cycle straight back. Measured after the first attempt: the formatter
    # undid it in the pre-commit hook.
    module: Any
    if module_name == ".workflow":
        import vibecrafted_core.workflow

        module = vibecrafted_core.workflow
    elif module_name == ".continuity":
        import vibecrafted_core.continuity

        module = vibecrafted_core.continuity
    elif module_name == ".delivery":
        import vibecrafted_core.delivery

        module = vibecrafted_core.delivery
    elif module_name == ".events":
        import vibecrafted_core.events

        module = vibecrafted_core.events
    elif module_name == ".control_plane":
        import vibecrafted_core.control_plane

        module = vibecrafted_core.control_plane
    elif module_name == ".settlement_ledger":
        import vibecrafted_core.settlement_ledger

        module = vibecrafted_core.settlement_ledger
    else:  # pragma: no cover - _LAZY_EXPORTS is the whitelist.
        raise AttributeError(f"module {__name__!r} has no lazy module for {name!r}")

    value = getattr(module, name)
    globals()[name] = value
    return value


__all__ = [
    "DEFAULT_MCP_TRANSPORT",
    "DEFAULT_WATCH_MODE",
    "DELIVERY_EVENT_KINDS",
    "ArtifactValidation",
    "AsyncRunHandle",
    "AsyncSupervisor",
    "DeliveryAxes",
    "DeliveryEventKind",
    "DeliveryStore",
    "DeliveryStoreError",
    "Event",
    "EventKind",
    "ProviderCapability",
    "RunState",
    "RunStatus",
    "SettlementLedgerAppendResult",
    "SettlementLedgerCollision",
    "SettlementLedgerCorrupt",
    "SettlementLedgerError",
    "SettlementLedgerOrderError",
    "ToolCapability",
    "WatchOutcome",
    "WorkflowLaunchSpec",
    "append_delivery_event",
    "append_event",
    "await_launch_truth",
    "await_run",
    "build_launch_command",
    "capability_registry",
    "control_plane_home",
    "doctor_run",
    "doctor_summary",
    "ensure_watch",
    "event_stream_path",
    "foundation_capabilities",
    "is_final_state",
    "is_negative_state",
    "launch_workflow",
    "loctree_mcp_config_entry",
    "lookup_run",
    "mcp_endpoint",
    "mcp_servers_config",
    "native_resume_run",
    "normalize_launch_spec",
    "port_for_root",
    "probe_provider",
    "probe_tool",
    "read_delivery_axes",
    "read_event_tail",
    "read_settlement_ledger",
    "read_staged_tools_version",
    "read_version_file",
    "repo_full",
    "repo_full_summary",
    "resolve_env_path",
    "retry_run",
    "run_snapshot_dir",
    "settlement_ledger_path",
    "stop_run",
    "subscribe_events",
    "sync_state",
    "transition_allowed",
    "validate_artifacts",
    "version_is_stamped",
    "vibecrafted_home",
    "vibecrafted_launcher",
    "watcher_running",
    "xdg_config_home",
]
