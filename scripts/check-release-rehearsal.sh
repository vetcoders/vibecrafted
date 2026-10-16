#!/usr/bin/env bash
# Read-only pre-tag admission; publication uses the same exact-SHA evidence.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${VIBECRAFTED_RELEASE_REPO:-vetcoders/vibecrafted}"
SHA="${1:-$(git -C "$ROOT" rev-parse HEAD)}"

refuse() {
  printf 'release-rehearsal: no successful Release gate rehearsal (macOS) for exact SHA %s\n' "$SHA" >&2
  printf 'Run after merging this SHA to main: gh workflow run gate-rehearsal.yml --repo %s --ref main\n' "$REPO" >&2
  exit 1
}

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || refuse
# A PR run may test a synthetic merge ref, not the commit later tagged.
# Require a completed manual rehearsal and independently check its headSha.
RUN_ID="$(gh run list --repo "$REPO" --workflow gate-rehearsal.yml \
  --commit "$SHA" --event workflow_dispatch --status success --limit 100 \
  --json databaseId,headSha,status,conclusion \
  --jq "map(select(.headSha == \"$SHA\" and .status == \"completed\" and .conclusion == \"success\"))[0].databaseId // empty")" || refuse
[[ "$RUN_ID" =~ ^[0-9]+$ ]] || refuse
printf 'release-rehearsal: exact SHA %s admitted by successful run %s\n' "$SHA" "$RUN_ID"
