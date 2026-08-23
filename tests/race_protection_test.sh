#!/usr/bin/env bash
# race_protection_test.sh — Plan 07 verification.
#
# Exercises scripts/lib/living-tree-commit.sh against two synthetic
# scenarios in a throwaway repo:
#
#   - Positive: clean working tree, no concurrent activity, the helper
#     commits and exits 0.
#   - Negative (synthetic race): between the helper's preflight stage and
#     its commit, another agent's commit lands on the same branch. The
#     helper must exit nonzero and emit a clear diagnostic.
#
# The race is injected via GIT_PRE_COMMIT_PROBE — a one-shot pre-commit
# hook installed only for the negative case that runs another `git commit`
# inside the same repo immediately before the helper's own `git commit`
# call returns. That faithfully reproduces the "another agent slipped in
# between stage-tree and commit" interleaving from the incident.

set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/.." && pwd)
HELPER="$REPO_ROOT/scripts/lib/living-tree-commit.sh"

if [[ ! -x "$HELPER" ]]; then
    echo "race_protection_test: helper not executable: $HELPER" >&2
    exit 2
fi

WORKDIR=$(mktemp -d -t living-tree-race.XXXXXX)
trap 'rm -rf "$WORKDIR"' EXIT

# ----- helpers ----------------------------------------------------------------

setup_repo() {
    local dir=$1
    rm -rf "$dir"
    mkdir -p "$dir"
    (
        cd "$dir"
        git init --quiet --initial-branch=main
        git config user.name "race-test"
        git config user.email "race-test@example.invalid"
        git config commit.gpgsign false
        echo "seed" >seed.txt
        git add seed.txt
        git commit --quiet -m "seed"
    )
}

PASS=0
FAIL=0

check() {
    local label=$1
    local cond=$2
    if [[ $cond -eq 0 ]]; then
        echo "  ok   $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL $label"
        FAIL=$((FAIL + 1))
    fi
}

# ----- positive case ----------------------------------------------------------

echo "[positive] clean commit on a quiet repo"
POS_REPO="$WORKDIR/positive"
setup_repo "$POS_REPO"
(
    cd "$POS_REPO"
    echo "alpha" >alpha.txt
    echo "beta"  >beta.txt
    if "$HELPER" "plan-07 positive case" -- alpha.txt beta.txt >"$WORKDIR/pos.stdout" 2>"$WORKDIR/pos.stderr"; then
        echo "POS_EXIT=0" >"$WORKDIR/pos.exit"
    else
        echo "POS_EXIT=$?" >"$WORKDIR/pos.exit"
    fi
)

# shellcheck disable=SC1091
source "$WORKDIR/pos.exit"
check "positive exit code is 0"           "$(( POS_EXIT == 0 ? 0 : 1 ))"
check "positive stdout reports clean commit" \
    "$(grep -q 'clean commit' "$WORKDIR/pos.stdout" && echo 0 || echo 1)"

POS_HEAD_PARENT_SUBJECT=$(cd "$POS_REPO" && git log -1 --format=%s HEAD~1)
POS_HEAD_SUBJECT=$(cd "$POS_REPO" && git log -1 --format=%s HEAD)
check "positive commit subject is helper message" \
    "$([[ "$POS_HEAD_SUBJECT" == "plan-07 positive case" ]] && echo 0 || echo 1)"
check "positive parent subject is seed" \
    "$([[ "$POS_HEAD_PARENT_SUBJECT" == "seed" ]] && echo 0 || echo 1)"

POS_DIFF_FILES=$(cd "$POS_REPO" && git diff-tree --no-commit-id --name-only -r HEAD | LC_ALL=C sort -u | tr '\n' ' ')
check "positive commit contains exactly alpha.txt + beta.txt" \
    "$([[ "$POS_DIFF_FILES" == "alpha.txt beta.txt " ]] && echo 0 || echo 1)"

# ----- negative case A: ref-lock race (concurrent commit lands first) ---------
#
# Simulates: another agent's commit lands on HEAD between our preflight
# stage and our commit ref-update. Git's own ref-lock catches this and our
# `git commit` errors out. The helper must propagate the failure as a
# nonzero exit and leave a diagnostic so the operator knows the helper
# blocked an unsafe commit.

