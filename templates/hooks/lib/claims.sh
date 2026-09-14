# shellcheck shell=bash
# Living Tree claim-bus adapter. The registry remains the only authority.

husky_claims_runtime_root() {
  local root="${VIBECRAFTED_ROOT:-}"
  if [ -n "$root" ] && [ -d "$root/vibecrafted-core/vibecrafted_core" ]; then
    printf '%s\n' "$root"
    return 0
  fi
  local current="${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted/tools/vibecrafted-current"
  if [ -e "$current" ]; then
    root="$(cd "$(dirname "$current")" && cd "$(readlink "$current" 2>/dev/null || basename "$current")" 2>/dev/null && pwd -P || true)"
    if [ -n "$root" ] && [ -d "$root/vibecrafted-core/vibecrafted_core" ]; then
      printf '%s\n' "$root"
      return 0
    fi
  fi
  return 1
}

husky_claims_invoke() {
  local root python
  root="$(husky_claims_runtime_root 2>/dev/null || true)"
  if [ -n "$root" ]; then
    for python in "$root/bin/python3" "$root/python/bin/python3"; do
      [ -x "$python" ] || continue
      PYTHONPATH="$root/vibecrafted-core" "$python" \
        -m vibecrafted_core.repository_claims "$@"
      return $?
    done
  fi
  if command -v vibecrafted >/dev/null 2>&1; then
    vibecrafted claims "$@"
    return $?
  fi
  return 127
}

husky_claims_check_staged() {
  local paths=()
  local path
  while IFS= read -r -d '' path; do
    paths+=("$path")
  done < <(git diff --cached --name-only --diff-filter=ACMR -z)
  [ "${#paths[@]}" -gt 0 ] || return 0

  local output rc=0
  output="$(husky_claims_invoke --json check \
    --repo "$HUSKY_REPO_ROOT" \
    --run-id "${VIBECRAFTED_RUN_ID:-}" \
    --session-id "${VIBECRAFTED_SESSION_ID:-${CODEX_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-}}}" \
    -- "${paths[@]}" 2>&1)" || rc=$?

  if printf '%s' "$output" | grep -q '"action": "check"'; then
    if [ "$rc" -eq 0 ]; then
      return 0
    fi
    printf '%s\n' "$output" >&2
    husky_err "Staged paths overlap another live session claim. Unstage them or coordinate an audited release."
    return 1
  fi
  if [ "$rc" -eq 127 ]; then
    husky_warn "Vibecrafted claim runtime unavailable — continuing with Git-only integrity gates."
    return 0
  fi
  husky_warn "Claim-bus probe unavailable or from an older runtime — continuing in documented degraded mode."
  return 0
}
