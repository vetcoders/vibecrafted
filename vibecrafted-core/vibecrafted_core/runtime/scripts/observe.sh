#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
  cat <<EOF_USAGE
Usage: observe.sh [codex|claude|agy|junie|grok|cursor] [--last|--run-id <id>|path-to-meta|path-to-transcript|path-to-report]

Examples:
  observe.sh codex --last
  observe.sh codex --run-id impl-123456
  observe.sh claude /path/to/report.meta.json
  observe.sh /path/to/transcript.log
EOF_USAGE
}

filter_observe_tail() {
  python3 -c '
import re
import sys

session_line = re.compile(
    r"(?:\x1b\[[0-9;]*m)?\[[0-9]{2}:[0-9]{2}:[0-9]{2}\] session: [0-9a-f-]{36}(?:\x1b\[0m)?"
)
for line in sys.stdin:
    if session_line.search(line):
        continue
    if "rmcp::transport::worker" in line and (
        "Connection refused" in line
        or "Transport channel closed" in line
        or "request failed" in line
        or "upstream connect error" in line
    ):
        continue
    sys.stdout.write(line)
'
}

agent=""
target="--last"
run_id=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    codex|claude|agy|junie|grok|cursor)
      [[ -z "$agent" ]] || spawn_die "Agent already set to $agent"
      agent="$1"
      ;;
    --last)
      target="--last"
      ;;
    --run-id)
      shift
      [[ $# -gt 0 ]] || spawn_die "Missing value for --run-id"
      run_id="$1"
      target="--run-id"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      target="$1"
      ;;
  esac
  shift
done

root="$(spawn_repo_root)"
store_root="${VIBECRAFTED_AWAIT_STORE_DIR:-$(spawn_store_dir "$root")}"
store_dir="${VIBECRAFTED_AWAIT_REPORTS_DIR:-$store_root/reports}"
meta=""
report=""
transcript=""

if [[ -n "$run_id" ]]; then
  meta="$(python3 - "$store_root" "$run_id" <<'PY'
import json
import sys
from pathlib import Path

store_root = Path(sys.argv[1])
target_run_id = sys.argv[2]
patterns = [
    "reports/*.meta.json",
    "research/*/logs/*.meta.json",
    "research/*/reports/*.meta.json",
]
for pattern in patterns:
    for path in sorted(store_root.glob(pattern), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("run_id") or "") == target_run_id:
            print(path)
            raise SystemExit(0)
PY
)"
  [[ -n "$meta" ]] || spawn_die "No metadata found for --run-id $run_id under $store_root. Use await --run-id $run_id to wait for metadata, or pass an explicit meta/report/transcript path."
elif [[ "$target" == "--last" ]]; then
  if [[ -n "$agent" ]]; then
    meta="$(find "$store_dir" -maxdepth 1 -type f -name "*_${agent}.meta.json" 2>/dev/null | sort | tail -n 1)"
    [[ -z "$meta" ]] && transcript="$(find "$store_dir" -maxdepth 1 -type f -name "*_${agent}.transcript.log" 2>/dev/null | sort | tail -n 1)"
  else
    meta="$(find "$store_dir" -maxdepth 1 -type f -name '*.meta.json' 2>/dev/null | sort | tail -n 1)"
    [[ -z "$meta" ]] && transcript="$(find "$store_dir" -maxdepth 1 -type f -name '*.transcript.log' 2>/dev/null | sort | tail -n 1)"
  fi
elif [[ -f "$target" || "$target" == *.meta.json ]]; then
  case "$target" in
    *.json)
      meta="$target"
      ;;
    *.transcript.log)
      transcript="$target"
      ;;
    *)
      report="$target"
      ;;
  esac
else
  usage
  exit 1
fi

if [[ -n "$meta" && ! -f "$meta" ]]; then
  resolved_meta="$(python3 - "$store_root" "$meta" <<'PY'
import json
import sys
from pathlib import Path

store_root = Path(sys.argv[1])
expected = Path(sys.argv[2]).expanduser()


def read_field(path: Path, field: str) -> str:
    if not path.is_file():
        return ""
    prefix = f"{field}:"
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:80]:
            stripped = line.strip()
            if stripped.startswith(prefix):
                return stripped.split(":", 1)[1].strip().strip("'\"")
    except OSError:
        return ""
    return ""


probes = []
if expected.name.endswith(".meta.json"):
    stem = expected.name[: -len(".meta.json")]
    probes.extend(
        [
            expected.with_name(f"{stem}.md"),
            expected.with_name(f"{stem}.transcript.log"),
        ]
    )

wanted_run_id = ""
wanted_session_id = ""
for probe in probes:
    wanted_run_id = wanted_run_id or read_field(probe, "run_id")
    wanted_session_id = wanted_session_id or read_field(probe, "session_id")
    if wanted_run_id and wanted_session_id:
        break

if not wanted_run_id and not wanted_session_id:
    raise SystemExit(0)

patterns = [
    "reports/*.meta.json",
    "research/*/logs/*.meta.json",
    "research/*/reports/*.meta.json",
]
for pattern in patterns:
    for path in sorted(store_root.glob(pattern), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if wanted_run_id and str(payload.get("run_id") or "") == wanted_run_id:
            print(path)
            raise SystemExit(0)
        if wanted_session_id and str(payload.get("session_id") or "") == wanted_session_id:
            print(path)
            raise SystemExit(0)
PY
)"
  if [[ -n "$resolved_meta" ]]; then
    meta="$resolved_meta"
  fi
fi

if [[ -n "$meta" ]]; then
  [[ -f "$meta" ]] || spawn_die "Metadata path is not readable and no canonical meta resolved: $meta"
  python3 - "$meta" <<'PY'
import json
import sys
with open(sys.argv[1], 'r', encoding='utf-8') as fh:
    data = json.load(fh)
print(f"Agent:      {data.get('agent')}")
print(f"Run ID:     {data.get('run_id')}")
print(f"Status:     {data.get('status')}")
print(f"Liveness:   {data.get('liveness')}")
print(f"Updated:    {data.get('updated_at')}")
print(f"Mode:       {data.get('mode')}")
print(f"Model:      {data.get('model') or '-'}")
print(f"Input:      {data.get('input')}")
print(f"Report:     {data.get('report')}")
print(f"Transcript: {data.get('transcript')}")
print(f"Launcher:   {data.get('launcher')}")
print(f"Exit code:  {data.get('exit_code')}")
PY
  transcript="$(python3 - "$meta" <<'PY'
import json
import sys
with open(sys.argv[1], 'r', encoding='utf-8') as fh:
    data = json.load(fh)
print(data.get('transcript') or '')
PY
)"
  report="$(python3 - "$meta" <<'PY'
import json
import sys
with open(sys.argv[1], 'r', encoding='utf-8') as fh:
    data = json.load(fh)
print(data.get('report') or '')
PY
)"
fi

if [[ -n "$report" && -s "$report" ]]; then
  echo '--- report tail ---'
  tail -n 80 "$report"
  exit 0
fi

if [[ -n "$transcript" && -f "$transcript" ]]; then
  # Streaming-json providers keep the raw machine transcript in *.log and the
  # AgentStreamParser rendering in *.human.log — show the human one when present.
  human_transcript="${transcript%.log}.human.log"
  if [[ -s "$human_transcript" ]]; then
    transcript="$human_transcript"
  fi
  echo '--- transcript tail ---'
  tail -n 80 "$transcript" | filter_observe_tail
  exit 0
fi

spawn_die 'No report or transcript found yet.'
