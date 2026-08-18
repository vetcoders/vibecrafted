"""Materialize package vc-frame config and wire live config projections.

Delivery contract (plan vcframe-config-delivery):
- Source: ``vc_frame_config_source()`` (wheel package data or checkout).
- Install: materialize host-adapted config inside an unpublished generation.
- Wire: point user views through the canonical package-owned
  ``vibecrafted_core/runtime/generated/vc-frame/`` without mutating
  the published generation.
- Ownership: config delivery never creates or flips the runtime-owned
  ``vibecrafted-current`` symlink.
- Views (both must stay in lockstep — operator runtime pins frontier first):
  1. ``$XDG_CONFIG_HOME/vc-frame/{config.kdl,layouts,themes,operator scripts}``
  2. ``$XDG_CONFIG_HOME/vetcoders/frontier/vc-frame/…`` (``VC_FRAME_CONFIG_DIR``)
- Stage-time host adaptation: rewrite every shipped zsh entrypoint and select
  an available clipboard command.
- Operator scripts (Composer / paste-stack / quick-cmd / …) are first-class
  install artifacts, not hand-copied orphans. STALE-FILE copies under frontier
  are backed up and re-wired on every delivery pass.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Imported by module path, not `from . import ...`: the relative form binds the
# sibling through the package barrel, and every static importer graph then
# records an edge back into `vibecrafted_core/__init__.py`. Runtime behaviour is
# identical (the package still initialises first); what changes is that the
# graph names the module that actually owns these symbols.
import vibecrafted_core.vc_frame_staging as _vc_frame_staging

from .frontier_assets import vc_frame_config_source
from .runtime_paths import vibecrafted_tools_home, xdg_config_home
from .vc_frame_staging import (
    materialize_vc_frame_config,
    resolve_clipboard_command,
    resolve_pane_shell,
)

substitute_host_commands = _vc_frame_staging.substitute_host_commands
substitute_pane_shell = _vc_frame_staging.substitute_pane_shell

_FENCE_BEGIN = "# >>> vibecrafted >>>"
_FENCE_END = "# <<< vibecrafted <<<"

# Shipped next to config.kdl. compact-bar / default keybinds prefer frontier paths
# first — if install skips these, an old STALE-FILE on disk shadows the package
# forever (see scaf-260805-triptych runtime diagnosis, 2026-08-07).
OPERATOR_SCRIPT_NAMES: tuple[str, ...] = (
    "auto-theme.sh",
    "vc-composer.sh",
    "paste-stack.sh",
    "copy-scrollback.sh",
    "scrollback-select.sh",
    "vc-quick-cmd.sh",
    "vc-deck.sh",
)

_CORE_VIEW_NAMES: tuple[str, ...] = ("config.kdl", "layouts", "themes")


@dataclass
class WireAction:
    """One recorded step of a delivery plan (a symlink write, backup, or note)."""

    kind: str  # link | backup | skip | remove | stage | flip | note
    path: str
    detail: str = ""


@dataclass
class DeliveryPlan:
    """The full set of actions computed by :func:`plan_delivery` for one host."""

    source: Path
    version_dir: Path
    current_link: Path
    view_root: Path
    channel: str  # store-current | dev-checkout
    dry_run: bool = False
    actions: list[WireAction] = field(default_factory=list)
    pane_shell: str = "zsh"
    clipboard_command: str | None = None

    def render(self) -> str:
        """Render the plan (target paths + recorded actions) as human-readable text."""
        lines = [
            f"source: {self.source}",
            f"version_dir: {self.version_dir}",
            f"current_link: {self.current_link}",
            f"view_root: {self.view_root}",
            f"channel: {self.channel}",
            f"pane_shell: {self.pane_shell}",
            f"clipboard_command: {self.clipboard_command or 'internal'}",
            f"dry_run: {self.dry_run}",
            "actions:",
        ]
        for act in self.actions:
            lines.append(
                f"  - {act.kind}: {act.path}"
                + (f" ({act.detail})" if act.detail else "")
            )
        return "\n".join(lines) + "\n"


def prefer_repo_vc_frame(env: dict[str, str] | None = None) -> bool:
    """True when VIBECRAFTED_PREFER_REPO_VC_FRAME opts into the dev-checkout channel."""
    source = env if env is not None else os.environ
    raw = str(source.get("VIBECRAFTED_PREFER_REPO_VC_FRAME", "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def vc_frame_user_config_dir(home: Path | None = None) -> Path:
    """Directory bare vc-frame reads (respects XDG_CONFIG_HOME)."""
    if home is not None:
        # Sandbox: treat HOME's .config unless XDG is set in env
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            return Path(xdg).expanduser() / "vc-frame"
        return home / ".config" / "vc-frame"
    return xdg_config_home() / "vc-frame"


def tools_current_path(tools_home: Path | None = None) -> Path:
    """Path to the runtime-owned ``vibecrafted-current`` publish symlink."""
    base = tools_home if tools_home is not None else vibecrafted_tools_home()
    return base / "vibecrafted-current"


def classify_view_path(
    path: Path,
    *,
    store_current: Path,
    checkout: Path | None,
) -> str:
    """Classify a view entry: store-current | dev-checkout | foreign | STALE-FILE | DANGLING."""
    if path.is_symlink():
        try:
            target = path.resolve(strict=True)
        except OSError:
            return "DANGLING"
        store_res = store_current.resolve() if store_current.exists() else store_current
        try:
            target.relative_to(store_res)
            return "store-current"
        except ValueError:
            pass
        if checkout is not None:
            check_res = checkout.resolve() if checkout.exists() else checkout
            try:
                target.relative_to(check_res)
                return "dev-checkout"
            except ValueError:
                pass
        return "foreign"
    if path.is_file() or path.is_dir():
        return "STALE-FILE"
    if path.exists():
        return "foreign"
    return "missing"


def _timestamp() -> str:
    """UTC timestamp used to make backup filenames collision-resistant."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _runtime_payload_root(runtime_root: Path) -> Path:
    """Return the sole package-owned runtime tree inside a generation."""
    return runtime_root / "vibecrafted-core" / "vibecrafted_core" / "runtime"


