#!/usr/bin/env bash


spawn_in_vc_frame_context() {
  # VC_FRAME=0 / ZELLIJ=0 are valid pane indexes inside vc-frame.
  # vc-frame dual-emits VC_FRAME* and legacy ZELLIJ* pane env during transition.
  [[ -n "${VC_FRAME_PANE_ID:-${ZELLIJ_PANE_ID:-}}" ]] \
    || [[ -n "${VC_FRAME+set}" ]] \
    || [[ -n "${ZELLIJ+set}" ]]
}

spawn_vc_frame_bin() {
  local bin=""
  bin="$(command -v vc-frame 2>/dev/null || true)"
  if [[ -n "$bin" ]]; then
    printf '%s\n' "$bin"
    return 0
  fi
  return 1
}

spawn_current_vc_frame_session_name() {
  printf '%s\n' "${VC_FRAME_SESSION_NAME:-${ZELLIJ_SESSION_NAME:-}}"
}

# A vc-frame session is a usable spawn target only when it is actually live.
# Guards against the dispatcher's per-run tracking id (operator_session_name() =
# "<repo>-<run_id>", see control_plane.py) being treated as a real session.
spawn_session_is_live() {
  local name="$1"
  [[ -n "$name" ]] || return 1
  local bin=""
  bin="$(spawn_vc_frame_bin 2>/dev/null || true)"
  [[ -n "$bin" ]] || return 1
  local sessions=""
  sessions="$("$bin" list-sessions 2>/dev/null || true)"
  [[ -n "$sessions" ]] || sessions="$("$bin" ls 2>/dev/null || true)"
  # Match full session name (G7 may use multi-word hosts like "<repo> workers").
  # Strip trailing status tags: [Created], (current), EXITED markers.
  printf '%s\n' "$sessions" \
    | sed 's/\x1b\[[0-9;]*m//g' \
    | awk -v s="$name" '
        {
          if ($0 ~ /EXITED/) next
          line = $0
          sub(/[[:space:]]+\[.*$/, "", line)
          sub(/[[:space:]]+\([^)]*\)$/, "", line)
          gsub(/[[:space:]]+$/, "", line)
          if (line == s) hit = 1
        }
        END { exit hit ? 0 : 1 }
      '
}

# G7 (2026-07-21): resolve the WORKER host session — never the human operator seat.
# Name kept for call-site compatibility; launch-log field operator_session records
# this host (truthful target), not the dispatcher's interactive session.
#
# Rules (exact order):
#   1. VIBECRAFTED_WORKER_SESSION if set — explicit override wins.
#   2. Else workspace-bound host from the control-plane catalog
#      ("{label}-{workspace_short}-w") via Python twin — two workspaces
#      with the same root basename never share a host (Cut A, 2026-08-10).
#   3. Emergency fallback: "<basename(root)>-w" only if Python catalog
#      resolution is unavailable.
# 2026-08-09: the suffix used to be conditional on host == dispatcher seat.
# That guarded only seat==repo, so dispatch from any other seat landed worker
# tabs in the operator's card. Unconditional invariant → unconditional rule.
# Missing host sessions are resurrected by G3 (attach --create-background).
spawn_effective_operator_session() {
  spawn_normalize_ambient_context

  if [[ -n "${VIBECRAFTED_WORKER_SESSION:-}" ]]; then
    printf '%s\n' "${VIBECRAFTED_WORKER_SESSION}"
    return 0
  fi

  local repo_root="${SPAWN_ROOT:-${VIBECRAFTED_ROOT:-}}"
  if [[ -z "$repo_root" ]]; then
    repo_root="$(pwd)"
  fi

  # Prefer the Python catalog resolver so shell and Python stay identical.
  # Resolve the checkout/installed package explicitly: relying on ambient
  # PYTHONPATH made source checkouts silently fall back to basename hosts.
  local core_path="" py="" python_path="${PYTHONPATH:-}" resolved=""
  core_path="$(spawn_python_core_path 2>/dev/null || true)"
  py="$(spawn_python_bin)"
  if [[ -n "$core_path" ]]; then
    python_path="${core_path}${python_path:+:$python_path}"
  fi
  if command -v "$py" >/dev/null 2>&1; then
    resolved="$(
      PYTHONPATH="$python_path" SPAWN_ROOT="$repo_root" VIBECRAFTED_ROOT="$repo_root" "$py" - <<'PY' 2>/dev/null
import os
from pathlib import Path
root = os.environ.get("SPAWN_ROOT") or os.environ.get("VIBECRAFTED_ROOT") or os.getcwd()
try:
    from vibecrafted_core.workspace_catalog import resolve_worker_host_session
    print(resolve_worker_host_session(root=root, env=os.environ), end="")
except Exception:
    print(f"{Path(root).name or 'vibecrafted'}-w", end="")
PY
    )" || resolved=""
  fi
  if [[ -n "$resolved" ]]; then
    printf '%s\n' "$resolved"
    return 0
  fi

  local host=""
  host="$(basename "$repo_root")"
  [[ -n "$host" ]] || return 1
  printf '%s-w\n' "$host"
}

spawn_in_target_vc_frame_session() {
  local target_session=""
  target_session="$(spawn_effective_operator_session 2>/dev/null || true)"
  spawn_in_vc_frame_context || return 1
  [[ -n "$target_session" ]] || return 0
  [[ "$(spawn_current_vc_frame_session_name)" == "$target_session" ]]
}

spawn_vc_frame_launch_lock_key() {
  local session_name="${1:-local}"
  printf '%s' "$session_name" | tr ' ' '-' | tr -cs '[:alnum:]._-' '-'
}

