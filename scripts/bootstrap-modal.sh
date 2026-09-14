#!/usr/bin/env bash
# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Runtime — modal bootstrap installer
#
# One-liner public install path. Source of truth lives in the vibecrafted repo at
# `scripts/bootstrap-modal.sh`; the publicly-fetched copy is mirrored to
# `vetcoders/vc-workspace` so that:
#
#   curl -fsSL https://raw.githubusercontent.com/vetcoders/vc-workspace/main/bootstrap-modal.sh | bash
#
# resolves to this exact script.
#
# What it does:
#   1. Detect host (Linux x86_64/aarch64 or macOS arm64 with Docker Desktop).
#   2. Ensure docker + docker compose v2 are present (offers install hints if not).
#   3. Clone (or update) the vibecrafted repo into a configurable path.
#   4. Build the base image with all ARGs enabled.
#   5. Prompt for TS_AUTHKEY and TS_HOSTNAME (or accept env vars / flags).
#   6. Bring up the runtime stack via docker compose.
#   7. Wait for healthchecks and report next-steps.
#
# Idempotent: re-runs update the checkout and rebuild only if needed.
#
# Usage:
#   curl -fsSL .../bootstrap-modal.sh | bash
#   curl -fsSL .../bootstrap-modal.sh | bash -s -- --branch main
#   curl -fsSL .../bootstrap-modal.sh | bash -s -- \
#     --branch release/v2.0.1 \
#     --ts-authkey tskey-auth-XXXX-YYYY \
#     --ts-hostname runtime-host-a \
#     --workdir /opt/vibecrafted
#
# Env overrides (in addition to flags):
#   VIBECRAFTED_BRANCH        (default: main)
#   VIBECRAFTED_WORKDIR       (default: $HOME/vibecrafted-runtime)
#   VIBECRAFTED_REPO_URL      (default: https://github.com/vetcoders/vibecrafted.git)
#   TS_AUTHKEY                (no default — prompts if missing and TTY)
#   TS_HOSTNAME               (default: runtime-$(hostname -s))
#   VIBECRAFTED_BOOTSTRAP_YES (default: 0 — set 1 to skip prompts)

set -euo pipefail

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log()   { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok()    { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m!\033[0m %s\n' "$*" >&2; }
die()   { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
hdr()   { printf '\n\033[1m\033[38;5;173m⚒  %s\033[0m\n' "$*"; printf '  ─────────────────────────────────────\n'; }

# ---------------------------------------------------------------------------
# Defaults + arg parsing
# ---------------------------------------------------------------------------
VIBECRAFTED_BRANCH="${VIBECRAFTED_BRANCH:-main}"
VIBECRAFTED_WORKDIR="${VIBECRAFTED_WORKDIR:-$HOME/vibecrafted-runtime}"
VIBECRAFTED_REPO_URL="${VIBECRAFTED_REPO_URL:-https://github.com/vetcoders/vibecrafted.git}"
TS_AUTHKEY="${TS_AUTHKEY:-}"
TS_HOSTNAME_DEFAULT="runtime-$(hostname -s 2>/dev/null || echo unknown)"
TS_HOSTNAME="${TS_HOSTNAME:-$TS_HOSTNAME_DEFAULT}"
ASSUME_YES="${VIBECRAFTED_BOOTSTRAP_YES:-0}"
SKIP_BUILD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch)        shift; VIBECRAFTED_BRANCH="${1:?--branch needs value}";;
    --workdir)       shift; VIBECRAFTED_WORKDIR="${1:?--workdir needs value}";;
    --repo-url)      shift; VIBECRAFTED_REPO_URL="${1:?--repo-url needs value}";;
    --ts-authkey)    shift; TS_AUTHKEY="${1:?--ts-authkey needs value}";;
    --ts-hostname)   shift; TS_HOSTNAME="${1:?--ts-hostname needs value}";;
    --yes|-y)        ASSUME_YES=1 ;;
    --skip-build)    SKIP_BUILD=1 ;;
    --help|-h)
      cat <<'EOF'
bootstrap-modal.sh — Vibecrafted runtime container installer

