#!/usr/bin/env bash
# living-tree-commit.sh — Plan 07 race-protected commit helper.
#
# Captures the doctrine 2026-04-16/17 incident learning: under parallel
# agent activity in a Living Tree checkout, `git commit --only path...`
# is NOT atomic versus another agent's commit running in the same instant
# — message of one commit can end up under the envelope of another's tree.
#
# This helper wraps the standard "stage these files + commit with this
# message" pattern with three race-detection primitives:
#
#   1. HEAD shift detection — capture HEAD before staging, compare against
#      the parent of the new commit afterwards.
#   2. Staged-tree fingerprinting — `git write-tree` before committing,
#      compare against the new commit's tree.
#   3. Foreign-file detection — list files in the new commit's diff vs
#      its parent and assert the set is exactly the set we staged.
#
# On race: print diagnostic with both SHAs + foreign files, leave the
# commit in place (operator decides), exit nonzero.
#
# Usage:
#   scripts/lib/living-tree-commit.sh "commit message" -- path1 path2 ...
#   scripts/lib/living-tree-commit.sh --message-file /tmp/msg.txt -- path1 ...
#
# This script is intentionally append-only: it never amends, never rebases,
# never force-pushes. Recovery on race is operator-driven.
#
# -----------------------------------------------------------------------------
# CHANGELOG
# -----------------------------------------------------------------------------
# v1.1 (Plan 07-b, 2026-05-12) — closes two limitations confirmed across
# Plans 04, 03, 06 marble rounds:
#
#   * Limitation #1 (prettier false-positive, 3 confirmations).
#     Repo's `scripts/hooks/pre-commit` runs `prettier --write` + `git add`
#     on .md/.yaml files AFTER the helper's `git write-tree` snapshot.
#     Tree-hash mismatch detector tripped because the staged index got
#     cosmetic auto-fixes between snapshot and commit. Parent SHA + foreign
#     files both stayed green — actual commit was correct, but helper
#     exited 3 (race) and confused operators.
#
#     CHOSEN FIX: relax the tree-hash detector. Mismatch alone is no longer
#     a race signal. It is only a race signal in combination with foreign
#     files OR HEAD shift. Tree mismatch with identical file set and
#     stable HEAD is classified as "hook-modified content" — informational
#     notice, exit 0. The foreign-file and HEAD-shift detectors remain
#     unchanged and continue to catch real concurrent-commit races.
#
#     Trade-off: a hypothetical race that mutates only the *content* of
#     staged files (without adding/removing files and without shifting
#     HEAD) would now slip through. We accept this trade because (a) no
#     such race has been observed in 4 plan rounds, (b) the false-positive
#     it eliminates was hit in 3 of those 4 rounds, (c) the foreign-file
#     detector still catches concurrent staging via pre-commit hooks (the
#     actual injection vector from doctrine 2026-04-16/17).
#
#   * Limitation #2 (multi-line MSG quoting, 1 confirmation in Plan 06).
#     `make commit-safe MSG="..."` failed on multi-line bodies due to
#     Makefile $$ escaping vs. shell expansion interaction. Plan 06
#     worked around by invoking the helper directly with a heredoc.
#
#     CHOSEN FIX: helper now accepts `--message-file <path>` as an
#     alternative to the positional message argument. Multi-line bodies
#     written to a file (e.g. `git commit -F` style) avoid quoting hell
#     entirely. Makefile `commit-safe` target adds MSG_FILE="..." support
#     alongside the existing MSG="..." single-line path; both work.

set -euo pipefail

# ----- argument parsing -------------------------------------------------------

usage() {
    cat >&2 <<'USAGE'
usage:
  living-tree-commit.sh "<commit message>" -- <file> [<file>...]
  living-tree-commit.sh --message-file <path> -- <file> [<file>...]

Race-protected commit helper for Vetcoders Living Tree workflow.

Captures pre-flight HEAD, stages only the named files, commits with the
given message, then verifies no concurrent agent commit interleaved between
stage and commit.

Multi-line commit bodies: use --message-file <path> to read the message
from a file (avoids shell-quoting hell on subject + body messages).

Exits nonzero on race; the racing commit is left in place for operator
review (no auto-rebase, no auto-reset).
USAGE
}

