# syntax=docker/dockerfile:1.7
FROM rust:1.95-bookworm@sha256:6258907abe69656e41cd992e0b705cdcfabcbbe3db374f92ed2d47121282d4a1 AS builder

ENV DEBIAN_FRONTEND=noninteractive CARGO_TERM_COLOR=always
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates clang cmake curl g++ git libclang-dev libfontconfig1-dev \
      libfreetype6-dev libssl-dev libudev-dev libwayland-dev libx11-dev \
      libxcb-shape0-dev libxcb-xfixes0-dev libxkbcommon-dev make ninja-build \
      npm perl pkg-config python3 python3-venv xz-utils \
 && rm -rf /var/lib/apt/lists/*
RUN rustup target add wasm32-unknown-unknown wasm32-wasip1 \
 && cargo install --locked cargo-leptos@0.3.7 \
 && curl -fL --proto '=https' --tlsv1.2 \
      https://github.com/astral-sh/uv/releases/download/0.8.14/uv-aarch64-unknown-linux-gnu.tar.gz \
      -o /tmp/uv.tar.gz \
 && printf '%s  %s\n' \
      69616218470b2ad053617efb9e7027b1518ea38918d933c2791e113d99cec507 \
      /tmp/uv.tar.gz | sha256sum -c - \
 && tar -xzf /tmp/uv.tar.gz -C /tmp \
 && install -m 0755 /tmp/uv-aarch64-unknown-linux-gnu/uv /usr/local/bin/uv \
 && rm -rf /tmp/uv.tar.gz /tmp/uv-aarch64-unknown-linux-gnu
# voc's Linux tray integration is a real runtime surface, so its native GTK
# development contract is explicit instead of depending on a rich base image.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libayatana-appindicator3-dev libdbus-1-dev libgtk-3-dev libxdo-dev \
      protobuf-compiler \
 && rm -rf /var/lib/apt/lists/*
ENV PATH=/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin
WORKDIR /src/vibecrafted
ARG VIBECRAFTED_SOURCE_REVISION
COPY . .
RUN test -n "$VIBECRAFTED_SOURCE_REVISION" \
 && VIBECRAFTED_SOURCE_OWNER_REPO=vetcoders/vibecrafted \
      VIBECRAFTED_SOURCE_REVISION="$VIBECRAFTED_SOURCE_REVISION" \
      scripts/build-linux-arm64-runtime-pack.sh /out/Vibecrafted_RuntimePack_linux-arm64.tar.gz

FROM scratch AS export
COPY --from=builder /out/ /
