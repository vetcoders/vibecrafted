#!/usr/bin/env bash

spawn_sync_control_plane() {
  if ! spawn_python_module vibecrafted_core.control_plane sync >/dev/null; then
    printf '%s\n' 'vibecrafted: warning: control-plane sync failed; projection may be stale' >&2
  fi
}

spawn_find_meta_for_run_id() {
  local reports_dir="$1"
  local target_run_id="$2"

  python3 - "$reports_dir" "$target_run_id" <<'PY'
import json
import os
import sys

reports_dir, target_run_id = sys.argv[1:3]
if not os.path.isdir(reports_dir):
    raise SystemExit(0)

for fname in sorted(os.listdir(reports_dir), reverse=True):
    if not fname.endswith(".meta.json"):
        continue
    fpath = os.path.join(reports_dir, fname)
    try:
        with open(fpath, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        continue
    if payload.get("run_id") == target_run_id:
        print(fpath)
        raise SystemExit(0)
PY
  spawn_sync_control_plane
}

spawn_read_meta_field() {
  local meta_path="$1"
  local field_name="$2"

  python3 - "$meta_path" "$field_name" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        payload = json.load(handle)
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)

value = payload.get(sys.argv[2], "")
if value is None:
    value = ""
print(value, end="")
PY
}

spawn_detect_model_identity() {
  local agent="${1:-}"
  local explicit_model="${2:-}"

  explicit_model="$(spawn_clean_model "$explicit_model")"
  if [[ -n "$explicit_model" ]]; then
    printf '%s\n' "$explicit_model"
    return 0
  fi

  local candidate
  for candidate in \
    "${VIBECRAFTED_PARENT_MODEL:-}" \
    "${CLAUDE_MODEL:-}" \
    "${CODEX_MODEL:-}" \
    "${GEMINI_MODEL:-}" \
    "${GROK_MODEL:-}"
  do
    candidate="$(spawn_clean_model "$candidate")"
    if [[ -n "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  case "$agent" in
    claude) printf 'claude-cli-default\n' ;;
    codex) printf 'codex-cli-default\n' ;;
    agy) printf 'gemini-cli-default\n' ;;
    grok) printf 'grok-cli-default\n' ;;
    junie) printf 'junie-cli-default\n' ;;
    *) printf '%s-cli-default\n' "${agent:-agent}" ;;
  esac
}

spawn_write_meta() {
  local meta_path="$1"
  local status="$2"
  local agent="$3"
  local mode="$4"
  local root="$5"
  local input_ref="$6"
  local report="$7"
  local transcript="$8"
  local launcher="$9"
  local model="${10:-}"
  local prompt_id="${SPAWN_PROMPT_ID:-}"
  local run_id="${SPAWN_RUN_ID:-}"
  local loop_nr="${SPAWN_LOOP_NR:-0}"
  local skill_code="${SPAWN_SKILL_CODE:-}"
  local framework_version
  framework_version="$(spawn_framework_version)"
  model="$(spawn_detect_model_identity "$agent" "$model")"

  spawn_python_module vibecrafted_core.spawn write-meta \
    "$meta_path" \
    "$status" \
    "$agent" \
    "$mode" \
    "$root" \
    "$input_ref" \
    "$report" \
    "$transcript" \
    "$launcher" \
    --model "$model" \
    --prompt-id "$prompt_id" \
    --run-id "$run_id" \
    --loop-nr "$loop_nr" \
    --skill-code "$skill_code" \
    --framework-version "$framework_version"

  spawn_sync_control_plane
}

