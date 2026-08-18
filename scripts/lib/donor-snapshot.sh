#!/usr/bin/env bash
# ============================================================================
# donor-snapshot.sh — build a release from a detached snapshot of a donor repo
# that is allowed to stay dirty
# ============================================================================
# BORN FROM (2026-08-11): `build-vibecrafted-release.sh` refuses a dirty donor
# with "FATAL: <donor> is dirty; release receipts refuse moving source". That
# refusal is correct — a receipt that binds a SHA must not have been built from
# a tree that moved underneath it. But on the Living Tree a dirty donor is the
# NORMAL state, so the operator hand-rolled the way around it:
#
#   git -C ../vc-frame worktree add --detach "$TMPDIR/.tmpQUEtCY/snapshot" HEAD
#
# and then the temp dir vanished before the worktree was removed. What was left
# behind was a ghost registration in the donor's `.git/worktrees/snapshot2`
# pointing at a path that no longer exists — `git worktree list` lied for a
# week, and nothing in either repo's history ever ran `worktree add` from a
# script, so there was no reaper to blame.
#
# This file makes that move a feature with a reaper attached:
#
# - the snapshot is a DETACHED worktree at the donor's HEAD, so the donor's
#   dirty files, index and stashes are never read, moved or touched;
# - the snapshot is clean by construction, so the existing dirty-donor gate
#   passes honestly instead of being bypassed;
# - `git_sha` on the snapshot IS the donor's HEAD, so the receipt keeps binding
#   the exact source revision it claims;
# - every created snapshot is recorded, and the reaper removes the worktree
#   THROUGH GIT (`worktree remove --force`) and then prunes, so no ghost
#   registration can survive — on success and on failure alike, because the
#   caller runs the reaper from a trap armed for EXIT INT TERM HUP.
#
# The reaper is idempotent: double delivery of a signal is harmless, and it
# never runs anything that could mutate the donor's index or stash list.

# Records of live snapshots, one "<donor>\t<path>" per entry.
DONOR_SNAPSHOTS=()

# _donor_snapshot_force_remove <donor> <path>
#
# The one place in this file allowed to run `rm -rf`. It existed only in the
# reaper, carefully guarded; `donor_snapshot_create` had a second, unguarded
# copy on its cleanup path. Both live call sites pass a hardcoded
# "$REPO_ROOT/build/..." so neither was reachable — but two spellings of the
# same dangerous operation, one of them guarded, is how the guard gets lost.
_donor_snapshot_force_remove() {
  local donor="$1" path="$2"
  # Never the donor repository itself, never a root, never an empty word — the
  # last of which is what an unquoted "${DONOR_SNAPSHOTS[@]}" expansion would
  # have produced by word-splitting a record on its tab.
  [[ -n "$path" && "$path" != "$donor" && "$path" != "/" ]] || return 0
  rm -rf "$path"
}
# The HEAD the most recent snapshot was taken at. Exported because its only
# reader is the script that sources this file, not this file itself.
export DONOR_SNAPSHOT_HEAD=""

# donor_snapshot_create <donor-repo> <snapshot-path>
# Sets DONOR_SNAPSHOT_HEAD to the donor HEAD the snapshot was taken at.
#
# It deliberately does NOT print the SHA for `head="$(donor_snapshot_create ...)"`
# to capture. MEASURED 2026-08-18 during this cut's own walk-around: with the
# printing shape, the release created both worktrees and then reaped NOTHING,
# because command substitution runs the function in a SUBSHELL — the
# `DONOR_SNAPSHOTS+=(...)` below mutated a copy that died with the subshell, and
# the parent entered its trap with an empty record list. Seven green unit tests
# had missed it because they called the function directly. A reaper that cannot
# see what it must reap is exactly how the 2026-08-11 ghost was born, so the
# recording side effect must happen in the caller's own shell. Read the result
# out of DONOR_SNAPSHOT_HEAD.
donor_snapshot_create() {
  local donor="$1" path="$2" head

  git -C "$donor" rev-parse --git-dir >/dev/null 2>&1 \
    || { printf 'FATAL: %s is not a git repository\n' "$donor" >&2; return 1; }
  head="$(git -C "$donor" rev-parse HEAD)"

  # Clear any residue from an earlier interrupted run before adding: a stale
  # registration for this exact path would make `worktree add` refuse.
  git -C "$donor" worktree prune >/dev/null 2>&1 || true
  if [[ -e "$path" ]]; then
    git -C "$donor" worktree remove --force "$path" >/dev/null 2>&1 \
      || _donor_snapshot_force_remove "$donor" "$path"
    git -C "$donor" worktree prune >/dev/null 2>&1 || true
  fi

  mkdir -p "$(dirname "$path")"
  git -C "$donor" worktree add --detach --quiet "$path" "$head" >/dev/null

  DONOR_SNAPSHOTS+=("$donor"$'\t'"$path")
  DONOR_SNAPSHOT_HEAD="$head"
}

# donor_snapshot_reap — remove every snapshot this process created.
# Safe to call more than once and safe to call when nothing was created.
donor_snapshot_reap() {
  local record donor path
  (( ${#DONOR_SNAPSHOTS[@]} == 0 )) && return 0
  # The expansion MUST stay quoted. Each record holds "<donor>\t<path>", and an
  # unquoted "${DONOR_SNAPSHOTS[@]}" word-splits on the tab in IFS: `record`
  # would then be the donor alone, `path` would resolve to the donor too, and
  # the fallback below would have been pointed at the donor repository itself.
  for record in "${DONOR_SNAPSHOTS[@]}"; do
    [[ -n "$record" ]] || continue
    donor="${record%%$'\t'*}"
    path="${record#*$'\t'}"
    if ! git -C "$donor" worktree remove --force "$path" >/dev/null 2>&1; then
      _donor_snapshot_force_remove "$donor" "$path"
    fi
    git -C "$donor" worktree prune >/dev/null 2>&1 || true
  done
  DONOR_SNAPSHOTS=()
}
