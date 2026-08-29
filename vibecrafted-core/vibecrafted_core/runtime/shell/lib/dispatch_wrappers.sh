# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

codex-review() {
  _vetcoders_spawn_plan codex review "$1" --runtime "$(_vetcoders_default_runtime)"
}

codex-plan() {
  _vetcoders_spawn_plan codex plan "$1" --runtime "$(_vetcoders_default_runtime)"
}

codex-implement() {
  _vetcoders_spawn_plan codex implement "$1" --runtime "$(_vetcoders_default_runtime)"
}

claude-review() {
  _vetcoders_spawn_plan claude review "$1" --runtime "$(_vetcoders_default_runtime)"
}

claude-plan() {
  _vetcoders_spawn_plan claude plan "$1" --runtime "$(_vetcoders_default_runtime)"
}

claude-implement() {
  _vetcoders_spawn_plan claude implement "$1" --runtime "$(_vetcoders_default_runtime)"
}

gemini-review() {
  _vetcoders_spawn_plan gemini review "$1" --runtime "$(_vetcoders_default_runtime)"
}

gemini-plan() {
  _vetcoders_spawn_plan gemini plan "$1" --runtime "$(_vetcoders_default_runtime)"
}

gemini-implement() {
  _vetcoders_spawn_plan gemini implement "$1" --runtime "$(_vetcoders_default_runtime)"
}

agy-review() {
  _vetcoders_spawn_plan agy review "$1" --runtime "$(_vetcoders_default_runtime)"
}

agy-plan() {
  _vetcoders_spawn_plan agy plan "$1" --runtime "$(_vetcoders_default_runtime)"
}

agy-implement() {
  _vetcoders_spawn_plan agy implement "$1" --runtime "$(_vetcoders_default_runtime)"
}

junie-review() {
  _vetcoders_spawn_plan junie review "$1" --runtime "$(_vetcoders_default_runtime)"
}

junie-plan() {
  _vetcoders_spawn_plan junie plan "$1" --runtime "$(_vetcoders_default_runtime)"
}

junie-implement() {
  _vetcoders_spawn_plan junie implement "$1" --runtime "$(_vetcoders_default_runtime)"
}

grok-review() {
  _vetcoders_spawn_plan grok review "$1" --runtime "$(_vetcoders_default_runtime)"
}

grok-plan() {
  _vetcoders_spawn_plan grok plan "$1" --runtime "$(_vetcoders_default_runtime)"
}

grok-implement() {
  _vetcoders_spawn_plan grok implement "$1" --runtime "$(_vetcoders_default_runtime)"
}

cursor-review() {
  _vetcoders_spawn_plan cursor review "$1" --runtime "$(_vetcoders_default_runtime)"
}

cursor-plan() {
  _vetcoders_spawn_plan cursor plan "$1" --runtime "$(_vetcoders_default_runtime)"
}

cursor-implement() {
  _vetcoders_spawn_plan cursor implement "$1" --runtime "$(_vetcoders_default_runtime)"
}

codex-research() {
  _vetcoders_spawn_plan codex research "$1" --runtime "$(_vetcoders_default_runtime)"
}

claude-research() {
  _vetcoders_spawn_plan claude research "$1" --runtime "$(_vetcoders_default_runtime)"
}

gemini-research() {
  _vetcoders_spawn_plan gemini research "$1" --runtime "$(_vetcoders_default_runtime)"
}

agy-research() {
  _vetcoders_spawn_plan agy research "$1" --runtime "$(_vetcoders_default_runtime)"
}

junie-research() {
  _vetcoders_spawn_plan junie research "$1" --runtime "$(_vetcoders_default_runtime)"
}

grok-research() {
  _vetcoders_spawn_plan grok research "$1" --runtime "$(_vetcoders_default_runtime)"
}

cursor-research() {
  _vetcoders_spawn_plan cursor research "$1" --runtime "$(_vetcoders_default_runtime)"
}

codex-prompt() {
  _vetcoders_prompt codex implement "$@"
}

claude-prompt() {
  _vetcoders_prompt claude implement "$@"
}

