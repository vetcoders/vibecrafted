# shellcheck shell=bash

_vetcoders_spawn_home() {
  local tool="$1"
  local crafted_home="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}"
  local xdg_data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
  local crafted_tools_home="${VIBECRAFTED_TOOLS_HOME:-$xdg_data_home/vibecrafted/tools}"
  local crafted_runtime="$crafted_home/runtime"
  local crafted_store="$crafted_home/skills/vc-agents"
  local current_runtime="$crafted_tools_home/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime"
  local current_store="$crafted_tools_home/vibecrafted-current/vibecrafted-core/vibecrafted_core/skills/vc-agents"
  local repo_root
  repo_root="${VIBECRAFTED_ROOT:-$(_vetcoders_repo_root)}"
  local legacy_store="$HOME/.runtime/skills/vc-agents"
  local candidate

  if [[ -f "$repo_root/VERSION" ]]; then
    # Installed generations physically own runtime/; source checkouts own the
    # same tree inside the Python package. Neither path is an alias.
    candidate="$repo_root/runtime"
    if [[ -d "$candidate/scripts" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
    candidate="$repo_root/vibecrafted-core/vibecrafted_core/runtime"
    if [[ -d "$candidate/scripts" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  fi

  for candidate in \
    "$current_runtime" \
    "$crafted_runtime" \
    "$current_store" \
    "$crafted_store" \
    "$legacy_store"; do
    [[ -d "$candidate/scripts" ]] || continue
    printf '%s' "$candidate"
    return 0
  done

  printf '%s' "$current_runtime"
}

_vetcoders_spawn_script() {
  local tool="$1"
  local script_name="$2"
  local base
  base="$(_vetcoders_spawn_home "$tool")"
  [[ -f "$base/scripts/$script_name" ]] || {
    echo "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. spawn script not found: $base/scripts/$script_name" >&2
    return 1
  }
  printf '%s/scripts/%s' "$base" "$script_name"
}

_vetcoders_workflow_script() {
  # Workflow-owned scripts live in runtime/<workflow>/, next to the shared
  # runtime/scripts/ surface that _vetcoders_spawn_script resolves.
  local workflow="$1"
  local script_path="$2"
  local base
  base="$(_vetcoders_spawn_home "$workflow")"
  [[ -f "$base/$workflow/$script_path" ]] || {
    echo "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. workflow script not found: $base/$workflow/$script_path" >&2
    return 1
  }
  printf '%s/%s/%s' "$base" "$workflow" "$script_path"
}

_vetcoders_repo_root() {
  git rev-parse --show-toplevel 2>/dev/null || pwd
}

_vetcoders_org_repo() {
  local root="${1:-$(_vetcoders_repo_root)}"
  local org_repo=""
  org_repo="$(cd "$root" && git remote get-url origin 2>/dev/null | sed -E 's|.*[:/]([^/]+)/([^/.]+)(\.git)?$|\1/\2|' || true)"
  if [[ -n "$org_repo" ]]; then
    printf '%s\n' "$org_repo"
  else
    printf '%s\n' "$(basename "$root")"
  fi
}

_vetcoders_store_dir() {
  local root="${1:-$(_vetcoders_repo_root)}"
  local crafted_home="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}"
  local date_dir
  date_dir="$(date +%Y_%m%d)"
  printf '%s/artifacts/%s/%s\n' "$crafted_home" "$(_vetcoders_org_repo "$root")" "$date_dir"
}

_vetcoders_tmp_dir() {
  local root="${1:-$(_vetcoders_repo_root)}"
  local dir
  dir="$(_vetcoders_store_dir "$root")/tmp"
  mkdir -p "$dir"
  printf '%s\n' "$dir"
}

_vetcoders_tmp_script_path() {
  local prefix="$1"
  local root="${2:-$(_vetcoders_repo_root)}"
  local dir stamp context

  dir="$(_vetcoders_tmp_dir "$root")" || return 1
  stamp="$(_vetcoders_spawn_timestamp)"
  context="${VIBECRAFTED_RUN_ID:-${VIBECRAFTED_SKILL_CODE:-$(_vetcoders_session_base_name)}}"
  context="$(printf '%s' "$context" | tr -cs '[:alnum:]._-' '-')"
  context="${context#-}"
  context="${context%-}"
  [[ -n "$context" ]] || context="session"

  mktemp "${dir%/}/${prefix}.${stamp}_${context}.XXXXXX"
}

_vetcoders_research_run_dir() {
  local root="$1"
  local run_id="$2"
  printf '%s/research/%s\n' "$(_vetcoders_store_dir "$root")" "$run_id"
}

_vetcoders_research_prompt_file() {
  local run_dir="$1"
  local prompt_text="$2"
  local ts slug prompt_file

  ts="$(_vetcoders_spawn_timestamp)"
  slug="$(printf '%s' "$prompt_text" | tr '\n' ' ' | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//' | cut -c1-48)"
  [[ -n "$slug" ]] || slug="research-plan"

  mkdir -p "$run_dir/plans"
  prompt_file="$run_dir/plans/${ts}_${slug}_research-plan.md"
  printf '%s\n' "$prompt_text" > "$prompt_file"
  printf '%s\n' "$prompt_file"
}

_vetcoders_write_research_summary() {
  local run_dir="$1"
  local run_id="$2"
  local root="$3"
  local prompt_file="$4"
  shift 4
  local summary_file="$run_dir/summary.md"
  local entry agent launcher label agents_csv

  cat > "$summary_file" <<EOF
# Research Run: $run_id

Root: $root
Prompt: $prompt_file
Await: vc-research-await --run-id $run_id

## Reports

EOF

  agents_csv=""
  for entry in "$@"; do
    agent="${entry%%=*}"
    launcher="${entry#*=}"
    [[ -n "$agent" && "$agent" != "$entry" ]] || continue
    label="$(printf '%s' "$agent" | awk '{print toupper(substr($0,1,1)) substr($0,2)}')"
    agents_csv="${agents_csv:+$agents_csv,}$agent"
    printf -- '- %s: %s/reports/%s.md\n' "$label" "$run_dir" "$agent" >> "$summary_file"
  done

  cat >> "$summary_file" <<EOF

## Internal Logs

- Metadata: $run_dir/logs/{${agents_csv}}.meta.json
- Transcripts: $run_dir/logs/{${agents_csv}}.transcript.log
- Launchers and runtime prompts: $run_dir/tmp/

## Launchers

EOF

  for entry in "$@"; do
    agent="${entry%%=*}"
    launcher="${entry#*=}"
    [[ -n "$agent" && "$agent" != "$entry" ]] || continue
    label="$(printf '%s' "$agent" | awk '{print toupper(substr($0,1,1)) substr($0,2)}')"
    printf -- '- %s: %s\n' "$label" "$launcher" >> "$summary_file"
  done

  printf '%s\n' "$summary_file"
}

_vetcoders_marbles_probe_ttl() {
  printf '%s\n' "${VIBECRAFTED_MARBLES_PROBE_TTL:-10}"
}

_vetcoders_marbles_emit_probe() {
  local root="$1"
  local marbles_run_id="$2"
  # NOTE: do NOT name this `status`. In zsh `status` is a read-only special
  # parameter mirroring `$?`; `local status=...` crashes the probe with
  # "read-only variable: status" the moment it runs under the operator's zsh.
  # Same class of bug as 24478f9 (zsh special `module_path`). Keep it renamed.
  local probe_status="${3:-launched}"
  local title body delay_s

  [[ "${VIBECRAFTED_MARBLES_PROBE_NOTIFY:-1}" == "1" ]] || return 0

  (cd "$root" >/dev/null 2>&1 || true)
  delay_s="$(_vetcoders_marbles_probe_ttl)"
  case "$probe_status" in
    launched)
      title="Marbles ${marbles_run_id}"
      body="Run launched · tab: marbles-${marbles_run_id}"
      ;;
    done|completed|converged)
      title="Marbles ${marbles_run_id} ✓"
      body="Run completed · inspect: vc-marbles inspect ${marbles_run_id}"
      ;;
    failed)
      title="Marbles ${marbles_run_id} ✗"
      body="Run failed · check tab: marbles-${marbles_run_id}"
      ;;
    stopped)
      title="Marbles ${marbles_run_id}"
      body="Run stopped · inspect: vc-marbles inspect ${marbles_run_id}"
      ;;
    *)
      title="Marbles ${marbles_run_id}"
      body="Status: ${probe_status}"
      ;;
  esac

  # Detach: probe must NEVER block the operator shell.
  (
    local tray_bin=""
    tray_bin="${VIBECRAFTED_TRAY_NOTIFY_BIN:-}"
    [[ -n "$tray_bin" ]] || tray_bin="$(command -v vc-mux-tray 2>/dev/null || true)"
    if [[ -n "$tray_bin" && -x "$tray_bin" ]]; then
      "$tray_bin" notify --title "$title" --message "$body" >/dev/null 2>&1 || true
    elif command -v notify-send >/dev/null 2>&1; then
      notify-send -t "$((delay_s * 1000))" "$title" "$body" >/dev/null 2>&1 || true
    else
      printf '\a[marbles %s] %s\n' "$marbles_run_id" "$body" >&2
    fi
  ) </dev/null >/dev/null 2>&1 &
  disown 2>/dev/null || true
}

