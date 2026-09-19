#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""fleet_scan.py — fleet config posture scanner for the vc-hello skill.

Reads the known config locations of the agent CLI fleet (Claude, Codex,
Grok, Kimi), normalizes each into a shared posture shape, and computes the
fleet consensus (majority per axis, conflicts listed). The `diff` subcommand
compares one target CLI against that consensus (drift audit, read-only).

Secrets policy: extraction is structural — secret-bearing fields (api_key,
env blocks, auth files) are never copied into the posture. A defensive
scrub pass still redacts any residual value that smells like a secret.

Stdlib only. Usage:
    uv run fleet_scan.py scan --output posture.json
    uv run fleet_scan.py diff --target kimi --output drift.json [--posture posture.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import tomllib

HOME = Path.home()

FLEET = ("claude", "codex", "grok", "kimi")

FULL_AUTO = {"full-auto"}
TOP_TIER_EFFORT = {"xhigh", "max"}

SECRET_KEY_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password|credential)")
SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|BSA[A-Za-z0-9]{10,}|[0-9a-f]{40,})"
)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _basename_command(command: str) -> str:
    """Reduce a hook command line to the meaningful script/binary name."""
    parts = command.strip().split()
    while parts and parts[0] in {
        "bash",
        "sh",
        "python3",
        "python",
        "node",
        "uv",
        "env",
    }:
        parts = parts[1:]
    return Path(parts[0]).name if parts else command.strip()


def _norm_event(event: str) -> str:
    """PreCompact -> pre_compact; pre_tool_use stays as-is."""
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()
    return snake


def _norm_language(value: str | None) -> str | None:
    if not value:
        return value
    low = value.strip().lower()
    if low in {"pl", "polish", "polski"}:
        return "pl"
    return low


def _hook_family(name: str) -> str:
    low = name.lower()
    if "aicx" in low and ("compact" in low or "recall" in low):
        return "aicx-compact"
    if "loctree-first" in low:
        return "loctree-first-guard"
    if "cc-status" in low:
        return "cc-status"
    if "strip-redirect" in low:
        return "strip-redirect"
    if "loct-context" in low:
        return "loct-context-card"
    return Path(low).stem


def _norm_server(name: str) -> str:
    low = name.strip().lower()
    low = re.sub(r"[@/].*$", "", low)  # strip plugin marketplace qualifiers
    for suffix in ("-mcp", "_mcp", "-server", "-lsp"):
        if low.endswith(suffix) and low != "mcp-server-semgrep":
            low = low[: -len(suffix)]
    if low == "mcp-server-semgrep":
        low = "semgrep"
    return low


# ---------------------------------------------------------------------------
# Per-CLI posture extraction. Each returns the same normalized shape; missing
# concepts are None / [] rather than invented values.
# ---------------------------------------------------------------------------


def _empty_posture() -> dict[str, Any]:
    return {
        "permission_posture": None,
        "effort": None,
        "theme": None,
        "language": None,
        "auto_update": None,
        "model": None,
        "mcp_servers": [],
        "hooks": [],
        "sources": [],
        "absent": [],
    }


def scan_claude() -> dict[str, Any]:
    posture = _empty_posture()
    settings = HOME / ".claude" / "settings.json"
    if not settings.exists():
        posture["absent"].append(str(settings))
        return posture
    data = _load_json(settings)
    posture["sources"].append(str(settings))

    perms = data.get("permissions", {})
    mode = perms.get("defaultMode", "")
    skip = data.get("skipDangerousModePermissionPrompt", False)
    if mode in {"bypassPermissions"} or (mode == "auto" and skip):
        posture["permission_posture"] = "full-auto"
    elif mode == "auto":
        posture["permission_posture"] = "auto"
    elif mode:
        posture["permission_posture"] = "ask"

    posture["effort"] = data.get("effortLevel")
    posture["theme"] = data.get("theme")
    posture["language"] = _norm_language(data.get("language"))
    posture["auto_update"] = bool(data.get("autoUpdatesChannel"))
    posture["model"] = data.get("model")

    servers = {_norm_server(name) for name in data.get("mcpServers", {})}
    servers |= {_norm_server(name) for name in data.get("enabledPlugins", {})}
    posture["mcp_servers"] = sorted(servers)

    hooks = []
    for event, entries in data.get("hooks", {}).items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                hooks.append(
                    {
                        "event": _norm_event(event),
                        "family": _hook_family(
                            _basename_command(hook.get("command", ""))
                        ),
                    }
                )
    posture["hooks"] = sorted(
        {(h["event"], h["family"]) for h in hooks} and hooks,
        key=lambda h: (h["event"], h["family"]),
    )
    return posture


