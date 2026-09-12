#!/usr/bin/env bash
set -euo pipefail
# Marbles Watcher — temporal guardian for convergence loops.
# Monitors promise → confirmed → done lifecycle per loop.
# Captures session IDs, tracks convergence trajectory, handles pause/stop.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

run_id="$1"
state_dir="$2"
total_count="$3"
root_dir="$4"
runtime="$5"
store="$6"
session_lock="$7"

ancestor_plan="$state_dir/ancestor.md"
god_plan="$state_dir/god.md"
state_file="$state_dir/state.json"
report_timeout_s="${VIBECRAFTED_MARBLES_REPORT_TIMEOUT_S:-5400}"
meta_timeout_s="${VIBECRAFTED_MARBLES_META_TIMEOUT_S:-60}"
case "$report_timeout_s" in
  ''|*[!0-9]*)
    report_timeout_s=5400
    ;;
esac
case "$meta_timeout_s" in
  ''|*[!0-9]*)
    meta_timeout_s=60
    ;;
esac
report_poll_s=5

# Internal runtime Python, named once for this process: the guards below and the
# state writer must agree on the interpreter, or a guard passes on the host while
# the write goes somewhere else.
watcher_py="$(spawn_python_bin)"

_state_json_edit() {
  local mutator="$1"
  shift

  command -v "$watcher_py" >/dev/null 2>&1 || return 1

  STATE_JSON_MUTATOR="$mutator" "$watcher_py" - "$state_file" "$@" <<'PY'
import datetime
import fcntl
import json
import os
import sys
import tempfile

state_path = sys.argv[1]
args = sys.argv[2:]
mutator = os.environ["STATE_JSON_MUTATOR"]
dir_path = os.path.dirname(state_path) or "."

if not os.path.isdir(dir_path):
    raise SystemExit(0)

try:
    lock = open(state_path + ".lock", "a+", encoding="utf-8")
except FileNotFoundError:
    raise SystemExit(0)

try:
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        with open(state_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        payload = {}

    exec(
        mutator,
        {"datetime": datetime, "json": json, "os": os},
        {"payload": payload, "args": args},
    )

    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(state_path) + ".", dir=dir_path
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_path, state_path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
finally:
    lock.close()
PY
}

_bold='\033[1m'
_copper='\033[38;5;173m'
_steel='\033[38;5;247m'
_green='\033[32m'
_yellow='\033[33m'
_red='\033[31m'
_dim='\033[2m'
_reset='\033[0m'

_write_state() {
  local tmp="$state_file.tmp"
  cat > "$tmp"
  mv "$tmp" "$state_file"
}

_init_state() {
  local initial_agent=""
  initial_agent="$(spawn_frontmatter_field "$ancestor_plan" "agent")"
  [[ -n "$initial_agent" ]] || initial_agent="unknown"

  if command -v "$watcher_py" >/dev/null 2>&1; then
    _state_json_edit "$(cat <<'PY'
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
payload.update(
    {
        "run_id": args[0],
        "agent": args[1] or payload.get("agent", "unknown"),
        "mode": payload.get("mode", "steered"),
        "plan": args[2],
        "god_plan": args[3],
        "ancestor_plan": args[2],
        "root": args[4],
        "runtime": args[5],
        "total_loops": int(args[6]),
        "current_loop": 0,
        "status": "initialized",
        "watcher_pid": int(args[7]),
        "updated_at": now,
        "loops": [],
        "trajectory": [],
    }
)
payload.setdefault("started_at", now)
PY
)" "$run_id" "$initial_agent" "$ancestor_plan" "$god_plan" "$root_dir" "$runtime" "$total_count" "$$" >/dev/null || true
  else
    _write_state <<EOF
{
  "run_id": "$run_id",
  "agent": "$initial_agent",
  "mode": "steered",
  "plan": "$ancestor_plan",
  "god_plan": "$god_plan",
  "ancestor_plan": "$ancestor_plan",
  "root": "$root_dir",
  "runtime": "$runtime",
  "total_loops": $total_count,
  "current_loop": 0,
  "status": "initialized",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "watcher_pid": $$,
  "loops": [],
  "trajectory": []
}
EOF
  fi
}

_update_status() {
  local new_status="$1"
  if command -v "$watcher_py" >/dev/null 2>&1; then
    _state_json_edit "$(cat <<'PY'
payload["status"] = args[0]
payload["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
PY
)" "$new_status" >/dev/null || true
  fi
}

_record_loop_start() {
  local loop_nr="$1"
  local transcript="$2"
  local agent_name="$3"
  local focus="$4"
  local ancestor_slug="$5"
  local model="${6:-}"
  local agent_source="${7:-}"

  if command -v "$watcher_py" >/dev/null 2>&1; then
    _state_json_edit "$(cat <<'PY'
loop_nr = int(args[0])
transcript, agent_name, focus, ancestor_slug, model, agent_source = args[1:7]
now = datetime.datetime.now(datetime.timezone.utc).isoformat()

payload["current_loop"] = loop_nr
payload["status"] = "promise"
payload["updated_at"] = now

loops = payload.get("loops", [])
target = None
for loop in loops:
    if loop.get("loop") == loop_nr:
        target = loop
        break

if target is None:
    target = {"loop": loop_nr, "started_at": now}
    loops.append(target)

target["status"] = "promise"
target["transcript"] = transcript
target["agent"] = agent_name
target["focus"] = focus
target["ancestor_slug"] = ancestor_slug
if model:
    target["model"] = model
else:
    target.pop("model", None)
if agent_source and "agent_source" not in target:
    target["agent_source"] = agent_source

# Keep loops materialized in ascending loop-number order. The watcher and
# marbles_next.sh mutate state.json concurrently; marbles_next can record the
# next loop (planned or spawn-failed) before the watcher appends the current
# loop. Sort by loop number so the materialization order is deterministic
# (done-then-failed, L1 before L2) regardless of which writer wins the race.
loops.sort(key=lambda lp: lp.get("loop") if isinstance(lp.get("loop"), int) else 0)
payload["loops"] = loops
PY
)" "$loop_nr" "$transcript" "$agent_name" "$focus" "$ancestor_slug" "$model" "$agent_source" >/dev/null || true
  fi
}

