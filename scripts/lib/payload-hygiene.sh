#!/usr/bin/env bash
# ============================================================================
# payload-hygiene.sh — the release-side entry to the payload anonymity gate
# ============================================================================
# The scanning is done by scripts/payload_hygiene.py, which is deliberately
# producer-agnostic: it reads finished bytes and knows nothing about rustc,
# Swift, cc-rs, uv or WASM. This file's only job is to decide WHICH literals a
# release must never contain, so both release channels ask the same question.
#
# The literal set is exactly "every absolute path that exists only on the build
# host": the operator's home, the checkout, both donors, and — under
# --snapshot-donors — the ephemeral snapshot roots. If one of these appears in
# a shipped byte, a customer can read the founder's account name and directory
# layout out of a signed, notarized artifact.
#
# Measured 2026-08-18 on Vibecrafted_4.1.0-20260817-237d2814.dmg: 8 of 2955
# files offended, across five unrelated producers. See payload_hygiene.py for
# the breakdown and why no single compiler flag closes it.
#
# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by VetCoders (c)2024-2026 LibraxisAI

# payload_hygiene_literals — one build-host-only absolute path per line.
#
# Reads the release scripts' own variables when they are set; every one of them
# is optional so the function is usable from a test with nothing exported.
payload_hygiene_literals() {
  local candidate
  for candidate in \
    "${HOME:-}" \
    "${PAYLOAD_HYGIENE_REPO_ROOT:-${REPO_ROOT:-}}" \
    "${TERMINAL_DONOR:-}" \
    "${FRAME_DONOR:-}" \
    "${TERMINAL_REPO:-}" \
    "${FRAME_REPO:-}" \
    "$@"
  do
    # `/` and the empty string would match the entire payload; the scanner
    # refuses them too, but not emitting them keeps the failure honest.
    [[ -n "$candidate" && "$candidate" != "/" ]] || continue
    printf '%s\n' "${candidate%/}"
  done | sort -u
}

# assert_payload_is_anonymous <root> <label> [extra-literal...]
#
# Dies through the caller's `die` when the payload names the build host. The
# caller owns `die`; every script that sources this file has one.
assert_payload_is_anonymous() {
  local root="$1" label="$2"
  shift 2
  local script_dir literal
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

  local -a arguments=(--root "$root" --label "$label")
  while IFS= read -r literal; do
    [[ -n "$literal" ]] || continue
    arguments+=(--forbid "$literal")
  done < <(payload_hygiene_literals "$@")

  python3 "$script_dir/payload_hygiene.py" "${arguments[@]}" \
    || die "$label leaks build-host paths; refusing to ship it"
}
