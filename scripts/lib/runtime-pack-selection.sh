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

# Every key this owner may write, in the order a record carries them. A record
# is one COMPLETE generation or it is nothing; there is no partial dialect.
RUNTIME_PACK_SELECTION_PENDING_KEYS="schema status attempt source_revision started_at"
RUNTIME_PACK_SELECTION_READY_KEYS="schema status attempt pack carrier_basename sha256 size version platform architecture source_revision terminal_revision frame_revision completed_at"

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

# A relative VIBECRAFTED_RELEASE_DIR is a supported way to put the carrier
# somewhere other than dist, and the reader deliberately accepts only absolute
# carriers — a relative one means something different from every directory. So
# the producer resolves the path it is about to record, once, here. The carrier
# exists by now, therefore its directory does, therefore `cd` can canonicalise
# it: `.`, `..` and a symlinked release dir cannot leave two spellings of one
# pack. Spaces survive because nothing here word-splits.
runtime_pack_selection_absolute_path() {
  local target="$1" directory base
  [[ -n "$target" ]] || return 1
  base="${target##*/}"
  [[ -n "$base" ]] || return 1
  if [[ "$target" == */* ]]; then
    directory="${target%/*}"
    [[ -n "$directory" ]] || directory="/"
  else
    directory="."
  fi
  directory="$(cd "$directory" >/dev/null 2>&1 && pwd)" || return 1
  if [[ "$directory" == "/" ]]; then
    printf '/%s\n' "$base"
  else
    printf '%s/%s\n' "$directory" "$base"
  fi
}

# Parse ONE coherent, complete record into RUNTIME_PACK_SELECTION_REC_* fields.
#
# Every read of the record goes through here, and every read consumes the whole
# file from a single open. That is what makes a generation coherent: the writer
# publishes by atomic rename, so an opened descriptor always sees exactly one
# generation. Pulling fields out one at a time — the shape this file used to
# have — could pair an old `pack` with a new `sha256`.
#
# Structure is the contract, not merely the presence of a matching line:
#   * the first line is `{` and the LAST line is `}`, so a truncated record
#     (no closing brace) is refused instead of read as if it were whole;
#   * every line between them is exactly one `  "key": "value"` pair, with a
#     trailing comma on all but the last, so nothing else can hide in there;
#   * a key may appear once — a duplicate `status` or `pack` is a refusal, not
#     a silent first-wins;
#   * a key outside this owner's schema is a refusal.
#
# Returns 0 on a well-formed record, 1 otherwise. Field semantics (which keys a
# given status requires) belong to the reader below.
runtime_pack_selection_parse() {
  local file="$1"
  local line index total key value comma seen="" pattern
  # Values carry no quote and no backslash by construction; a record claiming
  # otherwise was not written by runtime_pack_selection_write.
  pattern='^  "([a-z0-9_]+)": "([^"\]*)"(,?)$'

  RUNTIME_PACK_SELECTION_REC_schema=""
  RUNTIME_PACK_SELECTION_REC_status=""
  RUNTIME_PACK_SELECTION_REC_attempt=""
  RUNTIME_PACK_SELECTION_REC_pack=""
  RUNTIME_PACK_SELECTION_REC_carrier_basename=""
  RUNTIME_PACK_SELECTION_REC_sha256=""
  RUNTIME_PACK_SELECTION_REC_size=""
  RUNTIME_PACK_SELECTION_REC_version=""
  RUNTIME_PACK_SELECTION_REC_platform=""
  RUNTIME_PACK_SELECTION_REC_architecture=""
  RUNTIME_PACK_SELECTION_REC_source_revision=""
  RUNTIME_PACK_SELECTION_REC_terminal_revision=""
  RUNTIME_PACK_SELECTION_REC_frame_revision=""
  RUNTIME_PACK_SELECTION_REC_started_at=""
  RUNTIME_PACK_SELECTION_REC_completed_at=""
  RUNTIME_PACK_SELECTION_REC_KEYS=""

  local lines
  lines=()
  # A trailing fragment without its newline is still a line: that is how a
  # half-written record gets seen and refused rather than quietly ignored.
  while IFS= read -r line || [[ -n "$line" ]]; do
    lines[${#lines[@]}]="$line"
  done < "$file" || return 1

  total=${#lines[@]}
  # `{`, at least one field, `}` — an empty object records nothing.
  ((total >= 3)) || return 1
  [[ "${lines[0]}" == "{" ]] || return 1
  [[ "${lines[$((total - 1))]}" == "}" ]] || return 1

  index=1
  while ((index < total - 1)); do
    line="${lines[$index]}"
    [[ "$line" =~ $pattern ]] || return 1
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    comma="${BASH_REMATCH[3]}"
    if ((index < total - 2)); then
      [[ "$comma" == "," ]] || return 1
    else
      [[ -z "$comma" ]] || return 1
    fi
    case " $seen " in
      *" $key "*) return 1 ;;
    esac
    seen="$seen $key"
    case "$key" in
      schema) RUNTIME_PACK_SELECTION_REC_schema="$value" ;;
      status) RUNTIME_PACK_SELECTION_REC_status="$value" ;;
      attempt) RUNTIME_PACK_SELECTION_REC_attempt="$value" ;;
      pack) RUNTIME_PACK_SELECTION_REC_pack="$value" ;;
      carrier_basename) RUNTIME_PACK_SELECTION_REC_carrier_basename="$value" ;;
      sha256) RUNTIME_PACK_SELECTION_REC_sha256="$value" ;;
      size) RUNTIME_PACK_SELECTION_REC_size="$value" ;;
      version) RUNTIME_PACK_SELECTION_REC_version="$value" ;;
      platform) RUNTIME_PACK_SELECTION_REC_platform="$value" ;;
      architecture) RUNTIME_PACK_SELECTION_REC_architecture="$value" ;;
      source_revision) RUNTIME_PACK_SELECTION_REC_source_revision="$value" ;;
      terminal_revision) RUNTIME_PACK_SELECTION_REC_terminal_revision="$value" ;;
      frame_revision) RUNTIME_PACK_SELECTION_REC_frame_revision="$value" ;;
      started_at) RUNTIME_PACK_SELECTION_REC_started_at="$value" ;;
      completed_at) RUNTIME_PACK_SELECTION_REC_completed_at="$value" ;;
      *) return 1 ;;
    esac
    index=$((index + 1))
  done
  RUNTIME_PACK_SELECTION_REC_KEYS="${seen# }"
  return 0
}

# The key set must be EXACTLY the one this status writes. Missing keys mean an
# incomplete record; foreign keys mean two generations were mixed into one file
# — a pending body wearing a ready status, or the reverse.
runtime_pack_selection_keys_match() {
  local expected="$1" actual="$2" key
  for key in $expected; do
    case " $actual " in
      *" $key "*) ;;
      *) return 1 ;;
    esac
  done
  for key in $actual; do
    case " $expected " in
      *" $key "*) ;;
      *) return 1 ;;
    esac
  done
  return 0
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
    if [[ -z "$value" ]]; then
      rm -f "$tmp"
      printf 'runtime pack selection: refusing to record an empty %s\n' "$key" >&2
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

# Serialise the record's writers.
#
# Atomic rename gives a reader a whole generation; it does NOT give a writer
# compare-and-swap. Reading the current attempt and then writing is a
# check-then-act, and the window between the two is exactly as long as it takes
# to digest a several-hundred-megabyte archive. Under this lock the ownership
# check and the publication that depends on it are one indivisible step against
# a concurrent begin.
#
# `mkdir` is the atomic primitive available to every shell on every platform
# that builds packs. A builder killed mid-publication would otherwise wedge
# every later build, so the holder stamps its pid and a dead holder's lock is
# broken rather than waited out.
runtime_pack_selection_lock() {
  local file="$1" lock="$1.lock" waited=0 holder
  mkdir -p "${file%/*}" || return 1
  while ! mkdir "$lock" 2>/dev/null; do
    holder="$(cat "$lock/pid" 2>/dev/null || true)"
    if [[ -n "$holder" ]] && ! kill -0 "$holder" 2>/dev/null; then
      runtime_pack_selection_drop_lock "$lock"
      continue
    fi
    waited=$((waited + 1))
    if ((waited > 600)); then
      printf 'runtime pack selection: timed out waiting for %s\n' "$lock" >&2
      return 1
    fi
    sleep 0.1
  done
  printf '%s\n' "$$" > "$lock/pid"
}

# Release exactly one lock and nothing else. The one thing this file must never
# be able to do is delete an artifact, so the release path removes the single
# file it wrote and then rmdirs -- a directory that is not this lock, or holds
# anything this file did not put there, survives.
runtime_pack_selection_drop_lock() {
  local lock="$1"
  rm -f "$lock/pid"
  rmdir "$lock" 2>/dev/null
}

runtime_pack_selection_unlock() {
  runtime_pack_selection_drop_lock "$1.lock"
}

# Claim the attempt BEFORE anything that can fail. An interrupted or failed
# build therefore leaves a pending record, and `make install` refuses loudly
# instead of quietly installing whatever succeeded last time — including the
# case where the retry runs at the very same source SHA.
runtime_pack_selection_begin() {
  local repo_root="$1" attempt="$2" source_revision="$3"
  local file status=0
  file="$(runtime_pack_selection_file "$repo_root")"
  runtime_pack_selection_lock "$file" || return 1
  runtime_pack_selection_write "$file" \
    schema "$RUNTIME_PACK_SELECTION_SCHEMA" \
    status pending \
    attempt "$attempt" \
    source_revision "$source_revision" \
    started_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" || status=1
  runtime_pack_selection_unlock "$file"
  return $status
}

# Measure the carrier: the expensive half of publication, deliberately OUTSIDE
# the lock. Splitting it from the commit below is not only a throughput choice
# — it is what makes the losing interleaving reproducible without sleeps: a
# test can measure, let a second builder begin, and only then commit.
runtime_pack_selection_measure() {
  local pack="$1" resolved digest size
  RUNTIME_PACK_SELECTION_MEASURED_PACK=""
  RUNTIME_PACK_SELECTION_MEASURED_SHA256=""
  RUNTIME_PACK_SELECTION_MEASURED_SIZE=""
  [[ -f "$pack" ]] || return 1
  resolved="$(runtime_pack_selection_absolute_path "$pack")" || return 1
  digest="$(runtime_pack_selection_sha256 "$resolved")" || return 1
  size="$(runtime_pack_selection_size "$resolved")" || return 1
  [[ -n "$digest" && -n "$size" ]] || return 1
  RUNTIME_PACK_SELECTION_MEASURED_PACK="$resolved"
  RUNTIME_PACK_SELECTION_MEASURED_SHA256="$digest"
  RUNTIME_PACK_SELECTION_MEASURED_SIZE="$size"
}

# Publish the measured carrier, but only while this attempt still owns the
# record. The check and the write happen under one lock, so a builder that
# began after this one measured cannot be overwritten by it: the stale winner
# steps aside and the newer attempt's pending record survives to refuse
# `make install`. Stepping aside is not an error — that build succeeded, it
# simply is no longer the answer.
runtime_pack_selection_commit() {
  local repo_root="$1" attempt="$2" version="$3" platform="$4"
  local architecture="$5" source_revision="$6" terminal_revision="$7"
  local frame_revision="$8"
  local file current="" status=0
  [[ -n "$RUNTIME_PACK_SELECTION_MEASURED_PACK" ]] || return 1
  file="$(runtime_pack_selection_file "$repo_root")"
  runtime_pack_selection_lock "$file" || return 1
  if [[ -f "$file" ]] && runtime_pack_selection_parse "$file"; then
    current="$RUNTIME_PACK_SELECTION_REC_attempt"
  fi
  if [[ -n "$current" && "$current" != "$attempt" ]]; then
    runtime_pack_selection_unlock "$file"
    printf 'runtime pack selection: a newer build attempt (%s) owns the record; not publishing %s\n' \
      "$current" "$attempt" >&2
    return 0
  fi
  runtime_pack_selection_write "$file" \
    schema "$RUNTIME_PACK_SELECTION_SCHEMA" \
    status ready \
    attempt "$attempt" \
    pack "$RUNTIME_PACK_SELECTION_MEASURED_PACK" \
    carrier_basename "${RUNTIME_PACK_SELECTION_MEASURED_PACK##*/}" \
    sha256 "$RUNTIME_PACK_SELECTION_MEASURED_SHA256" \
    size "$RUNTIME_PACK_SELECTION_MEASURED_SIZE" \
    version "$version" \
    platform "$platform" \
    architecture "$architecture" \
    source_revision "$source_revision" \
    terminal_revision "$terminal_revision" \
    frame_revision "$frame_revision" \
    completed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" || status=1
  runtime_pack_selection_unlock "$file"
  return $status
}

runtime_pack_selection_publish() {
  local repo_root="$1" attempt="$2" pack="$3" version="$4" platform="$5"
  local architecture="$6" source_revision="$7" terminal_revision="$8"
  local frame_revision="$9"
  runtime_pack_selection_measure "$pack" || return 1
  runtime_pack_selection_commit "$repo_root" "$attempt" "$version" "$platform" \
    "$architecture" "$source_revision" "$terminal_revision" "$frame_revision"
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
  local file actual_digest actual_size

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

  if ! runtime_pack_selection_parse "$file"; then
    RUNTIME_PACK_SELECTION_ERROR="build selection record is truncated, duplicated or otherwise not one whole record: $file"
    return 2
  fi
  if [[ "$RUNTIME_PACK_SELECTION_REC_schema" != "$RUNTIME_PACK_SELECTION_SCHEMA" ]]; then
    RUNTIME_PACK_SELECTION_ERROR="build selection record has an unknown schema: $file"
    return 2
  fi
  case "$RUNTIME_PACK_SELECTION_REC_status" in
    ready)
      if ! runtime_pack_selection_keys_match \
        "$RUNTIME_PACK_SELECTION_READY_KEYS" "$RUNTIME_PACK_SELECTION_REC_KEYS"; then
        RUNTIME_PACK_SELECTION_ERROR="build selection record is not the complete set of fields a finished build writes: $file"
        return 2
      fi
      ;;
    pending)
      if ! runtime_pack_selection_keys_match \
        "$RUNTIME_PACK_SELECTION_PENDING_KEYS" "$RUNTIME_PACK_SELECTION_REC_KEYS"; then
        RUNTIME_PACK_SELECTION_ERROR="build selection record mixes an unfinished build with fields only a finished one writes: $file"
        return 2
      fi
      RUNTIME_PACK_SELECTION_ERROR="the last Runtime Pack build did not complete; rebuild it or pass an explicit pack"
      return 2
      ;;
    *)
      RUNTIME_PACK_SELECTION_ERROR="build selection record has no usable status: $file"
      return 2
      ;;
  esac

  local pack="$RUNTIME_PACK_SELECTION_REC_pack"
  if [[ "$pack" != /* || "$pack" != *.tar.gz ]]; then
    RUNTIME_PACK_SELECTION_ERROR="build selection record names no absolute Runtime Pack carrier: $file"
    return 2
  fi
  if [[ ! -f "$pack" ]]; then
    RUNTIME_PACK_SELECTION_ERROR="the recorded Runtime Pack is gone: $pack"
    return 2
  fi
  if [[ "${pack##*/}" != "$RUNTIME_PACK_SELECTION_REC_carrier_basename" ]]; then
    RUNTIME_PACK_SELECTION_ERROR="build selection record disagrees with itself about the carrier name: $file"
    return 2
  fi

  # The digest is what makes the record name BYTES rather than a path. A pack
  # swapped underneath a valid-looking record is refused here, before the
  # installer's own trust chain would even be handed a candidate.
  if [[ ! "$RUNTIME_PACK_SELECTION_REC_sha256" =~ ^[0-9a-f]{64}$ ]]; then
    RUNTIME_PACK_SELECTION_ERROR="build selection record has no usable digest: $file"
    return 2
  fi
  actual_digest="$(runtime_pack_selection_sha256 "$pack")" || {
    RUNTIME_PACK_SELECTION_ERROR="cannot digest the recorded Runtime Pack: $pack"
    return 2
  }
  if [[ "$actual_digest" != "$RUNTIME_PACK_SELECTION_REC_sha256" ]]; then
    RUNTIME_PACK_SELECTION_ERROR="the recorded Runtime Pack no longer matches its recorded digest: $pack"
    return 2
  fi
  if [[ ! "$RUNTIME_PACK_SELECTION_REC_size" =~ ^[0-9]+$ ]]; then
    RUNTIME_PACK_SELECTION_ERROR="build selection record has no usable size: $file"
    return 2
  fi
  actual_size="$(runtime_pack_selection_size "$pack")" || {
    RUNTIME_PACK_SELECTION_ERROR="cannot size the recorded Runtime Pack: $pack"
    return 2
  }
  if [[ "$RUNTIME_PACK_SELECTION_REC_size" != "$actual_size" ]]; then
    RUNTIME_PACK_SELECTION_ERROR="the recorded Runtime Pack no longer matches its recorded size: $pack"
    return 2
  fi

  if [[ -n "$expected_platform" \
        && "$RUNTIME_PACK_SELECTION_REC_platform" != "$expected_platform" ]]; then
    RUNTIME_PACK_SELECTION_ERROR="recorded Runtime Pack targets $RUNTIME_PACK_SELECTION_REC_platform, not $expected_platform"
    return 2
  fi
  if [[ -n "$expected_architecture" \
        && "$RUNTIME_PACK_SELECTION_REC_architecture" != "$expected_architecture" ]]; then
    RUNTIME_PACK_SELECTION_ERROR="recorded Runtime Pack targets $RUNTIME_PACK_SELECTION_REC_architecture, not $expected_architecture"
    return 2
  fi

  # Source and both donors are full SHAs or the record does not describe a
  # generation the installer can hold the payload to.
  if [[ ! "$RUNTIME_PACK_SELECTION_REC_source_revision" =~ ^[0-9a-f]{40}$ ]]; then
    RUNTIME_PACK_SELECTION_ERROR="build selection record has no full source revision: $file"
    return 2
  fi
  if [[ ! "$RUNTIME_PACK_SELECTION_REC_terminal_revision" =~ ^[0-9a-f]{40}$ \
     || ! "$RUNTIME_PACK_SELECTION_REC_frame_revision" =~ ^[0-9a-f]{40}$ ]]; then
    RUNTIME_PACK_SELECTION_ERROR="build selection record has no full donor revisions: $file"
    return 2
  fi

  RUNTIME_PACK_SELECTION_PACK="$pack"
  RUNTIME_PACK_SELECTION_SHA256="$RUNTIME_PACK_SELECTION_REC_sha256"
  RUNTIME_PACK_SELECTION_VERSION="$RUNTIME_PACK_SELECTION_REC_version"
  RUNTIME_PACK_SELECTION_PLATFORM="$RUNTIME_PACK_SELECTION_REC_platform"
  RUNTIME_PACK_SELECTION_ARCHITECTURE="$RUNTIME_PACK_SELECTION_REC_architecture"
  RUNTIME_PACK_SELECTION_SOURCE_REVISION="$RUNTIME_PACK_SELECTION_REC_source_revision"
  RUNTIME_PACK_SELECTION_TERMINAL_REVISION="$RUNTIME_PACK_SELECTION_REC_terminal_revision"
  RUNTIME_PACK_SELECTION_FRAME_REVISION="$RUNTIME_PACK_SELECTION_REC_frame_revision"
  return 0
}