def _complete_runtime_root(current: Path, *, dry_run: bool) -> Path:
    """Resolve the single runtime owner and refuse config-only substitutes."""
    if dry_run and not (current.exists() or current.is_symlink()):
        return current
    try:
        runtime_root = current.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(
            f"canonical runtime pointer is unavailable at {current}; "
            "stage the full distribution before vc-frame config"
        ) from exc
    required = (
        runtime_root / "Makefile",
        runtime_root / "vibecrafted-core",
        _runtime_payload_root(runtime_root) / "scripts",
    )
    missing = [
        str(path.relative_to(runtime_root)) for path in required if not path.exists()
    ]
    if missing:
        raise RuntimeError(
            f"canonical runtime at {runtime_root} is incomplete; missing: "
            + ", ".join(missing)
        )
    return runtime_root


def _require_materialized_config(runtime_root: Path, *, dry_run: bool) -> Path:
    """Return the published generation's pre-materialized vc-frame config dir.

    Raises when the published runtime lacks a complete ``config.kdl``/``layouts``/
    ``themes`` set — the store-current channel refuses to wire a partial config.
    """
    generated = _runtime_payload_root(runtime_root) / "generated" / "vc-frame"
    if dry_run and not runtime_root.exists():
        return generated
    required = (
        generated / "config.kdl",
        generated / "layouts",
        generated / "themes",
    )
    missing = [
        str(path.relative_to(runtime_root))
        for path in required
        if not (path.is_file() if path.suffix else path.is_dir())
    ]
    if missing:
        raise RuntimeError(
            f"published runtime at {runtime_root} has no complete pre-materialized "
            "vc-frame config; reinstall the full distribution; missing: "
            + ", ".join(missing)
        )
    return generated


