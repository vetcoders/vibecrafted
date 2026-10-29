# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
#
# v1.x Linux base — Debian Bookworm via the official node:22 slim image.
# We start from node:22-bookworm-slim (not bare debian) because every agent
# CLI we ship (claude, codex, gemini) is an npm package; bundling node here
# saves a multi-stage build for the INSTALL_AGENT_CLIS=true path.
#
# Plan 03 (META_22) hardens this image as the CI Linux smoke target.
# See docs/INSTALL.md for the per-platform install matrix.

FROM node:22.23.4-bookworm-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da

ARG INSTALL_AGENT_CLIS=false
ARG INSTALL_FOUNDATIONS=false
# INSTALL_RUST=true pulls rustup + stable toolchain. Off by default so the
# image stays slim; turn it on when foundations are being built from source.
ARG INSTALL_RUST=false

ENV DEBIAN_FRONTEND=noninteractive \
    NODE_ENV=production \
    PYTHONUNBUFFERED=1 \
    VIBECRAFTED_ROOT=/workspace \
    VIBECRAFTED_HOME=/workspace/.vibecrafted \
    VIBECRAFTED_SOURCE=/opt/vibecrafted \
    PATH=/opt/vibecrafted/scripts:/workspace/.vibecrafted/bin:/root/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

WORKDIR /opt/vibecrafted

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    jq \
    make \
    openssh-client \
    python3 \
    python3-venv \
    ripgrep \
    tar \
    unzip \
    xz-utils \
    zsh \
  && curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR="/usr/local/bin" sh \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

COPY . /opt/vibecrafted

RUN chmod 0755 \
    /opt/vibecrafted/install.sh \
    /opt/vibecrafted/scripts/vibecraft \
    /opt/vibecrafted/scripts/vibecrafted \
    /opt/vibecrafted/scripts/install-foundations.sh \
    /opt/vibecrafted/docker/entrypoint.sh \
  && ln -sf /opt/vibecrafted/scripts/vibecrafted /usr/local/bin/vibecrafted \
  && ln -sf /opt/vibecrafted/scripts/vibecraft /usr/local/bin/vibecraft \
  && ln -sf /opt/vibecrafted/docker/entrypoint.sh /usr/local/bin/vibecrafted-docker-entrypoint \
  && (uv sync --project /opt/vibecrafted/scripts/installer --locked || uv sync --project /opt/vibecrafted/scripts/installer)

RUN if [ "$INSTALL_RUST" = "true" ]; then \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
      | sh -s -- -y --default-toolchain stable --profile minimal --no-modify-path \
      && ln -sf /root/.cargo/bin/cargo /usr/local/bin/cargo \
      && ln -sf /root/.cargo/bin/rustc /usr/local/bin/rustc; \
  fi

RUN if [ "$INSTALL_AGENT_CLIS" = "true" ]; then \
    npm install -g \
      @anthropic-ai/claude-code \
      @openai/codex \
      @google/gemini-cli; \
  fi

RUN if [ "$INSTALL_FOUNDATIONS" = "true" ]; then \
    VIBECRAFTED_BIN=/usr/local/bin bash /opt/vibecrafted/scripts/install-foundations.sh --all; \
  fi

RUN groupadd --system vibecrafted \
  && useradd --system --gid vibecrafted --home-dir /workspace --shell /bin/bash vibecrafted

RUN mkdir -p /workspace /workspace/.vibecrafted \
  && chown -R vibecrafted:vibecrafted /workspace \
  && git config --system --add safe.directory /workspace \
  && vibecrafted version \
  && vibecrafted doctor || echo "[docker] doctor build-time check non-fatal; runtime entrypoint will re-seed skills"

WORKDIR /workspace

USER vibecrafted

ENTRYPOINT ["vibecrafted-docker-entrypoint"]
CMD ["help"]
