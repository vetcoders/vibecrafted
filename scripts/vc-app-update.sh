#!/bin/bash
# vc-app-update — replace the Vibecrafted UI bundle after the App process exits.
#
# This helper owns UI replacement only. It does not stop Frame, PTYs, workers,
# sessions, or rewrite Founder config, PATH, interpreters, or MCP.
set -euo pipefail

SOURCE=""
DESTINATION=""
RECEIPT=""
WAIT_PID=""
RELAUNCH=0

usage() {
  echo "usage: vc-app-update --source APP --destination APP --receipt FILE [--wait-pid PID] [--relaunch]" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="${2:-}"; shift 2 ;;
    --destination) DESTINATION="${2:-}"; shift 2 ;;
    --receipt) RECEIPT="${2:-}"; shift 2 ;;
    --wait-pid) WAIT_PID="${2:-}"; shift 2 ;;
    --relaunch) RELAUNCH=1; shift ;;
    *) usage ;;
  esac
done

[[ -n "$SOURCE" && -n "$DESTINATION" && -n "$RECEIPT" ]] || usage
[[ -e "$SOURCE" ]] || { echo "source app missing: $SOURCE" >&2; exit 3; }

wait_for_pid() {
  local pid="$1"
  local n=0
  while kill -0 "$pid" 2>/dev/null; do
    if (( n >= 600 )); then
      echo "timed out waiting for pid $pid" >&2
      return 1
    fi
    sleep 0.05
    n=$((n + 1))
  done
}

if [[ -n "$WAIT_PID" ]]; then
  wait_for_pid "$WAIT_PID"
fi

parent="$(dirname "$DESTINATION")"
mkdir -p "$parent"
backup="${DESTINATION}.vibecrafted-update-backup"
rm -rf "$backup"
if [[ -e "$DESTINATION" ]]; then
  mv "$DESTINATION" "$backup"
  if ! /usr/bin/ditto "$SOURCE" "$DESTINATION"; then
    rm -rf "$DESTINATION"
    mv "$backup" "$DESTINATION"
    echo "ditto failed; previous app restored" >&2
    exit 4
  fi
  rm -rf "$backup"
else
  /usr/bin/ditto "$SOURCE" "$DESTINATION"
fi

relaunched=false
if [[ "$RELAUNCH" -eq 1 ]]; then
  if /usr/bin/open -n "$DESTINATION"; then
    relaunched=true
  fi
fi

escaped="${DESTINATION//\\/\\\\}"
escaped="${escaped//\"/\\\"}"
umask 077
printf '{"destination":"%s","detail":"replaced","relaunched":%s,"replaced":true}\n' \
  "$escaped" "$relaunched" >"$RECEIPT"
exit 0
