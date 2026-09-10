#!/bin/bash
# vc-app-update — sole UI-bundle mutation owner.
#
# This script is the only replacement implementation. The App launches it and
# does not reimplement wait/ditto/backup. It does not stop Frame, PTYs, workers,
# sessions, or rewrite Founder config, PATH, interpreters, or MCP.
#
# Contract:
#   1. Preflight (source/destination/capture/lock + signed identity).
#   2. Persist a correlated READY admission (transaction, identity, destination,
#      parent PID/start). Parent may quit only after this record.
#   3. Wait for the bound parent identity to disappear.
#   4. Journal every mutation phase before changing the destination.
#   5. Write the validated terminal replacement receipt BEFORE relaunch.
#   6. Restore uses the same owner, verifies capture, and never treats
#      `mv ... || true` as rollback.
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
ALLOW_UNSIGNED=0

HARNESS=0
if [[ "${VIBECRAFTED_UPDATE_HELPER_HARNESS:-}" == "1" ]]; then
  HARNESS=1
fi

usage() {
  echo "usage: vc-app-update --source APP --destination APP --receipt FILE [--admission FILE] [--journal FILE] [--transaction ID] [--mode replace|restore] [--wait-pid PID] [--wait-start LSTART] [--wait-timeout SECONDS] [--relaunch] [--resume] [--expected-identifier ID] [--expected-team TEAM]" >&2
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
      if [[ "$HARNESS" -ne 1 ]]; then
        echo "refusing --allow-unsigned without VIBECRAFTED_UPDATE_HELPER_HARNESS=1" >&2
        exit 2
      fi
      ALLOW_UNSIGNED=1
      shift
      ;;
    *) usage ;;
  esac
done

[[ -n "$SOURCE" && -n "$DESTINATION" && -n "$RECEIPT" ]] || usage
if [[ "$MODE" != "replace" && "$MODE" != "restore" ]]; then
  echo "mode must be replace or restore" >&2
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
  /bin/mv -f "$tmp" "$dest"
}

