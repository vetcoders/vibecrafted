"""Coherence checks for the Windows-only EXE/MSI installer cut.

Does not build a pack, does not fetch WiX, and does not write under the
operator LOCALAPPDATA Vibecrafted home.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGING = REPO_ROOT / "packaging" / "windows"
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-windows-installers.ps1"
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-runtime-pack.ps1"
IDENTITY = PACKAGING / "Identity.wxi"
PRODUCT = PACKAGING / "Product.wxs"
BUNDLE = PACKAGING / "Bundle.wxs"
README = PACKAGING / "README.md"

STABLE_UPGRADE_CODE = "B7E4C2A1-9F3D-4B8E-A6C1-2D5E8F0A1B3C"


def test_windows_installer_sources_exist() -> None:
    for path in (IDENTITY, PRODUCT, BUNDLE, BUILD_SCRIPT, INSTALL_SCRIPT, README):
        assert path.is_file(), path


def test_windows_installer_identity_matches_version_and_upgrade_code() -> None:
    version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert version
    identity = IDENTITY.read_text(encoding="utf-8")
    product = PRODUCT.read_text(encoding="utf-8")
    bundle = BUNDLE.read_text(encoding="utf-8")
    build = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert f'UpgradeCode = "{STABLE_UPGRADE_CODE}"' in identity
    assert 'ProductName = "Vibecrafted"' in identity
    assert '<?include Identity.wxi ?>' in product
    assert '<?include Identity.wxi ?>' in bundle
    assert 'UpgradeCode="$(var.UpgradeCode)"' in product
    assert 'UpgradeCode="$(var.UpgradeCode)"' in bundle
    assert "ProductVersion" in build
    assert "VERSION" in build
    assert version.split(".")[0].isdigit()


def test_windows_installer_delegates_to_install_runtime_pack() -> None:
    product = PRODUCT.read_text(encoding="utf-8")
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "install-runtime-pack.ps1" in product
    assert "install-runtime-pack.ps1" in build
    assert r"scripts\install-runtime-pack.ps1" in build
    assert "InstallRuntimePack" in product


def test_windows_installer_fails_closed_without_pack() -> None:
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "Runtime Pack tarball is missing" in build
    assert "refusing to invent one" in build
    assert '[Parameter(Mandatory = $true)][string]$Pack' in build


def test_windows_installer_cut_does_not_require_voc_radio() -> None:
    product = PRODUCT.read_text(encoding="utf-8")
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    for blob in (product, build, readme):
        assert "voc radio is not in this installer cut" in blob or "voc radio" in readme
    assert "cfg(unix)" in readme
    assert "Windows AF_UNIX" in readme
    assert "operating system cannot" not in readme.lower()
    assert "cannot do Unix" not in readme
    # Builder does not Die on missing voc binaries.
    assert "voc radio was not built" not in build
    assert "-p voc" not in build
