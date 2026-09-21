#!/usr/bin/env python3
"""agy_monitor.py — Quota poller, accounting engine & statusline runner for Google Antigravity (AGY).

Supports both Antigravity IDE and Antigravity CLI (agy).

Modes:
  line      Render one-line statusline for the active/latest AGY session (default).
  once      Single snapshot of active session, print JSON, write quota.json.
  daemon    Poll active session and process health, write quota.json atomically.
  sessions  List recent AGY conversations with steps and token counts.

Stdlib only. Requires Python >= 3.11 (tomllib).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- config

DEFAULT_CONFIG = """\
[paths]
gemini_home = "~/.gemini"
ide_app_data = "~/.gemini/antigravity-ide"
cli_app_data = "~/.gemini/antigravity-cli"
runtime_dir = "~/.gemini/agy-monitor/runtime"
logs_dir = "~/.gemini/agy-monitor/logs"

[monitor]
poll_interval_s = 15

[statusline]
show_cost = true
show_tool_calls = true
desync_ceiling = 0.95

# Per-model pricing per 1M tokens (USD). Shadow pricing (api-equiv).
[pricing."gemini-3.8-flash-high"]
fresh = 0.15
cache_read = 0.0375
output = 0.60

[pricing."gemini-3.8-flash-medium"]
fresh = 0.15
cache_read = 0.0375
output = 0.60

[pricing."gemini-3.8-flash-low"]
fresh = 0.15
cache_read = 0.0375
output = 0.60

[pricing."gemini-3.7-flash-high"]
fresh = 0.15
cache_read = 0.0375
output = 0.60

[pricing."gemini-3.1-pro-high"]
fresh = 1.25
cache_read = 0.3125
output = 5.00

[pricing."claude-sonnet-4-6"]
fresh = 3.00
cache_read = 0.30
output = 15.00

[pricing."claude-opus-4-6-thinking"]
fresh = 15.00
cache_read = 1.50
output = 75.00