process_lstart() {
  local pid="$1"
  /bin/ps -p "$pid" -o lstart= 2>/dev/null | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
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
    sleep 0.05
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
      sleep 0.05
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

verify_signed_app() {
  local app="$1"
  if [[ "$ALLOW_UNSIGNED" -eq 1 ]]; then
    return 0
  fi
  if ! /usr/bin/codesign --verify --strict --verbose=4 "$app" >/dev/null 2>&1; then
    echo "codesign --verify --strict failed: $app" >&2
    return 1
  fi
  local display
  display="$(/usr/bin/codesign --display --verbose=4 "$app" 2>&1 || true)"
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
  /usr/bin/codesign --display --verbose=4 "$1" 2>&1 | /usr/bin/awk -F= '/^CDHash=/{print $2; exit}'
}

app_identity_token() {
  local app="$1"
  local hash
  hash="$(app_cdhash "$app")"
  if [[ -n "$hash" ]]; then
    printf 'cdhash:%s' "$hash"
    return 0
  fi
  if [[ "$ALLOW_UNSIGNED" -eq 1 && -d "$app" ]]; then
    /usr/bin/find "$app" -type f -print0 2>/dev/null \
      | /usr/bin/sort -z \
      | /usr/bin/xargs -0 /usr/sbin/shasum -a 256 2>/dev/null \
      | /usr/sbin/shasum -a 256 \
      | /usr/bin/awk '{print "sha256:"$1}'
    return 0
  fi
  return 1
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
    /bin/rm -rf "$path"
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
  /usr/bin/python3 - "$JOURNAL" "$key" <<'PY'
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

write_journal() {
  local phase="$1"
  local detail="${2:-}"
  atomic_write "$JOURNAL" "$(printf '{"schema":"io.vetcoders.vibecrafted.app-update-journal.v1","capture":"%s","destination":"%s","detail":"%s","displaced":"%s","identifier":"%s","mode":"%s","parent_pid":"%s","parent_start":"%s","phase":"%s","prepared":"%s","prior_identity":"%s","receipt":"%s","source":"%s","source_identity":"%s","team_id":"%s","transaction":"%s"}' \
    "$(escape_json "$CAPTURE")" \
    "$(escape_json "$DESTINATION")" \
    "$(escape_json "$detail")" \
    "$(escape_json "$DISPLACED")" \
    "$(escape_json "$EXPECTED_IDENTIFIER")" \
    "$(escape_json "$MODE")" \
    "$(escape_json "$WAIT_PID")" \
    "$(escape_json "$WAIT_START")" \
    "$(escape_json "$phase")" \
    "$(escape_json "$PREPARED")" \
    "$(escape_json "$PRIOR_IDENTITY")" \
    "$(escape_json "$RECEIPT")" \
    "$(escape_json "$SOURCE")" \
    "$(escape_json "$SOURCE_IDENTITY")" \
    "$(escape_json "$EXPECTED_TEAM")" \
    "$(escape_json "$TRANSACTION")")"
}

write_admission() {
  local status="$1"
  local detail="${2:-}"
  atomic_write "$ADMISSION" "$(printf '{"schema":"io.vetcoders.vibecrafted.app-replacement-admission.v1","capture":"%s","destination":"%s","detail":"%s","identifier":"%s","journal":"%s","mode":"%s","parent_pid":"%s","parent_start":"%s","receipt":"%s","source":"%s","source_identity":"%s","status":"%s","team_id":"%s","transaction":"%s"}' \
    "$(escape_json "$CAPTURE")" \
    "$(escape_json "$DESTINATION")" \
    "$(escape_json "$detail")" \
    "$(escape_json "$EXPECTED_IDENTIFIER")" \
    "$(escape_json "$JOURNAL")" \
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

write_terminal_receipt() {
  local detail="$1"
  local replaced="$2"
  atomic_write "$RECEIPT" "$(printf '{"schema":"io.vetcoders.vibecrafted.app-replacement.v1","capture":"%s","destination":"%s","detail":"%s","identifier":"%s","journal":"%s","mode":"%s","phase":"receipt_written","relaunched":false,"replaced":%s,"source_identity":"%s","team_id":"%s","transaction":"%s"}' \
    "$(escape_json "$CAPTURE")" \
    "$(escape_json "$DESTINATION")" \
    "$(escape_json "$detail")" \
    "$(escape_json "$EXPECTED_IDENTIFIER")" \
    "$(escape_json "$JOURNAL")" \
    "$(escape_json "$MODE")" \
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
  if ! "$OPEN_BIN" -n "$DESTINATION"; then
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

# Restore must never ditto the failed destination onto prior.app. The source
# is the owned capture; the failed new bundle is quarantined beside it.
restore_previous_tuple() {
  if [[ ! -d "$SOURCE" ]]; then
    echo "restore source missing: $SOURCE" >&2
    exit 3
  fi
  local source_abs capture_abs
  source_abs="$(cd "$SOURCE" && pwd)"
  if [[ "$(basename "$source_abs")" != "prior.app" ]] || ! owned_capture_root "$(dirname "$source_abs")"; then
    echo "restore source is not this transaction's owned prior.app" >&2
    exit 15
  fi
  CAPTURE="$(dirname "$source_abs")"
  PRIOR="$source_abs"
  FAILED_NEW="${CAPTURE}/failed-new.app"
  if ! SOURCE_IDENTITY="$(app_identity_token "$SOURCE")"; then
    SOURCE_IDENTITY=""
  fi
  PRIOR_IDENTITY="$SOURCE_IDENTITY"
  if ! verify_signed_app "$SOURCE"; then
    reject_preflight "restore source failed signed identity check" 9
  fi
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
  if ! verify_signed_app "$SOURCE"; then
    reject_preflight "restore source failed signed identity re-check" 9
  fi

  if [[ -d "$DESTINATION" ]]; then
    local dest_abs
    dest_abs="$(cd "$DESTINATION" && pwd)"
    if [[ "$dest_abs" == "$source_abs" ]]; then
      write_terminal_receipt "restored" "true"
      relaunch_destination || true
      exit 0
    fi
    if [[ -e "$FAILED_NEW" ]]; then
      if ! owned_failed_new "$FAILED_NEW"; then
        echo "refusing to replace a path this restore does not own: $FAILED_NEW" >&2
        exit 7
      fi
      /bin/rm -rf "$FAILED_NEW"
    fi
    write_journal "displacing" "quarantine failed destination; prior.app stays untouched"
    if ! /bin/mv "$DESTINATION" "$FAILED_NEW"; then
      echo "could not quarantine the failed destination" >&2
      write_journal "failed" "restore quarantine failed"
      exit 10
    fi
    write_journal "displaced" "destination absent; prior.app and failed-new present"
    fail_after_if "displaced"
    hold_if "displaced" || exit 18
  fi

  write_journal "preparing" "copy owned prior.app without mutating the capture"
  if owned_prepared "$PREPARED" && [[ -e "$PREPARED" ]]; then
    remove_owned_prepared "$PREPARED" || true
  fi
  if ! /usr/bin/ditto "$SOURCE" "$PREPARED"; then
    echo "prepare of the previous app failed" >&2
    write_journal "failed" "restore prepare failed"
    exit 8
  fi
  if ! verify_signed_app "$PREPARED"; then
    echo "prepared previous app failed signed identity check" >&2
    write_journal "failed" "restore prepared identity failed"
    remove_owned_prepared "$PREPARED" || true
    exit 9
  fi
  write_journal "prepared" "previous app prepared"
  fail_after_if "prepared"
  write_journal "adopting" "restore prepared rename"
  if ! /bin/mv "$PREPARED" "$DESTINATION"; then
    echo "could not adopt the previous app" >&2
    write_journal "failed" "restore adopt failed"
    if [[ -d "$FAILED_NEW" ]] && verify_signed_app "$FAILED_NEW"; then
      /bin/mv "$FAILED_NEW" "$DESTINATION" || true
    fi
    exit 11
  fi
  if ! verify_signed_app "$DESTINATION"; then
    echo "restored destination failed signed identity check" >&2
    write_journal "failed" "restore adopted identity failed"
    if [[ -d "$DESTINATION" ]]; then
      /bin/mv "$DESTINATION" "${CAPTURE}/failed-restore.app"
    fi
    exit 12
  fi
  local now_identity
  now_identity="$(app_identity_token "$DESTINATION" || true)"
  if [[ -n "$PRIOR_IDENTITY" && -n "$now_identity" && "$now_identity" != "$PRIOR_IDENTITY" ]]; then
    echo "restored destination identity drifted from owned prior.app" >&2
    write_journal "failed" "restore identity drifted"
    exit 15
  fi
  if [[ ! -d "$SOURCE" ]]; then
    echo "owned prior.app was destroyed during restore" >&2
    write_journal "failed" "prior.app missing after restore"
    exit 15
  fi
  write_journal "adopted" "previous app restored; capture retained"
  fail_after_if "adopted"
  write_terminal_receipt "restored" "true"
  fail_after_if "receipt"
  relaunch_destination || true
  exit 0
}

reject_preflight() {
  local detail="$1"
  local code="$2"
  write_journal "failed" "$detail"
  write_admission "rejected" "$detail"
  echo "$detail" >&2
  exit "$code"
}

[[ -e "$SOURCE" ]] || { echo "source app missing: $SOURCE" >&2; exit 3; }
if [[ -L "$SOURCE" || -L "$DESTINATION" ]]; then
  echo "refusing symlink source or destination" >&2
  exit 6
fi
if [[ -e "$DESTINATION" && ! -d "$DESTINATION" ]]; then
  echo "destination is not an application directory" >&2
  exit 6
fi

parent="$(cd "$(dirname "$DESTINATION")" && pwd)"
if [[ -z "$TRANSACTION" ]]; then
  if [[ "$RESUME" -eq 1 && -n "$JOURNAL" && -f "$JOURNAL" ]]; then
    TRANSACTION="$(journal_get transaction || true)"
  fi
  if [[ -z "$TRANSACTION" ]]; then
    TRANSACTION="$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
  fi
fi
if [[ -z "$ADMISSION" ]]; then
  ADMISSION="${RECEIPT}.admission.json"
fi
if [[ -z "$JOURNAL" ]]; then
  JOURNAL="${RECEIPT}.journal.json"
fi

CAPTURE="${parent}/.vc-update-capture-${TRANSACTION}"
PREPARED="${parent}/.vc-update-prepared-${TRANSACTION}.app"
DISPLACED="${CAPTURE}/displaced.app"
PRIOR="${CAPTURE}/prior.app"
LOCKDIR="${parent}/.vc-update.lock"
SOURCE_IDENTITY=""
PRIOR_IDENTITY=""
LOCK_OWNED=0

release_lock() {
  if [[ "$LOCK_OWNED" -eq 1 && -d "$LOCKDIR" ]]; then
    /bin/rm -rf "$LOCKDIR"
    LOCK_OWNED=0
  fi
}

lock_owner_live() {
  local owner="$LOCKDIR/owner.json"
  if [[ ! -f "$owner" ]]; then
    return 1
  fi
  local pid start
  pid="$(/usr/bin/python3 - "$owner" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("pid") or "")
PY
)"
  start="$(/usr/bin/python3 - "$owner" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("start") or "")
PY
)"
  if [[ -z "$pid" ]]; then
    return 1
  fi
  identity_live "$pid" "$start"
}

acquire_lock() {
  if mkdir "$LOCKDIR" 2>/dev/null; then
    LOCK_OWNED=1
    atomic_write "$LOCKDIR/owner.json" "$(printf '{"pid":"%s","start":"%s","transaction":"%s"}' \
      "$$" "$(escape_json "$(process_lstart "$$")")" "$(escape_json "$TRANSACTION")")"
    return 0
  fi
  if lock_owner_live; then
    echo "another update transaction holds the destination lock" >&2
    exit 13
  fi
  if [[ "$RESUME" -eq 1 ]]; then
    /bin/rm -rf "$LOCKDIR"
    if mkdir "$LOCKDIR" 2>/dev/null; then
      LOCK_OWNED=1
      atomic_write "$LOCKDIR/owner.json" "$(printf '{"pid":"%s","start":"%s","transaction":"%s"}' \
        "$$" "$(escape_json "$(process_lstart "$$")")" "$(escape_json "$TRANSACTION")")"
      return 0
    fi
  fi
  echo "another update transaction holds the destination lock" >&2
  exit 13
}

trap 'release_lock' EXIT
mkdir -p "$parent"
acquire_lock

if [[ "$RESUME" -eq 1 && -f "$JOURNAL" ]]; then
  local_phase="$(journal_get phase || true)"
  journal_txn="$(journal_get transaction || true)"
  journal_dest="$(journal_get destination || true)"
  if [[ -n "$journal_txn" && "$journal_txn" != "$TRANSACTION" ]]; then
    echo "resume journal transaction does not match" >&2
    exit 14
  fi
  if [[ -n "$journal_dest" && "$journal_dest" != "$DESTINATION" ]]; then
    echo "resume journal destination does not match" >&2
    exit 14
  fi
  CAPTURE="$(journal_get capture || printf '%s' "$CAPTURE")"
  PREPARED="$(journal_get prepared || printf '%s' "$PREPARED")"
  DISPLACED="$(journal_get displaced || printf '%s' "$DISPLACED")"
  PRIOR_IDENTITY="$(journal_get prior_identity || true)"
  SOURCE_IDENTITY="$(journal_get source_identity || true)"
  case "$local_phase" in
    displaced)
      if [[ ! -e "$DESTINATION" && -d "$DISPLACED" ]]; then
        if [[ -d "$PREPARED" ]] && verify_signed_app "$PREPARED"; then
          if ! /bin/mv "$PREPARED" "$DESTINATION"; then
            write_journal "failed" "resume could not adopt prepared"
            if [[ -d "$DISPLACED" ]] && verify_signed_app "$DISPLACED"; then
              if ! /bin/mv "$DISPLACED" "$DESTINATION"; then
                echo "resume could not restore the displaced app" >&2
                exit 11
              fi
              write_journal "rolled_back" "restored displaced after failed resume adopt"
              write_admission "rejected" "resume restored the previous destination"
              exit 11
            fi
            exit 11
          fi
          if ! verify_signed_app "$DESTINATION"; then
            echo "resumed destination failed signed identity check" >&2
            if [[ -d "$DESTINATION" ]]; then
              /bin/mv "$DESTINATION" "${CAPTURE}/failed-adopt.app"
            fi
            if [[ -d "$DISPLACED" ]] && verify_signed_app "$DISPLACED"; then
              if ! /bin/mv "$DISPLACED" "$DESTINATION"; then
                exit 12
              fi
              write_journal "rolled_back" "restored displaced after invalid resumed adopt"
              exit 12
            fi
            exit 12
          fi
          write_journal "adopted" "resumed adopt"
          write_terminal_receipt "replaced" "true"
          relaunch_destination || true
          exit 0
        fi
        if [[ -d "$DISPLACED" ]] && verify_signed_app "$DISPLACED"; then
          if [[ -n "$PRIOR_IDENTITY" ]]; then
            now_identity="$(app_identity_token "$DISPLACED" || true)"
            if [[ -n "$now_identity" && "$now_identity" != "$PRIOR_IDENTITY" ]]; then
              echo "displaced capture identity drifted; refusing blind restore" >&2
              write_journal "failed" "displaced identity drifted"
              exit 15
            fi
          fi
          if ! /bin/mv "$DISPLACED" "$DESTINATION"; then
            echo "resume could not restore the displaced app" >&2
            write_journal "failed" "resume restore of displaced failed"
            exit 11
          fi
          write_journal "rolled_back" "restored displaced after interruption"
          write_admission "rejected" "destination restored after interruption"
          exit 16
        fi
      fi
      ;;
    receipt_written|adopted|relaunched|relaunching)
      if [[ -d "$DESTINATION" ]] && verify_signed_app "$DESTINATION"; then
        if [[ ! -f "$RECEIPT" ]]; then
          write_terminal_receipt "replaced" "true"
        fi
        relaunch_destination || true
        exit 0
      fi
      ;;
    captured|prepared|preflight|ready|waiting_parent)
      if [[ -d "$DESTINATION" ]]; then
        if owned_prepared "$PREPARED" && [[ -e "$PREPARED" ]]; then
          remove_owned_prepared "$PREPARED" || true
        fi
        write_journal "failed" "resume aborted before destination displacement"
        write_admission "rejected" "previous destination is still installed"
        exit 17
      fi
      ;;
  esac
