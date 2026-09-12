# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_marbles() {
  local PATH="${PATH:-}"
  PATH="$(_vetcoders_path_with_bundled_bin_priority "$PATH")"
  export PATH
  local tool="$1"
  shift
  local script marbles_cmd quoted_args quoted_env operator_session root_dir marbles_run_id runtime launch_ts launch_report
  local loop_skill_name loop_skill_code loop_label loop_file_prefix
  local -a marbles_env
  script="$(_vetcoders_spawn_script "$tool" "marbles_spawn.sh")" || return 1
  _vetcoders_parse_contract "$@" || return 1
  [[ -z "$_vetcoders_contract_session" ]] || {
    echo "--session is only supported by vibecrafted resume." >&2
    return 1
  }
  if [[ -n "$_vetcoders_contract_task" ]]; then
    [[ -z "$_vetcoders_contract_file" ]] || {
      echo "Marbles accepts one file source: use either --task or --file, not both." >&2
      return 1
    }
    _vetcoders_contract_file="$_vetcoders_contract_task"
  fi

  local source_count=0
  [[ -n "$_vetcoders_contract_depth" ]] && ((source_count+=1))
  [[ -n "$_vetcoders_contract_file" ]] && ((source_count+=1))
  [[ -n "$_vetcoders_contract_prompt" ]] && ((source_count+=1))
  [[ $source_count -le 1 ]] || {
    echo "Marbles accepts one source at a time: use exactly one of --depth, --file, or --prompt." >&2
    return 1
  }
  [[ -z "$_vetcoders_contract_count" ]] || _vetcoders_require_positive_int "$_vetcoders_contract_count" "--count" || return 1
  [[ -z "$_vetcoders_contract_depth" ]] || _vetcoders_require_positive_int "$_vetcoders_contract_depth" "--depth" || return 1

  loop_skill_name="${VIBECRAFTED_LOOP_SKILL_NAME:-marbles}"
  loop_skill_code="${VIBECRAFTED_LOOP_SKILL_CODE:-$(_vetcoders_skill_prefix "$loop_skill_name")}"
  loop_label="${VIBECRAFTED_LOOP_LABEL:-Marbles}"
  loop_file_prefix="${VIBECRAFTED_LOOP_FILE_PREFIX:-$loop_skill_name}"

  # shellcheck disable=SC2031
  [[ -n "${VIBECRAFTED_SKILL_NAME:-}" ]] || export VIBECRAFTED_SKILL_NAME="$loop_skill_name"
  # shellcheck disable=SC2031
  export VIBECRAFTED_SKILL_CODE="$loop_skill_code"

  root_dir="${_vetcoders_contract_root:-$(_vetcoders_repo_root)}"
  marbles_run_id="${VIBECRAFTED_MARBLES_RUN_ID:-${VIBECRAFTED_LOOP_RUN_ID:-$(_vetcoders_generate_run_id "$loop_skill_code")}}"
  runtime="$(_vetcoders_effective_runtime)"
  marbles_env=(
    VIBECRAFTED_MARBLES_RUN_ID="$marbles_run_id"
    VIBECRAFTED_LOOP_SKILL_NAME="$loop_skill_name"
    VIBECRAFTED_LOOP_SKILL_CODE="$loop_skill_code"
    VIBECRAFTED_LOOP_LABEL="$loop_label"
    VIBECRAFTED_LOOP_FILE_PREFIX="$loop_file_prefix"
  )
  local marbles_args=(--agent "$tool" --runtime "$runtime" --skill-name "$loop_skill_name" --skill-code "$loop_skill_code" --loop-label "$loop_label" --loop-file-prefix "$loop_file_prefix")
  local source_args=()
  [[ -n "$_vetcoders_contract_root" ]] && marbles_args+=(--root "$_vetcoders_contract_root")
  [[ -n "$_vetcoders_contract_count" ]] && marbles_args+=(--count "$_vetcoders_contract_count")

  if [[ -n "$_vetcoders_contract_file" ]]; then
    source_args=(--file "$_vetcoders_contract_file")
  elif [[ -n "$_vetcoders_contract_prompt" ]]; then
    source_args=(--prompt "$_vetcoders_contract_prompt")
  else
    source_args=(--depth "${_vetcoders_contract_depth:-3}")
  fi
  if [[ "$runtime" == "headless" ]]; then
    marbles_args+=(--no-watch)
    launch_ts="$(_vetcoders_spawn_timestamp)"
    launch_report="$(_vetcoders_marbles_l1_report_path "$root_dir" "$launch_ts" "$tool")"
    marbles_env+=(VIBECRAFTED_SPAWN_TS="$launch_ts" VIBECRAFTED_SUPPRESS_REPORT_HINT=1)
    printf 'Agent launched. Report will land at: %s\n' "$launch_report"
  fi
  marbles_args+=("${source_args[@]}")

  quoted_env="$(_vetcoders_shell_quote_join "${marbles_env[@]}")"
  quoted_args="$(_vetcoders_shell_quote_join "${marbles_args[@]}")"
  marbles_cmd="env ${quoted_env} bash $(_vetcoders_shell_quote "$script") ${quoted_args}"
  operator_session="${VIBECRAFTED_OPERATOR_SESSION:-}"
  if [[ -z "$operator_session" ]] && _vetcoders_in_vc_frame; then
    operator_session="$(_vetcoders_current_vc_frame_session_name)"
  fi
  if [[ -z "$operator_session" ]]; then
    operator_session="$(_vetcoders_operator_session_name)"
  fi

  # Inside vc_frame: each marbles run_id gets its own tab named
  # "marbles-<run_id>". Subsequent loops (L2, L3, ...) inherit
  # VIBECRAFTED_MARBLES_TAB_NAME via env and stay in the same tab — one
  # run_id = one tab, no crossover. The "marbles-" prefix distinguishes
  # the tab from workflow/research tabs which also carry run_ids.
  # Temp script keeps vc_frame args ASCII-safe (no inline UTF-8 prompt bytes).
  local vc_frame_bin=""
  if [[ "$runtime" =~ ^(terminal|visible)$ ]] && _vetcoders_in_vc_frame && vc_frame_bin="$(_vetcoders_vc_frame_bin)"; then
    local cmd_script marbles_tab_name
    VIBECRAFTED_OPERATOR_SESSION="$(_vetcoders_current_vc_frame_session_name)"
    export VIBECRAFTED_OPERATOR_SESSION
    marbles_tab_name="${loop_file_prefix}-${marbles_run_id}"
    export VIBECRAFTED_MARBLES_TAB_NAME="$marbles_tab_name"
    marbles_env+=(VIBECRAFTED_MARBLES_TAB_NAME="$marbles_tab_name")
    quoted_env="$(_vetcoders_shell_quote_join "${marbles_env[@]}")"
    marbles_cmd="env ${quoted_env} bash $(_vetcoders_shell_quote "$script") ${quoted_args}"
    cmd_script="$(_vetcoders_tmp_script_path "vibecrafted-marbles" "$root_dir")"
    _vetcoders_write_command_script "$cmd_script" "$marbles_cmd" || return 1
    
    local original_tab
    original_tab="${VC_FRAME_TAB_NAME:-}"
    
    "$vc_frame_bin" action go-to-tab-name "$marbles_tab_name" --create >/dev/null 2>&1 || true
    "$vc_frame_bin" action new-pane \
      --name "$marbles_run_id" \
      --cwd "$root_dir" \
      -- "$cmd_script" >/dev/null || return 1

    printf '%s run launched in vc_frame tab: %s\n' "$loop_label" "$marbles_tab_name"
    printf '  run_id:  %s\n' "$marbles_run_id"
    printf '  inspect: vc-marbles inspect %s\n' "$marbles_run_id"
      
    if [[ -n "$original_tab" ]]; then
      "$vc_frame_bin" action go-to-tab-name "$original_tab" >/dev/null 2>&1 || true
    fi
    
    _vetcoders_marbles_emit_probe "$root_dir" "$marbles_run_id" "launched"
  elif [[ "$runtime" =~ ^(terminal|visible)$ ]]; then
    _vetcoders_prepare_operator_runtime "$runtime" || return 1
    if [[ -n "${VIBECRAFTED_OPERATOR_SESSION:-}" ]]; then
      _vetcoders_spawn_into_operator_session "marbles" "$marbles_cmd" || return 1
      printf '%s run launched in operator session: %s\n' "$loop_label" "$VIBECRAFTED_OPERATOR_SESSION"
      printf '  run_id:  %s\n' "$marbles_run_id"
      printf '  inspect: vc-marbles inspect %s\n' "$marbles_run_id"
      _vetcoders_marbles_emit_probe "$root_dir" "$marbles_run_id" "launched"
    else
      env "${marbles_env[@]}" bash "$script" "${marbles_args[@]}"
    fi
  else
    env "${marbles_env[@]}" bash "$script" "${marbles_args[@]}"
  fi
}

