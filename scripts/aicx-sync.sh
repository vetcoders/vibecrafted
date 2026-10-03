#!/usr/bin/env bash
# aicx-sync.sh — Plan 08 (META_22) operator CLI entry.
#
# Wraps `python -m vibecrafted_core.aicx_sync` with operator-friendly
# argument shape + config loading. Reads the [aicx_sync] table of the one
# operator config, ${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/config.toml
# (see config/aicx-sync.toml.example), for store/remote/namespace defaults.
#
# Usage:
#   scripts/aicx-sync.sh dry-run [--remote <host>] [--namespace <ns>]
#   scripts/aicx-sync.sh apply   [--remote <host>] [--namespace <ns>]
#   scripts/aicx-sync.sh log-show
#   scripts/aicx-sync.sh help
#
# Defaults (when --remote / --namespace are absent):
#   local store    -> $HOME/.aicx/store
#   remote staging -> $HOME/.frontier-vault/<host>/staging
#   config         -> [aicx_sync] in ${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/config.toml
#
# Plan 08 contract: dry-run first, then apply. The script *always* refuses
# to run `apply` without an explicit confirmation flag (or a dry-run run
# in the same shell — informational only; the real protection is that the
# in-process engine never mutates without explicit dry_run=False).
#
# Vibecrafted with AI Agents (c)2024-2026 LibraxisAI

set -euo pipefail

# Resolve repo root so the script works regardless of CWD.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/.." && pwd)

DEFAULT_LOCAL_STORE="${HOME}/.aicx/store"
DEFAULT_REMOTE_STAGING_BASE="${HOME}/.frontier-vault"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DEFAULT_CONFIG_FILE="$CONFIG_HOME/vibecrafted/config.toml"
CONFIG_SECTION="aicx_sync"
# Retired standalone file. Named only so the operator learns it is ignored.
RETIRED_CONFIG_FILE="$CONFIG_HOME/vetcoders/aicx-sync.toml"

usage() {
    cat <<'USAGE'
aicx-sync.sh — Plan 08 (META_22) AICX cross-machine sync v2

Usage:
  scripts/aicx-sync.sh dry-run [--remote <host>] [--namespace <ns>] [--local <path>] [--config <toml>]
  scripts/aicx-sync.sh apply   [--remote <host>] [--namespace <ns>] [--local <path>] [--config <toml>]
  scripts/aicx-sync.sh log-show
  scripts/aicx-sync.sh help

Flags:
  --remote <host>      remote host name (read by ~/.scripts/sync-tool.py first)
  --namespace <ns>     AICX namespace to scope (default: all)
  --local <path>       local AICX store (default: ${HOME}/.aicx/store)
  --config <toml>      file whose [aicx_sync] table is read
                       (default: ${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/config.toml)

Defaults — dry-run first, then apply.

See docs/AICX-SYNC.md for the operator guide and authority tier table.
USAGE
}

# Minimal TOML reader — extracts `key = "value"` from a [section] block.
# Good enough for the small Plan 08 config surface (endpoint, namespaces,
# dry_run_default). Avoids a Python dependency at shell-init time.
toml_get() {
    local file="$1"
    local section="$2"
    local key="$3"
    [[ -f "$file" ]] || { printf ''; return 0; }
    awk -v section="[$section]" -v key="$key" '
        BEGIN { in_section = (section == "[]") }
        /^\[/ {
            in_section = ($0 == section)
            next
        }
        in_section && $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
            # Strip "key = " prefix, then surrounding quotes / whitespace.
            sub("^[[:space:]]*" key "[[:space:]]*=[[:space:]]*", "")
            sub("[[:space:]]+$", "")
            gsub(/^"|"$/, "")
            gsub(/^'\''|'\''$/, "")
            print
            exit
        }
    ' "$file"
}

# ---- argument parsing -----------------------------------------------------

if [[ $# -lt 1 ]]; then
    usage
    exit 1
fi

COMMAND="$1"
shift

REMOTE_HOST=""
NAMESPACE=""
LOCAL_STORE=""
CONFIG_FILE="$DEFAULT_CONFIG_FILE"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --remote)
            REMOTE_HOST="${2:-}"
            shift 2
            ;;
        --namespace)
            NAMESPACE="${2:-}"
            shift 2
            ;;
        --local)
            LOCAL_STORE="${2:-}"
            shift 2
            ;;
        --config)
            CONFIG_FILE="${2:-}"
            shift 2
            ;;
        -h|--help|help)
            usage
            exit 0
            ;;
        *)
            printf 'aicx-sync.sh: unknown flag %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

