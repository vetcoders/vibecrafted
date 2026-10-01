#!/usr/bin/env bash
# ============================================================================
# bootstrap-workstation.sh — ONE script, NO repo required.
#
# Stands up a complete Vibecrafted dev workstation on a bare Debian/Ubuntu box
# for someone who has nothing but this script:
#
#   curl -fsSL https://raw.githubusercontent.com/vetcoders/vibecrafted/main/vibecrafted-vm/bootstrap-workstation.sh | bash
#
# What it installs, each from its PUBLIC channel (no checkout needed):
#   - toolchain ...... uv (Astral) · Node 22 (official) · Rust (rustup)
#   - loctree (loct) . npm  loctree
#   - aicx ........... npm  @loctree/aicx
#   - prview ......... crates.io  (cargo install prview)
#   - screenscribe ... PyPI  (pipx install screenscribe)
#   - vibecrafted .... https://vibecrafted.io/install.sh
#   - vc-frame ....... best-effort source build (no public Linux binary yet)
#   - desktop ........ XFCE + xrdp (RDP on :3389) for a real GUI session
#
# Idempotent: safe to re-run. Flags:
#   --no-rdp            skip the XFCE + xrdp desktop
#   --no-vc-frame       skip the vc-frame source build
#   --minimal           foundations only (no desktop, no vc-frame)
#   --rdp-user NAME     RDP login user (default: vcdev; 'root' uses root)
#   -h | --help
#
# Env:
#   VC_RDP_PASSWORD     password for the RDP user (default: vibecrafted — CHANGE IT)
# ============================================================================
set -uo pipefail

# ── Flags ───────────────────────────────────────────────────────────────────
DO_RDP=1
DO_VC_FRAME=1
RDP_USER="vcdev"
for arg in "$@"; do
  case "$arg" in
    --no-rdp)        DO_RDP=0 ;;
    --no-vc-frame)   DO_VC_FRAME=0 ;;
    --minimal)       DO_RDP=0; DO_VC_FRAME=0 ;;
    --rdp-user)      shift; RDP_USER="${1:-vcdev}" ;;
    --rdp-user=*)    RDP_USER="${arg#*=}" ;;
    -h|--help)       sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  esac
done

# ── Pretty logging ──────────────────────────────────────────────────────────
if [ -t 1 ]; then B='\033[0;34m'; G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
else B=''; G=''; Y=''; R=''; N=''; fi
log()  { printf "${B}[%s]${N} %s\n" "$(date +%H:%M:%S)" "$*"; }
ok()   { printf "${G}  ✓${N} %s\n" "$*"; }
warn() { printf "${Y}  ⚠${N} %s\n" "$*" >&2; }
err()  { printf "${R}  ✗${N} %s\n" "$*" >&2; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || { err "not root and no sudo — cannot install system packages"; exit 1; }
  SUDO="sudo"
fi
export DEBIAN_FRONTEND=noninteractive

# Where user-scoped binaries land (uv, pipx, cargo, vibecrafted launchers).
export PATH="$HOME/.local/bin:/usr/local/cargo/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
STATUS_OK=() ; STATUS_MISS=()
record() { if command -v "$1" >/dev/null 2>&1; then STATUS_OK+=("$1"); else STATUS_MISS+=("$1"); fi; }

log "── Vibecrafted workstation bootstrap ──"
log "OS: $( . /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" ) · user: $(whoami) · rdp:${DO_RDP} vc-frame:${DO_VC_FRAME}"

# ── Stage 1: base packages ──────────────────────────────────────────────────
log "Stage 1/8: base system packages"
$SUDO apt-get update -qq || warn "apt-get update had warnings"
$SUDO apt-get install -y --no-install-recommends \
  ca-certificates curl wget git build-essential pkg-config libssl-dev \
  python3 python3-venv python3-dev python3-pip pipx \
  jq ripgrep unzip xz-utils tar zsh openssh-client locales procps iproute2 \
  >/dev/null 2>&1 && ok "base deps installed" || warn "some base deps failed"
python3 -m pipx ensurepath >/dev/null 2>&1 || true

# ── Stage 2: uv (Python env owner) ──────────────────────────────────────────
log "Stage 2/8: uv (Astral)"
if command -v uv >/dev/null 2>&1; then ok "uv present: $(uv --version)"
else
  if [ -n "$SUDO" ]; then curl -LsSf https://astral.sh/uv/install.sh | $SUDO env UV_INSTALL_DIR=/usr/local/bin sh >/dev/null 2>&1
  else curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1; fi
  command -v uv >/dev/null 2>&1 && ok "uv installed: $(uv --version)" || warn "uv install failed"