if [[ $# -lt 1 ]]; then
    usage
    exit 2
fi

MESSAGE=""
MESSAGE_FILE=""

case "$1" in
    --message-file)
        if [[ $# -lt 2 ]]; then
            echo "living-tree-commit: --message-file requires a path argument" >&2
            exit 2
        fi
        MESSAGE_FILE=$2
        shift 2
        if [[ ! -f "$MESSAGE_FILE" ]]; then
            echo "living-tree-commit: message file not found: $MESSAGE_FILE" >&2
            exit 2
        fi
        if [[ ! -s "$MESSAGE_FILE" ]]; then
            echo "living-tree-commit: message file is empty: $MESSAGE_FILE" >&2
            exit 2
        fi
        ;;
    -h|--help)
        usage
        exit 0
        ;;
    *)
        if [[ $# -lt 3 ]]; then
            usage
            exit 2
        fi
        MESSAGE=$1
        shift
        ;;
esac

if [[ $# -lt 1 || $1 != "--" ]]; then
    echo "living-tree-commit: expected '--' before file list" >&2
    usage
    exit 2
fi
shift

if [[ $# -lt 1 ]]; then
    echo "living-tree-commit: at least one file path required after '--'" >&2
    exit 2
fi

FILES=("$@")

# ----- preflight --------------------------------------------------------------

if ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "living-tree-commit: not inside a git working tree" >&2
    exit 2
fi

# Disallow path arguments that look like add-everything sugar, per doctrine
# safety doctrine (never `git add -A`, never `git add .`).
for f in "${FILES[@]}"; do
    case "$f" in
        "."|"-A"|"--all"|"-a")
            echo "living-tree-commit: refusing wildcard/all-files argument '$f' — name files explicitly" >&2
            exit 2
            ;;
    esac
done

# Pre-flight HEAD. Empty if the repo has no commits yet.
PRE_HEAD=$(git rev-parse --verify HEAD 2>/dev/null || echo "")

# ----- stage ------------------------------------------------------------------

git add -- "${FILES[@]}"

# Fingerprint the index we are about to commit. Even on an empty repo this
# returns a tree (the empty tree, possibly).
STAGED_TREE=$(git write-tree)

# Snapshot the set of paths that should appear in the resulting commit.
# Use --cached because we have just staged. Limit to our path args to
# tolerate other staged files left over from operator interaction (they
# would also become foreign-file evidence below).
mapfile -t STAGED_FILES < <(
    git diff --cached --name-only -- "${FILES[@]}" | LC_ALL=C sort -u
)

if [[ ${#STAGED_FILES[@]} -eq 0 ]]; then
    # Nothing actually changed in the named files. Nothing to commit. Treat
    # as success — running this twice in a row should not error noisily.
    echo "living-tree-commit: no staged changes for: ${FILES[*]} (nothing to commit)"
    exit 0
fi

# ----- commit -----------------------------------------------------------------

# Use `git commit --only` semantics by passing the paths. This commits ONLY
# the index entries for those paths, ignoring any unrelated staged work in
# the parent's index. That is exactly the safety we want under Living Tree.
COMMIT_STDERR=$(mktemp)
trap 'rm -f "$COMMIT_STDERR"' EXIT

if [[ -n "$MESSAGE_FILE" ]]; then
    if ! git commit -F "$MESSAGE_FILE" --only -- "${FILES[@]}" 2>"$COMMIT_STDERR"; then
        cat "$COMMIT_STDERR" >&2
        echo "living-tree-commit: git commit failed — leaving worktree as-is" >&2
        exit 1
    fi
else
    if ! git commit -m "$MESSAGE" --only -- "${FILES[@]}" 2>"$COMMIT_STDERR"; then
        cat "$COMMIT_STDERR" >&2
        echo "living-tree-commit: git commit failed — leaving worktree as-is" >&2
        exit 1
    fi
fi

NEW_HEAD=$(git rev-parse --verify HEAD)
NEW_TREE=$(git rev-parse --verify "${NEW_HEAD}^{tree}")

# Parent of the new commit. If the repo had no commits pre-flight, the new
# commit is a root commit and has no parent.
if ! NEW_PARENT=$(git rev-parse --verify "${NEW_HEAD}^" 2>/dev/null); then
    NEW_PARENT=""
fi

# Files actually present in the new commit. For non-root commits git
# diff-tree -r implicitly diffs against the parent; for root commits we
# fall back to listing the tree's full file set.
if [[ -n "$NEW_PARENT" ]]; then
    mapfile -t COMMIT_FILES < <(
        git diff-tree --no-commit-id --name-only -r "$NEW_HEAD" | LC_ALL=C sort -u
    )
else
    mapfile -t COMMIT_FILES < <(
        git ls-tree -r --name-only "$NEW_HEAD" | LC_ALL=C sort -u
    )
fi

# ----- race detection ---------------------------------------------------------
#
# Plan 07-b: tree-hash mismatch alone is NOT a race signal. Pre-commit
# hooks (prettier --write, ruff format, etc.) legitimately mutate the
# content of staged files between our snapshot and the final commit. The
# real race signals are:
#
#   (a) HEAD shift — another commit slipped in via ref update.
#   (c) Foreign files — files appeared in the commit that we did not stage.
#
# Tree-hash mismatch with stable HEAD and identical file set is classified
# as informational "hook-modified content" and does NOT trip a race.

race_reasons=()
hook_modified_notice=""

# (a) HEAD shift: pre-flight HEAD must equal the parent of the new commit
# (or both empty for the root-commit case).
if [[ "$PRE_HEAD" != "$NEW_PARENT" ]]; then
    race_reasons+=("HEAD moved during commit: pre=${PRE_HEAD:-<root>} parent-of-new=${NEW_PARENT:-<root>}")
fi

# (c) Foreign-file detection: every file in the commit's diff must be in
# our staged-files snapshot. Anything extra is foreign content riding on
# our commit message.
foreign_files=()
declare -A staged_lookup=()
for f in "${STAGED_FILES[@]}"; do
    staged_lookup["$f"]=1
done
for f in "${COMMIT_FILES[@]}"; do
    if [[ -z "${staged_lookup[$f]:-}" ]]; then
        foreign_files+=("$f")
    fi
done
if [[ ${#foreign_files[@]} -gt 0 ]]; then
    race_reasons+=("foreign files in commit: ${foreign_files[*]}")
fi

# (b) Staged-tree fingerprint: informational only as of Plan 07-b. Only
# adds context to a race already detected above, OR emits an informational
# "hook-modified content" notice when no race signal fired.
if [[ "$STAGED_TREE" != "$NEW_TREE" ]]; then
    if [[ ${#race_reasons[@]} -gt 0 ]]; then
        race_reasons+=("tree-hash mismatch: staged=$STAGED_TREE committed=$NEW_TREE")
    else
        hook_modified_notice="staged=$STAGED_TREE committed=$NEW_TREE (pre-commit hooks rewrote content; not a race)"
    fi
fi

if [[ ${#race_reasons[@]} -eq 0 ]]; then
    echo "living-tree-commit: clean commit $NEW_HEAD (${#STAGED_FILES[@]} file(s))"
    if [[ -n "$hook_modified_notice" ]]; then
        echo "living-tree-commit: notice — $hook_modified_notice"
    fi
    exit 0
fi

# ----- race diagnostic --------------------------------------------------------

{
    echo "living-tree-commit: RACE DETECTED on commit $NEW_HEAD"
    echo "  pre-flight HEAD : ${PRE_HEAD:-<root>}"
    echo "  new commit      : $NEW_HEAD"
    echo "  new parent      : ${NEW_PARENT:-<root>}"
    echo "  staged tree     : $STAGED_TREE"
    echo "  committed tree  : $NEW_TREE"
    for reason in "${race_reasons[@]}"; do
        echo "  - $reason"
    done
    echo
    echo "Recovery options (operator decides — no auto-rewrite):"
    echo "  A) git reset HEAD~1 && git stash --include-untracked \\"
    echo "       && git pull --rebase && git stash pop && rerun helper"
    echo "  B) leave the commit in place and document the race in the"
    echo "     marble report so the next agent can reason about lineage"
} >&2

exit 3
