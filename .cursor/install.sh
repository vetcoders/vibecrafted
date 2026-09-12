#!/usr/bin/env bash
# Idempotent repository bootstrap for the Vibecrafted Cloud Agent environment.
# Runs after Cursor checks out the repo. Safe to run repeatedly and against a
# cached/partially-prepared state (env builds run this once to seed the snapshot).
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || echo /workspace)"
cd "$repo_root"

# Toolchains from the Docker image; export defensively in case PATH is trimmed.
export PATH="/usr/local/cargo/bin:/usr/local/bin:${HOME}/.local/bin:${PATH}"

# Debug-build linker fix for x86_64 Linux (rustc 1.97 rust-lld DWARF32 overflow).
# Mirrors the image ENV so this bootstrap also links cleanly on other bases.
export CARGO_PROFILE_DEV_DEBUG="${CARGO_PROFILE_DEV_DEBUG:-0}"

echo "[install] tool versions:"
uv --version
python3 --version
node --version
cargo --version

# 1) Python: sync the uv workspace (creates .venv with vibecrafted-{core,mcp,acp}).
echo "[install] uv sync (Python workspace)…"
uv sync

# 2) Rust: materialize the pinned toolchain (no-op once the image has it).
echo "[install] rust toolchain:"
rustup show active-toolchain || true

# 3) Pre-build the read-only control-plane server so the vc-server terminal
#    starts immediately on boot. CARGO_PROFILE_DEV_DEBUG=0 (image ENV) keeps the
#    x86_64 Linux debug link within the 32-bit relocation range.
echo "[install] building vc-server (control plane)…"
make server-build

echo "[install] Vibecrafted dev environment ready."
