#!/usr/bin/env bash
# Sonda E2E message busa (plan vc-message-bus-hub, cut W4-01).
#
# Mierzy na żywym procesie vc-server (izolowany VIBECRAFTED_HOME):
#   1. agregacja: tools/list po /mcp zawiera vc_* i (gdy loctree :5174 żyje) loctree_*,
#   2. narzut agregatora: p50/p95 tools/call `find` (żywy loctree) bezpośrednio
#      vs przez /mcp vs przez vc-mcp-shim (stdio), N=100, surowe próbki w JSON;
#      tools/list jako metryka pomocnicza,
#   3. szyna: POST /api/bus/messages -> receipt inbox_pending w magazynie,
#   4. dostarczenie do tury: koperta doklejona do najbliższego tools/call runu
#      (nagłówek x-vibecrafted-run-id), nonce DERYWOWANY z run_id (bez pola
#      w meta — parytet z monitor_lane.run_delivery_nonce), stan
#      context_injected, bez dubla,
#   5. shim: stdio->HTTP z VIBECRAFTED_RUN_ID — ta sama koperta przez vc-mcp-shim,
#   6. tabela poziomów dostarczenia per provider (monitor_lane.capability_rows).
#
# Wyjście: JSON na stdout (--json) + tabela markdown na stderr. Exit 0 tylko,
# gdy tabela jest kompletna (8 CLI), sekcje 1,3,4,5 przeszły ORAZ — przy żywym
# loctree — pomiar narzutu doszedł do skutku. Pomiar żywych CLI innych niż
# mechanika serwera pozostaje poza sondą — patrz raport W4-01.
# `slice` przez tools/call nie jest mierzony: wymaga pliku istniejącego
# w domyślnym projekcie loctree, którego sonda nie zna — jawna luka.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_BIN="$REPO_ROOT/vibecrafted-server/target/debug/vibecrafted-server-web"
SHIM_BIN="$REPO_ROOT/vibecrafted-server/target/debug/vc-mcp-shim"

for bin in "$SERVER_BIN" "$SHIM_BIN"; do
  if [ ! -x "$bin" ]; then
    echo "brak binarki: $bin — zbuduj: cargo build -p vibecrafted-server-web --features ssr -p vc-mcp-shim" >&2
    exit 2
  fi
done

T="$(mktemp -d /tmp/vc-bus-probe-XXXXXX)"
cleanup() {
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true
  rm -rf "$T"
}
trap cleanup EXIT

export VIBECRAFTED_HOME="$T/home"
export XDG_CONFIG_HOME="$T/xdg"
export VC_SERVER_MCP_BEARER="probe-bearer-0123456789abcdef0123456789abcdef"
mkdir -p "$VIBECRAFTED_HOME/control_plane/runtime_runs" "$XDG_CONFIG_HOME/vibecrafted" "$T/bin"

# Upstream loctree dla agregatora (jeśli :5174 żyje, sekcja narzutu go zmierzy).
cat > "$XDG_CONFIG_HOME/vibecrafted/config.toml" <<CFG
[mcp.upstream.loctree]
url = "http://127.0.0.1:5174/mcp"
CFG

# Wrapper vibecrafted -> kod z tego drzewa (magazyn wiadomości).
cat > "$T/bin/vibecrafted" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$REPO_ROOT/vibecrafted-core"
exec python3 -c 'from vibecrafted_core.cli import main; main()' "\$@"
WRAP
chmod +x "$T/bin/vibecrafted"
export PATH="$T/bin:$PATH"

# Fixture runy: claude (pas MCP) i gemini (inbox po W1-03). Meta BEZ pola
# context_injection_nonce — pas musi zadziałać na nonce derywowanym z run_id
# (tak wygląda zwykły run z launchera; pole w meta to override/opt-out).
for spec in "probe-claude:claude" "probe-gemini:gemini"; do
  rid="${spec%%:*}"; agent="${spec##*:}"
  mkdir -p "$VIBECRAFTED_HOME/control_plane/runtime_runs/$rid"
  cat > "$VIBECRAFTED_HOME/control_plane/runtime_runs/$rid/meta.json" <<META
{
  "run_id": "$rid",
  "agent": "$agent",
  "status": "active",
  "runtime_session_id": "runtime-$rid",
  "worker_pid": $$,
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
META
done

PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
"$SERVER_BIN" --addr "127.0.0.1:$PORT" >"$T/server.log" 2>&1 &
SERVER_PID=$!

python3 - "$REPO_ROOT" "$PORT" "$SHIM_BIN" "$$" <<'PROBE'
import json, os, statistics, subprocess, sys, time, urllib.request, urllib.error

repo, port, shim_bin, probe_pid = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
base = f"http://127.0.0.1:{port}"
bearer = os.environ["VC_SERVER_MCP_BEARER"]
home = os.environ["VIBECRAFTED_HOME"]
sys.path.insert(0, os.path.join(repo, "vibecrafted-core"))
from vibecrafted_core import monitor_lane
out = {"schema": "vibecrafted.bus-e2e-probe.v1", "sections": {}, "ok": False}

def http(path, payload, headers=None, timeout=10):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {bearer}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:400]}

