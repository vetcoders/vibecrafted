#!/usr/bin/env bash


spawn_generate_launcher() {
  local launcher="$1"
  local meta_path="$2"
  local report_path="$3"
  local transcript_path="$4"
  local common_path="$5"
  local command="$6"
  local pre_hook="${7:-}"
  local success_hook="${8:-}"
  local failure_hook="${9:-}"

  [[ -n "$launcher" ]] || spawn_die "Missing launcher path."
  [[ -f "$common_path" ]] || spawn_die "common.sh not found: $common_path"
  [[ -n "$command" ]] || spawn_die "Missing command payload for launcher."

  local q_meta q_report q_transcript q_common q_ulimits q_cmd q_python
  local q_root q_agent q_model q_prompt_id q_run_id q_run_lock q_loop_nr q_skill_code
  local q_skill_name q_operator_session q_spawn_direction q_marbles_tab q_marbles_watcher
  q_meta="$(spawn_shell_quote "$meta_path")"
  q_report="$(spawn_shell_quote "$report_path")"
  q_transcript="$(spawn_shell_quote "$transcript_path")"
  q_common="$(spawn_shell_quote "$common_path")"
  q_ulimits="$(spawn_shell_quote "$(dirname "$common_path")/lib/ulimits.sh")"
  q_cmd="$(spawn_shell_quote "$command")"
  q_python="$(spawn_shell_quote "$(command -v "$(spawn_python_bin)")")"
  q_root="$(spawn_shell_quote "${SPAWN_ROOT:-}")"
  q_agent="$(spawn_shell_quote "${SPAWN_AGENT:-}")"
  q_model="$(spawn_shell_quote "$(spawn_read_meta_field "$meta_path" "model")")"
  q_prompt_id="$(spawn_shell_quote "${SPAWN_PROMPT_ID:-}")"
  q_run_id="$(spawn_shell_quote "${SPAWN_RUN_ID:-}")"
  q_run_lock="$(spawn_shell_quote "${SPAWN_RUN_LOCK:-}")"
  q_loop_nr="$(spawn_shell_quote "${SPAWN_LOOP_NR:-${VIBECRAFTED_LOOP_NR:-0}}")"
  q_skill_code="$(spawn_shell_quote "${SPAWN_SKILL_CODE:-}")"
  q_skill_name="$(spawn_shell_quote "${SPAWN_SKILL_NAME:-${VIBECRAFTED_SKILL_NAME:-}}")"
  q_operator_session="$(spawn_shell_quote "${VIBECRAFTED_OPERATOR_SESSION:-}")"
  q_spawn_direction="$(spawn_shell_quote "${VIBECRAFTED_VC_FRAME_SPAWN_DIRECTION:-}")"
  q_marbles_tab="$(spawn_shell_quote "${VIBECRAFTED_MARBLES_TAB_NAME:-}")"
  q_marbles_watcher="$(spawn_shell_quote "${VIBECRAFTED_MARBLES_WATCHER:-}")"

  cat > "$launcher" <<EOF_LAUNCH
#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=lib/ulimits.sh
source $q_ulimits
vc_raise_launcher_limits
source $q_common

meta=$q_meta
report=$q_report
transcript=$q_transcript
SPAWN_CMD=$q_cmd
export SPAWN_ROOT=$q_root
export SPAWN_AGENT=$q_agent
export SPAWN_MODEL=$q_model
export SPAWN_PROMPT_ID=$q_prompt_id
export SPAWN_RUN_ID=$q_run_id
export SPAWN_RUN_LOCK=$q_run_lock
export SPAWN_LOOP_NR=$q_loop_nr
export SPAWN_SKILL_CODE=$q_skill_code
export SPAWN_SKILL_NAME=$q_skill_name
export VIBECRAFTED_RUN_ID=$q_run_id
export VIBECRAFTED_RUN_LOCK=$q_run_lock
export VIBECRAFTED_SKILL_CODE=$q_skill_code
export VIBECRAFTED_SKILL_NAME=\${VIBECRAFTED_SKILL_NAME:-$q_skill_name}
export VIBECRAFTED_OPERATOR_SESSION=\${VIBECRAFTED_OPERATOR_SESSION:-$q_operator_session}
export VIBECRAFTED_VC_FRAME_SPAWN_DIRECTION=\${VIBECRAFTED_VC_FRAME_SPAWN_DIRECTION:-$q_spawn_direction}
export VIBECRAFTED_MARBLES_TAB_NAME=\${VIBECRAFTED_MARBLES_TAB_NAME:-$q_marbles_tab}
export VIBECRAFTED_MARBLES_WATCHER=\${VIBECRAFTED_MARBLES_WATCHER:-$q_marbles_watcher}
export VIBECRAFTED_PYTHON=\${VIBECRAFTED_PYTHON:-$q_python}
startup_watch_pid=""

# Write this launcher's PID into meta.json so the watcher heartbeat and the
# spawn-time GC can validate liveness via kill -0. Dead launcher_pid = ghost.
spawn_update_meta_pid "\$meta" \$\$

rm -f "\$transcript" "\$report"
spawn_write_frontmatter "\$transcript" "\$SPAWN_AGENT" "\${SPAWN_MODEL:-unknown}" "transcript"
# Machine identity exists before the worker starts. The template marker keeps
# an untouched file equivalent to report_missing; only the worker may turn its
# explicit finalized/claim fields into self-attested settlement evidence.
spawn_python_module vibecrafted_core.spawn prepare-report \
  "\$report" \
  "\$SPAWN_RUN_ID" \
  "\$SPAWN_AGENT" \
  "\${SPAWN_SKILL_CODE:-unknown}"
if [[ -n "\${SPAWN_ROOT:-}" ]]; then
  cd "\$SPAWN_ROOT"
fi
EOF_LAUNCH

if [[ -n "$pre_hook" ]]; then
    printf '%s\n' "$pre_hook" >> "$launcher"
  fi

  cat >> "$launcher" <<'EOF_LAUNCH'
spawn_export_frontier_sidecars
export PATH="${PATH:-/usr/local/bin:/usr/bin:/bin}"
spawn_prepend_agent_tool_paths
if [[ "${SPAWN_SKILL_NAME:-}" == "research" || "${SPAWN_SKILL_CODE:-}" == "rsch" || "${VIBECRAFTED_RESEARCH_MODE:-0}" == "1" ]]; then
  export VIBECRAFTED_RESEARCH_MODE=1
  export VIBECRAFTED_NO_GIT_WRITES=1
  if [[ "${VIBECRAFTED_ALLOW_RESEARCH_GIT_WRITE:-0}" != "1" ]] && command -v git >/dev/null 2>&1; then
    export VIBECRAFTED_REAL_GIT="$(command -v git)"
    git_guard_dir="${TMPDIR:-/tmp}/vibecrafted-research-git-guard-${SPAWN_RUN_ID:-research}-$$"
    mkdir -p "$git_guard_dir"
    cat > "$git_guard_dir/git" <<'EOF_GIT_GUARD'
#!/usr/bin/env bash
set -euo pipefail

subcmd=""
args=("$@")
i=0
while (( i < ${#args[@]} )); do
  arg="${args[$i]}"
  case "$arg" in
    -C|-c|--git-dir|--work-tree|--namespace|--exec-path|--super-prefix)
      (( i += 2 ))
      ;;
    --git-dir=*|--work-tree=*|--namespace=*|--exec-path=*|--super-prefix=*|-c*)
      (( i += 1 ))
      ;;
    --no-pager|--paginate|--bare|--no-replace-objects|--literal-pathspecs|--glob-pathspecs|--noglob-pathspecs|--icase-pathspecs)
      (( i += 1 ))
      ;;
    --*)
      (( i += 1 ))
      ;;
    -*)
      (( i += 1 ))
      ;;
    *)
      subcmd="$arg"
      break
      ;;
  esac