_record_confirmed() {
  local loop_nr="$1"
  local session_id="$2"
  if command -v "$watcher_py" >/dev/null 2>&1; then
    _state_json_edit "$(cat <<'PY'
payload["status"] = "confirmed"
payload["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

for loop in payload.get("loops", []):
    if loop.get("loop") == int(args[0]):
        loop["status"] = "confirmed"
        loop["session_id"] = args[1]
PY
)" "$loop_nr" "$session_id" >/dev/null || true
  fi
}

_record_loop_done() {
  local loop_nr="$1"
  local report="$2"
  local duration="$3"
  local p0="${4:-}"
  local p1="${5:-}"
  local p2="${6:-}"
  local score="${7:-}"
  local commit_count="${8:-}"
  local meta_path="${9:-}"
  if command -v "$watcher_py" >/dev/null 2>&1; then
    _state_json_edit "$(cat <<'PY'
loop_nr = int(args[0])
report = args[1]
duration = int(args[2])
p0 = int(args[3]) if args[3] else None
p1 = int(args[4]) if args[4] else None
p2 = int(args[5]) if args[5] else None
score = int(args[6]) if args[6] else None
commit_count = int(args[7]) if args[7] else None
meta_path = args[8] if len(args) > 8 else ""
now = datetime.datetime.now(datetime.timezone.utc).isoformat()

telemetry = {}
if meta_path and os.path.isfile(meta_path):
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            m = json.load(fh)
            if "usage" in m: telemetry["usage"] = m["usage"]
            if "tools" in m: telemetry["tools"] = m["tools"]
            if "model" in m: telemetry["model"] = m["model"]
    except Exception:
        pass

payload["updated_at"] = now
for loop in payload.get("loops", []):
    if loop.get("loop") == loop_nr:
        loop["status"] = "done"
        loop["report"] = report
        loop["duration_s"] = duration
        loop["completed_at"] = now
        loop["metrics"] = {"p0": p0, "p1": p1, "p2": p2, "score": score}
        if commit_count is not None:
            loop["metrics"]["commits"] = commit_count
        if telemetry:
            loop["telemetry"] = telemetry

payload.setdefault("trajectory", []).append(score)
PY
)" "$loop_nr" "$report" "$duration" "$p0" "$p1" "$p2" "$score" "$commit_count" "$meta_path" >/dev/null || true
  fi
}

_record_loop_timeout() {
  local loop_nr="$1"
  local reason="$2"
  local duration="$3"
  if command -v "$watcher_py" >/dev/null 2>&1; then
    _state_json_edit "$(cat <<'PY'
loop_nr = int(args[0])
reason = args[1]
duration = int(args[2])
now = datetime.datetime.now(datetime.timezone.utc).isoformat()

payload["status"] = "timed_out"
payload["updated_at"] = now
for loop in payload.get("loops", []):
    if loop.get("loop") == loop_nr:
        loop["status"] = "timed_out"
        loop["failure_reason"] = reason
        loop["duration_s"] = duration
        loop["completed_at"] = now
PY
)" "$loop_nr" "$reason" "$duration" >/dev/null || true
  fi
}

_record_loop_failed() {
  local loop_nr="$1"
  local reason="$2"
  local duration="$3"
  local report_path="${4:-}"
  local exit_code="${5:-}"
  local meta_path="${6:-}"
  if command -v "$watcher_py" >/dev/null 2>&1; then
    _state_json_edit "$(cat <<'PY'
loop_nr = int(args[0])
reason = args[1]
duration = int(args[2])
report_path = args[3]
exit_code = int(args[4]) if args[4] else None
meta_path = args[5] if len(args) > 5 else ""
now = datetime.datetime.now(datetime.timezone.utc).isoformat()

telemetry = {}
if meta_path and os.path.isfile(meta_path):
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            m = json.load(fh)
            if "usage" in m: telemetry["usage"] = m["usage"]
            if "tools" in m: telemetry["tools"] = m["tools"]
            if "model" in m: telemetry["model"] = m["model"]
            if exit_code is None and m.get("exit_code") is not None:
                exit_code = int(m["exit_code"])
    except Exception:
        pass

payload["status"] = "failed"
payload["updated_at"] = now
for loop in payload.get("loops", []):
    if loop.get("loop") == loop_nr:
        loop["status"] = "failed"
        loop["failure_reason"] = reason
        loop["duration_s"] = duration
        loop["completed_at"] = now
        if report_path:
            loop["report"] = report_path
        if exit_code is not None:
            loop["exit_code"] = exit_code
        if telemetry:
            loop["telemetry"] = telemetry
PY
)" "$loop_nr" "$reason" "$duration" "$report_path" "$exit_code" "$meta_path" >/dev/null || true
  fi
}

