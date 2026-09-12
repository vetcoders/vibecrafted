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
# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI

# Directories every macOS or Linux box has. An ancestor walk must stop here: a
# payload that mentions `/Users` or `/Volumes` says nothing about who built it,
# and forbidding one would flag every legitimate path reference in the tree.
_PAYLOAD_HYGIENE_GENERIC_ROOTS=$'/\n/Applications\n/Library\n/System\n/Users\n/Volumes\n/home\n/media\n/mnt\n/opt\n/private\n/private/tmp\n/private/var\n/srv\n/tmp\n/usr\n/var'

# payload_hygiene_topmost_host_root <absolute-path>
#
# Print the highest ancestor of <absolute-path> that is still specific to this
# host, or nothing when the path sits directly under a generic root.
#
# MEASURED 2026-08-18 on Vibecrafted_4.1.0-20260818-c52f1326-portable.tar.gz:
# forbidding only the exact checkout named 5 offending files, while forbidding
# the workshop one level up named 12. The seven in the difference — among them
# vibecrafted_core/runtime_receipt.py, control-core/src/read.rs and
# tui-agent/src/state.rs — leak the workshop directory that sits ABOVE the
# checkout. Substring matching cannot see them from the checkout root: the
# workshop is a prefix of it, never a substring of the payload's own text. The
# gate promised "must not carry the operator's account or checkout" and, for
# every path one level up, quietly certified the opposite.
payload_hygiene_topmost_host_root() {
  local path="${1%/}" parent topmost=""
  [[ -n "$path" && "$path" != "/" ]] || return 0
  while :; do
    parent="$(dirname "$path")"
    [[ "$parent" != "$path" ]] || break
    if printf '%s\n' "$_PAYLOAD_HYGIENE_GENERIC_ROOTS" | grep -qxF -- "$parent"; then
      break
    fi
    topmost="$parent"
    path="$parent"
  done
  [[ -z "$topmost" ]] || printf '%s\n' "$topmost"
}

# payload_hygiene_literals — one build-host-only absolute path per line.
#
# Reads the release scripts' own variables when they are set; every one of them
# is optional so the function is usable from a test with nothing exported.
#
# Emits the workshop above each root as well: the topmost still-host-specific
# ancestor subsumes every longer path under it, so one literal closes the whole
# blind spot without drowning the report in near-duplicate matches.
# PAYLOAD_HYGIENE_EPHEMERAL_ROOTS — newline-separated absolute paths that
# identify nobody: the standard home and workspace roots of a hosted CI runner.
# Those roots are identical across the hosted macOS fleet, so naming them says
# nothing about who built the payload.
# This is NOT an allowlist of payload strings: the scanner still refuses every
# literal that survives, and a root is only ephemeral when the caller declares
# it so. Unset (the operator boundary) changes nothing.
payload_hygiene_is_ephemeral() {
  local path="${1%/}" root
  [[ -n "${PAYLOAD_HYGIENE_EPHEMERAL_ROOTS:-}" ]] || return 1
  while IFS= read -r root; do
    root="${root%/}"
    [[ -n "$root" && "$root" != "/" ]] || continue
    [[ "$path" == "$root" || "$path" == "$root"/* ]] && return 0
  done <<< "$PAYLOAD_HYGIENE_EPHEMERAL_ROOTS"
  return 1
}

payload_hygiene_literals() {
  local root
  local -a ancestors=()
  for root in \
    "${HOME:-}" \
    "${PAYLOAD_HYGIENE_REPO_ROOT:-${REPO_ROOT:-}}" \
    "${TERMINAL_DONOR:-}" \
    "${FRAME_DONOR:-}" \
    "${TERMINAL_REPO:-}" \
    "${FRAME_REPO:-}"
  do
    [[ -n "$root" ]] || continue
    while IFS= read -r ancestor; do
      [[ -n "$ancestor" ]] || continue
      ancestors+=("$ancestor")
    done < <(payload_hygiene_topmost_host_root "$root")
  done

  # PAYLOAD_HYGIENE_EXTRA_LITERALS — newline-separated literals a caller adds on
  # top of the build-host set. A hosted runner declares its own roots ephemeral
  # and then has nothing of its own to forbid; what it must still prove is that
  # the payload does not name the OPERATOR whose keys sign it. The operator's
  # home and workshop are those literals.
  local -a extra=()
  if [[ -n "${PAYLOAD_HYGIENE_EXTRA_LITERALS:-}" ]]; then
    local line
    while IFS= read -r line; do
      [[ -n "$line" ]] && extra+=("$line")
    done <<< "$PAYLOAD_HYGIENE_EXTRA_LITERALS"
  fi

  local candidate
  for candidate in \
    "${HOME:-}" \
    "${PAYLOAD_HYGIENE_REPO_ROOT:-${REPO_ROOT:-}}" \
    "${TERMINAL_DONOR:-}" \
    "${FRAME_DONOR:-}" \
    "${TERMINAL_REPO:-}" \
    "${FRAME_REPO:-}" \
    "${ancestors[@]+"${ancestors[@]}"}" \
    "${extra[@]+"${extra[@]}"}" \
    "$@"
  do
    # `/` and the empty string would match the entire payload; the scanner
    # refuses them too, but not emitting them keeps the failure honest.
    [[ -n "$candidate" && "$candidate" != "/" ]] || continue
    payload_hygiene_is_ephemeral "$candidate" && continue
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
