# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_dashboard_layout_name() {
  local requested="${1:-default}"
  case "$requested" in
    # The product entrypoints and vc-frame's native default must name the
    # same physical, shipped layout.  Keep the user-facing spellings, but do
    # not create a second vibecrafted.kdl (or a symlink: Runtime Packs reject
    # those) just to satisfy an alias.
    ""|default|operator|vibecrafted) printf 'operator\n' ;;
    dashboard|mc|mission-control|vc-dashboard) printf 'dashboard\n' ;;
    marbles|vc-marbles) printf 'marbles\n' ;;
    polarize|vc-polarize) printf 'polarize\n' ;;
    workflow|vc-workflow) printf 'workflow\n' ;;
    research|vc-research) printf 'research\n' ;;
    *)
      echo "Unknown dashboard layout: $requested" >&2
      # The sourced facade/parser initializes this shared result before the consuming function runs;
      # assigning a local default here would mask missing ownership initialization.
      # shellcheck disable=SC2154
      echo "Available layouts: ${_vetcoders_known_dashboard_layouts[*]}" >&2
      return 1
      ;;
  esac
}

_vetcoders_dashboard_layout_file() {
  local layout_name
  layout_name="$(_vetcoders_dashboard_layout_name "${1:-}")" || return 1
  _vetcoders_frontier_file "vc-frame/layouts/${layout_name}.kdl"
}

_vetcoders_dashboard_session_name() {
  local layout_name base_session
  _vetcoders_normalize_ambient_context
  layout_name="$(_vetcoders_dashboard_layout_name "${1:-}")" || return 1
  base_session="${VIBECRAFTED_OPERATOR_SESSION:-$(_vetcoders_operator_session_name)}"
  printf '%s\n' "$base_session"
}

