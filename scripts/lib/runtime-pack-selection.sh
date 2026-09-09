# shellcheck shell=bash
#
# Build → install handoff for the standalone Runtime Pack.
#
# `make runtime-pack && make install` used to lose the identity of the artifact
# in between: the builder knew the exact path it had just signed, threw it away,
# and both the Make recipe and the installer re-derived a name from the CURRENT
# HEAD, date and a hard-coded `dist`. With eighteen legitimate historical packs
# on disk the installer's glob found many and refused, so a successful build was
# never the pack that got installed.
#
# The producer already holds the answer, so it writes it down. This file is the
# single owner of that record: the producer marks an attempt PENDING before it
# can fail, publishes READY only after the archive is packaged, its Mach-O
# payload verified and its detached signature created, and the installer reads
# the result instead of guessing.
#
# What this record deliberately is NOT:
#   * not a release database — `dist/release-output.json` remains the App+DMG
#     tuple, and an old one is not proof of a later standalone pack;
#   * not user configuration — it is build state under the ignored `build/`;
#   * not a trust anchor — it SELECTS bytes, it never authenticates them. The
#     checksum, detached signature, archive topology and internal provenance
#     gates in install-runtime-pack.sh stay exactly where they are, and the
#     receipt merely feeds them the identity the producer captured.
#
# Selection never consults mtime, glob order, or a shortened SHA, and it never
# removes a historical archive to make an answer unambiguous.

RUNTIME_PACK_SELECTION_SCHEMA="vibecrafted.runtime-pack-selection.v1"
RUNTIME_PACK_SELECTION_BASENAME="runtime-pack-selection.json"

# The record lives beside the build tree, not inside `build/unified-release`,
# which the App lane deletes mid-build.
runtime_pack_selection_file() {
  printf '%s\n' "$1/build/$RUNTIME_PACK_SELECTION_BASENAME"
}

runtime_pack_selection_attempt_id() {
  printf '%s-%s-%s\n' "$(date -u +%Y%m%dT%H%M%SZ)" "$$" "${RANDOM}${RANDOM}"
}

# Values are written as bare JSON strings, so a value that would need escaping
# is refused at the source rather than emitted as something a reader could
# misparse. Spaces are legal (a release directory may contain them); quotes,
# backslashes and control characters are not.
runtime_pack_selection_safe_value() {
  case "$1" in
    *\"*|*\\*) return 1 ;;
  esac
  if [[ "$1" == *$'\n'* || "$1" == *$'\t'* || "$1" == *$'\r'* ]]; then
    return 1
  fi
  return 0
}

runtime_pack_selection_sha256() {
  local target="$1"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$target" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$target" | awk '{print $1}'
  else
    return 1
  fi
}

runtime_pack_selection_size() {
  # BSD and GNU stat disagree; both ship on the platforms that build packs.
  stat -f %z "$1" 2>/dev/null || stat -c %s "$1" 2>/dev/null
}

# Read one string field. Deliberately anchored to a top-level, single-line
# `"name": "value"` pair: this reader only ever accepts records this file
# wrote. A value carrying a backslash is treated as malformed rather than
# unescaped by hand.
runtime_pack_selection_field() {
  local file="$1" name="$2" value
  value="$(sed -n \
    's/^  "'"$name"'": "\(.*\)",\{0,1\}$/\1/p' "$file" 2>/dev/null | head -n 1)"
  [[ -n "$value" ]] || return 1
  case "$value" in
    *\\*) return 1 ;;
  esac
  printf '%s\n' "$value"
}

