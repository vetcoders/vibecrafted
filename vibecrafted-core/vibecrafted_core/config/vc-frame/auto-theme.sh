#!/usr/bin/env bash
# auto-theme.sh — host-aware vc_frame theme name resolver.
#
# Plan 12 (META_22) — Wave 4 agent-native runtime cut.
#
# Maps the current workstation to one of the mesh accent themes shipped in
# config/vc-frame/themes/vetcoders-mesh.kdl (mesh-red, mesh-purple,
# mesh-cyan, mesh-green). Falls back to "vibecrafted" (the neutral default
# in config.kdl) when the host is unknown or no mapping is configured.
#
# The host -> theme mapping is NOT built in. It is read from configuration,
# first source found wins:
#   1. VIBECRAFTED_MESH_MAP   comma-separated `host=theme` pairs, e.g.
#                             host-a=mesh-red,host-b=mesh-green
#   2. mesh.conf              one `host theme` pair per line; `#` comments
#                             and blank lines allowed; a host may appear on
#                             several lines (aliases). Path:
#                             ${VIBECRAFTED_MESH_CONF:-${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/mesh.conf}
#
# Match rules: exact host name, or `name-*` prefix (so `host-a-2` matches
# the `host-a` entry). Host names are normalized (lowercase, .local/.lan
# stripped) before matching; config keys are normalized the same way.
#
# Host detection order:
#   1. VIBECRAFTED_HOST_NAME (operator override, single source of truth)
#   2. scutil --get LocalHostName        (macOS canonical local name)
#   3. scutil --get ComputerName         (macOS user-friendly name)
#   4. hostname -s                       (Linux short hostname)
#   5. hostname                          (final fallback)
#
# Output: theme name on stdout. Exit 0 always; unknown host is not an error.
#
# Environment:
#   VIBECRAFTED_HOST_NAME  override detected host (for tests/staging)
#   VIBECRAFTED_THEME      pin the theme outright (skips detection)
#   VIBECRAFTED_MESH_MAP   inline host=theme mapping (wins over mesh.conf)
#   VIBECRAFTED_MESH_CONF  path to mesh.conf (default under VIBECRAFTED_HOME)
#   VIBECRAFTED_HOME       framework home (default $HOME/.vibecrafted)
#
# Vibecrafted with AI Agents (c)2024-2026 LibraxisAI

set -euo pipefail

FALLBACK_THEME="vibecrafted"

# Normalize a host name: strip .local / .lan suffixes, then lowercase.
# Override paths and config keys MUST flow through the same normalization
# so HOST-A, host-a.local, and host-a all resolve identically.
normalize_host() {
    local name="$1"
    name="${name%.local}"
    name="${name%.lan}"
    printf '%s' "$name" | tr '[:upper:]' '[:lower:]'
}

# Detect the current host name using a layered probe. The first non-empty
# answer wins. Each layer is forgiving — a missing tool is silently skipped.
detect_host() {
    local name=""

    if [[ -n "${VIBECRAFTED_HOST_NAME:-}" ]]; then
        name="$VIBECRAFTED_HOST_NAME"
    elif command -v scutil >/dev/null 2>&1; then
        name=$(scutil --get LocalHostName 2>/dev/null || true)
        if [[ -z "$name" ]]; then
            name=$(scutil --get ComputerName 2>/dev/null || true)
        fi
    fi

    if [[ -z "$name" ]]; then
        name=$(hostname -s 2>/dev/null || hostname 2>/dev/null || true)
    fi

    normalize_host "$name"
}

# Emit the mapping as `host<TAB>theme` lines, one per entry, from the first
# configured source. Emits nothing when no source is configured.
load_mapping() {
    if [[ -n "${VIBECRAFTED_MESH_MAP:-}" ]]; then
        local pair
        IFS=',' read -r -a pairs <<<"$VIBECRAFTED_MESH_MAP"
        for pair in "${pairs[@]}"; do
            pair="${pair//[[:space:]]/}"
            [[ -z "$pair" || "$pair" != *=* ]] && continue
            printf '%s\t%s\n' "${pair%%=*}" "${pair#*=}"
        done
        return 0
    fi

    local conf="${VIBECRAFTED_MESH_CONF:-${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/mesh.conf}"
    [[ -r "$conf" ]] || return 0

    local line host theme
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%%#*}"
        read -r host theme _ <<<"$line" || true
        [[ -z "${host:-}" || -z "${theme:-}" ]] && continue
        printf '%s\t%s\n' "$host" "$theme"
    done <"$conf"
}

resolve_theme() {
    if [[ -n "${VIBECRAFTED_THEME:-}" ]]; then
        printf '%s\n' "$VIBECRAFTED_THEME"
        return 0
    fi

    local host
    host=$(detect_host)

    local key theme
    while IFS=$'\t' read -r key theme; do
        key=$(normalize_host "$key")
        [[ -z "$key" ]] && continue
        if [[ "$host" == "$key" || "$host" == "$key"-* ]]; then
            printf '%s\n' "$theme"
            return 0
        fi
    done < <(load_mapping)

    printf '%s\n' "$FALLBACK_THEME"
}

resolve_theme