_vetcoders_product_core_cli() {
  local core_dir python_bin python_dir
  local config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
  # Product preferences are canonical without changing Atuin/Starship's XDG
  # environment in the parent shell.
  if ! _vetcoders_vc_frame_developer_mode; then
    config_home="$HOME/.config"
  fi
  if [[ -n "${VIBECRAFTED_PRODUCT_CORE_CLI:-}" ]]; then
    "$VIBECRAFTED_PRODUCT_CORE_CLI" "$@"
    return $?
  fi
  # Same owned interpreter + import-root as `_vetcoders_core_python_spec` /
  # `_vetcoders_owned_python_bin`. Do not re-walk BASH_SOURCE here.
  core_dir="$(_vetcoders_owned_core_dir)" || return 1
  python_bin="$(_vetcoders_owned_python_bin)" || return 1
  python_dir=""
  [[ "$python_bin" == */* ]] && python_dir="$(dirname "$python_bin")"
  [[ -f "$core_dir/vibecrafted_core/cli.py" ]] || return 1
  PATH="${python_dir:+$python_dir:}${PATH:-}" \
    XDG_CONFIG_HOME="$config_home" \
    PYTHONPATH="$core_dir" \
    "$python_bin" -m vibecrafted_core.cli "$@"
}

_vetcoders_product_workspace_prepare() {
  local requested_root="${1:-}"
  local line key value resolved resolve_status=0
  if [[ -z "$requested_root" ]]; then
    requested_root="$(pwd -P)" || return $?
  fi
  if _vetcoders_start_is_lobby "$requested_root"; then
    # The entry lobby is a host surface, never a project registration.
    unset VIBECRAFTED_WORKSPACE_ID VIBECRAFTED_SESSION_ID VIBECRAFTED_WORKSPACE_INSTANCE_ID
    unset VIBECRAFTED_BUILD_ID VIBECRAFTED_OPERATOR_SESSION VIBECRAFTED_WORKSPACE_ROOT
    return 0
  fi
  resolved="$(
    _vetcoders_product_core_cli \
      workspace resolve --root "$requested_root" --env
  )" || resolve_status=$?
  if [[ "$resolve_status" -ne 0 || -z "$resolved" ]]; then
    [[ "$resolve_status" -ne 0 ]] || resolve_status=1
    printf "vc-start: could not resolve requested workspace root '%s' (status %s).\n" \
      "$requested_root" "$resolve_status" >&2
    printf "vc-start: clear stale VIBECRAFTED_WORKSPACE_* values or re-run from the intended root.\n" >&2
    return "$resolve_status"
  fi
  while IFS= read -r line; do
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      VIBECRAFTED_WORKSPACE_ID|VIBECRAFTED_SESSION_ID|VIBECRAFTED_WORKSPACE_INSTANCE_ID|VIBECRAFTED_BUILD_ID|VIBECRAFTED_OPERATOR_SESSION|VIBECRAFTED_WORKSPACE_ROOT)
        export "$key=$value"
        ;;
    esac
  done <<< "$resolved"
  if _vetcoders_is_legacy_operator_session_name "${VIBECRAFTED_OPERATOR_SESSION:-}"; then
    VIBECRAFTED_OPERATOR_SESSION="$(_vetcoders_operator_session_name)"
    export VIBECRAFTED_OPERATOR_SESSION
  fi
}

# ONE canonical workspace owner for both public interactive entries.
#
# `vc-start` reached the catalogue through the selected generation's CLI above
# and exported the resulting identities. Bare resume and the interactive target
# resolver did not: they recomputed a session NAME through a generic `python3`
# that cannot import vibecrafted_core, swallowed that failure, and degraded to
# the repository basename. The two entries then disagreed about the same
# project, and — because no workspace/session/instance id was ever propagated —
# `_vetcoders_record_vc_frame_attachment` returned early, so the session resume
# opened carried no binding receipt at all.
#
# Idempotent: identities already prepared by the product entry choke (or an
# explicit operator override) are left exactly as they are. Otherwise this
# prepares them through the SAME owner start uses, in the CALLER's shell, so
# the binding ids propagate instead of dying in a subshell. Failure is a real
# failure: callers must refuse before any provider/AICX side effect rather than
# silently target a session they do not own.
#
# $1 (optional): an already-normalized explicit requested root (e.g. a public
# entry's parsed `--root`). When given, it is the effective request and wins
# over any inherited VIBECRAFTED_WORKSPACE_ROOT — an ambient value from a
# parent shell is not authoritative just because it is nonempty, and it must
# never override what the caller was actually asked to target. Absent an
# explicit root, behaviour is unchanged: ambient root, else cwd.
# Optional explicit root; resume intentionally omits it.
_vetcoders_ensure_canonical_workspace_identity() {
  local requested_root="${1:-}"
  local root_dir=""
  if [[ -n "$requested_root" ]]; then
    root_dir="$requested_root"
  else
    root_dir="${VIBECRAFTED_WORKSPACE_ROOT:-}"
    [[ -n "$root_dir" && -d "$root_dir" ]] || root_dir="$(_vetcoders_effective_project_root)"
  fi
  # A cached identity only counts as proof for THIS root: three nonempty
  # fields (missing VIBECRAFTED_SESSION_ID) and no root check let a stale
  # binding for a different project stand in for the one just requested.
  if [[ -n "${VIBECRAFTED_WORKSPACE_ID:-}" \
    && -n "${VIBECRAFTED_WORKSPACE_INSTANCE_ID:-}" \
    && -n "${VIBECRAFTED_SESSION_ID:-}" \
    && -n "${VIBECRAFTED_OPERATOR_SESSION:-}" \
    && -n "${VIBECRAFTED_WORKSPACE_ROOT:-}" \
    && "${VIBECRAFTED_WORKSPACE_ROOT:-}" == "$root_dir" ]]; then
    return 0
  fi
  _vetcoders_product_workspace_prepare "$root_dir"
}

_vetcoders_control_plane_eye_prepare() {
  _vetcoders_product_core_cli server status >/dev/null 2>&1 && return 0

  # The macOS product owns a persistent LaunchAgent. Reconcile that one owner
  # instead of starting a second foreground server with hard-coded defaults.
  # Linux and Windows keep their existing non-mutating entry behavior until
  # their platform service managers have an equivalent durable owner.
  if [[ "$(uname -s 2>/dev/null || true)" == "Darwin" ]]; then
    _vetcoders_product_core_cli server service reconcile >/dev/null 2>&1 || true
  fi
  return 0
}

# One read-only admission per preparation, never per frame action/status call.
# The installer owns receipts, publication transactions and generation validation.
# This consumer only decodes its envelope and rejects a changed selected owner.
_vetcoders_product_runtime_admit() {
  local owner_root="$1" resolution_mode="${2:-admit}"
  if [[ ! -f "$owner_root/scripts/vetcoders_install.py" || -L "$owner_root/scripts/vetcoders_install.py" ]]; then
    printf 'vc-start: selected runtime resolver missing; explicit upgrade/repair required\n' >&2
    return 2
  fi
  env -u PYTHONPATH -u PYTHONHOME PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
    "$owner_root/bin/python3" -I -B - "$owner_root" "$resolution_mode" <<'PY_RUNTIME_ADMIT'
import json
import os
from pathlib import Path
import subprocess
import sys

owner = Path(sys.argv[1])
# Installed generations live at <runtime-home>/releases/<generation>. The
# invoked facade already selected this physical root; do not select via env.
if owner.parent.name != "releases":
    print("vc-start: selected shell is not an installed generation; use explicit checkout development mode or install", file=sys.stderr)
    sys.exit(2)
try:
    result = subprocess.run(
        [str(owner / "bin/python3"), "-B",
         str(owner / "scripts/vetcoders_install.py"), "runtime-resolve",
         "--runtime-home", str(owner.parent.parent), "--json"],
        capture_output=True, timeout=30, check=False,
    )
    if len(result.stdout) > 1024 * 1024:
        raise ValueError("oversized resolution envelope")
    envelope = json.loads(result.stdout)
    if not isinstance(envelope, dict) or envelope.get("schema") != "vibecrafted.runtime-resolution.v1":
        raise ValueError("invalid resolution envelope")
    state = envelope.get("status")
    reason = envelope.get("reason")
    if not isinstance(reason, str):
        raise ValueError("invalid resolution reason")
    if state in ("absent", "unusable"):
        if result.returncode != (0 if state == "absent" else 2) or envelope.get("runtime") is not None:
            raise ValueError("inconsistent resolution status")
        # The owner contract supplies a human-readable, secret-free reason.
        print(f"vc-start: installed runtime {state}: {reason[:2000]}; explicit install/repair required", file=sys.stderr)
        sys.exit(2)
    runtime = envelope.get("runtime")
    if result.returncode != 0 or state != "ready" or not isinstance(runtime, dict):
        raise ValueError("runtime resolution did not return ready")
    if runtime.get("schema") != "vibecrafted.runtime-install-result.v1":
        raise ValueError("invalid runtime result schema")
    selected = runtime.get("root")
    if not isinstance(selected, str) or not os.path.isabs(selected):
        raise ValueError("invalid selected runtime root")
    if sys.argv[2] == "active-root":
        print(os.path.realpath(selected))
    elif os.path.realpath(selected) != str(owner):
        raise ValueError("selected generation changed; reopen through the current product entry")
except (OSError, ValueError, subprocess.TimeoutExpired) as error:
    # Do not replay raw subprocess stderr/JSON (CLI errors can include argv).
    message = str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else "resolver unavailable, timed out or returned invalid JSON"
    print(f"vc-start: {message}; explicit upgrade/repair required", file=sys.stderr)
    sys.exit(2)
PY_RUNTIME_ADMIT
}

# ---------------------------------------------------------------------------
# vc-start argument contract (2026-09-09, Founder P0 "one create-only workspace
# contract from every entrypoint"). Shell `vc-start` and deck `cmd_start` both
# parse through here and then enter _vetcoders_start_entry; there is no second
# parser and nothing is forwarded to vc-frame as opaque input any more.
#
#   vc-start [<workspace>] [--repo <path>|--root <path>]   create-only start
#   vc-start resume [...]                                  deliberate re-entry
#
# Outputs (globals, read by _vetcoders_start_entry):
#   VIBECRAFTED_START_ROOT          normalized explicit repository (or unset)
#   _vetcoders_start_frame_argv     the user's non-project argv, verbatim, in
#                                   order (reserved words, the explicit name,
#                                   resume's own arguments) -- what the
#                                   terminal child is handed back
#   _vetcoders_start_workspace_name the explicit name ("" = derive from root)
#   _vetcoders_start_mode           start | resume
#
# Reserved words: `operator` and `vibecrafted` are the historical layout
# aliases and mean the default start; `resume` is the deliberate re-entry and
# takes the rest of the argv as its own. Any other bare word is the workspace
# name, validated ONCE here (exit 2), before any terminal, workspace or Frame
# side effect. vc-start owns the session name and the operator layout, so the
# Frame spellings of those (-s/--session, -n/--new-session-with-layout,
# -l/--layout, --layout-string) and every other option are refused instead of
# being smuggled to the engine, where they used to fail inside a window the
# operator had already been handed.
_vetcoders_start_reserved_word() {
  case "${1:-}" in
    operator | vibecrafted | resume) return 0 ;;
  esac
  return 1
}

# Validate a workspace (vc-frame session) name once. Frame itself refuses only
# the empty name, `.`/`..`, path separators and a Windows drive prefix
# (zellij-utils/src/sessions.rs validate_session_name); the rest are this
# product's rules so a name is never silently truncated or normalized later:
# the rail's single max length (_vetcoders_vc_frame_session_max_length), no
# leading dash (it would parse as an option), no control characters, no
# leading/trailing whitespace. Spaces inside are legal (Frame hosts such names).
# Prints the reason on stderr and returns 2.
_vetcoders_start_validate_workspace_name() {
  local name="${1-}" label="${2:-workspace name}" reason="" max_len=""
  max_len="$(_vetcoders_vc_frame_session_max_length 2>/dev/null || printf '24\n')"
  if [[ -z "$name" || -z "${name//[[:space:]]/}" ]]; then
    reason="it is empty"
  elif [[ "$name" == "." || "$name" == ".." ]]; then
    reason="'.' and '..' are not names"
  elif [[ "$name" == */* || "$name" == *\\* ]]; then
    reason="path separators are not allowed"
  elif [[ "$name" == -* ]]; then
    reason="it starts with '-' (would be read as an option)"
  elif [[ "$name" == *[[:cntrl:]]* ]]; then
    reason="control characters are not allowed"
  elif [[ "$name" == [[:space:]]* || "$name" == *[[:space:]] ]]; then
    reason="leading or trailing whitespace is not allowed"
  elif ((${#name} > max_len)); then
    reason="it is ${#name} characters long; the limit is ${max_len}"
  fi
  [[ -n "$reason" ]] || return 0
  printf 'vc-start: %s %s cannot be used: %s.\n' "$label" "$(_vetcoders_shell_quote "$name")" "$reason" >&2
  return 2
}

_vetcoders_start_prepare_arguments() {
  local raw_root="" raw_repo="" normalized_root="" arg
  # Parse-only: these survive the function so execution can call the launch-spec
  # owner once. They must not enter `_vetcoders_select_repo`.
  _vetcoders_start_contract_base=""
  _vetcoders_start_contract_execution_runtime=""
  _vetcoders_start_contract_worktree=""
  unset VIBECRAFTED_START_LAUNCH_PINNED
  unset VIBECRAFTED_START_BASELINE_SHA
  unset VIBECRAFTED_START_PARENT_ROOT
  _vetcoders_start_frame_argv=()
  _vetcoders_start_workspace_name=""
  _vetcoders_start_mode="start"
  _vetcoders_start_new_host_requested=0
  while (($#)); do
    arg="$1"
    if [[ "$arg" == --new-host ]]; then
      _vetcoders_start_new_host_requested=1
      _vetcoders_start_frame_argv+=("$arg")
      shift
      continue
    fi
    if [[ "$_vetcoders_start_mode" == resume ]]; then
      case "$arg" in
        --repo | --repo=* | --root | --root=*) ;;
        *) _vetcoders_start_frame_argv+=("$arg"); shift; continue ;;
      esac
    fi
    case "$arg" in
      --base)
        shift; [[ $# -gt 0 && -n "$1" ]] || return 2
        _vetcoders_start_contract_base="$1"
        ;;
      --execution-runtime)
        shift; [[ $# -gt 0 && -n "$1" ]] || return 2
        _vetcoders_start_contract_execution_runtime="$1"
        ;;
      --worktree)
        _vetcoders_start_contract_worktree=true
        if [[ $# -gt 1 ]] && _vetcoders_is_worktree_word "$2"; then
          shift
          _vetcoders_start_contract_worktree="$(_vetcoders_worktree_word_value "$1")"
        fi
        ;;
      --worktree=*)
        if ! _vetcoders_is_worktree_word "${arg#--worktree=}"; then
          printf 'vc-start: --worktree expects true or false, got %s.\n' \
            "$(_vetcoders_shell_quote "${arg#--worktree=}")" >&2
          return 2
        fi
        _vetcoders_start_contract_worktree="$(_vetcoders_worktree_word_value "${arg#--worktree=}")"
        ;;
      --root)
        shift
        if (($# == 0)) || [[ -z "$1" ]]; then
          printf 'vc-start: --root requires an existing directory value.\n' >&2
          return 2
        fi
        raw_root="$1"
        ;;
      --root=*)
        raw_root="${arg#--root=}"
        if [[ -z "$raw_root" ]]; then
          printf 'vc-start: --root requires an existing directory value.\n' >&2
          return 2
        fi
        ;;
      --repo)
        shift
        if (($# == 0)) || [[ -z "$1" ]]; then
          printf 'vc-start: --repo requires an existing directory value.\n' >&2
          return 2
        fi
        raw_repo="$1"
        ;;
      --repo=*)
        raw_repo="${arg#--repo=}"
        if [[ -z "$raw_repo" ]]; then
          printf 'vc-start: --repo requires an existing directory value.\n' >&2
          return 2
        fi
        ;;
      resume)
        # Repository selection remains owned here, on either side of resume.
        _vetcoders_start_mode="resume"
        _vetcoders_start_frame_argv+=("$arg")
        ;;
      operator | vibecrafted)
        # Historical layout aliases: the default start, kept in the argv the
        # terminal child is handed so an escalated call replays exactly.
        _vetcoders_start_frame_argv+=("$arg")
        ;;
      -s | --session | -s=* | --session=* | -n | --new-session-with-layout | -n=* | --new-session-with-layout=* | -l | --layout | -l=* | --layout=* | --layout-string | --layout-string=*)
        printf 'vc-start: %s is not accepted; vc-start owns the session name and the operator layout.\n' "${arg%%=*}" >&2
        printf 'Name the workspace as a bare argument: vc-start <workspace> [--repo <path>]\n' >&2
        return 2
        ;;
      --)
        printf 'vc-start: -- is not accepted; vc-start forwards nothing to vc-frame.\n' >&2
        printf 'Usage: vc-start [<workspace>] [--repo <path>] | vc-start resume\n' >&2
        return 2
        ;;
      -*)
        printf 'vc-start: unknown option %s.\n' "$(_vetcoders_shell_quote "$arg")" >&2
        printf 'Usage: vc-start [<workspace>] [--repo <path>] | vc-start resume   (--root is the legacy spelling of --repo)\n' >&2
        return 2
        ;;
      *)
        if [[ -n "$_vetcoders_start_workspace_name" ]]; then
          printf 'vc-start: one workspace name only; got %s and %s.\n' \
            "$(_vetcoders_shell_quote "$_vetcoders_start_workspace_name")" "$(_vetcoders_shell_quote "$arg")" >&2
          printf 'Usage: vc-start [<workspace>] [--repo <path>]\n' >&2
          return 2
        fi
        _vetcoders_start_validate_workspace_name "$arg" "workspace name" || return $?
        _vetcoders_start_workspace_name="$arg"
        _vetcoders_start_frame_argv+=("$arg")
        ;;
    esac
    shift
  done

  if [[ "$_vetcoders_start_new_host_requested" == 1 ]] &&
    [[ "$_vetcoders_start_mode" == resume || -n "$_vetcoders_start_workspace_name" ]]; then
    printf 'vc-start: --new-host opens an empty host; use it without a workspace name or resume.\n' >&2
    return 2
  fi

  unset VIBECRAFTED_START_ROOT
  # Same selector as every other public verb: `--repo` standard, `--root`
  # legacy, conflicting pair refused, path must already exist. Launch-spec
  # flags stay out of this call; execution owns materialization.
  normalized_root="$(_vetcoders_select_repo "vc-start" "$raw_repo" "$raw_root")" || return $?
  [[ -n "$normalized_root" ]] || return 0
  export VIBECRAFTED_START_ROOT="$normalized_root"
}

# Execution-time launch-spec owner. Called once from `_vetcoders_start_entry`
# after directory selection. Pins SHA/branch/HEAD and materializes a worktree
# only when `--base` / `--worktree true` / `--execution-runtime` asked for it.
# `--worktree false` and an empty flag stay on the selected directory.
# The resolved root is exported so terminal escalation can replay `--repo`
# of the effective checkout instead of rematerializing.
_vetcoders_start_apply_launch_spec() {
  local selected="${1:-}"
  local base="${_vetcoders_start_contract_base:-}"
  local runtime="${_vetcoders_start_contract_execution_runtime:-}"
  local worktree="${_vetcoders_start_contract_worktree:-}"
  local wants="false" python_spec py import_root payload="" prepared="" baseline="" parent=""
  if [[ "${VIBECRAFTED_START_LAUNCH_PINNED:-}" == "1" ]]; then
    return 0
  fi
  if [[ -n "$worktree" ]] && ! _vetcoders_is_worktree_word "$worktree"; then
    printf 'vc-start: --worktree expects true or false, got %s.\n' \
      "$(_vetcoders_shell_quote "$worktree")" >&2
    return 2
  fi
  if [[ -n "$worktree" && "$(_vetcoders_worktree_word_value "$worktree")" == "true" ]]; then
    wants="true"
  fi
  if [[ -z "$base" && -z "$runtime" && "$wants" != "true" ]]; then
    return 0
  fi
  python_spec="$(_vetcoders_core_python_spec)" || return 1
  py="${python_spec%%$'\t'*}"
  import_root="${python_spec#*$'\t'}"
  local -a argv=(
    "$py" -m vibecrafted_core.repo_selection
    --label vc-start
    --json
  )
  if [[ -n "$selected" ]]; then
    argv+=(--repo "$selected")
  fi
  if [[ -n "$base" ]]; then
    argv+=(--base "$base")
  fi
  if [[ -n "$runtime" ]]; then
    argv+=(--execution-runtime "$runtime")
  fi
  if [[ -n "$worktree" ]]; then
    argv+=(--worktree "$worktree")
  fi
  if [[ "$wants" == "true" || "$runtime" == "local-worktrees" ]]; then
    argv+=(--prepare-worktree)
  fi
  if [[ -n "$import_root" ]]; then
    payload="$(PYTHONPATH="$import_root${PYTHONPATH:+:$PYTHONPATH}" "${argv[@]}")" || return $?
  else
    payload="$("${argv[@]}")" || return $?
  fi
  prepared="$(printf '%s\n' "$payload" | "$py" -c 'import json,sys; print(json.load(sys.stdin).get("root") or "")')" || return 2
  baseline="$(printf '%s\n' "$payload" | "$py" -c 'import json,sys; print(json.load(sys.stdin).get("baseline_sha") or "")')" || true
  parent="$(printf '%s\n' "$payload" | "$py" -c 'import json,sys; print(json.load(sys.stdin).get("parent_root") or "")')" || true
  [[ -n "$prepared" ]] || return 2
  export VIBECRAFTED_START_ROOT="$prepared"
  export VIBECRAFTED_START_BASELINE_SHA="$baseline"
  export VIBECRAFTED_START_PARENT_ROOT="${parent:-$selected}"
  export VIBECRAFTED_START_LAUNCH_PINNED=1
}

# Open the product terminal for a start that has no visible surface here.
# $1 = the decision mode:
#   strict  -- plain start: the ONLY signals are the re-entry boundary and
#              this process's own controlling terminal. An inherited
#              VC_FRAME_*/ZELLIJ_* marker or an operator-session name proves
#              nothing about THIS process's stdin (Founder repro 2026-09-09:
#              vc-start from an agent tool inside an attached pane reached the
#              engine and died on "stdin is not a terminal" after the whole
#              preparation had run).
#   declared -- `vc-start resume`: the declaration family's shared decision
#              (_vetcoders_needs_vc_terminal_entry), unchanged by this cut.
# $2 = project root (the terminal's working directory); the rest is the argv
# the child replays.
# The terminal child we opened carries the product-owned re-entry boundary
# (marker AND owner = this generation's vibecrafted front door). A bare
# `VIBECRAFTED_TERMINAL_ENTRY=1` is inherited ancestry, not a surface: start and
# resume share one admission proof (_vetcoders_has_owned_vc_terminal_entry).
_vetcoders_start_is_owned_terminal_child() {
  command -v _vetcoders_has_owned_vc_terminal_entry >/dev/null 2>&1 &&
    _vetcoders_has_owned_vc_terminal_entry
}

_vetcoders_start_open_terminal_if_needed() {
  local mode="${1:-strict}" project_root="${2:-}"
  shift 2 || shift $#
  unset VIBECRAFTED_START_ESCALATED
  [[ "${VIBECRAFTED_PRODUCT_ENTRY_PROBE:-0}" != "1" ]] || return 0
  case "$mode" in
    declared)
      if ! command -v _vetcoders_needs_vc_terminal_entry >/dev/null 2>&1 ||
        ! _vetcoders_needs_vc_terminal_entry; then
        return 0
      fi
      ;;
    *)
      # The child we open re-enters this very entry; the owned boundary stops
      # the loop even if the host somehow fails to hand it a PTY.
      ! _vetcoders_start_is_owned_terminal_child || return 0
      # A real controlling terminal is the direct path -- and the only proof.
      [[ ! -t 0 || ! -t 1 ]] || return 0
      ;;
  esac
  local front_door=""
  front_door="$(_vetcoders_product_front_door vc-start 2>/dev/null || true)"
  if [[ -z "$front_door" ]]; then
    printf 'vc-start: no TTY and no installed vc-start front door to open a terminal with.\n' >&2
    printf 'Run vc-start from a terminal, or install the runtime so bin/vc-terminal and bin/vc-start exist.\n' >&2
    return 1
  fi
  printf 'vc-start: opening terminal...\n' >&2
  _vetcoders_open_entry_in_vc_terminal "$front_door" "$project_root" "$@" || return 1
  VIBECRAFTED_START_ESCALATED=1
  export VIBECRAFTED_START_ESCALATED
  return 0
}

