#!/bin/bash
# vc-app-update — sole UI-bundle mutation owner.
#
# This script is the only replacement implementation. The App launches it and
# does not reimplement wait/ditto/backup. It does not stop Frame, PTYs, workers,
# sessions, or rewrite Founder config, PATH, interpreters, or MCP.
#
# Contract:
#   1. Canonicalize and reject symlink/traversal paths and path-like
#      --transaction values before any mutation.
#   2. Preflight (source/destination/capture + signed identity). There is no
#      production unsigned bypass.
#   3. Persist a correlated READY admission (transaction, identity, destination,
#      parent PID/start). Parent may quit only after this record.
#   4. Wait for the bound parent identity to disappear.
#   5. Journal every mutation phase before changing the destination. Resume
#      reconciles every write-ahead phase against the observed
#      source/dest/prepared/displaced tuple and never falls through to recapture.
#   6. Write the validated terminal receipt BEFORE relaunch. terminal_detail()
#      is the sole authority for that detail; no terminal spells its own.
#      Replace emits replaced, restore emits restored, recover emits recovered
#      -- on every path, including resume. Recover restores the prior Runtime
#      Pack through install-runtime-pack.sh first, then the matching prior app.
#   7. Destination exclusion is flock(2) on a durable inode, inlined from
#      scripts/install-runtime-pack.sh. The lock file is never unlinked.
#      Release closes this process's descriptor only. Installer, sleep, and
#      other children must not inherit a copy of that descriptor: a leftover
#      copy keeps the inode held after this helper is SIGTERM'd.
#
# System tools only: the destination App and its runtime Python may be moving.
set -euo pipefail

SOURCE=""
DESTINATION=""
RECEIPT=""
ADMISSION=""
JOURNAL=""
TRANSACTION=""
MODE="replace"
WAIT_PID=""
WAIT_START=""
WAIT_TIMEOUT="30"
RELAUNCH=0
RESUME=0
EXPECTED_IDENTIFIER="io.vetcoders.vibecrafted"
EXPECTED_TEAM="MW223P3NPX"
OPEN_BIN="/usr/bin/open"
FAIL_AFTER=""
HOLD_AFTER=""
HOLD_UNTIL=""
PRIOR_PACK=""
PRIOR_GENERATION=""
INSTALLER_STATUS=""

HARNESS=0
if [[ "${VIBECRAFTED_UPDATE_HELPER_HARNESS:-}" == "1" ]]; then
  HARNESS=1
fi

usage() {
  echo "usage: vc-app-update --source APP --destination APP --receipt FILE [--admission FILE] [--journal FILE] [--transaction ID] [--mode replace|restore|recover] [--wait-pid PID] [--wait-start LSTART] [--wait-timeout SECONDS] [--relaunch] [--resume] [--expected-identifier ID] [--expected-team TEAM]" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="${2:-}"; shift 2 ;;
    --destination) DESTINATION="${2:-}"; shift 2 ;;
    --receipt) RECEIPT="${2:-}"; shift 2 ;;
    --admission) ADMISSION="${2:-}"; shift 2 ;;
    --journal) JOURNAL="${2:-}"; shift 2 ;;
    --transaction) TRANSACTION="${2:-}"; shift 2 ;;
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --wait-pid) WAIT_PID="${2:-}"; shift 2 ;;
    --wait-start) WAIT_START="${2:-}"; shift 2 ;;
    --wait-timeout) WAIT_TIMEOUT="${2:-}"; shift 2 ;;
    --relaunch) RELAUNCH=1; shift ;;
    --resume) RESUME=1; shift ;;
    --expected-identifier) EXPECTED_IDENTIFIER="${2:-}"; shift 2 ;;
    --expected-team) EXPECTED_TEAM="${2:-}"; shift 2 ;;
    --open-bin)
      if [[ "$HARNESS" -ne 1 ]]; then
        echo "refusing --open-bin without VIBECRAFTED_UPDATE_HELPER_HARNESS=1" >&2
        exit 2
      fi
      OPEN_BIN="${2:-}"
      shift 2
      ;;
    --fail-after)
      if [[ "$HARNESS" -ne 1 ]]; then
        echo "refusing --fail-after without VIBECRAFTED_UPDATE_HELPER_HARNESS=1" >&2
        exit 2
      fi
      FAIL_AFTER="${2:-}"
      shift 2
      ;;
    --hold-after)
      if [[ "$HARNESS" -ne 1 ]]; then
        echo "refusing --hold-after without VIBECRAFTED_UPDATE_HELPER_HARNESS=1" >&2
        exit 2
      fi
      HOLD_AFTER="${2:-}"
      shift 2
      ;;
    --until)
      if [[ "$HARNESS" -ne 1 ]]; then
        echo "refusing --until without VIBECRAFTED_UPDATE_HELPER_HARNESS=1" >&2
        exit 2
      fi
      HOLD_UNTIL="${2:-}"
      shift 2
      ;;
    --allow-unsigned)
      echo "refusing --allow-unsigned: production and fixture trust share codesign --verify --strict" >&2
      exit 2
      ;;
    *) usage ;;
  esac
done

[[ -n "$SOURCE" && -n "$DESTINATION" && -n "$RECEIPT" ]] || usage
if [[ "$MODE" != "replace" && "$MODE" != "restore" && "$MODE" != "recover" ]]; then
  echo "mode must be replace, restore, or recover" >&2
  exit 2
fi

# Survive parent UI exit. Do not follow or replace through a symlink.
trap '' HUP

escape_json() {
  printf '%s' "$1" | /usr/bin/sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

atomic_write() {
  local dest="$1"
  local body="$2"
  local dir
  dir="$(dirname "$dest")"
  mkdir -p "$dir"
  local tmp
  tmp="${dest}.tmp.$$"
  umask 077
  printf '%s\n' "$body" >"$tmp"
  without_update_lock_fd /bin/mv -f "$tmp" "$dest"
}

process_lstart() {
  local pid="$1"
  without_update_lock_fd /bin/ps -p "$pid" -o lstart= 2>/dev/null | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

identity_live() {
  local pid="$1"
  local expected="$2"
  if ! /bin/kill -0 "$pid" 2>/dev/null; then
    return 1
  fi
  local now
  now="$(process_lstart "$pid")"
  if [[ -z "$now" ]]; then
    return 1
  fi
  if [[ -n "$expected" && "$now" != "$expected" ]]; then
    return 1
  fi
  return 0
}

wait_for_identity() {
  local pid="$1"
  local expected="$2"
  local timeout="$3"
  local n=0
  local max
  max="$(/usr/bin/awk -v t="$timeout" 'BEGIN { if (t < 1) t = 1; printf "%d", t * 20 }')"
  while (( n < max )); do
    if ! identity_live "$pid" "$expected"; then
      return 0
    fi
    without_update_lock_fd /bin/sleep 0.05
    n=$((n + 1))
  done
  echo "timed out waiting for pid $pid identity" >&2
  return 1
}

hold_if() {
  local phase="$1"
  if [[ -n "$HOLD_AFTER" && "$HOLD_AFTER" == "$phase" && -n "$HOLD_UNTIL" ]]; then
    local n=0
    while [[ ! -e "$HOLD_UNTIL" ]]; do
      without_update_lock_fd /bin/sleep 0.05
      n=$((n + 1))
      if (( n > 1200 )); then
        echo "timed out holding after $phase" >&2
        return 1
      fi
    done
  fi
  return 0
}

fail_after_if() {
  local phase="$1"
  if [[ -n "$FAIL_AFTER" && "$FAIL_AFTER" == "$phase" ]]; then
    echo "harness injected failure after $phase" >&2
    write_journal "$phase" "injected failure after ${phase}"
    exit 40
  fi
}

validate_transaction_id() {
  local id="$1"
  [[ -n "$id" ]] || return 0
  case "$id" in
    */*|*"\\"*|.*|*..*)
      echo "refusing path-like --transaction: $id" >&2
      exit 2
      ;;
  esac
  if ! printf '%s' "$id" | /usr/bin/grep -Eq '^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$'; then
    echo "refusing unsafe --transaction" >&2
    exit 2
  fi
}

path_has_dotdot() {
  case "/$1/" in
    */../*) return 0 ;;
    *) return 1 ;;
  esac
}

assert_no_symlink_components() {
  local input="$1"
  local abs current=""
  if path_has_dotdot "$input"; then
    echo "refusing traversal path: $input" >&2
    exit 6
  fi
  case "$input" in
    /*) abs="$input" ;;
    *) abs="$PWD/$input" ;;
  esac
  if path_has_dotdot "$abs"; then
    echo "refusing traversal path: $abs" >&2
    exit 6
  fi
  local rest="${abs#/}"
  current=""
  while [[ -n "$rest" ]]; do
    local part="${rest%%/*}"
    if [[ "$rest" == */* ]]; then
      rest="${rest#*/}"
    else
      rest=""
    fi
    [[ -n "$part" && "$part" != "." ]] || continue
    current="${current}/${part}"
    if [[ -L "$current" ]]; then
      echo "refusing symlink path component: $current" >&2
      exit 6
    fi
    if [[ ! -e "$current" ]]; then
      break
    fi
  done
}