fi

# ── Stage 3: Node 22 (official static) + corepack ───────────────────────────
log "Stage 3/8: Node.js 22"
if command -v node >/dev/null 2>&1 && node --version | grep -qE '^v(2[2-9]|[3-9][0-9])'; then
  ok "node present: $(node --version)"
else
  NODE_VERSION="${NODE_VERSION:-22.19.0}"
  arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  case "$arch" in amd64|x86_64) na=x64 ;; arm64|aarch64) na=arm64 ;; *) na="" ;; esac
  if [ -n "$na" ] && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${na}.tar.xz" -o /tmp/node.tar.xz 2>/dev/null; then
    $SUDO tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 && rm -f /tmp/node.tar.xz
    ok "node installed: $(node --version 2>&1)"
  else warn "node download failed for arch=$arch"; fi
fi
command -v corepack >/dev/null 2>&1 && { $SUDO corepack enable >/dev/null 2>&1 || corepack enable >/dev/null 2>&1 || true; }
command -v npm >/dev/null 2>&1 && { npm config set fund false >/dev/null 2>&1; npm config set audit false >/dev/null 2>&1; }

# ── Stage 4: Rust (rustup) — needed by prview + vc-frame ────────────────────
log "Stage 4/8: Rust toolchain"
if command -v cargo >/dev/null 2>&1; then ok "cargo present: $(cargo --version)"
else
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --no-modify-path >/dev/null 2>&1
  [ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
  command -v cargo >/dev/null 2>&1 && ok "rust installed: $(cargo --version)" || warn "rust install failed"
fi

# ── Stage 5: public foundations (loctree, aicx, prview, screenscribe) ───────
log "Stage 5/8: foundations from public channels"
if command -v npm >/dev/null 2>&1; then
  $SUDO env PATH="$PATH" npm install -g loctree @loctree/aicx >/dev/null 2>&1 \
    || npm install -g loctree @loctree/aicx >/dev/null 2>&1 || warn "npm loctree/@loctree/aicx failed"
  command -v loct  >/dev/null 2>&1 && ok "loct:  $(loct --version 2>&1 | head -1)"  || warn "loct not on PATH"
  command -v aicx  >/dev/null 2>&1 && ok "aicx:  $(aicx --version 2>&1 | head -1)"  || warn "aicx not on PATH"
else warn "npm missing — cannot install loctree/aicx"; fi

# prview ships prebuilt on GitHub releases (vetcoders/prview-rs); crates.io is a fallback.
if command -v prview >/dev/null 2>&1; then ok "prview present: $(prview --version 2>&1 | head -1)"
else
  case "$(uname -m)" in
    x86_64)        pv_triple="x86_64-unknown-linux-gnu" ;;
    aarch64|arm64) pv_triple="aarch64-unknown-linux-gnu" ;;
    *)             pv_triple="" ;;
  esac
  pv_ok=0
  if [ -n "$pv_triple" ]; then
    pv_tag="$(curl -fsSL --max-time 20 https://api.github.com/repos/vetcoders/prview-rs/releases/latest 2>/dev/null \
      | grep -m1 '"tag_name"' | cut -d'"' -f4)"
    if [ -n "$pv_tag" ]; then
      pv_tmp="$(mktemp -d)"
      if curl -fsSL --max-time 120 \
          "https://github.com/vetcoders/prview-rs/releases/download/${pv_tag}/prview-${pv_triple}.tar.gz" 2>/dev/null \
          | tar -xz -C "$pv_tmp" 2>/dev/null; then
        pv_bin="$(find "$pv_tmp" -type f -name prview 2>/dev/null | head -1)"
        if [ -n "$pv_bin" ]; then
          chmod +x "$pv_bin"
          if [ -n "$SUDO" ]; then $SUDO install -m 0755 "$pv_bin" /usr/local/bin/prview
          else mkdir -p "$HOME/.local/bin"; install -m 0755 "$pv_bin" "$HOME/.local/bin/prview"; fi
          command -v prview >/dev/null 2>&1 && { ok "prview installed from GitHub releases (${pv_tag})"; pv_ok=1; }
        fi
      fi
      rm -rf "$pv_tmp"
    fi
  fi
  if [ "$pv_ok" -ne 1 ]; then
    if command -v cargo >/dev/null 2>&1; then
      cargo install prview >/dev/null 2>&1 && ok "prview installed (crates.io fallback)" || warn "prview install failed (GH releases + cargo)"
    else warn "prview: no release asset for $(uname -m) and cargo missing"; fi
  fi
