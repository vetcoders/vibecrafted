#!/usr/bin/env bash


spawn_append_operator_intervention() {
  # Append-only, run-scoped operator intervention log. This is the safe
  # foundation for future live prompts: no shell-output scraping, no hidden
  # magic. Compatible watchers/bridges can consume the JSONL file explicitly.
  local meta_path="$1"
  local message="$2"
  local actor="${3:-operator}"

  [[ -f "$meta_path" ]] || spawn_die "meta.json not found: $meta_path"
  [[ -n "$message" ]] || spawn_die "operator intervention message is empty"

  "$(spawn_python_bin)" - "$meta_path" "$actor" "$message" <<'PY'
import datetime as dt
import json
import os
import sys

meta_path, actor, message = sys.argv[1:4]

try:
    with open(meta_path, "r", encoding="utf-8") as handle:
        meta = json.load(handle)
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"cannot read meta.json: {exc}")

run_id = str(meta.get("run_id") or "").strip()
if not run_id:
    raise SystemExit("meta.json has no run_id; refusing unscoped intervention")

base = meta_path[:-10] if meta_path.endswith(".meta.json") else meta_path
intervention_path = f"{base}.interventions.jsonl"
now = dt.datetime.now(dt.timezone.utc).isoformat()
event = {
    "schema": "vibecrafted.operator_intervention.v1",
    "created_at": now,
    "run_id": run_id,
    "actor": actor or "operator",
    "message": message,
    "consumer_contract": "compatible-watchers-and-bridges-only",
}

os.makedirs(os.path.dirname(intervention_path) or ".", exist_ok=True)
with open(intervention_path, "a", encoding="utf-8") as handle:
    json.dump(event, handle, ensure_ascii=False, sort_keys=True)
    handle.write("\n")

count = 0
try:
    with open(intervention_path, "r", encoding="utf-8") as handle:
        count = sum(1 for line in handle if line.strip())
except OSError:
    count = 1

meta["intervention_path"] = intervention_path
meta["intervention_count"] = count
meta["last_intervention_at"] = now
meta["updated_at"] = now
with open(meta_path, "w", encoding="utf-8") as handle:
    json.dump(meta, handle, indent=2, ensure_ascii=False)
    handle.write("\n")

transcript = str(meta.get("transcript") or "").strip()
if transcript:
    transcript_dir = os.path.dirname(transcript)
    if transcript_dir:
        os.makedirs(transcript_dir, exist_ok=True)
    visible = message.replace("\r", "\\r").replace("\n", "\\n")
    with open(transcript, "a", encoding="utf-8") as handle:
        handle.write(
            f"\n[operator intervention {now} run_id={run_id} actor={event['actor']}] "
            f"{visible}\n"
        )

print(intervention_path)
PY
}