spawn_acquire_vc_frame_launch_slot() {
  local session_name="${1:-local}"
  local stagger_seconds="${VIBECRAFTED_SPAWN_STAGGER_SECONDS:-1}"
  local max_wait_seconds="${VIBECRAFTED_SPAWN_STAGGER_MAX_WAIT_SECONDS:-30}"
  local lock_root="${TMPDIR:-/tmp}/vibecrafted-vc_frame-launch-locks"
  local lock_key lock_dir
  local waited_ms=0
  local poll_interval_ms=100

  [[ "${VIBECRAFTED_SPAWN_STAGGER:-1}" == "1" ]] || return 0
  [[ "$stagger_seconds" != "0" ]] || return 0

  lock_key="$(spawn_vc_frame_launch_lock_key "$session_name")"
  lock_dir="$lock_root/$lock_key.lock"
  mkdir -p "$lock_root"
  while ! mkdir "$lock_dir" 2>/dev/null; do
    # Stale-lock recovery: if the recorded owner PID is no longer alive,
    # reclaim the lock. Worker crashes between acquire and release would
    # otherwise wedge every subsequent dispatcher forever.
    local owner_pid=""
    if [[ -f "$lock_dir/pid" ]]; then
      owner_pid="$(cat "$lock_dir/pid" 2>/dev/null || true)"
      if [[ -n "$owner_pid" ]] && ! kill -0 "$owner_pid" 2>/dev/null; then
        rm -rf "$lock_dir" 2>/dev/null || true
        continue
      fi
    fi
    sleep 0.1
    waited_ms=$((waited_ms + poll_interval_ms))
    if (( waited_ms >= max_wait_seconds * 1000 )); then
      # Bounded fallback: a wedged holder must not deadlock the runtime.
      # Force-reclaim and proceed; worst case is a missed stagger window.
      rm -rf "$lock_dir" 2>/dev/null || true
      mkdir "$lock_dir" 2>/dev/null || return 0
      break
    fi
  done
  printf '%s' "$$" > "$lock_dir/pid" 2>/dev/null || true
  sleep "$stagger_seconds"
  printf '%s\n' "$lock_dir"
}

spawn_release_vc_frame_launch_slot() {
  local lock_dir="${1:-}"
  [[ -n "$lock_dir" ]] || return 0
  rm -rf "$lock_dir" 2>/dev/null || true
}

spawn_current_tab_id() {
  local session_name="${1:-}"
  local raw=""
  local vc_frame_bin=""
  vc_frame_bin="$(spawn_vc_frame_bin)" || return 1
  local -a vc_frame_cmd=("$vc_frame_bin")
  if [[ -n "$session_name" ]]; then
    vc_frame_cmd+=(--session "$session_name")
  fi
  raw="$("${vc_frame_cmd[@]}" action current-tab-info --json 2>/dev/null || true)"
  "$(spawn_python_bin)" - "$raw" <<'PY'
import json
import sys

raw = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
if not raw:
    raise SystemExit(1)
try:
    payload = json.loads(raw)
except Exception:
    raise SystemExit(1)

if isinstance(payload, list):
    payload = payload[0] if payload else {}
if not isinstance(payload, dict):
    raise SystemExit(1)

for key in ("tab_id", "id", "position", "index"):
    value = payload.get(key)
    if value not in (None, ""):
        print(value)
        raise SystemExit(0)
raise SystemExit(1)
PY
}

spawn_tab_id_by_name() {
  local tab_name="${1:-}"
  local session_name="${2:-}"
  local raw=""
  local vc_frame_bin=""
  [[ -n "$tab_name" ]] || return 1
  vc_frame_bin="$(spawn_vc_frame_bin)" || return 1
  local -a vc_frame_cmd=("$vc_frame_bin")
  if [[ -n "$session_name" ]]; then
    vc_frame_cmd+=(--session "$session_name")
  fi
  raw="$("${vc_frame_cmd[@]}" action list-tabs --json 2>/dev/null || true)"
  "$(spawn_python_bin)" - "$tab_name" "$raw" <<'PY'
import json
import sys

target_name = sys.argv[1]
raw = (sys.argv[2] if len(sys.argv) > 2 else "").strip()
if not raw:
    raise SystemExit(1)
try:
    payload = json.loads(raw)
except Exception:
    raise SystemExit(1)

def extract_tab_id(node):
    for key in ("tab_id", "id", "position", "index"):
        value = node.get(key)
        if value not in (None, ""):
            return value
    return None

def visit(node):
    if isinstance(node, dict):
        name = node.get("name")
        if name in (None, ""):
            name = node.get("tab_name")
        if str(name or "") == target_name:
            tab_id = extract_tab_id(node)
            if tab_id is not None:
                print(tab_id)
                return True
        for value in node.values():
            if visit(value):
                return True
    elif isinstance(node, list):
        for value in node:
            if visit(value):
                return True
    return False

if not visit(payload):
    raise SystemExit(1)
PY
}

spawn_current_focused_pane_id() {
  local raw=""
  local vc_frame_bin=""
  vc_frame_bin="$(spawn_vc_frame_bin)" || return 1
  raw="$("$vc_frame_bin" action list-panes --json --state 2>/dev/null || true)"
  "$(spawn_python_bin)" - "$raw" <<'PY'
import json
import sys

raw = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
if not raw:
    raise SystemExit(1)
try:
    payload = json.loads(raw)
except Exception:
    raise SystemExit(1)

def is_truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False

def pane_id(node):
    for key in ("pane_id", "id"):
        value = node.get(key)
        if value not in (None, ""):
            return value
    return None

def visit(node):
    if isinstance(node, dict):
        focused = node.get("is_focused")
        if focused is None:
            focused = node.get("focused")
        if is_truthy(focused):
            pid = pane_id(node)
            if pid is not None:
                print(pid)
                return True
        for value in node.values():
            if visit(value):
                return True
    elif isinstance(node, list):
        for value in node:
            if visit(value):
                return True
    return False

if not visit(payload):
    raise SystemExit(1)
PY
}

