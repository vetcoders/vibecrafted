"""Filesystem layout for vibecrafted's runtime homes, staged tools, and launchers.

Every path here is env-overridable (``VIBECRAFTED_HOME`` / ``XDG_*`` / etc.) so
callers never hardcode a user's layout; ``resolve_env_path`` is the shared knob.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from pathlib import Path

# Direct children of ``vibecrafted_home()`` have one product-wide ownership
# class.  Keep this grammar here: the host-Python installer loads this module by
# file, while the runtime imports it normally.  Unknown names are intentionally
# absent and therefore preserved by uninstall discovery.
VIBECRAFTED_HOME_RUNTIME_STATE = frozenset(
    {
        "control_plane",
        "server",
        "foundation",
        "locks",
        "runtime",
        "install-transactions",
        "recovery",
        "tmp",
        "logs",
        ".vc-install.json",
        "START_HERE.md",
        "install.log",
        ".DS_Store",
    }
)

VIBECRAFTED_HOME_FOUNDER_DATA = frozenset(
    {
        "artifacts",
        "inbox",
        "reports",
        "plans",
        "prompts",
        "specs",
        "backups",
        "worktrees",
        "monitor",
        "charter",
        "trust",
        "loctree",
        "vibecrafted",
        ".git",
        ".loctree",
    }
)


def classify_vibecrafted_home_child(child: str | Path) -> str:
    """Classify one direct state-home child for product uninstall.

    ``runtime-state`` is reproducible process/control state and is removed.
    ``founder-data`` is durable human/generated work and is preserved.
    Every unrecognized name is ``unknown`` and is preserved visibly.
    """

    name = Path(child).name
    if name in VIBECRAFTED_HOME_RUNTIME_STATE:
        return "runtime-state"
    if name in VIBECRAFTED_HOME_FOUNDER_DATA:
        return "founder-data"
    return "unknown"


def read_version_file(root: str | Path) -> str:
    """Read ``<root>/VERSION`` verbatim, or ``"unknown"`` when it is absent."""
    version_file = Path(root) / "VERSION"
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip()
    return "unknown"


def version_is_stamped(version: str) -> bool:
    """Install contract: ``X.Y.Z+gSHORTSHA`` (see docs/INSTALL.md).

    Bare ``X.Y.Z`` is not an install identity — it is either an unstamped
    living-tree checkout or a broken editable install that must not win PATH.
    """
    if not version or version == "unknown":
        return False
    # Accept +gabc1234 style only (not arbitrary local labels).
    plus = version.find("+g")
    if plus < 0:
        return False
    sha = version[plus + 2 :]
    return bool(sha) and all(c in "0123456789abcdefABCDEF" for c in sha)


def read_staged_tools_version() -> str:
    """VERSION stamped by ``make install`` into tools/vibecrafted-current.

    Prefer the root VERSION, then the package-local file next to the staged
    ``vibecrafted_core`` package (mirrors how the live package reads itself).
    """
    current = vibecrafted_tools_home() / "vibecrafted-current"
    for candidate in (
        current / "VERSION",
        current / "vibecrafted-core" / "vibecrafted_core" / "VERSION",
        current / "vibecrafted-core" / "VERSION",
    ):
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
    return "unknown"


def resolve_env_path(name: str, default: Path) -> Path:
    """Return ``$name`` expanded to a ``Path`` if set, else the expanded default."""
    raw = os.environ.get(name)
    if raw:
        return Path(raw).expanduser()
    return default.expanduser()


def xdg_config_home() -> Path:
    """``$XDG_CONFIG_HOME`` or ``~/.config``."""
    return resolve_env_path("XDG_CONFIG_HOME", Path.home() / ".config")


def xdg_data_home() -> Path:
    """``$XDG_DATA_HOME`` or ``~/.local/share``."""
    return resolve_env_path("XDG_DATA_HOME", Path.home() / ".local" / "share")


def vibecrafted_home() -> Path:
    """``$VIBECRAFTED_HOME`` or ``~/.vibecrafted`` — the control-plane root."""
    if os.environ.get("VIBECRAFTED_HOME"):
        return Path(os.environ["VIBECRAFTED_HOME"]).expanduser()
    return Path.home() / ".vibecrafted"


def run_signal_socket_path(run_id: str) -> Path:
    """Short, home-scoped Unix socket path for one dispatcher run.

    Darwin limits ``sun_path`` to 104 bytes.  Hashing both the configured
    Vibecrafted home and run id keeps isolated test/operator homes distinct
    without leaking long workspace paths into the socket address.
    """
    identity = f"{vibecrafted_home().resolve()}\0{run_id}".encode()
    digest = hashlib.sha256(identity).hexdigest()[:24]
    return Path("/tmp") / f"vc-cp-{os.getuid()}" / f"{digest}.sock"


def vibecrafted_backups_home() -> Path:
    """Where the installer stashes pre-install backups, under the home root."""
    return vibecrafted_home() / "backups" / "installer"


def vibecrafted_runtime_home() -> Path:
    """``$VIBECRAFTED_RUNTIME_HOME`` or ``<xdg_data_home>/vibecrafted``."""
    return resolve_env_path("VIBECRAFTED_RUNTIME_HOME", xdg_data_home() / "vibecrafted")


def vibecrafted_tools_home() -> Path:
    """``$VIBECRAFTED_TOOLS_HOME`` or ``<runtime_home>/tools`` — staged installs."""
    return resolve_env_path(
        "VIBECRAFTED_TOOLS_HOME",
        vibecrafted_runtime_home() / "tools",
    )


def vibecrafted_runtime_bin() -> Path:
    """``$VIBECRAFTED_RUNTIME_BIN`` or ``<runtime_home>/bin``."""
    return resolve_env_path(
        "VIBECRAFTED_RUNTIME_BIN", vibecrafted_runtime_home() / "bin"
    )


def selected_runtime_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return an environment coherent with an explicitly selected runtime.

    A public runtime launcher selects an immutable generation by exporting
    ``VIBECRAFTED_RUNTIME_ROOT``.  That selection owns its executable bin and
    interpreter; inherited ``VIBECRAFTED_RUNTIME_BIN`` / ``VIBECRAFTED_PYTHON``
    values are merely legacy process state and must not split a child across
    generations.  A runtime-bin override without a selected root remains
    supported for source and test lanes.

    Do not replace a selected root with the mutable runtime-home pointer.  A
    malformed selected root is an identity failure, not permission to fall
    back to whichever generation happens to be active.
    """

    env = dict(os.environ if environment is None else environment)
    raw_root = str(env.get("VIBECRAFTED_RUNTIME_ROOT", "")).strip()
    if not raw_root:
        return env

    root = Path(raw_root).expanduser()
    if not root.is_absolute():
        raise ValueError(
            "selected runtime root must be an absolute immutable generation path: "
            f"{raw_root}"
        )
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"selected runtime root is unavailable: {raw_root}") from exc

    version = read_version_file(root)
    runtime_bin = root / "bin"
    runtime_python = runtime_bin / "python3"
    if not root.is_dir() or not version_is_stamped(version):
        raise ValueError(
            f"selected runtime root is not an immutable stamped generation: {root}"
        )
    if (
        not runtime_bin.is_dir()
        or not runtime_python.is_file()
        or not os.access(runtime_python, os.X_OK)
    ):
        raise ValueError(
            "selected runtime generation is incomplete; expected executable "
            f"{runtime_python}"
        )

    selected = str(root)
    env["VIBECRAFTED_RUNTIME_ROOT"] = selected
    env["VIBECRAFTED_RUNTIME_BIN"] = str(runtime_bin)
    env["VIBECRAFTED_PYTHON"] = str(runtime_python)
    env["VIBECRAFTED_ROOT"] = selected
    env["VIBECRAFTED_CORE_DIR"] = str(root / "vibecrafted-core")
    return env