fi

if command -v pipx >/dev/null 2>&1; then
  command -v screenscribe >/dev/null 2>&1 && ok "screenscribe present" \
    || { pipx install screenscribe >/dev/null 2>&1 && ok "screenscribe installed from PyPI" || warn "pipx install screenscribe failed"; }
elif command -v uv >/dev/null 2>&1; then
  uv tool install screenscribe >/dev/null 2>&1 && ok "screenscribe installed (uv tool)" || warn "screenscribe install failed"
fi

# ── Stage 6: vibecrafted runtime (public installer) ─────────────────────────
log "Stage 6/8: vibecrafted runtime"
if command -v vibecrafted >/dev/null 2>&1; then
  ok "vibecrafted present: $(vibecrafted --version 2>&1 | head -1)"
else
  # Stranger path: the official installer selects a published Linux Runtime Pack.
  timeout 900 bash -c 'curl -fsSL https://vibecrafted.io/install.sh | bash -s -- --yes' >/dev/null 2>&1 || true
  export PATH="$HOME/.local/bin:$PATH"
  if command -v vibecrafted >/dev/null 2>&1; then
    ok "vibecrafted installed via vibecrafted.io: $(vibecrafted --version 2>&1 | head -1)"
  elif command -v uv >/dev/null 2>&1; then
    # Dev lane: when no Runtime Pack is bound to the public source candidate, a
    # dev box still has the full toolchain — expose the CLI via a shallow clone
    # and `uv sync` (the editable workspace install).
    warn "no public Runtime Pack bound to the candidate — using the source dev lane (clone + uv sync)"
    VC_SRC="${VC_SRC:-$HOME/.local/share/vibecrafted/src}"
    if [ ! -d "$VC_SRC/.git" ]; then
      mkdir -p "$(dirname "$VC_SRC")"
      timeout 300 git clone --depth 1 https://github.com/vetcoders/vibecrafted.git "$VC_SRC" >/dev/null 2>&1 || warn "clone of vetcoders/vibecrafted failed"
    fi
    if [ -f "$VC_SRC/pyproject.toml" ]; then
      ( cd "$VC_SRC" && timeout 600 uv sync >/dev/null 2>&1 ) || warn "uv sync failed"
      if [ -x "$VC_SRC/.venv/bin/vibecrafted" ]; then
        mkdir -p "$HOME/.local/bin"
        for s in "$VC_SRC"/.venv/bin/vibecrafted "$VC_SRC"/.venv/bin/vibecrafted-* "$VC_SRC"/.venv/bin/vc-*; do
          [ -x "$s" ] && ln -sf "$s" "$HOME/.local/bin/$(basename "$s")"
        done
        command -v vibecrafted >/dev/null 2>&1 \
          && ok "vibecrafted (source dev lane): $(vibecrafted --version 2>&1 | head -1)" \
          || warn "vibecrafted CLI still not on PATH"
      else warn "vibecrafted console script not found after uv sync"; fi
    fi
  else warn "vibecrafted not installed and uv missing"; fi
fi

# ── Stage 7: vc-frame (no public Linux binary — best-effort source build) ───
if [ "$DO_VC_FRAME" -eq 1 ]; then
  log "Stage 7/8: vc-frame (operator cockpit)"
  if command -v vc-frame >/dev/null 2>&1; then
    ok "vc-frame present: $(vc-frame --version 2>&1 | head -1)"
  else
    # Honest gap: vc-frame ships embedded in the Vibecrafted desktop app and has
    # no public Linux binary or standalone build target (see install-foundations.sh:
    # "a donor installed only by the Vibecrafted owner. There is no separate
    # vc-frame release or installer fallback."). Report it instead of burning
    # minutes on a build that cannot produce it.
    warn "vc-frame unavailable: no public Linux channel (ships inside the macOS app)."
    warn "  Everything else is installed; vibecrafted falls back to your terminal until vc-frame is present."
    warn "  Fix is upstream: publish a Linux vc-frame binary (e.g. GitHub releases like prview-rs)."
  fi
else
  log "Stage 7/8: vc-frame skipped (--no-vc-frame)"
fi

