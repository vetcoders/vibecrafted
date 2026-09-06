#!/usr/bin/env bash
set -euo pipefail
# ---------------------------------------------------------------------------
# install-foundations.sh — portable installer for 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. foundation layer
#
# Handles:
#   loctree / loctree-mcp  — required Loctree product binaries via Loctree installer
#   aicx / aicx-mcp       — required AICX product binaries via Loctree installer
#   vc-frame               — required donor BINARY installed by this owner from sibling source
#   prview                 — cargo install OR binary from GH releases
#
# Usage:
#   bash scripts/install-foundations.sh                   # install/validate foundations
#   bash scripts/install-foundations.sh --all             # + framework-owned extras
#   bash scripts/install-foundations.sh loctree           # Loctree product binaries
#   bash scripts/install-foundations.sh aicx              # AICX product binaries
#   bash scripts/install-foundations.sh vc-frame          # hard-install / validate vc-frame
#   bash scripts/install-foundations.sh --check           # dry-run: show what would install
#   bash scripts/install-foundations.sh --prefix /usr/local  # custom install prefix
#
# Product spine (audit SF-2): vc-frame is no longer validate-only. The gate is
# COCKPIT READY, not PATH-only:
#   1. binary exists AND runs
#   2. product identity (vc-frame, not stock zellij)
#   3. live config projection (frontier or view) with default_layout / Start here
#   4. operator scripts (at least vc-composer.sh) install-managed or present
#   5. launch door on PATH (vc-start and/or vibecrafted)
# Missing any of the hard checks fails foundations so install cannot "succeed"
# without a rideable operator surface.
# ---------------------------------------------------------------------------

PRVIEW_CRATE="prview"
PRVIEW_REPO="vetcoders/prview"

LOCTREE_INSTALL_URL="${LOCTREE_INSTALL_URL:-https://loct.io/install.sh}"

# Agent CLIs — npm packages when the vendor publishes an official package.
AGENT_PACKAGES=(
  "claude:@anthropic-ai/claude-code"
  "codex:@openai/codex"
  "junie:@jetbrains/junie"
  "grok:@xai-official/grok"
)

AGENT_MANUAL_INSTALLS=(
  "agy|Install Google Antigravity CLI from its vendor distribution, then run: agy install"
  "cursor-agent|Install the Cursor CLI: curl https://cursor.com/install -fsS | bash"
)

# Script/source resolution (used by the bundled-toolchain attempt).
# Respects VIBECRAFTED_SOURCE when set; otherwise resolves the parent of
# scripts/install-foundations.sh.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${VIBECRAFTED_SOURCE:-$(cd "$SCRIPT_DIR/.." && pwd)}"

# Runtime roots: one shell definition, shared with install-runtime.sh and
# pinned to install.sh by tests/tui/test_runtime_roots_parity.py.
# shellcheck source=scripts/lib/runtime-roots.sh
source "$SCRIPT_DIR/lib/runtime-roots.sh"

VIBECRAFTED_HOME="$(default_vibecrafted_home)"
VIBECRAFTED_RUNTIME_HOME="$(default_vibecrafted_runtime_home)"
PREFIX="${VIBECRAFTED_BIN:-$VIBECRAFTED_RUNTIME_HOME/bin}"
LAUNCHER_PREFIX="${VIBECRAFTED_LAUNCHER_BIN:-$HOME/.local/bin}"
CHECK_ONLY=0
INSTALL_ALL=0
AGENTS_REQUIRED=0
# Product foundations (loctree/aicx/vc-frame) are externally managed: this
# script deliberately refuses to guess crates/npm/checkout paths and points at
# the canonical installer instead. Their absence is therefore an ADVISORY, not
# an install failure — consistent with `make install-vendored-binaries`, which
# already keeps the "external fallback" path non-fatal when vendored binaries
# are absent. Set REQUIRE_FOUNDATIONS=1 (e.g. release validation) to make a
# missing product foundation fail the run instead.
REQUIRE_FOUNDATIONS="${REQUIRE_FOUNDATIONS:-0}"
TARGETS=()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

