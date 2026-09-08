#!/usr/bin/env bash

spawn_ancestor_mtime_iso() {
  local ancestor_plan="${1:-}"

  [[ -n "$ancestor_plan" ]] || return 0

  "$(spawn_python_bin)" - "$ancestor_plan" <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import sys

path = Path(sys.argv[1])
try:
    stat = path.stat()
except OSError:
    raise SystemExit(0)

print(datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
PY
}

spawn_ancestor_mtime_epoch() {
  local ancestor_plan="${1:-}"

  [[ -n "$ancestor_plan" ]] || return 0

  "$(spawn_python_bin)" - "$ancestor_plan" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
try:
    stat = path.stat()
except OSError:
    raise SystemExit(0)

print(int(stat.st_mtime))
PY
}