# When resume has no session_id: assemble a bounded multi-agent continuity pack
# from the AICX session chain (sessions list + continuity, CLI transport).
# Emits KEY=value lines on stdout:
#   SESSION_ID=        (always empty here — native attach needs --session)
#   CONTEXT_FILE=...
#   SESSION_COUNT=...
#   MODE=new_session
_vetcoders_aicx_resume_fallback() {
  local agent="$1"
  local root="${2:-$(_vetcoders_repo_root)}"
  local hours="${VIBECRAFTED_RESUME_AICX_HOURS:-48}"
  local tmp_dir context_file meta_file aicx_bin python_spec py import_root module source_dir
  aicx_bin="$(_vetcoders_aicx_bin 2>/dev/null)" || {
    echo "aicx foundation not found in the Vibecrafted runtime, ~/.local/bin, ~/.cargo/bin, or PATH." >&2
    echo "Install the AICX foundation or pass --session <session_id>." >&2
    return 1
  }
  tmp_dir="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/tmp"
  mkdir -p "$tmp_dir" 2>/dev/null || tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/vc-resume.XXXXXX")"
  context_file="$tmp_dir/resume-aicx-${agent}-$(date +%Y%m%d_%H%M%S).md"
  meta_file="${context_file}.meta.json"

  # Prefer the module in the owned core so a stale installed package cannot
  # hide the live assembler. Do not rediscover through BASH_SOURCE (empty
  # under zsh).
  module=""
  source_dir="$(_vetcoders_owned_core_dir 2>/dev/null || true)"
  if [[ -n "$source_dir" && -f "$source_dir/vibecrafted_core/aicx_session_chain.py" ]]; then
    module="$source_dir/vibecrafted_core/aicx_session_chain.py"
  fi
  python_spec="$(_vetcoders_core_python_spec)" || return 1
  py="${python_spec%%$'\t'*}"
  import_root="${python_spec#*$'\t'}"
  if [[ -z "$py" || -z "$module" ]]; then
    echo "Vibecrafted session-chain assembler unavailable (python or module missing)." >&2
    return 1
  fi
  if [[ -n "$import_root" ]]; then
    PYTHONPATH="$import_root" "$py" "$module" resume-pack \
      --agent "$agent" \
      --root "$root" \
      --hours "$hours" \
      --aicx "$aicx_bin" \
      --context-file "$context_file" \
      --meta-file "$meta_file"
  else
    "$py" "$module" resume-pack \
      --agent "$agent" \
      --root "$root" \
      --hours "$hours" \
      --aicx "$aicx_bin" \
      --context-file "$context_file" \
      --meta-file "$meta_file"
  fi
}

