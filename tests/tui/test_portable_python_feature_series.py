"""Current-stable portable CPython pin: identity, launcher, SSL, pip, venv.

The Runtime Pack seed is the published python-build-standalone install_only
archive named by scripts/lib/portable-python-artifact.json. These tests prove
the pin is the current CPython feature series (3.14), launchers follow the
derived python3.N name, and the extracted interpreter is that series — not
host/Homebrew Python and not a 3.12 path-literal freeze.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PIN_PATH = REPO_ROOT / "scripts/lib/portable-python-artifact.json"
HELPER = REPO_ROOT / "scripts/lib/portable-python.sh"
RELEASE = REPO_ROOT / "scripts/build-vibecrafted-release.sh"
LINUX = REPO_ROOT / "scripts/build-linux-arm64-runtime-pack.sh"
RENDER = REPO_ROOT / "scripts/render-python-entrypoint-launchers.py"
CORE_PYPROJECT = REPO_ROOT / "vibecrafted-core/pyproject.toml"
CACHED_ARCHIVE = (
    Path.home()
    / ".vibecrafted/artifacts/vetcoders/vibecrafted/2026_0914/tmp/python-314"
    / "cpython-3.14.7+20260901-aarch64-apple-darwin-install_only.tar.gz"
)


def _pin() -> dict[str, object]:
    return json.loads(PIN_PATH.read_text(encoding="utf-8"))


def _run(
    script: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.pop("PYTHONPATH", None)
    merged.pop("PYTHONHOME", None)
    if env:
        merged.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(REPO_ROOT),
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


def test_pin_is_current_stable_cpython_feature_series() -> None:
    pin = _pin()
    assert pin["publisher"] == "astral-sh/python-build-standalone"
    assert pin["cpython"] == "3.14.7"
    assert pin["tag"] == "20260901"
    assert pin["flavor"] == "install_only"
    assert "3.12" not in str(pin["cpython"])
    artifacts = {item["platform"]: item for item in pin["artifacts"]}  # type: ignore[index]
    for platform in ("darwin-arm64", "darwin-x86_64", "linux-arm64", "linux-x64"):
        item = artifacts[platform]
        assert "3.14.7" in item["archive"]
        assert "install_only.tar.gz" in item["archive"]
        assert pin["tag"] in item["url"]
        assert len(item["sha256"]) == 64
    assert (
        artifacts["darwin-arm64"]["sha256"]
        == "30daa970c7d223530120f1693cd3c6fa4c0c0d31ef158710b0dd77f286a5b23e"
    )


def test_helper_derives_series_bin_and_dylib_from_the_pin() -> None:
    result = _run(
        f"""
set -euo pipefail
. {HELPER.as_posix()!r}
portable_python_load_pin darwin-arm64
printf '%s\\n' "$PORTABLE_PYTHON_CPYTHON" "$PORTABLE_PYTHON_SERIES" "$PORTABLE_PYTHON_BIN" "$PORTABLE_PYTHON_DYLIB"
"""
    )
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line]
    assert lines == ["3.14.7", "3.14", "python3.14", "libpython3.14.dylib"]


def test_release_builders_follow_derived_series_not_python312_literals() -> None:
    builder = RELEASE.read_text(encoding="utf-8")
    linux = LINUX.read_text(encoding="utf-8")
    helper = HELPER.read_text(encoding="utf-8")
    assert "PORTABLE_PYTHON_BIN" in builder
    assert "portable_python_load_pin" in builder
    assert "PORTABLE_PYTHON_DYLIB" in builder
    assert "PORTABLE_PYTHON_BIN" in linux
    assert "portable_python_load_pin" in linux
    assert "python3.12" not in builder
    assert "python3.12" not in linux
    assert "*/bin/${PORTABLE_PYTHON_BIN}" in helper
    assert "uv python install" not in builder
    assert "uv python install" not in linux


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="PBS darwin-arm64 identity")
def test_extracted_pbs_python_is_3147_with_ssl_pip_venv_and_dispatcher(
    tmp_path: Path,
) -> None:
    pin = _pin()
    archive_name = next(
        item["archive"]
        for item in pin["artifacts"]  # type: ignore[union-attr]
        if item["platform"] == "darwin-arm64"
    )
    dest = tmp_path / "seed"
    dest.mkdir()
    if CACHED_ARCHIVE.is_file():
        shutil.copy2(CACHED_ARCHIVE, dest / archive_name)
    result = _run(
        f"""