physical_existing_dir() {
  local path="$1"
  assert_no_symlink_components "$path"
  if [[ ! -d "$path" || -L "$path" ]]; then
    echo "refusing non-directory path: $path" >&2
    exit 6
  fi
  (cd -P "$path" && pwd -P)
}

canonical_leaf_path() {
  local path="$1"
  local parent base parent_phys
  assert_no_symlink_components "$path"
  parent="$(dirname "$path")"
  base="$(basename "$path")"
  parent_phys="$(physical_existing_dir "$parent")"
  if [[ -e "${parent_phys}/${base}" && -L "${parent_phys}/${base}" ]]; then
    echo "refusing symlink leaf: ${parent_phys}/${base}" >&2
    exit 6
  fi
  printf '%s/%s\n' "$parent_phys" "$base"
}

verify_signed_app() {
  local app="$1"
  if [[ -L "$app" ]]; then
    echo "refusing symlink app: $app" >&2
    return 1
  fi
  if ! without_update_lock_fd /usr/bin/codesign --verify --strict --verbose=4 "$app" >/dev/null 2>&1; then
    echo "codesign --verify --strict failed: $app" >&2
    return 1
  fi
  local display
  display="$(without_update_lock_fd /usr/bin/codesign --display --verbose=4 "$app" 2>&1 || true)"
  echo "$display" | /usr/bin/grep -q "^Identifier=${EXPECTED_IDENTIFIER}$" || {
    echo "codesign identifier is not ${EXPECTED_IDENTIFIER}" >&2
    return 1
  }
  echo "$display" | /usr/bin/grep -q "^TeamIdentifier=${EXPECTED_TEAM}$" || {
    echo "codesign TeamIdentifier is not ${EXPECTED_TEAM}" >&2
    return 1
  }
  return 0
}

app_cdhash() {
  without_update_lock_fd /usr/bin/codesign --display --verbose=4 "$1" 2>&1 | /usr/bin/awk -F= '/^CDHash=/{print $2; exit}'
}

app_identity_token() {
  local app="$1"
  local hash
  if [[ ! -d "$app" || -L "$app" ]]; then
    return 1
  fi
  hash="$(app_cdhash "$app")"
  if [[ -z "$hash" ]]; then
    return 1
  fi
  printf 'cdhash:%s' "$hash"
}

require_exact_identity() {
  local app="$1"
  local expected="$2"
  local label="$3"
  local now
  if [[ -z "$expected" ]]; then
    echo "journal is missing the expected $label identity" >&2
    exit 14
  fi
  if ! verify_signed_app "$app"; then
    echo "$label failed signed identity check: $app" >&2
    exit 15
  fi
  now="$(app_identity_token "$app")" || {
    echo "$label has no durable content identity: $app" >&2
    exit 15
  }
  if [[ "$now" != "$expected" ]]; then
    echo "$label identity ${now} is not the journaled ${expected}" >&2
    exit 15
  fi
}

owned_prepared() {
  case "$1" in
    "${parent}/.vc-update-prepared-${TRANSACTION}.app"|"${parent}/.vc-update-prepared-${TRANSACTION}.app/")
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

remove_owned_prepared() {
  local path="$1"
  if owned_prepared "$path"; then
    without_update_lock_fd /bin/rm -rf "$path"
    return 0
  fi
  echo "refusing to remove a path this helper does not own: $path" >&2
  return 1
}

owned_capture_root() {
  case "$1" in
    "${parent}/.vc-update-capture-${TRANSACTION}"|"${parent}/.vc-update-capture-${TRANSACTION}/")
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

journal_get() {
  local key="$1"
  if [[ ! -f "$JOURNAL" ]]; then
    return 1
  fi
  without_update_lock_fd /usr/bin/python3 - "$JOURNAL" "$key" <<'PY'
import json, sys
path, key = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception:
    sys.exit(1)
value = data.get(key)
if value is None:
    sys.exit(1)
if isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

journal_require() {
  local key="$1"
  local value
  value="$(journal_get "$key")" || {
    echo "resume journal missing required field: $key" >&2
    exit 14
  }
  if [[ -z "$value" ]]; then
    echo "resume journal has empty required field: $key" >&2
    exit 14
  fi
  printf '%s' "$value"
}

write_journal() {
  local phase="$1"
  local detail="${2:-}"
  atomic_write "$JOURNAL" "$(printf '{"schema":"io.vetcoders.vibecrafted.app-update-journal.v1","capture":"%s","candidate_identity":"%s","destination":"%s","detail":"%s","displaced":"%s","identifier":"%s","installer_status":"%s","mode":"%s","operation":"%s","parent":"%s","parent_pid":"%s","parent_start":"%s","phase":"%s","prepared":"%s","prior_generation":"%s","prior_identity":"%s","prior_pack":"%s","receipt":"%s","source":"%s","source_identity":"%s","team_id":"%s","transaction":"%s"}' \
    "$(escape_json "$CAPTURE")" \
    "$(escape_json "$SOURCE_IDENTITY")" \
    "$(escape_json "$DESTINATION")" \
    "$(escape_json "$detail")" \
    "$(escape_json "$DISPLACED")" \
    "$(escape_json "$EXPECTED_IDENTIFIER")" \
    "$(escape_json "$INSTALLER_STATUS")" \
    "$(escape_json "$MODE")" \
    "$(escape_json "$MODE")" \
    "$(escape_json "$parent")" \
    "$(escape_json "$WAIT_PID")" \
    "$(escape_json "$WAIT_START")" \
    "$(escape_json "$phase")" \
    "$(escape_json "$PREPARED")" \
    "$(escape_json "$PRIOR_GENERATION")" \
    "$(escape_json "$PRIOR_IDENTITY")" \
    "$(escape_json "$PRIOR_PACK")" \
    "$(escape_json "$RECEIPT")" \
    "$(escape_json "$SOURCE")" \
    "$(escape_json "$SOURCE_IDENTITY")" \
    "$(escape_json "$EXPECTED_TEAM")" \
    "$(escape_json "$TRANSACTION")")"
}

write_admission() {
  local status="$1"
  local detail="${2:-}"
  atomic_write "$ADMISSION" "$(printf '{"schema":"io.vetcoders.vibecrafted.app-replacement-admission.v1","capture":"%s","destination":"%s","detail":"%s","identifier":"%s","journal":"%s","mode":"%s","operation":"%s","parent_pid":"%s","parent_start":"%s","receipt":"%s","source":"%s","source_identity":"%s","status":"%s","team_id":"%s","transaction":"%s"}' \
    "$(escape_json "$CAPTURE")" \
    "$(escape_json "$DESTINATION")" \
    "$(escape_json "$detail")" \
    "$(escape_json "$EXPECTED_IDENTIFIER")" \
    "$(escape_json "$JOURNAL")" \
    "$(escape_json "$MODE")" \
    "$(escape_json "$MODE")" \
    "$(escape_json "$WAIT_PID")" \
    "$(escape_json "$WAIT_START")" \
    "$(escape_json "$RECEIPT")" \
    "$(escape_json "$SOURCE")" \
    "$(escape_json "$SOURCE_IDENTITY")" \
    "$(escape_json "$status")" \
    "$(escape_json "$EXPECTED_TEAM")" \
    "$(escape_json "$TRANSACTION")")"
}

terminal_detail() {
  if [[ "$MODE" == "recover" ]]; then
    printf '%s' "recovered"
  elif [[ "$MODE" == "restore" ]]; then
    printf '%s' "restored"
  else
    printf '%s' "replaced"
  fi
}

write_terminal_receipt() {
  local detail="$1"
  local replaced="$2"
  local recovered="false"
  if [[ "$MODE" == "recover" ]]; then
    recovered="true"
  fi
  atomic_write "$RECEIPT" "$(printf '{"schema":"io.vetcoders.vibecrafted.app-replacement.v1","capture":"%s","destination":"%s","detail":"%s","identifier":"%s","installer_status":"%s","journal":"%s","mode":"%s","operation":"%s","pack_generation":"%s","phase":"receipt_written","prior_identity":"%s","recovered":%s,"relaunched":false,"replaced":%s,"source_identity":"%s","team_id":"%s","transaction":"%s"}' \
    "$(escape_json "$CAPTURE")" \
    "$(escape_json "$DESTINATION")" \
    "$(escape_json "$detail")" \
    "$(escape_json "$EXPECTED_IDENTIFIER")" \
    "$(escape_json "$INSTALLER_STATUS")" \
    "$(escape_json "$JOURNAL")" \
    "$(escape_json "$MODE")" \
    "$(escape_json "$MODE")" \
    "$(escape_json "$PRIOR_GENERATION")" \
    "$(escape_json "$PRIOR_IDENTITY")" \
    "$recovered" \
    "$replaced" \
    "$(escape_json "$SOURCE_IDENTITY")" \
    "$(escape_json "$EXPECTED_TEAM")" \
    "$(escape_json "$TRANSACTION")")"
  write_journal "receipt_written" "$detail"
}

relaunch_destination() {
  if [[ "$RELAUNCH" -ne 1 ]]; then
    return 0
  fi
  write_journal "relaunching" "receipt already persisted"
  if ! without_update_lock_fd "$OPEN_BIN" -n "$DESTINATION"; then
    echo "relaunch of $DESTINATION failed after the replacement receipt was written" >&2
    atomic_write "${RECEIPT}.relaunch.json" "$(printf '{"schema":"io.vetcoders.vibecrafted.app-replacement-relaunch.v1","destination":"%s","relaunched":false,"transaction":"%s"}' \
      "$(escape_json "$DESTINATION")" "$(escape_json "$TRANSACTION")")"
    return 1
  fi
  atomic_write "${RECEIPT}.relaunch.json" "$(printf '{"schema":"io.vetcoders.vibecrafted.app-replacement-relaunch.v1","destination":"%s","relaunched":true,"transaction":"%s"}' \
    "$(escape_json "$DESTINATION")" "$(escape_json "$TRANSACTION")")"
  write_journal "relaunched" "opened after receipt"
  return 0
}

owned_failed_new() {
  case "$1" in
    "${CAPTURE}/failed-new.app"|"${CAPTURE}/failed-new.app/")
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

owned_displaced() {
  case "$1" in
    "${CAPTURE}/displaced.app"|"${CAPTURE}/displaced.app/")
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

reject_preflight() {
  local detail="$1"
  local code="$2"
  write_journal "failed" "$detail"
  write_admission "rejected" "$detail"
  echo "$detail" >&2
  exit "$code"
}

# Destination exclusion is flock(2) on a durable inode. mkdir+pid reclaim is
# the race install-runtime-pack.sh already rejected: observing a dead owner
# and removing the directory can delete a live successor. The lock directory
# may exist from the previous mkdir protocol; the claim is $lock/held and is
# never unlinked. Release closes this process's descriptor only. Children
# (installer, sleep, published runtime) must not inherit a copy.
UPDATE_LOCK_FD=""
UPDATE_LOCK_FD_FALLBACK=201

close_update_lock_fd() {
  [[ -n "${UPDATE_LOCK_FD:-}" ]] || return 0
  eval "exec ${UPDATE_LOCK_FD}>&-" 2>/dev/null || true
  UPDATE_LOCK_FD=""
}

# Close this process's lock descriptor in the child only. The parent keeps
# the flock. Never unlink held/ and never signal a foreign holder.
without_update_lock_fd() {
  if [[ -z "${UPDATE_LOCK_FD:-}" ]]; then
    "$@"
    return
  fi
  (
    eval "exec ${UPDATE_LOCK_FD}>&-" || true
    "$@"
  )
}

open_update_held_fd() {
  local held="$1"
  UPDATE_LOCK_FD=""
  if ((BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 1))); then
    exec {UPDATE_LOCK_FD}>>"$held" || return 1
  else
    UPDATE_LOCK_FD="$UPDATE_LOCK_FD_FALLBACK"
    eval "exec ${UPDATE_LOCK_FD}>>\"\$held\"" || return 1
  fi
  [[ -n "${UPDATE_LOCK_FD:-}" ]] || return 1
}