# Product lifecycle choke shared by shell `vc-start` and deck `cmd_start`.
# Reads installed product config and scripts, then prepares workspace/control
# state. Configuration publication belongs exclusively to explicit installation.
_vetcoders_product_entry_prepare() {
  local requested_root="${1:-}" owner_root required entry_status=0
  [[ -n "$requested_root" ]] || requested_root="$(pwd -P)" || return $?
  unset VIBECRAFTED_PRODUCT_ENTRY VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS

  owner_root="$(_vetcoders_vc_frame_owner_root)" || {
    VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS=1
    return 1
  }
  if ! _vetcoders_vc_frame_developer_mode; then
    export VIBECRAFTED_ROOT="$owner_root"
    export VIBECRAFTED_RUNTIME_ROOT="$owner_root"
    export VIBECRAFTED_RUNTIME_BIN="$owner_root/bin"
    export VIBECRAFTED_CORE_DIR="$owner_root/vibecrafted-core"
    export VIBECRAFTED_PYTHON="$owner_root/bin/python3"
    unset VIBECRAFTED_PREFER_REPO_VC_FRAME VIBECRAFTED_PREFER_REPO_SPAWN
    unset VIBECRAFTED_PRODUCT_CORE_CLI PYTHONPATH PYTHONHOME
    if [[ ! -x "$VIBECRAFTED_PYTHON" || ! -f "$VIBECRAFTED_CORE_DIR/vibecrafted_core/cli.py" ]]; then
      printf 'vc-start: selected runtime Python/core missing under: %s\n' "$owner_root" >&2
      printf 'Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>\n' >&2
      VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS=1
      return 1
    fi
    printf 'vc-start: checking the installed runtime...\n' >&2
    _vetcoders_product_runtime_admit "$owner_root" || {
      entry_status=$?
      VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS="$entry_status"
      return "$entry_status"
    }
  fi
  _vetcoders_pin_vc_frame_config_dir || {
    VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS=1
    return 1
  }
  if ! _vetcoders_vc_frame_developer_mode \
    && [[ -L "$VC_FRAME_CONFIG_DIR" || -L "$VC_FRAME_CONFIG_DIR/layouts" ]]; then
    printf 'vc-start: product config must be installer-owned files: %s\n' "$VC_FRAME_CONFIG_DIR" >&2
    printf 'Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>\n' >&2
    VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS=1
    return 1
  fi
  for required in config.kdl layouts/operator.kdl pane-python vc-start-here.py vc-agent-workshop.py; do
    if [[ ! -f "$VC_FRAME_CONFIG_DIR/$required" || ! -r "$VC_FRAME_CONFIG_DIR/$required" || -L "$VC_FRAME_CONFIG_DIR/$required" ]]; then
      printf 'vc-start: product resource missing or symlinked: %s/%s\n' "$VC_FRAME_CONFIG_DIR" "$required" >&2
      printf 'Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>\n' >&2
      VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS=1
      return 1
    fi
  done
  if [[ ! -x "$VC_FRAME_CONFIG_DIR/pane-python" ]]; then
    printf 'vc-start: product pane runner is not executable: %s/pane-python\n' "$VC_FRAME_CONFIG_DIR" >&2
    printf 'Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>\n' >&2
    VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS=1
    return 1
  fi
  _vetcoders_require_vc_frame || {
    VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS=1
    return 1
  }

  # Host CLIs (node/codex) must be on PATH before workspace resolve and the
  # control-plane eye — AppDelegate/vc-start sanitize the inherited PATH
  # rather than trusting it blindly.
  if declare -F _vetcoders_path_with_bundled_bin_priority >/dev/null 2>&1; then
    PATH="$(_vetcoders_path_with_bundled_bin_priority "${PATH:-}")"
    export PATH
  fi
  if _vetcoders_start_is_lobby "$requested_root"; then
    printf 'vc-start: preparing Home...\n' >&2
  else
    printf 'vc-start: preparing workspace %s...\n' "$(_vetcoders_shell_quote "$requested_root")" >&2
  fi
  _vetcoders_product_workspace_prepare "$requested_root" || {
    entry_status=$?
    VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS="$entry_status"
    return "$entry_status"
  }
  if _vetcoders_start_is_lobby "$requested_root"; then
    # Home still uses the shared service; only project-owned preparation is skipped.
    _vetcoders_control_plane_eye_prepare
    export VIBECRAFTED_PRODUCT_ENTRY=1
    return 0
  fi
  if [[ -n "${VIBECRAFTED_WORKSPACE_ROOT:-}" && -d "$VIBECRAFTED_WORKSPACE_ROOT" ]]; then
    cd "$VIBECRAFTED_WORKSPACE_ROOT" || {
      entry_status=$?
      printf "vc-start: could not enter resolved workspace root '%s'.\n" \
        "$VIBECRAFTED_WORKSPACE_ROOT" >&2
      VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS="$entry_status"
      return "$entry_status"
    }
  fi

  # Vibecrafted.app moved new frames to a short product-owned socket root.
  # Preserve every physical session found in the old namespace as a WES
  # attachment before the new visible workspace is opened.
  if declare -F _vetcoders_import_legacy_vc_frame_sessions >/dev/null 2>&1; then
    _vetcoders_import_legacy_vc_frame_sessions || {
      entry_status=$?
      VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS="$entry_status"
      return "$entry_status"
    }
  fi

  # Normalize session context without changing the selected configuration.
  if declare -F _vetcoders_normalize_ambient_context >/dev/null 2>&1; then
    _vetcoders_normalize_ambient_context || true
  fi

  # Atuin and Starship remain optional and independent of product admission.
  if declare -F _vetcoders_load_frontier_sidecars >/dev/null 2>&1; then
    _vetcoders_load_frontier_sidecars || true
  fi

  # Control-plane eye — best effort; never block cockpit if repair is unavailable.
  _vetcoders_control_plane_eye_prepare

  export VIBECRAFTED_PRODUCT_ENTRY=1
  return 0
}

# Probe printer for tests / doctor: env effects without attach/create.
_vetcoders_product_entry_probe_print() {
  [[ -z "${VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS:-}" ]] || return "$VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS"
  local layout=""
  if [[ "${VIBECRAFTED_PRODUCT_ENTRY:-0}" != "1" ]] \
    || ! declare -F _vetcoders_dashboard_layout_file >/dev/null 2>&1; then
    printf 'vc-start: product probe requires successful preparation and layout helper\n' >&2
    return 1
  fi
  layout="$(_vetcoders_dashboard_layout_file operator)" || return $?
  if [[ ! -r "${VC_FRAME_CONFIG_DIR:-}/config.kdl" || -L "${VC_FRAME_CONFIG_DIR:-}/config.kdl" || ! -r "$layout" || -L "$layout" ]]; then
    printf 'vc-start: product probe config/layout unavailable\n' >&2
    return 1
  fi
  printf 'VIBECRAFTED_PRODUCT_ENTRY=%s\n' "${VIBECRAFTED_PRODUCT_ENTRY:-0}"
  printf 'VC_FRAME_CONFIG_DIR=%s\n' "${VC_FRAME_CONFIG_DIR:-}"
  if [[ -n "${VC_FRAME_CONFIG_DIR:-}" && -f "${VC_FRAME_CONFIG_DIR%/}/config.kdl" ]]; then
    printf 'VC_FRAME_CONFIG_KDL=present\n'
  else
    printf 'VC_FRAME_CONFIG_KDL=missing\n'
  fi
  printf 'OPERATOR_LAYOUT=%s\n' "${layout:-}"
  printf 'VIBECRAFTED_WORKSPACE_ID=%s\n' "${VIBECRAFTED_WORKSPACE_ID:-}"
  printf 'VIBECRAFTED_WORKSPACE_INSTANCE_ID=%s\n' "${VIBECRAFTED_WORKSPACE_INSTANCE_ID:-}"
  printf 'VIBECRAFTED_OPERATOR_SESSION=%s\n' "${VIBECRAFTED_OPERATOR_SESSION:-}"
  if [[ -n "$layout" && -f "$layout" ]]; then
    printf 'OPERATOR_LAYOUT_PRESENT=1\n'
  else
    printf 'OPERATOR_LAYOUT_PRESENT=0\n'
  fi
}