[pricing."gpt-oss-120b-medium"]
fresh = 0.50
cache_read = 0.10
output = 1.50
"""


def load_config(path: str | None = None) -> dict:
    if path is None:
        default = Path.home() / ".gemini" / "agy-monitor" / "agy-monitor.toml"
        path = str(default) if default.exists() else None
    if path is None:
        return tomllib.loads(DEFAULT_CONFIG)
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except Exception:
        return tomllib.loads(DEFAULT_CONFIG)


def expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


def quota_path(cfg: dict) -> Path:
    return expand(cfg.get("paths", {}).get("runtime_dir", "~/.gemini/agy-monitor/runtime")) / "quota.json"


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def read_json(path: Path) -> dict | None:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def normalize_model_name(raw: str | None) -> str:
    if not raw:
        return "gemini-3.8-flash-high"
    clean = raw.strip().lower()
    if "flash" in clean and ("3.8" in clean or "3-8" in clean):
        if "medium" in clean:
            return "gemini-3.8-flash-medium"
        if "low" in clean:
            return "gemini-3.8-flash-low"
        return "gemini-3.8-flash-high"
    if "flash" in clean and ("3.7" in clean or "3-7" in clean):
        return "gemini-3.7-flash-high"
    if "flash" in clean and ("3.6" in clean or "3-6" in clean):
        return "gemini-3.6-flash-high"
    if "pro" in clean and ("3.1" in clean or "3-1" in clean):
        if "low" in clean:
            return "gemini-3.1-pro-low"
        return "gemini-3.1-pro-high"
    if "sonnet" in clean:
        return "claude-sonnet-4-6"
    if "opus" in clean:
        return "claude-opus-4-6-thinking"
    if "gpt" in clean:
        return "gpt-oss-120b-medium"
    return clean.replace(" ", "-")


def get_model_rates(model_id: str, pricing_cfg: dict) -> dict | None:
    p = pricing_cfg.get(model_id)
    if not p and model_id.startswith("gemini-"):
        p = pricing_cfg.get(model_id.replace("preview", "high"))
    if isinstance(p, dict) and p.get("fresh", 0.0) > 0:
        return {
            "fresh_in": float(p.get("fresh", 0.0)),
            "cache_in": float(p.get("cache_read", 0.0)),
            "output": float(p.get("output", 0.0)),
        }
    return None


# ---------------------------------------------------------------- session discovery


def find_active_conversation(cfg: dict, preferred_id: str | None = None) -> tuple[Path | None, str, str]:
    """Finds the most recently modified transcript across IDE and CLI.

    Returns: (transcript_path, conversation_id, surface)
    """
    ide_brain = expand(cfg.get("paths", {}).get("ide_app_data", "~/.gemini/antigravity-ide")) / "brain"
    cli_brain = expand(cfg.get("paths", {}).get("cli_app_data", "~/.gemini/antigravity-cli")) / "brain"

    candidates = []
    for brain_dir, surface in [(ide_brain, "ide"), (cli_brain, "cli")]:
        if not brain_dir.exists():
            continue
        for p in brain_dir.glob("*/.system_generated/logs/transcript.jsonl"):
            try:
                cid = p.parent.parent.parent.name
                if preferred_id and cid != preferred_id:
                    continue
                candidates.append((p.stat().st_mtime, p, cid, surface))
            except OSError:
                pass

    if not candidates:
        return None, "", "unknown"

    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0]
    return best[1], best[2], best[3]


def get_git_branch(workspace_dir: str | None) -> str:
    if not workspace_dir or not os.path.exists(workspace_dir):
        return ""
    try:
        res = subprocess.check_output(
            ["git", "-C", workspace_dir, "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=1,
        )
        return res.decode("utf-8").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------- transcript parser


class AgyTranscriptTracker:
    """Parses Antigravity transcript.jsonl incrementally with safe offsets and quota error tracking."""

    def __init__(self, transcript_path: Path, conversation_id: str, cfg: dict):
        self.path = transcript_path
        self.conversation_id = conversation_id
        self.cfg = cfg
        self.pricing_cfg = cfg.get("pricing", {})

    def update(self) -> dict:
        agg = {
            "conversation_id": self.conversation_id,
            "steps_count": 0,
            "user_turns": 0,
            "planner_turns": 0,
            "tool_calls": 0,
            "total_chars": 0,
            "estimated_tokens": 0,
            "model_raw": None,
            "model_id": "gemini-3.8-flash-high",
            "workspace": None,
            "git_branch": "",
            "cost_usd": 0.0,
            "has_cost": False,
            "last_success_ts": 0,
            "last_error_ts": 0,
            "last_error_msg": "",
            "last_429_ts": 0,
            "last_429_msg": "",
            "quota_reset_in": "",
        }

        if not self.path.exists():
            return agg

        cache_dir = Path(f"/tmp/agy_statusline_cache_{os.getuid()}")
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{self.conversation_id}.json"

        offset = 0
        if cache_file.exists():
            try:
                cdata = json.loads(cache_file.read_text(encoding="utf-8"))
                if cdata.get("v") == 1:
                    offset = cdata.get("offset", 0)
                    cached_agg = cdata.get("agg")
                    if isinstance(cached_agg, dict):
                        agg.update(cached_agg)
            except Exception:
                offset = 0

        try:
            file_size = self.path.stat().st_size
            if file_size < offset:
                offset = 0
                for k in ("steps_count", "user_turns", "planner_turns", "tool_calls", "total_chars", "cost_usd"):
                    agg[k] = 0

            with open(self.path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(offset)
                while True:
                    pos = f.tell()
                    line = f.readline()
                    if not line or not line.endswith("\n"):
                        f.seek(pos)
                        break

                    line_str = line.strip()
                    if not line_str:
                        continue

                    try:
                        d = json.loads(line_str)
                    except Exception:
                        continue

                    agg["steps_count"] += 1
                    agg["total_chars"] += len(line)
                    st = d.get("type")
                    dt_str = d.get("created_at") or ""
                    ev_ts = 0
                    if dt_str:
                        try:
                            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                            ev_ts = int(dt.timestamp() * 1000)
                        except Exception:
                            ev_ts = int(time.time() * 1000)

                    content = d.get("content") or ""
                    # Model extraction
                    if "Model Selection" in content:
                        m = re.search(r"Model Selection`\s+from\s+.*?\s+to\s+(.*?)\.\s*(?:No need|<)", content)
                        if m:
                            agg["model_raw"] = m.group(1).strip()
                            agg["model_id"] = normalize_model_name(agg["model_raw"])

                    # Workspace extraction
                    if "<user_information>" in content and not agg["workspace"]:
                        m = re.search(r"(/Users/[^\s\n]+)\s+->", content)
                        if m:
                            agg["workspace"] = m.group(1).strip()

                    if st == "USER_INPUT":
                        agg["user_turns"] += 1
                    elif st == "PLANNER_RESPONSE":
                        agg["planner_turns"] += 1
                        tc = d.get("tool_calls") or []
                        agg["tool_calls"] += len(tc)
                        if ev_ts > agg["last_success_ts"]:
                            agg["last_success_ts"] = ev_ts
                    elif st == "ERROR_MESSAGE" or d.get("status") == "ERROR":
                        err_text = str(d.get("error") or content or "")
                        if ev_ts > agg["last_error_ts"]:
                            agg["last_error_ts"] = ev_ts
                            agg["last_error_msg"] = err_text[:200]
                        if "429" in err_text or "RESOURCE_EXHAUSTED" in err_text or "quota" in err_text.lower():
                            if ev_ts > agg["last_429_ts"]:
                                agg["last_429_ts"] = ev_ts
                                agg["last_429_msg"] = err_text[:200]
                                m_reset = re.search(r"Resets in ([0-9a-zA-Z]+)\.", err_text)
                                if m_reset:
                                    agg["quota_reset_in"] = m_reset.group(1)

                offset = f.tell()

        except Exception:
            pass

        # Estimate tokens (~4 characters per token for multi-modal code + json)
        agg["estimated_tokens"] = max(1, agg["total_chars"] // 4)

        # Shadow pricing
        rates = get_model_rates(agg["model_id"], self.pricing_cfg)
        if rates:
            # Assume 80% prompt / context tokens, 20% output tokens
            in_toks = int(agg["estimated_tokens"] * 0.80)
            out_toks = int(agg["estimated_tokens"] * 0.20)
            agg["cost_usd"] = (in_toks * rates["fresh_in"] + out_toks * rates["output"]) / 1_000_000
            agg["has_cost"] = True

        # Fallback workspace lookup from sqlite conversation db if not found in transcript
        if not agg["workspace"]:
            surface_dir = self.path.parents[4].name # antigravity-ide or antigravity-cli
            db_path = expand(f"~/.gemini/{surface_dir}/conversations/{self.conversation_id}.db")
            if db_path.exists():
                try:
                    import sqlite3
                    conn = sqlite3.connect(db_path)
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM trajectory_metadata_blob")
                    row = cur.fetchone()
                    if row:
                        raw_data = row[1] if len(row) > 1 else row[0]
                        txt = raw_data.decode("utf-8", errors="ignore")
                        m = re.search(r"file://(/Users/[a-zA-Z0-9_/.-]+)", txt)
                        if m:
                            agg["workspace"] = m.group(1).strip()
                    conn.close()
                except Exception:
                    pass

        if agg["workspace"]:
            agg["git_branch"] = get_git_branch(agg["workspace"])

        try:
            cache_file.write_text(json.dumps({"v": 1, "offset": offset, "agg": agg}), encoding="utf-8")
        except Exception:
            pass

        return agg


# ---------------------------------------------------------------- process inspection


def inspect_agy_processes() -> dict:
    """Checks whether Antigravity IDE or CLI language server is running."""
    res = {
        "ide_running": False,
        "ide_pid": None,
        "cli_running": False,
        "cli_pid": None,
        "language_server_port": None,
    }
    try:
        out = subprocess.check_output(["ps", "aux"], stderr=subprocess.DEVNULL, timeout=2).decode("utf-8")
        for line in out.splitlines():
            if "language_server_macos_arm" in line:
                m_port = re.search(r"--extension_server_port\s+([0-9]+)", line)
                if m_port:
                    res["language_server_port"] = int(m_port.group(1))
                parts = line.split()
                if len(parts) > 1 and parts[1].isdigit():
                    res["ide_pid"] = int(parts[1])
                    res["ide_running"] = True
            elif "bin/agy" in line or "/agy " in line:
                parts = line.split()
                if len(parts) > 1 and parts[1].isdigit():
                    res["cli_pid"] = int(parts[1])
                    res["cli_running"] = True
    except Exception:
        pass
    return res


# ---------------------------------------------------------------- statusline formatting


def _fmt_tokens(n: int | float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(int(n))


def render_statusline(cfg: dict | None = None, conversation_id: str | None = None) -> str:
    if cfg is None:
        cfg = load_config()

    transcript_path, cid, surface = find_active_conversation(cfg, preferred_id=conversation_id)
    if not transcript_path or not transcript_path.exists():
        return "agy  ·  no active session"

    tracker = AgyTranscriptTracker(transcript_path, cid, cfg)
    agg = tracker.update()

    parts = []

    # 1. Surface & Model
    model_display = agg.get("model_id", "gemini-3.8-flash")
    if model_display.startswith("gemini-"):
        model_display = model_display[len("gemini-"):]
    parts.append(f"agy[{surface}] ({model_display})")

    # 2. Workspace & Git
    ws = agg.get("workspace")
    branch = agg.get("git_branch")
    ws_name = Path(ws).name if ws else ""
    if ws_name and branch:
        parts.append(f"{ws_name}:{branch}")
    elif ws_name:
        parts.append(ws_name)
    elif branch:
        parts.append(f"git:{branch}")

    # 3. Turns & Tool calls
    u_turns = agg.get("user_turns", 0)
    tools = agg.get("tool_calls", 0)
    parts.append(f"turn {u_turns} ({tools} tools)")

    # 4. Token totals
    tokens = agg.get("estimated_tokens", 0)
    parts.append(f"~{_fmt_tokens(tokens)} toks")

    # 5. Shadow Pricing
    cost = agg.get("cost_usd", 0.0)
    if agg.get("has_cost") and cost > 0:
        parts.append(f"≈${cost:.3f} api-equiv")

    # 6. Quota Health & Error Detector
    last_429 = agg.get("last_429_ts", 0)
    last_succ = agg.get("last_success_ts", 0)
    is_active_429 = (last_429 > 0 and last_429 > last_succ)

    if is_active_429:
        reset_in = agg.get("quota_reset_in") or ""
        reset_tag = f" (reset in {reset_in})" if reset_in else ""
        ago_s = max(0, int(time.time() - last_429 / 1000))
        parts.append(f"⚠RESOURCE-EXHAUSTED-429({ago_s}s ago{reset_tag})")
    elif agg.get("last_error_ts", 0) > last_succ:
        ago_s = max(0, int(time.time() - agg.get("last_error_ts") / 1000))
        parts.append(f"⚠ERROR({ago_s}s ago)")
    else:
        parts.append("quota: OK")

    return "  ·  ".join(parts)


# ---------------------------------------------------------------- commands


def cmd_line(cfg: dict, conversation_id: str | None = None) -> int:
    print(render_statusline(cfg, conversation_id=conversation_id))
    return 0


def cmd_once(cfg: dict, conversation_id: str | None = None) -> int:
    transcript_path, cid, surface = find_active_conversation(cfg, preferred_id=conversation_id)
    if not transcript_path or not transcript_path.exists():
        print(json.dumps({"status": "no_active_session"}, indent=2))
        return 1

    tracker = AgyTranscriptTracker(transcript_path, cid, cfg)
    agg = tracker.update()
    procs = inspect_agy_processes()

    snap = {
        "ts": int(time.time()),
        "ts_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "conversation_id": cid,
        "surface": surface,
        "workspace": agg.get("workspace"),
        "git_branch": agg.get("git_branch"),
        "model_id": agg.get("model_id"),
        "model_raw": agg.get("model_raw"),
        "metrics": {
            "steps_count": agg.get("steps_count"),
            "user_turns": agg.get("user_turns"),
            "planner_turns": agg.get("planner_turns"),
            "tool_calls": agg.get("tool_calls"),
            "estimated_tokens": agg.get("estimated_tokens"),
            "cost_usd": round(agg.get("cost_usd", 0.0), 4),
        },
        "quota": {
            "status": "RESOURCE_EXHAUSTED" if (agg.get("last_429_ts", 0) > agg.get("last_success_ts", 0)) else "OK",
            "last_success_ts": agg.get("last_success_ts"),
            "last_error_ts": agg.get("last_error_ts"),
            "last_429_ts": agg.get("last_429_ts"),
            "quota_reset_in": agg.get("quota_reset_in"),
        },
        "processes": procs,
    }

    print(json.dumps(snap, indent=2, ensure_ascii=False))
    write_json_atomic(quota_path(cfg), snap)
    return 0


def cmd_daemon(cfg: dict) -> int:
    interval = int(cfg.get("monitor", {}).get("poll_interval_s", 15))
    out = quota_path(cfg)
    stop = False

    def _sig(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    print(f"[agy-monitor] daemon watching AGY sessions (every {interval}s) -> {out}", flush=True)

    while not stop:
        try:
            transcript_path, cid, surface = find_active_conversation(cfg)
            if transcript_path and transcript_path.exists():
                tracker = AgyTranscriptTracker(transcript_path, cid, cfg)
                agg = tracker.update()
                procs = inspect_agy_processes()

                is_429 = (agg.get("last_429_ts", 0) > agg.get("last_success_ts", 0))
                snap = {
                    "ts": int(time.time()),
                    "ts_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "conversation_id": cid,
                    "surface": surface,
                    "workspace": agg.get("workspace"),
                    "git_branch": agg.get("git_branch"),
                    "model_id": agg.get("model_id"),
                    "metrics": {
                        "steps_count": agg.get("steps_count"),
                        "user_turns": agg.get("user_turns"),
                        "planner_turns": agg.get("planner_turns"),
                        "tool_calls": agg.get("tool_calls"),
                        "estimated_tokens": agg.get("estimated_tokens"),
                        "cost_usd": round(agg.get("cost_usd", 0.0), 4),
                    },
                    "quota": {
                        "status": "RESOURCE_EXHAUSTED" if is_429 else "OK",
                        "reset_in": agg.get("quota_reset_in"),
                    },
                    "processes": procs,
                }
                write_json_atomic(out, snap)
                q_stat = "⚠429 EXHAUSTED" if is_429 else "quota OK"
                print(f"[agy-monitor] {snap['ts_iso']} session={cid[:8]} turns={agg['user_turns']} {q_stat}", flush=True)
        except Exception as e:
            print(f"[agy-monitor] watch error: {e}", file=sys.stderr, flush=True)

        for _ in range(int(interval * 4)):
            if stop:
                break
            time.sleep(0.25)

    return 0


def cmd_sessions(cfg: dict) -> int:
    ide_brain = expand(cfg.get("paths", {}).get("ide_app_data", "~/.gemini/antigravity-ide")) / "brain"
    cli_brain = expand(cfg.get("paths", {}).get("cli_app_data", "~/.gemini/antigravity-cli")) / "brain"

    sessions = []
    for brain_dir, surface in [(ide_brain, "ide"), (cli_brain, "cli")]:
        if not brain_dir.exists():
            continue
        for p in brain_dir.glob("*/.system_generated/logs/transcript.jsonl"):
            try:
                cid = p.parent.parent.parent.name
                mtime = p.stat().st_mtime
                sessions.append((mtime, p, cid, surface))
            except OSError:
                pass

    if not sessions:
        print("No Antigravity sessions found.")
        return 0

    sessions.sort(key=lambda x: x[0], reverse=True)
    print(f"{'MODIFIED':<20} {'SURFACE':<8} {'TURNS':<7} {'EST. TOKENS':<12} {'COST (API-EQ)':<14} {'ID'}")
    print("-" * 80)
    for mtime, p, cid, surface in sessions[:15]:
        tracker = AgyTranscriptTracker(p, cid, cfg)
        agg = tracker.update()
        dt = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        u_turns = agg.get("user_turns", 0)
        toks = _fmt_tokens(agg.get("estimated_tokens", 0))
        cost = f"≈${agg.get('cost_usd', 0.0):.3f}" if agg.get("has_cost") else "-"
        print(f"{dt:<20} {surface:<8} {u_turns:<7} {toks:<12} {cost:<14} {cid}")
    return 0


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description="Google Antigravity (AGY) Quota & Session Monitor")
    ap.add_argument("mode", nargs="?", default="line", choices=["line", "once", "daemon", "sessions"])
    ap.add_argument("--conversation", "-c", help="Conversation ID to inspect (default: auto-detect active)")
    ap.add_argument("--config", help="Path to TOML config (default: ~/.gemini/agy-monitor/agy-monitor.toml)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.mode == "daemon":
        return cmd_daemon(cfg)
    if args.mode == "once":
        return cmd_once(cfg, conversation_id=args.conversation)
    if args.mode == "sessions":
        return cmd_sessions(cfg)
    return cmd_line(cfg, conversation_id=args.conversation)


if __name__ == "__main__":
    sys.exit(main())
