#!/usr/bin/env python3
"""Browser-based guided installer: local HTTP control plane plus static HTML shell.

Runs a local ThreadingHTTPServer that serves either the pre-built Svelte
site bundle (when present) or an inline HTML fallback, exposes a JSON API
for preflight diagnostics/install/control-plane status, and drives the
actual install by spawning the same phased subprocess steps as the CLI
installer.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from textwrap import dedent
from typing import Any
from urllib.parse import urlparse

# vibecrafted_core is the only owner of roots, control-plane state and
# workflow launch. This script runs from a checkout (`python3 scripts/installer_gui.py`)
# where the package is not installed, so the checkout's package dir is put on
# sys.path first; there is no second copy of any of these names anywhere.
_CORE_SRC = Path(__file__).resolve().parents[1] / "vibecrafted-core"
if _CORE_SRC.is_dir() and str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

try:
    _installer_brand = importlib.import_module("installer_brand")
except ModuleNotFoundError:  # pragma: no cover - depends on entrypoint
    _installer_brand = importlib.import_module("scripts.installer_brand")

from vibecrafted_core.control_plane import sync_state
from vibecrafted_core.runtime_paths import (
    read_version_file,
    vibecrafted_home,
    vibecrafted_runtime_bin,
    vibecrafted_tools_home,
    xdg_config_home,
)
from vibecrafted_core.workflow import (
    launch_workflow,
    normalize_launch_spec,
)

PRODUCT_LINE = _installer_brand.PRODUCT_LINE
TAGLINE = _installer_brand.TAGLINE
VAPOR_HEADER = _installer_brand.VAPOR_HEADER


OUTPUT_TAIL_LIMIT = 120
CATEGORY_LABELS = {
    "frameworks": "Frameworks",
    "bundled": "Bundled toolchain",
    "foundations": "Foundations",
    "toolchains": "Toolchains",
    "agents": "Agents",
    "additional_tools": "Additional tools",
}
CATEGORY_ORDER = tuple(CATEGORY_LABELS)
FOUNDATION_COMMANDS = ("loctree-mcp", "aicx-mcp", "prview", "screenscribe")
BUNDLED_BIN_NAMES = ("aicx-mcp", "aicx", "loctree-mcp", "loctree", "loct", "prview")
TOOLCHAIN_COMMANDS = ("python3", "node", "git", "rsync")
AGENT_COMMANDS = ("claude", "codex", "agy", "junie", "grok", "cursor-agent")
ADDITIONAL_TOOL_COMMANDS = ("mise", "starship", "atuin", "zoxide")


def default_source_dir() -> str:
    """Default framework source directory: the repo root two levels above this file."""
    return str(Path(__file__).resolve().parent.parent)


def read_framework_version(source_dir: str) -> str:
    """Read the framework VERSION string for source_dir."""
    return read_version_file(source_dir)


def framework_store_dir() -> Path:
    """Directory where installed vc-* skill directories live."""
    return vibecrafted_home() / "skills"


def helper_layer_path() -> Path:
    """Path to the shell helper layer sourced by interactive shells."""
    return xdg_config_home() / "vetcoders" / "vc-skills.sh"


def install_log_path() -> Path:
    """Path to the persisted install log under the Vibecrafted home."""
    return vibecrafted_home() / "install.log"


def start_here_path() -> Path:
    """Path to the post-install START_HERE.md guide."""
    return vibecrafted_home() / "START_HERE.md"


def runtime_skill_views() -> dict[str, Path]:
    """Map each supported agent runtime to its per-user skills directory."""
    home = Path.home()
    return {
        "agents": home / ".agents" / "skills",
        "claude": home / ".claude" / "skills",
        "codex": home / ".codex" / "skills",
        "gemini": home / ".gemini" / "skills",
        "agy": home / ".agy" / "skills",
        "junie": home / ".junie" / "skills",
        "grok": home / ".grok" / "skills",
    }


def installer_script_path(source_dir: str) -> Path:
    """Path to the primary vetcoders_install.py entrypoint under source_dir."""
    return Path(source_dir).resolve() / "scripts" / "vetcoders_install.py"


def foundations_script_path(source_dir: str) -> Path:
    """Path to the optional foundations bootstrap script under source_dir."""
    return Path(source_dir).resolve() / "scripts" / "install-foundations.sh"


def runtime_script_path(source_dir: str) -> Path:
    """Path to the optional agent-runtime installer script under source_dir."""
    return Path(source_dir).resolve() / "scripts" / "install-runtime.sh"


def build_install_command(source_dir: str, *, with_shell: bool) -> list[str]:
    """Build the vetcoders_install.py invocation for a compact, non-interactive install."""
    installer_path = installer_script_path(source_dir)
    if not installer_path.exists():
        raise FileNotFoundError(f"Installer not found at {installer_path}")
    command = [
        sys.executable,
        str(installer_path),
        "install",
        "--source",
        str(Path(source_dir).resolve()),
        "--compact",
        "--non-interactive",
        "--mirror",
    ]
    if with_shell:
        command.append("--with-shell")
    return command


@dataclass(frozen=True)
class InstallStep:
    """One labeled subprocess command in the GUI install plan."""

    label: str
    command: list[str]


def _command_display(command: list[str]) -> str:
    """Render an argv list as a space-joined shell-style string for display."""
    return " ".join(command)


def _serialize_install_plan(steps: list[InstallStep]) -> list[dict[str, str]]:
    """Convert InstallStep objects to JSON-friendly {label, command} dicts."""
    return [
        {"label": step.label, "command": _command_display(step.command)}
        for step in steps
    ]


def build_install_steps(source_dir: str, *, with_shell: bool) -> list[InstallStep]:
    """Build the ordered install plan: optional foundations, core install, optional runtime."""
    steps: list[InstallStep] = []
    foundations_path = foundations_script_path(source_dir)
    if foundations_path.exists():
        steps.append(
            InstallStep(
                label="Bootstrap foundations",
                command=["bash", str(foundations_path)],
            )
        )

    steps.append(
        InstallStep(
            label="Install Vibecrafted",
            command=build_install_command(source_dir, with_shell=with_shell),
        )
    )
    runtime = os.environ.get("VIBECRAFTED_RUNTIME", "none").strip() or "none"
    runtime_path = runtime_script_path(source_dir)
    if runtime != "none" and runtime_path.exists():
        steps.append(
            InstallStep(
                label=f"Install runtime: {runtime}",
                command=["bash", str(runtime_path), "--runtime", runtime, "--yes"],
            )
        )
    return steps


def _command_check(name: str) -> dict[str, Any]:
    """Diagnostics entry: whether name resolves on PATH, and where."""
    path = shutil.which(name)
    return {
        "label": name,
        "found": bool(path),
        "detail": path or f"{name} not found on PATH",
        "kind": "command",
    }


def _path_check(
    label: str, path: Path, *, found: bool | None = None, detail: str | None = None
) -> dict[str, Any]:
    """Diagnostics entry for a filesystem path, with overridable found/detail."""
    is_found = path.exists() if found is None else found
    return {
        "label": label,
        "found": is_found,
        "detail": detail or str(path),
        "kind": "path",
    }


def _framework_checks() -> dict[str, dict[str, Any]]:
    """Diagnostics for the framework's own install: skills, helper file, binaries, symlinks."""
    store_dir = framework_store_dir()
    helper_file = helper_layer_path()
    skills = []
    if store_dir.is_dir():
        skills = sorted(
            child.name
            for child in store_dir.iterdir()
            if child.is_dir() and child.name.startswith("vc-")
        )

    binary_path = shutil.which("vibecraft") or shutil.which("vibecrafted")

    active_views = []
    for runtime, path in runtime_skill_views().items():
        if not path.is_dir():
            continue
        entries = [entry.name for entry in path.iterdir()]
        if entries:
            active_views.append(f"{runtime} ({len(entries)})")

    return {
        "workflows": _path_check(
            "workflows",
            store_dir,
            found=bool(skills),
            detail=f"{len(skills)} installed skill directories in {store_dir}"
            if skills
            else f"No installed skill directories in {store_dir}",
        ),
        "helpers": _path_check(
            "helpers",
            helper_file,
            detail=str(helper_file)
            if helper_file.exists()
            else f"Missing helper file at {helper_file}",
        ),
        "binaries": {
            "label": "binaries",
            "found": bool(binary_path),
            "detail": binary_path or "vibecraft/vibecrafted not found on PATH",
            "kind": "command",
        },
        "symlinks": {
            "label": "symlinks",
            "found": bool(active_views),
            "detail": ", ".join(active_views)
            if active_views
            else "No runtime skill views detected in $HOME/.agents, $HOME/.claude, $HOME/.codex, $HOME/.gemini, $HOME/.agy, $HOME/.junie, or $HOME/.grok",
            "kind": "path",
        },
    }


def _bundled_os() -> str:
    """Normalize sys.platform to the bundled-toolchain OS tag (macos/linux/windows)."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform.startswith(("win", "cygwin")):
        return "windows"
    return sys.platform


def _bundled_arch() -> str:
    """Normalize platform.machine() to the bundled-toolchain arch tag."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    if machine in ("aarch64", "arm64"):
        return "aarch64"
    if machine == "armv7l":
        return "armv7"
    return machine or "unknown"


def bundled_bin_root(source_dir: str) -> Path:
    """Resolve the bundled-toolchain bin directory: env override, per-arch, then generic."""
    override = os.environ.get("VIBECRAFTED_BUNDLED_BIN")
    if override:
        return Path(override).expanduser()
    per_arch = Path(source_dir) / "tools" / "bin" / f"{_bundled_os()}-{_bundled_arch()}"
    if per_arch.is_dir():
        return per_arch
    return Path(source_dir) / "tools" / "bin"