_record_verification_pending() {
  local loop_nr="$1"

  if command -v "$watcher_py" >/dev/null 2>&1; then
    _state_json_edit "$(cat <<'PY'
payload["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
for loop in payload.get("loops", []):
    if loop.get("loop") == int(args[0]) and loop.get("verification_status") != "completed":
        loop["verification_status"] = "pending"
PY
)" "$loop_nr" >/dev/null || true
  fi
}

_start_verification_watch() {
  local loop_nr="$1"
  local report_path="$2"

  [[ -n "$report_path" ]] || return 0
  [[ -f "$SCRIPT_DIR/marbles_verify_watch.sh" ]] || return 0

  _record_verification_pending "$loop_nr"
  nohup bash "$SCRIPT_DIR/marbles_verify_watch.sh" \
    "$state_file" "$loop_nr" "$report_path" >/dev/null 2>&1 &
}

_render_chain() {
  local current="$1"
  local total="$2"
  local chain=""
  for ((i = 1; i <= total; i++)); do
    if (( i <= current )); then
      chain+="◉"
    else
      chain+="○"
    fi
    if (( i < total )); then
      chain+="───"
    fi
  done
  printf '%s' "$chain"
}

_render_loop_phase() {
  local loop_nr="$1"
  local phase="$2"
  local detail="${3:-}"
  local chain=""
  chain="$(_render_chain "$loop_nr" "$total_count")"

  case "$phase" in
    promise)
      printf '\n %bL%s%b %s\n' "$_bold" "$loop_nr" "$_reset" "$chain"
      printf '    %bpromise    ░░░░░░░░░░░░░░░░░░░░%b\n' "$_dim" "$_reset"
      printf '    spawning %s...\n' "$detail"
      ;;
    confirmed)
      printf '\r\033[3A'
      printf '\n %bL%s%b %s\n' "$_bold" "$loop_nr" "$_reset" "$chain"
      printf '    %bconfirmed%b  session: %s\n' "$_green" "$_reset" "${detail:0:13}"
      printf '    ████░░░░░░░░░░░░░░░░  agent working\n'
      ;;
    done)
      printf '\r\033[3A'
      printf '\n %bL%s%b %s\n' "$_bold" "$loop_nr" "$_reset" "$chain"
      printf '    %bloop ✓%b     %s\n' "$_green" "$_reset" "$detail"
      ;;
    timeout)
      printf '\r\033[3A'
      printf '\n %bL%s%b %s\n' "$_bold" "$loop_nr" "$_reset" "$chain"
      printf '    %btimeout%b   %s\n' "$_red" "$_reset" "$detail"
      ;;
    failed)
      printf '\r\033[3A'
      printf '\n %bL%s%b %s\n' "$_bold" "$loop_nr" "$_reset" "$chain"
      printf '    %bfailed%b    %s\n' "$_red" "$_reset" "$detail"
      ;;
  esac
}

_capture_session_id() {
  local transcript="$1"
  local session_id=""
  local attempts=0

  while [[ -z "$session_id" ]] && (( attempts < 15 )); do
    sleep 2
    (( attempts++ ))

    [[ -f "$transcript" ]] || continue
    session_id=$(sed 's/\x1b\[[0-9;]*m//g' "$transcript" 2>/dev/null \
      | grep -m1 -oE 'session: [a-zA-Z0-9-]{8,}' \
      | awk '{print $2}' || true)
  done

  printf '%s' "$session_id"
}

_extract_metrics() {
  local report="$1"

  if [[ -f "$report" ]]; then
    "$watcher_py" - "$report" <<'PY'
import re
import sys

path = sys.argv[1]
p = {"p0": "", "p1": "", "p2": "", "score": "", "commits": ""}
commit_lines = []
no_commit = False

try:
    lines = open(path, encoding="utf-8").read().splitlines()
except OSError:
    print("||||", end="")
    raise SystemExit(0)

for line in lines:
    stripped = line.strip()
    m = re.match(r"^-?\s*(P[012])\s*:?\s*([0-9]+)\b", stripped, re.I)
    if m:
        p[m.group(1).lower()] = m.group(2)
        continue

    if not p["score"]:
        m = re.search(r"\b(score|convergence)\b.*?\b([0-9]+)\s*/\s*100\b", stripped, re.I)
        if m:
            p["score"] = m.group(2)

    if re.search(r"\bno commit (was )?(created|made)\b", stripped, re.I):
        no_commit = True
    if re.match(r"^-?\s*(commit|commits|commit sha|commit shas)\s*:", stripped, re.I):
        commit_lines.append(stripped)

if no_commit or any(re.search(r":\s*(none|no|n/a|0)\b", line, re.I) for line in commit_lines):
    p["commits"] = "0"
elif commit_lines:
    shas = []
    for line in commit_lines:
        shas.extend(re.findall(r"\b[0-9a-f]{7,40}\b", line, re.I))
    p["commits"] = str(len(set(sha.lower() for sha in shas)) or 1)

print("|".join([p["p0"], p["p1"], p["p2"], p["score"], p["commits"]]), end="")
PY
    return 0
  fi

  printf '||||'
}