spawn_pane_direction() {
  # Grid policy: 4 per row, 8 per tab, 9th opens new tab.
  # Uses SPAWN_LOOP_NR (marbles) or VIBECRAFTED_PANE_SEQ (manual).
  # Fresh top-level spawns default to a new tab so they never land in a stale
  # operator tab by accident.
  local seq=""
  local max_per_row=4
  local max_per_tab=8

  if [[ -n "${SPAWN_LOOP_NR:-}" && "${SPAWN_LOOP_NR:-0}" -gt 0 ]]; then
    seq="${SPAWN_LOOP_NR}"
  elif [[ -n "${VIBECRAFTED_PANE_SEQ:-}" && "${VIBECRAFTED_PANE_SEQ:-0}" -gt 0 ]]; then
    seq="${VIBECRAFTED_PANE_SEQ}"
  else
    printf 'new-tab\n'
    return 0
  fi

  if (( seq >= max_per_tab )); then
    printf 'new-tab\n'
  elif (( seq > 0 && seq % max_per_row == 0 )); then
    printf 'down\n'
  else
    printf 'right\n'
  fi
}

spawn_current_tab_name() {
  # Return the name of the currently focused vc-frame tab via env.
  printf '%s\n' "${VC_FRAME_TAB_NAME:-}"
}

spawn_write_visible_launch_script() {
  local script_path="$1"
  local launcher="$2"
  local transcript_path="${SPAWN_TRANSCRIPT:-}"

  mkdir -p "$(dirname "$script_path")"
  cat > "$script_path" <<EOF_VISIBLE
#!/usr/bin/env bash
set -euo pipefail
launcher=$(spawn_shell_quote "$launcher")
transcript=$(spawn_shell_quote "$transcript_path")
"\$launcher" &
pid="\$!"
if [[ -n "\$transcript" ]]; then
  mkdir -p "\$(dirname "\$transcript")"
  touch "\$transcript" 2>/dev/null || true
  tail -n +1 -f "\$transcript" &
  tail_pid="\$!"
  set +e
  wait "\$pid"
  rc="\$?"
  set -e
  kill "\$tail_pid" >/dev/null 2>&1 || true
  wait "\$tail_pid" >/dev/null 2>&1 || true
  exit "\$rc"
fi
wait "\$pid"
EOF_VISIBLE
  chmod +x "$script_path"
}

spawn_in_marbles_tab() {
  vc_raise_launcher_limits
  # Route a pane into the dedicated marbles tab without stealing operator focus.
  # Called only when SPAWN_LOOP_NR > 0 AND VIBECRAFTED_MARBLES_TAB_NAME is set.
  local launcher="$1"
  local pane_name="$2"
  local direction="$3"
  local marbles_tab="${VIBECRAFTED_MARBLES_TAB_NAME:-}"
  local operator_tab_id=""
  local marbles_tab_id=""
  local cmd_script=""
  local launch_cmd="bash '$launcher'"
  local pane_direction="$direction"
  local pane_lifecycle_args=(--stacked)
  local vc_frame_bin=""

  [[ -n "$marbles_tab" ]] || return 1
  vc_frame_bin="$(spawn_vc_frame_bin)" || return 1

  if [[ "$pane_direction" == "new-tab" ]]; then
    pane_direction="right"
  fi
  # Hard invariant: workers stack inside the marbles-${RUN_ID} tab. Operator's
  # tab stays ZEN. Stacking keeps long runs readable instead of 5px columns.
  # Backward compat: CLOSE_AGENT_PANES=0 now maps to KEEP for one release.
  if [[ "${VIBECRAFTED_VC_FRAME_CLOSE_AGENT_PANES:-1}" == "0" ]]; then
    : "${VIBECRAFTED_VC_FRAME_KEEP_AGENT_PANES:=1}"
  fi
  if [[ "${VIBECRAFTED_VC_FRAME_KEEP_AGENT_PANES:-0}" != "1" ]]; then
    pane_lifecycle_args+=(--close-on-exit)
  fi

  cmd_script="$(spawn_tmp_script_path "vc-spawn-cmd" "${SPAWN_ROOT:-$(pwd)}")"
  spawn_write_visible_launch_script "$cmd_script" "$launcher"

  marbles_tab_id="$(spawn_tab_id_by_name "$marbles_tab" 2>/dev/null || true)"
  if [[ -z "$marbles_tab_id" ]]; then
    operator_tab_id="$(spawn_current_tab_id 2>/dev/null || true)"
    "$vc_frame_bin" action go-to-tab-name "$marbles_tab" --create >/dev/null 2>&1 || true
    marbles_tab_id="$(spawn_tab_id_by_name "$marbles_tab" 2>/dev/null || true)"
    if [[ -n "$operator_tab_id" ]]; then
      "$vc_frame_bin" action go-to-tab-by-id "$operator_tab_id" >/dev/null 2>&1 || true
    fi
  fi

  # Create the pane inside the marbles tab. If tab-id lookup fails, keep a
  # conservative fallback path that restores the active tab by stable ID.
  # vc-frame 0.44+ rejects --direction together with --stacked, and the marbles
  # invariant IS stacked-inside-marbles-tab (see comment above), so we drop
  # --direction here and let vc-frame choose stack position.
  if [[ -n "$marbles_tab_id" ]]; then
    "$vc_frame_bin" action new-pane --tab-id "$marbles_tab_id" \
      --name "$pane_name" \
      "${pane_lifecycle_args[@]}" \
      --cwd "${SPAWN_ROOT:-$(pwd)}" \
      -- "$cmd_script" >/dev/null
  else
    if [[ -z "$operator_tab_id" ]]; then
      operator_tab_id="$(spawn_current_tab_id 2>/dev/null || true)"
    fi
    "$vc_frame_bin" action go-to-tab-name "$marbles_tab" --create >/dev/null 2>&1 || true
    "$vc_frame_bin" action new-pane --name "$pane_name" "${pane_lifecycle_args[@]}" --cwd "${SPAWN_ROOT:-$(pwd)}" -- "$cmd_script" >/dev/null # OPERATOR_TAB_OK: fallback after explicit go-to marbles tab.
    if [[ -n "$operator_tab_id" ]]; then
      "$vc_frame_bin" action go-to-tab-by-id "$operator_tab_id" >/dev/null 2>&1 || true
    fi
  fi

  return 0
}