done

case "$subcmd" in
  add|am|apply|bisect|branch|checkout|cherry-pick|clean|commit|fetch|merge|mv|pull|push|rebase|reset|restore|revert|rm|stash|submodule|switch|tag|update-index|worktree)
    printf 'vibecrafted research mode blocks git write operation: git %s\n' "$subcmd" >&2
    printf 'Write findings to the research report instead of mutating the source repo.\n' >&2
    exit 126
    ;;
esac

exec "${VIBECRAFTED_REAL_GIT:-/usr/bin/git}" "$@"
EOF_GIT_GUARD
    chmod +x "$git_guard_dir/git"
    export PATH="$git_guard_dir:$PATH"
  fi
fi
if [[ "${VIBECRAFTED_INLINE_STARTUP_WATCH:-1}" != "0" ]]; then
  VIBECRAFTED_STARTUP_WATCH_ECHO=0 spawn_watch_startup "$meta" "$transcript" "$report" &
  startup_watch_pid=$!
fi
# Lifecycle truth: the spawner wrote "launching"; the launcher owns the run
# from here, so the agent command starting is the launching->running edge.
spawn_mark_meta_running "$meta"
command_status=0
bash -c "$SPAWN_CMD" || command_status=$?
report_template_untouched=0
if spawn_report_template_untouched "$report"; then
  report_template_untouched=1
  last_message="${transcript%.log}.last-message.md"
  if [[ -s "$last_message" ]] &&
    [[ "${VIBECRAFTED_RESEARCH_MODE:-0}" != "1" ]] &&
    [[ "${SPAWN_SKILL_NAME:-}" != "research" ]] &&
    [[ "${SPAWN_SKILL_CODE:-}" != "rsch" ]]; then
    # Provider commands write their final message before returning. Preserve
    # the launcher-owned identity shell and attach that message as worker
    # evidence; finalization will stamp the terminal status without losing IDs.
    printf '\n' >> "$report"
    cat "$last_message" >> "$report"
    report_template_untouched=0
  fi
