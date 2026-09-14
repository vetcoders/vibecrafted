# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Runtime Container — full stack for host-a (tailnet-accessible)
#
# Extends ../Dockerfile (the v1.x Linux base) by baking in:
#   - all foundations (loctree-mcp, aicx-mcp, prview, screenscribe)
#   - all agent CLIs (claude, codex, gemini, agy)
#   - rust toolchain (so cargo paths in install-foundations.sh stay live)
#   - openssh-server (for tailscale-ssh-into-container operator access)
#   - tini (PID-1 signal handling for multi-process entrypoint)
#
# Network model:
#   This image runs WITHOUT its own network stack. It joins the tailscale
#   sidecar's namespace via `network_mode: service:tailscale` in compose.
#   That means: anything bound here listens on the tailnet interface that
#   the sidecar advertises — no port mapping, no host port exposure.
#
# Two-step build (compose orchestrates this automatically):
#   docker build -t vibecrafted-base:local \
#     --build-arg INSTALL_AGENT_CLIS=true \
#     --build-arg INSTALL_FOUNDATIONS=true \
#     --build-arg INSTALL_RUST=true .
#   docker build -t vibecrafted-runtime:local -f docker/runtime/Dockerfile .

ARG VIBECRAFTED_BASE_IMAGE=vibecrafted-base:local

FROM ${VIBECRAFTED_BASE_IMAGE} AS runtime

USER root

ENV DEBIAN_FRONTEND=noninteractive \
    VIBECRAFTED_RUNTIME_FLAVOR=full \
    VIBECRAFTED_DOCKER_SEED_SKILLS=1 \
    SSH_PORT=22

# Runtime-only system packages: ssh server for tailscale ssh, tini for PID 1,
# build essentials for the cargo paths that aicx pulls (llama.cpp / bindgen).
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    build-essential \
    clang \
    cmake \
    libclang-dev \
    libssl-dev \
    openssh-server \
    pkg-config \
    sudo \
    tini \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* \
  && mkdir -p /run/sshd

# SSH hardening — accept tailnet-only by virtue of tailscale sidecar, but
# still disable password auth, root login, and tunnel-by-default. Operator
# uses `tailscale ssh runtime-host-a` (tailscale identity, not unix passwords).
RUN sed -i \
      -e 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' \
      -e 's/^#*PermitRootLogin.*/PermitRootLogin no/' \
      -e 's/^#*ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' \
      -e 's/^#*UsePAM.*/UsePAM no/' \
      -e 's/^#*AllowTcpForwarding.*/AllowTcpForwarding no/' \
      -e 's/^#*X11Forwarding.*/X11Forwarding no/' \
      /etc/ssh/sshd_config \
  && printf '\n# vibecrafted runtime hardening\nAllowUsers vibecrafted\n' >> /etc/ssh/sshd_config

# Allow the vibecrafted user to start sshd / tailscale-side ops without root.
# Tailscale itself runs in the sidecar — this is only for in-container helpers.
RUN echo 'vibecrafted ALL=(root) NOPASSWD: /usr/sbin/sshd, /bin/systemctl, /usr/bin/ssh-keygen' \
      > /etc/sudoers.d/vibecrafted-runtime \
  && chmod 0440 /etc/sudoers.d/vibecrafted-runtime

# The runtime entrypoint: starts sshd, then chains into vibecrafted-docker-entrypoint
# (which seeds skills + dispatches the command).
COPY docker/runtime/entrypoint-runtime.sh /usr/local/bin/vibecrafted-runtime-entrypoint
RUN chmod +x /usr/local/bin/vibecrafted-runtime-entrypoint

# Healthcheck-friendly stamp: tag the image with the source VERSION so
# `docker inspect` shows what runtime is live without a separate label query.
RUN cp /opt/vibecrafted/VERSION /etc/vibecrafted-runtime.version

WORKDIR /workspace
USER vibecrafted

# Volume mount targets — declared for clarity, compose binds them explicitly.
VOLUME ["/workspace/.vibecrafted"]

# tini handles signal forwarding for the multi-process (sshd + cmd) shape.
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/vibecrafted-runtime-entrypoint"]
CMD ["serve"]