Options:
  --branch BRANCH        git branch / tag to check out (default: main)
  --workdir DIR          clone target (default: $HOME/vibecrafted-runtime)
  --repo-url URL         git remote (default: https://github.com/vetcoders/vibecrafted.git)
  --ts-authkey KEY       Tailscale auth key (prompts if missing and TTY)
  --ts-hostname NAME     Tailscale hostname (default: runtime-$(hostname -s))
  --yes / -y             skip all prompts (TS_AUTHKEY must be set via env/flag)
  --skip-build           reuse existing images (skip docker build step)
  --help / -h            show this help
EOF
      exit 0
      ;;
    *) die "Unknown argument: $1 (use --help)";;
  esac
  shift
done

is_interactive() { [[ -t 0 && -t 1 ]]; }

# ---------------------------------------------------------------------------
# Host detection
# ---------------------------------------------------------------------------
hdr "Step 1/6 — Detecting host"

OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
  Linux*)  HOST_OS="linux" ;;
  Darwin*) HOST_OS="macos" ;;
  *)       die "Unsupported OS: $OS (need Linux or macOS)" ;;
esac

case "$ARCH" in
  x86_64|amd64)     HOST_ARCH="x86_64" ;;
  aarch64|arm64)    HOST_ARCH="aarch64" ;;
  *)                die "Unsupported architecture: $ARCH" ;;
esac

ok "Host: $HOST_OS / $HOST_ARCH"
ok "Hostname: $(hostname)"

# ---------------------------------------------------------------------------
# Docker check
# ---------------------------------------------------------------------------
hdr "Step 2/6 — Docker prerequisites"

if ! command -v docker >/dev/null 2>&1; then
  warn "docker not found."
  case "$HOST_OS" in
    linux)
      warn "Install: curl -fsSL https://get.docker.com | sh"
      warn "Then: sudo usermod -aG docker \$USER && newgrp docker"
      ;;
    macos)
      warn "Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
      ;;
  esac
  die "Re-run bootstrap after Docker is available."
fi

if ! docker compose version >/dev/null 2>&1; then
  die "docker compose v2 not found. Update Docker Desktop, or install docker-compose-plugin."
fi

if ! docker info >/dev/null 2>&1; then
  die "Docker daemon not responsive. Start Docker Desktop / dockerd and re-run."
fi

ok "docker: $(docker --version)"
ok "compose: $(docker compose version --short 2>/dev/null || echo present)"

# ---------------------------------------------------------------------------
# Clone / update vibecrafted
# ---------------------------------------------------------------------------
hdr "Step 3/6 — Vibecrafted source"

if [[ -d "$VIBECRAFTED_WORKDIR/.git" ]]; then
  log "Existing checkout at $VIBECRAFTED_WORKDIR — updating..."
  git -C "$VIBECRAFTED_WORKDIR" fetch --prune --quiet
  git -C "$VIBECRAFTED_WORKDIR" checkout --quiet "$VIBECRAFTED_BRANCH"
  git -C "$VIBECRAFTED_WORKDIR" pull --ff-only --quiet || warn "ff-only pull failed (local changes?) — continuing with current state"
else
  log "Cloning $VIBECRAFTED_REPO_URL into $VIBECRAFTED_WORKDIR..."
  mkdir -p "$(dirname "$VIBECRAFTED_WORKDIR")"
  git clone --quiet --branch "$VIBECRAFTED_BRANCH" "$VIBECRAFTED_REPO_URL" "$VIBECRAFTED_WORKDIR"
fi

cd "$VIBECRAFTED_WORKDIR"
VERSION="$(cat VERSION 2>/dev/null || echo unknown)"
SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
ok "Checked out $VIBECRAFTED_BRANCH @ $SHA (VERSION=$VERSION)"

# ---------------------------------------------------------------------------
# Image build
# ---------------------------------------------------------------------------
hdr "Step 4/6 — Build container images"

if (( SKIP_BUILD )); then
  warn "--skip-build set — reusing existing images"
else
  log "Building vibecrafted-base:local (this is the slow one — ~5 min first time)..."
  docker build \
    -t vibecrafted-base:local \
    --build-arg INSTALL_AGENT_CLIS=true \
    --build-arg INSTALL_FOUNDATIONS=true \
    --build-arg INSTALL_RUST=true \
    "$VIBECRAFTED_WORKDIR"
  ok "vibecrafted-base:local built"

  log "Building vibecrafted-runtime:local..."
  docker compose -f "$VIBECRAFTED_WORKDIR/docker/runtime/docker-compose.yml" build vibecrafted-runtime
  ok "vibecrafted-runtime:local built"
