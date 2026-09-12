#!/usr/bin/env bash
# dev-up.sh — stand up a persistent, per-repo Vibecrafted dev container.
#
# Usage:
#   ./dev-up.sh                 # use the current directory as the repo
#   ./dev-up.sh /path/to/repo   # use a specific repo
#
# Each repo gets its own container + its own named volumes (session history for
# claude / codex / gemini / kimi + the AICX corpus). Rebuild or `down` freely —
# history survives. Wipe with:  docker compose -p <project> -f compose.dev.yaml down -v
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

repo="${1:-$PWD}"
if [ ! -d "$repo" ]; then
  echo "error: not a directory: $repo" >&2
  exit 2
fi
repo="$(cd "$repo" && pwd)"

# Stable per-repo project slug (lowercase, alnum + dashes).
slug="$(basename "$repo" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-*//;s/-*$//')"
[ -n "$slug" ] || slug="workspace"
project="vc-${slug}"

if command -v docker >/dev/null 2>&1; then
  DOCKER=docker
elif command -v podman >/dev/null 2>&1; then
  DOCKER=podman
else
  echo "error: neither docker nor podman found on PATH" >&2
  exit 1
fi

echo "[dev-up] repo:     $repo"
echo "[dev-up] project:  $project"
echo "[dev-up] building + starting (first build compiles the toolchain; grab a coffee)…"

VC_WORKSPACE_DIR="$repo" "$DOCKER" compose -p "$project" -f "$here/compose.dev.yaml" up -d --build

cat <<EOF

[dev-up] up. Session history persists in the '${project}_*' volumes.

  Enter shell:   $DOCKER compose -p $project -f $here/compose.dev.yaml exec dev zsh
  Stop:          $DOCKER compose -p $project -f $here/compose.dev.yaml down
  Wipe history:  $DOCKER compose -p $project -f $here/compose.dev.yaml down -v
EOF
