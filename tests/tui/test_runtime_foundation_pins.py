from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGER = REPO_ROOT / "scripts/stage-runtime-foundations.sh"

AICX_VERSION = "0.12.6"
AICX_REVISION = "215b8060fc56f3968e5a9a83a85cba845149a8bf"
AICX_ARCHIVE_SHA256 = "6a207d9c8ef82de919eb62db3d50294613e394416c3e28f1b7c5ac44a0151fb9"


def test_runtime_foundations_use_one_reproducible_aicx_pin() -> None:
    stager = STAGER.read_text(encoding="utf-8")

    assert f'AICX_VERSION="{AICX_VERSION}"' in stager
    assert f'AICX_REVISION="{AICX_REVISION}"' in stager
    assert f'AICX_ARCHIVE_SHA256="{AICX_ARCHIVE_SHA256}"' in stager
    assert (
        '"https://codeload.github.com/Loctree/aicx/tar.gz/${AICX_REVISION}"' in stager
    )
    assert '"$AICX_REVISION" "$AICX_ARCHIVE_SHA256" <<\'PY\'' in stager
    assert "aicx_revision, aicx_archive_sha256 = sys.argv[7:9]" in stager
    assert '"aicx": aicx_revision' in stager
    assert (
        'f"https://codeload.github.com/Loctree/aicx/tar.gz/{aicx_revision}"' in stager
    )
    assert '"sha256": aicx_archive_sha256' in stager

    # No manifest-local historical pin may diverge from the source-build pin.
    assert "ced57997dd97a2b08960f35e3a657d7b0c49a200" not in stager
    assert (
        "ffc65ad6652ee0e240beb333f54d7372b607690dcf5f6c29eb68adee2aed58e7" not in stager
    )