_vetcoders_session_base_name() {
  local root base
  root="$(_vetcoders_session_scope_root)"
  base="$(basename "$root" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g; s/^-*//; s/-*$//')"
  [[ -n "$base" ]] || base="vibecrafted"
  printf '%s\n' "$base"
}

_vetcoders_vc_frame_session_scope() {
  case "${VIBECRAFTED_VC_FRAME_SESSION_SCOPE:-repo}" in
    folder) printf 'folder\n' ;;
    repo|*) printf 'repo\n' ;;
  esac
}

_vetcoders_session_scope_root() {
  case "$(_vetcoders_vc_frame_session_scope)" in
    folder)
      pwd -P
      ;;
    *)
      _vetcoders_repo_root
      ;;
  esac
}

_vetcoders_vc_frame_session_max_length() {
  printf '24\n'
}

_vetcoders_short_hash() {
  local value="$1"
  local hash=""
  hash="$(printf '%s' "$value" | shasum -a 256 2>/dev/null || printf '%s' "$value" | sha256sum 2>/dev/null)" || return 1
  hash="${hash%% *}"
  printf '%.4s\n' "$hash"
}

_vetcoders_string_prefix() {
  local value="$1"
  local length="${2:-0}"
  if (( length <= 0 )); then
    return 0
  fi
  printf '%s' "$value" | cut -c "1-${length}"
}