# Resolve one owned Python >=3.11 that can import the live vibecrafted_core
# package. Interpreter selection is `_vetcoders_owned_python_bin` (installed
# generation fail-closed; source checkout keeps the development fallback).
# Import root is the captured/owned core dir — never empty zsh BASH_SOURCE.
# Output is: <python-path><TAB><optional-PYTHONPATH-import-root>.
# PYTHONPATH is applied only for this proof, never exported.
_vetcoders_core_python_spec() {
  local py="" package_parent=""
  py="$(_vetcoders_owned_python_bin)" || return 1
  package_parent="$(_vetcoders_owned_core_dir)" || return 1
  if "$py" -c 'import vibecrafted_core' >/dev/null 2>&1; then
    printf '%s\t\n' "$py"
    return 0
  fi
  if [[ -d "$package_parent/vibecrafted_core" ]] &&
    PYTHONPATH="$package_parent" \
      "$py" -c 'import vibecrafted_core' >/dev/null 2>&1; then
    printf '%s\t%s\n' "$py" "$package_parent"
    return 0
  fi
  echo "Vibecrafted core unavailable: cannot import vibecrafted_core." >&2
  return 1
}

_vetcoders_run_core_cli() {
  local python_spec py import_root
  python_spec="$(_vetcoders_core_python_spec)" || return 1
  py="${python_spec%%$'\t'*}"
  import_root="${python_spec#*$'\t'}"
  if [[ -n "$import_root" ]]; then
    PYTHONPATH="$import_root" \
      "$py" -m vibecrafted_core.cli "$@"
  else
    "$py" -m vibecrafted_core.cli "$@"
  fi
}

# Cursor --force/--trust admission uses the shared Cursor probe (commit
# 7597d881: probe_cursor_cli_surface / require_cursor_flags). Fail closed —
# never hardcode flags from error prose or a missing probe.
_vetcoders_cursor_permission_flags() {
  local python_spec py import_root timeout
  python_spec="$(_vetcoders_core_python_spec)" || return 1
  py="${python_spec%%$'\t'*}"
  import_root="${python_spec#*$'\t'}"
  timeout="${VIBECRAFTED_CURSOR_PROBE_TIMEOUT_S:-10}"
  if [[ -n "$import_root" ]]; then
    PYTHONPATH="$import_root" \
      "$py" - "$timeout" <<'PY'
import sys
from vibecrafted_core.cursor_admission import cursor_permission_flag_string

print(cursor_permission_flag_string(timeout=float(sys.argv[1])))
PY
  else
    "$py" - "$timeout" <<'PY'
import sys
from vibecrafted_core.cursor_admission import cursor_permission_flag_string

print(cursor_permission_flag_string(timeout=float(sys.argv[1])))
PY
  fi
}

_vetcoders_core_source_dir() {
  local python_spec py import_root
  python_spec="$(_vetcoders_core_python_spec)" || return 1
  py="${python_spec%%$'\t'*}"
  import_root="${python_spec#*$'\t'}"
  if [[ -n "$import_root" ]]; then
    PYTHONPATH="$import_root" \
      "$py" -c 'from vibecrafted_core.package_resources import package_root; print(package_root())'
  else
    "$py" -c 'from vibecrafted_core.package_resources import package_root; print(package_root())'
  fi
}

# Print a shell-safe filter pipeline fragment for non-interactive
# streaming-json agents through the same core-Python resolver as launch paths.
_vetcoders_agent_stream_filter_cmd() {
  local agent="$1"
  local raw_file="${2:-}"
  local python_spec py import_root python_prefix
  local quoted_agent quoted_raw quoted_parent quoted_py
  python_spec="$(_vetcoders_core_python_spec)" || return 1
  py="${python_spec%%$'\t'*}"
  import_root="${python_spec#*$'\t'}"
  quoted_py="$(_vetcoders_shell_quote "$py")"
  quoted_agent="$(_vetcoders_shell_quote "$agent")"
  if [[ -n "$raw_file" ]]; then
    quoted_raw="$(_vetcoders_shell_quote "$raw_file")"
  else
    quoted_raw=""
  fi
  if [[ -n "$import_root" ]]; then
    quoted_parent="$(
      _vetcoders_shell_quote "$import_root${PYTHONPATH:+:$PYTHONPATH}"
    )"
    python_prefix="PYTHONPATH=${quoted_parent} ${quoted_py}"
  else
    python_prefix="$quoted_py"
  fi
  if [[ -n "$quoted_raw" ]]; then
    printf '%s -m vibecrafted_core.agent_stream --agent %s --raw-file %s\n' \
      "$python_prefix" "$quoted_agent" "$quoted_raw"
  else
    printf '%s -m vibecrafted_core.agent_stream --agent %s\n' \
      "$python_prefix" "$quoted_agent"
  fi
}

# Path for raw streaming-json transcript (human pane sees filtered text only).
_vetcoders_resume_raw_transcript_path() {
  local agent="$1"
  local home_dir="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}"
  local stamp
  stamp="$(date +%Y%m%d-%H%M%S 2>/dev/null || echo now)"
  printf '%s/artifacts/resume/%s-%s.stream.jsonl\n' "$home_dir" "$agent" "$stamp"
}

# Wrap a headless agent command so streaming-json is rendered via AgentStreamParser.
# Grok always uses this path. Other agents pass through unchanged unless they emit
# streaming-json in the headless resume builders.
_vetcoders_wrap_with_agent_stream() {
  local agent="$1"
  local cmd="$2"
  local raw_file="${3:-}"
  local filter_cmd
  case "$agent" in
    grok)
      filter_cmd="$(_vetcoders_agent_stream_filter_cmd "$agent" "$raw_file")" || return 1
      # Keep raw transcript when requested (tee is inside the filter via --raw-file).
      printf 'set -o pipefail; { %s; } 2>&1 | %s\n' "$cmd" "$filter_cmd"
      ;;
    *)
      printf '%s\n' "$cmd"
      ;;
  esac
}

