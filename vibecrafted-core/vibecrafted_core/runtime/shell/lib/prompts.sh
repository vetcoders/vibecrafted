# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_prompt_file() {
  local agent="$1"
  shift
  if [[ $# -eq 0 ]]; then
    echo "Usage: ${agent}-prompt <prompt>" >&2
    return 1
  fi

  local root ts prompt_text slug prompt_file
  root="$(_vetcoders_repo_root)"
  ts="$(date +%Y%m%d_%H%M)"
  prompt_text="$*"
  slug="$(printf '%s' "$prompt_text" | tr '\n' ' ' | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//' | cut -c1-48)"
  [[ -n "$slug" ]] || slug="adhoc-prompt"

  mkdir -p "$root/.vibecrafted/tmp"
  prompt_file="$root/.vibecrafted/tmp/${ts}_${slug}_${agent}_prompt.md"
  printf '%s\n' "$prompt_text" > "$prompt_file"
  printf '%s\n' "$prompt_file"
}

_vetcoders_contract_reset() {
  _vetcoders_contract_prompt=""
  _vetcoders_contract_prompt_explicit=""
  _vetcoders_contract_file=""
  _vetcoders_contract_file_explicit=""
  _vetcoders_contract_task=""
  _vetcoders_contract_session=""
  _vetcoders_contract_run_id=""
  _vetcoders_contract_count=""
  _vetcoders_contract_depth=""
  _vetcoders_contract_runtime=""
  _vetcoders_contract_model=""
  _vetcoders_contract_policy_runtime=""
  _vetcoders_contract_permissions=""
  _vetcoders_contract_token_budget=""
  _vetcoders_contract_operator=""
  _vetcoders_contract_continuity=""
  _vetcoders_contract_parent_session=""
  _vetcoders_contract_continuity_parent=""
  _vetcoders_contract_root=""
  _vetcoders_contract_tail=""
  _vetcoders_contract_dry_run=""
  _vetcoders_contract_no_aicx=""
  _vetcoders_contract_no_context_corpus=""
  _vetcoders_contract_fork_session=""
  _vetcoders_contract_last=""
  _vetcoders_contract_help=""
  # Owned by _vetcoders_rewrite_contract_root_argv; reset here so a vector can
  # never survive into the next parse.
  _vetcoders_contract_argv=()
}

_vetcoders_append_tail() {
  local piece="${1:-}"
  [[ -n "$piece" ]] || return 0
  if [[ -n "$_vetcoders_contract_tail" ]]; then
    _vetcoders_contract_tail+=" "
  fi
  _vetcoders_contract_tail+="$piece"
}

_vetcoders_parse_contract() {
  _vetcoders_contract_reset
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -p|--prompt)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --prompt" >&2; return 1; }
        _vetcoders_contract_prompt_explicit=1
        # Greedy: everything after --prompt is the prompt text.
        # Flags must come BEFORE --prompt.
        _vetcoders_contract_prompt="$*"
        break
        ;;
      -f|--file)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --file" >&2; return 1; }
        _vetcoders_contract_file_explicit=1
        _vetcoders_contract_file="$1"
        ;;
      --task)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --task" >&2; return 1; }
        _vetcoders_contract_task="$1"
        ;;
      --no-aicx)
        _vetcoders_contract_no_aicx=1
        ;;
      --no-context-corpus)
        _vetcoders_contract_no_context_corpus=1
        ;;
      --dry-run)
        _vetcoders_contract_dry_run=1
        ;;
      --fork-session)
        _vetcoders_contract_fork_session=1
        ;;
      --session)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --session" >&2; return 1; }
        _vetcoders_contract_session="$1"
        ;;
      --run-id)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --run-id" >&2; return 1; }
        _vetcoders_contract_run_id="$1"
        ;;
      --last)
        _vetcoders_contract_last=1
        ;;
      -h|--help)
        _vetcoders_contract_help=1
        ;;
      --count)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --count" >&2; return 1; }
        _vetcoders_contract_count="$1"
        ;;
      --depth)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --depth" >&2; return 1; }
        _vetcoders_contract_depth="$1"
        ;;
      --runtime)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --runtime" >&2; return 1; }
        _vetcoders_contract_runtime="$1"
        ;;
      --model)
        if [[ -z "${_vetcoders_contract_allow_model:-}" ]]; then
          printf 'Unknown flag: %s (flags go before --prompt; use -- for literal text)\n' "$1" >&2
          return 1
        fi
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --model" >&2; return 1; }
        _vetcoders_contract_model="$1"
        ;;
      --policy-runtime)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --policy-runtime" >&2; return 1; }
        _vetcoders_contract_policy_runtime="$1"
        ;;
      --permissions)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --permissions" >&2; return 1; }
        _vetcoders_contract_permissions="$1"
        ;;
      --token-budget)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --token-budget" >&2; return 1; }
        _vetcoders_contract_token_budget="$1"
        ;;
      --operator)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --operator" >&2; return 1; }
        _vetcoders_contract_operator="$1"
        ;;
      --continuity)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --continuity" >&2; return 1; }
        _vetcoders_contract_continuity="$1"
        ;;
      --parent-session)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --parent-session" >&2; return 1; }
        _vetcoders_contract_parent_session="$1"
        ;;
      --continuity-parent)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --continuity-parent" >&2; return 1; }
        _vetcoders_contract_continuity_parent="$1"
        ;;
      --root)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --root" >&2; return 1; }
        _vetcoders_contract_root="$1"
        ;;
      --)
        shift
        while [[ $# -gt 0 ]]; do
          _vetcoders_append_tail "$1"
          shift
        done
        break
        ;;
      *)
        # Fail closed on unknown flags: a mistyped or unsupported flag must
        # never become silent prompt text (a leaked `--fork-session` once
        # launched a fresh worker whose entire job description was the flag).
        # Literal dash-leading prompt text still has the `--` escape hatch.
        if [[ "$1" == -?* ]]; then
          printf 'Unknown flag: %s (flags go before --prompt; use -- for literal text)\n' "$1" >&2
          return 1
        fi
        _vetcoders_append_tail "$1"
        ;;
    esac
    shift
  done

  if [[ -z "$_vetcoders_contract_prompt" && -n "$_vetcoders_contract_tail" ]]; then
    _vetcoders_contract_prompt="$_vetcoders_contract_tail"
  fi
}

