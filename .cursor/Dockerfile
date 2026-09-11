# Cloud Agent base image for the Vibecrafted monorepo — Debian 13 (trixie).
#
# Chosen to match vibecrafted-vm/ (debian:trixie), so the Cloud Agent
# environment and the operator VM carrier share one OS baseline instead of
# introducing a parallel toolchain. This image only supplies stable system
# packages and language toolchains; the repository is checked out separately by
# Cursor and its dependencies are installed by .cursor/install.sh.
FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# System packages: build/link toolchain (build-essential provides cc/ld used by
# rustc), git+curl for source and installers, and the CLI utilities the
# Makefile and skills shell out to (jq, ripgrep, make, tar/xz/unzip).
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    git \
    jq \
    libssl-dev \
    make \
    openssh-client \
    pkg-config \
    python3 \
    python3-dev \
    python3-venv \
    ripgrep \
    sudo \
    tar \
    unzip \
    xz-utils \
    zsh \
  && rm -rf /var/lib/apt/lists/*

# Non-root agent user. Cursor runs commands in the environment as this user, so
# it must exist in the image (see .cursor/environment.json "user").
RUN useradd --create-home --shell /bin/bash --uid 1000 ubuntu \
  && echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ubuntu \
  && chmod 0440 /etc/sudoers.d/ubuntu

# uv — canonical Python environment/owner for the uv workspace (pyproject.toml).
RUN curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh

# Node.js 22 (official static build) + corepack. Node powers the agent CLIs and
# corepack provides the repo-pinned pnpm@10 (package.json packageManager) used by
# the website build. Pinned for reproducibility.
ENV NODE_VERSION=22.14.0
RUN arch="$(dpkg --print-architecture)" \
  && case "$arch" in \
       amd64) node_arch=x64 ;; \
       arm64) node_arch=arm64 ;; \
       *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
     esac \
  && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" \
     -o /tmp/node.tar.xz \
  && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
  && rm /tmp/node.tar.xz \
  && corepack enable

# Rust toolchain, pinned to vibecrafted-server/rust-toolchain.toml (1.97.0) with
# the wasm target + clippy/rustfmt so `make server-check` and hydration builds
# work. Installed to a shared, world-writable CARGO_HOME/RUSTUP_HOME so the
# non-root agent user can build and populate the registry cache.
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --no-modify-path --profile minimal \
      --default-toolchain 1.97.0 \
      --component rustfmt --component clippy \
      --target wasm32-unknown-unknown \
  && chmod -R a+rwX "$RUSTUP_HOME" "$CARGO_HOME"

# Debian's /etc/profile resets PATH for login shells, which would drop
# /usr/local/cargo/bin (cargo/rustc) even though the image ENV sets it. A
# profile.d snippet re-exports the Rust homes and PATH so cargo is on PATH in
# login, non-login, and interactive shells alike.
RUN printf '%s\n' \
      'export RUSTUP_HOME=/usr/local/rustup' \
      'export CARGO_HOME=/usr/local/cargo' \
      'export PATH="/usr/local/cargo/bin:/usr/local/bin:$HOME/.local/bin:$PATH"' \
    > /etc/profile.d/10-vibecrafted-env.sh

# Debug-build linker fix for x86_64 Linux. rustc 1.97 defaults to rust-lld;
# the combined DWARF .debug_info of the ~260-crate vc-server debug build
# overflows the 32-bit R_X86_64_32 relocation range ("out of range … not in
# [0, 4294967295]"), which fails the link. Dropping dev debuginfo keeps panics
# and backtraces intact while eliminating the overflow. Scoped to this cloud
# image so it does not alter host/macOS developer builds.
ENV CARGO_PROFILE_DEV_DEBUG=0

WORKDIR /workspace
USER ubuntu