echo "[negative-A] foreign commit lands first via ref update"
NEG_REPO="$WORKDIR/negative_a"
setup_repo "$NEG_REPO"

HOOK_FILE="$NEG_REPO/.git/hooks/pre-commit"
cat >"$HOOK_FILE" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
flag="$(git rev-parse --git-dir)/race-probe-fired"
if [[ -f "$flag" ]]; then
    exit 0
fi
touch "$flag"

# Stage and commit a foreign file using a fresh index so we do not touch
# the parent's staged contents.
foreign_index="$(git rev-parse --git-dir)/race-probe-index"
rm -f "$foreign_index"
echo "intruder" >intruder.txt
GIT_INDEX_FILE="$foreign_index" git read-tree HEAD
GIT_INDEX_FILE="$foreign_index" git update-index --add intruder.txt
foreign_tree=$(GIT_INDEX_FILE="$foreign_index" git write-tree)
foreign_commit=$(git commit-tree "$foreign_tree" -p HEAD -m "intruder agent commit")
git update-ref HEAD "$foreign_commit"
exit 0
HOOK
chmod +x "$HOOK_FILE"

(
    cd "$NEG_REPO"
    echo "gamma" >gamma.txt
    if "$HELPER" "plan-07 racing message A" -- gamma.txt >"$WORKDIR/neg_a.stdout" 2>"$WORKDIR/neg_a.stderr"; then
        echo "NEG_A_EXIT=0" >"$WORKDIR/neg_a.exit"
    else
        echo "NEG_A_EXIT=$?" >"$WORKDIR/neg_a.exit"
    fi
)

# shellcheck disable=SC1091
source "$WORKDIR/neg_a.exit"
check "negative-A exit code is nonzero" \
    "$(( NEG_A_EXIT != 0 ? 0 : 1 ))"
check "negative-A blocked the unsafe commit (git or helper diagnostic)" \
    "$(grep -Eq 'cannot lock ref|RACE DETECTED|git commit failed' "$WORKDIR/neg_a.stderr" && echo 0 || echo 1)"
NEG_A_HEAD_MSG=$(cd "$NEG_REPO" && git log -1 --format=%s)
check "negative-A HEAD subject is NOT helper message" \
    "$([[ "$NEG_A_HEAD_MSG" != "plan-07 racing message A" ]] && echo 0 || echo 1)"

# ----- negative case B: foreign-file injection (post-commit detection) --------
#
# Simulates: a concurrent agent mutates the index between our stage and
# git's commit-tree call so an extra file rides into the commit. The
# helper's foreign-file detection must trip and exit nonzero.

echo "[negative-B] foreign file injected into the index mid-commit"
NEG_REPO_B="$WORKDIR/negative_b"
setup_repo "$NEG_REPO_B"

HOOK_FILE_B="$NEG_REPO_B/.git/hooks/pre-commit"
cat >"$HOOK_FILE_B" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
flag="$(git rev-parse --git-dir)/race-probe-fired"
if [[ -f "$flag" ]]; then
    exit 0
fi
touch "$flag"

# Inject an extra file into the index that the helper did not stage.
# `git commit --only` uses a temporary index for the commit tree, but
# pre-commit hooks can still call `git add` against it via the standard
# GIT_INDEX_FILE pointer.
echo "stowaway" >stowaway.txt
git add stowaway.txt
exit 0
HOOK
chmod +x "$HOOK_FILE_B"

(
    cd "$NEG_REPO_B"
    echo "delta" >delta.txt
    if "$HELPER" "plan-07 racing message B" -- delta.txt >"$WORKDIR/neg_b.stdout" 2>"$WORKDIR/neg_b.stderr"; then
        echo "NEG_B_EXIT=0" >"$WORKDIR/neg_b.exit"
    else
        echo "NEG_B_EXIT=$?" >"$WORKDIR/neg_b.exit"
    fi
)