def _atomic_view_symlink(target: Path, view_path: Path) -> None:
    """Point ``view_path`` at ``target`` via write-temp-then-rename (no partial state)."""
    view_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = view_path.parent / (
        f".{view_path.name}.vibecrafted-{os.getpid()}-{os.urandom(6).hex()}"
    )
    temporary.symlink_to(target)
    try:
        os.replace(temporary, view_path)
    finally:
        temporary.unlink(missing_ok=True)


def _wire_one(
    view_path: Path,
    target: Path,
    *,
    force: bool,
    dry_run: bool,
    actions: list[WireAction],
    store_current: Path,
    checkout: Path | None,
) -> None:
    """Wire one view path to ``target``, backing up STALE-FILEs and skipping foreign links."""
    channel = (
        classify_view_path(view_path, store_current=store_current, checkout=checkout)
        if view_path.exists() or view_path.is_symlink()
        else "missing"
    )
    if channel in {"store-current", "dev-checkout"} and not force:
        # Healthy: leave alone if already points at the intended target
        try:
            if view_path.is_symlink() and view_path.resolve() == target.resolve():
                actions.append(WireAction("skip", str(view_path), channel))
                return
        except OSError:
            pass
    if channel == "foreign" and view_path.is_symlink() and not force:
        actions.append(
            WireAction("skip", str(view_path), "user-managed foreign symlink")
        )
        return

    backup: Path | None = None
    if view_path.is_symlink() and channel == "DANGLING":
        actions.append(WireAction("remove", str(view_path), "dangling"))
    elif channel == "STALE-FILE" or (view_path.exists() and not view_path.is_symlink()):
        backup = Path(
            f"{view_path}.stale.{_timestamp()}-{os.getpid()}-{os.urandom(4).hex()}"
        )
        actions.append(WireAction("backup", str(view_path), f"-> {backup.name}"))
        if not dry_run:
            view_path.rename(backup)
    elif view_path.is_symlink() and force:
        actions.append(WireAction("remove", str(view_path), "force rewire"))

    actions.append(WireAction("link", str(view_path), f"-> {target}"))
    if dry_run:
        return
    try:
        _atomic_view_symlink(target, view_path)
    except BaseException:
        if (
            backup is not None
            and backup.exists()
            and not (view_path.exists() or view_path.is_symlink())
        ):
            backup.rename(view_path)
        raise