fi
# Research requires a first-class worker report. Preserve its established
# exit-65 contract even though the launcher now materializes an identity shell.
if [[ "$command_status" -eq 0 && "$report_template_untouched" -eq 1 ]] && {
  [[ "${VIBECRAFTED_RESEARCH_MODE:-0}" == "1" ]] ||
  [[ "${SPAWN_SKILL_NAME:-}" == "research" ]] ||
  [[ "${SPAWN_SKILL_CODE:-}" == "rsch" ]]
}; then
  command_status=65
fi
# Provider hooks use file presence as their fallback gate. Remove only a
# provably untouched launcher shell; an available final message was attached
# above and therefore remains worker evidence.
if [[ "$report_template_untouched" -eq 1 ]]; then
  rm -f "$report"
fi
if [[ "$command_status" -eq 0 ]]; then
  spawn_finish_meta "$meta" "completed" "0"
EOF_LAUNCH

  if [[ -n "$success_hook" ]]; then
    printf '%s\n' "$success_hook" >> "$launcher"
  fi
  cat >> "$launcher" <<'EOF_LAUNCH'
  # Final artifact closure may canonicalize the filename. Carry the returned
  # regular path forward; the announced path is now only a compatibility symlink.
  meta="$(spawn_finalize_artifacts "$meta" "$report" "$transcript")"
  if [[ -n "$startup_watch_pid" ]]; then
    wait "$startup_watch_pid" 2>/dev/null || true
  fi
  # Canonical evidence is closed before terminal-run residue is reaped.
  spawn_reap_run
else
  exit_code=$command_status
  spawn_finish_meta "$meta" "failed" "$exit_code"
EOF_LAUNCH

  if [[ -n "$failure_hook" ]]; then
    printf '%s\n' "$failure_hook" >> "$launcher"
  fi
  cat >> "$launcher" <<'EOF_LAUNCH'
  # Final artifact closure may canonicalize the filename. Carry the returned
  # regular path forward; the announced path is now only a compatibility symlink.
  meta="$(spawn_finalize_artifacts "$meta" "$report" "$transcript")"
  if [[ -n "$startup_watch_pid" ]]; then
    wait "$startup_watch_pid" 2>/dev/null || true
  fi
  # Canonical evidence is closed before terminal-run residue is reaped.
  spawn_reap_run
  exit "$exit_code"
fi
EOF_LAUNCH

  if ! spawn_check_shell_syntax "$launcher" "generated launcher"; then
    spawn_settle_early_failure "generated launcher has invalid shell syntax" || true
    if [[ -f "$meta_path" ]]; then
      spawn_finish_meta "$meta_path" "failed" "1" 2>/dev/null || true
    fi
    spawn_die "Generated launcher has invalid shell syntax: $launcher"
  fi
}

