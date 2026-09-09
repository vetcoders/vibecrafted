# shellcheck shell=bash
#
# Exclusive ownership of one checkout's shared release mutation.
#
# Two `build-vibecrafted-release.sh` processes from the same root share
# `build/runtime-pack-selection.json`, `build/unified-release/donor-snapshots`
# and the App/DMG output. The selection library serialises only the short
# write of that record; it unlocks before cargo, snapshots or notarization.
# This lock is the long-lived one: taken before any of those mutations and
# held until after cleanup, including descendants that inherit the descriptor.
#
# When VIBECRAFTED_RELEASE_DIR resolves to a directory outside this checkout,
# a second kernel lock on that physical path stops two checkouts from quietly
# sharing one output tree. A dist that stays under the checkout is already
# covered by build/release.lock.
#
# The exclusion belongs to the KERNEL, not to a pid file. The flock primitive
# is the one in runtime-pack-selection.sh: never unlink the lock inode, never
# reclaim by inspecting a dead pid, never close anyone else's descriptor.
# A duplicate must refuse promptly (timeout 0) and leave the first run's bytes
# untouched.
#
# Requires runtime_pack_selection_flock (source runtime-pack-selection.sh first).
#
# Open helpers MUST run in the caller's shell. Command substitution would
# exec the descriptor in a subshell that dies on return — the same class of
# bug donor_snapshot_create documents for DONOR_SNAPSHOTS.

RELEASE_SINGLE_FLIGHT_LOCK_FD=""
RELEASE_SINGLE_FLIGHT_OUTPUT_LOCK_FD=""
# A second builder is another live release, not a short writer. Do not wait.
RELEASE_SINGLE_FLIGHT_LOCK_TIMEOUT="${RELEASE_SINGLE_FLIGHT_LOCK_TIMEOUT:-0}"

release_single_flight_lock_file() {
  printf '%s\n' "$1/build/release.lock"
}

# Sibling of the canonical output directory, never a file inside it.
release_single_flight_output_lock_file() {
  printf '%s.release.lock\n' "$1"
}

# Same open as the selection lock: `>>` creates once and never truncates.
# A directory here is an older mkdir lock; removing it is the race this
# pattern exists to make unrepresentable.
release_single_flight_open_lock_fd() {
  local lock="$1"
  RELEASE_SINGLE_FLIGHT_LOCK_FD=""
  if [[ -d "$lock" ]]; then
    printf 'release: %s is a directory left by an older lock; remove it\n' \
      "$lock" >&2
    return 1
  fi
  if ((BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 1))); then
    exec {RELEASE_SINGLE_FLIGHT_LOCK_FD}>>"$lock" || return 1
  else
    RELEASE_SINGLE_FLIGHT_LOCK_FD=201
    eval "exec ${RELEASE_SINGLE_FLIGHT_LOCK_FD}>>\"\$lock\"" || return 1
  fi
  [[ -n "$RELEASE_SINGLE_FLIGHT_LOCK_FD" ]] || return 1
}

release_single_flight_close_lock_fd() {
  [[ -n "$RELEASE_SINGLE_FLIGHT_LOCK_FD" ]] || return 0
  eval "exec ${RELEASE_SINGLE_FLIGHT_LOCK_FD}>&-" 2>/dev/null || true
  RELEASE_SINGLE_FLIGHT_LOCK_FD=""
}

release_single_flight_open_output_lock_fd() {
  local lock="$1"
  RELEASE_SINGLE_FLIGHT_OUTPUT_LOCK_FD=""
  if [[ -d "$lock" ]]; then
    printf 'release: %s is a directory left by an older lock; remove it\n' \
      "$lock" >&2
    return 1
  fi
  if ((BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 1))); then
    exec {RELEASE_SINGLE_FLIGHT_OUTPUT_LOCK_FD}>>"$lock" || return 1
  else
    RELEASE_SINGLE_FLIGHT_OUTPUT_LOCK_FD=202
    eval "exec ${RELEASE_SINGLE_FLIGHT_OUTPUT_LOCK_FD}>>\"\$lock\"" || return 1
  fi
  [[ -n "$RELEASE_SINGLE_FLIGHT_OUTPUT_LOCK_FD" ]] || return 1
}

release_single_flight_close_output_lock_fd() {
  [[ -n "$RELEASE_SINGLE_FLIGHT_OUTPUT_LOCK_FD" ]] || return 0
  eval "exec ${RELEASE_SINGLE_FLIGHT_OUTPUT_LOCK_FD}>&-" 2>/dev/null || true
  RELEASE_SINGLE_FLIGHT_OUTPUT_LOCK_FD=""
}

