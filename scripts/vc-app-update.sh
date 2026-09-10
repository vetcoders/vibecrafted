#!/bin/bash
# vc-app-update — sole UI-bundle mutation owner.
#
# This script is the only replacement implementation. The App launches it and
# does not reimplement wait/ditto/backup. It does not stop Frame, PTYs, workers,
# sessions, or rewrite Founder config, PATH, interpreters, or MCP.
#
# System tools only: the destination App and its runtime Python may be moving.
set -euo pipefail

SOURCE=""
DESTINATION=""
RECEIPT=""
WAIT_PID=""
WAIT_START=""
WAIT_TIMEOUT="30"
RELAUNCH=0
EXPECTED_IDENTIFIER="io.vetcoders.vibecrafted"
EXPECTED_TEAM="MW223P3NPX"

usage() {
  echo "usage: vc-app-update --source APP --destination APP --receipt FILE [--wait-pid PID] [--wait-start LSTART] [--wait-timeout SECONDS] [--relaunch] [--expected-identifier ID] [--expected-team TEAM]" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="${2:-}"; shift 2 ;;
    --destination) DESTINATION="${2:-}"; shift 2 ;;
    --receipt) RECEIPT="${2:-}"; shift 2 ;;
    --wait-pid) WAIT_PID="${2:-}"; shift 2 ;;
    --wait-start) WAIT_START="${2:-}"; shift 2 ;;
    --wait-timeout) WAIT_TIMEOUT="${2:-}"; shift 2 ;;
    --relaunch) RELAUNCH=1; shift ;;
    --expected-identifier) EXPECTED_IDENTIFIER="${2:-}"; shift 2 ;;
    --expected-team) EXPECTED_TEAM="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done

[[ -n "$SOURCE" && -n "$DESTINATION" && -n "$RECEIPT" ]] || usage
[[ -e "$SOURCE" ]] || { echo "source app missing: $SOURCE" >&2; exit 3; }

# Survive parent UI exit. Do not follow or replace through a symlink.
trap '' HUP
if [[ -L "$SOURCE" || -L "$DESTINATION" ]]; then
  echo "refusing symlink source or destination" >&2
  exit 6
fi
if [[ -e "$DESTINATION" && ! -d "$DESTINATION" ]]; then
  echo "destination is not an application directory" >&2
  exit 6
fi

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

if [[ -n "$WAIT_PID" ]]; then
  if ! wait_for_identity "$WAIT_PID" "$WAIT_START" "$WAIT_TIMEOUT"; then
    exit 5
  fi
fi

verify_signed_app() {
  local app="$1"
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

# Unique owned capture/prepare only. Never a fixed backup name. Never rm -rf
# an arbitrary path. Existing sibling captures are left untouched.
parent="$(cd "$(dirname "$DESTINATION")" && pwd)"
txn="$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
capture="${parent}/.vc-update-capture-${txn}"
prepared="${parent}/.vc-update-prepared-${txn}.app"

remove_owned() {
  local path="$1"
  case "$path" in
    "${parent}/.vc-update-prepared-${txn}.app"|"${parent}/.vc-update-prepared-${txn}.app/")
      /bin/rm -rf "$path"
      ;;
    *)
      echo "refusing to remove a path this helper does not own: $path" >&2
      return 1
      ;;
  esac
}

mkdir -p "$parent"

if ! verify_signed_app "$SOURCE"; then
  echo "source failed signed identity check at the mutation boundary" >&2
  exit 9
fi

if [[ -e "$DESTINATION" ]]; then
  mkdir -p "$capture"
  if ! /usr/bin/ditto "$DESTINATION" "$capture/prior.app"; then
    echo "unique capture of the previous app failed" >&2
    exit 7
  fi
  if ! /usr/bin/ditto "$SOURCE" "$prepared"; then
    echo "prepare of the candidate app failed" >&2
    if [[ -e "$prepared" ]]; then
      remove_owned "$prepared" || true
    fi
    exit 8
  fi
  if ! verify_signed_app "$prepared"; then
    echo "prepared candidate failed signed identity check" >&2
    remove_owned "$prepared" || true
    exit 9
  fi
  if ! /bin/mv "$DESTINATION" "$capture/displaced.app"; then
    echo "could not displace the live destination" >&2
    remove_owned "$prepared" || true
    exit 10
  fi
  if ! /bin/mv "$prepared" "$DESTINATION"; then
    echo "could not adopt the prepared app; restoring the previous destination" >&2
    /bin/mv "$capture/displaced.app" "$DESTINATION" || true
    exit 11
  fi
  if ! verify_signed_app "$DESTINATION"; then
    echo "adopted destination failed signed identity check; restoring the previous app" >&2
    /bin/mv "$DESTINATION" "$capture/failed-adopt.app" || true
    /bin/mv "$capture/displaced.app" "$DESTINATION" || true
    exit 12
  fi
else
  if ! /usr/bin/ditto "$SOURCE" "$prepared"; then
    echo "prepare of the candidate app failed" >&2
    if [[ -e "$prepared" ]]; then
      remove_owned "$prepared" || true
    fi
    exit 8
  fi
  if ! verify_signed_app "$prepared"; then
    echo "prepared candidate failed signed identity check" >&2
    remove_owned "$prepared" || true
    exit 9
  fi
  if ! /bin/mv "$prepared" "$DESTINATION"; then
    echo "could not adopt the prepared app" >&2
    exit 11
  fi
  if ! verify_signed_app "$DESTINATION"; then
    echo "adopted destination failed signed identity check" >&2
    /bin/mv "$DESTINATION" "${parent}/.vc-update-failed-${txn}.app" || true
    exit 12
  fi
fi

# Capture is retained after a verified adopt so pack failure can restore the
# previous working tuple. Do not delete it here.
relaunched=false
if [[ "$RELAUNCH" -eq 1 ]]; then
  if /usr/bin/open -n "$DESTINATION"; then
    relaunched=true
  fi
fi

escape_json() {
  printf '%s' "$1" | /usr/bin/sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

umask 077
receipt_dir="$(dirname "$RECEIPT")"
mkdir -p "$receipt_dir"
printf '{"schema":"io.vetcoders.vibecrafted.app-replacement.v1","capture":"%s","destination":"%s","detail":"replaced","relaunched":%s,"replaced":true,"transaction":"%s"}\n' \
  "$(escape_json "$capture")" \
  "$(escape_json "$DESTINATION")" \
  "$relaunched" \
  "$(escape_json "$txn")" >"$RECEIPT"
exit 0