# 0 acquired · 1 held by a live descriptor · 2 this host offers no file lock
# · 3 the inherited descriptor could not be flocked
flock_update_lock_nb() {
  local fd="$1"
  if command -v flock >/dev/null 2>&1; then
    flock -n "$fd"
    return
  fi
  if command -v perl >/dev/null 2>&1; then
    perl -e '
      use Fcntl qw(:flock);
      open(my $handle, ">&=", $ARGV[0]) or exit 3;
      exit(flock($handle, LOCK_EX | LOCK_NB) ? 0 : 1);
    ' "$fd"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 -c '
import fcntl
import sys
try:
    fcntl.flock(int(sys.argv[1]), fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    raise SystemExit(1)
' "$fd"
    return
  fi
  return 2
}

acquire_lock() {
  local lock="$LOCKDIR"
  local held flock_status=0
  if [[ -L "$lock" ]]; then
    echo "update lock is a symlink: $lock" >&2
    exit 13
  fi
  if [[ -e "$lock" && ! -d "$lock" ]]; then
    echo "update lock is malformed: $lock" >&2
    exit 13
  fi
  if [[ ! -d "$lock" ]]; then
    mkdir -m 700 -- "$lock" 2>/dev/null || true
  fi
  if [[ -L "$lock" || ! -d "$lock" ]]; then
    echo "update lock is malformed: $lock" >&2
    exit 13
  fi
  held="${lock}/held"
  if [[ -L "$held" ]]; then
    echo "update lock holder is a symlink: $held" >&2
    exit 13
  fi
  if [[ -e "$held" && ! -f "$held" ]]; then
    echo "update lock holder is malformed: $held" >&2
    exit 13
  fi
  close_update_lock_fd
  open_update_held_fd "$held" || {
    echo "cannot open update lock: $held" >&2
    exit 13
  }
  if [[ -L "$held" ]]; then
    close_update_lock_fd
    echo "update lock holder is a symlink: $held" >&2
    exit 13
  fi
  chmod 600 "$held" 2>/dev/null || true
  flock_update_lock_nb "$UPDATE_LOCK_FD" || flock_status=$?
  if ((flock_status != 0)); then
    close_update_lock_fd
    if ((flock_status == 2)); then
      echo "this host cannot take an update lock (need flock, perl, or python3)" >&2
      exit 13
    fi
    echo "another update transaction holds the destination lock" >&2
    exit 13
  fi
  atomic_write "$lock/owner.json" "$(printf '{"pid":"%s","start":"%s","transaction":"%s","schema":"io.vetcoders.vibecrafted.app-update-lock.v1"}' \
    "$$" "$(escape_json "$(process_lstart "$$")")" "$(escape_json "$TRANSACTION")")"
}

# Called by the EXIT trap installed before acquire_lock. ShellCheck 0.11.0
# models this script's final `exit 0` as an edge that skips EXIT handlers,
# so a trap-only function reads as uncalled (SC2329). The trap is the call;
# the descriptor must stay open until the transaction ends.
# shellcheck disable=SC2329
release_lock() {
  # Close only this process's descriptor. Never unlink held or the lock dir:
  # removing the inode would let two recoverers flock two names.
  close_update_lock_fd
}

observe_tuple() {
  DEST_PRESENT=0
  PREPARED_PRESENT=0
  DISPLACED_PRESENT=0
  PRIOR_PRESENT=0
  FAILED_NEW_PRESENT=0
  DEST_IDENTITY=""
  PREPARED_IDENTITY=""
  DISPLACED_IDENTITY=""
  PRIOR_LIVE_IDENTITY=""
  if [[ -d "$DESTINATION" && ! -L "$DESTINATION" ]]; then
    DEST_PRESENT=1
    DEST_IDENTITY="$(app_identity_token "$DESTINATION" || true)"
  fi
  if [[ -d "$PREPARED" && ! -L "$PREPARED" ]]; then
    PREPARED_PRESENT=1
    PREPARED_IDENTITY="$(app_identity_token "$PREPARED" || true)"
  fi
  if [[ -d "$DISPLACED" && ! -L "$DISPLACED" ]]; then
    DISPLACED_PRESENT=1
    DISPLACED_IDENTITY="$(app_identity_token "$DISPLACED" || true)"
  fi
  if [[ -d "$PRIOR" && ! -L "$PRIOR" ]]; then
    PRIOR_PRESENT=1
    PRIOR_LIVE_IDENTITY="$(app_identity_token "$PRIOR" || true)"
  fi
  if [[ -d "$FAILED_NEW" && ! -L "$FAILED_NEW" ]]; then
    FAILED_NEW_PRESENT=1
  fi
}

observe_published_generation() {
  local runtime_home pointer receipt
  runtime_home="${VIBECRAFTED_RUNTIME_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted}"
  pointer="$runtime_home/active.json"
  receipt="$runtime_home/install-receipt.json"
  without_update_lock_fd /usr/bin/python3 - "$pointer" "$receipt" <<'PY'
import json, sys
pointer, receipt = sys.argv[1], sys.argv[2]
try:
    pointer_doc = json.load(open(pointer, encoding="utf-8"))
    receipt_doc = json.load(open(receipt, encoding="utf-8"))
except Exception:
    sys.exit(1)
if pointer_doc.get("schema") != "vibecrafted.active-runtime.v1":
    sys.exit(1)
if receipt_doc.get("schema") != "vibecrafted.runtime-install.v1":
    sys.exit(1)
version = pointer_doc.get("version") or ""
receipt_version = receipt_doc.get("version") or ""
if not version or not receipt_version or version != receipt_version:
    sys.exit(1)
if receipt_doc.get("install_phase") in {"preparing", "ancillary"}:
    sys.exit(1)
# Canonical installer pending publication: truthy flags plus any
# config_transaction key (even an empty object) is incomplete.
if "config_transaction" in receipt_doc:
    sys.exit(1)
for key in (
    "install_pending",
    "config_pending",
    "uninstall_pending",
    "config_conflicts",
):
    if receipt_doc.get(key):
        sys.exit(1)
print(version)
PY
}

pack_payload_in_dir() {
  local dir="$1"
  local packs
  [[ -d "$dir" && ! -L "$dir" ]] || return 1
  shopt -s nullglob
  packs=("$dir"/Vibecrafted_RuntimePack_*.tar.gz)
  shopt -u nullglob
  if ((${#packs[@]} != 1)); then
    return 1
  fi
  if [[ -L "${packs[0]}" || ! -f "${packs[0]}" ]]; then
    return 1
  fi
  if [[ ! -f "${packs[0]}.sha256" || -L "${packs[0]}.sha256" ]]; then
    return 1
  fi
  if [[ ! -f "${packs[0]}.sig" || -L "${packs[0]}.sig" ]]; then
    return 1
  fi
  printf '%s' "${packs[0]}"
}

# The rollback payload has exactly one authority: the Runtime Pack sealed inside
# the verified prior.app. An owned copy beside the capture was a second source of
# truth — losing it refused a perfectly good signed bundle — so there is none.
# The installer only reads the pack plus its .sha256/.sig and stages elsewhere,
# so pointing --pack into the sealed bundle never mutates the signature.
locate_prior_pack() {
  local app="$1"
  local dir found
  dir="${app}/Contents/Resources/runtime-pack"
  if [[ ! -d "$dir" || -L "$dir" ]]; then
    echo "owned prior.app is missing historical Runtime Pack rollback data: $dir" >&2
    return 1
  fi
  if found="$(pack_payload_in_dir "$dir")"; then
    printf '%s' "$found"
    return 0
  fi
  echo "owned prior.app does not contain exactly one historical Runtime Pack" >&2
  return 1
}

# Derive pack + generation from the signed bundle. Never reads them back from the
# journal: a recorded path is a cache, not an authority.
resolve_prior_runtime() {
  local app="$1"
  local pack generation
  [[ -d "$app" && ! -L "$app" ]] || return 1
  if [[ -n "$PRIOR_PACK" && -n "$PRIOR_GENERATION" ]]; then
    return 0
  fi
  pack="$(locate_prior_pack "$app")" || return 1
  generation="$(prior_pack_generation "$pack")" || return 1
  [[ -n "$generation" ]] || return 1
  PRIOR_PACK="$pack"
  PRIOR_GENERATION="$generation"
  return 0
}

# Resume/observation sites only ever ask "is the published generation the prior
# one?". A derivation that fails answers "not confirmed", never "confirmed".
published_is_prior_generation() {
  local published="$1"
  [[ -n "$published" ]] || return 1
  resolve_prior_runtime "$SOURCE" || return 1
  [[ "$published" == "$PRIOR_GENERATION" ]]
}

prior_pack_generation() {
  local pack="$1"
  local member
  member="$(without_update_lock_fd /usr/bin/tar -tzf "$pack" | /usr/bin/grep -E '(^|/)VERSION$' | /usr/bin/head -n 1)" || true
  if [[ -z "$member" ]]; then
    return 1
  fi
  without_update_lock_fd /usr/bin/tar -xOf "$pack" "$member" | tr -d '[:space:]'
}

installer_admits_allow_older() {
  local path="$1"
  [[ -x "$path" && -f "$path" ]] || return 1
  grep -Fq -- '--allow-older-runtime' "$path"
}

resolve_pack_installer() {
  local sibling candidate
  # Recover must call a wrapper that actually admits --allow-older-runtime.
  # Historical 79001 wrappers may lack that flag; prefer the running helper's
  # sibling, then a bundled wrapper that greps as supported. Never walk a
  # source checkout.
  sibling="$(cd "$(dirname "$0")" && pwd)/install-runtime-pack.sh"
  if installer_admits_allow_older "$sibling"; then
    printf '%s' "$sibling"
    return 0
  fi
  for candidate in \
    "${DESTINATION:-}/Contents/Resources/runtime-pack/install-runtime-pack.sh" \
    "${DESTINATION:-}/Contents/Helpers/install-runtime-pack.sh" \
    "${DESTINATION:-}/Contents/Resources/runtime/scripts/install-runtime-pack.sh" \
    "${SOURCE:-}/Contents/Resources/runtime-pack/install-runtime-pack.sh" \
    "${SOURCE:-}/Contents/Helpers/install-runtime-pack.sh" \
    "${SOURCE:-}/Contents/Resources/runtime/scripts/install-runtime-pack.sh"
  do
    [[ -n "$candidate" && "$candidate" != "/Contents/"* ]] || continue
    if installer_admits_allow_older "$candidate"; then
      printf '%s' "$(cd "$(dirname "$candidate")" && pwd)/$(basename "$candidate")"
      return 0
    fi
  done
  echo "Runtime Pack installer owner is missing or does not admit --allow-older-runtime; cannot recover the previous runtime" >&2
  return 1
}

require_recovered_tuple() {
  local label="$1"
  local published
  require_exact_identity "$DESTINATION" "$PRIOR_IDENTITY" "$label"
  published="$(observe_published_generation || true)"
  if ! published_is_prior_generation "$published"; then
    echo "recover resume refused app-only success; installer generation ${published:-unresolved} is not prior ${PRIOR_GENERATION:-unresolved}" >&2
    exit 19
  fi
}

emit_success_and_exit() {
  local detail
  detail="$(terminal_detail)"
  write_journal "adopted" "$detail"
  write_terminal_receipt "$detail" "true"
  fail_after_if "receipt"
  relaunch_destination || true
  exit 0
}

finish_replace_adopt() {
  if [[ "$PREPARED_PRESENT" -eq 1 ]]; then
    require_exact_identity "$PREPARED" "$SOURCE_IDENTITY" "prepared candidate"
    write_journal "adopting" "resume prepared rename"
    if ! without_update_lock_fd /bin/mv "$PREPARED" "$DESTINATION"; then
      echo "resume could not adopt the prepared app" >&2
      write_journal "failed" "resume could not adopt prepared"
      if [[ "$DISPLACED_PRESENT" -eq 1 ]]; then
        require_exact_identity "$DISPLACED" "$PRIOR_IDENTITY" "displaced prior"
        if ! without_update_lock_fd /bin/mv "$DISPLACED" "$DESTINATION"; then
          echo "resume could not restore the displaced app" >&2
          exit 11
        fi
        write_journal "rolled_back" "restored displaced after failed resume adopt"
        write_admission "rejected" "resume restored the previous destination"
        exit 11
      fi
      exit 11
    fi
    fail_after_if "adopting"
  fi
  require_exact_identity "$DESTINATION" "$SOURCE_IDENTITY" "adopted destination"
  emit_success_and_exit
}

restore_displaced_prior() {
  require_exact_identity "$DISPLACED" "$PRIOR_IDENTITY" "displaced prior"
  if ! without_update_lock_fd /bin/mv "$DISPLACED" "$DESTINATION"; then
    echo "resume could not restore the displaced app" >&2
    write_journal "failed" "resume restore of displaced failed"
    exit 11
  fi
  write_journal "rolled_back" "restored displaced after interruption"
  write_admission "rejected" "destination restored after interruption"
  exit 16
}

finish_restore_adopt() {
  if [[ "$PREPARED_PRESENT" -eq 1 ]]; then
    require_exact_identity "$PREPARED" "$PRIOR_IDENTITY" "prepared previous app"
    write_journal "adopting" "resume restore prepared rename"
    if ! without_update_lock_fd /bin/mv "$PREPARED" "$DESTINATION"; then
      echo "resume could not adopt the previous app" >&2
      write_journal "failed" "resume restore adopt failed"
      if [[ "$FAILED_NEW_PRESENT" -eq 1 ]] && verify_signed_app "$FAILED_NEW"; then
        without_update_lock_fd /bin/mv "$FAILED_NEW" "$DESTINATION" || true
      fi
      exit 11
    fi
    fail_after_if "adopting"
  fi
  require_exact_identity "$DESTINATION" "$PRIOR_IDENTITY" "restored destination"
  if [[ ! -d "$SOURCE" ]]; then
    echo "owned prior.app was destroyed during restore resume" >&2
    exit 15
  fi
  require_exact_identity "$SOURCE" "$PRIOR_IDENTITY" "owned prior.app"
  emit_success_and_exit
}

# Mode-aware reconciliation for every write-ahead phase and the observed
# source/dest/prepared/displaced/failed-new tuple. Every terminal here defers to
# terminal_detail(), so a resumed run can never contradict the mode it resumed.
# Recover finishes runtime first; app-only resume is not whole-tuple success.
reconcile_resume() {
  local phase="$1"
  observe_tuple
  case "$MODE" in
    recover)
      case "$phase" in
        runtime_recovering|runtime_restored)
          if [[ "$DEST_PRESENT" -eq 1 && -n "$PRIOR_IDENTITY" && "$DEST_IDENTITY" == "$PRIOR_IDENTITY" ]]; then
            local published
            published="$(observe_published_generation || true)"
            if published_is_prior_generation "$published"; then
              emit_success_and_exit
            fi
          fi
          return 0
          ;;
        receipt_written|adopted|relaunched|relaunching)
          if [[ "$DEST_PRESENT" -eq 1 ]]; then
            require_recovered_tuple "recovered destination"
            if [[ ! -f "$RECEIPT" ]]; then
              write_terminal_receipt "$(terminal_detail)" "true"
            fi
            relaunch_destination || true
            exit 0
          fi
          echo "recover resume is missing the restored destination" >&2
          exit 14
          ;;
        displacing|displaced|adopting|prepared|preparing)
          if [[ "$DEST_PRESENT" -eq 1 && "$DEST_IDENTITY" == "$PRIOR_IDENTITY" ]]; then
            finish_restore_adopt
          fi
          if [[ "$DEST_PRESENT" -eq 0 && "$PREPARED_PRESENT" -eq 1 ]]; then
            finish_restore_adopt
          fi
          if [[ "$DEST_PRESENT" -eq 0 && "$FAILED_NEW_PRESENT" -eq 1 && "$PRIOR_PRESENT" -eq 1 ]]; then
            require_exact_identity "$SOURCE" "$PRIOR_IDENTITY" "owned prior.app"
            return 0
          fi
          if [[ "$DEST_PRESENT" -eq 1 && "$PRIOR_PRESENT" -eq 1 && "$DEST_IDENTITY" != "$PRIOR_IDENTITY" ]]; then
            require_exact_identity "$SOURCE" "$PRIOR_IDENTITY" "owned prior.app"
            return 0
          fi
          echo "recover resume could not reconcile phase ${phase} with the observed tuple" >&2
          exit 14
          ;;
        preflight|ready|waiting_parent|failed)
          if [[ "$DEST_PRESENT" -eq 1 && "$DEST_IDENTITY" == "$PRIOR_IDENTITY" ]]; then
            local published
            published="$(observe_published_generation || true)"
            if published_is_prior_generation "$published"; then
              emit_success_and_exit
            fi
          fi
          return 0
          ;;
        *)
          echo "recover resume does not recognize phase ${phase}" >&2
          exit 14
          ;;
      esac
      ;;
    restore)
      case "$phase" in
        receipt_written|adopted|relaunched|relaunching)
          if [[ "$DEST_PRESENT" -eq 1 ]]; then
            require_exact_identity "$DESTINATION" "$PRIOR_IDENTITY" "restored destination"
            if [[ ! -f "$RECEIPT" ]]; then
              write_terminal_receipt "$(terminal_detail)" "true"
            fi
            relaunch_destination || true
            exit 0
          fi
          echo "restore resume is missing the restored destination" >&2
          exit 14
          ;;
        displacing|displaced|adopting|prepared|preparing)
          if [[ "$DEST_PRESENT" -eq 1 && "$DEST_IDENTITY" == "$PRIOR_IDENTITY" ]]; then
            finish_restore_adopt
          fi
          if [[ "$DEST_PRESENT" -eq 0 && "$PREPARED_PRESENT" -eq 1 ]]; then
            finish_restore_adopt
          fi
          if [[ "$DEST_PRESENT" -eq 0 && "$FAILED_NEW_PRESENT" -eq 1 && "$PRIOR_PRESENT" -eq 1 ]]; then
            require_exact_identity "$SOURCE" "$PRIOR_IDENTITY" "owned prior.app"
            return 0
          fi
          # Write-ahead displacing is journaled before the quarantine rename.
          # Destination still holds the failed new app; prior.app is intact.
          if [[ "$DEST_PRESENT" -eq 1 && "$PRIOR_PRESENT" -eq 1 && "$DEST_IDENTITY" != "$PRIOR_IDENTITY" ]]; then
            require_exact_identity "$SOURCE" "$PRIOR_IDENTITY" "owned prior.app"
            return 0
          fi
          echo "restore resume could not reconcile phase ${phase} with the observed tuple" >&2
          exit 14
          ;;
        preflight|ready|waiting_parent|failed)
          if [[ "$DEST_PRESENT" -eq 1 ]]; then
            write_journal "failed" "restore resume aborted before destination displacement"
            write_admission "rejected" "destination is still installed"
            exit 17
          fi
          return 0
          ;;
        *)
          echo "restore resume does not recognize phase ${phase}" >&2
          exit 14
          ;;
      esac
      ;;
    replace)
      case "$phase" in
        receipt_written|adopted|relaunched|relaunching)
          if [[ "$DEST_PRESENT" -eq 1 ]]; then
            require_exact_identity "$DESTINATION" "$SOURCE_IDENTITY" "replaced destination"
            if [[ ! -f "$RECEIPT" ]]; then
              write_terminal_receipt "$(terminal_detail)" "true"
            fi
            relaunch_destination || true
            exit 0
          fi
          echo "replace resume is missing the adopted destination" >&2
          exit 14
          ;;
        displacing|displaced|adopting)
          if [[ "$DEST_PRESENT" -eq 1 && "$DEST_IDENTITY" == "$SOURCE_IDENTITY" ]]; then
            finish_replace_adopt
          fi
          if [[ "$DEST_PRESENT" -eq 0 && "$PREPARED_PRESENT" -eq 1 && "$DISPLACED_PRESENT" -eq 1 ]]; then
            finish_replace_adopt
          fi
          if [[ "$DEST_PRESENT" -eq 0 && "$PREPARED_PRESENT" -eq 0 && "$DISPLACED_PRESENT" -eq 1 ]]; then
            restore_displaced_prior
          fi
          if [[ "$DEST_PRESENT" -eq 1 && "$DISPLACED_PRESENT" -eq 0 ]]; then
            write_journal "failed" "resume aborted; destination was never displaced"
            write_admission "rejected" "previous destination is still installed"
            exit 17
          fi
          echo "replace resume could not reconcile phase ${phase} with the observed tuple" >&2
          exit 14
          ;;
        capturing|captured|preparing|prepared|preflight|ready|waiting_parent)
          if [[ "$DEST_PRESENT" -eq 1 ]]; then
            if owned_prepared "$PREPARED" && [[ -e "$PREPARED" ]]; then
              remove_owned_prepared "$PREPARED" || true
            fi
            write_journal "failed" "resume aborted before destination displacement"
            write_admission "rejected" "previous destination is still installed"
            exit 17
          fi
          echo "replace resume found destination missing before displacement" >&2
          exit 14
          ;;
        *)
          echo "replace resume does not recognize phase ${phase}" >&2
          exit 14
          ;;
      esac
      ;;
  esac
}

