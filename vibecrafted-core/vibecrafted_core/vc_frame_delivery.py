"""Read-only product config paths and explicit host-shell onboarding.

The Runtime Pack installer is the sole product configuration writer. Shipped
host-adapted defaults stay inside their immutable generation; active physical
copies and preferences live under XDG_CONFIG_HOME/vibecrafted.
"""

from __future__ import annotations

import os
from pathlib import Path

from .runtime_paths import vibecrafted_tools_home, xdg_config_home

_FENCE_BEGIN = "# >>> vibecrafted >>>"
_FENCE_END = "# <<< vibecrafted <<<"

# Managed assets installed beside config.kdl, never user preference stores.
OPERATOR_SCRIPT_NAMES: tuple[str, ...] = (
    "auto-theme.sh",
    "vc-composer.sh",
    "paste-stack.sh",
    "copy-scrollback.sh",
    "scrollback-select.sh",
    "vc-quick-cmd.sh",
    "vc-deck.sh",
    "pane-python",
    "vc-agent-workshop.py",
    "vc-start-here.py",
)


def vc_frame_user_config_dir(home: Path | None = None) -> Path:
    """The one product-owned vc-frame config directory."""
    if home is not None:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            return Path(xdg).expanduser() / "vibecrafted" / "vc-frame"
        return home / ".config" / "vibecrafted" / "vc-frame"
    return xdg_config_home() / "vibecrafted" / "vc-frame"


def tools_current_path(tools_home: Path | None = None) -> Path:
    """Path to the runtime-owned ``vibecrafted-current`` publish symlink."""
    base = tools_home if tools_home is not None else vibecrafted_tools_home()
    return base / "vibecrafted-current"


# ---------------------------------------------------------------------------
# Host zshrc PATH onboarding (W1-B)
# ---------------------------------------------------------------------------

_ZSHRC_TEMPLATE = """\
# Vibecrafted launcher path. Product helpers are loaded only by vc-start.
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
"""

_FENCED_BLOCK = f"""\
{_FENCE_BEGIN}
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
{_FENCE_END}
"""


def zshrc_template_text() -> str:
    """Return the host zshrc template (also loadable from package runtime)."""
    try:
        from .package_resources import resource_path

        path = resource_path("runtime", "templates", "zshrc-host.template")
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    # Checkout fallback
    here = Path(__file__).resolve().parent
    candidate = here / "runtime" / "templates" / "zshrc-host.template"
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    return _ZSHRC_TEMPLATE


def ensure_zshrc(home: Path | None = None, *, dry_run: bool = False) -> dict[str, str]:
    """Idempotently add only the launcher PATH to zshrc after explicit invocation."""
    root = home if home is not None else Path.home()
    zshrc = root / ".zshrc"
    result = {"path": str(zshrc), "action": "noop"}
    if not zshrc.exists():
        result["action"] = "create"
        if not dry_run:
            zshrc.write_text(zshrc_template_text(), encoding="utf-8")
        return result
    text = zshrc.read_text(encoding="utf-8")
    if _FENCE_BEGIN in text and _FENCE_END in text:
        result["action"] = "already_present"
        return result
    result["action"] = "append_fence"
    if not dry_run:
        suffix = "" if text.endswith("\n") else "\n"
        zshrc.write_text(text + suffix + "\n" + _FENCED_BLOCK, encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# Frontier zombies (W2-B helpers usable from doctor)
# ---------------------------------------------------------------------------


def frontier_root(home: Path | None = None) -> Path:
    """Root of the frontier config projection (``VC_FRAME_CONFIG_DIR`` target)."""
    if home is not None:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            return Path(xdg).expanduser() / "vetcoders" / "frontier"
        return home / ".config" / "vetcoders" / "frontier"
    return xdg_config_home() / "vetcoders" / "frontier"


def list_dangling_frontier_links(root: Path | None = None) -> list[Path]:
    """Recursively find symlinks under the frontier root whose target no longer exists."""
    base = root if root is not None else frontier_root()
    dangling: list[Path] = []
    if not base.exists():
        return dangling
    for path in base.rglob("*"):
        if path.is_symlink():
            try:
                path.resolve(strict=True)
            except OSError:
                dangling.append(path)
    return dangling


def remove_dangling_frontier_links(
    root: Path | None = None, *, dry_run: bool = False
) -> list[Path]:
    """Delete dangling frontier symlinks (or, if ``dry_run``, just report them)."""
    removed: list[Path] = []
    for path in list_dangling_frontier_links(root):
        removed.append(path)
        if not dry_run:
            path.unlink(missing_ok=True)
    return removed
