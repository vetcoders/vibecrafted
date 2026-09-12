# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_research_launcher_path() {
  local tool="$1"
  local prompt_file="$2"
  local root="$3"
  local run_id="$4"
  local run_lock="$5"
  local runtime="$6"
  local run_dir="$7"
  local script output launcher

  script="$(_vetcoders_spawn_script "$tool" "${tool}_spawn.sh")" || return 1
  output="$(
    env \
      VIBECRAFTED_RUN_ID="$run_id" \
      VIBECRAFTED_RUN_LOCK="$run_lock" \
      VIBECRAFTED_SKILL_CODE="rsch" \
      VIBECRAFTED_SKILL_NAME="research" \
      VIBECRAFTED_RESEARCH_MODE="1" \
      VIBECRAFTED_STORE_DIR="$run_dir" \
      VIBECRAFTED_STORE_ROOT="$root" \
      VIBECRAFTED_RESEARCH_RUN_DIR="$run_dir" \
      bash "$script" --dry-run --mode research --runtime "$runtime" --root "$root" "$prompt_file" 2>&1
  )" || {
    printf '%s\n' "$output" >&2
    return 1
  }

  launcher="$(printf '%s\n' "$output" | awk -F': ' '/Dry run mode: launcher generated only:/ {print $NF}' | tail -1)"
  [[ -n "$launcher" && -f "$launcher" ]] || {
    printf 'Could not resolve %s research launcher.\n' "$tool" >&2
    printf '%s\n' "$output" >&2
    return 1
  }
  printf '%s\n' "$launcher"
}

_vetcoders_runtime_manifest_path() {
  local candidate
  for candidate in \
    "${VIBECRAFTED_ROOT:-}" \
    "$(_vetcoders_repo_root)" \
    "${VIBECRAFTED_TOOLS_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted/tools}/vibecrafted-current"
  do
    [[ -n "$candidate" && -f "$candidate/install.toml" ]] || continue
    printf '%s/install.toml\n' "$candidate"
    return 0
  done
  return 1
}

_vetcoders_user_config_path() {
  local config_home config_path
  config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
  config_path="$config_home/vibecrafted/config.toml"
  [[ -f "$config_path" ]] || return 1
  printf '%s\n' "$config_path"
}

_vetcoders_research_config_paths() {
  local config_path manifest

  [[ -n "${VIBECRAFTED_RESEARCH_CONFIG:-}" && -f "${VIBECRAFTED_RESEARCH_CONFIG}" ]] && printf '%s\n' "${VIBECRAFTED_RESEARCH_CONFIG}"
  [[ -f "${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/config/research.yaml" ]] && printf '%s\n' "${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/config/research.yaml"

  config_path="$(_vetcoders_user_config_path 2>/dev/null || true)"
  [[ -n "$config_path" ]] && printf '%s\n' "$config_path"

  manifest="$(_vetcoders_runtime_manifest_path 2>/dev/null || true)"
  [[ -n "$manifest" ]] && printf '%s\n' "$manifest"
}

_vetcoders_python311_bin() {
  local candidate bin
  for candidate in \
    "${VIBECRAFTED_PYTHON:-}" \
    python3.14 \
    python3.13 \
    python3.12 \
    python3.11 \
    python3
  do
    [[ -n "$candidate" ]] || continue
    bin="$(command -v "$candidate" 2>/dev/null || true)"
    [[ -n "$bin" ]] || continue
    "$bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1 || continue
    printf '%s\n' "$bin"
    return 0
  done

  printf 'Vibecrafted requires Python >=3.11 on PATH; Python 3.10/3.9 launchers are unsupported because runtime TOML parsing uses tomllib.\n' >&2
  return 1
}

