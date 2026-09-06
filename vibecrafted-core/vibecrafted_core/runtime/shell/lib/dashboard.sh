# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_dashboard_layout_name() {
  local requested="${1:-dashboard}"
  case "$requested" in
    ""|dashboard|mc|mission-control|vc-dashboard) printf 'dashboard\n' ;;
    marbles|vc-marbles) printf 'marbles\n' ;;
    polarize|vc-polarize) printf 'polarize\n' ;;
    workflow|vc-workflow) printf 'workflow\n' ;;
    research|vc-research) printf 'research\n' ;;
    operator|vibecrafted) printf 'operator\n' ;;
    *)
      echo "Unknown dashboard layout: $requested" >&2
      # shellcheck disable=SC2154 # sourced from core.sh by the facade.
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
  local source_file="${BASH_SOURCE[0]}" core_dir product_root python_bin python_dir checkout_python project_python embedded_python
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
  core_dir="${VIBECRAFTED_CORE_DIR:-$(cd "$(dirname "$source_file")/../../../.." && pwd)}"
  product_root="$(cd "$core_dir/.." && pwd)"
  checkout_python="$product_root/.venv/bin/python3"
  project_python="$product_root/scripts/project-python"
  embedded_python="$product_root/bin/python3"
  if [[ -n "${VIBECRAFTED_PYTHON:-}" && -x "$VIBECRAFTED_PYTHON" ]]; then
    python_bin="$VIBECRAFTED_PYTHON"
  elif [[ -x "$checkout_python" ]]; then
    python_bin="$checkout_python"
  elif [[ -x "$embedded_python" ]]; then
    python_bin="$embedded_python"
  elif [[ -x "$project_python" ]]; then
    python_bin="$project_python"
  else
    python_bin="python3"
  fi
  python_dir=""
  [[ "$python_bin" == */* ]] && python_dir="$(dirname "$python_bin")"
  [[ -f "$core_dir/vibecrafted_core/cli.py" ]] || return 1
  PATH="${python_dir:+$python_dir:}${PATH:-}" \
    XDG_CONFIG_HOME="$config_home" \
    PYTHONPATH="$core_dir${PYTHONPATH:+:$PYTHONPATH}" \
    "$python_bin" -m vibecrafted_core.cli "$@"
}

_vetcoders_product_workspace_prepare() {
  local requested_root="${1:-}"
  local line key value resolved resolve_status=0
  if [[ -z "$requested_root" ]]; then
    requested_root="$(pwd -P)" || return $?
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
    export VIBECRAFTED_OPERATOR_SESSION="$(_vetcoders_operator_session_name)"
  fi
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

# Product lifecycle choke shared by shell `vc-start` and deck `cmd_start`.
# Reads installed product config and scripts, then prepares workspace/control
# state. Configuration publication belongs exclusively to explicit installation.
_vetcoders_product_entry_prepare() {
  local requested_root owner_root required entry_status=0
  requested_root="$(pwd -P)" || return $?
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
    unset VIBECRAFTED_PRODUCT_CORE_CLI PYTHONPATH
    if [[ ! -x "$VIBECRAFTED_PYTHON" || ! -f "$VIBECRAFTED_CORE_DIR/vibecrafted_core/cli.py" ]]; then
      printf 'vc-start: selected runtime Python/core missing under: %s\n' "$owner_root" >&2
      printf 'Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>\n' >&2
      VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS=1
      return 1
    fi
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
  # control-plane eye — AppDelegate/vc-start start with a closed allowlist.
  if declare -F _vetcoders_path_with_bundled_bin_priority >/dev/null 2>&1; then
    PATH="$(_vetcoders_path_with_bundled_bin_priority "${PATH:-}")"
    export PATH
  fi
  _vetcoders_product_workspace_prepare "$requested_root" || {
    entry_status=$?
    VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS="$entry_status"
    return "$entry_status"
  }
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
  if declare -F _vetcoders_dashboard_layout_file >/dev/null 2>&1; then
    layout="$(_vetcoders_dashboard_layout_file operator 2>/dev/null || true)"
  fi
  if [[ -z "$layout" && -n "${VC_FRAME_CONFIG_DIR:-}" ]]; then
    layout="${VC_FRAME_CONFIG_DIR%/}/layouts/operator.kdl"
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

  local layout_name layout_file session_name state inside_vc_frame current_session vc_frame_bin
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

  layout_file="$(_vetcoders_dashboard_layout_file "$layout_name" 2>/dev/null || true)"
  [[ -n "$layout_file" ]] || {
    echo "Dashboard layout not found for: $layout_name" >&2
    printf 'Expected: %s/layouts/%s.kdl\n' "$(_vetcoders_vc_frame_config_dir)" "$layout_name" >&2
    echo "Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>" >&2
    return 1
  }

  session_name="$(_vetcoders_dashboard_session_name "$layout_name")"
  state="$(_vetcoders_vc_frame_session_state "$session_name")"
  # Trusted attached-context signal only: stale VC_FRAME/ZELLIJ leaks in a
  # parent shell must not route new-tab/switch-session at a session this
  # terminal is not actually attached to.
  _vetcoders_in_vc_frame && inside_vc_frame=1 || inside_vc_frame=0
  current_session="${VC_FRAME_SESSION_NAME:-${ZELLIJ_SESSION_NAME:-}}"

  if [[ "$layout_name" != "operator" && "$layout_name" != "dashboard" && "$state" == "live" ]]; then
    if (( inside_vc_frame )) && [[ "$current_session" == "$session_name" ]]; then
      "$vc_frame_bin" action new-tab --layout "$layout_file"
    else
      "$vc_frame_bin" --session "$session_name" action new-tab --layout "$layout_file"
      if (( inside_vc_frame )); then
        "$vc_frame_bin" action switch-session "$session_name"
      else
        "$vc_frame_bin" attach "$session_name"
      fi
    fi
    return 0
  fi

  if (( inside_vc_frame )) && [[ "$current_session" == "$session_name" ]]; then
    printf 'Already in Vibecrafted workspace: %s\n' "$session_name"
    return 0
  fi

  if _vetcoders_ensure_vc_frame_session "$session_name" "$layout_file" "$@"; then
    export VIBECRAFTED_OPERATOR_SESSION="${VIBECRAFTED_PREPARED_VC_FRAME_SESSION:-$session_name}"
    export VC_FRAME_SESSION_NAME="$VIBECRAFTED_OPERATOR_SESSION"
    return 0
  fi
  return 1
}

_vetcoders_resume_operator_session() {
  if [[ -n "${VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS:-}" ]]; then
    printf 'vc-start: product preparation failed; resume was not attempted.\n' >&2
    return "$VIBECRAFTED_PRODUCT_ENTRY_ERROR_STATUS"
  fi
  local session_name layout_file
  _vetcoders_normalize_ambient_context
  session_name="$(_vetcoders_operator_session_name)"
  layout_file="$(_vetcoders_operator_layout_file)" || {
    printf 'vc-start: operator layout missing under: %s\n' "$(_vetcoders_vc_frame_config_dir)" >&2
    printf 'Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>\n' >&2
    return 1
  }

  if _vetcoders_ensure_vc_frame_session "$session_name" "$layout_file"; then
    export VIBECRAFTED_OPERATOR_SESSION="${VIBECRAFTED_PREPARED_VC_FRAME_SESSION:-$session_name}"
    export VC_FRAME_SESSION_NAME="$VIBECRAFTED_OPERATOR_SESSION"
    return 0
  fi
  return 1
}
