from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_foundation_stager_rejects_linux_arm64_without_published_packages(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "uname",
        '#!/bin/sh\n[ "${1:-}" = -s ] && echo Linux || echo aarch64\n',
    )
    result = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts/stage-runtime-foundations.sh"),
            str(tmp_path / "out"),
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "VIBECRAFTED_FOUNDATIONS_TARGET_PROBE": "1",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "no published Runtime Foundations payload for Linux/aarch64" in result.stderr


def test_local_vm_image_consumes_only_the_exact_runtime_pack_carrier() -> None:
    containerfile = (REPO_ROOT / "vibecrafted-vm/Containerfile").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "COPY src/loctree-suite",
        "COPY src/aicx",
        "releases/latest",
        "installing stub",
        "best-effort",
        '|| echo "[warn]',
        'VOLUME ["/workspace"',
    )
    assert not [token for token in forbidden if token in containerfile]
    assert "ARG RUNTIME_PACK_ARCHIVE" in containerfile
    assert "runtime-pack-provenance.json" in containerfile
    assert "passwd tini" in containerfile
    assert "/usr/sbin/groupadd" in containerfile
    assert "/usr/sbin/useradd" in containerfile
    assert "chmod -R a-w" not in containerfile
    assert "chown -R root:root /opt/vibecrafted-runtime" in containerfile
    assert "USER vibecrafted" in containerfile
    assert "vc-frame vc-terminal voc" in containerfile
    entry = (REPO_ROOT / "vibecrafted-vm/runtime-entry.sh").read_text(encoding="utf-8")
    assert "vc-frame vc-terminal voc" in entry


def test_linux_builder_uses_pinned_public_inputs_for_arm64_and_x64() -> None:
    builder = (REPO_ROOT / "vibecrafted-vm/RuntimePack.Containerfile").read_text(
        encoding="utf-8"
    )
    wrapper = (REPO_ROOT / "scripts/build-linux-runtime-pack.sh").read_text(
        encoding="utf-8"
    )
    assembler = (REPO_ROOT / "scripts/build-linux-arm64-runtime-pack.sh").read_text(
        encoding="utf-8"
    )
    assert "build-linux-arm64-runtime-pack.sh" in wrapper
    assert "astral.sh/uv" not in builder
    assert "rustup target add wasm32-unknown-unknown wasm32-wasip1" in builder
    assert builder.index("ARG VIBECRAFTED_SOURCE_REVISION") > builder.index(
        "WORKDIR /src/vibecrafted"
    )
    assert "69616218470b2ad053617efb9e7027b1518ea38918d933c2791e113d99cec507" in builder
    assert "d6685ead9018ad89411291d6198476666e48b0f8" in assembler
    assert "7ab84069c9b7994ce0b705ccedd708aa3a35dcb6" in assembler
    assert "git clone" not in assembler
    assert "VIBECRAFTED_SOURCE_OWNER_REPO" in assembler
    assert 'export VIBECRAFTED_SOURCE_REVISION="$source_revision"' in assembler
    assert 'export RUSTUP_TOOLCHAIN="${RUSTUP_TOOLCHAIN:-1.97.0}"' in assembler
    assert 'export CC="${CC:-gcc}"' in assembler
    assert 'export CXX="${CXX:-g++}"' in assembler
    assert 'voc_target="$work/voc-target"' in assembler
    assert 'CARGO_TARGET_DIR="$voc_target" cargo build --locked' in assembler
    assert "--release -p voc --bin voc --bin vc-start" in assembler
    assert (
        'install -m 0755 "$voc_target/release/vc-start" '
        '"$payload/bin/vc-start"' in assembler
    )
    assert '"$repo_root/vibecrafted-app/target' not in assembler
    assert 'rm -rf "$work/vc-terminal" "$work/vc-terminal.tar.gz"' in assembler
    assert (
        'RUSTFLAGS="--remap-path-prefix=$work/vc-frame=/usr/src/vc-frame"' in assembler
    )
    assert "cargo xtask build --release --no-plugins" not in assembler
    assert "cargo xtask build --release" in assembler
    assert 'rm -rf "$work/vc-frame" "$work/vc-frame.tar.gz"' in assembler
    assert 'rm -rf "$voc_target"' in assembler
    assert 'rm -rf "$server_build"' in assembler
    assert (
        'install -m 0755 "$repo_root/scripts/vibecrafted" '
        '"$payload/scripts/vibecrafted"' in assembler
    )
    assert 'printf \'%s+g%.8s\\n\' "$version" "$source_revision"' in assembler
    assert 'architecture="arm64"' in assembler
    assert 'architecture="x64"' in assembler
    assert 'target="aarch64-unknown-linux-gnu"' in assembler
    assert 'target="x86_64-unknown-linux-gnu"' in assembler
    assert 'platform="linux-$architecture"' in assembler
    assert '--platform "$platform" --architecture "$architecture"' in assembler
    assert '"$payload/bin/vibecrafted-server-web"' in assembler
    assert '"$payload/bin/vc-server-supervisor"' in assembler
    assert '"$payload/bin/scaffold-doctor"' in assembler
    assert '"$payload/vibecrafted-mcp/"' in assembler

    foundations = (REPO_ROOT / "scripts/stage-runtime-foundations.sh").read_text(
        encoding="utf-8"
    )
    assert "@loctree/aicx-linux-x64-gnu" in foundations
    assert "@loctree/loctree-linux-x64-gnu" in foundations
    assert "stage_npm_bins" in foundations
    assert "cargo build --manifest-path" not in foundations
    assert "cargo install --locked --version" in foundations
    assert "will not cargo-build those donors" in foundations


def test_linux_x86_64_host_expects_carrier_architecture_x64() -> None:
    """uname -m is x86_64; the Runtime Pack carrier slug is x64 / linux-x64."""
    installer = (REPO_ROOT / "scripts/install-runtime-pack.sh").read_text(
        encoding="utf-8"
    )
    assert 'expected_architecture="x64"' in installer
    assert 'expected_architecture="x86_64"' not in installer
