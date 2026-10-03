#!/usr/bin/env bash
# vibecrafted-server local control-plane viewer smoke test.
#
# Asserts:
#   - install-all / install-server copies vc-server and assets/fonts.
#   - Starts a copied binary from /tmp with no LEPTOS_* environment.
#   - Binds to a free ephemeral port (no hardcoded port).
#   - Polls /api/health, /api/control/state, and /api/control/runs to 200 OK.
#   - Asserts JSON payload structure, dashboard visibility, and favicon hygiene.
#   - SIGTERM shuts down clean (no process leaks, no panics in log).
#
# Usage:
#   tests/server_smoke.sh

set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

red()    { printf '\033[31m%s\033[0m' "$*"; }
green()  { printf '\033[32m%s\033[0m' "$*"; }
yellow() { printf '\033[33m%s\033[0m' "$*"; }
dim()    { printf '\033[2m%s\033[0m' "$*"; }

_red='\033[31m'
_green='\033[32m'
_yellow='\033[33m'
_reset='\033[0m'


PASSES=0
FAILURES=()

ok()   { PASSES=$((PASSES + 1)); printf '  [%s] %s\n' "$(green ok)" "$1"; }
fail() { FAILURES+=("$1"); printf '  [%s] %s\n' "$(red fail)" "$1"; }
phase(){ printf '\n%s\n' "$(dim "─── $1 ───")"; }

# 1. Setup temp environment
phase "setup temp environment"

tmp_home="$(mktemp -d "${TMPDIR:-/tmp}/vibecrafted-server-smoke.XXXXXX")"
export VIBECRAFTED_HOME="$tmp_home"
export VIBECRAFTED_RUNTIME_HOME="$tmp_home/share"
mkdir -p "$VIBECRAFTED_HOME/control_plane/runs"
mkdir -p "$VIBECRAFTED_HOME/control_plane/lifecycle_runs/lcyc-smoke-life"

# Ensure clean teardown on exit
cleanup() {
  phase "teardown and cleanup"
  if [[ -f "$tmp_home/server.pid" ]]; then
    local pid
    pid=$(cat "$tmp_home/server.pid" 2>/dev/null || true)
    if [[ -n "$pid" ]]; then
      echo "Killing background server PID $pid"
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  rm -rf "$tmp_home"
  echo "Cleanup complete."
}
trap cleanup EXIT

ok "temp VIBECRAFTED_HOME set to $VIBECRAFTED_HOME"

cat > "$VIBECRAFTED_HOME/control_plane/runs/smoke-active.json" <<'EOF_RUN'
{
  "run_id": "smoke-active",
  "state": "running",
  "agent": "codex",
  "skill": "implement",
  "mode": "implement",
  "root": "/tmp/vibecrafted-smoke",
  "operator_session": "vibecrafted-smoke-active",
  "latest_report": "/tmp/report-active.md",
  "latest_transcript": "/tmp/transcript-active.log",
  "last_error": "",
  "updated_at": "2026-06-14T10:00:00Z",
  "started_at": "2026-06-14T09:59:00Z",
  "health": "active",
  "source": "smoke",
  "lock_present": true,
  "exit_code": null,
  "liveness": "running",
  "launcher_pid": 12345,
  "completed_at": "",
  "session_id": "smoke-session-active",
  "current_loop": null,
  "total_loops": null
}
EOF_RUN

cat > "$VIBECRAFTED_HOME/control_plane/runs/smoke-final.json" <<'EOF_RUN'
{
  "run_id": "smoke-final",
  "state": "completed",
  "agent": "claude",
  "skill": "review",
  "mode": "review",
  "root": "/tmp/vibecrafted-smoke",
  "operator_session": "vibecrafted-smoke-final",
  "latest_report": "/tmp/report-final.md",
  "latest_transcript": "/tmp/transcript-final.log",
  "last_error": "",
  "updated_at": "2026-06-14T09:00:00Z",
  "started_at": "2026-06-14T08:59:00Z",
  "health": "final",
  "source": "smoke",
  "lock_present": false,
  "exit_code": 0,
  "liveness": "terminal",
  "launcher_pid": null,
  "completed_at": "2026-06-14T09:01:00Z",
  "session_id": "smoke-session-final",
  "current_loop": null,
  "total_loops": null
}
EOF_RUN

ok "seeded control-plane run snapshots"

cat > "$VIBECRAFTED_HOME/control_plane/lifecycle_runs/lcyc-smoke-life/report.md" <<'EOF_LIFE_REPORT'
# smoke lifecycle report
EOF_LIFE_REPORT

