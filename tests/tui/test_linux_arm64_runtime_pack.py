from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_foundation_stager_accepts_linux_arm64_as_a_complete_target(
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
    assert (
        "no complete Runtime Foundations payload for Linux/aarch64" not in result.stderr
    )


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


def test_linux_arm64_builder_uses_pinned_public_inputs() -> None:
    builder = (REPO_ROOT / "vibecrafted-vm/RuntimePack.Containerfile").read_text(
        encoding="utf-8"
    )
    assembler = (REPO_ROOT / "scripts/build-linux-arm64-runtime-pack.sh").read_text(
        encoding="utf-8"
    )
    assert "astral.sh/uv" not in builder
    assert "rustup target add wasm32-unknown-unknown wasm32-wasip1" in builder
    assert builder.index("ARG VIBECRAFTED_SOURCE_REVISION") > builder.index(
        "WORKDIR /src/vibecrafted"
    )
    assert "69616218470b2ad053617efb9e7027b1518ea38918d933c2791e113d99cec507" in builder
    assert "d6685ead9018ad89411291d6198476666e48b0f8" in assembler
    assert "7ab84069c9b7994ce0b705ccedd708aa3a35dcb6" in assembler
    assert "git clone" not in assembler
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

    foundations = (REPO_ROOT / "scripts/stage-runtime-foundations.sh").read_text(
        encoding="utf-8"
    )
    assert 'rm -rf "$WORK/loctree"' in foundations
    assert 'rm -rf "$WORK/aicx"' in foundations