spawn_launch_headless() {
  local launcher="$1"
  [[ -x "$launcher" ]] || spawn_die "Launcher is not executable: $launcher"
  # Detach into a NEW SESSION (setsid), not just background+nohup. A run dispatched
  # from a GUI app (Pensieve) dies ~2s after spawn because nohup+& keeps the child
  # in the parent's process group/session — when the app's Process teardown reaps
  # that group, the "detached" run is killed before it writes a transcript.
  # macOS has no setsid(1); use Python's start_new_session (posix setsid) for a
  # portable true-detach, with stdio fully off the parent's (possibly piped) fds.
  # Fall back to nohup+& only where the resolved runtime python is absent.
  local launcher_pid=""
  local launcher_python=""
  launcher_python="$(spawn_python_bin)"
  if command -v "$launcher_python" >/dev/null 2>&1; then
    launcher_pid="$(VC_LAUNCHER="$launcher" "$launcher_python" - <<'PY'
import os, subprocess
proc = subprocess.Popen(
    [os.environ["VC_LAUNCHER"]],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
    close_fds=True,
)
print(proc.pid)
PY
)"
  fi
  if [[ -z "$launcher_pid" ]]; then
    nohup "$launcher" >/dev/null 2>&1 &
    launcher_pid=$!
  fi
  if [[ -n "$launcher_pid" ]]; then
    if [[ -n "${SPAWN_META:-}" ]]; then
      spawn_update_meta_pid "$SPAWN_META" "$launcher_pid"
    fi
    if [[ -n "${SPAWN_RUN_ID:-}" ]]; then
      local canonical_meta=""
      canonical_meta="$(spawn_runtime_meta_path "$SPAWN_RUN_ID" 2>/dev/null || true)"
      if [[ -n "$canonical_meta" ]]; then
        spawn_update_meta_pid "$canonical_meta" "$launcher_pid"
      fi
    fi
  fi
  printf 'Spawned headless launcher (pid=%s): %s\n' "$launcher_pid" "$launcher"
}

spawn_launch() {
  local launcher="$1"
  local runtime="${2:-headless}"
  local dry_run="${3:-0}"
  local pane_name="${4:-$(basename "$launcher" .sh)}"

  pane_name="$(printf '%s' "$pane_name" | tr ' ' '-' | tr -cs '[:alnum:]._-' '-')"
  pane_name="${pane_name#-}"
  pane_name="${pane_name%-}"
  [[ -n "$pane_name" ]] || pane_name="agent"

  # G7: always resolve the worker host session (per-project / override). Do not
  # keep ambient VIBECRAFTED_OPERATOR_SESSION from the human seat — that is what
  # dumped worker tabs into the operator interactive session.
  local discovered_session=""
  discovered_session="$(spawn_effective_operator_session 2>/dev/null || true)"
  if [[ -n "$discovered_session" ]]; then
    export VIBECRAFTED_OPERATOR_SESSION="$discovered_session"
  fi

  if (( dry_run )); then
    printf 'Dry run mode: launcher generated only: %s\n' "$launcher"
    return 0
  fi

  case "$runtime" in
    terminal|visible)
      # G3: host-session unrecoverable failure (return 2) must NOT degrade to
      # headless — that path is how process_spawned→stalled pid_gone hid the
      # "Session not found" death. Loud fail with receipt last_error instead.
      local _pane_status=0
      local _op_status=0
      if spawn_in_vc_frame_pane "$launcher" "$pane_name"; then
        :
      else
        _pane_status=$?
        if [[ "$_pane_status" -eq 2 ]]; then
          printf 'vc-frame host session launch failed (in-pane path); not degrading to headless.\n' >&2
          return 1
        fi
        if spawn_in_operator_session "$launcher" "$pane_name"; then
          :
        else
          _op_status=$?
          if [[ "$_op_status" -eq 2 ]]; then
            printf 'vc-frame host session launch failed; not degrading to headless.\n' >&2
            return 1
          fi
          # Degrade, don't die. No vc-frame operator session exists to host a
          # visible tab (e.g. dispatched from a plain terminal, outside vc-frame).
          # AppleScript/iTerm fallback stays forbidden — but a hard failure is
          # worse UX than running detached. Fall back to headless, show a short
          # honest live tail so the operator sees real progress, then hand off to
          # observe/await. Start from inside vc-start if you want a visible tab.
          printf 'No vc-frame operator session for %s runtime — running headless instead.\n' "$runtime" >&2
          spawn_launch_headless "$launcher"
          local _vc_transcript="${SPAWN_TRANSCRIPT:-}"
          local _vc_agent="${SPAWN_AGENT:-agent}"
          local _vc_run_id="${SPAWN_RUN_ID:-${VIBECRAFTED_RUN_ID:-}}"
          local _vc_tail_secs="${VIBECRAFTED_DEGRADE_TAIL_SECONDS:-10}"
          # The live tail is for a human at an interactive terminal. Skip it when
          # stderr is not a TTY (pipes, tests, CI, nested dispatch) so automated
          # callers are not blocked for 10s.
          if [[ -n "$_vc_transcript" && -t 2 && "$_vc_tail_secs" != "0" ]]; then
            local _vc_wait=0
            while [[ ! -s "$_vc_transcript" && $_vc_wait -lt 14 ]]; do
              sleep 0.5
              _vc_wait=$((_vc_wait + 1))
            done
            printf -- '--- live agent output (%ss) ------------------------------------\n' "$_vc_tail_secs" >&2
            tail -n 40 -F "$_vc_transcript" >&2 2>/dev/null &
            local _vc_tail_pid=$!
            sleep "$_vc_tail_secs"
            kill "$_vc_tail_pid" 2>/dev/null || true
            wait "$_vc_tail_pid" 2>/dev/null || true
            printf -- '----------------------------------------------------------------\n' >&2
          fi
          printf 'Agent is still running headless. Continue to observe it:\n' >&2
          printf '  vibecrafted observe %s --run-id %s\n' "$_vc_agent" "$_vc_run_id" >&2
          printf '  vibecrafted await   %s --run-id %s\n' "$_vc_agent" "$_vc_run_id" >&2
          return 0
        fi
      fi
      ;;
    headless|background|detached)
      spawn_launch_headless "$launcher"
      ;; 
    *)
      spawn_die "Unsupported runtime '$runtime'. Use terminal or headless."
      ;;
  esac

  # Spawn probe for operator observability (fire-and-forget)
  if [[ "$runtime" == "terminal" || "$runtime" == "visible" ]]; then
    spawn_probe "${SPAWN_TRANSCRIPT:-}" 2>/dev/null || true
  fi
}

