"""Live capability discovery for the product foundations (loctree / aicx).

The runtime owns its own lifecycle but treats ``loct``, ``loctree``,
``loctree-mcp``, ``aicx`` and ``aicx-mcp`` as *external product
dependencies*: it never installs, symlinks or shadows their binaries. This
module is the single place that answers two operator questions about those
foundations without ever mutating them:

* **Are they actually runnable** (real execution), not merely present on
  ``$PATH``? This distinguishes ``product_missing`` (binary absent) from
  ``product_broken`` (binary on ``$PATH`` but it fails to execute).
* **What capabilities does the *currently installed* binary expose** after an
  upgrade? Discovery probes the live ``--help`` surface, so a fresh ``loct``
  release that grows a subcommand is reflected the next time this is called.

Provenance is reported (canonical ``~/.local/bin`` vs. ghost roots such as
``~/.cargo/bin``) but never enforced here — enforcement stays in the
installer doctor. This module is read-only by contract.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .clock import utc_now_iso
from .runtime_paths import vibecrafted_launcher_bin

# Ghost roots the runtime explicitly refuses to treat as canonical. A product
# resolved from one of these is runnable but flagged ``noncanonical`` so the
# operator can clean up before it becomes a time bomb.
_GHOST_BIN_ROOTS = ("/.cargo/bin", "/usr/local/bin")

# Default product foundations probed by :func:`foundation_capabilities`.
# ``version_args`` and ``capability_args`` are kept conservative: every
# loctree/aicx entrypoint speaks ``--version`` and ``--help``.
DEFAULT_FOUNDATIONS: tuple[str, ...] = (
    "loct",
    "loctree",
    "loctree-mcp",
    "aicx",
    "aicx-mcp",
)

# A small executor signature so tests can inject deterministic results without
# spawning external processes.
Runner = Callable[[Sequence[str]], "ProbeResult"]


@dataclass(frozen=True)
class ProbeResult:
    """Raw result of executing a single probe command."""

    ok: bool
    returncode: int | None
    stdout: str = ""
    stderr: str = ""


@dataclass
class ToolCapability:
    """Discovered capability record for one product foundation."""

    tool: str
    present: bool
    executable: str | None
    provenance: str  # "canonical" | "noncanonical" | "missing"
    runnable: bool
    version: str | None
    capabilities: list[str] = field(default_factory=list)
    status: str = "product_missing"  # ok|product_missing|product_broken|noncanonical
    detail: str = ""
    checked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the capability record to a plain JSON-able dict."""
        return {
            "tool": self.tool,
            "present": self.present,
            "executable": self.executable,
            "provenance": self.provenance,
            "runnable": self.runnable,
            "version": self.version,
            "capabilities": list(self.capabilities),
            "status": self.status,
            "detail": self.detail,
            "checked_at": self.checked_at,
        }


