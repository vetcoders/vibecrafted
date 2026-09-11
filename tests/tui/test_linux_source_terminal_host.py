from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from scripts import vetcoders_install as installer

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_wrapper(runtime_root: Path) -> Path:
    source = runtime_root / "scripts" / "vc-terminal-product-entry.sh"
    source.parent.mkdir(parents=True)
    source.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    source.chmod(source.stat().st_mode | stat.S_IXUSR)
    return source


def test_source_candidate_keeps_terminal_wrapper_without_native_host(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "source-generation"
    source = _write_wrapper(runtime_root)

    installer._materialize_runtime_generation_vc_terminal_entry(
        runtime_root, require_native_host=False
    )

    wrapper = runtime_root / "bin" / "vc-terminal"
    host = runtime_root / "libexec" / "vc-terminal"
    assert wrapper.is_file()
    assert os.access(wrapper, os.X_OK)
    assert wrapper.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert not host.exists()


def test_runtime_pack_still_requires_native_terminal_host(tmp_path: Path) -> None:
    runtime_root = tmp_path / "pack-generation"
    _write_wrapper(runtime_root)

    with pytest.raises(OSError, match="source checkout is not a Runtime Pack") as exc:
        installer._materialize_runtime_generation_vc_terminal_entry(runtime_root)

    assert "--runtime-pack-file" in str(exc.value)
    assert "--skills-only" in str(exc.value)
    assert not (runtime_root / "bin" / "vc-terminal").exists()


def test_source_preflight_does_not_require_native_terminal_host() -> None:
    text = (REPO_ROOT / "scripts" / "vetcoders_install.py").read_text(encoding="utf-8")
    prepare_idx = text.index("def _prepare_runtime_generation_candidate")
    pack_idx = text.index("def _install_runtime_pack")
    assert "require_native_host=False" in text[prepare_idx:pack_idx]
    pack_call = text[pack_idx:].index(
        "_materialize_runtime_generation_vc_terminal_entry(staging)"
    )
    assert "require_native_host" not in text[pack_idx + pack_call : pack_idx + pack_call + 80]