bind_resume_journal() {
  local journal_schema journal_mode journal_operation journal_txn journal_dest journal_parent journal_source
  journal_schema="$(journal_require schema)"
  if [[ "$journal_schema" != "io.vetcoders.vibecrafted.app-update-journal.v1" ]]; then
    echo "resume journal schema is not app-update-journal.v1" >&2
    exit 14
  fi
  journal_txn="$(journal_require transaction)"
  journal_mode="$(journal_require mode)"
  journal_operation="$(journal_require operation)"
  journal_dest="$(journal_require destination)"
  journal_parent="$(journal_require parent)"
  journal_source="$(journal_require source)"
  if [[ -n "$TRANSACTION" && "$TRANSACTION" != "$journal_txn" ]]; then
    echo "resume journal transaction does not match" >&2
    exit 14
  fi
  TRANSACTION="$journal_txn"
  validate_transaction_id "$TRANSACTION"
  if [[ "$journal_mode" != "$MODE" || "$journal_operation" != "$MODE" ]]; then
    echo "resume journal operation ${journal_operation} does not match mode ${MODE}" >&2
    exit 14
  fi
  if [[ "$journal_dest" != "$DESTINATION" ]]; then
    echo "resume journal destination does not match" >&2
    exit 14
  fi
  if [[ "$journal_parent" != "$parent" ]]; then
    echo "resume journal parent does not match" >&2
    exit 14
  fi
  if [[ "$journal_source" != "$SOURCE" ]]; then
    echo "resume journal source does not match" >&2
    exit 14
  fi
  SOURCE_IDENTITY="$(journal_require source_identity)"
  local candidate
  candidate="$(journal_require candidate_identity)"
  if [[ "$candidate" != "$SOURCE_IDENTITY" ]]; then
    echo "resume journal candidate identity does not match source identity" >&2
    exit 14
  fi
  CAPTURE="$(journal_require capture)"
  PREPARED="$(journal_require prepared)"
  DISPLACED="$(journal_require displaced)"
  PRIOR="${CAPTURE}/prior.app"
  FAILED_NEW="${CAPTURE}/failed-new.app"
  if ! owned_capture_root "$CAPTURE"; then
    echo "resume capture path is not owned by this transaction" >&2
    exit 14
  fi
  if ! owned_prepared "$PREPARED"; then
    echo "resume prepared path is not owned by this transaction" >&2
    exit 14
  fi
  if ! owned_displaced "$DISPLACED"; then
    echo "resume displaced path is not owned by this transaction" >&2
    exit 14
  fi
  PRIOR_IDENTITY="$(journal_get prior_identity || true)"
  local phase
  phase="$(journal_require phase)"
  case "$phase" in
    captured|preparing|prepared|displacing|displaced|adopting|adopted|receipt_written|relaunching|relaunched|runtime_recovering|runtime_restored)
      if [[ -z "$PRIOR_IDENTITY" ]]; then
        echo "resume journal is missing the original prior content identity" >&2
        exit 14
      fi
      ;;
  esac
  if [[ "$MODE" == "restore" || "$MODE" == "recover" ]] && [[ -z "$PRIOR_IDENTITY" ]]; then
    echo "${MODE} resume is missing the original preserved capture identity" >&2
    exit 14
  fi
  INSTALLER_STATUS="$(journal_get installer_status || true)"
  if [[ -d "$SOURCE" ]]; then
    if [[ "$MODE" == "restore" || "$MODE" == "recover" ]]; then
      require_exact_identity "$SOURCE" "$PRIOR_IDENTITY" "${MODE} source"
    else
      require_exact_identity "$SOURCE" "$SOURCE_IDENTITY" "candidate source"
    fi
  fi
  reconcile_resume "$phase"
}

