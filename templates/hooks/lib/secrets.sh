# shellcheck shell=bash
# vibecrafted-husky-template :: lib/secrets.sh
#
# Canonical secret regex + scan helpers. Sourced after lib/core.sh.

# Canonical secret regex — unified across the Vetcoders hook templates.
# Covers: OpenAI sk-*, GitHub PAT/OAuth, Slack xox*, AWS access keys,
# Google API keys, OAuth refresh tokens, PEM private keys, product-specific
# bearer tokens, and Authorization: Bearer headers in plaintext.
HUSKY_SECRET_REGEX='(sk-(proj|live|test|dev)?-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36,}|gho_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|ya29\.[0-9A-Za-z_-]{20,}|sk_live_[A-Za-z0-9]{24,}|pk_live_[A-Za-z0-9]{24,}|-----BEGIN (RSA|OPENSSH|EC|DSA|PGP) PRIVATE KEY-----|vista-[A-Za-z0-9_-]{20,})'

HUSKY_BEARER_REGEX='Authorization:[[:space:]]*Bearer[[:space:]]+[A-Za-z0-9._-]{16,}'

# husky_secrets_scan_diff <git-diff-args...>
# Pipes the diff through grep with the canonical regex. Returns 0 on hit,
# 1 on clean. The caller decides whether to block or demote.
husky_secrets_scan_diff() {
  local matches
  matches="$(git diff "$@" --unified=0 --no-color \
    -- ':!docs/*' ':!**/__tests__/*' ':!**/*.test.*' ':!**/*.spec.*' ':!**/*.json' \
    2>/dev/null \
    | grep -E '^\+' \
    | grep -vE '^\+\+\+' \
    | sed 's/^+//' \
    | grep -E "${HUSKY_SECRET_REGEX}|${HUSKY_BEARER_REGEX}" \
    || true)"
  if [ -n "$matches" ]; then
    husky_err "Secret/token detected in added lines:"
    # Redact at the source: never echo the raw secret to stderr (it would
    # land in terminal scrollback / CI logs). Callers may also redact the
    # whole stream downstream — double-redaction is idempotent.
    printf '%s\n' "$matches" | husky_secrets_redact | sed 's/^/    + /' >&2
    return 0
  fi
  return 1
}

# husky_secrets_scan_staged
# Wrapper for pre-commit (cached changes).
husky_secrets_scan_staged() {
  if husky_secrets_scan_diff --cached; then
    if [ "${HUSKY_ALLOW_SECRETS:-0}" = "1" ]; then
      husky_warn "HUSKY_ALLOW_SECRETS=1 override active — secret scan bypassed."
      return 0
    fi
    return 1
  fi
  return 0
}

# husky_secrets_scan_range <base> <head>
# Wrapper for pre-push (range diff).
husky_secrets_scan_range() {
  local base="$1"
  local head="$2"
  if husky_secrets_scan_diff "$base" "$head"; then
    if [ "${HUSKY_ALLOW_SECRETS:-0}" = "1" ]; then
      husky_warn "HUSKY_ALLOW_SECRETS=1 override active — secret scan bypassed."
      return 0
    fi
    return 1
  fi
  return 0
}

# husky_secrets_redact <input>
# Reads stdin, redacts any matching secret pattern, writes to stdout.
# Used to scrub hook logs before archival.
husky_secrets_redact() {
  if command -v node >/dev/null 2>&1 && [ -f "$HUSKY_SCRIPTS_DIR/redact-output.mjs" ]; then
    node "$HUSKY_SCRIPTS_DIR/redact-output.mjs"
  else
    # Best-effort sed fallback (less precise but no node dependency).
    sed -E "s/${HUSKY_SECRET_REGEX}/<REDACTED>/g; s/${HUSKY_BEARER_REGEX}/Authorization: Bearer <REDACTED>/g"
  fi
}