# ---------------------------------------------------------------------------
# Host-session action wrapper (G3 + G3b): never swallow host-launch failures.
#
# G3 — dead hosting session:
# Observed: `vc-frame --session X action ...` prints "Session 'X' not found"
# while some builds still exit 0. Launcher then records process_spawned and
# later stalls as pid_gone with no receipt error.
#
# G3b — ambiguous NewTab ACK (2026-08):
# Observed: `action 'NewTab' did not acknowledge completion within 25s` under
# load (plugin cold-start on layout activation). The tab often *did* land —
# server ACK just lagged past CRITICAL_ACTION_COMPLETION_TIMEOUT. Blind retry
# would open a duplicate worker tab; presence probe first is mandatory.
#
# Contract:
#   1. Run the action, capture stderr (still re-emit it).
#   2. On session-not-found: ONE `attach --create-background NAME`, retry once.
#   3. On ambiguous ACK (did-not-acknowledge / channel-closed / timed-out):
#        a. if action carries --name NAME: list-tabs probe; present → success
#        b. else (or absent): brief backoff, ONE retry
#        c. after retry ACK fail: probe --name again before failing loud
#   4. On unrecoverable second failure: return 2 + SPAWN_VC_FRAME_LAST_ERROR
#      so the caller fails the control-plane receipt immediately.
# Happy path (session live, action ok): no create-background, no extra list.
# ---------------------------------------------------------------------------

SPAWN_VC_FRAME_LAST_ERROR=""

spawn_vc_frame_stderr_is_session_not_found() {
  local text="${1:-}"
  [[ -n "$text" ]] || return 1
  printf '%s' "$text" | command grep -qiE \
    "Session ['\"][^'\"]+['\"] not found|There is no active session!"
}

# Ambiguous host-action ACK: server may have applied the mutation (new-tab)
# even though the oneshot completion channel timed out. Parity with
# vc-frame triage `is_ambiguous_new_tab_failure`.
spawn_vc_frame_stderr_is_ambiguous_action_ack() {
  local text="${1:-}"
  [[ -n "$text" ]] || return 1
  printf '%s' "$text" | command grep -qiE \
    "did not acknowledge completion|completion channel closed before acknowledgement|timed out after"
}

# Extract `--name VALUE` from a vc-frame action argv (skip the action verb itself).
spawn_vc_frame_action_name_arg() {
  local prev=""
  local arg=""
  for arg in "$@"; do
    if [[ "$prev" == "--name" ]]; then
      printf '%s\n' "$arg"
      return 0
    fi
    prev="$arg"
  done
  return 1
}

# True when a named tab is already enumerable in the host session (G3b presence).
spawn_vc_frame_tab_present() {
  local session_name="${1:-}"
  local tab_name="${2:-}"
  local tab_id=""
  [[ -n "$tab_name" ]] || return 1
  tab_id="$(spawn_tab_id_by_name "$tab_name" "$session_name" 2>/dev/null || true)"
  [[ -n "$tab_id" ]]
}

spawn_vc_frame_create_host_session() {
  local vc_frame_bin="${1:-}"
  local session_name="${2:-}"
  [[ -n "$vc_frame_bin" && -n "$session_name" ]] || return 1
  local out="" status=0
  # Bounded create with ONLY the current client's attachment context cleared
  # (2026-09-09). A dispatcher's targeting export or a stale pane marker can
  # name exactly the host being resurrected; Frame mirrors VC_FRAME_SESSION_NAME
  # into ZELLIJ_SESSION_NAME at startup and src/commands.rs:844 then panics
  # ("trying to attach to the current session") instead of creating it. Twin
  # of _vetcoders_vc_frame_create_host_session (shell/lib/vc_frame.sh).
  out="$(env -u VC_FRAME -u VC_FRAME_PANE_ID -u VC_FRAME_SESSION_NAME \
    -u ZELLIJ -u ZELLIJ_PANE_ID -u ZELLIJ_SESSION_NAME \
    "$vc_frame_bin" attach --create-background "$session_name" 2>&1)" || status=$?
  if [[ -n "$out" ]]; then
    printf '%s\n' "$out" >&2
  fi
  # Prefer liveness over bare exit: create-background may report odd codes.
  if spawn_session_is_live "$session_name"; then
    return 0
  fi
  [[ "$status" -eq 0 ]] || return "$status"
  return 1
}

