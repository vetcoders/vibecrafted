#!/usr/bin/env bash


spawn_print_dashboard_hint() {
  printf '\nRun:\n\nvibecrafted dashboard\n\nto monitor your sessions live.\n'
}

spawn_watch_startup() {
  local meta_path="$1"
  local transcript_path="$2"
  local report_path="$3"
  local seconds="${4:-${VIBECRAFTED_SPAWN_WATCH_SECONDS:-10}}"
  local rc=0

  [[ "$seconds" =~ ^[0-9]+$ ]] || seconds=10
  (( seconds > 0 )) || return 0

  if "$(spawn_python_bin)" - "$meta_path" "$transcript_path" "$report_path" "$seconds" <<'PY'
import json
import os
import re
import sys
import time

meta_path, transcript_path, report_path, seconds_raw = sys.argv[1:5]
seconds = max(int(seconds_raw), 0)
deadline = time.monotonic() + seconds
ansi_re = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
failure_markers = (
    "Not logged in",
    "Please run /login",
    "Invalid UTF-8",
    "Permission denied",
    "Traceback",
    "panic",
)


def read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + 5 :].lstrip("\n")


def report_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def meta_status(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("status") or "")


initial_transcript = strip_frontmatter(read_text(transcript_path))
initial_report_size = report_size(report_path)
activity = bool(initial_transcript.strip()) or initial_report_size > 0
printed_len = 0
echo_enabled = os.environ.get("VIBECRAFTED_STARTUP_WATCH_ECHO", "1") != "0"

if initial_transcript and echo_enabled:
    sys.stdout.write(initial_transcript)
    sys.stdout.flush()
    printed_len = len(initial_transcript)
else:
    printed_len = len(initial_transcript)

clean_initial = ansi_re.sub("", initial_transcript)
if any(marker in clean_initial for marker in failure_markers):
    raise SystemExit(10)

status = meta_status(meta_path)
if status == "failed":
    raise SystemExit(10)
if status == "completed":
    raise SystemExit(0)

while time.monotonic() < deadline:
    status = meta_status(meta_path)
    if status == "failed":
        raise SystemExit(10)
    if status == "completed":
        raise SystemExit(0)

    transcript_body = strip_frontmatter(read_text(transcript_path))
    if len(transcript_body) > printed_len:
        appended = transcript_body[printed_len:]
        clean = ansi_re.sub("", appended)
        if appended.strip():
            activity = True
            if echo_enabled:
                sys.stdout.write(appended)
                sys.stdout.flush()
        printed_len = len(transcript_body)
        if any(marker in clean for marker in failure_markers):
            raise SystemExit(10)

    if report_size(report_path) > initial_report_size:
        activity = True

    time.sleep(0.2)

raise SystemExit(0 if activity else 11)
PY
  then
    rc=0
  else
    rc=$?
  fi

  case "$rc" in
    0)
      printf '\nStartup check: passed in the first %ss.\n' "$seconds"
      ;; 
    10)
      printf '\nStartup check: failed in the first %ss.\n' "$seconds"
      ;; 
    11)
      printf '\nStartup check: still launching after %ss.\n' "$seconds"
      ;; 
    *)
      printf '\nStartup check: inconclusive (watch rc=%s).\n' "$rc"
      ;; 
  esac

  if [[ "$rc" != "10" ]]; then
    spawn_print_dashboard_hint
  fi
  return 0
}
