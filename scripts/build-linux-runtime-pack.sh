#!/usr/bin/env bash
# Honest name for the multi-arch Linux Runtime Pack assembler.
# Historical filename `build-linux-arm64-runtime-pack.sh` stays in the portable
# REQUIRED_FILES inventory; this wrapper is what CI and docs should call.
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/build-linux-arm64-runtime-pack.sh" "$@"