spawn_record_host_session_failure() {
  local err="${SPAWN_VC_FRAME_LAST_ERROR:-hosting session launch failed}"
  SPAWN_VC_FRAME_LAST_ERROR="$err"
  printf 'host session launch failed: %s\n' "$err" >&2
  local meta_path="${SPAWN_META:-}"
  [[ -n "$meta_path" && -f "$meta_path" ]] || return 0
  "$(spawn_python_bin)" - "$meta_path" "$err" <<'PY' 2>/dev/null || true
import datetime as dt
import json
import os
import sys

meta_path, err = sys.argv[1], sys.argv[2]
try:
    with open(meta_path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
except (OSError, json.JSONDecodeError):
    sys.exit(0)

now = dt.datetime.now(dt.timezone.utc).isoformat()
payload["status"] = "failed"
payload["last_error"] = err
payload["updated_at"] = now
payload["completed_at"] = now
payload["exit_code"] = 1
payload["liveness"] = "terminal"
tmp = f"{meta_path}.tmp.{os.getpid()}"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
os.replace(tmp, meta_path)
PY
  if command -v spawn_sync_control_plane >/dev/null 2>&1; then
    spawn_sync_control_plane 2>/dev/null || true
  fi
}

# Run `vc-frame [--session NAME] <action...>` with host-resurrect + ACK recovery.
# Args: <vc_frame_bin> <session_name_or_empty> <action args...>
# Returns 0 ok, 2 unrecoverable host-session failure, else action exit status.
spawn_vc_frame_session_action() {
  local vc_frame_bin="${1:-}"
  local session_name="${2:-}"
  shift 2 || true
  SPAWN_VC_FRAME_LAST_ERROR=""
  [[ -n "$vc_frame_bin" ]] || return 1
  [[ "$#" -ge 1 ]] || return 1

  local err_file out_file status=0 err=""
  local tab_name=""
  tab_name="$(spawn_vc_frame_action_name_arg "$@" 2>/dev/null || true)"
  err_file="$(mktemp "${TMPDIR:-/tmp}/vc-frame-action.XXXXXX.err")"
  out_file="$(mktemp "${TMPDIR:-/tmp}/vc-frame-action.XXXXXX.out")"

  _spawn_vc_frame_action_invoke() {
    if [[ -n "$session_name" ]]; then
      "$vc_frame_bin" --session "$session_name" "$@" >"$out_file" 2>"$err_file"
    else
      "$vc_frame_bin" "$@" >"$out_file" 2>"$err_file"
    fi
  }

  # G3b: if ACK timed out but the named tab is already live, accept success.
  _spawn_vc_frame_ack_presence_ok() {
    local label="${1:-presence}"
    [[ -n "$tab_name" ]] || return 1
    # Tiny grace: list-tabs can lag the just-created tab by a beat.
    sleep 1
    if spawn_vc_frame_tab_present "$session_name" "$tab_name"; then
      printf 'vc-frame action ACK ambiguous (%s) but tab %s is present; treating as success\n' \
        "$label" "$tab_name" >&2
      return 0
    fi
    return 1
  }

  status=0
  _spawn_vc_frame_action_invoke "$@" || status=$?
  err="$(cat "$err_file" 2>/dev/null || true)"
  if [[ -n "$err" ]]; then
    printf '%s\n' "$err" >&2
  fi

  if spawn_vc_frame_stderr_is_session_not_found "$err"; then
    SPAWN_VC_FRAME_LAST_ERROR="$err"
    if [[ -z "$session_name" ]]; then
      rm -f "$err_file" "$out_file"
      return 2
    fi
    printf 'hosting session missing; one-shot attach --create-background %s\n' \
      "$session_name" >&2
    if ! spawn_vc_frame_create_host_session "$vc_frame_bin" "$session_name"; then
      SPAWN_VC_FRAME_LAST_ERROR="${SPAWN_VC_FRAME_LAST_ERROR}"$'\n'"attach --create-background '${session_name}' failed"
      rm -f "$err_file" "$out_file"
      return 2
    fi
    status=0
    _spawn_vc_frame_action_invoke "$@" || status=$?
    err="$(cat "$err_file" 2>/dev/null || true)"
    if [[ -n "$err" ]]; then
      printf '%s\n' "$err" >&2
    fi
    if spawn_vc_frame_stderr_is_session_not_found "$err" || [[ "$status" -ne 0 ]]; then
      SPAWN_VC_FRAME_LAST_ERROR="${err:-vc-frame action failed after host resurrect (exit ${status})}"
      rm -f "$err_file" "$out_file"
      return 2
    fi
  elif [[ "$status" -ne 0 ]]; then
    # G3b: ambiguous NewTab/critical-action ACK — presence first, then one retry.
    if spawn_vc_frame_stderr_is_ambiguous_action_ack "$err"; then
      if _spawn_vc_frame_ack_presence_ok "first-ack"; then
        rm -f "$err_file" "$out_file"
        return 0
      fi
      printf 'vc-frame action ACK timeout; one retry after brief backoff\n' >&2
      sleep 2
      status=0
      _spawn_vc_frame_action_invoke "$@" || status=$?
      err="$(cat "$err_file" 2>/dev/null || true)"
      if [[ -n "$err" ]]; then
        printf '%s\n' "$err" >&2
      fi
      if [[ "$status" -eq 0 ]]; then
        rm -f "$err_file" "$out_file"
        return 0
      fi
      if spawn_vc_frame_stderr_is_ambiguous_action_ack "$err" \
        && _spawn_vc_frame_ack_presence_ok "retry-ack"; then
        rm -f "$err_file" "$out_file"
        return 0
      fi
    fi
    SPAWN_VC_FRAME_LAST_ERROR="${err:-vc-frame action exit ${status}}"
    rm -f "$err_file" "$out_file"
    return "$status"
  fi

  rm -f "$err_file" "$out_file"
  return 0
}

spawn_in_vc_frame_pane() {
  vc_raise_launcher_limits
  local launcher="$1"
  local pane_name="${2:-agent}"
  local direction="${VIBECRAFTED_VC_FRAME_SPAWN_DIRECTION:-$(spawn_pane_direction)}"
  local launch_cmd="bash '$launcher'"
  local cmd_script
  local launch_lock=""
  local vc_frame_bin=""
  local host_session=""

  if spawn_in_vc_frame_context && vc_frame_bin="$(spawn_vc_frame_bin)"; then
    # If the operator explicitly targets another vc-frame session, do not open a
    # pane in the current live session. Fall through to spawn_in_operator_session().
    if ! spawn_in_target_vc_frame_session; then
      return 1
    fi

    # Marbles loop panes (L2, L3...) route to dedicated marbles tab to avoid
    # stealing operator focus.
    if [[ "${SPAWN_LOOP_NR:-0}" -gt 0 && -n "${VIBECRAFTED_MARBLES_TAB_NAME:-}" ]]; then
      if spawn_in_marbles_tab "$launcher" "$pane_name" "$direction"; then
        return 0
      fi
    fi

    host_session="${VC_FRAME_SESSION_NAME:-${ZELLIJ_SESSION_NAME:-}}"
    launch_lock="$(spawn_acquire_vc_frame_launch_slot "${host_session:-local}" 2>/dev/null || true)"

    cmd_script="$(spawn_tmp_script_path "vc-spawn-cmd" "${SPAWN_ROOT:-$(pwd)}")"
    spawn_write_visible_launch_script "$cmd_script" "$launcher"

    local launch_status=0
    if [[ "$direction" == "new-tab" ]]; then
      local run_tab_name="${SPAWN_RUN_ID:-${VIBECRAFTED_RUN_ID:-$pane_name}}"
      local run_tab_id=""
      run_tab_id="$(spawn_tab_id_by_name "$run_tab_name" 2>/dev/null || true)"
      if [[ -z "$run_tab_id" ]]; then
        # --after-base (W2-B-4c): run tabs grow from the base card. Probe the
        # binary — a stale install without the flag keeps append placement.
        local placement_flag=""
        local focus_flag=""
        local new_tab_help=""
        new_tab_help="$("$vc_frame_bin" action new-tab --help 2>&1 || true)"
        if [[ "$new_tab_help" == *"--after-base"* ]]; then
          placement_flag="--after-base"
        fi
        if [[ "$new_tab_help" == *"--no-focus"* ]]; then
          focus_flag="--no-focus"
        fi
        spawn_vc_frame_session_action "$vc_frame_bin" "$host_session" \
          action new-tab \
          ${placement_flag:+"$placement_flag"} \
          ${focus_flag:+"$focus_flag"} \
          --name "$run_tab_name" \
          --cwd "${SPAWN_ROOT:-$(pwd)}" \
          -- "$cmd_script" || launch_status=$?
      else
        local operator_tab_id=""
        operator_tab_id="$(spawn_current_tab_id 2>/dev/null || true)"
        spawn_vc_frame_session_action "$vc_frame_bin" "$host_session" \
          action new-pane --tab-id "$run_tab_id" \
          --stacked \
          --close-on-exit \
          --name "$pane_name" \
          --cwd "${SPAWN_ROOT:-$(pwd)}" \
          -- "$cmd_script" || launch_status=$?
        if [[ -n "$operator_tab_id" ]]; then
          "$vc_frame_bin" action go-to-tab-by-id "$operator_tab_id" >/dev/null 2>&1 || true
        fi
      fi
    else
      spawn_vc_frame_session_action "$vc_frame_bin" "$host_session" \
        action new-pane --direction "$direction" --name "$pane_name" \
        --cwd "${SPAWN_ROOT:-$(pwd)}" -- "$cmd_script" || launch_status=$? # OPERATOR_TAB_OK: explicit same-tab grid spawn.
    fi
    spawn_release_vc_frame_launch_slot "$launch_lock"
    if [[ "$launch_status" != "0" ]]; then
      if [[ "$launch_status" -eq 2 ]]; then
        spawn_record_host_session_failure
      fi
      return "$launch_status"
    fi

    # Auto-tail-await side pane in the same run tab. Silent no-op if the
    # helper is missing, jq is unavailable, or we are not in new-tab mode
    # (same-tab grid spawn is operator-explicit and should not be polluted).
    if [[ "$direction" == "new-tab" ]]; then
      spawn_await_watch_pane "${run_tab_id:-}" "${run_tab_name:-}" "$pane_name"
    fi
    return 0
  fi
  return 1
}

# Spawn the vibecrafted-await-watch helper as a floating mini probe in the
# worker's run tab so the operator gets live transcript tail + automatic
# self-exit without stealing layout space. Hard requirement: active SPAWN_META,
# jq available, the helper script executable, and the run tab actually exists
# (we re-query if the post-tab-creation race left run_tab_id empty).
spawn_await_status_is_active() {
  case "${1:-}" in
    launching|running|in-progress|pending|created|brief_rendered|process_spawned|prompt_delivered|first_output_seen|active|artifact_seen|report_started|posthook_running)
      return 0
      ;;
  esac
  return 1
}

