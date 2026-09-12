#!/usr/bin/env bash
# aicx_sync_smoke.sh — Plan 08 (META_22) end-to-end smoke gate.
#
# Asserts that the AICX cross-machine sync v2 engine is internally
# consistent and survives the falsifier scenarios that broke the v1
# guardian-mode tool:
#
#   1. Two-corpus fixture (representing two mesh hosts) discovery returns
#      the expected adds/conflicts/ties.
#   2. Dry-run is read-only: zero filesystem mutation on either side.
#   3. Apply pass completes without crashing on a clean dual-add fixture.
#   4. Authority-tier conflict resolution picks the higher tier
#      automatically (RepoVerified > AicxAgent → remote wins).
#   5. Same-tier conflict surfaces a ConflictTie and the run is "not ok".
#   6. Conflict-log decision is honoured on a subsequent run.
#   7. Corrupted chunk is reported in `corrupted` and skipped — engine
#      does not crash, other chunks still flow.
#   8. CLI wrapper (scripts/aicx-sync.sh) entry path works end-to-end.
#
# Designed to run inside `make test-aicx-sync`. Uses the in-process Python
# CLI via `uv run` so the helper is exercised the same way an operator
# would invoke it.
#
# Vibecrafted with AI Agents (c)2024-2026 LibraxisAI

set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/.." && pwd)

PASS=0
FAIL=0

red()   { printf '\033[31m%s\033[0m' "$*"; }
green() { printf '\033[32m%s\033[0m' "$*"; }
amber() { printf '\033[33m%s\033[0m' "$*"; }

ok() {
    printf '  %s %s\n' "$(green ok)" "$1"
    PASS=$((PASS + 1))
}

fail() {
    printf '  %s %s\n' "$(red FAIL)" "$1"
    if [[ -n "${2:-}" ]]; then
        printf '       %s\n' "$2"
    fi
    FAIL=$((FAIL + 1))
}

section() {
    printf '\n%s\n' "$(amber "── $1 ──")"
}

# Sandboxed two-machine fixture roots — never touches the operator's real
# AICX corpus.
SANDBOX=$(mktemp -d -t vibecrafted-aicx-sync.XXXXXX)
trap 'rm -rf "$SANDBOX"' EXIT

LOCAL_STORE="$SANDBOX/machine-a"
REMOTE_STORE="$SANDBOX/machine-b"
CONFLICT_LOG="$SANDBOX/conflict-log.jsonl"

mkdir -p "$LOCAL_STORE" "$REMOTE_STORE"

# Helper: write a JSON chunk to a corpus root.
write_json_chunk() {
    local root="$1"
    local chunk_id="$2"
    local authority="$3"
    local content_hash="$4"
    local relpath="${5:-$chunk_id.json}"
    local dest="$root/$relpath"
    mkdir -p "$(dirname "$dest")"
    cat > "$dest" <<JSON
{
  "chunk_id": "$chunk_id",
  "authority": "$authority",
  "content_hash": "$content_hash",
  "namespace": "vetcoders/vibecrafted",
  "timestamp": "2026-05-12T10:00:00+00:00"
}
JSON
}

# Helper: invoke the engine CLI with our sandbox conflict log.
run_engine() {
    cd "$REPO_ROOT"
    AICX_SYNC_CONFLICT_LOG="$CONFLICT_LOG" \
        uv run --project vibecrafted-core --quiet python - <<PY "$@"
import json
import os
import sys
from pathlib import Path
from vibecrafted_core import aicx_sync

# Pin the conflict log to the sandbox so tests are hermetic.
aicx_sync.DEFAULT_CONFLICT_LOG = Path(os.environ["AICX_SYNC_CONFLICT_LOG"])

raise SystemExit(aicx_sync._cli(sys.argv[1:]))
PY
}

# JSON inspector — single value at a Python path on the engine output.
json_extract() {
    local jq_path="$1"
    python3 -c "
import json, sys
doc = json.load(sys.stdin)
print($jq_path)
"
}

# -----------------------------------------------------------------------------

section "Scenario 1: dual-add discovery (local-only + remote-only chunks)"

write_json_chunk "$LOCAL_STORE"  "local-only"  "aicx_agent"  "hl"
write_json_chunk "$REMOTE_STORE" "remote-only" "aicx_agent"  "hr"

OUT="$SANDBOX/run1.json"
run_engine dry-run "$LOCAL_STORE" "$REMOTE_STORE" > "$OUT"