_vetcoders_compact_session_name() {
  local full_name="$1"
  local preserved_tail="${2:-}"
  local max_len hash prefix_len prefix compact

  max_len="${3:-$(_vetcoders_vc_frame_session_max_length)}"
  if (( ${#full_name} <= max_len )); then
    printf '%s\n' "$full_name"
    return 0
  fi

  hash="$(_vetcoders_short_hash "$full_name" 2>/dev/null || true)"
  [[ -n "$hash" ]] || hash="sess"

  if [[ -n "$preserved_tail" ]]; then
    prefix_len=$(( max_len - ${#preserved_tail} - ${#hash} - 2 ))
    if (( prefix_len > 0 )); then
      prefix="$(_vetcoders_string_prefix "$full_name" "$prefix_len")"
      prefix="${prefix%-}"
      [[ -n "$prefix" ]] || prefix="$(_vetcoders_string_prefix "$hash" 1)"
      compact="${prefix}-${hash}-${preserved_tail}"
      if (( ${#compact} <= max_len )); then
        printf '%s\n' "$compact"
        return 0
      fi
    fi
  fi

  prefix_len=$(( max_len - ${#hash} - 1 ))
  (( prefix_len > 0 )) || prefix_len=1
  prefix="$(_vetcoders_string_prefix "$full_name" "$prefix_len")"
  prefix="${prefix%-}"
  [[ -n "$prefix" ]] || prefix="$(_vetcoders_string_prefix "$hash" 1)"
  compact="${prefix}-${hash}"
  printf '%.24s\n' "$compact"
}

_vetcoders_operator_session_name_for_run_id() {
  local run_id="${1:-}"
  local base
  base="$(_vetcoders_session_base_name)"
  if [[ -n "$run_id" ]]; then
    _vetcoders_compact_session_name "${base}-${run_id}" "$run_id"
  else
    _vetcoders_compact_session_name "$base"
  fi
}

# Interactive rail is a place (repo/display label), never a run-id or
# catalog-fallback workspace-{8hex} token. Run-bound names stay available
# via _vetcoders_operator_session_name_for_run_id for leak detection only.
_vetcoders_operator_place_session_name() {
  if [[ "$(_vetcoders_vc_frame_session_scope)" == folder ]]; then
    _vetcoders_session_base_name
    return 0
  fi
  local root_dir="${SPAWN_ROOT:-${VIBECRAFTED_ROOT:-${_vetcoders_contract_root:-}}}"
  if [[ -z "$root_dir" ]]; then
    root_dir="$(_vetcoders_repo_root 2>/dev/null || pwd)"
  fi
  local resolved=""
  if command -v python3 >/dev/null 2>&1; then
    resolved="$(
      SPAWN_ROOT="$root_dir" VIBECRAFTED_ROOT="$root_dir" python3 - <<'PY' 2>/dev/null
import os
from pathlib import Path
root = os.environ.get("SPAWN_ROOT") or os.environ.get("VIBECRAFTED_ROOT") or os.getcwd()
try:
    from vibecrafted_core.workspace_catalog import resolve_operator_place_session
    print(resolve_operator_place_session(root=root, env=os.environ), end="")
except Exception:
    print(Path(root).name or "vibecrafted", end="")
PY
    )" || resolved=""
  fi
  if [[ -n "$resolved" ]]; then
    printf '%s\n' "$resolved"
    return 0
  fi
  _vetcoders_session_base_name
}

_vetcoders_is_legacy_operator_session_name() {
  local name="${1:-}"
  case "$name" in
    workspace-[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f])
      return 0
      ;;
  esac
  return 1
}

_vetcoders_operator_face_tab() {
  local tool="${1:-}"
  tool="${tool##*/}"
  [[ -n "$tool" ]] || return 1
  printf '%s\n' "$tool"
}

_vetcoders_expected_run_lock_path() {
  local run_id="${1:-}"
  local root="${2:-$(_vetcoders_repo_root)}"
  [[ -n "$run_id" ]] || return 1
  printf '%s/locks/%s/%s.lock\n' \
    "${VIBECRAFTED_HOME:-$HOME/.vibecrafted}" \
    "$(_vetcoders_org_repo "$root")" \
    "$run_id"
}

_vetcoders_normalize_ambient_context() {
  local run_id lock expected_lock operator_session expected_session

  run_id="${VIBECRAFTED_RUN_ID:-}"
  lock="${VIBECRAFTED_RUN_LOCK:-}"
  operator_session="${VIBECRAFTED_OPERATOR_SESSION:-}"

  [[ -n "$run_id" ]] || {
    unset VIBECRAFTED_RUN_LOCK VIBECRAFTED_SKILL_CODE VIBECRAFTED_SKILL_NAME
    return 0
  }

  [[ -n "$lock" ]] || return 0

  expected_lock="$(_vetcoders_expected_run_lock_path "$run_id" 2>/dev/null || true)"
  if [[ -n "$expected_lock" && "$lock" == "$expected_lock" && -f "$lock" ]]; then
    return 0
  fi

  expected_session="$(_vetcoders_operator_session_name_for_run_id "$run_id")"
  unset VIBECRAFTED_RUN_LOCK VIBECRAFTED_SKILL_CODE VIBECRAFTED_SKILL_NAME

  if [[ "$(basename "$lock")" != "${run_id}.lock" ]]; then
    if [[ -n "$operator_session" && "$operator_session" != "$expected_session" ]]; then
      unset VIBECRAFTED_OPERATOR_SESSION
    fi
    return 0
  fi

  unset VIBECRAFTED_RUN_ID
  if [[ -n "$operator_session" ]]; then
    unset VIBECRAFTED_OPERATOR_SESSION
  fi
}

_vetcoders_skill_prefix() {
  local name="${1:-}"
  case "$name" in
    agents) printf 'agnt\n' ;;
    decorate) printf 'deco\n' ;;
    delegate) printf 'delg\n' ;;
    dou) printf 'vdou\n' ;;
    followup) printf 'fwup\n' ;;
    hydrate) printf 'hydr\n' ;;
    implement|prompt) printf 'impl\n' ;;
    init) printf 'init\n' ;;
    justdo) printf 'just\n' ;;
    marbles) printf 'marb\n' ;;
    partner) printf 'prtn\n' ;;
    polarize) printf 'polr\n' ;;
    plan) printf 'plan\n' ;;
    prune) printf 'prun\n' ;;
    release) printf 'rels\n' ;;
    research) printf 'rsch\n' ;;
    review) printf 'rvew\n' ;;
    scaffold) printf 'scaf\n' ;;
    workflow) printf 'wflw\n' ;;
    *)
      if [[ -n "$name" ]]; then
        printf '%.4s\n' "$name"
      else
        printf 'impl\n'
      fi
      ;;
  esac
}