die()  { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }

detect_os() {
  case "$(uname -s)" in
    Linux*)   echo "linux" ;;
    Darwin*)  echo "macos" ;;
    MINGW*|MSYS*|CYGWIN*) echo "windows" ;;
    *)        die "Unsupported OS: $(uname -s)" ;;
  esac
}

detect_arch() {
  case "$(uname -m)" in
    x86_64|amd64)   echo "x86_64" ;;
    aarch64|arm64)   echo "aarch64" ;;
    armv7l)          echo "armv7" ;;
    *)               die "Unsupported architecture: $(uname -m)" ;;
  esac
}

has_cmd() { command -v "$1" >/dev/null 2>&1; }

is_interactive() { [[ -t 0 && -t 1 ]]; }

# Verify a binary actually runs (catches dyld / missing shared lib issues).
binary_runs() {
  local bin="$1"
  has_cmd "$bin" || return 1
  "$bin" --version >/dev/null 2>&1 || "$bin" --help >/dev/null 2>&1
}

# Sole live vc-frame config directory owned by the product.
_vcframe_config_roots() {
  local xdg="$HOME/.config"
  printf '%s\n' "$xdg/vibecrafted/vc-frame"
}

# COCKPIT READY — hard product spine after binary is on PATH.
# Prints findings; returns 0 only when the operator can ride.
verify_vcframe_cockpit() {
  local bin cfg_root cfg layout composer door fails=0
  local ver=""

  if ! has_cmd vc-frame; then
    warn "cockpit: vc-frame not on PATH"
    return 1
  fi
  bin="$(command -v vc-frame)"

  if ! binary_runs vc-frame; then
    warn "cockpit: vc-frame at $bin does not run (--version/--help failed)"
    return 1
  fi

  ver="$(vc-frame --version 2>/dev/null | head -1 || true)"
  # Product identity: refuse stock zellij masquerading as the frame.
  if printf '%s' "$ver" | grep -qiE '^zellij([[:space:]]|$)' \
    && ! printf '%s' "$ver" | grep -qi 'vc-frame'; then
    warn "cockpit: binary looks like stock zellij ($ver), not product vc-frame"
    fails=1
  fi
  if ! printf '%s' "$ver" | grep -qiE 'vc-frame|zellij'; then
    # Unknown version string — still require build-info or setup to respond.
    if ! vc-frame --build-info >/dev/null 2>&1 && ! vc-frame setup --check >/dev/null 2>&1; then
      warn "cockpit: vc-frame produces no version/build-info/setup signal"
      fails=1
    fi
  fi
  ok "cockpit: binary runs ($ver) @ $bin"

  cfg_root=""
  while IFS= read -r candidate; do
    [[ -n "$candidate" ]] || continue
    if [[ -d "$candidate" && ! -L "$candidate" && -f "$candidate/config.kdl" && ! -L "$candidate/config.kdl" ]]; then
      cfg_root="$candidate"
      break
    fi
  done < <(_vcframe_config_roots)

  if [[ -z "$cfg_root" ]]; then
    warn "cockpit: no live config.kdl under ~/.config/vibecrafted/vc-frame"
    warn "  fix: run make install from the Vibecrafted checkout with your verified Runtime Pack"
    fails=1
  else
    cfg="$cfg_root/config.kdl"
    ok "cockpit: config @ $cfg"
    if ! grep -qE 'default_layout[[:space:]]+"?(vibecrafted|operator)' "$cfg" 2>/dev/null \
      && ! grep -q 'default_layout' "$cfg" 2>/dev/null; then
      warn "cockpit: config has no default_layout (Start here path unclear)"
      fails=1
    fi
    # Super/Cmd product contract (install projection must not leave hollow copy).
    if grep -q 'support_kitty_keyboard_protocol[[:space:]]*false' "$cfg" 2>/dev/null; then
      warn "cockpit: support_kitty_keyboard_protocol is false — Super/Cmd chords die"
      fails=1
    fi
    if ! grep -q 'bind "Super' "$cfg" 2>/dev/null; then
      warn "cockpit: no Super/* keybinds in live config (Cmd switcher missing)"
      fails=1
    fi
  fi

  # Start here layout: operator.kdl or built-in default_layout vibecrafted.
  layout=""
  if [[ -n "$cfg_root" ]]; then
    for candidate in \
      "$cfg_root/layouts/operator.kdl" \
      "$cfg_root/layouts/vibecrafted.kdl"
    do
      if [[ -f "$candidate" ]] && grep -q 'Start here' "$candidate" 2>/dev/null; then
        layout="$candidate"
        break
      fi
    done
  fi
  if [[ -n "$layout" ]]; then
    ok "cockpit: Start here layout @ $layout"
  else
    # Built-in default_layout vibecrafted may carry Start here without a file copy.
    if [[ -n "$cfg_root" ]] && grep -qE 'default_layout[[:space:]]+"vibecrafted"' "$cfg_root/config.kdl" 2>/dev/null; then
      ok "cockpit: Start here via built-in default_layout vibecrafted"
    else
      warn "cockpit: no Start here layout file and default_layout is not vibecrafted"
      fails=1
    fi
  fi

  composer=""
  if [[ -n "$cfg_root" ]]; then
    candidate="$cfg_root/vc-composer.sh"
    if [[ -x "$candidate" && ! -L "$candidate" ]]; then
      # Reject tiny STALE stubs (legacy 606B antique).
      if [[ -f "$candidate" ]] && [[ "$(wc -c <"$candidate" | tr -d ' ')" -lt 1500 ]]; then
        warn "cockpit: $candidate looks like a STALE stub (<1.5KB) — reinstall the verified Runtime Pack"
        fails=1
      else
        composer="$candidate"
      fi
    fi
  fi
  if [[ -n "$composer" ]]; then
    ok "cockpit: operator composer @ $composer"
  else
    warn "cockpit: vc-composer.sh missing/unusable on live config path"
    fails=1
  fi

  door=""
  if has_cmd vc-start; then
    door="vc-start"
  elif has_cmd vibecrafted; then
    door="vibecrafted start"
  fi
  if [[ -n "$door" ]]; then
    ok "cockpit: launch door on PATH ($door)"
  else
    warn "cockpit: neither vc-start nor vibecrafted on PATH — no launch door"
    fails=1
  fi

  if (( fails )); then
    warn "cockpit: NOT READY — installer must not claim success without the above"
    return 1
  fi
  ok "cockpit: READY (binary + identity + config + Start here + scripts + door)"
  return 0
}