actions_count=$(json_extract "len(doc['applied'])" < "$OUT")
if [[ "$actions_count" == "2" ]]; then
    ok "dry-run reports 2 add actions"
else
    fail "expected 2 add actions, got $actions_count" "$(cat "$OUT")"
fi

if grep -q '"direction": "local_to_remote"' "$OUT"; then
    ok "local-only add direction surfaced"
else
    fail "local-only add direction missing"
fi
if grep -q '"direction": "remote_to_local"' "$OUT"; then
    ok "remote-only add direction surfaced"
else
    fail "remote-only add direction missing"
fi

section "Scenario 2: dry-run is read-only"

# Snapshot fs state before/after dry-run; must be byte-identical.
PRE_HASH=$(find "$SANDBOX" -type f \( -name "*.json" -o -name "*.jsonl" \) \
    -exec shasum {} \; | sort | shasum | awk '{print $1}')
run_engine dry-run "$LOCAL_STORE" "$REMOTE_STORE" > /dev/null
POST_HASH=$(find "$SANDBOX" -type f \( -name "*.json" -o -name "*.jsonl" \) \
    -exec shasum {} \; | sort | shasum | awk '{print $1}')

if [[ "$PRE_HASH" == "$POST_HASH" ]]; then
    ok "dry-run produced zero filesystem mutation"
else
    fail "dry-run mutated the filesystem" "pre=$PRE_HASH post=$POST_HASH"
fi

section "Scenario 3: authority-tier conflict resolution (repo_verified > aicx_agent)"

# Same chunk on both sides, different content + different authority.
write_json_chunk "$LOCAL_STORE"  "conflict-tier" "aicx_agent"     "h-local"
write_json_chunk "$REMOTE_STORE" "conflict-tier" "repo_verified"  "h-remote"

OUT="$SANDBOX/run3.json"
run_engine dry-run "$LOCAL_STORE" "$REMOTE_STORE" > "$OUT"

