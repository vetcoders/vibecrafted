"""Coherence checks for the Windows-only EXE/MSI installer cut.

Does not build a pack, does not fetch WiX, and does not write under the
operator LOCALAPPDATA Vibecrafted home.
"""

from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGING = REPO_ROOT / "packaging" / "windows"
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-windows-installers.ps1"
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-runtime-pack.ps1"
IDENTITY = PACKAGING / "Identity.wxi"
PRODUCT = PACKAGING / "Product.wxs"
BUNDLE = PACKAGING / "Bundle.wxs"
README = PACKAGING / "README.md"
LICENSE_RTF = PACKAGING / "License.rtf"
REPO_LICENSE = REPO_ROOT / "LICENSE"
SECURITY_MD = REPO_ROOT / "SECURITY.md"

STABLE_UPGRADE_CODE = "B7E4C2A1-9F3D-4B8E-A6C1-2D5E8F0A1B3C"


def test_windows_installer_sources_exist() -> None:
    for path in (
        IDENTITY,
        PRODUCT,
        BUNDLE,
        LICENSE_RTF,
        BUILD_SCRIPT,
        INSTALL_SCRIPT,
        README,
    ):
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
    assert 'ProductVersion = "0.0.0.0"' in identity
    assert "<?include Identity.wxi ?>" in product
    assert "<?include Identity.wxi ?>" in bundle
    assert 'UpgradeCode="$(var.UpgradeCode)"' in product
    assert 'UpgradeCode="$(var.UpgradeCode)"' in bundle
    assert "ProductVersion" in build
    assert "VERSION" in build
    assert "Write-Utf8NoBom" in build
    assert "Stamp ProductVersion into Identity only" in build
    assert STABLE_UPGRADE_CODE in build
    # candle must not receive named bindpaths; light uses "-b name=path".
    candle_block = build.split("& $candle", 1)[1].split("& $light", 1)[0]
    assert "-b " not in candle_block
    assert '-b "staging=' in build
    assert '-b "out=' in build
    assert version.split(".")[0].isdigit()


def test_windows_installer_is_per_user_portable() -> None:
    product = PRODUCT.read_text(encoding="utf-8")
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    assert 'InstallScope="perUser"' in product
    assert "LocalAppDataFolder" in product
    assert "ProgramFiles64Folder" not in product
    assert 'InstallScope="perMachine"' not in product
    assert 'Root="HKCU"' in product
    assert "RemoveFolder" in product
    assert "perUser" in build
    assert "LocalAppDataFolder" in build
    assert "per-user" in readme.lower() or "Per-user" in readme


def test_windows_installer_upgrade_and_uninstall_are_explicit() -> None:
    product = PRODUCT.read_text(encoding="utf-8")
    assert "<MajorUpgrade" in product
    assert 'AllowSameVersionUpgrades="no"' in product
    assert "DowngradeErrorMessage=" in product
    assert 'Return="check"' in product
    assert product.count('Return="check"') >= 2
    assert 'Impersonate="yes"' in product
    assert 'Impersonate="no"' not in product
    assert 'REMOVE~="ALL"' in product
    assert "NOT REMOVE" in product
    assert 'Return="ignore"' not in product


def test_windows_installer_delegates_to_install_runtime_pack() -> None:
    product = PRODUCT.read_text(encoding="utf-8")
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "install-runtime-pack.ps1" in product
    assert "install-runtime-pack.ps1" in build
    assert r"scripts\install-runtime-pack.ps1" in build
    assert "InstallRuntimePack" in product
    assert "UninstallRuntimePack" in product


def test_windows_installer_fails_closed_without_pack() -> None:
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "Runtime Pack tarball is missing" in build
    assert "refusing to invent one" in build
    assert "[Parameter(Mandatory = $true)][string]$Pack" in build
    assert "candle failed" in build
    assert "light MSI failed" in build
    assert "light Burn EXE failed" in build
    assert "WiX 3.14 download failed" in build


def test_windows_installer_fails_closed_on_pack_version_skew() -> None:
    """Builder must refuse stamping repo ProductVersion onto a skew carrier."""
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "VibecraftedRuntime/VERSION" in build
    assert "Get-BaseSemVer" in build
    assert "disagrees with repo VERSION" in build
    assert "refusing to stamp ProductVersion=" in build
    assert "disagrees with payload VERSION" in build
    assert "Vibecrafted_RuntimePack_" in build
    # Gate runs before WiX candle so a skew pack never reaches the linker.
    gate_at = build.index("refusing to stamp ProductVersion=")
    candle_at = build.index("& $candle")
    assert gate_at < candle_at