gemini-prompt() {
  _vetcoders_prompt gemini implement "$@"
}

agy-prompt() {
  _vetcoders_prompt agy implement "$@"
}

junie-prompt() {
  _vetcoders_prompt junie implement "$@"
}

grok-prompt() {
  _vetcoders_prompt grok implement "$@"
}

cursor-prompt() {
  _vetcoders_prompt cursor implement "$@"
}

codex-observe() {
  _vetcoders_observe codex "$@"
}

codex-await() {
  _vetcoders_await codex "$@"
}

claude-observe() {
  _vetcoders_observe claude "$@"
}

claude-await() {
  _vetcoders_await claude "$@"
}

gemini-observe() {
  _vetcoders_observe gemini "$@"
}

gemini-await() {
  _vetcoders_await gemini "$@"
}

agy-observe() {
  _vetcoders_observe agy "$@"
}

agy-await() {
  _vetcoders_await agy "$@"
}

junie-observe() {
  _vetcoders_observe junie "$@"
}

junie-await() {
  _vetcoders_await junie "$@"
}

grok-observe() {
  _vetcoders_observe grok "$@"
}

grok-await() {
  _vetcoders_await grok "$@"
}

cursor-observe() {
  _vetcoders_observe cursor "$@"
}

cursor-await() {
  _vetcoders_await cursor "$@"
}