_marbles_failure_hint() {
  local report_path="$1"
  local transcript_path="$2"
  local meta_path="$3"
  local hint=""

  hint="$(
    "$watcher_py" - "$report_path" "$transcript_path" "$meta_path" <<'PY'
import json
import os
import re
import sys

report_path, transcript_path, meta_path = sys.argv[1:4]

keyword_re = re.compile(
    r"\b(error|failed|failure|exception|traceback|reason|exit code|not found|permission denied|killed|timed out|denied|cannot)\b",
    re.IGNORECASE,
)


def strip_ansi(line: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)


def normalize(line: str) -> str:
    return strip_ansi(line).strip()


def pick_from_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return ""

    # Skip YAML frontmatter if present.
    frontmatter = 0
    if lines and lines[0].strip() == "---":
        frontmatter = 1

    last = ""
    signal = ""
    for line in lines:
        l = line.strip()
        if frontmatter == 1:
            if l == "---":
                frontmatter = 2
            continue
        if l == "---":
            continue

        cleaned = normalize(line)
        if not cleaned:
            continue
        if cleaned.startswith("#"):
            continue
        if cleaned.startswith("---"):
            continue

        last = cleaned
        if keyword_re.search(cleaned):
            signal = cleaned

    if signal:
        return signal
    return last


def pick_from_meta(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return ""

    if not isinstance(meta, dict):
        return ""

    if meta.get("error"):
        return f"reason: {meta['error']}"

    candidate_fields = []
    for key in ("reason", "message"):
        value = meta.get(key, "")
        if value == "" or value is None:
            continue
        candidate_fields.append(f"{key}: {value}")

    if candidate_fields:
        return " | ".join(candidate_fields[:3])
    return ""


hint = pick_from_text(report_path)
if not hint:
    hint = pick_from_text(transcript_path)
if not hint:
    hint = pick_from_meta(meta_path)

if hint:
    print(hint)
PY
  )"

  hint="${hint//$'\n'/ }"
  hint="$(printf '%s' "$hint" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  printf '%s' "$hint"
}