runtime_pack_selection_write() {
  local file="$1"
  shift
  local directory tmp key value
  directory="${file%/*}"
  mkdir -p "$directory" || return 1
  tmp="$file.tmp.$$"
  : > "$tmp" || return 1
  printf '{\n' >> "$tmp"
  # Walk the key/value pairs positionally: array index bases differ between
  # shells, and this record is read back by a fixed line shape that must not
  # depend on which shell happened to write it.
  while (($# >= 2)); do
    key="$1"
    value="$2"
    shift 2
    if ! runtime_pack_selection_safe_value "$value"; then
      rm -f "$tmp"
      printf 'runtime pack selection: refusing to record unrepresentable %s: %s\n' \
        "$key" "$value" >&2
      return 1
    fi
    if (($# == 0)); then
      printf '  "%s": "%s"\n' "$key" "$value" >> "$tmp"
    else
      printf '  "%s": "%s",\n' "$key" "$value" >> "$tmp"
    fi
  done
  printf '}\n' >> "$tmp"
  mv -f "$tmp" "$file"
}

# Claim the attempt BEFORE anything that can fail. An interrupted or failed
# build therefore leaves a pending record, and `make install` refuses loudly
# instead of quietly installing whatever succeeded last time — including the
# case where the retry runs at the very same source SHA.
runtime_pack_selection_begin() {
  local repo_root="$1" attempt="$2" source_revision="$3"
  local file
  file="$(runtime_pack_selection_file "$repo_root")"
  runtime_pack_selection_write "$file" \
    schema "$RUNTIME_PACK_SELECTION_SCHEMA" \
    status pending \
    attempt "$attempt" \
    source_revision "$source_revision" \
    started_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

# Publish only the attempt that is still the current one. Two concurrent
# builders cannot let the one that STARTED first overwrite the selection that
# belongs to the newer attempt; the loser leaves the newer record untouched.
runtime_pack_selection_publish() {
  local repo_root="$1" attempt="$2" pack="$3" version="$4" platform="$5"
  local architecture="$6" source_revision="$7" terminal_revision="$8"
  local frame_revision="$9"
  local file current digest size
  file="$(runtime_pack_selection_file "$repo_root")"
  if [[ -f "$file" ]]; then
    current="$(runtime_pack_selection_field "$file" attempt || true)"
    if [[ -n "$current" && "$current" != "$attempt" ]]; then
      printf 'runtime pack selection: a newer build attempt (%s) owns the record; not publishing %s\n' \
        "$current" "$attempt" >&2
      return 0
    fi
  fi
  [[ -f "$pack" ]] || return 1
  digest="$(runtime_pack_selection_sha256 "$pack")" || return 1
  size="$(runtime_pack_selection_size "$pack")" || return 1
  runtime_pack_selection_write "$file" \
    schema "$RUNTIME_PACK_SELECTION_SCHEMA" \
    status ready \
    attempt "$attempt" \
    pack "$pack" \
    carrier_basename "${pack##*/}" \
    sha256 "$digest" \
    size "$size" \
    version "$version" \
    platform "$platform" \
    architecture "$architecture" \
    source_revision "$source_revision" \
    terminal_revision "$terminal_revision" \
    frame_revision "$frame_revision" \
    completed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

# Resolve the record into RUNTIME_PACK_SELECTION_* variables.
#
# Exit codes are the contract:
#   0 — a ready, self-consistent record for this platform
#   1 — no record at all (callers may keep their legacy single-archive path)
#   2 — a record exists but cannot be honoured; the caller MUST fail visibly
#       rather than fall back to some other archive on disk
runtime_pack_selection_read() {
  local repo_root="$1" expected_platform="$2" expected_architecture="$3"
  local file schema status pack digest size actual_digest actual_size

  RUNTIME_PACK_SELECTION_PACK=""
  RUNTIME_PACK_SELECTION_SHA256=""
  RUNTIME_PACK_SELECTION_VERSION=""
  RUNTIME_PACK_SELECTION_PLATFORM=""
  RUNTIME_PACK_SELECTION_ARCHITECTURE=""
  RUNTIME_PACK_SELECTION_SOURCE_REVISION=""
  RUNTIME_PACK_SELECTION_TERMINAL_REVISION=""
  RUNTIME_PACK_SELECTION_FRAME_REVISION=""
  RUNTIME_PACK_SELECTION_ERROR=""

  file="$(runtime_pack_selection_file "$repo_root")"
  [[ -f "$file" ]] || return 1

  schema="$(runtime_pack_selection_field "$file" schema || true)"
  if [[ "$schema" != "$RUNTIME_PACK_SELECTION_SCHEMA" ]]; then
    RUNTIME_PACK_SELECTION_ERROR="build selection record has an unknown schema: $file"
    return 2
  fi
  status="$(runtime_pack_selection_field "$file" status || true)"
  case "$status" in
    ready) ;;
    pending)
      RUNTIME_PACK_SELECTION_ERROR="the last Runtime Pack build did not complete; rebuild it or pass an explicit pack"
      return 2
      ;;
    *)
      RUNTIME_PACK_SELECTION_ERROR="build selection record has no usable status: $file"
      return 2
      ;;
  esac

  pack="$(runtime_pack_selection_field "$file" pack || true)"
  if [[ -z "$pack" || "$pack" != /* || "$pack" != *.tar.gz ]]; then
    RUNTIME_PACK_SELECTION_ERROR="build selection record names no absolute Runtime Pack carrier: $file"
    return 2
  fi
  if [[ ! -f "$pack" ]]; then
    RUNTIME_PACK_SELECTION_ERROR="the recorded Runtime Pack is gone: $pack"
    return 2
  fi
  if [[ "${pack##*/}" != "$(runtime_pack_selection_field "$file" carrier_basename || true)" ]]; then
    RUNTIME_PACK_SELECTION_ERROR="build selection record disagrees with itself about the carrier name: $file"
    return 2
  fi

  # The digest is what makes the record name BYTES rather than a path. A pack
  # swapped underneath a valid-looking record is refused here, before the
  # installer's own trust chain would even be handed a candidate.
  digest="$(runtime_pack_selection_field "$file" sha256 || true)"
  if [[ ! "$digest" =~ ^[0-9a-f]{64}$ ]]; then
    RUNTIME_PACK_SELECTION_ERROR="build selection record has no usable digest: $file"
    return 2
  fi
  actual_digest="$(runtime_pack_selection_sha256 "$pack")" || {
    RUNTIME_PACK_SELECTION_ERROR="cannot digest the recorded Runtime Pack: $pack"
    return 2
  }
  if [[ "$actual_digest" != "$digest" ]]; then
    RUNTIME_PACK_SELECTION_ERROR="the recorded Runtime Pack no longer matches its recorded digest: $pack"
    return 2
  fi
  size="$(runtime_pack_selection_field "$file" size || true)"
  actual_size="$(runtime_pack_selection_size "$pack")" || true
  if [[ -n "$size" && -n "$actual_size" && "$size" != "$actual_size" ]]; then
    RUNTIME_PACK_SELECTION_ERROR="the recorded Runtime Pack no longer matches its recorded size: $pack"
    return 2
  fi

  RUNTIME_PACK_SELECTION_PLATFORM="$(runtime_pack_selection_field "$file" platform || true)"
  RUNTIME_PACK_SELECTION_ARCHITECTURE="$(runtime_pack_selection_field "$file" architecture || true)"
  if [[ -n "$expected_platform" \
        && "$RUNTIME_PACK_SELECTION_PLATFORM" != "$expected_platform" ]]; then
    RUNTIME_PACK_SELECTION_ERROR="recorded Runtime Pack targets $RUNTIME_PACK_SELECTION_PLATFORM, not $expected_platform"
    return 2
  fi
  if [[ -n "$expected_architecture" \
        && "$RUNTIME_PACK_SELECTION_ARCHITECTURE" != "$expected_architecture" ]]; then
    RUNTIME_PACK_SELECTION_ERROR="recorded Runtime Pack targets $RUNTIME_PACK_SELECTION_ARCHITECTURE, not $expected_architecture"
    return 2
  fi

  RUNTIME_PACK_SELECTION_VERSION="$(runtime_pack_selection_field "$file" version || true)"
  RUNTIME_PACK_SELECTION_SOURCE_REVISION="$(runtime_pack_selection_field "$file" source_revision || true)"
  RUNTIME_PACK_SELECTION_TERMINAL_REVISION="$(runtime_pack_selection_field "$file" terminal_revision || true)"
  RUNTIME_PACK_SELECTION_FRAME_REVISION="$(runtime_pack_selection_field "$file" frame_revision || true)"
  if [[ ! "$RUNTIME_PACK_SELECTION_SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
    RUNTIME_PACK_SELECTION_ERROR="build selection record has no full source revision: $file"
    return 2
  fi
  RUNTIME_PACK_SELECTION_PACK="$pack"
  RUNTIME_PACK_SELECTION_SHA256="$digest"
  return 0
}