spawn_await_watch_pane() {
  vc_raise_launcher_limits
  local run_tab_id="$1" run_tab_name="$2" worker_pane_name="$3"
  command -v jq >/dev/null 2>&1 || return 0
  [[ -n "${SPAWN_RUN_ID:-}" ]] || return 0
  [[ -n "${SPAWN_META:-}" && -f "${SPAWN_META:-}" ]] || return 0
  local meta_status=""
  meta_status="$(jq -r '.status // ""' "$SPAWN_META" 2>/dev/null || true)"
  spawn_await_status_is_active "$meta_status" || return 0
  local vc_frame_bin=""
  vc_frame_bin="$(spawn_vc_frame_bin)" || return 0

  local helper="${_SPAWN_LIB_DIR}/../vibecrafted-await-watch.sh"
  [[ -x "$helper" ]] || return 0

  # Re-query tab id when missing: the parent path may have created a fresh
  # tab via `vc-frame action new-tab` which does not return the id.
  if [[ -z "$run_tab_id" && -n "$run_tab_name" ]]; then
    # Tiny grace for the just-created tab to be enumerable.
    sleep 1
    run_tab_id="$(spawn_tab_id_by_name "$run_tab_name" 2>/dev/null || true)"
  fi
  [[ -n "$run_tab_id" ]] || return 0

  local focused_pane_id=""
  focused_pane_id="$(spawn_current_focused_pane_id 2>/dev/null || true)"
  local pane_name="await:${SPAWN_AGENT:-?}:${SPAWN_RUN_ID##*-}"
  "$vc_frame_bin" action new-pane --tab-id "$run_tab_id" \
    --floating \
    --width 24% \
    --height 35% \
    --x 76% \
    --y 8% \
    --close-on-exit \
    --name "$pane_name" \
    --cwd "${SPAWN_ROOT:-$(pwd)}" \
    -- "$helper" --meta "$SPAWN_META" >/dev/null 2>&1 || true
  if [[ -n "$focused_pane_id" ]]; then
    "$vc_frame_bin" action focus-pane-id "$focused_pane_id" >/dev/null 2>&1 || true
  fi
}