def scan_codex() -> dict[str, Any]:
    posture = _empty_posture()
    config = HOME / ".codex" / "config.toml"
    if not config.exists():
        posture["absent"].append(str(config))
        return posture
    data = _load_toml(config)
    posture["sources"].append(str(config))

    if (
        data.get("approval_policy") == "never"
        and data.get("sandbox_mode") == "danger-full-access"
    ):
        posture["permission_posture"] = "full-auto"
    elif data.get("approval_policy") in {"untrusted", "on-failure", "on-request"}:
        posture["permission_posture"] = "ask"

    posture["effort"] = data.get("model_reasoning_effort")
    posture["theme"] = data.get("tui", {}).get("theme")
    posture["model"] = data.get("model")

    servers = {_norm_server(name) for name in data.get("mcp_servers", {})}
    servers |= {
        _norm_server(name)
        for name, plug in data.get("plugins", {}).items()
        if plug.get("enabled")
    }
    posture["mcp_servers"] = sorted(servers)

    hooks = []
    for key, state in data.get("hooks", {}).get("state", {}).items():
        if state.get("enabled") is False:
            continue
        match = re.search(r":([a-z_]+):\d+:\d+$", key)
        event = match.group(1) if match else "unknown"
        source = key.split(":", 1)[0]
        hooks.append(
            {"event": event, "family": _hook_family(Path(source).name or source)}
        )
    posture["hooks"] = sorted(hooks, key=lambda h: (h["event"], h["family"]))
    return posture


def scan_grok() -> dict[str, Any]:
    posture = _empty_posture()
    config = HOME / ".grok" / "config.toml"
    if not config.exists():
        posture["absent"].append(str(config))
        return posture
    data = _load_toml(config)
    posture["sources"].append(str(config))

    ui = data.get("ui", {})
    if ui.get("permission_mode") == "always-approve" or ui.get("yolo") is True:
        posture["permission_posture"] = "full-auto"
    elif ui.get("permission_mode"):
        posture["permission_posture"] = "ask"

    posture["effort"] = data.get("models", {}).get("default_reasoning_effort")
    posture["language"] = _norm_language(ui.get("voice_stt_language"))
    posture["auto_update"] = data.get("cli", {}).get("auto_update")
    posture["model"] = data.get("models", {}).get("default")
    return posture


def scan_kimi() -> dict[str, Any]:
    posture = _empty_posture()
    home = HOME / ".kimi-code"
    config = home / "config.toml"
    if not config.exists():
        posture["absent"].append(str(config))
        return posture
    data = _load_toml(config)
    posture["sources"].append(str(config))

    mode = data.get("default_permission_mode", "manual")
    posture["permission_posture"] = {"yolo": "full-auto", "auto": "auto"}.get(
        mode, "ask"
    )

    thinking = data.get("thinking", {})
    effort = thinking.get("effort")
    if not effort:
        default_model = data.get("default_model", "")
        effort = data.get("models", {}).get(default_model, {}).get("default_effort")
    posture["effort"] = effort
    posture["model"] = data.get("default_model")

    tui = home / "tui.toml"
    if tui.exists():
        tui_data = _load_toml(tui)
        posture["sources"].append(str(tui))
        posture["theme"] = tui_data.get("theme")
        posture["auto_update"] = tui_data.get("upgrade", {}).get("auto_install")

    mcp = home / "mcp.json"
    if mcp.exists():
        mcp_data = _load_json(mcp)
        posture["sources"].append(str(mcp))
        posture["mcp_servers"] = sorted(
            {_norm_server(name) for name in mcp_data.get("mcpServers", {})}
        )

    hooks = []
    for hook in data.get("hooks", []):
        hooks.append(
            {
                "event": _norm_event(hook.get("event", "")),
                "family": _hook_family(_basename_command(hook.get("command", ""))),
            }
        )
    posture["hooks"] = sorted(hooks, key=lambda h: (h["event"], h["family"]))
    return posture


SCANNERS = {
    "claude": scan_claude,
    "codex": scan_codex,
    "grok": scan_grok,
    "kimi": scan_kimi,
}


# ---------------------------------------------------------------------------
# Secrets scrub — defensive; extraction above never reads secret fields.
# ---------------------------------------------------------------------------


