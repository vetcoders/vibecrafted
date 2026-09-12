# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_polarize_prism_axes() {
  local task="$1"
  printf '%s\n' "$task"
  printf '%s\n' "$task code truth"
  printf '%s\n' "$task product truth"
}

_vetcoders_polarize_prism_command_text() {
  local root="$1"
  local task="$2"
  local no_aicx="${3:-}"
  local args=(loct prism --project "$root")
  if [[ "$no_aicx" == "1" ]]; then
    args+=(--no-aicx)
  else
    args+=(--with-aicx)
  fi
  while IFS= read -r axis; do
    [[ -n "$axis" ]] || continue
    args+=(--task "$axis")
  done < <(_vetcoders_polarize_prism_axes "$task")
  args+=(--json)
  _vetcoders_shell_quote_join "${args[@]}"
}

_vetcoders_write_polarize_prism_payload() {
  local root="$1"
  local run_id="$2"
  local task="$3"
  local no_aicx="${4:-}"
  local out_dir payload_file command_file
  local -a prism_args

  command -v loct >/dev/null 2>&1 || {
    echo "vc-polarize requires loct for prism preflight; loct not found on PATH." >&2
    return 1
  }

  out_dir="$(_vetcoders_store_dir "$root")/polarize/$run_id"
  mkdir -p "$out_dir"
  payload_file="$out_dir/prism.json"
  command_file="$out_dir/prism.command.txt"

  prism_args=(prism --project "$root")
  if [[ "$no_aicx" == "1" ]]; then
    prism_args+=(--no-aicx)
  else
    prism_args+=(--with-aicx)
  fi
  while IFS= read -r axis; do
    [[ -n "$axis" ]] || continue
    prism_args+=(--task "$axis")
  done < <(_vetcoders_polarize_prism_axes "$task")
  prism_args+=(--json)

  printf '%s\n' "$(_vetcoders_polarize_prism_command_text "$root" "$task" "$no_aicx")" > "$command_file"
  (cd "$root" && loct "${prism_args[@]}") > "$payload_file" || {
    echo "vc-polarize prism preflight failed. Command: $(cat "$command_file")" >&2
    return 1
  }

  printf '%s\n' "$payload_file"
}

_vetcoders_polarize_score() {
  local prism_json="$1"
  "$(_vetcoders_internal_python)" - "$prism_json" <<'PY'
import json
import pathlib
import sys

data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(int(data.get("total_score", 0)))
PY
}

_vetcoders_polarize_band_select() {
  local prism_json="$1"
  "$(_vetcoders_internal_python)" - "$prism_json" <<'PY'
import json
import pathlib
import sys

data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
band_action = str(data.get("band_action", "")).strip().lower()
if band_action in {"abort", "memo", "pass", "doctrine"}:
    print(band_action)
    raise SystemExit(0)
score = int(data.get("total_score", 0))
if score < 5:
    print("abort")
elif score < 9:
    print("memo")
elif score < 13:
    print("pass")
else:
    print("doctrine")
PY
}

_vetcoders_polarize_band_range() {
  case "${1:-}" in
    abort) printf '0..4\n' ;;
    memo) printf '5..8\n' ;;
    pass) printf '9..12\n' ;;
    doctrine) printf '13..15\n' ;;
    *) printf 'unknown\n' ;;
  esac
}

_vetcoders_capture_session_uuid() {
  local agent_log="$1"
  [[ -r "$agent_log" ]] || return 0
  grep -oE 'session: [0-9a-f-]{36}' "$agent_log" | tail -n1 | awk '{print $2}'
}

_vetcoders_write_polarize_memo() {
  local prism_json="$1"
  local run_id="$2"
  local root="$3"
  local task="${4:-}"
  local band="${5:-memo}"
  local score memo_file

  score="$(_vetcoders_polarize_score "$prism_json")" || return 1
  memo_file="$(_vetcoders_store_dir "$root")/polarize/$run_id/memo.md"
  mkdir -p "$(dirname "$memo_file")"
  {
    printf '# vc-polarize memo\n\n'
    printf 'Run: %s\n' "$run_id"
    printf 'Band: %s (score %s/15)\n' "$band" "$score"
    [[ -z "$task" ]] || printf 'Task: %s\n' "$task"
    printf 'Prism payload: %s\n\n' "$prism_json"
    printf 'Runner action: memo only. No full polarize agent pass was dispatched.\n'
  } > "$memo_file"
  printf '%s\n' "$memo_file"
}