def test_windows_installer_refuses_skewed_pack_tarball(tmp_path: Path) -> None:
    """Behavioral lock: pack 9.9.9 must not stamp against repo VERSION."""
    if os.name != "nt":
        return
    repo_version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert repo_version
    skew = "9.9.9"
    assert skew != repo_version.split("+", 1)[0].split("-", 1)[0]

    pack = tmp_path / f"Vibecrafted_RuntimePack_{skew}-win32-x64.tar.gz"
    root = tmp_path / "payload"
    (root / "VibecraftedRuntime").mkdir(parents=True)
    (root / "VibecraftedRuntime" / "VERSION").write_text(f"{skew}\n", encoding="ascii")
    with tarfile.open(pack, "w:gz") as archive:
        archive.add(
            root / "VibecraftedRuntime",
            arcname="VibecraftedRuntime",
            recursive=True,
        )
    (tmp_path / f"{pack.name}.sha256").write_text(
        f"{'0' * 64}  {pack.name}\n", encoding="ascii"
    )
    (tmp_path / f"{pack.name}.sig").write_bytes(b"\x00" * 64)

    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BUILD_SCRIPT),
            "-Pack",
            str(pack),
            "-OutDir",
            str(tmp_path / "out"),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    blob = (result.stdout or "") + (result.stderr or "")
    compact = "".join(blob.split())
    assert result.returncode != 0, blob
    assert "disagreeswithrepoVERSION" in compact, blob
    assert "refusingtostampProductVersion=" in compact, blob
    assert skew in compact, blob
    assert not (tmp_path / "out" / "Vibecrafted.msi").exists()
    assert not (tmp_path / "out" / "Vibecrafted.exe").exists()


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


def test_windows_installer_bundle_chains_same_msi() -> None:
    bundle = BUNDLE.read_text(encoding="utf-8")
    product = PRODUCT.read_text(encoding="utf-8")
    assert "Vibecrafted.msi" in bundle
    assert 'Vital="yes"' in bundle
    assert "$(var.ProductName)" in bundle
    assert "$(var.ProductVersion)" in bundle
    assert 'UpgradeCode="$(var.UpgradeCode)"' in bundle
    assert 'Name="$(var.ProductName)"' in product


def test_windows_installer_license_comes_from_repo_license() -> None:
    """MSI/Burn must show the repo BUSL text, not a invented EULA placeholder."""
    assert REPO_LICENSE.is_file()
    assert LICENSE_RTF.is_file()
    assert SECURITY_MD.is_file()
    license_text = REPO_LICENSE.read_text(encoding="utf-8")
    rtf = LICENSE_RTF.read_text(encoding="utf-8")
    product = PRODUCT.read_text(encoding="utf-8")
    bundle = BUNDLE.read_text(encoding="utf-8")
    identity = IDENTITY.read_text(encoding="utf-8")
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    # Full legal company name from vista-win LICENSE Company definition.
    full_licensor = "Libraxis AI Sp. z o.o."
    # Stylized brand (mathematical sans-serif bold) + Framework; keep former name.
    licensed_work = "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Framework (formerly Vetcoders Skills)."
    assert f"Licensor:             {full_licensor}" in license_text
    assert "Licensor:             Vetcoders" not in license_text
    assert "Licensor:             LibraxisAI\n" not in license_text
    assert "Licensor:             LibraxisAI\r" not in license_text
    assert f"Licensed Work:        {licensed_work}" in license_text
    assert (
        "Licensed Work:        Vibecrafted (formerly Vetcoders Skills)."
        not in license_text
    )
    assert f"The Licensed Work is (c) 2024-2026 {full_licensor}" in license_text
    assert "Business Source License" in license_text
    assert "Individual developers and small teams" in license_text
    assert "fewer than 5" in license_text
    assert "production free of" in license_text
    assert f"Licensor:             {full_licensor}" in rtf
    assert "Licensor:             Vetcoders" not in rtf
    assert "Licensor:             LibraxisAI\\par" not in rtf
    # RTF must carry signed UTF-16 surrogate \\u escapes (not plain ASCII brand).
    assert "\\u-10187?" in rtf
    assert "\\u55349?" not in rtf
    assert "Framework (formerly Vetcoders Skills)." in rtf
    assert "Licensed Work:        Vibecrafted (formerly Vetcoders Skills)." not in rtf
    assert "Business Source License" in rtf
    assert "Individual developers and small teams" in rtf
    assert "fewer than 5" in rtf
    assert "PLACEHOLDER" not in rtf.upper()
    assert "EULA" not in rtf

    assert f'Manufacturer = "{full_licensor}"' in identity
    assert 'Manufacturer = "Vetcoders"' not in identity
    assert 'Manufacturer = "LibraxisAI"' not in identity
    assert f'Copyright="(c) 2024-2026 {full_licensor}"' in bundle
    assert 'Copyright="(c) 2024-2026 Vetcoders"' not in bundle
    assert 'Copyright="(c) 2024-2026 LibraxisAI"' not in bundle
    assert "WixUI_Minimal" in product
    assert "WixUILicenseRtf" in product
    assert "License.rtf" in product
    assert "ARPHELPLINK" in product
    assert "SECURITY.md" in product
    assert "hello@vetcoders.io" in product
    assert "ARPCONTACT" in product
    assert "hello@vetcoders.io" in SECURITY_MD.read_text(encoding="utf-8")

    assert "RtfLicense" in bundle
    assert 'LicenseFile="License.rtf"' in bundle
    assert "HyperlinkLicense" not in bundle

    assert "License.rtf" in build
    assert "WixUIExtension" in build
    assert "Business Source License" in build
    assert "License.rtf" in readme
    assert "BUSL" in readme or "LICENSE" in readme