def resolve_operator_launch_root(
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Root for `vibecrafted review` and siblings when the operator is at $HOME.

    App shells often start in the home directory. A selected WES workspace is
    the product root; reviewing `$HOME` is never a useful default.
    """

    environ = os.environ if env is None else env
    here = (cwd or Path.cwd()).expanduser().resolve()
    raw_home = str(environ.get("HOME") or "").strip()
    home = Path(raw_home).expanduser().resolve() if raw_home else Path.home().resolve()
    workspace = str(environ.get("VIBECRAFTED_WORKSPACE_ROOT") or "").strip()
    in_git = (here / ".git").exists() or any(
        (parent / ".git").exists() for parent in here.parents
    )
    if workspace and (here == home or not in_git):
        selected = Path(workspace).expanduser().resolve()
        if selected.is_dir():
            return selected
    return here


def is_operator_home_root(
    root: str | Path,
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    """True when ``root`` is the operator home directory (never a useful launch)."""

    environ = os.environ if env is None else env
    raw_home = str(environ.get("HOME") or "").strip()
    home = Path(raw_home).expanduser().resolve() if raw_home else Path.home().resolve()
    try:
        return Path(root).expanduser().resolve() == home
    except OSError:
        return False


def vibecrafted_launcher_bin() -> Path:
    """``$VIBECRAFTED_LAUNCHER_BIN`` or ``~/.local/bin`` — where shims land on PATH."""
    return resolve_env_path("VIBECRAFTED_LAUNCHER_BIN", Path.home() / ".local" / "bin")


def _owned_runtime_homes(environment: Mapping[str, str]) -> list[str]:
    """Owned runtime roots whose generation bins are private carriers.

    Anchoring on real owned roots — rather than a floating
    ``*/vibecrafted/releases/*/bin`` glob — is what lets a custom
    ``VIBECRAFTED_RUNTIME_HOME`` sanitize correctly while an unrelated
    lookalike user directory is preserved.
    """

    raw_home = str(environment.get("HOME", "")).strip()
    home = Path(raw_home).expanduser() if raw_home else Path.home()
    raw_xdg_data = str(environment.get("XDG_DATA_HOME", "")).strip()
    xdg_data = (
        Path(raw_xdg_data).expanduser() if raw_xdg_data else home / ".local/share"
    )
    raw_runtime_home = str(environment.get("VIBECRAFTED_RUNTIME_HOME", "")).strip()

    homes: list[str] = []
    if raw_runtime_home:
        homes.append(str(Path(raw_runtime_home).expanduser()).rstrip("/"))
    homes.append(str(xdg_data / "vibecrafted").rstrip("/"))
    return homes


def _is_owned_generation_bin(entry: str, environment: Mapping[str, str]) -> bool:
    """True for the selected root's bin or ``<owned home>/releases/<gen>/bin``."""

    candidate = entry.rstrip("/")
    if not candidate.endswith("/bin"):
        return False
    generation = candidate[: -len("/bin")]

    selected = str(environment.get("VIBECRAFTED_RUNTIME_ROOT", "")).strip().rstrip("/")
    if selected and generation == selected:
        return True

    for runtime_home in _owned_runtime_homes(environment):
        prefix = f"{runtime_home}/releases/"
        if not generation.startswith(prefix):
            continue
        leaf = generation[len(prefix) :]
        if leaf and "/" not in leaf:
            return True
    return False


def _host_agent_bin_dirs(environment: Mapping[str, str]) -> list[Path]:
    """Host CLI + public launcher directories, appended for minimal PATHs.

    Keep this list in lockstep with
    ``runtime/shell/lib/core.sh:_vetcoders_host_agent_bin_dirs`` and
    ``runtime/scripts/lib/util.sh:spawn_host_agent_bin_dirs``.
    """

    raw_home = str(environment.get("HOME", "")).strip()
    home = Path(raw_home).expanduser() if raw_home else Path.home()
    return [
        home / ".local/bin",
        home / ".cargo/bin",
        home / "tools/scripts",
        Path("/opt/homebrew/bin"),
        Path("/opt/homebrew/sbin"),
        Path("/usr/local/bin"),
        Path("/usr/bin"),
        Path("/bin"),
        Path("/usr/sbin"),
        Path("/sbin"),
    ]


def agent_tool_search_path(environment: Mapping[str, str] | None = None) -> str:
    """PATH for a detached provider process: Founder order, no private carrier.

    Launchd and other supervisors intentionally provide a minimal environment,
    so provider discovery must not depend on interactive shell startup — the
    host directories above are therefore APPENDED as a discovery suffix.  The
    inherited PATH is not discarded: a public child resolves the Founder's own
    ``aicx`` / ``loct`` / ``prview`` / ``screenscribe`` and any unrelated custom
    entry from user paths, in the user's own order.

    Removed is exactly one class: a Vibecrafted-owned generation bin.  Internal
    dependencies reach the private carrier through explicit owner paths
    (``VIBECRAFTED_RUNTIME_BIN`` / ``VIBECRAFTED_PYTHON``), so a missing host
    tool stays missing instead of silently resolving a bundled — possibly
    stale — private copy.

    Keep this in lockstep with
    ``runtime/scripts/lib/util.sh:spawn_prepend_agent_tool_paths`` and
    ``runtime/shell/lib/core.sh:_vetcoders_path_with_bundled_bin_priority``.
    """

    env = selected_runtime_environment(environment)
    inherited = str(env.get("PATH", os.defpath))

    resolved: list[str] = []
    seen: set[str] = set()
    for entry in inherited.split(os.pathsep):
        if not entry or entry in seen:
            continue
        if _is_owned_generation_bin(entry, env):
            continue
        seen.add(entry)
        resolved.append(entry)

    for candidate in _host_agent_bin_dirs(env):
        text = str(candidate)
        if text in seen or not candidate.is_dir():
            continue
        if _is_owned_generation_bin(text, env):
            continue
        seen.add(text)
        resolved.append(text)

    return os.pathsep.join(resolved)
