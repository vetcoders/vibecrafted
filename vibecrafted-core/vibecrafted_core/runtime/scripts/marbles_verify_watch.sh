#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/util.sh
source "$SCRIPT_DIR/lib/util.sh"

state_file="$1"
loop_nr="$2"
report_path="$3"

max_wait="${VIBECRAFTED_MARBLES_VERIFICATION_TIMEOUT_S:-600}"
poll_s="${VIBECRAFTED_MARBLES_VERIFICATION_POLL_S:-10}"

case "$max_wait" in
  ''|*[!0-9]*)
    max_wait=600
    ;;
esac
case "$poll_s" in
  ''|*[!0-9]*)
    poll_s=10
    ;;
esac
(( poll_s > 0 )) || poll_s=10

verified_path="${report_path%.md}_verified.md"

_update_verification_state() {
  local new_status="$1"
  local verified_report="${2:-}"

  "$(spawn_python_bin)" - "$state_file" "$loop_nr" "$new_status" "$verified_report" <<'PY'
import datetime
import fcntl
import json
import os
import stat
import sys
import time
import uuid

original_state_path, loop_nr_raw, new_status, verified_report = sys.argv[1:5]
loop_nr = int(loop_nr_raw)

original_state_path = os.path.abspath(original_state_path)
run_dir = os.path.dirname(original_state_path)
run_id = os.path.basename(run_dir)
marbles_root = os.path.dirname(run_dir)
archive_root = os.path.join(marbles_root, "_archived")


def _is_plain_dir(path):
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _is_plain_file(path):
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _owned_state_candidates():
    candidates = []
    if _is_plain_dir(run_dir) and _is_plain_file(original_state_path):
        candidates.append(original_state_path)

    if _is_plain_dir(archive_root):
        try:
            date_entries = list(os.scandir(archive_root))
        except OSError:
            date_entries = []
        for date_entry in date_entries:
            if date_entry.is_symlink() or not date_entry.is_dir(follow_symlinks=False):
                continue
            archived_run_dir = os.path.join(date_entry.path, run_id)
            archived_state = os.path.join(archived_run_dir, "state.json")
            if _is_plain_dir(archived_run_dir) and _is_plain_file(archived_state):
                candidates.append(archived_state)
    return candidates


def _update_once(state_path):
    directory = os.path.dirname(state_path)
    open_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    open_flags |= getattr(os, "O_NOFOLLOW", 0)
    dir_fd = os.open(directory, open_flags)
    try:
        file_flags = os.O_RDWR | os.O_CREAT
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        lock_fd = os.open(
            "state.json.lock", file_flags, 0o600, dir_fd=dir_fd
        )
        with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock:
            if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                return True
            fcntl.flock(lock, fcntl.LOCK_EX)

            state_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            state_fd = os.open("state.json", state_flags, dir_fd=dir_fd)
            with os.fdopen(state_fd, encoding="utf-8") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    return True
                payload = json.load(handle)

            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            for loop in payload.get("loops", []):
                if loop.get("loop") != loop_nr:
                    continue
                current = loop.get("verification_status", "")
                if new_status == "timed_out" and current != "pending":
                    return True
                loop["verification_status"] = new_status
                if new_status == "completed" and verified_report:
                    loop["verified_report"] = verified_report
                break
            else:
                return True

            payload["updated_at"] = now
            tmp_name = f"state.json.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            tmp_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            tmp_flags |= getattr(os, "O_NOFOLLOW", 0)
            tmp_fd = os.open(tmp_name, tmp_flags, 0o600, dir_fd=dir_fd)
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(
                    tmp_name,
                    "state.json",
                    src_dir_fd=dir_fd,
                    dst_dir_fd=dir_fd,
                )
            finally:
                try:
                    os.unlink(tmp_name, dir_fd=dir_fd)
                except FileNotFoundError:
                    pass
        return True
    finally:
        os.close(dir_fd)


# The terminal watcher atomically moves the owned run directory into
# `_archived/<date>/<run_id>`. Resolve that one permitted relocation and retry
# only across the bounded rename/write window. Multiple copies are ambiguous
# and therefore never mutated.
for attempt in range(20):
    candidates = _owned_state_candidates()
    if len(candidates) > 1:
        raise SystemExit(0)
    if len(candidates) == 1:
        try:
            if _update_once(candidates[0]):
                raise SystemExit(0)
        except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError):
            pass
        except OSError:
            pass
    if attempt < 19:
        time.sleep(0.05)
PY
}

elapsed=0
while (( elapsed < max_wait )); do
  if [[ -s "$verified_path" ]]; then
    _update_verification_state "completed" "$verified_path"
    exit 0
  fi
  sleep "$poll_s"
  (( elapsed += poll_s ))
done

_update_verification_state "timed_out"
