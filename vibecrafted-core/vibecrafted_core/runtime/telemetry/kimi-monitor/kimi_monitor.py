#!/usr/bin/env python3
"""kimi_monitor.py — Quota poller, accounting engine & statusline runner for Kimi Code CLI.

Modes:
  daemon    Poll Kimi's local API (ephemeral kimi web probe) for quota state, write quota.json atomically.
  once      Single quota fetch, print JSON, write quota.json, exit (0 if ok, 1 if API error).
  line      Render statusline for latest active session from wire.jsonl + quota.json (no network).
  tui       Statusline runner for Kimi Code TUI: receives StatusLinePayload JSON on stdin.

Stdlib only. Requires Python >= 3.11 (tomllib).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- config

DEFAULT_CONFIG = """\
[paths]
kimi_binary = "~/.kimi-code/bin/kimi"
sessions_dir = "~/.kimi-code/sessions"
runtime_dir = "~/.kimi-code/runtime"

[quota]
poll_interval_s = 30
startup_timeout_s = 20

[statusline]
near_limit = 0.85
desync_ceiling = 0.95

# Per-model pricing per 1M tokens. Shadow pricing (api-equiv).
[pricing.k3]
fresh = 3.0
cache_read = 0.30
output = 15.0

[pricing.kimi-k3]
fresh = 3.0
cache_read = 0.30
output = 15.0

[pricing.k3-256k]
fresh = 3.0
cache_read = 0.30
output = 15.0

[pricing.kimi-for-coding]
fresh = 3.0
cache_read = 0.30
output = 15.0

