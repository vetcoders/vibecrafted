#!/usr/bin/env bash
# ruff-spec.sh — print the one ruff this repository runs, as `ruff@X.Y.Z`.
#
# The pin lives once, in pyproject.toml `[tool.ruff] required-version`.
# ruff enforces it itself (any other version refuses to run in this tree),
# and the hooks hand this spec to uvx so every host resolves the same binary
# instead of whatever version it has cached. A missing or loose pin fails
# closed: a range cannot tell uvx which ruff to run.
set -euo pipefail

pyproject="${1:-pyproject.toml}"
version="$(
    sed -n 's/^required-version = "==\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)"$/\1/p' \
        "$pyproject" 2>/dev/null | head -n 1 || true
)"
if [ -z "$version" ]; then
    printf 'ruff-spec: %s must pin one exact ruff: [tool.ruff] required-version = "==X.Y.Z"\n' \
        "$pyproject" >&2
    exit 1
fi
printf 'ruff@%s\n' "$version"