_vetcoders_launch_dashboard() {
  if [[ -n "${VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS:-}" ]]; then
    printf 'vc-start: product preparation failed; dashboard attachment was not attempted.\n' >&2
    return "$VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS"
  fi
  local PATH="${PATH:-}"
  PATH="$(_vetcoders_path_with_bundled_bin_priority "$PATH")"
  export PATH
  vc_raise_launcher_limits
  local first_arg="${1:-}"

  # Thin shim subcommands — delegate directly to native vc-frame.
  case "$first_arg" in
    ls|list|sessions)
      local vc_frame_bin=""
      vc_frame_bin="$(_vetcoders_vc_frame_bin)" || {
        echo "vc-frame is required." >&2; return 1
      }
      "$vc_frame_bin" list-sessions
      return
      ;;
    switch)
      shift
      local vc_frame_bin=""
      vc_frame_bin="$(_vetcoders_vc_frame_bin)" || {
        echo "vc-frame is required." >&2; return 1
      }
      if _vetcoders_in_vc_frame; then
        "$vc_frame_bin" action switch-session "${1:?session name required}"
      else
        "$vc_frame_bin" attach "${1:?session name required}"
      fi
      return
      ;;
    attach)
      shift
      local vc_frame_bin=""
      vc_frame_bin="$(_vetcoders_vc_frame_bin)" || {
        echo "vc-frame is required." >&2; return 1
      }
      if _vetcoders_in_vc_frame; then
        "$vc_frame_bin" action switch-session "${1:?session name required}"
      else
        "$vc_frame_bin" attach "${1:?session name required}"
      fi
      return
      ;;
    kill)
      shift
      local vc_frame_bin=""
      vc_frame_bin="$(_vetcoders_vc_frame_bin)" || {
        echo "vc-frame is required." >&2; return 1
      }
      "$vc_frame_bin" kill-session "${1:?session name required}"
      return
      ;;
    gc)
      shift || true
      local gc_script
      gc_script="$(_vetcoders_vc_frame_gc_script 2>/dev/null || true)"
      [[ -n "$gc_script" && -f "$gc_script" ]] || {
        echo "vc-frame GC helper not found." >&2
        return 1
      }
      bash "$gc_script" "$@"
      return
      ;;
  esac

  local layout_name layout_file session_name inside_vc_frame current_session vc_frame_bin
  _vetcoders_normalize_ambient_context
  layout_name="$(_vetcoders_dashboard_layout_name "${first_arg}")" || return 1
  (( $# )) && shift

  vc_frame_bin="$(_vetcoders_vc_frame_bin)" || {
    # The dashboard is the optional operator surface, not the product. Saying
    # only "vc-frame is required" left a fresh install looking broken —
    # especially on platforms the installer ships no vc-frame binary for.
    # Name the gap, and hand over the path that works without any TUI.
    echo "vc-frame is not installed — the visual dashboard needs it." >&2
    echo "Everything else works without it. Run agents headless:" >&2
    echo "    vibecrafted workflow <agent> -p \"your task\"" >&2
    echo "    vibecrafted observe <agent> --run-id <id>" >&2
    echo "Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>" >&2
    return 1
  }

  _vetcoders_load_frontier_sidecars

  session_name="$(_vetcoders_dashboard_session_name "$layout_name")"

  layout_file="$(_vetcoders_dashboard_layout_file "$layout_name" 2>/dev/null || true)"
  [[ -n "$layout_file" ]] || {
    echo "Dashboard layout not found for: $layout_name" >&2
    printf 'Expected: %s/layouts/%s.kdl\n' "$(_vetcoders_vc_frame_config_dir)" "$layout_name" >&2
    echo "Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>" >&2
    return 1
  }

  # Trusted attached-context signal only: stale VC_FRAME/ZELLIJ leaks in a
  # parent shell must not route new-tab at a session this terminal is not
  # actually attached to.
  _vetcoders_in_vc_frame && inside_vc_frame=1 || inside_vc_frame=0
  current_session="${VC_FRAME_SESSION_NAME:-${ZELLIJ_SESSION_NAME:-}}"

  # A layout already open in this workspace gains its tab in place.
  if (( inside_vc_frame )) && [[ "$current_session" == "$session_name" ]]; then
    if [[ "$layout_name" != "operator" && "$layout_name" != "dashboard" ]]; then
      "$vc_frame_bin" action new-tab --layout "$layout_file"
      return $?
    fi
    printf 'Already in Vibecrafted workspace: %s\n' "$session_name"
    return 0
  fi

  # Each workspace owns its content and chrome; entry switches the client.
  _vetcoders_resume_workspace "$session_name" "$layout_file"
}

_vetcoders_resume_operator_session() {
  if [[ -n "${VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS:-}" ]]; then
    printf 'vc-start: product preparation failed; resume was not attempted.\n' >&2
    return "$VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS"
  fi
  local session_name layout_file identity_status=0
  _vetcoders_normalize_ambient_context
  # Same canonical owner as start. Resume used to recompute the name from
  # scratch and ignore the identities the owner had already resolved, so
  # `vc-start` opened `vibecrafted-<token>` while `vc-start resume` opened a
  # plain basename session with no binding receipt behind it.
  # Resume intentionally selects the ambient/cwd owner.
  _vetcoders_ensure_canonical_workspace_identity "" || {
    identity_status=$?
    printf 'vc-start: resume could not resolve the canonical workspace owner for this project.\n' >&2
    printf 'vc-start: refusing to target a session by name alone; re-run from the intended root or inspect '"'"'vibecrafted workspace list'"'"'.\n' >&2
    return "$identity_status"
  }
  session_name="${VIBECRAFTED_OPERATOR_SESSION:-$(_vetcoders_operator_session_name)}"
  layout_file="$(_vetcoders_operator_layout_file)" || {
    printf 'vc-start: operator layout missing under: %s\n' "$(_vetcoders_vc_frame_config_dir)" >&2
    printf 'Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>\n' >&2
    return 1
  }
  _vetcoders_resume_workspace "$session_name"
}

# Resume an ordinary durable workspace. Native switching moves only the caller's
# frontend; the engine owns its per-session tab/focus memory.
_vetcoders_resume_workspace() {
  local session_name="${1:-}" layout_file="${2:-}" vc_frame_bin="" state="" rc=0 dead_name=""
  local PATH="${PATH:-}"
  PATH="$(_vetcoders_path_with_bundled_bin_priority "$PATH")"
  export PATH
  _vetcoders_require_vc_frame || return 1
  _vetcoders_pin_vc_frame_config_dir || return $?
  vc_frame_bin="$(_vetcoders_vc_frame_bin)" || return 1
  _vetcoders_start_read_inventory_state "$session_name"
  state="$_vetcoders_start_inventory_state"
  case "$state" in
    error) _vetcoders_start_refuse_inventory "$session_name"; return $? ;;
    dead)
      dead_name="$session_name"
      _vetcoders_record_vc_frame_attachment dead "$dead_name" || return $?
      session_name="$(_vetcoders_recovery_vc_frame_session_name "$dead_name")"
      printf "Session '%s' is dead; preserving it and creating '%s'.\n" "$dead_name" "$session_name" >&2
      state="missing"
      ;;
  esac
  if [[ "$state" == missing ]]; then
    _vetcoders_start_create_project_session "$vc_frame_bin" "$session_name" "" "$layout_file" || rc=$?
    # Resume may join a concurrent creator, but only after the engine confirms
    # a live session. An EXITED record or inventory error is never an attach.
    if ((rc == 3)); then
      _vetcoders_start_read_inventory_state "$session_name"
      [[ "$_vetcoders_start_inventory_state" == live ]] || return 4
    elif ((rc != 0)); then
      return "$rc"
    fi
  fi
  _vetcoders_record_vc_frame_attachment live "$session_name" || return $?
  if [[ "$state" == live && -n "$layout_file" && "$layout_file" != "$(_vetcoders_operator_layout_file 2>/dev/null || true)" ]]; then
    _vetcoders_start_frame_env "$vc_frame_bin" --session "$session_name" action new-tab --layout "$layout_file" || return $?
  fi
  export VIBECRAFTED_OPERATOR_SESSION="$session_name"
  export VIBECRAFTED_PREPARED_VC_FRAME_SESSION="$session_name"
  _vetcoders_start_enter_workspace_session "$vc_frame_bin" "$session_name"
}

# ---------------------------------------------------------------------------
# vc-start: one create-only workspace contract (2026-09-09, Founder P0)
# ---------------------------------------------------------------------------
# The same owner from every entrypoint (shell `vc-start`, deck `vibecrafted
# start`, the bundled Rust front door, the terminal child):
#   1. root  -- explicit --repo/--root, else the Git top-level of the caller's
#               directory, else that directory. Never the runtime generation
#               or an ambient wrapper root: those name sessions after releases.
#   2. name  -- the explicit bare argument, else the root's basename VERBATIM,
#               validated once. A repository whose name is not a legal session
#               name is refused with the explicit-name form, never normalized.
#   3. check -- the selected engine's own `list-sessions` in the product socket
#               namespace, markers cleared. A live session (attached or not) or
#               an EXITED resurrection record under the name refuses the start
#               (exit 3) BEFORE any window, workspace record or provider; an
#               unreadable inventory refuses too (exit 4): doubt is not
#               permission to duplicate. Frame's `attach --create-background`
#               RESURRECTS a dead record instead of creating (src/commands.rs,
#               the ClientInfo::Resurrect arm), which is why the record has to
#               be ruled out here first.
#   4. create -- Frame's server-side exclusive `attach --create-background`
#               (zellij-client/src/lib.rs start_server_detached): the loser of
#               a race gets exit 1 + "Session already exists", told apart from
#               every other refusal. Nothing is killed, deleted, renamed,
#               switched or attached on conflict; no unique-name fallback.
#   5. enter  -- a caller with a controlling terminal attaches (outside a
#               frame) or moves its own client with switch-session (inside one:
#               the shared canvas, never a nested multiplexer). A caller WITHOUT
#               one has already created the session (step 4 needs no PTY) and
#               opens the VC Terminal host on the root with the same name/repo
#               argv plus VIBECRAFTED_START_CREATED_SESSION, so the child enters
#               the very session this start created instead of finding the
#               name taken. The parent's exit status therefore already says
#               whether the workspace exists.
# `vc-start resume` is the deliberate re-entry and keeps its own owner.

# One project identity for start: explicit root, else the Git top-level that
# contains the caller's directory (a subdirectory names the SAME workspace as
# the root does), else the directory itself. Documented default; no ambient
# SPAWN_ROOT/VIBECRAFTED_ROOT, no wrapper cwd, no generation.
_vetcoders_start_is_lobby() {
  local root="${1:-}" lobby="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/projects"
  [[ -d "$lobby" ]] || return 1
  lobby="$(cd "$lobby" && pwd -P)" || return 1
  [[ "$root" == "$lobby" ]]
}

_vetcoders_start_is_storage_root() {
  local root="${1:-}" storage="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}"
  [[ -d "$storage" ]] || return 1
  storage="$(cd "$storage" && pwd -P)" || return 1
  [[ "$root" == "$storage" ]]
}

_vetcoders_start_resolve_root() {
  local root="${VIBECRAFTED_START_ROOT:-}" top=""
  if [[ -z "$root" ]]; then
    root="$(pwd -P)" || return 1
    if _vetcoders_start_is_storage_root "$root"; then
      printf 'vc-start: VIBECRAFTED_HOME cannot be a workspace root; choose --repo <path>.\n' >&2
      return 2
    fi
    if _vetcoders_start_is_lobby "$root"; then
      printf '%s\n' "$root"
      return 0
    fi
    top="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -n "$top" && -d "$top" ]]; then
      root="$top"
    else
      root="$(pwd -P)" || return 1
    fi
  fi
  _vetcoders_absolute_physical_path "$root"
}

# The default workspace name is the root's basename, verbatim, under the same
# validator as an explicit name. Refusal says exactly what to type instead.
_vetcoders_start_default_workspace_name() {
  local root="${1:-}" base=""
  base="$(basename -- "$root")"
  if ! _vetcoders_start_validate_workspace_name "$base" "the repository name"; then
    printf 'Pass a workspace name explicitly: vc-start <workspace> --repo %s\n' \
      "$(_vetcoders_shell_quote "$root")" >&2
    return 2
  fi
  printf '%s\n' "$base"
}

