#!/usr/bin/env bash
# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. runtime entrypoint
#
# Sequence:
#   1. Start sshd in the background (so operator can `tailscale ssh` in)
#   2. Seed / refresh skills via the base entrypoint
#   3. Dispatch the runtime command:
#        - `serve`      : idle loop, sshd-only access (default)
#        - `vibecrafted ...` / any base-known command: pass through
#        - `bash` / `sh` / `zsh`: interactive shell
#
# Multi-process discipline:
#   - tini is PID 1 (handles SIGTERM → forwards to the child group)
#   - sshd runs in -D mode but backgrounded
#   - we trap SIGTERM and shut sshd down before exiting

set -euo pipefail

log() { printf '[vibecrafted-runtime] %s\n' "$*" >&2; }

# --- sshd preflight ---------------------------------------------------------
# sshd refuses to start without host keys; generate on first boot if missing.
if [[ -z "$(ls /etc/ssh/ssh_host_*_key 2>/dev/null)" ]]; then
  log "Generating SSH host keys (first boot)..."
  sudo ssh-keygen -A >/dev/null
fi

# vibecrafted user needs an authorized_keys path that survives container restarts.
# Compose mounts a host directory at /workspace/.vibecrafted/ssh/ — link it.
# The logs dir must exist too: sshd is started below with `-E .../logs/sshd.log`
# and refuses to start if it cannot open that log file on a fresh volume.
mkdir -p /workspace/.vibecrafted/ssh /workspace/.vibecrafted/logs
chmod 700 /workspace/.vibecrafted/ssh
if [[ ! -L "$HOME/.ssh" ]]; then
  rm -rf "$HOME/.ssh"
  ln -sf /workspace/.vibecrafted/ssh "$HOME/.ssh"
fi

# --- sshd start -------------------------------------------------------------
log "Starting sshd on port ${SSH_PORT:-22}..."
sudo /usr/sbin/sshd -D -p "${SSH_PORT:-22}" -E /workspace/.vibecrafted/logs/sshd.log &
SSHD_PID=$!

cleanup() {
  log "SIGTERM received — shutting down sshd (pid $SSHD_PID)..."
  if kill -0 "$SSHD_PID" 2>/dev/null; then
    sudo kill -TERM "$SSHD_PID" 2>/dev/null || true
    wait "$SSHD_PID" 2>/dev/null || true
  fi
  log "Bye."
  exit 0
}
trap cleanup SIGTERM SIGINT

# --- delegate to base entrypoint for skill-seed + command dispatch ---------
log "Chaining into base entrypoint with command: $*"

case "${1:-serve}" in
  serve)
    # No command — idle loop, sshd is the only surface. Operator gets in
    # via `tailscale ssh runtime-host-a` and runs `vibecrafted ...` there.
    log "Runtime mode: serve (sshd-only; tailscale ssh runtime-host-a to operate)"
    log "Stamp: $(cat /etc/vibecrafted-runtime.version 2>/dev/null || echo unknown)"
    # Seed skills synchronously so an interactive ssh session immediately
    # finds them under $VIBECRAFTED_HOME.
    VIBECRAFTED_DOCKER_SEED_SKILLS=1 vibecrafted-docker-entrypoint version >/dev/null 2>&1 || true
    # Idle on sshd — wait blocks until sshd exits or SIGTERM hits.
    wait "$SSHD_PID"
    ;;
  *)
    # Passthrough: delegate the rest to the base entrypoint, which seeds
    # skills and routes the command appropriately.
    vibecrafted-docker-entrypoint "$@" &
    CMD_PID=$!
    # If the command finishes, drop sshd and exit with the command's code.
    wait "$CMD_PID"
    CMD_EXIT=$?
    log "Command finished (exit $CMD_EXIT) — shutting down sshd..."
    sudo kill -TERM "$SSHD_PID" 2>/dev/null || true
    wait "$SSHD_PID" 2>/dev/null || true
    exit "$CMD_EXIT"
    ;;
esac