# Fresh provider session (no native --resume). Bare resume without --session
# always takes this path; the AICX pack is continuity, not a session picker.
_vetcoders_fresh_session_command() {
  local tool="$1"
  local prompt="${2:-}"
  local mode="${3:-interactive}"
  local quoted_prompt=""
  if [[ -n "$prompt" ]]; then
    quoted_prompt="$(_vetcoders_shell_quote "$prompt")"
  else
    quoted_prompt="$(_vetcoders_shell_quote "Continue from the AICX multi-agent continuity pack.")"
  fi

  case "$tool" in
    claude)
      if [[ "$mode" == headless ]]; then
        printf 'claude --print --dangerously-skip-permissions %s\n' "$quoted_prompt"
      else
        printf 'claude %s\n' "$quoted_prompt"
      fi
      ;;
    codex)
      if [[ "$mode" == headless ]]; then
        printf 'codex exec --dangerously-bypass-approvals-and-sandbox %s\n' "$quoted_prompt"
      else
        printf 'codex %s\n' "$quoted_prompt"
      fi
      ;;
    agy)
      if [[ "$mode" == headless ]]; then
        printf 'agy --dangerously-skip-permissions --print %s\n' "$quoted_prompt"
      else
        printf 'agy --prompt-interactive %s\n' "$quoted_prompt"
      fi
      ;;
    junie)
      printf 'junie --task=%s --project=. --skip-update-check\n' "$quoted_prompt"
      ;;
    grok)
      # Interactive: positional PROMPT into the TUI (stays open).
      # Headless: --single is one-shot stdout (fleet / await / baton-pass only).
      if [[ "$mode" == headless ]]; then
        printf 'grok --cwd . --permission-mode bypassPermissions --no-alt-screen --output-format streaming-json --single %s\n' "$quoted_prompt"
      else
        printf 'grok --cwd . --permission-mode bypassPermissions --no-alt-screen %s\n' "$quoted_prompt"
      fi
      ;;
    cursor)
      # cursor-agent: `-p` reads the prompt positionally. Bypass flags come
      # only from the shared Cursor probe (7597d881) — no hardcoded --force/--trust.
      local cursor_perm_flags=""
      cursor_perm_flags="$(_vetcoders_cursor_permission_flags)" || {
        echo "cursor-agent capability probe refused --force/--trust (Cursor 7597d881 required; no silent downgrade)." >&2
        return 1
      }
      if [[ "$mode" == headless ]]; then
        printf 'cursor-agent -p --output-format stream-json %s %s\n' "$cursor_perm_flags" "$quoted_prompt"
      else
        printf 'cursor-agent %s %s\n' "$cursor_perm_flags" "$quoted_prompt"
      fi
      ;;
    *)
      echo "Unknown agent for fresh resume session: $tool" >&2
      return 1
      ;;
  esac
}

_vetcoders_launch_tracked_resume() {
  local tool="$1"
  local agent_session_id="$2"
  local prompt_text="$3"
  local model="${4:-}"
  local root_dir source_dir
  local -a core_args
  root_dir="${_vetcoders_contract_root:-$(_vetcoders_repo_root)}"
  source_dir="$(_vetcoders_core_source_dir)" || {
    echo "Tracked resume refused: Vibecrafted core is unavailable." >&2
    return 1
  }
  [[ -n "$prompt_text" ]] || {
    echo "Tracked resume requires explicit input or an AICX continuity pack." >&2
    return 1
  }

  if [[ -n "$agent_session_id" ]]; then
    core_args=(
      resume-session "$tool"
      --agent-session-id "$agent_session_id"
      --prompt-stdin
      --root "$root_dir"
      --source-dir "$source_dir"
    )
    [[ -n "$model" ]] && core_args+=(--model "$model")
    printf '%s' "$prompt_text" | _vetcoders_run_core_cli "${core_args[@]}"
    return $?
  fi

  core_args=(
    workflow "$tool"
    --prompt-stdin
    --runtime headless
    --root "$root_dir"
    --source-dir "$source_dir"
    --mode resume-new-session
  )
  [[ -n "$model" ]] && core_args+=(--model "$model")
  printf '%s' "$prompt_text" | _vetcoders_run_core_cli "${core_args[@]}"
}

_vetcoders_looks_like_run_id() {
  case "${1%%-*}" in
    work|impl|wflw|rsme|marb|just|scaf|rese|revi|plan|ship|loop|init|hydr|deco|folw|prun|trus|ownr|polr|audt|canr|delg|intn|part|relz|wflo|guar) ;;
    *) return 1 ;;
  esac
  [[ "$1" == *-* ]]
}