_vetcoders_research_agents_from_config() {
  local config_path="$1"
  local python_bin output parse_status

  python_bin="$(_vetcoders_python311_bin)" || return 2

  output="$("$python_bin" -c '
import sys
import tomllib

path = sys.argv[1]
if path.endswith((".yaml", ".yml")):
    agents = []
    in_lanes = False
    current = None
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            stripped = line.strip()
            if not raw.startswith((" ", "\t")):
                in_lanes = stripped == "lanes:"
                current = None
                if stripped.startswith("agents:") and "[" in stripped and "]" in stripped:
                    inner = stripped.split("[", 1)[1].rsplit("]", 1)[0]
                    agents.extend(part.strip().strip("\"'\''") for part in inner.split(","))
                continue
            if in_lanes and stripped.startswith("-"):
                current = {"enabled": "true"}
                body = stripped[1:].strip()
                if body.startswith("agent:"):
                    current["agent"] = body.split(":", 1)[1].strip().strip("\"'\''")
                agents.append(current)
                continue
            if in_lanes and current is not None and ":" in stripped:
                key, value = stripped.split(":", 1)
                current[key.strip()] = value.strip().strip("\"'\''")
    for item in agents:
        if isinstance(item, dict):
            if item.get("enabled", "true").lower() in {"false", "no", "off"}:
                continue
            agent = item.get("agent", "")
        else:
            agent = item
        if agent:
            print(agent)
    sys.exit(0 if agents else 3)
else:
    with open(path, "rb") as handle:
        data = tomllib.load(handle)

    agents = (
        data.get("runtime", {})
        .get("picking", {})
        .get("research", {})
        .get("default_agents", [])
    )

    printed = False
    for agent in agents:
        if isinstance(agent, str) and agent.strip():
            print(agent.strip())
            printed = True

    sys.exit(0 if printed else 3)
' "$config_path")"
  parse_status=$?
  if (( parse_status == 0 )); then
    printf '%s\n' "$output"
    return 0
  fi
  if (( parse_status == 3 )); then
    return 1
  fi
  printf 'Failed to read research agent config: %s\n' "$config_path" >&2
  # `1` is the caller's sentinel for a readable config with no lane list.
  # Parse/read failures must stay distinguishable so the selection fails closed
  # instead of silently falling through to another source.
  return 2
}

# Output contract: first line is `__source:<origin>`, remaining lines are
# agent names. The caller runs this in a subshell, so the origin of the
# selection must travel in-band — a silent pick with no provenance is exactly
# the failure mode this launcher is being civilized out of.
_vetcoders_research_agents() {
  local config_path
  local parse_status

  if [[ -n "${VIBECRAFTED_RESEARCH_AGENTS:-}" ]]; then
    printf '__source:env:VIBECRAFTED_RESEARCH_AGENTS\n'
    printf '%s\n' "${VIBECRAFTED_RESEARCH_AGENTS}" | tr ', ' '\n' | awk 'NF'
    return 0
  fi

  while IFS= read -r config_path; do
    [[ -n "$config_path" ]] || continue
    local config_agents
    if config_agents="$(_vetcoders_research_agents_from_config "$config_path")"; then
      printf '__source:%s\n' "$config_path"
      printf '%s\n' "$config_agents"
      return 0
    else
      # Capture the condition status inside `else`: an `if` with no matching
      # branch itself returns success and would erase the parser's status.
      parse_status=$?
      if (( parse_status != 1 )); then
        return "$parse_status"
      fi
    fi
  done < <(_vetcoders_research_config_paths)

  printf '__source:builtin-default\n'
  printf '%s\n' claude codex agy
}

