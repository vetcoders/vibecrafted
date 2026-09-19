from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGER = REPO_ROOT / "scripts/stage-runtime-foundations.sh"

LOCTREE_VERSION = "0.14.4"
LOCTREE_REVISION = "3e9eb0a74cb3c043d740de5fe7d8c93985d0a876"
AICX_VERSION = "0.13.0"
AICX_REVISION = "91b2fe121e97e92df7fffbc12b81abbf68a34fc1"
PRVIEW_VERSION = "0.7.0"
PRVIEW_REVISION = "2e11cc6d6e90a606a17d71d0d093a1e2f564bc80"
PRVIEW_DARWIN_SHA256 = (
    "f37729af60f21ba8d29621cc5e139b907cae0264776f44030087d65dc24261f0"
)
PRVIEW_LINUX_SHA256 = "a5d2024a6c4c8aeecf97771780c15a13b45046c6c3f10e88307ead07ac88a053"


def test_runtime_foundations_consume_published_packages() -> None:
    stager = STAGER.read_text(encoding="utf-8")

    assert f'LOCTREE_VERSION="{LOCTREE_VERSION}"' in stager
    assert f'LOCTREE_REVISION="{LOCTREE_REVISION}"' in stager
    assert f'AICX_VERSION="{AICX_VERSION}"' in stager
    assert f'AICX_REVISION="{AICX_REVISION}"' in stager
    assert f'PRVIEW_VERSION="{PRVIEW_VERSION}"' in stager
    assert f'PRVIEW_REVISION="{PRVIEW_REVISION}"' in stager
    assert "@loctree/loctree-darwin-arm64" in stager
    assert "@loctree/loctree-linux-x64-gnu" in stager
    assert "@loctree/aicx-darwin-arm64" in stager
    assert "@loctree/aicx-linux-x64-gnu" in stager
    assert "npm pack" in stager
    assert (
        "https://github.com/vetcoders/prview-rs/releases/download/v0.7.0/"
        "prview-aarch64-apple-darwin.tar.gz" in stager
    )
    assert (
        "https://github.com/vetcoders/prview-rs/releases/download/v0.7.0/"
        "prview-x86_64-unknown-linux-gnu.tar.gz" in stager
    )
    assert f'PRVIEW_SHA256="{PRVIEW_DARWIN_SHA256}"' in stager
    assert f'PRVIEW_SHA256="{PRVIEW_LINUX_SHA256}"' in stager
    assert '"channel": "npm"' in stager
    assert '"channel": "github-release"' in stager
    assert '"prview": prview_revision' in stager


def test_runtime_foundations_never_compile_external_tools() -> None:
    stager = STAGER.read_text(encoding="utf-8")

    forbidden = (
        "cargo build",
        "cargo install",
        "CARGO_TARGET_DIR",
        "CARGO_PROFILE_RELEASE_STRIP",
        "remap-path-prefix",
        "ffile-prefix-map",
        "LOCTREE_SOURCE_BUILD",
        "fetch_source",
        "codeload.github.com/Loctree/aicx",
        "codeload.github.com/Loctree/loctree",
        "brew --prefix openssl",
        "OPENSSL_STATIC",
        "crates.io/api/v1/crates/prview",
    )
    present = [token for token in forbidden if token in stager]
    assert present == []
    assert "never cargo-build" in stager
    assert "no published Runtime Foundations payload for Linux/aarch64" in stager
    assert "no published Runtime Foundations payload for Windows/x86_64" in stager
    assert "darwin-relocate-openssl.sh" in stager
    assert "@loader_path/../lib/libssl.3.dylib" in stager
    assert "libexec/prview" in stager
    assert "links Homebrew OpenSSL" not in stager
    assert "refuse rather than rebuilding" not in stager
    # Historical source-build pin must not return.
    assert "215b8060fc56f3968e5a9a83a85cba845149a8bf" not in stager
    assert "ced57997dd97a2b08960f35e3a657d7b0c49a200" not in stager
    assert (
        "ffc65ad6652ee0e240beb333f54d7372b607690dcf5f6c29eb68adee2aed58e7" not in stager
    )


