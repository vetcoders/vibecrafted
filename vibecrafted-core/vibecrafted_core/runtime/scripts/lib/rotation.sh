#!/usr/bin/env bash

spawn_rotation_validate_mode() {
  local mode="${1:-single}"

  case "$mode" in
    single|duo|trio|multi) return 0 ;;
    *) spawn_die "Invalid rotation mode: $mode" ;;
  esac
}

spawn_rotation_pool_json() {
  "$(spawn_python_bin)" - <<'PY'
import json

print(json.dumps(["codex", "claude", "agy"]))
PY
}

spawn_rotation_schedule_agent() {
  local mode="${1:-single}"
  local seed_agent="${2:-codex}"
  local loop_nr="${3:-1}"

  "$(spawn_python_bin)" - "$mode" "$seed_agent" "$loop_nr" <<'PY'
import sys

mode, seed_agent, loop_raw = sys.argv[1:4]
pool = ["codex", "claude", "agy"]

try:
    loop_nr = max(int(loop_raw), 1)
except ValueError:
    loop_nr = 1

if seed_agent not in pool:
    seed_agent = pool[0]

seed_index = pool.index(seed_agent)
single_cycle = [seed_agent]
duo_cycle = [pool[seed_index], pool[(seed_index + 1) % len(pool)]]
trio_cycle = [
    pool[seed_index],
    pool[(seed_index + 1) % len(pool)],
    pool[(seed_index + 2) % len(pool)],
]
multi_cycle = [pool[(seed_index + offset) % len(pool)] for offset in range(len(pool))]

cycles = {
    "single": single_cycle,
    "duo": duo_cycle,
    "trio": trio_cycle,
    "multi": multi_cycle,
}

cycle = cycles.get(mode, single_cycle)
print(cycle[(loop_nr - 1) % len(cycle)])
PY
}