# Rewrite the accepted `--root` VALUE inside an argument vector to a path the
# caller has ALREADY normalized, leaving every other byte — order, quoting, the
# `--` payload, opaque provider arguments — exactly where it was. The result
# lands in the global array _vetcoders_contract_argv.
#
# Why this exists (2026-09-06): a public entry that normalizes `--root` and
# then forwards the RAW vector to a child running from the new working
# directory makes the child resolve the same relative token a SECOND time,
# against a different directory. `vc-resume codex --root child` from /project
# opened /project/child and then told the child to resolve `child` again —
# /project/child/child. The sibling form `../project-b` hid this, because from
# the sibling it happens to resolve back onto itself.
#
# The scanner mirrors _vetcoders_parse_contract's own flag table on purpose and
# lives directly beneath it: a value-taking flag missing from this list would
# let a value that merely reads like `--root` (e.g. `--session --root`) shift
# the rewrite onto the wrong argument. Keep the two lists in step.
#
# Only the LAST accepted occurrence is rewritten — that is the one the parser
# keeps, so that is the one the child resolves.
#
# Portability (2026-09-06): this used to walk the vector with a hand-rolled
# 0-based numeric index (`${_vetcoders_contract_argv[index]}`,
# `((index + 1 < $#))`). Bash arrays are 0-based; zsh arrays are 1-based with
# KSH_ARRAYS off, which is the facade's default in both shells. That single
# assumption baked into the bounds math meant the scanner silently declined to
# rewrite the LAST element of the vector under zsh (`vc-resume codex --root
# child` — child) and passed all-green under bash, because bash's array base
# happened to match the arithmetic. The fix below never subscripts the array
# with a computed index at all: it walks it twice with `for ... in
# "${arr[@]}"` (base-agnostic in both shells) tracking a plain 1..N ordinal
# counter of our own, so no shell's array-base convention is ever load-bearing.
_vetcoders_rewrite_contract_root_argv() {
  local normalized_root="$1"
  shift
  _vetcoders_contract_argv=("$@")
  [[ -n "$normalized_root" ]] || return 0

  # Pass 1 (read-only): find the ordinal — the Nth argument overall, counting
  # from 1 — of the LAST accepted --root value before --prompt/-p/-- ends the
  # scan. `skip` steps over a value that follows a value-taking flag so it is
  # never itself read as a flag.
  local -i ordinal=0 root_ordinal=0 skip=0
  local _arg
  for _arg in "${_vetcoders_contract_argv[@]}"; do
    ordinal+=1
    if ((skip > 0)); then
      skip=$((skip - 1))
      continue
    fi
    case "$_arg" in
      # --prompt is greedy and `--` opens the tail: past either of them nothing
      # is a flag any more, so nothing there may be rewritten.
      -p | --prompt | --)
        break
        ;;
      --root)
        root_ordinal=$((ordinal + 1))
        skip=1
        ;;
      # Value-taking flags: step over the VALUE too, so a value that happens to
      # spell a flag is never read as one.
      -f | --file | --task | --session | --run-id | --count | --depth | \
        --runtime | --model | --policy-runtime | --permissions | \
        --token-budget | --operator | --continuity | --parent-session | \
        --continuity-parent)
        skip=1
        ;;
    esac
  done

  ((root_ordinal > 0)) || return 0

  # Pass 2: rebuild the array by ordinal, appending in order — never assigning
  # into a computed subscript — so the shell's array base never matters.
  local -a _rewritten=()
  ordinal=0
  for _arg in "${_vetcoders_contract_argv[@]}"; do
    ordinal+=1
    if ((ordinal == root_ordinal)); then
      _rewritten+=("$normalized_root")
    else
      _rewritten+=("$_arg")
    fi
  done
  _vetcoders_contract_argv=("${_rewritten[@]}")
}

