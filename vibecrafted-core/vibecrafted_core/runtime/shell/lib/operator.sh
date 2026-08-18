# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_init_runtime() {
  local runtime="${1:-terminal}"
  case "$runtime" in
    terminal|visible)
      printf '%s\n' "$runtime"
      ;;
    *)
      echo "vc-init is interactive-only: use --runtime terminal or visible." >&2
      return 1
      ;;
  esac
}

# Resume payload for the checkout init is about to open. Prints nothing when
# there is no unfinished work, and nothing at all when core is unreachable:
# init must never fail because a convenience could not be computed.
_vetcoders_init_resume_block() {
  local python_spec py import_root
  command -v _vetcoders_core_python_spec >/dev/null 2>&1 || return 0
  python_spec="$(_vetcoders_core_python_spec 2>/dev/null)" || return 0
  py="${python_spec%%$'\t'*}"
  import_root="${python_spec#*$'\t'}"
  [[ -n "$py" ]] || return 0
  if [[ -n "$import_root" ]]; then
    PYTHONPATH="$import_root${PYTHONPATH:+:$PYTHONPATH}" \
      "$py" -m vibecrafted_core.init_resume --root . 2>/dev/null || true
  else
    "$py" -m vibecrafted_core.init_resume --root . 2>/dev/null || true
  fi
}

_vetcoders_compose_init_prompt() {
  local prompt_text="${1:-}"
  local file_path="${2:-}"
  local init_prompt="/vc-init"
  local extra resume_block

  # Resume is a payload of init, not a verb the operator has to remember.
  resume_block="$(_vetcoders_init_resume_block)"
  if [[ -n "$resume_block" ]]; then
    init_prompt+=$'\n\n'
    init_prompt+="$resume_block"
  fi

  extra="$(_vetcoders_compose_input_context "$prompt_text" "$file_path")" || return 1
  if [[ -n "$extra" ]]; then
    init_prompt+=$'\n\n'
    init_prompt+="$extra"
  fi

  printf '%s' "$init_prompt"
}

_vetcoders_init_command_text() {
  local tool="$1"
  local init_prompt="$2"
  local quoted_prompt
  quoted_prompt="$(_vetcoders_shell_quote "$init_prompt")"

  case "$tool" in
    claude)
      printf 'claude --verbose --dangerously-skip-permissions %s' "$quoted_prompt"
      ;;
    codex)
      printf 'codex --dangerously-bypass-approvals-and-sandbox %s' "$quoted_prompt"
      ;;
    gemini)
      printf 'gemini -y -i %s' "$quoted_prompt"
      ;;
    agy)
      printf 'agy --dangerously-skip-permissions --add-dir . --prompt-interactive %s' "$quoted_prompt"
      ;;
    junie)
      printf 'junie --task=%s --project=. --skip-update-check --use-local-cache' "$quoted_prompt"
      ;;
    grok)
      # Interactive TUI: positional PROMPT seeds the session and stays open.
      # NEVER use --single here — that is one-shot headless (prints + exits).
      printf 'grok --cwd . --permission-mode bypassPermissions --no-alt-screen %s' "$quoted_prompt"
      ;;
    *)
      echo "Unsupported init agent: $tool" >&2
      return 1
      ;;
  esac
}

# Operator-mode launcher helpers — parallel to init helpers above.
# vc-operator is NOT a dispatchable Iter-3 worker mode; it is an
# interactive session entry point per the vc-init pattern. Invocation
# opens the operator's primary tab in vc_frame with the agent of choice
# preloaded with the /vc-operator skill prompt.

_vetcoders_operator_runtime() {
  local runtime="${1:-terminal}"
  case "$runtime" in
    terminal|visible)
      printf '%s\n' "$runtime"
      ;;
    *)
      echo "vc-operator is interactive-only: use --runtime terminal or visible." >&2
      return 1
      ;;
  esac
}

_vetcoders_compose_operator_prompt() {
  local prompt_text="${1:-}"
  local file_path="${2:-}"
  local operator_prompt="/vc-operator"
  local extra

  extra="$(_vetcoders_compose_input_context "$prompt_text" "$file_path")" || return 1
  if [[ -n "$extra" ]]; then
    operator_prompt+=$'\n\n'
    operator_prompt+="$extra"
  fi

  printf '%s' "$operator_prompt"
}

_vetcoders_operator_command_text() {
  local tool="$1"
  local operator_prompt="$2"
  local quoted_prompt
  quoted_prompt="$(_vetcoders_shell_quote "$operator_prompt")"

  case "$tool" in
    claude)
      printf 'claude --verbose --dangerously-skip-permissions %s' "$quoted_prompt"
      ;;
    codex)
      printf 'codex --dangerously-bypass-approvals-and-sandbox %s' "$quoted_prompt"
      ;;
    gemini)
      printf 'gemini -y -i %s' "$quoted_prompt"
      ;;
    agy)
      printf 'agy --dangerously-skip-permissions --add-dir . --prompt-interactive %s' "$quoted_prompt"
      ;;
    junie)
      printf 'junie --task=%s --project=. --skip-update-check --use-local-cache' "$quoted_prompt"
      ;;
    grok)
      # Same contract as vc-init: interactive TUI, not --single one-shot.
      printf 'grok --cwd . --permission-mode bypassPermissions --no-alt-screen %s' "$quoted_prompt"
      ;;
    *)
      echo "Unsupported operator agent: $tool" >&2
      return 1
      ;;
  esac
}