def rpc(method, params=None, headers=None, _id=[0]):
    _id[0] += 1
    status, body = http("/mcp", {"jsonrpc": "2.0", "id": _id[0], "method": method, "params": params or {}}, headers)
    assert status == 200, (method, status, body)
    assert "error" not in body, (method, body)
    return body["result"]

# czekaj na serwer
for _ in range(100):
    try:
        rpc("initialize", {"protocolVersion": "2025-03-26"})
        break
    except Exception:
        time.sleep(0.1)
else:
    print("serwer nie wstał", file=sys.stderr); sys.exit(2)

# 1. agregacja
tools = rpc("tools/list")["tools"]
names = [t["name"] for t in tools]
vc_ok = all(n in names for n in ("vc_ping", "vc_run_status", "vc_message_send", "vc_message_reply", "vc_message_status"))
loctree_names = [n for n in names if n.startswith("loctree_")]
out["sections"]["aggregation"] = {"ok": vc_ok, "tools": len(names), "loctree_tools": len(loctree_names)}

# 2. narzut: tools/call `find` na żywym loctree — bezpośrednio vs przez
# agregator (ścieżka shim dochodzi w sekcji 5). Surowe próbki zostają w JSON.
overhead = {"measured": False}
FIND_ARGS = {"name": "loctree_find", "arguments": {"name": "main"}}
def timed(fn, n=100):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter(); fn(); xs.append((time.perf_counter() - t0) * 1000)
    s = sorted(xs)
    return {"p50_ms": round(statistics.median(s), 2), "p95_ms": round(s[int(0.95 * len(s)) - 1], 2),
            "n": n, "samples_ms": [round(x, 3) for x in xs]}