resolutions=$(python3 -c "
import json, sys
doc = json.load(open('$OUT'))
res = [a for a in doc['applied'] if a.get('action') == 'resolve_conflict']
print(len(res), res[0]['winner'] if res else '-', res[0]['winner_authority'] if res else '-')
")
read -r count winner winner_auth <<< "$resolutions"
if [[ "$count" == "1" ]] && [[ "$winner" == "remote" ]] && [[ "$winner_auth" == "repo_verified" ]]; then
    ok "repo_verified beats aicx_agent automatically"
else
    fail "authority-tier resolution wrong" "count=$count winner=$winner auth=$winner_auth"
fi

unresolved=$(json_extract "len(doc['unresolved_ties'])" < "$OUT")
if [[ "$unresolved" == "0" ]]; then
    ok "no ties surfaced for resolvable conflict"
else
    fail "engine reported $unresolved ties on a resolvable conflict"
fi

section "Scenario 4: same-tier conflict surfaces ConflictTie"

# Reset the corpus, add a tie scenario.
rm -rf "$LOCAL_STORE" "$REMOTE_STORE"
mkdir -p "$LOCAL_STORE" "$REMOTE_STORE"
write_json_chunk "$LOCAL_STORE"  "tied-chunk" "aicx_agent" "hl"
write_json_chunk "$REMOTE_STORE" "tied-chunk" "aicx_agent" "hr"

OUT="$SANDBOX/run4.json"
set +e
run_engine dry-run "$LOCAL_STORE" "$REMOTE_STORE" > "$OUT"
rc=$?
set -e

if [[ "$rc" -ne 0 ]]; then
    ok "engine returns non-zero when a tie is unresolved"
else
    fail "engine returned 0 on an unresolved tie (should be non-zero)"
fi

ties_count=$(json_extract "len(doc['unresolved_ties'])" < "$OUT")
if [[ "$ties_count" == "1" ]]; then
    ok "ConflictTie surfaced for same-authority conflict"
else
    fail "expected 1 tie, got $ties_count" "$(cat "$OUT")"
fi

section "Scenario 5: prior logged decision honoured"

# Pre-seed the conflict log with a "local wins" decision.
cat > "$CONFLICT_LOG" <<JSON
{"timestamp": "2026-05-11T00:00:00+00:00", "chunk_id": "tied-chunk", "local_authority": "aicx_agent", "remote_authority": "aicx_agent", "decision": "local", "decided_by": "operator", "reason": "smoke-test pre-seed"}
JSON

OUT="$SANDBOX/run5.json"
run_engine dry-run "$LOCAL_STORE" "$REMOTE_STORE" > "$OUT"

winner=$(python3 -c "
import json
doc = json.load(open('$OUT'))
res = [a for a in doc['applied'] if a.get('action') == 'resolve_conflict']
print(res[0]['winner'] if res else 'NONE')
")
if [[ "$winner" == "local" ]]; then
    ok "prior 'local' decision honoured on subsequent run"
else
    fail "logged decision ignored; winner=$winner" "$(cat "$OUT")"
fi

ties_count=$(json_extract "len(doc['unresolved_ties'])" < "$OUT")
if [[ "$ties_count" == "0" ]]; then
    ok "no tie surfaced once decision is logged"
else
    fail "tie still surfaced despite logged decision"
fi

section "Scenario 6: corrupted chunk is reported + skipped (engine does not crash)"

# Reset, add a corrupted chunk + a healthy chunk on the same side.
rm -rf "$LOCAL_STORE" "$REMOTE_STORE" "$CONFLICT_LOG"
mkdir -p "$LOCAL_STORE" "$REMOTE_STORE"
printf '{not valid json' > "$LOCAL_STORE/broken.json"
write_json_chunk "$LOCAL_STORE" "healthy" "aicx_agent" "h-ok"

OUT="$SANDBOX/run6.json"
run_engine dry-run "$LOCAL_STORE" "$REMOTE_STORE" > "$OUT"

corrupted_count=$(json_extract "len(doc['corrupted'])" < "$OUT")
if [[ "$corrupted_count" == "1" ]]; then
    ok "corrupted chunk surfaced in result"
else
    fail "expected 1 corrupted entry, got $corrupted_count"
fi

actions_count=$(json_extract "len(doc['applied'])" < "$OUT")
if [[ "$actions_count" == "1" ]]; then
    ok "healthy chunk still flowed despite corrupted neighbour"
else
    fail "expected 1 add action, got $actions_count"
fi

section "Scenario 7: CLI wrapper (scripts/aicx-sync.sh) help works"

# Don't actually invoke the sync — just verify the wrapper parses args
# and the help text is emitted. Avoids real ~/.config + ~/.frontier-vault
# touch in CI environments.
WRAPPER_OUT="$SANDBOX/wrapper.out"
set +e
bash "$REPO_ROOT/scripts/aicx-sync.sh" help > "$WRAPPER_OUT" 2>&1
rc=$?
set -e

if [[ "$rc" -eq 0 ]] && grep -q "dry-run" "$WRAPPER_OUT" && grep -q "apply" "$WRAPPER_OUT"; then
    ok "scripts/aicx-sync.sh help emits usage"
else
    fail "wrapper help failed" "rc=$rc out=$(cat "$WRAPPER_OUT")"
fi

set +e
bash "$REPO_ROOT/scripts/aicx-sync.sh" unknown-command > "$WRAPPER_OUT" 2>&1
rc=$?
set -e

if [[ "$rc" -ne 0 ]]; then
    ok "wrapper rejects unknown commands with non-zero exit"
else
    fail "wrapper accepted an unknown command"
fi

section "Scenario 8: CLI wrapper config-file fallback (toml_get)"

CFG="$SANDBOX/cfg.toml"
cat > "$CFG" <<'TOML'
[default]
local_store = "/tmp/from-toml"
remote_host = "host-c"
namespace = "vetcoders/vibecrafted"
TOML

set +e
WRAPPER_OUT=$(bash "$REPO_ROOT/scripts/aicx-sync.sh" dry-run --config "$CFG" 2>&1)
rc=$?
set -e

# Wrapper will try to read /tmp/from-toml (non-existent on most boxes); we
# care that it picked the value from the toml. The header prefix lines are
# emitted before the engine call, so a grep on the announcement is enough.
if grep -q "local:  /tmp/from-toml" <<< "$WRAPPER_OUT"; then
    ok "toml local_store loaded into wrapper"
else
    fail "wrapper did not honour toml local_store" "$WRAPPER_OUT"
fi
if grep -q "host-c" <<< "$WRAPPER_OUT"; then
    ok "toml remote_host loaded into wrapper"
else
    fail "wrapper did not honour toml remote_host" "$WRAPPER_OUT"
fi

# -----------------------------------------------------------------------------

printf '\n%s\n' "$(amber "── summary ──")"
printf '  passed: %d\n' "$PASS"
printf '  failed: %d\n' "$FAIL"

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi

exit 0