_vetcoders_generate_run_id() {
  local prefix="$1"
  local entropy=""
  entropy="$(python3 - <<'PY' 2>/dev/null || true
import os
print(f"{int.from_bytes(os.urandom(3), 'big') % 100000:05d}")
PY
)"
  [[ -n "$entropy" ]] || entropy="$(printf '%05d' "$(( (${RANDOM:-0} * 32768 + ${RANDOM:-0}) % 100000 ))")"
  # Canonical run-id grammar — identical to
  # vibecrafted_core.workflow.reserve_run_id ({code}-{%y%m%d-%H%M%S}-{entropy}).
  # ONE id shape across the Python dispatcher and every shell fallback keeps
  # control-plane records, observe/await resolution, and report frontmatter on a
  # single identity grammar; the old prefix-HHMMSS-<pid><entropy> shape produced
  # ids the canonical resolvers could not reconcile, orphaning a phantom
  # control-plane record beside the real run. Entropy (time_ns % 100000) defuses
  # same-second collisions exactly as the Python allocator does; the date segment
  # disambiguates across days. Kept identical to spawn_generate_run_id.
  printf '%s-%s-%s\n' "$prefix" "$(date +%y%m%d-%H%M%S)" "$entropy"
}

_vetcoders_spawn_timestamp() {
  if [[ -n "${VIBECRAFTED_SPAWN_TS:-}" ]]; then
    printf '%s\n' "${VIBECRAFTED_SPAWN_TS}"
  else
    date +%Y%m%d_%H%M
  fi
}

