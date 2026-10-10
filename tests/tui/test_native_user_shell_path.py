"""Exercise the App's native shared-PATH adapter without consulting host rc files."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_native_user_shell_path_api_cache_and_bounded_fallback(tmp_path: Path) -> None:
    compiler = shutil.which("swiftc")
    if sys.platform != "darwin" or compiler is None:
        pytest.skip("swiftc on macOS is required for the native PATH adapter")
    shell = REPO_ROOT / "vibecrafted-app/shell-agent"
    binary = tmp_path / "native-user-shell-path"
    compiled = subprocess.run(
        [
            compiler,
            "-swift-version",
            "6",
            str(shell / "app/Vibecrafted/NativeInstallerProcess.swift"),
            str(shell / "app/Vibecrafted/NativeUserShellPath.swift"),
            str(shell / "tests/NativeUserShellPathTests.swift"),
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    completed = subprocess.run(
        [str(binary)],
        env={**os.environ, "NATIVE_TEST_PYTHON": sys.executable},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "NativeUserShellPathTests passed" in completed.stdout


def test_native_app_composes_absolute_deduplicated_guest_path(tmp_path: Path) -> None:
    compiler = shutil.which("swiftc")
    if sys.platform != "darwin" or compiler is None:
        pytest.skip("swiftc on macOS is required for native PATH composition")
    source = (
        REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted/AppDelegate.swift"
    ).read_text(encoding="utf-8")
    # Compile the current production method; this is not a reimplementation of
    # its PATH policy or a regex assertion about which tokens it contains.
    method = source[
        source.index("  private func composedPath(") : source.index(
            "  /// Surface a launch failure"
        )
    ]
    harness = tmp_path / "composition.swift"
    harness.write_text(
        "import Foundation\n@main struct CompositionHarness {\n"
        + method
        + "\nstatic func main() { print(CompositionHarness().composedPath(\n"
        + 'generation: URL(fileURLWithPath: "/generation"),\n'
        + 'inherited: "/user/cargo/bin:relative:.:/usr/bin:/user/cargo/bin:/generation/bin")) }\n}\n',
        encoding="utf-8",
    )
    binary = tmp_path / "native-path-composition"
    compiled = subprocess.run(
        [
            compiler,
            "-swift-version",
            "6",
            "-parse-as-library",
            str(harness),
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    completed = subprocess.run(
        [str(binary)], capture_output=True, text=True, check=False, timeout=10
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == (
        "/generation/bin:/user/cargo/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    )