_vetcoders_polarize_emit_context_pack() {
  local agent="$1"
  local session_uuid="$2"
  local prism_json="$3"
  local run_id="$4"
  local target_repo="$5"
  local band="$6"
  local task="${7:-}"
  local org_repo org repo date pack_dir slug raw_path sidecar_path aicx_bin

  org_repo="$(_vetcoders_org_repo "$target_repo" 2>/dev/null || true)"
  if [[ "$org_repo" == */* ]]; then
    org="${org_repo%%/*}"
    repo="${org_repo##*/}"
  else
    org="local"
    repo="$(basename "$target_repo")"
  fi

  date="$(date +%Y_%m%d)"
  pack_dir="$HOME/.aicx/context-corpus/$org/$repo/$date/loct-context-pack/$run_id"
  slug="${run_id}_${band}"
  raw_path="$pack_dir/raw/${slug}.md"
  sidecar_path="$pack_dir/sidecars/${slug}.json"
  mkdir -p "$pack_dir/raw" "$pack_dir/sidecars"

  if [[ "$band" == "memo" ]]; then
    local memo_file
    memo_file="$(_vetcoders_write_polarize_memo "$prism_json" "$run_id" "$target_repo" "$task" "$band")" || return 0
    cp "$memo_file" "$raw_path" || return 0
  else
    [[ -n "$session_uuid" ]] || {
      printf 'vc-polarize: no session UUID found; skipping context-pack emission.\n' >&2
      return 0
    }
    aicx_bin="$(_vetcoders_aicx_bin 2>/dev/null)" || {
      printf 'vc-polarize: aicx not found; skipping context-pack emission. Use --no-context-corpus to silence this optional step.\n' >&2
      return 0
    }
    "$aicx_bin" extract --agent "$agent" --session "$session_uuid" --output "$raw_path" || {
      printf 'vc-polarize: aicx extract failed for session %s; skipping context-pack emission.\n' "$session_uuid" >&2
      return 0
    }
  fi

  "$(_vetcoders_internal_python)" - "$prism_json" "$sidecar_path" "$band" "$target_repo" "$slug" "$raw_path" "$task" <<'PY'
import hashlib
import json
import pathlib
import re
import subprocess
import sys

prism_path, sidecar_path, band, target_repo, slug, raw_path, task = sys.argv[1:]
prism = json.loads(pathlib.Path(prism_path).read_text(encoding="utf-8"))
try:
    head = subprocess.check_output(
        ["git", "-C", target_repo, "rev-parse", "HEAD"],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
except Exception:
    head = "unknown"

terms = []
for framing in prism.get("task_framings", []):
    value = framing.get("task", "")
    terms.extend(re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", value.lower()))
terms.extend(re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", task.lower()))
keywords = sorted(set(terms)) or ["polarize"]
allowed = ["format_examples"] if band == "memo" else [
    "format_examples",
    "section_order",
    "keyword_index",
]
raw_bytes = pathlib.Path(raw_path).read_bytes()
sidecar = {
    "schema_version": "context_corpus.v1",
    "artifact_family": "loct-context-pack",
    "truth_status": {
        "role": "example",
        "runtime_authoritative": False,
        "stale_against_current_head": False,
        "current_head_when_ingested": head,
    },
    "learning_use": {
        "allowed": allowed,
        "forbidden": ["current_code_truth", "implementation_claims", "gate_status"],
    },
    "keywords": keywords,
    "band": band,
    "total_score": prism.get("total_score"),
    "slug": slug,
    "content_sha256": hashlib.sha256(raw_bytes).hexdigest(),
}
sidecar_file = pathlib.Path(sidecar_path)
sidecar_file.write_text(json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8")
index_entry = {
    "id": slug,
    "path": f"raw/{slug}.md",
    "artifact_family": "loct-context-pack",
    "schema_version": "context_corpus.v1",
    "truth_status.role": "example",
    "keywords": keywords,
    "band": band,
}
idx_path = sidecar_file.parent.parent / "index.jsonl"
with idx_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(index_entry, sort_keys=True) + "\n")
PY
}

_vetcoders_compose_polarize_prompt() {
  local prompt_text="${1:-}"
  local file_path="${2:-}"
  local task="${3:-}"
  local payload_file="${4:-}"
  local prism_command="${5:-}"
  local band="${6:-}"
  local score="${7:-}"
  local base payload_body

  base="$(_vetcoders_compose_skill_prompt "polarize" "$prompt_text" "$file_path")" || return 1
  if [[ -n "$task" ]]; then
    base+=$'\n\n'
    base+="Polarize task: $task"
  fi
  if [[ -n "$band" && -n "$score" ]]; then
    base+=$'\n'
    base+="Band: $band (score $score/15)"
    base+=$'\n'
    base+="Runner action: $band"
  fi
  if [[ -n "$payload_file" ]]; then
    payload_body="$(cat "$payload_file")"
    base+=$'\n\n'
    base+="Prism preflight command: $prism_command"
    base+=$'\n'
    base+="Prism payload file: $payload_file"
    base+=$'\n\n'
    base+="The full prism payload is injected below. Treat it as the starting evidence pack, then verify live runtime truth before editing."
    base+=$'\n\n```json\n'
    base+="$payload_body"
    base+=$'\n```'
  fi

  printf '%s\n' "$base"
}

_vetcoders_polarize_loop() {
  local tool="$1"
  shift

  _vetcoders_parse_contract "$@" || return 1
  [[ -n "$_vetcoders_contract_count" ]] || {
    echo "vc-polarize loop runtime requires --count <n>." >&2
    return 1
  }
  _vetcoders_require_positive_int "$_vetcoders_contract_count" "--count" || return 1
  [[ -z "$_vetcoders_contract_depth" ]] || _vetcoders_require_positive_int "$_vetcoders_contract_depth" "--depth" || return 1
  [[ -z "$_vetcoders_contract_session" ]] || {
    echo "--session is only supported by vibecrafted resume." >&2
    return 1
  }

  local root skill_code run_id prompt seed_dir seed_file
  root="${_vetcoders_contract_root:-$(_vetcoders_repo_root)}"
  skill_code="$(_vetcoders_skill_prefix "polarize")"
  run_id="${VIBECRAFTED_LOOP_RUN_ID:-$(_vetcoders_generate_run_id "$skill_code")}"
  prompt="$(_vetcoders_compose_polarize_prompt "$_vetcoders_contract_prompt" "$_vetcoders_contract_file" "$_vetcoders_contract_task")" || return 1
  if [[ -n "$_vetcoders_contract_depth" ]]; then
    prompt+=$'\n\n'
    prompt+="Requested loop context depth: $_vetcoders_contract_depth"
  fi

  seed_dir="$root/.vibecrafted/tmp"
  mkdir -p "$seed_dir"
  seed_file="$seed_dir/${run_id}_polarize-loop.md"
  printf '%s\n' "$prompt" > "$seed_file"

  local marbles_args=(--count "$_vetcoders_contract_count" --file "$seed_file")
  [[ -n "$_vetcoders_contract_root" ]] && marbles_args+=(--root "$_vetcoders_contract_root")
  [[ -n "$_vetcoders_contract_runtime" ]] && marbles_args+=(--runtime "$_vetcoders_contract_runtime")

  VIBECRAFTED_LOOP_SKILL_NAME="polarize" \
  VIBECRAFTED_LOOP_SKILL_CODE="$skill_code" \
  VIBECRAFTED_LOOP_LABEL="Polarize" \
  VIBECRAFTED_LOOP_FILE_PREFIX="polarize" \
  VIBECRAFTED_LOOP_RUN_ID="$run_id" \
    _vetcoders_marbles "$tool" "${marbles_args[@]}"
}
