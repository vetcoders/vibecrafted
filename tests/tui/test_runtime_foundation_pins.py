from __future__ import annotations

from pathlib import Path

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
    assert "links Homebrew OpenSSL" in stager
    assert "refuse rather than rebuilding" in stager
    # Historical source-build pin must not return.
    assert "215b8060fc56f3968e5a9a83a85cba845149a8bf" not in stager
    assert "ced57997dd97a2b08960f35e3a657d7b0c49a200" not in stager
    assert (
        "ffc65ad6652ee0e240beb333f54d7372b607690dcf5f6c29eb68adee2aed58e7" not in stager
    )