_vetcoders_resume_agent() {
  local tool="$1"
  shift
  # Keep the caller's public vector for a possible no-TTY handoff. The parsed
  # contract projection intentionally does not retain every public spelling.
  local -a _vetcoders_resume_public_argv=("$@")
  local _vetcoders_contract_allow_model=1
  local _vetcoders_contract_single_prompt=1
  _vetcoders_parse_contract "$@" || return 1
  case "${_vetcoders_contract_runtime:-}" in
    ""|headless|terminal|visible) ;;
    *) printf 'Unsupported resume runtime; no host adapter is available.\n' >&2; return 2 ;;
  esac
  case "${_vetcoders_contract_execution_runtime:-}" in
    ""|living-tree|local-worktrees) ;;
    *) printf 'Unsupported execution runtime: no host adapter.\n' >&2; return 2 ;;
  esac
  if [[ -n "${_vetcoders_contract_last:-}" ]]; then
    printf 'Use --session last; resume --last is retired.\n' >&2
    return 2
  fi
  if [[ -n "${_vetcoders_contract_session:-}" && -n "${_vetcoders_contract_run_id:-}" ]]; then
    printf 'Choose one identity: --session or --run-id.\n' >&2
    return 2
  fi
  if [[ -n "${_vetcoders_contract_prompt_explicit:-}${_vetcoders_contract_file_explicit:-}" && -n "${_vetcoders_contract_runtime:-}" && "$_vetcoders_contract_runtime" != headless ]]; then
    printf 'Task resume is noninteractive; use --runtime headless.\n' >&2
    return 2
  fi
  # Normalize an explicit --root ONCE, before anything changes cwd (shared
  # owner with the init family; see _vetcoders_normalize_declared_contract_root).
  _vetcoders_normalize_declared_contract_root resume || return 1
  if [[ -n "${_vetcoders_contract_help:-}" ]]; then
    echo "Resume a provider session or a stopped control-plane run." >&2
    echo "  vibecrafted resume ${tool} --session <provider-uuid>" >&2
    echo "  vibecrafted resume ${tool} --run-id <work-...>" >&2
    echo "  vibecrafted resume ${tool} --session current|last" >&2
    return 0
  fi
  if [[ -n "${_vetcoders_contract_execution_runtime:-}${_vetcoders_contract_worktree:-}" && -n "${_vetcoders_contract_prompt_explicit:-}${_vetcoders_contract_file_explicit:-}" ]]; then
    printf 'Noninteractive resume preserves its checkout; execution/worktree overrides require fork.\n' >&2
    return 2
  fi
  if [[ -n "${_vetcoders_contract_run_id:-}" || -n "${_vetcoders_contract_last:-}" ]] && [[ -n "${_vetcoders_contract_prompt_explicit:-}${_vetcoders_contract_file_explicit:-}" ]]; then
    if [[ -n "${_vetcoders_contract_session:-}" ]]; then
      echo "--session and --run-id/--last cannot be combined. Use one identity." >&2
      return 1
    fi
    local core_args=(
      "$tool" resume
    )
    [[ -n "${_vetcoders_contract_run_id:-}" ]] && core_args+=(--run-id "$_vetcoders_contract_run_id")
    [[ -n "${_vetcoders_contract_last:-}" ]] && core_args+=(--last)
    [[ -n "${_vetcoders_contract_prompt:-}" ]] && core_args+=(--prompt-stdin)
    [[ -n "${_vetcoders_contract_model:-}" ]] && core_args+=(--model "$_vetcoders_contract_model")
    [[ -n "${_vetcoders_contract_base:-}" ]] && core_args+=(--base "$_vetcoders_contract_base")
    [[ -n "${_vetcoders_contract_file:-}" ]] && core_args+=(--file "$_vetcoders_contract_file")
    [[ -n "${_vetcoders_contract_root:-}" ]] && core_args+=(--root "$_vetcoders_contract_root")
    if [[ -n "${_vetcoders_contract_prompt:-}" ]]; then
      printf '%s' "$_vetcoders_contract_prompt" | _vetcoders_run_core_cli "${core_args[@]}"
    else
      _vetcoders_run_core_cli "${core_args[@]}"
    fi
    return $?
  fi
  if [[ -n "${_vetcoders_contract_session:-}" ]] && _vetcoders_looks_like_run_id "$_vetcoders_contract_session"; then
    echo "That is a control-plane run id, not a provider session: ${_vetcoders_contract_session}" >&2
    echo "  Use: vibecrafted resume ${tool} --run-id ${_vetcoders_contract_session}" >&2
    echo "  Or:  vibecrafted resume ${tool} --run-id ${_vetcoders_contract_session}" >&2
    return 1
  fi
  # --fork-session maps onto claude's verified `--resume … --fork-session`
  # compose (continuity capabilities kernel); other providers have no proven
  # equivalent, so anything else fails closed instead of dropping the flag.
  if [[ -n "${_vetcoders_contract_fork_session:-}" && "$tool" != claude ]]; then
    printf -- '--fork-session is only supported for claude resume (no verified equivalent for %s).\n' "$tool" >&2
    return 1
  fi
  # Positional form: `vc-resume <agent> <session_id> [prompt words...]`.
  # Without --session the shared parser routes positionals into tail/prompt.
  # Promote the first tail token only when it looks like a session id (not a
  # free-form prompt word).
  if [[ -z "$_vetcoders_contract_session" && -n "$_vetcoders_contract_tail" ]]; then
    local -a _resume_positional=()
    read -r -a _resume_positional <<<"$_vetcoders_contract_tail"
    local _maybe_session="${_resume_positional[0]:-}"
    # UUIDs / long hex / codex-style tokens; short words stay as prompt text.
    # A dash-leading token is never a session id — long flags like
    # --dangerously-skip-permissions would otherwise satisfy the length regex.
    if [[ "$_maybe_session" != -* ]] &&
      [[ "$_maybe_session" =~ ^[0-9a-fA-F-]{8,}$ || "$_maybe_session" =~ ^[0-9a-zA-Z_-]{16,}$ ]]; then
      _vetcoders_contract_session="$_maybe_session"
      local _resume_rest="${_vetcoders_contract_tail#"${_resume_positional[0]}"}"
      _resume_rest="${_resume_rest# }"
      if [[ "$_vetcoders_contract_prompt" == "$_vetcoders_contract_tail" ]]; then
        _vetcoders_contract_prompt="$_resume_rest"
      fi
      _vetcoders_contract_tail="$_resume_rest"
    fi
  fi

  # Preserve the operator's input intent before an internally-generated AICX
  # pack is attached as a file. Mode selection is based on this original
  # intent, never on the transport used for continuity context.
  local resume_explicit_input=""
  if {
    [[ -n "${_vetcoders_contract_prompt_explicit:-}" ]] ||
      [[ -n "${_vetcoders_contract_file_explicit:-}" ]] ||
      [[ -n "$_vetcoders_contract_prompt" ]] ||
      [[ -n "$_vetcoders_contract_file" ]]
  }; then
    resume_explicit_input=1
  fi

  if [[ -n "${_vetcoders_contract_base:-}" && -z "${_vetcoders_contract_run_id:-}" ]]; then
    printf 'A provider session has no baseline receipt; use --run-id with --base.\n' >&2
    return 2
  fi
  # Explicit native continuation is a tracked core job. Preserve the full
  # plan (including model frontmatter); never compose a file-pointer prompt.
  if [[ -n "$_vetcoders_contract_session" && -n "$resume_explicit_input" && -z "${_vetcoders_contract_fork_session:-}" ]]; then
    local -a native_args=(resume-session "$tool" --agent-session-id "$_vetcoders_contract_session")
    [[ -z "${_vetcoders_contract_root:-}" ]] || native_args+=(--repo "$_vetcoders_contract_root")
    [[ -z "${_vetcoders_contract_model:-}" ]] || native_args+=(--model "$_vetcoders_contract_model")
    if [[ -n "$_vetcoders_contract_file" ]]; then
      [[ -z "$_vetcoders_contract_prompt" ]] || { printf 'Use one of --prompt or --file.\n' >&2; return 2; }
      _vetcoders_run_core_cli "${native_args[@]}" --prompt-file "$_vetcoders_contract_file"
    else
      printf '%s' "$_vetcoders_contract_prompt" | _vetcoders_run_core_cli "${native_args[@]}" --prompt-stdin
    fi
    return $?
  fi

  if [[ -n "${_vetcoders_contract_fork_session:-}" ]]; then
    printf 'Use vibecrafted fork with the native session identity; resume never silently becomes a fork.\n' >&2
    return 2
  fi
  if [[ -n "$resume_explicit_input" ]]; then
    local -a fresh_args=(workflow "$tool" --runtime headless)
    [[ -z "${_vetcoders_contract_root:-}" ]] || fresh_args+=(--repo "$_vetcoders_contract_root")
    [[ -z "${_vetcoders_contract_model:-}" ]] || fresh_args+=(--model "$_vetcoders_contract_model")
    [[ -z "${_vetcoders_contract_base:-}" ]] || fresh_args+=(--base "$_vetcoders_contract_base")
    [[ -z "${_vetcoders_contract_execution_runtime:-}" ]] || fresh_args+=(--execution-runtime "$_vetcoders_contract_execution_runtime")
    [[ -z "${_vetcoders_contract_worktree:-}" ]] || fresh_args+=(--worktree "$_vetcoders_contract_worktree")
    if [[ -n "$_vetcoders_contract_file" ]]; then
      _vetcoders_run_core_cli "${fresh_args[@]}" --file "$_vetcoders_contract_file"
    else
      printf '%s' "$_vetcoders_contract_prompt" | _vetcoders_run_core_cli "${fresh_args[@]}" --prompt-stdin
    fi
    return $?
  fi
  # Admission resolves the checkout before the Frame target owner is consulted.
  local resume_declared_root="${_vetcoders_contract_root:-}"

  # Missing owned interpreter/import-root is a real refusal. Prove it before
  # AICX continuity or provider composition so a broken/foreign owner cannot
  # assemble a pack and then fail.
  _vetcoders_core_python_spec >/dev/null || return 1

  [[ -z "$_vetcoders_contract_count" ]] || {
    echo "--count is only supported by vibecrafted marbles." >&2
    return 1
  }
  [[ -z "$_vetcoders_contract_depth" ]] || {
    echo "--depth is only supported by vibecrafted marbles." >&2
    return 1
  }

  # Admission is deliberately before AICX: a public no-TTY request must hand
  # the declaration to its visible terminal child before composing continuity
  # or a provider command. The child re-parses this exact public argv and does
  # each side effect exactly once.
  local runtime="${_vetcoders_contract_runtime:-terminal}"
  local _resume_terminal_admission=0
  _vetcoders_declaration_escalate_if_needed resume "$tool" \
    "${_vetcoders_resume_public_argv[@]}" || _resume_terminal_admission=$?
  case "$_resume_terminal_admission" in 0) return 0 ;; 1) return 1 ;; esac

  local aicx_fallback_mode=""
  local aicx_context_file=""
  if [[ -z "$_vetcoders_contract_session" && -z "${_vetcoders_contract_run_id:-}${_vetcoders_contract_last:-}" && -z "$resume_explicit_input" ]]; then
    # No session id: compose multi-agent continuity from AICX (48h default).
    local root_dir fallback_lines
    root_dir="${_vetcoders_contract_root:-$(_vetcoders_repo_root)}"
    printf 'No --session: assembling AICX multi-agent continuity (last %sh)...\n' \
      "${VIBECRAFTED_RESUME_AICX_HOURS:-48}" >&2
    fallback_lines="$(_vetcoders_aicx_resume_fallback "$tool" "$root_dir")" || return 1
    local line key val
    while IFS= read -r line; do
      key="${line%%=*}"
      val="${line#*=}"
      case "$key" in
        SESSION_ID)
          # Bare resume never adopts an AICX catalog id. Native attach
          # requires an explicit operator --session.
          ;;
        CONTEXT_FILE) aicx_context_file="$val" ;;
        MODE) aicx_fallback_mode="$val" ;;
      esac
    done <<<"$fallback_lines"
    if [[ -n "$aicx_context_file" ]]; then
      # Continuity pack becomes the primary file input; operator --prompt stays.
      if [[ -n "$_vetcoders_contract_file" ]]; then
        _vetcoders_contract_prompt="$(
          printf '%s\n\nAlso see operator file: %s\n' \
            "${_vetcoders_contract_prompt:-}" \
            "$_vetcoders_contract_file"
        )"
      fi
      _vetcoders_contract_file="$aicx_context_file"
      printf '  context: %s\n' "$aicx_context_file" >&2
      printf '  NEW session with continuity pack (no implicit native attach)\n' >&2
    fi
    aicx_fallback_mode="new_session"
  fi

  local resume_cmd
  local _vetcoders_interactive_skill=resume
  resume_cmd="$(_vetcoders_init_command_text "$tool" "${_vetcoders_contract_prompt:-}" "${_vetcoders_contract_policy_runtime:-local-native}" "${_vetcoders_contract_permissions:-bypass}" "${_vetcoders_contract_token_budget:-unmetered}")" || return 1
  local _resume_admission=0
  _vetcoders_enter_admitted_interactive resume "$resume_cmd" || _resume_admission=$?
  case "$_resume_admission" in 0) return 0 ;; 1) return 1 ;; esac
  resume_declared_root="$_vetcoders_contract_root"

  # Interactive resume — provider-neutral policy (adapters only change argv):
  #   bare resume → interactive → explicit or detected operator target
  #   prompt/file → tracked headless worker (handled above)
  # Prepare resolves: explicit env | in-frame | attached/current | repo-bound
  # live | single live. Multi-candidate ambiguity fails closed with a list.
  if [[ "$runtime" =~ ^(terminal|visible)$ ]]; then
    # defer-attach: preparation may CREATE this project's session, but it must
    # not hand the terminal to a foreground client yet — that client blocks
    # until detach, so the provider tab below would only be created after the
    # operator closed the window they were waiting for.
    # A declared root hands preparation to the declared-workspace owner; a
    # bare resume keeps the generic detection (explicit env | in-frame |
    # canonical bound live | create).
    _vetcoders_prepare_operator_runtime "$runtime" defer-attach "$resume_declared_root" resume || return 1
    if [[ -n "${VIBECRAFTED_OPERATOR_SESSION:-}" ]]; then
      _vetcoders_spawn_into_operator_session "$(_vetcoders_operator_face_tab "$tool")" "$resume_cmd" || return 1
      printf 'Resume launched in operator session: %s\n' "$VIBECRAFTED_OPERATOR_SESSION"
      printf '  agent:   %s\n' "$tool"
      [[ -z "$resume_declared_root" ]] || printf '  root:    %s\n' "$resume_declared_root"
      if [[ -n "$_vetcoders_contract_session" ]]; then
        printf '  session: %s\n' "$_vetcoders_contract_session"
      else
        printf '  session: (new — aicx 48h multi-agent continuity)\n'
      fi
      [[ -n "$aicx_fallback_mode" ]] && printf '  mode:    %s\n' "$aicx_fallback_mode"
      [[ -n "$aicx_context_file" ]] && printf '  pack:    %s\n' "$aicx_context_file"
      # Last act: the tab exists, so the terminal may now be handed over. This
      # blocks until the Founder detaches, which is exactly what they asked for.
      _vetcoders_attach_prepared_vc_frame_session || return $?
      return 0
    fi
  fi

  printf 'Interactive %s resume requires an admitted Frame target; no provider was downgraded to headless.\n' "$tool" >&2
  return 1

}