_vetcoders_skill() {
  local tool="$1"
  local skill="$2"
  shift 2
  # shellcheck disable=SC2031
  local loop_nr="${VIBECRAFTED_LOOP_NR:-0}"
  local inherited_run_id
  local inherited_run_lock
  inherited_run_id="$(_vetcoders_effective_run_id 2>/dev/null || true)"
  inherited_run_lock="$(_vetcoders_effective_run_lock 2>/dev/null || true)"
  _vetcoders_parse_skill_contract "$@" || return 1
  if [[ "$skill" == "polarize" && -n "$_vetcoders_contract_count" ]]; then
    _vetcoders_polarize_loop "$tool" "$@"
    return
  fi
  [[ -z "$_vetcoders_contract_count" ]] || {
    echo "--count is only supported by vibecrafted marbles." >&2
    return 1
  }
  [[ -z "$_vetcoders_contract_depth" ]] || {
    echo "--depth is only supported by vibecrafted marbles." >&2
    return 1
  }
  [[ -z "$_vetcoders_contract_session" ]] || {
    echo "--session is only supported by vibecrafted resume." >&2
    return 1
  }
  local skill_code root run_id run_lock
  skill_code="$(_vetcoders_skill_prefix "$skill")"
  root="${_vetcoders_contract_root:-$(_vetcoders_repo_root)}"
  run_id="$inherited_run_id"
  [[ -n "$run_id" ]] || run_id="$(_vetcoders_generate_run_id "$skill_code")"
  run_lock="$inherited_run_lock"
  if [[ -z "$run_lock" || ! -f "$run_lock" ]]; then
    run_lock="$(_vetcoders_create_run_lock "$run_id" "$tool" "$skill" "$root")" || return 1
  fi
  # Default the prism_* locals: polarize only computes them when a --task is
  # given (see below). Without that, set -u must not trip on the band check at
  # dispatch time — an empty band falls through to a normal polarize dispatch.
  local prompt prism_payload="" prism_command="" prism_band="" prism_score="" memo_file=""
  if [[ "$skill" == "polarize" && -n "$_vetcoders_contract_task" ]]; then
    prism_payload="$(_vetcoders_write_polarize_prism_payload "$root" "$run_id" "$_vetcoders_contract_task" "$_vetcoders_contract_no_aicx")" || return 1
    prism_command="$(_vetcoders_polarize_prism_command_text "$root" "$_vetcoders_contract_task" "$_vetcoders_contract_no_aicx")"
    prism_band="$(_vetcoders_polarize_band_select "$prism_payload")" || return 1
    prism_score="$(_vetcoders_polarize_score "$prism_payload")" || return 1
    case "$prism_band" in
      abort)
        printf 'vc-polarize aborted: prism score %s/15 is below threshold. Inspect %s\n' "$prism_score" "$prism_payload" >&2
        return 12
        ;;
      memo)
        memo_file="$(_vetcoders_write_polarize_memo "$prism_payload" "$run_id" "$root" "$_vetcoders_contract_task" "$prism_band")" || return 1
        if [[ -z "$_vetcoders_contract_no_context_corpus" ]]; then
          _vetcoders_polarize_emit_context_pack "$tool" "" "$prism_payload" "$run_id" "$root" "$prism_band" "$_vetcoders_contract_task"
        else
          printf 'vc-polarize: --no-context-corpus set; skipped context-corpus emission.\n' >&2
        fi
        printf 'vc-polarize: emitted local memo (band %s, score %s/15). No agent dispatched. Memo: %s\n' "$(_vetcoders_polarize_band_range "$prism_band")" "$prism_score" "$memo_file"
        return 0
        ;;
      pass|doctrine)
        prompt="$(_vetcoders_compose_polarize_prompt "$_vetcoders_contract_prompt" "$_vetcoders_contract_file" "$_vetcoders_contract_task" "$prism_payload" "$prism_command" "$prism_band" "$prism_score")" || return 1
        ;;
      *)
        printf 'vc-polarize: unknown prism band %s from %s\n' "$prism_band" "$prism_payload" >&2
        return 1
        ;;
    esac
  else
    if [[ -n "$_vetcoders_contract_task" ]]; then
      if [[ -n "$_vetcoders_contract_prompt" ]]; then
        _vetcoders_contract_prompt+=$'\n\n'
      fi
      _vetcoders_contract_prompt+="Task: $_vetcoders_contract_task"
    fi
    prompt="$(_vetcoders_compose_skill_prompt "$skill" "$_vetcoders_contract_prompt" "$_vetcoders_contract_file")" || return 1
  fi
  local runtime
  runtime="$(_vetcoders_effective_runtime)"
  _vetcoders_prepare_operator_runtime "$runtime" || return 1
  local spawn_args=(--runtime "$runtime")
  [[ -z "$_vetcoders_contract_dry_run" ]] || spawn_args+=(--dry-run)
  [[ -n "$_vetcoders_contract_root" ]] && spawn_args+=(--root "$_vetcoders_contract_root")
  [[ -n "$_vetcoders_contract_model" ]] && spawn_args+=(--model "$_vetcoders_contract_model")
  if [[ "$skill" == "polarize" && "$prism_band" =~ ^(pass|doctrine)$ ]]; then
    local dispatch_output dispatch_status agent_log session_uuid
    agent_log="$(_vetcoders_store_dir "$root")/polarize/$run_id/${tool}.stdout.log"
    mkdir -p "$(dirname "$agent_log")"
    dispatch_output="$(_vetcoders_dispatch_skill_prompt "$tool" "$skill" "$skill_code" "$loop_nr" "$run_id" "$run_lock" "$prompt" "${spawn_args[@]}")"
    dispatch_status=$?
    printf '%s\n' "$dispatch_output"
    printf '%s\n' "$dispatch_output" > "$agent_log"
    _vetcoders_print_launch_receipt "$tool" "$skill" "$run_id" "$root" "$dispatch_status"
    [[ "$dispatch_status" -eq 0 ]] || return "$dispatch_status"
    if [[ -z "$_vetcoders_contract_no_context_corpus" ]]; then
      session_uuid="$(_vetcoders_capture_session_uuid "$agent_log")"
      _vetcoders_polarize_emit_context_pack "$tool" "$session_uuid" "$prism_payload" "$run_id" "$root" "$prism_band" "$_vetcoders_contract_task"
    else
      printf 'vc-polarize: --no-context-corpus set; skipped context-corpus emission.\n' >&2
    fi
    return 0
  fi

  local await_operator_tab_id="" await_operator_pane_id="" await_vc_frame_bin=""
  await_vc_frame_bin="$(_vetcoders_vc_frame_bin 2>/dev/null || true)"
  if [[ -n "$await_vc_frame_bin" ]] && _vetcoders_in_vc_frame; then
    await_operator_tab_id="$(_vetcoders_current_focused_vc_frame_tab_id "$await_vc_frame_bin" 2>/dev/null || true)"
    await_operator_pane_id="$(_vetcoders_current_focused_vc_frame_pane_id "$await_vc_frame_bin" 2>/dev/null || true)"
  fi

  _vetcoders_dispatch_skill_prompt "$tool" "$skill" "$skill_code" "$loop_nr" "$run_id" "$run_lock" "$prompt" "${spawn_args[@]}"
  local _dispatch_rc=$?
  _vetcoders_print_launch_receipt "$tool" "$skill" "$run_id" "$root" "$_dispatch_rc"
  _vetcoders_maybe_spawn_await_pane "$tool" "$skill" "$run_id" "$root" "$await_operator_tab_id" "$await_operator_pane_id"
  return "$_dispatch_rc"
}