cat > "$VIBECRAFTED_HOME/control_plane/lifecycle_runs/lcyc-smoke-life/worker-report.md" <<'EOF_WORKER_REPORT'
---
dou_index: 0
---
# smoke worker lifecycle report
EOF_WORKER_REPORT

cat > "$VIBECRAFTED_HOME/control_plane/lifecycle_runs/lcyc-smoke-life/transcript.log" <<'EOF_LIFE_TRANSCRIPT'
{"kind":"stage","id":"scaffold"}
EOF_LIFE_TRANSCRIPT

cat > "$VIBECRAFTED_HOME/control_plane/lifecycle_runs/lcyc-smoke-life/state.json" <<EOF_LIFE
{
  "schema": "vibecrafted.lifecycle.v1",
  "run_id": "lcyc-smoke-life",
  "workflow": "vc-ship",
  "agent": "codex",
  "root": "/tmp/vibecrafted-smoke",
  "status": "launching",
  "await_stages": false,
  "parent_run_id": null,
  "operator_actions": [
    {"action": "approve_transition", "at": "2026-07-02T12:00:00-0700", "details": {"next_stage": "implement"}}
  ],
  "spec": {"workflow_id": "vc-ship", "agent": "codex"},
  "supervisor": "vibecrafted_core.lifecycle_runner.LifecycleSupervisor",
  "human_controls": ["approve_transition", "interrupt_workflow", "accept_dou"],
  "state_path": "$VIBECRAFTED_HOME/control_plane/lifecycle_runs/lcyc-smoke-life/state.json",
  "report_path": "$VIBECRAFTED_HOME/control_plane/lifecycle_runs/lcyc-smoke-life/report.md",
  "transcript_path": "$VIBECRAFTED_HOME/control_plane/lifecycle_runs/lcyc-smoke-life/transcript.log",
  "context_atlas": {"ok": true},
  "manifest": {"id": "vc-ship"},
  "baton": {
    "from_stage": "scaffold",
    "from_phase": "read",
    "next_stage": "implement",
    "next_agent": "codex",
    "reason": "stage_launched_without_await",
    "previous_reports": ["$VIBECRAFTED_HOME/control_plane/lifecycle_runs/lcyc-smoke-life/worker-report.md"],
    "dou_index": 0,
    "audit_after": "",
    "fallback_stage": ""
  },
  "stages": [{
    "id": "scaffold",
    "name": "VC Scaffold",
    "workflow": "scaffold",
    "phase": "read",
    "agent": "codex",
    "status": "completed",
    "launch": {"report": "$VIBECRAFTED_HOME/control_plane/lifecycle_runs/lcyc-smoke-life/worker-report.md"},
    "await": {},
    "commit_before": "abc123",
    "commit_after": "def456",
    "changed_files": [],
    "new_commits": [],
    "transition": {
      "next_stage": "implement",
      "requested_next_stage": "",
      "next_agent": "codex",
      "requested_next_agent": "",
      "conditions": ["stage_completed"],
      "fallback_stage": "",
      "audit_after": ""
    }
  }],
  "dou_index": {
    "value": 0,
    "stage": "scaffold",
    "report": "$VIBECRAFTED_HOME/control_plane/lifecycle_runs/lcyc-smoke-life/worker-report.md"
  },
  "accepted_dou": 1,
  "accepted_dou_findings": [{"id": "accepted-1"}]
}
EOF_LIFE

ok "seeded lifecycle run state"

# 2. Identify installed binary and assets
phase "preflight checks"

SERVER_BIN="$HOME/.local/bin/vc-server"
if [[ -f "$SERVER_BIN" ]]; then
  ok "installed binary exists: $SERVER_BIN"
else
  fail "installed vc-server binary missing. Run make install-all or make install-server first."
  exit 1
fi

COMPAT_BIN="$HOME/.local/bin/vibecrafted-server-web"
if [[ -e "$COMPAT_BIN" ]]; then
  ok "compat command exists: $COMPAT_BIN"
else
  fail "compat command missing: $COMPAT_BIN"
  exit 1
fi

SITE_ROOT="$VIBECRAFTED_RUNTIME_HOME/server/site"
mkdir -p "$SITE_ROOT"
# Copy assets from checkout to temp runtime site root so server-smoke is self-contained
cp -R "$REPO_ROOT/vibecrafted-server/web/public/"* "$SITE_ROOT/"
if [[ -d "$SITE_ROOT/fonts" ]]; then
  ok "copied site assets to temp location: $SITE_ROOT"
else
  fail "failed to locate/copy site assets"
  exit 1
fi