spawn_in_operator_session() {
  vc_raise_launcher_limits
  local launcher="$1"
  local pane_name="${2:-agent}"
  local session_name=""
  local direction="${VIBECRAFTED_VC_FRAME_SPAWN_DIRECTION:-$(spawn_pane_direction)}"
  local effective_direction="$direction"
  local launch_cmd="bash '$launcher'"
  local cmd_script
  local launch_lock=""
  local vc_frame_bin=""

  spawn_normalize_ambient_context

  session_name="$(spawn_effective_operator_session 2>/dev/null || true)"
  [[ -n "$session_name" ]] || return 1
  vc_frame_bin="$(spawn_vc_frame_bin)" || return 1
  export VIBECRAFTED_OPERATOR_SESSION="$session_name"
  export VC_FRAME_SESSION_NAME="$session_name"
  export ZELLIJ_SESSION_NAME="$session_name"

  # When routing into a session from outside its active pane context, always
    # open a fresh tab. Otherwise vc-frame targets whichever operator tab is
  # currently focused, which can be a stale marbles tab.
  if ! spawn_in_target_vc_frame_session; then
    effective_direction="new-tab"
  fi

  launch_lock="$(spawn_acquire_vc_frame_launch_slot "$session_name" 2>/dev/null || true)"

  cmd_script="$(spawn_tmp_script_path "vc-spawn-cmd" "${SPAWN_ROOT:-$(pwd)}")"
  spawn_write_visible_launch_script "$cmd_script" "$launcher"

  # External spawn into existing operator session — route as pane or new tab per grid policy.
  # G3: action exit + "Session not found" → one create-background + retry; never silent.
  local launch_status=0
  if [[ "$effective_direction" == "new-tab" ]]; then
    local run_tab_name="${SPAWN_RUN_ID:-${VIBECRAFTED_RUN_ID:-$pane_name}}"
    local run_tab_id=""
    run_tab_id="$(spawn_tab_id_by_name "$run_tab_name" "$session_name" 2>/dev/null || true)"
    if [[ -z "$run_tab_id" ]]; then
      # --after-base probe (parity with spawn_in_vc_frame_pane / shell twin).
      local placement_flag=""
      local focus_flag=""
      local new_tab_help=""
      new_tab_help="$("$vc_frame_bin" action new-tab --help 2>&1 || true)"
      if [[ "$new_tab_help" == *"--after-base"* ]]; then
        placement_flag="--after-base"
      fi
      if [[ "$new_tab_help" == *"--no-focus"* ]]; then
        focus_flag="--no-focus"
      fi
      spawn_vc_frame_session_action "$vc_frame_bin" "$session_name" \
        action new-tab \
        ${placement_flag:+"$placement_flag"} \
        ${focus_flag:+"$focus_flag"} \
        --name "$run_tab_name" \
        --cwd "${SPAWN_ROOT:-$(pwd)}" \
        -- "$cmd_script" || launch_status=$?
    else
      local operator_tab_id=""
      operator_tab_id="$(spawn_current_tab_id "$session_name" 2>/dev/null || true)"
      spawn_vc_frame_session_action "$vc_frame_bin" "$session_name" \
        action new-pane --tab-id "$run_tab_id" \
        --stacked \
        --close-on-exit \
        --name "$pane_name" \
        --cwd "${SPAWN_ROOT:-$(pwd)}" \
        -- "$cmd_script" || launch_status=$?
      if [[ -n "$operator_tab_id" ]]; then
        "$vc_frame_bin" --session "$session_name" action go-to-tab-by-id "$operator_tab_id" >/dev/null 2>&1 || true
      fi
    fi
  else
    spawn_vc_frame_session_action "$vc_frame_bin" "$session_name" \
      action new-pane --direction "$effective_direction" --name "$pane_name" \
      --cwd "${SPAWN_ROOT:-$(pwd)}" -- "$cmd_script" || launch_status=$? # OPERATOR_TAB_OK: explicit same-tab grid spawn.
  fi
  spawn_release_vc_frame_launch_slot "$launch_lock"
  if [[ "$launch_status" != "0" ]]; then
    if [[ "$launch_status" -eq 2 ]]; then
      spawn_record_host_session_failure
    fi
    return "$launch_status"
  fi
  if [[ "$effective_direction" == "new-tab" ]]; then
    spawn_await_watch_pane "${run_tab_id:-}" "${run_tab_name:-}" "$pane_name"
  fi
}

spawn_probe() {
  local transcript_path="$1"
  local probe_seconds="${VIBECRAFTED_SPAWN_PROBE_SECONDS:-10}"
  local probe_delay="${VIBECRAFTED_SPAWN_PROBE_DELAY_SECONDS:-2}"
  local agent_name="${SPAWN_AGENT:-agent}"
  local run_id="${SPAWN_RUN_ID:-${VIBECRAFTED_RUN_ID:-?}}"
  local notify_enabled="${VIBECRAFTED_SPAWN_PROBE_NOTIFY:-1}"
  local vc_frame_bin=""

  # Skip if disabled or not in vc_frame
  [[ "${VIBECRAFTED_SPAWN_PROBE:-1}" == "1" ]] || return 0
  spawn_in_vc_frame_context || return 0
  vc_frame_bin="$(spawn_vc_frame_bin)" || return 0
  [[ -n "$transcript_path" ]] || return 0

  # Floating probe pane (10s ephemeral) + content heuristic + system notification.
  # Exit triggers (whichever fires first within probe_seconds):
  #   GOOD: transcript has [HH:MM:SS] timestamp OR `session: <uuid>` line
  #     → silent close, no notification
  #   WARN: ERROR in transcript before a good signal
  #   BAD:  FATAL|panic:|Traceback|BrokenPipe in transcript
  #         OR exit_code != 0 in meta
  #         OR worker pid dies within probe window
  #     → close + system notification with reason
  #   SILENT: nothing in probe_seconds
  #     → close + notification "silent on startup"
  (
    local focused_pane_id=""
    local focused_tab_id=""
    local -a probe_cmd=()
    sleep "$probe_delay"
    [[ -f "$transcript_path" ]] || exit 0
    focused_pane_id="$(spawn_current_focused_pane_id 2>/dev/null || true)"
    focused_tab_id="$(spawn_current_tab_id 2>/dev/null || true)"
    probe_cmd=(
      "$vc_frame_bin" action new-pane --floating
      --close-on-exit
      --width 20%
      --x 80%
      --y 10%
      --height 40%
      --name "probe-${agent_name}:${run_id##*-}"
    )
    if [[ -n "$focused_tab_id" ]]; then
      probe_cmd+=(--tab-id "$focused_tab_id")
    fi
    probe_cmd+=(-- timeout "$probe_seconds" tail -f "$transcript_path")
    "${probe_cmd[@]}" >/dev/null 2>&1 || true
    if [[ -n "$focused_pane_id" ]]; then
      "$vc_frame_bin" action focus-pane-id "$focused_pane_id" >/dev/null 2>&1 || true
    fi
  ) &

  # Parallel content watcher — same probe window, emits notification on
  # bad/silent signals. Good signals close silently.
  if [[ "$notify_enabled" == "1" ]]; then
    spawn_probe_watch "$transcript_path" "$probe_seconds" "$agent_name" "$run_id" &
  fi
}