# Run the selected engine the way the product entry does: attachment markers
# of THIS process cleared (a marker equal to the target trips Frame's nested-
# reattach panic, src/commands.rs:844, and `(current)` tags the listing), and
# the product socket namespace pinned when the caller has none.
# The engine is always started from a child that has already dropped this
# process's create-lock descriptor. flock(2) lives on the open file
# description: `--create-background` can leave a long-lived Frame/PTY that
# keeps the lock after the owner closes or dies if that fd is inherited.
# The extra subshell closes only the child's copy; the create owner keeps
# flock until it releases. `_vetcoders_os_fd_lock` is unchanged.
# The close is unconditional -- `_vetcoders_start_close_create_lock_fd` already
# returns 0 when no lock is held. The child copy must NOT clear
# `_vetcoders_start_create_lock_fd`: that name is the owning process's state,
# it is not exported, and nothing after the close reads it in this subshell.
_vetcoders_start_frame_env() {
  local socket_dir=""
  socket_dir="$(_vetcoders_vc_frame_socket_dir 2>/dev/null || true)"
  (
    _vetcoders_start_close_create_lock_fd
    # Name the client before the frame warden opens its TTL window.
    # ActionDeadline in vc-frame reads VC_FRAME_CALLER at fire time and
    # DeclareCaller sends it at connect; an unset value is "anonymous".
    # The timer itself is not extended here — that change belongs in
    # vc-frame zellij-client/src/cli_client.rs.
    if [[ -n "$socket_dir" ]]; then
      VC_FRAME_SOCKET_DIR="$socket_dir" ZELLIJ_SOCKET_DIR="$socket_dir" \
        env -u VC_FRAME -u VC_FRAME_PANE_ID -u VC_FRAME_SESSION_NAME \
        -u ZELLIJ -u ZELLIJ_PANE_ID -u ZELLIJ_SESSION_NAME VC_FRAME_CALLER="${VC_FRAME_CALLER:-vibecrafted}" "$@"
    else
      env -u VC_FRAME -u VC_FRAME_PANE_ID -u VC_FRAME_SESSION_NAME \
        -u ZELLIJ -u ZELLIJ_PANE_ID -u ZELLIJ_SESSION_NAME VC_FRAME_CALLER="${VC_FRAME_CALLER:-vibecrafted}" "$@"
    fi
  )
}

# Authoritative inventory state for ONE name, read in the CALLER's shell.
# Sets _vetcoders_start_inventory_state to exactly one of:
#   live     -- a running server owns the name (attached or detached alike)
#   dead     -- an EXITED resurrection record owns the name (not running)
#   missing  -- the name is free
#   error    -- the inventory could not be read or parsed; the reason is left
#               in _vetcoders_start_inventory_error
# and caches the live names for _vetcoders_start_live_inventory_hosts.
# Never call it in a command substitution: the reason and the cache are
# shell variables, and a subshell drops both -- that is how a refusal ended
# up saying "unknown reason" and every outside door paid a second listing.
# `list-sessions --no-formatting` prints `NAME [Created … ago] SUFFIX`, where
# SUFFIX is `(EXITED - attach to resurrect)` for a record (zellij-utils/src/
# sessions.rs print_sessions), and exits 1 with "No active vc-frame sessions
# found." when both lists are empty -- that one non-zero exit IS an answer.
# Unlike _vetcoders_vc_frame_session_state, nothing here is swallowed into
# "missing": every other failure is `error`. Always returns 0.
_vetcoders_start_read_inventory_state() {
  local session_name="${1:-}" vc_frame_bin="" listing="" rc=0 line="" name="" found="missing"
  _vetcoders_start_inventory_error=""
  _vetcoders_start_inventory_state="error"
  vc_frame_bin="$(_vetcoders_vc_frame_bin 2>/dev/null)" || {
    _vetcoders_start_inventory_error="the selected vc-frame engine is unavailable"
    printf 'vc-start: %s\n' "$_vetcoders_start_inventory_error" >&2
    return 0
  }
  listing="$(_vetcoders_start_frame_env "$vc_frame_bin" list-sessions --no-formatting 2>&1)" || rc=$?
  _vetcoders_start_cached_live_hosts=""
  _vetcoders_start_inventory_cache_valid=0
  if ((rc != 0)); then
    if [[ "$listing" == *"No active vc-frame sessions found."* ]]; then
      _vetcoders_start_inventory_cache_valid=1
      _vetcoders_start_inventory_state="missing"
      return 0
    fi
    _vetcoders_start_inventory_error="list-sessions exited ${rc}${listing:+: ${listing%%$'\n'*}}"
    printf 'vc-start: %s\n' "$_vetcoders_start_inventory_error" >&2
    return 0
  fi
  while IFS= read -r line; do
    line="${line%"${line##*[![:space:]]}"}"
    [[ -n "$line" ]] || continue
    case "$line" in
      *" [Created "*) ;;
      *)
        _vetcoders_start_inventory_error="unrecognized inventory line: ${line}"
        printf 'vc-start: %s\n' "$_vetcoders_start_inventory_error" >&2
        _vetcoders_start_cached_live_hosts=""
        return 0
        ;;
    esac
    name="${line%% \[Created *}"
    if [[ "$line" != *"(EXITED"* ]]; then
      _vetcoders_start_cached_live_hosts+="${name}"$'\n'
    fi
    [[ "$name" == "$session_name" ]] || continue
    if [[ "$line" == *"(EXITED"* ]]; then
      found="dead"
    else
      found="live"
    fi
  done <<<"$listing"
  _vetcoders_start_inventory_cache_valid=1
  _vetcoders_start_inventory_state="$found"
}

# Printing form of _vetcoders_start_read_inventory_state for probes and
# tests (`STATE=[$(...)]`). Product callers use the direct form so that a
# refusal can name the reason.
_vetcoders_start_session_inventory_state() {
  _vetcoders_start_read_inventory_state "${1:-}"
  printf '%s\n' "$_vetcoders_start_inventory_state"
}

# Live (non-EXITED) session names from the same inventory as
# `_vetcoders_start_read_inventory_state`, left one per line in
# `_vetcoders_start_cached_live_hosts` (nothing is printed; call it in the
# caller's shell). Empty inventory is success with no lines. Parse/engine
# failure is 2; the reason is left in `_vetcoders_start_inventory_error`.
_vetcoders_start_live_inventory_hosts() {
  local vc_frame_bin="" listing="" rc=0 line="" name=""
  _vetcoders_start_inventory_error=""
  # Reuse the listing start_entry already paid for. A second unattached
  # list-sessions per outside door was extra RPC against whatever socket
  # start_frame_env pins (including ambient Darwin when SOCKET_DIR leaks).
  if [[ "${_vetcoders_start_inventory_cache_valid:-0}" == 1 ]]; then
    return 0
  fi
  vc_frame_bin="$(_vetcoders_vc_frame_bin 2>/dev/null)" || {
    _vetcoders_start_inventory_error="the selected vc-frame engine is unavailable"
    printf 'vc-start: %s\n' "$_vetcoders_start_inventory_error" >&2
    return 2
  }
  listing="$(_vetcoders_start_frame_env "$vc_frame_bin" list-sessions --no-formatting 2>&1)" || rc=$?
  _vetcoders_start_cached_live_hosts=""
  if ((rc != 0)); then
    if [[ "$listing" == *"No active vc-frame sessions found."* ]]; then
      _vetcoders_start_inventory_cache_valid=1
      return 0
    fi
    _vetcoders_start_inventory_error="list-sessions exited ${rc}${listing:+: ${listing%%$'\n'*}}"
    printf 'vc-start: %s\n' "$_vetcoders_start_inventory_error" >&2
    return 2
  fi
  while IFS= read -r line; do
    line="${line%"${line##*[![:space:]]}"}"
    [[ -n "$line" ]] || continue
    case "$line" in
      *" [Created "*) ;;
      *)
        _vetcoders_start_inventory_error="unrecognized inventory line: ${line}"
        printf 'vc-start: %s\n' "$_vetcoders_start_inventory_error" >&2
        _vetcoders_start_cached_live_hosts=""
        return 2
        ;;
    esac
    name="${line%% \[Created *}"
    if [[ "$line" != *"(EXITED"* ]]; then
      _vetcoders_start_cached_live_hosts+="${name}"$'\n'
    fi
  done <<<"$listing"
  _vetcoders_start_inventory_cache_valid=1
  return 0
}

# Classify one live session from the engine's materialized layout, rather than
# inferring its role from existence or client attachment. Output is one of:
# host (declared frame_host identity) or guest (no host marker). A serialized
# operator layout repeats that identity in tabs and swap templates; repetitions
# are not separate sessions/owners and must never make a live host disappear.
# The workspace_surface plugin is only the empty-host placeholder and
# disappears after a guest projection, so it is deliberately not identity.
# Query/parser failure is exit 2 and produces no role: callers fail closed.
_vetcoders_start_session_projection_role() {
  local session_name="${1:-}" vc_frame_bin="${2:-}" layout="" python_bin=""
  [[ -n "$session_name" && -n "$vc_frame_bin" ]] || return 2
  layout="$(_vetcoders_start_frame_env "$vc_frame_bin" --session "$session_name" \
    action dump-layout 2>/dev/null)" || return 2
  [[ -n "$layout" ]] || return 2
  python_bin="$(_vetcoders_internal_python 2>/dev/null || true)"
  [[ -n "$python_bin" ]] || return 2
  "$python_bin" -c '
import re
import sys

text = sys.stdin.read()
# dump-layout is engine-normalized KDL. Keep strings atomic: labels, command
# arguments, and URLs containing marker/comment text are not declarations.
tokens = re.findall(r"\"(?:\\.|[^\"\\])*\"|//[^\n]*|/\*.*?\*/|[{};\n]|[^\s{};\"]+", text, re.S)
tokens = [token for token in tokens if not token.startswith(("//", "/*"))]
meaningful = [token for token in tokens if token not in ("\n", ";")]
if meaningful[:2] != ["layout", "{"] or meaningful[-1:] != ["}"]:
    sys.exit(2)
depth = 0
for token in tokens:
    depth += (token == "{") - (token == "}")
    if depth < 0:
        sys.exit(2)
if depth:
    sys.exit(2)
host = any(
    token == "frame_host"
    and index > 0 and tokens[index - 1] in ("{", ";", "\n")
    and tokens[index + 1:index + 2] in (["true"], ["\"true\""])
    and tokens[index + 2:index + 3] in ([";"], ["\n"], ["}"])
    for index, token in enumerate(tokens)
)
print("host" if host else "guest")
' <<<"$layout"
}

# Refuse a start whose name is taken. Every command printed is a real one:
# `vc-dashboard attach|switch` (dashboard.sh), `vc-frame attach`,
# `vc-frame kill-session`, `vc-frame delete-session` (vc-frame 0.47.3 --help).
# Deletion is offered only when nobody is attached; a watched session gets
# attach/switch and the rename form only. Exit 3.
_vetcoders_start_refuse_existing_workspace() {
  local name="${1:-}" state="${2:-live}" root="${3:-}" clients="unknown" q_name="" q_root=""
  q_name="$(_vetcoders_shell_quote "$name")"
  q_root="$(_vetcoders_shell_quote "$root")"
  case "$state" in
    dead)
      printf 'vc-start: workspace %s already exists in vc-frame as an EXITED session (a resurrection record, not running).\n' "$q_name" >&2
      ;;
    *)
      clients="$(_vetcoders_vc_frame_session_client_state "$name")"
      case "$clients" in
        clients) printf 'vc-start: workspace %s already exists in vc-frame (live, a client is attached).\n' "$q_name" >&2 ;;
        none) printf 'vc-start: workspace %s already exists in vc-frame (live, detached: nobody is attached).\n' "$q_name" >&2 ;;
        *) printf 'vc-start: workspace %s already exists in vc-frame (live).\n' "$q_name" >&2 ;;
      esac
      ;;
  esac
  printf '  Nothing was created, attached, switched or deleted (repository: %s). Choose one:\n' "$q_root" >&2
  if [[ "$state" == dead ]]; then
    printf '    resurrect it from a terminal:       vc-frame attach %s\n' "$q_name" >&2
  else
    if _vetcoders_in_vc_frame; then
      printf '    switch this Frame client to it:     vc-dashboard switch %s\n' "$q_name" >&2
    fi
    printf '    attach from a terminal:             vc-dashboard attach %s\n' "$q_name" >&2
  fi
  printf '    start a different workspace here:   vc-start <new-name> --repo %s\n' "$q_root" >&2
  if [[ "$state" == dead ]]; then
    printf '    delete the exited record:           vc-frame delete-session %s\n' "$q_name" >&2
  elif [[ "$clients" == none ]]; then
    printf '    stop it (nobody is attached):       vc-frame kill-session %s\n' "$q_name" >&2
  fi
  return 3
}

