"""One-shot, all-provider local usage collection owned by vc-server.

Runs every provider engine exactly once — no daemons, no permanent children —
and prints a ``vibecrafted.usage-collection.v1`` JSON receipt on stdout.

The six local-ledger providers (codex, claude, grok, junie, copilot, cursor)
are collected in-process through the canonical ``fleet_engine`` parsers, which
write ``quota.json`` + ``scan-cache.json`` under ``$VIBECRAFTED_HOME/telemetry``.
The kimi and agy monitors run as bounded subprocesses because their ``once``
mode may probe a local provider API; their failures are reported honestly and
never turned into fabricated quota state.

Usage: ``collect_all.py [--home PATH] [--only agent[,agent...]]``
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COLLECTION_SCHEMA = "vibecrafted.usage-collection.v1"
FLEET_AGENTS = ("codex", "claude", "grok", "junie", "copilot", "cursor")
MONITOR_AGENTS = ("kimi", "agy")
ALL_AGENTS = ("agy", "kimi", *FLEET_AGENTS)
MONITOR_TIMEOUT_SECONDS = 25.0
FLEET_TIMEOUT_SECONDS = 25.0

_ENGINE_DIR = Path(__file__).resolve().parent


def _load_fleet_engine() -> Any:
    spec = importlib.util.spec_from_file_location(
        "fleet_engine", _ENGINE_DIR / "fleet_engine.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("fleet_engine.py is not loadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _collect_fleet_agent(engine: Any, agent: str, home: Path) -> dict[str, str]:
    quota_root = home / "telemetry" / agent
    quota_root.mkdir(parents=True, exist_ok=True)
    store = Path(engine.default_store(agent))
    started = time.monotonic()
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink):
            rc = engine.run(agent, "once", store, quota_root)
    except Exception as exc:  # noqa: BLE001 - an engine bug must not kill the batch
        return {
            "agent": agent,
            "status": "error",
            "detail": f"engine raised {type(exc).__name__}: {exc}",
        }
    elapsed = time.monotonic() - started
    if elapsed > FLEET_TIMEOUT_SECONDS:
        return {
            "agent": agent,
            "status": "error",
            "detail": f"engine exceeded {FLEET_TIMEOUT_SECONDS:.0}s collection budget",
        }
    if rc != 0:
        tail = sink.getvalue().strip().splitlines()[-1][:200] if sink.getvalue() else ""
        return {
            "agent": agent,
            "status": "error",
            "detail": f"engine exited {rc}" + (f": {tail}" if tail else ""),
        }
    return {"agent": agent, "status": "ok", "detail": str(quota_root / "quota.json")}


def _collect_monitor_agent(agent: str, home: Path) -> dict[str, str]:
    script = _ENGINE_DIR / f"{agent}-monitor" / f"{agent}_monitor.py"
    if not script.is_file():
        return {
            "agent": agent,
            "status": "absent",
            "detail": "engine script not installed in this generation",
        }
    env = dict(os.environ)
    env["VIBECRAFTED_HOME"] = str(home)
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "once"],
            capture_output=True,
            text=True,
            timeout=MONITOR_TIMEOUT_SECONDS,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "agent": agent,
            "status": "error",
            "detail": f"monitor exceeded {MONITOR_TIMEOUT_SECONDS:.0}s collection budget",
        }
    except OSError as exc:
        return {"agent": agent, "status": "error", "detail": f"spawn failed: {exc}"}
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1][:200] if detail else ""
        return {
            "agent": agent,
            "status": "error",
            "detail": f"monitor exited {proc.returncode}"
            + (f": {tail}" if tail else ""),
        }
    return {"agent": agent, "status": "ok", "detail": "monitor once completed"}


def collect_all(home: Path, *, only: set[str] | None = None) -> dict[str, Any]:
    selected = [agent for agent in ALL_AGENTS if only is None or agent in only]
    engine: Any | None = None
    outcomes: list[dict[str, str]] = []
    for agent in selected:
        if agent in MONITOR_AGENTS:
            outcomes.append(_collect_monitor_agent(agent, home))
            continue
        if engine is None:
            try:
                engine = _load_fleet_engine()
            except (OSError, RuntimeError) as exc:
                for remaining in selected:
                    if remaining in MONITOR_AGENTS:
                        continue
                    outcomes.append(
                        {
                            "agent": remaining,
                            "status": "error",
                            "detail": f"fleet engine unavailable: {exc}",
                        }
                    )
                break
        outcomes.append(_collect_fleet_agent(engine, agent, home))
    ok = all(outcome["status"] in {"ok", "absent"} for outcome in outcomes)
    return {
        "schema": COLLECTION_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "home": str(home),
        "ok": ok,
        "outcomes": outcomes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="one-shot all-provider collection")
    parser.add_argument(
        "--home",
        default=os.environ.get("VIBECRAFTED_HOME", ""),
        help="VIBECRAFTED_HOME root (default: the environment value)",
    )
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated agent subset (default: all providers)",
    )
    args = parser.parse_args(argv)
    home = Path(args.home).expanduser() if args.home else None
    if home is None:
        print("collect_all: --home or VIBECRAFTED_HOME is required", file=sys.stderr)
        return 2
    only = {token.strip() for token in args.only.split(",") if token.strip()} or None
    receipt = collect_all(home, only=only)
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