def test_runtime_foundations_relocate_darwin_prview_onto_pinned_openssl() -> None:
    stager = STAGER.read_text(encoding="utf-8")
    relocator = (REPO_ROOT / "scripts/lib/darwin-relocate-openssl.sh").read_text(
        encoding="utf-8"
    )
    pins = (REPO_ROOT / "scripts/lib/published-foundation-digests.json").read_text(
        encoding="utf-8"
    )

    assert "stage_relocatable_openssl" in relocator
    assert "@loader_path/libssl.3.dylib" in relocator
    assert (
        "ebee4a51513f22f6efc6d3b9ff9d638015d2d924c0e443b31df2735798cb8dcb" in relocator
    )
    assert (
        "380d32d4d229136f9ded004906634491c8d416779d031abadc3d6463b1ef8e3d" in relocator
    )
    assert "homebrew-bottle-dylib" in stager
    assert "SSL_CERT_FILE" in relocator
    assert "vtool" in relocator
    assert "-set-build-version macos" in relocator
    assert 'DARWIN_MACOS_MINOS="14.0"' in relocator
    assert "6c88574eda7646be1850a609313d244c6c0080066d717729f8a3943b4aecb25f" in pins
    assert "c8d8d4d94096f780eeba2a9f4060bea25099046b7ff0569878bcaea84daf20ab" in pins
    assert "published-foundation-digests.json" in stager


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin OpenSSL relocation")
def test_relocated_openssl_dylibs_are_macos_14_minos(tmp_path: Path) -> None:
    relocator = REPO_ROOT / "scripts/lib/darwin-relocate-openssl.sh"
    ssl_src = Path("/opt/homebrew/opt/openssl@3/lib/libssl.3.dylib")
    crypto_src = Path("/opt/homebrew/opt/openssl@3/lib/libcrypto.3.dylib")
    if not ssl_src.is_file() or not crypto_src.is_file():
        pytest.skip("pinned Homebrew OpenSSL bottle is not present")

    lib_dir = tmp_path / "lib"
    license_dir = tmp_path / "licenses"
    env = os.environ.copy()
    env.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")

    def stage() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; stage_relocatable_openssl "$2" "$3"',
                "openssl-minos",
                str(relocator),
                str(lib_dir),
                str(license_dir),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    # The relocator copies only the pinned bottle BYTES. A runner whose
    # Homebrew carries another OpenSSL build (macos-latest: not 3.6.3) cannot
    # prove the minos stamp on those bytes -- but it can and must prove that
    # the pin refuses them before a single file is staged.
    relocator_text = relocator.read_text(encoding="utf-8")
    pins = {
        ssl_src: re.search(
            r'^OPENSSL_LIBSSL_SHA256="([0-9a-f]{64})"$', relocator_text, re.MULTILINE
        ),
        crypto_src: re.search(
            r'^OPENSSL_LIBCRYPTO_SHA256="([0-9a-f]{64})"$', relocator_text, re.MULTILINE
        ),
    }
    assert all(pins.values()), "relocator no longer pins both OpenSSL digests"
    foreign = [
        str(path)
        for path, pin in pins.items()
        if hashlib.sha256(path.read_bytes()).hexdigest() != pin.group(1)
    ]
    if foreign:
        refused = stage()
        assert refused.returncode != 0, refused.stdout + refused.stderr
        assert "is not the pinned Homebrew bottle bytes" in refused.stderr
        assert not lib_dir.exists()
        pytest.skip(
            "host OpenSSL is not the pinned Homebrew bottle "
            f"({', '.join(foreign)}); the pin refused it, minos needs the pinned bytes"
        )

    staged = stage()
    assert staged.returncode == 0, staged.stdout + staged.stderr
    for name in ("libssl.3.dylib", "libcrypto.3.dylib"):
        probe = subprocess.check_output(
            ["otool", "-l", str(lib_dir / name)],
            text=True,
        )
        assert "minos 14.0" in probe
        assert "minos 26.0" not in probe
