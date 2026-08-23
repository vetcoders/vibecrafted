"""Canonical maintenance of the Loctree perception layer during agent runs.

Where :mod:`vibecrafted_core.capabilities` is *read-only by contract* — it only
probes whether ``loct`` / ``loctree-mcp`` are runnable — this module owns the
one mutation the runtime is allowed to make on the perception layer: keeping
**exactly one** scan-only ``loct watch`` alive per repository root.

The single-instance guarantee is *not* invented here. ``loct watch`` already
enforces it with a kernel-level advisory lock at ``<root>/.loctree/scan.lock``
keyed by the canonical snapshot root; a second watcher for the same root exits
with ``EX_TEMPFAIL`` (75). This module's job is therefore narrow and honest:

* maintain, never multiply — before spawning, cheaply detect a live watcher via
  the same lock file (``watcher_running``); if one already holds the root, skip;
* handle contention *gently* — if our spawn loses the race anyway, the child
  exits 75 and we report ``already_running`` instead of crashing;
* never make a watcher own MCP transport — agent MCP routing belongs to the
  shared runtime supervisor, not to every repository root.

The historical HTTP helpers remain available for explicit callers while the
runtime migrates, but they are not the watcher default and are never selected
implicitly by an agent launch.

Everything here is best-effort: a missing or broken ``loct`` yields an
``unavailable`` outcome, never an exception. The runtime hot path treats a
failed perception bootstrap as a no-op, not a fatal error.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised on every supported (POSIX) platform
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl; flock probe is skipped
    fcntl = None  # type: ignore[assignment]

from .capabilities import _resolve_executable as _resolve_foundation
from .clock import utc_now_iso

# ---------------------------------------------------------------------------
# Transport canon
# ---------------------------------------------------------------------------

#: Per-root maintenance is scan-only. MCP process ownership is deliberately
#: separate and remains with the client's configured transport or an explicit
#: supervisor; this module does not pretend that such a supervisor is present.
DEFAULT_WATCH_MODE = "scan"

#: Compatibility default for callers that explicitly ask this legacy helper to
#: build a Loctree MCP entry. Runtime launches must obtain MCP routing from an
#: explicit caller instead of inferring it from a repository watcher.
DEFAULT_MCP_TRANSPORT = "http"

#: Loopback host the co-spawned MCP binds on. ``loct watch --http`` defaults to
#: a loopback-only listener; we never expose it on the network.
DEFAULT_MCP_HOST = "127.0.0.1"

#: Base port. Also ``loct watch --http``'s own default, so a root that hashes to
#: offset 0 lands exactly on loctree's default — the single-root happy path.
DEFAULT_MCP_PORT = 5174

#: Streamable-HTTP MCP surface is mounted at ``/mcp`` on the bind address.
MCP_HTTP_PATH = "/mcp"

#: Width of the per-root port window: ports live in ``[5174, 5174 + span)``.
_PORT_SPAN = 800

#: ``loct watch`` exit code when the per-root lock is held by another watcher
#: (``EX_TEMPFAIL``). This is the "already maintained" signal, not a failure.
EXIT_LOCK_CONTENDED = 75

# A spawner signature so tests can inject deterministic processes without
# launching a real ``loct watch``. The returned object only needs ``pid`` and a
# ``poll()`` returning the exit code or ``None`` while still running.
Spawner = Callable[[Sequence[str]], "subprocess.Popen[bytes]"]


# ---------------------------------------------------------------------------
# Root / port / endpoint derivation
# ---------------------------------------------------------------------------


def canonical_root(root: str | Path) -> str:
    """Resolve a root the way the watch lock is keyed.

    ``.``, ``./``, a trailing slash and an absolute path all collapse to the
    same canonical string, matching ``loct watch``'s own lock key so our
    detection agrees with loctree's enforcement.
    """
    return str(Path(root).expanduser().resolve())


def port_for_root(
    root: str | Path, *, base: int = DEFAULT_MCP_PORT, span: int = _PORT_SPAN
) -> int:
    """Deterministic, collision-resistant MCP port for one root.

    The same root always maps to the same port (so a run's MCP URL matches the
    listening watcher); different roots are spread across ``[base, base+span)``
    so simultaneous per-root watchers do not fight over ``5174``.
    """
    digest = zlib.crc32(canonical_root(root).encode("utf-8"))
    return base + (digest % span)


def mcp_endpoint(
    root: str | Path, *, host: str = DEFAULT_MCP_HOST, port: int | None = None
) -> str:
    """Streamable-HTTP MCP URL a run should dial for this root."""
    resolved = port if port is not None else port_for_root(root)
    return f"http://{host}:{resolved}{MCP_HTTP_PATH}"


def scan_lock_path(root: str | Path) -> Path:
    """Path of loctree's per-root watch lock (``<root>/.loctree/scan.lock``)."""
    return Path(canonical_root(root)) / ".loctree" / "scan.lock"