# Acquire exclusive ownership of this checkout's shared release state.
# Returns 0 holding the descriptor; 1 after printing why and leaving bytes
# unchanged. Closing this process's descriptor is the only release.
release_single_flight_acquire() {
  local repo_root="$1" lock status=0
  lock="$(release_single_flight_lock_file "$repo_root")"
  if [[ -n "$RELEASE_SINGLE_FLIGHT_LOCK_FD" ]]; then
    printf 'release: this process already holds %s\n' "$lock" >&2
    return 1
  fi
  mkdir -p "${lock%/*}" || return 1
  release_single_flight_open_lock_fd "$lock" || return 1
  runtime_pack_selection_flock \
    "$RELEASE_SINGLE_FLIGHT_LOCK_FD" "$RELEASE_SINGLE_FLIGHT_LOCK_TIMEOUT" \
    || status=$?
  if ((status != 0)); then
    release_single_flight_close_lock_fd
    case "$status" in
      1)
        printf 'release: another release is already running from this checkout\n' >&2
        printf '         lock: %s\n' "$lock" >&2
        printf '         shared: %s/build/runtime-pack-selection.json\n' "$repo_root" >&2
        printf '         shared: %s/build/unified-release/donor-snapshots\n' "$repo_root" >&2
        printf '         this attempt left those bytes unchanged; wait for the owner or stop it\n' >&2
        ;;
      2)
        printf 'release: this host offers no file lock (flock, perl or python3); refusing to mutate %s unserialised\n' \
          "$repo_root" >&2
        ;;
      *)
        printf 'release: could not lock %s\n' "$lock" >&2
        ;;
    esac
    return 1
  fi
  return 0
}

# Second lock when the resolved output directory is not inside this checkout.
# Physical identity (pwd -P) so two worktrees cannot share one dist via a
# symlink and miss each other. Dist under the checkout is already covered.
release_single_flight_acquire_output() {
  local repo_root="$1" dist_dir="$2" repo_abs dist_abs repo_phys dist_phys lock status=0
  [[ -n "$dist_dir" ]] || return 1
  repo_abs="$(cd "$repo_root" && pwd)" || return 1
  case "$dist_dir" in
    /*) dist_abs="$dist_dir" ;;
    *) dist_abs="$PWD/$dist_dir" ;;
  esac
  # Do not mkdir an in-checkout dist here: that would dirty SOURCE/REPO
  # before require_clean_repo. /dist is gitignored on the living tree, but
  # a custom in-repo VIBECRAFTED_RELEASE_DIR is not this lock's job.
  case "$dist_abs" in
    "$repo_abs"|"$repo_abs"/*)
      return 0
      ;;
  esac
  mkdir -p "$dist_abs" || return 1
  repo_phys="$(cd "$repo_abs" && pwd -P)" || return 1
  dist_phys="$(cd "$dist_abs" && pwd -P)" || return 1
  case "$dist_phys" in
    "$repo_phys"|"$repo_phys"/*)
      return 0
      ;;
  esac
  if [[ -n "$RELEASE_SINGLE_FLIGHT_OUTPUT_LOCK_FD" ]]; then
    printf 'release: this process already holds the output lock\n' >&2
    return 1
  fi
  lock="$(release_single_flight_output_lock_file "$dist_phys")"
  release_single_flight_open_output_lock_fd "$lock" || return 1
  runtime_pack_selection_flock \
    "$RELEASE_SINGLE_FLIGHT_OUTPUT_LOCK_FD" "$RELEASE_SINGLE_FLIGHT_LOCK_TIMEOUT" \
    || status=$?
  if ((status != 0)); then
    release_single_flight_close_output_lock_fd
    case "$status" in
      1)
        printf 'release: another release already owns this output directory\n' >&2
        printf '         lock: %s\n' "$lock" >&2
        printf '         this attempt left those bytes unchanged; wait for the owner or stop it\n' >&2
        ;;
      2)
        printf 'release: this host offers no file lock (flock, perl or python3); refusing to mutate %s unserialised\n' \
          "$dist_phys" >&2
        ;;
      *)
        printf 'release: could not lock %s\n' "$lock" >&2
        ;;
    esac
    return 1
  fi
  return 0
}

# Release exactly this process's own descriptors. Never unlink.
# Output closes first so a late checkout unlock cannot be mistaken for
# permission to keep writing a shared dist. The checkout descriptor stays
# open through that close.
release_single_flight_release() {
  release_single_flight_close_output_lock_fd
  release_single_flight_close_lock_fd
}