spawn_print_launch() {
  local agent="$1"
  local mode="$2"
  local runtime="${3:-headless}"
  local dry_run="${4:-0}"

  # ── 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. branded spawn output ──────────────────────────────
  local _dim='\033[2m'    _bold='\033[1m'
  local _copper='\033[38;5;173m'
  local _steel='\033[38;5;247m'
  local _reset='\033[0m'
  local _bar="${_steel}──────────────────────────────────${_reset}"

  local meta_receipt="${SPAWN_META:-—}"
  if [[ -n "${SPAWN_RUN_ID:-}" ]]; then
    local canonical_receipt=""
    canonical_receipt="$(spawn_runtime_meta_path "$SPAWN_RUN_ID" 2>/dev/null || true)"
    if [[ -n "$canonical_receipt" ]]; then
      meta_receipt="$canonical_receipt"
    fi
  fi

  printf '\n%b ⚒  𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. · %s-%s%b\n' "$_bold$_copper" "$agent" "$mode" "$_reset"
  printf '%b\n' "$_bar"
  printf '%b  plan:    %b%s%b\n'   "$_steel" "$_reset" "${SPAWN_PLAN:-—}" "$_reset"
  printf '%b  report:  %b%s%b\n'   "$_steel" "$_reset" "${SPAWN_REPORT:-—}" "$_reset"
  printf '%b  meta:    %b%s%b\n'   "$_steel" "$_reset" "$meta_receipt" "$_reset"
  printf '%b  trace:   %b%s%b\n'   "$_steel" "$_reset" "${SPAWN_TRANSCRIPT:-—}" "$_reset"
  printf '%b  runtime: %b%s%b\n'   "$_steel" "$_reset" "$runtime" "$_reset"
  printf '%b\n' "$_bar"
  if [[ "$dry_run" == "1" ]]; then
    printf '%b  Dry run: launcher generated only.%b\n\n' "$_dim" "$_reset"
  else
    printf '%b  Agent launched.%b\n\n' "$_dim" "$_reset"
  fi
}