_vetcoders_marbles_store_dir() {
  local root="$1"
  if cd "$root" && git remote get-url origin >/dev/null 2>&1; then
    printf '%s/marbles\n' "$(_vetcoders_store_dir "$root")"
  else
    printf '%s/.vibecrafted/marbles\n' "$root"
  fi
}

_vetcoders_marbles_l1_report_path() {
  local root="$1"
  local stamp="$2"
  local tool="$3"
  printf '%s/reports/%s_marbles-ancestor_L1_%s.md\n' \
    "$(_vetcoders_marbles_store_dir "$root")" \
    "$stamp" \
    "$tool"
}

_vetcoders_has_ambient_spawn_context() {
  [[ -n "${SPAWN_AGENT:-}" ]] || return 1
  [[ -n "${SPAWN_RUN_ID:-}" ]] || return 1
  [[ -n "${VIBECRAFTED_RUN_ID:-}" ]] || return 1
  [[ "${SPAWN_RUN_ID}" == "${VIBECRAFTED_RUN_ID}" ]] || return 1
  [[ -z "${VIBECRAFTED_OPERATOR_SESSION:-}" ]] || return 1
  _vetcoders_in_vc_frame && return 1
  return 0
}

_vetcoders_effective_run_id() {
  _vetcoders_normalize_ambient_context
  _vetcoders_has_ambient_spawn_context && return 1
  [[ -n "${VIBECRAFTED_RUN_ID:-}" ]] || return 1
  printf '%s\n' "${VIBECRAFTED_RUN_ID}"
}