def _bundled_check(name: str, source_dir: str) -> dict[str, Any]:
    """Diagnostics entry for a bundled binary: present and executable under bundled_bin_root."""
    root = bundled_bin_root(source_dir)
    path = root / name
    if path.is_file() and os.access(path, os.X_OK):
        return {
            "label": name,
            "found": True,
            "detail": str(path),
            "kind": "bundled",
        }
    missing_detail = (
        f"{name} missing from bundled drop-in ({root})"
        if root.is_dir()
        else f"{name} - bundled drop-in directory not present ({root})"
    )
    return {
        "label": name,
        "found": False,
        "detail": missing_detail,
        "kind": "bundled",
    }


def run_diagnostics(
    source_dir: str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Check: frameworks, bundled toolchain, foundations, toolchains, agents, tools."""
    if source_dir is None:
        source_dir = default_source_dir()
    diagnostics: dict[str, dict[str, dict[str, Any]]] = {}
    diagnostics["frameworks"] = _framework_checks()
    diagnostics["bundled"] = {
        name: _bundled_check(name, source_dir) for name in BUNDLED_BIN_NAMES
    }
    diagnostics["foundations"] = {
        name: _command_check(name) for name in FOUNDATION_COMMANDS
    }
    diagnostics["toolchains"] = {
        name: _command_check(name) for name in TOOLCHAIN_COMMANDS
    }
    diagnostics["agents"] = {name: _command_check(name) for name in AGENT_COMMANDS}
    diagnostics["additional_tools"] = {
        name: _command_check(name) for name in ADDITIONAL_TOOL_COMMANDS
    }
    return diagnostics


def summarize_diagnostics(
    diagnostics: dict[str, dict[str, dict[str, Any]]],
) -> tuple[list[str], list[str], dict[str, list[str]]]:
    """Flatten run_diagnostics() output into (found labels, missing labels, missing-by-category)."""
    found_items: list[str] = []
    missing_items: list[str] = []
    needs_install: dict[str, list[str]] = {}

    for category in CATEGORY_ORDER:
        missing_in_category: list[str] = []
        for name, entry in diagnostics.get(category, {}).items():
            label = entry.get("label", name)
            flat_label = f"{CATEGORY_LABELS[category]}: {label}"
            if entry.get("found"):
                found_items.append(flat_label)
            else:
                missing_items.append(flat_label)
                missing_in_category.append(label)
        if missing_in_category:
            needs_install[category] = missing_in_category

    return found_items, missing_items, needs_install


def install_runtime_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return an env dict with vibecrafted/node/cargo/local bin dirs prepended to PATH."""
    env = dict(os.environ if base_env is None else base_env)
    path_entries = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []

    candidates = [
        vibecrafted_runtime_bin(),
        vibecrafted_tools_home() / "node" / "bin",
        Path.home() / ".cargo" / "bin",
        Path.home() / ".local" / "bin",
    ]
    for candidate in candidates:
        candidate_str = str(candidate)
        if candidate.is_dir() and candidate_str not in path_entries:
            path_entries.insert(0, candidate_str)

    env["PATH"] = os.pathsep.join(path_entries)
    return env


def _trim_home(value: str) -> str:
    """Replace a leading home-directory prefix with `~` for display."""
    home = str(Path.home())
    if value.startswith(home):
        return value.replace(home, "~", 1)
    return value


def _open_target(target: str) -> bool:
    """Open target (path or URL) with the platform opener, falling back to webbrowser."""
    if sys.platform == "darwin" and shutil.which("open"):
        subprocess.Popen(
            ["open", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    if sys.platform.startswith("linux") and shutil.which("xdg-open"):
        subprocess.Popen(
            ["xdg-open", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    return webbrowser.open(target)


@dataclass
class InstallRun:
    """Mutable snapshot of an in-progress or finished GUI install invocation."""

    command: list[str] = field(default_factory=list)
    plan: list[list[str]] = field(default_factory=list)
    plan_labels: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)
    with_shell: bool = True
    running: bool = False
    completed: bool = False
    exit_code: int | None = None
    error: str | None = None
    current_stage: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


class InstallController:
    """Owns diagnostics, the running install thread, and control-plane state for the GUI."""

    def __init__(self, source_dir: str, *, bundle_dir: str | None = None) -> None:
        """Resolve source_dir, run initial diagnostics, and locate the site bundle."""
        self.source_dir = str(Path(source_dir).resolve())
        self.version = read_framework_version(self.source_dir)
        self.diagnostics = run_diagnostics(self.source_dir)
        self.found_items, self.missing_items, self.needs_install = (
            summarize_diagnostics(self.diagnostics)
        )
        self._lock = threading.Lock()
        self._run = InstallRun()
        self.site_dist_dir = self._resolve_site_dist(bundle_dir)
        self.bundled_bin_dir = bundled_bin_root(self.source_dir)

    def _resolve_site_dist(self, explicit: str | None) -> Path | None:
        """Locate the Svelte site bundle shipped with the source tree.

        Resolution order:
        1. --bundle-dir flag (explicit)
        2. VIBECRAFTED_SITE_BUNDLE env var
        3. <source>/site/dist (authored layout)
        4. <source>/dist (release-tarball layout)
        5. sibling ../vibecrafted-io/site/dist
        6. sibling ../vc-runtime/vibecrafted-io/site/dist
        7. ~/.vibecrafted/vc-runtime/vibecrafted-io/site/dist
        8. ~/Libraxis/vc-runtime/vibecrafted-io/site/dist (legacy fallback)

        Returns None when no bundle is present; the request handler
        then falls back to the inline HTML.
        """
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit))
        env_bundle = os.environ.get("VIBECRAFTED_SITE_BUNDLE")
        if env_bundle:
            candidates.append(Path(env_bundle))
        root = Path(self.source_dir)
        candidates.extend(
            [
                root / "site" / "dist",
                root / "dist",
                root.parent / "vibecrafted-io" / "site" / "dist",
                root.parent / "vc-runtime" / "vibecrafted-io" / "site" / "dist",
            ]
        )
        if root.name == "vibecrafted" or (root / "VERSION").is_file():
            candidates.append(
                vibecrafted_home() / "vc-runtime" / "vibecrafted-io" / "site" / "dist"
            )
            # Legacy fallback: honor a pre-existing $HOME/Libraxis checkout.
            candidates.append(
                Path.home()
                / "Libraxis"
                / "vc-runtime"
                / "vibecrafted-io"
                / "site"
                / "dist"
            )

        for candidate in candidates:
            if (candidate / "en" / "install" / "index.html").is_file():
                return candidate.resolve()
        return None

    def _category_cards(self) -> list[dict[str, Any]]:
        """Build the per-category diagnostics summary cards used by the preflight payload."""
        cards: list[dict[str, Any]] = []
        for key, label in CATEGORY_LABELS.items():
            entries = []
            present = 0
            for name, entry in self.diagnostics.get(key, {}).items():
                found = bool(entry.get("found"))
                if found:
                    present += 1
                entries.append(
                    {
                        "name": name,
                        "label": entry.get("label", name),
                        "found": found,
                        "detail": entry.get("detail", ""),
                    }
                )
            cards.append(
                {
                    "key": key,
                    "label": label,
                    "present": present,
                    "total": len(entries),
                    "items": entries,
                }
            )
        return cards

    def preflight_payload(self) -> dict[str, Any]:
        """Assemble the full `/api/preflight` JSON payload: brand, diagnostics, plan, status."""
        try:
            install_plan = _serialize_install_plan(
                build_install_steps(self.source_dir, with_shell=True)
            )
        except FileNotFoundError:
            install_plan = []

        control_plane = self.control_plane_payload()

        return {
            "brand": {
                "header": VAPOR_HEADER,
                "tagline": TAGLINE,
                "product_line": PRODUCT_LINE,
            },
            "version": self.version,
            "source_dir": self.source_dir,
            "source_dir_display": _trim_home(self.source_dir),
            "guide_path": str(start_here_path()),
            "guide_path_display": _trim_home(str(start_here_path())),
            "helper_path": str(helper_layer_path()),
            "helper_path_display": _trim_home(str(helper_layer_path())),
            "bundled_bin_dir": str(self.bundled_bin_dir),
            "bundled_bin_display": _trim_home(str(self.bundled_bin_dir)),
            "bundled_bin_present": self.bundled_bin_dir.is_dir(),
            "found_count": len(self.found_items),
            "missing_count": len(self.missing_items),
            "found_items": self.found_items,
            "missing_items": self.missing_items,
            "needs_install": self.needs_install,
            "categories": self._category_cards(),
            "install_plan": install_plan,
            "control_plane": control_plane,
            "launcher_defaults": {
                "workflows": ["workflow", "research", "review", "marbles"],
                "agents": ["claude", "codex", "agy", "junie", "grok", "cursor"],
                "runtimes": ["headless", "terminal", "visible"],
            },
            "status": self.status_payload(),
        }

    def control_plane_payload(self) -> dict[str, Any]:
        """Sync and enrich control-plane state with helper/guide paths and skills-ready count."""
        snapshot = sync_state()
        skill_store = framework_store_dir()
        skills_ready = 0
        if skill_store.is_dir():
            skills_ready = sum(
                1
                for child in skill_store.iterdir()
                if child.is_dir() and child.name.startswith("vc-")
            )
        snapshot["helper_path"] = str(helper_layer_path())
        snapshot["guide_path"] = str(start_here_path())
        snapshot["skills_ready"] = skills_ready
        return snapshot

    def status_payload(self) -> dict[str, Any]:
        """Return a thread-safe snapshot of the current InstallRun for `/api/install/status`."""
        with self._lock:
            output_tail = self._run.output[-OUTPUT_TAIL_LIMIT:]
            plan = self._run.plan or ([self._run.command] if self._run.command else [])
            return {
                "command": self._run.command,
                "plan": plan,
                "plan_labels": self._run.plan_labels,
                "command_display": " && ".join(_command_display(step) for step in plan),
                "with_shell": self._run.with_shell,
                "running": self._run.running,
                "completed": self._run.completed,
                "exit_code": self._run.exit_code,
                "error": self._run.error,
                "current_stage": self._run.current_stage,
                "output": output_tail,
                "output_line_count": len(self._run.output),
                "started_at": self._run.started_at,
                "finished_at": self._run.finished_at,
            }

    def start(self, *, with_shell: bool) -> tuple[bool, str]:
        """Start the install steps in a background daemon thread, refusing a concurrent run."""
        try:
            steps = build_install_steps(self.source_dir, with_shell=with_shell)
        except FileNotFoundError as exc:
            with self._lock:
                self._run = InstallRun(
                    command=[],
                    plan=[],
                    with_shell=with_shell,
                    running=False,
                    completed=True,
                    exit_code=-1,
                    error=str(exc),
                    current_stage=None,
                    finished_at=time.time(),
                )
            return False, str(exc)

        with self._lock:
            if self._run.running:
                return False, "Installation is already running."
            self._run = InstallRun(
                command=steps[-1].command,
                plan=[step.command for step in steps],
                plan_labels=[step.label for step in steps],
                with_shell=with_shell,
                running=True,
                completed=False,
                exit_code=None,
                error=None,
                output=[],
                current_stage=steps[0].label,
                started_at=time.time(),
            )

        worker = threading.Thread(
            target=self._worker,
            args=(steps,),
            daemon=True,
            name="installer-gui-worker",
        )
        worker.start()
        return True, "Installer started."

    def _append_output(self, line: str) -> None:
        """Thread-safely append one line to the current run's output buffer."""
        with self._lock:
            self._run.output.append(line)

    def _worker(self, steps: list[InstallStep]) -> None:
        """Background-thread body: run install steps sequentially, streaming their output."""
        exit_code = 0
        error: str | None = None
        env = install_runtime_env()
        try:
            for step in steps:
                with self._lock:
                    self._run.current_stage = step.label
                self._append_output(f"[stage] {step.label}")
                self._append_output(f"$ {' '.join(step.command)}")
                process = subprocess.Popen(
                    step.command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env,
                )
                assert process.stdout is not None
                for raw_line in process.stdout:
                    self._append_output(raw_line.rstrip("\n"))
                exit_code = process.wait()
                if exit_code != 0:
                    break
                env = install_runtime_env(env)
        except Exception as exc:  # pragma: no cover - defensive path  # noqa: BLE001
            error = str(exc)
            exit_code = -1

        with self._lock:
            self._run.running = False
            self._run.completed = True
            self._run.exit_code = exit_code
            self._run.error = error
            self._run.finished_at = time.time()

    def open_start_here(self) -> tuple[bool, str]:
        """Open the START_HERE.md guide with the platform opener."""
        guide = start_here_path()
        if not guide.exists():
            return False, f"Guide not found at {guide}"
        if not _open_target(str(guide)):
            return False, f"Could not open {guide}"
        return True, f"Opened {_trim_home(str(guide))}"

    def launch_workflow(self, payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """Normalize and launch a workflow spec from `/api/workflows/launch`."""
        try:
            spec = normalize_launch_spec(payload, self.source_dir)
            launch_payload = launch_workflow(
                spec,
                self.source_dir,
                env=install_runtime_env(),
            )
            return True, launch_payload
        except (FileNotFoundError, ValueError) as exc:
            return False, {
                "accepted": False,
                "message": str(exc),
                "control_plane": self.control_plane_payload(),
            }


class InstallerHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server carrying the InstallController for request handlers to reach."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        controller: InstallController,
    ) -> None:
        """Bind server_address and attach controller for handlers to read via self.server."""
        super().__init__(server_address, InstallerRequestHandler)
        self.controller = controller


STATIC_MIME_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".webmanifest": "application/manifest+json",
}


class InstallerRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler serving the JSON API plus the static/inline installer HTML."""

    server: InstallerHTTPServer

    def do_GET(self) -> None:
        """Route GET: /api/* JSON endpoints, static site-bundle assets, then inline HTML."""
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            if path == "/api/preflight":
                self._send_json(self.server.controller.preflight_payload())
                return
            if path == "/api/install/status":
                self._send_json(self.server.controller.status_payload())
                return
            if path == "/api/control-plane":
                self._send_json(self.server.controller.control_plane_payload())
                return
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return

        static_file = self._resolve_static(path)
        if static_file is not None:
            self._send_file(static_file)
            return

        if path == "/":
            self._send_html(build_html(self.server.controller.preflight_payload()))
            return

        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def _resolve_static(self, url_path: str) -> Path | None:
        """Map a URL path to a file inside the site bundle, refusing path escapes.

        Returns None when there is no bundle, no match, or the resolved path
        would fall outside site_dist.
        """
        site_dist = self.server.controller.site_dist_dir
        if site_dist is None:
            return None

        if url_path in ("/", "") or url_path in ("/en/install", "/en/install/"):
            candidate = site_dist / "en" / "install" / "index.html"
        elif url_path in ("/pl/install", "/pl/install/"):
            candidate = site_dist / "pl" / "install" / "index.html"
        else:
            relative = url_path.lstrip("/")
            if not relative:
                return None
            candidate = site_dist / relative
            if url_path.endswith("/"):
                candidate = candidate / "index.html"

        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            return None

        try:
            resolved.relative_to(site_dist)
        except ValueError:
            return None

        if resolved.is_file():
            return resolved
        return None

    def _send_file(self, path: Path) -> None:
        """Serve a static file with the right MIME type; injects a live-mode flag into HTML."""
        try:
            data = path.read_bytes()
        except OSError:
            self._send_json(
                {"error": "Asset not readable"}, status=HTTPStatus.INTERNAL_SERVER_ERROR
            )
            return
        mime = STATIC_MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")
        # Mark Astro-built install pages as live when served by us. The
        # Svelte InstallerShell reads window.__VIBECRAFTED_LIVE__ to decide
        # between preview (static manifest) and live (this API) mode; without
        # the flag, hostname-only detection false-positives on Astro dev.
        if path.suffix.lower() == ".html":
            try:
                html = data.decode("utf-8")
            except UnicodeDecodeError:
                html = None
            if html is not None and "__VIBECRAFTED_LIVE__" not in html:
                injection = "<script>window.__VIBECRAFTED_LIVE__=true;</script></head>"
                if "</head>" in html:
                    html = html.replace("</head>", injection, 1)
                    data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        """Route POST: /api/install, /api/open-start-here, /api/workflows/launch."""
        parsed = urlparse(self.path)
        if parsed.path == "/api/install":
            payload = self._read_json()
            accepted, message = self.server.controller.start(
                with_shell=bool(payload.get("with_shell", True))
            )
            status_payload = self.server.controller.status_payload()
            status_payload["accepted"] = accepted
            status_payload["message"] = message
            status = HTTPStatus.ACCEPTED if accepted else HTTPStatus.CONFLICT
            if status_payload.get("error") and not status_payload.get("running"):
                status = HTTPStatus.BAD_REQUEST
            self._send_json(status_payload, status=status)
            return
        if parsed.path == "/api/open-start-here":
            ok, message = self.server.controller.open_start_here()
            self._send_json(
                {"ok": ok, "message": message},
                status=HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST,
            )
            return
        if parsed.path == "/api/workflows/launch":
            ok, payload = self.server.controller.launch_workflow(self._read_json())
            self._send_json(
                payload,
                status=HTTPStatus.ACCEPTED if ok else HTTPStatus.BAD_REQUEST,
            )
            return
        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        """Suppress BaseHTTPRequestHandler's default per-request stderr logging."""
        return

    def _read_json(self) -> dict[str, Any]:
        """Read and parse the request body as JSON; {} when there is no body."""
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _send_html(self, payload: str, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        """Write an HTML response with the given status code."""
        body = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(
        self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        """Write a JSON response with no-store caching and the given status code."""
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_html(preflight: dict[str, Any]) -> str:
    """Render the inline HTML/CSS/JS installer shell, embedding preflight as boot JSON.

    Used only when no pre-built Svelte site bundle is found; the returned
    document polls the JSON API to drive its own UI.
    """
    boot_json = json.dumps(preflight).replace("</", "<\\/")
    template = dedent(
        """\
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Vibecrafted Control Plane</title>
            <style>
              :root {
                --bg: #091016;
                --bg-top: rgba(197, 143, 101, 0.14);
                --bg-side: rgba(122, 159, 157, 0.12);
                --panel: rgba(14, 21, 29, 0.9);
                --panel-strong: rgba(20, 29, 37, 0.96);
                --panel-soft: rgba(255, 255, 255, 0.03);
                --border: rgba(197, 143, 101, 0.22);
                --border-strong: rgba(197, 143, 101, 0.36);
                --copper: #d79a63;
                --patina: #8cb5b0;
                --stone: #d9d5c8;
                --text: #eef2f3;
                --muted: #96a8ae;
                --muted-strong: #b8c4c8;
                --ok: #84d59a;
                --warn: #f0c36e;
                --fail: #f48c7f;
                --shadow: 0 28px 90px rgba(0, 0, 0, 0.38);
                --radius-xl: 30px;
                --radius-lg: 24px;
                --radius-md: 18px;
              }

              * { box-sizing: border-box; }

              body {
                margin: 0;
                min-height: 100vh;
                min-height: 100dvh;
                padding: 18px;
                overflow: hidden;
                background:
                  radial-gradient(circle at top left, var(--bg-top), transparent 34%),
                  radial-gradient(circle at right, var(--bg-side), transparent 30%),
                  linear-gradient(180deg, #0e151c 0%, #081016 100%);
                color: var(--text);
                font-family: "SF Mono", "JetBrains Mono", "IBM Plex Mono", monospace;
              }

              a {
                color: inherit;
              }

              button,
              input {
                font: inherit;
              }

              .shell {
                width: min(1220px, 100%);
                min-height: calc(100vh - 36px);
                height: calc(100vh - 36px);
                height: calc(100dvh - 36px);
                max-height: calc(100vh - 36px);
                max-height: calc(100dvh - 36px);
                display: grid;
                grid-template-columns: minmax(280px, 340px) minmax(0, 1fr);
                gap: 18px;
                margin: 0 auto;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: var(--radius-xl);
                background: rgba(8, 11, 16, 0.76);
                box-shadow: var(--shadow);
                backdrop-filter: blur(18px);
                overflow: hidden;
              }

              .rail,
              .main {
                min-height: 0;
              }

              .rail {
                padding: 18px;
                display: grid;
                gap: 14px;
                align-content: start;
                overflow: auto;
                background:
                  linear-gradient(180deg, rgba(197, 143, 101, 0.08), transparent 36%),
                  linear-gradient(180deg, rgba(10, 16, 22, 0.96), rgba(10, 16, 22, 0.86));
                border-right: 1px solid rgba(255, 255, 255, 0.05);
              }

              .rail-card,
              .main-card,
              .slide-card,
              .status-card,
              .summary-card {
                padding: 18px;
                border: 1px solid var(--border);
                border-radius: var(--radius-lg);
                background: var(--panel);
              }

              .eyebrow {
                color: var(--patina);
                letter-spacing: 0.14em;
                text-transform: uppercase;
                font-size: 12px;
              }

              .rail-brand {
                display: grid;
                gap: 12px;
              }

              .rail-brand h1,
              .slide-title {
                margin: 0;
                font-size: clamp(30px, 4vw, 44px);
                line-height: 0.94;
                letter-spacing: -0.06em;
              }

              .slide-title {
                font-size: clamp(24px, 3vw, 34px);
              }

              .rail-brand strong {
                color: var(--copper);
                letter-spacing: 0.08em;
                text-transform: uppercase;
              }

              .rail-lead,
              .slide-copy,
              .slide-note,
              .status-copy,
              .summary-copy,
              .footer-hint,
              .item-detail {
                margin: 0;
                line-height: 1.65;
                color: var(--muted);
              }

              .rail-mini {
                display: grid;
                gap: 10px;
              }

              .fallback-code,
              .command-box,
              .log-box {
                padding: 14px 16px;
                border-radius: var(--radius-md);
                border: 1px solid rgba(255, 255, 255, 0.08);
                background: rgba(4, 8, 12, 0.78);
                color: var(--stone);
                overflow: auto;
              }

              .fallback-code {
                margin: 0;
                display: block;
                white-space: pre-wrap;
                word-break: break-word;
              }

              .facts {
                display: grid;
                gap: 10px;
              }

              .fact {
                display: grid;
                gap: 4px;
                padding: 12px 14px;
                border-radius: 16px;
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.06);
              }

              .fact span {
                font-size: 11px;
                letter-spacing: 0.12em;
                text-transform: uppercase;
                color: var(--patina);
              }

              .fact strong {
                color: var(--text);
                word-break: break-word;
              }

              .counts {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 10px;
              }

              .count {
                padding: 14px;
                border-radius: 18px;
                border: 1px solid rgba(255, 255, 255, 0.07);
                background: rgba(255, 255, 255, 0.03);
              }

              .count span {
                display: block;
                font-size: 11px;
                letter-spacing: 0.12em;
                text-transform: uppercase;
                color: var(--muted);
              }

              .count strong {
                display: block;
                margin-top: 6px;
                font-size: 28px;
                color: var(--text);
              }

              .main {
                padding: 18px 18px 18px 0;
                display: grid;
                grid-template-rows: auto auto minmax(0, 1fr) auto;
                gap: 16px;
              }

              .control-plane {
                display: grid;
                gap: 14px;
                grid-template-columns: 1.2fr 1fr 1fr;
                padding: 14px;
              }

              .panel-card {
                padding: 16px;
                border-radius: var(--radius-lg);
                border: 1px solid rgba(255, 255, 255, 0.08);
                background: rgba(255, 255, 255, 0.02);
                display: grid;
                gap: 12px;
                min-height: 0;
              }

              .panel-card h3,
              .panel-card h4 {
                margin: 0;
              }

              .panel-card h4 {
                font-size: 14px;
                color: var(--patina);
                letter-spacing: 0.08em;
                text-transform: uppercase;
              }

              .launcher-form {
                display: grid;
                gap: 10px;
              }

              .launcher-grid,
              .status-grid {
                display: grid;
                gap: 10px;
                grid-template-columns: repeat(2, minmax(0, 1fr));
              }

              .field {
                display: grid;
                gap: 6px;
              }

              .field label {
                font-size: 12px;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                color: var(--muted-strong);
              }

              .field input,
              .field select {
                width: 100%;
                padding: 11px 12px;
                border-radius: 14px;
                border: 1px solid rgba(255, 255, 255, 0.1);
                background: rgba(4, 8, 12, 0.78);
                color: var(--text);
              }

              .run-list,
              .event-list {
                margin: 0;
                padding: 0;
                list-style: none;
                display: grid;
                gap: 8px;
              }

              .run-row,
              .event-row {
                padding: 12px 14px;
                border-radius: 16px;
                border: 1px solid rgba(255, 255, 255, 0.07);
                background: rgba(0, 0, 0, 0.16);
                display: grid;
                gap: 6px;
              }

              .run-head,
              .event-head {
                display: flex;
                justify-content: space-between;
                gap: 10px;
                align-items: center;
              }

              .run-meta,
              .event-meta {
                color: var(--muted);
                font-size: 13px;
                line-height: 1.5;
              }

              .warning-list {
                margin: 0;
                padding-left: 18px;
                color: var(--warn);
                display: grid;
                gap: 6px;
              }

              .launch-feedback {
                min-height: 1.3em;
                color: var(--muted);
              }

              .progress {
                margin: 0;
                padding: 0;
                list-style: none;
                display: grid;
                grid-template-columns: repeat(6, minmax(0, 1fr));
                gap: 10px;
                padding: 14px;
              }

              .progress-button {
                width: 100%;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 18px;
                background: rgba(255, 255, 255, 0.03);
                color: var(--muted);
                padding: 12px 10px;
                display: grid;
                gap: 8px;
                justify-items: center;
                cursor: pointer;
                transition: border-color 160ms ease, transform 160ms ease, background 160ms ease;
              }

              .progress-button:hover:not(:disabled) {
                transform: translateY(-1px);
                border-color: rgba(255, 255, 255, 0.16);
              }

              .progress-button:disabled {
                cursor: not-allowed;
                opacity: 0.62;
              }

              .progress-dot {
                width: 14px;
                height: 14px;
                border-radius: 999px;
                border: 2px solid rgba(255, 255, 255, 0.18);
                background: transparent;
                transition: background 160ms ease, border-color 160ms ease, transform 160ms ease;
              }

              .progress-meta {
                display: grid;
                gap: 2px;
                justify-items: center;
              }

              .progress-meta span:first-child {
                font-size: 11px;
                letter-spacing: 0.12em;
                text-transform: uppercase;
              }

              .progress-meta strong {
                font-size: 13px;
              }

              .progress-button.is-active {
                border-color: var(--border-strong);
                color: var(--text);
                background: linear-gradient(180deg, rgba(215, 154, 99, 0.16), rgba(255, 255, 255, 0.03));
              }

              .progress-button.is-active .progress-dot,
              .progress-button.is-complete .progress-dot {
                border-color: var(--copper);
                background: var(--copper);
                transform: scale(1.04);
              }

              .progress-button.is-complete {
                color: var(--muted-strong);
              }

              .stage {
                min-height: 0;
                padding: 0 14px 14px;
                display: flex;
              }

              .slides {
                min-height: 0;
                height: 100%;
                display: flex;
                flex: 1 1 auto;
              }

              .slide {
                display: none;
                min-height: 100%;
              }

              .slide.is-active {
                display: flex;
                flex: 1 1 auto;
                animation: fade-in 220ms cubic-bezier(0.2, 0.9, 0.2, 1);
              }

              .slide-card {
                display: flex;
                flex-direction: column;
                gap: 18px;
                width: 100%;
                min-height: 0;
                background:
                  linear-gradient(180deg, rgba(197, 143, 101, 0.06), transparent 24%),
                  var(--panel-strong);
              }

              .slide-body {
                min-height: 0;
                flex: 1 1 auto;
                overflow-y: auto;
                overscroll-behavior: contain;
                -webkit-overflow-scrolling: touch;
                scrollbar-gutter: stable both-edges;
                touch-action: pan-y;
                display: grid;
                gap: 16px;
                padding-right: 4px;
              }

              .lead-grid,
              .install-grid {
                display: grid;
                gap: 14px;
                grid-template-columns: repeat(2, minmax(0, 1fr));
              }

              .bullet-list,
              .compact-list,
              .timeline,
              .next-steps,
              .decision-points {
                margin: 0;
                padding: 0;
                list-style: none;
                display: grid;
                gap: 10px;
              }

              .bullet-list li,
              .compact-list li,
              .timeline li,
              .decision-points li {
                padding: 14px 16px;
                border-radius: 18px;
                border: 1px solid rgba(255, 255, 255, 0.07);
                background: var(--panel-soft);
              }

              .compact-list li {
                padding: 10px 12px;
              }

              .category-grid {
                display: grid;
                gap: 12px;
                grid-template-columns: repeat(2, minmax(0, 1fr));
              }

              .category-card {
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 20px;
                padding: 16px;
                background: rgba(255, 255, 255, 0.02);
                display: grid;
                gap: 12px;
              }

              .category-head,
              .timeline li,
              .item-line,
              .button-row,
              .log-meta,
              .footer {
                display: flex;
                justify-content: space-between;
                gap: 12px;
                align-items: center;
              }

              .status-pill {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
                border-radius: 999px;
                padding: 6px 11px;
                border: 1px solid rgba(255, 255, 255, 0.08);
                background: rgba(255, 255, 255, 0.03);
                color: var(--muted);
                font-size: 12px;
                text-transform: uppercase;
                letter-spacing: 0.08em;
              }

              .status-pill--ok { color: var(--ok); }
              .status-pill--warn { color: var(--warn); }
              .status-pill--fail { color: var(--fail); }

              .chip {
                display: inline-flex;
                align-items: center;
                gap: 8px;
                border-radius: 999px;
                padding: 10px 14px;
                border: 1px solid rgba(255, 255, 255, 0.08);
                color: var(--muted);
                background: rgba(255, 255, 255, 0.03);
                align-self: flex-start;
              }

              .chip.ok { color: var(--ok); }
              .chip.warn { color: var(--warn); }
              .chip.fail { color: var(--fail); }

              .category-items {
                display: grid;
                gap: 8px;
              }

              .category-items li {
                display: flex;
                justify-content: space-between;
                gap: 10px;
                align-items: center;
                padding: 10px 12px;
                border-radius: 14px;
                background: rgba(0, 0, 0, 0.16);
              }

              .summary-grid {
                display: grid;
                gap: 12px;
                grid-template-columns: repeat(2, minmax(0, 1fr));
              }

              .summary-card {
                display: flex;
                flex-direction: column;
                gap: 12px;
                min-height: 0;
              }

              .summary-card h3,
              .status-card h3 {
                margin: 0;
                font-size: 18px;
              }

              .install-form,
              .status-stack {
                display: grid;
                gap: 14px;
              }

              .toggle {
                display: flex;
                gap: 12px;
                align-items: flex-start;
                padding: 14px 16px;
                border-radius: 18px;
                border: 1px solid rgba(255, 255, 255, 0.07);
                background: rgba(255, 255, 255, 0.02);
              }

              .toggle input {
                margin-top: 2px;
              }

              .button-row {
                display: flex;
                flex-wrap: wrap;
                justify-content: flex-start;
              }

              button {
                border: 1px solid transparent;
                border-radius: 999px;
                padding: 12px 18px;
                cursor: pointer;
                transition: transform 120ms ease, opacity 120ms ease, border-color 120ms ease;
              }

              button:hover {
                transform: translateY(-1px);
              }

              button:disabled {
                cursor: not-allowed;
                opacity: 0.65;
                transform: none;
              }

              .primary {
                background: linear-gradient(135deg, #d79a63 0%, #e8b98d 100%);
                color: #21160e;
                font-weight: 700;
              }

              .secondary {
                background: rgba(255, 255, 255, 0.06);
                color: var(--text);
                border: 1px solid rgba(255, 255, 255, 0.1);
              }

              .command-box,
              .log-box {
                min-height: 0;
                overflow: auto;
              }

              .command-box {
                max-height: 144px;
              }

              .log-box {
                max-height: min(34vh, 320px);
              }

              .command-box code,
              .log-box pre {
                margin: 0;
                white-space: pre-wrap;
                word-break: break-word;
                font: inherit;
              }

              .log-meta {
                color: var(--muted);
                font-size: 13px;
              }

              .status-card {
                display: grid;
                gap: 14px;
              }

              .timeline li {
                align-items: flex-start;
              }

              .timeline li strong {
                display: block;
                color: var(--text);
                margin-bottom: 4px;
              }

              .timeline-state {
                flex-shrink: 0;
              }

              .timeline li[data-state="running"] {
                border-color: rgba(240, 195, 110, 0.28);
              }

              .timeline li[data-state="done"] {
                border-color: rgba(132, 213, 154, 0.24);
              }

              .timeline li[data-state="failed"] {
                border-color: rgba(244, 140, 127, 0.28);
              }

              .success-panel,
              .locked-panel {
                padding: 18px;
                border-radius: 18px;
                border: 1px solid rgba(132, 213, 154, 0.24);
                background: rgba(132, 213, 154, 0.08);
                display: grid;
                gap: 12px;
              }

              .locked-panel {
                border-color: rgba(240, 195, 110, 0.22);
                background: rgba(240, 195, 110, 0.08);
              }

              .next-steps {
                gap: 8px;
              }

              .next-steps li::before,
              .decision-points li::before,
              .bullet-list li::before {
                content: ">";
                color: var(--copper);
                margin-right: 10px;
              }

              .next-steps li,
              .decision-points li,
              .bullet-list li {
                display: flex;
                align-items: flex-start;
              }

              .footer {
                padding: 0 14px 14px;
              }

              .footer-hint {
                flex: 1;
                text-align: center;
              }

              .nav-button {
                min-width: 120px;
              }

              .guide-feedback {
                min-height: 1.3em;
                color: var(--muted);
              }

              .muted-strong {
                color: var(--muted-strong);
              }

              @keyframes fade-in {
                from {
                  opacity: 0;
                  transform: translateY(8px);
                }
                to {
                  opacity: 1;
                  transform: translateY(0);
                }
              }

              @media (max-width: 960px) {
                body {
                  padding: 0;
                }

                .shell {
                  grid-template-columns: 1fr;
                  grid-template-rows: auto minmax(0, 1fr);
                  min-height: 100vh;
                  min-height: 100dvh;
                  height: 100vh;
                  height: 100dvh;
                  max-height: 100vh;
                  max-height: 100dvh;
                  border-radius: 0;
                }

                .rail {
                  border-right: 0;
                  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
                  max-height: 34vh;
                  border-radius: 0;
                }

                .main {
                  padding: 0 10px 10px;
                }

                .progress {
                  grid-template-columns: repeat(3, minmax(0, 1fr));
                }

                .lead-grid,
                .install-grid,
                .control-plane,
                .launcher-grid,
                .status-grid,
                .category-grid,
                .summary-grid {
                  grid-template-columns: 1fr;
                }

                .footer {
                  padding: 0 10px 10px;
                  flex-wrap: wrap;
                  justify-content: center;
                }
              }
            </style>
          </head>
          <body>
            <div class="shell">
              <aside class="rail">
                <section class="rail-card rail-brand">
                  <div class="eyebrow">Control plane</div>
                  <strong>%%HEADER%%</strong>
                  <h1>Install locally. Work from evidence.</h1>
                  <p class="rail-lead">
                    One local installer. One visible plan.
                  </p>
                </section>

                <section class="rail-card">
                  <div class="counts">
                    <div class="count">
                      <span>Ready now</span>
                      <strong id="ready-count">0</strong>
                    </div>
                    <div class="count">
                      <span>Needs install</span>
                      <strong id="missing-count">0</strong>
                    </div>
                  </div>
                </section>

                <section class="rail-card facts">
                  <div class="fact">
                    <span>Framework</span>
                    <strong id="version-value"></strong>
                  </div>
                  <div class="fact">
                    <span>Source</span>
                    <strong id="source-value"></strong>
                  </div>
                  <div class="fact">
                    <span>Guide</span>
                    <strong id="guide-value"></strong>
                  </div>
                  <div class="fact">
                    <span>Helpers</span>
                    <strong id="helper-value"></strong>
                  </div>
                </section>

                <section class="rail-card rail-mini">
                  <div class="eyebrow">Why this shape</div>
                  <p class="rail-lead">%%TAGLINE%%</p>
                  <p class="slide-note">%%PRODUCT_LINE%%</p>
                  <code class="fallback-code">make wizard</code>
                  <p class="slide-note">
                    Local checkout GUI stays available for operators who want this browser-guided rhythm. For terminal-native flow, use <code>make install</code>.
                  </p>
                </section>
              </aside>

              <main class="main">
                <section class="main-card control-plane">
                  <section class="panel-card">
                    <div class="eyebrow">Start</div>
                    <h3>Install without shell glue</h3>
                    <p class="summary-copy">
                      The GUI is launch-capable on purpose: it dispatches through the same command deck, then switches to observability instead of pretending to be the operator console.
                    </p>
                    <form class="launcher-form" id="launcher-form">
                      <div class="launcher-grid">
                        <div class="field">
                          <label for="launch-skill">Workflow</label>
                          <select id="launch-skill" name="skill"></select>
                        </div>
                        <div class="field">
                          <label for="launch-agent">Agent</label>
                          <select id="launch-agent" name="agent"></select>
                        </div>
                        <div class="field">
                          <label for="launch-runtime">Runtime</label>
                          <select id="launch-runtime" name="runtime"></select>
                        </div>
                        <div class="field">
                          <label for="launch-root">Root</label>
                          <input id="launch-root" name="root" type="text">
                        </div>
                      </div>
                      <div class="field">
                        <label for="launch-prompt">Prompt</label>
                        <input id="launch-prompt" name="prompt" type="text" placeholder="Launch with an inline prompt">
                      </div>
                      <div class="field">
                        <label for="launch-file">Prompt File</label>
                        <input id="launch-file" name="file" type="text" placeholder="/absolute/path/to/brief.md">
                      </div>
                      <div class="button-row">
                        <button class="primary" id="launch-button" type="submit">Launch workflow</button>
                        <button class="secondary open-guide-button" type="button">Open START_HERE</button>
                      </div>
                    </form>
                    <p class="launch-feedback" id="launch-feedback"></p>
                  </section>

                  <section class="panel-card">
                    <div class="eyebrow">Observe</div>
                    <h3>Active and recent runs</h3>
                    <div class="status-grid">
                      <div class="count">
                        <span>Active</span>
                        <strong id="active-run-count">0</strong>
                      </div>
                      <div class="count">
                        <span>Recent</span>
                        <strong id="recent-run-count">0</strong>
                      </div>
                    </div>
                    <div>
                      <h4>Warnings</h4>
                      <ul class="warning-list" id="warning-list"></ul>
                    </div>
                    <div>
                      <h4>Active runs</h4>
                      <ul class="run-list" id="active-run-list"></ul>
                    </div>
                    <div>
                      <h4>Recent events</h4>
                      <ul class="event-list" id="event-list"></ul>
                    </div>
                  </section>

                  <section class="panel-card">
                    <div class="eyebrow">Foundations</div>
                    <h3>Doctor truth and helper surface</h3>
                    <p class="summary-copy">
                      Installer scan, shown in one place.
                    </p>
                    <div class="status-grid">
                      <div class="count">
                        <span>Skills ready</span>
                        <strong id="skills-ready-count">0</strong>
                      </div>
                      <div class="count">
                        <span>Missing</span>
                        <strong id="needs-install-count">0</strong>
                      </div>
                    </div>
                    <div>
                      <h4>Helper layer</h4>
                      <div class="command-box"><code id="helper-path-inline"></code></div>
                    </div>
                    <div>
                      <h4>Needs install</h4>
                      <ul class="compact-list" id="needs-install-list"></ul>
                    </div>
                  </section>
                </section>

                <ol class="progress main-card" id="wizard-progress">
                  <li>
                    <button class="progress-button" data-step="0" type="button">
                      <span class="progress-dot"></span>
                      <span class="progress-meta"><span>01</span><strong>Welcome</strong></span>
                    </button>
                  </li>
                  <li>
                    <button class="progress-button" data-step="1" type="button">
                      <span class="progress-dot"></span>
                      <span class="progress-meta"><span>02</span><strong>Explain</strong></span>
                    </button>
                  </li>
                  <li>
                    <button class="progress-button" data-step="2" type="button">
                      <span class="progress-dot"></span>
                      <span class="progress-meta"><span>03</span><strong>Diagnostics</strong></span>
                    </button>
                  </li>
                  <li>
                    <button class="progress-button" data-step="3" type="button">
                      <span class="progress-dot"></span>
                      <span class="progress-meta"><span>04</span><strong>Checklist</strong></span>
                    </button>
                  </li>
                  <li>
                    <button class="progress-button" data-step="4" type="button">
                      <span class="progress-dot"></span>
                      <span class="progress-meta"><span>05</span><strong>Install</strong></span>
                    </button>
                  </li>
                  <li>
                    <button class="progress-button" data-step="5" type="button">
                      <span class="progress-dot"></span>
                      <span class="progress-meta"><span>06</span><strong>Finish</strong></span>
                    </button>
                  </li>
                </ol>

                <section class="stage">
                  <div class="slides">
                    <article class="slide is-active" data-step="0">
                      <section class="slide-card">
                        <div class="eyebrow">Step 1 of 6</div>
                        <h2 class="slide-title">Install Vibecrafted</h2>
                        <div class="slide-body">
                          <div class="lead-grid">
                            <div class="main-card">
                              <p class="slide-copy">
                                This setup checks the machine and runs the repo-owned installer.
                              </p>
                            </div>
                            <div class="main-card">
                              <p class="slide-copy">
                                Until you launch the install, this wizard is read-only.
                              </p>
                            </div>
                          </div>
                          <ul class="bullet-list">
                            <li>Foundations are bootstrapped before workflow skills touch your runtime.</li>
                            <li>The browser surface stays thin on purpose: one mutation engine, one live log, one truthful outcome.</li>
                            <li>The result should feel like onboarding, not like scrolling through raw terminal entropy.</li>
                          </ul>
                        </div>
                      </section>
                    </article>

                    <article class="slide" data-step="1">
                      <section class="slide-card">
                        <div class="eyebrow">Step 2 of 6</div>
                        <h2 class="slide-title">What this wizard is doing for you</h2>
                        <div class="slide-body">
                          <ul class="decision-points">
                            <li>TwinSweep-style effortlessness: local web surface, lower-friction first run, readable trust contract.</li>
                            <li>`rmcp-memex`-style wizard rhythm: welcome, detection, checklist, explicit execution, clean finish state.</li>
                          <li>No second installer: this view runs the same install path as automation.</li>
                          </ul>
                          <div class="summary-grid">
                            <section class="summary-card">
                              <h3>Human front door</h3>
                              <p class="summary-copy">
                                Useful when someone needs the install path before memorizing commands.
                              </p>
                            </section>
                            <section class="summary-card">
                              <h3>Local install view</h3>
                              <p class="summary-copy">
                                `make wizard` opens this same browser-guided surface from a local checkout. `make install` stays the shell-first default.
                              </p>
                            </section>
                          </div>
                        </div>
                      </section>
                    </article>

                    <article class="slide" data-step="2">
                      <section class="slide-card">
                        <div class="eyebrow">Step 3 of 6</div>
                        <h2 class="slide-title">Preflight diagnostics</h2>
                        <div class="slide-body">
                          <p class="slide-copy" id="diagnostic-summary">
                            We check the current framework surface, foundations, toolchains, agent CLIs, and helper tools before touching the filesystem.
                          </p>
                          <div class="category-grid" id="category-grid"></div>
                        </div>
                      </section>
                    </article>

                    <article class="slide" data-step="3">
                      <section class="slide-card">
                        <div class="eyebrow">Step 4 of 6</div>
                        <h2 class="slide-title">Checklist and install choices</h2>
                        <div class="slide-body">
                          <div class="summary-grid">
                            <section class="summary-card">
                              <h3>Already ready</h3>
                              <ul class="compact-list" id="ready-list"></ul>
                            </section>
                            <section class="summary-card">
                              <h3>We will install</h3>
                              <ul class="compact-list" id="missing-list"></ul>
                            </section>
                          </div>

                          <section class="summary-card">
                            <h3>Execution stages</h3>
                            <ul class="compact-list" id="plan-list"></ul>
                          </section>

                          <form class="install-form" id="install-form">
                            <label class="toggle" for="with-shell">
                              <input checked id="with-shell" name="with-shell" type="checkbox">
                              <span>
                                <strong>Install shell helpers</strong><br>
                                Add the optional helper layer so `vc-*` wrappers and the command deck are available in future sessions.
                              </span>
                            </label>
                          </form>
                        </div>
                      </section>
                    </article>

                    <article class="slide" data-step="4">
                      <section class="slide-card">
                        <div class="eyebrow">Step 5 of 6</div>
                        <h2 class="slide-title">Launch the real installer</h2>
                        <div class="slide-body">
                          <div class="install-grid">
                            <section class="status-card">
                              <div class="chip" id="status-chip">Ready</div>
                              <p class="status-copy" id="status-text">
                                Review the plan, then install.
                              </p>
                              <ul class="timeline" id="status-plan"></ul>
                              <div class="button-row">
                                <button class="primary" id="install-button" type="button">Launch guided install</button>
                                <button class="secondary open-guide-button" type="button">Open START_HERE</button>
                              </div>
                              <p class="guide-feedback" id="guide-feedback"></p>
                            </section>
                            <section class="status-card">
                              <h3>Command preview</h3>
                              <div class="command-box">
                                <code id="command-line">Install command will appear here.</code>
                              </div>
                              <div class="log-meta">
                                <span id="log-lines">0 lines captured</span>
                                <span id="status-meta">Compact mode, repo-owned installer truth.</span>
                              </div>
                            </section>
                          </div>
                          <section class="status-card">
                            <h3>Live output</h3>
                            <div class="log-box">
                              <pre id="log-output">No install has run yet.</pre>
                            </div>
                          </section>
                        </div>
                      </section>
                    </article>

                    <article class="slide" data-step="5">
                      <section class="slide-card">
                        <div class="eyebrow">Step 6 of 6</div>
                        <h2 class="slide-title">Finish state</h2>
                        <div class="slide-body">
                          <section class="success-panel" id="finish-panel" hidden>
                            <strong>Install finished cleanly.</strong>
                            <p class="summary-copy" id="finish-summary">
                              Install complete. Open START_HERE, then use the command deck.
                            </p>
                            <ul class="next-steps">
                              <li><code>vibecrafted help</code></li>
                              <li><code>vibecrafted doctor</code></li>
                              <li><code>vibecrafted init claude</code></li>
                            </ul>
                            <div class="button-row">
                              <button class="secondary open-guide-button" type="button">Open START_HERE</button>
                            </div>
                            <p class="guide-feedback" id="finish-feedback"></p>
                          </section>

                          <section class="locked-panel" id="finish-locked">
                            <strong>Finish unlocks after install.</strong>
                            <p class="summary-copy">
                              Run the install first. Once the repo-owned flow exits cleanly, this step becomes the handoff surface for the next commands.
                            </p>
                          </section>
                        </div>
                      </section>
                    </article>
                  </div>
                </section>

                <footer class="footer">
                  <button class="secondary nav-button" id="back-button" type="button">Back</button>
                  <p class="footer-hint" id="footer-hint">
                    Welcome. This flow stays explanatory until you explicitly start the install.
                  </p>
                  <button class="primary nav-button" id="next-button" type="button">Continue</button>
                </footer>
              </main>
            </div>

            <script>
              window.__BOOT__ = %%BOOT%%;
            </script>
            <script>
              const boot = window.__BOOT__;
              const dom = {
                version: document.getElementById('version-value'),
                source: document.getElementById('source-value'),
                guide: document.getElementById('guide-value'),
                helper: document.getElementById('helper-value'),
                helperInline: document.getElementById('helper-path-inline'),
                launcherForm: document.getElementById('launcher-form'),
                launchSkill: document.getElementById('launch-skill'),
                launchAgent: document.getElementById('launch-agent'),
                launchRuntime: document.getElementById('launch-runtime'),
                launchRoot: document.getElementById('launch-root'),
                launchPrompt: document.getElementById('launch-prompt'),
                launchFile: document.getElementById('launch-file'),
                launchButton: document.getElementById('launch-button'),
                launchFeedback: document.getElementById('launch-feedback'),
                activeRunCount: document.getElementById('active-run-count'),
                recentRunCount: document.getElementById('recent-run-count'),
                activeRunList: document.getElementById('active-run-list'),
                eventList: document.getElementById('event-list'),
                warningList: document.getElementById('warning-list'),
                skillsReadyCount: document.getElementById('skills-ready-count'),
                needsInstallCount: document.getElementById('needs-install-count'),
                needsInstallList: document.getElementById('needs-install-list'),
                readyCount: document.getElementById('ready-count'),
                missingCount: document.getElementById('missing-count'),
                categoryGrid: document.getElementById('category-grid'),
                diagnosticSummary: document.getElementById('diagnostic-summary'),
                readyList: document.getElementById('ready-list'),
                missingList: document.getElementById('missing-list'),
                planList: document.getElementById('plan-list'),
                installForm: document.getElementById('install-form'),
                withShell: document.getElementById('with-shell'),
                installButton: document.getElementById('install-button'),
                statusChip: document.getElementById('status-chip'),
                statusText: document.getElementById('status-text'),
                commandLine: document.getElementById('command-line'),
                logOutput: document.getElementById('log-output'),
                logLines: document.getElementById('log-lines'),
                statusMeta: document.getElementById('status-meta'),
                statusPlan: document.getElementById('status-plan'),
                finishPanel: document.getElementById('finish-panel'),
                finishLocked: document.getElementById('finish-locked'),
                finishSummary: document.getElementById('finish-summary'),
                guideFeedbackNodes: Array.from(document.querySelectorAll('.guide-feedback')),
                backButton: document.getElementById('back-button'),
                nextButton: document.getElementById('next-button'),
                footerHint: document.getElementById('footer-hint'),
                progressButtons: Array.from(document.querySelectorAll('.progress-button')),
                slides: Array.from(document.querySelectorAll('.slide')),
                openGuideButtons: Array.from(document.querySelectorAll('.open-guide-button')),
              };

              let pollTimer = null;
              let controlPlaneTimer = null;
              let currentStep = 0;
              let latestStatus = boot.status;
              let latestControlPlane = boot.control_plane || { active_runs: [], recent_runs: [], warnings: [], events: [] };
              const stepHints = [
                'Welcome. This flow stays explanatory until you explicitly start the install.',
                'The GUI is the public front door, but it still runs the repo-owned installer truth.',
                'Diagnostics show what is already present and what still needs to be materialized.',
                'This is the consent and checklist step. Decide whether shell helpers should be installed.',
                'Launch the install and watch the real stages stream live.',
                'Finish state: handoff to START_HERE and the command deck.',
              ];

              function escapeHtml(value) {
                return String(value)
                  .replaceAll('&', '&amp;')
                  .replaceAll('<', '&lt;')
                  .replaceAll('>', '&gt;')
                  .replaceAll('"', '&quot;')
                  .replaceAll("'", '&#39;');
              }

              function statusClass(found) {
                return found ? 'status-pill status-pill--ok' : 'status-pill status-pill--warn';
              }

              function optionMarkup(values, selectedValue) {
                return values.map((value) => (
                  `<option value="${escapeHtml(value)}" ${value === selectedValue ? 'selected' : ''}>${escapeHtml(value)}</option>`
                )).join('');
              }

              function populateLauncherOptions() {
                dom.launchSkill.innerHTML = optionMarkup(boot.launcher_defaults.workflows, 'workflow');
                dom.launchAgent.innerHTML = optionMarkup(boot.launcher_defaults.agents, 'claude');
                dom.launchRuntime.innerHTML = optionMarkup(boot.launcher_defaults.runtimes, 'headless');
                dom.launchRoot.value = boot.source_dir || '';
                syncLaunchForm();
              }

              function syncLaunchForm() {
                const skill = dom.launchSkill.value;
                const researchMode = skill === 'research';
                dom.launchAgent.disabled = researchMode;
                if (researchMode) {
                  dom.launchFeedback.textContent = 'Research launches the triple-agent swarm through the existing command deck.';
                } else if (!latestStatus.running) {
                  dom.launchFeedback.textContent = '';
                }
              }

              function formatTimestamp(value) {
                if (!value) {
                  return 'unknown time';
                }
                const parsed = new Date(value);
                return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
              }

              function renderRunRows(runs, emptyLabel) {
                if (!runs.length) {
                  return `<li class="run-row"><div class="run-meta">${escapeHtml(emptyLabel)}</div></li>`;
                }
                return runs.map((run) => `
                  <li class="run-row">
                    <div class="run-head">
                      <strong>${escapeHtml(run.run_id)}</strong>
                      <span class="status-pill ${run.health === 'stalled' ? 'status-pill--warn' : run.state === 'failed' ? 'status-pill--fail' : 'status-pill--ok'}">${escapeHtml(run.state)}</span>
                    </div>
                    <div class="run-meta">
                      ${escapeHtml(run.agent)} · ${escapeHtml(run.skill)} · ${escapeHtml(run.mode)}<br>
                      ${escapeHtml(run.operator_session || 'no operator session')}<br>
                      ${escapeHtml(formatTimestamp(run.updated_at))}
                    </div>
                  </li>
                `).join('');
              }

              function renderEventRows(events) {
                if (!events.length) {
                  return '<li class="event-row"><div class="event-meta">No control-plane events captured yet.</div></li>';
                }
                return events.map((event) => `
                  <li class="event-row">
                    <div class="event-head">
                      <strong>${escapeHtml(event.run_id || 'run')}</strong>
                      <span class="status-pill">${escapeHtml(event.kind || 'event')}</span>
                    </div>
                    <div class="event-meta">${escapeHtml(event.message || '')}<br>${escapeHtml(formatTimestamp(event.ts || ''))}</div>
                  </li>
                `).join('');
              }

              function renderControlPlane(controlPlane) {
                latestControlPlane = controlPlane || latestControlPlane;
                dom.activeRunCount.textContent = String((latestControlPlane.active_runs || []).length);
                dom.recentRunCount.textContent = String((latestControlPlane.recent_runs || []).length);
                dom.activeRunList.innerHTML = renderRunRows(
                  latestControlPlane.active_runs || [],
                  'No active runs yet. Launch from Start or use the command deck.'
                );
                dom.eventList.innerHTML = renderEventRows(latestControlPlane.events || []);
                dom.warningList.innerHTML = (latestControlPlane.warnings && latestControlPlane.warnings.length)
                  ? latestControlPlane.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join('')
                  : '<li>No warnings right now.</li>';
                dom.helperInline.textContent = latestControlPlane.helper_path || boot.helper_path_display || '';
                dom.skillsReadyCount.textContent = String(latestControlPlane.skills_ready || 0);
                dom.needsInstallCount.textContent = String(boot.missing_count || 0);
                dom.needsInstallList.innerHTML = (boot.missing_items && boot.missing_items.length)
                  ? boot.missing_items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')
                  : '<li>Nothing missing. This machine already looks ready.</li>';
              }

              function setGuideFeedback(message) {
                dom.guideFeedbackNodes.forEach((node) => {
                  node.textContent = message || '';
                });
              }

              function previewPlanCommands() {
                return (boot.install_plan || []).map((step) => (
                  dom.withShell.checked
                    ? step.command
                    : step.command.replace(' --with-shell', '')
                ));
              }

              function installSucceeded(status) {
                return Boolean(status.completed && status.exit_code === 0);
              }

              function maxAccessibleStep() {
                return installSucceeded(latestStatus) ? 5 : 4;
              }

              function setStep(targetStep) {
                const nextStep = Math.max(0, Math.min(targetStep, maxAccessibleStep()));
                const stepChanged = currentStep !== nextStep;
                currentStep = nextStep;

                dom.slides.forEach((slide) => {
                  slide.classList.toggle('is-active', Number(slide.dataset.step) === currentStep);
                });

                dom.progressButtons.forEach((button) => {
                  const step = Number(button.dataset.step);
                  const isActive = step === currentStep;
                  const isComplete = step < currentStep || (installSucceeded(latestStatus) && step === 5);
                  const isLocked = step > maxAccessibleStep();
                  button.classList.toggle('is-active', isActive);
                  button.classList.toggle('is-complete', isComplete);
                  button.disabled = isLocked;
                });

                dom.backButton.disabled = currentStep === 0 || latestStatus.running;
                if (currentStep >= 4) {
                  dom.nextButton.hidden = true;
                } else {
                  dom.nextButton.hidden = false;
                  dom.nextButton.disabled = latestStatus.running;
                  dom.nextButton.textContent = currentStep === 3 ? 'Go to install' : 'Continue';
                }
                dom.footerHint.textContent = stepHints[currentStep];

                if (stepChanged) {
                  const activeSlide = dom.slides.find((slide) => Number(slide.dataset.step) === currentStep);
                  activeSlide?.querySelector('.slide-body')?.scrollTo({ top: 0, behavior: 'auto' });
                }
              }

              function renderBoot() {
                dom.version.textContent = boot.version;
                dom.source.textContent = boot.source_dir_display;
                dom.guide.textContent = boot.guide_path_display;
                dom.helper.textContent = boot.helper_path_display;
                dom.readyCount.textContent = String(boot.found_count || 0);
                dom.missingCount.textContent = String(boot.missing_count || 0);
                dom.diagnosticSummary.textContent = boot.missing_count
                  ? `We found ${boot.found_count} ready surface(s) and ${boot.missing_count} missing or incomplete item(s).`
                  : `Everything required for the public surface is already present on this machine.`;

                dom.categoryGrid.innerHTML = boot.categories.map((category) => {
                  const items = category.items.map((item) => `
                    <li>
                      <span>${escapeHtml(item.label)}</span>
                      <span class="${statusClass(item.found)}">${item.found ? 'ready' : 'missing'}</span>
                    </li>
                  `).join('');
                  return `
                    <section class="category-card">
                      <div class="category-head">
                        <strong>${escapeHtml(category.label)}</strong>
                        <span>${category.present}/${category.total}</span>
                      </div>
                      <ul class="category-items">${items}</ul>
                    </section>
                  `;
                }).join('');

                dom.readyList.innerHTML = (boot.found_items && boot.found_items.length)
                  ? boot.found_items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')
                  : '<li>Nothing detected yet.</li>';

                dom.missingList.innerHTML = (boot.missing_items && boot.missing_items.length)
                  ? boot.missing_items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')
                  : '<li>Nothing missing. This machine already looks ready.</li>';

                dom.planList.innerHTML = (boot.install_plan && boot.install_plan.length)
                  ? boot.install_plan.map((step) => `<li><strong>${escapeHtml(step.label)}</strong><div class="item-detail">${escapeHtml(step.command)}</div></li>`).join('')
                  : '<li>Install plan is not available for this source tree.</li>';

                populateLauncherOptions();
                renderControlPlane(boot.control_plane || {});
                renderStatus(latestStatus);
                setStep(0);
              }

              function renderPlan(status) {
                const labels = (status.plan_labels && status.plan_labels.length)
                  ? status.plan_labels
                  : (boot.install_plan || []).map((step) => step.label);
                const seenStages = new Set(
                  (status.output || [])
                    .filter((line) => line.startsWith('[stage] '))
                    .map((line) => line.slice(8).trim())
                );

                dom.statusPlan.innerHTML = labels.map((label) => {
                  let state = 'pending';
                  if (status.running && status.current_stage === label) {
                    state = 'running';
                  } else if (status.completed && status.exit_code !== 0 && status.current_stage === label) {
                    state = 'failed';
                  } else if (installSucceeded(status) || seenStages.has(label)) {
                    state = 'done';
                  }

                  const badgeClass = state === 'done'
                    ? 'status-pill status-pill--ok'
                    : state === 'running'
                      ? 'status-pill status-pill--warn'
                      : state === 'failed'
                        ? 'status-pill status-pill--fail'
                        : 'status-pill';

                  const badgeText = state === 'done'
                    ? 'done'
                    : state === 'running'
                      ? 'running'
                      : state === 'failed'
                        ? 'failed'
                        : 'pending';

                  return `
                    <li data-state="${state}">
                      <div>
                        <strong>${escapeHtml(label)}</strong>
                        <div class="item-detail">${state === 'running' ? 'Running now.' : state === 'done' ? 'Done.' : state === 'failed' ? 'Failed. Check the log.' : 'Queued.'}</div>
                      </div>
                      <span class="timeline-state ${badgeClass}">${badgeText}</span>
                    </li>
                  `;
                }).join('');
              }

              function renderStatus(status) {
                latestStatus = status;
                const commandDisplay = status.command_display || previewPlanCommands().join(' && ') || 'Install command will appear here.';
                dom.commandLine.textContent = commandDisplay;
                dom.logOutput.textContent = status.output && status.output.length
                  ? status.output.join('\\n')
                  : 'No install has run yet.';
                dom.logLines.textContent = `${status.output_line_count || 0} lines captured`;
                renderPlan(status);

                if (status.running) {
                  dom.installButton.disabled = true;
                  dom.withShell.disabled = true;
                  dom.installButton.textContent = 'Installing...';
                  dom.statusChip.className = 'chip warn';
                  dom.statusChip.textContent = 'Installing';
                  const stage = status.current_stage ? `${status.current_stage} is running now.` : 'The guided install is running now.';
                  dom.statusText.textContent = `${stage} You can leave this window open and watch the live log.`;
                  dom.statusMeta.textContent = 'Streaming repo-owned foundations + compact installer output.';
                  dom.finishPanel.hidden = true;
                  dom.finishLocked.hidden = false;
                  setStep(4);
                  return;
                }

                dom.installButton.disabled = false;
                dom.withShell.disabled = false;
                dom.installButton.textContent = status.completed && status.exit_code !== 0
                  ? 'Retry guided install'
                  : 'Launch guided install';

                if (installSucceeded(status)) {
                  dom.statusChip.className = 'chip ok';
                  dom.statusChip.textContent = 'Install complete';
                  dom.statusText.textContent = 'Install finished. Open START_HERE, then use the command deck.';
                  dom.statusMeta.textContent = 'Installer exited cleanly.';
                  dom.finishPanel.hidden = false;
                  dom.finishLocked.hidden = true;
                  dom.finishSummary.textContent = 'Open START_HERE, then use the command deck.';
                  setStep(5);
                  return;
                }

                if (status.completed) {
                  dom.statusChip.className = 'chip fail';
                  dom.statusChip.textContent = 'Failed';
                  dom.statusText.textContent = status.error || `Installer exited with code ${status.exit_code}. Check the log.`;
                  dom.statusMeta.textContent = 'The installer stayed open.';
                  dom.finishPanel.hidden = true;
                  dom.finishLocked.hidden = false;
                  setStep(4);
                  return;
                }

                dom.statusChip.className = 'chip';
                dom.statusChip.textContent = 'Ready';
                dom.statusText.textContent = 'Review the plan, then install.';
                dom.statusMeta.textContent = 'Local installer. Repo-owned plan.';
                dom.finishPanel.hidden = true;
                dom.finishLocked.hidden = false;
                setStep(currentStep);
              }

              async function fetchStatus() {
                const response = await fetch('/api/install/status', { cache: 'no-store' });
                if (!response.ok) {
                  throw new Error('Could not fetch install status');
                }
                return response.json();
              }

              async function fetchControlPlane() {
                const response = await fetch('/api/control-plane', { cache: 'no-store' });
                if (!response.ok) {
                  throw new Error('Could not fetch control-plane state');
                }
                return response.json();
              }

              async function pollStatus() {
                try {
                  const status = await fetchStatus();
                  renderStatus(status);
                  if (status.running) {
                    pollTimer = window.setTimeout(pollStatus, 700);
                  }
                } catch (error) {
                  dom.statusChip.className = 'chip fail';
                  dom.statusChip.textContent = 'Connection issue';
                  dom.statusText.textContent = error.message;
                }
              }

              async function pollControlPlane() {
                try {
                  renderControlPlane(await fetchControlPlane());
                } catch (error) {
                  dom.launchFeedback.textContent = error.message;
                } finally {
                  controlPlaneTimer = window.setTimeout(pollControlPlane, 2500);
                }
              }

              dom.progressButtons.forEach((button) => {
                button.addEventListener('click', () => {
                  setStep(Number(button.dataset.step));
                });
              });

              function isWizardKeyTarget(target) {
                return !(target instanceof HTMLElement)
                  || (!target.isContentEditable
                    && !['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(target.tagName));
              }

              dom.backButton.addEventListener('click', () => {
                setStep(currentStep - 1);
              });

              dom.nextButton.addEventListener('click', () => {
                setStep(currentStep + 1);
              });

              dom.withShell.addEventListener('change', () => {
                renderStatus(latestStatus);
              });

              dom.launchSkill.addEventListener('change', syncLaunchForm);

              dom.installForm.addEventListener('submit', async (event) => {
                event.preventDefault();
                if (pollTimer) {
                  window.clearTimeout(pollTimer);
                }
                setGuideFeedback('');
                const response = await fetch('/api/install', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ with_shell: dom.withShell.checked }),
                });
                const payload = await response.json();
                renderStatus(payload);
                if (payload.message) {
                  setGuideFeedback(payload.message);
                }
                if (payload.running) {
                  pollStatus();
                }
              });

              dom.installButton.addEventListener('click', () => {
                dom.installForm.requestSubmit();
              });

              dom.launcherForm.addEventListener('submit', async (event) => {
                event.preventDefault();
                const launchPayload = {
                  skill: dom.launchSkill.value,
                  agent: dom.launchSkill.value === 'research' ? 'swarm' : dom.launchAgent.value,
                  mode: dom.launchSkill.value,
                  runtime: dom.launchRuntime.value,
                  root: dom.launchRoot.value.trim(),
                  prompt: dom.launchPrompt.value.trim(),
                  file: dom.launchFile.value.trim(),
                };

                const response = await fetch('/api/workflows/launch', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify(launchPayload),
                });
                const payload = await response.json();
                dom.launchFeedback.textContent = payload.message || '';
                if (payload.control_plane) {
                  renderControlPlane(payload.control_plane);
                }
                if (!response.ok) {
                  return;
                }
                dom.launchPrompt.value = '';
                dom.launchFile.value = '';
              });

              dom.openGuideButtons.forEach((button) => {
                button.addEventListener('click', async () => {
                  const response = await fetch('/api/open-start-here', { method: 'POST' });
                  const payload = await response.json();
                  setGuideFeedback(payload.message || '');
                });
              });

              document.addEventListener('keydown', (event) => {
                if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) {
                  return;
                }
                if (!isWizardKeyTarget(event.target)) {
                  return;
                }

                if (event.key === 'ArrowLeft' && currentStep > 0 && !latestStatus.running) {
                  event.preventDefault();
                  setStep(currentStep - 1);
                  return;
                }

                if (event.key === 'Escape' && currentStep > 0 && !latestStatus.running) {
                  event.preventDefault();
                  setStep(currentStep - 1);
                  return;
                }

                if (!['ArrowRight', 'Enter', ' '].includes(event.key)) {
                  return;
                }

                if (event.key === ' ' && currentStep >= 4) {
                  return;
                }

                event.preventDefault();
                if (currentStep < 4) {
                  setStep(currentStep + 1);
                  return;
                }
                if (currentStep === 4 && !latestStatus.running) {
                  dom.installForm.requestSubmit();
                }
              });

              renderBoot();
              pollStatus();
              pollControlPlane();
            </script>
          </body>
        </html>
        """
    )
    return (
        template.replace("%%BOOT%%", boot_json)
        .replace("%%HEADER%%", VAPOR_HEADER)
        .replace("%%TAGLINE%%", TAGLINE)
        .replace("%%PRODUCT_LINE%%", PRODUCT_LINE)
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse installer_gui CLI arguments (--source, --host, --port, --no-open, --bundle-dir)."""
    parser = argparse.ArgumentParser(
        description="Launch the browser-based guided installer for Vibecrafted."
    )
    parser.add_argument(
        "--source",
        default=default_source_dir(),
        help="Framework source directory to stage from.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port to bind. Use 0 for an ephemeral free port.",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Start the local server without opening a browser tab.",
    )
    parser.add_argument(
        "--bundle-dir",
        default=None,
        help=(
            "Optional path to the pre-built Svelte site bundle (site/dist). "
            "Takes precedence over VIBECRAFTED_SITE_BUNDLE env var and the "
            "default <source>/site/dist lookup."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: start the local control-plane server, open a browser, serve forever."""
    args = parse_args(argv)
    controller = InstallController(args.source, bundle_dir=args.bundle_dir)
    server = InstallerHTTPServer((args.host, args.port), controller)
    host, port = server.server_address[:2]
    host = host.decode() if isinstance(host, bytes) else str(host)
    url = f"http://{host}:{port}/"

    print(f"Vibecrafted control plane ready at {url}")
    print("Press Ctrl-C to stop the local server.")
    if not args.no_open:
        _open_target(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Vibecrafted control plane.")
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