_vetcoders_write_research_layout() {
  local layout_file="$1"
  shift
  local entry agent script

  cat > "$layout_file" <<EOF
layout {
    default_tab_template {
        pane size=1 borderless=true {
            plugin location="compact-bar"
        }
        children
        pane size=1 borderless=true {
            plugin location="status-bar"
        }
    }

    tab name="𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Research" {
        pane split_direction="vertical" {
            pane name="synthesis" size="55%" focus=true command="zsh"
            pane split_direction="horizontal" size="45%" {
EOF

  for entry in "$@"; do
    agent="${entry%%=*}"
    script="${entry#*=}"
    [[ -n "$agent" && "$agent" != "$entry" ]] || continue
    cat >> "$layout_file" <<EOF
                pane name="$agent" command="bash" {
                    args "$script"
                }
EOF
  done

  cat >> "$layout_file" <<EOF
            }
        }
    }
}
EOF
}

_vetcoders_research_session_ready() {
  # Non-blocking session discovery for research. The general
  # _vetcoders_prepare_operator_runtime may run a BLOCKING vc_frame client
  # (attach / --new-session-with-layout) inside the calling terminal — the
  # operator's shell gets swallowed and the research flow freezes until the
  # client exits. Research only ever needs an EXISTING live session to hang
  # its tab on; when none exists we degrade to headless instead.
  _vetcoders_vc_frame_bin >/dev/null 2>&1 || return 1
  if _vetcoders_in_vc_frame; then
    VIBECRAFTED_OPERATOR_SESSION="$(_vetcoders_current_vc_frame_session_name)"
    export VIBECRAFTED_OPERATOR_SESSION
    return 0
  fi
  if [[ -n "${VIBECRAFTED_OPERATOR_SESSION:-}" ]] \
    && [[ "$(_vetcoders_vc_frame_session_state "$VIBECRAFTED_OPERATOR_SESSION")" == "live" ]]; then
    return 0
  fi
  local guessed_session
  guessed_session="$(_vetcoders_guess_active_vc_frame_session 2>/dev/null || true)"
  if [[ -n "$guessed_session" ]]; then
    export VIBECRAFTED_OPERATOR_SESSION="$guessed_session"
    return 0
  fi
  return 1
}

_vetcoders_research_help() {
  cat <<'HELP'
⚒  research
─────────────────────────────────────────
Configurable triple-agent research swarm launcher.

  Usage:
    vc-research --prompt "Question to research"
    vc-research --file /path/to/plan.md
    vc-research <agent1> [agent2 agent3] --prompt "Question to research"
    vc-research <agent1> [agent2 agent3] --file /path/to/plan.md
    vc-research uno|duo|trio <agent...> --prompt "Question to research"
    vc-research uno|duo|trio <agent...> --file /path/to/plan.md

Common flags:
  -p, --prompt <text>            Inline prompt
  -f, --file <path.md>           Input file as prompt context
    --runtime <runtime>             Runtime backend (terminal|headless|visible)
    --root <path>                   Root workspace for this research run
    --synthesizer <agent>           Override synthesis agent (default: first positional agent, or YAML/default behavior)

Examples:
    vc-research --prompt "Compare API alternatives for oauth libraries"
    vc-research codex --prompt "Probe just one research lane"
    vc-research claude codex agy --synthesizer claude --file /path/to/research-plan.md
    vc-research --file /path/to/research-plan.md
    vibecrafted research --prompt "State of the art for MCP streaming"

One invocation is one full swarm. Positional agents override the YAML lane set for this run.

Agent picking policy (explicit, fail-closed):
  1. positional agents           highest priority, honored exactly as given
  2. VIBECRAFTED_RESEARCH_AGENTS env override
  3. research.yaml lanes         ~/.vibecrafted/config/research.yaml
  4. config.toml default_agents  [runtime.picking.research]
  5. builtin default             claude codex agy
The resolved lanes and their source are always printed at launch.
`uno|duo|trio <agents>` declare arity and must match the agent count exactly.
Unknown tokens abort the launch — nothing is silently rerouted to config defaults.
HELP
}