def scrub(value: Any, key: str = "") -> Any:
    if SECRET_KEY_RE.search(key):
        return "__REDACTED__"
    if isinstance(value, dict):
        return {k: scrub(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str) and SECRET_VALUE_RE.search(value):
        return "__REDACTED__"
    return value


# ---------------------------------------------------------------------------
# Consensus
# ---------------------------------------------------------------------------


def _majority(values: dict[str, Any]) -> dict[str, Any]:
    present = {cli: v for cli, v in values.items() if v is not None}
    if not present:
        return {"value": None, "support": [], "conflicts": []}
    counts = Counter(present.values())
    winner, _ = counts.most_common(1)[0]
    support = [cli for cli, v in present.items() if v == winner]
    conflicts = [{"cli": cli, "value": v} for cli, v in present.items() if v != winner]
    return {"value": winner, "support": support, "conflicts": conflicts}


def compute_consensus(fleet: dict[str, dict[str, Any]]) -> dict[str, Any]:
    consensus: dict[str, Any] = {}
    for axis in ("permission_posture", "effort", "theme", "language", "auto_update"):
        consensus[axis] = _majority({cli: p[axis] for cli, p in fleet.items()})

    server_votes: dict[str, list[str]] = {}
    for cli, posture in fleet.items():
        for server in posture["mcp_servers"]:
            server_votes.setdefault(server, []).append(cli)
    consensus["mcp_servers"] = {
        "shared": sorted(name for name, clis in server_votes.items() if len(clis) >= 2),
        "per_cli": {cli: p["mcp_servers"] for cli, p in fleet.items()},
    }

    hook_votes: dict[str, list[str]] = {}
    for cli, posture in fleet.items():
        families = {h["family"] for h in posture["hooks"]}
        for family in families:
            hook_votes.setdefault(family, []).append(cli)
    consensus["hooks"] = {
        "shared": sorted(name for name, clis in hook_votes.items() if len(clis) >= 2),
        "per_cli": {
            cli: sorted({h["family"] for h in p["hooks"]}) for cli, p in fleet.items()
        },
    }
    return consensus


# ---------------------------------------------------------------------------
# Drift audit
# ---------------------------------------------------------------------------


def _effort_equivalent(consensus_value: str | None, target_value: str | None) -> str:
    if consensus_value in TOP_TIER_EFFORT:
        return "match" if target_value in TOP_TIER_EFFORT else "divergent"
    return "match" if consensus_value == target_value else "divergent"


def diff_target(
    target: str, fleet: dict[str, dict[str, Any]], consensus: dict[str, Any]
) -> dict[str, Any]:
    posture = fleet.get(target)
    if posture is None:
        raise SystemExit(f"unknown target: {target} (known: {', '.join(FLEET)})")

    axes: dict[str, Any] = {}
    for axis in ("permission_posture", "effort", "theme", "language", "auto_update"):
        want = consensus[axis]["value"]
        have = posture[axis]
        if have is None:
            status = "no-key"
        elif axis == "effort":
            status = _effort_equivalent(want, have)
        elif axis == "permission_posture":
            status = (
                "match"
                if (want in FULL_AUTO) == (have in FULL_AUTO) and want == have
                else "divergent"
            )
            if want in FULL_AUTO and have in FULL_AUTO:
                status = "match"
        else:
            status = "match" if want == have else "divergent"
        axes[axis] = {"consensus": want, "target": have, "status": status}

    missing_servers = sorted(
        set(consensus["mcp_servers"]["shared"]) - set(posture["mcp_servers"])
    )
    axes["mcp_servers"] = {
        "consensus": consensus["mcp_servers"]["shared"],
        "target": posture["mcp_servers"],
        "missing": missing_servers,
        "status": "match" if not missing_servers else "divergent",
    }

    target_families = {h["family"] for h in posture["hooks"]}
    missing_hooks = sorted(set(consensus["hooks"]["shared"]) - target_families)
    axes["hooks"] = {
        "consensus": consensus["hooks"]["shared"],
        "target": sorted(target_families),
        "missing": missing_hooks,
        "status": "match" if not missing_hooks else "divergent",
    }

    divergent = [
        axis for axis, report in axes.items() if report["status"] == "divergent"
    ]
    return {
        "target": target,
        "axes": axes,
        "summary": {
            "divergent": divergent,
            "no_key": [
                axis for axis, report in axes.items() if report["status"] == "no-key"
            ],
            "match": [
                axis for axis, report in axes.items() if report["status"] == "match"
            ],
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write_output(payload: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"Success! Data written to: {output}")


def _fresh_fleet() -> dict[str, dict[str, Any]]:
    return {cli: scanner() for cli, scanner in SCANNERS.items()}


def cmd_scan(args: argparse.Namespace) -> int:
    fleet = _fresh_fleet()
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "fleet": fleet,
        "consensus": compute_consensus(fleet),
        "secrets_policy": "structural extraction; secret-bearing fields never copied; defensive scrub applied",
    }
    _write_output(scrub(payload), Path(args.output))
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    fleet = _fresh_fleet()
    if args.posture:
        stored = _load_json(Path(args.posture))
        consensus = stored.get("consensus")
        if consensus is None:
            print(
                f"posture file lacks a consensus section: {args.posture}",
                file=sys.stderr,
            )
            return 1
    else:
        consensus = compute_consensus(fleet)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        **diff_target(args.target, fleet, consensus),
    }
    _write_output(scrub(payload), Path(args.output))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser(
        "scan", help="scan the fleet and emit normalized posture + consensus JSON"
    )
    scan.add_argument(
        "--output", required=True, help="file to write the posture JSON into"
    )
    scan.set_defaults(func=cmd_scan)

    diff = sub.add_parser(
        "diff", help="diff one target CLI against the fleet consensus (drift audit)"
    )
    diff.add_argument("--target", required=True, choices=FLEET, help="CLI to audit")
    diff.add_argument(
        "--output", required=True, help="file to write the drift report into"
    )
    diff.add_argument(
        "--posture", help="reuse a previous scan output instead of rescanning the fleet"
    )
    diff.set_defaults(func=cmd_diff)

    args = parser.parse_args()
    try:
        return args.func(args)
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        print(f"fleet_scan error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
