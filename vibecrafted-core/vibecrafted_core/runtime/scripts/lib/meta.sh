#!/usr/bin/env bash

spawn_sync_control_plane() {
  if ! spawn_python_module vibecrafted_core.control_plane sync >/dev/null; then
    printf '%s\n' 'vibecrafted: warning: control-plane sync failed; projection may be stale' >&2
  fi
}

spawn_is_safe_run_id() {
  # Same grammar as control-core is_safe_run_id: ASCII token, no traversal.
  local run_id="${1:-}"
  [[ -n "$run_id" && ${#run_id} -le 255 ]] || return 1
  [[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || return 1
}

spawn_runtime_meta_path() {
  # Canonical receipt: $VIBECRAFTED_HOME/control_plane/runtime_runs/<id>/meta.json
  # Never advertise control_plane/runs/<id>.json — that file is not written here.
  # Destination identity is the validated token, never a path fragment.
  local run_id="${1:-${SPAWN_RUN_ID:-}}"
  spawn_is_safe_run_id "$run_id" || return 1
  printf '%s/control_plane/runtime_runs/%s/meta.json\n' \
    "${VIBECRAFTED_HOME:-$HOME/.vibecrafted}" "$run_id"
}

spawn_mirror_meta_to_runtime_runs() {
  # Canonical dest comes from the source document's run_id, not SPAWN_RUN_ID.
  # finish/reap/GC may run with a different ambient run in the environment.
  local src="${1:-}"
  [[ -n "$src" && -f "$src" ]] || return 0
  "$(spawn_python_bin)" - "$src" <<'PY'
import json
import os
import shutil
import sys

src = sys.argv[1]
home = os.environ.get("VIBECRAFTED_HOME") or os.path.join(
    os.path.expanduser("~"), ".vibecrafted"
)

def is_safe_run_id(run_id: object) -> bool:
    if not isinstance(run_id, str) or not run_id or len(run_id) > 255:
        return False
    if not run_id[0].isalnum() or not run_id[0].isascii():
        return False
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
    return all(ch in allowed for ch in run_id)

try:
    with open(src, encoding="utf-8") as handle:
        payload = json.load(handle)
except (OSError, json.JSONDecodeError, UnicodeError):
    raise SystemExit(1)

if not isinstance(payload, dict):
    raise SystemExit(1)

run_id = payload.get("run_id")
if not is_safe_run_id(run_id):
    raise SystemExit(1)

runtime_runs = os.path.join(os.path.abspath(home), "control_plane", "runtime_runs")
dest_dir = os.path.join(runtime_runs, run_id)
dest = os.path.join(dest_dir, "meta.json")
if os.path.basename(dest) != "meta.json" or os.path.dirname(dest) != dest_dir:
    raise SystemExit(1)
if os.path.commonpath([runtime_runs, dest_dir]) != runtime_runs:
    raise SystemExit(1)

real_root = os.path.realpath(runtime_runs)
if os.path.lexists(dest_dir):
    real_dir = os.path.realpath(dest_dir)
    if real_dir != real_root and not real_dir.startswith(real_root + os.sep):
        raise SystemExit(1)
    if os.path.basename(real_dir) != run_id:
        raise SystemExit(1)

src_real = os.path.realpath(src)
if src_real.startswith(real_root + os.sep):
    rel = os.path.relpath(src_real, real_root)
    path_id = rel.split(os.sep, 1)[0]
    if path_id != run_id:
        raise SystemExit(1)

if os.path.isfile(dest):
    try:
        with open(dest, encoding="utf-8") as handle:
            existing = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError):
        existing = None
    if isinstance(existing, dict):
        existing_id = existing.get("run_id")
        if existing_id is not None and existing_id != run_id:
            raise SystemExit(1)
    try:
        if os.path.samefile(src, dest):
            raise SystemExit(0)
    except OSError:
        pass

os.makedirs(dest_dir, exist_ok=True)
tmp = f"{dest}.tmp.{os.getpid()}"
try:
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)
except OSError:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise SystemExit(1)
PY
}

spawn_settle_early_failure() {
  [[ -z "${SPAWN_SETTLING_EARLY_FAILURE:-}" ]] || return 0
  SPAWN_SETTLING_EARLY_FAILURE=1
  local reason="${1:-early failure}"
  local meta_path="${SPAWN_META:-}"
  local run_id="${SPAWN_RUN_ID:-}"
  local canonical=""
  canonical="$(spawn_runtime_meta_path "$run_id" 2>/dev/null || true)"
  if [[ -n "$meta_path" && -f "$meta_path" ]]; then
    spawn_finish_meta "$meta_path" "failed" "1" 2>/dev/null || true
  fi
  if [[ -z "$canonical" ]]; then
    return 0
  fi
  if [[ -f "$canonical" ]]; then
    spawn_finish_meta "$canonical" "failed" "1" 2>/dev/null || true
    return 0
  fi
  [[ -n "$run_id" ]] || return 0
  mkdir -p "$(dirname "$canonical")"
  spawn_write_meta "$canonical" "failed" "${SPAWN_AGENT:-unknown}" \
    "${SPAWN_SKILL_NAME:-unknown}" "${SPAWN_ROOT:-}" \
    "${SPAWN_PLAN:-}" "${SPAWN_REPORT:-}" "${SPAWN_TRANSCRIPT:-}" \
    "${SPAWN_LAUNCHER:-}" 2>/dev/null || true
  spawn_finish_meta "$canonical" "failed" "1" 2>/dev/null || true
  :
}

spawn_find_meta_for_run_id() {
  local reports_dir="$1"
  local target_run_id="$2"
  local canonical=""
  canonical="$(spawn_runtime_meta_path "$target_run_id" 2>/dev/null || true)"
  if [[ -n "$canonical" && -f "$canonical" ]]; then
    printf '%s\n' "$canonical"
    spawn_sync_control_plane
    return 0
  fi

  "$(spawn_python_bin)" - "$reports_dir" "$target_run_id" <<'PY'
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

  "$(spawn_python_bin)" - "$meta_path" "$field_name" <<'PY'
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
    "${GROK_MODEL:-}" \
    "${CURSOR_MODEL:-}"
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

  spawn_mirror_meta_to_runtime_runs "$meta_path"
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

  "$(spawn_python_bin)" - "$meta_path" "$pid" <<'PY'
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
  spawn_mirror_meta_to_runtime_runs "$meta_path"
  spawn_sync_control_plane
}

spawn_mark_meta_running() {
  # Called by the generated launcher immediately before it starts the agent
  # command. The launcher is the run owner: once it is alive (PID written)
  # and about to exec the agent, the run is running — keeping "launching"
  # past this point is a lie the dashboards repeat. Written atomically
  # (tmp + os.replace) so watchers never read a half-written meta.
  local meta_path="$1"
  [[ -f "$meta_path" ]] || return 0

  "$(spawn_python_bin)" - "$meta_path" <<'PY'
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
  spawn_mirror_meta_to_runtime_runs "$meta_path"
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

  "$(spawn_python_bin)" - "$meta_path" <<'PY'
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
  spawn_mirror_meta_to_runtime_runs "$meta_path"
  spawn_sync_control_plane
}

spawn_mark_unknown_liveness() {
  # Older live meta without launcher_pid is not safe to reap. Mark it
  # explicitly so dashboards stop pretending it is verified-live.
  local meta_path="$1"
  [[ -f "$meta_path" ]] || return 0

  "$(spawn_python_bin)" - "$meta_path" <<'PY'
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

spawn_gc_dead_meta_tree() {
  local root="$1"
  local name_glob="$2"
  [[ -d "$root" ]] || return 0

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
  done < <(find "$root" -type f -name "$name_glob" -print0 2>/dev/null)
}

spawn_gc_dead_runs() {
  # Scan reports *.meta.json and canonical runtime_runs/<id>/meta.json.
  local reports_dir="$1"
  spawn_gc_dead_meta_tree "$reports_dir" "*.meta.json"
  spawn_gc_dead_meta_tree \
    "${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/control_plane/runtime_runs" \
    "meta.json"
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

# spawn_python_bin lives in util.sh — beside spawn_prepend_agent_tool_paths, the
# sanitizer that removed the owned generation bin from PATH and therefore created
# the need for an explicit interpreter owner. util.sh is the no-deps layer sourced
# first, so every module below it can name the runtime interpreter.

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
  spawn_mirror_meta_to_runtime_runs "$meta_path"
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

# Sweep processes that outlived this (now terminal) run after canonical artifact
# closure. Run presentation belongs to vc-server/VOC; this helper never moves or
# closes a terminal tab.
#
# The reaper excludes its own pid and every ancestor, so calling it from inside
# the run it is cleaning up after is safe; only siblings (monitors, watchers) are
# candidates. It never fails a run that already finished.
spawn_reap_run() {
  spawn_python_module vibecrafted_core.run_reaper || true
}
