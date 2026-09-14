#!/usr/bin/env bash
# vibecrafted-hooks-template :: install.sh
#
# Installs the polyglot git-hook template into the current repo. The same
# shell hooks + lib run under whichever activator the repo prefers:
#
#   lefthook    — Go binary, single install, parallel-capable (default).
#   husky       — npm-based, fits TS/JS repos that already have package.json.
#   pre-commit  — Python framework, fits Python repos (https://pre-commit.com).
#   manual      — bare `git config core.hooksPath .husky` for repos without
#                 a package manager.
#
# Idempotent — re-running refreshes lib/, scripts/, hook entries, and the
# activator config without touching .husky/config.env or .husky/local/.
#
# Usage:
#   bash /path/to/vibecrafted/templates/hooks/install.sh [--activator <kind>] [opts]
#
# Options:
#   --activator <lefthook|husky|pre-commit|manual>
#                   Pick the activator (default: lefthook)
#   --force         Overwrite .husky/config.env even if present
#   --no-gitignore  Skip .gitignore patching
#   --no-activate   Install template files but skip activator config write
#   --dry-run       Show what would happen without writing anything
#
# Vibecrafted with AI Agents by Vetcoders (c)2024-2026 LibraxisAI

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_LIB="$SCRIPT_DIR/lib"
SOURCE_HOOKS="$SCRIPT_DIR/hooks"
SOURCE_SCRIPTS="$SCRIPT_DIR/scripts"
SOURCE_CONFIG="$SCRIPT_DIR/config/template.husky.env"
SOURCE_ACTIVATORS="$SCRIPT_DIR/activators"

ACTIVATOR=""        # empty = auto-suggest based on detection
AUTO=1              # default: auto-detect repo profile + write config.env
FORCE=0
NO_GITIGNORE=0
NO_ACTIVATE=0
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --activator)        ACTIVATOR="${2:-}"; shift 2 ;;
    --activator=*)      ACTIVATOR="${1#--activator=}"; shift ;;
    --no-auto)          AUTO=0; shift ;;          # keep stock template defaults
    --force)            FORCE=1; shift ;;
    --no-gitignore)     NO_GITIGNORE=1; shift ;;
    --no-activate)      NO_ACTIVATE=1; shift ;;
    --dry-run)          DRY_RUN=1; shift ;;
    -h|--help)
      sed -n '1,/^# Vibecrafted/p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

# Source detection helpers so suggestion logic works before activator
# validation. detect.sh is sourced from the template location, not the
# target — we may not have installed lib/ yet.
. "$SOURCE_LIB/detect.sh"

# Auto-suggest activator from repo shape unless caller explicitly overrode.
if [ -z "$ACTIVATOR" ]; then
  ACTIVATOR="$(husky_suggest_activator "$REPO_ROOT")"
fi

case "$ACTIVATOR" in
  lefthook|husky|pre-commit|manual) ;;
  *)
    echo "Invalid --activator: $ACTIVATOR" >&2
    echo "Valid choices: lefthook | husky | pre-commit | manual" >&2
    exit 1
    ;;
esac

TARGET_HUSKY="$REPO_ROOT/.husky"
TARGET_LIB="$TARGET_HUSKY/lib"
TARGET_SCRIPTS="$TARGET_HUSKY/scripts"
TARGET_CONFIG="$TARGET_HUSKY/config.env"
TARGET_LOCAL_DIR="$TARGET_HUSKY/local"
TARGET_WARNS_DIR="$TARGET_HUSKY/warns"

say() { printf '[hooks-install] %s\n' "$*"; }
do_cp() {
  local src="$1"
  local dst="$2"
  if [ "$DRY_RUN" = "1" ]; then
    say "DRY: cp $src → $dst"
  else
    install -m "${3:-0644}" "$src" "$dst"
  fi
}
do_mkdir() {
  if [ "$DRY_RUN" = "1" ]; then
    say "DRY: mkdir -p $1"
  else
    mkdir -p "$1"
  fi
}

say "Installing vibecrafted-hooks-template into: $REPO_ROOT"
say "Activator: $ACTIVATOR"

# Detection summary — emit before any file changes so the operator can ^C
# if the profile looks wrong. Doesn't change behavior; just transparency.
if [ "$AUTO" = "1" ]; then
  husky_profile_summary "$REPO_ROOT" | sed 's/^/[hooks-install] /'
fi

# ---------------------------------------------------------------------------
# target dirs
# ---------------------------------------------------------------------------
do_mkdir "$TARGET_HUSKY"
do_mkdir "$TARGET_LIB"
do_mkdir "$TARGET_SCRIPTS"
do_mkdir "$TARGET_LOCAL_DIR"
do_mkdir "$TARGET_WARNS_DIR"