_marbles_truncate_hint() {
  local hint="$1"
  local max_len="${2:-200}"
  local fallback=""

  if [[ ${#hint} -gt $max_len ]]; then
    fallback="${hint:0:$max_len-3}..."
    printf '%s' "$fallback"
  else
    printf '%s' "$hint"
  fi
}

_find_convergence_report() {
  local convergence=""
  while IFS= read -r -d '' convergence; do
    if [[ "$(spawn_frontmatter_field "$convergence" "run_id")" == "$run_id" ]]; then
      printf '%s\n' "$convergence"
      return 0
    fi
  done < <(find "$store/reports" -maxdepth 1 -type f -name '*_CONVERGENCE.md' -print0 2>/dev/null)
  return 1
}

_wait_for_convergence_report() {
  local attempts="$1"
  local backoff_s="$2"
  local backoff_max_s="$3"
  local attempt=1
  local delay="$backoff_s"

  if _find_convergence_report >/dev/null; then
    return 0
  fi

  while (( attempt <= attempts )); do
    (( delay > 0 )) && sleep "$delay"
    if _find_convergence_report >/dev/null; then
      return 0
    fi
    (( delay > 0 )) || delay=1
    delay=$((delay * 2))
    if (( backoff_max_s > 0 && delay > backoff_max_s )); then
      delay="$backoff_max_s"
    fi
    ((attempt++))
  done

  return 1
}

_write_reception_convergence_guard() {
  local observed_status="$1"
  local reason="$2"
  local fallback_attempts="${3:-0}"
  local backoff_initial_s="${4:-0}"
  local backoff_max_s="${5:-0}"
  local convergence="$store/reports/$(spawn_timestamp)_marbles-$(spawn_slug_from_path "$ancestor_plan")_CONVERGENCE.md"
  local agent_name=""

  agent_name="$(spawn_frontmatter_field "$ancestor_plan" "agent")"
  [[ -n "$agent_name" ]] || agent_name="${actual_meta_agent:-unknown}"

  cat > "$convergence" <<CONV
---
run_id: $run_id
agent: $agent_name
status: FAILED
observed_status: $observed_status
reason: $reason
guard: pani_krysia
guard_kind: reception_guard
failure_kind: missing_convergence_handoff
effect: guard_failure
guard_policy: convergence_handoff_backoff_v1
fallback_attempts: $fallback_attempts
backoff_initial_s: $backoff_initial_s
backoff_max_s: $backoff_max_s
failover: reception_guard_failure_report
total_loops: $total_count
god_plan: $god_plan
ancestor_plan: $ancestor_plan
---

# Marbles Convergence — FAILED

Reception guard observed terminal watcher status without a convergence report.

- Observed watcher status: $observed_status
- Reason: $reason
- Guard: Pani Krysia
- Failure kind: missing_convergence_handoff
- Policy: convergence_handoff_backoff_v1
- Fallback attempts: $fallback_attempts
- Backoff: ${backoff_initial_s}s initial, ${backoff_max_s}s max
- Failover: reception_guard_failure_report
- Effect: the run is treated as failed until the missing convergence handoff is explained
- GOD: $god_plan
- ANCESTOR: $ancestor_plan
CONV

  _update_status "failed"
  printf '\n  %bPani Krysia:%b missing convergence report — wrote guard failure\n' "$_yellow" "$_reset"
  printf '  guard convergence: %s\n' "$convergence"
}

_wait_for_loop_meta() {
  local loop_nr="$1"
  local timeout_s="$2"
  local elapsed=0
  local meta_path=""
  local expected_run_id="${run_id}-$(printf '%03d' "$loop_nr")"
  local final_grace_s="${VIBECRAFTED_MARBLES_META_FINAL_GRACE_S:-4}"
  local grace_elapsed=0

  while true; do
    meta_path="$(spawn_find_meta_for_run_id "$store/reports" "$expected_run_id")"
    if [[ -n "$meta_path" ]]; then
      printf '%s\n' "$meta_path"
      return 0
    fi

    if [[ -f "$state_dir/stop" ]]; then
      return 1
    fi

    if (( timeout_s > 0 && elapsed >= timeout_s )); then
      while (( final_grace_s > 0 && grace_elapsed < final_grace_s )); do
        sleep 1
        (( grace_elapsed += 1 ))
        meta_path="$(spawn_find_meta_for_run_id "$store/reports" "$expected_run_id")"
        if [[ -n "$meta_path" ]]; then
          printf '%s\n' "$meta_path"
          return 0
        fi
        if [[ -f "$state_dir/stop" ]]; then
          return 1
        fi
      done
      return 2
    fi

    sleep 2
    (( elapsed += 2 ))
  done
}

_log_file_size() {
  local file_path="$1"
  if [[ -f "$file_path" ]]; then
    wc -c < "$file_path" 2>/dev/null | tr -d ' '
  else
    printf '0\n'
  fi
}

_wait_for_report_path() {
  local report_path="$1"
  local timeout_s="$2"
  local transcript_file="${3:-}"
  local meta_path="${4:-}"
  local stall_limit_s="${VIBECRAFTED_MARBLES_STALL_LIMIT_S:-600}"
  local elapsed=0
  local last_size=0
  local stall_elapsed=0

  while true; do
    local meta_status=""
    if [[ -n "$meta_path" ]]; then
      meta_status="$(spawn_read_meta_field "$meta_path" "status")"
      if [[ "$meta_status" == "failed" ]]; then
        return 4
      fi

      # Heartbeat — validate launcher_pid via kill -0. If the bash launcher
      # died without running its exit handler (terminal close, SIGKILL, crash),
      # meta would sit as "launching"/"running" forever and downstream tools
      # would treat the corpse as live. Reap it now: flip status to "ghost",
      # release the lock, return as failed so this loop terminates cleanly.
      if [[ "$meta_status" =~ ^(launching|running|in-progress)$ ]]; then
        local _launcher_pid=""
        _launcher_pid="$(spawn_read_meta_field "$meta_path" "launcher_pid")"
        if [[ -n "$_launcher_pid" && "$_launcher_pid" != "None" ]]; then
          if ! spawn_pid_alive "$_launcher_pid"; then
            spawn_reap_dead_run "$meta_path"
            return 4
          fi
        else
          spawn_mark_unknown_liveness "$meta_path"
        fi
      fi
    fi

    if [[ -n "$meta_path" && -z "$report_path" ]]; then
      report_path="$(spawn_read_meta_field "$meta_path" "report")"
    fi

    # A report can appear before the launcher finishes. Only consume the loop
    # once meta has reached "completed"; otherwise the watcher can advance to
    # the next loop while the current success_hook has not launched it yet.
    if [[ -n "$report_path" && -s "$report_path" ]]; then
      if [[ -z "$meta_path" || "$meta_status" == "completed" ]]; then
        printf '%s\n' "$report_path"
        return 0
      fi
    fi

    if [[ -f "$state_dir/stop" ]]; then
      return 1
    fi

    if [[ -n "$transcript_file" && -f "$transcript_file" ]]; then
      local current_size=""
      current_size="$(_log_file_size "$transcript_file")"
      if (( current_size > last_size )); then
        last_size=$current_size
        stall_elapsed=0
      else
        (( stall_elapsed += report_poll_s ))
      fi

      if (( stall_limit_s > 0 && stall_elapsed >= stall_limit_s )); then
        return 3
      fi
    fi

    if (( timeout_s > 0 && elapsed >= timeout_s )); then
      return 2
    fi

    sleep "$report_poll_s"
    (( elapsed += report_poll_s ))
  done
}

_check_pause() {
  if [[ -f "$state_dir/pause" ]]; then
    _update_status "paused"
    printf '\n %b⏸ PAUSED%b  (vc-marbles resume %s)\n' "$_yellow" "$_reset" "$run_id"
    while [[ -f "$state_dir/pause" ]]; do
      sleep 3
      [[ -f "$state_dir/stop" ]] && return 1
    done
    _update_status "running"
    printf ' %b▶ RESUMED%b\n' "$_green" "$_reset"
  fi
  return 0
}

_check_stop() {
  if [[ -f "$state_dir/stop" ]]; then
    _update_status "stopped"
    printf '\n %b■ STOPPED%b  by user\n' "$_red" "$_reset"
    return 1
  fi
  return 0
}

_check_locker() {
  if command -v rust-ai-locker >/dev/null 2>&1; then
    local heavy_count=""
    heavy_count=$(rust-ai-locker scan --json 2>/dev/null | "$watcher_py" -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('heavy',[])))" 2>/dev/null || echo "0")
    if [[ "$heavy_count" -gt 0 ]]; then
      printf '    %b⚠ %s heavy process(es) detected — consider waiting%b\n' "$_yellow" "$heavy_count" "$_reset"
    fi
  fi
}

_init_state

ancestor_slug="$(spawn_slug_from_path "$ancestor_plan")"
total_start=$(date +%s)
converged=0
stopped=0
timed_out=0
timed_out_loop=0
failed=0
failed_loop=0
terminal_status=""

for ((loop_nr = 1; loop_nr <= total_count; loop_nr++)); do
  if ! _check_stop; then
    stopped=1
    break
  fi
  if ! _check_pause; then
    stopped=1
    break
  fi

  _check_locker

  ln_plan="$(spawn_marbles_child_plan_path "$store" "$ancestor_plan" "$loop_nr")"
  loop_agent=""
  loop_focus=""
  loop_model=""
  if [[ -f "$ln_plan" ]]; then
    loop_agent="$(spawn_frontmatter_field "$ln_plan" "agent")"
    loop_focus="$(spawn_frontmatter_field "$ln_plan" "focus")"
    loop_model="$(spawn_frontmatter_field "$ln_plan" "model")"
  fi
  # When child plan has no agent, consult state.json loop records before
  # falling back to ancestor.md — keeps watcher aligned with marbles_next.sh
  if [[ -z "$loop_agent" && -f "$state_file" ]] && command -v "$watcher_py" >/dev/null 2>&1; then
    loop_agent="$("$watcher_py" - "$state_file" "$loop_nr" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    d = json.load(f)
for loop in d.get("loops", []):
    if loop.get("loop") == int(sys.argv[2]) and loop.get("agent"):
        print(loop["agent"], end="")
        raise SystemExit(0)
PY
    )"
  fi
  if [[ -z "$loop_agent" ]]; then
    loop_agent="$(spawn_frontmatter_field "$ancestor_plan" "agent")"
  fi
  if [[ -z "$loop_focus" ]]; then
    loop_focus="$(spawn_frontmatter_field "$ancestor_plan" "focus")"
  fi
  if [[ -z "$loop_model" ]]; then
    loop_model="$(spawn_frontmatter_field "$ancestor_plan" "model")"
  fi
  [[ -n "$loop_agent" ]] || loop_agent="unknown"

  _record_loop_start "$loop_nr" "" "$loop_agent" "$loop_focus" "$ancestor_slug" "$loop_model" "rotation"

  promise_detail="$loop_agent"
  if [[ -n "$loop_focus" ]]; then
    promise_detail="$promise_detail · $loop_focus"
  fi
  _render_loop_phase "$loop_nr" "promise" "$promise_detail"

  loop_start=$(date +%s)

  meta_path=""
  if meta_path="$(_wait_for_loop_meta "$loop_nr" "$meta_timeout_s")"; then
    :
  else
    meta_status=$?
    loop_end=$(date +%s)
    duration=$((loop_end - loop_start))
    duration_fmt="$(printf '%dm %02ds' $((duration/60)) $((duration%60)))"
    if (( meta_status == 1 )); then
      _check_stop || true
      stopped=1
      break
    fi
    timed_out=1
    timed_out_loop=$loop_nr
    _record_loop_timeout "$loop_nr" "meta-missing" "$duration"
    _render_loop_phase "$loop_nr" "timeout" "$duration_fmt  no meta.json within ${meta_timeout_s}s"
    break
  fi

  actual_transcript="$(spawn_read_meta_field "$meta_path" "transcript")"
  actual_report_hint="$(spawn_read_meta_field "$meta_path" "report")"
  actual_meta_status="$(spawn_read_meta_field "$meta_path" "status")"
  actual_exit_code="$(spawn_read_meta_field "$meta_path" "exit_code")"
  failure_hint="$(_marbles_failure_hint "$actual_report_hint" "$actual_transcript" "$meta_path")"
  # Trust the meta's agent over the pre-resolved child plan agent — the plan
  # may not exist yet when the watcher enters this loop (race with marbles_next).
  actual_meta_agent="$(spawn_read_meta_field "$meta_path" "agent")"
  if [[ -n "$actual_meta_agent" ]]; then
    loop_agent="$actual_meta_agent"
  fi
  _record_loop_start "$loop_nr" "$actual_transcript" "$loop_agent" "$loop_focus" "$ancestor_slug" "$loop_model"

  if [[ "$actual_meta_status" == "failed" ]]; then
    loop_end=$(date +%s)
    duration=$((loop_end - loop_start))
    duration_fmt="$(printf '%dm %02ds' $((duration/60)) $((duration%60)))"
    _record_loop_failed "$loop_nr" "spawn-failed" "$duration" "$actual_report_hint" "$actual_exit_code" "$meta_path"
    detail="$duration_fmt  failed before report"
    if [[ -n "$failure_hint" ]]; then
      detail="$detail  - $(_marbles_truncate_hint "$failure_hint" 220)"
    fi
    if [[ -n "$actual_exit_code" ]]; then
      detail="$detail  exit ${actual_exit_code}"
    fi
    _render_loop_phase "$loop_nr" "failed" "$detail"
    failed=1
    failed_loop=$loop_nr
    break
  fi

  session_id=""
  if [[ -n "$actual_transcript" ]]; then
    session_id="$(_capture_session_id "$actual_transcript")"
  fi
  if [[ -z "$session_id" ]]; then
    session_id="$(spawn_read_meta_field "$meta_path" "session_id")"
  fi
  if [[ -n "$session_id" ]]; then
    _record_confirmed "$loop_nr" "$session_id"
    _render_loop_phase "$loop_nr" "confirmed" "$session_id"
  fi

  wait_status=0
  actual_report=""
  if actual_report="$(_wait_for_report_path "$actual_report_hint" "$report_timeout_s" "$actual_transcript" "$meta_path")"; then
    :
  else
    wait_status=$?
  fi

  if [[ -z "$actual_report" || ! -s "$actual_report" ]] && (( wait_status != 0 )); then
    loop_end=$(date +%s)
    duration=$((loop_end - loop_start))
    duration_fmt="$(printf '%dm %02ds' $((duration/60)) $((duration%60)))"

    if (( wait_status == 1 )); then
      _check_stop || true
      stopped=1
      break
    fi

    if (( wait_status == 4 )); then
      exit_code_hint="$(spawn_read_meta_field "$meta_path" "exit_code")"
      if [[ -z "$failure_hint" ]]; then
        failure_hint="$(_marbles_failure_hint "$actual_report_hint" "$actual_transcript" "$meta_path")"
      fi
      _record_loop_failed "$loop_nr" "spawn-failed" "$duration" "$actual_report_hint" "$exit_code_hint" "$meta_path"
      detail="$duration_fmt  failed before report"
      if [[ -n "$failure_hint" ]]; then
        detail="$detail  - $(_marbles_truncate_hint "$failure_hint" 220)"
      fi
      if [[ -n "$exit_code_hint" ]]; then
        detail="$detail  exit ${exit_code_hint}"
      fi
      _render_loop_phase "$loop_nr" "failed" "$detail"
      failed=1
      failed_loop=$loop_nr
      break
    fi

    timed_out=1
    timed_out_loop=$loop_nr
    if (( wait_status == 3 )); then
      _record_loop_timeout "$loop_nr" "agent-stalled" "$duration"
      _render_loop_phase "$loop_nr" "timeout" "$duration_fmt  transcript stalled"
    else
      _record_loop_timeout "$loop_nr" "report-missing" "$duration"
      _render_loop_phase "$loop_nr" "timeout" "$duration_fmt  no report within ${report_timeout_s}s"
    fi
    break
  fi

  loop_end=$(date +%s)
  duration=$((loop_end - loop_start))
  duration_fmt="$(printf '%dm %02ds' $((duration/60)) $((duration%60)))"

  actual_meta_status="$(spawn_read_meta_field "$meta_path" "status")"
  report_status="$(_report_frontmatter_status "$actual_report")"
  if [[ "$actual_meta_status" == "failed" || "$report_status" == "failed" ]]; then
    exit_code_hint="$(spawn_read_meta_field "$meta_path" "exit_code")"
    if [[ -z "$exit_code_hint" ]]; then
      for _exit_wait_i in 1 2 3 4 5 6 7 8; do
        sleep 0.25
        exit_code_hint="$(spawn_read_meta_field "$meta_path" "exit_code")"
        [[ -n "$exit_code_hint" ]] && break
      done
    fi
    failure_reason="spawn-failed"
    if [[ "$report_status" == "failed" && "$actual_meta_status" != "failed" ]]; then
      failure_reason="report-failed"
    fi
    _record_loop_failed "$loop_nr" "$failure_reason" "$duration" "$actual_report" "$exit_code_hint" "$meta_path"
    detail="$duration_fmt  failed before convergence report"
    if [[ -z "$failure_hint" ]]; then
      failure_hint="$(_marbles_failure_hint "$actual_report" "$actual_transcript" "$meta_path")"
    fi
    if [[ -n "$failure_hint" ]]; then
      detail="$detail  - $(_marbles_truncate_hint "$failure_hint" 220)"
    fi
    if [[ -n "$exit_code_hint" ]]; then
      detail="$detail  exit ${exit_code_hint}"
    fi
    _render_loop_phase "$loop_nr" "failed" "$detail"
    failed=1
    failed_loop=$loop_nr
    break
  fi

  IFS='|' read -r p0 p1 p2 score commit_count <<< "$(_extract_metrics "$actual_report")"
  _record_loop_done "$loop_nr" "$actual_report" "$duration" "$p0" "$p1" "$p2" "$score" "$commit_count" "$meta_path"

  detail="$duration_fmt"
  if [[ -n "$p0" || -n "$p1" || -n "$p2" ]]; then
    detail="$duration_fmt  P0:${p0:-?} P1:${p1:-?} P2:${p2:-?}"
    [[ -n "$score" ]] && detail="$detail  score:${score}/100"
    [[ -n "$commit_count" ]] && detail="$detail  commits:${commit_count}"
  fi
  _render_loop_phase "$loop_nr" "done" "$detail"

  _start_verification_watch "$loop_nr" "$actual_report"

  if [[ "${p0:-}" == "0" && "${p1:-}" == "0" && "${p2:-}" == "0" ]] \
     && [[ -n "$p0" && -n "$p1" && -n "$p2" ]]; then
    converged=1
    break
  fi
done

total_end=$(date +%s)
total_duration=$((total_end - total_start))
total_fmt="$(printf '%dm %02ds' $((total_duration/60)) $((total_duration%60)))"

trajectory=""
if command -v "$watcher_py" >/dev/null 2>&1 && [[ -f "$state_file" ]]; then
  trajectory=$("$watcher_py" - "$state_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)

scores = [str(score) for score in payload.get("trajectory", []) if score is not None]
print(" → ".join(scores))
PY
  )
fi

if (( converged )); then
  terminal_status="converged"
  _update_status "converged"
  loops_saved=$((total_count - loop_nr))
  printf '\n %b⚒  Converged · %s/%s loops · %s%b\n' "$_bold$_green" "$loop_nr" "$total_count" "$total_fmt" "$_reset"
  printf '%b──────────────────────────────────%b\n' "$_steel" "$_reset"
  printf '  %s  circle full\n' "$(_render_chain "$loop_nr" "$total_count")"
  [[ -n "$trajectory" ]] && printf '  %s\n' "$trajectory"
  printf '  ████████████████████████████████████████████████\n'
  (( loops_saved > 0 )) && printf '\n  loops saved: %s (converged early)\n' "$loops_saved"
elif (( timed_out )); then
  terminal_status="failed"
  _update_status "failed"
  completed_loops=$((timed_out_loop - 1))
  printf '\n %b⚒  Failed · timeout at L%s/%s · %s%b\n' "$_bold$_red" "$timed_out_loop" "$total_count" "$total_fmt" "$_reset"
  printf '%b──────────────────────────────────%b\n' "$_steel" "$_reset"
  printf '  %s\n' "$(_render_chain "$completed_loops" "$total_count")"
  printf '  report pathing is meta.json-only; loop not consumed\n'
elif (( failed )); then
  terminal_status="failed"
  _update_status "failed"
  completed_loops=$((failed_loop - 1))
  printf '\n %b⚒  Failed · loop failure at L%s/%s · %s%b\n' "$_bold$_red" "$failed_loop" "$total_count" "$total_fmt" "$_reset"
  printf '%b──────────────────────────────────%b\n' "$_steel" "$_reset"
  printf '  %s\n' "$(_render_chain "$completed_loops" "$total_count")"
  failed_summary_hint="$(_marbles_failure_hint "$actual_report_hint" "$actual_transcript" "$meta_path")"
  if [[ -n "$failed_summary_hint" ]]; then
    printf '  loop consumed truthfully; failure surfaced as: %s\n' "$failed_summary_hint"
  else
    printf '  loop consumed truthfully; failure surfaced from launch metadata\n'
  fi
elif (( stopped )); then
  terminal_status="stopped"
  _update_status "stopped"
  printf '\n %b⚒  Stopped · %s%b\n' "$_bold$_yellow" "$total_fmt" "$_reset"
  printf '%b──────────────────────────────────%b\n' "$_steel" "$_reset"
  printf '  %s\n' "$(_render_chain "$((loop_nr-1))" "$total_count")"
else
  terminal_status="completed"
  _update_status "completed"
  printf '\n %b⚒  Complete · %s loops · %s%b\n' "$_bold$_copper" "$total_count" "$total_fmt" "$_reset"
  printf '%b──────────────────────────────────%b\n' "$_steel" "$_reset"
  printf '  %s\n' "$(_render_chain "$total_count" "$total_count")"
  [[ -n "$trajectory" ]] && printf '  %s\n' "$trajectory"
fi

if [[ -n "$terminal_status" ]]; then
  convergence_grace_s="${VIBECRAFTED_MARBLES_CONVERGENCE_GRACE_S:-}"
  convergence_attempts="${VIBECRAFTED_MARBLES_CONVERGENCE_ATTEMPTS:-4}"
  convergence_backoff_s="${VIBECRAFTED_MARBLES_CONVERGENCE_BACKOFF_S:-1}"
  convergence_backoff_max_s="${VIBECRAFTED_MARBLES_CONVERGENCE_BACKOFF_MAX_S:-8}"

  # Backward compatibility: the old single-grace knob remains a deterministic
  # one-shot policy when explicitly set by tests/operators.
  if [[ -n "$convergence_grace_s" ]]; then
    convergence_attempts=1
    convergence_backoff_s="$convergence_grace_s"
  fi

  case "$convergence_attempts" in
    ''|*[!0-9]*)
      convergence_attempts=4
      ;;
  esac
  case "$convergence_backoff_s" in
    ''|*[!0-9]*)
      convergence_backoff_s=1
      ;;
  esac
  case "$convergence_backoff_max_s" in
    ''|*[!0-9]*)
      convergence_backoff_max_s=8
      ;;
  esac
  if ! _wait_for_convergence_report "$convergence_attempts" "$convergence_backoff_s" "$convergence_backoff_max_s"; then
    guard_reason="missing_convergence_after_${terminal_status}"
    _write_reception_convergence_guard \
      "$terminal_status" \
      "$guard_reason" \
      "$convergence_attempts" \
      "$convergence_backoff_s" \
      "$convergence_backoff_max_s"
    terminal_status="failed"
  fi