_vetcoders_resume_command() {
  local tool="$1"
  local session_id="$2"
  local resume_prompt="${3:-}"
  # mode: "interactive" (resume into a visible operator pane) | "headless"
  # (direct eval / async-supervisor baton-pass — no tty). Per-agent resume flags
  # differ; the headless invocations were verified against each agent's --help.
  local mode="${4:-interactive}"
  # fork_session: non-empty branches the resume into a NEW provider session id,
  # leaving the base session untouched (claude-only; callers gate other agents).
  local fork_session="${5:-}"
  local quoted_session quoted_prompt
  quoted_session="$(_vetcoders_shell_quote "$session_id")"
  if [[ -n "$resume_prompt" ]]; then
    quoted_prompt="$(_vetcoders_shell_quote "$resume_prompt")"
  fi

  case "$tool" in
    claude)
      # headless resume needs --print (+ skip-permissions); plain --resume opens
      # an interactive session and would hang under eval with no tty.
      local claude_fork_flag=""
      [[ -z "$fork_session" ]] || claude_fork_flag=" --fork-session"
      if [[ "$mode" == headless ]]; then
        if [[ -n "$resume_prompt" ]]; then
          printf 'claude --print --dangerously-skip-permissions --resume %s%s %s\n' "$quoted_session" "$claude_fork_flag" "$quoted_prompt"
        else
          printf 'claude --print --dangerously-skip-permissions --resume %s%s\n' "$quoted_session" "$claude_fork_flag"
        fi
      elif [[ -n "$resume_prompt" ]]; then
        printf 'claude --resume %s%s %s\n' "$quoted_session" "$claude_fork_flag" "$quoted_prompt"
      else
        printf 'claude --resume %s%s\n' "$quoted_session" "$claude_fork_flag"
      fi
      ;;
    codex)
      # headless = `codex exec resume` (non-interactive); `codex resume` is the
      # interactive picker and cannot run under a pipe.
      if [[ "$mode" == headless ]]; then
        if [[ -n "$resume_prompt" ]]; then
          printf 'codex exec --dangerously-bypass-approvals-and-sandbox resume %s %s\n' "$quoted_session" "$quoted_prompt"
        else
          printf 'codex exec --dangerously-bypass-approvals-and-sandbox resume %s\n' "$quoted_session"
        fi
      elif [[ -n "$resume_prompt" ]]; then
        printf 'codex resume %s %s\n' "$quoted_session" "$quoted_prompt"
      else
        printf 'codex resume %s\n' "$quoted_session"
      fi
      ;;
    gemini)
      # NOTE: gemini --resume takes an index or "latest", NOT a session UUID;
      # resuming a specific session by id is a known gap (use --session-file for
      # that). Best-effort: -p makes it headless.
      if [[ "$mode" == headless ]]; then
        if [[ -n "$resume_prompt" ]]; then
          printf 'gemini --approval-mode yolo --resume %s -p %s\n' "$quoted_session" "$quoted_prompt"
        else
          printf 'gemini --approval-mode yolo --resume %s -p ""\n' "$quoted_session"
        fi
      elif [[ -n "$resume_prompt" ]]; then
        printf 'gemini --resume %s %s\n' "$quoted_session" "$quoted_prompt"
      else
        printf 'gemini --resume %s\n' "$quoted_session"
      fi
      ;;
    agy)
      # agy resumes by --conversation <id>; headless needs --print <prompt>.
      # Since agy 1.1 --print takes the prompt as its VALUE (Go flags) and
      # print mode reads no stdin — flags first, prompt as the flag value.
      if [[ "$mode" == headless ]]; then
        if [[ -n "$resume_prompt" ]]; then
          printf 'agy --dangerously-skip-permissions --conversation %s --print %s\n' "$quoted_session" "$quoted_prompt"
        else
          printf 'agy --dangerously-skip-permissions --conversation %s --print "Continue."\n' "$quoted_session"
        fi
      elif [[ -n "$resume_prompt" ]]; then
        printf 'agy --conversation %s --prompt-interactive %s\n' "$quoted_session" "$quoted_prompt"
      else
        printf 'agy --conversation %s\n' "$quoted_session"
      fi
      ;;
    junie)
      # junie is non-interactive by construction (runs a task and exits); the
      # same command serves both modes.
      if [[ -n "$resume_prompt" ]]; then
        printf 'junie --session-id=%s --resume --task=%s --project=. --skip-update-check\n' "$quoted_session" "$quoted_prompt"
      else
        printf 'junie --session-id=%s --resume --project=. --skip-update-check\n' "$quoted_session"
      fi
      ;;
    grok)
      # NEVER pass --restore-code: it checks out the original session's commit
      # and would clobber the working tree.
      # Interactive: --resume into TUI (optional seed prompt as positional).
      # Headless: --single + streaming-json for fleet/await transcript parse.
      if [[ "$mode" == headless ]]; then
        if [[ -n "$resume_prompt" ]]; then
          printf 'grok --resume %s --cwd . --permission-mode bypassPermissions --no-alt-screen --output-format streaming-json --single %s\n' "$quoted_session" "$quoted_prompt"
        else
          printf 'grok --resume %s --cwd . --permission-mode bypassPermissions --no-alt-screen --output-format streaming-json --single "Continue."\n' "$quoted_session"
        fi
      elif [[ -n "$resume_prompt" ]]; then
        printf 'grok --resume %s --cwd . --permission-mode bypassPermissions --no-alt-screen %s\n' "$quoted_session" "$quoted_prompt"
      else
        printf 'grok --resume %s --cwd . --permission-mode bypassPermissions --no-alt-screen\n' "$quoted_session"
      fi
      ;;
    *)
      echo "Unknown agent for resume: $tool" >&2
      return 1
      ;;
  esac
}