_vetcoders_research() {
  local first_arg="${1:-}"
  local inherited_run_id inherited_run_lock
  local prompt root run_id run_lock runtime run_dir prompt_file layout_file summary_file
  local session_name agent launcher cmd_file research_mode requested_research_agent lock_actor launch_label research_synthesizer
  local -a research_agents launchers launcher_entries command_entries positional_research_agents contract_args

  for _arg in "$@"; do
    case "$_arg" in
      help|-h|--help)
        _vetcoders_research_help
        return 0
        ;;
    esac
  done

  research_mode="swarm"
  requested_research_agent=""
  positional_research_agents=()
  # Explicit arity keywords. They declare how many positional agents MUST
  # follow — the operator's selection is a contract, never a hint.
  local expected_lane_count=0
  case "$first_arg" in
    uno) expected_lane_count=1; shift || true ;;
    duo) expected_lane_count=2; shift || true ;;
    trio) expected_lane_count=3; shift || true ;;
  esac
  if (( expected_lane_count > 0 )); then
    research_mode="override"
  fi

  while [[ $# -gt 0 ]]; do
    case "${1:-}" in
      gemini)
        printf 'vc-research: gemini CLI is deprecated (dead upstream). Use agy (Google Antigravity CLI) instead.\n' >&2
        return 1
        ;;
      claude|codex|agy|junie|grok|cursor)
        if [[ " ${positional_research_agents[*]:-} " == *" ${1} "* ]]; then
          printf 'vc-research: agent %s given twice.\n' "${1}" >&2
          return 1
        fi
        positional_research_agents+=("$1")
        research_mode="override"
        shift || true
        ;;
      *)
        break
        ;;
    esac
  done

  # Fail closed on any stray non-flag token. Silently routing leftovers into
  # the prompt/config-default swarm is how an explicit operator selection got
  # silently replaced once — never again.
  if [[ $# -gt 0 && "${1:0:1}" != "-" ]]; then
    printf 'vc-research: unknown agent or token %s.\n' "${1}" >&2
    printf 'Supported agents: claude codex agy junie grok cursor (gemini is deprecated - use agy).\n' >&2
    printf 'Usage: vc-research [uno|duo|trio] [agent ...] --prompt "..." | --file /path/to/plan.md\n' >&2
    return 1
  fi
  if (( expected_lane_count > 0 )) && (( ${#positional_research_agents[@]} != expected_lane_count )); then
    printf 'vc-research: %s expects exactly %d agent(s), got %d (%s).\n' \
      "$([[ $expected_lane_count == 1 ]] && echo uno || { [[ $expected_lane_count == 2 ]] && echo duo || echo trio; })" \
      "$expected_lane_count" "${#positional_research_agents[@]}" "${positional_research_agents[*]:-none}" >&2
    printf 'Supported agents: claude codex agy junie grok cursor (gemini is deprecated - use agy).\n' >&2
    return 1
  fi
  if (( ${#positional_research_agents[@]} > 3 )); then
    printf 'vc-research: at most 3 research lanes per run, got %d (%s).\n' \
      "${#positional_research_agents[@]}" "${positional_research_agents[*]}" >&2
    return 1
  fi
  requested_research_agent="${positional_research_agents[0]:-}"

  research_synthesizer=""
  contract_args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --synthesizer)
        shift
        [[ $# -gt 0 ]] || { echo "Missing value for --synthesizer" >&2; return 1; }
        _vetcoders_has_agent "$1" || {
          printf 'vc-research --synthesizer expects <claude|codex|agy|junie|grok|cursor>.\n' >&2
          return 1
        }
        research_synthesizer="$1"
        ;;
      -p|--prompt)
        contract_args+=("$@")
        break
        ;;
      *)
        contract_args+=("$1")
        ;;
    esac
    shift || true
  done

  _vetcoders_parse_contract "${contract_args[@]}" || return 1
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
  [[ -n "$_vetcoders_contract_prompt" || -n "$_vetcoders_contract_file" ]] || {
    echo "vc-research requires --prompt or --file." >&2
    return 1
  }

  prompt="$(_vetcoders_compose_research_worker_prompt "$_vetcoders_contract_prompt" "$_vetcoders_contract_file")" || return 1
  root="${_vetcoders_contract_root:-$(_vetcoders_repo_root)}"
  runtime="$(_vetcoders_effective_runtime)"

  if [[ "$runtime" =~ ^(terminal|visible)$ ]] && ! _vetcoders_research_session_ready; then
    printf 'vc-research: no live vc_frame operator session to attach the research tab to.\n' >&2
    printf 'Degrading to headless so your terminal stays yours (start one with vc-start for the shared tab).\n' >&2
    runtime="headless"
  fi

  inherited_run_id="$(_vetcoders_effective_run_id 2>/dev/null || true)"
  inherited_run_lock="$(_vetcoders_effective_run_lock 2>/dev/null || true)"
  run_id="$inherited_run_id"
  [[ -n "$run_id" ]] || run_id="$(_vetcoders_generate_run_id "rsch")"
  run_lock="$inherited_run_lock"
  if [[ -z "$run_lock" || ! -f "$run_lock" ]]; then
    lock_actor="swarm"
    [[ "$research_mode" == "override" && ${#positional_research_agents[@]} -gt 0 ]] && lock_actor="${positional_research_agents[0]}"
    run_lock="$(_vetcoders_create_run_lock "$run_id" "$lock_actor" "research" "$root")" || return 1
  fi

  run_dir="$(_vetcoders_research_run_dir "$root" "$run_id")"
  mkdir -p "$run_dir/plans" "$run_dir/reports" "$run_dir/logs" "$run_dir/tmp"
  prompt_file="$(_vetcoders_research_prompt_file "$run_dir" "$prompt")" || return 1

  research_agents=()
  local research_agents_source
  if [[ "$research_mode" == "override" ]]; then
    research_agents=("${positional_research_agents[@]}")
    research_agents_source="positional-override"
    [[ -n "$research_synthesizer" ]] || research_synthesizer="${research_agents[0]:-}"
  else
    local agents_output
    agents_output="$(_vetcoders_research_agents)" || return 1
    research_agents_source="builtin-default"
    while IFS= read -r agent; do
      case "$agent" in
        __source:*) research_agents_source="${agent#__source:}" ;;
        claude|codex|agy|junie|grok|cursor) research_agents+=("$agent") ;;
        gemini)
          printf 'vc-research: config selects gemini, but gemini CLI is deprecated (dead upstream).\n' >&2
          printf 'Fix the picking config to use agy (Google Antigravity CLI) - refusing to silently shrink the swarm.\n' >&2
          return 1
          ;;
        "") ;;
        *)
          printf 'vc-research: unsupported research agent in runtime picking config: %s\n' "$agent" >&2
          printf 'Fix the picking config - refusing to silently shrink the swarm.\n' >&2
          return 1
          ;;
      esac
    done <<< "$agents_output"
    if (( ${#research_agents[@]} == 0 )); then
      printf 'vc-research: no supported research agents configured.\n' >&2
      return 1
    fi
  fi
  # The resolved selection is ALWAYS announced with its source. A swarm that
  # launches without saying who picked its lanes is a wrong-assumption factory.
  printf 'Research lanes: %s (source: %s)\n' "${research_agents[*]}" "$research_agents_source"
  if [[ -n "$research_synthesizer" ]]; then
    export VIBECRAFTED_RESEARCH_SYNTHESIZER="$research_synthesizer"
  fi

  launchers=()
  launcher_entries=()
  for agent in "${research_agents[@]}"; do
    launcher="$(_vetcoders_research_launcher_path "$agent" "$prompt_file" "$root" "$run_id" "$run_lock" "$runtime" "$run_dir")" || return 1
    launchers+=("$launcher")
    launcher_entries+=("$agent=$launcher")
  done

  summary_file="$(_vetcoders_write_research_summary "$run_dir" "$run_id" "$root" "$prompt_file" "${launcher_entries[@]}")" || return 1

  if [[ "$runtime" =~ ^(terminal|visible)$ ]]; then
    # Session readiness was proven non-blockingly above; never call the
    # blocking operator-runtime preparer from the research dispatch path.
    local vc_frame_bin=""
    vc_frame_bin="$(_vetcoders_vc_frame_bin)" || return 1
    session_name="${VIBECRAFTED_OPERATOR_SESSION:-$(_vetcoders_operator_session_name)}"
    [[ -n "$session_name" ]] || {
      echo "Could not determine the operator vc_frame session." >&2
      return 1
    }

    layout_file="$run_dir/tmp/research.kdl"

    command_entries=()
    for entry in "${launcher_entries[@]}"; do
      agent="${entry%%=*}"
      launcher="${entry#*=}"
      cmd_file="$run_dir/tmp/${agent}_cmd.sh"
      _vetcoders_write_command_script "$cmd_file" "bash $(_vetcoders_shell_quote "$launcher")" || return 1
      command_entries+=("$agent=$cmd_file")
    done
    _vetcoders_write_research_layout "$layout_file" "${command_entries[@]}"

    # Intended exports to env for the vc_frame child process — false-positive SC2031.
    # shellcheck disable=SC2031
    export VIBECRAFTED_RUN_ID="$run_id"
    # shellcheck disable=SC2031
    export VIBECRAFTED_RUN_LOCK="$run_lock"
    # shellcheck disable=SC2031
    export VIBECRAFTED_SKILL_CODE="rsch"
    # shellcheck disable=SC2031
    export VIBECRAFTED_SKILL_NAME="research"
    # shellcheck disable=SC2031
    export VIBECRAFTED_RESEARCH_MODE="1"
    # shellcheck disable=SC2031
    export VIBECRAFTED_STORE_DIR="$run_dir"
    # shellcheck disable=SC2031
    export VIBECRAFTED_STORE_ROOT="$root"
    # shellcheck disable=SC2031
    export VIBECRAFTED_RESEARCH_RUN_DIR="$run_dir"
    local focus_flag=""
    if "$vc_frame_bin" action new-tab --help 2>&1 | command grep -q -- '--no-focus'; then
      focus_flag="--no-focus"
    fi
    "$vc_frame_bin" --session "$session_name" action new-tab \
      ${focus_flag:+"$focus_flag"} --layout "$layout_file" >/dev/null
    launch_label="Research swarm"
    [[ "$research_mode" == "override" ]] && launch_label="Research override (${research_agents[*]})"
    printf '%s launched in shared tab (run_id=%s).\n' "$launch_label" "$run_id"
    printf '  run dir: %s\n' "$run_dir"
    printf '  reports: %s\n' "$run_dir/reports"
    printf '  summary: %s\n' "$summary_file"
    _vetcoders_await "" --describe "${launchers[@]}" || true
    printf '\nAwait:\n\n'
    printf 'vc-research-await --run-id %s\n' "$run_id"
    return 0
  fi

  launch_label="Research swarm"
  [[ "$research_mode" == "override" ]] && launch_label="Research override (${research_agents[*]})"
  printf '%s prepared (run_id=%s), but runtime %s does not use the shared vc_frame layout.\n' "$launch_label" "$run_id" "$runtime"
  printf 'Run directory: %s\n' "$run_dir"
  printf 'Reports: %s\n' "$run_dir/reports"
  printf 'Summary: %s\n' "$summary_file"
  printf 'Launchers:\n'
  for entry in "${launcher_entries[@]}"; do
    agent="${entry%%=*}"
    launcher="${entry#*=}"
    printf '  %s: %s\n' "$agent" "$launcher"
  done
  printf '\nAwait:\n\n'
  printf 'vc-research-await --run-id %s\n' "$run_id"
}