# shellcheck disable=SC1091
source "$WORKDIR/neg_b.exit"
check "negative-B exit code is nonzero" \
    "$(( NEG_B_EXIT != 0 ? 0 : 1 ))"
# This case may either trip helper race detection OR be blocked by git's
# own pre-commit-staged-changes guard. Either is an acceptable defence;
# what matters is the unsafe commit does not silently succeed.
check "negative-B blocked the unsafe commit (helper or git diagnostic)" \
    "$(grep -Eq 'RACE DETECTED|cannot lock ref|git commit failed|tree-hash mismatch|foreign files' "$WORKDIR/neg_b.stderr" && echo 0 || echo 1)"

# ----- positive case C: prettier-style hook modifies staged file content -----
#
# Plan 07-b closure of Limitation #1 (3 confirmations: Plans 04, 03, 06).
# The repo's pre-commit hook runs `prettier --write` then `git add` on
# .md/.yaml files AFTER the helper's `git write-tree` snapshot. Pre-Plan-07-b
# this tripped the tree-hash detector as a false race. With the relaxed
# detector, helper must exit 0 (clean) and emit an informational
# "hook-modified content" notice.

echo "[positive-C] pre-commit hook rewrites staged file content (Plan 07-b)"
POS_C_REPO="$WORKDIR/positive_c"
setup_repo "$POS_C_REPO"

HOOK_FILE_C="$POS_C_REPO/.git/hooks/pre-commit"
cat >"$HOOK_FILE_C" <<'HOOK'
#!/usr/bin/env bash
# Simulate prettier --write on .md files: rewrite content and re-add.
set -euo pipefail
flag="$(git rev-parse --git-dir)/hook-fired"
if [[ -f "$flag" ]]; then
    exit 0
fi
touch "$flag"

# Iterate over staged .md files, normalize trailing whitespace + add a
# trailing newline, then re-stage. This mirrors prettier's behaviour
# without requiring node/npx in the test environment.
mapfile -t md_files < <(git diff --cached --name-only --diff-filter=ACM | grep '\.md$' || true)
for f in "${md_files[@]}"; do
    if [[ -f "$f" ]]; then
        # Append a cosmetic line so the tree hash MUST change.
        printf "\n<!-- prettier touched this -->\n" >>"$f"
        git add -- "$f"
    fi
done
exit 0
HOOK
chmod +x "$HOOK_FILE_C"

(
    cd "$POS_C_REPO"
    echo "# epsilon"  >epsilon.md
    if "$HELPER" "plan-07b prettier-modify case" -- epsilon.md >"$WORKDIR/pos_c.stdout" 2>"$WORKDIR/pos_c.stderr"; then
        echo "POS_C_EXIT=0" >"$WORKDIR/pos_c.exit"
    else
        echo "POS_C_EXIT=$?" >"$WORKDIR/pos_c.exit"
    fi
)

# shellcheck disable=SC1091
source "$WORKDIR/pos_c.exit"
check "positive-C exit code is 0 (no false race on prettier-modify)" \
    "$(( POS_C_EXIT == 0 ? 0 : 1 ))"
check "positive-C stdout reports clean commit" \
    "$(grep -q 'clean commit' "$WORKDIR/pos_c.stdout" && echo 0 || echo 1)"
check "positive-C emits hook-modified content notice" \
    "$(grep -q 'hook-modified\|pre-commit hooks rewrote' "$WORKDIR/pos_c.stdout" && echo 0 || echo 1)"

POS_C_HEAD_SUBJECT=$(cd "$POS_C_REPO" && git log -1 --format=%s)
check "positive-C commit subject is helper message" \
    "$([[ "$POS_C_HEAD_SUBJECT" == "plan-07b prettier-modify case" ]] && echo 0 || echo 1)"

POS_C_DIFF_FILES=$(cd "$POS_C_REPO" && git diff-tree --no-commit-id --name-only -r HEAD | LC_ALL=C sort -u | tr '\n' ' ')
check "positive-C commit contains exactly epsilon.md" \
    "$([[ "$POS_C_DIFF_FILES" == "epsilon.md " ]] && echo 0 || echo 1)"