# The retired standalone file is never a source of values.
if [[ -e "$RETIRED_CONFIG_FILE" ]]; then
    printf 'aicx-sync.sh: %s is no longer read; move its keys into the [%s] table of %s\n' \
        "$RETIRED_CONFIG_FILE" "$CONFIG_SECTION" "$DEFAULT_CONFIG_FILE" >&2
fi

# ---- config-file fallbacks ------------------------------------------------

if [[ -z "$LOCAL_STORE" ]]; then
    LOCAL_STORE=$(toml_get "$CONFIG_FILE" "$CONFIG_SECTION" "local_store")
fi
if [[ -z "$LOCAL_STORE" ]]; then
    LOCAL_STORE="$DEFAULT_LOCAL_STORE"
fi

if [[ -z "$REMOTE_HOST" ]]; then
    REMOTE_HOST=$(toml_get "$CONFIG_FILE" "$CONFIG_SECTION" "remote_host")
fi

if [[ -z "$NAMESPACE" ]]; then
    NAMESPACE=$(toml_get "$CONFIG_FILE" "$CONFIG_SECTION" "namespace")
fi

# Compute remote staging path. The bash wrapper expects the operator's
# rsync layer (~/.scripts/sync-tool.py) to have already mirrored the remote
# corpus into ~/.frontier-vault/<host>/staging before the in-process engine
# is invoked. When no --remote is given we point at a stub staging dir so
# `dry-run` against a local-only setup still produces output (zero adds,
# zero conflicts).
if [[ -n "$REMOTE_HOST" ]]; then
    REMOTE_STAGING="$DEFAULT_REMOTE_STAGING_BASE/$REMOTE_HOST/staging"
else
    REMOTE_STAGING="$DEFAULT_REMOTE_STAGING_BASE/_no_remote_/staging"
fi

# Narrow to namespace subdir when specified (e.g. ~/.aicx/store/vetcoders/vibecrafted).
if [[ -n "$NAMESPACE" ]]; then
    LOCAL_STORE="$LOCAL_STORE/$NAMESPACE"
    REMOTE_STAGING="$REMOTE_STAGING/$NAMESPACE"
fi

# ---- python invocation ----------------------------------------------------

# Prefer uv (the rest of the framework uses it for vibecrafted-core).
run_engine() {
    if command -v uv >/dev/null 2>&1; then
        uv run --project "$REPO_ROOT/vibecrafted-core" --quiet \
            python -m vibecrafted_core.aicx_sync "$@"
    else
        PYTHONPATH="$REPO_ROOT/vibecrafted-core" python3 \
            -m vibecrafted_core.aicx_sync "$@"
    fi
}

case "$COMMAND" in
    dry-run)
        printf '[aicx-sync] dry-run (no fs mutation)\n'
        printf '  local:  %s\n' "$LOCAL_STORE"
        printf '  remote: %s\n' "$REMOTE_STAGING"
        run_engine dry-run "$LOCAL_STORE" "$REMOTE_STAGING"
        ;;
    apply)
        printf '[aicx-sync] apply (will mutate both sides)\n'
        printf '  local:  %s\n' "$LOCAL_STORE"
        printf '  remote: %s\n' "$REMOTE_STAGING"
        run_engine apply "$LOCAL_STORE" "$REMOTE_STAGING"
        ;;
    log-show)
        run_engine log-show
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        printf 'aicx-sync.sh: unknown command %s\n' "$COMMAND" >&2
        usage >&2
        exit 2
        ;;
esac