fi

verification_grace_s="${VIBECRAFTED_MARBLES_VERIFICATION_GRACE_S:-30}"
case "$verification_grace_s" in
  ''|*[!0-9]*)
    verification_grace_s=30
    ;;
esac

if command -v "$watcher_py" >/dev/null 2>&1 && [[ -f "$state_file" ]]; then
  printf '\n  %bverification:%b ' "$_dim" "$_reset"
  if (( verification_grace_s > 0 )); then
    sleep "$verification_grace_s"
  fi
  if [[ -f "$state_file" ]]; then
    "$watcher_py" - "$state_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)

pending = completed = timed = 0
for loop in payload.get("loops", []):
    status = loop.get("verification_status", "")
    if status == "completed":
        completed += 1
    elif status == "pending":
        pending += 1
    elif status == "timed_out":
        timed += 1

parts = []
if completed:
    parts.append(f"{completed} done")
if pending:
    parts.append(f"{pending} pending")
if timed:
    parts.append(f"{timed} timed out")
print(", ".join(parts) if parts else "none tracked")
PY
  else
    printf 'state archived before verification summary'
  fi
fi

printf '\n  lock released: %s\n' "$run_id"
printf '%b──────────────────────────────────%b\n\n' "$_steel" "$_reset"

rm -f "$session_lock" 2>/dev/null || true

if [[ -n "$terminal_status" ]]; then
  archived_state_dir="$(spawn_archive_marbles_state_dir "$run_id" "$terminal_status" 2>/dev/null || true)"
  if [[ -n "$archived_state_dir" ]]; then
    printf '  state archived: %s\n' "$archived_state_dir"
  fi
fi