# Spawn a vc-frame floating probe running vibecrafted-await-watch on the
# operator tab for the just-fired worker. observe/await stay artifact commands
# for the dispatcher; this float is the user-facing status signal.
#
# Silent no-op unless:
#   - the operator is inside an active vc-frame session
#   - the await-watch helper is installed and executable
#   - jq is available (helper needs it to parse meta.json)
#
# Legacy shell helpers are only a compatibility shim now. They must speak the
# Launchdeck/meta contract: resolve an active meta.json first, pass it directly,
# and otherwise stay silent instead of creating panes that guess by run_id.
_vetcoders_await_watch_helper() {
  local repo_root crafted_root candidate
  repo_root="$(_vetcoders_repo_root)"
  crafted_root="${VIBECRAFTED_TOOLS_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted/tools}/vibecrafted-current"

  for candidate in \
    "${VIBECRAFTED_RUNTIME_ROOT:+$VIBECRAFTED_RUNTIME_ROOT/runtime/scripts/vibecrafted-await-watch.sh}" \
    "${VIBECRAFTED_ROOT:+$VIBECRAFTED_ROOT/vibecrafted-core/vibecrafted_core/runtime/scripts/vibecrafted-await-watch.sh}" \
    "${VIBECRAFTED_ROOT:+$VIBECRAFTED_ROOT/runtime/scripts/vibecrafted-await-watch.sh}" \
    "$repo_root/vibecrafted-core/vibecrafted_core/runtime/scripts/vibecrafted-await-watch.sh" \
    "$crafted_root/vibecrafted-core/vibecrafted_core/runtime/scripts/vibecrafted-await-watch.sh" \
    "$(_vetcoders_frontier_file "runtime/scripts/vibecrafted-await-watch.sh" 2>/dev/null || true)"
  do
    [[ -n "$candidate" && -x "$candidate" ]] || continue
    printf '%s\n' "$candidate"
    return 0
  done

  return 1
}

_vetcoders_await_status_is_active() {
  case "${1:-}" in
    launching|running|in-progress|pending|created|brief_rendered|process_spawned|prompt_delivered|first_output_seen|active|artifact_seen|report_started|posthook_running)
      return 0
      ;;
  esac
  return 1
}

_vetcoders_active_await_meta_for_run_id() {
  local run_id="$1"
  local artifacts_root="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/artifacts"
  local candidate status pid
  [[ -n "$run_id" && -d "$artifacts_root" ]] || return 1

  while IFS= read -r candidate; do
    [[ -f "$candidate" ]] || continue
    [[ "$(jq -r '.run_id // ""' "$candidate" 2>/dev/null)" == "$run_id" ]] || continue
    status="$(jq -r '.status // ""' "$candidate" 2>/dev/null || true)"
    _vetcoders_await_status_is_active "$status" || continue
    pid="$(jq -r '.launcher_pid // ""' "$candidate" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && [[ "$pid" -gt 0 ]] && ! kill -0 "$pid" 2>/dev/null; then
      continue
    fi
    printf '%s\n' "$candidate"
    return 0
  done < <(find "$artifacts_root" -maxdepth 8 -name "*.meta.json" -type f -mtime -2 2>/dev/null)

  return 1
}