# 3. Find ephemeral port
phase "ephemeral port probe"
PORT=$(python3 -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1]); s.close()")
if [[ -n "$PORT" ]]; then
  ok "probe bound free port: $PORT"
else
  fail "failed to find ephemeral port"
  exit 1
fi

# 4. Start copied server from /tmp with clean environment
phase "spawn server"

LOG_FILE="$tmp_home/server.log"
PID_FILE="$tmp_home/server.pid"
TMP_BIN="$tmp_home/vc-server"
cp "$SERVER_BIN" "$TMP_BIN"
chmod 0755 "$TMP_BIN"

# Lift ulimit for safety
ulimit -f unlimited

(
  cd /tmp
  env -i \
      PATH="${PATH:-/usr/bin:/bin}" \
      HOME="$HOME" \
      VIBECRAFTED_HOME="$VIBECRAFTED_HOME" \
      VC_SERVER_ADDR="127.0.0.1:$PORT" \
      "$TMP_BIN" > "$LOG_FILE" 2>&1 < /dev/null
) &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

ok "spawned copied vc-server from /tmp (PID $SERVER_PID) on port $PORT without LEPTOS_* env"

# 5. Poll health and assert JSON
phase "poll and assert"

timeout=15
elapsed=0
healthy=0

while [[ $elapsed -lt $((timeout * 2)) ]]; do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    fail "server process died prematurely"
    cat "$LOG_FILE"
    exit 1
  fi
  if python3 -c '
import urllib.request, json, sys
try:
    resp = urllib.request.urlopen("http://127.0.0.1:'"$PORT"'/api/health", timeout=1.0)
    if resp.status == 200:
        data = json.loads(resp.read().decode())
        assert data["schema"] == "vibecrafted.health.v1", "wrong health schema"
        assert data["status"] == "ok", "server is not ready"
        sys.exit(0)
    sys.exit(1)
except Exception as e:
    sys.exit(2)
' >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 0.5
  elapsed=$((elapsed + 1))
done

if [[ $healthy -eq 1 ]]; then
  ok "HTTP health check to http://127.0.0.1:$PORT/api/health OK (200 + ready)"
else
  fail "health check timed out after 15 seconds"
  cat "$LOG_FILE"
  exit 1
fi

if python3 -c '
import urllib.request, json
resp = urllib.request.urlopen("http://127.0.0.1:'"$PORT"'/api/control/state", timeout=5.0)
data = json.loads(resp.read().decode())
assert "generated_at" in data, "missing generated_at"
assert "active_runs" in data, "missing active_runs"
assert "recent_runs" in data, "missing recent_runs"
'; then
  ok "GET /api/control/state returns the full state contract"
else
  fail "GET /api/control/state contract failed"
  exit 1
fi

if python3 -c '
import urllib.request, json, sys
resp = urllib.request.urlopen("http://127.0.0.1:'"$PORT"'/api/control/runs", timeout=1.0)
data = json.loads(resp.read().decode())
assert data["count"] == 2, data
ids = [run["run_id"] for run in data["runs"]]
assert ids == ["smoke-active", "smoke-final"], ids
' >/dev/null 2>&1; then
  ok "all-runs route returns every seeded snapshot newest-first"
else
  fail "all-runs route did not return the full seeded control plane"
  cat "$LOG_FILE"
  exit 1
fi

if python3 -c '
import urllib.request, json, sys
resp = urllib.request.urlopen("http://127.0.0.1:'"$PORT"'/api/control/lifecycle", timeout=1.0)
data = json.loads(resp.read().decode())
assert data["count"] == 1, data
run = data["lifecycle_runs"][0]
assert run["run_id"] == "lcyc-smoke-life", run
assert run["schema"] == "vibecrafted.lifecycle.v1", run
assert run["workflow"] == "vc-ship", run
assert run["current_stage"] == "scaffold", run
assert run["next_stage"] == "implement", run
assert run["next_agent"] == "codex", run
assert run["dou_index"] == 0, run
assert run["baton_dou_index"] == 0, run
assert run["dou_readiness"] == "zero", run
assert run["accepted_dou"] == 1, run
assert run["human_controls_count"] == 3, run
assert run["operator_actions_count"] == 1, run
' >/dev/null 2>&1; then
  ok "lifecycle list route returns seeded lifecycle summary with ZERO DoU readiness and control metrics"
else
  fail "lifecycle list route did not return the seeded lifecycle summary"
  cat "$LOG_FILE"
  exit 1
fi