# Refuse when the inventory cannot answer. Exit 4.
_vetcoders_start_refuse_inventory() {
  local name="${1:-}"
  printf 'vc-start: could not read the live vc-frame session inventory (%s); refusing to create %s because a duplicate cannot be ruled out.\n' \
    "${_vetcoders_start_inventory_error:-unknown reason}" "$(_vetcoders_shell_quote "$name")" >&2
  printf '  Inspect the engine yourself: vc-frame list-sessions --no-formatting   -- then re-run vc-start.\n' >&2
  return 4
}

# Exclusive create lock for one session name in this socket namespace.
# Frame `--new-session-with-layout` + `attach --create-background` follows
# ClientInfo::New and can return 0 on both racers (the "Session already exists"
# string is the Attach arm). The adapter must refuse the loser.
# Ownership is the existing OS fd-lock (`_vetcoders_os_fd_lock`, same flock(2)
# as scripts/lib/runtime-pack-selection.sh): the lock FILE is created once and
# never unlinked, release closes this process's descriptor, and SIGKILL drops
# the kernel lock. A leftover mkdir(2) directory is refused, not removed.
# zsh parses `exec 11>&-` as exec of command 11 and replaces the shell
# (exit 127) before any `|| true` or following printf. Close by variable
# name on zsh and bash 4.1+; bash 3.2 keeps numeric close of fixed fd 211.
_vetcoders_start_close_create_lock_fd() {
  [[ -n "${_vetcoders_start_create_lock_fd:-}" ]] || return 0
  if [[ -n "${ZSH_VERSION:-}" ]]; then
    builtin exec {_vetcoders_start_create_lock_fd}>&- || :
  elif [[ -n "${BASH_VERSINFO:-}" ]] && ((BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 1))); then
    exec {_vetcoders_start_create_lock_fd}>&- || :
  else
    eval "exec ${_vetcoders_start_create_lock_fd}>&-" || :
  fi
  return 0
}

_vetcoders_start_acquire_create_lock() {
  # lock_rc — not `status`. zsh's $status is a readonly special parameter;
  # `local status` aborts the function at line 1 (`read-only variable`).
  local session_name="${1:-}" socket_dir="" lock_file="" lock_rc=0
  local timeout="${VIBECRAFTED_START_CREATE_LOCK_TIMEOUT:-30}"
  [[ -n "$session_name" ]] || return 4
  socket_dir="$(_vetcoders_vc_frame_socket_dir 2>/dev/null || true)"
  [[ -n "$socket_dir" ]] || socket_dir="${TMPDIR:-/tmp}"
  mkdir -p "$socket_dir" || return 4
  lock_file="$socket_dir/.vc-start-create.${session_name}.lock"
  if [[ -d "$lock_file" ]]; then
    printf 'vc-start: create lock %s is a directory left by an older mkdir lock; refusing without removing it.\n' \
      "$(_vetcoders_shell_quote "$lock_file")" >&2
    return 4
  fi
  _vetcoders_start_create_lock_fd=""
  if [[ -n "${BASH_VERSINFO:-}" ]] && ((BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 1))); then
    exec {_vetcoders_start_create_lock_fd}>>"$lock_file" || return 4
  elif [[ -n "${ZSH_VERSION:-}" ]]; then
    exec {_vetcoders_start_create_lock_fd}>>"$lock_file" || return 4
  else
    _vetcoders_start_create_lock_fd=211
    eval "exec ${_vetcoders_start_create_lock_fd}>>\"\$lock_file\"" || return 4
  fi
  [[ -n "${_vetcoders_start_create_lock_fd:-}" ]] || return 4
  _vetcoders_os_fd_lock "$_vetcoders_start_create_lock_fd" "$timeout" || lock_rc=$?
  if ((lock_rc != 0)); then
    _vetcoders_start_close_create_lock_fd
    _vetcoders_start_create_lock_fd=""
    printf 'vc-start: could not obtain exclusive create lock for %s.\n' \
      "$(_vetcoders_shell_quote "$session_name")" >&2
    return 4
  fi
}

_vetcoders_start_release_create_lock() {
  [[ -n "${_vetcoders_start_create_lock_fd:-}" ]] || return 0
  _vetcoders_start_close_create_lock_fd
  _vetcoders_start_create_lock_fd=""
}

# The one create primitive. Returns 0 (created and live), 3 (the exact name
# was taken meanwhile -- the caller re-reads the inventory and refuses), or 4
# (any other engine refusal / the session never came up). Never waits a real
# refusal out, never treats "already exists" as success.
# $4 = chrome (embedded Operator) or workspace (the selected full layout).
# Project creation preserves both custom content and session_layer chrome.
# Do not substitute the `vibecrafted` builtin — that discards selected content.
_vetcoders_start_create_workspace_session() {
  local vc_frame_bin="${1:-}" session_name="${2:-}" layout_file="${3:-}" kind="${4:-workspace}" out="" rc=0
  local create_argv=() state=""
  [[ -n "$vc_frame_bin" && -n "$session_name" ]] || return 4
  if [[ "$kind" != chrome && ( -z "$layout_file" || ! -f "$layout_file" ) ]]; then
    local layouts_dir=""
    layouts_dir="$(_vetcoders_vc_frame_config_dir 2>/dev/null || printf '?')/layouts"
    printf 'vc-start: workspace layout missing: %s\n' "${layout_file:-$layouts_dir/operator.kdl}" >&2
    printf 'Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>\n' >&2
    return 4
  fi
  if [[ "$kind" != chrome ]]; then
    create_argv+=(--new-session-with-layout "$layout_file")
  fi
  create_argv+=(attach --create-background "$session_name")
  _vetcoders_start_acquire_create_lock "$session_name" || return $?
  _vetcoders_start_read_inventory_state "$session_name"
  state="$_vetcoders_start_inventory_state"
  if [[ "$state" == error ]]; then
    _vetcoders_start_release_create_lock
    _vetcoders_start_refuse_inventory "$session_name"
    return 4
  fi
  if [[ "$state" != missing ]]; then
    _vetcoders_start_release_create_lock
    return 3
  fi
  # The embedded Operator is independent of any project workspace record.
  if [[ "$kind" != chrome ]]; then
    _vetcoders_record_vc_frame_attachment missing "$session_name" || {
      rc=$?
      _vetcoders_start_release_create_lock
      return "$rc"
    }
  fi
  if [[ "$kind" != chrome ]]; then
    printf 'vc-start: opening workspace %s...\n' "$(_vetcoders_shell_quote "$session_name")" >&2
  fi
  if [[ "$kind" == chrome ]]; then
    # Session 00 is not the project that happened to create it. Do not leak
    # that project's durable identity into its dashboard/config/tool panes.
    out="$(_vetcoders_start_frame_env env \
      -u VIBECRAFTED_WORKSPACE_ID -u VIBECRAFTED_SESSION_ID \
      -u VIBECRAFTED_WORKSPACE_INSTANCE_ID -u VIBECRAFTED_BUILD_ID \
      -u VIBECRAFTED_OPERATOR_SESSION -u VIBECRAFTED_WORKSPACE_ROOT \
      "$vc_frame_bin" "${create_argv[@]}" 2>&1)" || rc=$?
  else
    out="$(_vetcoders_start_frame_env "$vc_frame_bin" "${create_argv[@]}" 2>&1)" || rc=$?
  fi
  if ((rc != 0)); then
    _vetcoders_start_release_create_lock
    if _vetcoders_vc_frame_stderr_is_session_already_exists "$out"; then
      return 3
    fi
    if [[ "$kind" == chrome ]]; then
      printf 'vc-start: vc-frame refused to create the Frame host %s (exit %s).\n' \
        "$(_vetcoders_shell_quote "$session_name")" "$rc" >&2
    else
      printf 'vc-start: vc-frame refused to create workspace %s (exit %s).\n' \
        "$(_vetcoders_shell_quote "$session_name")" "$rc" >&2
    fi
    [[ -z "$out" ]] || printf '%s\n' "$out" >&2
    return 4
  fi
  if ! _vetcoders_wait_for_vc_frame_session "$session_name" 40; then
    _vetcoders_start_release_create_lock
    printf 'vc-start: workspace %s did not come up in vc-frame after the create call.\n' \
      "$(_vetcoders_shell_quote "$session_name")" >&2
    [[ -z "$out" ]] || printf '%s\n' "$out" >&2
    return 4
  fi
  _vetcoders_start_inventory_cache_valid=0
  if [[ "$kind" == chrome ]]; then
    _vetcoders_start_release_create_lock
    return 0
  fi
  _vetcoders_record_vc_frame_attachment live "$session_name" || {
    rc=$?
    _vetcoders_start_release_create_lock
    return "$rc"
  }
  _vetcoders_start_release_create_lock
  export VIBECRAFTED_PREPARED_VC_FRAME_SESSION="$session_name"
  return 0
}

# Enter a peer session. Creation/attach RPCs clear pane markers, but a native
# switch must retain them so Frame can identify the requesting client. Never
# infer its identity from the number of attached clients or another pane.
_vetcoders_start_enter_workspace_session() {
  local vc_frame_bin="${1:-}" session_name="${2:-}" current="" socket_dir="" rc=0
  [[ -n "$vc_frame_bin" && -n "$session_name" ]] || return 4
  if _vetcoders_in_vc_frame; then
    current="$(_vetcoders_current_vc_frame_session_name)"
    [[ -n "$current" ]] || return 4
    [[ "$current" != "$session_name" ]] || return 0
    socket_dir="$(_vetcoders_vc_frame_socket_dir)" || return 4
    (
      _vetcoders_start_close_create_lock_fd
      export VC_FRAME_SOCKET_DIR="$socket_dir" ZELLIJ_SOCKET_DIR="$socket_dir"
      VC_FRAME_CALLER="${VC_FRAME_CALLER:-vibecrafted}" \
        "$vc_frame_bin" --session "$current" action switch-session "$session_name"
    ) || rc=$?
    if ((rc != 0)); then
      printf 'vc-start: entry into %s failed (exit %s). Retry with: vc-frame attach %s\n' \
        "$(_vetcoders_shell_quote "$session_name")" "$rc" \
        "$(_vetcoders_shell_quote "$session_name")" >&2
    fi
    return "$rc"
  fi
  _vetcoders_start_frame_env "$vc_frame_bin" attach "$session_name"
}

_vetcoders_start_host_session_name() {
  printf '%s\n' "${VIBECRAFTED_FRAME_HOST_SESSION:-vc-host}"
}