# ---------------------------------------------------------------------------
# Bundled-toolchain drop-in
# ---------------------------------------------------------------------------
# Resolves the per-arch directory where release tarballs ship notarized
# binaries. Order of precedence:
#   1. $VIBECRAFTED_BUNDLED_BIN (explicit override — absolute path)
#   2. $SOURCE_DIR/tools/bin/<os>-<arch>
#   3. $SOURCE_DIR/tools/bin  (flat fallback for dev / single-arch drops)
bundled_bin_root() {
  local os arch
  if [[ -n "${VIBECRAFTED_BUNDLED_BIN:-}" ]]; then
    printf '%s\n' "$VIBECRAFTED_BUNDLED_BIN"
    return
  fi
  os="$(detect_os)"
  arch="$(detect_arch)"
  if [[ -d "$SOURCE_DIR/tools/bin/${os}-${arch}" ]]; then
    printf '%s\n' "$SOURCE_DIR/tools/bin/${os}-${arch}"
    return
  fi
  printf '%s\n' "$SOURCE_DIR/tools/bin"
}

_realpath_quiet() {
  local path="$1"
  if [[ -d "$path" ]]; then
    (cd "$path" && pwd -P)
    return
  fi
  return 1
}

# Attempt 0 for every install_*: copy a drop-in binary from the bundled
# tarball directory before reaching out to GitHub / cargo / npm.
# Returns 0 only when the binary copied, chmod+x'd, and actually runs.
install_from_bundled() {
  local name="$1"
  local root
  root="$(bundled_bin_root)"
  local src="$root/$name"

  [[ -f "$src" ]] || return 1

  if (( CHECK_ONLY )); then
    info "Would install $name from bundled tarball ($src)"
    return 0
  fi

  ensure_prefix
  cp "$src" "$PREFIX/$name" || return 1
  chmod +x "$PREFIX/$name"
  if ! binary_runs "$name"; then
    warn "Bundled $name failed to run — falling back to remote sources."
    rm -f "$PREFIX/$name"
    return 1
  fi
  ok "Installed $name from bundled tarball (notarized): $PREFIX/$name"
  return 0
}