# Skill launchers own model selection. Keep the shared parser fail-closed for
# init/resume/operator call sites, while allowing documented skill syntax such
# as `vibecrafted audit claude --model claude-opus-5 --file brief.md`.
_vetcoders_parse_skill_contract() {
  _vetcoders_contract_allow_model=1
  _vetcoders_parse_contract "$@"
  local status=$?
  unset _vetcoders_contract_allow_model
  return "$status"
}

# Explicit operator job text — not a positional tail and not an AICX pack.
# Bare resume stays interactive; --prompt/--file on resume send a tracked
# headless worker. Init/operator/partner keep the TTY and append extra text
# to the seed. Partner never uses this predicate to select a worker lane.
_vetcoders_argv_has_job_input() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      -p|--prompt|-f|--file|--prompt-stdin|--prompt=*|--file=*)
        return 0
        ;;
    esac
  done
  return 1
}

_vetcoders_effective_runtime() {
  if [[ -n "$_vetcoders_contract_runtime" ]]; then
    printf '%s\n' "$_vetcoders_contract_runtime"
  else
    _vetcoders_default_runtime
  fi
}

_vetcoders_require_positive_int() {
  local value="${1:-}"
  local flag_name="${2:-value}"
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || {
    echo "${flag_name} must be a positive integer." >&2
    return 1
  }
}

_vetcoders_require_file() {
  local file_path="${1:-}"
  [[ -n "$file_path" ]] || {
    echo "Missing file path." >&2
    return 1
  }
  [[ -f "$file_path" ]] || {
    echo "Input file not found: $file_path" >&2
    return 1
  }
}

_vetcoders_compose_input_context() {
  local prompt_text="${1:-}"
  local file_path="${2:-}"
  local combined="$prompt_text"

  if [[ -n "$file_path" ]]; then
    _vetcoders_require_file "$file_path" || return 1
    local abs_file
    abs_file="$(cd "$(dirname "$file_path")" && pwd)/$(basename "$file_path")"
    if [[ -n "$combined" ]]; then
      combined+=$'\n\n'
    fi
    # Pointer, never payload. Inlining the file body here put whole
    # continuity packs (session ids, paths) into agent argv — world-readable
    # in `ps`, capped by ARG_MAX, mangled on newlines. The file is the single
    # source of truth; every agent runs with file access and reads it itself.
    combined+="Primary input file: $abs_file"
    combined+=$'\n'
    combined+="Read that file in full before acting — it is the task payload, not optional context. Do not act on this pointer alone."
  fi

  printf '%s' "$combined"
}

_vetcoders_compose_skill_prompt() {
  local skill="$1"
  local prompt_text="${2:-}"
  local file_path="${3:-}"
  local base="Perform the vc-${skill} skill on this repository."
  local extra
  extra="$(_vetcoders_compose_input_context "$prompt_text" "$file_path")" || return 1
  if [[ -n "$extra" ]]; then
    base+=$'\n\n'
    base+="$extra"
  fi
  printf '%s\n' "$base"
}