fi

if [[ "$MODE" == "restore" ]]; then
  restore_previous_tuple
fi

if ! SOURCE_IDENTITY="$(app_identity_token "$SOURCE")"; then
  SOURCE_IDENTITY=""
fi
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

if [[ -e "$DESTINATION" ]]; then
  write_journal "capturing" "unique owned capture"
  if ! /usr/bin/ditto "$DESTINATION" "$PRIOR"; then
    echo "unique capture of the previous app failed" >&2
    write_journal "failed" "capture failed"
    exit 7
  fi
  if ! PRIOR_IDENTITY="$(app_identity_token "$PRIOR")"; then
    echo "captured prior app has no durable identity" >&2
    write_journal "failed" "capture identity missing"
    exit 7
  fi
  if [[ "$ALLOW_UNSIGNED" -ne 1 ]] && ! verify_signed_app "$PRIOR"; then
    echo "captured prior app failed signed identity check" >&2
    write_journal "failed" "capture failed identity"
    exit 7
  fi
  write_journal "captured" "prior identity bound"
  fail_after_if "captured"
  hold_if "captured" || exit 18

  write_journal "preparing" "candidate copy"
  if ! /usr/bin/ditto "$SOURCE" "$PREPARED"; then
    echo "prepare of the candidate app failed" >&2
    write_journal "failed" "prepare failed"
    if [[ -e "$PREPARED" ]]; then
      remove_owned_prepared "$PREPARED" || true
    fi
    exit 8
  fi
  if ! verify_signed_app "$PREPARED"; then
    echo "prepared candidate failed signed identity check" >&2
    write_journal "failed" "prepared identity failed"
    remove_owned_prepared "$PREPARED" || true
    exit 9
  fi
  write_journal "prepared" "prepared candidate verified"
  fail_after_if "prepared"
  hold_if "prepared" || exit 18

  write_journal "displacing" "destination move is journaled, not atomic"
  if ! /bin/mv "$DESTINATION" "$DISPLACED"; then
    echo "could not displace the live destination" >&2
    write_journal "failed" "displace failed"
    remove_owned_prepared "$PREPARED" || true
    exit 10
  fi
  write_journal "displaced" "destination absent; displaced and prepared present"
  fail_after_if "displaced"
  hold_if "displaced" || exit 18

  write_journal "adopting" "prepared rename"
  if ! /bin/mv "$PREPARED" "$DESTINATION"; then
    echo "could not adopt the prepared app; restoring the verified displaced app" >&2
    write_journal "failed" "adopt failed"
    if [[ -d "$DISPLACED" ]] && verify_signed_app "$DISPLACED"; then
      now_identity="$(app_identity_token "$DISPLACED" || true)"
      if [[ -n "$PRIOR_IDENTITY" && -n "$now_identity" && "$now_identity" != "$PRIOR_IDENTITY" ]]; then
        echo "displaced capture identity drifted; refusing blind restore" >&2
        exit 15
      fi
      if ! /bin/mv "$DISPLACED" "$DESTINATION"; then
        echo "verified restore of the displaced app failed" >&2
        exit 11
      fi
      write_journal "rolled_back" "restored displaced after failed adopt"
    fi
    exit 11
  fi
  if ! verify_signed_app "$DESTINATION"; then
    echo "adopted destination failed signed identity check; restoring the verified previous app" >&2
    write_journal "failed" "adopted identity failed"
    if [[ -d "$DESTINATION" ]]; then
      /bin/mv "$DESTINATION" "${CAPTURE}/failed-adopt.app"
    fi
    if [[ -d "$DISPLACED" ]] && verify_signed_app "$DISPLACED"; then
      now_identity="$(app_identity_token "$DISPLACED" || true)"
      if [[ -n "$PRIOR_IDENTITY" && -n "$now_identity" && "$now_identity" != "$PRIOR_IDENTITY" ]]; then
        echo "displaced capture identity drifted; refusing blind restore" >&2
        exit 15
      fi
      if ! /bin/mv "$DISPLACED" "$DESTINATION"; then
        echo "verified restore of the displaced app failed" >&2
        exit 12
      fi
      write_journal "rolled_back" "restored displaced after invalid adopt"
    fi
    exit 12
  fi
  write_journal "adopted" "destination verified"
  fail_after_if "adopted"