set -euo pipefail
. {HELPER.as_posix()!r}
portable_python_load_pin
seed="$(install_portable_python {dest.as_posix()!r})"
printf 'SEED=%s\\n' "$seed"
printf 'SERIES=%s\\n' "$PORTABLE_PYTHON_SERIES"
printf 'BIN=%s\\n' "$PORTABLE_PYTHON_BIN"
"$seed" -c 'import sys,ssl,platform,sysconfig; print("VER="+sys.version.split()[0]); print("EXEC="+sys.executable); print("OPENSSL="+ssl.OPENSSL_VERSION); print("PREFIX="+sys.prefix)'
"""
    )
    assert result.returncode == 0, result.stderr + result.stdout
    lines = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert lines["VER"] == "3.14.7"
    assert lines["SERIES"] == "3.14"
    assert lines["BIN"] == "python3.14"
    seed = Path(lines["SEED"])
    assert seed.is_file() and os.access(seed, os.X_OK)
    assert seed.name == "python3.14"
    assert "/opt/homebrew" not in str(seed)
    assert str(seed).startswith(str(dest))
    assert "Homebrew" not in lines.get("PREFIX", "")
    file_probe = subprocess.run(
        ["file", "-b", str(seed)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Mach-O" in file_probe.stdout
    assert "arm64" in file_probe.stdout

    clean_env = {
        k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}
    }
    ssl_probe = subprocess.run(
        [
            str(seed),
            "-c",
            (
                "import ssl, urllib.request; ctx=ssl.create_default_context(); "
                "r=urllib.request.urlopen('https://pypi.org/simple/pip/', context=ctx, timeout=30); "
                "print(r.status)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=clean_env,
    )
    assert ssl_probe.returncode == 0, ssl_probe.stderr
    assert ssl_probe.stdout.strip() == "200"

    pip_probe = subprocess.run(
        [str(seed), "-m", "pip", "--version"],
        check=False,
        capture_output=True,
        text=True,
        env=clean_env,
    )
    assert pip_probe.returncode == 0, pip_probe.stderr
    assert "pip" in pip_probe.stdout
    assert "3.14" in pip_probe.stdout

    venv_dir = tmp_path / "venv"
    venv_probe = subprocess.run(
        [str(seed), "-m", "venv", str(venv_dir)],
        check=False,
        capture_output=True,
        text=True,
        env=clean_env,
    )
    assert venv_probe.returncode == 0, venv_probe.stderr
    venv_python = venv_dir / "bin" / "python"
    assert venv_python.is_file()
    venv_ver = subprocess.run(
        [str(venv_python), "-c", "import sys; print(sys.version.split()[0])"],
        check=True,
        capture_output=True,
        text=True,
        env=clean_env,
    )
    assert venv_ver.stdout.strip() == "3.14.7"
    venv_pip = subprocess.run(
        [str(venv_python), "-m", "pip", "--version"],
        check=False,
        capture_output=True,
        text=True,
        env=clean_env,
    )
    assert venv_pip.returncode == 0, venv_pip.stderr
    assert "pip" in venv_pip.stdout

    runtime = tmp_path / "runtime"
    python_home = seed.parent.parent
    (runtime / "python-site").mkdir(parents=True)
    (runtime / "bin").mkdir()
    shutil.copytree(python_home, runtime / "python")
    core_dest = runtime / "vibecrafted-core"
    shutil.copytree(
        REPO_ROOT / "vibecrafted-core/vibecrafted_core",
        core_dest / "vibecrafted_core",
    )
    wrapper = runtime / "bin" / "python3"
    wrapper.write_text(
        (
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            'runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"\n'
            "export PYTHONNOUSERSITE=1\n"
            "export PYTHONDONTWRITEBYTECODE=1\n"
            'export PYTHONPATH="$runtime_root/vibecrafted-core:$runtime_root/python-site"\n'
            'exec "$runtime_root/python/bin/python3.14" "$@"\n'
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    dispatcher = subprocess.run(
        [
            str(wrapper),
            "-c",
            (
                "import sys, vibecrafted_core; print(sys.version.split()[0]); "
                "print(sys.executable); print(vibecrafted_core.__name__)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=clean_env,
    )
    assert dispatcher.returncode == 0, dispatcher.stderr
    out = dispatcher.stdout.splitlines()
    assert out[0] == "3.14.7"
    assert "python3.14" in out[1]
    assert out[2] == "vibecrafted_core"

    render = subprocess.run(
        [
            str(wrapper),
            str(RENDER),
            "--pyproject",
            str(CORE_PYPROJECT),
            "--bin-dir",
            str(runtime / "bin"),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=clean_env,
    )
    assert render.returncode == 0, render.stderr
    launched = subprocess.run(
        [str(runtime / "bin" / "vibecrafted"), "--version"],
        check=False,
        capture_output=True,
        text=True,
        env=clean_env,
    )
    assert launched.returncode == 0, launched.stderr + launched.stdout
    assert launched.stdout.strip()
    launcher_text = (runtime / "bin" / "vibecrafted").read_text(encoding="utf-8")
    assert 'exec "$bin_dir/python3"' in launcher_text
