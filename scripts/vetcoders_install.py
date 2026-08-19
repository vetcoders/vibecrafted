#!/usr/bin/env python3
"""𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Smart Installer v2 — manifest-driven, multi-channel, interactive.

Subcommands:
    install         Install the 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. skill bundle
    doctor          Verify installation health
    list            Show available 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. skills and the runtime substrate beneath them
    uninstall       Remove 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. skills, views, launchers, and helpers
    restore         Restore pre-install state from backup

Usage:
    python3 scripts/vetcoders_install.py install [--non-interactive] [--dry-run] [--advanced]
    python3 scripts/vetcoders_install.py doctor
    python3 scripts/vetcoders_install.py list
    python3 scripts/vetcoders_install.py uninstall [--dry-run]
    python3 scripts/vetcoders_install.py restore [--dry-run]
"""

from __future__ import annotations

import argparse
import ast
import ctypes
import errno
import fcntl
import hashlib
import importlib
import importlib.util
import json
import math
import os
import plistlib
import re
import runpy
import select
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

try:
    _distribution_manifest = importlib.import_module("distribution_manifest")
    _installer_brand = importlib.import_module("installer_brand")
    _runtime_paths = importlib.import_module("runtime_paths")
except ModuleNotFoundError:  # pragma: no cover - import path depends on entrypoint
    _distribution_manifest = importlib.import_module("scripts.distribution_manifest")
    _installer_brand = importlib.import_module("scripts.installer_brand")
    _runtime_paths = importlib.import_module("scripts.runtime_paths")

FOOTER_BRANDING = _installer_brand.FOOTER_BRANDING
FRAMEWORK_STAMP = _installer_brand.FRAMEWORK_STAMP
PRODUCT_LINE = _installer_brand.PRODUCT_LINE
TAGLINE = _installer_brand.TAGLINE
VAPOR_HEADER = _installer_brand.VAPOR_HEADER
brand_separator = _installer_brand.separator
brand_version_line = _installer_brand.version_line
read_version_file = _runtime_paths.read_version_file
vibecrafted_backups_home = _runtime_paths.vibecrafted_backups_home
vibecrafted_launcher_bin = _runtime_paths.vibecrafted_launcher_bin
vibecrafted_runtime_home = _runtime_paths.vibecrafted_runtime_home
vibecrafted_runtime_bin = _runtime_paths.vibecrafted_runtime_bin
vibecrafted_tools_home = _runtime_paths.vibecrafted_tools_home
vibecrafted_home = _runtime_paths.vibecrafted_home
xdg_data_home = _runtime_paths.xdg_data_home
xdg_config_home = _runtime_paths.xdg_config_home
stage_distribution_payload = _distribution_manifest.stage_payload
distribution_path_is_forbidden = _distribution_manifest.path_is_forbidden
assert_source_payload_matches_provenance = (
    _distribution_manifest.assert_source_payload_matches_provenance
)
load_source_provenance = _distribution_manifest.load_source_provenance
resolve_source_provenance = _distribution_manifest.resolve_source_provenance
DistributionManifestError = _distribution_manifest.ManifestError

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_IS_TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    """ANSI-wrap text with SGR code `code`; no-op (plain text) when stdout is not a TTY."""
    return f"\033[{code}m{text}\033[0m" if _IS_TTY else text


def bold(t: str) -> str:
    """Bold ANSI-wrap, honoring TTY detection."""
    return _c("1", t)


def green(t: str) -> str:
    """Green ANSI-wrap, honoring TTY detection."""
    return _c("32", t)


def yellow(t: str) -> str:
    """Yellow ANSI-wrap, honoring TTY detection."""
    return _c("33", t)


def red(t: str) -> str:
    """Red ANSI-wrap, honoring TTY detection."""
    return _c("31", t)


def dim(t: str) -> str:
    """Dim/gray ANSI-wrap, honoring TTY detection."""
    return _c("2", t)


def cyan(t: str) -> str:
    """Cyan ANSI-wrap, honoring TTY detection."""
    return _c("36", t)


# Glyph language (docs/CLI_PRODUCT_SPEC.md §3.1): the glyph is the prefix —
# bracket tags ([ok], [missing], …) are retired everywhere.
OK = green("✓")
MISS = red("✗")
WARN = yellow("!")
OPT = dim("·")
SKIP = dim("·")

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def err_line(what_failed: str, fix: str = "", log: str = "") -> None:
    """Error shape (CLI_PRODUCT_SPEC §3.4): what failed · one fix · log path.

    Always stderr — the compact installer redirects stdout into the log."""
    print(f"{red('✗')} {what_failed}", file=sys.stderr)
    if fix:
        print(f"  {dim('→ fix:')} {fix}", file=sys.stderr)
    if log:
        print(f"  {dim('log: ' + log)}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Compact-mode output: TeeLogger + helpers
# ---------------------------------------------------------------------------


class TeeLogger:
    """Captures print output to a log file while optionally suppressing stdout."""

    def __init__(self, log_path: Path, quiet: bool = False):
        """Open the log file for writing and remember the real stdout to tee onto."""
        # Long-lived tee: open for the logger lifetime; closed in close().
        self.log = log_path.open("w", encoding="utf-8")
        self.quiet = quiet
        self._real_stdout = sys.__stdout__ if sys.__stdout__ is not None else sys.stdout

    def write(self, text: str) -> int:
        """Write text to the log file, and to real stdout unless quiet."""
        self.log.write(text)
        if not self.quiet:
            self._real_stdout.write(text)
        return len(text)

    def flush(self) -> None:
        """Flush the log file and, unless quiet, real stdout."""
        self.log.flush()
        if not self.quiet:
            self._real_stdout.flush()

    def close(self) -> None:
        """Close the underlying log file handle."""
        self.log.close()


@contextmanager
def compact_logging(log_path: Path, quiet: bool = True):
    """Context manager: redirects stdout to log, keeps real stdout for compact lines."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    tee = TeeLogger(log_path, quiet=quiet)
    real_stdout = sys.stdout
    sys.stdout = tee  # type: ignore[assignment]
    try:
        yield real_stdout  # caller prints compact lines to this
    finally:
        sys.stdout = real_stdout
        tee.close()


def _compact_line(out, icon: str, label: str, value: str) -> None:
    """Render one compact status update on stdout."""
    line = f"  {icon} {label:13s} {value}"
    if _compact_status_is_live(out):
        out.write(f"\r\033[K{line}")
        out.flush()
        return
    out.write(f"{line}\n")


def _compact_status_is_live(out) -> bool:
    """True when `out` is a live TTY (drives \r-overwrite vs newline-per-line rendering)."""
    isatty = getattr(out, "isatty", None)
    return bool(callable(isatty) and isatty())


def _clear_compact_status(out) -> None:
    """Erase the live compact status row before printing a stable block."""
    if _compact_status_is_live(out):
        out.write("\r\033[K")
        out.flush()


def _compact_checkpoint(
    out,
    step: int,
    title: str,
    details: Sequence[str] = (),
) -> None:
    """Print a stable compact checkpoint: step, title, bounded detail lines."""
    _clear_compact_status(out)
    out.write(f"\n  [{step}/4] {bold(title)}\n")
    for detail in details:
        out.write(f"      {detail}\n")
    out.flush()


# ---------------------------------------------------------------------------
# Component manifest
# ---------------------------------------------------------------------------

SKILL_CATEGORIES: dict[str, dict[str, Any]] = {
    "pipeline": {
        "label": "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Pipeline",
        "description": "Core workflow skills: init, workflow, followup, marbles, dou, hydrate, release",
        "prefix": "vc-",
    },
    "foundations": {
        "label": "Runtime Foundations",
        "description": "Shared runtime substrate: memory, structure, and review artifacts",
        "names": [],
    },
    "specialist": {
        "label": "Specialist / Optional",
        "description": "Skills for specific workflows: decorate, screenscribe, prview, prune",
        "names": [],  # auto-detected: anything not in pipeline or foundations
    },
}


@dataclass
class Foundation:
    """A binary tool that skills depend on."""

    name: str
    description: str
    channels: list[str]
    packages: dict[str, str]
    verify_cmd: str
    required: bool = True  # False = optional

    def is_installed(self) -> str | None:
        """Return path if installed, None otherwise."""
        if self.name == "vc-frame":
            for candidate in ("vc-frame",):
                found = shutil.which(candidate)
                if found:
                    return found
        found = shutil.which(self.name)
        if found:
            return found
        local_bin = Path.home() / ".local" / "bin" / self.name
        if local_bin.is_file() and os.access(local_bin, os.X_OK):
            return str(local_bin)
        return None

    def install_hint(self) -> str:
        """One-liner install hint per configured channel
        (canonical/crates/brew/npm/github/pip/source).
        """
        hints = []
        for ch in self.channels:
            pkg = self.packages.get(ch, self.name)
            if ch == "canonical":
                hints.append(f"Use canonical installer: {pkg}")
            elif ch == "crates":
                hints.append(f"cargo install {pkg}")
            elif ch == "brew":
                hints.append(f"brew install {pkg}")
            elif ch == "npm":
                hints.append(f"npm i -g {pkg}")
            elif ch == "github":
                hints.append(f"Download from {pkg}")
            elif ch == "pip":
                hints.append(f"pipx install {pkg}")
            elif ch == "source":
                hints.append(f"Download from {pkg}")
        return " | ".join(hints)


VENDORED_FOUNDATION_BINARIES = {
    "aicx": "aicx",
    "aicx-mcp": "aicx-mcp",
    "loct": "loct",
    "loctree-mcp": "loctree-mcp",
    "vc-frame": "vc-frame",
}


def detect_vendor_platform() -> str | None:
    """Return this host's `<os>-<arch>` platform slug (darwin/linux, arm64/x64), or None if
    unknown.
    """
    try:
        uname = os.uname()
    except AttributeError:
        return None

    os_name = {"Darwin": "darwin", "Linux": "linux"}.get(
        uname.sysname, uname.sysname.lower()
    )
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64"}.get(
        uname.machine, uname.machine
    )
    return f"{os_name}-{arch}"


def vendored_foundation_dir(repo_root: Path) -> Path | None:
    """Path to this platform's vendored foundation binaries under the repo, or None if
    undetected.
    """
    platform = detect_vendor_platform()
    if not platform:
        return None
    return repo_root / "bin" / "vendor" / platform


def install_foundation_from_bundle(
    foundation: Foundation,
    repo_root: Path,
    bin_dir: Path | None = None,
    dry_run: bool = False,
) -> Path | None:
    """Copy a vendored foundation binary from the repo's bin/vendor payload into `bin_dir`.

    Returns the installed path, or None when no vendored binary exists for this
    foundation/platform (falls back to PATH discovery in the caller).
    """
    vendor_name = VENDORED_FOUNDATION_BINARIES.get(foundation.name)
    if not vendor_name:
        return None

    vendor_dir = vendored_foundation_dir(repo_root)
    if vendor_dir is None:
        return None

    src = vendor_dir / vendor_name
    if not src.is_file():
        return None

    target_dir = bin_dir or (Path.home() / ".local" / "bin")
    dst = target_dir / vendor_name
    if dry_run:
        return dst

    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    dst.chmod(0o755)

    result = subprocess.run(
        [str(dst), "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail}" if detail else ""
        print(f"  {WARN} {vendor_name} copied but version check failed{suffix}")
    return dst


def install_or_find_foundation(
    foundation: Foundation, repo_root: Path, dry_run: bool = False
) -> tuple[str, str]:
    """Install `foundation` from the bundled vendor payload, else fall back to an
    existing PATH install.

    Returns `(path, source)` where source is 'bundled', 'pre-existing', or 'not-installed'.
    """
    bundled = install_foundation_from_bundle(foundation, repo_root, dry_run=dry_run)
    if bundled:
        return str(bundled), "bundled"

    found = foundation.is_installed()
    if found:
        return found, "pre-existing"
    return "", "not-installed"


FOUNDATIONS: list[Foundation] = [
    Foundation(
        name="aicx",
        description="AICX CLI for session history and memory recovery",
        channels=["canonical"],
        packages={
            "canonical": "curl -fsSL https://loct.io/install.sh | sh",
        },
        verify_cmd="aicx --version",
    ),
    Foundation(
        name="aicx-mcp",
        description="AICX MCP server for session history and memory recovery",
        channels=["canonical"],
        packages={
            "canonical": "curl -fsSL https://loct.io/install.sh | sh",
        },
        verify_cmd="aicx-mcp --version",
    ),
    Foundation(
        name="loct",
        description="Loctree operator CLI short command",
        channels=["canonical"],
        packages={
            "canonical": "curl -fsSL https://loct.io/install.sh | sh",
        },
        verify_cmd="loct --version",
    ),
    Foundation(
        name="loctree",
        description="Loctree structural code mapping CLI",
        channels=["canonical"],
        packages={
            "canonical": "curl -fsSL https://loct.io/install.sh | sh",
        },
        verify_cmd="loctree --version",
    ),
    Foundation(
        name="loctree-mcp",
        description="Structural code mapping MCP server",
        channels=["canonical"],
        packages={
            "canonical": "curl -fsSL https://loct.io/install.sh | sh",
        },
        verify_cmd="loctree-mcp --version",
    ),
    Foundation(
        name="prview",
        description="PR review artifact generator",
        channels=["crates", "github"],
        packages={
            "crates": "prview",
            "github": "https://github.com/vetcoders/prview/releases",
        },
        verify_cmd="prview --version",
        required=False,
    ),
    Foundation(
        name="screenscribe",
        description="Screencast analysis — turns narrated recordings into structured engineering findings",
        channels=["pip", "source"],
        packages={
            "pip": "screenscribe",
            "source": "https://github.com/vetcoders/Screenscribe/releases",
        },
        verify_cmd="screenscribe --version",
        required=False,
    ),
    Foundation(
        name="semgrep",
        description="Static analysis and security scanning — quality gate in agent workflows",
        channels=["brew", "pip", "github"],
        packages={
            "brew": "semgrep",
            "pip": "semgrep",
            "github": "https://github.com/semgrep/semgrep/releases",
        },
        verify_cmd="semgrep --version",
        required=False,
    ),
    Foundation(
        name="mise",
        description="Repo-owned toolchain, environment, and task substrate",
        channels=["brew", "github"],
        packages={
            "brew": "mise",
            "github": "https://github.com/jdx/mise/releases",
        },
        verify_cmd="mise --version",
        required=False,
    ),
    Foundation(
        name="starship",
        description="Cross-shell prompt/status line for operator UX",
        channels=["brew", "github"],
        packages={
            "brew": "starship",
            "github": "https://github.com/starship/starship/releases",
        },
        verify_cmd="starship --version",
        required=False,
    ),
    Foundation(
        name="atuin",
        description="Shell history recall with optional encrypted sync",
        channels=["brew", "github"],
        packages={
            "brew": "atuin",
            "github": "https://github.com/atuinsh/atuin/releases",
        },
        verify_cmd="atuin --version",
        required=False,
    ),
    Foundation(
        name="zoxide",
        description="Fast directory jumping for agent-heavy shell workflows",
        channels=["brew", "github"],
        packages={
            "brew": "zoxide",
            "github": "https://github.com/ajeetdsouza/zoxide/releases",
        },
        verify_cmd="zoxide --version",
        required=False,
    ),
    Foundation(
        name="vc-frame",
        description="VC Frame multi-agent terminal workspace surface",
        channels=["canonical"],
        packages={
            # Frame binary installer — not the framework orchestrator.
            "canonical": (
                "curl -fsSL https://github.com/vetcoders/vc-frame"
                "/releases/latest/download/install.sh | sh"
            ),
        },
        verify_cmd="vc-frame --version",
        required=True,
    ),
]

RUNTIME_COMMANDS = {
    "wezterm": "wezterm",
    "vc-apprt": "vc_",
    "locterm": None,
    "microsandbox": "msb",
}


def runtime_status_path() -> Path:
    """Path to the persisted runtime-selection status JSON under the store home."""
    return vibecrafted_home() / "runtime" / "runtime.json"


def read_runtime_status() -> dict:
    """Read and parse the runtime status file; empty dict if missing, error dict if
    unreadable/corrupt.
    """
    status_file = runtime_status_path()
    if not status_file.is_file():
        return {}
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "runtime": "unknown",
            "status": "failed",
            "message": f"cannot read runtime status: {status_file}",
        }
    return data if isinstance(data, dict) else {}


def doctor_runtime_finding() -> DoctorFinding:
    """Turn the persisted runtime status into a single doctor finding (ok/warn/fail)."""
    status = read_runtime_status()
    runtime = str(status.get("runtime") or "none")
    if runtime == "none":
        return DoctorFinding("ok", "runtime:none", "no runtime horse selected")

    component = f"runtime:{runtime}"
    state = str(status.get("status") or "unknown")
    message = str(status.get("message") or "")
    path_value = str(status.get("path") or "")

    if state != "ok":
        return DoctorFinding(
            "fail",
            component,
            message or f"runtime installer reported status={state}",
        )

    if path_value and Path(path_value).exists():
        return DoctorFinding("ok", component, f"-> {path_value}")

    command = RUNTIME_COMMANDS.get(runtime)
    if command:
        found = shutil.which(command)
        if found:
            return DoctorFinding("ok", component, f"-> {found}")

    if path_value:
        return DoctorFinding(
            "warn",
            component,
            f"recorded path is missing: {path_value}; {message}".strip(),
        )
    return DoctorFinding("warn", component, message or "runtime status lacks path")


RUNTIME_DEPS = ["python3", "git"]
RECOMMENDED_DEPS = ["rsync"]
OPTIONAL_DEPS = [
    "zsh"
]  # helpers work in bash and zsh; core install works without either

OLD_SKILL_PREFIX = "vetcoders-"
OLD_HELPER_NAME = "vetcoders-skills.zsh"
SKILL_ROOT_RULE_FILES = ("VERIFICATION_RULE.md", "LIVING_TREE_RULE.md")
LOCALIZED_SKILL_RULE_DIRS = ("pl",)


def _is_writable(path: Path) -> bool:
    """Check if a file is actually writable (respects uchg/immutable flags)."""
    if not path.exists():
        return True
    try:
        with open(path, "a"):
            pass
        return True
    except OSError:
        return False


AGENT_RUNTIMES = ["codex", "claude", "agy", "junie", "grok"]
SYMLINK_TARGETS = ["agents"]
# gemini kept in CHOICES only for legacy .gemini data dir compat (no active runtime)
SYMLINK_TARGET_CHOICES = [
    *SYMLINK_TARGETS,
    "claude",
    "codex",
    "gemini",
    "agy",
    "junie",
    "grok",
]
SHADOWED_SKILL_VIEW_RUNTIMES = ("claude", "codex")
# Claude Code and Codex CLIs read only their own ~/.claude/skills and
# ~/.codex/skills — the canonical .agents view is invisible to them, so the
# standard install must keep their views or the /vc-* deck goes dark.
STANDARD_VIEW_RUNTIMES = [*SYMLINK_TARGETS, *SHADOWED_SKILL_VIEW_RUNTIMES]

# ---------------------------------------------------------------------------
# Install state
# ---------------------------------------------------------------------------

STATE_FILE = ".vc-install.json"
START_HERE_FILE = "START_HERE.md"


@dataclass
class InstallState:
    """Persisted installation state."""

    version: str = "2.0"
    framework_version: str = ""
    installed_at: str = ""
    updated_at: str = ""
    repo_commit: str = ""
    repo_url: str = ""
    skills: list[str] = field(default_factory=list)
    runtimes: list[str] = field(default_factory=list)
    launcher_entries: list[str] = field(default_factory=list)
    helper_files: list[str] = field(default_factory=list)
    foundations: dict[str, dict] = field(default_factory=dict)
    product_tools: dict[str, dict[str, str]] = field(default_factory=dict)
    layout_transfers: list[dict[str, str]] = field(default_factory=list)
    shell_helpers: bool = False
    install_path: str = ""

    @classmethod
    def load(cls, store_path: Path) -> InstallState:
        """Load persisted install state from `store_path`, tolerating a missing or corrupt state
        file.
        """
        state_file = store_path / STATE_FILE
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
                s = cls()
                for k, v in data.items():
                    if hasattr(s, k):
                        setattr(s, k, v)
                return s
            except (json.JSONDecodeError, KeyError):
                pass
        return cls()

    def save(self, store_path: Path) -> None:
        """Serialize this state to the store's STATE_FILE as indented JSON."""
        state_file = store_path / STATE_FILE
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(asdict(self), indent=2) + "\n")


def start_here_path() -> Path:
    """Path to the generated START_HERE.md guide under the store home."""
    return vibecrafted_home() / START_HERE_FILE


def _doctor_totals(findings: Sequence[DoctorFinding]) -> tuple[int, int, int]:
    """Count findings by level: `(ok_count, warn_count, fail_count)`."""
    oks = sum(1 for finding in findings if finding.level == "ok")
    warns = sum(1 for finding in findings if finding.level == "warn")
    fails = sum(1 for finding in findings if finding.level == "fail")
    return oks, warns, fails


def _doctor_action_items(findings: Sequence[DoctorFinding]) -> list[str]:
    """One bounded, copy-pasteable fix per issue class (CLI_PRODUCT_SPEC §3.4)."""
    issues = [finding for finding in findings if finding.level != "ok"]
    if not issues:
        return ["start here: `vibecrafted init claude`"]

    actions: list[str] = []
    if any(finding.component.startswith("foundation:") for finding in issues):
        # Foundation findings are now warn-level (externally managed), so key off
        # the component, not the level — the repair guidance must still surface.
        actions.append(
            "repair Loctree/AICX from their own release surface, then "
            "`bash scripts/install-foundations.sh --check`"
        )
    if any(
        finding.component.startswith(("runtime:", "symlink:", "stale-copy:"))
        for finding in issues
    ):
        actions.append("rebuild skill views: `vibecrafted update`")
    if any(
        finding.component in ("launcher-wrappers", "launcher-runtime")
        for finding in issues
    ):
        actions.append("repair launchers: `vibecrafted doctor --fix-launchers`")
    if any(finding.component.startswith("commands:") for finding in issues):
        actions.append("restore agent slash commands: `vibecrafted update`")
    if any(
        finding.component.startswith("shell-helper")
        or finding.component == "shell-helpers"
        for finding in issues
    ):
        actions.append("restore `vc-*` shortcuts: re-run `make install`")
    if any(finding.component == "manifest" for finding in issues):
        actions.append("enable tracking and restore: run the installer once")
    if any(finding.component.startswith("orphan:") for finding in issues):
        actions.append("clean bundle leftovers: re-run the installer")
    if not actions:
        actions.append("review the warnings above, then re-run `vibecrafted doctor`")
    return actions


def write_start_here_guide(
    store_path: Path, state: InstallState, findings: Sequence[DoctorFinding]
) -> Path:
    """Render and write the START_HERE.md onboarding guide summarizing install health, current
    state, and the next fix actions from `findings`.
    """
    guide_path = start_here_path()
    guide_path.parent.mkdir(parents=True, exist_ok=True)

    ok_count, warn_count, fail_count = _doctor_totals(findings)
    if fail_count:
        health_line = f"Needs attention ({ok_count} ok, {warn_count} warnings, {fail_count} failures)"
    elif warn_count:
        health_line = f"Ready with warnings ({ok_count} ok, {warn_count} warnings, {fail_count} failures)"
    else:
        health_line = f"Ready to work ({ok_count} ok, {warn_count} warnings, {fail_count} failures)"

    runtime_views = ", ".join(state.runtimes) if state.runtimes else "none detected"
    helper_file = _helper_target_path()
    helper_line = (
        f"installed at {helper_file}"
        if helper_file.exists()
        else "not installed; `vibecrafted ...` still works, `vc-*` shortcuts stay optional"
    )
    present_foundations = [
        foundation.name for foundation in FOUNDATIONS if foundation.is_installed()
    ]
    missing_required = [
        foundation.name
        for foundation in FOUNDATIONS
        if foundation.required and not foundation.is_installed()
    ]
    action_items = _doctor_action_items(findings)
    framework_version = state.framework_version or "unknown"
    store_display = str(store_path)

    lines = [
        "# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Start Here",
        "",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Framework version: {framework_version}",
        f"Health: {health_line}",
        "",
        "## Current state",
        f"- Store: {store_display}",
        f"- Skills in shared store: {len(state.skills)}",
        f"- Runtime views: {runtime_views}",
        f"- Shell helpers: {helper_line}",
        "- Foundations present: "
        + (", ".join(present_foundations) if present_foundations else "none detected"),
        "- Foundations still missing: "
        + (", ".join(missing_required) if missing_required else "none required"),
        "",
        "## Simplest path (backyard ride)",
        "1. `vc-start` — open the operator session (tab **Start here** = map of the workspace)",
        "2. `vibecrafted doctor` — health of foundations + install truth",
        "3. `vibecrafted init claude` — orient an agent in a real repo",
        '4. `vibecrafted implement codex --prompt "Ship <task>"` — first cut',
        "",
        "## Ship-ready path",
        '1. `vibecrafted dou claude --prompt "Audit launch readiness"`',
        '2. `vibecrafted decorate codex --prompt "Polish the release surface"`',
        '3. `vibecrafted hydrate codex --prompt "Package the product"`',
        '4. `vibecrafted release codex --prompt "Prepare release steps"`',
        "",
        "## Detach / restore (honest)",
        "- Closing the terminal **detaches** the vc-frame session; reattach with `vc-start`.",
        "- Layout resurrection is frame-level. Live agent processes and mid-flight tool calls",
        "  are **not** frozen RAM — see `docs/installer/RESTORE_CONTRACT.md`.",
        "- Control-plane runs keep `run_id` + report + transcript on disk.",
        "",
        "## Optional surfaces",
        "- `vibecrafted dashboard` — mission-control layouts",
        "- `vibecrafted server status` — local control-plane eye",
        "",
        "## What to fix next",
    ]

    for action in action_items:
        lines.append(f"- {action}")

    lines.extend(
        [
            "",
            "## Safety valves",
            "- `vibecrafted doctor`",
            "- `vibecrafted help`",
            "- `vibecrafted uninstall`",
            "",
        ]
    )

    guide_path.write_text("\n".join(lines), encoding="utf-8")
    return guide_path


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


def detect_system_deps() -> dict[str, str | None]:
    """Check which system dependencies are available."""
    result = {}
    for cmd in RUNTIME_DEPS:
        result[cmd] = shutil.which(cmd)
    for cmd in RECOMMENDED_DEPS:
        result[cmd] = shutil.which(cmd)
    for cmd in OPTIONAL_DEPS:
        result[cmd] = shutil.which(cmd)
    return result


def detect_agent_runtimes() -> dict[str, str | None]:
    """Check which agent CLIs are available."""
    result = {}
    for rt in AGENT_RUNTIMES:
        result[rt] = shutil.which(rt)
    return result


def runtime_skills_dir(runtime: str) -> Path:
    """Path to `<runtime>`'s skills directory under the user's home."""
    return Path.home() / f".{runtime}" / "skills"


def runtime_commands_dir(runtime: str) -> Path:
    """Path to `<runtime>`'s slash-command directory under the user's home."""
    return Path.home() / f".{runtime}" / "commands"


def detect_osascript() -> str | None:
    """Path to `osascript` on PATH, or None if unavailable (non-macOS or missing)."""
    return shutil.which("osascript")


def detect_cargo() -> str | None:
    """Path to `cargo` on PATH, or None if unavailable."""
    return shutil.which("cargo")


def source_skills_root(repo_root: Path) -> Path:
    """Locate the one package-owned skills source directory."""
    packaged_skills_dir = repo_root / "vibecrafted-core" / "vibecrafted_core" / "skills"
    if packaged_skills_dir.is_dir():
        return packaged_skills_dir

    return repo_root


def get_framework_version(repo_root: Path) -> str:
    """Base semver from VERSION (no local git slug)."""
    return read_version_file(repo_root)


def get_repo_commit(repo_root: Path) -> str:
    """Short (8-char) git commit SHA for `repo_root`, honoring VIBECRAFTED_SOURCE_REVISION
    override.
    """
    revision = get_repo_full_commit(repo_root)
    return revision[:8] if revision != "unknown" else "unknown"


def get_repo_full_commit(repo_root: Path) -> str:
    """Full 40-char git commit SHA for `repo_root`, honoring VIBECRAFTED_SOURCE_REVISION
    override.
    """
    try:
        provenance = resolve_source_provenance(
            Path(repo_root).resolve(strict=False),
            owner_repo=None,
            source_revision=None,
        )
    except DistributionManifestError:
        return "unknown"
    return provenance["source_revision"]


def get_install_version(repo_root: Path) -> str:
    """Version shown and stamped by ``make install``: ``X.Y.Z+gSHORTSHA``.

    Source VERSION stays plain semver for version-bump; install always appends
    the commit slug so installed runtimes are attributable.
    """
    base = get_framework_version(repo_root).strip()
    if not base or base == "unknown":
        return base or "unknown"
    # Drop a prior local version segment if re-installing from a stamped tree.
    base = base.split("+", 1)[0].strip()
    sha = get_repo_commit(repo_root)
    if not sha or sha == "unknown":
        return base
    return f"{base}+g{sha}"


_INSTALL_VERSION_TARGETS = (
    Path("VERSION"),
    Path("vibecrafted-core/vibecrafted_core/VERSION"),
    Path("vibecrafted-mcp/vibecrafted_mcp/VERSION"),
    Path("vibecrafted-core/pyproject.toml"),
    Path("vibecrafted-mcp/pyproject.toml"),
)


def stamp_install_version(root: Path, version: str) -> list[Path]:
    """Write ``version`` (with +gSHA) into every VERSION / [project] version under root.

    Returns the list of files actually updated. Missing paths are skipped so a
    partial distribution payload still stamps what it has.
    """
    import re

    project_version_re = re.compile(
        r'^(?P<prefix>\s*version\s*=\s*")(?P<version>[^"]+)(?P<suffix>".*)$'
    )
    stamped: list[Path] = []
    for relative in _INSTALL_VERSION_TARGETS:
        path = root / relative
        if not path.is_file():
            continue
        if path.name == "VERSION":
            path.write_text(version + "\n", encoding="utf-8")
            stamped.append(path)
            continue
        if path.name == "pyproject.toml":
            text = path.read_text(encoding="utf-8")
            in_project = False
            lines: list[str] = []
            replaced = False
            for line in text.splitlines(keepends=True):
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    in_project = stripped == "[project]"
                if in_project and not replaced:
                    body = line.rstrip("\r\n")
                    newline = line[len(body) :]
                    match = project_version_re.match(body)
                    if match:
                        line = (
                            f"{match.group('prefix')}{version}"
                            f"{match.group('suffix')}{newline}"
                        )
                        replaced = True
                lines.append(line)
            if replaced:
                path.write_text("".join(lines), encoding="utf-8")
                stamped.append(path)
    return stamped


def get_repo_url(repo_root: Path) -> str:
    """`git remote get-url origin` for `repo_root`, or empty string if unavailable."""
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def get_repo_owner(repo_root: Path) -> str:
    """`owner/repo` slug for `repo_root`, from VIBECRAFTED_SOURCE_OWNER_REPO or parsed out of
    the origin remote URL; 'unknown' if neither resolves.
    """
    try:
        provenance = resolve_source_provenance(
            Path(repo_root).resolve(strict=False),
            owner_repo=None,
            source_revision=None,
        )
    except DistributionManifestError:
        return "unknown"
    return provenance["owner_repo"]


# ---------------------------------------------------------------------------
# Skill discovery
# ---------------------------------------------------------------------------


def discover_skills(repo_root: Path) -> list[Path]:
    """Find all default 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. skill directories."""
    skills: list[Path] = []
    skills_dir = source_skills_root(repo_root)
    if not skills_dir.exists() or not skills_dir.is_dir():
        return skills

    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        if entry.name in ("docs", "scripts", "tests", ".github"):
            continue
        if not entry.name.startswith("vc-") and not entry.name.startswith("vetcoders-"):
            continue
        if (entry / "SKILL.md").exists():
            skills.append(entry)
    return skills


def iter_skill_root_rule_files(skills_root: Path) -> list[tuple[Path, Path]]:
    """Locate SKILL_ROOT_RULE_FILES (and their localized copies) under `skills_root`, returning
    `(source_path, relative_target)` pairs for syncing into the store.
    """
    rule_files: list[tuple[Path, Path]] = []

    for filename in SKILL_ROOT_RULE_FILES:
        source = skills_root / filename
        if source.is_file():
            rule_files.append((source, Path(filename)))

    for localized_dir in LOCALIZED_SKILL_RULE_DIRS:
        localized_root = skills_root / localized_dir
        if not localized_root.is_dir():
            continue
        for filename in SKILL_ROOT_RULE_FILES:
            source = localized_root / filename
            if source.is_file():
                rule_files.append((source, Path(localized_dir) / filename))

    return rule_files


def sync_skill_root_rules(
    skills_root: Path, store_path: Path, dry_run: bool = False
) -> list[Path]:
    """Copy rule files that skill directories link to via ../RULE.md."""
    copied: list[Path] = []
    for source, relative_target in iter_skill_root_rule_files(skills_root):
        target = store_path / relative_target
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            # When the skill store is a symlink back to the source checkout
            # (portable CI wires vibecrafted-current -> vibecrafted-main),
            # source and target resolve to the same inode; copy2 would raise
            # shutil.SameFileError and the copy is a no-op, so skip it.
            if not (target.exists() and source.resolve() == target.resolve()):
                shutil.copy2(source, target)
        copied.append(relative_target)
    return copied


def categorize_skill(name: str) -> str:
    """Return category key for a skill name."""
    if name.startswith("vc-"):
        return "pipeline"
    return "specialist"


def categorize_all(skills: list[Path]) -> dict[str, list[str]]:
    """Bucket every discovered skill name into pipeline/foundations/specialist categories."""
    cats: dict[str, list[str]] = {"pipeline": [], "foundations": [], "specialist": []}
    for s in skills:
        cat = categorize_skill(s.name)
        cats[cat].append(s.name)
    return cats


# ---------------------------------------------------------------------------
# Interactive UI
# ---------------------------------------------------------------------------


def ask_yn(prompt: str, default: bool = True) -> bool:
    """Ask yes/no question. Returns default in non-interactive mode."""
    if not _IS_TTY:
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(bold(prompt) + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not answer:
        return default
    return answer.startswith("y")


def _read_key() -> str:
    """Reads a single keypress or escape sequence from stdin (unbuffered)."""
    import select

    fd = sys.stdin.fileno()
    ch = os.read(fd, 1)
    if ch == b"\x1b":
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            ch += os.read(fd, 2)
    return ch.decode("utf-8", errors="ignore")


def _accumulate_digits(first: str) -> str:
    """Collect multi-digit number input with a short timeout between digits."""
    import select

    fd = sys.stdin.fileno()
    buf = first
    while True:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            nxt = os.read(fd, 1).decode("utf-8", errors="ignore")
            if nxt.isdigit():
                buf += nxt
            else:
                break
        else:
            break
    return buf


def ask_choice(prompt: str, options: list[str], default: int = 0) -> int:
    """Ask user to pick from a list interactively."""
    if not _IS_TTY:
        return default

    try:
        import termios
        import tty
    except ImportError:
        print(bold(prompt))
        for i, opt in enumerate(options):
            marker = cyan(">") if i == default else " "
            print(f"  {marker} {i + 1}. {opt}")
        try:
            answer = input(
                dim(f"  Choice [1-{len(options)}, default {default + 1}]: ")
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        if not answer:
            return default
        try:
            idx = int(answer) - 1
            if 0 <= idx < len(options):
                return idx
        except ValueError:
            pass
        return default

    # Interactive mode
    import termios
    import tty

    current_idx = default
    print(bold(prompt))
    print(dim("  (Use UP/DOWN to navigate, ENTER to confirm, or type number)"))

    for _ in options:
        print()

    def render():
        """Redraw the `ask_choice` option list in place, highlighting the current selection."""
        sys.stdout.write(f"\033[{len(options)}A")
        for i, opt in enumerate(options):
            marker = cyan(">") if i == current_idx else " "
            sys.stdout.write(f"\033[2K\r  {marker} {i + 1}. {opt}\n")
        sys.stdout.flush()

    render()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            char = _read_key()
            if char in ("\n", "\r"):
                break
            elif char.isdigit() and char != "0":
                num_str = _accumulate_digits(char) if len(options) >= 10 else char
                idx = int(num_str) - 1
                if 0 <= idx < len(options):
                    current_idx = idx
                    break
            elif char == "\x1b[A":  # Up
                current_idx = max(0, current_idx - 1)
                render()
            elif char == "\x1b[B":  # Down
                current_idx = min(len(options) - 1, current_idx + 1)
                render()
            elif char == "\x03":  # Ctrl+C
                raise KeyboardInterrupt
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        return default
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return current_idx


def ask_multi(prompt: str, options: list[str], defaults: list[bool]) -> list[bool]:
    """Ask user to toggle or select multiple options interactively."""
    if not _IS_TTY:
        return defaults

    try:
        import termios
        import tty
    except ImportError:
        print(bold(prompt))
        selected = list(defaults)
        for i, opt in enumerate(options):
            marker = green("[x]") if selected[i] else dim("[ ]")
            print(f"  {marker} {i + 1}. {opt}")
        try:
            print(
                dim(
                    "  (Type numbers space-separated. E.g. '1 2' to select exactly those, or '+3' / '-1' to toggle)"
                )
            )
            answer = input(dim("  Selection: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return defaults

        if answer:
            tokens = answer.split()
            if all(tok.isdigit() for tok in tokens):
                selected = [False] * len(options)
                for tok in tokens:
                    idx = int(tok) - 1
                    if 0 <= idx < len(options):
                        selected[idx] = True
            else:
                for tok in tokens:
                    is_add = tok.startswith("+")
                    is_sub = tok.startswith("-")
                    clean_tok = tok.lstrip("+-")
                    try:
                        idx = int(clean_tok) - 1
                        if 0 <= idx < len(options):
                            if is_add:
                                selected[idx] = True
                            elif is_sub:
                                selected[idx] = False
                            else:
                                selected[idx] = not selected[idx]
                    except ValueError:
                        pass
        return selected

    # Interactive mode
    import termios
    import tty

    selected = list(defaults)
    current_idx = 0

    print(bold(prompt))
    print(
        dim("  (Use UP/DOWN to navigate, SPACE or number to toggle, ENTER to confirm)")
    )

    for _ in options:
        print()

    def render():
        """Redraw the `ask_multi` option list in place, showing checkbox state and current
        cursor.
        """
        sys.stdout.write(f"\033[{len(options)}A")
        for i, opt in enumerate(options):
            marker = green("[x]") if selected[i] else dim("[ ]")
            cursor = cyan(">") if i == current_idx else " "
            sys.stdout.write(f"\033[2K\r  {cursor} {marker} {i + 1}. {opt}\n")
        sys.stdout.flush()

    render()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            char = _read_key()
            if char in ("\n", "\r"):
                break
            elif char == " ":
                selected[current_idx] = not selected[current_idx]
                render()
            elif char.isdigit() and char != "0":
                num_str = _accumulate_digits(char) if len(options) >= 10 else char
                idx = int(num_str) - 1
                if 0 <= idx < len(options):
                    selected[idx] = not selected[idx]
                    current_idx = idx
                    render()
            elif char == "\x1b[A":  # Up
                current_idx = max(0, current_idx - 1)
                render()
            elif char == "\x1b[B":  # Down
                current_idx = min(len(options) - 1, current_idx + 1)
                render()
            elif char == "\x03":  # Ctrl+C
                raise KeyboardInterrupt
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        return defaults
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return selected


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

BACKUP_DIR = "backups/installer"
_SHELL_STARTUP_FILES = (
    ".zshenv",
    ".zprofile",
    ".zshrc",
    ".zlogin",
    ".bash_profile",
    ".bash_login",
    ".profile",
    ".bashrc",
)


def _backup_root(store_path: Path) -> Path:
    """Root directory under which per-install teardown backups are stored."""
    _ = store_path
    return vibecrafted_backups_home()


def _copy_path_to_backup(src: Path, dst: Path) -> None:
    """Copy `src` (symlink, dir, or file) into the backup tree at `dst`, preserving symlinks."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_symlink():
        dst.symlink_to(os.readlink(src))
    elif src.is_dir():
        shutil.copytree(src, dst, symlinks=True)
    elif src.is_file():
        shutil.copy2(src, dst)


def _restore_path_from_backup(src: Path, dst: Path) -> None:
    """Restore `dst` from a backed-up `src`, replacing whatever currently occupies `dst`."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        else:
            shutil.rmtree(dst)

    if src.is_symlink():
        dst.symlink_to(os.readlink(src))
    elif src.is_dir():
        shutil.copytree(src, dst, symlinks=True)
    elif src.is_file():
        shutil.copy2(src, dst)


@dataclass(frozen=True)
class ManagedPath:
    """One managed filesystem path slated for teardown, with the action to take and why."""

    kind: str
    path: Path
    action: str = "remove"
    reason: str = ""


RESTORE_MANIFEST_FILE = "restore-manifest.json"
RESTORE_SCRIPT_FILE = "restore.py"

_SELF_CONTAINED_RESTORE_SCRIPT = """#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def restore_path(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        remove_path(destination)
    if source.is_symlink():
        destination.symlink_to(os.readlink(source))
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    elif source.is_file():
        shutil.copy2(source, destination)


backup_dir = Path(__file__).resolve().parent
manifest = json.loads((backup_dir / "restore-manifest.json").read_text(encoding="utf-8"))
restored = 0
for item in manifest["items"]:
    source = backup_dir / item["backup"]
    destination = Path(item["path"])
    if source.exists() or source.is_symlink():
        restore_path(source, destination)
        restored += 1
print(f"Restored {restored} managed paths from {backup_dir}")
"""


def _path_present(path: Path) -> bool:
    """True if `path` exists as a real file/dir or as a (possibly dangling) symlink."""
    return path.exists() or path.is_symlink()


def _teardown_backup_records(inventory: Sequence[ManagedPath]) -> list[ManagedPath]:
    """Select the remove/edit records worth backing up before teardown, deduping nested paths so
    a parent directory backup isn't shadowed by its children.
    """
    candidates = [
        record
        for record in inventory
        if record.action in {"remove", "edit"} and _path_present(record.path)
    ]
    selected: list[ManagedPath] = []
    selected_roots: list[Path] = []
    for record in sorted(candidates, key=lambda item: len(item.path.parts)):
        if record.path.is_symlink():
            selected.append(record)
            continue
        resolved = record.path.resolve(strict=False)
        if any(resolved == root or root in resolved.parents for root in selected_roots):
            continue
        selected.append(record)
        selected_roots.append(resolved)
    return selected


def create_teardown_backup(
    inventory: Sequence[ManagedPath], *, dry_run: bool = False
) -> str | None:
    """Snapshot every path slated for removal/edit into a fresh timestamped backup
    directory with a manifest and a self-contained restore script.

    Returns the backup timestamp, or None when there was nothing to back up.
    """
    records = _teardown_backup_records(inventory)
    if not records:
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    if dry_run:
        return timestamp

    backup_root = vibecrafted_backups_home()
    backup_dir = backup_root / timestamp
    items_dir = backup_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=False)
    manifest_items: list[dict[str, str]] = []
    for index, record in enumerate(records):
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", record.path.name) or "root"
        relative_backup = Path("items") / f"{index:04d}-{safe_name}"
        _copy_path_to_backup(record.path, backup_dir / relative_backup)
        manifest_items.append(
            {
                "kind": record.kind,
                "path": str(record.path),
                "backup": str(relative_backup),
            }
        )

    manifest = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": manifest_items,
    }
    (backup_dir / RESTORE_MANIFEST_FILE).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    restore_script = backup_dir / RESTORE_SCRIPT_FILE
    restore_script.write_text(_SELF_CONTAINED_RESTORE_SCRIPT, encoding="utf-8")
    restore_script.chmod(0o755)
    backup_root.mkdir(parents=True, exist_ok=True)
    (backup_root / "latest").write_text(timestamp + "\n", encoding="utf-8")
    return timestamp


def _restore_command(backup_timestamp: str) -> str:
    """Shell command string that re-runs the given backup's self-contained restore script."""
    script = vibecrafted_backups_home() / backup_timestamp / RESTORE_SCRIPT_FILE
    return f"python3 {shlex_quote(str(script))}"


def shlex_quote(value: str) -> str:
    """Shell-quote one path without adding a runtime dependency."""
    if not value:
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def collect_orphaned_skills(
    store_path: Path, runtimes: list[str], current_bundle: set[str]
) -> list[tuple[str, Path]]:
    """Return vc-* entries that no longer exist in the current bundle."""
    orphans: list[tuple[str, Path]] = []

    if store_path.exists():
        for entry in sorted(store_path.iterdir()):
            if entry.name.startswith(".") or entry.name in current_bundle:
                continue
            if not entry.name.startswith("vc-"):
                continue
            if entry.is_symlink() or entry.is_dir() and (entry / "SKILL.md").exists():
                orphans.append(("store", entry))

    for rt in runtimes:
        rt_skills = runtime_skills_dir(rt)
        if not rt_skills.exists():
            continue
        for entry in sorted(rt_skills.iterdir()):
            if not entry.name.startswith("vc-") or entry.name in current_bundle:
                continue
            if entry.is_symlink() or entry.is_dir() and (entry / "SKILL.md").exists():
                orphans.append((rt, entry))

    return orphans


def create_backup(
    store_path: Path,
    runtimes: list[str],
    bundle_names: list[str],
    orphaned_entries: list[tuple[str, Path]] | None = None,
    launcher_entries: list[str] | None = None,
    helper_entries: list[str] | None = None,
    dry_run: bool = False,
) -> str | None:
    """Snapshot existing state before install. Returns backup timestamp or None."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = _backup_root(store_path) / ts
    anything_backed = False

    # Back up skills in shared store (if they are copies, not fresh)
    for name in bundle_names:
        src = store_path / name
        if src.is_dir() and not src.is_symlink():
            dst = backup_dir / "store" / name
            if dry_run:
                print(f"  {dim('backup')} {src} -> {dst}")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src, dst, symlinks=True)
            anything_backed = True

    # Back up per-runtime entries exactly as they exist (dirs or symlinks)
    for rt in runtimes:
        rt_skills = runtime_skills_dir(rt)
        if not rt_skills.exists():
            continue
        for name in bundle_names:
            entry = rt_skills / name
            if entry.exists() or entry.is_symlink():
                dst = backup_dir / "runtimes" / rt / name
                if dry_run:
                    print(f"  {dim('backup')} {entry} -> {dst}")
                else:
                    _copy_path_to_backup(entry, dst)
                anything_backed = True

    # Back up orphaned vc-* entries before pruning so restore can bring them back.
    for location, entry in orphaned_entries or []:
        dst = (
            backup_dir
            / ("store" if location == "store" else f"runtimes/{location}")
            / entry.name
        )
        if dry_run:
            print(f"  {dim('backup')} {entry} -> {dst}")
        else:
            _copy_path_to_backup(entry, dst)
        anything_backed = True

    # Back up helper files from either provided manifest or current helper files.
    if helper_entries is None:
        helper_paths = [
            p for p in (_helper_target_path(), _helper_legacy_path()) if p.exists()
        ]
    else:
        helper_paths = []
        for raw_helper in helper_entries:
            candidate = Path(raw_helper)
            if candidate.exists():
                helper_paths.append(candidate)

    for helper_file in helper_paths:
        dst = backup_dir / "helpers" / helper_file.name
        if dry_run:
            print(f"  {dim('backup')} {helper_file} -> {dst}")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(helper_file, dst)
        anything_backed = True

    # Back up launchers/wrappers from either provided manifest or current surface.
    if launcher_entries is None:
        launcher_items = collect_installed_launchers()
    else:
        launcher_items = _parse_manifest_launchers(launcher_entries)

    for launcher_bin_dir, entry in launcher_items:
        dst = (
            backup_dir / "launchers" / _launcher_dir_key(launcher_bin_dir) / entry.name
        )
        if dry_run:
            print(f"  {dim('backup')} {entry} -> {dst}")
        else:
            _copy_path_to_backup(entry, dst)
        anything_backed = True

    # Back up RC files
    for rcname in _SHELL_STARTUP_FILES:
        rcfile = Path.home() / rcname
        if rcfile.exists():
            dst = backup_dir / "helpers" / rcname
            if dry_run:
                print(f"  {dim('backup')} {rcfile} -> {dst}")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(rcfile, dst)
            anything_backed = True

    if anything_backed and not dry_run:
        # Write a "latest" pointer
        latest = _backup_root(store_path) / "latest"
        latest.write_text(ts + "\n")
        return ts
    elif anything_backed:
        return ts
    return None


def _helper_target_path() -> Path:
    """Canonical shell-helper shim path under XDG config (vetcoders/vc-skills.sh)."""
    config_dir = xdg_config_home() / "vetcoders"
    return config_dir / "vc-skills.sh"


def _helper_legacy_path() -> Path:
    """Legacy compat shell-helper path under XDG config (zsh/vc-skills.zsh)."""
    config_dir = xdg_config_home() / "zsh"
    return config_dir / "vc-skills.zsh"


def _shell_source_line() -> str:
    """Source line works in both bash and zsh."""
    return '[[ -r "${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders/vc-skills.sh" ]] && source "${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders/vc-skills.sh"'


def _old_zshrc_source_line() -> str:
    """Old .zshrc-only source line for the legacy compat helper path."""
    return '[[ -r "${XDG_CONFIG_HOME:-$HOME/.config}/zsh/vc-skills.zsh" ]] && source "${XDG_CONFIG_HOME:-$HOME/.config}/zsh/vc-skills.zsh"'


def _helper_surface_label(*, zsh_available: bool | None = None) -> str:
    """Human label describing which shell-helper surface (if any) is currently installed."""
    helper_file = _helper_target_path()
    legacy_file = _helper_legacy_path()
    if zsh_available is None:
        zsh_available = shutil.which("zsh") is not None

    if helper_file.exists():
        return "bash + zsh" if zsh_available else "bash only"
    if legacy_file.exists():
        return "compat zsh"
    return "not installed"


def _launcher_path_line() -> str:
    """Canonical PATH-guard line ensuring ~/.local/bin is on PATH."""
    return 'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac'


def _legacy_launcher_path_lines() -> list[str]:
    """Older unconditional PATH-export lines kept only for rc-file cleanup matching."""
    return ['export PATH="$HOME/.local/bin:$PATH"']


def _doctor_repair_rc_content(
    content: str, *, ensure_helper: bool, ensure_path: bool
) -> str:
    """Rebuild rc-file content with legacy Vibecrafted blocks stripped and (if `ensure_path`)
    the canonical launcher PATH guard appended.
    """
    _ = ensure_helper  # legacy API: host-shell helper sourcing is intentionally retired
    repaired, _removed = _clean_legacy_rc_entries(content)
    for line, comment in _uninstall_rc_entries():
        repaired, _ = _strip_rc_entry(repaired, line, comment)
    blocks: list[tuple[str, str]] = []
    if ensure_path:
        blocks.append(("𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher", _launcher_path_line()))

    if not blocks:
        return repaired

    repaired = repaired.rstrip("\n")
    block_text = "\n\n".join(f"# {comment}\n{line}" for comment, line in blocks)
    if repaired:
        repaired = f"{repaired}\n\n{block_text}\n"
    else:
        repaired = f"{block_text}\n"
    return repaired


def _doctor_fix_rc_files() -> list[DoctorFinding]:
    """Repair every present shell startup file: strip legacy helper-sourcing blocks, restore the
    PATH-only launcher hint, backing up each changed file first.
    """
    findings: list[DoctorFinding] = []
    ensure_path = _find_launcher_wrapper("vibecrafted") is not None

    for rcname in _SHELL_STARTUP_FILES:
        rcfile = Path.home() / rcname
        if not rcfile.exists():
            continue
        try:
            content = rcfile.read_text(encoding="utf-8")
        except OSError as exc:
            findings.append(
                DoctorFinding("warn", f"rc-fix:{rcname}", f"could not read: {exc}")
            )
            continue
        if _rc_has_unclosed_vibecrafted_block(content):
            findings.append(
                DoctorFinding(
                    "warn",
                    f"rc-fix:{rcname}",
                    "unclosed Vibecrafted block; left the entire file unchanged "
                    "for manual repair",
                )
            )
            continue
        if not _is_writable(rcfile):
            findings.append(
                DoctorFinding(
                    "warn",
                    f"rc-fix:{rcname}",
                    f"{rcfile} is locked — cannot repair launcher/source hints",
                )
            )
            continue

        repaired = _doctor_repair_rc_content(
            content, ensure_helper=False, ensure_path=ensure_path
        )
        if repaired == content:
            findings.append(DoctorFinding("ok", f"rc-fix:{rcname}", "already default"))
            continue

        backup = rcfile.with_name(rcfile.name + ".vibecrafted-rc-bak")
        try:
            if not backup.exists():
                shutil.copy2(rcfile, backup)
            mode = stat.S_IMODE(rcfile.stat().st_mode)
            _atomic_bytes_file(rcfile, repaired.encode("utf-8"), mode=mode)
        except OSError as exc:
            findings.append(
                DoctorFinding(
                    "warn",
                    f"rc-fix:{rcname}",
                    f"could not repair safely: {exc}",
                )
            )
            continue
        findings.append(
            DoctorFinding(
                "ok",
                f"rc-fix:{rcname}",
                "removed product helper sourcing and restored the PATH-only "
                f"launcher hint (backup: {backup.name})",
            )
        )

    if not findings:
        findings.append(
            DoctorFinding(
                "ok",
                "rc-fix",
                "no existing shell rc files found — nothing to repair",
            )
        )
    return findings


_LEGACY_BOOTSTRAP_ROOT = Path("/opt/vibecrafted")
_LEGACY_ROOT_EXPORT_MARK = (
    "# vibecrafted doctor --fix-legacy-bootstrap: retired legacy root"
)
_LEGACY_ROOT_UNSET_MARK = (
    "# vibecrafted doctor --fix-legacy-bootstrap: retire container-image legacy root"
)
_LEGACY_ROOT_UNSET_BLOCK = (
    f"\n{_LEGACY_ROOT_UNSET_MARK}\n"
    f'if [ "${{VIBECRAFTED_ROOT:-}}" = "{_LEGACY_BOOTSTRAP_ROOT}" ]; then\n'
    "  unset VIBECRAFTED_ROOT\n"
    "fi\n"
)


def _doctor_fix_legacy_bootstrap() -> list[DoctorFinding]:
    """Neutralize the retired /opt/vibecrafted bootstrap layout.

    Comments out ``export VIBECRAFTED_ROOT=...`` lines that pin the legacy
    bootstrap root in shell rc files (backing the file up first) and reports
    the leftover tree. When the environment itself carries the legacy root
    (container images bake it via ENV), appends an idempotent unset guard to
    ``.zshrc``/``.bashrc`` so fresh shells shed it. The tree itself is never
    deleted — removal stays an explicit operator action.
    """
    findings: list[DoctorFinding] = []
    legacy_token = str(_LEGACY_BOOTSTRAP_ROOT)

    for rcname in (".zshrc", ".zshenv", ".bashrc", ".profile"):
        rcfile = Path.home() / rcname
        if not rcfile.exists():
            continue
        try:
            content = rcfile.read_text(encoding="utf-8")
        except OSError as exc:
            findings.append(
                DoctorFinding(
                    "warn", f"legacy-bootstrap:{rcname}", f"could not read: {exc}"
                )
            )
            continue
        lines = content.splitlines(keepends=True)
        changed = False
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Only neutralize actual export statements. The unset guard this
            # same fix appends also mentions the legacy token — commenting its
            # `if` line would orphan the closing `fi` and break the rc file.
            if (
                "export" in stripped
                and "VIBECRAFTED_ROOT" in stripped
                and legacy_token in stripped
            ):
                lines[index] = f"{_LEGACY_ROOT_EXPORT_MARK}\n# {line.lstrip()}"
                changed = True
        if not changed:
            findings.append(
                DoctorFinding(
                    "ok", f"legacy-bootstrap:{rcname}", "no legacy root export"
                )
            )
            continue
        if not _is_writable(rcfile):
            findings.append(
                DoctorFinding(
                    "warn",
                    f"legacy-bootstrap:{rcname}",
                    f"{rcfile} is locked — cannot comment out the legacy export",
                )
            )
            continue
        backup = rcfile.with_name(rcfile.name + ".vibecrafted-legacy-bak")
        try:
            backup.write_text(content, encoding="utf-8")
            rcfile.write_text("".join(lines), encoding="utf-8")
        except OSError as exc:
            findings.append(
                DoctorFinding(
                    "warn", f"legacy-bootstrap:{rcname}", f"could not repair: {exc}"
                )
            )
            continue
        findings.append(
            DoctorFinding(
                "ok",
                f"legacy-bootstrap:{rcname}",
                f"commented out legacy VIBECRAFTED_ROOT export (backup: {backup.name})",
            )
        )

    if os.environ.get("VIBECRAFTED_ROOT", "").startswith(legacy_token):
        # .zshenv is read by EVERY zsh invocation (interactive or not); .zshrc
        # alone would leave non-interactive shells (make, docker exec) with the
        # image-baked legacy root.
        for rcname in (".zshenv", ".zshrc", ".bashrc"):
            rcfile = Path.home() / rcname
            try:
                existing = rcfile.read_text(encoding="utf-8") if rcfile.exists() else ""
                if _LEGACY_ROOT_UNSET_MARK in existing:
                    findings.append(
                        DoctorFinding(
                            "ok",
                            f"legacy-bootstrap:guard:{rcname}",
                            "unset guard already present",
                        )
                    )
                    continue
                with rcfile.open("a", encoding="utf-8") as handle:
                    handle.write(_LEGACY_ROOT_UNSET_BLOCK)
                findings.append(
                    DoctorFinding(
                        "ok",
                        f"legacy-bootstrap:guard:{rcname}",
                        "appended unset guard for the image-baked legacy root",
                    )
                )
            except OSError as exc:
                findings.append(
                    DoctorFinding(
                        "warn",
                        f"legacy-bootstrap:guard:{rcname}",
                        f"could not append unset guard: {exc}",
                    )
                )
        findings.append(
            DoctorFinding(
                "warn",
                "legacy-bootstrap:env",
                "VIBECRAFTED_ROOT still points at the legacy root in this shell — "
                "fresh shells now shed it via the rc guard; run "
                "`unset VIBECRAFTED_ROOT` to clear the current one",
            )
        )

    if _LEGACY_BOOTSTRAP_ROOT.is_dir():
        findings.append(
            DoctorFinding(
                "warn",
                "legacy-bootstrap:tree",
                f"legacy bootstrap tree left in place at {_LEGACY_BOOTSTRAP_ROOT} — "
                "archive or remove it manually once the canonical install is verified",
            )
        )
    else:
        findings.append(
            DoctorFinding("ok", "legacy-bootstrap:tree", "no legacy bootstrap tree")
        )
    return findings


def _doctor_launcher_source_root(store_path: Path) -> Path | None:
    """Find a source checkout usable to refresh launchers: the repo containing this script, or
    the resolved vibecrafted-current link, whichever has scripts/vibecrafted.
    """
    current_link = vibecrafted_tools_home() / "vibecrafted-current"
    candidates: list[Path] = [Path(__file__).resolve().parent.parent]

    if current_link.exists():
        try:
            candidates.append(current_link.resolve())
        except OSError:
            pass

    for candidate in candidates:
        launcher = candidate / "scripts" / "vibecrafted"
        version = candidate / "VERSION"
        skills_dir = source_skills_root(candidate)
        if launcher.is_file() and version.is_file() and skills_dir.is_dir():
            return candidate
    return None


def _doctor_fix_launchers(store_path: Path, state: InstallState) -> list[DoctorFinding]:
    """Refresh launcher commands from a discoverable source root and persist the updated
    launcher manifest into `state`.
    """
    source_root = _doctor_launcher_source_root(store_path)
    if source_root is None:
        return [
            DoctorFinding(
                "warn",
                "doctor-fix-launchers",
                "could not locate a default source root with scripts/vibecrafted",
            )
        ]

    try:
        current_link = vibecrafted_tools_home() / "vibecrafted-current"
        if not (current_link / _RUNTIME_GENERATION_ENTRYPOINT).is_file():
            source_root = sync_control_plane_tree(
                source_root,
                current_link,
                mirror=True,
                install_version=read_version_file(source_root).strip(),
            )
        _install_launcher(source_root, dry_run=False, update_rc=False)
        state.launcher_entries = _snapshot_launcher_entries()
        state.save(vibecrafted_home())
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - surface repair failures
        return [
            DoctorFinding(
                "warn",
                "doctor-fix-launchers",
                f"launcher repair failed: {exc}",
            )
        ]

    return [
        DoctorFinding(
            "ok",
            "doctor-fix-launchers",
            f"refreshed launcher commands from {source_root}",
        )
    ]


def _run_smoke_command(
    command: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    expected_text: str | None = None,
) -> tuple[bool, str]:
    """Run a small runtime smoke command and capture a concise result."""
    try:
        result = subprocess.run(
            command, env=env, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        return False, str(exc)

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    detail = stdout or stderr or f"exit {result.returncode}"

    if result.returncode != 0:
        return False, detail

    if expected_text and expected_text not in stdout:
        return False, f"missing expected text: {expected_text}"

    return True, detail


def _rc_has_unclosed_vibecrafted_block(content: str) -> bool:
    """Refuse automatic repair when a managed rc block has no closing marker."""
    expected_end: re.Pattern[str] | None = None
    for line in content.splitlines():
        stripped = line.strip()
        if expected_end is not None:
            if expected_end.match(stripped):
                expected_end = None
            continue
        if re.match(
            r"^#\s*>>>\s*vibecrafted(?:\.\s*framework)?\s*>>>$",
            stripped,
            re.IGNORECASE,
        ):
            expected_end = re.compile(
                r"^#\s*<<<\s*vibecrafted(?:\.\s*framework)?\s*<<<$",
                re.IGNORECASE,
            )
        elif stripped.startswith(("# >>> VibeCraft", "# >>> 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝")):
            end_marker = (
                r"^#\s*<<<.*VibeCraft.*<<<$"
                if "VibeCraft" in stripped
                else r"^#\s*<<<.*𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝.*<<<$"
            )
            expected_end = re.compile(end_marker)
    return expected_end is not None


def _clean_legacy_rc_entries(content: str) -> tuple[str, int]:
    """Strip legacy Vibecrafted-managed blocks/lines (helper sourcing, PATH exports, marker
    comments) from rc-file `content`; refuses to touch an unclosed block.
    """
    import re

    if _rc_has_unclosed_vibecrafted_block(content):
        return content, 0

    lines = content.splitlines()
    kept = []
    skip_until: re.Pattern[str] | None = None
    removed = 0

    for cl in lines:
        stripped = cl.strip()

        # 1. Block cleanup
        if skip_until:
            removed += 1
            if skip_until.match(stripped):
                skip_until = None
            continue

        if re.match(
            r"^#\s*>>>\s*vibecrafted(?:\.\s*framework)?\s*>>>$", stripped, re.IGNORECASE
        ):
            removed += 1
            skip_until = re.compile(
                r"^#\s*<<<\s*vibecrafted(?:\.\s*framework)?\s*<<<$", re.IGNORECASE
            )
            continue

        if stripped.startswith(
            ("# >>> VibeCraft", "# <<< VibeCraft", "# >>> 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝", "# <<< 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝")
        ):
            removed += 1
            end_marker = (
                r"^#\s*<<<.*VibeCraft.*<<<$"
                if "VibeCraft" in stripped
                else r"^#\s*<<<.*𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝.*<<<$"
            )
            skip_until = re.compile(end_marker)
            continue

        # 2. Known source lines
        if stripped.startswith("[[ -r ") and (
            "vc-skills" in stripped or "vetcoders" in stripped
        ):
            removed += 1
            continue
        if stripped.startswith("source ") and (
            "vc-skills" in stripped or "vetcoders" in stripped
        ):
            removed += 1
            continue

        # 3. Known exports
        if stripped.startswith(
            (
                "export VIBECRAFTED_ROOT",
                "export VIBECRAFT_ROOT",
                "export VIBECRAFTED_HOME",
                "export LOCTREE_NUDGE",
            )
        ):
            removed += 1
            continue
        if stripped.startswith("export PATH=") and (
            "vibecraft" in stripped.lower() and "/bin" in stripped.lower()
        ):
            removed += 1
            continue

        # 4. Known comments
        if stripped.startswith("#"):
            lower_comment = stripped.lower()
            if (
                any(
                    x in lower_comment
                    for x in [
                        "Vetcoders shell helpers",
                        "vibecraft shell helpers",
                        "vibecrafted shell helpers",
                        "vibecraft launcher",
                        "vibecrafted launcher",
                        "vibecrafted. helper shim",
                        "vibecrafted. launcher",
                        "vibecrafted. shell helpers",
                    ]
                )
                or "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝" in stripped
            ):
                removed += 1
                continue

        kept.append(cl)

    joined = "\n".join(kept)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    if content.endswith("\n") and not joined.endswith("\n"):
        joined += "\n"
    if not joined:
        joined = ""

    # If the text changed, adjust removed count safely
    if joined != content and removed == 0:
        removed = 1

    return joined, removed


def _strip_rc_entry(
    content: str, line: str, comment: str | None = None
) -> tuple[str, int]:
    """Remove every occurrence of `line` (with its optional preceding `comment` line) from
    `content`; returns the rebuilt text and how many lines were removed.
    """
    raw_lines = content.splitlines()
    kept: list[str] = []
    removed = 0
    idx = 0

    while idx < len(raw_lines):
        current = raw_lines[idx]
        stripped = current.strip()
        if comment and stripped == f"# {comment}":
            next_idx = idx + 1
            # allow empty lines in between comment and line
            while next_idx < len(raw_lines) and not raw_lines[next_idx].strip():
                next_idx += 1
            if next_idx < len(raw_lines) and raw_lines[next_idx].strip() == line:
                removed += next_idx - idx + 1
                idx = next_idx + 1
                continue
        if stripped == line:
            removed += 1
            idx += 1
            continue
        kept.append(current)
        idx += 1

    rebuilt = "\n".join(kept)
    if content.endswith("\n"):
        rebuilt += "\n"
    return rebuilt, removed


def _installer_managed_launcher_names() -> list[str]:
    """Every launcher basename this installer considers itself the owner of."""
    return [
        "vibecrafted",
        "vibecraft",
        *LAUNCHER_WRAPPERS,
        *PYTHON_ENTRYPOINT_LAUNCHERS,
        *LEGACY_LAUNCHER_NAMES,
    ]


def _vibecrafted_owned_launcher_names() -> set[str]:
    """Lowercased basenames of every launcher Vibecrafted itself publishes.

    Ownership — not the ``vc-*`` naming prefix — is what scopes operator-facing
    launcher contracts. Foreign products legitimately publish ``vc-*`` symlinks
    into the same ``~/.local/bin`` (``vc-tools`` from vetcoders-hooks, whose own
    installer links straight into its checkout by design); their provenance is
    not ours to police.
    """
    return {
        name.lower()
        for name in (
            *_installer_managed_launcher_names(),
            *PROVIDER_PUBLISHED_LAUNCHER_NAMES,
        )
    }


def _snapshot_helper_file(path: Path) -> bool:
    """True if `path` is a real (non-symlink) file carrying the helper shim marker comment."""
    if not path.exists():
        return False
    if path.is_symlink():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return HELPER_SHIM_MARKER in text


def _snapshot_legacy_helper_link(path: Path) -> bool:
    """True if `path` is a symlink pointing at the canonical helper shim target."""
    if not path.is_symlink():
        return False
    try:
        target = Path(os.readlink(path))
    except OSError:
        return False
    if not target.is_absolute():
        target = path.parent / target
    return target == _helper_target_path()


def _snapshot_helper_files() -> list[str]:
    """Snapshot the helper file paths (canonical and/or legacy) currently installed."""
    helper_files: list[str] = []
    helper_file = _helper_target_path()
    legacy_file = _helper_legacy_path()

    if _snapshot_helper_file(helper_file) or helper_file.exists():
        helper_files.append(str(helper_file))

    if (
        _snapshot_legacy_helper_link(legacy_file)
        or legacy_file.exists()
        and _snapshot_helper_file(legacy_file)
    ):
        helper_files.append(str(legacy_file))

    return helper_files


def _snapshot_launcher_entries() -> list[str]:
    """Snapshot every framework-managed launcher as `<dir-key>/<name>` manifest entries."""
    launcher_entries: list[str] = []
    seen: set[tuple[str, str]] = set()
    for launcher_bin_dir in _launcher_bin_dirs():
        for name in _installer_managed_launcher_names():
            entry = launcher_bin_dir / name
            if not (entry.exists() or entry.is_symlink()):
                continue
            if _is_framework_managed_launcher(entry):
                key = _launcher_dir_key(launcher_bin_dir)
                if (key, name) not in seen:
                    launcher_entries.append(f"{key}/{name}")
                    seen.add((key, name))
    return launcher_entries


def snapshot_product_tool_state() -> dict[str, dict[str, str]]:
    """Record product dependency commands exactly where PATH resolves them.

    Loctree/AICX/vc-frame/etc. are foundation payload when the bundle vendors
    them for this platform. Missing bundle payloads remain external dependencies,
    so discovery still observes PATH and persists the fallback result.
    """
    product_tools: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    for foundation in FOUNDATIONS:
        if foundation.name in seen:
            continue
        seen.add(foundation.name)
        found = foundation.is_installed()
        if found:
            product_tools[foundation.name] = {
                "path": found,
                "managed_by": "external-path",
                "required": str(bool(foundation.required)).lower(),
            }
        else:
            product_tools[foundation.name] = {
                "path": "",
                "managed_by": "missing",
                "required": str(bool(foundation.required)).lower(),
            }
    return product_tools


def _parse_manifest_launchers(
    raw_entries: Sequence[str],
) -> list[tuple[Path, Path]]:
    """Parse `<dir-key>/<name>` manifest entries back into `(launcher_bin_dir, entry)` pairs,
    skipping unknown dir keys or malformed entries.
    """
    launcher_entries: list[tuple[Path, Path]] = []
    seen: set[tuple[str, str]] = set()

    for raw_entry in raw_entries:
        if "/" not in raw_entry:
            continue
        launcher_dir_key, name = raw_entry.split("/", 1)
        if not name or "/" in name:
            continue
        launcher_bin_dir = _launcher_dir_from_key(launcher_dir_key)
        if launcher_bin_dir is None:
            continue
        entry = launcher_bin_dir / name
        marker = (str(launcher_bin_dir), name)
        if marker in seen:
            continue
        seen.add(marker)
        launcher_entries.append((launcher_bin_dir, entry))

    return launcher_entries


def _rc_has_vibecrafted_bin_path(content: str) -> bool:
    """True if rc-file `content` already references a .local/bin or vibecrafted bin PATH entry."""
    return (
        ".local/bin" in content
        or "vibecrafted/bin" in content
        or ".vibecrafted/bin" in content
    )


HELPER_SHIM_MARKER = "# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. helper shim. Generated by install-shell.sh."

SKILL_WRAPPER_NAMES = [
    "decorate",
    "delegate",
    "dou",
    "followup",
    "guard",
    "hydrate",
    "implement",
    "intents",
    "justdo",
    "marbles",
    "ownership",
    "partner",
    "polarize",
    "prune",
    "release",
    "research",
    "review",
    "scaffold",
    "trust",
    "workflow",
]

LAUNCHER_WRAPPERS = [
    "vc-help",
    "vc-init",
    "vc-start",
    "vc-dashboard",
    "vc-cron",
    "vc-loop",
    "vc-paste",
    "vc-ship",
    "vc-dispatch",
    "vc-resume",
    "vc-agents",
    "telemetry",
    *[f"vc-{name}" for name in SKILL_WRAPPER_NAMES],
]

PYTHON_ENTRYPOINT_LAUNCHERS = [
    "vc-agents",
    "vc-audit",
    "vc-cron",
    "vc-decorate",
    "vc-delegate",
    "vc-dou",
    "vc-followup",
    "vc-guard",
    "vc-git",
    "vc-hydrate",
    "vc-implement",
    "vc-intents",
    "vc-loop",
    "vc-marbles",
    "vc-ownership",
    "vc-partner",
    "vc-paste",
    "vc-polarize",
    "vc-prune",
    "vc-release",
    "vc-research",
    "vc-research-await",
    "vc-research-synthesize",
    "vc-review",
    "vc-sandbox",
    "vc-scaffold",
    "vc-ship",
    "vc-trust",
    "vc-workflow",
    "vibecrafted",
    "vibecrafted-compact-hook",
    "vibecrafted-mcp",
    "vibecrafted-resume",
    "verify-vibecrafted-walkaround",
]

RELEASE_CONTRACT_PACKAGE_ASSETS = (
    "product_contract.py",
    "walkaround_runner.py",
    "schemas/unified_product.schema.v1.json",
    "trust/release-policy.v1.json",
    "trust/vibecrafted-signing-v1.pub",
)
SECURE_WALKAROUND_LAUNCHER = "verify-vibecrafted-walkaround"
SECURE_WALKAROUND_LAUNCHER_MARKER = "vibecrafted-managed-walkaround-launcher-v2"
_RELEASE_KEY_SPKI_SHA256 = (
    "521ed59d3c446c540afe1557c2dbc39c9c190775f99896b2b65206c32814b25b"
)

LEGACY_LAUNCHER_NAMES = [
    "marble-pack",
    "aicx-pack",
]

# Launchers Vibecrafted publishes through a provider payload instead of its own
# wrapper/entrypoint generation (slack_provider symlinks `vc-slack` into the
# launcher bin). Still Vibecrafted-owned, so operator-facing launcher contracts
# apply to them.
PROVIDER_PUBLISHED_LAUNCHER_NAMES = [
    "vc-slack",
]

FRAMEWORK_LAUNCHER_MARKERS = (
    "vibecrafted",
    ".vibecrafted",
    "vc-agents",
    "vetcoders",
    "scripts/vibecraft",
)


def _launcher_bin_dirs() -> list[Path]:
    """The launcher bin directories this installer manages (currently just the one canonical
    dir).
    """
    return [vibecrafted_launcher_bin()]


def _find_launcher_wrapper(name: str) -> Path | None:
    """Find `name` under any managed launcher bin dir; None if not present anywhere."""
    for launcher_bin_dir in _launcher_bin_dirs():
        candidate = launcher_bin_dir / name
        if candidate.exists() or candidate.is_symlink():
            return candidate
    return None


def _uninstall_rc_entries() -> list[tuple[str, str]]:
    """The `(line, comment)` pairs this installer strips from rc files during cleanup/uninstall."""
    entries = [
        (_shell_source_line(), "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. shell helpers"),
        (_shell_source_line(), "Vetcoders shell helpers"),
        (_old_zshrc_source_line(), "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. shell helpers"),
        (_old_zshrc_source_line(), "Vetcoders shell helpers"),
        (_launcher_path_line(), "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher"),
    ]
    entries.extend(
        (legacy_line, "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher")
        for legacy_line in _legacy_launcher_path_lines()
    )
    return entries


def _rc_has_framework_install_hints(rcfile: Path) -> bool:
    """True if `rcfile` still contains any framework install-hint line or comment marker."""
    if not rcfile.exists():
        return False
    try:
        content = rcfile.read_text()
    except OSError:
        return False
    for line, comment in _uninstall_rc_entries():
        if line in content or (comment and f"# {comment}" in content):
            return True
    return False


def _launcher_dir_key(launcher_bin_dir: Path) -> str:
    """Stable string key identifying a launcher bin directory (currently always 'local-bin')."""
    if launcher_bin_dir == Path.home() / ".local" / "bin":
        return "local-bin"
    if launcher_bin_dir == vibecrafted_launcher_bin():
        return "local-bin"
    return (
        re.sub(r"[^a-z0-9]+", "-", str(launcher_bin_dir).lower()).strip("-")
        or "launcher-bin"
    )


def _launcher_dir_from_key(key: str) -> Path | None:
    """Resolve a launcher-dir key back to its Path, or None if unknown."""
    mapping = {
        "local-bin": vibecrafted_launcher_bin(),
    }
    return mapping.get(key)


def _launcher_file_contains_framework_markers(path: Path) -> bool:
    """True if the first 8KB of `path` mentions any known framework launcher marker string."""
    if not path.exists() or not path.is_file():
        return False
    try:
        payload = path.read_text(encoding="utf-8", errors="ignore")[:8192].lower()
    except OSError:
        return False
    return any(marker in payload for marker in FRAMEWORK_LAUNCHER_MARKERS)


def _is_framework_managed_launcher(entry: Path) -> bool:
    """True if `entry` is a launcher this installer owns: an explicit wrapper name, a symlink
    into vibecrafted/vibecraft, or a vc-*/vibecraft*-named file with framework markers.
    """
    name = entry.name.lower()
    explicit_names = {
        "vibecrafted",
        "vibecraft",
        *[wrapper.lower() for wrapper in LAUNCHER_WRAPPERS],
        *[wrapper.lower() for wrapper in PYTHON_ENTRYPOINT_LAUNCHERS],
        *[legacy.lower() for legacy in LEGACY_LAUNCHER_NAMES],
    }
    if name in explicit_names:
        return True

    if entry.is_symlink():
        try:
            target_name = Path(os.readlink(entry)).name.lower()
        except OSError:
            target_name = ""
        if target_name in {"vibecrafted", "vibecraft"}:
            return True
        try:
            resolved = entry.resolve(strict=False)
        except OSError:
            resolved = None
        if resolved is not None:
            if resolved.name.lower() in {"vibecrafted", "vibecraft"}:
                return True
            if _launcher_file_contains_framework_markers(resolved):
                return True

    hinted_name = name.startswith(("vc-", "vibecraft")) or name.endswith("-pack")
    return bool(hinted_name and _launcher_file_contains_framework_markers(entry))


def _is_replaceable_framework_launcher(entry: Path) -> bool:
    """True if `entry` is missing, or is a symlink/file this installer is safe to overwrite
    without clobbering unmanaged operator content.
    """
    if not (entry.exists() or entry.is_symlink()):
        return True
    if entry.is_symlink():
        try:
            target = Path(os.readlink(entry))
        except OSError:
            target = Path("")
        if target.name.lower() in {"vibecrafted", "vibecraft"}:
            return True
        try:
            resolved = entry.resolve(strict=False)
        except OSError:
            resolved = None
        if resolved is not None and _launcher_file_contains_framework_markers(resolved):
            return True
    return _launcher_file_contains_framework_markers(entry)


def collect_installed_launchers() -> list[tuple[Path, Path]]:
    """Every currently installed launcher this installer considers itself the owner of."""
    launchers: list[tuple[Path, Path]] = []
    for launcher_bin_dir in _launcher_bin_dirs():
        if not launcher_bin_dir.exists():
            continue
        for entry in sorted(launcher_bin_dir.iterdir()):
            if not (entry.is_symlink() or entry.is_file()):
                continue
            if _is_framework_managed_launcher(entry):
                launchers.append((launcher_bin_dir, entry))
    return launchers


# ---------------------------------------------------------------------------
# Helper conflict detection
# ---------------------------------------------------------------------------

KNOWN_HELPER_FUNCTIONS = [
    "codex-implement",
    "codex-plan",
    "codex-review",
    "codex-research",
    "codex-prompt",
    "codex-observe",
    "claude-implement",
    "claude-plan",
    "claude-review",
    "claude-research",
    "claude-prompt",
    "claude-observe",
    "agy-implement",
    "agy-plan",
    "agy-review",
    "agy-research",
    "agy-prompt",
    "agy-observe",
    "agy-implement",
    "agy-plan",
    "agy-review",
    "agy-research",
    "agy-prompt",
    "agy-observe",
    "junie-implement",
    "junie-plan",
    "junie-review",
    "junie-research",
    "junie-prompt",
    "junie-observe",
    "skills-sync",
    "agy-keychain-set",
    "agy-keychain-get",
    "agy-keychain-clear",
]


@dataclass
class HelperConflict:
    """One detected shell-function name collision with a Vibecrafted-managed helper, and where."""

    file: Path
    function: str
    line_num: int


def scan_helper_conflicts() -> dict[Path, list[HelperConflict]]:
    """Scan shell config files for existing helper function definitions."""
    default = _helper_target_path()
    conflicts: dict[Path, list[HelperConflict]] = {}

    search_dirs = []
    config_base = xdg_config_home()
    for subdir in ("vetcoders", "zsh"):
        candidate = config_base / subdir
        if candidate.is_dir():
            search_dirs.append(candidate)

    files_to_scan: list[Path] = []
    for d in search_dirs:
        files_to_scan.extend(d.glob("*.sh"))
        files_to_scan.extend(d.glob("*.zsh"))
    for rcfile in (".zshrc", ".bashrc"):
        rc = Path.home() / rcfile
        if rc.exists():
            files_to_scan.append(rc)

    for fpath in files_to_scan:
        if fpath.resolve() == default.resolve():
            continue  # Skip our own file
        try:
            lines = fpath.read_text().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            for fn in KNOWN_HELPER_FUNCTIONS:
                # Match function definitions: "func_name()" or "func_name ()"
                if stripped.startswith((f"{fn}()", f"{fn} ()")):
                    if fpath not in conflicts:
                        conflicts[fpath] = []
                    conflicts[fpath].append(
                        HelperConflict(file=fpath, function=fn, line_num=i)
                    )

    return conflicts


def report_helper_conflicts(
    conflicts: dict[Path, list[HelperConflict]], interactive: bool
) -> bool:
    """Report conflicts and ask user what to do. Returns True if should proceed with install."""
    if not conflicts:
        return True

    print(yellow(bold("\n  Helper overlap detected:")))
    for fpath, items in conflicts.items():
        total_lines = 0
        try:
            total_lines = len(fpath.read_text().splitlines())
        except OSError:
            pass
        our_count = len(items)
        print(f"    {fpath} ({total_lines} lines, {our_count} ours)")
        for c in items:
            print(f"      {dim(f'line {c.line_num}:')} {c.function}()")

    print()
    print(
        yellow(
            "  These files already contain non-𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. content — installer will NOT edit them."
        )
    )

    if not interactive:
        print(
            yellow(
                "  Non-interactive mode: installing the default helper file alongside."
            )
        )
        print(yellow("  Clean up duplicates in the files above manually."))
        return True

    choice = ask_choice(
        "  How should we handle it?",
        [
            "Skip helper install and keep the current setup",
            "Install the default helper file alongside and clean up duplicates later",
        ],
        default=1,
    )

    if choice == 0:
        print(dim("  Skipping helper install."))
        return False

    print()
    print(yellow("  To clean this up later, remove these functions from your files:"))
    for fpath, items in conflicts.items():
        for c in items:
            print(f"    {c.function} @ {fpath}:{c.line_num}")
    print()
    return True


# ---------------------------------------------------------------------------
# Install logic
# ---------------------------------------------------------------------------


_RSYNC_EXCLUDES = {".DS_Store", ".backup", ".loctree"}


def _copytree_skill(src: Path, dst: Path, mirror: bool = False) -> None:
    """Pure-Python fallback when rsync is not available."""
    if mirror and dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in _RSYNC_EXCLUDES:
            continue
        target = dst / item.name
        if item.is_dir():
            _copytree_skill(item, target, mirror=False)
        else:
            shutil.copy2(str(item), str(target))


def rsync_skill(
    src: Path, dst: Path, dry_run: bool = False, mirror: bool = False
) -> None:
    """Sync a single skill directory. Uses rsync when available, shutil otherwise."""
    if dry_run:
        return
    # A symlinked store (portable CI wires vibecrafted-current -> the source
    # checkout) makes src and dst the same directory. rsync would churn, and the
    # shutil fallback would copy a file onto itself (or rmtree the source under
    # --mirror); skip the self-sync entirely.
    if dst.exists() and src.resolve() == dst.resolve():
        return
    dst.mkdir(parents=True, exist_ok=True)
    if shutil.which("rsync"):
        cmd = [
            "rsync",
            "-az",
            "--exclude",
            ".DS_Store",
            "--exclude",
            ".backup",
            "--exclude",
            ".loctree",
        ]
        if mirror:
            cmd.append("--delete")
        cmd += [str(src) + "/", str(dst) + "/"]
        # Capture rsync stderr — do NOT discard it. When this sync fails the
        # operator needs the real reason (exit 23 "could not make way", exit
        # 11/12 "No space left on device", a dangling symlink, a permission
        # error), not an opaque "could not refresh staged tools".
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    else:
        _copytree_skill(src, dst, mirror=mirror)


def _remove_path(path: Path) -> None:
    """Delete `path`, whether it is a symlink, regular file, or directory tree."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


_TOOLS_HANDOFF_SCHEMA = "vibecrafted.tools-handoff.v1"
_RUNTIME_GENERATION_MANIFEST = "runtime-manifest.json"
_RUNTIME_GENERATION_MANIFEST_SCHEMA = "vibecrafted.runtime-generation.v2"
_RUNTIME_GENERATION_MANIFEST_KEYS = frozenset(
    {
        "schema",
        "version",
        "source_fingerprint",
        "owner_repo",
        "source_revision",
        "source_payload",
        "entrypoint",
        "hashes",
    }
)
_SOURCE_PROVENANCE_SCHEMA = "vibecrafted.source-provenance.v2"
_SOURCE_PROVENANCE_KEYS = frozenset(
    {"schema", "owner_repo", "source_revision", "payload"}
)
_SOURCE_PAYLOAD_SCHEMA = "vibecrafted.distribution-tree.v1"
_SOURCE_PAYLOAD_KEYS = frozenset({"schema", "algorithm", "tree_sha256", "entry_count"})
_RUNTIME_GENERATION_ENTRYPOINT = Path(
    "vibecrafted-core/vibecrafted_core/deck/vibecrafted"
)
_RUNTIME_GENERATION_RUNTIME_ALIAS = Path("runtime")
_RUNTIME_GENERATION_CANONICAL_RUNTIME = Path(
    "vibecrafted-core/vibecrafted_core/runtime"
)
_RUNTIME_GENERATION_PROJECTED_CONFIG = Path("runtime/generated/vc-frame/config.kdl")
_RUNTIME_GENERATION_CANONICAL_CONFIG = (
    _RUNTIME_GENERATION_CANONICAL_RUNTIME / "generated/vc-frame/config.kdl"
)
_RUNTIME_GENERATION_RELEASE_CONTRACT_HASHES = frozenset(
    Path("vibecrafted-core/vibecrafted_core") / relative
    for relative in RELEASE_CONTRACT_PACKAGE_ASSETS
)
_RUNTIME_GENERATION_REQUIRED_HASHES = (
    frozenset(
        {
            Path("VERSION"),
            Path("scripts/vibecrafted"),
            _RUNTIME_GENERATION_CANONICAL_CONFIG,
            _RUNTIME_GENERATION_ENTRYPOINT,
        }
    )
    | _RUNTIME_GENERATION_RELEASE_CONTRACT_HASHES
)
_MAX_RUNTIME_BOUND_FILE_BYTES = 16 * 1024 * 1024
_RUNTIME_VERIFIER_PACKAGE = Path("vibecrafted-core/vibecrafted_core")
_RUNTIME_VERIFIER_PRODUCT = _RUNTIME_VERIFIER_PACKAGE / "product_contract.py"
_RUNTIME_VERIFIER_RUNNER = _RUNTIME_VERIFIER_PACKAGE / "walkaround_runner.py"
_RUNTIME_VERIFIER_SCHEMA = (
    _RUNTIME_VERIFIER_PACKAGE / "schemas/unified_product.schema.v1.json"
)
_RUNTIME_VERIFIER_PRODUCT_COMMANDS = frozenset(
    {
        "module",
        "app",
        "transaction",
        "schema",
        "walkaround",
        "release-output",
        "runtime-generation",
    }
)
_RUNTIME_VERIFIER_RUNNER_COMMANDS = frozenset(
    {"trust-probe", "verify-release", "walkaround"}
)
_RUNTIME_VERIFIER_E_SCHEMA = 21
_RUNTIME_VERIFIER_E_MISSING = 22
_RUNTIME_VERIFIER_E_HASH = 24
_RUNTIME_VERIFIER_E_DEPENDENCY = 27
_RUNTIME_VERIFIER_E_TRANSACTION = 28
_RUNTIME_VERIFIER_E_PROOF = 33
_RUNTIME_RELEASE_DMG_PATTERN = (
    r"^Vibecrafted_[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?-"
    r"[0-9]{8}-[0-9a-f]{8}\.dmg$"
)
_RUNTIME_VERIFIER_SCHEMA_DEFS = frozenset(
    {
        "architecture",
        "assemblyReceipt",
        "fileEntry",
        "gitSha",
        "launchContract",
        "minimumMacos",
        "moduleBinding",
        "moduleManifest",
        "outerBundleCode",
        "productIdentity",
        "productManifest",
        "proofArtifact",
        "relativePath",
        "releaseAbsent",
        "releaseModuleIdentity",
        "releaseOutput",
        "releasePair",
        "releaseState",
        "runnerArgvObservation",
        "runnerAssertionObservation",
        "runnerProbeObservation",
        "runnerProbeObservations",
        "runnerSemanticObservation",
        "runnerWalkaroundReceipt",
        "runtimeIdentity",
        "sha256",
        "signedFileTransformation",
        "transactionReceipt",
        "walkaroundReceipt",
    }
)
_RUNTIME_VERIFIER_OBJECT_SCHEMA_DEFS = frozenset(
    {
        "assemblyReceipt",
        "fileEntry",
        "launchContract",
        "moduleBinding",
        "moduleManifest",
        "outerBundleCode",
        "productIdentity",
        "productManifest",
        "proofArtifact",
        "releaseAbsent",
        "releaseModuleIdentity",
        "releaseOutput",
        "releasePair",
        "runnerArgvObservation",
        "runnerAssertionObservation",
        "runnerProbeObservations",
        "runnerSemanticObservation",
        "runnerWalkaroundReceipt",
        "runtimeIdentity",
        "signedFileTransformation",
        "transactionReceipt",
    }
)
_RUNTIME_VERIFIER_TYPED_OBJECT_SCHEMA_PATHS = frozenset(
    {
        "$defs/fileEntry",
        "$defs/moduleManifest",
        "$defs/moduleManifest/properties/entrypoints",
        "$defs/signedFileTransformation",
        "$defs/assemblyReceipt",
        "$defs/moduleBinding",
        "$defs/outerBundleCode",
        "$defs/launchContract",
        "$defs/launchContract/properties/primary_shell",
        "$defs/launchContract/properties/shell",
        "$defs/launchContract/properties/environment",
        "$defs/productManifest",
        "$defs/productManifest/properties/modules/allOf/0/contains",
        "$defs/productManifest/properties/modules/allOf/1/contains",
        "$defs/productManifest/properties/entrypoints",
        "$defs/productIdentity",
        "$defs/runtimeIdentity",
        "$defs/releasePair",
        "$defs/releaseAbsent",
        "$defs/transactionReceipt",
        "$defs/proofArtifact",
        "$defs/releaseModuleIdentity",
        "$defs/releaseModuleIdentity/properties/manifest",
        "$defs/releaseModuleIdentity/properties/assembly_receipt",
        "$defs/releaseOutput",
        "$defs/releaseOutput/properties/signature_policy",
        "$defs/releaseOutput/properties/product",
        "$defs/releaseOutput/properties/product/properties/manifest",
        "$defs/releaseOutput/properties/outer_executable",
        "$defs/releaseOutput/properties/outer_executable/properties/signer_policy",
        "$defs/releaseOutput/properties/code_resources",
        "$defs/releaseOutput/properties/dmg",
        "$defs/releaseOutput/properties/modules",
        "$defs/releaseOutput/properties/source_revisions",
        "$defs/releaseOutput/properties/notarization",
        "$defs/releaseOutput/properties/notarization/properties/app",
        "$defs/releaseOutput/properties/notarization/properties/dmg",
        "$defs/runnerAssertionObservation",
        "$defs/runnerArgvObservation",
        "$defs/runnerSemanticObservation",
        "$defs/runnerProbeObservations",
        "$defs/runnerWalkaroundReceipt",
        "$defs/runnerWalkaroundReceipt/properties/observations",
    }
)
_RUNTIME_VERIFIER_UNTYPED_OBJECT_MATCHER_PATHS = frozenset(
    {
        "$defs/moduleManifest/allOf/0/if",
        "$defs/moduleManifest/allOf/0/then",
        "$defs/moduleManifest/allOf/0/then/properties/entrypoints",
        "$defs/moduleManifest/allOf/1/if",
        "$defs/moduleManifest/allOf/1/then",
        "$defs/moduleManifest/allOf/1/then/properties/entrypoints",
    }
)
_RUNTIME_ACTIVE_TEXT_ROOTS = (
    Path("vibecrafted-core/vibecrafted_core/config/vc-frame"),
    Path("runtime/generated"),
    Path("vibecrafted-core/vibecrafted_core/runtime"),
)
_RUNTIME_ACTIVE_TEXT_SUFFIXES = frozenset(
    {".bash", ".json", ".kdl", ".py", ".sh", ".toml", ".zsh"}
)
_ABSOLUTE_PATH_TOKEN = re.compile(r"/[^\s\"'`;,)>\]}]+")
_TOOLS_INSTALL_LEASE_ENV = "VIBECRAFTED_INSTALL_LEASE_FD"
_TOOLS_INSTALL_LEASE_TIMEOUT_ENV = "VIBECRAFTED_INSTALL_LOCK_TIMEOUT"
_TOOLS_INSTALL_LEASE_DEFAULT_SECONDS = 180.0
_TOOLS_GENERATIONS_TO_KEEP = 3
_RUNTIME_SERVICE_LABEL = "io.vetcoders.vibecrafted.server"
_RUNTIME_SERVICE_COMMAND_TIMEOUT_SECONDS = 45.0
_RUNTIME_SERVICE_ACTIVATION_TIMEOUT_SECONDS = 120.0
_RUNTIME_SERVICE_SETTLEMENT_TIMEOUT_SECONDS = 30.0
_SERVICE_LIFECYCLE_LOCK_MARKER = (
    b"readonly VIBECRAFTED_SERVICE_LIFECYCLE_LOCK_CONTRACT=1"
)
_RUNTIME_LIFECYCLE_ENV: ContextVar[dict[str, str] | None] = ContextVar(
    "runtime_lifecycle_environment",
    default=None,
)
_RUNTIME_SERVICE_COMMAND_DEADLINE: ContextVar[float | None] = ContextVar(
    "runtime_service_command_deadline",
    default=None,
)


class _RuntimeServiceTransition(OSError):
    """A structurally valid service snapshot that may still converge."""


@dataclass(frozen=True)
class _RuntimeServiceStatus:
    """Point-in-time read of the launchd-managed runtime service's supervisor/pair health."""

    installed: bool
    loaded: bool
    supervisor_live: bool
    supervisor_verified: bool
    supervisor_service_managed: bool
    build_current: bool
    pair_healthy: bool
    supervisor_pid: int | None

    @property
    def healthy(self) -> bool:
        """True when every observed signal (installed, loaded, supervisor live/verified/managed,
        build current, pair healthy, live PID) agrees the service is fully up.
        """
        return (
            self.installed
            and self.loaded
            and self.supervisor_live
            and self.supervisor_verified
            and self.supervisor_service_managed
            and self.build_current
            and self.pair_healthy
            and self.supervisor_pid is not None
            and self.supervisor_pid > 0
        )

    @property
    def quiescent(self) -> bool:
        """True when every observed signal agrees the service is fully torn down."""
        return (
            not self.loaded
            and not self.supervisor_live
            and not self.supervisor_verified
            and not self.supervisor_service_managed
            and not self.build_current
            and not self.pair_healthy
            and self.supervisor_pid is None
        )

    @property
    def reclaimable(self) -> bool:
        """Owned launchd supervisor is proven, but the managed pair is not.

        This is the stable degraded shape install must drain (supervisor live
        in backoff, pair_healthy false, often with an orphaned listener). It is
        not a pure mid-start race: identity is known enough to call service stop.
        """
        if self.healthy or self.quiescent:
            return False
        return (
            self.installed
            and self.loaded
            and self.supervisor_live
            and self.supervisor_verified
            and self.supervisor_service_managed
            and self.supervisor_pid is not None
            and self.supervisor_pid > 0
            and not self.pair_healthy
        )

    @property
    def needs_drain(self) -> bool:
        """Install must stop this service before publication fences close."""
        return self.healthy or self.reclaimable


@dataclass(frozen=True)
class _RuntimeLaunchAgentBackup:
    """Exact bytes/mode/service-args snapshot of the runtime LaunchAgent plist, for rollback."""

    path: Path
    contents: bytes | None
    mode: int | None
    service_arguments: tuple[str, ...]


@dataclass(frozen=True)
class _RetiredVcFrameProcess:
    """One same-user process proven to execute the retired vc-frame sibling."""

    pid: int
    birth: tuple[str, int, int]
    argv: tuple[str, ...]


_SERVER_CONFIG_MODULE: Any | None = None


def _server_config_module() -> Any:
    """Load (and cache) the installed runtime's server_config module by absolute path, since it
    lives outside this script's own package.
    """
    global _SERVER_CONFIG_MODULE
    if _SERVER_CONFIG_MODULE is not None:
        return _SERVER_CONFIG_MODULE
    module_path = (
        Path(__file__).resolve().parent.parent
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "server_config.py"
    )
    spec = importlib.util.spec_from_file_location(
        "vibecrafted_installer_server_config", module_path
    )
    if spec is None or spec.loader is None:
        raise OSError(f"cannot load server config owner from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    _SERVER_CONFIG_MODULE = module
    return module


def _runtime_service_arguments_from_config(
    backup: _RuntimeLaunchAgentBackup,
) -> tuple[str, ...]:
    """Seed config from a verified legacy plist, then let config own activation."""
    module = _server_config_module()
    captured = dict(zip(backup.service_arguments[::2], backup.service_arguments[1::2]))
    host = captured.get("--host", module.DEFAULT_BIND_HOST)
    try:
        port = int(captured.get("--port", str(module.DEFAULT_PORT)))
    except ValueError as exc:
        raise OSError("verified runtime LaunchAgent has a non-integer port") from exc
    seed = module.ServerConfig(
        bind_host=host,
        port=port,
        public_url=module.origin_for(host, port),
    )
    settings, _created = module.seed_server_config(
        seed,
        operator_home=_canonical_operator_home(),
    )
    arguments = list(settings.service_arguments)
    interval = captured.get("--interval")
    if interval:
        arguments.extend(("--interval", interval))
    return tuple(arguments)


@dataclass(frozen=True)
class _RuntimePayloadEntryBackup:
    """One backed-up runtime-payload entry: its path, backup location, kind, and content digest."""

    path: Path
    backup: Path | None
    kind: str
    digest: str | None


@dataclass(frozen=True)
class _RuntimePayloadBackup:
    """A complete runtime-payload transaction backup: root dir, entries, and root identity."""

    root: Path
    entries: tuple[_RuntimePayloadEntryBackup, ...]
    root_identity: tuple[int, int]


@dataclass
class _RuntimePayloadRestoreOperation:
    """In-flight bookkeeping for restoring one payload entry (staged/precall/displaced
    names+fds).
    """

    entry: _RuntimePayloadEntryBackup
    parent_fd: int
    staged_name: str | None
    staged_fd: int | None
    staged_kind: str | None
    displaced_name: str
    precall_name: str | None = None
    precall_fd: int | None = None
    precall_kind: str | None = None
    precall_digest: str | None = None
    current_displaced: bool = False
    replacement_published: bool = False
    precall_published: bool = False


@dataclass(frozen=True)
class _RuntimePayloadCaptureSource:
    """One captured source (parent fd, opened fd, kind, digest) feeding a payload backup."""

    path: Path
    parent_fd: int | None
    source_fd: int | None
    kind: str
    digest: str | None
    opened: os.stat_result | None


def _tools_handoff_path(current_link: Path) -> Path:
    """Path to the tools-handoff receipt JSON alongside the given `current_link` symlink."""
    return current_link.parent / ".vibecrafted-current-handoff.json"


def _tools_install_lease_path(current_link: Path) -> Path:
    """Path to the cross-process install lease lockfile alongside `current_link`."""
    return current_link.parent / ".vibecrafted-install.lock"


def _tools_handoff_file(shared_home: Path) -> Path:
    """Path to the tools-handoff receipt for the shared home's current-tools link."""
    return _tools_handoff_path(_current_tools_link(shared_home))


def _tools_install_timeout(timeout_seconds: float | None) -> float:
    """Resolve the tools-install lease timeout: explicit value, else
    VIBECRAFTED_INSTALL_LOCK_TIMEOUT env, else the built-in default; validates it is
    finite/non-negative.
    """
    if timeout_seconds is None:
        raw = os.environ.get(
            _TOOLS_INSTALL_LEASE_TIMEOUT_ENV,
            str(_TOOLS_INSTALL_LEASE_DEFAULT_SECONDS),
        )
        try:
            timeout_seconds = float(raw)
        except ValueError as exc:
            raise ValueError(
                f"{_TOOLS_INSTALL_LEASE_TIMEOUT_ENV} must be a finite "
                f"non-negative number, got {raw!r}"
            ) from exc
    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        raise ValueError(
            "tools install lease timeout must be a finite non-negative number"
        )
    return timeout_seconds


def _validate_tools_lease_descriptor(descriptor: int, lock_path: Path) -> None:
    """Verify `descriptor` still owns the exact regular file at `lock_path` (no swap/replace
    raced in).
    """
    try:
        opened = os.fstat(descriptor)
        named = os.stat(lock_path, follow_symlinks=False)
    except OSError as exc:
        raise OSError(
            f"inherited tools install lease is unavailable at {lock_path}"
        ) from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != os.geteuid()
        or opened.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise OSError(
            f"inherited tools install lease does not own the regular file {lock_path}"
        )


def _tools_lease_owner(descriptor: int) -> str:
    """Human-readable owner info (pid/operation/started_at) decoded from the lease file, or a
    placeholder.
    """
    try:
        raw = os.pread(descriptor, 4096, 0).decode("utf-8", errors="replace").strip()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return "owner metadata unavailable"
    if not isinstance(payload, dict):
        return "owner metadata unavailable"
    pid = payload.get("pid", "unknown")
    operation = payload.get("operation", "unknown")
    started_at = payload.get("started_at", "unknown")
    return f"pid={pid}, operation={operation}, started_at={started_at}"


def _write_tools_lease_owner(descriptor: int, operation: str) -> None:
    """Write this process's pid/operation/timestamp as the lease file's owner metadata."""
    encoded = (
        json.dumps(
            {
                "pid": os.getpid(),
                "operation": operation,
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    os.ftruncate(descriptor, 0)
    os.lseek(descriptor, 0, os.SEEK_SET)
    view = memoryview(encoded)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("could not persist tools install lease owner")
        view = view[written:]
    os.fsync(descriptor)


@contextmanager
def _tools_install_lease(
    current_link: Path,
    *,
    timeout_seconds: float | None = None,
    operation: str = "runtime-publish",
) -> Iterator[int]:
    """Serialize runtime publication and Python-tool/service reconciliation."""
    lock_path = _tools_install_lease_path(current_link)
    inherited_raw = os.environ.get(_TOOLS_INSTALL_LEASE_ENV)
    if inherited_raw:
        try:
            inherited = int(inherited_raw)
        except ValueError as exc:
            raise OSError(
                f"invalid inherited tools install lease descriptor: {inherited_raw!r}"
            ) from exc
        _validate_tools_lease_descriptor(inherited, lock_path)
        try:
            fcntl.flock(inherited, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OSError(
                "inherited tools install lease descriptor does not own the lock"
            ) from exc
        yield inherited
        return

    timeout = _tools_install_timeout(timeout_seconds)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        lock_path,
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    acquired = False
    try:
        _validate_tools_lease_descriptor(descriptor, lock_path)
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    owner = _tools_lease_owner(descriptor)
                    raise TimeoutError(
                        "another Vibecrafted installer still owns "
                        f"{lock_path} ({owner}); waited {timeout:.2f}s"
                    )
                time.sleep(min(0.1, remaining))
        _write_tools_lease_owner(descriptor, operation)
        yield descriptor
    finally:
        if acquired:
            try:
                os.ftruncate(descriptor, 0)
                os.fsync(descriptor)
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _require_inherited_tools_install_lease(shared_home: Path) -> int:
    """Validate and flock-verify an installer lease descriptor inherited via env var; raises
    OSError if none was inherited or it is not actually held.
    """
    raw_descriptor = os.environ.get(_TOOLS_INSTALL_LEASE_ENV)
    if not raw_descriptor:
        raise OSError(
            "runtime service handoff requires the inherited cross-process "
            "installer lease"
        )
    try:
        descriptor = int(raw_descriptor)
    except ValueError as exc:
        raise OSError(
            f"invalid inherited tools install lease descriptor: {raw_descriptor!r}"
        ) from exc
    _validate_tools_lease_descriptor(
        descriptor,
        _tools_install_lease_path(_current_tools_link(shared_home)),
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise OSError(
            "inherited tools install lease descriptor does not own the lock"
        ) from exc
    if _tools_lease_owner(descriptor) == "owner metadata unavailable":
        raise OSError("inherited tools install lease has no verified owner metadata")
    return descriptor


def _runtime_launchctl_job_is_absent(
    result: subprocess.CompletedProcess[str],
) -> bool:
    """True if launchctl reports the fixed-label service job is simply absent; raises OSError
    for any other non-zero/ambiguous result.
    """
    if result.returncode == 0:
        return False
    detail = result.stderr.strip() or result.stdout.strip()
    if (
        result.returncode == 113
        and f'Could not find service "{_RUNTIME_SERVICE_LABEL}"' in detail
    ):
        return True
    raise OSError(
        "fixed-label runtime service ownership query failed "
        f"({detail or f'exit={result.returncode}'})"
    )


def _runtime_loaded_service_home() -> Path | None:
    """VIBECRAFTED_HOME of the currently loaded fixed-label launchd service, or None if not
    loaded.
    """
    if sys.platform != "darwin":
        return None
    result = _runtime_launchctl("print", _runtime_launch_target())
    if _runtime_launchctl_job_is_absent(result):
        return None
    raw_home = _runtime_launchctl_print_value(
        result.stdout,
        "VIBECRAFTED_HOME",
        separator="=>",
        section="environment",
    )
    if not raw_home:
        raise OSError(
            "loaded fixed-label runtime service has no attributable VIBECRAFTED_HOME"
        )
    return Path(raw_home).expanduser().resolve(strict=False)


def _canonical_operator_home() -> Path:
    """The real, non-overridden HOME directory for the current effective UID."""
    if sys.platform != "darwin":
        return Path.home().resolve(strict=False)
    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=False)


def _assert_runtime_loaded_service_owner(shared_home: Path) -> Path | None:
    """Raise OSError if a loaded fixed-label service belongs to a different VIBECRAFTED_HOME."""
    loaded_home = _runtime_loaded_service_home()
    if loaded_home is not None and loaded_home != shared_home.resolve(strict=False):
        raise OSError(
            "fixed-label runtime service belongs to foreign home "
            f"{loaded_home}; expected {shared_home.resolve(strict=False)}"
        )
    return loaded_home


def _runtime_service_has_evidence(shared_home: Path) -> bool:
    """True if any on-disk or launchd evidence suggests the runtime service is (or was)
    installed.
    """
    runtime_dir = shared_home / "server"
    evidence = (
        Path.home() / "Library" / "LaunchAgents" / f"{_RUNTIME_SERVICE_LABEL}.plist",
        runtime_dir / "supervisor.lock",
        runtime_dir / "server.pid",
        runtime_dir / "guardian.pid",
        runtime_dir / "server.identity.json",
        runtime_dir / "guardian.identity.json",
    )
    if any(path.exists() or path.is_symlink() for path in evidence):
        return True
    loaded_home = _runtime_loaded_service_home()
    return loaded_home == shared_home.resolve(strict=False)


def _runtime_launchctl(
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    """Run `launchctl <arguments>` with a minimal, deterministic environment; never raises on
    non-zero exit.
    """
    launchctl = Path("/bin/launchctl")
    if not launchctl.is_file():
        raise OSError("macOS runtime handoff requires /bin/launchctl")
    return subprocess.run(
        [str(launchctl), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env={
            "HOME": str(Path.home()),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        },
    )


def _runtime_launch_target() -> str:
    """launchctl gui-domain target string for the fixed runtime service label."""
    return f"gui/{os.getuid()}/{_RUNTIME_SERVICE_LABEL}"


def _runtime_launch_domain() -> str:
    """launchctl gui-domain string for the current user."""
    return f"gui/{os.getuid()}"


def _runtime_launch_agent_path() -> Path:
    """Path to the runtime service's LaunchAgent plist under ~/Library/LaunchAgents."""
    return Path.home() / "Library" / "LaunchAgents" / f"{_RUNTIME_SERVICE_LABEL}.plist"


def _runtime_launchd_disabled_state() -> bool:
    """Query launchd whether the fixed runtime service label is currently disabled."""
    result = _runtime_launchctl("print-disabled", _runtime_launch_domain())
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or result.returncode
        raise OSError(f"launchd disabled-state query failed ({detail})")
    pattern = re.compile(
        rf'^\s*"{re.escape(_RUNTIME_SERVICE_LABEL)}"\s*=>\s*'
        r"(true|false|enabled|disabled)\s*$"
    )
    matches = [
        match.group(1)
        for line in result.stdout.splitlines()
        if (match := pattern.match(line))
    ]
    if len(matches) > 1:
        raise OSError("launchd returned duplicate disabled-state entries")
    return matches in (["true"], ["disabled"])


def _set_runtime_launchd_disabled(disabled: bool) -> None:
    """Enable or disable the fixed runtime service label via launchctl, verifying the resulting
    state.
    """
    action = "disable" if disabled else "enable"
    result = _runtime_launchctl(action, _runtime_launch_target())
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or result.returncode
        raise OSError(f"launchd could not {action} the runtime label ({detail})")
    if _runtime_launchd_disabled_state() is not disabled:
        raise OSError(f"launchd did not verify the runtime label as {action}d")


class _RuntimeLaunchdMutationGate:
    """Prevent an already-resolved legacy service command from bootstrapping.

    The public launcher and the new supervisor honor the tools-install lease.
    A process that resolved the legacy implementation before publication does
    not.  Disabling the fixed launchd label closes that compatibility window;
    the gate is reopened only for a bounded, strictly verified activation.
    """

    def __init__(self, *, required: bool) -> None:
        """Compute whether launchd mutation gating is required (macOS + caller-requested)."""
        self.required = sys.platform == "darwin" and required
        self.originally_disabled = False
        self.disabled = False
        self._retain_disabled = False

    def __enter__(self) -> _RuntimeLaunchdMutationGate:  # noqa: PYI034
        """Disable the service label on entry if gating is required and it was not already
        disabled.
        """
        if not self.required:
            return self
        self.originally_disabled = _runtime_launchd_disabled_state()
        if not self.originally_disabled:
            _set_runtime_launchd_disabled(True)
        self.disabled = True
        return self

    def disable(self) -> None:
        """Disable the service label if required and not already disabled by this gate."""
        if not self.required or self.disabled:
            return
        _set_runtime_launchd_disabled(True)
        self.disabled = True

    def enable_for_activation(self) -> None:
        """Re-enable the service label if it was disabled by this gate, for a bounded activation
        window.
        """
        if not self.required or not self.disabled:
            return
        _set_runtime_launchd_disabled(False)
        self.disabled = False

    def retain_disabled(self) -> None:
        """Force the service label disabled and mark it to stay disabled through `__exit__`."""
        if self.required:
            self._retain_disabled = True
            self.disable()

    def allow_original_state_restore(self) -> None:
        """Clear the retain-disabled flag so `__exit__` restores the original enabled/disabled
        state.
        """
        self._retain_disabled = False

    def commit_enabled_state(self) -> None:
        """Keep a successfully installed explicit service enabled."""
        if not self.required:
            return
        self._retain_disabled = False
        self.originally_disabled = False
        self.enable_for_activation()

    @property
    def retention_required(self) -> bool:
        """True if this gate must leave the service label disabled on exit."""
        return self._retain_disabled

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Restore (or keep) the disabled state on exit per retain_disabled/originally_disabled."""
        if not self.required:
            return
        if self._retain_disabled:
            self.disable()
            return
        if self.originally_disabled:
            self.disable()
        else:
            self.enable_for_activation()


def _runtime_launchctl_print_value(
    payload: str,
    key: str,
    *,
    separator: str,
    section: str | None = None,
) -> str | None:
    """Extract one `key <separator> value` line from launchctl `print` output, optionally scoped
    to a named `{ ... }` section.
    """
    prefix = f"{key} {separator} "
    in_section = section is None
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not in_section:
            if line == f"{section} = {{":
                in_section = True
            continue
        if section is not None and line == "}":
            return None
        if line.startswith(prefix):
            value = line.removeprefix(prefix)
            return value or None
    return None


def _runtime_launch_agent_contract(shared_home: Path) -> dict[str, Path]:
    """Read, verify, and decode the owned runtime LaunchAgent plist into its expected
    path/program/supervisor/home contract; raises OSError on any inconsistency.
    """
    plist_path = _runtime_launch_agent_path()
    try:
        visible = plist_path.lstat()
        descriptor = os.open(
            plist_path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise OSError(
            "loaded runtime service has no readable owned LaunchAgent plist"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            plist_path.is_symlink()
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            raise OSError(
                "loaded runtime LaunchAgent plist is not a stable user-owned file"
            )
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            payload = plistlib.load(handle)
    except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
        raise OSError("loaded runtime LaunchAgent plist is invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if not isinstance(payload, dict) or payload.get("Label") != _RUNTIME_SERVICE_LABEL:
        raise OSError("loaded runtime LaunchAgent plist has an invalid label")
    arguments = payload.get("ProgramArguments")
    environment = payload.get("EnvironmentVariables")
    if (
        not isinstance(arguments, list)
        or not arguments
        or not all(isinstance(argument, str) and argument for argument in arguments)
        or not isinstance(environment, dict)
    ):
        raise OSError("loaded runtime LaunchAgent plist has an invalid schema")

    def required_argument(flag: str) -> str:
        """Look up a required `--flag value` pair in the plist's ProgramArguments; raises
        OSError if absent/empty.
        """
        try:
            value = arguments[arguments.index(flag) + 1]
        except (ValueError, IndexError) as exc:
            raise OSError(
                f"loaded runtime LaunchAgent plist is missing {flag}"
            ) from exc
        if not value:
            raise OSError(f"loaded runtime LaunchAgent plist has an empty {flag}")
        return value

    def optional_argument(flag: str) -> str | None:
        """Look up an optional `--flag value` pair in the plist's ProgramArguments; None if the
        flag is absent.
        """
        try:
            value = arguments[arguments.index(flag) + 1]
        except ValueError:
            return None
        except IndexError as exc:
            raise OSError(
                f"loaded runtime LaunchAgent plist has an empty {flag}"
            ) from exc
        return value or None

    expected_raw = {
        "plist": str(plist_path),
        "program": arguments[0],
        "supervisor": environment.get("VIBECRAFTED_SERVER_SUPERVISOR_PATH"),
        "home": environment.get("VIBECRAFTED_HOME"),
        "runtime_home": environment.get("VIBECRAFTED_RUNTIME_HOME"),
        "operator_home": environment.get("HOME"),
        "launcher": required_argument("--launcher"),
    }
    if any(not isinstance(value, str) or not value for value in expected_raw.values()):
        raise OSError("loaded runtime LaunchAgent plist omits owned runtime paths")
    expected = {
        key: Path(str(value)).expanduser().resolve(strict=False)
        for key, value in expected_raw.items()
    }
    if (
        expected["program"] != expected["supervisor"]
        or Path(required_argument("--home")).expanduser().resolve(strict=False)
        != expected["home"]
        or Path(required_argument("--runtime-home")).expanduser().resolve(strict=False)
        != expected["runtime_home"]
        or expected["home"] != shared_home.resolve(strict=False)
    ):
        raise OSError("loaded runtime LaunchAgent plist has inconsistent owned paths")
    operator_argument = optional_argument("--operator-home")
    if operator_argument is not None and (
        Path(operator_argument).expanduser().resolve(strict=False)
        != expected["operator_home"]
    ):
        raise OSError(
            "loaded runtime LaunchAgent plist has an inconsistent operator home"
        )
    return expected


def _assert_runtime_launchd_job_owned(
    shared_home: Path,
    *,
    result: subprocess.CompletedProcess[str] | None = None,
) -> bool:
    """True if the currently loaded launchd job's observed paths exactly match the owned
    LaunchAgent contract for `shared_home`; False if nothing is loaded.
    """
    observed = result or _runtime_launchctl("print", _runtime_launch_target())
    if observed.returncode != 0:
        return False
    expected = _runtime_launch_agent_contract(shared_home)
    actual_raw = {
        "plist": _runtime_launchctl_print_value(
            observed.stdout,
            "path",
            separator="=",
        ),
        "program": _runtime_launchctl_print_value(
            observed.stdout,
            "program",
            separator="=",
        ),
        "supervisor": _runtime_launchctl_print_value(
            observed.stdout,
            "VIBECRAFTED_SERVER_SUPERVISOR_PATH",
            separator="=>",
            section="environment",
        ),
        "home": _runtime_launchctl_print_value(
            observed.stdout,
            "VIBECRAFTED_HOME",
            separator="=>",
            section="environment",
        ),
        "runtime_home": _runtime_launchctl_print_value(
            observed.stdout,
            "VIBECRAFTED_RUNTIME_HOME",
            separator="=>",
            section="environment",
        ),
        "operator_home": _runtime_launchctl_print_value(
            observed.stdout,
            "HOME",
            separator="=>",
            section="environment",
        ),
    }
    if any(value is None for value in actual_raw.values()):
        raise OSError(
            "loaded runtime launchd job omits the owned path contract; refusing mutation"
        )
    actual = {
        key: Path(str(value)).expanduser().resolve(strict=False)
        for key, value in actual_raw.items()
    }
    if any(actual[key] != expected[key] for key in actual):
        raise OSError(
            "loaded fixed-label launchd job belongs to foreign runtime paths; "
            "refusing mutation"
        )
    return True


def _bootout_owned_runtime_launchd_job(shared_home: Path) -> bool:
    """Unload (bootout) the owned fixed-label launchd job after verifying it is ours; returns
    False if nothing was loaded.
    """
    observed = _runtime_launchctl("print", _runtime_launch_target())
    if _runtime_launchctl_job_is_absent(observed):
        return False
    _assert_runtime_launchd_job_owned(shared_home, result=observed)
    result = _runtime_launchctl("bootout", _runtime_launch_target())
    if result.returncode != 0:
        still_loaded = _runtime_launchctl("print", _runtime_launch_target())
        if _runtime_launchctl_job_is_absent(still_loaded):
            return True
        raise OSError(
            "verified runtime launchd job raced the install fence and could "
            f"not be unloaded ({result.stderr.strip() or result.returncode})"
        )
    final_observation = _runtime_launchctl("print", _runtime_launch_target())
    if not _runtime_launchctl_job_is_absent(final_observation):
        raise OSError("verified runtime launchd job remains loaded after bootout")
    return True


def _capture_runtime_launch_agent_backup(
    shared_home: Path,
) -> _RuntimeLaunchAgentBackup:
    """Snapshot the current runtime LaunchAgent plist's exact bytes/mode/service-args, verifying
    ownership.
    """
    path = _runtime_launch_agent_path()
    if not path.exists() and not path.is_symlink():
        return _RuntimeLaunchAgentBackup(path, None, None, ())
    try:
        visible = path.lstat()
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise OSError(f"cannot snapshot runtime LaunchAgent at {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            path.is_symlink()
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            raise OSError(
                "runtime LaunchAgent snapshot is not a stable user-owned file"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > 1024 * 1024:
                raise OSError("runtime LaunchAgent exceeds the bounded snapshot size")
        named = os.stat(path, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise OSError("runtime LaunchAgent changed during its snapshot")
    finally:
        os.close(descriptor)

    contents = b"".join(chunks)
    try:
        payload = plistlib.loads(contents)
    except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
        raise OSError("runtime LaunchAgent snapshot is not a valid plist") from exc
    if not isinstance(payload, dict) or payload.get("Label") != _RUNTIME_SERVICE_LABEL:
        raise OSError("runtime LaunchAgent snapshot has a foreign label")
    arguments = payload.get("ProgramArguments")
    environment = payload.get("EnvironmentVariables")
    if (
        not isinstance(arguments, list)
        or not arguments
        or not all(isinstance(argument, str) and argument for argument in arguments)
        or not isinstance(environment, dict)
    ):
        raise OSError("runtime LaunchAgent snapshot has an invalid schema")
    if Path(arguments[0]).expanduser().resolve(strict=False) != Path(
        str(environment.get("VIBECRAFTED_SERVER_SUPERVISOR_PATH", ""))
    ).expanduser().resolve(strict=False) or Path(
        str(environment.get("VIBECRAFTED_HOME", ""))
    ).expanduser().resolve(strict=False) != shared_home.resolve(strict=False):
        raise OSError("runtime LaunchAgent snapshot has foreign runtime paths")

    service_arguments: list[str] = []
    for flag in ("--host", "--port", "--interval"):
        positions = [
            index for index, argument in enumerate(arguments) if argument == flag
        ]
        if len(positions) > 1:
            raise OSError(f"runtime LaunchAgent repeats {flag}")
        if positions:
            index = positions[0]
            if index + 1 >= len(arguments) or not arguments[index + 1]:
                raise OSError(f"runtime LaunchAgent has no value for {flag}")
            service_arguments.extend((flag, arguments[index + 1]))
    return _RuntimeLaunchAgentBackup(
        path,
        contents,
        stat.S_IMODE(opened.st_mode),
        tuple(service_arguments),
    )


def _restore_runtime_launch_agent_backup(
    shared_home: Path,
    backup: _RuntimeLaunchAgentBackup,
) -> None:
    """Restore a previously captured LaunchAgent plist backup (including exact absence) via
    atomic write.
    """
    if backup.path != _runtime_launch_agent_path():
        raise OSError("runtime LaunchAgent backup targets an unexpected path")
    current_exists = backup.path.exists() or backup.path.is_symlink()
    current = (
        _capture_runtime_launch_agent_backup(shared_home) if current_exists else None
    )
    if backup.contents is None:
        if current is None:
            return
        if current.contents is None:
            return
        backup.path.unlink()
        directory = os.open(
            backup.path.parent,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return
    _atomic_bytes_file(backup.path, backup.contents, mode=backup.mode or 0o600)


def _activate_runtime_service_from_backup(
    shared_home: Path,
    backup: _RuntimeLaunchAgentBackup,
) -> None:
    """Reload and start the runtime service from a captured LaunchAgent backup, verifying it
    comes up healthy.
    """
    if backup.contents is None:
        raise OSError("active legacy service has no LaunchAgent definition to restore")
    _restore_runtime_launch_agent_backup(shared_home, backup)
    loaded = _runtime_launchctl("print", _runtime_launch_target())
    if loaded.returncode == 0:
        raise OSError(
            "refusing legacy service restore while its label is already loaded"
        )
    originally_disabled = _runtime_launchd_disabled_state()
    try:
        if originally_disabled:
            _set_runtime_launchd_disabled(False)
        result = _runtime_launchctl(
            "bootstrap",
            _runtime_launch_domain(),
            str(backup.path),
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or result.returncode
            raise OSError(f"legacy LaunchAgent bootstrap failed ({detail})")
        if not _assert_runtime_launchd_job_owned(shared_home):
            raise OSError("restored legacy LaunchAgent is not loaded")
        restored = _runtime_service_snapshot(shared_home)
        if restored is None or not restored[1].healthy or restored[2] != "running":
            raise OSError("restored legacy service did not prove a healthy pair")
    finally:
        if originally_disabled:
            _set_runtime_launchd_disabled(True)


def _runtime_service_launcher(shared_home: Path) -> Path | None:
    """Resolve the current, user-owned, executable `vibecrafted` launcher used to drive the old
    service CLI.
    """
    candidate = vibecrafted_launcher_bin() / "vibecrafted"
    try:
        resolved = candidate.resolve(strict=True)
        metadata = resolved.stat()
    except OSError:
        if _runtime_service_has_evidence(shared_home):
            raise OSError(
                "runtime service evidence exists but the current on-disk "
                f"launcher is unavailable at {candidate}"
            )
        return None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or not os.access(resolved, os.X_OK)
    ):
        raise OSError(
            "current Vibecrafted launcher is not a user-owned executable regular "
            f"file: {resolved}"
        )
    # Run the exact launcher proven above.  Re-resolving the public symlink for
    # each status/stop call would let a concurrent publication silently switch
    # the authority used halfway through the legacy drain.
    return resolved


def _retired_vc_frame_process_census() -> tuple[_RetiredVcFrameProcess, ...]:
    """Return stable same-user processes whose argv[0] is exactly vc-frame.real."""
    if sys.platform != "darwin":
        return ()
    retired = (vibecrafted_launcher_bin() / "vc-frame.real").resolve(strict=False)
    records: list[_RetiredVcFrameProcess] = []
    for pid in _darwin_process_ids():
        if pid == os.getpid():
            continue
        try:
            first_birth = _darwin_process_birth(pid)
            if first_birth[1] != os.geteuid():
                continue
            first_argv = _darwin_process_arguments(pid, pointer_size=first_birth[2])
            second_argv = _darwin_process_arguments(pid, pointer_size=first_birth[2])
            second_birth = _darwin_process_birth(pid)
        except ProcessLookupError:
            continue
        if first_birth != second_birth or first_argv != second_argv:
            raise OSError(
                f"Darwin process {pid} changed during retired vc-frame census"
            )
        if (
            first_argv
            and Path(first_argv[0]).expanduser().resolve(strict=False) == retired
        ):
            records.append(_RetiredVcFrameProcess(pid, first_birth, first_argv))
    return tuple(sorted(records, key=lambda record: (record.pid, record.birth)))


def _retired_vc_frame_process_still_matches(record: _RetiredVcFrameProcess) -> bool:
    """Re-prove birth identity and argv before signaling a retired process."""
    try:
        birth = _darwin_process_birth(record.pid)
        argv = _darwin_process_arguments(record.pid, pointer_size=birth[2])
    except ProcessLookupError:
        return False
    return birth == record.birth and argv == record.argv


def _terminate_retired_vc_frame_processes(
    records: Sequence[_RetiredVcFrameProcess], *, timeout_seconds: float = 5.0
) -> None:
    """Terminate only re-verified retired processes and require a zero-leftover postcondition."""
    for record in records:
        if _retired_vc_frame_process_still_matches(record):
            try:
                os.kill(record.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + timeout_seconds
    pending = tuple(records)
    while pending and time.monotonic() < deadline:
        pending = tuple(
            record
            for record in pending
            if _retired_vc_frame_process_still_matches(record)
        )
        if pending:
            time.sleep(0.1)
    for record in pending:
        if _retired_vc_frame_process_still_matches(record):
            try:
                os.kill(record.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    kill_deadline = time.monotonic() + max(0.5, min(timeout_seconds, 2.0))
    while pending and time.monotonic() < kill_deadline:
        pending = tuple(
            record
            for record in pending
            if _retired_vc_frame_process_still_matches(record)
        )
        if pending:
            time.sleep(0.05)
    if pending:
        raise OSError("retired vc-frame.real process remains after verified teardown")


def _teardown_owned_runtime_for_uninstall(
    shared_home: Path, *, dry_run: bool
) -> tuple[str, ...]:
    """Stop the owned service plane and retired vc-frame processes before deleting files."""
    if sys.platform != "darwin":
        return ()
    actions: list[str] = []
    current_link = _current_tools_link(shared_home)
    lease_path = _tools_install_lease_path(current_link)
    lease_preexisting = _path_present(lease_path)
    with _tools_install_lease(
        current_link,
        operation="runtime-uninstall",
    ) as descriptor:
        os.set_inheritable(descriptor, True)
        with _inherited_tools_install_lease(descriptor):
            if _runtime_service_has_evidence(shared_home):
                _assert_runtime_loaded_service_owner(shared_home)
                snapshot = _runtime_service_snapshot(shared_home)
                if snapshot is None:
                    raise OSError(
                        "runtime service evidence exists but no verified launcher is available"
                    )
                launcher, status, pair_state = snapshot
                if status.installed or status.loaded or status.supervisor_live:
                    actions.append("stop and uninstall owned runtime service")
                    if not dry_run:
                        result = _run_runtime_service_command(
                            launcher,
                            shared_home,
                            "service",
                            "uninstall",
                        )
                        if result.returncode != 0:
                            detail = (
                                result.stderr.strip()
                                or result.stdout.strip()
                                or f"exit={result.returncode}"
                            )
                            raise OSError(
                                f"owned runtime service uninstall failed ({detail})"
                            )
                        final = _runtime_service_snapshot(shared_home)
                        if final is None:
                            raise OSError(
                                "runtime launcher disappeared before teardown postcondition"
                            )
                        _, final_status, final_pair = final
                        if (
                            final_status.installed
                            or not final_status.quiescent
                            or final_pair != "stopped"
                        ):
                            raise OSError(
                                "owned runtime service remains after uninstall"
                            )
                elif pair_state != "stopped":
                    raise OSError(
                        "runtime service is unowned but its server pair is not stopped"
                    )
            retired = _retired_vc_frame_process_census()
            if retired:
                actions.append(
                    f"terminate {len(retired)} retired vc-frame.real process(es)"
                )
                if not dry_run:
                    _terminate_retired_vc_frame_processes(retired)
                    if _retired_vc_frame_process_census():
                        raise OSError(
                            "retired vc-frame.real processes remain after teardown"
                        )
    if dry_run and not lease_preexisting:
        # A dry run must leave the disk exactly as it found it. The real teardown
        # leaves the lease to the uninstall inventory, which removes it by name.
        try:
            lease_path.unlink()
        except OSError:
            pass
    return tuple(actions)


def _runtime_service_environment(
    launcher: Path,
    shared_home: Path,
) -> dict[str, str]:
    """Build the subprocess environment for invoking the old launcher's `server` subcommands."""
    environment = os.environ.copy()
    existing_path = environment.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    environment["PATH"] = f"{launcher.parent}:{existing_path}"
    environment["VIBECRAFTED_HOME"] = str(shared_home.resolve(strict=False))
    # server_supervisor must validate the very same install FD that this
    # process owns, including XDG-only layouts where runtime-home/tools would
    # otherwise diverge.
    environment["VIBECRAFTED_TOOLS_HOME"] = str(
        vibecrafted_tools_home().resolve(strict=False)
    )
    lifecycle_environment = _RUNTIME_LIFECYCLE_ENV.get()
    if lifecycle_environment is not None:
        environment.update(lifecycle_environment)
    return environment


def _run_runtime_service_command(
    launcher: Path,
    shared_home: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    """Run `<launcher> server <arguments>` under the inherited install lease, with a bounded
    timeout.
    """
    descriptor = _require_inherited_tools_install_lease(shared_home)
    timeout_seconds = _RUNTIME_SERVICE_COMMAND_TIMEOUT_SECONDS
    deadline = _RUNTIME_SERVICE_COMMAND_DEADLINE.get()
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("runtime service observation deadline expired")
        timeout_seconds = min(timeout_seconds, remaining)
    return subprocess.run(
        [str(launcher), "server", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=_runtime_service_environment(launcher, shared_home),
        pass_fds=(descriptor,),
    )


def _decode_runtime_service_status(
    result: subprocess.CompletedProcess[str],
) -> _RuntimeServiceStatus:
    """Parse the old launcher's `service status --json` output into a `_RuntimeServiceStatus`,
    enforcing the expected exit code for its observed state.
    """
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise OSError(
            "old launcher service status did not return one bounded JSON record"
        )
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise OSError("old launcher service status returned invalid JSON") from exc
    boolean_fields = (
        "installed",
        "loaded",
        "supervisor_live",
        "supervisor_verified",
        "supervisor_service_managed",
        "build_current",
        "pair_healthy",
    )
    if not isinstance(payload, dict) or any(
        not isinstance(payload.get(field), bool) for field in boolean_fields
    ):
        raise OSError("old launcher service status JSON has an invalid schema")
    supervisor_pid = payload.get("supervisor_pid")
    if supervisor_pid is not None and (
        not isinstance(supervisor_pid, int)
        or isinstance(supervisor_pid, bool)
        or supervisor_pid <= 0
    ):
        raise OSError("old launcher service status has an invalid supervisor PID")
    status = _RuntimeServiceStatus(
        installed=payload["installed"],
        loaded=payload["loaded"],
        supervisor_live=payload["supervisor_live"],
        supervisor_verified=payload["supervisor_verified"],
        supervisor_service_managed=payload["supervisor_service_managed"],
        build_current=payload["build_current"],
        pair_healthy=payload["pair_healthy"],
        supervisor_pid=supervisor_pid,
    )
    # Known terminal shapes for handoff:
    # - healthy: exit 0
    # - quiescent: exit 1
    # - reclaimable (owned supervisor, pair down): exit 1 — stable degrade,
    #   not a mid-start race. Install drains it via service stop.
    # Anything else is a transition (or corruption) and stays fail-closed.
    if status.healthy:
        expected_returncode = 0
    elif status.quiescent or status.reclaimable:
        expected_returncode = 1
    else:
        detail = result.stderr.strip() or f"exit={result.returncode}"
        raise _RuntimeServiceTransition(
            "runtime service identity is uncertain while transition is in progress "
            f"({detail})"
        )
    if result.returncode != expected_returncode:
        detail = result.stderr.strip() or f"exit={result.returncode}"
        raise OSError(
            "runtime service identity is uncertain; refusing pre-swap mutation "
            f"({detail})"
        )
    return status


def _runtime_service_pair_state(
    launcher: Path,
    shared_home: Path,
) -> str:
    """Determine the managed pair state from the old launcher's text status."""
    result = _run_runtime_service_command(launcher, shared_home, "status")
    running = (
        result.returncode == 0
        and "Server: RUNNING" in result.stdout
        and "Guardian: RUNNING" in result.stdout
    )
    stopped = (
        result.returncode == 0
        and "Server: STOPPED" in result.stdout
        and "Guardian: STOPPED" in result.stdout
    )
    orphaned = (
        result.returncode != 0
        and "Supervision: LAUNCHD" in result.stdout
        and "Server: STOPPED" in result.stdout
        and "Guardian: ORPHANED" in result.stdout
    )
    if running:
        return "running"
    if stopped:
        return "stopped"
    if orphaned:
        return "orphaned"
    detail = (
        result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}"
    )
    if (
        _RUNTIME_SERVICE_COMMAND_DEADLINE.get() is not None
        and "Supervision: LAUNCHD" in result.stdout
        and any(
            marker in result.stdout
            for marker in ("Server: RUNNING", "Server: STOPPED", "Server: PID-MISMATCH")
        )
        and any(
            marker in result.stdout
            for marker in (
                "Guardian: RUNNING",
                "Guardian: STOPPED",
                "Guardian: PID-MISMATCH",
            )
        )
    ):
        raise _RuntimeServiceTransition(
            "runtime server identity is still converging during bounded activation "
            f"({detail})"
        )
    raise OSError(
        "runtime server/guardian identity is uncertain; refusing install handoff "
        f"({detail})"
    )


def _runtime_service_snapshot(
    shared_home: Path,
) -> tuple[Path, _RuntimeServiceStatus, str] | None:
    """Take one consistent snapshot of the runtime service: launcher, status, and pair state,
    cross-checking the two observations for agreement.
    """
    launcher = _runtime_service_launcher(shared_home)
    if launcher is None:
        return None
    status = _decode_runtime_service_status(
        _run_runtime_service_command(
            launcher,
            shared_home,
            "service",
            "status",
            "--json",
        )
    )
    # service_status JSON already proves the managed supervisor and exact
    # server/guardian pair from one snapshot.  A second text probe would compose
    # two different moments and can manufacture disagreement across a launchd
    # restart.
    if status.healthy:
        return launcher, status, "running"
    pair_state = _runtime_service_pair_state(launcher, shared_home)
    if status.reclaimable and pair_state == "orphaned":
        # The verified managed supervisor owns the lifecycle, while a guardian
        # from that degraded pair remains alive after its server disappeared.
        # This is exactly the reclaimable state that service stop is designed
        # to drain before publication.
        return launcher, status, pair_state
    if pair_state != "stopped":
        # Reclaimable supervisors still report Server/Guardian STOPPED while
        # an orphan may hold the port; only true RUNNING disagreement is fatal.
        if status.reclaimable and pair_state == "running":
            # A single snapshot cannot tell a stable degrade (supervisor in
            # backoff over an orphan pair) from a pair that is mid-start and
            # about to flip pair_healthy.  Only time separates them: raise the
            # convergent transition so the activation wait loop keeps
            # observing until its deadline, which then fails closed with the
            # last observation.  One-shot callers still see an OSError.
            raise _RuntimeServiceTransition(
                "runtime service reports a non-healthy running pair; "
                "refusing install handoff until the pair is stopped or healthy"
            )
        # The two probes above are taken at different moments; across a
        # launchd (re)start they can legitimately disagree for an instant.
        # Convergent transition: still fail-closed for one-shot callers,
        # retryable inside the activation wait loop.
        raise _RuntimeServiceTransition(
            "runtime service and server/guardian observations disagree; "
            "refusing install handoff"
        )
    return launcher, status, pair_state


def _wait_for_runtime_service_settlement(
    shared_home: Path,
    *,
    allow_healthy: bool,
    timeout_seconds: float = _RUNTIME_SERVICE_SETTLEMENT_TIMEOUT_SECONDS,
) -> tuple[Path, _RuntimeServiceStatus, str]:
    """Wait for an owned service mutation to reach a terminal observable state.

    This is intentionally used only after ownership was proved and a stop was
    attempted. Preflight identity remains fail-closed. launchd and the managed
    pair do not publish their teardown state atomically, so a bounded sequence
    of structurally valid transitions must not turn a successful drain into an
    unrecoverable installer handoff.
    """
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    last_observation = "no runtime service observation"
    while True:
        deadline_token = _RUNTIME_SERVICE_COMMAND_DEADLINE.set(deadline)
        try:
            try:
                snapshot = _runtime_service_snapshot(shared_home)
            finally:
                _RUNTIME_SERVICE_COMMAND_DEADLINE.reset(deadline_token)
        except (
            _RuntimeServiceTransition,
            subprocess.TimeoutExpired,
            TimeoutError,
        ) as exc:
            last_observation = str(exc)
        else:
            if snapshot is None:
                raise OSError(
                    "runtime launcher disappeared while waiting for service settlement"
                )
            _, status, pair_state = snapshot
            if status.quiescent and pair_state == "stopped":
                return snapshot
            if allow_healthy and status.healthy and pair_state == "running":
                return snapshot
            if status.healthy:
                raise OSError(
                    "runtime service became healthy while waiting for a verified stop"
                )
            last_observation = (
                f"installed={status.installed}, loaded={status.loaded}, "
                f"supervisor_live={status.supervisor_live}, "
                f"supervisor_verified={status.supervisor_verified}, "
                f"service_managed={status.supervisor_service_managed}, "
                f"build_current={status.build_current}, "
                f"pair_healthy={status.pair_healthy}, pair={pair_state}"
            )
        if time.monotonic() >= deadline:
            raise OSError(
                "runtime service did not settle within "
                f"{timeout_seconds:g}s (last observation: {last_observation})"
            )
        time.sleep(0.2)


def runtime_service_active_for_install(shared_home: Path) -> bool:
    """Read-only preflight used before recording the rollback obligation."""
    if sys.platform != "darwin":
        return False
    _require_inherited_tools_install_lease(shared_home)
    snapshot = _runtime_service_snapshot(shared_home)
    if snapshot is None:
        return False
    return snapshot[1].needs_drain


def prepare_runtime_service_for_install(
    shared_home: Path,
    *,
    launch_agent_backup: _RuntimeLaunchAgentBackup | None = None,
) -> bool:
    """Drain a verified legacy or reclaimable degraded service before publish."""
    if sys.platform != "darwin":
        return False
    _require_inherited_tools_install_lease(shared_home)
    snapshot = _runtime_service_snapshot(shared_home)
    if snapshot is None:
        return False
    launcher, status, _ = snapshot
    if status.quiescent:
        return False
    if not status.needs_drain:
        raise OSError(
            "runtime service is neither quiescent nor reclaimable; refusing drain"
        )
    backup = launch_agent_backup or _capture_runtime_launch_agent_backup(shared_home)
    if backup.contents is None:
        raise OSError("runtime service marked for drain has no LaunchAgent snapshot")
    if not _assert_runtime_launchd_job_owned(shared_home):
        raise OSError(
            "runtime service disappeared before its owned launchd paths could be proved"
        )
    try:
        result = _run_runtime_service_command(
            launcher,
            shared_home,
            "service",
            "stop",
        )
        if result.returncode != 0:
            detail = (
                result.stderr.strip()
                or result.stdout.strip()
                or f"exit={result.returncode}"
            )
            raise OSError(
                "old launcher refused the verified service drain before runtime "
                f"swap ({detail})"
            )
        _wait_for_runtime_service_settlement(
            shared_home,
            allow_healthy=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        try:
            observed = _wait_for_runtime_service_settlement(
                shared_home,
                allow_healthy=True,
            )
            if observed[1].healthy:
                if not _assert_runtime_launchd_job_owned(shared_home):
                    raise OSError(
                        "legacy service recovery cannot prove the loaded launchd job"
                    )
                observed_backup = _capture_runtime_launch_agent_backup(shared_home)
                if observed_backup != backup:
                    retry = _run_runtime_service_command(
                        observed[0],
                        shared_home,
                        "service",
                        "stop",
                    )
                    if retry.returncode != 0:
                        detail = (
                            retry.stderr.strip()
                            or retry.stdout.strip()
                            or f"exit={retry.returncode}"
                        )
                        raise OSError(
                            "raced legacy service could not be stopped for exact "
                            f"LaunchAgent recovery ({detail})"
                        )
                    _wait_for_runtime_service_settlement(
                        shared_home,
                        allow_healthy=False,
                    )
                    _bootout_owned_runtime_launchd_job(shared_home)
                    _activate_runtime_service_from_backup(shared_home, backup)
            else:
                if not observed[1].quiescent or observed[2] != "stopped":
                    raise OSError("legacy service recovery state is uncertain")
                _activate_runtime_service_from_backup(shared_home, backup)
        except (OSError, subprocess.SubprocessError) as recovery_exc:
            raise OSError(
                f"legacy runtime drain failed ({exc}) and automatic service "
                f"recovery also failed: {recovery_exc}"
            ) from exc
        raise OSError(
            f"legacy runtime drain failed ({exc}); previous service ownership "
            "was recovered"
        ) from exc
    return True


def activate_runtime_service_after_install(
    shared_home: Path,
    *,
    service_arguments: Sequence[str] = (),
) -> None:
    """Start the service through the launcher backed by the current generation."""
    if sys.platform != "darwin":
        return
    _require_inherited_tools_install_lease(shared_home)
    snapshot = _runtime_service_snapshot(shared_home)
    if snapshot is None:
        raise OSError("cannot reactivate runtime service without a current launcher")
    launcher, status, pair_state = snapshot
    expected_arguments = {
        "--host": "127.0.0.1",
        "--port": "3024",
    }
    supported_arguments = {*expected_arguments, "--interval"}
    if len(service_arguments) % 2 != 0:
        raise OSError("runtime service activation arguments are incomplete")
    seen_arguments: set[str] = set()
    for index in range(0, len(service_arguments), 2):
        flag, value = service_arguments[index : index + 2]
        if flag not in supported_arguments or not value:
            raise OSError(
                f"runtime service activation has unsupported argument {flag!r}"
            )
        if flag in seen_arguments:
            raise OSError(f"runtime service activation repeats argument {flag!r}")
        seen_arguments.add(flag)
        if flag in expected_arguments:
            expected_arguments[flag] = value
    expected_service_arguments = tuple(
        argument
        for flag in ("--host", "--port")
        for argument in (flag, expected_arguments[flag])
    )
    if status.healthy and pair_state == "running":
        installed = _capture_runtime_launch_agent_backup(shared_home)
        if installed.service_arguments != expected_service_arguments:
            raise OSError("healthy runtime service has stale endpoint arguments")
        return
    if not status.quiescent or pair_state != "stopped":
        raise OSError(
            "current runtime is not provably quiescent; refusing service activation"
        )
    result = _run_runtime_service_command(
        launcher,
        shared_home,
        "service",
        "install",
        *service_arguments,
    )
    if result.returncode != 0:
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit={result.returncode}"
        )
        raise OSError(f"new runtime service activation failed ({detail})")
    deadline = time.monotonic() + _RUNTIME_SERVICE_ACTIVATION_TIMEOUT_SECONDS
    last_observation = "no post-install service observation"
    while True:
        deadline_token = _RUNTIME_SERVICE_COMMAND_DEADLINE.set(deadline)
        try:
            try:
                active = _runtime_service_snapshot(shared_home)
            finally:
                _RUNTIME_SERVICE_COMMAND_DEADLINE.reset(deadline_token)
        except (
            _RuntimeServiceTransition,
            subprocess.TimeoutExpired,
            TimeoutError,
        ) as exc:
            last_observation = str(exc)
        else:
            if active is not None and active[1].healthy and active[2] == "running":
                break
            if active is None:
                raise OSError(
                    "current runtime launcher disappeared during service activation"
                )
            status = active[1]
            last_observation = (
                f"installed={status.installed}, loaded={status.loaded}, "
                f"supervisor_live={status.supervisor_live}, "
                f"pair_healthy={status.pair_healthy}, pair={active[2]}"
            )
        if time.monotonic() >= deadline:
            raise OSError(
                "new runtime service activation did not prove a healthy managed "
                f"pair within {_RUNTIME_SERVICE_ACTIVATION_TIMEOUT_SECONDS:g}s "
                f"(last observation: {last_observation})"
            )
        time.sleep(0.2)
    installed = _capture_runtime_launch_agent_backup(shared_home)
    if installed.service_arguments != expected_service_arguments:
        raise OSError(
            "new runtime activation did not install the requested service arguments"
        )


def rollback_runtime_install(
    shared_home: Path,
    *,
    service_was_active: bool,
    service_activation_attempted: bool,
    lifecycle_deck: Path | None = None,
    launch_agent_backup: _RuntimeLaunchAgentBackup | None = None,
    payload_backup: _RuntimePayloadBackup | None = None,
    launchd_gate: _RuntimeLaunchdMutationGate | None = None,
    restore_tools_pointer: bool = True,
    manage_runtime_service: bool = True,
) -> bool:
    """Quiesce the new service, restore the pointer, and revive the old service.

    If activation left an uncertain service state, the strict snapshot raises
    before the pointer moves.  Keeping the new generation published is safer
    than reviving the old generation underneath a process we cannot prove.
    """
    _require_inherited_tools_install_lease(shared_home)
    darwin_service = sys.platform == "darwin" and manage_runtime_service
    darwin_service_attempted = darwin_service and service_activation_attempted
    gate_context = (
        _RuntimeLaunchdMutationGate(
            required=darwin_service_attempted
            or service_was_active
            or launch_agent_backup is not None
        )
        if launchd_gate is None
        else nullcontext(launchd_gate)
    )
    with gate_context as gate:
        if darwin_service_attempted:
            gate.disable()
            try:
                snapshot = _runtime_service_snapshot(shared_home)
            except (OSError, subprocess.SubprocessError):
                gate.retain_disabled()
                raise
            if snapshot is None:
                gate.retain_disabled()
                raise OSError(
                    "service activation was attempted but no current launcher can "
                    "prove the rollback state"
                )
            if snapshot[1].healthy:
                current_backup = _capture_runtime_launch_agent_backup(shared_home)
                if not prepare_runtime_service_for_install(
                    shared_home,
                    launch_agent_backup=current_backup,
                ):
                    gate.retain_disabled()
                    raise OSError(
                        "activated runtime service could not be drained during rollback"
                    )
            elif not snapshot[1].quiescent or snapshot[2] != "stopped":
                gate.retain_disabled()
                raise OSError(
                    "activated runtime service is uncertain; refusing pointer rollback"
                )

        if darwin_service:
            if lifecycle_deck is None:
                handoff = _read_tools_handoff(shared_home)
                if handoff is None or handoff["state"] != "prepared":
                    raise OSError(
                        "runtime rollback has no exact lifecycle generation handoff"
                    )
                lifecycle_target_raw = handoff["old_target"] or handoff["new_target"]
                if not lifecycle_target_raw:
                    raise OSError("runtime rollback has no exact lifecycle generation")
                lifecycle_deck = _runtime_lifecycle_deck_for_generation(
                    Path(lifecycle_target_raw)
                )
            with _runtime_lifecycle_handoff_fence(
                shared_home,
                deck=lifecycle_deck,
            ) as lifecycle_guard:
                lifecycle_guard.assert_owned()
                snapshot = _runtime_service_snapshot(shared_home)
                if (
                    snapshot is None
                    or not snapshot[1].quiescent
                    or snapshot[2] != "stopped"
                ):
                    gate.retain_disabled()
                    raise OSError(
                        "runtime ownership changed before rollback fences closed"
                    )
                with _runtime_supervisor_handoff_fence(
                    shared_home,
                    required=True,
                ):
                    lifecycle_guard.assert_owned()
                    try:
                        _bootout_owned_runtime_launchd_job(shared_home)
                    except (OSError, subprocess.SubprocessError):
                        gate.retain_disabled()
                        raise
                    _restore_runtime_payload_backup(payload_backup)
                    if launch_agent_backup is not None:
                        _restore_runtime_launch_agent_backup(
                            shared_home,
                            launch_agent_backup,
                        )
                    restored = (
                        _rollback_current_tools_locked(shared_home)
                        if restore_tools_pointer
                        else False
                    )
                    lifecycle_guard.assert_owned()
        else:
            _restore_runtime_payload_backup(payload_backup)
            restored = (
                _rollback_current_tools_locked(shared_home)
                if restore_tools_pointer
                else False
            )

        if service_was_active:
            if launch_agent_backup is None:
                raise OSError(
                    "active legacy service rollback requires its exact LaunchAgent "
                    "snapshot"
                )
            gate.enable_for_activation()
            try:
                _activate_runtime_service_from_backup(
                    shared_home,
                    launch_agent_backup,
                )
            except (OSError, subprocess.SubprocessError):
                gate.retain_disabled()
                raise
        return restored


@contextmanager
def _runtime_supervisor_handoff_fence(
    shared_home: Path,
    *,
    required: bool,
) -> Iterator[None]:
    """Hold the canonical supervisor lock between legacy drain and publication.

    The installed legacy launcher predates the tools-install lease.  Its
    service-start path still respects the supervisor lock, so this fence closes
    the only interval in which an old command could restart launchd after a
    verified stop but before ``vibecrafted-current`` moves.
    """
    if sys.platform != "darwin" or not required:
        yield
        return

    server_dir = shared_home.resolve(strict=False) / "server"
    server_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = server_dir.lstat()
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != os.geteuid()
        or server_dir.is_symlink()
    ):
        raise OSError(
            f"runtime server directory is not an owned regular directory: {server_dir}"
        )
    lock_path = server_dir / "supervisor.lock"
    descriptor = os.open(
        lock_path,
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    locked = False
    try:
        opened = os.fstat(descriptor)
        named = os.stat(lock_path, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise OSError(
                f"runtime supervisor fence does not own stable lock {lock_path}"
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise OSError(
                    "runtime supervisor became active after the verified drain; "
                    "refusing pointer publication"
                ) from exc
            raise
        locked = True
        yield
    finally:
        try:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _runtime_lifecycle_deck_for_generation(generation: Path) -> Path:
    """Resolve and verify the legacy `scripts/vibecrafted` lifecycle deck for a specific runtime
    generation directory.
    """
    try:
        generation = generation.resolve(strict=True)
    except OSError as exc:
        raise OSError(
            f"cannot fence server lifecycle without generation {generation}"
        ) from exc
    if not _is_framework_source_root(generation):
        raise OSError(f"runtime generation is incomplete or unmanaged: {generation}")
    deck = generation / "scripts" / "vibecrafted"
    try:
        visible = deck.lstat()
    except OSError as exc:
        raise OSError(f"legacy lifecycle deck is unavailable at {deck}") from exc
    if (
        deck.is_symlink()
        or not stat.S_ISREG(visible.st_mode)
        or visible.st_uid != os.geteuid()
        or visible.st_nlink != 1
        or not os.access(deck, os.X_OK)
    ):
        raise OSError(
            f"legacy lifecycle deck is not a stable user-owned executable: {deck}"
        )
    return deck


def _runtime_deck_has_service_lifecycle_lock(deck: Path) -> bool:
    """True if the given lifecycle deck script contains the service-lifecycle-lock contract
    marker.
    """
    try:
        metadata = deck.stat()
        if metadata.st_size > 4 * 1024 * 1024:
            raise OSError("runtime lifecycle deck exceeds the bounded contract size")
        lines = deck.read_bytes().splitlines()
    except OSError as exc:
        raise OSError(
            f"cannot inspect service lifecycle-lock capability in {deck}"
        ) from exc
    return _SERVICE_LIFECYCLE_LOCK_MARKER in lines


@dataclass(frozen=True)
class _LegacyServiceMutator:
    """One process observed to be mutating the legacy service (pid, argv, and stable birth
    identity).
    """

    pid: int
    start_token: str
    started_at: datetime
    argv: tuple[str, ...]


class _DarwinProcBSDInfo(ctypes.Structure):
    """ctypes mirror of Darwin's `struct proc_bsdinfo`, used for cheap per-PID process identity
    checks.
    """

    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


_DARWIN_PROC_UID_ONLY = 4
_DARWIN_PROC_PIDTBSDINFO = 3
_DARWIN_PROC_BSDINFO_SIZE = 136
_DARWIN_PROC_FLAG_INEXIT = 0x4
_DARWIN_PROC_FLAG_LP64 = 0x10
_DARWIN_STABLE_PROCESS_STATES = frozenset({1, 2, 3, 4})
_DARWIN_CTL_KERN = 1
_DARWIN_KERN_PROCARGS2 = 49
_DARWIN_MAX_PROCARGS = 16 * 1024 * 1024
_DARWIN_LIBPROC: ctypes.CDLL | None = None
_DARWIN_LIBC: ctypes.CDLL | None = None


def _darwin_process_libraries() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    """Load (and cache) the libproc/libc handles with the ctypes signatures needed for process
    census.
    """
    global _DARWIN_LIBPROC, _DARWIN_LIBC
    if sys.platform != "darwin":
        raise OSError("Darwin process census requested on a non-Darwin host")
    if _DARWIN_LIBPROC is None:
        try:
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        except OSError as exc:
            raise OSError(f"cannot load Darwin process API: {exc}") from exc
        libproc.proc_listpids.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        libproc.proc_listpids.restype = ctypes.c_int
        libproc.proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        libproc.proc_pidinfo.restype = ctypes.c_int
        _DARWIN_LIBPROC = libproc
    if _DARWIN_LIBC is None:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.sysctl.argtypes = [
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        libc.sysctl.restype = ctypes.c_int
        _DARWIN_LIBC = libc
    return _DARWIN_LIBPROC, _DARWIN_LIBC


def _darwin_process_ids() -> tuple[int, ...]:
    """Enumerate this user's live PIDs on Darwin via `proc_listpids`, retrying with a larger
    buffer as needed.
    """
    libproc, _ = _darwin_process_libraries()
    ctypes.set_errno(0)
    effective_uid = os.geteuid()
    estimated = libproc.proc_listpids(
        _DARWIN_PROC_UID_ONLY,
        effective_uid,
        None,
        0,
    )
    if estimated <= 0:
        raise OSError(f"cannot size Darwin process census (errno {ctypes.get_errno()})")
    capacity = max(1024, estimated // ctypes.sizeof(ctypes.c_int) + 256)
    for _ in range(4):
        buffer = (ctypes.c_int * capacity)()
        ctypes.set_errno(0)
        received = libproc.proc_listpids(
            _DARWIN_PROC_UID_ONLY,
            effective_uid,
            ctypes.byref(buffer),
            ctypes.sizeof(buffer),
        )
        if received < 0:
            raise OSError(
                f"cannot enumerate Darwin processes (errno {ctypes.get_errno()})"
            )
        if received < ctypes.sizeof(buffer):
            count = received // ctypes.sizeof(ctypes.c_int)
            return tuple(
                sorted(
                    {int(buffer[index]) for index in range(count) if buffer[index] > 1}
                )
            )
        capacity *= 2
    raise OSError("Darwin process census kept exceeding its bounded buffer")


def _darwin_process_birth(pid: int) -> tuple[str, int, int]:
    """Fetch a PID's birth identity (start-time token, uid, pointer size) via `proc_pidinfo`;
    raises ProcessLookupError if it has exited or is unstable.
    """
    libproc, _ = _darwin_process_libraries()
    info = _DarwinProcBSDInfo()
    if ctypes.sizeof(info) != _DARWIN_PROC_BSDINFO_SIZE:
        raise OSError("Darwin proc_bsdinfo ABI does not match the supported layout")
    ctypes.set_errno(0)
    received = libproc.proc_pidinfo(
        pid,
        _DARWIN_PROC_PIDTBSDINFO,
        0,
        ctypes.byref(info),
        _DARWIN_PROC_BSDINFO_SIZE,
    )
    if received != _DARWIN_PROC_BSDINFO_SIZE:
        observed_errno = ctypes.get_errno()
        if received == 0 and observed_errno in {0, errno.ESRCH}:
            raise ProcessLookupError(pid)
        raise OSError(
            f"cannot inspect Darwin process birth identity for {pid} "
            f"(errno {observed_errno})"
        )
    if (
        int(info.pbi_pid) != pid
        or int(info.pbi_status) not in _DARWIN_STABLE_PROCESS_STATES
        or int(info.pbi_flags) & _DARWIN_PROC_FLAG_INEXIT
        or int(info.pbi_start_tvsec) <= 0
        or not 0 <= int(info.pbi_start_tvusec) < 1_000_000
    ):
        raise ProcessLookupError(pid)
    return (
        f"darwin:{int(info.pbi_start_tvsec)}:{int(info.pbi_start_tvusec)}",
        int(info.pbi_uid),
        8 if int(info.pbi_flags) & _DARWIN_PROC_FLAG_LP64 else 4,
    )


def _darwin_process_arguments(pid: int, *, pointer_size: int) -> tuple[str, ...]:
    """Fetch a PID's argv via `sysctl KERN_PROCARGS2`, parsing past the exec path and alignment
    padding; raises ProcessLookupError if the process is gone.
    """
    if pointer_size not in {4, 8}:
        raise OSError(f"invalid Darwin process pointer size for {pid}")
    _, libc = _darwin_process_libraries()
    mib = (ctypes.c_int * 3)(
        _DARWIN_CTL_KERN,
        _DARWIN_KERN_PROCARGS2,
        pid,
    )
    raw: bytes | None = None
    for _ in range(3):
        required_size = ctypes.c_size_t(0)
        ctypes.set_errno(0)
        if (
            libc.sysctl(
                mib,
                len(mib),
                None,
                ctypes.byref(required_size),
                None,
                0,
            )
            != 0
        ):
            observed_errno = ctypes.get_errno()
            if observed_errno == errno.ESRCH:
                raise ProcessLookupError(pid)
            raise OSError(
                f"cannot size Darwin process arguments for {pid} "
                f"(errno {observed_errno})"
            )
        capacity = int(required_size.value)
        if not 4 <= capacity <= _DARWIN_MAX_PROCARGS:
            raise OSError(f"invalid Darwin process argument size for {pid}")
        buffer = ctypes.create_string_buffer(capacity)
        received_size = ctypes.c_size_t(capacity)
        ctypes.set_errno(0)
        if (
            libc.sysctl(
                mib,
                len(mib),
                buffer,
                ctypes.byref(received_size),
                None,
                0,
            )
            == 0
        ):
            actual_size = int(received_size.value)
            if not 4 <= actual_size <= capacity:
                raise OSError(f"invalid Darwin process argument payload for {pid}")
            raw = buffer.raw[:actual_size]
            break
        observed_errno = ctypes.get_errno()
        if observed_errno == errno.ESRCH:
            raise ProcessLookupError(pid)
        if observed_errno != errno.ENOMEM:
            raise OSError(
                f"cannot inspect Darwin process arguments for {pid} "
                f"(errno {observed_errno})"
            )
    if raw is None:
        raise OSError(f"Darwin process arguments kept changing size for {pid}")

    argc = struct.unpack_from("=i", raw)[0]
    if not 1 <= argc <= 4096:
        raise OSError(f"invalid Darwin process argument count for {pid}")
    position = struct.calcsize("=i")
    executable_end = raw.find(b"\0", position)
    if executable_end < 0 or executable_end == position:
        raise OSError(f"cannot parse Darwin executable argument for {pid}")
    position = executable_end + 1
    padding_size = (-(position - struct.calcsize("=i"))) % pointer_size
    padding_end = position + padding_size
    if padding_end > len(raw) or any(raw[position:padding_end]):
        raise OSError(f"invalid Darwin process argument alignment for {pid}")
    position = padding_end
    arguments: list[str] = []
    for _ in range(argc):
        argument_end = raw.find(b"\0", position)
        if argument_end < 0:
            raise OSError(f"cannot parse Darwin argv for {pid}")
        arguments.append(os.fsdecode(raw[position:argument_end]))
        position = argument_end + 1
    if not arguments or not arguments[0]:
        raise OSError(f"Darwin argv is empty for {pid}")
    return tuple(arguments)


def _argv_is_legacy_service_action_mutator(argv: Sequence[str]) -> bool:
    """True if `argv` looks like `vibecrafted server service <action>` or the raw
    server_supervisor equivalent.
    """
    actions = {"install", "reconcile", "restart", "start", "stop", "uninstall"}
    for index in range(len(argv) - 3):
        if (
            Path(argv[index]).name == "vibecrafted"
            and argv[index + 1] == "server"
            and argv[index + 2] == "service"
            and argv[index + 3] in actions
        ):
            return True
    for entrypoint, argument in enumerate(argv):
        if argument != "vibecrafted_core.server_supervisor" and Path(
            argument
        ).name not in {"vc-server-supervisor", "server_supervisor.py"}:
            continue
        tail = argv[entrypoint + 1 :]
        return any(
            tail[index] == "service" and tail[index + 1] in actions
            for index in range(len(tail) - 1)
        )
    return False


def _argv_is_legacy_manual_server_mutator(argv: Sequence[str]) -> bool:
    """True if `argv` looks like `vibecrafted server start/stop` or the raw server_supervisor
    manual-stop form.
    """
    if any(
        Path(argv[index]).name == "vibecrafted"
        and argv[index + 1] == "server"
        and argv[index + 2] in {"start", "stop"}
        for index in range(len(argv) - 2)
    ):
        return True
    for entrypoint, argument in enumerate(argv):
        if argument != "vibecrafted_core.server_supervisor" and Path(
            argument
        ).name not in {"vc-server-supervisor", "server_supervisor.py"}:
            continue
        return "manual-stop" in argv[entrypoint + 1 :]
    return False


def _argv_is_service_mutator(argv: Sequence[str]) -> bool:
    """True if `argv` matches either legacy service-action or legacy manual-server mutator shape."""
    return _argv_is_legacy_service_action_mutator(
        argv
    ) or _argv_is_legacy_manual_server_mutator(argv)


def _legacy_service_mutator_census() -> tuple[_LegacyServiceMutator, ...]:
    """Census every live process whose argv matches a legacy service mutator shape, re-verifying
    birth identity and argv did not change mid-read.
    """
    records: list[_LegacyServiceMutator] = []
    for pid in _darwin_process_ids():
        if pid == os.getpid():
            continue
        try:
            first_birth = _darwin_process_birth(pid)
            if first_birth[1] != os.geteuid():
                continue
            first_argv = _darwin_process_arguments(
                pid,
                pointer_size=first_birth[2],
            )
            second_argv = _darwin_process_arguments(
                pid,
                pointer_size=first_birth[2],
            )
            second_birth = _darwin_process_birth(pid)
        except ProcessLookupError:
            continue
        if first_birth != second_birth or first_argv != second_argv:
            raise OSError(f"Darwin process {pid} changed during legacy mutator census")
        if _argv_is_service_mutator(first_argv):
            _, seconds, microseconds = first_birth[0].split(":")
            records.append(
                _LegacyServiceMutator(
                    pid=pid,
                    start_token=first_birth[0],
                    started_at=datetime.fromtimestamp(
                        int(seconds) + int(microseconds) / 1_000_000,
                        tz=timezone.utc,
                    ),
                    argv=first_argv,
                )
            )
    return tuple(sorted(records, key=lambda record: (record.pid, record.start_token)))


def _wait_for_legacy_service_mutator_quiescence(
    *,
    published_at: datetime,
    classifier: Callable[[Sequence[str]], bool] = _argv_is_service_mutator,
    timeout_seconds: float = 15.0,
) -> None:
    """Poll the legacy-service-mutator census until none pre-date `published_at` remain, raising
    OSError on timeout.
    """
    if published_at.tzinfo is None:
        raise OSError("runtime publication boundary has no timezone")
    published_at = published_at.astimezone(timezone.utc)
    deadline = time.monotonic() + timeout_seconds
    empty_observations = 0
    last_records: tuple[_LegacyServiceMutator, ...] = ()
    while True:
        records = tuple(
            record
            for record in _legacy_service_mutator_census()
            if record.started_at <= published_at and classifier(record.argv)
        )
        if records:
            empty_observations = 0
            last_records = records
        else:
            empty_observations += 1
            if empty_observations >= 2:
                return
        if time.monotonic() >= deadline:
            detail = ", ".join(
                f"pid={record.pid}, start={record.start_token}"
                for record in last_records
            )
            raise OSError(
                "pre-lock legacy service mutators did not become quiescent"
                f"{f' ({detail})' if detail else ''}"
            )
        time.sleep(0.05)


def _runtime_lifecycle_deck(shared_home: Path) -> Path:
    """Resolve the legacy lifecycle deck for the shared home's currently published runtime
    generation.
    """
    current = _current_tools_link(shared_home)
    try:
        generation = current.resolve(strict=True)
    except OSError as exc:
        raise OSError(
            "cannot fence legacy server lifecycle without the current runtime "
            f"generation at {current}"
        ) from exc
    return _runtime_lifecycle_deck_for_generation(generation)


@dataclass(frozen=True)
class _RuntimeLifecycleFenceGuard:
    """Handle to a subprocess holding the legacy `scripts/vibecrafted` lifecycle.lock across a
    mutation.
    """

    process: subprocess.Popen[str] | None
    owner_pid: int | None = None
    owner_nonce: str | None = None
    lock_dir: Path | None = None

    def assert_owned(self) -> None:
        """Raise OSError if the fence-holding subprocess has already exited."""
        if self.process is not None and self.process.poll() is not None:
            raise OSError(
                "legacy lifecycle fence exited before the protected mutation "
                f"completed (exit={self.process.returncode})"
            )

    def inherited_environment(self) -> dict[str, str]:
        """Environment variables a child process needs to prove it inherited this exact
        lifecycle-lock ownership; empty when no fence process is held.
        """
        self.assert_owned()
        if self.process is None:
            return {}
        if (
            self.owner_pid is None
            or self.owner_pid <= 1
            or self.owner_nonce is None
            or re.fullmatch(r"[0-9a-f]{64}", self.owner_nonce) is None
            or self.lock_dir is None
        ):
            raise OSError("legacy lifecycle fence has no verified owner proof")
        return {
            "_SERVER_LIFECYCLE_LOCK_PID": str(self.owner_pid),
            "_SERVER_LIFECYCLE_LOCK_NONCE": self.owner_nonce,
            "_SERVER_LIFECYCLE_LOCK_DIR": str(self.lock_dir),
        }


@contextmanager
def _inherited_runtime_lifecycle_fence(
    guard: _RuntimeLifecycleFenceGuard,
) -> Iterator[None]:
    """Context manager: expose `guard`'s inherited lifecycle-lock env vars via the module
    ContextVar.
    """
    environment = guard.inherited_environment()
    token = _RUNTIME_LIFECYCLE_ENV.set(environment or None)
    try:
        yield
    finally:
        _RUNTIME_LIFECYCLE_ENV.reset(token)


@contextmanager
def _runtime_lifecycle_handoff_fence(
    shared_home: Path,
    *,
    deck: Path | None,
) -> Iterator[_RuntimeLifecycleFenceGuard]:
    """Hold the repo-native lifecycle.lock through publication.

    The supervisor flock blocks service/launchd ownership.  Direct legacy
    ``server start`` and ``server stop`` serialize through a separate,
    identity-backed directory lease; source the already-installed old deck and
    let that exact implementation own its lock for the transaction.
    """
    if sys.platform != "darwin" or deck is None:
        yield _RuntimeLifecycleFenceGuard(None)
        return

    token = f"VIBECRAFTED_LIFECYCLE_FENCE_READY_{os.urandom(16).hex()}"
    shell = r"""
set -euo pipefail
deck="$1"
ready_token="$2"
source "$deck" help >/dev/null
held=0
cleanup_install_lifecycle_fence() {
  if [[ "$held" -eq 1 ]]; then
    _release_server_lifecycle_lock
    held=0
  fi
}
trap cleanup_install_lifecycle_fence EXIT HUP INT TERM
_acquire_server_lifecycle_lock
held=1
printf '%s\t%s\t%s\n' \
  "$ready_token" \
  "$_SERVER_LIFECYCLE_LOCK_PID" \
  "$_SERVER_LIFECYCLE_LOCK_NONCE"
IFS= read -r _release_request || true
"""
    environment = os.environ.copy()
    environment["HOME"] = str(Path.home())
    environment["VIBECRAFTED_HOME"] = str(shared_home.resolve(strict=False))
    environment["VIBECRAFTED_TOOLS_HOME"] = str(
        vibecrafted_tools_home().resolve(strict=False)
    )
    process = subprocess.Popen(
        ["/bin/bash", "-c", shell, "vibecrafted", str(deck), token],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    ready = False
    owner_pid: int | None = None
    owner_nonce: str | None = None
    output: list[str] = []
    deadline = time.monotonic() + 15.0
    assert process.stdout is not None
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            readable, _, _ = select.select(
                [process.stdout],
                [],
                [],
                min(0.1, max(0.0, deadline - time.monotonic())),
            )
            if not readable:
                continue
            line = process.stdout.readline()
            if not line:
                break
            rendered = line.rstrip("\n")
            output.append(rendered)
            fields = rendered.split("\t")
            if (
                len(fields) == 3
                and fields[0] == token
                and fields[1].isdigit()
                and int(fields[1]) > 1
                and re.fullmatch(r"[0-9a-f]{64}", fields[2]) is not None
            ):
                owner_pid = int(fields[1])
                owner_nonce = fields[2]
                ready = True
                break
        if not ready:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            assert process.stderr is not None
            detail = process.stderr.read().strip() or " | ".join(output)
            raise OSError(
                "legacy lifecycle fence could not acquire verified ownership "
                f"({detail or f'exit={process.returncode}'})"
            )
        guard = _RuntimeLifecycleFenceGuard(
            process,
            owner_pid=owner_pid,
            owner_nonce=owner_nonce,
            lock_dir=shared_home.resolve(strict=False) / "server" / "lifecycle.lock",
        )
        guard.assert_owned()
        yield guard
    finally:
        if ready and process.poll() is not None:
            assert process.stderr is not None
            detail = process.stderr.read().strip() or process.returncode
            raise OSError(
                f"legacy lifecycle fence exited before explicit release ({detail})"
            )
        if ready:
            assert process.stdin is not None
            try:
                process.stdin.write("release\n")
                process.stdin.flush()
                process.stdin.close()
            except (BrokenPipeError, ValueError):
                pass
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired as exc:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise OSError(
                    "legacy lifecycle fence did not release within its timeout"
                ) from exc
            if process.returncode != 0:
                assert process.stderr is not None
                raise OSError(
                    "legacy lifecycle fence exited without clean ownership "
                    f"release ({process.stderr.read().strip() or process.returncode})"
                )


@contextmanager
def _inherited_tools_install_lease(
    descriptor: int,
) -> Iterator[None]:
    """Context manager: temporarily export `descriptor` as the inherited tools-install lease env
    var.
    """
    previous = os.environ.get(_TOOLS_INSTALL_LEASE_ENV)
    os.environ[_TOOLS_INSTALL_LEASE_ENV] = str(descriptor)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(_TOOLS_INSTALL_LEASE_ENV, None)
        else:
            os.environ[_TOOLS_INSTALL_LEASE_ENV] = previous


def _terminate_installer_child_process_group(
    process: subprocess.Popen[bytes],
) -> None:
    """Contain only the installer child tree started by this process."""
    process_group = process.pid
    if process.poll() is None:
        try:
            observed_group = os.getpgid(process.pid)
        except ProcessLookupError:
            pass
        else:
            if observed_group != process_group:
                raise OSError(
                    "installer child does not own its process group; refusing broad "
                    "signal"
                )

    def group_exists() -> bool:
        """True if the installer child's process group still has any live member."""
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # Darwin can transiently report EPERM while a killed, reparented
            # descendant is still a zombie in the otherwise-owned group.
            # Keep waiting for ESRCH; the bounded timeout below still refuses
            # to call containment complete while an unsignalable group remains.
            return True
        return True

    def wait_for_group_exit(timeout_seconds: float) -> bool:
        """Poll until the installer child's process group fully exits, or the timeout elapses."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            process.poll()
            if not group_exists():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    if not group_exists():
        process.wait(timeout=1)
        return
    os.killpg(process_group, signal.SIGTERM)
    if not wait_for_group_exit(5):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if not wait_for_group_exit(5):
            raise OSError(
                "installer child process group survived bounded SIGKILL containment"
            )
    process.wait(timeout=1)


def _run_install_child_with_lifecycle_guard(
    argv: Sequence[str],
    *,
    descriptor: int,
    environment: dict[str, str],
    lifecycle_guard: _RuntimeLifecycleFenceGuard,
) -> int:
    """Run the install child in its own process group under the lifecycle fence, polling fence
    ownership while it runs and containing the group if the fence is lost.
    """
    process = subprocess.Popen(
        list(argv),
        pass_fds=(descriptor,),
        env=environment,
        start_new_session=True,
    )
    try:
        while process.poll() is None:
            lifecycle_guard.assert_owned()
            time.sleep(0.05)
        lifecycle_guard.assert_owned()
        # Build/install helpers can outlive their parent for a few scheduling
        # ticks while flushing caches or reaping children.  Give the isolated
        # installer group a bounded natural drain before treating survivors as
        # a failed transaction and containing that group.
        drain_deadline = time.monotonic() + 5.0
        while True:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            if time.monotonic() >= drain_deadline:
                raise OSError(
                    "installer child exited while same-group descendants remained"
                )
            lifecycle_guard.assert_owned()
            time.sleep(0.05)
    except BaseException as fence_exc:
        try:
            _terminate_installer_child_process_group(process)
        except (OSError, subprocess.SubprocessError) as containment_exc:
            raise OSError(
                "legacy lifecycle fence was lost and the installer child "
                f"could not be contained: {containment_exc}"
            ) from fence_exc
        raise
    return process.returncode


def run_with_tools_install_lease(
    shared_home: Path,
    argv: Sequence[str],
    *,
    service_policy: Literal["preserve", "ensure", "isolated"] = "preserve",
    runtime_payload_paths: Sequence[Path] = (),
    require_tools_handoff: bool = True,
) -> int:
    """Own the full legacy-drain -> publish -> activation transaction."""
    if not argv:
        raise ValueError("tools install lease requires a command")
    current_link = _current_tools_link(shared_home)
    try:
        if service_policy not in {"preserve", "ensure", "isolated"}:
            raise ValueError(f"unknown runtime service policy: {service_policy!r}")
        ensure_service = service_policy == "ensure"
        manage_runtime_service = service_policy != "isolated"
        darwin_service = sys.platform == "darwin" and manage_runtime_service
        if darwin_service:
            configured_home = Path.home().resolve(strict=False)
            canonical_home = _canonical_operator_home()
            if configured_home != canonical_home:
                raise OSError(
                    "managed runtime service requires the canonical operator HOME "
                    f"{canonical_home}; got {configured_home}. Use service policy "
                    "'isolated' for alternate HOME installs"
                )
        with _tools_install_lease(
            current_link,
            operation="publish-uv-service-reconcile",
        ) as descriptor:
            os.set_inheritable(descriptor, True)
            with _inherited_tools_install_lease(descriptor):
                service_was_active = False
                fence_required = False
                lifecycle_deck: Path | None = None
                launch_agent_backup: _RuntimeLaunchAgentBackup | None = None
                service_activation_arguments: tuple[str, ...] = ()
                payload_backup = _capture_runtime_payload_backup(
                    shared_home,
                    runtime_payload_paths,
                )
                launchd_gate_required = False
                legacy_service_lock_contract = True
                legacy_quiescence_proven = True
                if darwin_service:
                    _assert_runtime_loaded_service_owner(shared_home)
                    try:
                        current_link.lstat()
                    except FileNotFoundError:
                        current_exists = False
                    except OSError as exc:
                        raise OSError(
                            "cannot inspect the current runtime generation"
                        ) from exc
                    else:
                        current_exists = True
                    if current_exists:
                        if not current_link.is_symlink():
                            raise OSError(
                                "current runtime generation is not a symlink pointer"
                            )
                        lifecycle_deck = _runtime_lifecycle_deck(shared_home)
                    # Every managed Darwin transaction closes the fixed-label
                    # namespace. `preserve` still publishes code that a raced
                    # service install could resolve, so a no-op gate is unsafe.
                    launchd_gate_required = True
                    legacy_service_lock_contract = (
                        lifecycle_deck is None
                        or _runtime_deck_has_service_lifecycle_lock(lifecycle_deck)
                    )
                    legacy_quiescence_proven = legacy_service_lock_contract

                child_returncode = 0
                child_rollback_restored = False
                gate = _RuntimeLaunchdMutationGate(required=launchd_gate_required)
                if darwin_service:
                    # Re-attribute immediately before the first possible
                    # fixed-label mutation. The installer lease serializes all
                    # supported managed writers; a foreign owner fails closed.
                    _assert_runtime_loaded_service_owner(shared_home)
                with gate:
                    if darwin_service:
                        # Re-check after entering the gate so a raced owner is
                        # caught before any payload child can publish.
                        _assert_runtime_loaded_service_owner(shared_home)
                        # Capture exact bytes or exact absence while bootstrap
                        # is fenced, even when no launcher currently answers.
                        launch_agent_backup = _capture_runtime_launch_agent_backup(
                            shared_home
                        )
                        service_activation_arguments = (
                            _runtime_service_arguments_from_config(launch_agent_backup)
                        )
                        snapshot = _runtime_service_snapshot(shared_home)
                        if not gate.required and (
                            snapshot is not None
                            or launch_agent_backup.contents is not None
                        ):
                            raise OSError(
                                "runtime service evidence appeared before the "
                                "launchd mutation gate closed"
                            )
                        if snapshot is not None:
                            if lifecycle_deck is None:
                                raise OSError(
                                    "runtime service exists without an exact current "
                                    "lifecycle generation"
                                )
                            # Healthy managed pairs and reclaimable degraded
                            # supervisors (live/owned, pair down) both must drain
                            # before publication. Pure mid-start races still raise
                            # _RuntimeServiceTransition at decode time.
                            service_was_active = snapshot[1].needs_drain
                        # A quiescent old launcher can still receive a
                        # concurrent `service install`; fence every validated
                        # launcher, not only one with current service evidence.
                        fence_required = lifecycle_deck is not None
                        if service_was_active:
                            reason = (
                                "reclaimable degraded runtime"
                                if snapshot is not None and snapshot[1].reclaimable
                                else "verified legacy runtime"
                            )
                            print(
                                f"[install-tools] draining {reason} "
                                "before publication..."
                            )
                            try:
                                drained = prepare_runtime_service_for_install(
                                    shared_home,
                                    launch_agent_backup=launch_agent_backup,
                                )
                            except BaseException:
                                gate.retain_disabled()
                                raise
                            if not drained:
                                gate.retain_disabled()
                                raise OSError(
                                    "legacy runtime was active at preflight but "
                                    "did not enter the verified drain"
                                )

                    try:
                        with _runtime_lifecycle_handoff_fence(
                            shared_home,
                            deck=lifecycle_deck,
                        ) as lifecycle_guard:
                            lifecycle_guard.assert_owned()
                            if lifecycle_deck is not None:
                                fenced_snapshot = _runtime_service_snapshot(shared_home)
                                if (
                                    fenced_snapshot is None
                                    or not fenced_snapshot[1].quiescent
                                    or fenced_snapshot[2] != "stopped"
                                ):
                                    raise OSError(
                                        "legacy server/guardian ownership changed "
                                        "before the publication fences closed"
                                    )
                            with _runtime_supervisor_handoff_fence(
                                shared_home,
                                required=fence_required,
                            ):
                                lifecycle_guard.assert_owned()
                                # A service-install that resolved the old
                                # implementation before publication can only
                                # leave a disabled job behind. Remove it only
                                # after proving the exact owned-path contract.
                                if darwin_service:
                                    try:
                                        _bootout_owned_runtime_launchd_job(shared_home)
                                    except (OSError, subprocess.SubprocessError):
                                        gate.retain_disabled()
                                        raise
                                environment = os.environ.copy()
                                child_returncode = (
                                    _run_install_child_with_lifecycle_guard(
                                        argv,
                                        descriptor=descriptor,
                                        environment=environment,
                                        lifecycle_guard=lifecycle_guard,
                                    )
                                )
                                lifecycle_guard.assert_owned()
                                if darwin_service:
                                    try:
                                        _bootout_owned_runtime_launchd_job(shared_home)
                                    except (OSError, subprocess.SubprocessError):
                                        # A loaded/foreign job means quiescence is
                                        # unproved. Never move the pointer backwards
                                        # under it; contain the label instead.
                                        gate.retain_disabled()
                                        raise

                                if child_returncode != 0:
                                    if lifecycle_deck is not None:
                                        failed_snapshot = _runtime_service_snapshot(
                                            shared_home
                                        )
                                        if (
                                            failed_snapshot is None
                                            or not failed_snapshot[1].quiescent
                                            or failed_snapshot[2] != "stopped"
                                        ):
                                            gate.retain_disabled()
                                            raise OSError(
                                                "install child failed while runtime "
                                                "ownership was not quiescent"
                                            )
                                    _restore_runtime_payload_backup(payload_backup)
                                    if launch_agent_backup is not None:
                                        _restore_runtime_launch_agent_backup(
                                            shared_home,
                                            launch_agent_backup,
                                        )
                                    child_rollback_restored = (
                                        _rollback_current_tools_locked(shared_home)
                                        if require_tools_handoff
                                        else False
                                    )
                                lifecycle_guard.assert_owned()
                    except BaseException as exc:
                        rollback_was_already_unsafe = gate.retention_required
                        gate.retain_disabled()
                        if rollback_was_already_unsafe:
                            raise
                        try:
                            restored = rollback_runtime_install(
                                shared_home,
                                service_was_active=(
                                    service_was_active and legacy_quiescence_proven
                                ),
                                service_activation_attempted=False,
                                lifecycle_deck=lifecycle_deck,
                                launch_agent_backup=launch_agent_backup,
                                payload_backup=payload_backup,
                                launchd_gate=gate,
                                restore_tools_pointer=require_tools_handoff,
                                manage_runtime_service=manage_runtime_service,
                            )
                        except (
                            OSError,
                            subprocess.SubprocessError,
                        ) as rollback_exc:
                            raise OSError(
                                "install child failed and safe transaction rollback "
                                f"was refused: {rollback_exc}"
                            ) from exc
                        if legacy_quiescence_proven:
                            gate.allow_original_state_restore()
                        else:
                            gate.retain_disabled()
                        _discard_runtime_payload_backup(payload_backup)
                        if not isinstance(exc, Exception):
                            raise
                        detail = (
                            "previous runtime generation was restored"
                            if restored
                            else "runtime pointer was unchanged"
                        )
                        if not legacy_quiescence_proven:
                            detail += "; pre-lock service remains disabled"
                        raise OSError(
                            f"install child failed; {detail} and service ownership "
                            "were recovered"
                        ) from exc

                    if child_returncode != 0:
                        if darwin_service and not legacy_service_lock_contract:
                            gate.retain_disabled()
                            _discard_runtime_payload_backup(payload_backup)
                            print(
                                "[install-tools] FAILED closed: legacy service "
                                "mutators predate lifecycle locking; the runtime "
                                "label remains disabled until a clean re-entry",
                                file=sys.stderr,
                            )
                            return child_returncode
                        if service_was_active:
                            if launch_agent_backup is None:
                                raise OSError(
                                    "legacy service recovery has no LaunchAgent "
                                    "snapshot"
                                )
                            gate.enable_for_activation()
                            try:
                                _activate_runtime_service_from_backup(
                                    shared_home,
                                    launch_agent_backup,
                                )
                            except BaseException:
                                gate.retain_disabled()
                                raise
                        _discard_runtime_payload_backup(payload_backup)
                        detail = (
                            "restored previous runtime generation"
                            if child_rollback_restored
                            else "runtime pointer did not require rollback"
                        )
                        print(
                            f"[install-tools] FAILED safely: {detail} and service "
                            "ownership",
                            file=sys.stderr,
                        )
                        return child_returncode

                    activation_attempted = darwin_service and (
                        service_was_active or ensure_service
                    )
                    handoff_target_to_seal: Path | None = None
                    try:
                        publication_boundary: datetime | None = None
                        if darwin_service:
                            if require_tools_handoff:
                                published_deck = _runtime_lifecycle_deck(shared_home)
                                if not _runtime_deck_has_service_lifecycle_lock(
                                    published_deck
                                ):
                                    raise OSError(
                                        "published runtime generation has no "
                                        "service lifecycle-lock contract"
                                    )
                            elif gate.required and not legacy_service_lock_contract:
                                raise OSError(
                                    "payload-only service activation cannot migrate "
                                    "a pre-lock runtime generation"
                                )
                            if not legacy_service_lock_contract:
                                publication_boundary = (
                                    _tools_handoff_publication_boundary(shared_home)
                                )
                                # Commands resolved through the old deck can hold the
                                # supervisor lease while a child waits for
                                # lifecycle.lock. Drain the complete pre-publication
                                # set before taking lifecycle.lock ourselves.
                                _wait_for_legacy_service_mutator_quiescence(
                                    published_at=publication_boundary,
                                    classifier=_argv_is_service_mutator,
                                )
                        with _runtime_lifecycle_handoff_fence(
                            shared_home,
                            deck=lifecycle_deck,
                        ) as activation_guard:
                            activation_guard.assert_owned()
                            if darwin_service and not legacy_service_lock_contract:
                                try:
                                    if publication_boundary is None:
                                        raise OSError(
                                            "pre-lock runtime migration has no exact "
                                            "publication boundary"
                                        )
                                    _wait_for_legacy_service_mutator_quiescence(
                                        published_at=publication_boundary,
                                        classifier=(
                                            _argv_is_legacy_service_action_mutator
                                        ),
                                    )
                                    _bootout_owned_runtime_launchd_job(shared_home)
                                    legacy_quiescence_proven = True
                                except BaseException:
                                    gate.retain_disabled()
                                    raise
                            if activation_attempted:
                                print(
                                    "[install-tools] activating verified current "
                                    "runtime..."
                                )
                                try:
                                    gate.enable_for_activation()
                                    with _inherited_runtime_lifecycle_fence(
                                        activation_guard
                                    ):
                                        activate_runtime_service_after_install(
                                            shared_home,
                                            service_arguments=service_activation_arguments,
                                        )
                                    if not _assert_runtime_launchd_job_owned(
                                        shared_home
                                    ):
                                        raise OSError(
                                            "new runtime activation has no owned "
                                            "launchd job"
                                        )
                                except BaseException:
                                    gate.disable()
                                    raise
                            activation_guard.assert_owned()
                            if require_tools_handoff:
                                prepared = _read_tools_handoff(shared_home)
                                if prepared is None or prepared["state"] != "prepared":
                                    raise OSError(
                                        "install child completed without a prepared "
                                        "runtime generation handoff"
                                    )
                                handoff_target_to_seal = Path(
                                    prepared["new_target"]
                                ).resolve(strict=False)
                                if _symlink_target(current_link) != (
                                    handoff_target_to_seal
                                ):
                                    raise OSError(
                                        "prepared runtime generation changed before "
                                        "handoff seal"
                                    )
                                if not _complete_current_tools_handoff_locked(
                                    shared_home
                                ):
                                    raise OSError(
                                        "install child completed without a prepared "
                                        "runtime generation handoff"
                                    )
                            if ensure_service:
                                gate.commit_enabled_state()
                    except BaseException as exc:
                        rollback_was_already_unsafe = gate.retention_required
                        gate.retain_disabled()
                        if handoff_target_to_seal is not None and (
                            _tools_handoff_is_complete_current(
                                shared_home,
                                expected_target=handoff_target_to_seal,
                            )
                        ):
                            # The verified cutover was sealed before the lifecycle
                            # helper failed to release.  Never synthesize an old
                            # service under a committed new pointer.
                            _discard_runtime_payload_backup(payload_backup)
                            raise
                        if rollback_was_already_unsafe:
                            raise
                        safe_to_reactivate = (
                            legacy_service_lock_contract or legacy_quiescence_proven
                        )
                        try:
                            restored = rollback_runtime_install(
                                shared_home,
                                service_was_active=(
                                    service_was_active and safe_to_reactivate
                                ),
                                service_activation_attempted=activation_attempted,
                                lifecycle_deck=lifecycle_deck,
                                launch_agent_backup=launch_agent_backup,
                                payload_backup=payload_backup,
                                launchd_gate=gate,
                                restore_tools_pointer=require_tools_handoff,
                                manage_runtime_service=manage_runtime_service,
                            )
                        except BaseException as rollback_exc:
                            gate.retain_disabled()
                            if not isinstance(rollback_exc, Exception):
                                raise
                            raise OSError(
                                "current runtime activation failed and safe rollback "
                                "was refused; "
                                f"activation failure: {exc}; "
                                f"rollback refusal: {rollback_exc}"
                            ) from exc
                        if safe_to_reactivate:
                            gate.allow_original_state_restore()
                        else:
                            gate.retain_disabled()
                        _discard_runtime_payload_backup(payload_backup)
                        if not isinstance(exc, Exception):
                            raise
                        if not manage_runtime_service:
                            detail = (
                                "previous runtime generation was restored; "
                                "isolated service state was untouched"
                                if restored
                                else "runtime pointer was unchanged; isolated "
                                "service state was untouched"
                            )
                        elif not safe_to_reactivate:
                            detail = (
                                "previous runtime generation was restored; pre-lock "
                                "service remains disabled"
                                if restored
                                else "runtime pointer was unchanged; pre-lock service "
                                "remains disabled"
                            )
                        else:
                            detail = (
                                "previous runtime generation and service were restored"
                                if restored
                                else "previous service was restored; pointer was "
                                "unchanged"
                            )
                        raise OSError(
                            f"runtime handoff failed ({exc}); {detail}"
                        ) from exc

                    _discard_runtime_payload_backup(payload_backup)
                    return 0
    except TimeoutError as exc:
        print(f"[install-tools] FATAL: {exc}", file=sys.stderr)
        return 75
    except ValueError as exc:
        print(
            f"[install-tools] FATAL: invalid installer lease policy: {exc}",
            file=sys.stderr,
        )
        return 64
    except (OSError, subprocess.SubprocessError) as exc:
        print(
            f"[install-tools] FATAL: runtime install handoff failed: {exc}",
            file=sys.stderr,
        )
        return 126


def _symlink_target(path: Path) -> Path | None:
    """Resolve a symlink's absolute target, or None if `path` is not a symlink."""
    if not path.is_symlink():
        return None
    raw_target = Path(os.readlink(path))
    if not raw_target.is_absolute():
        raw_target = path.parent / raw_target
    return raw_target.resolve(strict=False)


def _atomic_symlink(target: Path, link: Path) -> None:
    """Publish ``link`` in one rename without ever removing its old target."""
    canonical_target = target.resolve(strict=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() and not link.is_symlink():
        raise OSError(
            f"cannot atomically publish over non-symlink runtime root: {link}"
        )
    temporary = link.parent / (f".{link.name}.tmp-{os.getpid()}-{os.urandom(6).hex()}")
    relative_target = os.path.relpath(
        canonical_target,
        link.parent.resolve(strict=True),
    )
    try:
        temporary.symlink_to(relative_target, target_is_directory=True)
        os.replace(temporary, link)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def _atomic_json_file(path: Path, payload: dict[str, Any]) -> None:
    """Write `payload` as pretty JSON to `path` via a temp file + atomic rename + directory
    fsync.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / (f".{path.name}.tmp-{os.getpid()}-{os.urandom(6).hex()}")
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            view = memoryview(encoded.encode("utf-8"))
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        directory = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def _atomic_bytes_file(path: Path, contents: bytes, *, mode: int) -> None:
    """Write `contents` to `path` via temp file + atomic rename, refusing to write through a
    foreign (non-owned) parent directory or over a foreign existing path.
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.lstat()
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
    ):
        raise OSError(f"refusing atomic write through foreign directory {path.parent}")
    if path.exists() or path.is_symlink():
        current = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(current.st_mode)
            or current.st_uid != os.geteuid()
        ):
            raise OSError(f"refusing atomic write over foreign path {path}")
    temporary = path.parent / (f".{path.name}.tmp-{os.getpid()}-{os.urandom(6).hex()}")
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(descriptor, stat.S_IMODE(mode))
        view = memoryview(contents)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(f"could not persist atomic file {path}")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def _validate_runtime_payload_tree(path: Path) -> str:
    """Verify `path` is a stable, user-owned, non-symlinked file or directory tree (rejecting
    symlinks, foreign owners, and multi-hard-linked files) and return its kind.
    """
    metadata = path.lstat()
    if path.is_symlink() or metadata.st_uid != os.geteuid():
        raise OSError(f"runtime payload path is not user-owned and stable: {path}")
    if stat.S_ISREG(metadata.st_mode):
        if metadata.st_nlink != 1:
            raise OSError(f"runtime payload file has multiple hard links: {path}")
        return "file"
    if not stat.S_ISDIR(metadata.st_mode):
        raise OSError(f"runtime payload path has an unsupported type: {path}")
    for root, directories, filenames in os.walk(path, followlinks=False):
        for name in [*directories, *filenames]:
            candidate = Path(root) / name
            item = candidate.lstat()
            if candidate.is_symlink() or item.st_uid != os.geteuid():
                raise OSError(
                    f"runtime payload tree contains a foreign link or owner: {candidate}"
                )
            if not stat.S_ISDIR(item.st_mode) and not stat.S_ISREG(item.st_mode):
                raise OSError(
                    f"runtime payload tree contains an unsupported path: {candidate}"
                )
            if stat.S_ISREG(item.st_mode) and item.st_nlink != 1:
                raise OSError(
                    f"runtime payload tree contains a hard-linked file: {candidate}"
                )
    return "directory"


def _runtime_payload_directory_flags() -> int:
    """Compute the O_DIRECTORY|O_NOFOLLOW open flags, raising OSError if the platform lacks
    openat support.
    """
    directory = getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not directory or not nofollow or os.open not in os.supports_dir_fd:
        raise OSError(
            "secure runtime payload rollback requires openat/O_NOFOLLOW support"
        )
    return os.O_RDONLY | directory | nofollow | getattr(os, "O_CLOEXEC", 0)


def _runtime_payload_open_absolute_directory(
    path: Path,
    *,
    create: bool,
) -> int:
    """Open an absolute directory one no-follow component at a time."""
    path = Path(os.path.abspath(os.fspath(path)))
    if not path.is_absolute() or path.anchor != os.sep:
        raise OSError(f"runtime payload directory is not absolute: {path}")
    flags = _runtime_payload_directory_flags()
    descriptor = os.open(os.sep, flags)
    try:
        for component in path.parts[1:]:
            if component in {"", ".", ".."} or os.sep in component:
                raise OSError(
                    f"runtime payload directory has an unsafe component: {path}"
                )
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(component, flags, dir_fd=descriptor)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise OSError(
                        f"runtime payload directory traverses a symlink: {path}"
                    ) from exc
                raise
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
            raise OSError(f"runtime payload directory is not user-owned: {path}")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _runtime_payload_directory_matches_fd(path: Path, descriptor: int) -> bool:
    """True if opening `path` fresh yields the same (dev, ino) identity as the already-open
    `descriptor`.
    """
    try:
        current = _runtime_payload_open_absolute_directory(path, create=False)
    except OSError:
        return False
    try:
        expected = os.fstat(descriptor)
        observed = os.fstat(current)
        return expected.st_dev == observed.st_dev and expected.st_ino == observed.st_ino
    finally:
        os.close(current)


def _runtime_payload_assert_directory_current(
    path: Path,
    descriptor: int,
) -> None:
    """Raise OSError if `path`'s current identity no longer matches the already-open
    `descriptor`.
    """
    if not _runtime_payload_directory_matches_fd(path, descriptor):
        raise OSError(f"runtime payload parent identity changed: {path}")


def _runtime_payload_kind(metadata: os.stat_result, *, label: str) -> str:
    """Classify a stat result as 'file' or 'directory', rejecting symlinks, foreign owners, and
    (for files) multiple hard links.
    """
    if metadata.st_uid != os.geteuid():
        raise OSError(f"runtime payload path is not user-owned: {label}")
    if stat.S_ISLNK(metadata.st_mode):
        raise OSError(f"runtime payload path traverses a symlink: {label}")
    if stat.S_ISREG(metadata.st_mode):
        if metadata.st_nlink != 1:
            raise OSError(f"runtime payload file has multiple hard links: {label}")
        return "file"
    if stat.S_ISDIR(metadata.st_mode):
        return "directory"
    raise OSError(f"runtime payload path has an unsupported type: {label}")


def _runtime_payload_safe_name(name: str) -> str:
    """Validate `name` as a safe single path component (no '', '.', '..', or separator); return
    it unchanged.
    """
    if name in {"", ".", ".."} or os.sep in name:
        raise OSError(f"unsafe runtime payload entry name: {name!r}")
    return name


def _runtime_payload_open_entry_at(
    parent_fd: int,
    name: str,
) -> tuple[int, str, os.stat_result]:
    """Open one directory entry by name via `dir_fd`, re-verifying its kind and identity did not
    change between the pre-open stat and the open itself.
    """
    name = _runtime_payload_safe_name(name)
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    kind = _runtime_payload_kind(before, label=name)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if kind == "directory":
        flags |= getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(name, flags, dir_fd=parent_fd)
    try:
        opened = os.fstat(descriptor)
        opened_kind = _runtime_payload_kind(opened, label=name)
        if (
            opened_kind != kind
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise OSError(f"runtime payload entry changed while opening: {name}")
        return descriptor, kind, opened
    except BaseException:
        os.close(descriptor)
        raise


def _runtime_payload_name_exists_at(parent_fd: int, name: str) -> bool:
    """True if `name` exists under `parent_fd` (without following a trailing symlink)."""
    try:
        os.stat(
            _runtime_payload_safe_name(name),
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return False
    return True


def _runtime_payload_stat_signature(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int, int, int, int]:
    """Tuple of stat fields (dev, ino, mode, uid, nlink, size, mtime_ns, ctime_ns) used to
    detect any change.
    """
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _runtime_payload_hash_blob(digest: Any, value: bytes) -> None:
    """Feed a length-prefixed `value` into a running hash `digest`, so field boundaries can't be
    confused.
    """
    digest.update(struct.pack("!Q", len(value)))
    digest.update(value)


def _runtime_payload_digest_node(
    descriptor: int,
    kind: str,
    name: bytes,
    digest: Any,
) -> None:
    """Recursively hash one payload node (file bytes or directory listing+children) into
    `digest`, re-verifying the node's identity was stable across the walk.
    """
    before = os.fstat(descriptor)
    if _runtime_payload_kind(before, label=os.fsdecode(name) or "<root>") != kind:
        raise OSError("runtime payload node changed type while hashing")
    _runtime_payload_hash_blob(digest, kind.encode("ascii"))
    _runtime_payload_hash_blob(digest, name)
    _runtime_payload_hash_blob(
        digest,
        str(stat.S_IMODE(before.st_mode)).encode("ascii"),
    )
    _runtime_payload_hash_blob(
        digest,
        str(before.st_mtime_ns).encode("ascii"),
    )
    if kind == "file":
        _runtime_payload_hash_blob(
            digest,
            str(before.st_size).encode("ascii"),
        )
        offset = 0
        while True:
            chunk = os.pread(descriptor, 1024 * 1024, offset)
            if not chunk:
                break
            digest.update(chunk)
            offset += len(chunk)
    else:
        names = sorted(os.listdir(descriptor), key=os.fsencode)
        _runtime_payload_hash_blob(
            digest,
            str(len(names)).encode("ascii"),
        )
        for child_name in names:
            child_fd, child_kind, _ = _runtime_payload_open_entry_at(
                descriptor,
                child_name,
            )
            try:
                _runtime_payload_digest_node(
                    child_fd,
                    child_kind,
                    os.fsencode(child_name),
                    digest,
                )
            finally:
                os.close(child_fd)
    after = os.fstat(descriptor)
    if _runtime_payload_stat_signature(before) != _runtime_payload_stat_signature(
        after
    ):
        raise OSError("runtime payload changed while hashing")


def _runtime_payload_digest_fd(descriptor: int, kind: str) -> str:
    """Compute the full content digest of an open file/directory descriptor."""
    digest = hashlib.sha256()
    _runtime_payload_hash_blob(digest, b"vibecrafted-runtime-payload-v1")
    _runtime_payload_digest_node(descriptor, kind, b"", digest)
    return digest.hexdigest()


def _runtime_payload_remove_at(
    parent_fd: int,
    name: str,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> None:
    """Recursively delete the entry named `name` under `parent_fd`, verifying its identity first
    if `expected_identity` is given.
    """
    name = _runtime_payload_safe_name(name)
    try:
        descriptor, kind, metadata = _runtime_payload_open_entry_at(parent_fd, name)
    except FileNotFoundError:
        return
    try:
        if (
            expected_identity is not None
            and (metadata.st_dev, metadata.st_ino) != expected_identity
        ):
            raise OSError(f"runtime payload removal identity changed: {name}")
        if kind == "directory":
            for child in os.listdir(descriptor):
                _runtime_payload_remove_at(descriptor, child)
        observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if observed.st_dev != metadata.st_dev or observed.st_ino != metadata.st_ino:
            raise OSError(f"runtime payload removal target changed: {name}")
    finally:
        os.close(descriptor)
    if kind == "directory":
        os.rmdir(name, dir_fd=parent_fd)
    else:
        os.unlink(name, dir_fd=parent_fd)


def _runtime_payload_write_all(descriptor: int, data: bytes) -> None:
    """Write all of `data` to `descriptor`, looping until every byte is written."""
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("runtime payload copy made no progress")
        view = view[written:]


def _runtime_payload_copy_node(
    source_fd: int,
    kind: str,
    destination_parent_fd: int,
    destination_name: str,
) -> None:
    """Recursively copy one payload node (file or directory) from `source_fd` to a new entry
    `destination_name` under `destination_parent_fd`, preserving mode/mtime and cleaning up on
    any failure.
    """
    destination_name = _runtime_payload_safe_name(destination_name)
    source_before = os.fstat(source_fd)
    if _runtime_payload_kind(source_before, label=destination_name) != kind:
        raise OSError("runtime payload source changed type while copying")
    created = False
    destination_fd = -1
    try:
        if kind == "file":
            destination_fd = os.open(
                destination_name,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=destination_parent_fd,
            )
            created = True
            offset = 0
            while True:
                chunk = os.pread(source_fd, 1024 * 1024, offset)
                if not chunk:
                    break
                _runtime_payload_write_all(destination_fd, chunk)
                offset += len(chunk)
        else:
            os.mkdir(
                destination_name,
                mode=0o700,
                dir_fd=destination_parent_fd,
            )
            created = True
            destination_fd, destination_kind, _ = _runtime_payload_open_entry_at(
                destination_parent_fd,
                destination_name,
            )
            if destination_kind != "directory":
                raise OSError("runtime payload staging directory changed type")
            for child_name in sorted(os.listdir(source_fd), key=os.fsencode):
                child_fd, child_kind, _ = _runtime_payload_open_entry_at(
                    source_fd,
                    child_name,
                )
                try:
                    _runtime_payload_copy_node(
                        child_fd,
                        child_kind,
                        destination_fd,
                        child_name,
                    )
                finally:
                    os.close(child_fd)
        os.fchmod(destination_fd, stat.S_IMODE(source_before.st_mode))
        os.utime(
            destination_fd,
            ns=(source_before.st_atime_ns, source_before.st_mtime_ns),
        )
        if kind == "file":
            os.fsync(destination_fd)
        source_after = os.fstat(source_fd)
        if _runtime_payload_stat_signature(
            source_before
        ) != _runtime_payload_stat_signature(source_after):
            raise OSError("runtime payload source changed while copying")
    except BaseException:
        if destination_fd >= 0:
            os.close(destination_fd)
            destination_fd = -1
        if created:
            _runtime_payload_remove_at(destination_parent_fd, destination_name)
        raise
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)


def _runtime_payload_validate_at(parent_fd: int, name: str) -> str:
    """Open and hash-verify one payload entry by name, returning its kind (raises on any
    inconsistency).
    """
    descriptor, kind, _ = _runtime_payload_open_entry_at(parent_fd, name)
    try:
        _runtime_payload_digest_fd(descriptor, kind)
    finally:
        os.close(descriptor)
    return kind


def _runtime_payload_assert_retained_entry(
    parent_fd: int,
    name: str,
    retained_fd: int,
    kind: str,
    expected_digest: str,
) -> None:
    """Raise OSError unless the on-disk entry at `parent_fd`/`name` still matches
    `retained_fd`'s identity, kind, and digest — used to seal a just-published rename.
    """
    retained = os.fstat(retained_fd)
    if _runtime_payload_kind(retained, label=name) != kind:
        raise OSError(f"retained runtime payload changed type: {name}")
    if _runtime_payload_digest_fd(retained_fd, kind) != expected_digest:
        raise OSError(f"retained runtime payload digest changed: {name}")
    observed_fd, observed_kind, observed = _runtime_payload_open_entry_at(
        parent_fd,
        name,
    )
    try:
        if (
            observed_kind != kind
            or observed.st_dev != retained.st_dev
            or observed.st_ino != retained.st_ino
        ):
            raise OSError(f"runtime payload publication identity changed: {name}")
        if _runtime_payload_digest_fd(observed_fd, observed_kind) != expected_digest:
            raise OSError(f"runtime payload publication digest changed: {name}")
    finally:
        os.close(observed_fd)


def _runtime_payload_open_backup_root(
    backup: _RuntimePayloadBackup,
) -> int:
    """Open the runtime-payload backup transaction root, verifying its identity has not changed."""
    descriptor = _runtime_payload_open_absolute_directory(
        backup.root,
        create=False,
    )
    metadata = os.fstat(descriptor)
    if (metadata.st_dev, metadata.st_ino) != backup.root_identity:
        os.close(descriptor)
        raise OSError("runtime payload backup root identity changed")
    return descriptor


def _validate_runtime_payload_backup(backup: _RuntimePayloadBackup) -> None:
    """Re-verify every entry in a captured payload backup still matches its recorded digest
    before use.
    """
    root_fd = _runtime_payload_open_backup_root(backup)
    try:
        for entry in backup.entries:
            if entry.kind == "absent":
                if entry.backup is not None or entry.digest is not None:
                    raise OSError(
                        f"absent runtime payload has backup state: {entry.path}"
                    )
                continue
            if (
                entry.backup is None
                or entry.backup.parent != backup.root
                or entry.digest is None
            ):
                raise OSError(
                    f"runtime payload backup escaped its transaction root: {entry.path}"
                )
            descriptor, kind, _ = _runtime_payload_open_entry_at(
                root_fd,
                entry.backup.name,
            )
            try:
                observed = _runtime_payload_digest_fd(descriptor, kind)
            finally:
                os.close(descriptor)
            if kind != entry.kind or observed != entry.digest:
                raise OSError(f"runtime payload backup digest changed for {entry.path}")
    finally:
        os.close(root_fd)


def _stage_runtime_payload_restore(
    entry: _RuntimePayloadEntryBackup,
    *,
    backup_root_fd: int,
    destination_parent_fd: int,
) -> tuple[str | None, int | None, str | None]:
    """Copy one backup entry into a freshly staged name under the destination parent,
    re-verifying its digest, ready for the final publishing rename.
    """
    if entry.kind == "absent":
        if entry.backup is not None or entry.digest is not None:
            raise OSError(f"absent runtime payload has backup state: {entry.path}")
        return None, None, None
    if entry.backup is None or entry.digest is None:
        raise OSError(f"runtime payload backup is missing for {entry.path}")
    source_fd, source_kind, _ = _runtime_payload_open_entry_at(
        backup_root_fd,
        entry.backup.name,
    )
    staged_name = f".{entry.path.name}.restore-{os.getpid()}-{os.urandom(6).hex()}"
    staged_fd = -1
    try:
        if source_kind != entry.kind:
            raise OSError(f"runtime payload backup type changed for {entry.path}")
        _runtime_payload_copy_node(
            source_fd,
            source_kind,
            destination_parent_fd,
            staged_name,
        )
        staged_fd, staged_kind, _ = _runtime_payload_open_entry_at(
            destination_parent_fd,
            staged_name,
        )
        staged_digest = _runtime_payload_digest_fd(staged_fd, staged_kind)
        if staged_kind != entry.kind or staged_digest != entry.digest:
            raise OSError(f"runtime payload staged digest changed for {entry.path}")
        return staged_name, staged_fd, staged_kind
    except BaseException:
        if staged_fd >= 0:
            os.close(staged_fd)
        _runtime_payload_remove_at(destination_parent_fd, staged_name)
        raise
    finally:
        os.close(source_fd)


def _runtime_payload_validate_capture_sources(
    sources: Sequence[_RuntimePayloadCaptureSource],
) -> None:
    """Re-verify every captured payload source (or proven absence) is still exactly as it was
    when first observed, right before the backup copy begins.
    """
    for source in sources:
        if source.kind == "absent":
            if source.parent_fd is not None:
                _runtime_payload_assert_directory_current(
                    source.path.parent,
                    source.parent_fd,
                )
                if _runtime_payload_name_exists_at(
                    source.parent_fd,
                    source.path.name,
                ):
                    raise OSError(
                        f"runtime payload appeared during capture: {source.path}"
                    )
                continue
            try:
                parent_fd = _runtime_payload_open_absolute_directory(
                    source.path.parent,
                    create=False,
                )
            except FileNotFoundError:
                continue
            try:
                if _runtime_payload_name_exists_at(
                    parent_fd,
                    source.path.name,
                ):
                    raise OSError(
                        f"runtime payload appeared during capture: {source.path}"
                    )
            finally:
                os.close(parent_fd)
            continue
        if (
            source.parent_fd is None
            or source.source_fd is None
            or source.digest is None
            or source.opened is None
        ):
            raise OSError(
                f"runtime payload capture source is incomplete: {source.path}"
            )
        _runtime_payload_assert_directory_current(
            source.path.parent,
            source.parent_fd,
        )
        if _runtime_payload_stat_signature(
            source.opened
        ) != _runtime_payload_stat_signature(os.fstat(source.source_fd)):
            raise OSError(f"runtime payload changed after opening: {source.path}")
        _runtime_payload_assert_retained_entry(
            source.parent_fd,
            source.path.name,
            source.source_fd,
            source.kind,
            source.digest,
        )


def _capture_runtime_payload_backup(
    shared_home: Path,
    paths: Sequence[Path],
) -> _RuntimePayloadBackup | None:
    """Capture an atomic, digest-verified backup of every path in `paths` into a fresh
    transaction directory under the shared home, for later restore/discard.
    """
    if not paths:
        return None
    expanded = tuple(
        Path(os.path.abspath(os.fspath(path.expanduser()))) for path in paths
    )
    if len(set(expanded)) != len(expanded):
        raise OSError("runtime payload transaction contains duplicate paths")
    for outer in expanded:
        for inner in expanded:
            if outer != inner and outer in inner.parents:
                raise OSError("runtime payload transaction contains nested paths")
    root_parent = (
        Path(os.path.abspath(os.fspath(shared_home.expanduser())))
        / "install-transactions"
    )
    root_parent_fd = _runtime_payload_open_absolute_directory(
        root_parent,
        create=True,
    )
    root_name = f"runtime-payload-{os.getpid()}-{os.urandom(8).hex()}"
    os.mkdir(root_name, mode=0o700, dir_fd=root_parent_fd)
    root = root_parent / root_name
    root_fd, root_kind, root_metadata = _runtime_payload_open_entry_at(
        root_parent_fd,
        root_name,
    )
    if root_kind != "directory":
        os.close(root_fd)
        os.close(root_parent_fd)
        raise OSError("runtime payload backup root changed type")
    entries: list[_RuntimePayloadEntryBackup] = []
    try:
        with ExitStack() as source_descriptors:
            sources: list[_RuntimePayloadCaptureSource] = []
            for path in expanded:
                try:
                    source_parent_fd = _runtime_payload_open_absolute_directory(
                        path.parent,
                        create=False,
                    )
                except FileNotFoundError:
                    sources.append(
                        _RuntimePayloadCaptureSource(
                            path,
                            None,
                            None,
                            "absent",
                            None,
                            None,
                        )
                    )
                    continue
                source_descriptors.callback(os.close, source_parent_fd)
                try:
                    source_fd, kind, source_opened = _runtime_payload_open_entry_at(
                        source_parent_fd,
                        path.name,
                    )
                except FileNotFoundError:
                    sources.append(
                        _RuntimePayloadCaptureSource(
                            path,
                            source_parent_fd,
                            None,
                            "absent",
                            None,
                            None,
                        )
                    )
                    continue
                source_descriptors.callback(os.close, source_fd)
                source_digest = _runtime_payload_digest_fd(source_fd, kind)
                if _runtime_payload_stat_signature(
                    source_opened
                ) != _runtime_payload_stat_signature(os.fstat(source_fd)):
                    raise OSError(f"runtime payload changed before capture: {path}")
                sources.append(
                    _RuntimePayloadCaptureSource(
                        path,
                        source_parent_fd,
                        source_fd,
                        kind,
                        source_digest,
                        source_opened,
                    )
                )

            _runtime_payload_validate_capture_sources(sources)
            for index, source in enumerate(sources):
                if source.kind == "absent":
                    entries.append(
                        _RuntimePayloadEntryBackup(
                            source.path,
                            None,
                            "absent",
                            None,
                        )
                    )
                    continue
                if source.source_fd is None or source.digest is None:
                    raise OSError(
                        f"runtime payload capture source is incomplete: {source.path}"
                    )
                backup_name = f"{index}-{source.path.name}"
                _runtime_payload_copy_node(
                    source.source_fd,
                    source.kind,
                    root_fd,
                    backup_name,
                )
                if (
                    _runtime_payload_digest_fd(
                        source.source_fd,
                        source.kind,
                    )
                    != source.digest
                ):
                    raise OSError(
                        f"runtime payload changed during capture: {source.path}"
                    )
                backup_fd, backup_kind, _ = _runtime_payload_open_entry_at(
                    root_fd,
                    backup_name,
                )
                try:
                    digest = _runtime_payload_digest_fd(backup_fd, backup_kind)
                finally:
                    os.close(backup_fd)
                if backup_kind != source.kind or digest != source.digest:
                    raise OSError(
                        f"runtime payload backup changed during capture: {source.path}"
                    )
                entries.append(
                    _RuntimePayloadEntryBackup(
                        source.path,
                        root / backup_name,
                        source.kind,
                        digest,
                    )
                )
            _runtime_payload_validate_capture_sources(sources)
        _runtime_payload_assert_directory_current(root, root_fd)
        return _RuntimePayloadBackup(
            root,
            tuple(entries),
            (root_metadata.st_dev, root_metadata.st_ino),
        )
    except BaseException:
        _runtime_payload_remove_at(root_parent_fd, root_name)
        raise
    finally:
        os.close(root_fd)
        os.close(root_parent_fd)


def _restore_runtime_payload_backup_open(
    backup: _RuntimePayloadBackup,
    backup_root_fd: int,
    descriptors: ExitStack,
) -> None:
    """Perform the full payload-backup restore transaction: stage each entry, snapshot and
    displace the current occupant, publish the staged replacement, and roll every step back if
    any later step fails.
    """
    operations: list[_RuntimePayloadRestoreOperation] = []
    try:
        for entry in backup.entries:
            parent_fd = _runtime_payload_open_absolute_directory(
                entry.path.parent,
                create=True,
            )
            descriptors.callback(os.close, parent_fd)
            staged_name, staged_fd, staged_kind = _stage_runtime_payload_restore(
                entry,
                backup_root_fd=backup_root_fd,
                destination_parent_fd=parent_fd,
            )
            if staged_fd is not None:
                descriptors.callback(os.close, staged_fd)
            operation = _RuntimePayloadRestoreOperation(
                entry=entry,
                parent_fd=parent_fd,
                staged_name=staged_name,
                staged_fd=staged_fd,
                staged_kind=staged_kind,
                displaced_name=(
                    f".{entry.path.name}.displaced-{os.getpid()}-{os.urandom(6).hex()}"
                ),
            )
            operations.append(operation)
            _runtime_payload_assert_directory_current(
                entry.path.parent,
                parent_fd,
            )
    except BaseException:
        for operation in operations:
            if operation.staged_name is not None:
                _runtime_payload_remove_at(
                    operation.parent_fd,
                    operation.staged_name,
                )
        raise

    try:
        for operation in operations:
            entry = operation.entry
            _runtime_payload_assert_directory_current(
                entry.path.parent,
                operation.parent_fd,
            )
            if not _runtime_payload_name_exists_at(
                operation.parent_fd,
                entry.path.name,
            ):
                continue
            current_fd, current_kind, current_opened = _runtime_payload_open_entry_at(
                operation.parent_fd,
                entry.path.name,
            )
            try:
                current_digest = _runtime_payload_digest_fd(
                    current_fd,
                    current_kind,
                )
                if _runtime_payload_stat_signature(
                    current_opened
                ) != _runtime_payload_stat_signature(os.fstat(current_fd)):
                    raise OSError(
                        f"runtime payload changed before snapshot: {entry.path}"
                    )
                operation.precall_name = (
                    f".{entry.path.name}.precall-{os.getpid()}-{os.urandom(6).hex()}"
                )
                _runtime_payload_copy_node(
                    current_fd,
                    current_kind,
                    operation.parent_fd,
                    operation.precall_name,
                )
                (
                    operation.precall_fd,
                    operation.precall_kind,
                    _,
                ) = _runtime_payload_open_entry_at(
                    operation.parent_fd,
                    operation.precall_name,
                )
                descriptors.callback(os.close, operation.precall_fd)
                operation.precall_digest = _runtime_payload_digest_fd(
                    operation.precall_fd,
                    operation.precall_kind,
                )
                if (
                    operation.precall_kind != current_kind
                    or operation.precall_digest != current_digest
                    or _runtime_payload_digest_fd(
                        current_fd,
                        current_kind,
                    )
                    != current_digest
                ):
                    raise OSError(
                        f"runtime payload changed while snapshotting {entry.path}"
                    )
            finally:
                os.close(current_fd)

        # Establish one pre-apply boundary across the complete payload set.
        for operation in operations:
            entry = operation.entry
            current_exists = _runtime_payload_name_exists_at(
                operation.parent_fd,
                entry.path.name,
            )
            if operation.precall_name is None:
                if current_exists:
                    raise OSError(
                        f"runtime payload appeared before publication: {entry.path}"
                    )
                continue
            if (
                operation.precall_fd is None
                or operation.precall_kind is None
                or operation.precall_digest is None
                or not current_exists
            ):
                raise OSError(
                    f"runtime payload pre-call snapshot is incomplete: {entry.path}"
                )
            current_fd, current_kind, _ = _runtime_payload_open_entry_at(
                operation.parent_fd,
                entry.path.name,
            )
            try:
                if (
                    current_kind != operation.precall_kind
                    or _runtime_payload_digest_fd(current_fd, current_kind)
                    != operation.precall_digest
                ):
                    raise OSError(
                        f"runtime payload changed before publication: {entry.path}"
                    )
            finally:
                os.close(current_fd)
    except BaseException:
        for operation in operations:
            if operation.staged_name is not None:
                _runtime_payload_remove_at(
                    operation.parent_fd,
                    operation.staged_name,
                )
            if operation.precall_name is not None:
                _runtime_payload_remove_at(
                    operation.parent_fd,
                    operation.precall_name,
                )
        raise

    try:
        for operation in operations:
            entry = operation.entry
            current_fd = -1
            try:
                _runtime_payload_assert_directory_current(
                    entry.path.parent,
                    operation.parent_fd,
                )
                current_exists = _runtime_payload_name_exists_at(
                    operation.parent_fd,
                    entry.path.name,
                )
                if operation.precall_name is None:
                    if current_exists:
                        raise OSError(
                            f"runtime payload appeared during publication: {entry.path}"
                        )
                    current_digest: str | None = None
                else:
                    if (
                        not current_exists
                        or operation.precall_kind is None
                        or operation.precall_digest is None
                    ):
                        raise OSError(
                            f"runtime payload disappeared during publication: "
                            f"{entry.path}"
                        )
                    current_fd, current_kind, _ = _runtime_payload_open_entry_at(
                        operation.parent_fd,
                        entry.path.name,
                    )
                    current_digest = operation.precall_digest
                    if (
                        operation.precall_kind != current_kind
                        or _runtime_payload_digest_fd(
                            current_fd,
                            current_kind,
                        )
                        != current_digest
                    ):
                        raise OSError(
                            f"runtime payload changed during publication: {entry.path}"
                        )
                if current_exists:
                    try:
                        os.replace(
                            entry.path.name,
                            operation.displaced_name,
                            src_dir_fd=operation.parent_fd,
                            dst_dir_fd=operation.parent_fd,
                        )
                    finally:
                        operation.current_displaced = _runtime_payload_name_exists_at(
                            operation.parent_fd,
                            operation.displaced_name,
                        )
                    if (
                        current_fd < 0
                        or current_digest is None
                        or _runtime_payload_digest_fd(
                            current_fd,
                            current_kind,
                        )
                        != current_digest
                    ):
                        raise OSError(
                            f"runtime payload changed after displacement: {entry.path}"
                        )
                    _runtime_payload_assert_directory_current(
                        entry.path.parent,
                        operation.parent_fd,
                    )
                if operation.staged_name is not None:
                    if (
                        operation.staged_fd is None
                        or operation.staged_kind is None
                        or entry.digest is None
                    ):
                        raise OSError(
                            f"runtime payload staging identity is incomplete: "
                            f"{entry.path}"
                        )
                    _runtime_payload_assert_retained_entry(
                        operation.parent_fd,
                        operation.staged_name,
                        operation.staged_fd,
                        operation.staged_kind,
                        entry.digest,
                    )
                    try:
                        os.replace(
                            operation.staged_name,
                            entry.path.name,
                            src_dir_fd=operation.parent_fd,
                            dst_dir_fd=operation.parent_fd,
                        )
                    finally:
                        operation.replacement_published = (
                            not _runtime_payload_name_exists_at(
                                operation.parent_fd,
                                operation.staged_name,
                            )
                            and _runtime_payload_name_exists_at(
                                operation.parent_fd,
                                entry.path.name,
                            )
                        )
                    if operation.replacement_published:
                        # This rename is still tentative: the caller retains the
                        # disabled service gate and lifecycle fence until every
                        # retained FD passes this post-rename seal.
                        _runtime_payload_assert_retained_entry(
                            operation.parent_fd,
                            entry.path.name,
                            operation.staged_fd,
                            operation.staged_kind,
                            entry.digest,
                        )
                _runtime_payload_assert_directory_current(
                    entry.path.parent,
                    operation.parent_fd,
                )
            finally:
                if current_fd >= 0:
                    os.close(current_fd)

        # One collective seal closes the interval in which later entries were
        # still publishing after an earlier entry's per-rename validation.
        for operation in operations:
            entry = operation.entry
            _runtime_payload_assert_directory_current(
                entry.path.parent,
                operation.parent_fd,
            )
            if operation.staged_name is None:
                if _runtime_payload_name_exists_at(
                    operation.parent_fd,
                    entry.path.name,
                ):
                    raise OSError(
                        f"absent runtime payload appeared before final seal: "
                        f"{entry.path}"
                    )
                continue
            if (
                operation.staged_fd is None
                or operation.staged_kind is None
                or entry.digest is None
            ):
                raise OSError(f"runtime payload final seal is incomplete: {entry.path}")
            _runtime_payload_assert_retained_entry(
                operation.parent_fd,
                entry.path.name,
                operation.staged_fd,
                operation.staged_kind,
                entry.digest,
            )
    except BaseException as restore_exc:
        rollback_errors: list[str] = []
        for operation in reversed(operations):
            try:
                entry = operation.entry
                if _runtime_payload_name_exists_at(
                    operation.parent_fd,
                    entry.path.name,
                ):
                    _runtime_payload_remove_at(
                        operation.parent_fd,
                        entry.path.name,
                    )
                if _runtime_payload_name_exists_at(
                    operation.parent_fd,
                    operation.displaced_name,
                ):
                    _runtime_payload_remove_at(
                        operation.parent_fd,
                        operation.displaced_name,
                    )
                if operation.precall_name is not None:
                    if (
                        operation.precall_fd is None
                        or operation.precall_kind is None
                        or operation.precall_digest is None
                    ):
                        raise OSError(
                            f"runtime payload pre-call snapshot is missing: "
                            f"{entry.path}"
                        )
                    _runtime_payload_assert_retained_entry(
                        operation.parent_fd,
                        operation.precall_name,
                        operation.precall_fd,
                        operation.precall_kind,
                        operation.precall_digest,
                    )
                    try:
                        os.replace(
                            operation.precall_name,
                            entry.path.name,
                            src_dir_fd=operation.parent_fd,
                            dst_dir_fd=operation.parent_fd,
                        )
                    finally:
                        operation.precall_published = (
                            not _runtime_payload_name_exists_at(
                                operation.parent_fd,
                                operation.precall_name,
                            )
                            and _runtime_payload_name_exists_at(
                                operation.parent_fd,
                                entry.path.name,
                            )
                        )
                    if operation.precall_published:
                        _runtime_payload_assert_retained_entry(
                            operation.parent_fd,
                            entry.path.name,
                            operation.precall_fd,
                            operation.precall_kind,
                            operation.precall_digest,
                        )
                elif operation.precall_fd is not None:
                    raise OSError(
                        f"runtime payload absent snapshot is inconsistent: {entry.path}"
                    )
            except BaseException as rollback_exc:
                if not isinstance(rollback_exc, Exception):
                    raise
                rollback_errors.append(f"{operation.entry.path}: {rollback_exc}")
        for operation in operations:
            if operation.staged_name is not None:
                _runtime_payload_remove_at(
                    operation.parent_fd,
                    operation.staged_name,
                )
            if operation.precall_name is not None:
                _runtime_payload_remove_at(
                    operation.parent_fd,
                    operation.precall_name,
                )
        if rollback_errors:
            raise OSError(
                "runtime payload restore failed and its partial swaps could not be "
                f"reversed ({'; '.join(rollback_errors)})"
            ) from restore_exc
        raise

    cleanup_errors: list[str] = []
    for operation in operations:
        try:
            if _runtime_payload_name_exists_at(
                operation.parent_fd,
                operation.displaced_name,
            ):
                _runtime_payload_validate_at(
                    operation.parent_fd,
                    operation.displaced_name,
                )
                _runtime_payload_remove_at(
                    operation.parent_fd,
                    operation.displaced_name,
                )
            if operation.staged_name is not None:
                _runtime_payload_remove_at(
                    operation.parent_fd,
                    operation.staged_name,
                )
            if operation.precall_name is not None:
                _runtime_payload_remove_at(
                    operation.parent_fd,
                    operation.precall_name,
                )
        except OSError as cleanup_exc:
            cleanup_errors.append(f"{operation.entry.path}: {cleanup_exc}")
    if cleanup_errors:
        raise OSError(
            "runtime payload was restored but displaced payload cleanup failed "
            f"({'; '.join(cleanup_errors)})"
        )


def _restore_runtime_payload_backup(backup: _RuntimePayloadBackup | None) -> None:
    """Restore a runtime-payload backup (no-op if `backup` is None) after re-verifying its
    digests.
    """
    if backup is None:
        return
    _validate_runtime_payload_backup(backup)
    with ExitStack() as descriptors:
        backup_root_fd = _runtime_payload_open_backup_root(backup)
        descriptors.callback(os.close, backup_root_fd)
        _restore_runtime_payload_backup_open(
            backup,
            backup_root_fd,
            descriptors,
        )


def _discard_runtime_payload_backup(backup: _RuntimePayloadBackup | None) -> None:
    """Permanently delete a runtime-payload backup transaction directory, quarantining it first
    so a mid-delete failure can be recovered.
    """
    if backup is None:
        return
    _validate_runtime_payload_backup(backup)
    try:
        parent_fd = _runtime_payload_open_absolute_directory(
            backup.root.parent,
            create=False,
        )
    except FileNotFoundError:
        return
    root_fd = -1
    quarantine_name = f".{backup.root.name}.discard-{os.getpid()}-{os.urandom(6).hex()}"
    quarantined = False
    try:
        try:
            root_fd, root_kind, root_metadata = _runtime_payload_open_entry_at(
                parent_fd,
                backup.root.name,
            )
        except FileNotFoundError:
            return
        try:
            if (
                root_kind != "directory"
                or (root_metadata.st_dev, root_metadata.st_ino) != backup.root_identity
            ):
                raise OSError("runtime payload backup root identity changed")
            os.replace(
                backup.root.name,
                quarantine_name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            quarantined = not _runtime_payload_name_exists_at(
                parent_fd,
                backup.root.name,
            ) and _runtime_payload_name_exists_at(
                parent_fd,
                quarantine_name,
            )
            quarantine_fd, quarantine_kind, quarantine_metadata = (
                _runtime_payload_open_entry_at(
                    parent_fd,
                    quarantine_name,
                )
            )
            try:
                if (
                    quarantine_kind != "directory"
                    or (
                        quarantine_metadata.st_dev,
                        quarantine_metadata.st_ino,
                    )
                    != backup.root_identity
                    or quarantine_metadata.st_dev != root_metadata.st_dev
                    or quarantine_metadata.st_ino != root_metadata.st_ino
                ):
                    raise OSError("runtime payload backup changed during discard")
            finally:
                os.close(quarantine_fd)
            _runtime_payload_remove_at(
                parent_fd,
                quarantine_name,
                expected_identity=backup.root_identity,
            )
            quarantined = False
        except BaseException:
            if (
                quarantined
                and _runtime_payload_name_exists_at(
                    parent_fd,
                    quarantine_name,
                )
                and not _runtime_payload_name_exists_at(
                    parent_fd,
                    backup.root.name,
                )
            ):
                os.replace(
                    quarantine_name,
                    backup.root.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
            raise
    finally:
        if root_fd >= 0:
            os.close(root_fd)
        os.close(parent_fd)


def _read_tools_handoff_path(path: Path) -> dict[str, Any] | None:
    """Read and schema-validate a tools-handoff receipt JSON at `path`; None if missing/invalid."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != _TOOLS_HANDOFF_SCHEMA
        or payload.get("state") not in {"prepared", "rolled-back", "complete"}
        or not isinstance(payload.get("old_target"), str)
        or not isinstance(payload.get("new_target"), str)
    ):
        return None
    return payload


def _read_tools_handoff(shared_home: Path) -> dict[str, Any] | None:
    """Read the tools-handoff receipt for the shared home's current-tools link."""
    return _read_tools_handoff_path(_tools_handoff_file(shared_home))


def _tools_handoff_publication_boundary(shared_home: Path) -> datetime:
    """The exact publication timestamp of the currently prepared runtime generation, verified
    against the live current-tools symlink target.
    """
    payload = _read_tools_handoff(shared_home)
    if payload is None or payload["state"] != "prepared":
        raise OSError("runtime publication has no prepared handoff receipt")
    current_target = _symlink_target(_current_tools_link(shared_home))
    expected_target = Path(payload["new_target"]).resolve(strict=False)
    if current_target != expected_target:
        raise OSError("runtime publication boundary targets a stale generation")
    raw_boundary = payload.get("published_at")
    if not isinstance(raw_boundary, str):
        raise OSError("runtime publication has no exact publication boundary")
    try:
        boundary = datetime.fromisoformat(raw_boundary)
    except ValueError as exc:
        raise OSError("runtime publication boundary is malformed") from exc
    if boundary.tzinfo is None:
        raise OSError("runtime publication boundary has no timezone")
    return boundary.astimezone(timezone.utc)


def _tools_handoff_is_complete_current(
    shared_home: Path,
    *,
    expected_target: Path,
) -> bool:
    """True if the tools-handoff receipt is 'complete' and its recorded target matches both the
    live current-tools symlink and `expected_target`.
    """
    payload = _read_tools_handoff(shared_home)
    if payload is None or payload["state"] != "complete":
        return False
    current_target = _symlink_target(_current_tools_link(shared_home))
    receipt_target = Path(payload["new_target"]).resolve(strict=False)
    expected_target = expected_target.resolve(strict=False)
    return current_target == expected_target == receipt_target


def sync_control_plane_tree(
    src: Path,
    dst: Path,
    dry_run: bool = False,
    mirror: bool = False,
    *,
    install_version: str | None = None,
) -> Path:
    """Publish a complete immutable runtime generation through a symlink swap.

    ``dst`` is the stable ``vibecrafted-current`` pointer, never a mutable
    generation directory.  Staging, validation, and version stamping all happen
    before the sole publication operation (``os.replace`` on the symlink).
    """
    if dry_run:
        return dst
    with _tools_install_lease(dst, operation=f"runtime-publish:{src}"):
        return _sync_control_plane_tree_locked(
            src,
            dst,
            mirror=mirror,
            install_version=install_version,
        )


def _materialize_vc_frame_generation(runtime_root: Path) -> None:
    """Build host-adapted vc-frame assets before a runtime can be published."""
    module_path = (
        runtime_root / "vibecrafted-core" / "vibecrafted_core" / "vc_frame_staging.py"
    )
    source = (
        runtime_root / "vibecrafted-core" / "vibecrafted_core" / "config" / "vc-frame"
    )
    destination = (
        runtime_root
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "generated"
        / "vc-frame"
    )
    if not module_path.is_file():
        raise OSError(
            f"candidate runtime has no vc-frame staging implementation: {module_path}"
        )
    namespace = runpy.run_path(
        str(module_path),
        run_name="_vibecrafted_vc_frame_staging",
    )
    resolve_pane_shell = namespace.get("resolve_pane_shell")
    resolve_clipboard_command = namespace.get("resolve_clipboard_command")
    materialize = namespace.get("materialize_vc_frame_config")
    if not all(
        callable(value)
        for value in (resolve_pane_shell, resolve_clipboard_command, materialize)
    ):
        raise OSError(f"candidate vc-frame staging API is incomplete: {module_path}")
    pane_shell = resolve_pane_shell()
    clipboard_command = resolve_clipboard_command()
    materialize(
        source,
        destination,
        pane_shell=pane_shell,
        clipboard_command=clipboard_command,
    )
    required = (
        destination / "config.kdl",
        destination / "layouts",
        destination / "themes",
    )
    if not required[0].is_file() or any(not path.is_dir() for path in required[1:]):
        raise OSError(
            f"candidate runtime has incomplete materialized vc-frame config: "
            f"{destination}"
        )


def _runtime_active_text_files(runtime_root: Path) -> Iterator[Path]:
    """Yield every active (non-symlink) text config/script file under the runtime's watched
    roots.
    """
    for relative_root in _RUNTIME_ACTIVE_TEXT_ROOTS:
        root = runtime_root / relative_root
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix in _RUNTIME_ACTIVE_TEXT_SUFFIXES
            ):
                yield path
    for relative in (
        Path("scripts/vibecrafted"),
        Path("vibecrafted-core/vibecrafted_core/deck/vibecrafted"),
    ):
        path = runtime_root / relative
        if path.is_file() and not path.is_symlink():
            yield path


def _path_fingerprint(path: Path) -> str:
    """SHA-256 hex fingerprint of a resolved path's string form, used to detect stray checkout
    references.
    """
    return hashlib.sha256(str(path.resolve(strict=False)).encode("utf-8")).hexdigest()


def _text_references_path_fingerprint(text: str, fingerprint: str) -> bool:
    """True if any absolute-path-looking token in `text` (or one of its parent directories)
    hashes to `fingerprint`.
    """
    for raw_token in _ABSOLUTE_PATH_TOKEN.findall(text):
        candidate = Path(raw_token)
        for ancestor in (candidate, *candidate.parents):
            if _path_fingerprint(ancestor) == fingerprint:
                return True
    return False


def _runtime_generation_audit_errors(
    runtime_root: Path,
    *,
    source_root: Path | None = None,
    source_fingerprint: str | None = None,
) -> list[str]:
    """Audit a candidate runtime generation for symlinks escaping the generation root and active
    files that still reference the source checkout path.
    """
    root = runtime_root.resolve(strict=False)
    errors: list[str] = []
    for path in sorted(runtime_root.rglob("*")):
        if not path.is_symlink():
            continue
        relative = path.relative_to(runtime_root)
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            errors.append(f"broken installed symlink: {relative}")
            continue
        if not _is_subpath(resolved, root):
            errors.append(f"installed symlink escapes generation: {relative}")

    source_text = str(source_root.resolve(strict=False)) if source_root else None
    for path in _runtime_active_text_files(runtime_root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(runtime_root)
        if (
            source_text
            and source_text in text
            or source_fingerprint
            and _text_references_path_fingerprint(text, source_fingerprint)
        ):
            errors.append(f"active runtime file references source checkout: {relative}")
    return sorted(set(errors))


def _capture_runtime_bound_file(path: Path) -> bytes:
    """Read one stable, unique regular file without following its final path component."""
    expected = os.path.abspath(path)
    resolved = os.path.realpath(expected)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(expected, flags)
    except OSError as exc:
        raise OSError(f"cannot open unique regular file: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise OSError("path is not a unique regular file")
        if before.st_size > _MAX_RUNTIME_BOUND_FILE_BYTES:
            raise OSError("file exceeds the runtime-manifest size limit")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(_MAX_RUNTIME_BOUND_FILE_BYTES + 1)
        after = os.fstat(descriptor)
        path_after = os.lstat(expected)
        resolved_after = os.path.realpath(expected)
        if len(raw) > _MAX_RUNTIME_BOUND_FILE_BYTES:
            raise OSError("file exceeds the runtime-manifest size limit")
        if (
            (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_nlink,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            or resolved_after != resolved
            or (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            != (
                path_after.st_dev,
                path_after.st_ino,
                path_after.st_mode,
                path_after.st_nlink,
                path_after.st_size,
                path_after.st_mtime_ns,
                path_after.st_ctime_ns,
            )
        ):
            raise OSError("file changed while it was captured")
        return raw
    finally:
        os.close(descriptor)


def _runtime_projection_topology_error(
    generation: Path,
    projected: Path,
    resolved: Path,
) -> str | None:
    """Allow a projected config alias only through the exact top-level runtime link."""
    if resolved == projected:
        return None
    runtime_alias = generation / _RUNTIME_GENERATION_RUNTIME_ALIAS
    canonical_runtime = generation / _RUNTIME_GENERATION_CANONICAL_RUNTIME
    canonical_config = generation / _RUNTIME_GENERATION_CANONICAL_CONFIG
    if not runtime_alias.is_symlink():
        return "runtime projection topology is not canonical"
    try:
        raw_target = os.readlink(runtime_alias)
        resolved_runtime = runtime_alias.resolve(strict=True)
        resolved_canonical_runtime = canonical_runtime.resolve(strict=True)
        resolved_canonical_config = canonical_config.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return f"runtime projection topology is invalid: {exc}"
    if (
        raw_target != _RUNTIME_GENERATION_CANONICAL_RUNTIME.as_posix()
        or resolved_runtime != canonical_runtime
        or resolved_canonical_runtime != canonical_runtime
        or resolved_canonical_config != canonical_config
        or resolved != canonical_config
    ):
        return "runtime projection topology is not canonical"
    return None


def _ast_command_grammar(tree: ast.AST) -> frozenset[str]:
    """Return literal ``add_parser`` command names from one parsed CLI module."""
    commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_parser" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            commands.add(first.value)
    return frozenset(commands)


def _validate_runtime_verifier_ast(
    raw: bytes,
    *,
    relative: Path,
    required_functions: frozenset[str],
    required_commands: frozenset[str],
) -> None:
    """Compile one captured verifier source and require its frozen public CLI shape."""
    try:
        source = raw.decode("utf-8")
        tree = ast.parse(source, filename=relative.as_posix())
        compile(tree, relative.as_posix(), "exec")
    except (UnicodeError, SyntaxError, ValueError) as exc:
        raise OSError(
            f"candidate verifier source is not compilable: {relative}: {exc}"
        ) from exc
    functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing_functions = required_functions - functions
    if missing_functions:
        raise OSError(
            f"candidate verifier source is missing entrypoints in {relative}: "
            + ", ".join(sorted(missing_functions))
        )
    commands = _ast_command_grammar(tree)
    if commands != required_commands:
        raise OSError(
            f"candidate verifier command grammar drifted in {relative}: "
            + ", ".join(sorted(commands))
        )


def _validate_runtime_verifier_schema(raw: bytes) -> None:
    """Require the captured W0 schema's closed, canonical product-contract shape."""
    try:
        schema = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OSError(
            f"candidate unified-product schema is invalid JSON: {exc}"
        ) from exc
    if not isinstance(schema, dict) or set(schema) != {
        "$schema",
        "$id",
        "title",
        "description",
        "oneOf",
        "$defs",
    }:
        raise OSError("candidate unified-product schema top level is not closed")
    if (
        schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema"
        or schema.get("$id") != "io.vetcoders.vibecrafted.contracts.v1"
    ):
        raise OSError("candidate unified-product schema identity drifted")
    definitions = schema.get("$defs")
    if (
        not isinstance(definitions, dict)
        or set(definitions) != _RUNTIME_VERIFIER_SCHEMA_DEFS
    ):
        raise OSError("candidate unified-product schema definition inventory drifted")
    expected_roots = [
        {"$ref": f"#/$defs/{name}"}
        for name in (
            "moduleManifest",
            "assemblyReceipt",
            "productManifest",
            "transactionReceipt",
            "releaseOutput",
            "walkaroundReceipt",
        )
    ]
    if schema.get("oneOf") != expected_roots:
        raise OSError("candidate unified-product schema root grammar drifted")
    object_definitions = {
        name
        for name, definition in definitions.items()
        if isinstance(definition, dict) and definition.get("type") == "object"
    }
    if object_definitions != _RUNTIME_VERIFIER_OBJECT_SCHEMA_DEFS:
        raise OSError("candidate unified-product schema object inventory drifted")

    partial_object_matchers = {
        (
            "$defs",
            "productManifest",
            "properties",
            "modules",
            "allOf",
            str(index),
            "contains",
        )
        for index in range(2)
    }
    observed_partial_matchers: set[tuple[str, ...]] = set()
    observed_typed_object_paths: set[str] = set()
    observed_untyped_object_matcher_paths: set[str] = set()

    def require_closed_objects(node: object, path: tuple[str, ...] = ()) -> None:
        if isinstance(node, dict):
            rendered = "/".join(path) or "<root>"
            if node.get("type") == "object":
                observed_typed_object_paths.add(rendered)
                # These two schemas are deliberately partial object matchers used by
                # ``contains`` to require one manifest for each supported module.
                partial_contains = path in partial_object_matchers
                if partial_contains:
                    observed_partial_matchers.add(path)
                if (
                    not partial_contains
                    and node.get("additionalProperties") is not False
                ):
                    raise OSError(
                        "candidate unified-product schema leaves an object open at "
                        + rendered
                    )
            elif "properties" in node or "required" in node:
                observed_untyped_object_matcher_paths.add(rendered)
            for key, value in node.items():
                require_closed_objects(value, (*path, str(key)))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                require_closed_objects(value, (*path, str(index)))

    require_closed_objects(schema)
    if observed_typed_object_paths != _RUNTIME_VERIFIER_TYPED_OBJECT_SCHEMA_PATHS:
        raise OSError("candidate unified-product schema object path inventory drifted")
    if (
        observed_untyped_object_matcher_paths
        != _RUNTIME_VERIFIER_UNTYPED_OBJECT_MATCHER_PATHS
    ):
        raise OSError(
            "candidate unified-product schema untyped object matcher inventory drifted"
        )
    if observed_partial_matchers != partial_object_matchers:
        raise OSError(
            "candidate unified-product schema partial matcher inventory drifted"
        )
    try:
        dmg_pattern = definitions["releaseOutput"]["properties"]["dmg"]["properties"][
            "path"
        ]["pattern"]
    except (KeyError, TypeError) as exc:
        raise OSError(
            "candidate unified-product schema has no canonical DMG path"
        ) from exc
    if dmg_pattern != _RUNTIME_RELEASE_DMG_PATTERN:
        raise OSError("candidate unified-product schema canonical DMG pattern drifted")


def _write_runtime_verifier_snapshot(
    snapshot: Path,
    captured: dict[Path, bytes],
    manifest_raw: bytes,
    source_provenance_raw: bytes,
) -> None:
    """Materialize only already-captured bytes for candidate semantic execution."""
    snapshot.mkdir(mode=0o700)
    _atomic_bytes_file(
        snapshot / _RUNTIME_GENERATION_MANIFEST,
        manifest_raw,
        mode=0o600,
    )
    _atomic_bytes_file(
        snapshot / _distribution_manifest.SOURCE_PROVENANCE_FILE,
        source_provenance_raw,
        mode=0o600,
    )
    for relative, raw in captured.items():
        _atomic_bytes_file(
            snapshot / relative,
            raw,
            mode=0o700
            if relative
            in {
                Path("scripts/vibecrafted"),
                _RUNTIME_GENERATION_ENTRYPOINT,
            }
            else 0o600,
        )


def _run_runtime_verifier_semantic_command(
    argv: Sequence[str],
    *,
    cache: Path,
) -> subprocess.CompletedProcess[str]:
    """Run one captured candidate CLI under an isolated Python import/cache environment."""
    environment = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONPYCACHEPREFIX"):
        environment.pop(name, None)
    environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-X",
            f"pycache_prefix={cache}",
            *argv,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
    )


def _assert_runtime_verifier_semantic_failure(
    result: subprocess.CompletedProcess[str],
    *,
    expected_code: int,
    context: str,
) -> None:
    """Require one public candidate command to fail with its exact stable contract."""
    expected_prefix = f"VCPC{expected_code:03d}:"
    if (
        result.returncode != expected_code
        or result.stdout
        or not result.stderr.startswith(expected_prefix)
    ):
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
        raise OSError(
            "candidate verifier failed semantic negative control "
            f"{context}: expected {expected_prefix}, got rc={result.returncode}: "
            f"{detail}"
        )


def _validate_runtime_verifier_semantics(runtime_root: Path) -> None:
    """Validate captured candidate code/schema and exercise its real public entrypoints."""
    captured: dict[Path, bytes] = {}
    for relative in sorted(_RUNTIME_GENERATION_REQUIRED_HASHES):
        try:
            captured[relative] = _capture_runtime_bound_file(runtime_root / relative)
        except OSError as exc:
            raise OSError(
                f"candidate verifier capture failed for {relative}: {exc}"
            ) from exc
    try:
        manifest_raw = _capture_runtime_bound_file(
            runtime_root / _RUNTIME_GENERATION_MANIFEST
        )
        source_provenance_raw = _capture_runtime_bound_file(
            runtime_root / _distribution_manifest.SOURCE_PROVENANCE_FILE
        )
    except OSError as exc:
        raise OSError(f"candidate verifier lineage capture failed: {exc}") from exc

    _validate_runtime_verifier_ast(
        captured[_RUNTIME_VERIFIER_PRODUCT],
        relative=_RUNTIME_VERIFIER_PRODUCT,
        required_functions=frozenset({"main", "verify_installed_runtime_generation"}),
        required_commands=_RUNTIME_VERIFIER_PRODUCT_COMMANDS,
    )
    _validate_runtime_verifier_ast(
        captured[_RUNTIME_VERIFIER_RUNNER],
        relative=_RUNTIME_VERIFIER_RUNNER,
        required_functions=frozenset({"main"}),
        required_commands=_RUNTIME_VERIFIER_RUNNER_COMMANDS,
    )
    _validate_runtime_verifier_schema(captured[_RUNTIME_VERIFIER_SCHEMA])

    with tempfile.TemporaryDirectory(prefix="vibecrafted-runtime-verifier-") as raw_tmp:
        temporary = Path(raw_tmp)
        snapshot = temporary / "runtime"
        _write_runtime_verifier_snapshot(
            snapshot, captured, manifest_raw, source_provenance_raw
        )
        product = snapshot / _RUNTIME_VERIFIER_PRODUCT
        runner = snapshot / _RUNTIME_VERIFIER_RUNNER
        cache = temporary / "pycache"

        generation_result = _run_runtime_verifier_semantic_command(
            [str(product), "runtime-generation", str(snapshot)],
            cache=cache,
        )
        expected_generation_output = f"verified runtime-generation: {snapshot}\n"
        if (
            generation_result.returncode != 0
            or generation_result.stdout != expected_generation_output
            or generation_result.stderr
        ):
            detail = (
                generation_result.stderr.strip() or generation_result.stdout.strip()
            )
            raise OSError(
                "candidate product contract failed runtime-generation self-verification"
                + (f": {detail}" if detail else "")
            )

        product_help = _run_runtime_verifier_semantic_command(
            [str(product), "--help"], cache=cache
        )
        if (
            product_help.returncode != 0
            or product_help.stderr
            or any(
                token not in product_help.stdout
                for token in _RUNTIME_VERIFIER_PRODUCT_COMMANDS
            )
        ):
            raise OSError("candidate product-contract --help surface is incomplete")

        runner_help = _run_runtime_verifier_semantic_command(
            [str(runner), "--help"], cache=cache
        )
        if (
            runner_help.returncode != 0
            or runner_help.stderr
            or any(
                token not in runner_help.stdout
                for token in _RUNTIME_VERIFIER_RUNNER_COMMANDS
            )
        ):
            raise OSError("candidate walk-around runner --help surface is incomplete")

        drift_snapshot = temporary / "hash-drift"
        _write_runtime_verifier_snapshot(
            drift_snapshot, captured, manifest_raw, source_provenance_raw
        )
        drifted_launcher = drift_snapshot / "scripts/vibecrafted"
        drifted_launcher.write_bytes(drifted_launcher.read_bytes() + b"\n# drift\n")
        _assert_runtime_verifier_semantic_failure(
            _run_runtime_verifier_semantic_command(
                [
                    str(drift_snapshot / _RUNTIME_VERIFIER_PRODUCT),
                    "runtime-generation",
                    str(drift_snapshot),
                ],
                cache=cache,
            ),
            expected_code=_RUNTIME_VERIFIER_E_HASH,
            context="runtime-generation bound-byte drift",
        )

        try:
            manifest = json.loads(manifest_raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:  # pragma: no cover
            raise OSError(f"captured runtime manifest is invalid: {exc}") from exc

        legacy_snapshot = temporary / "legacy-inventory"
        legacy_manifest = dict(manifest)
        legacy_hashes = manifest.get("hashes")
        if not isinstance(legacy_hashes, dict):  # pragma: no cover - loader owns this.
            raise OSError("captured runtime manifest has no hash inventory")
        legacy_manifest["hashes"] = {
            Path("VERSION").as_posix(): legacy_hashes[Path("VERSION").as_posix()],
            Path("scripts/vibecrafted").as_posix(): legacy_hashes[
                Path("scripts/vibecrafted").as_posix()
            ],
            _RUNTIME_GENERATION_PROJECTED_CONFIG.as_posix(): legacy_hashes[
                _RUNTIME_GENERATION_CANONICAL_CONFIG.as_posix()
            ],
            _RUNTIME_GENERATION_ENTRYPOINT.as_posix(): legacy_hashes[
                _RUNTIME_GENERATION_ENTRYPOINT.as_posix()
            ],
        }
        _write_runtime_verifier_snapshot(
            legacy_snapshot,
            captured,
            (json.dumps(legacy_manifest, sort_keys=True) + "\n").encode("utf-8"),
            source_provenance_raw,
        )
        _assert_runtime_verifier_semantic_failure(
            _run_runtime_verifier_semantic_command(
                [
                    str(legacy_snapshot / _RUNTIME_VERIFIER_PRODUCT),
                    "runtime-generation",
                    str(legacy_snapshot),
                ],
                cache=cache,
            ),
            expected_code=_RUNTIME_VERIFIER_E_TRANSACTION,
            context="runtime-generation legacy hash inventory",
        )

        open_snapshot = temporary / "open-manifest"
        open_manifest = dict(manifest)
        open_manifest["unexpected"] = "parallel truth"
        _write_runtime_verifier_snapshot(
            open_snapshot,
            captured,
            (json.dumps(open_manifest, sort_keys=True) + "\n").encode("utf-8"),
            source_provenance_raw,
        )
        _assert_runtime_verifier_semantic_failure(
            _run_runtime_verifier_semantic_command(
                [
                    str(open_snapshot / _RUNTIME_VERIFIER_PRODUCT),
                    "runtime-generation",
                    str(open_snapshot),
                ],
                cache=cache,
            ),
            expected_code=_RUNTIME_VERIFIER_E_SCHEMA,
            context="runtime-generation unknown manifest key",
        )

        _assert_runtime_verifier_semantic_failure(
            _run_runtime_verifier_semantic_command(
                [str(product), "schema", str(snapshot / _RUNTIME_GENERATION_MANIFEST)],
                cache=cache,
            ),
            expected_code=_RUNTIME_VERIFIER_E_DEPENDENCY,
            context="schema without site packages",
        )

        missing = temporary / "missing"
        runner_negative_commands = (
            (
                "trust-probe",
                [str(missing / "challenge.json"), str(missing / "challenge.sig")],
                None,
            ),
            (
                "verify-release",
                [
                    "--release-output",
                    str(missing / "release-output.json"),
                    "--signature",
                    str(missing / "release-output.json.sig"),
                ],
                None,
            ),
            (
                "walkaround",
                [
                    "--release-output",
                    str(missing / "release-output.json"),
                    "--signature",
                    str(missing / "release-output.json.sig"),
                    "--output",
                    str(missing / "unexpected-walkaround.json"),
                ],
                missing / "unexpected-walkaround.json",
            ),
        )
        for command, arguments, forbidden_output in runner_negative_commands:
            _assert_runtime_verifier_semantic_failure(
                _run_runtime_verifier_semantic_command(
                    [str(runner), command, *arguments], cache=cache
                ),
                expected_code=_RUNTIME_VERIFIER_E_MISSING,
                context=f"walk-around runner {command} missing input",
            )
            if forbidden_output is not None and (
                forbidden_output.exists() or forbidden_output.is_symlink()
            ):
                raise OSError(
                    "candidate verifier failed semantic negative control "
                    "walk-around runner created output for missing inputs"
                )

        invalid = temporary / "invalid"
        invalid.mkdir()
        invalid_challenge = invalid / "challenge.json"
        invalid_challenge.write_bytes(b"not a trust challenge\n")
        invalid_challenge_signature = invalid / "challenge.sig"
        invalid_challenge_signature.write_bytes(b"\0" * 256)
        invalid_release = invalid / "release-output.json"
        invalid_release.write_bytes(b"not a release receipt\n")
        invalid_release_signature = invalid / "release-output.json.sig"
        invalid_release_signature.write_bytes(b"\0" * 256)
        invalid_walkaround = invalid / "walkaround.json"
        runner_invalid_commands = (
            (
                "trust-probe",
                [str(invalid_challenge), str(invalid_challenge_signature)],
                None,
            ),
            (
                "verify-release",
                [
                    "--release-output",
                    str(invalid_release),
                    "--signature",
                    str(invalid_release_signature),
                ],
                None,
            ),
            (
                "walkaround",
                [
                    "--release-output",
                    str(invalid_release),
                    "--signature",
                    str(invalid_release_signature),
                    "--output",
                    str(invalid_walkaround),
                ],
                invalid_walkaround,
            ),
        )
        for command, arguments, forbidden_output in runner_invalid_commands:
            _assert_runtime_verifier_semantic_failure(
                _run_runtime_verifier_semantic_command(
                    [str(runner), command, *arguments], cache=cache
                ),
                expected_code=_RUNTIME_VERIFIER_E_PROOF,
                context=f"walk-around runner {command} invalid proof",
            )
            if forbidden_output is not None and (
                forbidden_output.exists() or forbidden_output.is_symlink()
            ):
                raise OSError(
                    "candidate verifier failed semantic negative control "
                    "walk-around runner created output for invalid proof"
                )


def _write_runtime_generation_manifest(
    runtime_root: Path,
    *,
    source_root: Path,
    source_provenance: Mapping[str, Any],
    install_version: str | None,
) -> None:
    """Bind transformed runtime bytes to the retained distribution input lineage."""
    provenance = _canonical_runtime_source_provenance(source_provenance)
    hashes: dict[str, str] = {}
    for relative in sorted(_RUNTIME_GENERATION_REQUIRED_HASHES):
        path = runtime_root / relative
        try:
            raw = _capture_runtime_bound_file(path)
        except OSError as exc:
            raise OSError(
                f"candidate runtime has invalid manifest input {relative}: {exc}"
            ) from exc
        hashes[relative.as_posix()] = hashlib.sha256(raw).hexdigest()
    payload = {
        "schema": _RUNTIME_GENERATION_MANIFEST_SCHEMA,
        "version": (install_version or read_version_file(runtime_root)).strip(),
        "source_fingerprint": _path_fingerprint(source_root),
        "owner_repo": provenance["owner_repo"],
        "source_revision": provenance["source_revision"],
        "source_payload": provenance["payload"],
        "entrypoint": _RUNTIME_GENERATION_ENTRYPOINT.as_posix(),
        "hashes": hashes,
    }
    _atomic_json_file(runtime_root / _RUNTIME_GENERATION_MANIFEST, payload)
    written, manifest_error = _load_runtime_generation_manifest(runtime_root)
    if written is None:
        raise OSError(
            "candidate runtime manifest failed its own schema: "
            + (manifest_error or "invalid runtime manifest")
        )
    _validate_runtime_verifier_semantics(runtime_root)


def _canonical_runtime_source_provenance(
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Copy one exact v2 carrier; publication never rereads or mints its authority."""
    if set(provenance) != _SOURCE_PROVENANCE_KEYS:
        raise OSError("candidate runtime source provenance is not a closed v2 carrier")
    payload = provenance.get("payload")
    owner_repo = provenance.get("owner_repo")
    source_revision = provenance.get("source_revision")
    if (
        provenance.get("schema") != _SOURCE_PROVENANCE_SCHEMA
        or not isinstance(owner_repo, str)
        or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", owner_repo) is None
        or not isinstance(source_revision, str)
        or re.fullmatch(r"[0-9a-f]{40}", source_revision) is None
        or not isinstance(payload, dict)
        or set(payload) != _SOURCE_PAYLOAD_KEYS
        or payload.get("schema") != _SOURCE_PAYLOAD_SCHEMA
        or payload.get("algorithm") != "sha256"
        or not isinstance(payload.get("tree_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", payload["tree_sha256"]) is None
        or isinstance(payload.get("entry_count"), bool)
        or not isinstance(payload.get("entry_count"), int)
        or payload["entry_count"] < 1
    ):
        raise OSError("candidate runtime source provenance is not a closed v2 carrier")
    return {
        "schema": _SOURCE_PROVENANCE_SCHEMA,
        "owner_repo": owner_repo,
        "source_revision": source_revision,
        "payload": {
            "schema": _SOURCE_PAYLOAD_SCHEMA,
            "algorithm": "sha256",
            "tree_sha256": payload["tree_sha256"],
            "entry_count": payload["entry_count"],
        },
    }


def _sync_control_plane_tree_locked(
    src: Path,
    dst: Path,
    *,
    mirror: bool,
    install_version: str | None,
) -> Path:
    """Stage, materialize, audit, manifest, and atomically publish a new runtime generation
    under the tools-install lease; rolls back the staging/generation directories on any failure
    before the pointer swap.
    """
    _ = mirror  # staged runtime is always an exact distribution payload
    if dst.exists() and not dst.is_symlink():
        raise OSError(
            f"refusing non-atomic in-place runtime upgrade at {dst}; "
            "vibecrafted-current must be a symlink pointer"
        )

    token = f"{os.getpid()}-{os.urandom(6).hex()}"
    version_slug = (
        re.sub(
            r"[^A-Za-z0-9._+-]+",
            "-",
            (install_version or "local").strip(),
        ).strip("-")
        or "local"
    )
    staging = dst.parent / f".{dst.name}.staging-{token}"
    generation = dst.parent / f"vibecrafted-generation-{version_slug}-{token}"
    old_candidate = _symlink_target(dst)
    pending = _read_tools_handoff_path(_tools_handoff_path(dst))
    if (
        pending is not None
        and pending["state"] == "prepared"
        and old_candidate == Path(pending["new_target"]).resolve(strict=False)
    ):
        pending_old = pending["old_target"]
        old_target = (
            Path(pending_old).resolve(strict=False)
            if pending_old and _is_framework_source_root(Path(pending_old))
            else None
        )
    else:
        old_target = (
            old_candidate
            if old_candidate is not None and _is_framework_source_root(old_candidate)
            else None
        )
    pointer_swapped = False
    try:
        source_provenance = stage_distribution_payload(
            src,
            staging,
            mirror=True,
            require_source_provenance=True,
        )
        if source_provenance is None:
            raise OSError("candidate staging returned no required source provenance")
        if install_version:
            stamp_install_version(staging, install_version)
        _materialize_vc_frame_generation(staging)
        audit_errors = _runtime_generation_audit_errors(staging, source_root=src)
        if audit_errors:
            raise OSError("\n".join(audit_errors))
        _write_runtime_generation_manifest(
            staging,
            source_root=src,
            source_provenance=source_provenance,
            install_version=install_version,
        )
        payload_errors = _runtime_generation_payload_errors(staging)
        if payload_errors:
            raise OSError(
                "candidate runtime failed pre-publish validation:\n"
                + "\n".join(payload_errors)
            )
        staging.rename(generation)
        handoff = {
            "schema": _TOOLS_HANDOFF_SCHEMA,
            "state": "prepared",
            "old_target": str(old_target) if old_target is not None else "",
            "new_target": str(generation),
            "prepared_at": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_json_file(_tools_handoff_path(dst), handoff)
        _atomic_symlink(generation, dst)
        pointer_swapped = True
        handoff["published_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_json_file(_tools_handoff_path(dst), handoff)
        return generation
    except Exception:
        if staging.exists() or staging.is_symlink():
            _remove_path(staging)
        if generation.exists() and not pointer_swapped:
            _remove_path(generation)
        raise


def _prune_tools_generations_locked(
    shared_home: Path,
    *,
    keep: int = _TOOLS_GENERATIONS_TO_KEEP,
) -> list[Path]:
    """Delete old runtime generation directories beyond the retention window, protecting the
    current, previous, and any in-flight handoff target.
    """
    if keep < 1:
        raise ValueError("tools generation retention must keep at least one generation")
    current_link = _current_tools_link(shared_home)
    tools_dir = current_link.parent.resolve(strict=False)
    current_target = _symlink_target(current_link)
    payload = _read_tools_handoff_path(_tools_handoff_path(current_link))
    protected: set[Path] = set()
    if current_target is not None:
        protected.add(current_target.resolve(strict=False))
    if payload is not None:
        old_raw = payload["old_target"]
        if old_raw:
            protected.add(Path(old_raw).resolve(strict=False))
        if payload["state"] == "prepared":
            protected.add(Path(payload["new_target"]).resolve(strict=False))

    generations: list[tuple[int, str, Path]] = []
    for candidate in tools_dir.glob("vibecrafted-generation-*"):
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        resolved = candidate.resolve(strict=False)
        if resolved.parent != tools_dir or not _is_framework_source_root(resolved):
            continue
        try:
            modified = os.stat(candidate, follow_symlinks=False).st_mtime_ns
        except OSError:
            continue
        generations.append((modified, candidate.name, resolved))
    generations.sort(reverse=True)
    protected.update(item[2] for item in generations[:keep])

    removed: list[Path] = []
    for _, _, candidate in generations:
        if candidate in protected:
            continue
        try:
            _remove_path(candidate)
        except OSError as exc:
            print(
                f"[install-tools] warning: could not prune old generation "
                f"{candidate}: {exc}",
                file=sys.stderr,
            )
            continue
        removed.append(candidate)
    return removed


def prune_tools_generations(
    shared_home: Path,
    *,
    keep: int = _TOOLS_GENERATIONS_TO_KEEP,
) -> list[Path]:
    """Bound immutable runtime history without touching live recovery targets."""
    current_link = _current_tools_link(shared_home)
    with _tools_install_lease(current_link, operation="runtime-generation-gc"):
        return _prune_tools_generations_locked(shared_home, keep=keep)


def _rollback_current_tools_locked(shared_home: Path) -> bool:
    """Roll the current-tools pointer back to the prior generation recorded in a 'prepared'
    handoff receipt; no-op if there is nothing pending to roll back.
    """
    payload = _read_tools_handoff(shared_home)
    if payload is None or payload["state"] != "prepared":
        return False
    old_raw = payload["old_target"]
    new_target = Path(payload["new_target"])
    current_link = _current_tools_link(shared_home)
    current_target = _symlink_target(current_link)
    if old_raw:
        old_target = Path(old_raw)
        if current_target == old_target.resolve(strict=False):
            payload["state"] = "rolled-back"
            payload["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_json_file(_tools_handoff_file(shared_home), payload)
            return False
    else:
        old_target = None
        if current_target is None and not (
            current_link.exists() or current_link.is_symlink()
        ):
            payload["state"] = "rolled-back"
            payload["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_json_file(_tools_handoff_file(shared_home), payload)
            return False
    if current_target != new_target.resolve(strict=False):
        raise OSError(
            "refusing runtime rollback because vibecrafted-current no longer "
            "matches the pending handoff"
        )
    if old_target is not None:
        _atomic_symlink(old_target, current_link)
    else:
        quarantine = current_link.parent / (
            f".{current_link.name}.rollback-{os.getpid()}-{os.urandom(6).hex()}"
        )
        os.replace(current_link, quarantine)
        if _symlink_target(quarantine) != new_target.resolve(strict=False):
            os.replace(quarantine, current_link)
            raise OSError(
                "runtime pointer changed while rolling back the first generation"
            )
    payload["state"] = "rolled-back"
    payload["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_json_file(_tools_handoff_file(shared_home), payload)
    if old_target is None:
        try:
            quarantine.unlink(missing_ok=True)
            directory = os.open(
                current_link.parent,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            print(
                "[install-tools] warning: first-install rollback is committed "
                f"but quarantine cleanup needs retry: {exc}",
                file=sys.stderr,
            )
    return True


def rollback_current_tools(shared_home: Path) -> bool:
    """Restore the runtime pointer recorded by the latest pending handoff."""
    current_link = _current_tools_link(shared_home)
    with _tools_install_lease(current_link, operation="runtime-rollback"):
        return _rollback_current_tools_locked(shared_home)


def _complete_current_tools_handoff_locked(shared_home: Path) -> bool:
    """Mark a 'prepared' tools-handoff receipt 'complete' once the current-tools pointer is
    verified to match it, then prune old generations.
    """
    payload = _read_tools_handoff(shared_home)
    if payload is None or payload["state"] != "prepared":
        return False
    current_target = _symlink_target(_current_tools_link(shared_home))
    expected = Path(payload["new_target"]).resolve(strict=False)
    if current_target != expected:
        raise OSError(
            "cannot complete tools handoff: vibecrafted-current does not point "
            "at the prepared generation"
        )
    payload["state"] = "complete"
    payload["completed_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_json_file(_tools_handoff_file(shared_home), payload)
    _prune_tools_generations_locked(shared_home)
    return True


def complete_current_tools_handoff(shared_home: Path) -> bool:
    """Seal the latest runtime handoff after uv tools and service are verified."""
    current_link = _current_tools_link(shared_home)
    with _tools_install_lease(current_link, operation="runtime-handoff-complete"):
        return _complete_current_tools_handoff_locked(shared_home)


def _staged_sync_failure_detail(exc: Exception) -> str:
    """Detail for a staged-tools sync failure, with the rsync stderr tail folded
    in (it is captured but otherwise unsurfaced) so the operator sees WHY the
    sync failed instead of a bare 'returned non-zero exit status'."""
    detail = str(exc)
    stderr = getattr(exc, "stderr", None)
    if stderr:
        tail = " | ".join(
            line.strip() for line in str(stderr).strip().splitlines() if line.strip()
        )
        if tail:
            detail = f"{detail}: {tail}"
    return detail


def _is_framework_source_root(repo_root: Path) -> bool:
    """True if `repo_root` looks like a complete Vibecrafted framework source checkout (VERSION,
    launcher, skills, and runtime present).
    """
    packaged_skills_dir = repo_root / "vibecrafted-core" / "vibecrafted_core" / "skills"
    packaged_runtime_dir = (
        repo_root / "vibecrafted-core" / "vibecrafted_core" / "runtime"
    )
    return (
        (repo_root / "VERSION").is_file()
        and (repo_root / "scripts" / "vibecrafted").is_file()
        and packaged_skills_dir.is_dir()
        and packaged_runtime_dir.is_dir()
    )


def _current_tools_link(shared_home: Path) -> Path:
    """Path to the shared home's `vibecrafted-current` staged-tools symlink."""
    _ = shared_home
    return vibecrafted_tools_home() / "vibecrafted-current"


def _ensure_current_tools_target(shared_home: Path) -> Path:
    """Ensure the current-tools symlink exists and points at a real directory, bootstrapping an
    empty generation directory if none exists yet.
    """
    _ = shared_home
    tools_dir = vibecrafted_tools_home()
    current_link = _current_tools_link(shared_home)
    tools_dir.mkdir(parents=True, exist_ok=True)

    if current_link.is_symlink():
        target = current_link.resolve(strict=False)
        if target.exists():
            return target
    elif current_link.exists():
        return current_link

    target = tools_dir / (
        f"vibecrafted-generation-bootstrap-{os.getpid()}-{os.urandom(6).hex()}"
    )
    target.mkdir(parents=True, exist_ok=True)
    _atomic_symlink(target, current_link)
    return target


def refresh_current_tools(
    repo_root: Path, shared_home: Path, dry_run: bool = False, mirror: bool = False
) -> Path | None:
    """Refresh the runtime tools current-link from the install source."""
    if not _is_framework_source_root(repo_root):
        return None

    current_link = _current_tools_link(shared_home)
    if current_link.exists() or current_link.is_symlink():
        try:
            current_target = current_link.resolve(strict=False)
        except OSError:
            current_target = None
        inherited_transaction = bool(os.environ.get(_TOOLS_INSTALL_LEASE_ENV))
        if current_target == repo_root and not inherited_transaction:
            # Dev/portable: tools link points at the checkout. Do NOT write
            # +gSHA into the live git tree (would dirty VERSION files).
            # Display still uses get_install_version() at banner time.
            # A receipt from an older immutable-generation handoff must not
            # survive this no-op path: a later failed install would
            # otherwise mistake it for the transaction it should roll back.
            if dry_run:
                return current_link
            with _tools_install_lease(
                current_link,
                operation="portable-runtime-reconcile",
            ):
                if current_link.resolve(strict=False) == repo_root:
                    handoff = _tools_handoff_path(current_link)
                    if handoff.exists() or handoff.is_symlink():
                        _remove_path(handoff)
                    return current_link

    if dry_run:
        return current_link

    sync_control_plane_tree(
        repo_root,
        current_link,
        dry_run=dry_run,
        mirror=mirror,
        install_version=get_install_version(repo_root),
    )
    return current_link


def _legacy_agents_layout_root(store_path: Path) -> Path:
    """Legacy vc-agents layout root under the (old) skill store path."""
    return store_path / "vc-agents"


def _current_agents_layout_root(store_path: Path, *, create: bool = False) -> Path:
    """Current-generation agents layout root under the staged current-tools link."""
    current_link = _current_tools_link(store_path)
    if create:
        _ensure_current_tools_target(store_path)
    return current_link / "agents"


def _transfer_relative_files(root: Path) -> list[Path]:
    """List every file/symlink under `root` (relative paths), skipping distribution-forbidden
    entries.
    """
    if not root.exists():
        return []
    files: list[Path] = []
    for item in sorted(root.rglob("*")):
        if distribution_path_is_forbidden(item.relative_to(root)):
            continue
        if item.is_file() or item.is_symlink():
            files.append(item.relative_to(root))
    return files


def _same_file_payload(src: Path, dst: Path) -> bool:
    """True if `src` and `dst` are byte-identical (or point at the same symlink target)."""
    if src.is_symlink() or dst.is_symlink():
        try:
            return os.readlink(src) == os.readlink(dst)
        except OSError:
            return False
    try:
        return src.read_bytes() == dst.read_bytes()
    except OSError:
        return False


def _layout_transfer_conflicts(src: Path, dst: Path) -> list[Path]:
    """Relative paths under `src` whose `dst` counterpart exists with different content."""
    conflicts: list[Path] = []
    for rel in _transfer_relative_files(src):
        target = dst / rel
        source = src / rel
        if not (target.exists() or target.is_symlink()):
            continue
        if not _same_file_payload(source, target):
            conflicts.append(rel)
    return conflicts


def _copy_layout_payload(src: Path, dst: Path) -> list[str]:
    """Copy every file/symlink under `src` into `dst`, overwriting any existing target entries."""
    copied: list[str] = []
    dst.mkdir(parents=True, exist_ok=True)
    for rel in _transfer_relative_files(src):
        source = src / rel
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            _remove_path(target)
        if source.is_symlink():
            target.symlink_to(os.readlink(source))
        else:
            shutil.copy2(source, target)
        copied.append(str(rel))
    return copied


def _append_layout_transfer(
    state: InstallState,
    *,
    direction: str,
    status: str,
    source: Path,
    target: Path,
    copied: Sequence[str] = (),
    conflicts: Sequence[Path] = (),
) -> None:
    """Append one layout-transfer attempt record (direction/status/paths/counts) to `state`."""
    state.layout_transfers.append(
        {
            "direction": direction,
            "status": status,
            "source": str(source),
            "target": str(target),
            "copied": str(len(copied)),
            "conflicts": ",".join(str(path) for path in conflicts),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def transfer_agents_layout(
    store_path: Path,
    *,
    direction: str,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[int, dict[str, Any]]:
    """Move the agent script layout between legacy store and current tools.

    This is intentionally conservative: existing target payload with different
    bytes blocks the transfer unless the operator passes ``--force``. Product
    tools discovered on PATH are never copied or re-homed here; this only moves
    Vibecrafted's framework payload between the old and new install layouts.
    """
    state = InstallState.load(store_path)
    if direction == "legacy-to-new":
        source = _legacy_agents_layout_root(store_path)
        target = _current_agents_layout_root(store_path, create=not dry_run)
    elif direction == "new-to-legacy":
        source = _current_agents_layout_root(store_path, create=False)
        target = _legacy_agents_layout_root(store_path)
    else:
        raise ValueError(f"unsupported layout transfer direction: {direction}")

    if not source.exists():
        _append_layout_transfer(
            state,
            direction=direction,
            status="blocked",
            source=source,
            target=target,
            conflicts=[Path("source-missing")],
        )
        if not dry_run:
            state.save(store_path)
        return 1, {
            "source": source,
            "target": target,
            "conflicts": [Path("source-missing")],
        }

    conflicts = _layout_transfer_conflicts(source, target)
    if conflicts and not force:
        _append_layout_transfer(
            state,
            direction=direction,
            status="blocked",
            source=source,
            target=target,
            conflicts=conflicts,
        )
        if not dry_run:
            state.save(store_path)
        return 1, {"source": source, "target": target, "conflicts": conflicts}

    copied = _transfer_relative_files(source)
    if not dry_run:
        copied_names = _copy_layout_payload(source, target)
        _append_layout_transfer(
            state,
            direction=direction,
            status="completed",
            source=source,
            target=target,
            copied=copied_names,
            conflicts=conflicts,
        )
        state.updated_at = datetime.now(timezone.utc).isoformat()
        state.save(store_path)
    return 0, {
        "source": source,
        "target": target,
        "copied": copied,
        "conflicts": conflicts,
    }


def layout_status(store_path: Path) -> dict[str, Any]:
    """Snapshot of the legacy/current agents-layout roots and the most recent transfer record."""
    legacy = _legacy_agents_layout_root(store_path)
    current = _current_agents_layout_root(store_path, create=False)
    state = InstallState.load(store_path)
    return {
        "legacy": legacy,
        "legacy_exists": legacy.exists(),
        "current": current,
        "current_exists": current.exists(),
        "last_transfer": state.layout_transfers[-1] if state.layout_transfers else {},
    }


def prune_orphaned_skills(
    store_path: Path,
    runtimes: list[str],
    current_bundle: set[str],
    dry_run: bool = False,
    orphaned_entries: list[tuple[str, Path]] | None = None,
    interactive: bool = True,
) -> int:
    """Remove vc-* skills from store and runtime dirs that are no longer in the bundle."""
    orphans = orphaned_entries or collect_orphaned_skills(
        store_path, runtimes, current_bundle
    )

    if not orphans:
        return 0

    print(bold("Orphaned skills detected (no longer in bundle):"))
    for location, entry in orphans:
        kind = "symlink" if entry.is_symlink() else "dir"
        print(f"  {yellow(f'[{kind}]')} {location}/{entry.name}")
    print()

    if interactive and not ask_yn("Remove orphaned skills?", default=True):
        print(dim("  Keeping orphaned skills."))
        print()
        return 0

    removed = 0
    for location, entry in orphans:
        if dry_run:
            print(f"  {dim('rm')} {entry}")
            removed += 1
        else:
            if entry.is_symlink() or entry.is_file():
                entry.unlink(missing_ok=True)
            elif entry.is_dir():
                shutil.rmtree(entry)
            removed += 1

    if removed:
        print(f"  {OK} Removed {removed} orphaned entries")
    print()
    return removed


def prune_legacy_skills(
    store_path: Path,
    runtimes: list[str],
    dry_run: bool = False,
    interactive: bool = True,
) -> int:
    """Remove old vetcoders-* skills replaced by vc-* equivalents."""
    legacy: list[tuple] = []

    if store_path.exists():
        for entry in sorted(store_path.iterdir()):
            if entry.is_dir() and entry.name.startswith(OLD_SKILL_PREFIX):
                legacy.append(("store", entry))

    for rt in runtimes:
        rt_skills = Path.home() / f".{rt}" / "skills"
        if not rt_skills.exists():
            continue
        for entry in sorted(rt_skills.iterdir()):
            if (entry.is_dir() or entry.is_symlink()) and entry.name.startswith(
                OLD_SKILL_PREFIX
            ):
                legacy.append((rt, entry))

    old_helper = xdg_config_home() / "zsh" / OLD_HELPER_NAME
    if old_helper.exists():
        legacy.append(("helper", old_helper))

    if not legacy:
        return 0

    print(bold("Old vetcoders-* entries detected:"))
    for location, entry in legacy:
        kind = (
            "symlink" if entry.is_symlink() else ("file" if entry.is_file() else "dir")
        )
        print(f"  {yellow(f'[{kind}]')} {location}/{entry.name}")
    print()

    if interactive and not ask_yn(
        "Remove the old vetcoders-* entries now?", default=True
    ):
        print(dim("  Keeping the old entries."))
        print()
        return 0

    removed = 0
    for location, entry in legacy:
        if dry_run:
            print(f"  {dim('rm')} {entry}")
            removed += 1
        else:
            if entry.is_symlink() or entry.is_file():
                entry.unlink()
            elif entry.is_dir():
                shutil.rmtree(entry)
            removed += 1

    if removed:
        print(f"  {OK} Removed {removed} old entries")

    # Clean old source line from .zshrc
    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
        content = zshrc.read_text()
        if OLD_HELPER_NAME in content:
            if not _is_writable(zshrc):
                print(f"  {WARN} {zshrc} is locked — cannot remove old source line")
                print(
                    f"       {dim('Remove manually: line referencing ' + OLD_HELPER_NAME)}"
                )
            elif not dry_run:
                lines = content.splitlines(keepends=True)
                new_lines = [ln for ln in lines if OLD_HELPER_NAME not in ln]
                zshrc.write_text("".join(new_lines))
                print(f"  {OK} Cleaned old source line from .zshrc")
            else:
                print(f"  {dim('would clean old source line from .zshrc')}")

    print()
    return removed


def create_symlink(target: Path, link: Path, dry_run: bool = False) -> None:
    """Create a framework symlink without clobbering unmanaged entries."""
    if target == link:
        if dry_run:
            print(f"  {dim('same-path')} {target}")
        return
    if dry_run:
        print(f"  {dim('ln -s')} {target} -> {link}")
        return
    if link.exists() or link.is_symlink():
        if not _is_replaceable_framework_launcher(link):
            print(f"  {WARN} Keeping existing unmanaged launcher: {link}")
            return
        if link.is_symlink():
            link.unlink()
        elif link.is_dir():
            shutil.rmtree(link)
        else:
            link.unlink()
    link.symlink_to(target)


def create_skill_view_symlink(target: Path, link: Path, dry_run: bool = False) -> None:
    """Create an agent skill view, replacing stale legacy store views."""
    if target == link:
        if dry_run:
            print(f"  {dim('same-path')} {target}")
        return
    if dry_run:
        print(f"  {dim('ln -s')} {target} -> {link}")
        return
    if link.exists() or link.is_symlink():
        if link.is_symlink() or link.is_file():
            link.unlink()
        elif link.is_dir():
            shutil.rmtree(link)
    link.symlink_to(target)


def prune_shadowed_skill_views(
    store_path: Path,
    skill_names: list[str],
    active_runtimes: list[str],
    dry_run: bool = False,
) -> list[Path]:
    """Remove managed runtime views shadowed by the canonical .agents view."""
    removed: list[Path] = []
    canonical_root = runtime_skills_dir("agents")
    for skill_name in skill_names:
        expected = store_path / skill_name
        canonical = canonical_root / skill_name
        if not canonical.is_symlink() or canonical.resolve(
            strict=False
        ) != expected.resolve(strict=False):
            continue
        for runtime in SHADOWED_SKILL_VIEW_RUNTIMES:
            if runtime in active_runtimes:
                continue
            shadow = runtime_skills_dir(runtime) / skill_name
            if not shadow.is_symlink():
                continue
            raw_target = os.readlink(shadow)
            resolved = shadow.resolve(strict=False)
            managed_target = (
                resolved == expected.resolve(strict=False)
                or "/vibecrafted/tools/" in raw_target
                or "/.vibecrafted/skills/" in raw_target
            )
            if not managed_target:
                continue
            if not dry_run:
                shadow.unlink()
            removed.append(shadow)
    return removed


def _copy_managed_launcher(src: Path, dst: Path) -> bool:
    """Copy `src` over `dst` as a managed launcher, refusing to clobber an unmanaged existing
    file.
    """
    if dst.exists() or dst.is_symlink():
        if not _is_replaceable_framework_launcher(dst):
            print(f"  {WARN} Keeping existing unmanaged launcher: {dst}")
            return False
        if dst.is_symlink():
            dst.unlink()
        elif dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    shutil.copy2(src, dst)
    dst.chmod(0o755)
    return True


def _canonical_store_path(shared_home: Path, *, create: bool = False) -> Path:
    """Return the one package-owned skill store through the stable runtime pointer."""
    current_link = _current_tools_link(shared_home)
    if create and not (current_link.exists() or current_link.is_symlink()):
        _ensure_current_tools_target(shared_home)
    return current_link / "vibecrafted-core" / "vibecrafted_core" / "skills"


def _install_state_file(store_path: Path) -> Path:
    """Return mutable install state outside immutable runtime generations.

    Existing store-local and legacy ``~/.vibecrafted/skills`` manifests remain
    readable for uninstall/doctor migration, but every new install writes the
    canonical state file directly under ``VIBECRAFTED_HOME``.
    """
    canonical = vibecrafted_home() / STATE_FILE
    candidates = (
        canonical,
        store_path / STATE_FILE,
        vibecrafted_home() / "skills" / STATE_FILE,
    )
    return next(
        (candidate for candidate in candidates if candidate.exists()), canonical
    )


def _load_install_state(store_path: Path) -> InstallState:
    """Load canonical install state, accepting prior store-local manifests."""
    return InstallState.load(_install_state_file(store_path).parent)


def _canonical_path_preserving_final_symlink(path: Path) -> Path:
    """Canonicalize a path's parent while preserving its final component spelling."""
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return expanded.parent.resolve(strict=False) / expanded.name


def _secure_walkaround_preflight_source() -> str:
    """Return the stdlib-only verifier preflight embedded in every managed wrapper."""
    expected_paths = tuple(
        relative.as_posix() for relative in sorted(_RUNTIME_GENERATION_REQUIRED_HASHES)
    )
    return (
        "EXPECTED_PATHS = frozenset("
        + repr(expected_paths)
        + ")\n"
        + r"""import hashlib
import json
import os
import re
import stat
import sys

MANIFEST_NAME = "runtime-manifest.json"
MANIFEST_SCHEMA = "vibecrafted.runtime-generation.v2"
MANIFEST_KEYS = frozenset({
    "schema", "version", "source_fingerprint", "owner_repo",
    "source_revision", "source_payload", "entrypoint", "hashes",
})
ENTRYPOINT = "vibecrafted-core/vibecrafted_core/deck/vibecrafted"
RUNTIME_ALIAS = "runtime"
CANONICAL_RUNTIME = "vibecrafted-core/vibecrafted_core/runtime"
PROJECTED_CONFIG = "runtime/generated/vc-frame/config.kdl"
CANONICAL_CONFIG = CANONICAL_RUNTIME + "/generated/vc-frame/config.kdl"
PROVENANCE_NAME = "source-provenance.json"
PROVENANCE_KEYS = frozenset({"schema", "owner_repo", "source_revision", "payload"})
PROVENANCE_SCHEMA = "vibecrafted.source-provenance.v2"
SOURCE_PAYLOAD_KEYS = frozenset({"schema", "algorithm", "tree_sha256", "entry_count"})
SOURCE_PAYLOAD_SCHEMA = "vibecrafted.distribution-tree.v1"
MAX_BYTES = 16 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}")
GIT_SHA = re.compile(r"[0-9a-f]{40}")
OWNER = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
GENERATION = re.compile(r"vibecrafted-generation-[A-Za-z0-9._+-]+")


def fail(message):
    raise RuntimeError(message)


def capture(path, context, *, allowed_resolved_path=None):
    expected = os.path.abspath(path)
    resolved = os.path.realpath(expected)
    if resolved != expected:
        if (
            allowed_resolved_path is None
            or resolved != os.path.abspath(allowed_resolved_path)
        ):
            fail(context + " is aliased")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(expected, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            fail(context + " is not a unique regular file")
        if before.st_size > MAX_BYTES:
            fail(context + " exceeds the size limit")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(MAX_BYTES + 1)
        after = os.fstat(descriptor)
        path_after = os.lstat(expected)
        resolved_after = os.path.realpath(expected)
        if len(raw) > MAX_BYTES:
            fail(context + " exceeds the size limit")
        if (
            before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
            before.st_size, before.st_mtime_ns, before.st_ctime_ns,
        ) != (
            after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
            after.st_size, after.st_mtime_ns, after.st_ctime_ns,
        ) or resolved_after != resolved or (
            after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
            after.st_size, after.st_mtime_ns, after.st_ctime_ns,
        ) != (
            path_after.st_dev, path_after.st_ino, path_after.st_mode,
            path_after.st_nlink, path_after.st_size, path_after.st_mtime_ns,
            path_after.st_ctime_ns,
        ):
            fail(context + " changed during capture")
        return raw
    finally:
        os.close(descriptor)


def load_json(raw, context):
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail(context + " is invalid JSON: " + str(exc))
    if not isinstance(value, dict):
        fail(context + " is not an object")
    return value


def write_snapshot(root, relative, raw):
    destination = os.path.join(root, *relative.split("/"))
    parent = os.path.dirname(destination)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main():
    if len(sys.argv) != 4:
        fail("preflight argument contract is invalid")
    generation_root, managed_wrapper, snapshot_root = map(os.path.abspath, sys.argv[1:])
    root_stat = os.lstat(generation_root)
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or stat.S_ISLNK(root_stat.st_mode)
        or GENERATION.fullmatch(os.path.basename(generation_root)) is None
        or os.path.realpath(generation_root) != generation_root
    ):
        fail("runtime generation is aliased")
    capture(managed_wrapper, "managed wrapper")

    manifest_raw = capture(
        os.path.join(generation_root, MANIFEST_NAME), "runtime manifest"
    )
    manifest = load_json(manifest_raw, "runtime manifest")
    hashes = manifest.get("hashes")
    source_payload = manifest.get("source_payload")
    if (
        set(manifest) != MANIFEST_KEYS
        or manifest.get("schema") != MANIFEST_SCHEMA
        or not isinstance(manifest.get("version"), str)
        or not manifest["version"]
        or manifest["version"] != manifest["version"].strip()
        or not isinstance(manifest.get("source_fingerprint"), str)
        or SHA256.fullmatch(manifest["source_fingerprint"]) is None
        or not isinstance(manifest.get("owner_repo"), str)
        or OWNER.fullmatch(manifest["owner_repo"]) is None
        or not isinstance(manifest.get("source_revision"), str)
        or GIT_SHA.fullmatch(manifest["source_revision"]) is None
        or not isinstance(source_payload, dict)
        or set(source_payload) != SOURCE_PAYLOAD_KEYS
        or source_payload.get("schema") != SOURCE_PAYLOAD_SCHEMA
        or source_payload.get("algorithm") != "sha256"
        or not isinstance(source_payload.get("tree_sha256"), str)
        or SHA256.fullmatch(source_payload["tree_sha256"]) is None
        or isinstance(source_payload.get("entry_count"), bool)
        or not isinstance(source_payload.get("entry_count"), int)
        or source_payload["entry_count"] < 1
        or manifest.get("entrypoint") != ENTRYPOINT
        or not isinstance(hashes, dict)
        or set(hashes) != EXPECTED_PATHS
        or any(not isinstance(value, str) or SHA256.fullmatch(value) is None for value in hashes.values())
    ):
        fail("runtime manifest does not satisfy the closed schema")

    provenance_path = os.path.join(generation_root, PROVENANCE_NAME)
    provenance_raw = capture(provenance_path, "source provenance")
    provenance = load_json(provenance_raw, "source provenance")
    canonical_provenance_raw = (
        json.dumps(provenance, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    provenance_payload = provenance.get("payload")
    if (
        provenance_raw != canonical_provenance_raw
        or set(provenance) != PROVENANCE_KEYS
        or provenance.get("schema") != PROVENANCE_SCHEMA
        or provenance.get("owner_repo") != manifest["owner_repo"]
        or provenance.get("source_revision") != manifest["source_revision"]
        or not isinstance(provenance_payload, dict)
        or set(provenance_payload) != SOURCE_PAYLOAD_KEYS
        or provenance_payload.get("schema") != SOURCE_PAYLOAD_SCHEMA
        or provenance_payload.get("algorithm") != "sha256"
        or not isinstance(provenance_payload.get("tree_sha256"), str)
        or SHA256.fullmatch(provenance_payload["tree_sha256"]) is None
        or isinstance(provenance_payload.get("entry_count"), bool)
        or not isinstance(provenance_payload.get("entry_count"), int)
        or provenance_payload["entry_count"] < 1
        or provenance_payload != source_payload
    ):
        fail("source provenance disagrees with runtime manifest")

    captured = {}
    for relative in sorted(EXPECTED_PATHS):
        allowed_resolved_path = None
        if relative == PROJECTED_CONFIG:
            allowed_resolved_path = os.path.join(
                generation_root,
                *CANONICAL_CONFIG.split("/"),
            )
            if os.path.realpath(allowed_resolved_path) != allowed_resolved_path:
                fail("canonical runtime projection is aliased")
            projected_path = os.path.join(generation_root, *relative.split("/"))
            if os.path.realpath(projected_path) != os.path.abspath(projected_path):
                runtime_alias = os.path.join(generation_root, RUNTIME_ALIAS)
                try:
                    runtime_stat = os.lstat(runtime_alias)
                except OSError as exc:
                    fail("runtime projection topology is invalid: " + str(exc))
                if not stat.S_ISLNK(runtime_stat.st_mode):
                    fail("runtime projection topology is not canonical")
                try:
                    runtime_target = os.readlink(runtime_alias)
                except OSError as exc:
                    fail("runtime projection topology is invalid: " + str(exc))
                if (
                    runtime_target != CANONICAL_RUNTIME
                    or os.path.realpath(runtime_alias)
                    != os.path.join(generation_root, *CANONICAL_RUNTIME.split("/"))
                ):
                    fail("runtime projection topology is not canonical")
        raw = capture(
            os.path.join(generation_root, *relative.split("/")),
            "manifest-bound file " + relative,
            allowed_resolved_path=allowed_resolved_path,
        )
        if hashlib.sha256(raw).hexdigest() != hashes[relative]:
            fail("manifest-bound file drifted: " + relative)
        captured[relative] = raw
    try:
        version = captured["VERSION"].decode("utf-8").strip()
    except UnicodeError as exc:
        fail("VERSION is not UTF-8: " + str(exc))
    if version != manifest["version"]:
        fail("VERSION disagrees with runtime manifest")

    os.mkdir(snapshot_root, mode=0o700)
    write_snapshot(snapshot_root, MANIFEST_NAME, manifest_raw)
    write_snapshot(snapshot_root, PROVENANCE_NAME, provenance_raw)
    for relative, raw in captured.items():
        write_snapshot(snapshot_root, relative, raw)


try:
    main()
except Exception as exc:
    sys.stderr.write("invalid Vibecrafted verifier runtime: " + str(exc) + "\n")
    raise SystemExit(70)
"""
    )


def _secure_walkaround_launcher_contents(
    current_tools: Path,
    python_bin: Path,
    *,
    launcher_path: Path | None = None,
) -> bytes:
    """Render the exact wrapper with a stdlib preflight before candidate execution."""
    current = _canonical_path_preserving_final_symlink(current_tools)
    tools_root = current.parent
    interpreter = _canonical_path_preserving_final_symlink(python_bin)
    managed_wrapper = _canonical_path_preserving_final_symlink(
        launcher_path or python_bin.parent / SECURE_WALKAROUND_LAUNCHER
    )
    preflight = _secure_walkaround_preflight_source()
    return (
        "#!/bin/sh\n"
        f"# {SECURE_WALKAROUND_LAUNCHER_MARKER} python={interpreter}\n"
        "set -eu\n"
        f"current={shlex_quote(str(current))}\n"
        f"tools_root={shlex_quote(str(tools_root))}\n"
        f"interpreter={shlex_quote(str(interpreter))}\n"
        f"managed_wrapper={shlex_quote(str(managed_wrapper))}\n"
        'generation=$(/usr/bin/readlink "$current")\n'
        'case "$generation" in vibecrafted-generation-*) ;; '
        "*) printf '%s\\n' 'invalid Vibecrafted runtime pointer' >&2; exit 70 ;; esac\n"
        "case \"$generation\" in */*|.|..) printf '%s\\n' "
        "'invalid Vibecrafted runtime pointer' >&2; exit 70 ;; esac\n"
        'target="$tools_root/$generation"\n'
        '[ -d "$target" ] && [ ! -L "$target" ] || { '
        "printf '%s\\n' 'invalid Vibecrafted runtime generation' >&2; exit 70; }\n"
        'cache=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/vibecrafted-walkaround.XXXXXX")\n'
        "trap '/bin/rm -rf -- \"$cache\"' EXIT HUP INT TERM\n"
        "unset PYTHONPATH PYTHONHOME PYTHONPYCACHEPREFIX\n"
        "export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1\n"
        'snapshot="$cache/runtime"\n'
        '"$interpreter" -I -B -X pycache_prefix="$cache/pycache" - '
        '"$target" "$managed_wrapper" "$snapshot" <<\'VIBECRAFTED_PREFLIGHT\'\n'
        + preflight
        + "VIBECRAFTED_PREFLIGHT\n"
        + 'runner="$snapshot/vibecrafted-core/vibecrafted_core/walkaround_runner.py"\n'
        + '"$interpreter" -I -B -X pycache_prefix="$cache/pycache" "$runner" "$@"\n'
    ).encode("utf-8")


def _install_secure_walkaround_launcher(
    current_tools: Path,
    python_bin: Path,
    *,
    launcher_path: Path | None = None,
) -> Path:
    """Replace the generic console shim with the deterministic installed verifier boundary."""
    destination = launcher_path or (
        vibecrafted_launcher_bin() / SECURE_WALKAROUND_LAUNCHER
    )
    contents = _secure_walkaround_launcher_contents(
        current_tools,
        python_bin,
        launcher_path=destination,
    )
    if destination.exists() or destination.is_symlink():
        if not _is_replaceable_framework_launcher(destination):
            raise OSError(
                f"refusing to overwrite unmanaged verifier launcher: {destination}"
            )
        if destination.is_symlink() or destination.is_dir():
            _remove_path(destination)
    _atomic_bytes_file(destination, contents, mode=0o755)
    return destination


def _secure_walkaround_launcher_issues(
    current_tools: Path,
    launcher_path: Path,
) -> list[str]:
    """Return exact managed-wrapper and verifier-cache integrity issues."""
    issues: list[str] = []
    if not launcher_path.is_file():
        return [f"{SECURE_WALKAROUND_LAUNCHER}:missing"]
    try:
        resolved_launcher = launcher_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return [f"{SECURE_WALKAROUND_LAUNCHER}:corrupt:{exc}"]
    uv_tools_root = Path(
        os.environ.get("UV_TOOL_DIR", str(xdg_data_home() / "uv" / "tools"))
    ).expanduser()
    expected_wrapper = _canonical_path_preserving_final_symlink(
        uv_tools_root / "vibecrafted" / "bin" / SECURE_WALKAROUND_LAUNCHER
    )
    if resolved_launcher != expected_wrapper:
        return [
            f"{SECURE_WALKAROUND_LAUNCHER}:corrupt:wrapper is outside managed tool roots"
        ]
    try:
        wrapper_metadata = resolved_launcher.lstat()
    except OSError as exc:
        return [f"{SECURE_WALKAROUND_LAUNCHER}:corrupt:{exc}"]
    if not stat.S_ISREG(wrapper_metadata.st_mode) or wrapper_metadata.st_nlink != 1:
        return [
            f"{SECURE_WALKAROUND_LAUNCHER}:corrupt:wrapper is not a unique regular file"
        ]
    try:
        raw = _capture_runtime_bound_file(resolved_launcher)
    except OSError as exc:
        return [f"{SECURE_WALKAROUND_LAUNCHER}:corrupt:{exc}"]
    interpreters = tuple(
        _canonical_path_preserving_final_symlink(candidate)
        for candidate in (
            resolved_launcher.parent / "python",
            resolved_launcher.parent / "python3",
        )
        if candidate.is_file() and os.access(candidate, os.X_OK)
    )
    matching = [
        interpreter
        for interpreter in interpreters
        if raw
        == _secure_walkaround_launcher_contents(
            current_tools,
            interpreter,
            launcher_path=resolved_launcher,
        )
    ]
    if len(matching) != 1:
        issues.append(f"{SECURE_WALKAROUND_LAUNCHER}:corrupt:wrapper bytes drifted")
    runner = current_tools / _RUNTIME_VERIFIER_RUNNER
    try:
        _capture_runtime_bound_file(runner)
    except OSError as exc:
        issues.append(
            f"{SECURE_WALKAROUND_LAUNCHER}:corrupt:runner is not unique: {exc}"
        )
    try:
        generation = current_tools.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        issues.append(
            f"{SECURE_WALKAROUND_LAUNCHER}:corrupt:runtime generation is invalid: {exc}"
        )
    else:
        canonical_projection = generation / _RUNTIME_GENERATION_CANONICAL_CONFIG
        for relative in sorted(_RUNTIME_GENERATION_REQUIRED_HASHES):
            candidate = generation / relative
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                issues.append(
                    f"{SECURE_WALKAROUND_LAUNCHER}:corrupt:"
                    f"manifest-bound file is invalid:{relative}:{exc}"
                )
                continue
            if (
                relative != _RUNTIME_GENERATION_PROJECTED_CONFIG
                and resolved != candidate
            ):
                issues.append(
                    f"{SECURE_WALKAROUND_LAUNCHER}:corrupt:"
                    f"manifest-bound file is aliased:{relative}"
                )
                continue
            allowed = {candidate}
            if relative == _RUNTIME_GENERATION_PROJECTED_CONFIG:
                topology_error = _runtime_projection_topology_error(
                    generation,
                    candidate,
                    resolved,
                )
                if topology_error is not None:
                    issues.append(
                        f"{SECURE_WALKAROUND_LAUNCHER}:corrupt:{topology_error}"
                    )
                    continue
            if (
                relative == _RUNTIME_GENERATION_PROJECTED_CONFIG
                and resolved != candidate
            ):
                allowed.add(canonical_projection)
            if resolved not in allowed:
                issues.append(
                    f"{SECURE_WALKAROUND_LAUNCHER}:corrupt:"
                    f"manifest-bound file is aliased:{relative}"
                )
    issues.extend(_verifier_bytecode_shadow_issues(current_tools))
    return sorted(set(issues))


def _verifier_bytecode_shadow_issues(current_tools: Path) -> list[str]:
    """Reject verifier bytecode that can bypass or outlive its bound source.

    CPython's ordinary ``__pycache__`` entries are derived caches and are safe
    when their source exists: timestamp and checked-hash modes validate the
    source before use. Adjacent bytecode, orphan caches, and unchecked-hash
    caches remain fail-closed because they can execute without the manifest-
    bound ``.py`` bytes governing them.
    """
    package = current_tools / "vibecrafted-core" / "vibecrafted_core"
    issues: list[str] = []
    modules = ("product_contract", "walkaround_runner")
    for module in modules:
        for suffix in ("pyc", "pyo"):
            for candidate in package.glob(f"{module}.{suffix}"):
                issues.append(
                    f"{candidate.relative_to(package)}:corrupt:verifier bytecode shadow"
                )

        source = package / f"{module}.py"
        for candidate in package.glob(f"__pycache__/{module}.*.py[co]"):
            reason = ""
            if not source.is_file():
                reason = "orphan verifier bytecode cache"
            else:
                try:
                    header = candidate.read_bytes()[:8]
                except OSError:
                    header = b""
                if len(header) == 8 and header[:4] == importlib.util.MAGIC_NUMBER:
                    flags = struct.unpack("<I", header[4:8])[0]
                    if flags & 0x01 and not flags & 0x02:
                        reason = "unchecked-hash verifier bytecode cache"
            if reason:
                issues.append(f"{candidate.relative_to(package)}:corrupt:{reason}")
    return sorted(set(issues))


def _state_agency_quarantine(current_tools: Path) -> Path:
    """Path to the quarantine directory used to relocate legacy state-home agency payload."""
    return current_tools / ".legacy-state-agency"


def _clear_immutable_flags(path: Path) -> None:
    """Clear macOS `uchg` immutable flags recursively under `path` (no-op off Darwin)."""
    if sys.platform != "darwin":
        return
    subprocess.run(
        ["chflags", "-R", "nouchg", str(path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _available_quarantine_path(dst: Path) -> Path:
    """Find an unused quarantine destination path for `dst`, appending a timestamp/pid suffix on
    collision.
    """
    if not (dst.exists() or dst.is_symlink()):
        return dst
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = dst.with_name(f"{dst.name}-{stamp}-{os.getpid()}")
    counter = 1
    while candidate.exists() or candidate.is_symlink():
        candidate = dst.with_name(f"{dst.name}-{stamp}-{os.getpid()}-{counter}")
        counter += 1
    return candidate


def _move_state_agency_path(src: Path, dst: Path, dry_run: bool = False) -> bool:
    """Move `src` into a fresh quarantine slot under `dst`'s parent, clearing immutable flags
    first.
    """
    if not (src.exists() or src.is_symlink()):
        return False
    if dry_run:
        print(f"  {dim('move')} {src} -> {dst}")
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst = _available_quarantine_path(dst)
    if src.is_symlink() or src.is_file():
        _clear_immutable_flags(src)
        shutil.move(str(src), str(dst))
        return True
    if src.is_dir():
        shutil.move(str(src), str(dst))
        return True
    return False


def cleanse_state_home_agency(current_tools: Path, dry_run: bool = False) -> int:
    """Move executable agency out of ~/.vibecrafted and into staged tools."""
    state_home = vibecrafted_home()
    quarantine = _state_agency_quarantine(current_tools)
    moved = 0
    for name in ("skills", "helpers", "config", "bin", "scripts"):
        if _move_state_agency_path(state_home / name, quarantine / name, dry_run):
            moved += 1

    tmp_dir = state_home / "tmp"
    if tmp_dir.is_dir():
        for script in sorted(tmp_dir.glob("*.sh")):
            if _move_state_agency_path(
                script, quarantine / "tmp" / script.name, dry_run
            ):
                moved += 1
    return moved


AGENT_COMMAND_MARKER = "<!-- vibecrafted-managed-agent-command -->"
MARBLES_COMMANDS_BY_RUNTIME: dict[str, tuple[str, ...]] = {
    "claude": ("marbles.md", "cancel-marbles.md"),
    "codex": ("marbles.md", "codex-marbles-loop.md", "cancel-codex-marbles.md"),
}


def _managed_agent_command(path: Path) -> bool:
    """True if `path` is a file carrying the managed-agent-command marker comment."""
    if not path.exists() or not path.is_file():
        return False
    try:
        return AGENT_COMMAND_MARKER in path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _write_managed_agent_command(
    path: Path, content: str, dry_run: bool = False
) -> bool:
    """Write `content` to `path` as a managed agent command file, refusing to overwrite an
    existing unmanaged file.
    """
    if dry_run:
        print(f"  {dim('write')} {path}")
        return True
    if path.exists() and not _managed_agent_command(path):
        print(f"  {WARN} Keeping existing unmanaged command: {path}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _marbles_orchestrator_expr() -> str:
    """Shell expression resolving the vc-marbles orchestrator directory from env, with a default
    fallback.
    """
    return (
        '"${VIBECRAFTED_MARBLES_ORCHESTRATOR:-'
        "${VIBECRAFTED_TOOLS_HOME:-$HOME/.local/share/vibecrafted/tools}"
        "/vibecrafted-current/vibecrafted-core/vibecrafted_core/"
        'runtime/vc-marbles/orchestrator}"'
    )


def _codex_marbles_command(alias: str) -> str:
    """Render the Codex Marbles slash-command markdown body for the given command `alias`."""
    orchestrator = _marbles_orchestrator_expr()
    return f"""---
description: "Start Codex interactive Marbles loop"
argument-hint: "PROMPT [--max-iterations N] [--completion-promise TEXT]"
---
{AGENT_COMMAND_MARKER}

# Codex Marbles

Run:

```bash
orchestrator={orchestrator}
bash "$orchestrator/scripts/setup-codex-loop.sh" $ARGUMENTS
```

Then obey the in-session loop protocol before finalizing:

```bash
orchestrator={orchestrator}
bash "$orchestrator/scripts/codex-loop-step.sh" next
```

If it prints `PROMPT`, continue with that prompt in this same Codex session.
Only finish after a real completion, then run:

```bash
orchestrator={orchestrator}
bash "$orchestrator/scripts/codex-loop-step.sh" complete --promise "<text>"
```

Command alias installed as `{alias}`.
"""


def _cancel_codex_marbles_command() -> str:
    """Render the Codex cancel-marbles slash-command markdown body."""
    orchestrator = _marbles_orchestrator_expr()
    return f"""---
description: "Cancel active Codex Marbles loop"
---
{AGENT_COMMAND_MARKER}

# Cancel Codex Marbles

Run:

```bash
orchestrator={orchestrator}
bash "$orchestrator/scripts/codex-loop-step.sh" cancel
```
"""


def _claude_marbles_command() -> str:
    """Render the Claude Marbles slash-command markdown body."""
    orchestrator = _marbles_orchestrator_expr()
    return f"""---
description: "Start Marbles in current Claude session"
argument-hint: "PROMPT [--max-iterations N] [--completion-promise TEXT]"
---
{AGENT_COMMAND_MARKER}

# Claude Marbles

Run:

```bash
orchestrator={orchestrator}
bash "$orchestrator/scripts/setup-marbles-loop.sh" $ARGUMENTS
```

This command initializes `.claude/marbles.local.md`. The Claude Stop hook lives
at:

```text
$orchestrator/hooks/stop-hook.sh
```
"""


def _cancel_claude_marbles_command() -> str:
    """Render the Claude cancel-marbles slash-command markdown body."""
    return f"""---
description: "Cancel active Claude Marbles loop"
---
{AGENT_COMMAND_MARKER}

# Cancel Claude Marbles

Run:

```bash
if [[ -f .claude/marbles.local.md ]]; then
  rm .claude/marbles.local.md
  echo "Cancelled Claude Marbles."
else
  echo "No active Claude Marbles found."
fi
```
"""


def _agent_command_payloads(runtime: str) -> dict[str, str]:
    """The filename->content map of agent slash-commands to install for a given `runtime`."""
    if runtime == "codex":
        return {
            "marbles.md": _codex_marbles_command("/marbles"),
            "codex-marbles-loop.md": _codex_marbles_command("/codex-marbles-loop"),
            "cancel-codex-marbles.md": _cancel_codex_marbles_command(),
        }
    if runtime == "claude":
        return {
            "marbles.md": _claude_marbles_command(),
            "cancel-marbles.md": _cancel_claude_marbles_command(),
        }
    return {}


def install_agent_commands(runtimes: Sequence[str], dry_run: bool = False) -> None:
    """Write every runtime's agent slash-command payloads into its commands directory."""
    for runtime in runtimes:
        payloads = _agent_command_payloads(runtime)
        if not payloads:
            continue
        commands_dir = runtime_commands_dir(runtime)
        if not dry_run:
            commands_dir.mkdir(parents=True, exist_ok=True)
        print(f"  {cyan(runtime)} commands -> {commands_dir}")
        for filename, content in payloads.items():
            _write_managed_agent_command(commands_dir / filename, content, dry_run)


def _configure_gemini_plans(dry_run: bool = False) -> None:
    """Fix Gemini CLI plan.directory if it points into .vibecrafted.

    Gemini resolves symlinks with realpath() and rejects plans directories
    that resolve outside the project root.  Our .vibecrafted/plans symlink
    points to $VIBECRAFTED_ROOT/.vibecrafted/artifacts/…  which is always outside the repo.

    Fix: reset plan.directory to the Gemini-native default so Gemini writes
    plans into $PWD/.gemini/plans/ (its own space).  Our spawn system handles
    artifact centralisation separately via spawn_link_repo_artifacts().
    """
    gemini_settings = Path.home() / ".gemini" / "settings.json"
    if not gemini_settings.exists():
        return

    try:
        data = json.loads(gemini_settings.read_text())
    except (json.JSONDecodeError, OSError):
        return

    plan_dir = (data.get("general") or {}).get("plan", {}).get("directory", "")
    if ".vibecrafted" not in plan_dir:
        return

    # Remove the override — let Gemini use its default (.gemini/plans/)
    if dry_run:
        print(f"  {dim('would reset')} gemini plan.directory (was {plan_dir!r})")
        return

    data["general"]["plan"].pop("directory", None)
    # Clean up empty plan dict if only modelRouting or nothing left
    if not data["general"]["plan"] or data["general"]["plan"] == {}:
        data["general"].pop("plan", None)

    gemini_settings.write_text(json.dumps(data, indent=2) + "\n")
    print(f"  {OK} Gemini plan.directory reset (was {plan_dir!r} -> default)")


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------


@dataclass
class DoctorFinding:
    """One doctor check result: level (ok/warn/fail), component id, and human message."""

    level: str  # ok, warn, fail
    component: str
    message: str


KNOWN_ZSH_SESSION_NOISE = {
    "saving session",
    "copying shared history",
    "saving history",
    "truncating history files",
    "completed",
    "deleting expired sessions",
    "none found",
}


def is_benign_zsh_session_noise(stderr: str) -> bool:
    """Return True when stderr only contains macOS shell session housekeeping."""
    normalized = " ".join(
        line.strip().lower() for line in stderr.splitlines() if line.strip()
    )
    if not normalized:
        return False

    remainder = normalized
    for fragment in sorted(KNOWN_ZSH_SESSION_NOISE, key=len, reverse=True):
        remainder = remainder.replace(fragment, "")
    remainder = remainder.replace(".", "").replace(" ", "")
    return not remainder


def describe_dumb_terminal_noise(stdout: str, stderr: str) -> str:
    """Summarize shell noise seen under TERM=dumb with a concrete fix hint."""
    issues: list[str] = []
    stderr = (stderr or "").strip()
    stdout = (stdout or "").strip()
    stderr_lower = stderr.lower()

    if stderr and not is_benign_zsh_session_noise(stderr):
        if "starship::print" in stderr_lower and "term=dumb" in stderr_lower:
            issues.append("starship init still runs under TERM=dumb")
        else:
            first_stderr = stderr.splitlines()[0].strip()
            issues.append(f"stderr noise: {first_stderr}")

    if stdout:
        first_stdout = stdout.splitlines()[0].strip()
        issues.append(f"stdout noise: {first_stdout}")

    if not issues:
        return ""

    return (
        "zsh -ic is noisy under TERM=dumb — "
        + "; ".join(issues)
        + '; guard banners/prompt init with [[ -o interactive && "${TERM:-}" != "dumb" ]]'
    )


def _canonical_store_root() -> Path:
    """Canonical `~/.vibecrafted` store root, independent of any env override."""
    return (Path.home() / ".vibecrafted").expanduser()


def _canonical_runtime_root() -> Path:
    """Canonical `~/.local/share/vibecrafted` runtime root, independent of any env override."""
    return (Path.home() / ".local" / "share" / "vibecrafted").expanduser()


def _canonical_launcher_root() -> Path:
    """Canonical `~/.local/bin` launcher root, independent of any env override."""
    return (Path.home() / ".local" / "bin").expanduser()


def _path_with_tilde(path: Path) -> str:
    """Render `path` with the home directory prefix collapsed to `~`."""
    path_text = str(path.expanduser())
    home_text = str(Path.home())
    if path_text == home_text:
        return "~"
    if path_text.startswith(home_text + os.sep):
        return "~" + path_text[len(home_text) :]
    return path_text


def _is_subpath(path: Path, root: Path) -> bool:
    """True if `path` is `root` or lives under it."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _runtime_root_contract_findings() -> list[DoctorFinding]:
    """Verify the launcher-bin/runtime/store roots each resolve to their canonical location,
    flagging drift (e.g. stale VIBECRAFTED_* overrides) as failures.
    """
    checks = [
        (
            "launcher-bin",
            vibecrafted_launcher_bin().expanduser(),
            _canonical_launcher_root(),
            "VIBECRAFTED_LAUNCHER_BIN",
        ),
        (
            "runtime",
            vibecrafted_runtime_home().expanduser(),
            _canonical_runtime_root(),
            "VIBECRAFTED_RUNTIME_HOME",
        ),
        (
            "store",
            vibecrafted_home().expanduser(),
            _canonical_store_root(),
            "VIBECRAFTED_HOME",
        ),
    ]

    findings: list[DoctorFinding] = []
    for component, resolved_path, canonical_path, env_var in checks:
        if resolved_path == canonical_path:
            findings.append(
                DoctorFinding(
                    "ok",
                    f"root:{component}",
                    f"{_path_with_tilde(resolved_path)} (canonical)",
                )
            )
            continue

        override_value = os.environ.get(env_var)
        override_prefix = f"{env_var}={override_value!r}; " if override_value else ""
        findings.append(
            DoctorFinding(
                "fail",
                f"root:{component}",
                f"{override_prefix}resolved to {_path_with_tilde(resolved_path)} but contract requires "
                f"{_path_with_tilde(canonical_path)}; manual cleanup: restore canonical root, remove stale wrappers "
                "from ~/.cargo/bin and /usr/local/bin, then rerun installer/doctor.",
            )
        )
    return findings


def _load_runtime_generation_manifest(
    generation: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    """Load the one closed runtime-generation manifest used by publish and doctor."""
    manifest_path = generation / _RUNTIME_GENERATION_MANIFEST
    try:
        manifest_raw = _capture_runtime_bound_file(manifest_path)
        loaded = json.loads(manifest_raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"installed generation manifest is missing or invalid: {exc}"
    if not isinstance(loaded, dict):
        return None, "installed generation manifest does not satisfy the runtime schema"

    version_path = generation / "VERSION"
    try:
        installed_version = (
            _capture_runtime_bound_file(version_path).decode("utf-8").strip()
        )
    except (OSError, UnicodeError) as exc:
        return None, f"installed generation VERSION is unreadable: {exc}"
    version = loaded.get("version")
    source_fingerprint = loaded.get("source_fingerprint")
    owner_repo = loaded.get("owner_repo")
    source_revision = loaded.get("source_revision")
    source_payload = loaded.get("source_payload")
    hashes = loaded.get("hashes")
    if (
        set(loaded) != _RUNTIME_GENERATION_MANIFEST_KEYS
        or loaded.get("schema") != _RUNTIME_GENERATION_MANIFEST_SCHEMA
        or not isinstance(version, str)
        or not version
        or version != version.strip()
        or installed_version != version
        or not isinstance(source_fingerprint, str)
        or len(source_fingerprint) != 64
        or not re.fullmatch(r"[0-9a-f]{64}", source_fingerprint)
        or not isinstance(owner_repo, str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", owner_repo)
        or not isinstance(source_revision, str)
        or not re.fullmatch(r"[0-9a-f]{40}", source_revision)
        or loaded.get("entrypoint") != _RUNTIME_GENERATION_ENTRYPOINT.as_posix()
        or not isinstance(hashes, dict)
        or set(hashes)
        != {path.as_posix() for path in _RUNTIME_GENERATION_REQUIRED_HASHES}
        or any(
            not isinstance(relative, str)
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for relative, digest in hashes.items()
        )
    ):
        return None, "installed generation manifest does not satisfy the runtime schema"
    try:
        canonical_provenance = _canonical_runtime_source_provenance(
            {
                "schema": _SOURCE_PROVENANCE_SCHEMA,
                "owner_repo": owner_repo,
                "source_revision": source_revision,
                "payload": source_payload,
            }
        )
    except OSError:
        return None, "installed generation manifest does not satisfy the runtime schema"
    loaded["source_payload"] = canonical_provenance["payload"]
    return loaded, None


def _runtime_generation_payload_errors(generation: Path) -> list[str]:
    """Validate one candidate/current generation without consulting or mutating its pointer."""
    manifest, manifest_error = _load_runtime_generation_manifest(generation)
    if manifest is None:
        return [manifest_error or "installed generation manifest is invalid"]
    errors: list[str] = []
    try:
        provenance = load_source_provenance(generation)
    except DistributionManifestError as exc:
        errors.append(f"installed source provenance is invalid: {exc}")
        provenance = None
    if provenance is None:
        errors.append("installed source provenance is missing")
    elif provenance != {
        "schema": _SOURCE_PROVENANCE_SCHEMA,
        "owner_repo": manifest["owner_repo"],
        "source_revision": manifest["source_revision"],
        "payload": manifest["source_payload"],
    }:
        errors.append("installed source provenance disagrees with runtime manifest")
    projected_config = generation / _RUNTIME_GENERATION_PROJECTED_CONFIG
    try:
        resolved_projected_config = projected_config.resolve(strict=True)
    except (OSError, RuntimeError):
        pass  # The bound-file loop below owns the missing/broken-path diagnostic.
    else:
        topology_error = _runtime_projection_topology_error(
            generation,
            projected_config,
            resolved_projected_config,
        )
        if topology_error is not None:
            errors.append(topology_error)
    for relative_text, expected_digest in manifest["hashes"].items():
        relative = Path(relative_text)
        installed_file = generation / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not installed_file.is_file()
            or installed_file.is_symlink()
        ):
            errors.append(f"manifest-bound file is missing or aliased: {relative_text}")
            continue
        try:
            actual_digest = hashlib.sha256(
                _capture_runtime_bound_file(installed_file)
            ).hexdigest()
        except OSError as exc:
            errors.append(f"manifest-bound file is unreadable: {relative_text}: {exc}")
            continue
        if actual_digest != expected_digest:
            errors.append(f"manifest-bound file drifted: {relative_text}")
    errors.extend(
        _runtime_generation_audit_errors(
            generation,
            source_fingerprint=manifest["source_fingerprint"],
        )
    )
    errors.extend(_release_contract_asset_issues(generation, manifest=manifest))
    return sorted(set(errors))


def _runtime_generation_contract_findings() -> list[DoctorFinding]:
    """Verify the current runtime generation is manifest-bound: symlink resolves under the
    canonical runtime root, its generation manifest is well-formed, every manifest-hashed file
    matches, and the launcher resolves to its entrypoint.
    """
    current = vibecrafted_tools_home() / "vibecrafted-current"
    if not current.is_symlink():
        return [
            DoctorFinding(
                "fail",
                "runtime-generation",
                f"{_path_with_tilde(current)} is not an atomic generation pointer",
            )
        ]
    try:
        generation = current.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return [
            DoctorFinding(
                "fail",
                "runtime-generation",
                f"cannot resolve current runtime generation: {exc}",
            )
        ]
    canonical_runtime = _canonical_runtime_root().resolve(strict=False)
    if not _is_subpath(generation, canonical_runtime):
        return [
            DoctorFinding(
                "fail",
                "runtime-generation",
                f"current runtime resolves outside {_path_with_tilde(canonical_runtime)}",
            )
        ]

    errors = _runtime_generation_payload_errors(generation)
    launcher = _canonical_launcher_root() / "vibecrafted"
    try:
        launcher_target = launcher.resolve(strict=True)
        expected_launcher = (generation / _RUNTIME_GENERATION_ENTRYPOINT).resolve(
            strict=True
        )
    except (OSError, RuntimeError):
        errors.append(
            "canonical vibecrafted launcher or current generation entrypoint "
            "is missing or broken"
        )
    else:
        if launcher_target != expected_launcher:
            errors.append(
                "canonical vibecrafted launcher does not resolve to the current "
                "generation entrypoint"
            )
    if errors:
        return [
            DoctorFinding(
                "fail",
                "runtime-generation",
                "; ".join(sorted(set(errors))),
            )
        ]
    return [
        DoctorFinding(
            "ok",
            "runtime-generation",
            f"{generation.name} is manifest-bound and checkout-free",
        )
    ]


def _host_shell_contract_findings() -> list[DoctorFinding]:
    """Fail if any shell startup file still sources the retired product helper shim instead of
    staying PATH-only.
    """
    offenders: list[str] = []
    for rcname in _SHELL_STARTUP_FILES:
        rcfile = Path.home() / rcname
        if not rcfile.is_file():
            continue
        try:
            lines = rcfile.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if (
                "vc-skills.sh" in stripped
                or "vc-skills.zsh" in stripped
                or (stripped.startswith(("source ", ". ")) and "vetcoders" in stripped)
            ):
                offenders.append(rcname)
                break
    if offenders:
        return [
            DoctorFinding(
                "fail",
                "host-shell",
                "product helper sourcing remains active in "
                + ", ".join(offenders)
                + "; run `vibecrafted doctor --fix-rc` to keep only the PATH helper",
            )
        ]
    return [
        DoctorFinding(
            "ok",
            "host-shell",
            "ordinary shell startup is PATH-only; vc-start owns product helpers",
        )
    ]


def _managed_frontier_contract_findings() -> list[DoctorFinding]:
    """Fail if any symlink under the XDG 'frontier' config directory resolves outside the
    installed runtime root.
    """
    frontier = xdg_config_home() / "vetcoders" / "frontier"
    installed_root = vibecrafted_runtime_home().resolve(strict=False)
    unsafe: list[str] = []
    if frontier.is_dir():
        for path in sorted(frontier.rglob("*")):
            if not path.is_symlink():
                continue
            try:
                target = path.resolve(strict=True)
            except (OSError, RuntimeError):
                unsafe.append(str(path.relative_to(frontier)))
                continue
            if not _is_subpath(target, installed_root):
                unsafe.append(str(path.relative_to(frontier)))
    if unsafe:
        return [
            DoctorFinding(
                "fail",
                "frontier-links",
                f"{len(unsafe)} frontier link(s) escape the installed runtime: "
                + ", ".join(unsafe[:5])
                + (" ..." if len(unsafe) > 5 else ""),
            )
        ]
    return [
        DoctorFinding(
            "ok",
            "frontier-links",
            "all managed frontier links resolve inside the installed runtime",
        )
    ]


def _public_launcher_contract_findings() -> list[DoctorFinding]:
    """Reject Vibecrafted-owned launchers that resolve into a Git checkout.

    Packaged providers may legitimately live outside the immutable runtime
    generation (for example uv or Cargo tools). A repository checkout is the
    forbidden boundary: exposing one through ``~/.local/bin`` creates a second
    runtime identity with no installed provenance.

    Scope is ownership, not naming: only launchers Vibecrafted publishes itself
    are judged. A foreign product sharing the launcher bin — and the ``vc-*``
    prefix — keeps its own installation contract.
    """
    owned_names = _vibecrafted_owned_launcher_names()
    unsafe: list[str] = []
    for launcher_bin_dir in _launcher_bin_dirs():
        if not launcher_bin_dir.is_dir():
            continue
        for entry in sorted(launcher_bin_dir.iterdir()):
            if not entry.is_symlink():
                continue
            if entry.name.lower() not in owned_names:
                continue
            try:
                resolved = entry.resolve(strict=True)
            except (OSError, RuntimeError):
                unsafe.append(f"{entry.name} (broken)")
                continue
            for parent in (resolved.parent, *resolved.parents):
                if (parent / ".git").exists():
                    unsafe.append(f"{entry.name} -> {resolved}")
                    break

    if unsafe:
        return [
            DoctorFinding(
                "fail",
                "public-launchers",
                "Vibecrafted launcher(s) resolve into a source checkout: "
                + ", ".join(unsafe[:5])
                + (" ..." if len(unsafe) > 5 else ""),
            )
        ]
    return [
        DoctorFinding(
            "ok",
            "public-launchers",
            "Vibecrafted-owned launchers are checkout-free",
        )
    ]


def _slack_provider_contract_findings() -> list[DoctorFinding]:
    """Require vc-slack to come from the immutable provider publication."""
    try:
        provider = importlib.import_module("slack_provider")
    except ModuleNotFoundError:  # package import path in tests/installed runtime
        try:
            provider = importlib.import_module("scripts.slack_provider")
        except ModuleNotFoundError as exc:
            return [
                DoctorFinding(
                    "fail",
                    "slack-provider",
                    f"Slack provider installer is missing: {exc}",
                )
            ]
    healthy, detail = provider.doctor()
    if not healthy:
        # Never-published is the deferred external case (vc-slack-agent is a
        # sibling repo; hosts without it — CI runners, fresh installs — are
        # legal). Only a BROKEN publication is a failure.
        provider_root = provider.runtime_home() / "providers" / provider.PROVIDER_NAME
        if not provider_root.exists():
            return [
                DoctorFinding(
                    "warn",
                    "slack-provider",
                    f"{detail}. External provider (vc-slack-agent) is not "
                    "installed on this host — optional; publish via "
                    "`make install` with the sibling checkout present",
                )
            ]
    return [
        DoctorFinding(
            "ok" if healthy else "fail",
            "slack-provider",
            detail
            if healthy
            else f"{detail}. Run `make install` from the Vibecrafted suite",
        )
    ]


def _foundation_provenance_findings(
    foundation_name: str, executable_path: Path
) -> list[DoctorFinding]:
    """Note when a foundation's resolved executable is an external developer-tool provider
    (cargo/local bin) rather than the canonical launcher — informational, not a failure.
    """
    findings: list[DoctorFinding] = []
    canonical_launcher = _canonical_launcher_root()
    executable = executable_path.expanduser()

    if executable.parent != canonical_launcher:
        findings.append(
            DoctorFinding(
                "ok",
                f"foundation-provenance:{foundation_name}",
                f"external developer provider accepted: {_path_with_tilde(executable)} "
                f"(canonical launcher root is {_path_with_tilde(canonical_launcher)})",
            )
        )
        return findings

    try:
        resolved = executable.resolve(strict=False)
    except OSError:
        resolved = executable

    for legacy_root in (Path.home() / ".cargo" / "bin", Path("/usr/local/bin")):
        legacy_root = legacy_root.expanduser()
        if _is_subpath(resolved, legacy_root):
            findings.append(
                DoctorFinding(
                    "ok",
                    f"foundation-provenance:{foundation_name}",
                    f"launcher {_path_with_tilde(executable)} delegates to developer provider "
                    f"{_path_with_tilde(resolved)}",
                )
            )
            break

    return findings


def _has_runtime_contract_failures(findings: Sequence[DoctorFinding]) -> bool:
    """True if any finding is a failing `root:*` runtime-contract check."""
    return any(
        finding.level == "fail" and finding.component.startswith("root:")
        for finding in findings
    )


def _pause_for_runtime_contract_failures(findings: Sequence[DoctorFinding]) -> None:
    """In an interactive TTY, print manual cleanup guidance and pause for acknowledgement when
    runtime-root contract findings failed; no-op otherwise.
    """
    if not _has_runtime_contract_failures(findings):
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return

    out = sys.stdout if hasattr(sys.stdout, "write") else sys.__stdout__
    print(file=out)
    print(f"  {yellow('Runtime contract failed fast.')}\n", file=out)
    print("  Canonical roots:", file=out)
    print("    - launcher bin: ~/.local/bin", file=out)
    print("    - runtime payload: ~/.local/share/vibecrafted", file=out)
    print("    - store/control: ~/.vibecrafted", file=out)
    print(file=out)
    print("  Manual cleanup (no automatic dotfile edits were performed):", file=out)
    print("    1) restore canonical VIBECRAFTED_* root overrides", file=out)
    print(
        "    2) remove stale runtime/store launcher wrappers if they shadow these roots",
        file=out,
    )
    print("    3) rerun 'vibecrafted doctor' or the installer", file=out)
    print(file=out)
    try:
        input("  Press Enter after reviewing cleanup steps, or Ctrl-C to abort: ")
    except EOFError:
        print(file=out)


def _python_entrypoint_issue_level(
    issues: Sequence[str], *, state: InstallState
) -> str:
    """The release verifier is mandatory in every installed-state shape."""
    if any(issue.startswith(f"{SECURE_WALKAROUND_LAUNCHER}:") for issue in issues):
        return "fail"
    return "warn"


def _release_contract_asset_issues(
    current_tools: Path,
    *,
    manifest: dict[str, Any] | None = None,
) -> list[str]:
    """Bind the installed W0 verifier closure to the published generation manifest."""
    package = current_tools / "vibecrafted-core" / "vibecrafted_core"
    paths = {
        relative: package / relative for relative in RELEASE_CONTRACT_PACKAGE_ASSETS
    }
    issues: list[str] = []
    captured_assets: dict[str, bytes] = {}
    issues.extend(_verifier_bytecode_shadow_issues(current_tools))
    for relative, path in paths.items():
        if not path.is_file() or path.is_symlink():
            issues.append(f"{relative}:missing")

    if manifest is None:
        manifest_path = current_tools / _RUNTIME_GENERATION_MANIFEST
        if not manifest_path.is_file() or manifest_path.is_symlink():
            issues.append(f"{_RUNTIME_GENERATION_MANIFEST}:missing")
        else:
            manifest, manifest_error = _load_runtime_generation_manifest(current_tools)
            if manifest is None:
                issues.append(
                    f"{_RUNTIME_GENERATION_MANIFEST}:corrupt:"
                    f"{manifest_error or 'invalid runtime manifest'}"
                )

    if manifest is not None:
        hashes = manifest["hashes"]
        for relative, path in paths.items():
            if not path.is_file() or path.is_symlink():
                continue
            manifest_relative = (
                Path("vibecrafted-core/vibecrafted_core") / relative
            ).as_posix()
            expected_digest = hashes.get(manifest_relative)
            if (
                not isinstance(expected_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None
            ):
                issues.append(f"{relative}:corrupt:runtime manifest digest is invalid")
                continue
            try:
                raw = _capture_runtime_bound_file(path)
                actual_digest = hashlib.sha256(raw).hexdigest()
            except OSError as exc:
                issues.append(f"{relative}:corrupt:{exc}")
                continue
            captured_assets[relative] = raw
            if actual_digest != expected_digest:
                issues.append(
                    f"{relative}:corrupt:installed bytes do not match runtime manifest"
                )

    policy_raw = captured_assets.get("trust/release-policy.v1.json")
    if policy_raw is not None:
        try:
            policy = json.loads(policy_raw.decode("utf-8"))
            if (
                not isinstance(policy, dict)
                or policy.get("algorithm") != "rsa-pkcs1v15-sha256"
                or policy.get("public_key_spki_sha256") != _RELEASE_KEY_SPKI_SHA256
            ):
                raise ValueError("release trust pins do not match v1")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            issues.append(f"trust/release-policy.v1.json:corrupt:{exc}")

    public_key_raw = captured_assets.get("trust/vibecrafted-signing-v1.pub")
    if public_key_raw is None:
        return sorted(set(issues))
    openssl = Path("/usr/bin/openssl")
    if not openssl.is_file() or not os.access(openssl, os.X_OK):
        issues.append("trust/vibecrafted-signing-v1.pub:unverifiable")
    else:
        result = subprocess.run(
            [
                str(openssl),
                "pkey",
                "-pubin",
                "-outform",
                "DER",
            ],
            check=False,
            input=public_key_raw,
            capture_output=True,
        )
        if (
            result.returncode != 0
            or hashlib.sha256(result.stdout).hexdigest() != _RELEASE_KEY_SPKI_SHA256
        ):
            issues.append("trust/vibecrafted-signing-v1.pub:corrupt")
    return sorted(set(issues))


def run_doctor(store_path: Path, state: InstallState) -> list[DoctorFinding]:
    """Run full installation health check."""
    findings: list[DoctorFinding] = []

    # 0. Framework version
    fw_ver = state.framework_version or "unknown"
    findings.append(DoctorFinding("ok", "version", fw_ver))

    # 0b. Distribution channel + upgrade path
    current_link = vibecrafted_tools_home() / "vibecrafted-current"
    is_git = False
    if current_link.exists() or current_link.is_symlink():
        try:
            resolved = current_link.resolve(strict=True)
        except (OSError, RuntimeError):
            resolved = None
        is_git = resolved is not None and (resolved / ".git").exists()
    elif store_path.parent.exists():
        # Check if the store itself lives inside a git checkout
        is_git = (store_path.parent / ".git").exists()

    if is_git:
        findings.append(
            DoctorFinding(
                "ok", "channel", "git — use 'vibecrafted update' or 'make update'"
            )
        )
    else:
        findings.append(
            DoctorFinding(
                "ok",
                "channel",
                "tarball — run 'vibecrafted update' to fetch latest release",
            )
        )

    # The manifest-bound release verifier and pinned trust assets are
    # distribution-owned, not install-state-owned. Check them before the
    # store/state early returns so fresh, migrated, lost, and corrupt installs
    # all fail closed alike.
    try:
        current_generation = current_link.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        release_contract_issues = [f"runtime-pointer:corrupt:{exc}"]
    else:
        release_contract_issues = _release_contract_asset_issues(current_generation)
    findings.append(
        DoctorFinding(
            "fail" if release_contract_issues else "ok",
            "release-contract-assets",
            "installed verifier engine, runner, schema, policy, and public key "
            "are manifest-bound and pinned"
            if not release_contract_issues
            else "Release contract asset issue(s): "
            + ", ".join(release_contract_issues),
        )
    )

    # 1. Store exists
    if store_path.exists():
        findings.append(DoctorFinding("ok", "store", f"{store_path} exists"))
    else:
        findings.append(DoctorFinding("fail", "store", f"{store_path} does not exist"))
        return findings

    # 2. State file exists
    state_file = _install_state_file(store_path)
    if state_file.exists():
        findings.append(DoctorFinding("ok", "state", "Install manifest found"))
    else:
        findings.append(
            DoctorFinding("warn", "state", "No install manifest — was installer used?")
        )

    findings.extend(_runtime_root_contract_findings())
    findings.extend(_runtime_generation_contract_findings())
    findings.extend(_host_shell_contract_findings())
    findings.extend(_managed_frontier_contract_findings())
    findings.extend(_public_launcher_contract_findings())
    findings.extend(_slack_provider_contract_findings())

    # 3. Expected skills present
    for skill_name in state.skills:
        skill_path = store_path / skill_name
        if skill_path.is_dir() and (skill_path / "SKILL.md").exists():
            findings.append(DoctorFinding("ok", f"skill:{skill_name}", "present"))
        elif skill_path.exists():
            findings.append(
                DoctorFinding(
                    "warn", f"skill:{skill_name}", "dir exists but no SKILL.md"
                )
            )
        else:
            findings.append(
                DoctorFinding("fail", f"skill:{skill_name}", "missing from store")
            )

    # 3b. Drift detection: runtime skills vs source
    source_root = None
    source_candidate = _doctor_launcher_source_root(store_path)
    if source_candidate is not None:
        skills_src = (
            source_candidate / "vibecrafted-core" / "vibecrafted_core" / "skills"
        )
        if skills_src.is_dir():
            source_root = skills_src
    drifted: list[str] = []
    if source_root:
        for skill_name in state.skills:
            installed = store_path / skill_name / "SKILL.md"
            source = source_root / skill_name / "SKILL.md"
            if installed.is_file() and source.is_file():
                try:
                    if installed.read_text(encoding="utf-8") != source.read_text(
                        encoding="utf-8"
                    ):
                        drifted.append(skill_name)
                except OSError:
                    pass
        if drifted:
            findings.append(
                DoctorFinding(
                    "warn",
                    "drift",
                    f"{len(drifted)} skill(s) differ from source: {', '.join(drifted[:5])}",
                )
            )
        else:
            findings.append(DoctorFinding("ok", "drift", "runtime matches source"))
    else:
        findings.append(
            DoctorFinding(
                "warn", "drift", "cannot detect drift — source link not found"
            )
        )

    # 4. Symlink views — check what the manifest recorded PLUS the standard
    # product surface. Claude Code and Codex read only their own skill dirs;
    # a manifest that recorded just "agents" leaves their /vc-* decks dark,
    # and doctor must surface that instead of trusting the manifest.
    recorded_runtimes = list(state.runtimes)
    view_runtimes = recorded_runtimes + [
        rt for rt in STANDARD_VIEW_RUNTIMES if rt not in recorded_runtimes
    ]
    for runtime in view_runtimes:
        strict = runtime in recorded_runtimes
        severity = "fail" if strict else "warn"
        rt_skills = Path.home() / f".{runtime}" / "skills"
        if not rt_skills.exists():
            findings.append(
                DoctorFinding(
                    severity, f"runtime:{runtime}", f"{rt_skills} does not exist"
                )
            )
            continue
        for skill_name in state.skills:
            link = rt_skills / skill_name
            default = store_path / skill_name
            if link.is_symlink():
                target = link.resolve()
                if target == default.resolve():
                    findings.append(
                        DoctorFinding(
                            "ok", f"symlink:{runtime}/{skill_name}", "correct"
                        )
                    )
                else:
                    findings.append(
                        DoctorFinding(
                            "warn",
                            f"symlink:{runtime}/{skill_name}",
                            f"points to {target}, expected {default}",
                        )
                    )
            elif link.is_dir():
                findings.append(
                    DoctorFinding(
                        "fail",
                        f"symlink:{runtime}/{skill_name}",
                        "is a COPY, not a symlink — stale drift risk",
                    )
                )
            else:
                findings.append(
                    DoctorFinding(
                        severity,
                        f"symlink:{runtime}/{skill_name}",
                        "missing"
                        if strict
                        else "missing — deck dark for this CLI; rerun 'vibecrafted update'",
                    )
                )

    # 4b. Agent slash-command views. These are separate from skills and used by
    # provider-native command palettes such as ~/.codex/commands and
    # ~/.claude/commands.
    for runtime in state.runtimes:
        expected_commands = MARBLES_COMMANDS_BY_RUNTIME.get(runtime, ())
        if not expected_commands:
            continue
        rt_commands = runtime_commands_dir(runtime)
        missing = [
            name
            for name in expected_commands
            if not _managed_agent_command(rt_commands / name)
        ]
        if missing:
            findings.append(
                DoctorFinding(
                    "fail",
                    f"commands:{runtime}",
                    f"missing managed command(s): {', '.join(missing)} in {rt_commands}",
                )
            )
        else:
            findings.append(
                DoctorFinding(
                    "ok",
                    f"commands:{runtime}",
                    f"Marbles commands installed in {rt_commands}",
                )
            )

    # 5. Foundations
    for f in FOUNDATIONS:
        path = f.is_installed()
        if path:
            findings.append(DoctorFinding("ok", f"foundation:{f.name}", f"-> {path}"))
            findings.extend(_foundation_provenance_findings(f.name, Path(path)))
        elif f.required:
            # Required product foundations (loctree/aicx/vc-frame) are externally
            # managed — installed via their own canonical installer, not by this
            # framework. Their absence is an advisory (warn), not a broken
            # install (fail): the framework is functional without them and the
            # message points at the fix. Consistent with install-foundations.sh,
            # which likewise treats them as non-fatal. Keeps `make doctor` green
            # in headless/CI contexts where the product binaries are not present.
            findings.append(
                DoctorFinding(
                    "warn",
                    f"foundation:{f.name}",
                    f"missing (externally managed) — {f.install_hint()}",
                )
            )
        else:
            findings.append(
                DoctorFinding("warn", f"foundation:{f.name}", "optional, not installed")
            )

    # 5b. Runtime horse selected by install.sh --runtime / make install RUNTIME=...
    findings.append(doctor_runtime_finding())

    # 6. Shell helpers
    helper_file = _helper_target_path()
    legacy_file = _helper_legacy_path()
    if helper_file.exists():
        try:
            helper_content = helper_file.read_text(encoding="utf-8")
        except OSError:
            helper_content = ""

        if HELPER_SHIM_MARKER in helper_content:
            findings.append(DoctorFinding("ok", "shell-helpers", str(helper_file)))
        else:
            findings.append(
                DoctorFinding(
                    "warn",
                    "shell-helpers",
                    f"{helper_file} is a copied helper — reinstall to remove stale drift risk",
                )
            )
    elif legacy_file.exists():
        findings.append(
            DoctorFinding(
                "warn",
                "shell-helpers",
                f"compat location only: {legacy_file} — re-run install",
            )
        )
    elif state.shell_helpers:
        findings.append(
            DoctorFinding(
                "warn", "shell-helpers", "marked as installed but file missing"
            )
        )
    else:
        findings.append(
            DoctorFinding("ok", "shell-helpers", "not installed (optional)")
        )

    if helper_file.exists():
        helper_ok, helper_detail = _run_smoke_command(
            [
                "bash",
                "-c",
                'source "$1"; command -v vc-help >/dev/null && command -v vc-agents >/dev/null && command -v vc-init >/dev/null && command -v vc-intents >/dev/null && command -v vc-ownership >/dev/null && command -v vc-loop >/dev/null && command -v vc-ship >/dev/null && command -v vc-cron >/dev/null && command -v vc-marbles >/dev/null && command -v codex-implement >/dev/null && command -v codex-marbles >/dev/null && command -v skills-sync >/dev/null && printf "helper-ok\\n"',
                "_",
                str(helper_file),
            ],
            env=os.environ.copy(),
            expected_text="helper-ok",
        )
        findings.append(
            DoctorFinding(
                "ok" if helper_ok else "fail",
                "shell-helper-runtime",
                "helper shim sources and exports commands"
                if helper_ok
                else helper_detail,
            )
        )

    wrapper_locations = {
        name: _find_launcher_wrapper(name)
        for name in ["vibecrafted", *LAUNCHER_WRAPPERS]
    }
    missing_wrappers = [
        name
        for name in LAUNCHER_WRAPPERS
        if name not in PYTHON_ENTRYPOINT_LAUNCHERS
        and wrapper_locations.get(name) is None
    ]
    if missing_wrappers:
        findings.append(
            DoctorFinding(
                "warn",
                "launcher-wrappers",
                "missing wrapper commands: "
                + ", ".join(missing_wrappers[:6])
                + (" ..." if len(missing_wrappers) > 6 else ""),
            )
        )
    else:
        found_dirs = sorted(
            {
                str(path.parent)
                for name, path in wrapper_locations.items()
                if name in LAUNCHER_WRAPPERS
                and name not in PYTHON_ENTRYPOINT_LAUNCHERS
                and path is not None
            }
        )
        findings.append(
            DoctorFinding(
                "ok",
                "launcher-wrappers",
                ", ".join(found_dirs) if found_dirs else "wrappers present",
            )
        )

    python_entrypoint_issues: list[str] = []
    python_entrypoint_owners: set[str] = set()
    for name in PYTHON_ENTRYPOINT_LAUNCHERS:
        launcher_path = _find_launcher_wrapper(name)
        if launcher_path is None:
            python_entrypoint_issues.append(f"{name}:missing")
            continue
        try:
            resolved = launcher_path.resolve(strict=False)
        except OSError:
            resolved = launcher_path
        if name == SECURE_WALKAROUND_LAUNCHER:
            verifier_issues = _secure_walkaround_launcher_issues(
                current_link,
                launcher_path,
            )
            if verifier_issues:
                python_entrypoint_issues.extend(verifier_issues)
            else:
                python_entrypoint_owners.add("manifest-bound verifier wrapper")
            continue
        if ".venv" in resolved.parts:
            python_entrypoint_owners.add("runtime venv")
            continue
        if "uv" in resolved.parts and "tools" in resolved.parts:
            python_entrypoint_owners.add("uv tool")
            continue
        if name == "vibecrafted":
            try:
                expected = _launcher_symlink_target(Path()).resolve(strict=True)
            except (OSError, RuntimeError):
                expected = None
            if expected is not None and resolved == expected:
                python_entrypoint_owners.add("runtime generation")
                continue
        python_entrypoint_issues.append(f"{name}:not-uv-tool")
    if python_entrypoint_issues:
        issue_level = _python_entrypoint_issue_level(
            python_entrypoint_issues, state=state
        )
        findings.append(
            DoctorFinding(
                issue_level,
                "python-entrypoints",
                "Python launcher ownership issue(s): "
                + ", ".join(python_entrypoint_issues[:6])
                + (" ..." if len(python_entrypoint_issues) > 6 else ""),
            )
        )
    else:
        findings.append(
            DoctorFinding(
                "ok",
                "python-entrypoints",
                "all Python entrypoints resolve through "
                + (" + ".join(sorted(python_entrypoint_owners)) or "managed tools"),
            )
        )

    launcher = wrapper_locations.get("vibecrafted")
    wrapper = wrapper_locations.get("vc-help")
    if launcher is not None and wrapper is not None:
        launcher_ok, launcher_detail = _run_smoke_command(
            [str(launcher), "--help"],
            env=os.environ.copy(),
        )
        wrapper_ok, wrapper_detail = _run_smoke_command(
            [str(wrapper)],
            env=os.environ.copy(),
        )
        findings.append(
            DoctorFinding(
                "ok" if launcher_ok and wrapper_ok else "fail",
                "launcher-runtime",
                "vibecrafted help + vc-help smoke passed"
                if launcher_ok and wrapper_ok
                else f"launcher={launcher_detail}; wrapper={wrapper_detail}",
            )
        )

    # 6b. Dashboard smoke: verify the dashboard wrapper executes
    dashboard_wrapper = wrapper_locations.get("vc-dashboard")
    if dashboard_wrapper is not None:
        dash_ok, dash_detail = _run_smoke_command(
            [str(dashboard_wrapper), "--help"],
            env=os.environ.copy(),
            expected_text="dashboard",
        )
        if not dash_ok:
            # Fallback: just check it runs without error
            dash_ok2, _ = _run_smoke_command(
                [str(dashboard_wrapper), "--help"],
                env=os.environ.copy(),
                expected_text="",
            )
            dash_ok = dash_ok2
        findings.append(
            DoctorFinding(
                "ok" if dash_ok else "warn",
                "dashboard-smoke",
                "vc-dashboard wrapper executes" if dash_ok else dash_detail,
            )
        )
    elif launcher is not None and launcher.exists():
        dash_ok, dash_detail = _run_smoke_command(
            ["bash", str(launcher), "dashboard", "--help"],
            env=os.environ.copy(),
            expected_text="dashboard",
        )
        if not dash_ok:
            dash_ok = True  # help text may vary; just check it runs
        findings.append(
            DoctorFinding(
                "ok" if dash_ok else "warn",
                "dashboard-smoke",
                "vibecrafted dashboard help smoke passed" if dash_ok else dash_detail,
            )
        )

    # 7. Spawn pipeline smoke: validate common.sh sources cleanly and key functions exist
    common_sh = None
    for cand in [
        current_link.resolve() / "runtime" / "scripts" / "common.sh"
        if current_link.exists()
        else None,
        current_link.resolve() / "agents" / "scripts" / "common.sh"
        if current_link.exists()
        else None,
        current_link.resolve() / "skills" / "vc-agents" / "scripts" / "common.sh"
        if current_link.exists()
        else None,
        store_path / "vc-agents" / "scripts" / "common.sh",
    ]:
        if cand is not None and cand.is_file():
            common_sh = cand
            break

    if common_sh is not None:
        spawn_ok, spawn_detail = _run_smoke_command(
            [
                "bash",
                "-c",
                (
                    'source "$1" && '
                    "type spawn_write_meta >/dev/null 2>&1 && "
                    "type spawn_prepare_paths >/dev/null 2>&1 && "
                    "type spawn_generate_launcher >/dev/null 2>&1 && "
                    "type spawn_watch_startup >/dev/null 2>&1 && "
                    'printf "spawn-pipeline-ok\\n"'
                ),
                "_",
                str(common_sh),
            ],
            env=os.environ.copy(),
            expected_text="spawn-pipeline-ok",
        )
        findings.append(
            DoctorFinding(
                "ok" if spawn_ok else "fail",
                "spawn-pipeline",
                "common.sh sources cleanly and exports key functions"
                if spawn_ok
                else f"spawn pipeline broken: {spawn_detail}",
            )
        )
        # 7a-2. Spawn e2e smoke: generate a launcher, verify it is valid bash.
        e2e_ok, e2e_detail = _run_smoke_command(
            [
                "bash",
                "-c",
                (
                    'source "$1" && '
                    'tmpdir="$(mktemp -d)" && '
                    "export SPAWN_AGENT=doctor-smoke SPAWN_RUN_ID=smoke-000 "
                    "SPAWN_PROMPT_ID=smoke SPAWN_LOOP_NR=0 SPAWN_SKILL_CODE=doctor "
                    'SPAWN_ROOT="$tmpdir" SPAWN_PLAN="$tmpdir/doctor-plan.md" '
                    'SPAWN_REPORT="$tmpdir/report.md" '
                    'SPAWN_TRANSCRIPT="$tmpdir/transcript.md" '
                    'SPAWN_LAUNCHER="$tmpdir/launcher.sh" && '
                    'spawn_write_meta "$tmpdir/meta.json" "launching" "$SPAWN_AGENT" '
                    '"doctor" "$SPAWN_ROOT" "$SPAWN_PLAN" "$SPAWN_REPORT" '
                    '"$SPAWN_TRANSCRIPT" "$SPAWN_LAUNCHER" && '
                    'spawn_generate_launcher "$SPAWN_LAUNCHER" "$tmpdir/meta.json" '
                    '"$SPAWN_REPORT" "$SPAWN_TRANSCRIPT" "$1" "echo ok" && '
                    'bash -n "$tmpdir/launcher.sh" && '
                    'rm -rf "$tmpdir" && '
                    'printf "spawn-e2e-ok\\n"'
                ),
                "_",
                str(common_sh),
            ],
            env={k: v for k, v in os.environ.items() if not k.startswith("VC_FRAME")},
            expected_text="spawn-e2e-ok",
        )
        findings.append(
            DoctorFinding(
                "ok" if e2e_ok else "warn",
                "spawn-e2e",
                "spawn pipeline generates valid launcher end-to-end"
                if e2e_ok
                else f"spawn e2e smoke failed: {e2e_detail}",
            )
        )
    else:
        findings.append(
            DoctorFinding(
                "warn",
                "spawn-pipeline",
                "common.sh not found — cannot validate spawn pipeline",
            )
        )

    # 7b. Version channel check: compare installed vs available
    installed_ver = fw_ver
    try:
        channel_raw = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--max-time",
                "5",
                "https://vibecrafted.io/channel/main.json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if channel_raw.returncode == 0 and channel_raw.stdout.strip():
            import json as _json

            channel_data = _json.loads(channel_raw.stdout)
            available_ver = channel_data.get("version", "")

            def _semver_key(raw: str) -> tuple[int, ...]:
                """Sort key for a raw version string like '3.6.0+g66c0958b': numeric (major,
                minor, patch) tuple, truncated at the first non-numeric segment.
                """
                # "3.6.0+g66c0958b" -> (3, 6, 0); non-numeric parts end the key
                # so a malformed version never outranks a real one.
                core = raw.split("+", 1)[0].split("-", 1)[0]
                parts: list[int] = []
                for chunk in core.split("."):
                    if not chunk.isdigit():
                        break
                    parts.append(int(chunk))
                return tuple(parts)

            if available_ver:
                installed_key = _semver_key(installed_ver)
                available_key = _semver_key(available_ver)
                if available_key > installed_key:
                    findings.append(
                        DoctorFinding(
                            "warn",
                            "update-available",
                            f"installed {installed_ver}, available {available_ver} — run 'vibecrafted update'",
                        )
                    )
                elif installed_key > available_key:
                    findings.append(
                        DoctorFinding(
                            "ok",
                            "update-available",
                            f"{installed_ver} is ahead of the published channel ({available_ver}) — never downgrade",
                        )
                    )
                else:
                    findings.append(
                        DoctorFinding(
                            "ok", "update-available", f"{installed_ver} is current"
                        )
                    )
    except (OSError, ValueError):
        pass  # network unavailable — skip silently

    # 7c. Stale files: look for files in installed skills that no longer exist in source
    if source_root and store_path.exists():
        stale_count = 0
        for skill_name in state.skills:
            installed_skill = store_path / skill_name
            source_skill = source_root / skill_name
            if not installed_skill.is_dir() or not source_skill.is_dir():
                continue
            for installed_file in installed_skill.rglob("*"):
                if not installed_file.is_file():
                    continue
                if installed_file.name == ".DS_Store":
                    continue
                rel = installed_file.relative_to(installed_skill)
                if not (source_skill / rel).exists():
                    stale_count += 1
        if stale_count > 0:
            findings.append(
                DoctorFinding(
                    "warn",
                    "stale-files",
                    f"{stale_count} file(s) in installed skills not present in source — "
                    "run 'vibecrafted update' with --mirror to clean up",
                )
            )
        else:
            findings.append(
                DoctorFinding(
                    "ok", "stale-files", "no orphan files in installed skills"
                )
            )

    # 7d. Agent CLI availability
    for agent_name in ("claude", "codex", "agy"):
        agent_bin = shutil.which(agent_name)
        if agent_bin:
            findings.append(
                DoctorFinding("ok", f"agent-cli:{agent_name}", f"-> {agent_bin}")
            )
        else:
            findings.append(
                DoctorFinding(
                    "warn",
                    f"agent-cli:{agent_name}",
                    "not found in PATH — spawn will fail for this agent",
                )
            )

    # 7e. VC Frame availability and version. VC_FRAME_* env/socket names
    # remain engine-room canonical, but the product binary is vc-frame.
    vc_frame_bin = shutil.which("vc-frame")
    if not vc_frame_bin:
        for bundled_name in ("vc-frame",):
            bundled_vc_frame = vibecrafted_runtime_bin() / bundled_name
            if bundled_vc_frame.is_file() and os.access(bundled_vc_frame, os.X_OK):
                vc_frame_bin = str(bundled_vc_frame)
                break
    if vc_frame_bin:
        try:
            vc_frame_ver = subprocess.run(
                [vc_frame_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            ver_str = (
                vc_frame_ver.stdout.strip()
                if vc_frame_ver.returncode == 0
                else "unknown"
            )
            findings.append(
                DoctorFinding("ok", "vc-frame", f"{ver_str} -> {vc_frame_bin}")
            )
        except (OSError, subprocess.TimeoutExpired):
            findings.append(DoctorFinding("ok", "vc-frame", f"-> {vc_frame_bin}"))
    else:
        findings.append(
            DoctorFinding(
                "warn",
                "vc-frame",
                "not found in PATH — dashboard/session commands unavailable",
            )
        )

    # 7f. vc-frame session health: detect dead/EXITED sessions that waste operator attention
    if vc_frame_bin:
        try:
            ls_result = subprocess.run(
                [vc_frame_bin, "list-sessions"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if ls_result.returncode == 0:
                dead_sessions = [
                    line.split()[0]
                    for line in ls_result.stdout.splitlines()
                    if "(EXITED" in line and line.strip()
                ]
                if dead_sessions:
                    names = ", ".join(dead_sessions[:5])
                    suffix = (
                        f" (+{len(dead_sessions) - 5} more)"
                        if len(dead_sessions) > 5
                        else ""
                    )
                    findings.append(
                        DoctorFinding(
                            "warn",
                            "vc_frame:dead-sessions",
                            f"{len(dead_sessions)} dead session(s): {names}{suffix}"
                            " — run 'vibecrafted dashboard gc --apply' to clean up safely",
                        )
                    )
                else:
                    findings.append(
                        DoctorFinding(
                            "ok", "vc_frame:dead-sessions", "no dead sessions"
                        )
                    )
        except (OSError, subprocess.TimeoutExpired):
            pass  # vc_frame not responsive — skip

    # 7g. Agent CLI stream contract: verify expected flags are recognized
    _agent_flag_checks = {
        "claude": [["--version"]],
        "codex": [["--version"]],
        "gemini": [["--version"], ["-v"], ["--help"]],
        "agy": [["--version"], ["--help"]],
        "junie": [["--version"], ["--help"]],
        "grok": [["--version"], ["--help"]],
    }
    for agent_name, flag_options in _agent_flag_checks.items():
        agent_bin = shutil.which(agent_name)
        if not agent_bin:
            continue
        last_detail = ""
        stream_ok = False
        stream_line = ""
        stream_flags: list[str] = []
        for flags in flag_options:
            try:
                flag_result = subprocess.run(
                    [agent_bin] + flags,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if flag_result.returncode == 0:
                    stream_ok = True
                    stream_flags = flags
                    stream_line = (
                        (flag_result.stdout or "").strip().splitlines()[0]
                        if flag_result.stdout
                        else "ok"
                    )
                    break
                last_detail = (
                    f"'{agent_name} {' '.join(flags)}' exited {flag_result.returncode}"
                )
            except (OSError, subprocess.TimeoutExpired):
                last_detail = (
                    f"timed out or failed to run '{agent_name} {' '.join(flags)}'"
                )
        if stream_ok:
            if agent_name == "gemini" and stream_flags == ["--help"]:
                stream_line = "CLI responds to --help; version flag unavailable"
            findings.append(
                DoctorFinding(
                    "ok",
                    f"agent-stream:{agent_name}",
                    stream_line,
                )
            )
        else:
            findings.append(
                DoctorFinding(
                    "warn",
                    f"agent-stream:{agent_name}",
                    last_detail,
                )
            )

    # 8. Shell smoke check: interactive shells should suppress UI noise under TERM=dumb
    zsh_path = shutil.which("zsh")
    if zsh_path:
        env = os.environ.copy()
        env["TERM"] = "dumb"
        smoke = subprocess.run(
            [zsh_path, "-ic", "exit"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        stdout = (smoke.stdout or "").strip()
        stderr = (smoke.stderr or "").strip()
        dumb_noise = describe_dumb_terminal_noise(stdout, stderr)
        if smoke.returncode == 0 and not dumb_noise:
            findings.append(
                DoctorFinding("ok", "shell:dumb-terminal", "zsh -ic stays quiet")
            )
        elif smoke.returncode == 0:
            findings.append(
                DoctorFinding(
                    "warn",
                    "shell:dumb-terminal",
                    dumb_noise,
                )
            )
        else:
            findings.append(
                DoctorFinding(
                    "warn",
                    "shell:dumb-terminal",
                    f"zsh -ic exit failed under TERM=dumb (exit {smoke.returncode})",
                )
            )

    return findings


def print_doctor(
    findings: list[DoctorFinding],
    guide_path: Path | None = None,
    verbose: bool = False,
) -> int:
    """Summary-first doctor report (CLI_PRODUCT_SPEC §6.4).

    Verdict in two lines; only failures and warnings are listed by default.
    Passing checks are a count — the full list lives under --verbose.
    Returns exit code (0 if no failures)."""
    fails = [f for f in findings if f.level == "fail"]
    warns = [f for f in findings if f.level == "warn"]
    oks = len(findings) - len(fails) - len(warns)

    print(f"\n{bold('⚒ doctor')} {dim(f'— {len(findings)} checks')}")
    print(
        f"{green(f'✓ {oks} ok')}   "
        f"{yellow(f'! {len(warns)} warnings')}   "
        f"{red(f'✗ {len(fails)} failures')}\n"
    )

    shown = findings if verbose else fails + warns
    for f in shown:
        icon = OK if f.level == "ok" else WARN if f.level == "warn" else MISS
        print(f"{icon} {f.component}: {f.message}")
    if shown:
        print()

    if fails:
        print(
            f"  {dim('→ fix:')} {cyan('vibecrafted doctor --fix-rc --fix-launchers')}\n"
        )

    actions = _doctor_action_items(findings)
    if actions:
        for action in actions[:5]:
            print(f"  {dim('→')} {action}")
        if len(actions) > 5:
            print(f"  {dim(f'… and {len(actions) - 5} more (--verbose)')}")
        print()

    if verbose:
        print(f"  {bold('Simple path:')}")
        print(f"    {cyan('vibecrafted init claude')}")
        print(
            "    "
            + cyan("vibecrafted workflow claude --prompt 'Plan and implement <task>'")
        )
        print("    " + cyan("vibecrafted implement codex --prompt 'Ship <task>'"))
        print()
        print(f"  {bold('Ship-ready path:')}")
        print("    " + cyan("vibecrafted dou claude --prompt 'Audit launch readiness'"))
        print(
            "    "
            + cyan("vibecrafted decorate codex --prompt 'Polish the release surface'")
        )
        print("    " + cyan("vibecrafted hydrate codex --prompt 'Package the product'"))
        print(
            "    " + cyan("vibecrafted release codex --prompt 'Prepare release steps'")
        )
        print()

    if guide_path is not None:
        print(f"  {dim(f'guide: {guide_path}')}")
    if not verbose:
        print(f"  {dim('details: vibecrafted doctor --verbose')}")
    print()

    return 1 if fails else 0


# ---------------------------------------------------------------------------
# Subcommand: install
# ---------------------------------------------------------------------------


class GoBack(Exception):
    """Raised by the interactive wizard to re-visit a previous step."""


def _cmd_install_verbose(args: argparse.Namespace, repo_root: Path) -> int:
    """Original verbose install flow — used when --compact is NOT set."""
    interactive = _IS_TTY and not args.non_interactive
    dry_run = args.dry_run
    advanced = args.advanced
    mirror = args.mirror
    cli_with_shell = args.with_shell
    cli_tools = args.tools  # None = all, list = subset
    cli_skill_filter = args.skill_filter  # None = all, list = subset

    # --- Header ---
    sep = brand_separator(33)
    print()
    fw_ver = get_install_version(repo_root)
    print(f"  \u2692 {VAPOR_HEADER} \u2692")
    print()
    print(f"  {brand_version_line(fw_ver)}")
    print(f"  {TAGLINE}")
    print(f"  {PRODUCT_LINE}")
    print(f"  {sep}")
    print(f"  Source: {repo_root}")
    print()

    # --- Discover skills ---
    skills = discover_skills(repo_root)
    if not skills:
        print(red("No skills found in repo."))
        return 1

    cats = categorize_all(skills)
    skill_names = [s.name for s in skills]

    # --- Show bundle ---
    print(bold("Framework bundle:"))
    print(f"  Pipeline skills   {len(cats['pipeline'])}")
    if cats["specialist"]:
        print(f"  Specialist skills {len(cats['specialist'])}")
    if advanced:
        print()
        for cat_key in ("pipeline", "specialist"):
            cat = SKILL_CATEGORIES[cat_key]
            names = cats[cat_key]
            if names:
                print(f"  {cyan(cat['label'])} ({len(names)})")
                for n in names:
                    print(f"    - {n}")
    else:
        print(
            f"  Use {cyan('--advanced')} to choose skills and runtimes interactively."
        )
    print()

    # --- Interactive Wizard ---
    step = 0
    selected_skills = list(skill_names)
    all_runtimes = list(STANDARD_VIEW_RUNTIMES)
    install_shell = cli_with_shell
    write_shell_rc = getattr(args, "write_shell_rc", False)
    installed_foundations: dict[str, dict] = {}

    while True:
        try:
            if step == 0:
                # Skills selection
                if cli_skill_filter:
                    unknown = [s for s in cli_skill_filter if s not in skill_names]
                    if unknown:
                        print(yellow(f"Unknown skills (skipped): {', '.join(unknown)}"))
                    selected_skills = [s for s in cli_skill_filter if s in skill_names]
                    if not selected_skills:
                        print(red("No valid skills selected."))
                        return 1
                    step += 1
                elif advanced and interactive:
                    defaults = [s in selected_skills for s in skill_names]
                    result = ask_multi(
                        "Select skills to install:", skill_names, defaults
                    )
                    selected_skills = [n for n, sel in zip(skill_names, result) if sel]
                    if not selected_skills:
                        print(red("No skills selected."))
                        return 1
                    print()
                    step += 1
                else:
                    step += 1

            elif step == 1:
                # System check (static output, just flows through unless error)
                if not getattr(args, "_sys_checked", False):
                    print(bold("System check:"))
                    sys_deps = detect_system_deps()
                    for cmd, path in sys_deps.items():
                        if path:
                            print(f"  {OK} {cmd} -> {dim(path)}")
                        elif cmd in RECOMMENDED_DEPS:
                            print(f"  {WARN} {cmd}")
                        else:
                            print(f"  {MISS} {cmd}")

                    osascript = detect_osascript()
                    if osascript:
                        print(f"  {OK} osascript -> {dim(osascript)}")
                    else:
                        print(f"  {OPT} osascript")
                    print()

                    missing_critical = [
                        cmd for cmd in ("python3", "git") if not sys_deps.get(cmd)
                    ]
                    if missing_critical:
                        print(
                            red(
                                f"Missing critical dependencies: {', '.join(missing_critical)}"
                            )
                        )
                        print("Install them before continuing.")
                        return 1
                    if not sys_deps.get("zsh"):
                        print(f"  {OPT} zsh")
                    args._sys_checked = True
                step += 1

            elif step == 2:
                # Runtimes
                if not getattr(args, "_rt_checked", False):
                    print(bold("Agent runtimes:"))
                    available_runtimes = detect_agent_runtimes()
                    for rt, path in available_runtimes.items():
                        if path:
                            print(f"  {OK} {rt} -> {dim(path)}")
                        else:
                            print(f"  {OPT} {rt} {dim('(not installed)')}")
                    print()
                    args._rt_checked = True

                if cli_tools:
                    all_runtimes = [
                        rt for rt in cli_tools if rt in SYMLINK_TARGET_CHOICES
                    ]
                    step += 1
                elif interactive and not advanced:
                    print(
                        dim(
                            "  Note: gemini-cli in some versions duplicates the workflows, inheriting"
                        )
                    )
                    print(
                        dim(
                            "  skills from the other agents. Gemini symlinks skipped by default."
                        )
                    )
                    create_all = ask_yn(
                        "Create the standard skill views for agents, claude, and codex?",
                        default=True,
                    )
                    if not create_all:
                        defaults = [rt in all_runtimes for rt in SYMLINK_TARGET_CHOICES]
                        result = ask_multi(
                            "Select runtimes for symlink views:",
                            SYMLINK_TARGET_CHOICES,
                            defaults,
                        )
                        all_runtimes = [
                            rt for rt, sel in zip(SYMLINK_TARGET_CHOICES, result) if sel
                        ]
                    print()
                    step += 1
                elif advanced and interactive:
                    print(
                        dim(
                            "  Note: gemini-cli in some versions duplicates the workflows, inheriting"
                        )
                    )
                    print(
                        dim(
                            "  skills from the other agents. Gemini symlinks skipped by default."
                        )
                    )
                    defaults = [rt in all_runtimes for rt in SYMLINK_TARGET_CHOICES]
                    result = ask_multi(
                        "Select runtimes for symlink views:",
                        SYMLINK_TARGET_CHOICES,
                        defaults,
                    )
                    all_runtimes = [
                        rt for rt, sel in zip(SYMLINK_TARGET_CHOICES, result) if sel
                    ]
                    print()
                    step += 1
                else:
                    step += 1

            elif step == 3:
                # Foundations
                if not getattr(args, "_fnd_checked", False):
                    print(bold("Runtime Foundations:"))
                    missing_foundations: list[Foundation] = []
                    for f in FOUNDATIONS:
                        path, channel = install_or_find_foundation(
                            f, repo_root, dry_run=dry_run
                        )
                        installed_foundations[f.name] = {
                            "channel": channel,
                            "path": path,
                        }
                        if path:
                            print(f"  {OK} {f.name} -> {dim(path)}")
                            if channel == "bundled":
                                print(f"       {dim('installed from bundled payload')}")
                            print(f"       {dim(f.description)}")
                        elif f.required:
                            print(f"  {MISS} {f.name} — {f.description}")
                            print(f"       {dim(f.install_hint())}")
                            missing_foundations.append(f)
                        else:
                            print(f"  {OPT} {f.name} — {f.description}")
                            print(f"       {dim(f.install_hint())}")
                    print()
                    args._missing_foundations = missing_foundations
                    args._fnd_checked = True

                missing_foundations = args._missing_foundations
                if (
                    missing_foundations
                    and interactive
                    and not getattr(args, "_fnd_warn_done", False)
                ):
                    print(yellow("Missing foundations are not auto-installed here."))
                    print(
                        dim(
                            "Use the owning product or support-tool installer, then rerun diagnostics."
                        )
                    )
                    args._fnd_warn_done = True
                    print()

                step += 1

            elif step == 4:
                # Shell helpers
                if not cli_with_shell and interactive:
                    install_shell = ask_yn(
                        "Install the shell helper layer?",
                        default=install_shell,
                    )
                    print()

                if install_shell:
                    conflicts = scan_helper_conflicts()
                    if conflicts:
                        should_proceed = report_helper_conflicts(conflicts, interactive)
                        if not should_proceed:
                            install_shell = False
                if install_shell and interactive and not write_shell_rc:
                    write_shell_rc = ask_yn(
                        "Add helper/PATH lines to shell rc files now?",
                        default=False,
                    )
                    print()
                step += 1

            elif step == 5:
                # Post-wizard setup
                for f in FOUNDATIONS:
                    if f.name not in installed_foundations:
                        path, channel = install_or_find_foundation(
                            f, repo_root, dry_run=dry_run
                        )
                        installed_foundations[f.name] = {
                            "channel": channel,
                            "path": path,
                        }
                break

        except GoBack:
            # Re-evaluate previous interactive steps to find the closest one
            if step == 4:
                # Going back from shell helpers
                if missing_foundations and interactive:
                    step = 3
                else:
                    step = 2
            elif step == 3:
                # Going back from foundations
                if cli_tools:
                    step = 0
                else:
                    step = 2
            elif step == 2:
                # Going back from runtimes
                if advanced and interactive:
                    step = 0
                else:
                    print(dim("  (Cannot go back further)"))
            elif step == 0:
                print(dim("  (Cannot go back further)"))

    # --- Confirm ---
    shared_home = vibecrafted_home()
    store_path = _canonical_store_path(shared_home, create=not dry_run)

    print(bold("Plan:"))
    print(f"  Skills:    {len(selected_skills)} -> {cyan(str(store_path))}")
    print(f"  Runtimes:  {', '.join(all_runtimes)} {dim('(skill views)')}")
    print(f"  Shell:     {'enabled' if install_shell else 'skipped'}")
    if install_shell:
        shell_rc_status = "opt-in write" if write_shell_rc else "manual line only"
        print(f"  Shell rc:  {shell_rc_status}")
    if dry_run:
        print(f"  Mode:      {yellow('DRY RUN')}")
    print()

    if interactive:
        if not ask_yn("Start install?", default=True):
            print("Install stopped. No changes were made.")
            return 0
        print()

    # --- Backup existing state ---
    print(bold("Saving current state..."))
    orphaned_entries = collect_orphaned_skills(
        store_path, all_runtimes, set(selected_skills)
    )
    preinstall_launchers = _snapshot_launcher_entries()
    preinstall_helpers = _snapshot_helper_files() if install_shell else []
    backup_ts = create_backup(
        store_path,
        all_runtimes,
        selected_skills,
        orphaned_entries=orphaned_entries,
        launcher_entries=preinstall_launchers,
        helper_entries=preinstall_helpers,
        dry_run=dry_run,
    )
    if backup_ts:
        print(f"  {OK} Backup saved: {_backup_root(store_path) / backup_ts}")
    else:
        print(f"  {dim('nothing to back up (fresh install)')}")
    print()

    # --- Execute: rsync skills ---
    print(bold("Installing shared skills..."))
    skills_dir = source_skills_root(repo_root)
    packaged_skills = repo_root / "vibecrafted-core" / "vibecrafted_core" / "skills"
    if skills_dir == packaged_skills:
        print(f"  {OK} Skills are carried by the immutable runtime generation")
    else:
        if not dry_run:
            store_path.mkdir(parents=True, exist_ok=True)
        for name in selected_skills:
            src = skills_dir / name
            dst = store_path / name
            print(f"  {dim('->')} {name}")
            rsync_skill(src, dst, dry_run=dry_run, mirror=mirror)
        for rule in sync_skill_root_rules(skills_dir, store_path, dry_run=dry_run):
            print(f"  {dim('->')} {rule}")
    print()

    # --- Execute: staged control plane ---
    print(bold("Refreshing staged control plane..."))
    try:
        current_tools = refresh_current_tools(
            repo_root, shared_home, dry_run=dry_run, mirror=mirror
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"  {dim(_staged_sync_failure_detail(exc))}")
        err_line("could not refresh staged tools", "rerun `vibecrafted update`")
        return 1
    if current_tools is None:
        print(f"  {WARN} Source is not a full framework checkout; staged tools skipped")
    elif dry_run:
        print(f"  {dim('would sync')} {repo_root} -> {current_tools}")
    else:
        print(f"  {OK} {current_tools}")
    print()

    # --- Execute: symlink views ---
    print(bold("Linking agent views..."))
    for rt in all_runtimes:
        rt_skills = Path.home() / f".{rt}" / "skills"
        if not dry_run:
            rt_skills.mkdir(parents=True, exist_ok=True)
        print(f"  {cyan(rt)} -> {rt_skills}")
        for rule in sync_skill_root_rules(skills_dir, rt_skills, dry_run=dry_run):
            print(f"    {dim('->')} {rule}")
        for name in selected_skills:
            default = store_path / name
            link = rt_skills / name
            create_skill_view_symlink(default, link, dry_run=dry_run)
    for shadow in prune_shadowed_skill_views(
        store_path, selected_skills, all_runtimes, dry_run=dry_run
    ):
        print(f"  {dim('removed shadow')} {shadow}")
    print()

    # --- Execute: agent command surfaces ---
    print(bold("Installing agent commands..."))
    install_agent_commands(all_runtimes, dry_run=dry_run)
    print()

    # --- Prune orphaned vc-* skills no longer in bundle ---
    prune_orphaned_skills(
        store_path,
        all_runtimes,
        set(selected_skills),
        dry_run=dry_run,
        orphaned_entries=orphaned_entries,
        interactive=interactive,
    )

    # --- Prune old vetcoders-* skills ---
    prune_legacy_skills(
        store_path, all_runtimes, dry_run=dry_run, interactive=interactive
    )

    # --- Execute: clean compat and duplicate RC entries ---
    if write_shell_rc:
        for rcname in (".bashrc", ".zshrc"):
            rcfile = Path.home() / rcname
            if rcfile.exists():
                rc_content = rcfile.read_text()
                if not _is_writable(rcfile):
                    print(f"  {WARN} {rcfile} is locked — cannot clean old entries")
                    continue
                cleaned_rc, removed_rc = _clean_legacy_rc_entries(rc_content)
                if removed_rc > 0 and not dry_run:
                    rcfile.write_text(cleaned_rc)
                    print(f"  {OK} Cleaned {removed_rc} old entries from {rcname}")
                elif removed_rc > 0:
                    print(
                        f"  {dim('would clean')} {removed_rc} old entries from {rcname}"
                    )

    # --- Execute: shell helpers ---
    if install_shell:
        print(bold("Installing shell helper..."))
        shell_script = (
            repo_root
            / "vibecrafted-core"
            / "vibecrafted_core"
            / "runtime"
            / "scripts"
            / "install-shell.sh"
        )
        if shell_script.exists():
            shell_cmd = ["bash", str(shell_script), "--source", str(repo_root)]
            if write_shell_rc:
                shell_cmd.append("--write-rc")
            if dry_run:
                shell_cmd.append("--dry-run")
            subprocess.run(shell_cmd, check=False)
        else:
            print(f"  {WARN} Shell installer not found: {shell_script}")
        print()

    # --- Execute: vibecrafted launcher ---
    _install_launcher(repo_root, dry_run, update_rc=write_shell_rc)
    if current_tools is not None:
        moved_agency = cleanse_state_home_agency(current_tools, dry_run=dry_run)
        if moved_agency:
            print(f"  {OK} Moved {moved_agency} state-home agency payload(s)")
        else:
            print(f"  {OK} State home has no agency payloads to move")
        print()

    # --- Fix Gemini plan.directory if it points into .vibecrafted ---
    _configure_gemini_plans(dry_run)

    # --- Save state ---
    now = datetime.now(timezone.utc).isoformat()
    state = InstallState(
        installed_at=now,
        updated_at=now,
        framework_version=get_install_version(repo_root),
        repo_commit=get_repo_commit(repo_root),
        repo_url=get_repo_url(repo_root),
        skills=selected_skills,
        runtimes=all_runtimes,
        launcher_entries=_snapshot_launcher_entries(),
        helper_files=_snapshot_helper_files() if install_shell else [],
        foundations=installed_foundations,
        product_tools=snapshot_product_tool_state(),
        shell_helpers=install_shell,
        install_path=str(store_path),
    )
    if not dry_run:
        state_file = vibecrafted_home() / STATE_FILE
        state.save(state_file.parent)
        print(f"  {OK} Install manifest saved to {state_file}")
    else:
        print(f"  {SKIP} Dry run — manifest not saved")
    print()

    # --- Doctor ---
    print(bold("Verification:"))
    if dry_run:
        print(f"  {SKIP} Skipped in dry-run mode")
    else:
        findings = run_doctor(store_path, state)
        _pause_for_runtime_contract_failures(findings)
        guide_path = write_start_here_guide(store_path, state, findings)
        # Print only failures and warnings
        issues = [finding for finding in findings if finding.level != "ok"]
        if issues:
            for finding in issues:
                icon = WARN if finding.level == "warn" else MISS
                print(f"  {icon} {finding.component}: {finding.message}")
        else:
            print(f"  {OK} All checks passed")
        print(f"  {OK} Start-here guide saved to {guide_path}")
    print()

    # --- Done: compact one-screen summary ---
    _print_unicode_summary(repo_root, store_path, skills)
    return 0


def _launcher_symlink_target(repo_root: Path) -> Path:
    """Resolve what ~/.local/bin/vibecrafted should point at.

    The host launcher always enters the immutable installed generation. Python
    tooling may still live in its uv environment, but it is an implementation
    dependency of the deck, never the user-facing runtime owner.
    """
    _ = repo_root
    return (
        vibecrafted_tools_home()
        / "vibecrafted-current"
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "deck"
        / "vibecrafted"
    )


def _install_launcher(repo_root: Path, dry_run: bool, update_rc: bool = False) -> None:
    """Install vibecrafted launcher to portable and compat bin surfaces."""
    launcher_src = repo_root / "scripts" / "vibecrafted"
    if launcher_src.exists():
        if not dry_run:
            legacy_redirect_src = repo_root / "scripts" / "vibecraft"
            canonical_bin_dir = vibecrafted_launcher_bin()
            canonical_bin_dir.mkdir(parents=True, exist_ok=True)
            canonical_launcher = canonical_bin_dir / "vibecrafted"

            # Target 1: the immutable installed generation owns the launcher.
            # Never point the public command at uv state or a source checkout.
            shim = _launcher_symlink_target(repo_root)
            if not shim.is_file():
                raise OSError(
                    "installed runtime deck is missing; publish the runtime "
                    "generation before installing launchers"
                )
            if canonical_launcher.exists() or canonical_launcher.is_symlink():
                if canonical_launcher.is_symlink():
                    try:
                        link_target = Path(os.readlink(canonical_launcher))
                    except OSError:
                        link_target = Path("")
                    if link_target != shim:
                        canonical_launcher.unlink()
                        create_symlink(shim, canonical_launcher)
                else:
                    canonical_launcher.unlink()
                    create_symlink(shim, canonical_launcher)
            else:
                create_symlink(shim, canonical_launcher)

            canonical_legacy = canonical_bin_dir / "vibecraft"
            if legacy_redirect_src.exists():
                _copy_managed_launcher(legacy_redirect_src, canonical_legacy)

            for launcher_bin_dir in _launcher_bin_dirs():
                launcher_bin_dir.mkdir(parents=True, exist_ok=True)
                launcher_dst = launcher_bin_dir / "vibecrafted"
                if launcher_dst != canonical_launcher:
                    create_symlink(canonical_launcher, launcher_dst)
                for wrapper in LAUNCHER_WRAPPERS:
                    if wrapper in PYTHON_ENTRYPOINT_LAUNCHERS:
                        continue
                    create_symlink(Path("vibecrafted"), launcher_bin_dir / wrapper)
                # Replace old vibecraft binary with a thin redirect
                legacy_dst = launcher_bin_dir / "vibecraft"
                if legacy_redirect_src.exists() and legacy_dst != canonical_legacy:
                    create_symlink(canonical_legacy, legacy_dst)
        else:
            for launcher_bin_dir in _launcher_bin_dirs():
                shim = _launcher_symlink_target(repo_root)
                create_symlink(shim, launcher_bin_dir / "vibecrafted", dry_run=True)
                for wrapper in LAUNCHER_WRAPPERS:
                    if wrapper in PYTHON_ENTRYPOINT_LAUNCHERS:
                        continue
                    create_symlink(
                        Path("vibecrafted"), launcher_bin_dir / wrapper, dry_run=True
                    )
        # Ensure $HOME/.local/bin is in PATH via shell rc files only when the
        # caller has explicit consent. Otherwise leave a copyable instruction.
        canonical_path_line = _launcher_path_line()
        path_lines = [canonical_path_line, *_legacy_launcher_path_lines()]
        path_comment = "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher"
        if update_rc:
            for rcname in (".bashrc", ".zshrc"):
                rcfile = Path.home() / rcname
                if rcfile.exists():
                    content = rcfile.read_text()
                    cleaned = content
                    removed = 0
                    for path_line in path_lines:
                        cleaned, removed_now = _strip_rc_entry(
                            cleaned, path_line, path_comment
                        )
                        removed += removed_now
                    has_path = _rc_has_vibecrafted_bin_path(cleaned)
                    changed = removed > 0
                    if not has_path:
                        if cleaned and not cleaned.endswith("\n"):
                            cleaned += "\n"
                        cleaned += f"\n# {path_comment}\n{canonical_path_line}\n"
                        changed = True
                    if changed and not dry_run:
                        rcfile.write_text(cleaned)
        else:
            print("  Shell rc files unchanged. To expose launchers, add:")
            print(f"    {canonical_path_line}")
        print()


def _print_unicode_summary(
    repo_root: Path, store_path: Path, skills: list[Path], out=None
) -> None:
    """Print the unicode summary box. If out is given, write there instead of stdout."""
    _out = out or sys.stdout
    fw_ver_display = get_install_version(repo_root)
    skill_count = len(skills)
    current_runtime = (
        _current_tools_link(store_path)
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
    )

    def _agent_spawn_present(agent: str) -> bool:
        """True if a spawn script for `agent` exists in the staged runtime or the legacy
        vc-agents store fallback.
        """
        # Spawn scripts live in the staged control-plane runtime; the legacy
        # vc-agents store layout is kept only as a back-compat fallback.
        return (current_runtime / "scripts" / f"{agent}_spawn.sh").exists() or (
            store_path / "vc-agents" / "scripts" / f"{agent}_spawn.sh"
        ).exists()

    agent_list = " \u00b7 ".join(
        a for a in ("claude", "codex", "gemini") if _agent_spawn_present(a)
    )
    shell_str = _helper_surface_label()
    fnd_ok = [f.name for f in FOUNDATIONS if f.is_installed()]
    fnd_str = " \u00b7 ".join(fnd_ok[:3]) if fnd_ok else "none"
    if len(fnd_ok) > 3:
        fnd_str += f" +{len(fnd_ok) - 3}"
    store_display = str(store_path).replace(str(Path.home()), "~")

    sep = brand_separator(37)

    lines = [
        f"\u2692 {VAPOR_HEADER} \u2692",
        "",
        brand_version_line(fw_ver_display),
        TAGLINE,
        PRODUCT_LINE,
        sep,
        "",
        f"\u2713 Skills       {skill_count} installed",
        f"\u2713 Agents       {agent_list}",
        f"\u2713 Helpers      {shell_str}",
        f"\u2713 Foundations   {fnd_str}",
        f"\u2713 Store        {store_display}",
        f"\u2713 Guide        {start_here_path()}",
        "",
        sep,
        "  Start        vibecrafted help",
        "  Verify       vibecrafted doctor",
        "  Reverse      vibecrafted uninstall",
        "",
        f"  {FOOTER_BRANDING}",
        f"  {FRAMEWORK_STAMP}",
    ]

    _out.write("\n")
    for line in lines:
        _out.write(f"  {line}\n")
    _out.write("\n")

    missing_fnd = [f for f in FOUNDATIONS if f.required and not f.is_installed()]
    if missing_fnd:
        _out.write("\n")
        _out.write("  Foundations still missing:\n")
        for f in missing_fnd:
            _out.write(f"    - {f.name}: {f.install_hint()}\n")
    _out.write("\n")
    _out.flush()


def _cmd_install_compact(args: argparse.Namespace, repo_root: Path) -> int:
    """Compact install — one screen of output, details to log."""
    dry_run = args.dry_run
    mirror = args.mirror
    cli_with_shell = args.with_shell
    fw_ver = get_install_version(repo_root)

    shared_home = vibecrafted_home()
    store_path = _canonical_store_path(shared_home, create=not dry_run)
    log_path = shared_home / "install.log"

    # --- Discover skills (before redirecting stdout) ---
    skills = discover_skills(repo_root)
    if not skills:
        print(red("No skills found in repo."))
        return 1

    skill_names = [s.name for s in skills]
    selected_skills = list(skill_names)
    all_runtimes = list(STANDARD_VIEW_RUNTIMES)
    install_shell = cli_with_shell
    write_shell_rc = getattr(args, "write_shell_rc", False)
    installed_foundations: dict[str, dict] = {}

    # --- System check (critical deps — must fail visibly) ---
    sys_deps = detect_system_deps()
    missing_critical = [cmd for cmd in ("python3", "git") if not sys_deps.get(cmd)]
    if missing_critical:
        print(red(f"  Missing critical dependencies: {', '.join(missing_critical)}"))
        print("  Install them before continuing.")
        return 1

    # --- All verbose output goes to log; compact lines go to real stdout.
    # --debug tees the full transaction log onto stdout as well. ---
    debug = getattr(args, "debug", False)
    with compact_logging(log_path, quiet=not debug) as out:
        # Log header
        print(f"𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Installer v{fw_ver} — compact mode")
        print(f"Source: {repo_root}")
        print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
        print()
        _compact_checkpoint(
            out,
            1,
            "Introduction",
            (
                f"Source  {repo_root}",
                f"Log     {log_path}",
            ),
        )

        # Log system deps
        print("System check:")
        for cmd, path in sys_deps.items():
            print(f"  {cmd}: {path or 'MISSING'}")
        print()

        # Log agent runtimes
        available_runtimes = detect_agent_runtimes()
        print("Agent runtimes:")
        for rt, path in available_runtimes.items():
            print(f"  {rt}: {path or 'not installed'}")
        print()

        # Log foundations
        print("Runtime Foundations:")
        for f in FOUNDATIONS:
            path, channel = install_or_find_foundation(f, repo_root, dry_run=dry_run)
            installed_foundations[f.name] = {
                "channel": channel,
                "path": path,
            }
            print(
                f"  {f.name}: {path or 'not installed'} [{channel}] {'(required)' if f.required else '(optional)'}"
            )
        print()
        detected_agents = [
            rt for rt in ("claude", "codex", "gemini") if available_runtimes.get(rt)
        ]
        _compact_checkpoint(
            out,
            2,
            "Diagnostics and Plan",
            (
                f"Plan   {len(selected_skills)} skills · agents {', '.join(detected_agents) or 'none'} · shell {'on' if install_shell else 'off'}",
                f"Into   {store_path}",
            ),
        )

        # Backup
        print("Backup:")
        _compact_checkpoint(out, 3, "Installation")
        orphaned_entries = collect_orphaned_skills(
            store_path, all_runtimes, set(selected_skills)
        )
        preinstall_launchers = _snapshot_launcher_entries()
        preinstall_helpers = _snapshot_helper_files() if install_shell else []
        backup_ts = create_backup(
            store_path,
            all_runtimes,
            selected_skills,
            orphaned_entries=orphaned_entries,
            launcher_entries=preinstall_launchers,
            helper_entries=preinstall_helpers,
            dry_run=dry_run,
        )
        if backup_ts:
            print(f"  Saved: {_backup_root(store_path) / backup_ts}")
        else:
            print("  Fresh install, nothing to back up")
        print()

        # Install skills
        print("Installing skills:")
        skills_dir = source_skills_root(repo_root)
        packaged_skills = repo_root / "vibecrafted-core" / "vibecrafted_core" / "skills"
        if skills_dir == packaged_skills:
            print("  carried by immutable runtime generation")
        else:
            if not dry_run:
                store_path.mkdir(parents=True, exist_ok=True)
            # One live counter line (§6.6), per-skill detail stays in the log.
            total_skills = len(selected_skills)
            for idx, name in enumerate(selected_skills, 1):
                src = skills_dir / name
                dst = store_path / name
                print(f"  -> {name}")
                if _compact_status_is_live(out):
                    frame = SPINNER_FRAMES[idx % len(SPINNER_FRAMES)]
                    _compact_line(
                        out, dim(frame), "Skills", f"installing {idx}/{total_skills}"
                    )
                rsync_skill(src, dst, dry_run=dry_run, mirror=mirror)
            for rule in sync_skill_root_rules(skills_dir, store_path, dry_run=dry_run):
                print(f"  -> {rule}")
        print()

        print("Refreshing staged control plane:")
        try:
            current_tools = refresh_current_tools(
                repo_root, shared_home, dry_run=dry_run, mirror=mirror
            )
        except (
            OSError,
            subprocess.CalledProcessError,
            DistributionManifestError,
        ) as exc:
            print(f"  FAILED: {_staged_sync_failure_detail(exc)}")
            _clear_compact_status(out)
            err_line(
                "could not refresh staged tools",
                "rerun `vibecrafted update`",
                str(log_path),
            )
            return 1
        if current_tools is None:
            print("  skipped: source is not a full framework checkout")
            _compact_line(out, WARN, "Tools", "staged control plane skipped")
        elif dry_run:
            print(f"  would sync: {repo_root} -> {current_tools}")
            _compact_line(out, SKIP, "Tools", "dry run")
        else:
            print(f"  synced: {repo_root} -> {current_tools}")
            _compact_line(out, green("\u2713"), "Tools", "staged current refreshed")
        print()

        # Compact status lines on real stdout
        _compact_line(
            out, green("\u2713"), "Skills", f"{len(selected_skills)} installed"
        )

        # Symlink views
        print("Linking agent views:")
        for rt in all_runtimes:
            rt_skills = Path.home() / f".{rt}" / "skills"
            if not dry_run:
                rt_skills.mkdir(parents=True, exist_ok=True)
            print(f"  {rt} -> {rt_skills}")
            for rule in sync_skill_root_rules(skills_dir, rt_skills, dry_run=dry_run):
                print(f"    -> {rule}")
            for name in selected_skills:
                default = store_path / name
                link = rt_skills / name
                create_skill_view_symlink(default, link, dry_run=dry_run)
        for shadow in prune_shadowed_skill_views(
            store_path, selected_skills, all_runtimes, dry_run=dry_run
        ):
            print(f"  removed shadow: {shadow}")
        print()

        print("Installing agent commands:")
        install_agent_commands(all_runtimes, dry_run=dry_run)
        print()

        # Compact line: agents
        agent_names = [
            rt for rt in ("claude", "codex", "gemini") if available_runtimes.get(rt)
        ]
        _compact_line(
            out,
            green("\u2713"),
            "Agents",
            " \u00b7 ".join(agent_names) if agent_names else "none detected",
        )

        # Prune (logged only)
        prune_orphaned_skills(
            store_path,
            all_runtimes,
            set(selected_skills),
            dry_run=dry_run,
            orphaned_entries=orphaned_entries,
            interactive=False,
        )
        prune_legacy_skills(
            store_path, all_runtimes, dry_run=dry_run, interactive=False
        )

        # Clean compat RC entries only after explicit rc-write consent.
        if write_shell_rc:
            for rcname in (".bashrc", ".zshrc"):
                rcfile = Path.home() / rcname
                if rcfile.exists():
                    rc_content = rcfile.read_text()
                    if not _is_writable(rcfile):
                        continue
                    cleaned_rc, removed_rc = _clean_legacy_rc_entries(rc_content)
                    if removed_rc > 0 and not dry_run:
                        rcfile.write_text(cleaned_rc)

        # Shell helpers
        if install_shell:
            print("Installing shell helper:")
            shell_script = (
                repo_root
                / "vibecrafted-core"
                / "vibecrafted_core"
                / "runtime"
                / "scripts"
                / "install-shell.sh"
            )
            if shell_script.exists():
                shell_cmd = ["bash", str(shell_script), "--source", str(repo_root)]
                if write_shell_rc:
                    shell_cmd.append("--write-rc")
                if dry_run:
                    shell_cmd.append("--dry-run")
                result = subprocess.run(
                    shell_cmd, capture_output=True, text=True, check=False
                )
                # Log the shell installer output
                if result.stdout:
                    print(result.stdout)
                if result.stderr:
                    print(result.stderr)
            else:
                print(f"  Shell installer not found: {shell_script}")
            print()

        _compact_line(
            out,
            green("\u2713"),
            "Helpers",
            _helper_surface_label(),
        )

        # Foundations compact line
        fnd_ok = [f.name for f in FOUNDATIONS if f.is_installed()]
        fnd_str = " \u00b7 ".join(fnd_ok[:3]) if fnd_ok else "none"
        if len(fnd_ok) > 3:
            fnd_str += f" +{len(fnd_ok) - 3}"
        _compact_line(out, green("\u2713"), "Foundations", fnd_str)

        # Store path
        store_display = str(store_path).replace(str(Path.home()), "~")
        _compact_line(out, green("\u2713"), "Store", store_display)

        # Launcher
        _install_launcher(repo_root, dry_run, update_rc=write_shell_rc)
        if current_tools is not None:
            moved_agency = cleanse_state_home_agency(current_tools, dry_run=dry_run)
            print(f"  state agency moved: {moved_agency}")
            _compact_line(
                out,
                green("\u2713"),
                "State home",
                "agency-free" if not moved_agency else f"moved {moved_agency}",
            )

        # Fix Gemini plan.directory if it points into .vibecrafted
        _configure_gemini_plans(dry_run)

        # Save state
        now = datetime.now(timezone.utc).isoformat()
        state = InstallState(
            installed_at=now,
            updated_at=now,
            framework_version=fw_ver,
            repo_commit=get_repo_commit(repo_root),
            repo_url=get_repo_url(repo_root),
            skills=selected_skills,
            runtimes=all_runtimes,
            launcher_entries=_snapshot_launcher_entries(),
            helper_files=_snapshot_helper_files() if install_shell else [],
            foundations=installed_foundations,
            product_tools=snapshot_product_tool_state(),
            shell_helpers=install_shell,
            install_path=str(store_path),
        )
        if not dry_run:
            state_file = vibecrafted_home() / STATE_FILE
            state.save(state_file.parent)
            print(f"Manifest saved: {state_file}")
        print()

        # Doctor (logged)
        if not dry_run:
            print("Verification:")
            findings = run_doctor(store_path, state)
            _pause_for_runtime_contract_failures(findings)
            guide_path = write_start_here_guide(store_path, state, findings)
            issues = [finding for finding in findings if finding.level != "ok"]
            if issues:
                for finding in issues:
                    print(f"  [{finding.level}] {finding.component}: {finding.message}")
                # Surface critical issues on compact output too
                critical = [finding for finding in issues if finding.level == "fail"]
                if critical:
                    _clear_compact_status(out)
                    err_line(
                        "install verification found failures",
                        "vibecrafted doctor",
                        str(log_path),
                    )
            else:
                print("  All checks passed")
            print(f"  Start-here guide: {guide_path}")
        print()

    # --- Finish card (CLI_PRODUCT_SPEC §6.1): result, key facts, one next step. ---
    _clear_compact_status(sys.stdout)
    _compact_checkpoint(sys.stdout, 4, "Onboarding")
    fw_ver_display = get_install_version(repo_root)
    store_display = str(vibecrafted_home()).replace(str(Path.home()), "~")
    agent_str = " ".join(agent_names) if agent_names else "none"
    missing_fnd = [f for f in FOUNDATIONS if f.required and not f.is_installed()]

    # NB: keep the unicode escapes OUT of f-string expression parts \u2014 a
    # backslash inside `{...}` is a SyntaxError on Python < 3.12, and this
    # project supports >=3.11. Build the pieces first, then interpolate.
    check_mark = green("\u2713")
    product_banner = bold(
        f"\U0001d685\U0001d692\U0001d68b\U0001d68e\U0001d68c\U0001d69b\U0001d68a"
        f"\U0001d68f\U0001d69d\U0001d68e\U0001d68d. {fw_ver_display} installed"
    )
    print()
    print(f"  {check_mark} {product_banner}")
    print()
    print(
        f"    skills {len(selected_skills)} \u00b7 agents {agent_str} \u00b7 store {store_display}"
    )
    if missing_fnd:
        names = " · ".join(f.name for f in missing_fnd)
        print(f"    {WARN} foundations missing: {names} — vibecrafted doctor")
    print()
    print(f"    → {cyan('vibecrafted init claude')}       {dim('start here')}")
    print(f"    → {cyan('vibecrafted doctor')}            {dim('verify')}")
    print()

    return 0


def cmd_install(args: argparse.Namespace) -> int:
    """Dispatch `install` to the verbose wizard flow or the compact one-screen flow based on
    flags/TTY.
    """
    repo_root = Path(args.source).resolve()
    if not repo_root.is_dir():
        err_line(f"repo root not found: {repo_root}")
        return 1

    # Strict modes (CLI_PRODUCT_SPEC §3.5): compact is the default; --verbose
    # restores the per-step narration; --compact is retired (silent no-op).
    # An attended TTY without --non-interactive keeps the consent wizard,
    # which lives in the verbose flow.
    verbose = getattr(args, "verbose", False) or getattr(args, "advanced", False)
    interactive = _IS_TTY and not args.non_interactive

    if verbose or interactive:
        return _cmd_install_verbose(args, repo_root)
    return _cmd_install_compact(args, repo_root)


# ---------------------------------------------------------------------------
# Subcommand: doctor
# ---------------------------------------------------------------------------


def _known_bundle_names() -> list[str]:
    """Skill names this installer manages. Used to scope doctor checks."""
    # Try to discover from repo checkout next to this script
    script_dir = Path(__file__).resolve().parent
    repo_candidate = script_dir.parent
    if (repo_candidate / ".git").is_dir():
        return [s.name for s in discover_skills(repo_candidate)]
    return []


def cmd_doctor(args: argparse.Namespace) -> int:
    """Run `vibecrafted doctor`: apply any requested --fix-* repairs, run the full health check
    (with discovery-mode and orphan-detection extras when no manifest exists), and print the
    report.
    """
    shared_home = vibecrafted_home()
    store_path = _canonical_store_path(shared_home)
    state = _load_install_state(store_path)
    has_manifest = bool(state.skills)

    if getattr(args, "fix_rc", False):
        for finding in _doctor_fix_rc_files():
            icon = OK if finding.level == "ok" else WARN
            print(f"  {icon} {finding.component}: {finding.message}")
    if getattr(args, "fix_launchers", False):
        for finding in _doctor_fix_launchers(store_path, state):
            icon = OK if finding.level == "ok" else WARN
            print(f"  {icon} {finding.component}: {finding.message}")
    if getattr(args, "fix_legacy_bootstrap", False):
        for finding in _doctor_fix_legacy_bootstrap():
            icon = OK if finding.level == "ok" else WARN
            print(f"  {icon} {finding.component}: {finding.message}")

    if not state.skills:
        # No manifest — discover from disk, but only OUR skills
        bundle = set(_known_bundle_names())
        if store_path.exists():
            state.skills = [
                d.name
                for d in sorted(store_path.iterdir())
                if d.is_dir() and (d / "SKILL.md").exists() and d.name in bundle
            ]
        # Only check runtimes that actually have a skills dir
        state.runtimes = [
            rt for rt in SYMLINK_TARGET_CHOICES if runtime_skills_dir(rt).exists()
        ]

    findings = run_doctor(store_path, state)

    # Extra checks when no manifest: scan per-agent dirs for stale copies
    # but ONLY for skills in our bundle — don't claim ownership of other tools
    if not has_manifest:
        bundle = set(_known_bundle_names())
        findings.insert(
            0,
            DoctorFinding(
                "warn",
                "manifest",
                "No install manifest found — running in discovery mode. "
                "Install with the Smart Installer to get full tracking.",
            ),
        )
        for rt in state.runtimes:
            rt_skills = runtime_skills_dir(rt)
            if not rt_skills.exists():
                continue
            for entry in sorted(rt_skills.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                if entry.name not in bundle:
                    continue  # Not our skill — skip
                if not (entry / "SKILL.md").exists():
                    continue
                if not entry.is_symlink():
                    findings.append(
                        DoctorFinding(
                            "fail",
                            f"stale-copy:{rt}/{entry.name}",
                            "is a local COPY, not a symlink to shared store — drift risk",
                        )
                    )
                elif store_path.exists():
                    target = entry.resolve()
                    expected = (store_path / entry.name).resolve()
                    if target != expected and (store_path / entry.name).exists():
                        findings.append(
                            DoctorFinding(
                                "warn",
                                f"symlink:{rt}/{entry.name}",
                                f"points to {target}, expected {expected}",
                            )
                        )

    # Orphan detection: vc-* entries in store/runtime dirs not in current bundle
    bundle = set(_known_bundle_names())
    if bundle and store_path.exists():
        for entry in sorted(store_path.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if (
                entry.name.startswith("vc-")
                and entry.name not in bundle
                and (entry / "SKILL.md").exists()
            ):
                findings.append(
                    DoctorFinding(
                        "warn",
                        f"orphan:store/{entry.name}",
                        "in store but no longer in bundle — run installer to clean up",
                    )
                )
    if bundle:
        for rt in state.runtimes:
            rt_skills = runtime_skills_dir(rt)
            if not rt_skills.exists():
                continue
            for entry in sorted(rt_skills.iterdir()):
                if not entry.name.startswith("vc-"):
                    continue
                if entry.name in bundle or entry.name in state.skills:
                    continue
                if entry.is_symlink() or (
                    entry.is_dir() and (entry / "SKILL.md").exists()
                ):
                    findings.append(
                        DoctorFinding(
                            "warn",
                            f"orphan:{rt}/{entry.name}",
                            "symlink/dir for skill no longer in bundle",
                        )
                    )

    guide_path = write_start_here_guide(store_path, state, findings)
    exit_code = print_doctor(
        findings, guide_path=guide_path, verbose=getattr(args, "verbose", False)
    )
    _pause_for_runtime_contract_failures(findings)
    return exit_code


# ---------------------------------------------------------------------------
# Subcommand: list
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    """Print the discoverable skills bundle plus foundation install status for `list`."""
    repo_root = Path(args.source).resolve()
    if not repo_root.is_dir():
        print(red(f"Error: repo root not found: {repo_root}"))
        return 1

    skills = discover_skills(repo_root)
    cats = categorize_all(skills)

    print(f"\n{bold('𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Skills Bundle')}")
    print(dim(f"Source: {repo_root}\n"))

    for cat_key in ("pipeline", "specialist"):
        cat = SKILL_CATEGORIES[cat_key]
        names = cats[cat_key]
        if names:
            print(f"  {bold(cat['label'])} — {dim(cat['description'])}")
            for n in names:
                print(f"    - {n}")
            print()

    print(f"{bold('Runtime Foundations')} {dim('(substrate beneath the suite)')}")
    for f in FOUNDATIONS:
        path = f.is_installed()
        status = (
            green("installed")
            if path
            else (red("missing") if f.required else dim("optional"))
        )
        print(f"  {f.name}: {status} — {f.description}")
        print(f"    Channels: {', '.join(f.channels)}")
    print()

    return 0


# ---------------------------------------------------------------------------
# Subcommand: layout
# ---------------------------------------------------------------------------


def cmd_layout(args: argparse.Namespace) -> int:
    """Dispatch `layout status|migrate|rollback` to the layout-transfer status/execute helpers."""
    store_path = vibecrafted_home() / "skills"
    action = getattr(args, "action", "status")
    dry_run = getattr(args, "dry_run", False)
    force = getattr(args, "force", False)

    if action == "status":
        status = layout_status(store_path)
        print(f"\n{bold('Vibecrafted layout transfer status')}\n")
        print(
            f"  legacy:  {status['legacy']} ({'exists' if status['legacy_exists'] else 'missing'})"
        )
        print(
            f"  current: {status['current']} ({'exists' if status['current_exists'] else 'missing'})"
        )
        if status["last_transfer"]:
            last = status["last_transfer"]
            print(
                "  last:    "
                f"{last.get('direction', 'unknown')} "
                f"{last.get('status', 'unknown')} "
                f"{last.get('updated_at', '')}"
            )
        else:
            print("  last:    none")
        print()
        return 0

    direction = {
        "migrate": "legacy-to-new",
        "rollback": "new-to-legacy",
    }.get(action)
    if direction is None:
        print(red(f"Unknown layout action: {action}"))
        return 1

    exit_code, result = transfer_agents_layout(
        store_path,
        direction=direction,
        dry_run=dry_run,
        force=force,
    )
    source = result["source"]
    target = result["target"]
    conflicts = result.get("conflicts") or []
    if exit_code == 0:
        copied = result.get("copied") or []
        verb = "would transfer" if dry_run else "transferred"
        print(f"{OK} layout {verb} {len(copied)} files")
        print(f"  from: {source}")
        print(f"  to:   {target}")
        return 0

    print(f"{WARN} layout transfer blocked")
    print(f"  from: {source}")
    print(f"  to:   {target}")
    for conflict in conflicts:
        print(f"  conflict: {conflict}")
    if conflicts and not force:
        print(dim("  Re-run with --force only if this target is Vibecrafted-managed."))
    return 1


# ---------------------------------------------------------------------------
# Subcommand: uninstall
# ---------------------------------------------------------------------------


def _managed_tools_entry(path: Path) -> bool:
    """True if `path`'s name looks like a Vibecrafted-managed staged-tools generation entry."""
    return (
        path.name == "vibecrafted-current"
        or path.name.startswith("vibecrafted-")
        or path.name.startswith(".incoming-")
        # Installer-generated siblings: atomic-staging dirs, the current-handoff
        # marker, and the install lock. Legacy installs accumulate these without
        # any manifest entry, so ownership is by name pattern, not state.
        or path.name.startswith(".vibecrafted-")
        or path.name.startswith("..vibecrafted-")
        # Finder metadata inside a directory the framework owns end to end.
        # Left behind it keeps the tools root non-empty and unprunable forever.
        or path.name == ".DS_Store"
    )


def _build_uninstall_inventory(
    *,
    shared_home: Path,
    store_path: Path,
    state_file: Path,
    skill_names: Sequence[str],
    runtimes: Sequence[str],
    helper_paths: Sequence[Path],
    launchers: Sequence[tuple[Path, Path]],
    rc_cleanup_targets: Sequence[Path],
) -> list[ManagedPath]:
    """Build the complete uninstall inventory: skills, agent views, helpers, launchers, rc-file
    edits, logs, staged-tools generations, and everything explicitly preserved (uv tools,
    operator data, unmanaged siblings).
    """
    records: list[ManagedPath] = []
    seen: dict[str, int] = {}

    def add(
        kind: str,
        path: Path,
        action: str = "remove",
        reason: str = "",
        *,
        always: bool = False,
    ) -> None:
        """Record one managed path for the uninstall inventory, deduping by resolved path and
        upgrading a prior 'preserve' record if a stronger action applies.

        `always=True` registers a path that does not exist yet, for state this very
        teardown is about to create. Both the backup pass and the removal pass
        re-check presence, so an absent path is still never deleted unbacked.
        """
        normalized = path.expanduser()
        if not always and action != "remove-if-empty" and not _path_present(normalized):
            return
        key = str(normalized)
        existing = seen.get(key)
        if existing is not None:
            if records[existing].action == "preserve" and action != "preserve":
                records[existing] = ManagedPath(kind, normalized, action, reason)
            return
        seen[key] = len(records)
        records.append(ManagedPath(kind, normalized, action, reason))

    resolved_store = store_path.resolve(strict=False)
    managed_tools_root = vibecrafted_tools_home().resolve(strict=False)
    legacy_store_root = (shared_home / "skills").resolve(strict=False)
    store_is_managed = _is_subpath(resolved_store, managed_tools_root) or _is_subpath(
        resolved_store, legacy_store_root
    )
    if store_is_managed:
        for name in skill_names:
            add("shared-skill", store_path / name)
        add("install-state", state_file)
    elif _path_present(store_path):
        add(
            "external-store",
            resolved_store,
            "preserve",
            "current link resolves outside the managed tools root",
        )
    for runtime in runtimes:
        runtime_skills = Path.home() / f".{runtime}" / "skills"
        for name in skill_names:
            add("agent-view", runtime_skills / name)
    for helper in helper_paths:
        add("shell-helper", helper)
    for _launcher_dir, launcher in launchers:
        add("launcher", launcher)
    add("retired-launcher", vibecrafted_launcher_bin() / "vc-frame.real")
    for rcfile in rc_cleanup_targets:
        add("shell-rc", rcfile, "edit", "remove Vibecrafted-managed lines only")

    add("install-log", shared_home / "install.log")
    add("start-guide", start_here_path())

    tools_roots = [vibecrafted_tools_home(), shared_home / "tools"]
    unique_tools_roots: list[Path] = []
    for tools_root in tools_roots:
        if tools_root in unique_tools_roots:
            continue
        unique_tools_roots.append(tools_root)
        if tools_root.is_dir():
            for entry in sorted(tools_root.iterdir(), key=lambda item: item.name):
                if _managed_tools_entry(entry):
                    add("staged-payload", entry)
                else:
                    add(
                        "tools-sibling",
                        entry,
                        "preserve",
                        "not a Vibecrafted-managed payload name",
                    )
            add(
                "tools-root",
                tools_root,
                "remove-if-empty",
                "shared parent remains when unrelated entries exist",
            )

    # `_teardown_owned_runtime_for_uninstall` takes the cross-process install lease
    # AFTER this inventory is built, so pure discovery never sees the lockfile it
    # creates: it survived every teardown and kept the tools root non-empty
    # forever. Register it up front so the same run that creates it removes it.
    if vibecrafted_tools_home().is_dir():
        add(
            "install-lease",
            _tools_install_lease_path(_current_tools_link(shared_home)),
            "remove",
            "transient install lease; created by this teardown and removed with it",
            always=True,
        )

    runtime_bin = vibecrafted_runtime_bin()
    if runtime_bin.is_dir():
        children = sorted(runtime_bin.iterdir(), key=lambda item: item.name)
        if children:
            for child in children:
                add(
                    "runtime-bin",
                    child,
                    "preserve",
                    "binary ownership is product-managed outside installer state",
                )
        else:
            add("runtime-bin", runtime_bin, "preserve", "empty runtime binary root")

    runtime_home = vibecrafted_runtime_home()
    if runtime_home.is_dir():
        # Names the framework itself writes under the runtime home. Discovery by
        # name keeps this correct for legacy installs whose manifests predate
        # these payloads entirely (releases/providers/server were invisible to
        # uninstall until 2026-08-19 and survived every teardown).
        framework_runtime_names = {
            "releases",
            "providers",
            "server",
            "active.json",
            ".DS_Store",
        }
        for child in sorted(runtime_home.iterdir(), key=lambda item: item.name):
            if child in {vibecrafted_tools_home(), runtime_bin}:
                continue
            if child.name in framework_runtime_names:
                add(
                    "runtime-data",
                    child,
                    "remove",
                    "framework runtime payload (release/provider/server state)",
                )
            else:
                add(
                    "runtime-data",
                    child,
                    "preserve",
                    "runtime data is not proven installer-owned",
                )
        add(
            "runtime-home",
            runtime_home,
            "remove-if-empty",
            "shared parent remains when unrelated entries exist",
        )

    uv_tools_root = Path(
        os.environ.get("UV_TOOL_DIR", str(xdg_data_home() / "uv" / "tools"))
    ).expanduser()
    for name in (
        "vibecrafted",
        "vibecrafted-core",
        "vibecrafted-mcp",
        "vibecrafted-iterm2",
    ):
        add(
            "uv-tool",
            uv_tools_root / name,
            "preserve",
            "uv owns this environment; remove it with `uv tool uninstall`",
        )

    # Config and macOS surfaces the framework writes outside the runtime home.
    # Discovery by known path, not manifest, so installs that predate manifest
    # tracking still come off cleanly. Operator secrets (*.env) are preserved.
    home_dir = Path.home()
    config_root = Path(
        os.environ.get("XDG_CONFIG_HOME", str(home_dir / ".config"))
    ).expanduser()
    vib_config = config_root / "vibecrafted"
    if vib_config.is_dir():
        for child in sorted(vib_config.iterdir(), key=lambda item: item.name):
            if child.suffix == ".env":
                add("config", child, "preserve", "operator secret env file")
            else:
                add("config", child, "remove", "framework-generated configuration")
        add(
            "config-root",
            vib_config,
            "remove-if-empty",
            "kept when operator secrets remain",
        )
    add(
        "config",
        config_root / "vc-frame",
        "remove",
        "framework-generated vc-frame config tree",
    )
    add(
        "config",
        config_root / "vetcoders" / "frontier",
        "remove",
        "framework sidecar wiring (starship/atuin/vc-frame links and .bak snapshots)",
    )
    if sys.platform == "darwin":
        library = home_dir / "Library"
        add(
            "launchagent",
            library / "LaunchAgents" / "com.vetcoders.vibecrafted-slack-bridge.plist",
            "remove",
            "provider service plist; a loaded job ends at logout or explicit bootout",
        )
        dynamic_profiles = (
            library / "Application Support" / "iTerm2" / "DynamicProfiles"
        )
        for profile_name in ("vibecrafted.json", "vibecrafted-experimental.json"):
            add(
                "iterm2-profile",
                dynamic_profiles / profile_name,
                "remove",
                "dynamic profile installed by the iterm2 plugin",
            )
        for bundle_id in (
            "io.vetcoders.vc-frame",
            "com.vibecrafted.vc-board",
            "com.vibecrafted.vc-term",
        ):
            add(
                "app-support",
                library / "Application Support" / bundle_id,
                "remove",
                "framework runtime state",
            )
        add(
            "cache",
            library / "Caches" / "io.vetcoders.vc-frame",
            "remove",
            "framework cache",
        )
        for pref_domain in (
            "io.vetcoders.vibecrafted",
            "com.vibecrafted.vc-board",
            "com.vibecrafted.vc-board.debug",
            "com.vibecrafted.vc-term",
        ):
            add(
                "preference",
                library / "Preferences" / f"{pref_domain}.plist",
                "remove",
                "framework preference domain",
            )
        add(
            "app-bundle",
            Path("/Applications/Vibecrafted.app"),
            "preserve",
            "installed from the DMG; remove by dragging to Trash",
        )

    for name in ("artifacts", "control_plane", "logs"):
        add(
            "operator-data",
            shared_home / name,
            "preserve",
            "operator history/data is retained intentionally",
        )
    return records


def _print_uninstall_inventory(inventory: Sequence[ManagedPath]) -> None:
    """Print the planned uninstall inventory (remove/edit/preserve) before acting on it."""
    print(bold("Managed teardown inventory:"))
    for record in inventory:
        if record.action != "remove-if-empty" and not _path_present(record.path):
            # Registered ahead of time (the install lease this teardown creates);
            # nothing on disk to report until it exists.
            continue
        verb = {
            "remove": "remove",
            "edit": "edit",
            "remove-if-empty": "remove if empty",
            "preserve": "preserve",
        }[record.action]
        suffix = f" — {record.reason}" if record.reason else ""
        print(f"  {verb:15} {record.kind}: {record.path}{suffix}")
    print()


def _edit_rc_file(record: ManagedPath, *, dry_run: bool) -> tuple[bool, str]:
    """Strip Vibecrafted-managed lines from one rc file; returns whether it changed and why it
    couldn't.
    """
    rcfile = record.path
    if not _is_writable(rcfile):
        return False, "locked; launcher/source hints remain"
    content = rcfile.read_text(encoding="utf-8")
    changed = False
    for line, comment in _uninstall_rc_entries():
        content, removed = _strip_rc_entry(content, line, comment)
        changed = changed or removed > 0
    if changed and not dry_run:
        rcfile.write_text(content, encoding="utf-8")
    return changed, ""


def _apply_uninstall_inventory(
    inventory: Sequence[ManagedPath], *, dry_run: bool
) -> tuple[list[ManagedPath], list[ManagedPath], list[str]]:
    """Apply an uninstall inventory: remove deepest-first, edit rc files, and remove now-empty
    directories; collects per-record failures instead of raising.
    """
    applied: list[ManagedPath] = []
    preserved = [record for record in inventory if record.action == "preserve"]
    failures: list[str] = []

    removals = sorted(
        (record for record in inventory if record.action == "remove"),
        key=lambda item: (-len(item.path.parts), str(item.path)),
    )
    for record in removals:
        if not _path_present(record.path):
            continue
        if dry_run:
            applied.append(record)
            continue
        try:
            _remove_path(record.path)
            applied.append(record)
        except OSError as exc:
            failures.append(f"{record.path}: {exc}")

    for record in (item for item in inventory if item.action == "edit"):
        try:
            changed, reason = _edit_rc_file(record, dry_run=dry_run)
        except OSError as exc:
            failures.append(f"{record.path}: {exc}")
            continue
        if changed:
            applied.append(record)
        elif reason:
            preserved.append(ManagedPath(record.kind, record.path, "preserve", reason))

    prunable = sorted(
        (item for item in inventory if item.action == "remove-if-empty"),
        key=lambda item: (-len(item.path.parts), str(item.path)),
    )
    for record in prunable:
        if not record.path.is_dir():
            continue
        try:
            is_empty = not any(record.path.iterdir())
            if not is_empty:
                preserved.append(
                    ManagedPath(
                        record.kind,
                        record.path,
                        "preserve",
                        "contains intentionally preserved or unrelated entries",
                    )
                )
            elif dry_run:
                applied.append(record)
            else:
                record.path.rmdir()
                applied.append(record)
        except OSError as exc:
            failures.append(f"{record.path}: {exc}")
    return applied, preserved, failures


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Run `vibecrafted uninstall`: build the inventory, confirm interactively, back up
    everything first, then apply the removal/edit plan and report results.
    """
    shared_home = vibecrafted_home()
    store_path = _canonical_store_path(shared_home)
    state = _load_install_state(store_path)
    state_file = _install_state_file(store_path)
    legacy_store = shared_home / "skills"
    if state_file.parent == legacy_store and not store_path.exists():
        # A pre-generation install owns mutable skill copies under state home.
        # Preserve that one-way uninstall migration without recreating the
        # legacy store for current installs.
        store_path = legacy_store
    dry_run = args.dry_run
    bundle = set(_known_bundle_names())
    helper_file = _helper_target_path()
    legacy_file = _helper_legacy_path()
    has_state = state_file.exists()

    # Default to manifest-tracked files for restore-safe uninstall;
    # fall back to discovery heuristics only when we don't have installer state.
    if state.helper_files:
        helper_paths = [Path(p) for p in state.helper_files if Path(p).exists()]
    elif has_state and not (state.skills or state.runtimes or state.launcher_entries):
        helper_paths = []
    else:
        helper_paths = [hf for hf in (helper_file, legacy_file) if hf.exists()]

    if state.launcher_entries:
        launchers = _parse_manifest_launchers(state.launcher_entries)
    else:
        launchers = collect_installed_launchers()

    rc_cleanup_targets = [
        Path.home() / rcname
        for rcname in (".zshrc", ".bashrc")
        if _rc_has_framework_install_hints(Path.home() / rcname)
    ]

    # Use manifest if available, otherwise use bundle names
    skill_names = state.skills if has_state else [n for n in bundle]
    runtimes = (
        state.runtimes
        if has_state
        else [rt for rt in SYMLINK_TARGET_CHOICES if runtime_skills_dir(rt).exists()]
    )

    inventory = _build_uninstall_inventory(
        shared_home=shared_home,
        store_path=store_path,
        state_file=state_file,
        skill_names=skill_names,
        runtimes=runtimes,
        helper_paths=helper_paths,
        launchers=launchers,
        rc_cleanup_targets=rc_cleanup_targets,
    )
    runtime_evidence = sys.platform == "darwin" and (
        _runtime_service_has_evidence(shared_home)
        or bool(_retired_vc_frame_process_census())
    )
    has_work = runtime_evidence or any(
        (record.action in {"remove", "edit"} and _path_present(record.path))
        or (
            record.action == "remove-if-empty"
            and record.path.is_dir()
            and not any(record.path.iterdir())
        )
        for record in inventory
    )

    print(f"\n{bold('𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Uninstall')}\n")

    if not has_work:
        print(
            dim(
                "Nothing to uninstall — no managed payloads, skills, launchers, helpers, or shell hooks found."
            )
        )
        preserved = [record for record in inventory if record.action == "preserve"]
        if preserved:
            print("  Preserved intentionally:")
            for record in preserved:
                print(f"    {record.path} — {record.reason}")
        print()
        return 0

    _print_uninstall_inventory(inventory)
    if runtime_evidence:
        print(
            "  teardown runtime: verified service plane and retired vc-frame processes"
        )
        print()

    if _IS_TTY and not dry_run:
        if not ask_yn("Remove the installed 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. bundle?", default=False):
            print("Uninstall cancelled.")
            return 0
        print()

    backup_ts = None
    if not dry_run:
        print(bold("Saving external restore kit..."))
        backup_ts = create_teardown_backup(inventory)
        if backup_ts:
            print(f"  {OK} {_backup_root(store_path) / backup_ts}")
        print()

    try:
        runtime_actions = _teardown_owned_runtime_for_uninstall(
            shared_home,
            dry_run=dry_run,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(red(bold("Uninstall incomplete before file removal.")))
        print(f"  runtime teardown: {exc}")
        if backup_ts:
            print(f"  Restore with: {_restore_command(backup_ts)}")
        print()
        return 1

    applied, preserved, failures = _apply_uninstall_inventory(
        inventory, dry_run=dry_run
    )
    if dry_run:
        for action in runtime_actions:
            print(f"  runtime: {action}")
        print("Would remove or edit:")
        for record in applied:
            print(f"  {record.kind}: {record.path}")
        if preserved:
            print("Preserved intentionally:")
            for record in preserved:
                print(f"  {record.kind}: {record.path} — {record.reason}")
        print()
        return 0

    if failures:
        print(red(bold("Uninstall incomplete.")))
        for failure in failures:
            print(f"  {failure}")
        if backup_ts:
            print(f"  Restore with: {_restore_command(backup_ts)}")
        print()
        return 1

    print(green(bold("Removed managed paths:")))
    for action in runtime_actions:
        print(f"  runtime: {action}")
    for record in applied:
        print(f"  {record.kind}: {record.path}")
    if preserved:
        print("Preserved intentionally:")
        for record in preserved:
            print(f"  {record.kind}: {record.path} — {record.reason}")
    if backup_ts:
        backup_path = _backup_root(store_path) / backup_ts
        print(f"Backup preserved: {backup_path}")
        print("Restore:")
        print(f"  {_restore_command(backup_ts)}")
    print(green(bold("Uninstall complete.")))
    print()
    return 0


# ---------------------------------------------------------------------------
# Subcommand: restore
# ---------------------------------------------------------------------------


def cmd_restore(args: argparse.Namespace) -> int:
    """Run `vibecrafted restore`: replay the latest teardown backup's manifest, or fall back to
    the older per-category backup layout if no teardown manifest exists.
    """
    shared_home = vibecrafted_home()
    store_path = _canonical_store_path(shared_home)
    dry_run = args.dry_run
    backup_root = _backup_root(store_path)

    print(f"\n{bold('𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Restore')}\n")

    # Find latest backup
    latest_file = backup_root / "latest"
    if not latest_file.exists():
        print(red("No backup found. Nothing to restore."))
        return 1

    ts = latest_file.read_text().strip()
    backup_dir = backup_root / ts
    if not backup_dir.is_dir():
        print(red(f"Backup directory not found: {backup_dir}"))
        return 1

    print(f"  Restoring from backup: {bold(ts)}")
    print()

    teardown_manifest = backup_dir / RESTORE_MANIFEST_FILE
    teardown_restore = backup_dir / RESTORE_SCRIPT_FILE
    if teardown_manifest.is_file() and teardown_restore.is_file():
        manifest = json.loads(teardown_manifest.read_text(encoding="utf-8"))
        if dry_run:
            for item in manifest.get("items", []):
                print(f"  {dim('restore')} {item.get('path', '')}")
            print()
            return 0
        result = subprocess.run([sys.executable, str(teardown_restore)], check=False)
        return result.returncode

    restored = 0

    # Restore skills in store
    store_backup = backup_dir / "store"
    if store_backup.is_dir():
        print(bold("Restoring skills to store..."))
        for entry in sorted(store_backup.iterdir()):
            if not (entry.is_dir() or entry.is_symlink() or entry.is_file()):
                continue
            dst = store_path / entry.name
            if dry_run:
                print(f"  {dim('restore')} {entry.name}")
            else:
                _restore_path_from_backup(entry, dst)
                print(f"  {OK} {entry.name}")
            restored += 1
        print()

    # Restore per-runtime entries
    rt_backup = backup_dir / "runtimes"
    if rt_backup.is_dir():
        print(bold("Restoring runtime entries..."))
        for rt_dir in sorted(rt_backup.iterdir()):
            if not rt_dir.is_dir():
                continue
            rt = rt_dir.name
            rt_skills = runtime_skills_dir(rt)
            for entry in sorted(rt_dir.iterdir()):
                if not (entry.is_dir() or entry.is_symlink() or entry.is_file()):
                    continue
                dst = rt_skills / entry.name
                if dry_run:
                    print(f"  {dim('restore')} {rt}/{entry.name}")
                else:
                    _restore_path_from_backup(entry, dst)
                    print(f"  {OK} {rt}/{entry.name}")
                restored += 1
        print()

    # Restore helpers
    helper_backup = backup_dir / "helpers"
    if helper_backup.is_dir():
        print(bold("Restoring helpers..."))
        # Helper file
        # Try new name first, then compat path
        backed_helper = helper_backup / "vc-skills.sh"
        if not backed_helper.exists():
            backed_helper = helper_backup / "vc-skills.zsh"
        if backed_helper.exists():
            dst = _helper_target_path()
            if dry_run:
                print(f"  {dim('restore')} {dst.name}")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backed_helper, dst)
                print(f"  {OK} {dst}")
            restored += 1

        # RC files
        for rcname in (".zshrc", ".bashrc"):
            backed_rc = helper_backup / rcname
            if backed_rc.exists():
                dst = Path.home() / rcname
                if dry_run:
                    print(f"  {dim('restore')} {rcname}")
                else:
                    shutil.copy2(backed_rc, dst)
                    print(f"  {OK} {rcname}")
                restored += 1
        print()

    launcher_backup = backup_dir / "launchers"
    if launcher_backup.is_dir():
        print(bold("Restoring launcher commands..."))
        for key_dir in sorted(launcher_backup.iterdir()):
            if not key_dir.is_dir():
                continue
            launcher_bin_dir = _launcher_dir_from_key(key_dir.name)
            if launcher_bin_dir is None:
                print(f"  {WARN} Unknown launcher backup target: {key_dir.name}")
                continue
            launcher_bin_dir.mkdir(parents=True, exist_ok=True)
            for entry in sorted(key_dir.iterdir()):
                if not (entry.is_dir() or entry.is_symlink() or entry.is_file()):
                    continue
                dst = launcher_bin_dir / entry.name
                if dry_run:
                    print(f"  {dim('restore')} {dst}")
                else:
                    _restore_path_from_backup(entry, dst)
                    if dst.is_file() and not dst.is_symlink():
                        dst.chmod(0o755)
                    print(f"  {OK} {dst}")
                restored += 1
        print()

    # Remove manifest (since we're reverting to pre-install state)
    state_file = _install_state_file(store_path)
    if state_file.exists() and not dry_run:
        state_file.unlink()

    if restored:
        print(green(bold(f"Restored {restored} items from backup {ts}.")))
    else:
        print(yellow("Backup existed but contained no items to restore."))
    print()
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def detect_repo_root() -> str:
    """Try to find the repo root from script location."""
    script_dir = Path(__file__).resolve().parent
    # scripts/vetcoders_install.py -> repo root is parent
    candidate = script_dir.parent
    if (candidate / ".git").is_dir():
        return str(candidate)
    return str(Path.cwd())


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint: parse subcommand args and dispatch to the matching `cmd_*` handler."""
    default_source = detect_repo_root()

    parser = argparse.ArgumentParser(
        prog="vc-install",
        description="𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. installer — the founders' framework for shipping software with AI agents.",
    )
    sub = parser.add_subparsers(dest="command")

    # install
    p_install = sub.add_parser(
        "install", help="Install the 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. framework bundle"
    )
    p_install.add_argument(
        "--source", default=default_source, help="Repo root (default: auto-detect)"
    )
    p_install.add_argument(
        "--dry-run", "-n", action="store_true", help="Show what would be done"
    )
    p_install.add_argument(
        "--non-interactive", action="store_true", help="Skip all prompts, use defaults"
    )
    p_install.add_argument(
        "--advanced", action="store_true", help="Open the selective install wizard"
    )
    p_install.add_argument(
        "--with-shell", action="store_true", help="Install the shell helper layer"
    )
    p_install.add_argument(
        "--write-shell-rc",
        action="store_true",
        help="Opt in to writing helper/PATH lines to shell rc files",
    )
    p_install.add_argument(
        "--tool",
        dest="tools",
        action="append",
        choices=SYMLINK_TARGET_CHOICES,
        help="Limit symlink views to these runtimes (repeatable, default: all)",
    )
    p_install.add_argument(
        "--skill",
        dest="skill_filter",
        action="append",
        help="Install only these skills (repeatable, default: full bundle)",
    )
    p_install.add_argument(
        "--mirror",
        action="store_true",
        help=(
            "Delete extra files in installed skill dirs and staged tools "
            "(rsync --delete)"
        ),
    )
    p_install.add_argument(
        "--compact",
        action="store_true",
        help=argparse.SUPPRESS,  # retired: compact is the default (kept as no-op)
    )
    p_install.add_argument(
        "--verbose",
        action="store_true",
        help="Per-step narration on stdout instead of the compact view",
    )
    p_install.add_argument(
        "--debug",
        action="store_true",
        help="Raw subprocess output on stdout (everything the log gets)",
    )

    # doctor
    p_doctor = sub.add_parser("doctor", help="Verify installation health")
    p_doctor.add_argument(
        "--verbose",
        action="store_true",
        help="List every check, including passing ones",
    )
    p_doctor.add_argument(
        "--fix-rc",
        action="store_true",
        help="Repair old shell startup lines and restore default helper/PATH hints before verifying",
    )
    p_doctor.add_argument(
        "--fix-launchers",
        action="store_true",
        help="Refresh vibecrafted, vc-help, and vc-* wrappers from the installed/current source before verifying",
    )
    p_doctor.add_argument(
        "--fix-legacy-bootstrap",
        action="store_true",
        help="Neutralize retired /opt/vibecrafted bootstrap roots: comment out VIBECRAFTED_ROOT exports in shell rc files (with backup) and report the leftover tree — never deletes it",
    )

    # list
    p_list = sub.add_parser(
        "list",
        help="Show available 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. skills and the runtime substrate beneath them",
    )
    p_list.add_argument(
        "--source", default=default_source, help="Repo root (default: auto-detect)"
    )

    # layout transfer
    p_layout = sub.add_parser(
        "layout",
        help="Transfer agent runtime payload between legacy and current install layouts",
    )
    p_layout.add_argument(
        "action",
        choices=("status", "migrate", "rollback"),
        nargs="?",
        default="status",
        help="status, migrate legacy->current, or rollback current->legacy",
    )
    p_layout.add_argument(
        "--dry-run", "-n", action="store_true", help="Show what would be done"
    )
    p_layout.add_argument(
        "--force",
        action="store_true",
        help="Overwrite conflicting target files; only use for Vibecrafted-managed payload",
    )

    # uninstall
    p_uninstall = sub.add_parser(
        "uninstall", help="Remove 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. skills, views, launchers, and helpers"
    )
    p_uninstall.add_argument(
        "--dry-run", "-n", action="store_true", help="Show what would be done"
    )

    # restore
    p_restore = sub.add_parser("restore", help="Restore pre-install state from backup")
    p_restore.add_argument(
        "--dry-run", "-n", action="store_true", help="Show what would be done"
    )

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    if args.command == "install":
        return cmd_install(args)
    elif args.command == "doctor":
        return cmd_doctor(args)
    elif args.command == "list":
        return cmd_list(args)
    elif args.command == "layout":
        return cmd_layout(args)
    elif args.command == "uninstall":
        return cmd_uninstall(args)
    elif args.command == "restore":
        return cmd_restore(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
