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


def test_fail_g1_missing_source_interpreter_falls_back_to_installer(
    tmp_path: Path,
) -> None:
    """Genuine source-staging absence (no bin/python3 inode) uses installer python."""
    staging = tmp_path / "tools" / ".vibecrafted-current.staging-abc"
    (staging / "bin").mkdir(parents=True)

    resolved = installer._runtime_verifier_python(staging)

    assert resolved == Path(sys.executable)
    assert os.access(resolved, os.X_OK)
    assert not (staging / "bin/python3").exists()
    assert not (staging / "bin/python3").is_symlink()


def test_fail_g1_dangling_packaged_python_link_fail_closes(tmp_path: Path) -> None:
    """A dangling packaged bin/python3 is corruption, not a host-python fallback."""
    pack = tmp_path / "pack"
    (pack / "bin").mkdir(parents=True)
    (pack / "bin/python3").symlink_to(tmp_path / "missing-packaged-python")

    with pytest.raises(OSError, match="dangling link"):
        installer._runtime_verifier_python(pack)


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


def test_fail_g1_nonexecutable_symlink_target_fail_closes(tmp_path: Path) -> None:
    """A link whose target exists but is not executable is pack corruption."""
    pack = tmp_path / "pack"
    (pack / "bin").mkdir(parents=True)
    target = tmp_path / "nonexec-python"
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target.chmod(0o644)
    (pack / "bin/python3").symlink_to(target)

    with pytest.raises(OSError, match="not executable"):
        installer._runtime_verifier_python(pack)


def test_fail_g1_directory_python_path_fail_closes(tmp_path: Path) -> None:
    """bin/python3 as a directory is corruption, not a host-python fallback."""
    pack = tmp_path / "pack"
    python_dir = pack / "bin/python3"
    python_dir.mkdir(parents=True)
    (python_dir / "nested").write_text("nope\n", encoding="utf-8")
    assert python_dir.is_dir()

    with pytest.raises(OSError, match="directory"):
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


def test_fail_g1_installer_probe_refuses_sub_floor_remaining_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """div0-030: remaining 96ms must not spawn a doomed status probe."""
    spawned: list[float | None] = []

    def fake_run(*_args, **kwargs):
        spawned.append(kwargs.get("timeout"))
        raise AssertionError("sub-floor remaining budget must not spawn a probe")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(
        installer, "_require_inherited_tools_install_lease", lambda *_a, **_k: 3
    )
    now = 10_000.0
    monkeypatch.setattr(installer.time, "monotonic", lambda: now)
    token = installer._RUNTIME_SERVICE_COMMAND_DEADLINE.set(now + 0.0958)
    try:
        with pytest.raises(TimeoutError, match="budget exhausted"):
            installer._run_runtime_service_command(
                tmp_path / "launcher",
                tmp_path,
                "service",
                "status",
                "--json",
            )
    finally:
        installer._RUNTIME_SERVICE_COMMAND_DEADLINE.reset(token)
    assert spawned == []


def test_fail_g1_installer_probe_uses_remaining_budget_when_above_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real remaining budget still bounds the probe, never below the floor."""
    captured: list[float] = []

    def fake_run(*_args, **kwargs):
        captured.append(float(kwargs["timeout"]))

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Result()

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(
        installer, "_require_inherited_tools_install_lease", lambda *_a, **_k: 3
    )
    monkeypatch.setattr(
        installer, "_runtime_service_environment", lambda *_a, **_k: os.environ.copy()
    )
    now = 10_000.0
    remaining = 5.0
    monkeypatch.setattr(installer.time, "monotonic", lambda: now)
    token = installer._RUNTIME_SERVICE_COMMAND_DEADLINE.set(now + remaining)
    try:
        installer._run_runtime_service_command(
            tmp_path / "launcher", tmp_path, "service", "status", "--json"
        )
    finally:
        installer._RUNTIME_SERVICE_COMMAND_DEADLINE.reset(token)
    assert captured == [remaining]
    assert captured[0] >= installer._RUNTIME_SERVICE_PROBE_MIN_TIMEOUT_SECONDS