# Ensure the independent Operator session exists, preserving its state on
# resurrection. Its embedded layout declares the role; a name alone is not
# enough. $2 is the project name and cannot collide with this session.
_vetcoders_start_ensure_host() {
  local vc_frame_bin="${1:-}" session_name="${2:-}" host="" state="" role="" role_rc=0 rc=0
  _vetcoders_start_operator_session=""
  host="$(_vetcoders_start_host_session_name)"
  if [[ "$host" == "$session_name" ]]; then
    printf 'vc-start: %s is the Frame host name; pass a different workspace name.\n' \
      "$(_vetcoders_shell_quote "$host")" >&2
    return 2
  fi
  _vetcoders_start_read_inventory_state "$host"
  state="$_vetcoders_start_inventory_state"
  case "$state" in
    error)
      _vetcoders_start_refuse_inventory "$host"
      return $?
      ;;
    missing)
      printf 'vc-start: starting host %s...\n' "$(_vetcoders_shell_quote "$host")" >&2
      _vetcoders_start_create_workspace_session "$vc_frame_bin" "$host" "" chrome || rc=$?
      # 3: a concurrent start created the host first — that host is ours too.
      if ((rc != 0 && rc != 3)); then
        return "$rc"
      fi
      rc=0
      ;;
    dead)
      printf 'vc-start: restoring host %s...\n' "$(_vetcoders_shell_quote "$host")" >&2
      _vetcoders_start_frame_env env \
        -u VIBECRAFTED_WORKSPACE_ID -u VIBECRAFTED_SESSION_ID \
        -u VIBECRAFTED_WORKSPACE_INSTANCE_ID -u VIBECRAFTED_BUILD_ID \
        -u VIBECRAFTED_OPERATOR_SESSION -u VIBECRAFTED_WORKSPACE_ROOT \
        "$vc_frame_bin" attach --create-background "$host" >/dev/null 2>&1 || true
      _vetcoders_wait_for_vc_frame_session "$host" 40 || {
        printf 'vc-start: Frame host %s exited and could not be resurrected.\n' \
          "$(_vetcoders_shell_quote "$host")" >&2
        return 4
      }
      _vetcoders_start_inventory_cache_valid=0
      ;;
  esac
  role="$(_vetcoders_start_session_projection_role "$host" "$vc_frame_bin")" || role_rc=$?
  if ((role_rc == 0)) && [[ "$role" == guest && "$host" != *-recovered ]]; then
    # A prior generation may have used the reserved name for a workspace.
    # Never delete/promote that live session or relabel it based on its name.
    # A deterministic sibling lets concurrent/repeated starts converge.
    printf 'vc-start: %s has guest layout; preserving it and recovering host %s. Access the old session: vc-frame attach %s. After saving its work, remove it with: vc-frame kill-session %s && vc-frame delete-session %s\n' \
      "$(_vetcoders_shell_quote "$host")" "$(_vetcoders_shell_quote "$host-recovered")" \
      "$(_vetcoders_shell_quote "$host")" "$(_vetcoders_shell_quote "$host")" "$(_vetcoders_shell_quote "$host")" >&2
    local VIBECRAFTED_FRAME_HOST_SESSION="$host-recovered"
    _vetcoders_start_ensure_host "$vc_frame_bin" "$session_name"
    return $?
  fi
  if ((role_rc != 0)) || [[ "$role" != host ]]; then
    printf 'vc-start: session %s exists but is not a Frame host (role: %s); refusing before creating %s.\n' \
      "$(_vetcoders_shell_quote "$host")" "$(_vetcoders_shell_quote "${role:-unknown}")" \
      "$(_vetcoders_shell_quote "$session_name")" >&2
    return 4
  fi
  _vetcoders_start_operator_session="$host"
}

# The Operator is an independent ordinary session; projects never borrow its
# canvas. Keep its existing physical name, while Frame's role marks rail 00.
_vetcoders_start_create_project_session() {
  local vc_frame_bin="${1:-}" session_name="${2:-}" root="${3:-}" layout_file="${4:-}"
  [[ -n "$layout_file" ]] || layout_file="$(_vetcoders_operator_layout_file 2>/dev/null || true)"
  _vetcoders_start_ensure_host "$vc_frame_bin" "$session_name" || return $?
  _vetcoders_start_create_workspace_session "$vc_frame_bin" "$session_name" "$layout_file"
}

# No-project entry: independent Operator session, no project workspace IDs.
# Terminal escalation replays from its cwd without turning the lobby into --repo.
_vetcoders_start_enter_lobby() {
  local root="${1:-}" vc_frame_bin="" host="" rc=0
  _vetcoders_product_workspace_prepare "$root" || return $?
  unset VIBECRAFTED_START_CREATED_SESSION VIBECRAFTED_PREPARED_VC_FRAME_SESSION
  if [[ -n "${_vetcoders_start_workspace_name:-}" \
    || -n "${_vetcoders_start_contract_base:-}" \
    || -n "${_vetcoders_start_contract_execution_runtime:-}" \
    || "${_vetcoders_start_contract_worktree:-false}" == true ]]; then
    printf 'vc-start: choose a project with --repo <path> before naming or preparing a workspace.\n' >&2
    return 2
  fi
  _vetcoders_require_vc_frame || return 1
  _vetcoders_pin_vc_frame_config_dir || return $?
  vc_frame_bin="$(_vetcoders_vc_frame_bin)" || return 1
  _vetcoders_start_ensure_host "$vc_frame_bin" "" || return $?
  host="$_vetcoders_start_operator_session"
  printf 'vc-start: opening Operator Frame; choose a project when ready.\n' >&2
  if _vetcoders_in_vc_frame; then
    _vetcoders_start_enter_workspace_session "$vc_frame_bin" "$host"
    return $?
  fi
  if ! _vetcoders_start_is_owned_terminal_child && [[ ! -t 0 || ! -t 1 ]]; then
    _vetcoders_start_open_terminal_if_needed strict "$root"
    return $?
  fi
  if [[ ! -t 0 || ! -t 1 ]]; then
    printf 'vc-start: terminal supplied no TTY; host %s remains detached.\n' "$host" >&2
    return 4
  fi
  _vetcoders_product_entry_prepare "$root" || return $?
  _vetcoders_start_frame_env "$vc_frame_bin" attach "$host"
}

# Create-only start from a caller WITHOUT a controlling terminal: the create
# itself needs no PTY, so it happens here, before any window -- the caller's
# exit status reflects the workspace, not the terminal host. Config pin and
# engine admission only; the full product preparation (workspace record,
# server eye) runs in the terminal child, which has the surface.
_vetcoders_start_create_before_terminal() {
  local session_name="${1:-}" root="${2:-}" vc_frame_bin="" rc=0 state=""
  local PATH="${PATH:-}"
  PATH="$(_vetcoders_path_with_bundled_bin_priority "$PATH")"
  export PATH
  _vetcoders_require_vc_frame || return 1
  _vetcoders_pin_vc_frame_config_dir || return $?
  vc_frame_bin="$(_vetcoders_vc_frame_bin)" || return 1
  _vetcoders_start_create_project_session "$vc_frame_bin" "$session_name" "$root" || rc=$?
  if ((rc == 3)); then
    _vetcoders_start_read_inventory_state "$session_name"
    state="$_vetcoders_start_inventory_state"
    [[ "$state" == dead ]] || state="live"
    _vetcoders_start_refuse_existing_workspace "$session_name" "$state" "$root"
    return $?
  fi
  return "$rc"
}

# The surfaced half: product preparation has run (identities exported, cwd is
# the root). Re-read the inventory (cheap; the create below is exclusive
# anyway), create unless this very start already did, then enter.
_vetcoders_start_launch_workspace() {
  local session_name="${1:-}" root="${2:-}" vc_frame_bin="" state="" rc=0
  if [[ -n "${VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS:-}" ]]; then
    printf 'vc-start: product preparation failed; the workspace was not created.\n' >&2
    return "$VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS"
  fi
  local PATH="${PATH:-}"
  PATH="$(_vetcoders_path_with_bundled_bin_priority "$PATH")"
  export PATH
  vc_raise_launcher_limits
  vc_frame_bin="$(_vetcoders_vc_frame_bin)" || {
    echo "vc-frame is not installed — the visual workspace needs it." >&2
    echo "Everything else works without it. Run agents headless:" >&2
    echo "    vibecrafted workflow <agent> -p \"your task\"" >&2
    echo "    vibecrafted observe <agent> --run-id <id>" >&2
    echo "Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>" >&2
    return 1
  }
  _vetcoders_load_frontier_sidecars

  _vetcoders_start_read_inventory_state "$session_name"
  state="$_vetcoders_start_inventory_state"
  case "$state" in
    error)
      _vetcoders_start_refuse_inventory "$session_name"
      return $?
      ;;
    dead)
      _vetcoders_start_refuse_existing_workspace "$session_name" dead "$root"
      return $?
      ;;
    live)
      if [[ "${VIBECRAFTED_START_CREATED_SESSION:-}" != "$session_name" ]]; then
        _vetcoders_start_refuse_existing_workspace "$session_name" live "$root"
        return $?
      fi
      # Created by the start that opened this terminal; bind it now that the
      # workspace identities exist, then enter.
      _vetcoders_record_vc_frame_attachment live "$session_name" || return $?
      ;;
    *)
      _vetcoders_start_create_project_session "$vc_frame_bin" "$session_name" "$root" || rc=$?
      if ((rc == 3)); then
        _vetcoders_start_read_inventory_state "$session_name"
        state="$_vetcoders_start_inventory_state"
        [[ "$state" == dead ]] || state="live"
        _vetcoders_start_refuse_existing_workspace "$session_name" "$state" "$root"
        return $?
      fi
      ((rc == 0)) || return "$rc"
      printf 'vc-start: created workspace %s for %s\n' \
        "$(_vetcoders_shell_quote "$session_name")" "$(_vetcoders_shell_quote "$root")"
      ;;
  esac
  unset VIBECRAFTED_START_CREATED_SESSION
  export VIBECRAFTED_OPERATOR_SESSION="$session_name"
  _vetcoders_start_enter_workspace_session "$vc_frame_bin" "$session_name"
}

# Explicit additional Operator session for a selected runtime generation.
# Project sessions have their own chrome and do not use this path.
_vetcoders_start_active_host_root() {
  local owner=""
  owner="$(_vetcoders_vc_frame_owner_root)" || return 4
  if [[ "$(basename "$(dirname "$owner")")" == releases ]]; then
    _vetcoders_product_runtime_admit "$owner" active-root
  else
    # Source/developer entry keeps its selected engine. Installed entries
    # use the installer's verified resolution, never ambient roots.
    printf '%s\n' "$owner"
  fi
}

_vetcoders_start_generation_label() {
  local owner="${1:-}" version=""
  if [[ -f "$owner/VERSION" ]]; then
    IFS= read -r version <"$owner/VERSION" || true
  fi
  printf '%s\n' "${version:-unknown}"
}

# Attribute a host to the process owning its exact socket, not pane commands,
# session name, or this client's environment. Unknown stays unknown.
_vetcoders_start_host_generation() {
  local host="${1:-}" socket_dir="" python_bin=""
  socket_dir="$(_vetcoders_vc_frame_socket_dir)"
  python_bin="$(_vetcoders_internal_python)" || return 1
  "$python_bin" - "$socket_dir" "$host" <<'PY_HOST_GENERATION'
from pathlib import Path
import subprocess
import sys

root, name = Path(sys.argv[1]), sys.argv[2]
labels = set()
try:
    sockets = [root / name, *(p / name for p in root.glob("contract_version_*"))]
    for path in sockets:
        if not path.is_socket():
            continue
        holders = subprocess.run(["lsof", "-t", str(path)], capture_output=True,
                                 text=True, timeout=3, check=False)
        for pid in set(holders.stdout.split()):
            if not pid.isdecimal():
                continue
            proc = subprocess.run(["ps", "-p", pid, "-o", "comm="],
                                  capture_output=True, text=True, timeout=3, check=False)
            exe = Path(proc.stdout.strip())
            if not exe.is_absolute() or exe.name != "vc-frame":
                continue
            for parent in exe.parents:
                if parent.parent.name == "releases":
                    labels.add(parent.name)
                    break
    print(next(iter(labels)) if len(labels) == 1 else "unknown")
except (OSError, subprocess.TimeoutExpired):
    print("unknown")
PY_HOST_GENERATION
}