# ---------------------------------------------------------------------------
# refresh lib/ + scripts/ (always overwrite)
# ---------------------------------------------------------------------------
say "Refreshing lib/*"
for f in "$SOURCE_LIB"/*.sh; do
  do_cp "$f" "$TARGET_LIB/$(basename "$f")" 0644
done

say "Refreshing scripts/*"
for f in "$SOURCE_SCRIPTS"/*; do
  [ -f "$f" ] || continue
  do_cp "$f" "$TARGET_SCRIPTS/$(basename "$f")" 0755
done

# ---------------------------------------------------------------------------
# hook entry points
# ---------------------------------------------------------------------------
HOOKS=(pre-commit pre-push pre-merge-commit prepare-commit-msg post-commit commit-msg)
for hook in "${HOOKS[@]}"; do
  src="$SOURCE_HOOKS/$hook"
  [ -f "$src" ] || { say "Skipping missing $hook"; continue; }
  dst="$TARGET_HUSKY/$hook"
  say "Refreshing $hook"
  do_cp "$src" "$dst" 0755
done

# ---------------------------------------------------------------------------
# config.env (preserve unless --force; auto-detect unless --no-auto)
# ---------------------------------------------------------------------------
if [ -f "$TARGET_CONFIG" ] && [ "$FORCE" = "0" ]; then
  say "Keeping existing .husky/config.env (use --force to overwrite)"
elif [ "$AUTO" = "1" ]; then
  if [ "$DRY_RUN" = "1" ]; then
    say "DRY: would write .husky/config.env (auto-detected from repo profile)"
  else
    say "Writing .husky/config.env (auto-detected from repo profile)"
    husky_write_auto_config "$REPO_ROOT" "$TARGET_CONFIG"
  fi
else
  say "Writing default .husky/config.env (--no-auto)"
  do_cp "$SOURCE_CONFIG" "$TARGET_CONFIG" 0644
fi

# ---------------------------------------------------------------------------
# .gitignore (warns archive)
# ---------------------------------------------------------------------------
if [ "$NO_GITIGNORE" = "0" ]; then
  GITIGNORE="$REPO_ROOT/.gitignore"
  if [ ! -f "$GITIGNORE" ]; then
    if [ "$DRY_RUN" = "1" ]; then
      say "DRY: would create .gitignore with .husky/warns/"
    else
      printf '.husky/warns/\n' > "$GITIGNORE"
    fi
  else
    if ! grep -qx '.husky/warns/' "$GITIGNORE" 2>/dev/null; then
      if [ "$DRY_RUN" = "1" ]; then
        say "DRY: would append .husky/warns/ to .gitignore"
      else
        printf '\n# vibecrafted-hooks-template warns retention\n.husky/warns/\n' >> "$GITIGNORE"
      fi
    fi
  fi
fi

# ---------------------------------------------------------------------------
# activator wiring
# ---------------------------------------------------------------------------
activator_lefthook() {
  local target="$REPO_ROOT/lefthook.yml"
  if [ -f "$target" ] && [ "$FORCE" = "0" ]; then
    say "Keeping existing lefthook.yml (use --force to overwrite)"
  else
    say "Writing lefthook.yml"
    do_cp "$SOURCE_ACTIVATORS/lefthook.yml" "$target" 0644
  fi

  # Resolve husky residue: if the repo previously used husky, git config
  # still points core.hooksPath at .husky/_. Lefthook refuses to overwrite
  # that path without --force. We detect the collision and pass --force so
  # the installer succeeds without operator intervention.
  local existing_hookspath
  existing_hookspath="$( cd "$REPO_ROOT" && git config --get core.hooksPath 2>/dev/null || true )"
  local force_flag=""
  if [ -n "$existing_hookspath" ] && [ "$existing_hookspath" != ".git/hooks" ]; then
    force_flag="--force"
    say "Detected core.hooksPath='${existing_hookspath}' (likely husky residue) — using lefthook install --force"
  fi

  if command -v lefthook >/dev/null 2>&1; then
    if [ "$DRY_RUN" = "1" ]; then
      say "DRY: would run 'lefthook install ${force_flag}'"
    else
      # force_flag may be empty, intentional split
      ( cd "$REPO_ROOT" && lefthook install $force_flag ) \
        || say "lefthook install reported a non-zero exit — check repo state"
    fi
  else
    say "lefthook not installed — install with one of:"
    say "    brew install lefthook"
    say "    go install github.com/evilmartians/lefthook@latest"
    say "    npm install -D lefthook && npx lefthook install"
    say "Then run: lefthook install (in repo root)"
  fi
}

activator_husky() {
  if [ ! -f "$REPO_ROOT/package.json" ]; then
    say "WARN: package.json not found — husky activator expects an npm/pnpm/yarn project."
    say "      Run: pnpm init -y && pnpm add -D husky && pnpm exec husky"
    return 0
  fi
  if ! grep -q '"husky"' "$REPO_ROOT/package.json" 2>/dev/null; then
    say "Note: 'husky' is not yet in package.json devDependencies."
    say "      Run: pnpm add -D husky && pnpm exec husky"
  fi
  if [ -d "$REPO_ROOT/node_modules/husky" ]; then
    if [ "$DRY_RUN" = "1" ]; then
      say "DRY: would run 'pnpm exec husky' (husky activate)"
    else
      ( cd "$REPO_ROOT" && pnpm exec husky 2>/dev/null \
        || ( cd "$REPO_ROOT" && npx husky 2>/dev/null ) \
        || say "husky activation failed — run \`pnpm exec husky\` manually" )
    fi
  else
    if [ "$DRY_RUN" = "1" ]; then
      say "DRY: would run 'git config core.hooksPath .husky' (manual fallback)"
    else
      ( cd "$REPO_ROOT" && git config core.hooksPath .husky )
      say "Set core.hooksPath=.husky (husky package missing — install for auto-activation)"
    fi
  fi
}

activator_precommit() {
  local target="$REPO_ROOT/.pre-commit-config.yaml"
  if [ -f "$target" ] && [ "$FORCE" = "0" ]; then
    say "Keeping existing .pre-commit-config.yaml (use --force to overwrite)"
  else
    say "Writing .pre-commit-config.yaml"
    do_cp "$SOURCE_ACTIVATORS/.pre-commit-config.yaml" "$target" 0644
  fi
  if command -v pre-commit >/dev/null 2>&1; then
    if [ "$DRY_RUN" = "1" ]; then
      say "DRY: would run 'pre-commit install --hook-type ...' for each hook"
    else
      ( cd "$REPO_ROOT" && \
        pre-commit install --hook-type pre-commit \
                           --hook-type pre-push \
                           --hook-type pre-merge-commit \
                           --hook-type prepare-commit-msg \
                           --hook-type post-commit \
                           --hook-type commit-msg \
      ) || say "pre-commit install reported a non-zero exit — check repo state"
    fi
  else
    say "pre-commit not installed — install with one of:"
    say "    pip install pre-commit"
    say "    brew install pre-commit"
    say "    pipx install pre-commit"
    say "Then run: pre-commit install (in repo root)"
  fi
}

activator_manual() {
  if [ "$DRY_RUN" = "1" ]; then
    say "DRY: would run 'git config core.hooksPath .husky'"
  else
    ( cd "$REPO_ROOT" && git config core.hooksPath .husky )
    say "Set core.hooksPath=.husky"
  fi
}

if [ "$NO_ACTIVATE" = "1" ]; then
  say "Activator wiring skipped (--no-activate)."
else
  case "$ACTIVATOR" in
    lefthook)    activator_lefthook ;;
    husky)       activator_husky ;;
    pre-commit)  activator_precommit ;;
    manual)      activator_manual ;;
  esac
fi

# ---------------------------------------------------------------------------
# README pointer (drop into .husky/README.md)
# ---------------------------------------------------------------------------
README_NOTICE="$TARGET_HUSKY/README.md"
if [ ! -f "$README_NOTICE" ]; then
  if [ "$DRY_RUN" = "1" ]; then
    say "DRY: would drop pointer at .husky/README.md"
  else
    cat > "$README_NOTICE" <<EOF
# .husky/ (managed by vibecrafted-hooks-template)

Hooks here are installed from \`vibecrafted/templates/hooks/\`.

- Activator: **${ACTIVATOR}**
- Tweak behavior in \`config.env\` (opt-in flags).
- Drop repo-specific extensions in \`local/<hook>.d/*.sh\` (auto-discovered).
- Failed warn logs land in \`warns/\` (gitignored, rolling retention).

Re-run the installer to refresh:
\`\`\`
bash /path/to/vibecrafted/templates/hooks/install.sh --activator ${ACTIVATOR}
\`\`\`

Switch activator: re-run with a different \`--activator\` flag and remove
the old activator's config file at repo root (\`lefthook.yml\` /
\`.pre-commit-config.yaml\`).
EOF
  fi
fi

say "Done."
[ "$DRY_RUN" = "1" ] && say "(dry-run — no files were modified)"
say ""
say "Next steps:"
say "  1. Edit .husky/config.env to enable the gates your repo needs."
say "  2. Add repo-specific extensions in .husky/local/<hook>.d/*.sh if needed."
case "$ACTIVATOR" in
  lefthook)
    say "  3. Verify lefthook picked up the hooks: lefthook run pre-commit"
    ;;
  husky)
    say "  3. Verify husky registered hooks: git config --get core.hooksPath"
    ;;
  pre-commit)
    say "  3. Verify pre-commit hooks are active: pre-commit run --all-files"
    ;;
  manual)
    say "  3. Verify hooksPath: git config --get core.hooksPath"
    ;;
esac
say "  4. Smoke test: git commit --allow-empty -m \"chore(hooks): smoke\""