def loctree_mcp_config_entry(
    root: str | Path,
    *,
    transport: str = DEFAULT_MCP_TRANSPORT,
    host: str = DEFAULT_MCP_HOST,
    port: int | None = None,
) -> dict[str, Any]:
    """MCP server config entry for ``loctree`` for one run.

    The canon (``transport="http"``) points the run at the shared streamable
    server. The ``stdio`` form is kept only as an explicit legacy fallback.
    """
    if transport == "http":
        return {"type": "http", "url": mcp_endpoint(root, host=host, port=port)}
    return {"command": "loctree-mcp", "args": [], "env": {}}


def mcp_servers_config(
    root: str | Path,
    *,
    transport: str = DEFAULT_MCP_TRANSPORT,
    host: str = DEFAULT_MCP_HOST,
    port: int | None = None,
) -> dict[str, Any]:
    """Full ``{"mcpServers": {...}}`` block wiring ``loctree`` for one run."""
    return {
        "mcpServers": {
            "loctree": loctree_mcp_config_entry(
                root, transport=transport, host=host, port=port
            )
        }
    }


def default_loctree_mcp_config_entry() -> dict[str, Any]:
    """Root-less streamable-HTTP entry for static templates (marketplace bundle).

    A shipped template cannot know the run root, so it documents the canon at the
    default port. vibecrafted's own per-run wiring uses the root-aware
    :func:`loctree_mcp_config_entry` instead.
    """
    return {
        "type": "http",
        "url": f"http://{DEFAULT_MCP_HOST}:{DEFAULT_MCP_PORT}{MCP_HTTP_PATH}",
    }


# ---------------------------------------------------------------------------
# Single-instance detection + watch command
# ---------------------------------------------------------------------------