POS_C_CONTENT=$(cd "$POS_C_REPO" && git show HEAD:epsilon.md)
check "positive-C committed content includes hook modification" \
    "$(echo "$POS_C_CONTENT" | grep -q 'prettier touched this' && echo 0 || echo 1)"

# ----- positive case D: multi-line commit message via --message-file ---------
#
# Plan 07-b closure of Limitation #2 (1 confirmation in Plan 06).
# `make commit-safe MSG="..."` failed on multi-line message bodies due to
# Makefile $$ escaping. Plan 07-b adds `--message-file <path>` support to
# the helper as an alternative invocation path. Exercises a body with
# embedded newlines, double quotes, dollar signs, and single quotes — the
# combination that historically broke shell expansion.

echo "[positive-D] multi-line commit message via --message-file (Plan 07-b)"
POS_D_REPO="$WORKDIR/positive_d"
setup_repo "$POS_D_REPO"

MSG_FILE="$WORKDIR/multiline_msg.txt"
cat >"$MSG_FILE" <<'MSGEOF'
plan-07b multi-line subject line

Body paragraph one with "double quotes" and 'single quotes'.

Body paragraph two with $shell $vars and $(would-be-subshell).

- bullet one
- bullet two with embedded `backticks`
MSGEOF

(
    cd "$POS_D_REPO"
    echo "zeta-content" >zeta.txt
    if "$HELPER" --message-file "$MSG_FILE" -- zeta.txt >"$WORKDIR/pos_d.stdout" 2>"$WORKDIR/pos_d.stderr"; then
        echo "POS_D_EXIT=0" >"$WORKDIR/pos_d.exit"
    else
        echo "POS_D_EXIT=$?" >"$WORKDIR/pos_d.exit"
    fi
)

# shellcheck disable=SC1091
source "$WORKDIR/pos_d.exit"
check "positive-D exit code is 0" \
    "$(( POS_D_EXIT == 0 ? 0 : 1 ))"
check "positive-D stdout reports clean commit" \
    "$(grep -q 'clean commit' "$WORKDIR/pos_d.stdout" && echo 0 || echo 1)"

POS_D_SUBJECT=$(cd "$POS_D_REPO" && git log -1 --format=%s)
check "positive-D commit subject matches first line of message file" \
    "$([[ "$POS_D_SUBJECT" == "plan-07b multi-line subject line" ]] && echo 0 || echo 1)"

POS_D_BODY=$(cd "$POS_D_REPO" && git log -1 --format=%B)
# Literal fragments expected verbatim in the committed body. Held in vars
# so shellcheck doesn't see literal $ / backtick inside single quotes (SC2016).
expect_dq='"double quotes"'
expect_dollar=$'shell \x24vars'
expect_subshell=$'\x24(would-be-subshell)'
expect_backtick=$'embedded \x60backticks\x60'
check "positive-D commit body preserves double quotes verbatim" \
    "$(printf '%s' "$POS_D_BODY" | grep -qF -- "$expect_dq" && echo 0 || echo 1)"
check "positive-D commit body preserves dollar-shell-vars verbatim" \
    "$(printf '%s' "$POS_D_BODY" | grep -qF -- "$expect_dollar" && echo 0 || echo 1)"
check "positive-D commit body preserves would-be-subshell literal" \
    "$(printf '%s' "$POS_D_BODY" | grep -qF -- "$expect_subshell" && echo 0 || echo 1)"
check "positive-D commit body preserves backticks literal" \
    "$(printf '%s' "$POS_D_BODY" | grep -qF -- "$expect_backtick" && echo 0 || echo 1)"

# ----- summary ----------------------------------------------------------------

echo
echo "race_protection_test: $PASS pass, $FAIL fail"
if [[ $FAIL -gt 0 ]]; then
    for slot in pos neg_a neg_b pos_c pos_d; do
        for stream in stdout stderr; do
            f="$WORKDIR/$slot.$stream"
            if [[ -s "$f" ]]; then
                echo "---- $slot $stream ----"
                sed 's/^/  /' "$f" || true
            fi
        done
    done
    exit 1
fi