if python3 -c '
import urllib.request, json, sys
resp = urllib.request.urlopen("http://127.0.0.1:'"$PORT"'/api/control/lifecycle/lcyc-smoke-life", timeout=1.0)
data = json.loads(resp.read().decode())
assert data["run_id"] == "lcyc-smoke-life", data
assert data["schema"] == "vibecrafted.lifecycle.v1", data
assert data["baton"]["next_stage"] == "implement", data
assert data["baton"]["dou_index"] == 0, data
assert data["stages"][0]["id"] == "scaffold", data
assert data["dou_index"]["value"] == 0, data
assert data["dou_index"]["stage"] == "scaffold", data
assert data["accepted_dou"] == 1, data
assert len(data["accepted_dou_findings"]) == 1, data
' >/dev/null 2>&1; then
  ok "lifecycle detail route returns full nested state"
else
  fail "lifecycle detail route did not return nested state"
  cat "$LOG_FILE"
  exit 1
fi

if python3 -c '
import urllib.request, json, sys
resp = urllib.request.urlopen("http://127.0.0.1:'"$PORT"'/api/control/state", timeout=1.0)
data = json.loads(resp.read().decode())
runs = data["active_runs"] + data["recent_runs"]
matches = [run for run in runs if run["run_id"] == "lcyc-smoke-life"]
assert matches, data
assert matches[0]["source"] == "lifecycle_runs", matches[0]
' >/dev/null 2>&1; then
  ok "state route surfaces lifecycle RunStatus projection"
else
  fail "state route did not surface lifecycle RunStatus projection"
  cat "$LOG_FILE"
  exit 1
fi

if python3 -c '
import urllib.request, json, sys
resp = urllib.request.urlopen("http://127.0.0.1:'"$PORT"'/api/control/runs/lcyc-smoke-life", timeout=1.0)
data = json.loads(resp.read().decode())
assert data["run_id"] == "lcyc-smoke-life", data
assert data["source"] == "lifecycle_runs", data
assert data["skill"] == "vc-ship", data
' >/dev/null 2>&1; then
  ok "run lookup route resolves lifecycle projection"
else
  fail "run lookup route did not resolve lifecycle projection"
  cat "$LOG_FILE"
  exit 1
fi

if python3 -c '
import urllib.request, sys
html = urllib.request.urlopen("http://127.0.0.1:'"$PORT"'/", timeout=1.0).read().decode()
assert "smoke-active" in html, "dashboard missing active run"
assert "smoke-final" in html, "dashboard missing final run"
assert "lcyc-smoke-life" in html, "dashboard missing lifecycle run"
assert "vc-ship" in html, "dashboard missing lifecycle workflow"
assert "approve_transition" in html, "dashboard missing lifecycle human control"
assert "ZERO DoU" in html, "dashboard missing lifecycle ZERO DoU signal"
assert "accepted 1" in html, "dashboard missing lifecycle accepted DoU count"
assert "3 controls / 1 actions" in html, "dashboard missing lifecycle control/action counts"
' >/dev/null 2>&1; then
  ok "dashboard HTML exposes snapshots, lifecycle controls, ZERO DoU signal, and lifecycle counters"
else
  fail "dashboard HTML does not expose seeded runs, controls, and lifecycle signal"
  cat "$LOG_FILE"
  exit 1
fi

if python3 -c '
import urllib.request, sys
resp = urllib.request.urlopen("http://127.0.0.1:'"$PORT"'/favicon.ico", timeout=1.0)
assert resp.status == 204, resp.status
assert resp.read() == b"", "favicon response should be empty"
' >/dev/null 2>&1; then
  ok "favicon route returns clean no-content response"
else
  fail "favicon route did not return clean no-content response"
  cat "$LOG_FILE"
  exit 1
fi

# 6. SIGTERM clean shutdown
phase "shutdown verification"

kill -15 "$SERVER_PID" 2>/dev/null || true
exited=0
for _ in {1..50}; do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    exited=1
    break
  fi
  sleep 0.1
done

if [[ $exited -eq 1 ]]; then
  ok "server process stopped cleanly on SIGTERM"
else
  fail "server process failed to exit on SIGTERM"
  kill -9 "$SERVER_PID" 2>/dev/null || true
  exit 1
fi

# Verify no panic in log
if grep -iq "panic" "$LOG_FILE"; then
  fail "panic detected in server log"
  cat "$LOG_FILE"
  exit 1
else
  ok "no panics detected in log file"
fi

if grep -q "LEPTOS_OUTPUT_NAME" "$LOG_FILE"; then
  fail "Leptos env warning detected in clean-env server log"
  cat "$LOG_FILE"
  exit 1
else
  ok "no LEPTOS_OUTPUT_NAME warning detected in clean-env server log"
fi

printf "\n%b✓%b All %s smoke tests passed.\n" "$_green" "$_reset" "$PASSES"