# ── Stage 8: XFCE desktop + xrdp (RDP :3389) ────────────────────────────────
if [ "$DO_RDP" -eq 1 ]; then
  log "Stage 8/8: XFCE desktop + xrdp (RDP on :3389)"
  $SUDO apt-get install -y --no-install-recommends \
    xfce4 xfce4-terminal dbus dbus-x11 xorgxrdp xrdp x11-xserver-utils \
    >/dev/null 2>&1 && ok "xfce4 + xrdp installed" || warn "xfce4/xrdp install had problems"

  # RDP needs a login user with a password. root RDP is refused by many clients;
  # a dedicated user is the safe default.
  RDP_PASSWORD="${VC_RDP_PASSWORD:-vibecrafted}"
  if [ "$RDP_USER" != "root" ]; then
    if ! id "$RDP_USER" >/dev/null 2>&1; then
      $SUDO useradd -m -s /bin/bash "$RDP_USER" && ok "created RDP user '$RDP_USER'"
      $SUDO usermod -aG sudo "$RDP_USER" 2>/dev/null || true
    fi
    echo "${RDP_USER}:${RDP_PASSWORD}" | $SUDO chpasswd && ok "RDP password set for '$RDP_USER'"
    RDP_HOME="$(getent passwd "$RDP_USER" | cut -d: -f6)"
  else
    echo "root:${RDP_PASSWORD}" | $SUDO chpasswd 2>/dev/null || true
    RDP_HOME="/root"
  fi
  # XFCE as the RDP session for the login user.
  printf '%s\n' "startxfce4" | $SUDO tee "${RDP_HOME}/.xsession" >/dev/null
  $SUDO chown "$(basename "$RDP_HOME" 2>/dev/null || echo root)" "${RDP_HOME}/.xsession" 2>/dev/null || \
    { [ "$RDP_USER" != root ] && $SUDO chown "$RDP_USER":"$RDP_USER" "${RDP_HOME}/.xsession" 2>/dev/null; }
  # xrdp's postinst generates the keys; ensure runtime dirs then bring it up.
  $SUDO mkdir -p /var/run/xrdp /var/run/dbus /var/log
  if $SUDO ss -ltn 2>/dev/null | grep -q ':3389'; then
    ok "xrdp already listening on :3389"
  elif $SUDO systemctl enable --now xrdp >/dev/null 2>&1 && \
       $SUDO systemctl enable --now xrdp-sesman >/dev/null 2>&1; then
    ok "xrdp enabled via systemd"
  else
    # No systemd (containers): the system dbus + nodaemon sesman/xrdp is the
    # combination that actually binds 3389 (daemon-fork mode raced on boot).
    $SUDO sh -c 'dbus-daemon --system --fork >/dev/null 2>&1 || true'
    $SUDO sh -c 'setsid /usr/sbin/xrdp-sesman --nodaemon >/var/log/xrdp-sesman.log 2>&1 &'
    sleep 1
    $SUDO sh -c 'setsid /usr/sbin/xrdp --nodaemon >/var/log/xrdp.log 2>&1 &'
    ok "xrdp started (no systemd; nodaemon)"
  fi
  bound=0
  for _ in 1 2 3 4 5 6; do
    if $SUDO ss -ltn 2>/dev/null | grep -q ':3389'; then bound=1; break; fi
    sleep 1
  done
  [ "$bound" -eq 1 ] && ok "xrdp is listening on :3389" \
    || warn "xrdp not listening on :3389 — check /var/log/xrdp*.log"
else
  log "Stage 8/8: desktop/RDP skipped (--no-rdp / --minimal)"
fi

# ── Summary ─────────────────────────────────────────────────────────────────
for b in vibecrafted vc-frame aicx loct prview screenscribe; do record "$b"; done
echo ""
log "── Summary ──"
[ "${#STATUS_OK[@]}"  -gt 0 ] && ok   "installed: ${STATUS_OK[*]}"
[ "${#STATUS_MISS[@]}" -gt 0 ] && warn "missing:   ${STATUS_MISS[*]}"
if [ "$DO_RDP" -eq 1 ]; then
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo ""
  echo "  RDP:   connect an RDP client to  ${ip:-<this-host>}:3389"
  echo "         user: ${RDP_USER}   password: (VC_RDP_PASSWORD, default 'vibecrafted' — change it)"
fi
echo ""
echo "  Next:  vibecrafted doctor   ·   vibecrafted init claude"