def test_windows_license_rtf_is_regenerated_from_repo_license() -> None:
    """WiX ScrollableText is empty unless License.rtf is real RTF from LICENSE.

    A byte copy of LICENSE contains the legal phrases and still renders as a
    blank license box. Drift between LICENSE and License.rtf must fail.
    """
    from scripts.windows_license_rtf import main, normalize_rtf_text, render_license_rtf

    source = REPO_LICENSE.read_text(encoding="utf-8")
    rendered = render_license_rtf(source)
    on_disk = normalize_rtf_text(LICENSE_RTF.read_text(encoding="utf-8"))
    product = PRODUCT.read_text(encoding="utf-8")
    bundle = BUNDLE.read_text(encoding="utf-8")
    build = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert rendered.startswith("{\\rtf1")
    assert rendered.isascii()
    assert "\r" not in rendered
    assert "\x00" not in rendered
    assert on_disk == rendered
    assert on_disk != source
    for line in source.splitlines():
        if line.isascii() and line and all(ch not in line for ch in "\\{}"):
            assert line in rendered
    assert "\\u-10187?" in rendered
    assert "\\u55349?" not in rendered
    drifted = source.replace("BUSL-1.1", "BUSL-DRIFT", 1)
    assert drifted != source
    assert render_license_rtf(drifted) != rendered
    assert main(["--check"]) == 0

    assert 'WixVariable Id="WixUILicenseRtf" Value="License.rtf"' in product
    assert "WixUI_Minimal" in product
    assert (
        'BootstrapperApplicationRef Id="WixStandardBootstrapperApplication.RtfLicense"'
        in bundle
    )
    assert 'LicenseFile="License.rtf"' in bundle
    assert "windows_license_rtf.py" in build
    assert "--write" in build
    assert "License.rtf is not RTF" in build


def test_windows_license_rtf_check_fails_when_file_is_plain_text(
    tmp_path: Path,
) -> None:
    """A LICENSE byte copy must not pass the drift gate."""
    from scripts.windows_license_rtf import main

    plain = tmp_path / "License.rtf"
    plain.write_text(REPO_LICENSE.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["--check", "--output", str(plain)]) == 1
    assert main(["--write", "--output", str(plain)]) == 0
    rewritten = plain.read_text(encoding="ascii")
    assert rewritten.startswith("{\\rtf1")
    assert "Business Source License" in rewritten
    assert main(["--check", "--output", str(plain)]) == 0


def test_windows_installer_launches_vc_terminal_after_install_not_uninstall() -> None:
    """Post-install must start vc-terminal; uninstall must not."""
    product = PRODUCT.read_text(encoding="utf-8")
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    assert 'Id="LaunchVcTerminal"' in product
    assert "vc-terminal.cmd" in product
    assert 'Directory="INSTALLDIR"' in product
    assert 'After="InstallFinalize"' in product
    assert "LaunchVcTerminal" in product
    # Launch is gated on NOT REMOVE; uninstall path stays REMOVE~="ALL" only.
    launch_line = [
        line
        for line in product.splitlines()
        if "LaunchVcTerminal" in line and "Custom" in line
    ]
    assert launch_line, product
    assert any("NOT REMOVE" in line for line in launch_line)
    assert "LaunchVcTerminal" in build
    assert "vc-terminal" in readme.lower()