def watcher_running(root: str | Path) -> bool:
    """Best-effort: is a live ``loct watch`` already holding this root?

    Probes loctree's own ``scan.lock`` with a non-blocking advisory lock — the
    same kernel mechanism loctree uses. A held lock means a watcher is alive; an
    acquirable (then immediately released) lock means none is. This never writes
    to or creates the lock file: a missing file means "no watcher", and any
    error degrades to ``False`` so the spawn path can fall back on loctree's own
    exit-75 contention guard.
    """
    lock = scan_lock_path(root)
    if fcntl is None or not lock.exists():
        return False
    try:
        fd = os.open(str(lock), os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Held by a live watcher (BlockingIOError is an OSError subclass).
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def build_watch_command(
    loct: str,
    root: str | Path,
    *,
    transport: str = DEFAULT_WATCH_MODE,
    port: int | None = None,
) -> list[str]:
    """Compose the canonical ``loct watch`` invocation.

    ``http`` co-spawns the streamable MCP and runs in the foreground (we detach
    it ourselves); any other transport falls back to a plain self-daemonizing
    ``--bg`` watcher with no co-spawned MCP.
    """
    cmd = [loct, "watch"]
    if transport == "http":
        resolved = port if port is not None else port_for_root(root)
        cmd += ["--http", "--port", str(resolved)]
    else:
        cmd += ["--bg"]
    cmd.append(canonical_root(root))
    return cmd


def _resolve_loct() -> str | None:
    """Resolve the ``loct`` binary without trusting only the process PATH."""
    return _resolve_foundation("loct")


def _default_spawner(cmd: Sequence[str]) -> subprocess.Popen[bytes]:
    """Launch a detached ``loct watch`` in its own session.

    Output is discarded — loctree keeps its own ``.loctree/watch.log``; our
    truth signals are the lock and the bound port, not this process's stdout.
    ``start_new_session`` detaches it so the watcher outlives the spawn pipeline.
    """
    return subprocess.Popen(
        list(cmd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


@dataclass
class WatchOutcome:
    """Result of ensuring the perception layer for one root."""

    root: str
    status: str  # started | already_running | unavailable | failed
    transport: str
    endpoint: str | None
    port: int | None
    pid: int | None = None
    returncode: int | None = None
    detail: str = ""
    checked_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialize this outcome for JSON printing / receipt embedding."""
        return {
            "root": self.root,
            "status": self.status,
            "transport": self.transport,
            "endpoint": self.endpoint,
            "port": self.port,
            "pid": self.pid,
            "returncode": self.returncode,
            "detail": self.detail,
            "checked_at": self.checked_at,
        }


def ensure_watch(
    root: str | Path,
    *,
    transport: str = DEFAULT_WATCH_MODE,
    port: int | None = None,
    spawner: Spawner | None = None,
    resolver: Callable[[], str | None] = _resolve_loct,
    lock_probe: Callable[[str | Path], bool] = watcher_running,
    probe_window: float = 1.5,
    poll_interval: float = 0.1,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> WatchOutcome:
    """Ensure exactly one ``loct watch`` is maintained for ``root``.

    Returns a :class:`WatchOutcome`; never raises on contention or a
    missing/broken ``loct``. Status:

    * ``already_running`` — a watcher already holds the root (cheap lock probe,
      or our spawn lost the race and exited 75);
    * ``started`` — we launched the watcher this call;
    * ``unavailable`` — ``loct`` is not runnable; nothing was spawned;
    * ``failed`` — the watcher exited non-zero for a reason other than
      contention (reported, not raised).
    """
    canonical = canonical_root(root)
    resolved_port = (
        (port if port is not None else port_for_root(canonical))
        if transport == "http"
        else None
    )
    endpoint = (
        mcp_endpoint(canonical, port=resolved_port) if transport == "http" else None
    )

    loct = resolver()
    if loct is None:
        return WatchOutcome(
            root=canonical,
            status="unavailable",
            transport=transport,
            endpoint=endpoint,
            port=resolved_port,
            detail="loct not found on the launcher bin or $PATH; perception skipped",
        )

    if lock_probe(canonical):
        return WatchOutcome(
            root=canonical,
            status="already_running",
            transport=transport,
            endpoint=endpoint,
            port=resolved_port,
            detail="watcher already maintained for this root (lock held)",
        )

    cmd = build_watch_command(loct, canonical, transport=transport, port=resolved_port)
    spawn = spawner or _default_spawner
    proc = spawn(cmd)

    # Early-exit probe: distinguish a healthy watcher (still alive, or --bg fork
    # returning 0) from gentle contention (75) and genuine failure.
    deadline = clock() + probe_window
    rc = proc.poll()
    while rc is None and clock() < deadline:
        sleeper(poll_interval)
        rc = proc.poll()

    if rc is None or rc == 0:
        return WatchOutcome(
            root=canonical,
            status="started",
            transport=transport,
            endpoint=endpoint,
            port=resolved_port,
            pid=proc.pid,
            returncode=rc,
            detail=f"watcher started: {' '.join(cmd)}",
        )
    if rc == EXIT_LOCK_CONTENDED:
        return WatchOutcome(
            root=canonical,
            status="already_running",
            transport=transport,
            endpoint=endpoint,
            port=resolved_port,
            pid=proc.pid,
            returncode=rc,
            detail="another watcher won the lock race (exit 75); not multiplied",
        )
    return WatchOutcome(
        root=canonical,
        status="failed",
        transport=transport,
        endpoint=endpoint,
        port=resolved_port,
        pid=proc.pid,
        returncode=rc,
        detail=f"watcher exited {rc}; perception not established",
    )


# ---------------------------------------------------------------------------
# CLI — the runtime spawn bridge calls this best-effort, once per run.
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """``python -m vibecrafted_core.perception <ensure-watch|status> --root R``.

    Always returns 0: the perception layer is best-effort and must never fail a
    run. The JSON payload carries the real outcome for observability.
    """
    parser = argparse.ArgumentParser(
        prog="vibecrafted-perception",
        description=(
            "Maintain exactly one scan-only loct watch per repository root "
            "for an agent run. MCP transport is supervised separately."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ensure = sub.add_parser(
        "ensure-watch",
        help="Ensure a single scan watcher is maintained for a root.",
    )
    p_ensure.add_argument("--root", default=".", help="Repository root (default: .)")
    p_ensure.add_argument(
        "--transport",
        default=DEFAULT_WATCH_MODE,
        choices=("scan", "http", "stdio"),
        help="Watcher mode (default: scan; http is an explicit legacy mode).",
    )
    p_ensure.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override the per-root MCP port (default: derived from the root).",
    )

    p_status = sub.add_parser(
        "status", help="Report whether a watcher is maintained for a root."
    )
    p_status.add_argument("--root", default=".", help="Repository root (default: .)")
    p_status.add_argument(
        "--transport",
        default=DEFAULT_WATCH_MODE,
        choices=("scan", "http", "stdio"),
        help="Watcher mode to report (default: scan).",
    )

    args = parser.parse_args(argv)

    if args.command == "ensure-watch":
        outcome = ensure_watch(args.root, transport=args.transport, port=args.port)
        print(json.dumps(outcome.to_dict(), indent=2))
        return 0

    # status
    canonical = canonical_root(args.root)
    running = watcher_running(canonical)
    payload = {
        "root": canonical,
        "running": running,
        "transport": args.transport,
        "endpoint": (mcp_endpoint(canonical) if args.transport == "http" else None),
        "port": (port_for_root(canonical) if args.transport == "http" else None),
        "checked_at": utc_now_iso(),
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