def plan_delivery(
    *,
    home: Path | None = None,
    tools_home: Path | None = None,
    version: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    force_frontier: bool = False,
    prefer_repo: bool | None = None,
    path_env: str | None = None,
) -> DeliveryPlan:
    """Compute (and, unless ``dry_run``, apply) the vc-frame config delivery plan.

    Re-materializes host-adapted config from the package source into the published
    generation's package-owned ``runtime/generated/vc-frame`` (never mutating
    the source itself or the ``vibecrafted-current`` owner symlink), then wires
    both the legacy view and frontier projections to point at that generation.
    """
    source = vc_frame_config_source()
    tools = tools_home if tools_home is not None else vibecrafted_tools_home()
    # Isolate tools under sandbox when tools_home not overridden
    if home is not None and tools_home is None:
        tools = home / ".local" / "share" / "vibecrafted" / "tools"
    current = tools / "vibecrafted-current"
    view_root = vc_frame_user_config_dir(home)
    use_repo = prefer_repo if prefer_repo is not None else prefer_repo_vc_frame()
    channel = "dev-checkout" if use_repo else "store-current"
    pane_shell = resolve_pane_shell(path_env)
    clipboard_command = resolve_clipboard_command(path_env)
    runtime_root = (
        source if use_repo else _complete_runtime_root(current, dry_run=dry_run)
    )
    plan = DeliveryPlan(
        source=source,
        version_dir=runtime_root,
        current_link=current,
        view_root=view_root,
        channel=channel,
        dry_run=dry_run,
        pane_shell=pane_shell,
        clipboard_command=clipboard_command,
    )

    # The mirrored distribution exposes package data through a symlink back to
    # ``<runtime>/config/vc-frame``.  Never stage *into* that package path:
    # deleting the previous generated tree would then delete the source.
    # Re-materialize only into the package-owned runtime/generated/vc-frame.
    if not use_repo:
        _require_materialized_config(runtime_root, dry_run=dry_run)
        plan.actions.append(
            WireAction(
                "note",
                str(current),
                f"preserve runtime owner (requested config version {version or 'current'})",
            )
        )
        # Re-materialize into the published generation's generated/ tree so
        # `vibecrafted config install` refreshes operator scripts + Super binds
        # without a full tools republish. Does not flip vibecrafted-current.
        generated = _runtime_payload_root(current) / "generated" / "vc-frame"
        if dry_run:
            plan.actions.append(
                WireAction(
                    "note",
                    str(generated),
                    "would re-materialize host-adapted config from package source",
                )
            )
        else:
            materialize_vc_frame_config(
                source,
                generated,
                pane_shell=pane_shell,
                clipboard_command=clipboard_command,
            )
            plan.actions.append(
                WireAction(
                    "stage",
                    str(generated),
                    "re-materialized host-adapted config from package source",
                )
            )
        base = generated
    else:
        plan.actions.append(
            WireAction("note", str(source), "dev-checkout: skip stage copy")
        )
        base = source

    checkout = source if use_repo else None
    # Ownership is the complete current runtime, while exact-target equality
    # decides whether an existing owned link is current or needs migration.
    store_anchor = current
    store_current = store_anchor if not use_repo else current
    # Two projections: legacy view + frontier (the path VC_FRAME_CONFIG_DIR
    # pins via _vetcoders_pin_vc_frame_config_dir). Wiring only the view left
    # frontier as STALE-FILE forever — scripts/config never refreshed.
    projection_roots = (
        view_root,
        frontier_root(home) / "vc-frame",
    )
    managed_frontier = frontier_root(home) / "vc-frame"
    for projection in projection_roots:
        _wire_projection(
            projection,
            base,
            force=force or (force_frontier and projection == managed_frontier),
            dry_run=dry_run,
            actions=plan.actions,
            store_current=store_current,
            checkout=checkout,
        )
    return plan


def _wire_projection(
    projection_root: Path,
    base: Path,
    *,
    force: bool,
    dry_run: bool,
    actions: list[WireAction],
    store_current: Path,
    checkout: Path | None,
) -> None:
    """Wire one config projection (view or frontier) from the staged base."""
    for name in _CORE_VIEW_NAMES:
        target = base / name
        if not target.exists() and not dry_run:
            continue
        _wire_one(
            projection_root / name,
            target,
            force=force,
            dry_run=dry_run,
            actions=actions,
            store_current=store_current,
            checkout=checkout,
        )
    for name in OPERATOR_SCRIPT_NAMES:
        target = base / name
        if not target.exists() and not dry_run:
            # dry_run still records the intended link so plans stay auditable
            if dry_run:
                actions.append(
                    WireAction(
                        "note",
                        str(projection_root / name),
                        f"operator script absent from source base: {name}",
                    )
                )
            continue
        _wire_one(
            projection_root / name,
            target,
            force=force,
            dry_run=dry_run,
            actions=actions,
            store_current=store_current,
            checkout=checkout,
        )


def stage_vc_frame_config(
    *,
    home: Path | None = None,
    tools_home: Path | None = None,
    version: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    prefer_repo: bool | None = None,
    path_env: str | None = None,
) -> DeliveryPlan:
    """Compatibility entrypoint: wire views without mutating the live runtime."""
    return plan_delivery(
        home=home,
        tools_home=tools_home,
        version=version,
        dry_run=dry_run,
        force=force,
        prefer_repo=prefer_repo,
        path_env=path_env,
    )


def wire_vc_frame_config(
    *,
    home: Path | None = None,
    tools_home: Path | None = None,
    version: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    force_frontier: bool = False,
    prefer_repo: bool | None = None,
    path_env: str | None = None,
) -> DeliveryPlan:
    """Wire user views without changing the published runtime generation."""
    return plan_delivery(
        home=home,
        tools_home=tools_home,
        version=version,
        dry_run=dry_run,
        force=force,
        force_frontier=force_frontier,
        prefer_repo=prefer_repo,
        path_env=path_env,
    )


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
