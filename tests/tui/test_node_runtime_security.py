"""Keep every shipped/developer Node carrier above the CVE-2026-111497 floor."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CARRIERS = (
    ("Dockerfile", r"^FROM node:([0-9.]+)-bookworm-slim(?:@sha256:[0-9a-f]{64})?$"),
    (".cursor/Dockerfile", r"^ENV NODE_VERSION=([0-9.]+)$"),
    (
        "vibecrafted-vm/Containerfile",
        r"^FROM node:([0-9.]+)-bookworm-slim@sha256:[0-9a-f]{64}$",
    ),
    (
        "vibecrafted-core/vibecrafted_core/runtime/dev-container/Dockerfile.dev",
        r"^ARG NODE_VERSION=([0-9.]+)$",
    ),
    (
        "vibecrafted-vm/bootstrap-workstation.sh",
        r'NODE_VERSION="\$\{NODE_VERSION:-([0-9.]+)\}"',
    ),
)
# Fixed releases from the Node.js October 21, 2026 security advisory. A patch
# bump needs no test edit; an unsupported/EOL release line is not a safe default.
FIXED = {22: (22, 23, 4), 24: (24, 22, 1), 26: (26, 12, 1)}


def _default_version(relative: str, pattern: str) -> tuple[int, int, int]:
    match = re.search(pattern, (ROOT / relative).read_text(), re.MULTILINE)
    assert match, f"{relative} must select a complete, fixed Node release"
    parts = tuple(int(part) for part in match[1].split("."))
    assert len(parts) == 3
    return parts


@pytest.mark.parametrize(("relative", "pattern"), CARRIERS)
def test_default_node_releases_have_the_brotli_security_fix(relative, pattern):
    version = _default_version(relative, pattern)
    assert version[0] in FIXED, f"{relative}: unsupported Node release line {version}"
    assert version >= FIXED[version[0]], (
        f"{relative}: vulnerable Node default {version}"
    )


def test_node_carriers_select_one_generation():
    versions = {_default_version(*carrier) for carrier in CARRIERS}
    assert len(versions) == 1, f"Node defaults diverged: {sorted(versions)}"


def test_hardened_and_developer_base_images_share_the_verified_digest():
    images = []
    for relative in ("Dockerfile", "vibecrafted-vm/Containerfile"):
        match = re.search(
            r"^FROM (node:[0-9.]+-bookworm-slim@sha256:[0-9a-f]{64})$",
            (ROOT / relative).read_text(),
            re.MULTILINE,
        )
        assert match, f"{relative}: base image needs its immutable manifest digest"
        images.append(match[1])
    assert images[0] == images[1]


@pytest.mark.parametrize(
    ("installed", "override", "downloads", "exit_code", "installation"),
    (
        ("v22.19.0", "", True, 0, "ok"),
        ("v22.23.3", "", True, 0, "ok"),
        ("v22.23.4", "", False, 0, "ok"),
        ("v22.24.0", "", False, 0, "ok"),
        ("v24.22.0", "", True, 0, "ok"),
        ("v24.22.1", "", False, 0, "ok"),
        ("v26.12.0", "", True, 0, "ok"),
        ("v26.12.1", "", False, 0, "ok"),
        ("v20.19.0", "", True, 0, "ok"),
        ("v22.23.4-rc.1", "", True, 0, "ok"),
        ("v22.19.0", "22.19.0", False, 1, "ok"),
        ("v22.19.0", "24.22.0", False, 1, "ok"),
        ("v22.19.0", "26.12.1", True, 0, "ok"),
        ("v22.19.0", "", True, 1, "shadow"),
        ("v22.19.0", "", True, 1, "fail"),
    ),
)
def test_bootstrap_repairs_vulnerable_node_without_replacing_fixed_node(
    tmp_path, installed, override, downloads, exit_code, installation
):
    source = (ROOT / "vibecrafted-vm/bootstrap-workstation.sh").read_text()
    # Execute the actual Node stage with inert command doubles: never source the
    # workstation bootstrap or invoke its apt, download or installation stages.
    stage = source.split("# ── Stage 3:", 1)[1].split("# ── Stage 4:", 1)[0]
    stage = stage[stage.index("\n") :]
    receipt = tmp_path / "download"
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            r"""
set -uo pipefail
INSTALLED="$1" NODE_VERSION="$2" RECEIPT="$3" SUDO="" NODE_INSTALLED=0
INSTALLATION="$4"
log() { :; }
ok() { :; }
warn() { :; }
err() { printf '%s\n' "$*" >&2; }
node() {
  if (( NODE_INSTALLED )); then printf 'v%s\n' "$NODE_VERSION"
  else printf '%s\n' "$INSTALLED"; fi
}
dpkg() { printf 'arm64\n'; }
curl() { printf '%s\n' "$*" > "$RECEIPT"; }
tar() {
  [[ "$INSTALLATION" != fail ]] || return 1
  if [[ "$INSTALLATION" != shadow ]]; then NODE_INSTALLED=1; fi
}
rm() { :; }
corepack() { :; }
npm() { :; }
"""
            + stage,
            "fixture",
            installed,
            override,
            str(receipt),
            installation,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == exit_code, result.stderr
    assert receipt.exists() is downloads
    if downloads:
        expected = override or "22.23.4"
        assert (
            f"/v{expected}/node-v{expected}-linux-arm64.tar.xz" in receipt.read_text()
        )
    if installation == "shadow":
        assert "node on PATH is still below the security floor" in result.stderr
    elif installation == "fail":
        assert "node extraction failed" in result.stderr
    elif exit_code:
        assert "NODE_VERSION must select a supported release" in result.stderr