fi

# ---------------------------------------------------------------------------
# Tailscale env wizard
# ---------------------------------------------------------------------------
hdr "Step 5/6 — Tailscale config"

ENV_FILE="$VIBECRAFTED_WORKDIR/docker/runtime/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$VIBECRAFTED_WORKDIR/docker/runtime/tailscale.env.example" "$ENV_FILE"
  log "Created $ENV_FILE from template"
fi

if [[ -z "$TS_AUTHKEY" ]]; then
  # Try to read existing value from env file
  existing="$(grep -E '^TS_AUTHKEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"
  if [[ -n "$existing" ]]; then
    TS_AUTHKEY="$existing"
    log "Using existing TS_AUTHKEY from $ENV_FILE"
  elif (( ASSUME_YES )); then
    die "TS_AUTHKEY not set (env / flag / .env) and --yes given — cannot prompt"
  elif is_interactive; then
    printf '\n  Tailscale auth key (https://login.tailscale.com/admin/settings/keys)\n'
    printf '  TS_AUTHKEY: '
    read -r TS_AUTHKEY
    [[ -n "$TS_AUTHKEY" ]] || die "TS_AUTHKEY required"
  else
    die "TS_AUTHKEY not set and no TTY for prompt — pass --ts-authkey or set env"
  fi
fi

# Rewrite env file with current values (idempotent)
tmp_env="$(mktemp)"
{
  printf 'TS_AUTHKEY=%s\n' "$TS_AUTHKEY"
  printf 'TS_HOSTNAME=%s\n' "$TS_HOSTNAME"
  printf 'TS_EXTRA_ARGS=--ssh\n'
  printf '# Generated by bootstrap-modal.sh on %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$tmp_env"
mv "$tmp_env" "$ENV_FILE"
chmod 600 "$ENV_FILE"
ok "Wrote $ENV_FILE (mode 600)"
ok "Hostname on tailnet: $TS_HOSTNAME"

# ---------------------------------------------------------------------------
# Bring stack up
# ---------------------------------------------------------------------------
hdr "Step 6/6 — Bring up runtime stack"

cd "$VIBECRAFTED_WORKDIR/docker/runtime"
docker compose up -d
ok "Stack started"

log "Waiting for tailscale sidecar to authenticate..."
TS_READY=0
for _ in {1..30}; do
  if docker compose exec -T tailscale tailscale status --peers=false --self=true >/dev/null 2>&1; then
    TS_READY=1
    break
  fi
  sleep 2
done

if (( TS_READY )); then
  ok "Tailscale up — node visible on tailnet as: $TS_HOSTNAME"
  docker compose exec -T tailscale tailscale status --peers=false --self=true || true
else
  warn "Tailscale did not become ready within 60s — check: docker compose logs tailscale"
fi

log "Waiting for vibecrafted-runtime healthcheck..."
RT_READY=0
for _ in {1..30}; do
  status="$(docker inspect -f '{{.State.Health.Status}}' vibecrafted-runtime 2>/dev/null || echo unknown)"
  if [[ "$status" == "healthy" ]]; then
    RT_READY=1
    break
  fi
  sleep 4
done

if (( RT_READY )); then
  ok "vibecrafted-runtime healthy"
else
  warn "Runtime didn't reach healthy within 2 min — check: docker compose logs vibecrafted-runtime"
fi

# ---------------------------------------------------------------------------
# Next steps
# ---------------------------------------------------------------------------
cat <<EOF

  ╭─────────────────────────────────────────────────────────────╮
  │  𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. runtime is up on $TS_HOSTNAME
  ╰─────────────────────────────────────────────────────────────╯

  Test from any tailnet device:
    tailscale ssh $TS_HOSTNAME
    tailscale ssh $TS_HOSTNAME vibecrafted doctor

  Operator commands (run from $VIBECRAFTED_WORKDIR/docker/runtime):
    docker compose ps
    docker compose logs -f vibecrafted-runtime
    docker compose restart vibecrafted-runtime
    docker compose down            # keep state
    docker compose down -v         # full reset

  Docs:
    $VIBECRAFTED_WORKDIR/docs/RUNTIME_CONTAINER.md
    $VIBECRAFTED_WORKDIR/docker/runtime/README.md

  Source ($VIBECRAFTED_BRANCH @ $SHA, VERSION=$VERSION).

EOF
