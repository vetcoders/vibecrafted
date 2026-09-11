from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGER = REPO_ROOT / "scripts/stage-runtime-foundations.sh"

AICX_VERSION = "0.13.0"
AICX_LINUX_PACKAGE = "@loctree/aicx-linux-x64-gnu"
AICX_LINUX_TARBALL_SHA256 = (
    "1ac9b9e0d056b9871cc2140474611e41af85a4601415bdd1d29cba657f306e2d"
)


def test_runtime_foundations_use_one_reproducible_aicx_npm_pin() -> None:
    stager = STAGER.read_text(encoding="utf-8")

    assert f'AICX_VERSION="{AICX_VERSION}"' in stager
    assert f'AICX_PACKAGE="{AICX_LINUX_PACKAGE}"' in stager
    assert f'AICX_TARBALL_SHA256="{AICX_LINUX_TARBALL_SHA256}"' in stager
    assert "npm pack" in stager
    assert "stage_npm_bins" in stager
    assert '"aicx": versions["aicx"]' in stager
    assert "codeload.github.com/Loctree/aicx" not in stager
    assert "cargo build --manifest-path" not in stager

    # No manifest-local historical source-build pin may return.
    assert "215b8060fc56f3968e5a9a83a85cba845149a8bf" not in stager
    assert "ced57997dd97a2b08960f35e3a657d7b0c49a200" not in stager
    assert (
        "ffc65ad6652ee0e240beb333f54d7372b607690dcf5f6c29eb68adee2aed58e7" not in stager
    )