restore_prior_runtime() {
  local installer published terminal_host frame_helper public_key
  installer="$(resolve_pack_installer)" || {
    write_journal "failed" "Runtime Pack installer owner is missing"
    write_admission "rejected" "historical rollback cannot find the installer owner"
    exit 19
  }
  PRIOR_PACK="$(locate_prior_pack "$SOURCE")" || {
    write_journal "failed" "historical Runtime Pack rollback data is missing from owned prior.app"
    write_admission "rejected" "historical Runtime Pack rollback data is missing"
    echo "missing historical rollback data: owned prior.app has no recoverable Runtime Pack" >&2
    exit 19
  }
  PRIOR_GENERATION="$(prior_pack_generation "$PRIOR_PACK")" || {
    write_journal "failed" "historical Runtime Pack has no VERSION identity"
    write_admission "rejected" "historical Runtime Pack has no VERSION identity"
    echo "missing historical rollback data: prior Runtime Pack has no VERSION" >&2
    exit 19
  }
  published="$(observe_published_generation || true)"
  if [[ "$published" == "$PRIOR_GENERATION" ]]; then
    INSTALLER_STATUS="0"
    write_journal "runtime_restored" "installer already published prior generation ${PRIOR_GENERATION}"
    return 0
  fi
  terminal_host="${SOURCE}/Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty"
  frame_helper="${SOURCE}/Contents/Helpers/vc-frame"
  if [[ ! -x "$terminal_host" || ! -x "$frame_helper" ]]; then
    write_journal "failed" "owned prior.app is missing matching helper binaries"
    write_admission "rejected" "owned prior.app is missing matching helper binaries"
    echo "missing historical rollback data: prior.app helpers are not executable" >&2
    exit 19
  fi
  write_journal "runtime_recovering" "restoring prior runtime ${PRIOR_GENERATION} through the installer owner"
  fail_after_if "runtime_recovering"
  public_key=""
  if [[ -n "${VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY:-}" && -f "$VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY" ]]; then
    public_key="$VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY"
  elif [[ -f "$(dirname "$installer")/vibecrafted-signing-v1.pub" ]]; then
    public_key="$(dirname "$installer")/vibecrafted-signing-v1.pub"
  fi
  if [[ -n "$public_key" ]]; then
    export VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY="$public_key"
  fi
  if [[ -z "${VIBECRAFTED_SOURCE_INSTALLER:-}" ]]; then
    local helper_dir source_installer
    helper_dir="$(cd "$(dirname "$0")" && pwd)"
    for source_installer in \
      "$SOURCE/Contents/Resources/runtime/scripts/vetcoders_install.py" \
      "$helper_dir/vetcoders_install.py"
    do
      if [[ -f "$source_installer" ]] && grep -Fq -- '--allow-older-runtime' "$source_installer"; then
        export VIBECRAFTED_SOURCE_INSTALLER="$source_installer"
        break
      fi
    done
  fi
  local live_root="$SOURCE"
  if [[ -d "$DESTINATION" ]]; then
    live_root="$DESTINATION"
  fi
  set +e
  without_update_lock_fd /bin/bash "$installer" \
    --pack "$PRIOR_PACK" \
    --app-root "$live_root" \
    --terminal-host "$terminal_host" \
    --frame-helper "$frame_helper" \
    --allow-older-runtime
  INSTALLER_STATUS="$?"
  set -e
  if [[ "$INSTALLER_STATUS" != "0" ]]; then
    write_journal "failed" "installer did not restore prior runtime (exit ${INSTALLER_STATUS})"
    write_admission "rejected" "installer did not restore prior runtime"
    echo "whole-tuple recovery failed: installer exited ${INSTALLER_STATUS} before the previous app was touched" >&2
    exit "$INSTALLER_STATUS"
  fi
  published="$(observe_published_generation || true)"
  if [[ "$published" != "$PRIOR_GENERATION" ]]; then
    write_journal "failed" "installer left generation ${published:-unresolved}; prior ${PRIOR_GENERATION} was not selected"
    write_admission "rejected" "installer did not select the prior generation"
    echo "whole-tuple recovery failed: candidate runtime remains selected after installer; previous app was not restored" >&2
    exit 19
  fi
  write_journal "runtime_restored" "installer published prior generation ${PRIOR_GENERATION}"
}