spawn_update_meta_pid() {
  # Called by the generated launcher as soon as it starts. Writes the
  # launcher's own PID into the meta.json so the watcher and the
  # spawn-time GC can validate liveness via `kill -0`. Dead PID = ghost.
  local meta_path="$1"
  local pid="$2"

  [[ -f "$meta_path" ]] || return 0
  [[ -n "$pid" ]] || return 0

  python3 - "$meta_path" "$pid" <<'PY'
import json
import os
import sys

meta_path, pid = sys.argv[1:3]
try:
    with open(meta_path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
except (OSError, json.JSONDecodeError):
    sys.exit(0)

try:
    payload["launcher_pid"] = int(pid)
    payload["liveness"] = "pid_alive"
except (TypeError, ValueError):
    payload["launcher_pid"] = None
    payload["liveness"] = "unknown_legacy"

tmp_path = f"{meta_path}.tmp.{os.getpid()}"
with open(tmp_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
os.replace(tmp_path, meta_path)
PY
}

spawn_mark_meta_running() {
  # Called by the generated launcher immediately before it starts the agent
  # command. The launcher is the run owner: once it is alive (PID written)
  # and about to exec the agent, the run is running — keeping "launching"
  # past this point is a lie the dashboards repeat. Written atomically
  # (tmp + os.replace) so watchers never read a half-written meta.
  local meta_path="$1"
  [[ -f "$meta_path" ]] || return 0

  python3 - "$meta_path" <<'PY'
import datetime as dt
import json
import os
import sys

meta_path = sys.argv[1]
try:
    with open(meta_path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
except (OSError, json.JSONDecodeError):
    sys.exit(0)

# Only the launching->running edge belongs here; never resurrect a run that
# a watcher or finisher already moved to a terminal state.
if payload.get("status") not in ("launching", "pending"):
    sys.exit(0)

payload["status"] = "running"
payload["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()

tmp_path = f"{meta_path}.tmp.{os.getpid()}"
with open(tmp_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
os.replace(tmp_path, meta_path)
PY
  spawn_sync_control_plane
}

spawn_pid_alive() {
  # Returns 0 if pid is alive, 1 if dead or invalid. Uses kill -0 semantics;
  # treats permission denied as alive (kernel says: exists, not yours).
  local pid="$1"
  [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

spawn_reap_dead_run() {
  # Given a meta.json whose launcher_pid is dead, flip status to "ghost",
  # release any lock file referenced in the meta, and sync control plane.
  # Idempotent — callable from spawn-time GC and from watcher heartbeat.
  local meta_path="$1"
  [[ -f "$meta_path" ]] || return 0

  python3 - "$meta_path" <<'PY'
import datetime as dt
import json
import os
import sys

meta_path = sys.argv[1]
try:
    with open(meta_path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
except (OSError, json.JSONDecodeError):
    sys.exit(0)

status = payload.get("status")
if status not in ("launching", "running", "in-progress"):
    sys.exit(0)

now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
payload["status"] = "ghost"
payload["updated_at"] = now_iso
payload.setdefault("completed_at", now_iso)
payload.setdefault("exit_code", 137)  # canonical kill-killed code, for parity
payload["ghost_reason"] = "launcher_pid dead at reap"
payload["liveness"] = "pid_dead"

tmp_path = f"{meta_path}.tmp.{os.getpid()}"
with open(tmp_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
os.replace(tmp_path, meta_path)

# Best-effort lock cleanup — meta may reference a lock path.
lock_path = payload.get("run_lock") or payload.get("lock")
if lock_path and os.path.isfile(lock_path):
    try:
        os.unlink(lock_path)
    except OSError:
        pass
PY
  spawn_sync_control_plane
}

spawn_mark_unknown_liveness() {
  # Older live meta without launcher_pid is not safe to reap. Mark it
  # explicitly so dashboards stop pretending it is verified-live.
  local meta_path="$1"
  [[ -f "$meta_path" ]] || return 0

  python3 - "$meta_path" <<'PY'
import datetime as dt
import json
import os
import sys

meta_path = sys.argv[1]
try:
    with open(meta_path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
except (OSError, json.JSONDecodeError):
    sys.exit(0)

if payload.get("status") not in ("launching", "running", "in-progress"):
    sys.exit(0)

pid = payload.get("launcher_pid")
if pid not in (None, "", "None"):
    sys.exit(0)

payload["liveness"] = "unknown_legacy"
payload.setdefault("liveness_reason", "live status without launcher_pid")
payload["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()

tmp_path = f"{meta_path}.tmp.{os.getpid()}"
with open(tmp_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
os.replace(tmp_path, meta_path)
PY
  spawn_sync_control_plane
}

spawn_gc_dead_runs() {
  # Scan a reports directory for meta.json files whose status is live
  # (launching/running/in-progress) but whose launcher_pid is dead.
  # Flip those to ghost. Safe to call at spawn-time before taking locks.
  local reports_dir="$1"
  [[ -d "$reports_dir" ]] || return 0

  local meta_path pid_value
  while IFS= read -r -d '' meta_path; do
    pid_value="$(spawn_read_meta_field "$meta_path" "launcher_pid")"
    # Safe reap contract: only reap when we can VERIFY the PID is dead.
    # Missing launcher_pid = pre-GC-era meta or older launcher that never
    # wrote it — we cannot prove death, so we leave it alone. This avoids
    # false-positive reaping of still-running agents whose meta was written
    # by an older launcher template. TTL-based cleanup is a separate path.
    if [[ -z "$pid_value" || "$pid_value" == "None" ]]; then
      spawn_mark_unknown_liveness "$meta_path"
      continue
    fi
    if ! spawn_pid_alive "$pid_value"; then
      spawn_reap_dead_run "$meta_path"
    fi
  done < <(find "$reports_dir" -type f -name '*.meta.json' -print0 2>/dev/null)
}

spawn_python_core_path() {
  local candidate_core=""
  local candidate_root=""
  if [[ -n "${VIBECRAFTED_CORE_PYTHONPATH:-}" ]]; then
    printf '%s\n' "$VIBECRAFTED_CORE_PYTHONPATH"
    return 0
  fi
  if [[ -n "${_SPAWN_LIB_DIR:-}" ]]; then
    candidate_core="$(cd "$_SPAWN_LIB_DIR/../../../.." && pwd)"
    if [[ -d "$candidate_core/vibecrafted_core" ]]; then
      printf '%s\n' "$candidate_core"
      return 0
    fi
  fi
  for candidate_root in "${SPAWN_ROOT:-}" "${VIBECRAFTED_STORE_ROOT:-}" "$PWD"; do
    [[ -n "$candidate_root" ]] || continue
    if [[ -d "$candidate_root/vibecrafted_core" ]]; then
      printf '%s\n' "$candidate_root"
      return 0
    fi
    candidate_core="$candidate_root/vibecrafted-core"
    if [[ -d "$candidate_core/vibecrafted_core" ]]; then
      printf '%s\n' "$candidate_core"
      return 0
    fi
  done
  return 1
}

# RESOLVER TRUTH: This resolver is kept exclusively for runtime-side/split-brain
# ./runtime execution where the uv shim might not be directly in the execution chain.
# This is NOT a deck-level plaster.
#
# Resolve an interpreter that can import vibecrafted_core. The package needs
# tomllib (Python 3.11+); bare `python3` on macOS is often /usr/bin/python3 3.9.6
# which lacks tomllib, so vibecrafted_core dies with ModuleNotFoundError. Prefer
# the uv tool venv python (has the package + deps), then VIBECRAFTED_PYTHON, then
# any 3.11+ python on PATH.
spawn_python_bin() {
  local candidate
  for candidate in \
    "${VIBECRAFTED_PYTHON:-}" \
    "${XDG_DATA_HOME:-$HOME/.local/share}/uv/tools/vibecrafted/bin/python3" \
    "${XDG_DATA_HOME:-$HOME/.local/share}/uv/tools/vibecrafted-core/bin/python3" \
    python3.13 python3.12 python3.11 python3; do
    [[ -n "$candidate" ]] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  printf 'python3\n'
}

spawn_python_module() {
  local core_path py
  core_path="$(spawn_python_core_path 2>/dev/null || true)"
  py="$(spawn_python_bin)"
  if [[ -n "$core_path" ]]; then
    PYTHONPATH="${core_path}${PYTHONPATH:+:$PYTHONPATH}" "$py" -m "$@"
  else
    "$py" -m "$@"
  fi
}

spawn_finish_meta() {
  local meta_path="$1"
  local status="$2"
  local exit_code="${3:-0}"

  # Terminal meta state is Python-owned; this shell call is the stable wrapper.
  spawn_python_module vibecrafted_core.spawn finish-meta "$meta_path" "$status" "$exit_code"
  spawn_sync_control_plane
}

spawn_finalize_artifacts() {
  local meta_path="$1"
  local report_path="${2:-}"
  local transcript_path="${3:-}"
  local final_meta=""

  [[ -f "$meta_path" ]] || return 0

  final_meta="$(
    spawn_python_module vibecrafted_core.spawn finalize-artifacts \
      "$meta_path" \
      "$report_path" \
      "$transcript_path"
  )" || return 1
  [[ -n "$final_meta" && -f "$final_meta" && ! -L "$final_meta" ]] || return 1
  spawn_sync_control_plane
  printf '%s\n' "$final_meta"
}

# Move a finished run's tab into its vc-frame status bucket. Runs LAST, after
# artifacts are closed: a successful transfer closes the tab this launcher is
# running in, so anything sequenced after it may never execute.
#
# Triage is decoration on an already-finished run, so this never fails a run —
# the Python side swallows every error and records a receipt instead. The `|| true`
# is belt-and-braces for the interpreter itself failing to start.
spawn_triage_run() {
  local meta_path="$1"

  [[ -f "$meta_path" ]] || return 0

  spawn_python_module vibecrafted_core.run_triage "$meta_path" || true
}

# Sweep processes that outlived this (now terminal) run. Runs BEFORE triage: a
# successful triage closes the tab we are running in, so anything sequenced after
# it may never execute — and the survivors would keep burning cores until reboot.
#
# The reaper excludes its own pid and every ancestor, so calling it from inside
# the run it is cleaning up after is safe; only siblings (monitors, watchers) are
# candidates. Like triage, it never fails a run that already finished.
spawn_reap_run() {
  spawn_python_module vibecrafted_core.run_reaper || true
}