ensure_node() {
  has_cmd node && has_cmd npm && return 0

  # Try brew first (if available)
  if has_cmd brew; then
    info "Installing Node.js via Homebrew..."
    brew install node 2>&1 | tail -3 && has_cmd node && has_cmd npm && return 0
  fi

  # Download official Node.js binary — no sudo needed
  local os arch node_os node_arch node_version="v22.16.0"
  os="$(detect_os)"
  arch="$(detect_arch)"

  case "$os" in
    macos)  node_os="darwin" ;;
    linux)  node_os="linux" ;;
    *)      warn "No Node.js binary for $os"; return 1 ;;
  esac
  case "$arch" in
    x86_64)  node_arch="x64" ;;
    aarch64) node_arch="arm64" ;;
    *)       warn "No Node.js binary for $arch"; return 1 ;;
  esac

  local node_pkg="node-${node_version}-${node_os}-${node_arch}"
  local node_url="https://nodejs.org/dist/${node_version}/${node_pkg}.tar.gz"
  local node_dir="${VIBECRAFTED_HOME}/tools/node"

  if is_interactive; then
    printf '\n'
    info "Node.js is required for npm-installed agent CLIs."
    info "Installing to ${node_dir} (no sudo needed)."
    printf '  Press Enter to proceed, or Ctrl-C to skip: '
    read -r _
  else
    info "Installing Node.js ${node_version} to ${node_dir}..."
  fi

  has_cmd curl || { warn "curl required to download Node.js"; return 1; }

  local tmpdir
  tmpdir="$(mktemp -d)"
  if curl -fsSL -o "$tmpdir/node.tar.gz" "$node_url"; then
    mkdir -p "$node_dir"
    tar -xzf "$tmpdir/node.tar.gz" -C "$node_dir" --strip-components=1
    rm -rf "$tmpdir"
    export PATH="${node_dir}/bin:${PATH}"

    if has_cmd node && has_cmd npm; then
      ok "Node.js $(node --version) installed to ${node_dir}"
      return 0
    fi
  fi

  rm -rf "$tmpdir"
  warn "Could not install Node.js."
  return 1
}

install_from_npm() {
  local package="$1" binary="${2:-$1}"

  if binary_runs "$binary"; then
    ok "$binary already installed: $(command -v "$binary")"
    return 0
  fi

  ensure_node || {
    warn "npm not available — cannot install $package."
    return 1
  }

  info "Installing $package via npm..."
  npm install -g "$package" 2>&1 | tail -3 || {
    warn "npm install $package failed."
    return 1
  }

  binary_runs "$binary"
}

ensure_prefix() {
  mkdir -p "$PREFIX"
  # Add to PATH for the rest of this script
  case ":$PATH:" in
    *":$PREFIX:"*) ;;
    *) export PATH="$PREFIX:$PATH" ;;
  esac
}