# Watch transcript content during probe window. Emits one system notification
# describing the startup verdict (good = silent close, bad/silent = notify).
spawn_probe_watch() {
  local transcript="$1"
  local window="$2"
  local agent="$3"
  local rid="$4"
  local meta="${VIBECRAFTED_SPAWN_META:-}"

  # Derive meta path from transcript if not explicitly set.
  # Convention: <stem>.transcript.log <-> <stem>.meta.json
  if [[ -z "$meta" && -n "$transcript" ]]; then
    case "$transcript" in
      *.transcript.log) meta="${transcript%.transcript.log}.meta.json" ;;
    esac
  fi

  local deadline=$(( $(date +%s) + window ))
  local good=0 bad="" warning="" reason=""

  while true; do
    if [[ -f "$transcript" ]]; then
      # GOOD: timestamp line or session UUID present
      if [[ "$good" == "0" ]] && grep -qE '\[[0-9]{2}:[0-9]{2}:[0-9]{2}\]|session: [a-f0-9-]{8,}' "$transcript" 2>/dev/null; then
        good=1
      fi
      # WARN: transient transport/runtime noise. Do not call the worker failed
      # unless the authoritative meta/launcher state or a fatal pattern agrees.
      if [[ -z "$warning" ]]; then
        if reason=$(grep -oE 'ERROR' "$transcript" 2>/dev/null | head -1); then
          if [[ -n "$reason" ]]; then
            warning="$reason"
          fi
        fi
      fi
      # BAD: fatal patterns
      if [[ -z "$bad" ]]; then
        if reason=$(grep -oE 'FATAL|panic:|Traceback|BrokenPipeError' "$transcript" 2>/dev/null | head -1); then
          if [[ -n "$reason" ]]; then
            bad="$reason"
          fi
        fi
      fi
    fi
    # BAD: meta exit code != 0
    if [[ -n "$meta" && -f "$meta" && -z "$bad" ]] && command -v jq >/dev/null 2>&1; then
      local ec
      ec=$(jq -r '.exit_code // "null"' "$meta" 2>/dev/null)
      if [[ "$ec" != "null" && "$ec" != "0" ]]; then
        bad="exit_code=$ec"
      fi
    fi
    # BAD: launcher pid dead
    if [[ -n "$meta" && -f "$meta" && -z "$bad" ]] && command -v jq >/dev/null 2>&1; then
      local pid
      pid=$(jq -r '.launcher_pid // 0' "$meta" 2>/dev/null)
      if [[ "$pid" =~ ^[0-9]+$ ]] && [[ "$pid" -gt 0 ]] && ! kill -0 "$pid" 2>/dev/null; then
        bad="worker pid $pid dead"
      fi
    fi
    [[ -n "$bad" ]] && break
    [[ $(date +%s) -ge $deadline ]] && break
    sleep 1
  done

  # Emit notification iff bad OR silent/warn-without-good. A good signal after
  # a transient ERROR means the worker is alive; do not create a fake failure.
  if [[ -n "$bad" ]]; then
    spawn_probe_notify "Worker FAILED" "${agent}:${rid##*-} — $bad"
  elif [[ "$good" == "0" && -n "$warning" ]]; then
    spawn_probe_notify "Worker startup warning" "${agent}:${rid##*-} — $warning; check await pane"
  elif [[ "$good" == "0" ]]; then
    spawn_probe_notify "Worker silent on startup" "${agent}:${rid##*-} — check logs"
  fi
}

# Cross-platform system notification helper. iTerm2 OSC 9 is preferred inside
# iTerm; otherwise route through the Vibecrafted tray app bridge. Do not use
# AppleScript for notifications: macOS attributes those to Script Editor.
# Silent no-op when no notifier is available.
spawn_probe_notify() {
  local title="$1"
  local body="$2"
  local message="𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. — ${title}: ${body}"
  local tray_bin=""
  message="${message//$'\033'/ }"
  message="${message//$'\a'/ }"

  if [[ -n "${ITERM_SESSION_ID:-}" || "${TERM_PROGRAM:-}" == "iTerm.app" ]]; then
    if printf '\033]9;%s\a' "$message" >/dev/tty 2>/dev/null; then
      return 0
    fi
  fi

  tray_bin="${VIBECRAFTED_TRAY_NOTIFY_BIN:-}"
  [[ -n "$tray_bin" ]] || tray_bin="$(command -v vc-mux-tray 2>/dev/null || true)"
  if [[ -n "$tray_bin" && -x "$tray_bin" ]]; then
    "$tray_bin" notify --title "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. — $title" --message "$body" >/dev/null 2>&1 && return 0
  fi

  if command -v notify-send >/dev/null 2>&1; then
    notify-send "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. — $title" "$body" >/dev/null 2>&1 || true
  fi
}