else
  write_journal "preparing" "candidate copy"
  if ! /usr/bin/ditto "$SOURCE" "$PREPARED"; then
    echo "prepare of the candidate app failed" >&2
    write_journal "failed" "prepare failed"
    if [[ -e "$PREPARED" ]]; then
      remove_owned_prepared "$PREPARED" || true
    fi
    exit 8
  fi
  if ! verify_signed_app "$PREPARED"; then
    echo "prepared candidate failed signed identity check" >&2
    write_journal "failed" "prepared identity failed"
    remove_owned_prepared "$PREPARED" || true
    exit 9
  fi
  write_journal "prepared" "prepared candidate verified"
  fail_after_if "prepared"
  if ! /bin/mv "$PREPARED" "$DESTINATION"; then
    echo "could not adopt the prepared app" >&2
    write_journal "failed" "adopt failed"
    exit 11
  fi
  if ! verify_signed_app "$DESTINATION"; then
    echo "adopted destination failed signed identity check" >&2
    write_journal "failed" "adopted identity failed"
    if owned_prepared "${parent}/.vc-update-failed-${TRANSACTION}.app"; then
      :
    fi
    /bin/mv "$DESTINATION" "${parent}/.vc-update-failed-${TRANSACTION}.app"
    exit 12
  fi
  write_journal "adopted" "destination verified"
  fail_after_if "adopted"
fi

# Capture is retained after a verified adopt so pack failure can restore the
# previous working tuple. Do not delete it here.
detail="replaced"
if [[ "$MODE" == "restore" ]]; then
  detail="restored"
fi
write_terminal_receipt "$detail" "true"
fail_after_if "receipt"
relaunch_destination || true
exit 0