_vetcoders_agent_for_session() {
  local session_id="$1"
  [[ -n "$session_id" ]] || return 1
  local python_bin=""
  python_bin="$(_vetcoders_internal_python)"
  "$python_bin" - "$session_id" "${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/artifacts" <<'PY'
import json
import pathlib
import sys

session_id, artifacts_root = sys.argv[1:3]
root = pathlib.Path(artifacts_root)
if not root.is_dir():
    raise SystemExit(1)

matches = []
for meta_path in root.rglob("*.meta.json"):
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    if payload.get("session_id") != session_id:
        continue
    agent = payload.get("agent")
    if agent:
        try:
            mtime = meta_path.stat().st_mtime
        except OSError:
            mtime = 0
        matches.append((mtime, agent))

if not matches:
    raise SystemExit(1)
print(sorted(matches)[-1][1])
PY
}

# Real resume helper used by deck cmd_resume via _run_helper.
# NEVER `command vibecrafted resume` here — that re-enters Python lifecycle →
# deck → this function forever (fork bomb, 2026-07-28).
# Leading --help must not enter resume parsers (audit: accidental control runs).
vc-resume() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    command vibecrafted resume --help
    return $?
  fi
  local tool="${1:-}"
  [[ -n "$tool" ]] || {
    echo "Usage: vc-resume <claude|codex|agy|junie|grok|cursor> [<session_id>] [prompt ...] | --session <session_id> [--prompt <text>] [--file <path>]" >&2
    echo "  Without --session: NEW interactive session + AICX continuity pack (last ${VIBECRAFTED_RESUME_AICX_HOURS:-48}h). Never native attach." >&2
    return 1
  }
  if [[ "$tool" == "--session" ]]; then
    _vetcoders_parse_contract "$@" || return 1
    tool="$(_vetcoders_agent_for_session "$_vetcoders_contract_session")" || {
      echo "Could not infer agent for session: $_vetcoders_contract_session" >&2
      echo "Usage: vc-resume <claude|codex|agy|junie|grok|cursor> --session $_vetcoders_contract_session" >&2
      return 1
    }
  else
    shift || true
  fi
  _vetcoders_resume_agent "$tool" "$@"
}

