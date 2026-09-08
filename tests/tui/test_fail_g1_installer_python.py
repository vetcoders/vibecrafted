"""G1 falsifiers: source-staging Python fallback and explicit runtime downgrade.

These tests execute installer helpers, not string mirrors. They fail on the
baseline where a dangling staging ``bin/python3`` raised and where downgrade
had no ``--allow-older-runtime`` override.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from scripts import vetcoders_install as installer
from tests.tui.test_installer_uninstall import _provenance as _pack_provenance


def test_fail_g1_dangling_staging_python_falls_back_to_installer(
    tmp_path: Path,
) -> None:
    """Source-staging phantom python must not drain a live pair (div0-046)."""
    staging = tmp_path / "tools" / ".vibecrafted-current.staging-abc"
    (staging / "bin").mkdir(parents=True)
    (staging / "bin/python3").symlink_to(tmp_path / "missing-python")

    resolved = installer._runtime_verifier_python(staging)

    assert resolved == Path(sys.executable)
    assert os.access(resolved, os.X_OK)


def test_fail_g1_carried_nonexecutable_python_still_fail_closed(
    tmp_path: Path,
) -> None:
    """A real carried interpreter that is not executable remains pack corruption."""
    pack = tmp_path / "pack"
    carried = pack / "bin/python3"
    carried.parent.mkdir(parents=True)
    carried.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    carried.chmod(0o644)

    with pytest.raises(OSError, match="not executable"):
        installer._runtime_verifier_python(pack)


def test_fail_g1_allow_older_runtime_is_explicit_only(
    tmp_path: Path,
) -> None:
    runtime_home = tmp_path / "runtime"
    active_root = runtime_home / "releases" / "4.3.0+g3bbe57a2"
    active_root.mkdir(parents=True)
    (active_root / installer.RUNTIME_PACK_PROVENANCE_NAME).write_text(
        json.dumps(
            _pack_provenance("4.3.0+g3bbe57a2", "20260827", **{"vc-frame": "f" * 40})
        ),
        encoding="utf-8",
    )
    (runtime_home / "active.json").write_text(
        json.dumps({"runtime_root": str(active_root), "version": "4.3.0+g3bbe57a2"}),
        encoding="utf-8",
    )
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / installer.RUNTIME_PACK_PROVENANCE_NAME).write_text(
        json.dumps(
            _pack_provenance("4.3.0+ga64dd3e4", "20260826", **{"vc-frame": "9" * 40})
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeError, match="refusing to replace a newer active runtime"
    ):
        installer._refuse_runtime_pack_downgrade(candidate, runtime_home)

    installer._refuse_runtime_pack_downgrade(candidate, runtime_home, allow_older=True)