if loctree_names:
    # bezpośredni klient loctree: initialize raz (sesja), potem tools/call
    _sess = {}
    def _direct(payload, capture=False):
        data = json.dumps(payload).encode()
        req = urllib.request.Request("http://127.0.0.1:5174/mcp", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if _sess.get("id"):
            req.add_header("Mcp-Session-Id", _sess["id"])
        with urllib.request.urlopen(req, timeout=10) as r:
            if capture:
                sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
                if sid: _sess["id"] = sid
            r.read()
    _direct({"jsonrpc": "2.0", "id": 0, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "probe", "version": "0"}}}, capture=True)
    _direct({"jsonrpc": "2.0", "method": "notifications/initialized"})
    def direct_find():
        _direct({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "find", "arguments": dict(FIND_ARGS["arguments"])}})
    def via_find():
        rpc("tools/call", FIND_ARGS)
    def direct_list():
        _direct({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    def via_list():
        rpc("tools/list")
    try:
        probe_result = rpc("tools/call", FIND_ARGS)
        assert not probe_result.get("isError"), probe_result
        direct_find()
        overhead = {
            "measured": True,
            "call_find": {"direct": timed(direct_find), "via_mcp": timed(via_find)},
            "list_tools": {"direct": timed(direct_list), "via_mcp": timed(via_list)},
            "slice_not_measured": "wymaga pliku z domyślnego projektu loctree — jawna luka",
        }
    except Exception as e:
        overhead = {"measured": False, "reason": str(e)[:200]}
out["sections"]["overhead"] = overhead

# 3. szyna: wysyłka do probe-claude
env_id = "e2e00000000000000000000000000001"
envelope = {
    "spec": "fleet.envelope/0.1", "id": env_id, "messageId": env_id,
    "source": "fleet:fable", "type": "fleet.message",
    "time": "2026-09-27T00:00:00Z",
    "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
    "contextId": "ctx-probe",
    "taskId": None, "referenceTaskIds": [], "role": "agent",
    "parts": [{"kind": "text", "text": "PROBE-ENVELOPE payload for injection"}],
    "recipient": "kodeksik", "hopCount": 0, "maxHops": 8,
    "status": None, "statusDetail": None,
    "metadata": {"vibecrafted": {"run_id": "probe-claude"}},
}
status, receipt = http("/api/bus/messages", envelope)
send_ok = status == 200 and receipt.get("delivery_state") == "inbox_pending"
out["sections"]["send"] = {"ok": send_ok, "status": status, "message_id": receipt.get("message_id"), "delivery_state": receipt.get("delivery_state"), "detail": (None if send_ok else receipt)}

# 4. wstrzyknięcie do tury (nagłówek run-id), dokładnie raz. Meta runu nie
# niesie nonce'a — blok musi przyjść z nonce'em DERYWOWANYM, dokładnie tym,
# który monitor_lane drukuje workerowi w prompcie starcia (parytet Rust↔python).
expected_nonce = monitor_lane.run_delivery_nonce("probe-claude")
r1 = rpc("tools/call", {"name": "vc_ping", "arguments": {}}, headers={"x-vibecrafted-run-id": "probe-claude", "x-vibecrafted-pid": probe_pid})
text1 = json.dumps(r1)
injected = f"[vibecrafted-bus nonce={expected_nonce}]" in text1 and "PROBE-ENVELOPE" in text1
r2 = rpc("tools/call", {"name": "vc_ping", "arguments": {}}, headers={"x-vibecrafted-run-id": "probe-claude", "x-vibecrafted-pid": probe_pid})
no_dup = "PROBE-ENVELOPE" not in json.dumps(r2)
# stan w magazynie
mid = receipt.get("message_id", "")
state = ""
p = os.path.join(home, "control_plane", "messages", f"{mid}.json")
if os.path.exists(p):
    state = json.load(open(p)).get("delivery_state", "")
out["sections"]["injection"] = {"ok": injected and no_dup and state == "context_injected", "injected": injected, "no_duplicate": no_dup, "store_state": state, "derived_nonce": expected_nonce}

# 5. shim: druga koperta, dostarczenie przez stdio
status, receipt2 = http("/api/bus/messages", {**envelope, "id": "e2e00000000000000000000000000002", "messageId": "e2e00000000000000000000000000002", "parts": [{"kind": "text", "text": "PROBE-SHIM payload"}]})
shim_env = {**os.environ, "VC_MCP_URL": f"{base}/mcp", "VIBECRAFTED_RUN_ID": "probe-claude"}
proc = subprocess.Popen([shim_bin], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=shim_env, text=True)
def shim_rpc(payload):
    proc.stdin.write(json.dumps(payload) + "\n"); proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line: return None
        msg = json.loads(line)
        if msg.get("id") == payload.get("id"): return msg
shim_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"); proc.stdin.flush()
resp = shim_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "vc_ping", "arguments": {}}})
shim_ok = resp is not None and "PROBE-SHIM" in json.dumps(resp)
# trzecia ścieżka narzutu: tools/call `find` przez shim (stdio->HTTP->agregator),
# z aktywnym VIBECRAFTED_RUN_ID — czyli z pełnym kosztem pasa kopert.
if shim_ok and overhead.get("measured"):
    _sid = [100]
    def shim_find():
        _sid[0] += 1
        shim_rpc({"jsonrpc": "2.0", "id": _sid[0], "method": "tools/call", "params": dict(FIND_ARGS)})
    try:
        shim_find()
        overhead["call_find"]["shim"] = timed(shim_find)
    except Exception as e:
        overhead["measured"] = False
        overhead["reason"] = f"shim benchmark: {str(e)[:160]}"
proc.stdin.close(); proc.terminate()
out["sections"]["shim"] = {"ok": shim_ok}

# 6. tabela poziomów
rows = [c.as_dict() for c in monitor_lane.capability_rows()]
out["sections"]["fleet_table"] = rows
complete = len(rows) >= 8

# Exit 0 wymaga KAŻDEJ mierzonej powierzchni: agregacji, wysyłki, wstrzyknięcia,
# shima, kompletnej tabeli — i narzutu, jeśli loctree żyje. Kontrprzykłady
# audytu F6 (zepsuty shim / brak benchmarku przy żywym loctree) dają exit 1.
overhead_ok = (not loctree_names) or bool(overhead.get("measured"))
out["ok"] = bool(vc_ok and send_ok and out["sections"]["injection"]["ok"] and shim_ok and complete and overhead_ok)
print("\n| provider | poziom deklarowany | monitor | fallback |", file=sys.stderr)
print("|---|---|---|---|", file=sys.stderr)
for r in rows:
    print(f"| {r['provider']} | {r['declared_level']} | {r['monitor']} | {r['when_unavailable']} |", file=sys.stderr)
print(json.dumps(out, indent=2, ensure_ascii=False))
sys.exit(0 if out["ok"] else 1)
PROBE