codex-marbles() { _vetcoders_marbles codex "$@"; }
claude-marbles() { _vetcoders_marbles claude "$@"; }
gemini-marbles() { _vetcoders_marbles gemini "$@"; }
agy-marbles() { _vetcoders_marbles agy "$@"; }
junie-marbles() { _vetcoders_marbles junie "$@"; }
grok-marbles() { _vetcoders_marbles grok "$@"; }

# Marbles control subcommands
marbles-pause()   { local s; s="$(_vetcoders_spawn_script claude "marbles_ctl.sh")" && bash "$s" pause "$@"; }
marbles-stop()    { local s; s="$(_vetcoders_spawn_script claude "marbles_ctl.sh")" && bash "$s" stop "$@"; }
marbles-resume()  { local s; s="$(_vetcoders_spawn_script claude "marbles_ctl.sh")" && bash "$s" resume "$@"; }
marbles-session() { local s; s="$(_vetcoders_spawn_script claude "marbles_ctl.sh")" && bash "$s" session "$@"; }
marbles-inspect() { local s; s="$(_vetcoders_spawn_script claude "marbles_ctl.sh")" && bash "$s" inspect "$@"; }
marbles-delete()  { local s; s="$(_vetcoders_spawn_script claude "marbles_ctl.sh")" && bash "$s" delete "$@"; }
marbles-gc()      { local s; s="$(_vetcoders_spawn_script claude "marbles_ctl.sh")" && bash "$s" gc "$@"; }
