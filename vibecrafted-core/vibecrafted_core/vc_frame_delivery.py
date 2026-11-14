"""Product config paths, retained project layouts and host-shell onboarding.

The Runtime Pack installer is the sole base configuration writer. Shipped
host-adapted defaults stay inside their immutable generation; active physical
copies and preferences live under ~/.config/vibecrafted. The workspace create
owner may add immutable selected-layout snapshots to that writable product view.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path

from .runtime_paths import vibecrafted_tools_home

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
    """The one product-owned vc-frame config directory.

    POSIX: ``~/.config/vibecrafted/vc-frame`` (or ``<home>/.config/...`` when
    tests pass an explicit home). Windows: ``%APPDATA%\\Vibecrafted\\vc-frame``.
    """
    if home is not None:
        return home / ".config" / "vibecrafted" / "vc-frame"
    from .runtime_paths import is_windows, vibecrafted_product_config_home

    if is_windows():
        return vibecrafted_product_config_home() / "vc-frame"
    return Path.home() / ".config" / "vibecrafted" / "vc-frame"


def _physical_layout_bytes(path: Path) -> bytes:
    if not path.is_absolute() or any(
        item.is_symlink() for item in (path, *path.parents)
    ):
        raise OSError(f"project layout must be an absolute physical file: {path}")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
    )
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OSError(f"project layout must be a regular file: {path}")
        return stream.read()


def _publish_layout_bytes(path: Path, contents: bytes) -> None:
    """Publish complete bytes without replacing an existing inode or its content."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _physical_layout_bytes(path) != contents:
                raise OSError(f"conflicting retained project layout: {path}") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish_selected_project_layout(layout: Path) -> Path:
    """Retain an exact layout/swap pair in the existing writable product view.

    Called only inside the canonical missing-seat create lock. Never refresh
    installed defaults, create a missing installation, or write a Runtime Pack.
    The public Frame wrapper keeps its normal flat installed-layout guard.
    """
    layouts = vc_frame_user_config_dir() / "layouts"
    config = layouts.parent / "config.kdl"
    for item in (layouts, *layouts.parents):
        if item.is_symlink() or (item / "runtime-manifest.json").exists():
            raise OSError(f"unsafe product layout directory: {item}")
    if not layouts.is_dir() or config.is_symlink() or not config.is_file():
        raise OSError(f"installed product config/layouts missing: {layouts.parent}")
    main = _physical_layout_bytes(layout)
    swap = layout.with_suffix(".swap.kdl")
    swap_bytes = (
        _physical_layout_bytes(swap) if swap.exists() or swap.is_symlink() else None
    )
    # An already installed layout remains the exact selected file. Validate its
    # sibling first so a symlinked swap cannot escape the same product guard.
    if layout.parent == layouts:
        return layout
    digest = hashlib.sha256(
        len(main).to_bytes(8, "big")
        + main
        + (b"\x00" if swap_bytes is None else b"\x01" + swap_bytes)
    ).hexdigest()
    published = layouts / f"project-{digest}.kdl"
    # Publish the companion before exposing the primary path to Frame. Both
    # names include the pair digest, so distinct swap input cannot alias it.
    if swap_bytes is not None:
        _publish_layout_bytes(published.with_suffix(".swap.kdl"), swap_bytes)
    elif (
        published.with_suffix(".swap.kdl").exists()
        or published.with_suffix(".swap.kdl").is_symlink()
    ):
        raise OSError(f"unexpected retained project swap layout: {published}")
    _publish_layout_bytes(published, main)
    return published


def tools_current_path(tools_home: Path | None = None) -> Path:
    """Path to the runtime-owned ``vibecrafted-current`` publish symlink."""
    base = tools_home if tools_home is not None else vibecrafted_tools_home()
    return base / "vibecrafted-current"


# ---------------------------------------------------------------------------
# Host zshrc PATH onboarding (W1-B)
# ---------------------------------------------------------------------------

_ZSHRC_TEMPLATE = """\
# Vibecrafted launcher path. Product helpers are loaded only by vc-start.
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
"""

_FENCED_BLOCK = f"""\
{_FENCE_BEGIN}
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
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