# Whole-tuple recovery: restore prior runtime/config/launchers through the
# existing installer owner, then the matching prior app. App-only restore is
# not success while the candidate runtime remains selected.
recover_whole_tuple() {
  if [[ ! -d "$SOURCE" ]]; then
    echo "recover source missing: $SOURCE" >&2
    exit 3
  fi
  local source_abs
  source_abs="$(physical_existing_dir "$SOURCE")"
  if [[ "$(basename "$source_abs")" != "prior.app" ]] || ! owned_capture_root "$(dirname "$source_abs")"; then
    echo "recover source is not this transaction's owned prior.app" >&2
    exit 15
  fi
  SOURCE="$source_abs"
  CAPTURE="$(dirname "$source_abs")"
  PRIOR="$source_abs"
  FAILED_NEW="${CAPTURE}/failed-new.app"
  if [[ -z "$PRIOR_IDENTITY" ]]; then
    echo "recover is missing the original preserved capture identity" >&2
    exit 14
  fi
  require_exact_identity "$SOURCE" "$PRIOR_IDENTITY" "owned prior.app"
  SOURCE_IDENTITY="$PRIOR_IDENTITY"
  write_journal "preflight" "owned prior.app accepted; runtime will be restored before the app"
  fail_after_if "preflight"
  write_admission "ready" "recover helper preflight complete"
  write_journal "ready" "recover admission persisted"
  fail_after_if "ready"
  hold_if "ready" || exit 18

  if [[ -n "$WAIT_PID" ]]; then
    write_journal "waiting_parent" "bound parent still live"
    if ! wait_for_identity "$WAIT_PID" "$WAIT_START" "$WAIT_TIMEOUT"; then
      write_journal "failed" "parent identity still live"
      write_admission "rejected" "parent identity still live"
      exit 5
    fi
  fi
  require_exact_identity "$SOURCE" "$PRIOR_IDENTITY" "owned prior.app"
  local phase published
  phase="$(journal_get phase || true)"
  if [[ "$phase" != "runtime_restored" ]]; then
    restore_prior_runtime
  fi
  fail_after_if "runtime_restored"
  hold_if "runtime_restored" || exit 18
  published="$(observe_published_generation || true)"
  if ! published_is_prior_generation "$published"; then
    write_journal "failed" "refusing app-only restore while installer generation is ${published:-unresolved}"
    write_admission "rejected" "candidate runtime remains selected"
    echo "whole-tuple recovery refused app-only restore; installer generation ${published:-unresolved} is not prior ${PRIOR_GENERATION:-unresolved}" >&2
    exit 19
  fi
  restore_previous_tuple
}

