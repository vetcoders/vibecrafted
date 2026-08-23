#!/usr/bin/env bash
# ============================================================================
# entry.sh — vc-workspace container entrypoint
#
# Boots tailscale (if TAILSCALE_AUTHKEY present) + warms up framework env +
# drops into shell.
#
# Environment variables (operator-provided at docker run):
#   TAILSCALE_AUTHKEY    — tskey-auth-... (ephemeral or persistent)
#   TAILSCALE_HOSTNAME   — node name advertised on tailnet (default: vc-workspace-$(hostname))
#   TAILSCALE_TAGS       — comma-separated tags (e.g. tag:devbox)
#   TAILSCALE_EXIT_NODE  — optional: route traffic via specific exit node
#   LOCTREE_GPG_KEY_ID   — GPG key id for release-tag signing (optional)
#   AICX_NO_MUTATION_WARN=1 — suppress aicx all/store mutation warning for scripts
#
# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
# ============================================================================

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
else
    GREEN=''; YELLOW=''; BLUE=''; NC=''
fi
log()  { printf "${BLUE}[entry]${NC} %s\n" "$*"; }
ok()   { printf "${GREEN}  ✓${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}  ⚠${NC} %s\n" "$*" >&2; }

# ── Banner ────────────────────────────────────────────────────────────────
cat <<'EOF'
⚒  vc-workspace — Vetcoders / vibecrafted / loctree / aicx dev container
   debian:trixie · multi-arch · tailnet-aware · 21 skills · 11 foundations
EOF

# ── Tailscale (optional, userspace) ───────────────────────────────────────
if [ -n "${TAILSCALE_AUTHKEY:-}" ]; then
    log "Starting tailscaled (userspace networking)..."
    HOSTNAME_DEFAULT="vc-workspace-$(hostname 2>/dev/null || echo unknown)"
    TS_HOSTNAME="${TAILSCALE_HOSTNAME:-$HOSTNAME_DEFAULT}"
    TS_TAGS="${TAILSCALE_TAGS:-}"

    # Socket + state dirs MUST exist before tailscaled starts — on a fresh boot
    # /var/run/tailscale does not exist and the daemon exits immediately without
    # it (the symptom: "tailnet up ... @ unknown" then tailscaled dead).
    mkdir -p /var/run/tailscale /var/lib/tailscale

    # tailscaled in background, userspace mode (no /dev/net/tun required)
    /usr/sbin/tailscaled --tun=userspace-networking --state=/var/lib/tailscale/tailscaled.state \
        --socket=/var/run/tailscale/tailscaled.sock >/var/log/tailscaled.log 2>&1 &
    TAILSCALED_PID=$!

    # Wait until the daemon answers on its socket (up to ~15s) instead of a blind
    # sleep — and confirm the process actually stayed alive.
    for _i in $(seq 1 15); do
        if tailscale status >/dev/null 2>&1 || tailscale status 2>&1 | grep -qi "stopped\|NeedsLogin\|logged out"; then
            break
        fi
        kill -0 "$TAILSCALED_PID" 2>/dev/null || { warn "tailscaled exited early — see /var/log/tailscaled.log"; break; }
        sleep 1
    done

    # tailscale up
    # --ssh enables Tailscale SSH (peers ssh in WITHOUT an sshd in the image,
    # gated by tailnet ACLs). This is what makes `ssh root@vc-workspace-<host>`
    # work from host-b/host-d/host-e — the container ships no openssh-server.
    TS_UP_ARGS=(
        --authkey="$TAILSCALE_AUTHKEY"
        --hostname="$TS_HOSTNAME"
        --ssh
        --accept-routes=true
        --accept-dns=true
    )
    [ -n "$TS_TAGS" ] && TS_UP_ARGS+=(--advertise-tags="$TS_TAGS")
    [ -n "${TAILSCALE_EXIT_NODE:-}" ] && TS_UP_ARGS+=(--exit-node="$TAILSCALE_EXIT_NODE")

    if tailscale up "${TS_UP_ARGS[@]}" 2>&1 | tail -3; then
        # IP may take a moment to settle — poll instead of reading once.
        TS_IP="unknown"
        for _i in $(seq 1 10); do
            TS_IP="$(tailscale ip -4 2>/dev/null | head -1)"
            [ -n "$TS_IP" ] && break
            TS_IP="unknown"; sleep 1
        done
        ok "tailnet up: $TS_HOSTNAME @ $TS_IP"
        ok "Tailscale SSH on port 22 — peers: ssh root@$TS_HOSTNAME (may require browser check)"
    else
        warn "tailscale up failed — check /var/log/tailscaled.log"
    fi

    # OpenSSH key path on :2222 (batch/agent-friendly; no Tailscale check URL).
    # Tailscale SSH owns :22; key auth goes through serve → local sshd.
    if command -v sshd >/dev/null 2>&1 || [ -x /usr/sbin/sshd ]; then
        mkdir -p /var/run/sshd /root/.ssh
        chmod 700 /root/.ssh
        ssh-keygen -A >/dev/null 2>&1 || true
        if ! pgrep -x sshd >/dev/null 2>&1; then
            /usr/sbin/sshd -p 2222 || warn "sshd -p 2222 failed to start"
        fi
        if tailscale serve --bg --tcp 2222 tcp://127.0.0.1:2222 >/dev/null 2>&1; then
            ok "OpenSSH key auth: ssh -p 2222 root@$TS_HOSTNAME (or Host vetcoders)"
        else
            warn "tailscale serve :2222 failed — key SSH not exposed on tailnet"
        fi
    else
        warn "openssh-server not installed — only Tailscale SSH (port 22) available"
    fi
else
    warn "TAILSCALE_AUTHKEY not set — skipping tailnet (set to join mesh)"
fi

# ── Verify framework readiness ────────────────────────────────────────────
log "Framework readiness probe:"
for tool in aicx aicx-mcp loct loctree-mcp claude codex gemini uv vc_frame starship; do
    if command -v "$tool" >/dev/null 2>&1; then
        version="$("$tool" --version 2>&1 | head -1 | head -c 60)"
        ok "$tool — $version"
    else
        warn "$tool — not found"
    fi
done

# ── Vibecrafted framework state (skills + foundations + symlinks) ─────────
if command -v vibecrafted >/dev/null 2>&1; then
    log "Vibecrafted doctor (lightweight check):"
    vibecrafted doctor 2>&1 | tail -5 || warn "vibecrafted doctor not fully ready"
fi

# ── GPG signing readiness ─────────────────────────────────────────────────
if [ -n "${LOCTREE_GPG_KEY_ID:-}" ]; then
    if [ -d /root/.gnupg ]; then
        ok "GPG key id set: ${LOCTREE_GPG_KEY_ID:0:16}... (keyring at /root/.gnupg)"
    else
        warn "LOCTREE_GPG_KEY_ID set but /root/.gnupg missing — mount ~/.gnupg from host"
    fi
fi

# ── Final note ────────────────────────────────────────────────────────────
log ""
log "Workspace: /workspace (mount the multiroot via -v ~/.vibecrafted/vc-runtime:/workspace)"
log "Quick start: cd /workspace && aicx all -H 4   # canonical corpus build"
log ""

# ── Exec into shell or operator-provided command ──────────────────────────
exec "$@"
