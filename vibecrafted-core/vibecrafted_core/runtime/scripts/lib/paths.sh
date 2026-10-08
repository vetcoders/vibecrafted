#!/usr/bin/env bash

# Central artifact store: $HOME/.vibecrafted/artifacts/<org>/<repo>/<YYYY_MMDD>/
# Override with VIBECRAFTED_HOME env var for custom location
# Falls back to <repo>/.vibecrafted/ if git remote unavailable
VIBECRAFTED_HOME="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}"

spawn_repo_root() {
  git rev-parse --show-toplevel 2>/dev/null || pwd
}

spawn_store_dir() {
  local root="${1:-$(spawn_repo_root)}"
  local org_repo=""
  org_repo="$(spawn_org_repo "$root" 0)"
  if [[ -n "$org_repo" ]]; then
    local date_dir
    date_dir="$(date +%Y_%m%d)"
    printf '%s/artifacts/%s/%s\n' "$VIBECRAFTED_HOME" "$org_repo" "$date_dir"
  else
    # Fallback: per-repo
    printf '%s/.vibecrafted\n' "$root"
  fi
}

spawn_effective_store_dir() {
  local root="${1:-$(spawn_repo_root)}"
  local store_root_hint="${2:-}"
  local resolved_root resolved_store_root=""

  resolved_root="$(spawn_abspath "$root")"
  if [[ -n "${VIBECRAFTED_STORE_DIR:-}" ]]; then
    resolved_store_root="${VIBECRAFTED_STORE_ROOT:-$store_root_hint}"
    if [[ -n "$resolved_store_root" ]] && [[ "$resolved_root" == "$(spawn_abspath "$resolved_store_root")" ]]; then
      spawn_abspath "$VIBECRAFTED_STORE_DIR"
      return 0
    fi
  fi
  spawn_store_dir "$resolved_root"
}

spawn_tmp_dir() {
  local root="${1:-${SPAWN_ROOT:-$(spawn_repo_root)}}"
  local tmp_dir="${SPAWN_TMP_DIR:-}"

  if [[ -z "$tmp_dir" ]]; then
    tmp_dir="$(spawn_effective_store_dir "$root" "${VIBECRAFTED_STORE_ROOT:-}")/tmp"
  fi

  mkdir -p "$tmp_dir"
  printf '%s\n' "$tmp_dir"
}

spawn_tmp_script_path() {
  local prefix="$1"
  local root="${2:-${SPAWN_ROOT:-$(spawn_repo_root)}}"
  local tmp_dir stamp context

  tmp_dir="$(spawn_tmp_dir "$root")" || return 1
  stamp="${SPAWN_TS:-$(spawn_timestamp)}"
  context="${SPAWN_RUN_ID:-${VIBECRAFTED_RUN_ID:-${SPAWN_SKILL_CODE:-${VIBECRAFTED_SKILL_CODE:-session}}}}"
  context="$(printf '%s' "$context" | tr -cs '[:alnum:]._-' '-')"
  context="${context#-}"
  context="${context%-}"
  [[ -n "$context" ]] || context="session"

  mktemp "${tmp_dir%/}/${prefix}.${stamp}_${context}.XXXXXX"
}

spawn_marbles_store_dir() {
  local root="${1:-$(spawn_repo_root)}"
  printf '%s/marbles\n' "$(spawn_store_dir "$root")"
}

spawn_abspath() {
  local path="$1"
  if [[ "$path" == /* ]]; then
    printf '%s\n' "$path"
  else
    printf '%s/%s\n' "$(cd "$(dirname "$path")" && pwd)" "$(basename "$path")"
  fi
}

spawn_slug_from_path() {
  local raw
  raw="$(basename "${1%.*}")"
  raw="$(printf '%s' "$raw" | tr ' ' '-' | tr -cs '[:alnum:]._-' '-')"
  raw="${raw#-}"
  raw="${raw%-}"
  [[ -n "$raw" ]] || raw="agent-task"
  # Truncate to max 60 chars to prevent filesystem-busting filenames.
  # Cut at a word boundary (dash or underscore) to keep slugs readable.
  if (( ${#raw} > 60 )); then
    raw="${raw:0:60}"
    # Trim trailing partial word
    raw="${raw%-}"
    [[ -n "$raw" ]] || raw="${1:0:60}"
  fi
  printf '%s\n' "$raw"
}

spawn_link_repo_artifacts() {
  local store_base="$1"
  local repo_root="$2"
  local repo_vibecrafted="$repo_root/.vibecrafted"

  [[ -n "$store_base" && -n "$repo_root" ]] || return 0
  [[ "$store_base" != "$repo_root/.vibecrafted" ]] || return 0

  # Durable plans/reports already live under VIBECRAFTED_HOME (spawn_store_dir).
  # The $repo_root/.vibecrafted/{plans,reports} links are a convenience view for
  # an operator project. Tests pin VIBECRAFTED_HOME at a fixture and keep cwd at
  # the source checkout so git remote still names this repo; writing the view
  # into that cwd leaves untracked symlinks in the product tree.
  if [[ "${VIBECRAFTED_TEST_MODE:-0}" == "1" ]]; then
    mkdir -p "$store_base/plans" "$store_base/reports"
    return 0
  fi

  mkdir -p "$repo_vibecrafted" "$store_base/plans" "$store_base/reports"
  ln -sfn "$store_base/plans" "$repo_vibecrafted/plans" 2>/dev/null || true
  ln -sfn "$store_base/reports" "$repo_vibecrafted/reports" 2>/dev/null || true
}