# Restore must never ditto the failed destination onto prior.app. The source
# is the owned capture; the failed new bundle is quarantined beside it.
# PRIOR_IDENTITY is the original preserved capture identity from the replace
# journal, never a live hash of whatever is currently at source.
#
# recover_whole_tuple() finishes here as well, once the prior Runtime Pack is
# published. That makes this the shared epilogue of two modes, so both terminals
# below read terminal_detail() instead of naming a detail: the same code answers
# restored under --mode restore and recovered under --mode recover.
restore_previous_tuple() {
  if [[ ! -d "$SOURCE" ]]; then
    echo "restore source missing: $SOURCE" >&2
    exit 3
  fi
  local source_abs
  source_abs="$(physical_existing_dir "$SOURCE")"
  if [[ "$(basename "$source_abs")" != "prior.app" ]] || ! owned_capture_root "$(dirname "$source_abs")"; then
    echo "restore source is not this transaction's owned prior.app" >&2
    exit 15
  fi
  CAPTURE="$(dirname "$source_abs")"
  PRIOR="$source_abs"
  FAILED_NEW="${CAPTURE}/failed-new.app"
  if [[ -z "$PRIOR_IDENTITY" ]]; then
    echo "restore is missing the original preserved capture identity" >&2
    exit 14
  fi
  require_exact_identity "$SOURCE" "$PRIOR_IDENTITY" "owned prior.app"
  SOURCE_IDENTITY="$PRIOR_IDENTITY"
  write_journal "preflight" "owned prior.app accepted; destination will not overwrite it"
  fail_after_if "preflight"
  write_admission "ready" "restore helper preflight complete"
  write_journal "ready" "restore admission persisted"
  fail_after_if "ready"
  hold_if "ready" || exit 18

  if [[ -n "$WAIT_PID" ]]; then
    write_journal "waiting_parent" "bound parent still live"
    if ! wait_for_identity "$WAIT_PID" "$WAIT_START" "$WAIT_TIMEOUT"; then
      write_journal "failed" "parent identity still live"
      write_admission "rejected" "parent identity still live"
      exit 5
    fi
  fi
  require_exact_identity "$SOURCE" "$PRIOR_IDENTITY" "owned prior.app"

  if [[ -d "$DESTINATION" ]]; then
    local dest_abs
    dest_abs="$(physical_existing_dir "$DESTINATION")"
    if [[ "$dest_abs" == "$source_abs" ]]; then
      write_terminal_receipt "$(terminal_detail)" "true"
      relaunch_destination || true
      exit 0
    fi
    if [[ -e "$FAILED_NEW" ]]; then
      if ! owned_failed_new "$FAILED_NEW"; then
        echo "refusing to replace a path this restore does not own: $FAILED_NEW" >&2
        exit 7
      fi
      without_update_lock_fd /bin/rm -rf "$FAILED_NEW"
    fi
    write_journal "displacing" "quarantine failed destination; prior.app stays untouched"
    if ! without_update_lock_fd /bin/mv "$DESTINATION" "$FAILED_NEW"; then
      echo "could not quarantine the failed destination" >&2
      write_journal "failed" "restore quarantine failed"
      exit 10
    fi
    fail_after_if "displacing"
    write_journal "displaced" "destination absent; prior.app and failed-new present"
    fail_after_if "displaced"
    hold_if "displaced" || exit 18
  fi

  write_journal "preparing" "copy owned prior.app without mutating the capture"
  if owned_prepared "$PREPARED" && [[ -e "$PREPARED" ]]; then
    remove_owned_prepared "$PREPARED" || true
  fi
  if ! without_update_lock_fd /usr/bin/ditto "$SOURCE" "$PREPARED"; then
    echo "prepare of the previous app failed" >&2
    write_journal "failed" "restore prepare failed"
    exit 8
  fi
  require_exact_identity "$PREPARED" "$PRIOR_IDENTITY" "prepared previous app"
  write_journal "prepared" "previous app prepared"
  fail_after_if "prepared"
  write_journal "adopting" "restore prepared rename"
  if ! without_update_lock_fd /bin/mv "$PREPARED" "$DESTINATION"; then
    echo "could not adopt the previous app" >&2
    write_journal "failed" "restore adopt failed"
    if [[ -d "$FAILED_NEW" ]] && verify_signed_app "$FAILED_NEW"; then
      without_update_lock_fd /bin/mv "$FAILED_NEW" "$DESTINATION" || true
    fi
    exit 11
  fi
  fail_after_if "adopting"
  require_exact_identity "$DESTINATION" "$PRIOR_IDENTITY" "restored destination"
  if [[ ! -d "$SOURCE" ]]; then
    echo "owned prior.app was destroyed during restore" >&2
    write_journal "failed" "prior.app missing after restore"
    exit 15
  fi
  require_exact_identity "$SOURCE" "$PRIOR_IDENTITY" "owned prior.app"
  write_journal "adopted" "previous app restored; capture retained"
  fail_after_if "adopted"
  write_terminal_receipt "$(terminal_detail)" "true"
  fail_after_if "receipt"
  relaunch_destination || true
  exit 0
}

validate_transaction_id "$TRANSACTION"
if [[ "$RESUME" -eq 1 && -z "$JOURNAL" ]]; then
  JOURNAL="${RECEIPT}.journal.json"
fi

[[ -e "$SOURCE" ]] || { echo "source app missing: $SOURCE" >&2; exit 3; }
SOURCE="$(physical_existing_dir "$SOURCE")"
DESTINATION="$(canonical_leaf_path "$DESTINATION")"
RECEIPT="$(canonical_leaf_path "$RECEIPT")"
if [[ -n "$ADMISSION" ]]; then
  ADMISSION="$(canonical_leaf_path "$ADMISSION")"
fi
if [[ -n "$JOURNAL" ]]; then
  JOURNAL="$(canonical_leaf_path "$JOURNAL")"
fi
if [[ -e "$DESTINATION" && ! -d "$DESTINATION" ]]; then
  echo "destination is not an application directory" >&2
  exit 6
fi

parent="$(physical_existing_dir "$(dirname "$DESTINATION")")"
if [[ -z "$ADMISSION" ]]; then
  ADMISSION="$(canonical_leaf_path "${RECEIPT}.admission.json")"
fi
if [[ -z "$JOURNAL" ]]; then
  JOURNAL="$(canonical_leaf_path "${RECEIPT}.journal.json")"
fi

if [[ "$RESUME" -eq 1 ]]; then
  if [[ ! -f "$JOURNAL" ]]; then
    echo "resume requires a complete immutable journal" >&2
    exit 14
  fi