[pricing.kimi-for-coding-highspeed]
fresh = 0.95
cache_read = 0.19
output = 4.0
"""


def load_config(path: str | None = None) -> dict:
    if path is None:
        default = Path.home() / ".kimi-code" / "kimi-monitor.toml"
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
    return expand(cfg.get("paths", {}).get("runtime_dir", "~/.kimi-code/runtime")) / "quota.json"


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


def get_model_rates(model_name: str, pricing_cfg: dict) -> dict | None:
    if not model_name or not pricing_cfg:
        return None
    p = pricing_cfg.get(model_name)
    if not p and model_name.startswith("kimi-"):
        p = pricing_cfg.get(model_name[len("kimi-"):])
    if isinstance(p, dict) and p.get("fresh", 0.0) > 0:
        return {
            "fresh_in": float(p.get("fresh", 0.0)),
            "cache_in": float(p.get("cache_read", 0.0)),
            "output": float(p.get("output", 0.0)),
        }
    return None


# ---------------------------------------------------------------- ephemeral probe


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class KimiWebProbe:
    """Manages an ephemeral `kimi web` instance, queries its local API, and shuts it down."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.base = ""

    def start(self) -> None:
        port = _free_port()
        self.base = f"http://127.0.0.1:{port}"
        binary = str(expand(self.cfg.get("paths", {}).get("kimi_binary", "~/.kimi-code/bin/kimi")))
        timeout = float(self.cfg.get("quota", {}).get("startup_timeout_s", 20))
        self.proc = subprocess.Popen(
            [binary, "web", "--port", str(port), "--no-open", "--dangerous-bypass-auth"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"kimi web exited early rc={self.proc.returncode}")
            try:
                self._req("/api/v1/healthz")
                return
            except Exception:
                time.sleep(0.20)
        self.stop()
        raise RuntimeError("timeout waiting for kimi web healthz")

    def _req(self, path: str, method: str = "GET") -> dict:
        r = urllib.request.Request(self.base + path, method=method)
        with urllib.request.urlopen(r, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def req_data(self, path: str, method: str = "GET") -> dict:
        res = self._req(path, method=method)
        return res.get("data", res) if isinstance(res, dict) else res

    def stop(self) -> None:
        if self.proc:
            try:
                self._req("/api/v1/shutdown", method="POST")
            except Exception:
                pass
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            self.proc = None


def build_snapshot(auth_data: dict, info_data: dict, usage_data: dict) -> dict:
    snap = {
        "ts": int(time.time()),
        "ts_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "auth": (auth_data.get("managed_provider") or {}).get("status", "unknown"),
        "kind": usage_data.get("kind", "error"),
    }
    ui = (info_data.get("userInfo") or {}) if isinstance(info_data, dict) else {}
    snap["plan"] = ui.get("userLevelName")
    snap["account_status"] = ui.get("status")

    quota = (usage_data.get("quota") or {}) if usage_data.get("kind") == "ok" else {}
    usages = quota.get("usages") or {}
    for key in ("limit5h", "monthTotal", "monthCode"):
        u = usages.get(key) or {}
        snap[key] = {"usedRatio": u.get("usedRatio"), "resetAt": u.get("resetAt")}

    if usage_data.get("kind") != "ok":
        snap["error"] = {"status": usage_data.get("status"), "message": usage_data.get("message")}
    return snap


def cmd_once(cfg: dict) -> int:
    probe = KimiWebProbe(cfg)
    try:
        probe.start()
        auth_data = probe.req_data("/api/v1/auth")
        info_data = probe.req_data("/api/v1/oauth/userinfo")
        usage_data = probe.req_data("/api/v1/oauth/usage")
    finally:
        probe.stop()

    snap = build_snapshot(auth_data, info_data, usage_data)
    print(json.dumps(snap, indent=2, ensure_ascii=False))
    write_json_atomic(quota_path(cfg), snap)
    return 0 if snap.get("kind") == "ok" else 1


def cmd_daemon(cfg: dict) -> int:
    interval = int(cfg.get("quota", {}).get("poll_interval_s", 30))
    out = quota_path(cfg)
    stop = False

    def _sig(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    print(f"[kimi-monitor] daemon started (ephemeral probe every {interval}s) -> {out}", flush=True)

    last_known_good = read_json(out) or {}
    last_userinfo_ts = 0
    cached_userinfo = {}
    cached_auth = "unknown"

    while not stop:
        probe = KimiWebProbe(cfg)
        now = time.time()
        try:
            probe.start()
            # Only fetch userinfo/auth on startup or once per hour
            if (now - last_userinfo_ts > 3600) or not cached_userinfo:
                try:
                    auth_data = probe.req_data("/api/v1/auth")
                    cached_auth = (auth_data.get("managed_provider") or {}).get("status", "unknown")
                    info_data = probe.req_data("/api/v1/oauth/userinfo")
                    cached_userinfo = (info_data.get("userInfo") or {}) if isinstance(info_data, dict) else {}
                    last_userinfo_ts = now
                except Exception:
                    pass

            usage_data = probe.req_data("/api/v1/oauth/usage")
            probe.stop()

            if usage_data.get("kind") == "ok":
                quota = usage_data.get("quota") or {}
                usages = quota.get("usages") or {}
                snap = {
                    "ts": int(now),
                    "ts_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "auth": cached_auth,
                    "kind": "ok",
                    "plan": cached_userinfo.get("userLevelName"),
                    "account_status": cached_userinfo.get("status"),
                    "last_poll_error": None,
                    "last_poll_error_ts": None,
                }
                for key in ("limit5h", "monthTotal", "monthCode"):
                    u = usages.get(key) or {}
                    snap[key] = {"usedRatio": u.get("usedRatio"), "resetAt": u.get("resetAt")}
                last_known_good = snap
                write_json_atomic(out, snap)
                r5 = (snap.get("limit5h") or {}).get("usedRatio")
                print(f"[kimi-monitor] {snap['ts_iso']} 5h={r5} auth={snap['auth']}", flush=True)
            else:
                err_msg = usage_data.get("message") or usage_data.get("kind") or "unknown"
                raise RuntimeError(f"usage API error: {err_msg}")

        except Exception as e:
            probe.stop()
            # Preserve last-known-good quota data; record poll error without breaking statusline
            if last_known_good and last_known_good.get("kind") == "ok":
                snap = dict(last_known_good)
                snap["last_poll_error"] = str(e)
                snap["last_poll_error_ts"] = int(time.time())
                write_json_atomic(out, snap)
            else:
                write_json_atomic(out, {
                    "ts": int(time.time()),
                    "kind": "error",
                    "error": str(e),
                    "last_poll_error": str(e),
                    "last_poll_error_ts": int(time.time()),
                })
            print(f"[kimi-monitor] poll error: {e}", file=sys.stderr, flush=True)

        for _ in range(int(interval * 4)):
            if stop:
                break
            time.sleep(0.25)

    return 0


# ---------------------------------------------------------------- wire accounting


def _find_latest_session_id(sessions_dir: Path) -> str | None:
    if not sessions_dir.exists():
        return None
    candidates = []
    for p in sessions_dir.glob("*/session_*"):
        try:
            candidates.append((p.stat().st_mtime, p.name))
        except OSError:
            pass
    if not candidates:
        return None
    candidates.sort(reverse=True)
    latest_name = candidates[0][1]
    if latest_name.startswith("session_"):
        return latest_name[len("session_"):]
    return latest_name


class SessionWireTracker:
    """Incrementally parses wire.jsonl files for a given session with safe offsets & chronological tracking."""

    def __init__(self, cfg: dict, session_id: str | None = None):
        self.cfg = cfg
        self.pricing_cfg = cfg.get("pricing", {})
        self.sessions_dir = expand(cfg.get("paths", {}).get("sessions_dir", "~/.kimi-code/sessions"))
        if not session_id:
            session_id = _find_latest_session_id(self.sessions_dir) or ""
        self.session_id = session_id
        self.session_dir_id = session_id[len("session_"):] if session_id.startswith("session_") else session_id

    def update(self) -> dict:
        agg = {
            "fresh_in": 0,
            "cache_read": 0,
            "output": 0,
            "cost_usd": 0.0,
            "has_cost": False,
            "ctx_tokens": 0,
            "ctx_max": 0,
            "model": None,
            "last_success_ts": 0,
            "last_error_ts": 0,
            "last_error_status": None,
            "last_error_msg": "",
            "last_403_ts": 0,
            "last_403_msg": "",
        }

        if not self.session_dir_id:
            return agg

        cache_dir = Path(f"/tmp/kimi_statusline_cache_{os.getuid()}")
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{self.session_id}.json"

        files_cache = {}
        if cache_file.exists():
            try:
                cdata = json.loads(cache_file.read_text(encoding="utf-8"))
                if cdata.get("v") == 3:
                    files_cache = cdata.get("files", {})
            except Exception:
                files_cache = {}

        pattern = str(self.sessions_dir / "*" / f"session_{self.session_dir_id}" / "agents" / "*" / "wire.jsonl")
        wires = glob.glob(pattern)

        for wire_path in wires:
            entry = files_cache.get(wire_path) or {}
            offset = entry.get("offset", 0)
            f_fresh = entry.get("fresh_in", 0)
            f_cache = entry.get("cache_in", 0)
            f_out = entry.get("out_tok", 0)
            f_cost = entry.get("cost", 0.0)
            f_model = entry.get("model")
            f_ctx_tokens = entry.get("ctx_tokens", 0)
            f_ctx_max = entry.get("ctx_max", 0)
            f_last_succ = entry.get("last_succ", 0)
            f_last_err = entry.get("last_err", 0)
            f_last_err_status = entry.get("last_err_status")
            f_last_err_msg = entry.get("last_err_msg", "")
            f_last_403 = entry.get("last_403", 0)
            f_last_403_msg = entry.get("last_403_msg", "")

            try:
                file_size = os.path.getsize(wire_path)
                if file_size < offset:
                    offset, f_fresh, f_cache, f_out, f_cost = 0, 0, 0, 0, 0.0
                    f_last_succ, f_last_err, f_last_403 = 0, 0, 0
                    f_last_err_status, f_last_err_msg, f_last_403_msg = None, "", ""

                with open(wire_path, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(offset)
                    while True:
                        pos = f.tell()
                        line = f.readline()
                        if not line or not line.endswith("\n"):
                            f.seek(pos)
                            break
                        if '"usage.record"' not in line and '"turn.ended"' not in line and '"token_counting' not in line:
                            continue
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        ev_type = d.get("type")
                        if ev_type == "usage.record" and d.get("usageScope") == "turn":
                            u = d.get("usage") or {}
                            rec_model = str(d.get("model", "")).replace("kimi-code/", "")
                            if rec_model:
                                f_model = rec_model
                            rates = get_model_rates(rec_model, self.pricing_cfg)
                            fi = u.get("inputOther", 0) + u.get("inputCacheCreation", 0)
                            ci = u.get("inputCacheRead", 0)
                            ot = u.get("output", 0)
                            f_fresh += fi
                            f_cache += ci
                            f_out += ot
                            if rates:
                                f_cost += (fi * rates["fresh_in"] + ci * rates["cache_in"] + ot * rates["output"]) / 1_000_000
                                agg["has_cost"] = True
                        elif ev_type == "token_counting.turn_recorded":
                            f_ctx_tokens = max(f_ctx_tokens, d.get("tokens") or 0)
                        elif ev_type == "llm.request":
                            f_ctx_max = max(f_ctx_max, d.get("maxTokens") or 0)
                        elif ev_type == "turn.ended":
                            ev_time = d.get("time") or int(time.time() * 1000)
                            reason = d.get("reason")
                            err = d.get("error") or {}
                            msg = str(err.get("message") or "")
                            if reason == "completed":
                                if ev_time > f_last_succ:
                                    f_last_succ = ev_time
                            elif reason == "failed":
                                if ev_time > f_last_err:
                                    f_last_err = ev_time
                                    f_last_err_status = 403 if "403" in msg else 500
                                    f_last_err_msg = msg[:200]
                                if "403" in msg and any(k in msg.lower() for k in ("5-hour", "5h", "limit", "quota")):
                                    if ev_time > f_last_403:
                                        f_last_403 = ev_time
                                        f_last_403_msg = msg[:200]

                    files_cache[wire_path] = {
                        "offset": f.tell(),
                        "fresh_in": f_fresh,
                        "cache_in": f_cache,
                        "out_tok": f_out,
                        "cost": f_cost,
                        "model": f_model,
                        "ctx_tokens": f_ctx_tokens,
                        "ctx_max": f_ctx_max,
                        "last_succ": f_last_succ,
                        "last_err": f_last_err,
                        "last_err_status": f_last_err_status,
                        "last_err_msg": f_last_err_msg,
                        "last_403": f_last_403,
                        "last_403_msg": f_last_403_msg,
                    }
            except Exception:
                pass

            agg["fresh_in"] += f_fresh
            agg["cache_read"] += f_cache
            agg["output"] += f_out
            agg["cost_usd"] += f_cost
            if f_model and not agg["model"]:
                agg["model"] = f_model
            agg["ctx_tokens"] = max(agg["ctx_tokens"], f_ctx_tokens)
            agg["ctx_max"] = max(agg["ctx_max"], f_ctx_max)

            # Chronological global tracking across all wires
            if f_last_succ > agg["last_success_ts"]:
                agg["last_success_ts"] = f_last_succ
            if f_last_err > agg["last_error_ts"]:
                agg["last_error_ts"] = f_last_err
                agg["last_error_status"] = f_last_err_status
                agg["last_error_msg"] = f_last_err_msg
            if f_last_403 > agg["last_403_ts"]:
                agg["last_403_ts"] = f_last_403
                agg["last_403_msg"] = f_last_403_msg

        try:
            cache_file.write_text(json.dumps({"v": 3, "files": files_cache}), encoding="utf-8")
        except Exception:
            pass

        return agg


# ---------------------------------------------------------------- statusline formatting


def _fmt_tokens(n: int | float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(int(n))


def _reset_in(reset_at: str | None) -> str:
    if not reset_at:
        return "?"
    try:
        dt = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
        delta = dt - datetime.now(timezone.utc)
        secs = max(0, int(delta.total_seconds()))
        return f"{secs // 3600}:{(secs % 3600) // 60:02d}"
    except Exception:
        return "?"


def render_statusline(payload: dict | None = None, cfg: dict | None = None) -> str:
    if cfg is None:
        cfg = load_config()

    session_id = ""
    model = "kimi"
    branch = ""
    perm_mode = "manual"
    plan_mode = False
    ctx_tokens = 0
    max_ctx = 0

    if payload:
        model_raw = payload.get("model", "kimi")
        if isinstance(model_raw, dict):
            model_raw = model_raw.get("id") or model_raw.get("name") or "kimi"
        model = str(model_raw).replace("kimi-code/", "")
        branch_raw = payload.get("gitBranch") or ""
        if isinstance(branch_raw, dict):
            branch_raw = branch_raw.get("name") or branch_raw.get("branch") or ""
        branch = str(branch_raw)
        perm_mode = str(payload.get("permissionMode") or "manual")
        plan_mode = bool(payload.get("planMode", False))
        c_tok = payload.get("contextTokens")
        if isinstance(c_tok, (int, float)):
            ctx_tokens = c_tok
        m_tok = payload.get("maxContextTokens")
        if isinstance(m_tok, (int, float)):
            max_ctx = m_tok
        s_id = payload.get("sessionId")
        if isinstance(s_id, str):
            session_id = s_id

    tracker = SessionWireTracker(cfg, session_id=session_id)
    agg = tracker.update()

    if not payload:
        model = agg.get("model") or "kimi"
        ctx_tokens = agg.get("ctx_tokens", 0)
        max_ctx = agg.get("ctx_max", 0)

    total_in = agg["fresh_in"] + agg["cache_read"]
    total_tokens = total_in + agg["output"]
    cache_pct = (agg["cache_read"] / total_in * 100) if total_in > 0 else 0
    ctx_pct = (ctx_tokens / max_ctx * 100) if max_ctx > 0 else 0

    parts = []

    # 1. Model & Mode
    mode_tag = f" ({perm_mode})" if perm_mode != "manual" else ""
    if plan_mode:
        mode_tag += " [plan]"
    parts.append(f"{model}{mode_tag}")

    # 2. Git branch
    if branch:
        parts.append(f"git:{branch}")

    # 3. Context Window
    if max_ctx > 0:
        parts.append(f"ctx: {_fmt_tokens(ctx_tokens)}/{_fmt_tokens(max_ctx)} ({ctx_pct:.1f}%)")

    # 4. Cumulative tokens & Cache efficiency
    if total_tokens > 0:
        if cache_pct > 0:
            parts.append(f"{_fmt_tokens(total_tokens)} toks ({cache_pct:.0f}% cache)")
        else:
            parts.append(f"{_fmt_tokens(total_tokens)} toks")

    # 5. Shadow Pricing (API equivalent cost, omitted if unpriced)
    cost = agg.get("cost_usd", 0.0)
    if (agg.get("has_cost") or cost > 0) and total_tokens > 0:
        parts.append(f"≈${cost:.3f} api-equiv")

    # 6. Live Quota & Desync Detector
    quota = read_json(quota_path(cfg))
    poll_interval = int(cfg.get("quota", {}).get("poll_interval_s", 30))
    near_limit = float(cfg.get("statusline", {}).get("near_limit", 0.85))
    desync_ceiling = float(cfg.get("statusline", {}).get("desync_ceiling", 0.95))

    warnings = []
    if quota and quota.get("kind") == "ok":
        age = int(time.time()) - int(quota.get("ts", 0))
        if age > 3 * poll_interval:
            warnings.append("STALE-QUOTA")

        l5h = quota.get("limit5h") or {}
        mt = quota.get("monthTotal") or {}
        mc = quota.get("monthCode") or {}
        r5 = float(l5h.get("usedRatio") or 0.0)
        rm = float(mt.get("usedRatio") or 0.0)
        rc = float(mc.get("usedRatio") or 0.0)

        reset_str = _reset_in(l5h.get("resetAt"))
        parts.append(f"5h {r5 * 100:.0f}%↻{reset_str}")
        parts.append(f"M {rm * 100:.0f}%·c{rc * 100:.0f}%")

        # Chronological Desync & Limit Detector
        # An active 403 quota error exists ONLY if the most recent turn ended in a 403
        # and has NOT been superseded by a subsequent successful inference turn!
        last_403 = agg.get("last_403_ts", 0)
        last_succ = agg.get("last_success_ts", 0)
        is_active_403 = (last_403 > 0 and last_403 > last_succ)

        if is_active_403:
            if r5 < desync_ceiling:
                ago_s = max(0, int(time.time() - last_403 / 1000))
                warnings.append(f"QUOTA-DESYNC({ago_s}s)")
            else:
                warnings.append("NEAR-5H-LIMIT")
        elif r5 >= near_limit:
            warnings.append("NEAR-5H-LIMIT")
        elif rm >= near_limit:
            warnings.append("MONTHLY-HIGH")
    else:
        warnings.append("STALE-QUOTA")
        parts.append("quota ?")

    for w in warnings:
        parts.append(f"⚠{w}")

    return "  ·  ".join(parts)


def cmd_line(cfg: dict) -> int:
    line = render_statusline(payload=None, cfg=cfg)
    print(line)
    return 0


def cmd_tui(cfg: dict | None = None) -> int:
    try:
        raw = sys.stdin.read().strip()
        payload = json.loads(raw) if raw else None
    except Exception:
        payload = None
    line = render_statusline(payload=payload, cfg=cfg)
    print(line)
    return 0


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description="Kimi Code Quota Monitor & Statusline")
    ap.add_argument("mode", nargs="?", default="line", choices=["daemon", "once", "line", "tui"])
    ap.add_argument("--config", help="path to TOML config (default: ~/.kimi-code/kimi-monitor.toml)")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.mode == "daemon":
        return cmd_daemon(cfg)
    if args.mode == "once":
        return cmd_once(cfg)
    if args.mode == "tui":
        return cmd_tui(cfg)
    return cmd_line(cfg)


if __name__ == "__main__":
    sys.exit(main())