_vetcoders_effective_run_lock() {
  _vetcoders_normalize_ambient_context
  _vetcoders_has_ambient_spawn_context && return 1
  [[ -n "${VIBECRAFTED_RUN_LOCK:-}" ]] || return 1
  printf '%s\n' "${VIBECRAFTED_RUN_LOCK}"
}

_vetcoders_effective_skill_name() {
  _vetcoders_normalize_ambient_context
  _vetcoders_has_ambient_spawn_context && return 1
  [[ -n "${VIBECRAFTED_SKILL_NAME:-}" ]] || return 1
  printf '%s\n' "${VIBECRAFTED_SKILL_NAME}"
}

_vetcoders_effective_skill_code() {
  _vetcoders_normalize_ambient_context
  _vetcoders_has_ambient_spawn_context && return 1
  [[ -n "${VIBECRAFTED_SKILL_CODE:-}" ]] || return 1
  printf '%s\n' "${VIBECRAFTED_SKILL_CODE}"
}

_vetcoders_create_run_lock() {
  local run_id="$1"
  local agent="$2"
  local skill="$3"
  local root="$4"
  local org_repo lock_dir lock_file
  org_repo="$(_vetcoders_org_repo "$root")"
  lock_dir="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/locks/$org_repo"
  mkdir -p "$lock_dir"
  lock_file="$lock_dir/${run_id}.lock"
  cat > "$lock_file" <<LOCK
run_id=$run_id
agent=$agent
skill=$skill
root=$root
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
status=running
LOCK
  printf '%s\n' "$lock_file"
}

_vetcoders_spawn_root_arg() {
  local arg
  while [[ $# -gt 0 ]]; do
    arg="$1"
    shift
    case "$arg" in
      --root)
        [[ $# -gt 0 ]] || break
        printf '%s\n' "$1"
        return 0
        ;;
    esac
  done
  return 1
}

_vetcoders_ensure_run_context() {
  local tool="$1"
  local mode="$2"
  local root="${3:-$(_vetcoders_repo_root)}"
  local skill_name
  local skill_code
  local run_id
  local lock_file

  skill_name="$(_vetcoders_effective_skill_name 2>/dev/null || true)"
  [[ -n "$skill_name" ]] || skill_name="$mode"
  skill_code="$(_vetcoders_effective_skill_code 2>/dev/null || true)"
  run_id="$(_vetcoders_effective_run_id 2>/dev/null || true)"
  lock_file="$(_vetcoders_effective_run_lock 2>/dev/null || true)"

  [[ -n "$skill_code" ]] || skill_code="$(_vetcoders_skill_prefix "$skill_name")"
  [[ -n "${VIBECRAFTED_SKILL_NAME:-}" ]] || export VIBECRAFTED_SKILL_NAME="$skill_name"
  export VIBECRAFTED_SKILL_CODE="$skill_code"

  # Preserve the first run_id created for this workflow so prompts, locks,
  # operator sessions, and spawned workers stay traceable as one run.
  if [[ -z "$run_id" ]]; then
    run_id="$(_vetcoders_generate_run_id "$skill_code")"
  fi
  export VIBECRAFTED_RUN_ID="$run_id"

  if [[ -z "$lock_file" || ! -f "$lock_file" ]]; then
    lock_file="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/locks/$(_vetcoders_org_repo "$root")/${run_id}.lock"
  fi
  if [[ ! -f "$lock_file" ]]; then
    lock_file="$(_vetcoders_create_run_lock "$run_id" "$tool" "$skill_name" "$root")"
  fi
  export VIBECRAFTED_RUN_LOCK="$lock_file"
}