elif [[ -z "$TRANSACTION" ]]; then
  TRANSACTION="$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
fi
validate_transaction_id "$TRANSACTION"

CAPTURE="${parent}/.vc-update-capture-${TRANSACTION}"
PREPARED="${parent}/.vc-update-prepared-${TRANSACTION}.app"
DISPLACED="${CAPTURE}/displaced.app"
PRIOR="${CAPTURE}/prior.app"
FAILED_NEW="${CAPTURE}/failed-new.app"
LOCKDIR="${parent}/.vc-update.lock"
SOURCE_IDENTITY=""
PRIOR_IDENTITY=""

if [[ "$RESUME" -ne 1 && -f "$JOURNAL" ]]; then
  # Fresh restore/recover must compare the original preserved capture identity.
  # A leftover replace journal is the authority; do not hash whatever is live.
  if [[ "$MODE" == "restore" || "$MODE" == "recover" ]]; then
    PRIOR_IDENTITY="$(journal_get prior_identity || true)"
    SOURCE_IDENTITY="$(journal_get source_identity || true)"
    TRANSACTION="$(journal_get transaction || printf '%s' "$TRANSACTION")"
    validate_transaction_id "$TRANSACTION"
    CAPTURE="${parent}/.vc-update-capture-${TRANSACTION}"
    PREPARED="${parent}/.vc-update-prepared-${TRANSACTION}.app"
    DISPLACED="${CAPTURE}/displaced.app"
    PRIOR="${CAPTURE}/prior.app"
    FAILED_NEW="${CAPTURE}/failed-new.app"
  fi
fi

trap 'release_lock' EXIT
trap 'exit 143' TERM INT
acquire_lock

if [[ "$RESUME" -eq 1 ]]; then
  bind_resume_journal
fi

if [[ "$MODE" == "recover" ]]; then
  recover_whole_tuple
fi

if [[ "$MODE" == "restore" ]]; then
  restore_previous_tuple
fi

SOURCE_IDENTITY="$(app_identity_token "$SOURCE")" || {
  reject_preflight "source has no durable content identity" 9
}
if ! verify_signed_app "$SOURCE"; then
  reject_preflight "source failed signed identity check at the mutation boundary" 9
fi

mkdir -p "$CAPTURE"
if ! owned_capture_root "$CAPTURE"; then
  reject_preflight "capture path is not owned by this transaction" 7
fi

write_journal "preflight" "source and destination accepted"
fail_after_if "preflight"
write_admission "ready" "helper preflight complete"
write_journal "ready" "admission persisted"
fail_after_if "ready"
hold_if "ready" || exit 18

if [[ -n "$WAIT_PID" ]]; then
  write_journal "waiting_parent" "bound parent still live"
  if ! wait_for_identity "$WAIT_PID" "$WAIT_START" "$WAIT_TIMEOUT"; then
    write_journal "failed" "parent identity still live"
    write_admission "rejected" "parent identity still live"
    exit 5
  fi
fi

if ! verify_signed_app "$SOURCE"; then
  reject_preflight "source failed signed identity re-check at the mutation boundary" 9
fi
require_exact_identity "$SOURCE" "$SOURCE_IDENTITY" "candidate source"

if [[ -e "$DESTINATION" ]]; then
  write_journal "capturing" "unique owned capture"
  if ! without_update_lock_fd /usr/bin/ditto "$DESTINATION" "$PRIOR"; then
    echo "unique capture of the previous app failed" >&2
    write_journal "failed" "capture failed"
    exit 7
  fi
  PRIOR_IDENTITY="$(app_identity_token "$PRIOR")" || {
    echo "captured prior app has no durable identity" >&2
    write_journal "failed" "capture identity missing"
    exit 7
  }
  if ! verify_signed_app "$PRIOR"; then
    echo "captured prior app failed signed identity check" >&2
    write_journal "failed" "capture failed identity"
    exit 7
  fi
  # The capture deliberately derives nothing about the pack here. prior_pack /
  # prior_generation stay empty until recover derives them from the sealed
  # bundle, so the journal can only ever record what a run actually used.
  write_journal "captured" "prior identity bound"
  fail_after_if "captured"
  hold_if "captured" || exit 18

  write_journal "preparing" "candidate copy"
  if ! without_update_lock_fd /usr/bin/ditto "$SOURCE" "$PREPARED"; then
    echo "prepare of the candidate app failed" >&2
    write_journal "failed" "prepare failed"
    if [[ -e "$PREPARED" ]]; then
      remove_owned_prepared "$PREPARED" || true
    fi
    exit 8
  fi
  require_exact_identity "$PREPARED" "$SOURCE_IDENTITY" "prepared candidate"
  write_journal "prepared" "prepared candidate verified"
  fail_after_if "prepared"
  hold_if "prepared" || exit 18

  write_journal "displacing" "destination move is journaled, not atomic"
  if ! without_update_lock_fd /bin/mv "$DESTINATION" "$DISPLACED"; then
    echo "could not displace the live destination" >&2
    write_journal "failed" "displace failed"
    remove_owned_prepared "$PREPARED" || true
    exit 10
  fi
  fail_after_if "displacing"
  write_journal "displaced" "destination absent; displaced and prepared present"
  fail_after_if "displaced"
  hold_if "displaced" || exit 18

  write_journal "adopting" "prepared rename"
  if ! without_update_lock_fd /bin/mv "$PREPARED" "$DESTINATION"; then
    echo "could not adopt the prepared app; restoring the verified displaced app" >&2
    write_journal "failed" "adopt failed"
    if [[ -d "$DISPLACED" ]]; then
      require_exact_identity "$DISPLACED" "$PRIOR_IDENTITY" "displaced prior"
      if ! without_update_lock_fd /bin/mv "$DISPLACED" "$DESTINATION"; then
        echo "verified restore of the displaced app failed" >&2
        exit 11
      fi
      write_journal "rolled_back" "restored displaced after failed adopt"
    fi
    exit 11
  fi
  fail_after_if "adopting"
  if ! verify_signed_app "$DESTINATION"; then
    echo "adopted destination failed signed identity check; restoring the verified previous app" >&2
    write_journal "failed" "adopted identity failed"
    if [[ -d "$DESTINATION" ]]; then
      without_update_lock_fd /bin/mv "$DESTINATION" "${CAPTURE}/failed-adopt.app"
    fi
    if [[ -d "$DISPLACED" ]]; then
      require_exact_identity "$DISPLACED" "$PRIOR_IDENTITY" "displaced prior"
      if ! without_update_lock_fd /bin/mv "$DISPLACED" "$DESTINATION"; then
        echo "verified restore of the displaced app failed" >&2
        exit 12
      fi
      write_journal "rolled_back" "restored displaced after invalid adopt"
    fi
    exit 12
  fi
  require_exact_identity "$DESTINATION" "$SOURCE_IDENTITY" "adopted destination"
  write_journal "adopted" "destination verified"
  fail_after_if "adopted"
else
  write_journal "preparing" "candidate copy"
  if ! without_update_lock_fd /usr/bin/ditto "$SOURCE" "$PREPARED"; then
    echo "prepare of the candidate app failed" >&2
    write_journal "failed" "prepare failed"
    if [[ -e "$PREPARED" ]]; then
      remove_owned_prepared "$PREPARED" || true
    fi
    exit 8
  fi
  require_exact_identity "$PREPARED" "$SOURCE_IDENTITY" "prepared candidate"
  write_journal "prepared" "prepared candidate verified"
  fail_after_if "prepared"
  write_journal "adopting" "prepared rename"
  if ! without_update_lock_fd /bin/mv "$PREPARED" "$DESTINATION"; then
    echo "could not adopt the prepared app" >&2
    write_journal "failed" "adopt failed"
    exit 11
  fi
  fail_after_if "adopting"
  if ! verify_signed_app "$DESTINATION"; then
    echo "adopted destination failed signed identity check" >&2
    write_journal "failed" "adopted identity failed"
    without_update_lock_fd /bin/mv "$DESTINATION" "${parent}/.vc-update-failed-${TRANSACTION}.app"
    exit 12
  fi
  require_exact_identity "$DESTINATION" "$SOURCE_IDENTITY" "adopted destination"
  write_journal "adopted" "destination verified"
  fail_after_if "adopted"
fi

# Capture is retained after a verified adopt so pack failure can restore the
# previous working tuple. Do not delete it here.
write_terminal_receipt "$(terminal_detail)" "true"
fail_after_if "receipt"
relaunch_destination || true
exit 0