install_loctree() {
  loctree_suite_ready() {
    local missing=() bin
    for bin in loct loctree loctree-mcp; do
      binary_runs "$bin" || missing+=("$bin")
    done
    if (( ${#missing[@]} == 0 )); then
      return 0
    fi
    warn "Loctree incomplete; missing or broken: ${missing[*]}"
    return 1
  }

  if loctree_suite_ready; then
    ok "loctree suite already installed: loct=$(command -v loct), loctree-mcp=$(command -v loctree-mcp)"
    return 0
  fi

  if (( CHECK_ONLY )); then
    info "Would install Loctree foundations from canonical installer:"
    info "  curl -fsSL $LOCTREE_INSTALL_URL | sh"
    return 0
  fi

  warn "Loctree foundations are required, but Vibecrafted will not guess crates, npm packages, or local checkout paths."
  warn "Use the canonical installer, then rerun this check:"
  warn "  curl -fsSL $LOCTREE_INSTALL_URL | sh"
  return 1
}

# ---------------------------------------------------------------------------
# Generic cargo installer
# ---------------------------------------------------------------------------

install_from_cargo() {
  local crate="$1" binary="${2:-$1}"

  if has_cmd "$binary"; then
    ok "$binary already installed: $(command -v "$binary")"
    return 0
  fi

  if (( CHECK_ONLY )); then
    if has_cmd cargo; then
      info "Would run: cargo install $crate"
    else
      warn "$binary not found and cargo not available"
      info "Install Rust (https://rustup.rs) then: cargo install $crate"
    fi
    return 0
  fi

  if ! has_cmd cargo; then
    warn "cargo not found. Cannot install $crate from crates.io."
    warn "Options:"
    warn "  1. Install Rust: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    warn "  2. Download binary from GitHub releases"
    return 1
  fi

  ensure_prefix

  info "Installing $crate via cargo..."
  # Install to a temp dir, then copy binaries to PREFIX
  local cargo_root
  cargo_root="$(mktemp -d)"

  if cargo install "$crate" --root "$cargo_root" 2>&1; then
    local installed=0
    for bin in "$cargo_root/bin/"*; do
      [ -f "$bin" ] || continue
      local name
      name="$(basename "$bin")"
      cp "$bin" "$PREFIX/$name"
      chmod +x "$PREFIX/$name"
      ok "Installed $name -> $PREFIX/$name"
      installed=1
    done
    if (( !installed )); then
      rm -rf "$cargo_root"
      warn "cargo install $crate succeeded but no binaries found"
      return 1
    fi
  else
    rm -rf "$cargo_root"
    warn "cargo install $crate failed"
    return 1
  fi

  rm -rf "$cargo_root"
}

# ---------------------------------------------------------------------------
# aicx installer
# ---------------------------------------------------------------------------

install_aicx() {
  if has_cmd aicx-mcp; then
    ok "aicx-mcp already installed: $(command -v aicx-mcp)"
    return 0
  fi

  if (( CHECK_ONLY )); then
    info "Would install AICX foundations from canonical Loctree installer:"
    info "  curl -fsSL $LOCTREE_INSTALL_URL | sh"
    return 0
  fi

  warn "AICX foundations are required, but Vibecrafted will not guess crates, npm packages, or local checkout paths."
  warn "Use the canonical installer, then rerun this check:"
  warn "  curl -fsSL $LOCTREE_INSTALL_URL | sh"
  return 1
}

# ---------------------------------------------------------------------------
# vc-frame — product frame binary (hard install on product path)
#
# The Runtime Pack installer publishes vc-frame with its matching config.
# This foundations step inspects that installation.
# ---------------------------------------------------------------------------

# vc-frame and its product config are installed together by runtime-install.
# Foundations consumes that result; it cannot build a second donor installation
# or patch the selected immutable generation.
install_vcframe() {
  if ! verify_vcframe_cockpit; then
    warn "Repair: run make install from the Vibecrafted checkout with your verified Runtime Pack."
    return 1
  fi
}


# ---------------------------------------------------------------------------
# Agent CLI installer
# ---------------------------------------------------------------------------

install_agents() {
  local entry binary package hint installed=0 total=0

  for entry in "${AGENT_PACKAGES[@]}"; do
    binary="${entry%%:*}"
    package="${entry#*:}"
    total=$((total + 1))

    if has_cmd "$binary"; then
      ok "$binary already installed: $(command -v "$binary")"
      installed=$((installed + 1))
      continue
    fi

    if (( CHECK_ONLY )); then
      info "Would install $binary via: npm install -g $package"
      continue
    fi

    install_from_npm "$package" "$binary" && installed=$((installed + 1))
  done

  for entry in "${AGENT_MANUAL_INSTALLS[@]}"; do
    binary="${entry%%|*}"
    hint="${entry#*|}"
    total=$((total + 1))

    if has_cmd "$binary"; then
      ok "$binary already installed: $(command -v "$binary")"
      installed=$((installed + 1))
      continue
    fi

    if (( CHECK_ONLY )); then
      info "Would verify $binary; install manually: $hint"
    else
      warn "$binary not installed. $hint"
    fi
  done

  if (( installed == total )); then
    ok "All agent CLIs installed ($installed/$total)"
    return 0
  else
    warn "Agent CLIs: $installed/$total installed"
    if (( CHECK_ONLY )); then
      info "Check-only mode: missing agent CLIs are reported but do not fail the run"
      return 0
    fi
    return 1
  fi
}

# ---------------------------------------------------------------------------
# prview installer
# ---------------------------------------------------------------------------

install_prview() {
  if has_cmd prview; then
    ok "prview already installed: $(command -v prview)"
    return 0
  fi

  # --- Attempt 0: bundled tarball (notarized drop-in) ---
  install_from_bundled "prview" && return 0

  info "Installing prview from crate metadata; release repo is $PRVIEW_REPO"
  install_from_cargo "$PRVIEW_CRATE" "prview"
}

# ---------------------------------------------------------------------------
# Microsandbox installer hint
# ---------------------------------------------------------------------------

install_sandbox() {
  if binary_runs msbserver; then
    ok "msbserver already installed: $(command -v msbserver)"
    return 0
  fi

  local os arch source_root microsandbox_dir answer
  os="$(detect_os)"
  arch="$(detect_arch)"
  source_root="$(_realpath_quiet "$SOURCE_DIR/../experimental/microsandbox" 2>/dev/null || true)"
  microsandbox_dir="${MICROSANDBOX_SOURCE:-$source_root}"

  case "$os" in
    linux|macos) ;;
    *)
      warn "microsandbox is not supported on $os"
      return 1
      ;;
  esac

  if [[ "$os" == "linux" && ! -e /dev/kvm ]]; then
    warn "libkrun requires KVM on Linux; /dev/kvm is missing."
    return 1
  fi

  if (( CHECK_ONLY )); then
    info "Would install microsandbox runtime for ${os}/${arch}"
    info "  Source: ${microsandbox_dir:-<set MICROSANDBOX_SOURCE>}"
    return 0
  fi

  if is_interactive; then
    printf '\n'
    info "Detected libkrun-capable platform candidate (${os}/${arch})."
    printf '  Install microsandbox runtime from %s? (y/N): ' "${microsandbox_dir:-MICROSANDBOX_SOURCE}"
    read -r answer
    [[ "$answer" == "y" || "$answer" == "Y" ]] || {
      warn "Skipping optional microsandbox runtime."
      return 0
    }
  else
    warn "Skipping optional microsandbox runtime in non-interactive mode."
    warn "Run: cd ${microsandbox_dir:-/path/to/microsandbox} && make build"
    return 0
  fi

  [[ -n "$microsandbox_dir" && -f "$microsandbox_dir/Makefile" ]] || {
    warn "microsandbox source not found. Set MICROSANDBOX_SOURCE and rerun."
    return 1
  }
  make -C "$microsandbox_dir" build
}

# ---------------------------------------------------------------------------
# iTerm2 / locterm AutoLaunch plugin (macOS, opt-in)
# ---------------------------------------------------------------------------

iterm2_app_present() {
  [[ "$(detect_os)" == "macos" ]] || return 1
  local candidate
  for candidate in \
    "/Applications/iTerm.app" \
    "/Applications/locterm.app" \
    "$HOME/Applications/iTerm.app" \
    "$HOME/Applications/locterm.app"; do
    [[ -d "$candidate" ]] && return 0
  done
  return 1
}

install_iterm2_integration() {
  if ! iterm2_app_present; then
    info "iTerm2 / locterm not detected — skipping vibecrafted plugin install"
    return 0
  fi

  if (( CHECK_ONLY )); then
    info "Would install vibecrafted iTerm2 / locterm AutoLaunch plugin"
    return 0
  fi

  local answer
  if is_interactive; then
    printf '\n'
    info "Detected iTerm2 (or locterm) — install vibecrafted plugin? (y/N): "
    read -r answer
    case "${answer:-}" in
      [yY]|[yY][eE][sS]) ;;
      *)
        warn "Skipping vibecrafted iTerm2 plugin (run later: python -m vibecrafted_iterm2.install_autolaunch)"
        return 0
        ;;
    esac
  else
    warn "Skipping iTerm2 plugin install in non-interactive mode."
    warn "Run later: python -m vibecrafted_iterm2.install_autolaunch"
    return 0
  fi

  # The iTerm2 plugin is OPT-IN and OPTIONAL — its failure must NEVER abort the
  # whole install (it used to: a bare system python3 lacks vibecrafted_iterm2 and
  # returning 1 killed `make install-all` with Error 1). Prefer the installed
  # uv-tool entry point, then the uv-tool venv python, then PATH python; warn on
  # failure but always return 0.
  if command -v vibecrafted-iterm2-autolaunch >/dev/null 2>&1; then
    vibecrafted-iterm2-autolaunch --force \
      || warn "vibecrafted iTerm2 plugin install failed (non-fatal); rerun: vibecrafted-iterm2-autolaunch --force"
    return 0
  fi

  local python_bin=""
  local candidate
  for candidate in \
    "${VIBECRAFTED_PYTHON:-}" \
    "${XDG_DATA_HOME:-$HOME/.local/share}/uv/tools/vibecrafted-iterm2/bin/python3" \
    "$(command -v python3 || true)" \
    "$(command -v python || true)"; do
    [[ -n "$candidate" && -x "$candidate" ]] && { python_bin="$candidate"; break; }
  done
  if [[ -z "$python_bin" ]]; then
    warn "no python on PATH — skipping vibecrafted iTerm2 plugin (non-fatal)"
    return 0
  fi

  "$python_bin" -m vibecrafted_iterm2.install_autolaunch --force \
    || warn "vibecrafted iTerm2 plugin install failed (non-fatal); rerun: vibecrafted-iterm2-autolaunch --force"
  return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