_vetcoders_current_focused_vc_frame_pane_id() {
  local vc_frame_bin="$1"
  local raw=""
  raw="$("$vc_frame_bin" action list-panes --json --state 2>/dev/null || true)"
  python3 - "$raw" <<'PY'
import json
import sys

raw = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
if not raw:
    raise SystemExit(1)
try:
    payload = json.loads(raw)
except Exception:
    raise SystemExit(1)

def is_truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False

def visit(node):
    if isinstance(node, dict):
        if is_truthy(node.get("is_focused")) or is_truthy(node.get("focused")):
            for key in ("pane_id", "id"):
                value = node.get(key)
                if value not in (None, ""):
                    print(value)
                    return True
        for value in node.values():
            if visit(value):
                return True
    elif isinstance(node, list):
        for value in node:
            if visit(value):
                return True
    return False

if not visit(payload):
    raise SystemExit(1)
PY
}

_vetcoders_current_focused_vc_frame_tab_id() {
  local vc_frame_bin="$1"
  local raw=""
  raw="$("$vc_frame_bin" action list-panes --json --state 2>/dev/null || true)"
  python3 - "$raw" <<'PY'
import json
import sys

raw = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
if not raw:
    raise SystemExit(1)
try:
    payload = json.loads(raw)
except Exception:
    raise SystemExit(1)

def is_truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False

def visit(node):
    if isinstance(node, dict):
        if is_truthy(node.get("is_focused")) or is_truthy(node.get("focused")):
            value = node.get("tab_id")
            if value not in (None, ""):
                print(value)
                return True
        for value in node.values():
            if visit(value):
                return True
    elif isinstance(node, list):
        for value in node:
            if visit(value):
                return True
    return False

if not visit(payload):
    raise SystemExit(1)
PY
}

_vetcoders_maybe_spawn_await_pane() {
  local PATH="${PATH:-}"
  PATH="$(_vetcoders_path_with_bundled_bin_priority "$PATH")"
  export PATH
  local tool="$1" skill="$2" run_id="$3" root="$4" operator_tab_id="${5:-}" operator_pane_id="${6:-}"
  local vc_frame_bin=""
  vc_frame_bin="$(_vetcoders_vc_frame_bin)" || return 0
  _vetcoders_in_vc_frame || return 0
  command -v jq >/dev/null 2>&1 || return 0

  local helper
  helper="$(_vetcoders_await_watch_helper 2>/dev/null || true)"
  [[ -n "$helper" && -x "$helper" ]] || return 0

  # Best effort: short delay so the wrapper has a moment to drop meta.json.
  ( sleep 1
    local meta=""
    meta="$(_vetcoders_active_await_meta_for_run_id "$run_id" 2>/dev/null || true)"
    [[ -n "$meta" && -f "$meta" ]] || exit 0
    local pane_name="await:${tool}:${run_id##*-}"
    local cwd="${root:-$PWD}"
    local tab_args=()
    [[ -z "$operator_tab_id" ]] || tab_args=(--tab-id "$operator_tab_id")
    # ${arr[@]+...} guard: macOS bash 3.2 treats an empty array expansion as
    # an unbound variable under `set -u` and kills the await-pane subshell.
    "$vc_frame_bin" action new-pane \
      ${tab_args[@]+"${tab_args[@]}"} \
      --floating \
      --width 24% \
      --height 35% \
      --x 76% \
      --y 8% \
      --name "$pane_name" \
      --close-on-exit \
      --cwd "$cwd" \
      -- "$helper" --meta "$meta" >/dev/null 2>&1 || true
    if [[ -n "$operator_pane_id" ]]; then
      "$vc_frame_bin" action focus-pane-id "$operator_pane_id" >/dev/null 2>&1 || true
    fi
  ) &
}

_vetcoders_skill_entry() {
  local tool="$1"
  local skill="$2"
  shift 2
  case "$skill" in
    marbles) _vetcoders_marbles "$tool" "$@" ;;
    polarize) _vetcoders_skill "$tool" polarize "$@" ;;
    *) _vetcoders_skill "$tool" "$skill" "$@" ;;
  esac
}