# No kill, resurrect, guest projection, or workspace binding on this path.
# The existing exclusive create rechecks under its per-name lock. A racing
# creator causes us to try the next numbered name, never adopt its session.
_vetcoders_start_new_host() (
  local root="${1:-}" active="" owner="" generation="" short="" base="vc-host" host="" rc=0 index=1
  local vc_frame_bin="" role="" front_door="" existing="" sessions=""
  local created_here=0 role_rc=0 role_attempts=0 role_deadline=0
  local entry_args=(--new-host)
  if ! _vetcoders_start_is_lobby "$root"; then
    entry_args+=(--repo "$root")
  fi
  owner="$(_vetcoders_vc_frame_owner_root)" || return 4
  active="$(_vetcoders_start_active_host_root)" || return 4
  if [[ "$(basename "$(dirname "$active")")" == releases ]]; then
    # The native vc-start front door reads these roots before sourcing its
    # shell. Pin the admitted owner for both re-entry and the terminal child;
    # an absolute executable path alone does not defeat inherited old roots.
    export VIBECRAFTED_ROOT="$active" VIBECRAFTED_RUNTIME_ROOT="$active"
    export VIBECRAFTED_RUNTIME_BIN="$active/bin" VIBECRAFTED_CORE_DIR="$active/vibecrafted-core"
    export VIBECRAFTED_PYTHON="$active/bin/python3" VIBECRAFTED_VC_FRAME_BIN="$active/bin/vc-frame"
    unset VIBECRAFTED_APP_ROOT PYTHONPATH PYTHONHOME
  fi
  if [[ "$active" != "$owner" ]]; then
    if [[ -n "${VIBECRAFTED_START_CREATED_HOST:-}" ]] && _vetcoders_start_is_owned_terminal_child; then
      printf 'vc-start: active generation changed while opening host %s; it remains intact. Retry --new-host explicitly.\n' "$VIBECRAFTED_START_CREATED_HOST" >&2
      return 4
    fi
    printf 'vc-start: opening the active generation %s (selected shell: %s).\n' \
      "$(basename "$active")" "$(basename "$owner")" >&2
    "$active/bin/vc-start" "${entry_args[@]}"
    return $?
  fi
  generation="$(_vetcoders_start_generation_label "$active")"
  _vetcoders_require_vc_frame || return 4
  _vetcoders_pin_vc_frame_config_dir || return 4
  vc_frame_bin="$(_vetcoders_vc_frame_bin)" || return 4
  # Only an owned terminal child may consume the exact created host.
  if [[ -n "${VIBECRAFTED_START_CREATED_HOST:-}" ]] && _vetcoders_start_is_owned_terminal_child; then
    host="$VIBECRAFTED_START_CREATED_HOST"
    unset VIBECRAFTED_START_CREATED_HOST
    _vetcoders_start_validate_workspace_name "$host" "host name" || return 2
    _vetcoders_start_read_inventory_state "$host"
    [[ "$_vetcoders_start_inventory_state" == live ]] || return 4
    if [[ ! -t 0 || ! -t 1 ]]; then
      printf 'vc-start: terminal supplied no TTY; host %s remains detached.\n' "$host" >&2
      return 4
    fi
  else
    unset VIBECRAFTED_START_CREATED_HOST
    _vetcoders_start_live_inventory_hosts || return 4
    sessions="${_vetcoders_start_cached_live_hosts:-}"
    while IFS= read -r existing; do
      [[ -n "$existing" ]] || continue
      role="$(_vetcoders_start_session_projection_role "$existing" "$vc_frame_bin")" || role="unknown"
      [[ "$role" == host ]] || continue
      printf 'vc-start: existing host %s runs on %s.\n' "$existing" \
        "$(_vetcoders_start_host_generation "$existing")"
    done <<<"$sessions"
    short="${generation##*+g}"
    if [[ "$generation" == *+g* && -n "$short" && "$short" != *[![:xdigit:]]* ]]; then
      base="vc-host@${short:0:8}"
    else
      index=2
    fi
    while ((index <= 9999)); do
      host="$base"
      ((index == 1)) || host="$base-$index"
      _vetcoders_start_create_workspace_session "$vc_frame_bin" "$host" "" chrome && rc=0 || rc=$?
      ((rc == 3)) || break
      index=$((index + 1))
    done
    ((rc == 0)) || return "$rc"
    printf 'vc-start: created host %s on generation %s; existing sessions remain untouched.\n' "$host" "$generation"
    created_here=1
  fi
  if ((created_here)); then
    # A live socket precedes materialized host plugins. Retry only the host
    # this invocation exclusively created; never adopt another live session.
    # Both limits are fixed. The engine bounds each individual action RPC;
    # stop scheduling probes after ten seconds or forty calls, whichever first.
    role_deadline=$((SECONDS + 10))
    while ((role_attempts < 40 && SECONDS < role_deadline)); do
      role_attempts=$((role_attempts + 1))
      role="$(_vetcoders_start_session_projection_role "$host" "$vc_frame_bin")" && role_rc=0 || role_rc=$?
      [[ "$role" == host ]] && break
      ((SECONDS < role_deadline)) || break
      sleep 0.25
    done
    if [[ "$role" != host ]]; then
      if ((role_rc != 0)); then
        printf 'vc-start: host %s remains detached: host-role probe failed (exit %s).\n' "$host" "$role_rc" >&2
      else
        printf 'vc-start: host %s remains detached: its layout has no frame_host true marker.\n' "$host" >&2
      fi
      printf 'Enter it explicitly with: %s attach %s\n' \
        "$(_vetcoders_shell_quote "$vc_frame_bin")" "$(_vetcoders_shell_quote "$host")" >&2
      return 4
    fi
  else
    role="$(_vetcoders_start_session_projection_role "$host" "$vc_frame_bin")" || return 4
    [[ "$role" == host ]] || return 4
  fi
  if _vetcoders_in_vc_frame || [[ ! -t 0 || ! -t 1 ]]; then
    # Inside-host requests need another terminal even with a TTY. Reuse the
    # canonical PTY supplier, which clears inherited Frame markers.
    front_door="$(_vetcoders_product_front_door vc-start)" || return 4
    export VIBECRAFTED_START_CREATED_HOST="$host"
    _vetcoders_open_entry_in_vc_terminal "$front_door" "$root" "${entry_args[@]}" || {
      printf 'vc-start: host %s remains detached; enter it with: vc-frame attach %s\n' "$host" "$host" >&2
      return 4
    }
  else
    _vetcoders_start_frame_env "$vc_frame_bin" attach "$host"
  fi
)

# Shared entry for shell `vc-start` and deck `cmd_start`, after
# _vetcoders_start_prepare_arguments. $@ = _vetcoders_start_frame_argv.
_vetcoders_start_entry() {
  local root="" session_name="" state="" rc=0
  # Emit life before launch-spec/catalogue resolution can do expensive work.
  # Progress stays on stderr so machine-readable entry probes keep stdout.
  printf 'vc-start: checking your workspace and available sessions...\n' >&2
  # Long-lived shells must refresh inventory before each generation decision.
  _vetcoders_start_inventory_cache_valid=0
  root="$(_vetcoders_start_resolve_root)" || {
    rc=$?
    printf 'vc-start: could not resolve the project root.\n' >&2
    return "$rc"
  }
  if _vetcoders_start_is_lobby "$root"; then
    if [[ "${VIBECRAFTED_PRODUCT_ENTRY_PROBE:-0}" == 1 ]]; then
      _vetcoders_product_entry_prepare "$root" || return $?
      _vetcoders_product_entry_probe_print
      return $?
    fi
    if [[ "${_vetcoders_start_new_host_requested:-0}" == 1 ]]; then
      _vetcoders_product_workspace_prepare "$root" || return $?
      _vetcoders_start_new_host "$root"
      return $?
    fi
    _vetcoders_start_enter_lobby "$root"
    return $?
  fi
  _vetcoders_start_apply_launch_spec "$root" || return $?
  root="$(_vetcoders_start_resolve_root)" || {
    rc=$?
    printf 'vc-start: could not resolve the project root.\n' >&2
    return "$rc"
  }

  if [[ "${_vetcoders_start_mode:-start}" == "resume" ]]; then
    # Deliberate re-entry: the declaration family's own escalation and owner.
    _vetcoders_start_open_terminal_if_needed declared "$root" "$@" --repo "$root" || return $?
    [[ -z "${VIBECRAFTED_START_ESCALATED:-}" ]] || return 0
    _vetcoders_product_entry_prepare "$root" || return $?
    if [[ "${VIBECRAFTED_PRODUCT_ENTRY_PROBE:-0}" == "1" ]]; then
      _vetcoders_product_entry_probe_print
      return $?
    fi
    while (($#)) && _vetcoders_start_reserved_word "$1"; do
      [[ "$1" != "resume" ]] || {
        shift
        break
      }
      shift
    done
    _vetcoders_resume_operator_session "$@"
    return $?
  fi

  if [[ "${_vetcoders_start_new_host_requested:-0}" == 1 ]]; then
    _vetcoders_start_new_host "$root"
    return $?
  fi
  session_name="${_vetcoders_start_workspace_name:-}"
  if [[ -z "$session_name" ]]; then
    session_name="$(_vetcoders_start_default_workspace_name "$root")" || return $?
  fi

  # Tests/doctor: preparation effects only; no inventory, no create, no attach.
  if [[ "${VIBECRAFTED_PRODUCT_ENTRY_PROBE:-0}" == "1" ]]; then
    _vetcoders_product_entry_prepare "$root" || return $?
    _vetcoders_product_entry_probe_print
    return $?
  fi

  # 3. The authoritative inventory, before any window, record or provider.
  _vetcoders_start_read_inventory_state "$session_name"
  state="$_vetcoders_start_inventory_state"
  case "$state" in
    error)
      _vetcoders_start_refuse_inventory "$session_name"
      return $?
      ;;
    dead)
      _vetcoders_start_refuse_existing_workspace "$session_name" dead "$root"
      return $?
      ;;
    live)
      if [[ "${VIBECRAFTED_START_CREATED_SESSION:-}" != "$session_name" ]]; then
        _vetcoders_start_refuse_existing_workspace "$session_name" live "$root"
        return $?
      fi
      ;;
  esac

  # An attached caller enters through Frame's native per-client switch. It
  # already has a frontend even when this tool invocation itself has no TTY.
  if _vetcoders_in_vc_frame; then
    _vetcoders_product_entry_prepare "$root" || return $?
    _vetcoders_start_launch_workspace "$session_name" "$root"
    return $?
  fi

  # 4+5 without a surface: create here, then open the terminal that enters.
  if ! _vetcoders_start_is_owned_terminal_child && [[ ! -t 0 || ! -t 1 ]]; then
    if [[ "$state" != "live" ]]; then
      _vetcoders_start_create_before_terminal "$session_name" "$root" || return $?
      printf 'vc-start: created workspace %s for %s\n' \
        "$(_vetcoders_shell_quote "$session_name")" "$(_vetcoders_shell_quote "$root")"
    fi
    export VIBECRAFTED_START_CREATED_SESSION="$session_name"
    _vetcoders_start_open_terminal_if_needed strict "$root" "$@" --repo "$root" || rc=$?
    unset VIBECRAFTED_START_CREATED_SESSION
    if ((rc != 0)); then
      printf 'vc-start: workspace %s exists (detached) but no terminal could be opened for it; attach with: vc-dashboard attach %s\n' \
        "$(_vetcoders_shell_quote "$session_name")" "$(_vetcoders_shell_quote "$session_name")" >&2
      return "$rc"
    fi
    return 0
  fi

  # 4+5 with a surface (a real terminal, or the child the terminal opened).
  _vetcoders_product_entry_prepare "$root" || return $?
  _vetcoders_start_launch_workspace "$session_name" "$root"
}