usage() {
  cat <<EOF
Usage: install-foundations.sh [options] [targets...]

Targets:
  loctree         Validate loctree + loctree-mcp product binaries
  aicx            Validate aicx / aicx-mcp product binaries
  vc-frame        Validate vc-frame product binary
  prview          Install prview (cargo)
  sandbox         Optional microsandbox/libkrun runtime
  iterm2-plugin   vibecrafted iTerm2 / locterm AutoLaunch plugin (macOS, opt-in)
  (no target)     Validate required foundations; install only framework-owned tools

Options:
  --all        Install all foundations (including optional)
  --check      Dry-run: show what would be installed
  --prefix DIR Install framework-owned runtime binaries to DIR (default: $PREFIX)
  --help       Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)       INSTALL_ALL=1 ;;
    --check)     CHECK_ONLY=1 ;;
    --prefix)    shift; PREFIX="${1:?Missing prefix value}" ;;
    --help|-h)   usage; exit 0 ;;
    loctree)     TARGETS+=("loctree") ;;
    aicx)        TARGETS+=("aicx") ;;
    vc-frame)    TARGETS+=("vc-frame") ;;
    agents)      TARGETS+=("agents"); AGENTS_REQUIRED=1 ;;
    prview)      TARGETS+=("prview") ;;
    sandbox)     TARGETS+=("sandbox") ;;
    iterm2-plugin) TARGETS+=("iterm2-plugin") ;;
    *)           die "Unknown argument: $1" ;;
  esac
  shift