def _default_runner(timeout: float) -> Runner:
    """Build a :data:`Runner` that executes real subprocesses with ``timeout`` seconds."""

    def run(cmd: Sequence[str]) -> ProbeResult:
        """Run ``cmd``, mapping not-found/timeout/OS errors to a failed :class:`ProbeResult`."""
        try:
            completed = subprocess.run(
                list(cmd),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return ProbeResult(ok=False, returncode=None, stderr="not found")
        except subprocess.TimeoutExpired:
            return ProbeResult(ok=False, returncode=None, stderr="timeout")
        except OSError as exc:  # pragma: no cover - defensive
            return ProbeResult(ok=False, returncode=None, stderr=str(exc))
        return ProbeResult(
            ok=completed.returncode == 0,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    return run


def _ghost_bin_roots() -> tuple[Path, ...]:
    """Return known non-canonical install roots to probe as a fallback discovery surface."""
    return (Path.home() / ".cargo" / "bin", Path("/usr/local/bin"))


def _resolve_executable(name: str) -> str | None:
    """Resolve a product foundation without trusting only the process PATH.

    MCP/server processes are often launched with a minimal environment that has
    not sourced the operator shell profile yet. The runtime contract still says
    launchers live in ``~/.local/bin`` (or ``VIBECRAFTED_LAUNCHER_BIN``), so
    probe that canonical location first and fall back to ``$PATH`` only as a
    discovery surface. This never creates or mutates the product binary.
    """
    canonical = vibecrafted_launcher_bin() / name
    if canonical.exists() or canonical.is_symlink():
        return str(canonical)
    path_executable = shutil.which(name)
    if path_executable:
        return path_executable
    # If an operator shell can run a product from a known ghost root but the
    # MCP/server process did not inherit that PATH, report the real provenance
    # instead of falsely saying the product is missing. Still read-only: this is
    # observability for cleanup, not ownership of the binary.
    for root in _ghost_bin_roots():
        candidate = root / name
        if candidate.exists() or candidate.is_symlink():
            return str(candidate)
    return None


def _classify_provenance(executable: str | None) -> str:
    """Classify ``executable`` as "missing", "canonical" (launcher bin), or "noncanonical"."""
    if not executable:
        return "missing"
    resolved = str(Path(executable).expanduser())
    canonical = str(vibecrafted_launcher_bin())
    if resolved.startswith(canonical):
        return "canonical"
    for ghost in _GHOST_BIN_ROOTS:
        if ghost in resolved:
            return "noncanonical"
    # Anywhere else on PATH (e.g. brew, pyenv shims) is acceptable but not the
    # canonical launcher root — report it honestly rather than silently OK.
    return "noncanonical"


# Match a semver-ish token in a ``--version`` line, e.g. "loct 0.11.2".
_VERSION_RE = re.compile(r"\b\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.]+)?\b")


def _extract_version(text: str) -> str | None:
    """Pull the first line containing a semver-ish token from ``--version`` output."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _VERSION_RE.search(line)
        if match:
            return line
    stripped = text.strip()
    return stripped or None


# Parse a clap/argparse-style help block, collecting subcommand tokens listed
# under a ``Commands:``/``Subcommands:`` heading.
_HELP_SECTION_RE = re.compile(r"^(commands|subcommands)\s*:?\s*$", re.IGNORECASE)
_HELP_ITEM_RE = re.compile(r"^([a-z][a-z0-9][a-z0-9_-]*)\b")


def _extract_capabilities(help_text: str) -> list[str]:
    """Parse subcommand names listed under a clap/argparse ``Commands:`` heading in ``--help``."""
    capabilities: list[str] = []
    in_section = False
    seen: set[str] = set()
    for raw in help_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if _HELP_SECTION_RE.match(line.strip()):
            in_section = True
            continue
        # A new non-indented heading ends the commands section.
        if in_section and not (raw.startswith((" ", "\t"))):
            if line.strip().endswith(":"):
                in_section = False
            continue
        if not in_section:
            continue
        item = _HELP_ITEM_RE.match(line.strip())
        if item:
            name = item.group(1)
            if name in {"options", "arguments", "usage", "help"}:
                continue
            if name not in seen:
                seen.add(name)
                capabilities.append(name)
    return capabilities


def probe_tool(
    name: str,
    *,
    version_args: Sequence[str] = ("--version",),
    capability_args: Sequence[str] | None = ("--help",),
    timeout: float = 5.0,
    runner: Runner | None = None,
) -> ToolCapability:
    """Probe one product foundation through real execution.

    Returns a :class:`ToolCapability` whose ``status`` distinguishes a missing
    product (binary absent from ``$PATH``) from a broken runtime (binary
    present but not executable) and from a non-canonical install location.
    """
    checked_at = utc_now_iso()
    executable = _resolve_executable(name)
    provenance = _classify_provenance(executable)

    if executable is None:
        return ToolCapability(
            tool=name,
            present=False,
            executable=None,
            provenance="missing",
            runnable=False,
            version=None,
            capabilities=[],
            status="product_missing",
            detail=(
                f"{name} not found in {vibecrafted_launcher_bin()} or on $PATH "
                "(external product dependency)"
            ),
            checked_at=checked_at,
        )

    run = runner or _default_runner(timeout)
    version_probe = run([executable, *version_args])
    if not version_probe.ok:
        reason = (
            version_probe.stderr or version_probe.stdout or "non-zero exit"
        ).strip()
        return ToolCapability(
            tool=name,
            present=True,
            executable=executable,
            provenance=provenance,
            runnable=False,
            version=None,
            capabilities=[],
            status="product_broken",
            detail=f"{name} present at {executable} but failed to execute: {reason}",
            checked_at=checked_at,
        )

    version = _extract_version(version_probe.stdout or version_probe.stderr)
    capabilities: list[str] = []
    if capability_args is not None:
        help_probe = run([executable, *capability_args])
        help_text = help_probe.stdout or help_probe.stderr
        if help_text:
            capabilities = _extract_capabilities(help_text)

    status = "noncanonical" if provenance == "noncanonical" else "ok"
    detail = (
        f"{name} runnable from non-canonical location {executable}; "
        "expected under ~/.local/bin"
        if status == "noncanonical"
        else f"{name} runnable at {executable}"
    )
    return ToolCapability(
        tool=name,
        present=True,
        executable=executable,
        provenance=provenance,
        runnable=True,
        version=version,
        capabilities=capabilities,
        status=status,
        detail=detail,
        checked_at=checked_at,
    )


def foundation_capabilities(
    tools: Sequence[str] = DEFAULT_FOUNDATIONS,
    *,
    timeout: float = 5.0,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Discover live capabilities for the product foundations.

    The payload is a stable, machine-diffable schema so an MCP client can tell,
    after a foundation upgrade, exactly which products are runnable and what
    subcommands they expose — without the runtime ever owning those binaries.
    """
    records = [
        probe_tool(name, timeout=timeout, runner=runner).to_dict() for name in tools
    ]
    summary = {
        "ok": sum(1 for r in records if r["status"] == "ok"),
        "noncanonical": sum(1 for r in records if r["status"] == "noncanonical"),
        "product_missing": sum(1 for r in records if r["status"] == "product_missing"),
        "product_broken": sum(1 for r in records if r["status"] == "product_broken"),
    }
    healthy = summary["product_broken"] == 0
    return {
        "schema": "vibecrafted.capabilities.v1",
        "generated_at": utc_now_iso(),
        "canonical_bin": str(vibecrafted_launcher_bin()),
        "healthy": healthy,
        "summary": summary,
        "tools": records,
    }