done

enforce_runtime_root_contract || exit 1

# Default: install required foundations
if (( ${#TARGETS[@]} == 0 )); then
  TARGETS=("loctree" "aicx" "vc-frame" "agents")
  if (( INSTALL_ALL )); then
    TARGETS+=("prview" "sandbox")
  fi
  # macOS users always get the optional iTerm2 plugin prompt; the
  # install function itself is a no-op on Linux/Windows and in
  # non-interactive shells.
  if [[ "$(detect_os)" == "macos" ]]; then
    TARGETS+=("iterm2-plugin")
  fi
fi

printf '\n\033[1m  Foundation Installer\033[0m\n'
printf '  ─────────────────────\n'
printf '  Runtime bin:  %s\n' "$PREFIX"
printf '  Launcher bin: %s\n\n' "$LAUNCHER_PREFIX"

# Product foundations are externally managed; a missing binary is advisory
# unless the caller opted into strict validation via REQUIRE_FOUNDATIONS=1.
foundation_optional_fail() {
  local name="$1"
  if [[ "$REQUIRE_FOUNDATIONS" == "1" ]]; then
    exit_code=1
  else
    warn "$name unavailable — deferring to external/canonical install (non-fatal). Set REQUIRE_FOUNDATIONS=1 to enforce."
  fi
}

exit_code=0
for target in "${TARGETS[@]}"; do
  case "$target" in
    loctree)  install_loctree  || foundation_optional_fail loctree ;;
    aicx)     install_aicx     || foundation_optional_fail aicx ;;
    # vc-frame is a donor installed only by the Vibecrafted owner. There is no
    # separate vc-frame release or installer fallback.
    vc-frame)
      install_vcframe  || foundation_optional_fail vc-frame
      ;;
    agents)
      if ! install_agents; then
        if (( AGENTS_REQUIRED )); then
          exit_code=1
        else
          warn "agent CLIs incomplete — optional, install later: vibecrafted doctor"
        fi
      fi
      ;;
    prview)  install_prview  || exit_code=1 ;;
    sandbox) install_sandbox || exit_code=1 ;;
    iterm2-plugin) install_iterm2_integration || exit_code=1 ;;
  esac
  echo
done

# Python ``vc-*`` launchers are owned exclusively by ``uv tool install`` in
# Makefile::install-tools-held. Copying the checkout's ``bin/vc-*`` files here
# used to follow ~/.local/bin symlinks and overwrite the uv console scripts in
# place, replacing their venv shebang with ``#!/usr/bin/env python3``.
if (( exit_code == 0 )) && (( !CHECK_ONLY )); then
  printf '\033[1mFoundation install complete.\033[0m\n'
  # Remind about PATH
  case ":$PATH:" in
    *":$LAUNCHER_PREFIX:"*) ;;
    *)
      printf '\n\033[33mAdd to your shell profile:\033[0m\n'
      # shellcheck disable=SC2016 # $PATH is literal output for the user
      printf '  export PATH="%s:$PATH"\n\n' "$LAUNCHER_PREFIX"
      ;;
  esac
fi

exit $exit_code
